"""Driver-level rules of the translator that need no game to check.

    .venv/bin/python -m pytest -q tools/recomp/tests/test_translate_driver.py
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "tools/recomp"))
import translate as T  # noqa: E402


def test_a_withdrawn_block_leaves_a_trap_where_it_was_dispatched_to():
    """A function whose listing ends on a call that never returns (a C++
    throw, `exit`) falls through to padding.  The sweep recovers a block
    there, finds it dispatches nowhere and withdraws it; whatever referred to
    the block must then stop rather than call into nothing, and must not
    trip the entry-point gate.  Every spelling of a literal transfer to the
    block becomes `recomp_unknown_call(c, addr); return;`."""
    body = [
        "void fn_0061e4c0(X86 *c) {",
        "{   /* 0061e9ea CALL 0x0064cf1f */",
        "    c->r[4] -= 4; wr32(c->r[4], 0x0061e9efu);",
        "    CALL_FN(0064cf1f);",
        "}",
        "CALL_FN(0061e9ef); return;  /* fall-through past the listing */",
        "case 0x0061e9efu: CALL_FN(0061e9ef); return;",
        "c->eip = 0x0061e9eau; recomp_jump(c, 0x0061e9efu); return;",
        "c->eip = 0x61e9eau; recomp_jump(c, 0x61e9efu); return;",
        "CALL_FN(00500000);",
        "}",
    ]
    out = T.retarget_withdrawn(body, {0x0061E9EF})
    assert out[3] == "    CALL_FN(0064cf1f);"           # a live callee is untouched
    assert out[5].startswith("recomp_unknown_call(c, 0x0061e9efu); return;")
    assert out[5].endswith("/* fall-through past the listing */")
    assert out[6] == "case 0x0061e9efu: recomp_unknown_call(c, 0x0061e9efu); return;"
    assert out[7] == "recomp_unknown_call(c, 0x0061e9efu); return;"
    assert out[8] == "recomp_unknown_call(c, 0x0061e9efu); return;"   # hexlit's unpadded spelling
    assert out[9] == "CALL_FN(00500000);"
    assert "0061e9ef" not in "".join(hit for line in out for hit in
                                     __import__("re").findall(r"FN\(([0-9a-f]{8})\)", line))


def test_retargeting_nothing_is_the_identity():
    body = ["CALL_FN(00500000); return;", "recomp_jump(c, 0x00500010u); return;"]
    assert T.retarget_withdrawn(body, set()) == body


def synthetic_image(code_at, base=0x00400000, size=0x2000):
    """An Image over hand-placed bytes: {address: bytes}, one executable section."""
    import capstone
    img = T.Image.__new__(T.Image)
    img.base, img.size, img.end = base, size, base + size
    data = bytearray(size)
    for va, code in code_at.items():
        data[va - base:va - base + len(code)] = code
    img.data = bytes(data)
    img.reloc_dir = (0, 0)
    img.exec_ranges = [(base, base + size, ".text")]
    img.data_ranges = []
    img.recover_errors = []
    img.string_candidates = set()
    img.thunk_candidates = set()
    img.weak_candidates = set()
    img.interior_candidates = set()
    img.noreturn_callees = set()
    img.recover_errors = []
    img.md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    return img


def _walker_image(slots):
    """A __initterm-shaped walker, a caller naming a range, and that range."""
    import struct
    base, walker, caller, table = 0x00400000, 0x00401000, 0x00401100, 0x00402000
    lo, hi = table, table + 4 * len(slots)
    code = {
        # CALL dword ptr [EDX]; ADD EAX,4; RET - the shape, in registers the
        # matcher used not to know.
        walker: b"\xff\x12\x83\xc0\x04\xc3",
        # PUSH hi; PUSH lo; CALL walker; RET
        caller: (b"\x68" + struct.pack("<I", hi) + b"\x68" + struct.pack("<I", lo)
                 + b"\xe8" + struct.pack("<i", walker - (caller + 15)) + b"\xc3"),
        table: b"".join(struct.pack("<I", v) for v in slots),
    }
    # Big enough that the table is inside the image: the default 0x2000 ends
    # exactly at `table`, and every slot would read out of bounds - which a
    # test looking for a refusal would have passed for the wrong reason.
    img = synthetic_image(code, size=0x4000)
    img.data_ranges = [(table, table + 0x100, ".data")]
    fns = []
    for at in (walker, caller):
        insns = img.recover(at, set())
        fn = T.Function(at, "fn_%08x" % at, insns[-1].addr + 1 - at, insns)
        fn.measure(img)
        fns.append(fn)
    return img, fns


def test_initterm_takes_a_table_of_function_pointers():
    """The walker is found by its shape, whatever registers it happens to use."""
    img, fns = _walker_image([0x00401000, 0, 0x00401005])
    ranges, entries = img.initterm_tables(fns)
    assert len(ranges) == 1
    assert entries == {0x00401000, 0x00401005}


def test_initterm_refuses_a_range_of_text():
    """The strict table test is what makes the loose walker test safe.

    Populous has two functions of exactly this shape that are not __initterm
    at all: one is handed "%s: %d bytes allocated f..." from eight call sites.
    Matching the walk by shape reaches them, and only a table that holds
    function pointers and nulls and nothing else keeps a format string out of
    the entry points.
    """
    import struct
    text = b"%s: %d bytes allocated free\0"
    img, fns = _walker_image(struct.unpack("<7I", text[:28]))
    ranges, entries = img.initterm_tables(fns)
    assert ranges == [] and entries == set()
@pytest.mark.parametrize("what,code,expected", [
    ("ordinary code", b"\x55\x8b\xec\xc3", None),
    ("a halt", b"\xf4\xc3", "hlt"),
    # 16-bit addressing - ADD byte ptr [BX + DI],CH - decodes cleanly and
    # becomes a listing Insn without complaint; it is the EMITTER that refuses
    # the operand, so this probe is not what catches it and must not pretend
    # to. The acceptance check beside it is.
    ("16-bit addressing", b"\x67\x00\x29\xc3", None),
    # Past a terminator the bytes belong to whatever comes next.
    ("a halt behind a RET", b"\xc3\xf4", None),
])
def test_undecodable_run_names_only_what_the_decoder_itself_refuses(what, code, expected):
    """A pointer into .text is only an entry point if the bytes are code."""
    at = 0x00401000
    got = synthetic_image({at: code}).undecodable_run(at)
    if expected is None:
        assert got is None, "%s should read as ordinary code, got %r" % (what, got)
    else:
        assert got and expected in got, "%s should be refused, got %r" % (what, got)


@pytest.mark.parametrize("what,blob,expected", [
    ("an API name", b"GetLongPathNameW\0", True),
    ("text too short to be a name", b"Get\0", False),
    # 0xff, not a zero-filled tail: the run has to END, and end at a NUL.
    ("printable bytes that run into binary", b"GetLongPathNameW\xff", False),
    # PUSH EBP; MOV EBP,ESP; SUB ESP,0x28; PUSH ESI - ordinary code, and
    # several of those bytes are printable, which is why the run has a floor.
    ("ordinary code", b"\x55\x8b\xec\x83\xec\x28\x56\x00", False),
])
def test_a_narrow_literal_reads_as_data(what, blob, expected):
    """GetProcAddress names live in .text; a pointer to one is an argument."""
    at = 0x00401000
    assert synthetic_image({at: blob}).starts_with_ascii_run(at) is expected, what


def test_a_wide_literal_reads_as_data_not_as_a_function_start():
    """The shape the rule relies on: Delphi keeps literals in .text."""
    at = 0x00401000
    img = synthetic_image({at: "kernel32.dll\0".encode("utf-16-le")})
    assert img.starts_with_utf16_run(at)
    # and it is not refused as undecodable, which is why the literal test has
    # to exist in its own right rather than leaning on the decoder.
    assert img.undecodable_run(at) is None


@pytest.mark.parametrize("code,expected", [
    (b"\xeb\xfe", True),                       # closed unconditional loop
    (b"\xc3", False),                          # ordinary return
    (b"\x75\x02\xeb\xfc\xc3", False),          # conditional return path
    (b"\xeb\x0e", False),                      # outward tail call
    (b"\xff\xe0", False),                      # computed tail call
    (b"\xe2\x0e\xeb\xfc", False),              # LOOP escapes the body
    (b"\xe3\x0e\xeb\xfc", False),              # JECXZ escapes the body
    (b"\xe8\xfb\x00\x00\x00\xeb\xf9", True), # callback inside a closed loop
])
def test_closed_loop_requires_every_return_path_to_stay_inside(code, expected):
    entry = 0x00401000
    img = synthetic_image({entry: code})
    img.md.detail = True
    insns = [img.to_insn(ci) for ci in img.md.disasm(code, entry)]
    fn = T.Function(entry, "loop", len(code), insns)
    fn.measure(img)
    assert T.Translator.closed_noreturn_loop(fn) == expected


@pytest.mark.parametrize("artifact", ["symbols.json", "translate-report.json"])
@pytest.mark.parametrize("push_ret", [False, True, "noreturn_loop"])
def test_output_records_the_loaded_image_base(tmp_path, monkeypatch, artifact, push_ret):
    """Nondefault images include omitted PUSH/RET epilogues, without pointer guesses."""
    import json
    import struct
    base, entry = 0x00600000, 0x00601000
    landing = entry + 6
    code = b"\x68" + struct.pack("<I", landing) + b"\xc3\x40\xc3" if push_ret else b"\xc3"
    loop = entry + 0x100
    if push_ret == "noreturn_loop":
        # Omitted continuation calls a shutdown loop, followed by non-code.
        code = code[:6] + b"\xe8" + struct.pack("<i", loop - landing - 5) + b"\x0f\x0b"
    img = synthetic_image({entry: code, loop: b"\xeb\xfe"}, base=base)
    img.plausible_immediate_target = lambda addr: False
    img.code_pointers = lambda *args, **kwargs: (set(), set())
    listings = tmp_path / "functions"
    listings.mkdir()
    listing = ("%08x  PUSH 0x%x\n%08x  RET\n" % (entry, landing, entry + 5)
               if push_ret else "%08x  RET\n" % entry)
    (listings / ("%08x.asm" % entry)).write_text(listing)
    table = tmp_path / "functions.tsv"
    table.write_text("address\tname\tsize\n%08x\treturn_only\t1\n" % entry)
    if push_ret == "noreturn_loop":
        (listings / ("%08x.asm" % loop)).write_text("%08x  JMP 0x%x\n" % (loop, loop))
        with table.open("a") as fh:
            fh.write("%08x\tshutdown_loop\t2\n" % loop)
    binary = tmp_path / "synthetic-image"
    binary.write_bytes(img.data)
    curated = tmp_path / "globals.toml"
    curated.write_text("")
    out = tmp_path / "gen"
    report = out / "translate-report.json"
    monkeypatch.setattr(T, "configure", lambda cfg: None)
    monkeypatch.setattr(T.game_config, "load", lambda path: {})
    monkeypatch.setattr(T, "LISTINGS", str(listings))
    monkeypatch.setattr(T, "FUNCS_TSV", str(table))
    monkeypatch.setattr(T, "BINARY", str(binary))
    monkeypatch.setattr(T, "CURATED", str(curated))
    monkeypatch.setattr(T, "Image", lambda path: img)
    monkeypatch.setattr(sys, "argv", ["translate.py", "--game", str(tmp_path),
                                     "--out", str(out), "--report", str(report), "--quiet"])
    assert T.main() == 0
    result = json.loads((out / artifact).read_text())
    assert result["image_base"] == "%08x" % base
    if push_ret:
        text = "\n".join(p.read_text() for p in out.glob("chunk_*.c"))
        assert "void fn_%08x(" % landing in text
        assert "case %s: goto L_%08x;" % (T.hexlit(landing), landing) in text


@pytest.mark.parametrize("resumable", [False, True])
def test_computed_returns_use_sorted_call_continuations(tmp_path, monkeypatch, resumable):
    """Direct and indirect CALL lengths name returns, not new dispatch entries."""
    import re
    import struct
    entry, callee = 0x00601000, 0x00601080
    code = (b"\xb8" + struct.pack("<I", callee) + b"\xff\xd0\xe8"
            + struct.pack("<i", callee - entry - 12)
            + b"\xff\x15\x00\x18\x60\x00\xc3")
    img = synthetic_image({entry: code, callee: b"\xc3"}, base=0x00600000)
    img.code_pointers = lambda *args, **kwargs: (set(), set())
    listings = tmp_path / "functions"
    listings.mkdir()
    (listings / ("%08x.asm" % entry)).write_text(
        "00601000  MOV EAX,0x601080\n00601005  CALL EAX\n"
        "00601007  CALL 0x601080\n0060100c  CALL dword ptr [0x601800]\n00601012  RET\n")
    (listings / ("%08x.asm" % callee)).write_text("00601080  RET\n")
    table = tmp_path / "functions.tsv"
    table.write_text("address\tname\tsize\n00601000\tcaller\t19\n00601080\tcallee\t1\n")
    binary, curated = tmp_path / "image", tmp_path / "globals.toml"
    binary.write_bytes(img.data)
    curated.write_text("")
    out = tmp_path / "gen"
    monkeypatch.setattr(T, "configure", lambda cfg: None)
    monkeypatch.setattr(T.game_config, "load", lambda path: {})
    for name, value in (("LISTINGS", listings), ("FUNCS_TSV", table),
                        ("BINARY", binary), ("CURATED", curated)):
        monkeypatch.setattr(T, name, str(value))
    monkeypatch.setattr(T, "EXTRA_ENTRY_POINTS", frozenset())
    monkeypatch.setattr(T, "RESUMABLE_STACKS", resumable)
    monkeypatch.setattr(T, "Image", lambda path: img)
    discovered = tmp_path / "discovered.txt"
    discovered.write_text("00601080 call 00601005 1\n1000d250 call 1000bad0 1\n")
    monkeypatch.setattr(sys, "argv", ["translate.py", "--game", str(tmp_path),
                                     "--out", str(out), "--quiet",
                                     "--discovered", str(discovered)])
    assert T.main() == 0
    assert callee in T.EXTRA_ENTRY_POINTS
    assert 0x1000d250 not in T.EXTRA_ENTRY_POINTS
    chunks = "\n".join(p.read_text() for p in out.glob("chunk_*.c"))
    for address in (entry + 7, entry + 12, entry + 18):
        assert ("if (c->eip != %s) return;" % T.hexlit(address) in chunks) == resumable
        assert ("void fn_%08x(X86 *c)" % address in chunks) == resumable
    text = (out / "table.c").read_text()
    assert "int recomp_is_call_return(uint32_t target)" in text
    array = text.split("recomp_call_returns[] = {", 1)[1].split("};", 1)[0]
    assert [int(a, 16) for a in re.findall(r"0x([0-9a-f]+)u", array)] == [
        entry + 7, entry + 12, entry + 18]
    jump = text.split("void recomp_jump(", 1)[1].split("void recomp_unknown_jump(", 1)[0]
    assert jump.index("if (i >= 0)") < jump.index("recomp_is_call_return(target)")
    assert "if (recomp_is_call_return(target) || recomp_module_is_call_return(target)) { c->eip = target; return; }" in jump
    assert jump.index("recomp_is_call_return(target)") < jump.index("recomp_unknown_jump(c, target)")
    call = text.split("void recomp_call(", 1)[1].split("void recomp_jump(", 1)[0]
    assert "recomp_is_call_return" not in call
    assert '#include "thunks.h"' in text
    assert "int recomp_thunk_target_kind(uint32_t target)" in text
    assert "return (recomp_is_call_return(target) || recomp_module_is_call_return(target)) ? 2 : 0;" in text
    unknown = text.split("void recomp_unknown_jump(", 1)[1]
    assert "if (recomp_run_thunk(c, target)) return;" in unknown


@pytest.mark.parametrize("target,dispatch", [(0x0060100b, True),
                                           (0x00601002, False),
                                           (0x00601100, False)])
def test_return_switch_requires_pushed_instruction_boundary(target, dispatch):
    """Only a pushed instruction in this body makes RET an interior dispatch."""
    import struct
    from test_translate_insns import Opts
    entry = 0x00601000
    code = b"\x68" + struct.pack("<I", target) + b"\xb8\x2a\x00\x00\x00\xc3\xc2\x08\x00"
    img = synthetic_image({entry: code}, base=0x00600000)
    insns = T.parse_listing_text(
        "00601000  PUSH 0x%x\n00601005  MOV EAX,0x2a\n0060100a  RET\n0060100b  RET 0x8\n" % target)
    fn = T.Function(entry, "cleanup", len(code), insns)
    fn.measure(img)
    tr = T.Translator(img, {entry}, Opts())
    tr.prepare(fn)
    text = "\n".join(tr.translate(fn, [entry + 5]))
    assert ("switch (r_)" in text) == dispatch
    if dispatch:
        assert text.count("switch (r_)") == 2
        assert text.count("case 0x60100bu: goto L_0060100b;") == 2
        assert "c->r[4] += 12u;" in text
        assert text.count("default: c->eip = r_; recomp_return(c); return;") == 2
    assert "void fn_00601005(X86 *c) { body_00601000(c, 0x601005u); }" in text


@pytest.mark.parametrize("indirect", [False, True])
def test_tableless_jump_uses_all_local_instruction_labels(indirect):
    from test_translate_insns import Opts
    entry = 0x00601000
    code = b"\xff\xe0\x90\xc3" if indirect else b"\x89\xc0\x90\xc3"
    img = synthetic_image({entry: code}, base=0x00600000)
    listing = "00601000  %s\n00601002  NOP\n00601003  RET\n" % (
        "JMP EAX" if indirect else "MOV EAX,EAX")
    fn = T.Function(entry, "local_dispatch", len(code), T.parse_listing_text(listing))
    fn.measure(img)
    tr = T.Translator(img, {entry}, Opts())
    tr.prepare(fn)
    text = "\n".join(tr.translate(fn))
    assert ("switch (t_)" in text) == indirect
    if indirect:
        assert "if (t_ >= 0x601000u && t_ < 0x601004u)" in text
        for addr in sorted(fn.addrs):
            assert "case %s: goto L_%08x;" % (T.hexlit(addr), addr) in text
            assert "L_%08x: ;" % addr in text
        assert "default: break;" in text
        assert text.index("default: break;") < text.index("recomp_jump(c, t_)")
        assert "case 0x601001u:" not in text  # inside an instruction uses the existing fallback


@pytest.mark.parametrize("jump_to_cleanup", [False, True])
@pytest.mark.parametrize("listed_cleanup", [False, True])
@pytest.mark.parametrize("recovered_owner", [False, True, "speculative_epilogue",
                                           "speculative_body", "zero_prefix", "config", "direct"])
def test_finally_cleanup_and_epilogue_belong_to_establishing_body(
        tmp_path, monkeypatch, jump_to_cleanup, listed_cleanup, recovered_owner,
        complete_listing=False, epilogue_handler=False, edx_frame=False):
    """Omitted normal cleanup is also callable through an alternate SEH entry."""
    import struct
    entry, stub, epilogue, helper = 0x00601000, 0x00601040, 0x00601060, 0x00601080
    dispatcher = 0x00601090
    code = bytearray(b"\x55\x89\xe5\x31\xc0\x55\x68" + struct.pack("<I", stub)
                     + b"\x64\xff\x30\x64\x89\x20\x5a\x59\x59\x64\x89\x10\x68"
                     + struct.pack("<I", epilogue))
    if edx_frame:
        code[3:5] = b"\x31\xd2"  # XOR EDX,EDX before the three frame pushes
        code[13] = 0x32           # PUSH FS:[EDX]
        code[16] = 0x22           # MOV FS:[EDX],ESP
        code[17:17] = b"\x31\xc0"  # restore later through FS:[EAX]
    cleanup = 0x00601050 if jump_to_cleanup else entry + len(code)
    if jump_to_cleanup:
        code += b"\xe9" + struct.pack("<i", cleanup - entry - len(code) - 5)
    listed_end = len(code)
    cleanup_code = b"\x90\xe8" + struct.pack("<i", helper - cleanup - 6) + b"\xc3"
    img = synthetic_image({entry: bytes(code), cleanup: cleanup_code,
                           stub: b"\xe9" + struct.pack("<i", dispatcher - stub - 5)
                                 + b"\xe9" + struct.pack("<i", cleanup - stub - 10),
                           epilogue: b"\x89\xec\x5d\xc3", helper: b"\xc3", dispatcher: b"\xc3"},
                          base=0x00600000)
    img.code_pointers = lambda *a, **kw: (set(), set())
    img.plausible_immediate_target = lambda addr: False
    img.md.detail = True
    insns = [img.to_insn(ci) for ci in img.md.disasm(bytes(code), entry)]
    if complete_listing:
        insns += [img.to_insn(ci) for ci in img.md.disasm(cleanup_code, cleanup)]
    img.md.detail = False
    listings = tmp_path / "functions"
    listings.mkdir()
    (listings / ("%08x.asm" % entry)).write_text("\n".join(
        "%08x  %s %s" % (i.addr, i.mnem, ",".join(i.ops)) for i in insns) + "\n")
    for addr in (helper, dispatcher):
        (listings / ("%08x.asm" % addr)).write_text("%08x  RET\n" % addr)
    table = tmp_path / "functions.tsv"
    table.write_text("address\tname\tsize\n%08x\testablishing\t%d\n%08x\thelper\t1\n%08x\tdispatcher\t1\n"
                     % (entry, listed_end, helper, dispatcher))
    if listed_cleanup:
        (listings / ("%08x.asm" % cleanup)).write_text(
            "%08x  NOP\n%08x  CALL 0x%x\n%08x  RET\n" % (cleanup, cleanup + 1, helper, cleanup + 6))
        with table.open("a") as fh:
            fh.write("%08x\tshared_cleanup\t7\n" % cleanup)
    if recovered_owner:
        (listings / ("%08x.asm" % entry)).unlink()
        table.write_text("\n".join(line for line in table.read_text().splitlines()
                                   if not line.startswith("%08x\t" % entry)) + "\n")
        img.code_pointers = lambda *a, **kw: ({entry}, set())
    if recovered_owner == "direct":
        # Entry protection follows a listed caller's direct edge, even though
        # the callee itself is absent from the listings.
        caller = entry + 0x100
        raw = b"\xe8" + struct.pack("<i", entry - caller - 5) + b"\xc3"
        data = bytearray(img.data)
        data[caller - img.base:caller - img.base + len(raw)] = raw
        img.data = bytes(data)
        (listings / ("%08x.asm" % caller)).write_text(
            "%08x  CALL 0x%x\n%08x  RET\n" % (caller, entry, caller + 5))
        with table.open("a") as fh:
            fh.write("%08x\tcaller\t6\n" % caller)
    if recovered_owner in ("speculative_epilogue", "speculative_body", "zero_prefix"):
        # A pointer guess owns the real epilogue, but has an invalid prefix.
        # Adopting the epilogue must not import that prefix into the real body.
        prefix = entry - 0x20
        target = epilogue if recovered_owner == "speculative_epilogue" else entry + 6
        raw = (b"\x0f\x84" + struct.pack("<i", target - prefix - 6)
               + b"\xe9" + struct.pack("<i", -0x10000))
        if recovered_owner == "zero_prefix":
            # This completely translatable block is nevertheless a data guess.
            # Its first instruction is ADD byte ptr [EAX],AL (00 00).
            raw = b"\x00\x00\xc3"
        data = bytearray(img.data)
        data[prefix - img.base:prefix - img.base + len(raw)] = raw
        img.data = bytes(data)
        img.code_pointers = lambda *a, **kw: ({prefix, entry}, set())
    if epilogue_handler:
        # A separately established handler branches into the same epilogue.
        # Resolving that structural entry must not split it out again after
        # the normal owner adopts it, or discovery never reaches a fixed point.
        other, other_stub = entry + 0x200, entry + 0x240
        raw = (b"\x31\xc0\x55\x68" + struct.pack("<I", other_stub)
               + b"\x64\xff\x30\x64\x89\x20\xc3")
        data = bytearray(img.data)
        data[other - img.base:other - img.base + len(raw)] = raw
        handler_target = epilogue + 2 if epilogue_handler == "interior" else epilogue
        if epilogue_handler == "prefix":
            handler_target = entry + 17  # restore the chain, then push and run cleanup
        if epilogue_handler == "cleanup":
            handler_target = cleanup
        data[other_stub - img.base:other_stub - img.base + 10] = (
            b"\xe9" + struct.pack("<i", dispatcher - other_stub - 5)
            + b"\xe9" + struct.pack("<i", handler_target - other_stub - 10))
        img.data = bytes(data)
        img.md.detail = True
        other_insns = [img.to_insn(ci) for ci in img.md.disasm(raw, other)]
        img.md.detail = False
        (listings / ("%08x.asm" % other)).write_text("\n".join(
            "%08x  %s %s" % (i.addr, i.mnem, ",".join(i.ops)) for i in other_insns) + "\n")
        if epilogue_handler in ("interior", "prefix"):
            img.code_pointers = lambda *a, **kw: ({entry, other}, set())
        else:
            with table.open("a") as fh:
                fh.write("%08x\tother_frame\t%d\n" % (other, len(raw)))
    binary, curated = tmp_path / "image", tmp_path / "globals.toml"
    binary.write_bytes(img.data)
    curated.write_text("")
    out = tmp_path / "gen"
    monkeypatch.setattr(T, "configure", lambda cfg: None)
    monkeypatch.setattr(T.game_config, "load", lambda path: {})
    for name, value in (("LISTINGS", listings), ("FUNCS_TSV", table),
                        ("BINARY", binary), ("CURATED", curated)):
        monkeypatch.setattr(T, name, str(value))
    # The handler stub alone makes the epilogue an SEH entry. It is not also
    # configured: a configured entry point is never absorbed into another
    # body, which is the opposite of what these cases check.
    seeds = {entry} if recovered_owner == "config" else set()
    monkeypatch.setattr(T, "EXTRA_ENTRY_POINTS", frozenset(seeds))
    monkeypatch.setattr(T, "Image", lambda path: img)
    monkeypatch.setattr(sys, "argv", ["translate.py", "--game", str(tmp_path), "--out", str(out), "--quiet"])
    assert T.main() == 0
    text = "\n".join(p.read_text() for p in out.glob("chunk_*.c"))
    assert "void fn_%08x(X86 *c) { body_%08x(c, %s); }" % (cleanup, entry, T.hexlit(cleanup)) in text
    assert text.count("void fn_%08x(" % cleanup) == 1
    body = text.split("static void body_%08x(" % entry, 1)[1].split("void fn_%08x(" % entry, 1)[0]
    assert "L_%08x: ;" % epilogue in body
    assert "case %s: goto L_%08x;" % (T.hexlit(epilogue), epilogue) in body
    assert "CALL_FN(%08x);" % helper in body
    assert "CALL_FN(%08x);" % cleanup not in body
    if recovered_owner in ("speculative_epilogue", "speculative_body", "zero_prefix"):
        assert "void fn_%08x(" % prefix not in text
    if recovered_owner:
        import json
        symbols = json.loads((out / "symbols.json").read_text())
        establishing = next(f for f in symbols["functions"] if f["addr"] == "%08x" % entry)
        if recovered_owner == "config":
            assert establishing["provenance"] == "config"
        elif recovered_owner == "direct":
            assert establishing["provenance"] == "seh"
        else:
            assert establishing["provenance"] != "seh", "SEH content cannot promote a scan guess"


def test_overlapping_cleanup_body_is_retired_when_already_in_owner(tmp_path, monkeypatch):
    test_finally_cleanup_and_epilogue_belong_to_establishing_body(
        tmp_path, monkeypatch, jump_to_cleanup=False, listed_cleanup=True,
        recovered_owner=False, complete_listing=True)


def test_zero_edx_frame_keeps_cleanup_in_its_owner(tmp_path, monkeypatch):
    test_finally_cleanup_and_epilogue_belong_to_establishing_body(
        tmp_path, monkeypatch, jump_to_cleanup=False, listed_cleanup=False,
        recovered_owner=False, complete_listing=True, edx_frame=True)


def test_adopted_epilogue_is_not_split_again_by_seh_resolution(tmp_path, monkeypatch):
    test_finally_cleanup_and_epilogue_belong_to_establishing_body(
        tmp_path, monkeypatch, jump_to_cleanup=False, listed_cleanup=False,
        recovered_owner=True, epilogue_handler=True)


def test_adopted_body_keeps_interior_handler_entries(tmp_path, monkeypatch):
    test_finally_cleanup_and_epilogue_belong_to_establishing_body(
        tmp_path, monkeypatch, jump_to_cleanup=False, listed_cleanup=False,
        recovered_owner=True, epilogue_handler="interior")


def test_speculative_establishing_body_adopts_preexisting_cleanup_before_admission(tmp_path, monkeypatch):
    """An already seeded cleanup alias must not look like unterminated flow."""
    test_finally_cleanup_and_epilogue_belong_to_establishing_body(
        tmp_path, monkeypatch, jump_to_cleanup=False, listed_cleanup=False,
        recovered_owner=True, epilogue_handler="cleanup")


def test_retired_cleanup_prefix_remains_an_alternate_entry(tmp_path, monkeypatch):
    test_finally_cleanup_and_epilogue_belong_to_establishing_body(
        tmp_path, monkeypatch, jump_to_cleanup=False, listed_cleanup=False,
        recovered_owner=True, epilogue_handler="prefix")


def test_a_pushed_destructor_thunk_is_an_entry_candidate():
    """`atexit` is handed the address of a ten-byte `MOV ECX,obj / JMP dtor`
    thunk packed right after its initializer's RET: unaligned, not preceded
    by padding, so the function-start signals reject it, yet the CRT calls
    it at exit.  A pushed immediate that decodes as a thunk is a candidate."""
    dtor, thunk = 0x00401000, 0x0040105a
    rel = (dtor - (thunk + 10)) & 0xFFFFFFFF
    img = synthetic_image({dtor: b"\xc3",
                           thunk: bytes([0xb9, 0xd8, 0x30, 0x72, 0x00, 0xe9]) + rel.to_bytes(4, "little")})
    assert img.plausible_immediate_target(thunk)
    assert img.plausible_immediate_target(dtor)          # an aligned start after padding


@pytest.mark.parametrize("alignment,admitted", [(16, False), (4, True)])
def test_unaligned_frame_callback_uses_configured_alignment(monkeypatch, alignment, admitted):
    callback = 0x00401108
    # MOV EAX,EAX padding, then a frame with more than the thunk decoder's
    # eight instructions before its stdcall return. Only a PUSH names it.
    code = bytes.fromhex("55 8bec 6a00 53 56 33c0 33db 33f6 8b4508 5e 5b 59 5d c20400")
    img = synthetic_image({callback - 2: b"\x8b\xc0", callback: code})
    monkeypatch.setattr(T, "FUNCTION_ALIGNMENT", alignment, raising=False)
    assert img.is_exec(callback)
    assert img.recover(callback, set())
    assert not img.looks_like_thunk(callback)
    assert img.looks_like_code_start(callback) == admitted
    assert img.plausible_immediate_target(callback) == admitted
    # The stronger gate still needs its padding signal as well as alignment.
    assert not img.looks_like_function(callback)
    img.data = img.data[:callback - img.base - 1] + b"\x90" + img.data[callback - img.base:]
    assert img.looks_like_function(callback) == admitted


def test_a_wild_jump_is_not_an_entry_candidate():
    # MSVC's three-byte NOP padding `8d 49 00` decodes at its last byte as a
    # JMP to nowhere; an immediate landing there names nothing.
    pad = 0x00401082
    img = synthetic_image({pad - 2: b"\x8d\x49\x00\xe9\x00\x00\x00\xf5"})
    assert not img.plausible_immediate_target(pad + 1)


def test_recovery_stops_at_a_call_that_never_returns():
    """A block recovered from the PE follows fall-through after a CALL, and
    after a call to `__CxxThrowException` or `exit` the fall-through is
    padding and then a jump table: decoding on turns the table's bytes into
    `BOUND` and `POP ES`, the emitter refuses them, and the whole block - a
    real function a vtable names - is rejected as data.  A callee the
    listings show never returning (a function whose listing ends on the CALL)
    ends the block."""
    throw, fn = 0x00401000, 0x00401100
    rel = (throw - (fn + 5 + 5)) & 0xFFFFFFFF
    code = (b"\x68\x78\xc6\x69\x00"                  # PUSH 0x69c678
            + b"\xe8" + rel.to_bytes(4, "little")      # CALL throw
            + b"\x90"                                  # padding
            + (0x00401149).to_bytes(4, "little")        # a jump table: dwords, not code
            + (0x00401159).to_bytes(4, "little"))
    img = synthetic_image({throw: b"\xc3", fn: code})
    # Without the knowledge, the walk runs into the table.
    plain = img.recover(fn, set())
    assert plain and plain[-1].addr > fn + 10
    img.noreturn_callees = {throw}
    stopped = img.recover(fn, set())
    assert [i.addr for i in stopped] == [fn, fn + 5]


def test_a_call_that_never_returns_ends_the_block_with_a_trap():
    """After `CALL __CxxThrowException` nothing follows that the game can
    reach, and the bytes after it are padding and a switch table.  The
    emitter must not jump on to them; it leaves a trap that names the
    address, so reaching it (the callee returning after all) is reported."""
    from test_translate_insns import NoImage, Opts
    throw, fn = 0x00401000, 0x00401100
    tr = T.Translator(NoImage(), {fn, throw}, Opts())
    tr.noreturn_callees = {throw}
    insns = T.parse_listing_text("00401100  PUSH 0x69c678\n00401105  CALL 0x00401000\n")
    f = T.Function(fn, "thrower", 10, insns)
    f.measure(NoImage())
    tr.prepare(f)
    text = "\n".join(tr.translate(f))
    assert "CALL_FN(00401000);" in text
    assert "recomp_unknown_call(c, 0x40110au); return;" in text
    assert "recomp_jump(c, 0x40110au)" not in text and "CALL_FN(0040110a)" not in text


def test_an_iat_call_to_exitprocess_ends_the_block_but_raiseexception_does_not():
    """A listing that stops on `CALL dword ptr [IAT ExitProcess]` must not fall
    through into the padding behind it (NFSU1, 9da309e). RaiseException is not
    such an import: a continuable exception returns when a handler continues,
    and NFSMW's C runtime reads the exception record back after the call."""
    from test_translate_insns import NoImage, Opts
    assert {"ExitProcess", "ExitThread", "FatalAppExitA"} <= T.NORETURN_IAT_IMPORTS
    assert "RaiseException" not in T.NORETURN_IAT_IMPORTS
    fn, slot = 0x00401100, 0x00408000
    listing = "00401100  CALL dword ptr [0x00408000]\n00401106  MOV EAX,0x1\n0040110b  RET\n"
    texts = []
    for noreturn in (set(), {slot}):
        tr = T.Translator(NoImage(), {fn}, Opts())
        tr.noreturn_iat = noreturn
        f = T.Function(fn, "exits", 12, T.parse_listing_text(listing))
        f.measure(NoImage())
        tr.prepare(f)
        texts.append("\n".join(tr.translate(f)))
    assert "00401106 MOV EAX,0x1" in texts[0]
    assert "00401106 MOV EAX,0x1" not in texts[1]


