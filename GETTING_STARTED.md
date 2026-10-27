# Getting started

Setup turns **your own copy** of Need for Speed: Most Wanted into a native Mac app. It checks your game files,
translates and compiles the game on your Mac, converts your assets and installs the app. Nothing is uploaded, and
nothing it makes is meant to be shared.

## What you need

| | |
|---|---|
| **Mac** | Apple Silicon (M1 or later) with **macOS 14.5 or later** (provisional). |
| **The game** | Your own **Need for Speed: Most Wanted (2005)** for PC, **version 1.3**, installed: the folder that holds `speed.exe`, `GLOBAL`, `TRACKS`, `CARS`, `FRONTEND` and `SOUND`, or the Black Edition `.zip` you downloaded (Setup unpacks a copy into its work folder and only reads the `.zip`). Any `speed.exe` that has the 1.3 code is accepted; Setup checks it first and changes nothing if it can't be used. A **PC** disc image (`.iso`) isn't supported yet (the Xbox 360 `.iso` is, below). |
| **Xcode Command Line Tools** | Apple's free developer tools. Setup offers to install them; in Terminal, `xcode-select --install`. |
| **Disk space** | About 12 GB free while Setup builds; the installed game then uses about 9 GB (3.5 GB without the Xbox 360 copy). |
| **Optional: Xbox 360 copy** | Your own Xbox 360 copy of the game, as a folder or an `.iso` (both work), for the Xbox 360 features (below). |

This project does not provide, link to, or explain how to obtain any game file. Use your own copy.

## Set up

1. Download the Setup disk image from the
   [latest release](https://github.com/elforeign/nfs-most-wanted-mac/releases/latest) and open it.
2. Open **NFS Most Wanted Native - Setup**. The app is not notarized by Apple, so the first time macOS blocks it: click **Done**, then go to
   **System Settings > Privacy & Security** and click **Open Anyway** next to its name. You only do this once.
3. **Choose your game folder, or the Black Edition .zip you downloaded.** Optionally choose your Xbox 360 copy. Choose where the app goes.
4. If asked, install Apple's Command Line Tools and wait for them.
5. Click **Build**. You see each step and its progress. The first build compiles the whole game on your Mac, so it
   takes a while. **Cancel** stops it and leaves anything already installed as it was.
6. When it finishes, click **Open**.

If something fails, Setup says which step failed and what to do. **Show Log** opens the full log.

## The Xbox 360 features

With your own Xbox 360 copy, Setup can add: world, audio, reverb, music, police, nis (cutscenes), vinyls, textures
and movies. At the end Setup reports which ones it could add. Without an Xbox 360 copy the PC game alone is used and
these features are simply absent. Setup only reads your copy; it never provides these files.

## Playing

- **F10** (Fn+F10 on a Mac keyboard) opens the settings menu; so does the touchpad click on a DualSense or DualShock 4.
  The same key closes it. Up/Down and Left/Right change things; Esc closes it.
- Rows marked with a restart symbol take effect after **Restart game** (Display & performance).
- **Display & performance > Online:** *Check for updates* asks GitHub once, only when you press it, and opens the
  release page if a newer version exists. *Discord status* is off by default.

## Where things are

- Your saves and settings: `~/Library/Application Support/NFSMW-port/`
- The game's logs: `~/Library/Logs/NFSMW-port/`
- Setup's own files (safe to delete after building): `~/Library/Application Support/NFSMW-Setup/` and
  `~/Library/Caches/NFSMW-Setup/`
- Setup's logs: `~/Library/Logs/NFSMW-Setup/`

## Updating

Download the newer Setup from the releases page and build again. Your game folder is only read, never changed.

## Coming from 3.x

4.0.0 is a new native recompilation and runtime, not an update of 3.x. Download the 4.0.0 Setup and build again.

**4.0.0 replaces the 3.x app and keeps it.** Setup installs **NFS Most Wanted Native.app** where the 3.x app was, so
the Dock icon carries over. The 3.x app is not deleted: it is renamed "NFS Most Wanted Native 3.x.app" (or "... 3.x (2).app"
if that name is taken) and still runs. A 4.0.0 app already there is simply replaced. Quit every copy of the game first.

**Your career comes with you.** When Setup installs 4.0.0 and 4.0.0 has no saves yet, it copies your 3.x careers
(`~/Library/Application Support/NFS Most Wanted Native/Documents/NFS Most Wanted/`) into
`~/Library/Application Support/NFSMW-port/NFS Most Wanted/` and tells you: "Your 3.x career was copied into 4.0; the
3.x copy is untouched." They are the game's own save files, so 4.0.0 loads them as they are.

- The 3.x files are only read, never moved or changed, so 3.x keeps working with them.
- Saves you already have in 4.0.0 are never replaced; then nothing is copied.
- If a 3.x save is damaged or incomplete, Setup copies nothing and says so. Your 3.x files stay as they were.
- Settings are not carried over: 4.0.0 has its own settings (F10). `Save Backups/` stays with 3.x.
