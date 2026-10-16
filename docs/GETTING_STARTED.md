# Getting started

The setup kit turns **your own copy** of Need for Speed: Most Wanted into a native Mac app. It checks your game files,
analyses and translates your `speed.exe`, builds the app, and prepares the optional extras from your own files.
It runs on your Mac only. Nothing is uploaded, and nothing it makes is meant to be shared.

## What you need

| | |
|---|---|
| **Mac** | Apple Silicon (M1 or later) with **macOS 14.5 (Sonoma) or later**: the game is built with Command Line Tools 16 or later (the macOS 15 SDK), which need macOS 14.5. Developed and tested on macOS 27 (M5 Pro); earlier macOS versions are untested. The app is built for the macOS version your Mac runs. About 10 GB free: the setup's tools and working files take about 4–5 GB, and the finished app is about the size of your game folder (about 6 GB more with the optional HD textures). |
| **The game** | Your own **Need for Speed: Most Wanted (2005)** for PC (Black Edition), as an **installed game folder** with its own working `speed.exe` (the folder that holds `speed.exe`, `GLOBAL`, `TRACKS`, `MOVIES` and so on). Copy it to the Mac from a Windows PC where it is installed, or from a backup. The setup checks first that the exe is one it can use, and stops without changing anything if it is not. |
| **Xcode Command Line Tools** | Apple's free developer tools, **version 16 or later**. The Setup app offers to install them; in Terminal, `xcode-select --install`. The setup checks the version first and says if yours is too old. |
| **Time** | About 10 minutes the first time on an M5 Pro, including the one-time analysis of `speed.exe`. The optional shader warm-up adds about 35 minutes and the optional HD textures about 1¼ hours (see below); allow longer on older Macs. Updates are quicker. |