def test_a_poll_checkpoint_goes_on_the_named_edge_only():
    """[translate] poll_checkpoints: a scheduler checkpoint before the taken
    branch of a verified busy-wait edge, nowhere else; NFSMW names none, so its
    generated code is unchanged."""
    from test_translate_insns import NoImage, Opts
    fn = 0x00401100
    listing = "00401100  CMP EAX,0x0\n00401103  JNZ 0x00401100\n00401105  RET\n"
    saved = dict(T.POLL_CHECKPOINTS)
    texts = []
    try:
        for edges in ({}, {0x00401103: 0x00401100}):
            T.POLL_CHECKPOINTS.clear()
            T.POLL_CHECKPOINTS.update(edges)
            T.POLL_CHECKPOINTS_APPLIED.clear()
            tr = T.Translator(NoImage(), {fn}, Opts())
            f = T.Function(fn, "poll", 6, T.parse_listing_text(listing))
            f.measure(NoImage())
            tr.prepare(f)
            texts.append("\n".join(tr.translate(f)))
        assert 0x00401103 in T.POLL_CHECKPOINTS_APPLIED
    finally:
        T.POLL_CHECKPOINTS.clear()
        T.POLL_CHECKPOINTS.update(saved)
        T.POLL_CHECKPOINTS_APPLIED.clear()
    assert "recomp_poll_checkpoint(c);" not in texts[0]
    assert texts[1].count("recomp_poll_checkpoint(c);") == 1


