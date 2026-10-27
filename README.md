# Need for Speed: Most Wanted (2005) — Native for Apple Silicon Macs

A native macOS version of the 2005 PC game, made by **static recompilation**: the game's own x86 code is translated to
C ahead of time and compiled for Apple Silicon, with a Metal renderer, native audio and native controller support.
There is no emulator, no Wine and no CrossOver.

> **You need your own copy of the game.** This repository contains **no game code, executables, artwork, sound,
> movies, tracks or textures.** The Setup app builds the game on **your** Mac from **your** game files. Nothing it
> builds is meant to be shared.

This project is unofficial. It is not affiliated with or endorsed by Electronic Arts, Criterion Games or Black Box.
*Need for Speed* and *Most Wanted* are trademarks of Electronic Arts Inc.; all other names belong to their owners.
Details: [NOTICE](NOTICE).

## Play it on your Mac

1. Have your own PC game, **version 1.3**, on your Mac: the installed game folder or a .zip of it.
2. Download the **Setup app** (the disk image) from the
   [latest release](https://github.com/elforeign/nfs-most-wanted-mac/releases/latest) and open it.
3. Choose your game folder (or a .zip of it), optionally your Xbox 360 copy, and click **Build**. Setup compiles the game on your Mac and
   puts **NFS Most Wanted Native.app** in Applications.
4. Questions and help: the **[Discord](https://discord.gg/d6HfMwdM9v)**.

Step by step, including the one-time "Open Anyway" for an app that is not notarized by Apple:
**[Getting started](GETTING_STARTED.md)**. Problems: **[Troubleshooting](TROUBLESHOOTING.md)**.

**Coming from 3.x?** 4.0.0 is a new build, not an update of 3.x. Download the 4.0.0 Setup and build again. 4.0.0 replaces the 3.x app in place
(same name, same Dock icon) and keeps it as "NFS Most Wanted Native 3.x.app". See the
[changelog](CHANGELOG.md).

## What you need

- An Apple Silicon Mac (M1 or later) with macOS 14.5 or later (provisional).
- Xcode Command Line Tools (free; Setup offers to install them).
- About 12 GB free disk space while Setup builds; the installed game then uses about 9 GB (3.5 GB without the Xbox 360 copy).
- Your own PC game, version 1.3: the installed game folder or a `.zip` of it. Any `speed.exe` that has the 1.3
  code is accepted. A PC disc image (`.iso`) isn't supported yet.
- Optional: your own Xbox 360 copy of the game, as a folder or an `.iso` (both work), for the Xbox 360 features.

## What you get

- A native Apple Silicon app: Metal renderer, CoreAudio sound, widescreen.
- **F10 settings menu** (Fn+F10 on a Mac keyboard) over the game. On a DualSense or DualShock 4 the touchpad click
  opens it.
- **Controllers:** DualSense, DualShock 4 and other controllers macOS supports; PlayStation and Xbox button layouts;
  button prompts that follow the controller you use; rumble.
- **Audio:** the game's 5.1 mix, with optional Dolby Digital, Dolby Digital Plus and TrueHD bitstream output.
- **Xbox 360 features, only if you give Setup your own Xbox 360 copy:** Setup lists them as world, audio, reverb,
  music, police, nis (cutscenes), vinyls, textures and movies. Without that copy the PC game alone is used and these
  are absent.
- **Discord status** (off by default) and **Check for updates** (only when you press it), both in the F10 menu under
  Display & performance > Online.

## How it works

Setup checks your `speed.exe`, translates its code to C, compiles it with Apple's tools, builds the audio and shader
pieces, converts your assets and installs the app. It uses a bundled Python and a pinned FFmpeg 7.1.1 build; the
exact pieces are in [NOTICE](NOTICE). Everything happens on your Mac and nothing is uploaded.

## Credits and licenses

Thanks to everyone listed in [NOTICE](NOTICE), including **prex-xo** for the Discord status.
The project's own code is under the MIT license ([LICENSE](LICENSE)). The game itself is not relicensed.