The setup downloads the free developer tools it needs into its own folder (`~/Library/NFSMW-Native-Setup`):
**Ghidra** (the NSA's open-source disassembler, used once to map the functions in your `speed.exe`), a **Java
runtime** for it, and **Python 3.12** if your Mac has no Python 3.11 or later. It also builds Ghidra's decompiler for
Apple Silicon once, which uses Gradle's own folder (`~/.gradle`). Nothing is installed system-wide.

This project does not provide, link to, or explain how to obtain any game file. Use your own legally obtained copy.

## Set up with the Setup app (easiest)

1. Download the **NFS-Most-Wanted-Native-Setup** disk image (`.dmg`) from the
   [latest release](https://github.com/elforeign/nfs-most-wanted-mac/releases/latest) and open it.
2. Open **NFS Most Wanted Native Setup**. The app is not notarized by Apple, so the first time macOS blocks it: click
   **Done**, then go to **System Settings → Privacy & Security** and click **Open Anyway** next to its name.
3. **Choose your game folder.** The app checks your `speed.exe` straight away and changes nothing if it can't be used.
4. If asked, **install Apple's Command Line Tools**; the app waits for them.
5. Optional: add your copy of `XenonEffects.tpk`, of the Xbox 360 movies or of the Xbox 360 Stuff Pack download
   (its unzipped *Easy Installation* folder), tick *Prepare the shaders now* (about 35 minutes more; it removes the
   first-drive stutter), tick *Make HD textures now* (about 1¼ hours, see below), or choose where the game goes
   (Applications by default).
6. Click **Build**. You see each step, the progress and the time; the Mac stays awake while it runs. When it finishes,
   click **Open the game**.

The app keeps its working files in `~/Library/NFSMW-Native-Setup`, so building again (for example with a newer
Setup app) reuses the one-time analysis and is much quicker.

## Set up in Terminal

```sh
git clone https://github.com/elforeign/nfs-most-wanted-mac.git
cd nfs-most-wanted-mac
./setup.sh "/path/to/Need for Speed Most Wanted"
```

The setup prints each step and what it found:

1. **Check:** the game folder and its `speed.exe`.
2. **Link:** your game folder is linked (not copied or changed) into `original/retail`.
3. **Analyse:** Ghidra maps the functions in your `speed.exe` (once; later runs reuse it).
4. **Translate and build:** the game code is translated to C and compiled with the native host.
5. **Effects and extras from your game:** the rebuilt effects are assembled from this repository's own shader
   programs (`effects/`), and the light pools from your game's own lamp data and effects.
6. **Optional packs:** the Xbox 360 Stuff Pack's textures in your game folder (`TRACKS/TexWizardX360`) and
   `XenonEffects.tpk` (in its `scripts` folder) and your own Xbox 360 movies (an `X360-Movies` folder) are found and
   added. How to add them on your Mac: the
   [companion guide](COMPANION_GUIDE.md#optional-mods).
7. **Package:** `dist/NFS Most Wanted Native.app` is written, with your game data inside it.

If you ask for it, step 6 also prepares the game's shaders (about 35 minutes): the built game drives a few Quick Races
offscreen, with no window, and keeps every shader it translates, so your first drive does not stutter. It is off by
default (3.0): tick *Prepare the shaders now* in the Setup app or pass `--shader-seed`. Without it the game prepares them
while you drive, and the first minutes may hitch once at each new sight. Either way they are kept in your profile
folder after the first time.

#### HD textures (optional, 2.1)

Step 6 can also make the **HD texture pack**: every world, car and front-end texture of *your* game is upscaled on
your Mac (a CC0 upscaling model on the GPU for the world, a sharp resampling for cars, signs and the HUD), some roads,
rock, grass, metal and wood get fine real-world grain from CC0 photos (ambientCG), and the result goes into the app,
where the *Remaster HD textures* setting uses it. With the Xbox 360 Stuff Pack, its textures are upscaled too.

- **Turn it on:** tick *Make HD textures now* in the Setup app, or pass `--hd-textures` (`--no-hd-textures` skips it).
  It is off unless you turn it on.
- **Updates keep it (3.0.1):** when you open a newer Setup, *Make HD textures now* and *Prepare the shaders now*
  start ticked if an earlier build made them. The HD textures are reused (minutes, not hours). The shaders are
  reused too (seconds) when the new version draws them the same way (3.0.4: the setup log says "shader warm-up:
  reused the prepared shaders"); otherwise they are prepared again (about 35 minutes). Untick to leave them out.
  From the command line, pass `--hd-textures` / `--shader-seed` again.
- **Time:** about 1¼ hours on an M5 Pro the first time (measured, including the one-time downloads), much longer on a
  base M1. The Mac stays awake.
- **Disk and downloads:** the app grows by about 2 GB, and the work folder `~/Library/NFSMW-Native-Setup/hd`
  holds about 3.5 GB (its own Python tools with PyTorch, about 1 GB downloaded once; the 140 MB model; the photos,
  about 140 MB; your game's textures and the pack). Every download is checked against a pinned SHA-256.
- **Later or interrupted:** run Setup again with the option on. It carries on where it stopped and reuses everything
  it already made; nothing is made twice. Deleting `~/Library/NFSMW-Native-Setup/hd` frees the space (the app keeps
  its copy).
- If it cannot finish, the setup log says `HD textures: skipped` and the app is made without it: the game's own
  textures show, exactly as before.

### Options

| Option | |
|---|---|
| `--xenon-effects FILE` | Use this `XenonEffects.tpk` for the sparks and light trails (otherwise the game folder's `scripts/XenonEffects.tpk` is used if it's there). |
| `--x360-movies DIR` | Use your own copy of the US Xbox 360 disc's movies from this folder (otherwise an `X360-Movies` folder in the game folder is used if it's there). Only the exact original files are added. |
| `--no-pools` | Skip building the light pools. |
| `--hd-textures` / `--no-hd-textures` | Make (or skip) the HD texture pack (about 1¼ hours on an M5 Pro; off unless asked; see *HD textures* above). |
| `--out DIR` | Write the app somewhere other than `dist/`. |
| `--check-only` | Only check the game folder and its `speed.exe`, then stop. |

Run `./setup.sh --help` for the full list.

## Updating

With the Setup app: download the newer Setup app, open it, choose your game folder and click **Build**.

In Terminal:

```sh
git pull
./setup.sh "/path/to/Need for Speed Most Wanted"
```

The analysis is reused, so an update translates, builds, prepares the shaders (if on) and packages again. Your saves and settings are kept:
they live in your profile folder, not in the app, and since 2.0.4 the first start of each new build copies your saves
to `Save Backups/` first (see the [companion guide](COMPANION_GUIDE.md#keeping-your-game-and-saves-safe)). Don't
install mods or packs over your game folder: the guide lists what is safe to add and how.

**From 1.0 to 2.0:** your saves and settings carry over. Anti-aliasing starts at Off once (choose 2x or 4x again),
and the look is Definitive (3.0 retired the *Look* setting). A 1.0 profile keeps its own Dolby Digital choice (new profiles
start with it on). What changed:
[What's new in 2.0](COMPANION_GUIDE.md#whats-new-in-20).

**From 2.x to 3.0:** your saves and settings carry over. The *Look* setting is retired: Definitive is the only look.
A profile saved on the *Xbox 360* or *PC* look opens in Definitive once (from the Xbox 360 look the golden colour
filter stays on); the features with an Xbox 360 version keep your own choice.

## Removing it

Delete the game app, the Setup app (or the cloned folder), `~/Library/NFSMW-Native-Setup` and `~/.gradle` (used once
to build Ghidra's decompiler). Your saves and settings are in `~/Library/Application Support/NFS Most Wanted Native/`;
delete that too if you want them gone.