def test_a_code_pointer_spelled_in_printable_bytes_is_still_a_pointer():
    """`PUSH 0x5b2370` stores the address as bytes 70 23 5b 00: 'p', '#', '['
    and a terminator, which the string-tail test reads as the end of a
    string and drops.  The address is 16-aligned, follows padding and
    decodes: every signal of a function start.  Those outrank a text
    coincidence, which is what one printable dword is."""
    target, holder = 0x00402370, 0x00401800
    img = synthetic_image({target - 1: b"\x90\x8b\x44\x24\x04\xc3",
                           holder: b"\x68" + target.to_bytes(4, "little")},  # PUSH target
                          size=0x4000)
    assert img.looks_like_function(target)
    starts, _interior = img.code_pointers(set())
    assert target in starts


def test_except_calls_push_decoded_returns_after_short_jump(tmp_path, monkeypatch):
    """A short jump skips an E9 stub and three CALLs ending at a listed epilogue."""
    import struct
    entry, stub, helper, done, dispatch = 0x00601000, 0x00601020, 0x00601100, 0x00601110, 0x00601120
    cleanup, epilogue = stub + 5, stub + 20
    prefix = (b"\x55\x89\xe5\x31\xc0\x55\x68" + struct.pack("<I", stub)
              + b"\x64\xff\x30\x64\x89\x20\x5a\x59\x59\x64\x89\x10")
    prefix += b"\x90" * (stub - entry - 2 - len(prefix)) + b"\xeb\x14"
    omitted = b"\xe9" + struct.pack("<i", dispatch - stub - 5)
    for at, target in ((cleanup, helper), (cleanup + 5, helper), (cleanup + 10, done)):
        omitted += b"\xe8" + struct.pack("<i", target - at - 5)
    epilogue_code = b"\x5d\xc3"
    # DoneExcept's relevant contract: pop the CALL continuation and JMP to it.
    img = synthetic_image({entry: prefix + omitted + epilogue_code,
                           helper: b"\xc3", done: b"\x5a\xff\xe2", dispatch: b"\xc3"},
                          base=0x00600000)
    img.code_pointers = lambda *a, **kw: (set(), set())
    img.plausible_immediate_target = lambda addr: False
    img.md.detail = True
    insns = [img.to_insn(ci) for ci in img.md.disasm(prefix, entry)]
    insns += [img.to_insn(ci) for ci in img.md.disasm(epilogue_code, epilogue)]
    listings = tmp_path / "functions"
    listings.mkdir()
    rows = [(entry, "establishing", epilogue + 2 - entry, insns)]
    for addr, code in ((helper, b"\xc3"), (done, b"\x5a\xff\xe2"), (dispatch, b"\xc3")):
        rows.append((addr, "helper", len(code), [img.to_insn(ci) for ci in img.md.disasm(code, addr)]))
    img.md.detail = False
    for addr, name, size, insns in rows:
        (listings / ("%08x.asm" % addr)).write_text("\n".join(
            "%08x  %s %s" % (i.addr, i.mnem, ",".join(i.ops)) for i in insns) + "\n")
    table = tmp_path / "functions.tsv"
    table.write_text("address\tname\tsize\n" + "".join(
        "%08x\t%s\t%d\n" % (addr, name, size) for addr, name, size, _ in rows))
    binary, curated = tmp_path / "image", tmp_path / "globals.toml"
    binary.write_bytes(img.data)
    curated.write_text("")
    out = tmp_path / "gen"
    monkeypatch.setattr(T, "configure", lambda cfg: None)
    monkeypatch.setattr(T.game_config, "load", lambda path: {})
    for name, value in (("LISTINGS", listings), ("FUNCS_TSV", table),
                        ("BINARY", binary), ("CURATED", curated)):
        monkeypatch.setattr(T, name, str(value))
    monkeypatch.setattr(T, "EXTRA_ENTRY_POINTS", frozenset())
    monkeypatch.setattr(T, "Image", lambda path: img)
    monkeypatch.setattr(sys, "argv", ["translate.py", "--game", str(tmp_path), "--out", str(out), "--quiet"])
    assert T.main() == 0
    text = "\n".join(p.read_text() for p in out.glob("chunk_*.c"))
    block = text.split("static void body_%08x(" % entry, 1)[1].split("void fn_%08x(" % entry, 1)[0]
    for ret in (cleanup + 5, cleanup + 10, epilogue):
        assert "wr32(c->r[4], %s);" % T.hexlit(ret) in block
    assert "wr32(c->r[4], %s);" % T.hexlit(cleanup + 11) not in block
    assert "c->eip = c->r[2]; recomp_return(c); return;" in text
    table_text = (out / "table.c").read_text()
    returns = table_text.split("recomp_call_returns[] = {", 1)[1].split("};", 1)[0]
    assert "0x%08xu" % epilogue in returns


