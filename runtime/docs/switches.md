# Switch registry

Every NFS_* / BOOT_* name the code reads is a row in `runtime/switches.tsv` (kind, default, where read, docs, retire);
`runtime/switches_test.py` fails on a name missing from the table. The lines below document names that had no doc.


- `BOOT_DIAG_GILWAIT`: reports guest lock wait diagnostics when diagnostic capture is enabled.
- `BOOT_DIAG_HDR_CLIP_MUTANT`: test control that restores the pre-fix HDR clipping behavior.
- `BOOT_DIAG_PROFILE_MAX`: sets the maximum diagnostic profile sample count.
- `BOOT_DIAG_SOFT_SMOKE`: selects soft-smoke diagnostic capture.
- `BOOT_DIAG_SOFT_SMOKE_COST`: measures the soft-smoke rendering cost.
- `BOOT_DIAG_SPLITPOST_DROPS`: reports dropped split post-processing work.
- `BOOT_DIAG_SSAO_CPU`: selects the CPU diagnostic path for SSAO.
- `BOOT_DIAG_UI_ADDED_MIPS`: diagnostic control for added UI texture mip levels.
- `BOOT_DIAG_UI_TEXTURES`: enables UI texture diagnostic output.
- `BOOT_PAD2`: scripted input for the second simulated controller slot.
- `NFS_COP_TEST_ALLOC_FAIL`: test injection for cop-speech allocation failure.
- `NFS_COP_TEST_OFFSET_BAD`: test injection for an invalid cop-speech offset.
- `NFS_COP_TEST_PARITY`: selects cop-speech parity test behavior.
- `NFS_COP_TEST_PREF`: selects the cop-speech preference test case.
- `NFS_COP_TEST_QUIET`: suppresses cop-speech test output.
- `NFS_COP_TEST_WARM`: exercises warm cop-speech cache behavior.
- `NFS_FRAME_LIMIT`: overrides the frame pacing limit.
- `NFS_FRAME_MEMORY_KB`: sets the frame-memory budget in kilobytes.
- `NFS_HDR_OUTPUT`: selects the HDR output mode.
- `NFS_MUSIC_TEST_PREF`: selects the music preference test case.
- `NFS_NO_CATCHUP`: disables simulation catch-up steps.
- `NFS_TEST_ADDCARS_SEED_META`: injects added-car seed metadata for tests.
- `NFS_TEST_AUDIOSET`: selects a test audio set.
- `NFS_TEST_CARDRAW_MISS`: injects a car draw miss for tests.
- `NFS_TEST_ENVCUBE_NO_MIPS`: tests environment-cube loading without mip levels.
- `NFS_TEST_SOFT_SMOKE_EARLY`: exercises early soft-smoke initialization.
- `NFS_TEST_SPLITPOOL`: selects a split-pool test configuration.
- `NFS_TEST_UPGRADE_INTERVAL`: overrides the upgrade test interval.
- `NFS_TEST_X360ABK`: selects the X360 audio-bank test case.

