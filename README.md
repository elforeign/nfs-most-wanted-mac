# Need for Speed: Most Wanted (2005) — Native for Apple Silicon Macs

**Best of all worlds:** The PC game on your Mac, with console-inspired bloom, colour and car detail already included, plus more Xbox 360 features from your own console files.

Setup builds the game on your Mac with native graphics, sound and controller support.

> **You need your own copy of the game.** This repository contains **no game code, executables, artwork, sound,
> movies, tracks or textures.** Setup builds the game on **your** Mac from **your** game files. Nothing it builds is
> meant to be shared, and nothing is uploaded.

Watch it running live on a Mac: [YouTube live stream](https://www.youtube.com/watch?v=wI0Jr0FTvRc&t=210s).

## What you need

- An Apple Silicon Mac (M1 or later) with macOS 14.5 or later.
- About 12 GB free while Setup builds (the game then uses about 9 GB, or 3.5 GB without the Xbox 360 copy).

## What you can use

- Choose your own installed PC game folder, or pick its `speed.exe`. You need version 1.3.
- Or choose a `.zip` or `.7z` of that folder, or your original PC discs as `.iso` images or mounted/inserted discs. For older discs, choose the official 1.3 patch and a compatible 1.3 `speed.exe` when Setup asks; the disc executable is copy-protected.
- Optional: add your own Xbox 360 files as an `.iso`, extracted folder, `.zip` or `.7z` for more console features. They do not replace the PC game.
- Have a `.rar` or `.bin/.cue`? Unpack it to a folder first.

## Install

1. Download the **Setup app** from the [latest release](https://github.com/elforeign/nfs-most-wanted-mac/releases/latest)
   and open it. The first time, macOS asks you to allow it once (System Settings > Privacy & Security > Open Anyway).
2. Choose your PC game using one of the options above, optionally add your Xbox 360 files, and click **Build**.
   Setup offers to install Apple's free Command Line Tools if you need them.
3. Play **NFS Most Wanted Native** from Applications. Updating later keeps your career and settings.

Step by step, and coming from 3.x: **[Getting started](GETTING_STARTED.md)**. Problems:
**[Troubleshooting](TROUBLESHOOTING.md)**.

## Better than the original PC game

Press **F10** (Fn+F10 on a Mac keyboard, or click the touchpad on a DualSense or DualShock 4) for the settings menu.
**F10 > Help > Guide** explains every setting.

**Picture and looks**
- Widescreen up to 4K, anti-aliasing up to 8x, and HDR on screens that have it.
- Xbox 360 looks made from your PC files: the console's bloom and colour, car paint and metallic flake, tyre tread,
  building glass, raindrops on the lens, sea, wall depth, smoke and distant fog. With your Xbox 360 copy its own
  versions are used.
- Glossy clear coat on car paint, clear car glass, contact shadows, soft smoke, and rain that stays out from under
  roofs and bridges.
- A sharper rear-view mirror, longer draw distance and sharper car reflections, if you want them.
- Look presets: Port, PC retail, Xbox 360 retail or your own picks.

**Performance**
- Native Apple Silicon code; shaders are prepared while the game loads, so races don't stutter. A frame limit
  (Off, 120, 60 or 40) and an optional performance overlay.

**Controllers**
- DualSense, DualShock 4, Xbox and other controllers macOS supports, detected automatically.
- PS2 or Xbox button layouts, button prompts that match your controller, rumble.
- DualSense adaptive triggers and lightbar; analog trigger pedals.
- An on-screen keyboard for typing names with a controller.

**Sound**
- The game's 5.1 surround mix, with optional Dolby Digital, Dolby Digital Plus or TrueHD to your receiver or TV.
- A peak limiter against distortion. With your Xbox 360 copy: its music, reverb, police radio and cutscene sound.

**Quality of life**
- Gameplay options: fixed heat level, no rubber banding, skip the intro movies or the prologue, more reward cards,
  Unlock All.
- Record clips with sound, save the last 30 seconds, or stream to YouTube.
- Underglow on every car you can own, the stock BMW M3 GTR in the car lot, and [75 optional cars](GETTING_STARTED.md#added-cars-optional) in Setup
  from your own files. Carbon PC offers 30 cars, with 14 of those also available from Carbon Xbox 360.
  ProStreet PC offers 31 and Undercover PC offers 14.
- Updates from inside the game, problem reports ready for a GitHub issue, and Discord status (off until you turn it
  on).

Not available yet: online free roam. What changed in each version: [changelog](CHANGELOG.md).

## Help and news

- **[Discord](https://discord.gg/d6HfMwdM9v)**: questions, help and news.
- **[GitHub issues](https://github.com/elforeign/nfs-most-wanted-mac/issues)**: bug reports.

## Also from this project: Carbon

**[NFS Carbon Native](https://github.com/elforeign/nfs-carbon-mac)** is the same kind of port for Need for Speed:
Carbon (2006), in its own repository. It installs alone and needs only your Carbon PC files.

## Credits and licenses

Unofficial; not affiliated with or endorsed by Electronic Arts, Criterion Games or Black Box. *Need for Speed* and
*Most Wanted* are trademarks of Electronic Arts Inc. Credits, bundled tools and licences: [NOTICE](NOTICE); the project's
own code is MIT ([LICENSE](LICENSE)) and the game is not relicensed. Thanks to **prex-xo** for the Discord status.