@pytest.mark.parametrize("code,is_return", [
    ("5a 83 c4 08 ff e2", True),                   # pop own return, clean arguments
    ("50 58 5a ff e2", True),                     # balanced push/pop before return
    ("66 50 66 58 5a ff e2", True),               # 16-bit stack slots
    ("55 89 e5 83 ec 08 c9 5a ff e2", True),      # LEAVE restores entry ESP
    ("5a 8b 64 24 2c 31 c0 ff e2", True),         # unknown ESP preserves EDX
    ("5a 75 01 90 ff e2", True),                  # agreeing control-flow paths
    ("ba 00 11 60 00 ff e2", False),              # ordinary computed jump
    ("50 5a ff e2", False),                       # popped a local, not the return
    ("5a b2 01 ff e2", False),                    # partial register overwrite
    ("5a 87 c2 ff e2", False),                    # XCHG writes both registers
    ("5a f7 e1 ff e2", False),                    # MUL implicitly overwrites EDX
    ("8b 64 24 2c 5a ff e2", False),              # unknown ESP before POP
    ("5a 75 02 31 d2 ff e2", False),              # one path loses the return
    ("75 01 50 5a ff e2", False),                 # inconsistent stack deltas
])
def test_popped_return_register_is_classified_statically(code, is_return):
    from test_translate_insns import Opts
    entry = 0x00601000
    raw = bytes.fromhex(code)
    img = synthetic_image({entry: raw}, base=0x00600000)
    img.md.detail = True
    fn = T.Function(entry, "popped_return", len(raw),
                    [img.to_insn(ci) for ci in img.md.disasm(raw, entry)])
    fn.measure(img)
    tr = T.Translator(img, {entry}, Opts())
    tr.prepare(fn)
    text = "\n".join(tr.translate(fn))
    assert ("c->eip = c->r[2]; recomp_return(c); return;" in text) == is_return
    assert ("recomp_jump(c, t_);" in text) != is_return


def test_popped_return_avoids_dispatch_when_continuation_is_also_an_entry(tmp_path, monkeypatch):
    import struct
    entry, done, helper = 0x00601000, 0x00601020, 0x00601060
    continuation = entry + 5
    caller = b"\xe8" + struct.pack("<i", done - entry - 5) + b"\x40\xc3"
    # Like an exception epilogue: POP EDX, restore ESP, CALL cleanup, JMP EDX.
    body = (b"\x5a\x8b\x64\x24\x2c\xe8"
            + struct.pack("<i", helper - done - 10) + b"\xff\xe2")
    blocks = {entry: caller, done: body, helper: b"\xc3"}
    img = synthetic_image(blocks, base=0x00600000)
    img.code_pointers = lambda *a, **kw: (set(), set())
    text = translate_entry_fixture(tmp_path, monkeypatch, img,
                                   blocks | {continuation: caller[5:]})
    done_text = text.split("void fn_%08x(X86 *c) {" % done, 1)[1].split("\n}", 1)[0]
    assert "c->eip = c->r[2]; recomp_return(c); return;" in done_text
    assert "recomp_jump" not in done_text
    table = (tmp_path / "gen/table.c").read_text()
    returns = table.split("recomp_call_returns[] = {", 1)[1].split("};", 1)[0]
    assert "0x%08xu" % continuation in returns
    jump = table.split("void recomp_jump(", 1)[1].split("void recomp_unknown_jump(", 1)[0]
    assert jump.index("if (i >= 0)") < jump.index("recomp_is_call_return(target)")


def test_ret_classifies_after_pop_without_an_interior_switch_for_plain_returns():
    from pathlib import Path
    from test_translate_insns import Opts
    entry = 0x00601000
    img = synthetic_image({entry: b"\xc3"}, base=0x00600000)
    fn = T.Function(entry, "plain_return", 1, T.parse_listing_text("00601000  RET\n"))
    fn.measure(img)
    tr = T.Translator(img, {entry}, Opts())
    tr.prepare(fn)
    text = "\n".join(tr.translate(fn))
    assert "c->eip = rd32(c->r[4]); c->r[4] += 4u; recomp_return(c); return;" in text
    assert "switch" not in text
    # Shared classification also serves the default of pushed-continuation
    # switches. A continuation that is also an entry must return, not run twice.
    header = (Path(ROOT) / "runtime/x86.h").read_text()
    helper = header.split("static inline void recomp_return(X86 *c)", 1)[1].split("\n}\n", 1)[0]
    assert helper.index("recomp_is_call_return") < helper.index("recomp_index_of")
    assert "recomp_call(c, c->eip);" in helper


def test_nested_except_join_keeps_outer_finally_in_establishing_body(tmp_path, monkeypatch):
    import struct
    entry, outer_stub, inner_stub = 0x00601000, 0x00601080, 0x00601040
    join, cleanup, epilogue = 0x00601050, 0x0060105d, 0x00601090
    helper, dispatcher, done = 0x00601100, 0x00601110, 0x00601120
    def rel(op, at, target):
        return bytes([op]) + struct.pack('<i', target - at - 5)
    code = bytearray(b'\x55\x89\xe5')
    for stub in (outer_stub, inner_stub):
        code += b'\x31\xc0\x55\x68' + struct.pack('<I', stub) + b'\x64\xff\x30\x64\x89\x20'
    code += b'\x31\xc0\x5a\x59\x59\x64\x89\x10'
    code += rel(0xe9, entry + len(code), join)
    blocks = {
        entry: bytes(code),
        inner_stub: rel(0xe9, inner_stub, dispatcher)
            + rel(0xe8, inner_stub + 5, helper)
            + rel(0xe8, inner_stub + 10, done) + b'\x90',
        join: b'\x31\xc0\x5a\x59\x59\x64\x89\x10\x68' + struct.pack('<I', epilogue),
        cleanup: b'\x40\xc3',
        outer_stub: rel(0xe9, outer_stub, dispatcher)
            + rel(0xe9, outer_stub + 5, cleanup),
        epilogue: b'\x89\xec\x5d\xc3',
        helper: b'\xc3', dispatcher: b'\xc3', done: b'\x5a\xff\xe2',
    }
    img = synthetic_image(blocks, base=0x00600000)
    img.code_pointers = lambda *a, **kw: (set(), set())
    img.plausible_immediate_target = lambda addr: False
    listings = tmp_path / 'functions'
    listings.mkdir()
    table = tmp_path / 'functions.tsv'
    rows = ['address\tname\tsize']
    img.md.detail = True
    for addr in (entry, helper, dispatcher, done):
        insns = [img.to_insn(ci) for ci in img.md.disasm(blocks[addr], addr)]
        (listings / ('%08x.asm' % addr)).write_text('\n'.join(i.raw for i in insns) + '\n')
        rows.append('%08x\tfixture_%08x\t%d' % (addr, addr, len(blocks[addr])))
    img.md.detail = False
    table.write_text('\n'.join(rows) + '\n')
    binary, curated, out = tmp_path / 'image', tmp_path / 'globals.toml', tmp_path / 'gen'
    binary.write_bytes(img.data)
    curated.write_text('')
    monkeypatch.setattr(T, 'configure', lambda cfg: None)
    monkeypatch.setattr(T.game_config, 'load', lambda path: {})
    for name, value in (('LISTINGS', listings), ('FUNCS_TSV', table), ('BINARY', binary), ('CURATED', curated)):
        monkeypatch.setattr(T, name, str(value))
    monkeypatch.setattr(T, 'EXTRA_ENTRY_POINTS', frozenset())
    monkeypatch.setattr(T, 'Image', lambda path: img)
    monkeypatch.setattr(sys, 'argv', ['translate.py', '--game', str(tmp_path), '--out', str(out), '--quiet'])
    assert T.main() == 0
    text = '\n'.join(p.read_text() for p in out.glob('chunk_*.c'))
    for stub in (outer_stub, inner_stub):
        assert "void fn_%08x(X86 *c) {\n    CALL_FN(%08x); return;" % (stub, dispatcher) in text
    # Normal execution must retain the frame until the epilogue restores ESP/EBP.
    assert 'void fn_%08x(X86 *c) { body_%08x(c, %s); }' % (cleanup, entry, T.hexlit(cleanup)) in text
    body = text.split('static void body_%08x(' % entry, 1)[1].split('void fn_%08x(' % entry, 1)[0]
    assert 'case %s: goto L_%08x;' % (T.hexlit(epilogue), epilogue) in body


