# Need for Speed: Most Wanted (2005) — Native for Apple Silicon Macs

A native macOS build of the 2005 PC game, made by **static recompilation**: the game's own x86 code is translated to
C ahead of time and compiled for Apple Silicon, with a Metal renderer, native audio and native controller support.
There is no emulator, no Wine and no CrossOver.

> **You need your own copy of the game.** This repository contains **no game code, executables, artwork, sound,
> movies, tracks or texture packs**. The setup kit takes the files from **your** installation and builds the app on
> **your** Mac. Nothing it builds is meant to be shared.

This project is not affiliated with or endorsed by Electronic Arts. *Need for Speed* and *Most Wanted* are
trademarks of Electronic Arts Inc.

## Play it on your Mac

1. Have your own installed copy of the PC game (Black Edition, version 1.3) on your Mac.
2. Download the **Setup app** (`NFS-Most-Wanted-Native-Setup` disk image) from the
   [latest release](https://github.com/elforeign/nfs-most-wanted-mac/releases/latest), open it, and choose your game folder.
3. Click **Build**. About 10 minutes later on a recent Mac (longer on older ones) the game is in your Applications folder.

The Setup app holds no game files: it builds the game on your Mac from your copy. Step by step, including the one-time
"Open Anyway" for an app that is not notarized by Apple: **[Getting started](docs/GETTING_STARTED.md)**. You can
also build in Terminal with `./setup.sh`.

## What you get

- Native Apple Silicon app (tested at 4K 60 fps on an M5 Pro MacBook Pro), any resolution and aspect ratio, true widescreen
  (field of view, HUD and minimap moved out to the edges, widescreen movies fitted correctly).
- **Definitive look** (default): the XBOX 360 game's heavy gold haze removed, with rebuilt effects written for this
  port: glow, gentle auto brightness for tunnels, motion blur at speed and movie-style depth of field in
  cut-scenes. **PC - Original** shows the game exactly as it shipped.
- **Light pools** cast by the PC game's own street lamps and tunnel lights onto the road and walls.
- Rear-view mirror: sharp HD picture, full scenery, car shadows, headlights and police lights, knocked-over props.
- **HDR output** for HDR TVs, and 4x MSAA anti-aliasing.
- **Audio:** the game's own 5.1 mix as Mono, Stereo or 5.1 PCM, switchable live, plus an optional **Dolby Digital
  5.1** bitstream for TVs and soundbars that misplace multichannel PCM.
- **Controllers:** DualSense tested over USB and Bluetooth (other controllers macOS supports should work), PlayStation-style layout and button icons, analog
  triggers as pedals if you like, rumble from the game's own force-feedback.
- **Extra Options, built in:** Black Edition content, replay beaten Blacklist rivals, special vinyls, max-performance
  shop button, 6 rival reward markers, longer profile names, starting cash, police heat levels 1–10, helicopter
  takedown bounty, skip the music track while driving (T or L3), and optional unlock-everything and barrier
  removal.
- Optional community packs you already own work too: the Xbox 360 Stuff Pack's **textures** and the
  **XenonEffects** sparks and light trails. See the [companion guide](docs/COMPANION_GUIDE.md).

## Get started

1. Read **[Getting started](docs/GETTING_STARTED.md)**: what you need, then the Setup app (or one Terminal command).
2. Then the **[companion guide](docs/COMPANION_GUIDE.md)**: playing, the F10 settings menu, controllers, audio,
   and adding the optional mods.
3. Problems: **[Troubleshooting](docs/TROUBLESHOOTING.md)**. All settings: **[Settings](docs/SETTINGS.md)**.

## How it works

The translator, runtime, Direct3D 9 → Metal renderer, audio and input hosts are
[recomp-kit](https://github.com/veritr1x/recomp-kit) by veritr1x, included in `kit/` with this port's changes; this game's configuration started as
veritr1x's [nfsmw-recomp](https://github.com/veritr1x/nfsmw-recomp). This repository adds the fixes, features and
setup kit that make it a complete, playable Mac game: `game.toml` (the game's identity, addresses and translation
fixes), `mods/core/nfsmw/` (the core mod: widescreen, settings, effects, controls, audio, Extra Options), `effects/`
(the rebuilt effects' shader sources) and `tools/setup_kit/` (the setup).

You provide your own game folder, including its `speed.exe`. The setup checks that the exe is one the port can use
before it builds anything.

## Credits

This port is built on **recomp-kit** and **nfsmw-recomp** by **veritr1x**.

Community mods this port learned from or works with, and the people their readmes credit:

- **Extra Options** — ExOpts Team
- **Need for Speed Most Wanted (2005) – Xbox 360 Stuff Pack 4.2** (its textures) — "by a bunch of people", including
  osdever, SpeedyHeart, MaxHwoy, Kevin4e, Aero_ and Xanvier
- **NFS HD Reflections** — Aero_; based on Extra Options by the ExOpts Team; thanks to osdever
- **NFS XtendedInput** — xan1242 (Lovro Pleše) and Berkay Yiğit; button icons by Aero_ (AeroWidescreen); injector by
  LINK/2012
- **NFSMW XenonEffects** — xan1242 (Lovro Pleše)
- **NFS Most Wanted Widescreen Fix** — ThirteenAG
- **TexWizard** — R-033; thanks to nlgxzef

No code from these mods is included; their behaviour is reimplemented natively. Licence notices are in [NOTICE](NOTICE).

## Licence

MIT licence (see [LICENSE](LICENSE)).
