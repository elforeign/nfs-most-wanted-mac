# Save commit timings

`BOOT_DIAG` also writes `save_NNNNNN.txt` after each career save-task update in
state `0xb`. No additional switch is needed. The header identifies the task,
sequence, thread and presented-frame count (`frames.log` correlation), with total
wall time, span count and dropped span count. Each stage line has an ID, parent
ID (`-1` for top level), start/end offsets and duration in milliseconds, syscall
result and the errno left by that call (errno is meaningful on failure only).
The clock is `mach_absolute_time`; conversion uses the host timebase.

Stages cover each guest write and explicit flush, provenance preparation and
backup, file `fullfsync` and conditional `fsync.fallback`, close, rename and
`directory.sync` (including its individual flush attempts and close). Trax
publication and the pad-binding, underglow and provenance completion callbacks
have separate enclosing spans, including callbacks which have nothing pending.
Buffered stream output is charged to publication and its `stream.flush` or
`stream.close`; underglow's direct write/fsync/rename are individual spans.
Nested spans are inclusive: do not add a directory or sidecar total to its
children. Compare top-level spans with the header total to see uninstrumented
work in the save-task update. Preparation/backup may themselves contain flush
attempts; parent IDs associate those with their enclosing stage.

Up to 2048 spans per thread are retained in memory. A nonzero `dropped` count
means the report is incomplete. Report creation and formatting happen after the
whole task and completion callbacks; they are outside all measured stages and
the reported total, but still add diagnostics-only work to that frame. Existing
diagnostics emitted by a callback remain part of its inclusive time. Failed
commits retain their attempted stages and results; later stages may be absent.
This measures publication, not earlier profile serialization. It does not change
flush policy or error handling. With diagnostics off each timing hook takes one
predicted branch, with no clock/TLS/allocation/output work. This is a cold save
path, so an off-mode A/B run is unnecessary.

Offline proof: `save_timing_test.py` drives the production file shims, save-task
hook and Trax publication in temporary directories. Good: exit 0, 27 ordered
spans, four full-sync attempts and four forced fallbacks; removed timing hook:
check exit 1, zero spans. The diagnostics-off pass writes no report.