@pytest.mark.parametrize("second_kind", ["inside", "next_function", "bad_decode"])
def test_span_recovers_a_chain_of_omitted_pushed_continuations(tmp_path, monkeypatch, second_kind):
    import struct
    entry, first, second, next_fn = 0x00601000, 0x00601020, 0x00601040, 0x00601060
    if second_kind == "next_function":
        second = next_fn
    blocks = {entry: b"\x55\x89\xe5\x68" + struct.pack("<I", first) + b"\xc3",
              first: b"\x68" + struct.pack("<I", second) + b"\xc3",
              second: b"\x89\xec\x5d\xc3", next_fn: b"\xc3"}
    if second_kind == "bad_decode":
        blocks[second] = b"\x0f\x0b\xc3"  # UD2 cannot be translated as a continuation.
    img = synthetic_image(blocks, base=0x00600000)
    img.code_pointers = lambda *a, **kw: (set(), set())
    img.plausible_immediate_target = lambda addr: False
    listings = tmp_path / "functions"
    listings.mkdir()
    table = tmp_path / "functions.tsv"
    rows = ["address\tname\tsize"]
    img.md.detail = True
    for addr in (entry, next_fn):
        insns = [img.to_insn(ci) for ci in img.md.disasm(blocks[addr], addr)]
        (listings / ("%08x.asm" % addr)).write_text("\n".join(i.raw for i in insns) + "\n")
        rows.append("%08x\tfixture_%08x\t%d" % (addr, addr, len(blocks[addr])))
    img.md.detail = False
    table.write_text("\n".join(rows) + "\n")
    binary, curated, out = tmp_path / "image", tmp_path / "globals.toml", tmp_path / "gen"
    binary.write_bytes(img.data)
    curated.write_text("")
    monkeypatch.setattr(T, "configure", lambda cfg: None)
    monkeypatch.setattr(T.game_config, "load", lambda path: {})
    for name, value in (("LISTINGS", listings), ("FUNCS_TSV", table), ("BINARY", binary), ("CURATED", curated)):
        monkeypatch.setattr(T, name, str(value))
    monkeypatch.setattr(T, "EXTRA_ENTRY_POINTS", frozenset())
    monkeypatch.setattr(T, "Image", lambda path: img)
    monkeypatch.setattr(sys, "argv", ["translate.py", "--game", str(tmp_path), "--out", str(out), "--quiet"])
    if second_kind == "bad_decode":
        # The listed caller must still reject a literal jump to undecodable
        # code; span recovery must not turn the bad bytes into an owned block.
        with pytest.raises(T.TranslateError, match="literal dispatch targets"):
            T.main()
        return
    assert T.main() == 0
    text = "\n".join(p.read_text() for p in out.glob("chunk_*.c"))
    marker = "static void body_%08x(" % entry
    if marker not in text:
        marker = "void fn_%08x(" % entry
    body = text.split(marker, 1)[1].split("\n}", 1)[0]
    for target in (first, second):
        expected = target == first or second_kind == "inside"
        assert ("L_%08x: ;" % target in body) == expected
        assert ("case %s: goto L_%08x;" % (T.hexlit(target), target) in body) == expected


@pytest.mark.parametrize("code,bound", [(b"\x90\xcc", 2), (b"\xb8\x01\x00\x00\x00", 4)])
def test_bounded_continuation_recovery_rejects_incomplete_code(code, bound):
    entry = 0x00601000
    img = synthetic_image({entry: code}, base=0x00600000)
    assert img.recover(entry, set(), bounds=(entry, entry + bound)) == []


def translate_entry_fixture(tmp_path, monkeypatch, img, listings_at):
    """Run discovery over a synthetic PE while preserving real scan order."""
    listings = tmp_path / "functions"
    listings.mkdir()
    rows = ["address\tname\tsize"]
    img.md.detail = True
    for addr, raw in listings_at.items():
        # A listing is one run of bytes, or (address, bytes) runs with the
        # gaps between them that Ghidra left unlisted.
        runs = raw if isinstance(raw, list) else [(addr, raw)]
        insns = [img.to_insn(ci) for at, run in runs for ci in img.md.disasm(run, at)]
        (listings / ("%08x.asm" % addr)).write_text("\n".join(i.raw for i in insns) + "\n")
        size = runs[-1][0] + len(runs[-1][1]) - addr
        rows.append("%08x\tfixture_%08x\t%d" % (addr, addr, size))
    img.md.detail = False
    table, binary, curated, out = (tmp_path / name for name in
                                  ("functions.tsv", "image", "globals.toml", "gen"))
    table.write_text("\n".join(rows) + "\n")
    binary.write_bytes(img.data)
    curated.write_text("")
    monkeypatch.setattr(T, "configure", lambda cfg: None)
    monkeypatch.setattr(T.game_config, "load", lambda path: {})
    for name, value in (("LISTINGS", listings), ("FUNCS_TSV", table),
                        ("BINARY", binary), ("CURATED", curated)):
        monkeypatch.setattr(T, name, str(value))
    monkeypatch.setattr(T, "FUNCTION_ALIGNMENT", 4)
    monkeypatch.setattr(T, "EXTRA_ENTRY_POINTS", frozenset())
    monkeypatch.setattr(T, "Image", lambda path: img)
    monkeypatch.setattr(sys, "argv", ["translate.py", "--game", str(tmp_path),
                                      "--out", str(out), "--quiet"])
    assert T.main() == 0
    return "\n".join(p.read_text() for p in out.glob("chunk_*.c"))


def test_a_branch_reached_tail_may_fall_into_a_known_entry(tmp_path, monkeypatch):
    """An exception landing pad keeps the unlisted tail its own JMP names.

    Populous: The Beginning is a Watcom image whose dispatcher __trandisp2
    ends on `JMP dword ptr [EBX]` at 00567f11 and enters landing pads a
    relocation names - 00565cc0, 00565cd1, 00565d2e. 00565cc0's tail jumps
    to 00567f18, two x87 instructions the listing never covered, which fall
    into a shared RET at 00567f1c that a relocation names as well.

    A speculative body is bounded at its listed span, so the landing pad
    cannot sweep its own jump target, and that target's recovery stops at
    the RET's candidate boundary with nothing to terminate it. Refused, the
    landing pad has a dangling target and the pruning pass withdraws it; the
    dispatcher's indirect jump then has no block to enter and the image
    aborts before its first frame. A block a branch named is not a pointer
    guess, so it may end on a fall-out into an entry already carried.
    """
    import struct
    listed, pad, next_fn, tail, shared, slot = (0x00601000, 0x00601020, 0x00601200,
                                                0x00601300, 0x00601304, 0x00601800)
    blocks = {listed: b"\xc3",
              # MOV EAX,0x2a; JMP tail - out of this span, so bounded
              # recovery of the pad leaves the target to discovery.
              pad: b"\xb8\x2a\x00\x00\x00\xe9" + struct.pack("<i", tail - pad - 10),
              next_fn: b"\xc3",
              tail: b"\x40\x40\x40\x40",           # INC EAX, falling into the RET
              shared: b"\xc3",
              slot: struct.pack("<II", pad, shared)}
    # INT3 between them, the way a compiler pads: nothing sweeps the filler.
    placed = dict([(0x00600000, b"\xcc" * 0x2000)] + sorted(blocks.items()))
    img = synthetic_image(placed, base=0x00600000)
    img.relocated_pointers = lambda: {pad: slot, shared: slot + 4}
    text = translate_entry_fixture(tmp_path, monkeypatch, img,
                                   {a: blocks[a] for a in (listed, next_fn)})
    assert "void fn_%08x(" % pad in text          # the landing pad survives
    assert "void fn_%08x(" % tail in text         # with the tail it jumps to
    assert "void fn_%08x(" % shared in text
    assert "recomp_unknown_call(c, %s)" % T.hexlit(tail) not in text


def test_short_string_does_not_hide_relocated_stub(tmp_path, monkeypatch):
    """A one-character literal and its adjacent method have equal evidence."""
    import struct
    entry, string, method, next_fn, slot = (0x00601000, 0x00601028, 0x0060102c,
                                           0x00601040, 0x00601800)
    blocks = {entry: b"\x68" + struct.pack("<I", string) + b"\x58\xc3",
              string: ".\0".encode("utf-16le"), method: b"\x33\xc0\xc3\x90",
              next_fn: b"\xc3", slot: struct.pack("<I", method)}
    img = synthetic_image(blocks, base=0x00600000)
    img.relocated_pointers = lambda: {string: entry + 1, method: slot}
    text = translate_entry_fixture(tmp_path, monkeypatch, img,
                                   {a: blocks[a] for a in (entry, next_fn)})
    assert "void fn_%08x(" % method in text


def test_bare_scan_hit_inside_relocated_instruction_is_not_a_boundary(tmp_path, monkeypatch):
    """An unrelocated dword coincidence cannot split a stronger decoded opcode."""
    import struct
    entry, method, guess, next_fn, slot = (0x00601000, 0x00601020, 0x00601024,
                                          0x00601100, 0x00601800)
    blocks = {entry: b"\xc3", method: b"\xb8\x90\x90\x90\x90\xc3",
              next_fn: b"\xc3", slot: struct.pack("<II", method, guess)}
    img = synthetic_image(blocks, base=0x00600000)
    img.relocated_pointers = lambda: {method: slot}
    text = translate_entry_fixture(tmp_path, monkeypatch, img,
                                   {a: blocks[a] for a in (entry, next_fn)})
    assert "void fn_%08x(" % method in text
    assert "void fn_%08x(" % guess not in text


@pytest.mark.parametrize("through_helper", [False, True])
def test_relocated_method_callee_is_not_split_by_a_bare_pointer(tmp_path, monkeypatch, through_helper):
    """A real direct callee is named by code, even without its own relocation.

    A bare dword points into the callee's MOV immediate. Treating it as a
    function boundary rejects the callee and then withdraws its caller too.
    """
    import struct
    entry, method, callee, guess, next_fn, slot = (
        0x00601000, 0x00601020, 0x00601040, 0x00601044, 0x00601100, 0x00601800)
    blocks = {entry: b"\xc3",
              method: b"\xe8" + struct.pack("<i", callee - method - 5) + b"\xc3",
              callee: b"\xb8\x90\x90\x90\x90\xc3", next_fn: b"\xc3",
              slot: struct.pack("<II", method, guess)}
    if through_helper:
        helper = 0x00601030
        blocks[method] = b"\xe8" + struct.pack("<i", helper - method - 5) + b"\xc3"
        blocks[helper] = b"\xe8" + struct.pack("<i", callee - helper - 5) + b"\xc3"
    img = synthetic_image(blocks, base=0x00600000)
    img.relocated_pointers = lambda: {method: slot}
    text = translate_entry_fixture(tmp_path, monkeypatch, img,
                                   {a: blocks[a] for a in (entry, next_fn)})
    assert "void fn_%08x(" % method in text
    assert "void fn_%08x(" % callee in text
    assert "void fn_%08x(" % guess not in text


def test_pointer_callee_with_an_unresolved_call_remains_prunable(tmp_path, monkeypatch):
    """Following CALL evidence must not promote guesses to structural code."""
    import struct
    entry, method, callee, next_fn, slot = (
        0x00601000, 0x00601020, 0x00601040, 0x00601100, 0x00601800)
    blocks = {entry: b"\xc3",
              method: b"\xe8" + struct.pack("<i", callee - method - 5) + b"\xc3",
              callee: b"\xe8" + struct.pack("<i", 0x00700000 - callee - 5) + b"\xc3",
              next_fn: b"\xc3", slot: struct.pack("<I", method)}
    img = synthetic_image(blocks, base=0x00600000)
    img.relocated_pointers = lambda: {method: slot}
    text = translate_entry_fixture(tmp_path, monkeypatch, img,
                                   {a: blocks[a] for a in (entry, next_fn)})
    assert "void fn_%08x(" % method not in text
    assert "void fn_%08x(" % callee not in text


