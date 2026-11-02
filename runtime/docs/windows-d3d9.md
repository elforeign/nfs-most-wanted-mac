# Guest D3D9 CPU recording

`runtime/win/d3d9.c` (guest COM surface) and `d3d9_state.c` (objects, lifetime,
draw snapshots) record the bounded D3D9 subset the front-end shader fixtures
need. No renderer, shader translation or Mac dependency is involved.
Proof: `recomp/windows_d3d9_test.py` (numbers in its header; `--desktop` runs
the same fixture with Windows SDK/CRT headers).

## Integration

Call `win_d3d9_register()` once under the guest GIL after startup
registration; it binds `d3d9.dll!Direct3DCreate9` (IAT 008904A8) and returns 0
on a second call or when adapter capacity is exhausted. Default first-import
mode (exit 10) and the CRT-prefix mode stay authoritative. Registration
installs no backend.

Callback budget: 61 host registrations of `WIN_ADAPTER_LIMIT` 128 (startup
uses 1) plus 1 import. Shared callbacks cover IUnknown, GetDevice/GetDirect3D,
buffer Lock/Unlock/GetDesc and shader GetFunction across interfaces.

## Guest objects

Interface pointers are 32-bit guest offsets into one 2 KiB block of 8-byte
headers `{vtable, generation}`, one per table slot (`WD_OBJECTS` 256); a freed
slot's address is reused, like heap memory. Vtables are guest arrays with the
real slot counts (IDirect3D9 17, IDirect3DDevice9 119, texture 22, VB/IB 14,
declaration and shaders 5). Resource data lives in guest memory from
`win_alloc` and is not reclaimed (bounded front-end use); creation takes the
table slot first, so a full table never consumes arena. READONLY locks of
WRITEONLY buffers are invalid.

Lifetime matches D3D9: `refs` is the public count; device bindings (`binds`)
keep data alive without changing it. A child holds its parent (factory or
device) reference while `refs` is nonzero. A released but bound resource can
still be drawn and is revived by a Get*; it is destroyed when the last binding
goes. After full release `win_d3d9_live_objects()` is 0.

## Unsupported frontier (exit 2)

- Every unimplemented vtable slot points to one callback that prints
  `win: D3D9 unsupported <interface> slot <n> (caller <pc>)`, decoding the
  slot from the guest `call [r32+disp8/disp32]` before the return address
  (-1 if not decodable), then fails. It never guesses a signature.
- Valid calls outside the subset fail with `D3D9 outside the recorded subset`:
  multisampled or non-X8R8G8B8/A8R8G8B8 back buffers, auto depth formats other
  than D24S8/D16, textures with levels other than 1 or formats other than
  A8R8G8B8, X8R8G8B8, R5G6B5, A8, L8, vertex element types above D3DCOLOR or
  UBYTE4 or non-default methods, and render states outside the list in
  `render_state()`.
- A draw with no backend installed fails (`D3D9 draw without a backend`).
- A draw with a NULL vertex or pixel shader (fixed-function processing) fails.

Invalid arguments by D3D9 rules (bad pointers, ranges, locks, slots) return
D3DERR_INVALIDCALL. Shaders must be vs_1_1/ps_1_1 (what the caps report);
lengths use the SM1 rule that only parameter tokens set bit 31.

## Draw snapshots

Each recorded draw is a `WinD3D9Snapshot` (d3d9_state.h) with: serial, device
id/generation, topology, primitive count, indexed/UP flags, base vertex, min
and number of vertices, start, index format; the full supported state (render,
sampler and texture-stage states, viewport, scissor, float constants and their
dirty versions); and owned byte copies of the VS, PS, declaration, each read
vertex stream range, the index range and bound textures, each tagged with the
resource id, generation, version (bumped per writing unlock), format,
dimensions, pitch and offset.

Ownership: `submit` returning true owns the snapshot until
`win_d3d9_complete`, even across device teardown; false makes the draw return
D3DERR_NOTAVAILABLE. No snapshot pointer refers into guest memory, so later
locks, constant writes, rebinding, release and slot reuse never change
submitted bytes. At most 16 snapshots may be pending (E_OUTOFMEMORY beyond).

Validation before recording: in a scene, a declaration bound, no indexed
point list, unlocked
resources, every index inside [min, min+num), every read vertex range inside
its buffer with stride covering the elements, and no stream above 0 for UP
draws. A successful DrawPrimitiveUP unbinds stream 0 (offset and stride 0);
DrawIndexedPrimitiveUP also unbinds the index buffer.
