"""Extract EA Carbon PC discs into a separate game tree; never patch or validate an executable.

ISO9660/Joliet images (including ISO-compatible 2048-byte MDF) and copied disc
folders share the bounded PC disc reader. Raw-sector MDF/MDS is unsupported.
Setup routing is deliberately left to the caller.
"""
from contextlib import ExitStack
from pathlib import Path
import shutil
import zipfile

from . import pc_disc

PRODUCT = "{259C0ABB-A3B2-4D70-008F-BF7EE491B70B}"
FIX = "Choose the Carbon PC game disc image (.iso or ISO-compatible .mdf), with any other game discs beside it."
MISSING_FIX = ("Keep all disc images together, or copy each disc into its own folder beside the others. "
               "Then choose disc 1 again.")


def is_disc(path):
    """Recognize a Carbon EA game disc by its layout and product, without writing."""
    if not pc_disc.is_disc(path, allow_mdf=True):
        return False
    try:
        disc = pc_disc.Disc(path)
        try:
            return disc.product == PRODUCT and bool(disc.archives)
        finally:
            disc.close()
    except (pc_disc.DiscError, OSError):
        return False


def prepare(source, work, events, extra=()):
    """Return <work>/carbon_src with all archives and listed loose files.

    Uses Setup's transactional extraction/cache and archive path/type/size/CRC
    checks. No executable compatibility or installed-version claim is made.
    """
    from . import backend as b

    source, work = Path(source), Path(work)
    discs = {}
    try:
        # Refuse another game's product before discovering siblings or unpacking.
        first = pc_disc.Disc(source)
        try:
            if first.product != PRODUCT:
                raise b.SetupError("This is not a supported Carbon PC disc.", FIX)
        finally:
            first.close()
        discs = pc_disc.disc_set(source, extra, allow_mdf=True, missing_fix=MISSING_FIX)
        archives = [name.casefold() for disc in discs.values() for name in disc.archives]
        if "0compressed.zip" not in archives or "1compressed.zip" not in archives:
            raise b.SetupError("The Carbon discs must hold both 0compressed.zip and 1compressed.zip.", FIX)
        output = (work / "carbon_src").resolve()
        for disc in discs.values():
            path = disc.path.resolve()
            if path == output or output in path.parents or (disc.handle is None and path in output.parents):
                raise b.SetupError("The extraction folder overlaps a source disc.", FIX)

        def stamp(disc):
            def stat(path):
                st = path.stat()
                return [str(path.resolve()), st.st_size, st.st_mtime_ns]
            return (stat(disc.path) if disc.handle else
                    [str(disc.path.resolve())] + [stat(disc.path / name) for name, _, _ in sorted(disc.files.values())])

        key = dict(layout=1, discs=[stamp(disc) for disc in discs.values()])

        def unpack(out):
            _extract(discs, out, work, events)
            if key["discs"] != [stamp(disc) for disc in discs.values()]:
                raise b.SetupError("A Carbon disc changed while being extracted.", FIX)
            return out

        return b.unpacked(work, key, events, "the Carbon discs", unpack, name="carbon_src")
    except (pc_disc.DiscError, zipfile.BadZipFile, OSError, EOFError) as error:
        raise b.SetupError(f"The Carbon discs can't be read: {error}", getattr(error, "fix", FIX)) from None
    finally:
        for disc in discs.values():
            disc.close()


def _extract(discs, out, work, events):
    from . import backend as b

    first = discs[1]
    _, names = pc_disc.install_list(first)
    # safe_name also protects folder sources: install_list is an installer list,
    # not itself a path validator. Only root loose files are listed for copying.
    loose = [(first, pc_disc.safe_name(name)) for name in names]
    loose += [(disc, name) for disc in discs.values() for name, _, _ in disc.files.values()
              if name.casefold().startswith("movies/")]
    with ExitStack() as stack:
        archives = []
        budget = b.ZipDirectoryBudget()
        for disc in discs.values():
            for name in disc.archives:
                label = f"{name} on Carbon disc {disc.number}"
                stream = stack.enter_context(disc.open(name))
                archive = stack.enter_context(budget.open(stream, label, FIX))
                archives.append((label, archive) + b.zip_members(archive, label, FIX))
        total = sum(size for _, _, _, size in archives) + sum(disc.size(name) for disc, name in loose)
        count = sum(len(members) for _, _, members, _ in archives) + len(loose)
        if total > b.ZIP_MAX_BYTES or count > b.ZIP_MAX_FILES:
            raise b.SetupError("The Carbon discs are larger than 8 GB or 20,000 files.", FIX)
        if shutil.disk_usage(work).free < total + 512 * 1024**2:
            raise b.SetupError("There is not enough free disk space to unpack the Carbon discs.", FIX)
        out.mkdir(parents=True)
        done = 0
        events.progress(done, total)
        for label, archive, members, size in archives:
            b.unpack_zip(archive, members, size, out,
                         lambda i, written, base=done: events.progress(base + written, total), label, FIX)
            done += size
        for disc, name in loose:
            # Same Windows case merge and duplicate-content policy as MW discs.
            target = out
            for part in name.split("/"):
                pc_disc.safe_name(part)
                matches = [p for p in target.iterdir() if p.name.casefold() == part.casefold()] if target.is_dir() else []
                if len(matches) > 1:
                    raise b.SetupError(f"The Carbon discs list {name} twice in different case.", FIX)
                target = matches[0] if matches else target / part
            existing = target.exists()
            if existing and (not target.is_file() or target.stat().st_size != disc.size(name)):
                raise b.SetupError(f"The Carbon discs hold conflicting copies of {name}.", FIX)
            target.parent.mkdir(parents=True, exist_ok=True)
            copied = 0
            with disc.open(name) as src, target.open("rb" if existing else "xb") as dst:
                for block in iter(lambda: src.read(1 << 20), b""):
                    copied += len(block)
                    if copied > disc.size(name):
                        raise b.SetupError(f"{name} grew while being copied.", FIX)
                    if existing:
                        if block != dst.read(len(block)):
                            raise b.SetupError(f"The Carbon discs hold conflicting copies of {name}.", FIX)
                    else:
                        dst.write(block)
                    events.progress(done + copied, total)
            if copied != disc.size(name):
                raise b.SetupError(f"{name} was cut short while being copied.", FIX)
            done += copied
        events.progress(total, total)