def test_bare_pointer_to_relocated_routines_ret_is_an_alias(tmp_path, monkeypatch):
    """A weaker RET hit cannot withdraw the stronger method preceding it."""
    import struct
    entry, method, tail, next_fn, slot = (0x00601000, 0x00601020, 0x00601025,
                                         0x00601100, 0x00601800)
    blocks = {entry: b"\xc3", method: b"\xb8\x2a\0\0\0\xc3",
              next_fn: b"\xc3", slot: struct.pack("<II", method, tail)}
    img = synthetic_image(blocks, base=0x00600000)
    img.relocated_pointers = lambda: {method: slot}
    text = translate_entry_fixture(tmp_path, monkeypatch, img,
                                   {a: blocks[a] for a in (entry, next_fn)})
    assert "void fn_%08x(" % method in text
    assert "void fn_%08x(X86 *c) { body_%08x(c, %s); }" % (tail, method, T.hexlit(tail)) in text


def test_speculative_body_keeps_branches_over_cleanup_stub_and_pushed_alias(tmp_path, monkeypatch):
    """A branch skips a stub; its path falls into the PUSH-named epilogue."""
    import struct
    entry, method, stub, alternate, epilogue, next_fn, slot = (
        0x00601000, 0x00601020, 0x00601030, 0x00601038,
        0x0060103d, 0x00601100, 0x00601800)
    blocks = {
        entry: b"\xc3",
        method: b"\x85\xc0\x74" + bytes([alternate - method - 4])
                + b"\x68" + struct.pack("<I", epilogue) + b"\x90\xc3",
        stub: b"\xe9" + struct.pack("<i", next_fn - stub - 5),
        alternate: b"\xb8\x2a\0\0\0", epilogue: b"\xc3",
        next_fn: b"\xc3", slot: struct.pack("<III", method, stub, epilogue),
    }
    img = synthetic_image(blocks, base=0x00600000)
    img.relocated_pointers = lambda: {method: slot, stub: slot + 4, epilogue: slot + 8}
    text = translate_entry_fixture(tmp_path, monkeypatch, img,
                                   {a: blocks[a] for a in (entry, next_fn)})
    assert "void fn_%08x(" % method in text
    marker = ("static void body_%08x(" if "static void body_%08x(" % method in text else
              "void fn_%08x(") % method
    body = text.split(marker, 1)[1].split("\n}", 1)[0]
    assert "L_%08x:" % alternate in body
    assert "L_%08x:" % epilogue in body
    assert "case %s: goto L_%08x;" % (T.hexlit(epilogue), epilogue) in body
    assert "L_%08x:" % stub not in body
    assert "void fn_%08x(" % stub in text


@pytest.mark.parametrize("terminated", [False, True])
def test_equal_rank_candidates_bound_speculative_sweeps(tmp_path, monkeypatch, terminated):
    """Boundary handling must not depend on stronger evidence or a text guard."""
    import struct
    entry, guess, method, next_fn, slot = (0x00601000, 0x00601020, 0x00601030,
                                          0x00601100, 0x00601800)
    # The JMP otherwise pulls the method into the prefix's recursive sweep.
    # NOP fallthrough at exactly the boundary has no valid terminating edge.
    prefix = b"\xeb\x0e" if terminated else b"\x90" * 16
    blocks = {entry: b"\xba" + struct.pack("<I", guess) + b"\xc3", guess: prefix,
              method: b"\x33\xc0\xc3", next_fn: b"\xc3",
              slot: struct.pack("<II", guess, method)}
    img = synthetic_image(blocks, base=0x00600000)
    img.relocated_pointers = lambda: {guess: slot, method: slot + 4}
    text = translate_entry_fixture(tmp_path, monkeypatch, img,
                                   {a: blocks[a] for a in (entry, next_fn)})
    assert "void fn_%08x(X86 *c) {\n" % method in text
    assert ("void fn_%08x(" % guess in text) == terminated
    if terminated:
        body = text.split("void fn_%08x(X86 *c) {" % guess)[1].split("\n}", 1)[0]
        assert "XOR EAX,EAX" not in body
        assert "CALL_FN(%08x)" % method in body


@pytest.mark.parametrize("fault", [None, "element_size", "refcount", "zero_length",
                                    "embedded_zero", "terminator", "out_of_image"])
def test_utf16_constant_header_requires_complete_layout(fault):
    """UnicodeString has codepage/element words, refcount, length, then units."""
    import struct
    target = 0x00601020
    header = struct.pack("<HHII", 1200, 1 if fault == "element_size" else 2,
                         1 if fault == "refcount" else 0xffffffff,
                         0 if fault == "zero_length" else
                         0xffffffff if fault == "out_of_image" else 1)
    value = (b"\0\0" if fault == "embedded_zero" else b".\0")
    value += b"x\0" if fault == "terminator" else b"\0\0"
    img = synthetic_image({target - 12: header + value}, base=0x00600000)
    assert img.is_utf16_constant(target) == (fault is None)


def test_one_character_utf16_constant_is_not_a_relocated_entry(tmp_path, monkeypatch):
    import struct
    entry, string, next_fn, slot = 0x00601000, 0x00601028, 0x00601100, 0x00601800
    # Without the header guard these bytes decode cleanly through the RET.
    blocks = {entry: b"\xc3", string - 12: struct.pack("<HHII", 1200, 2, 0xffffffff, 1),
              string: b".\0\0\0\xc0\xc3", next_fn: b"\xc3",
              slot: struct.pack("<I", string)}
    img = synthetic_image(blocks, base=0x00600000)
    img.relocated_pointers = lambda: {string: slot}
    text = translate_entry_fixture(tmp_path, monkeypatch, img,
                                   {a: blocks[a] for a in (entry, next_fn)})
    assert "void fn_%08x(" % string not in text


@pytest.mark.parametrize("relocated_string", [False, True])
def test_wide_string_prefix_does_not_hide_relocated_method(tmp_path, monkeypatch, relocated_string):
    """A MOV-immediate string guess must not hide a relocated vtable method."""
    import struct
    entry, string, method, next_fn, holder = (0x00601000, 0x0060101c, 0x00601030,
                                             0x00601100, 0x00601800)
    blocks = {entry: b"\xba" + struct.pack("<I", string) + b"\xc3",
              string: "MDICLIENT\0".encode("utf-16le"),
              method: b"\x55\x8b\xec\xb8\x2a\x00\x00\x00\x5d\xc3",
              next_fn: b"\xc3", holder: struct.pack("<I", method)}
    img = synthetic_image(blocks, base=0x00600000)
    img.relocated_pointers = lambda: ({method: holder, string: entry + 1}
                                     if relocated_string else {method: holder})
    text = translate_entry_fixture(tmp_path, monkeypatch, img,
                                   {a: blocks[a] for a in (entry, next_fn)})
    assert "void fn_%08x(" % method in text
    assert "void fn_%08x(" % string not in text


@pytest.mark.parametrize("crossing", [False, True])
@pytest.mark.parametrize("protected_caller", [False, True])
def test_protected_call_target_truncates_earlier_scan_guess(
        tmp_path, monkeypatch, crossing, protected_caller):
    """The stronger CALL edge arrives after a weak body already swept its target.

    The prefix is NOPs, not UTF-16: only evidence ordering can fix this case.
    Both a partial instruction and unterminated NOP fallthrough withdraw it.
    """
    import struct
    entry, guess, method, caller, next_fn = (0x00601000, 0x00601020, 0x00601030,
                                            0x00601100, 0x00601200)
    blocks = {entry: b"\xba" + struct.pack("<I", guess) + b"\xc3",
              guess: b"\x90" * 15 + (b"\x00" if crossing else b"\x90"),
              method: b"\x55\x8b\xec\xb8\x2a\x00\x00\x00\x5d\xc3",
              caller: b"\xe8" + struct.pack("<i", method - caller - 5) + b"\xc3",
              next_fn: b"\xc3"}
    img = synthetic_image(blocks, base=0x00600000)
    # Static initializer entries have structural evidence. They are seeded in
    # the scan round, and their CALLs are followed on the next round.
    img.initterm_tables = lambda parsed: ([], {caller} if protected_caller else set())
    img.code_pointers = lambda *args, **kwargs: (set() if protected_caller else {caller}, set())
    text = translate_entry_fixture(tmp_path, monkeypatch, img,
                                   {a: blocks[a] for a in (entry, next_fn)})
    if not protected_caller:
        assert "void fn_%08x(" % guess in text
        assert ("IN AL,DX" in text) == crossing
        return  # a direct edge from another guess earns no stronger rank
    assert "void fn_%08x(X86 *c) {\n" % method in text
    assert "void fn_%08x(" % guess not in text  # NOP fallthrough is not a terminator.
    assert "IN AL,DX" not in text


@pytest.mark.parametrize("evidence", ["speculative", "branch", "reloc"])
def test_utf16_run_filter_applies_only_to_speculative_entries(tmp_path, monkeypatch, evidence):
    """Four printable UTF-16 pairs inside the first 16 bytes reject only guesses."""
    import struct
    entry, target, next_fn = 0x00601000, 0x00601020, 0x00601100
    # At instruction boundaries these are legal instructions; a direct CALL
    # is explicit evidence and must outrank the cheap text heuristic.
    raw = b"\x90\x90" + "ABCD".encode("utf-16le") + b"\x90\xc3"
    edge = (b"\xe8" + struct.pack("<i", target - entry - 5) if evidence == "branch" else
            b"\xba" + struct.pack("<I", target))
    blocks = {entry: edge + b"\xc3", target: raw, next_fn: b"\xc3"}
    img = synthetic_image(blocks, base=0x00600000)
    img.code_pointers = lambda *args, **kwargs: (set(), set())
    if evidence == "reloc":
        img.relocated_pointers = lambda: {target: entry + 1}
    text = translate_entry_fixture(tmp_path, monkeypatch, img,
                                   {a: blocks[a] for a in (entry, next_fn)})
    assert ("void fn_%08x(" % target in text) == (evidence == "branch")


@pytest.mark.parametrize("crossing", [False, True])
@pytest.mark.parametrize("prefix_evidence", ["bare", "seed", "listed"])
@pytest.mark.parametrize("guess_first", [False, True])
def test_relocated_method_outranks_bare_guess_but_not_listing(
        tmp_path, monkeypatch, crossing, prefix_evidence, guess_first):
    """Relocations outrank bare guesses in either scan order, below seeds/listings."""
    import struct
    entry, guess, method, next_fn, slot = (0x00601000, 0x00601020, 0x00601030,
                                          0x00601100, 0x00601800)
    prefix = b"\x90" * 15 + (b"\x00" if crossing else b"\x90")
    code = b"\x55\x8b\xec\xb8\x2a\x00\x00\x00\x5d\xc3"
    # Without the MOV immediate, only the data scan sees the prefix, after
    # the relocated method. The PE bytes always match the synthetic listing.
    blocks = {entry: (b"\xba" + struct.pack("<I", guess) if guess_first else b"") + b"\xc3",
              guess: prefix + code, next_fn: b"\xc3", slot: struct.pack("<I", method),
              slot + 4: struct.pack("<I", guess)}
    img = synthetic_image(blocks, base=0x00600000)
    img.relocated_pointers = lambda: {method: slot}
    listings = {a: blocks[a] for a in (entry, next_fn)}
    if prefix_evidence == "listed":
        listings[guess] = blocks[guess]
    elif prefix_evidence == "seed":
        img.initterm_tables = lambda parsed: ([], {guess})
    text = translate_entry_fixture(tmp_path, monkeypatch, img, listings)
    if prefix_evidence != "bare":
        assert ("IN AL,DX" in text) == crossing
        if crossing:
            assert "void fn_%08x(" % method not in text
        else:
            assert "void fn_%08x(X86 *c) { body_%08x" % (method, guess) in text
    else:
        assert "void fn_%08x(X86 *c) {\n" % method in text
        assert "IN AL,DX" not in text
        assert "void fn_%08x(" % guess not in text  # Neither NOP prefix terminates.


def test_relocated_alias_keeps_listed_instruction_evidence(tmp_path, monkeypatch):
    """Text-like bytes at a proven instruction boundary are already code."""
    import struct
    entry, target, slot = 0x00601000, 0x00601008, 0x00601800
    raw = b"\x90" * 8 + "ABCD".encode("utf-16le") + b"\x90\xc3"
    img = synthetic_image({entry: raw, slot: struct.pack("<I", target)}, base=0x00600000)
    img.relocated_pointers = lambda: {target: slot}
    text = translate_entry_fixture(tmp_path, monkeypatch, img, {entry: raw})
    assert "void fn_%08x(X86 *c) { body_%08x" % (target, entry) in text


