# Changelog

## 4.0.0 — a new native version, built on your Mac

4.0.0 replaces 3.x. It is a new native recompilation and runtime, not an update of 3.x, so there is nothing to patch:
download the 4.0.0 Setup and build again.

**What 3.x users should do:** download the 4.0.0 Setup from the
[latest release](https://github.com/elforeign/nfs-most-wanted-mac/releases/latest), run it, and build. Your game folder
is only read. 4.0.0 installs as "NFS Most Wanted Native.app", replacing the 3.x app in place so the Dock icon carries over, and
Setup keeps the 3.x app as "NFS Most Wanted Native 3.x.app" (never deleted) until you are happy with 4.0.0.

**Your 3.x career carries over.** If 4.0.0 has no saves yet, Setup copies your 3.x careers into 4.0.0 (the 3.x files
are only read, never changed) and tells you. Saves already in 4.0.0 are never replaced. Settings are not carried over.
See [Getting started](GETTING_STARTED.md#coming-from-3x).

### New

- **Setup builds the game on your Mac** from your own PC game folder, or the Black Edition `.zip` you downloaded, version 1.3. Any `speed.exe` with the 1.3 code is
  accepted. Setup checks it first and changes nothing if it can't be used. No game files are shipped.
- **Optional Xbox 360 features from your own Xbox 360 copy** (a folder or an `.iso`): world, audio, reverb, music,
  police, nis (cutscenes), vinyls, textures and movies. Setup reports which it could add. Without the copy the PC game
  alone is used.
- **F10 settings menu** (Fn+F10 on a Mac keyboard; touchpad click on a DualSense or DualShock 4) over the game.
- **Discord status** (off by default) and **Check for updates** (only when you press it), in F10 > Display &
  performance > Online. The Discord status is ported from the 3.x work of prex-xo.
- **Controllers:** DualSense and DualShock 4 detected automatically, PlayStation and Xbox layouts, button prompts that
  follow your controller, rumble.
- **Audio:** the game's 5.1 mix, with optional Dolby Digital, Dolby Digital Plus and TrueHD bitstream output.
- Setup bundles its own Python and a pinned FFmpeg 7.1.1 build (LGPL, no GPL or nonfree parts); see
  [NOTICE](NOTICE).

### Needs

Apple Silicon; macOS 14.5 or later (provisional); Xcode Command Line Tools; about 12 GB free (provisional); your own
installed PC game, version 1.3.

### Not available yet

- Building from a PC disc image. Choose an installed game folder or the Black Edition `.zip`. (The Xbox 360 `.iso`
  is supported.)

### Earlier versions

Versions 1.0.0 to 3.0.4 remain on the `legacy-3.x` branch and their release tags.