def test_truncated_guess_drops_its_old_jump_table_site(tmp_path, monkeypatch):
    """A table moved to a stronger owner must not remain a gap on its old prefix."""
    import struct
    entry, guess, method, first, second, default, table, slot = (
        0x00601000, 0x00601020, 0x00601030, 0x00601050, 0x00601060,
        0x00601070, 0x00601900, 0x00601800)
    blocks = {
        entry: b"\xba" + struct.pack("<I", guess) + b"\xc3",
        guess: b"\x90" * 16,
        method: b"\x83\xf8\x01\x77" + bytes([default - method - 5])
                + b"\xff\x24\x85" + struct.pack("<I", table),
        first: b"\xb8\x01\0\0\0\xc3", second: b"\xb8\x02\0\0\0\xc3",
        default: b"\xc3", table: struct.pack("<II", first, second),
        slot: struct.pack("<I", method),
    }
    img = synthetic_image(blocks, base=0x00600000)
    img.relocated_pointers = lambda: {method: slot}
    text = translate_entry_fixture(tmp_path, monkeypatch, img, {entry: blocks[entry]})
    assert "void fn_%08x(" % method in text
    assert "case 0x%xu:" % first in text
    assert "case 0x%xu:" % second in text


def test_final_table_gate_still_rejects_an_undecoded_live_site(tmp_path, monkeypatch):
    """Clearing discovery records must not hide a switch still in a live body."""
    import struct
    entry, table = 0x00601000, 0x00601900
    raw = b"\xff\x24\x85" + struct.pack("<I", table)
    img = synthetic_image({entry: raw, table: b"\0" * 4}, base=0x00600000)
    with pytest.raises(T.TranslateError, match="1 table sites decoded nothing"):
        translate_entry_fixture(tmp_path, monkeypatch, img, {entry: raw})


@pytest.mark.parametrize("relocated", [False, True])
def test_method_with_zero_local_pushes_is_an_entry(tmp_path, monkeypatch, relocated):
    """PUSH 0 reserves Delphi locals; its 6a 00 bytes are not a text run."""
    import struct
    entry, method, next_fn, slot = 0x00601000, 0x00601020, 0x00601100, 0x00601800
    blocks = {
        entry: b"\xc3",
        method: b"\x55\x8b\xec" + b"\x6a\x00" * 7 + b"\x89\xec\x5d\xc3",
        next_fn: b"\xc3", slot: struct.pack("<I", method),
    }
    img = synthetic_image(blocks, base=0x00600000)
    if relocated:
        img.relocated_pointers = lambda: {method: slot}
    text = translate_entry_fixture(tmp_path, monkeypatch, img,
                                   {a: blocks[a] for a in (entry, next_fn)})
    assert "void fn_%08x(" % method in text


@pytest.mark.parametrize("location", ["inside", "outside", "bad_decode", "chain"])
def test_pruned_cleanup_alias_falls_back_to_listed_span(tmp_path, monkeypatch, location):
    """Prune both weak owners, then retain the cleanup through its listed span.

    A bad prefix covers a callee. Losing that callee prunes the speculative
    caller too, including its cleanup alias; the protected SEH landing survives.
    """
    import struct
    anchor, guess, stub, epilogue = 0x00601000, 0x00601020, 0x00601080, 0x00601090
    prefix, method, dispatcher = 0x006011f0, 0x00601200, 0x00601400
    if location == "outside":
        anchor = 0x00601070  # the cleanup lies before every listed span
    setup = b"\x55\x8b\xec\x31\xc0\x55\x68" + struct.pack("<I", stub) + b"\x64\xff\x30\x64\x89\x20"
    call = guess + len(setup)
    setup += b"\xe8" + struct.pack("<i", method - call - 5)
    setup += b"\x5a\x59\x59\x64\x89\x10\x68" + struct.pack("<I", epilogue)
    cleanup = guess + len(setup)
    setup += (b"\x68" + struct.pack("<I", epilogue) + b"\x90\xc3" if location == "chain" else
              b"\x0f\x0b\xc3" if location == "bad_decode" else b"\x90\xc3")
    blocks = {
        anchor: b"\xc3", guess: setup,
        stub: b"\xe9" + struct.pack("<i", dispatcher - stub - 5)
            + b"\xe9" + struct.pack("<i", cleanup - stub - 10),
        epilogue: b"\x8b\xe5\x5d\xc3",
        prefix: b"\x0f\x85" + struct.pack("<i", 0x00700000 - prefix - 6) + b"\x90" * 10,
        method: b"\xb8\x2a\0\0\0\xc3", dispatcher: b"\xc3",
    }
    if location == "chain":
        blocks[epilogue] = b"\x68" + struct.pack("<I", epilogue + 0x10) + b"\x90\xc3"
        blocks[epilogue + 0x10] = b"\x8b\xe5\x5d\xc3"
    img = synthetic_image(blocks, base=0x00600000)
    img.code_pointers = lambda *a, **kw: ({guess, prefix}, set())
    img.plausible_immediate_target = lambda addr: False
    listings = {a: blocks[a] for a in (anchor, dispatcher)}
    if location == "bad_decode":
        # The malformed cleanup prevents discovery of the weak caller. Keep
        # the landing explicitly listed so its dispatch must still fail.
        listings[stub + 5] = blocks[stub][5:]
    if location in ("outside", "bad_decode"):
        with pytest.raises(T.TranslateError, match="literal dispatch targets are not entry points"):
            translate_entry_fixture(tmp_path, monkeypatch, img, listings)
        return
    text = translate_entry_fixture(tmp_path, monkeypatch, img, listings)
    assert "void fn_%08x(X86 *c) { body_%08x(c, %s); }" % (cleanup, anchor, T.hexlit(cleanup)) in text
    assert "void fn_%08x(" % guess not in text
    assert "void fn_%08x(" % prefix not in text
    if location == "chain":
        body = text.split("static void body_%08x(" % anchor, 1)[1].split("void fn_%08x(" % anchor, 1)[0]
        for target in (epilogue, epilogue + 0x10):
            assert "L_%08x:" % target in body
            assert "case %s: goto L_%08x;" % (T.hexlit(target), target) in body


@pytest.mark.parametrize("with_seh", [False, True])
@pytest.mark.parametrize("relocated_literal", [False, True])
def test_pushed_data_fragment_cannot_hide_later_called_method(tmp_path, monkeypatch, relocated_literal, with_seh):
    """A span PUSH argument is still a guess until its whole sweep is code.

    The listed owner pushes a short, headerless string. Its speculative
    continuation sweep crosses an omitted method, only later named by a
    direct CALL from a relocated initializer. The literal fragment must be
    withdrawn without disturbing the owner's original instructions.
    """
    import struct
    entry, literal, method, next_fn, callback, slot = (
        0x00601000, 0x0060102c, 0x00601030, 0x00601100, 0x00601200, 0x00601800)
    blocks = {
        entry: b"\x68" + struct.pack("<I", literal) + b"\x58\xc3",
        literal: b"\x5c\x00\x00\x00",
        method: b"\x55\x8b\xec\xb8\x2a\0\0\0\x5d\xc3",
        next_fn: b"\xc3",
        callback: b"\xe8" + struct.pack("<i", method - callback - 5) + b"\xc3",
        slot: struct.pack("<I", callback),
    }
    if with_seh:
        stub, epilogue = method + 0x30, method + 0x40
        code = (b"\x55\x8b\xec\x33\xc0\x55\x68" + struct.pack("<I", stub)
                + b"\x64\xff\x30\x64\x89\x20\x33\xc0\x5a\x59\x59\x64\x89\x10\x68"
                + struct.pack("<I", epilogue))
        cleanup = method + len(code)
        blocks[method] = code + b"\x90\xc3"
        blocks[stub] = b"\xe9" + struct.pack("<i", next_fn - stub - 5) + b"\xe9" + struct.pack("<i", cleanup - stub - 10)
        blocks[epilogue] = b"\x5d\xc3"
    img = synthetic_image(blocks, base=0x00600000)
    img.relocated_pointers = lambda: ({callback: slot, literal: entry + 1}
                                     if relocated_literal else {callback: slot})
    img.code_pointers = lambda *args, **kwargs: (set(), set())
    text = translate_entry_fixture(tmp_path, monkeypatch, img,
                                   {a: blocks[a] for a in (entry, next_fn)})
    assert "void fn_%08x(" % callback in text
    assert "void fn_%08x(" % method in text
    assert "IN AL,DX" not in text
    assert "void fn_%08x(" % literal not in text
    assert "goto L_%08x" % literal not in text
    assert "CALL_FN(%08x)" % method in text


def test_span_fragment_keeps_its_pushed_interior_alias(tmp_path, monkeypatch):
    """An independently scanned epilogue is still the fragment's own RET target."""
    import struct
    entry, fragment, epilogue, next_fn, slot = (
        0x00601000, 0x00601020, 0x00601030, 0x00601100, 0x00601800)
    blocks = {
        entry: b"\x68" + struct.pack("<I", fragment) + b"\xc3",
        fragment: b"\x68" + struct.pack("<I", epilogue)
                  + b"\x85\xc0\x74" + bytes([epilogue - fragment - 9]) + b"\xc3",
        epilogue: b"\xb8\x2a\0\0\0\xc3", next_fn: b"\xc3",
        slot: struct.pack("<I", epilogue),
    }
    img = synthetic_image(blocks, base=0x00600000)
    img.relocated_pointers = lambda: {epilogue: slot}
    text = translate_entry_fixture(tmp_path, monkeypatch, img,
                                   {a: blocks[a] for a in (entry, next_fn)})
    assert "void fn_%08x(X86 *c) { body_%08x(c, %s); }" % (
        epilogue, entry, T.hexlit(epilogue)) in text
    body = text.split("static void body_%08x(" % entry, 1)[1].split("void fn_%08x(" % entry, 1)[0]
    assert "case %s: goto L_%08x;" % (T.hexlit(epilogue), epilogue) in body
    assert "L_%08x:" % epilogue in body

def test_auxiliary_module_emits_prefixed_tables_that_self_register(tmp_path, monkeypatch):
    """A module translation (`--module`) keeps the main image's dispatch entry
    points and registers its own tables with the runtime instead."""
    import re
    import struct
    entry, callee = 0x10001000, 0x10001080
    code = (b"\xb8" + struct.pack("<I", callee) + b"\xff\xd0\xc3")
    img = synthetic_image({entry: code, callee: b"\xc3"}, base=0x10000000)
    img.code_pointers = lambda *args, **kwargs: (set(), set())
    listings = tmp_path / "functions"
    listings.mkdir()
    (listings / ("%08x.asm" % entry)).write_text(
        "10001000  MOV EAX,0x10001080\n10001005  CALL EAX\n10001007  RET\n")
    (listings / ("%08x.asm" % callee)).write_text("10001080  RET\n")
    table = tmp_path / "functions.tsv"
    table.write_text("address\tname\tsize\n10001000\tStartupLibrary\t8\n10001080\tcallee\t1\n")
    binary, curated = tmp_path / "image", tmp_path / "globals.toml"
    binary.write_bytes(img.data)
    curated.write_text("")
    out = tmp_path / "gen"
    monkeypatch.setattr(T, "configure", lambda cfg: None)
    monkeypatch.setattr(T.game_config, "load", lambda path: {})
    for name, value in (("LISTINGS", listings), ("FUNCS_TSV", table),
                        ("BINARY", binary), ("CURATED", curated)):
        monkeypatch.setattr(T, name, str(value))
    monkeypatch.setattr(T, "EXTRA_ENTRY_POINTS", frozenset())
    monkeypatch.setattr(T, "Image", lambda path: img)
    monkeypatch.setattr(T, "SYMBOL_PREFIX", "recomp_blit_")
    monkeypatch.setattr(T, "AUX_MODULE", {"key": "blit", "name": "Blit_p6.dll", "base": 0x10000000, "size": 0x1000})
    monkeypatch.setattr(sys, "argv", ["translate.py", "--game", str(tmp_path),
                                     "--out", str(out), "--quiet"])
    assert T.main() == 0
    text = (out / "table.c").read_text()
    header = (out / "funcs.h").read_text()
    assert "const uint32_t recomp_blit_func_addrs[] = {" in text
    assert "recomp_blit_hooked[" in text and "recomp_blit_hook_ptrs[]" in text
    assert "recomp_blit_call_returns[] = {" in text
    assert "recomp_module_register(&recomp_blit_module)" in text
    assert '"Blit_p6.dll", 0x10000000u, 0x10001000u' in text
    for main_only in ("void recomp_call(", "void recomp_jump(", "int recomp_is_call_return(",
                      "int recomp_thunk_target_kind(", "recomp_override_hash", "const char *recomp_profile_name("):
        assert main_only not in text
    assert "recomp_blit_func_addrs[i_]" in header and "recomp_blit_hooked[i_]" in header
    assert re.search(r"recomp_func_addrs\b", header) is None


class _Bytes(object):
    """An image that occupies 0x00400000..0x00401000 and nothing else."""
    base = 0x00400000
    end = 0x00401000
    size = 0x1000
    md = None

    def insn_end(self, addr, mnem):
        return None

    def rd32(self, va):
        return None

    def rd8(self, va):
        return None

    def is_exec(self, va):
        return self.base <= va < self.end

    def relocated_pointers(self):
        return set()


class _Opts(object):
    eager_flags = False
    allow_unmodelled = None


def _translator(reason="padding"):
    opts = _Opts()
    opts.allow_unmodelled = reason
    return T.Translator(_Bytes(), set(), opts)


def test_a_call_outside_the_image_is_unmodelled_under_the_switch():
    """A listing that decodes padding as code invents calls to nowhere. With
    the switch, such a call is refused where the instruction is, so it becomes
    a trap there instead of a dispatch target reported from far away."""
    tr = _translator()
    with pytest.raises(T.TranslateError) as e:
        tr.reject_offimage_call(0x8F28759A)
    assert "outside the image" in str(e.value)
    with pytest.raises(T.TranslateError):
        tr.reject_offimage_call(0x00401000)  # one past the end


def test_a_call_inside_the_image_or_to_a_shim_is_allowed():
    tr = _translator()
    tr.reject_offimage_call(0x00400000)
    tr.reject_offimage_call(0x00400fff)
    tr.reject_offimage_call(T.GUEST_SHIM_BASE)
    tr.reject_offimage_call(T.INTRINSIC_SETJMP)


def test_without_the_switch_the_dangling_check_keeps_it():
    """The stricter reading stays the default: nothing is swallowed unless
    the build asked for the tolerance by name."""
    tr = T.Translator(_Bytes(), set(), _Opts())
    tr.reject_offimage_call(0x8F28759A)


def test_a_translator_with_no_bytes_rejects_nothing():
    """The unit tests build translators over an empty image; the check has
    nothing to say about a program whose extent is not known."""
    class Empty(_Bytes):
        base = 0
        end = 0
    opts = _Opts()
    opts.allow_unmodelled = "padding"
    tr = T.Translator(Empty(), set(), opts)
    tr.reject_offimage_call(0x8F28759A)


def test_a_dangling_target_becomes_a_trap_under_the_switch():
    """The last shape of the listing defect: after recovery has settled, a
    literal target no instruction boundary agrees with. With the switch it
    becomes the trap a withdrawn block gets, at the site that names it."""
    body = [
        "void fn_00410170(X86 *c) {",
        "c->eip = 0x004102f0u; recomp_jump(c, 0x004103cfu); return;",
        "CALL_FN(00410370);",
        "}",
    ]
    out = T.retarget_withdrawn(body, {0x004103CF})
    assert out[1].startswith("recomp_unknown_call(c, 0x004103cfu); return;")
    assert out[2] == "CALL_FN(00410370);"   # a real entry is untouched


def test_a_truncated_listing_grows_into_its_pushed_continuation(tmp_path, monkeypatch):
    """Delphi leaves a finally block with PUSH continuation; POP EAX; JMP EAX.
    When the listing stops before the continuation, nothing names it: the
    jump dispatches on a variable, so it is never a dangling literal. The body
    must grow into it anyway, or the jump reaches no translated code at run
    time."""
    import struct
    fn, cont = 0x00401000, 0x00401020
    listed = b"\x55\x8b\xec" + b"\x68" + struct.pack("<I", cont) + b"\x58\xff\xe0"
    blocks = {fn: listed, cont: b"\xb8\x01\x00\x00\x00\x5d\xc3"}
    img = synthetic_image(blocks)
    img.code_pointers = lambda *a, **kw: (set(), set())
    img.plausible_immediate_target = lambda addr: False
    text = translate_entry_fixture(tmp_path, monkeypatch, img, {fn: listed})
    assert "L_00401020:" in text, "the continuation was not decoded into the body"
    assert ("case 0x401020u: goto L_00401020;" in text
            or "case 0x00401020u: goto L_00401020;" in text)
    # And it is a block entry, so a second body that shares this code and
    # executes the same JMP reaches it through recomp_jump.
    assert "void fn_00401020(X86 *c) { body_00401000(c, %s); }" % T.hexlit(cont) in text
def test_a_callee_that_returns_is_not_taken_as_never_returning():
    """Ghidra ends a C++ catch funclet's listing on the call before its
    rethrow, so ending a listing is not proof the callee never returns: a
    callee whose own listing returns keeps returning.  The throw helper
    returns too, but only after RaiseException, which does not come back."""
    free_node, throw, funclet, thrower = 0x00401000, 0x00401100, 0x00401200, 0x00401300
    listings = {
        free_node: "00401000  MOV ECX,dword ptr [ESP + 0x4]\n00401004  RET 0x8\n",
        throw: "00401100  CALL dword ptr [0x008901ac]\n00401106  RET 0x8\n",
        funclet: "00401200  PUSH 0x0\n00401202  CALL 0x00401000\n",
        thrower: "00401300  PUSH 0x0\n00401302  CALL 0x00401100\n",
    }
    parsed = [T.Function(a, "f%x" % a, 8, T.parse_listing_text(text)) for a, text in listings.items()]
    found = T.noreturn_callees_from(parsed, {0x008901ac: "RaiseException"})
    assert found == {throw}


def test_padding_after_a_cut_call_keeps_the_callee_never_returning():
    """`longjmp` has a RET on a path the call never takes; the INT3 padding
    after the call says the compiler expected nothing to come back."""
    jumper, fn = 0x00401000, 0x00401100
    rel = (jumper - (fn + 5)) & 0xFFFFFFFF
    img = synthetic_image({jumper: b"\xc3", fn: b"\xe8" + rel.to_bytes(4, "little") + b"\xcc\xcc"})
    parsed = [T.Function(jumper, "jumper", 1, T.parse_listing_text("00401000  RET\n")),
              T.Function(fn, "caller", 5, T.parse_listing_text("00401100  CALL 0x00401000\n"))]
    assert T.noreturn_callees_from(parsed, {}) == set()
    assert T.noreturn_callees_from(parsed, {}, img) == {jumper}


def test_an_operand_redirect_rewrites_one_instruction():
    listing = ("00401000  FLD float ptr [0x008970f0]\n"
               "00401006  FMUL float ptr [0x008970f0]\n")
    fn = T.Function(0x00401000, "f", 12, T.parse_listing_text(listing))
    T.apply_operand_redirects([fn], {0x00401006: (0x008970f0, 0x00a37f10)})
    assert fn.insns[0].ops == ["float ptr [0x008970f0]"]
    assert fn.insns[1].ops == ["float ptr [0x00a37f10]"]
    with pytest.raises(T.TranslateError):
        T.apply_operand_redirects([fn], {0x00401000: (0x00123456, 0x00a37f10)})


def test_an_instruction_patch_replaces_the_text_and_keeps_the_address():
    listing = ("00401000  CMP DL,byte ptr [EBP + 0x34]\n"
               "00401003  JNZ 0x00401010\n")
    saved = dict(T.INSTRUCTION_PATCHES)
    T.INSTRUCTION_PATCHES.clear()
    T.INSTRUCTION_PATCHES[0x00401000] = "CMP DL,0x1"
    try:
        fn = T.Function(0x00401000, "f", 5, T.parse_listing_text(listing))
    finally:
        T.INSTRUCTION_PATCHES.clear()
        T.INSTRUCTION_PATCHES.update(saved)
    assert fn.insns[0].addr == 0x00401000
    assert (fn.insns[0].mnem, fn.insns[0].ops) == ("CMP", ["DL", "0x1"])
    assert 0x00401000 in T.PATCHES_APPLIED
    assert fn.insns[1].mnem == "JNZ"


def test_a_wide_literal_behind_a_halt_is_not_code(tmp_path, monkeypatch):
    """Siege of Avalon 1.19's single-instance check, fn_00c79198, reduced.

    The function pushes L"DigitalTomeSiegeOfAvalon" as an argument, leaves a
    finally block with PUSH continuation; POP EAX; JMP EAX, and ends on a
    call to _Halt0. The literal is kept in .text right behind that call, and
    Ghidra listed its bytes as instructions - 16-bit addressing among them,
    which the emitter refuses. Two things kept those bytes alive: the pushed
    pointer to the literal was taken for a pushed continuation and an
    alternate entry, and the computed jump made every instruction a possible
    successor. A pointer that names a literal is an argument, the jump goes
    only where a continuation was pushed, and nothing follows _Halt0."""
    import struct
    halt, fn = 0x00401000, 0x00401100
    cont, site = fn + 0x20, fn + 0x40
    lit = site + 7
    head = (b"\x55\x8b\xec"                                  # PUSH EBP; MOV EBP,ESP
            + b"\x68" + struct.pack("<I", lit) + b"\x59"      # PUSH lit; POP ECX
            + b"\x68" + struct.pack("<I", cont)               # PUSH cont
            + b"\x58\xff\xe0")                                # POP EAX; JMP EAX
    tail = (b"\xe8" + struct.pack("<i", halt - (site + 5)) + b"\x00\x00"
            + "DigitalTomeSiegeOfAvalon\0".encode("utf-16-le"))
    blocks = {
        halt: b"\xeb\xfe",                                      # _Halt0 never returns
        fn: head,
        cont: b"\x5d\xe9" + struct.pack("<i", site - (cont + 6)),  # POP EBP; JMP site
        site: tail,
    }
    img = synthetic_image(blocks)
    img.code_pointers = lambda *a, **kw: (set(), set())
    img.plausible_immediate_target = lambda addr: False
    text = translate_entry_fixture(tmp_path, monkeypatch, img,
                                   {halt: blocks[halt], fn: [(fn, head), (site, tail)]})
    assert "CALL_FN(%08x)" % halt in text
    assert "L_%08x:" % cont in text, "the pushed continuation is still a label"
    assert "L_%08x" % lit not in text
    assert "void fn_%08x(" % lit not in text
    assert "BX" not in text
    import re
    placed = set(re.findall(r"^(L_[0-9a-f]{8}): ;", text, re.M))
    assert set(re.findall(r"goto (L_[0-9a-f]{8});", text)) <= placed


@pytest.mark.parametrize('addresses', [
    [], [0], [0xffffffff], [0x400003, 0x400023, 0x400024, 0x400111],
    [0xffffffc0, 0xffffffff], [0, 0xffffffff],
])
def test_emitted_call_return_membership_matches_exact_set(tmp_path, addresses):
    """Execute emitted C, including holes, word edges, empty and sparse images."""
    import ctypes
    import io
    import random
    import shutil
    import subprocess
    compiler = shutil.which('clang')
    if not compiler:
        pytest.skip('clang needed to execute the emitted lookup')
    out = io.StringIO()
    out.write('#include <stdint.h>\nstatic const uint32_t recomp_call_returns[] = {')
    out.write(','.join(str(x)+'u' for x in addresses or [0]))
    out.write('};\nstatic const uint32_t recomp_call_return_count = %du;\n' % len(addresses))
    T.emit_call_return_lookup(out, addresses)
    source = tmp_path/'lookup.c'
    source.write_text(out.getvalue())
    libpath = tmp_path/'lookup.so'
    subprocess.run([compiler, '-shared', '-fPIC', '-O2', str(source), '-o', str(libpath)], check=True)
    lib = ctypes.CDLL(str(libpath))
    lookup = lib.recomp_is_call_return
    lookup.argtypes, lookup.restype = [ctypes.c_uint32], ctypes.c_int
    rng = random.Random(946)
    probes = {0, 1, 0xfffffffe, 0xffffffff}
    probes.update(rng.randrange(1 << 32) for _ in range(1000))
    for address in addresses:
        probes.update((address + n) & 0xffffffff for n in range(-35, 36))
    for address in probes:
        assert lookup(address) == int(address in addresses), hex(address)
