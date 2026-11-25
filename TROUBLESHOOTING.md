# Troubleshooting

## Setup

**An all-in-one export says "Re-export from Setup to update"** — automatic updates are disabled for exports.
Run the latest Setup to build the normal install, then choose the same export again and press **Update this export**
(Setup replaces it only after the new copy is complete and signed). To keep a rollback copy, export under a new name instead. Setup also refuses to install over an exported app; choose a separate
location for the normal install. Saves and settings remain in Application Support and are backed up separately.

**"The export destination already exists and is not a verified export of this game"** — choose a new `.app` name, or an
earlier export of this game to update it. Setup only replaces a verified export of the same game, never another app.
If the export reports missing, linked or damaged assets, repair the normal install with Setup and export again.
Changing the saved game folder or holding Option does not redirect an exported app to external files.

**An export uses more space on the backup drive** — APFS clones share blocks only on the same volume.
Copies to another volume need the full data size and are checked with SHA-256. The export result distinguishes
allocated file blocks (which include shared blocks) from the observed volume free-space change. Other disk
activity can affect that change. **Show Log** includes the file counts and byte measurements.

**"Apple could not verify" when opening the Setup app** — it is not notarized by Apple. Click **Done**, then
**System Settings > Privacy & Security > Open Anyway**. You only need to do this once. The game app it builds is made
on your Mac.

**Setup stopped with an error** — it names the step, says what to do, and **Show Log** opens the full log. Logs are in
`~/Library/Logs/NFSMW-Setup/`. A failed build installs nothing and leaves any earlier install as it was.

**"Setup requires native Apple Silicon macOS"** — run the Apple Silicon version of Setup, not under Rosetta. If you
run `setup.sh` in Terminal, turn off **Open using Rosetta** for Terminal (Finder > Get Info) or use
`arch -arm64 ./setup.sh ...`. Intel Macs are not supported.

**"Intel-only (x86_64) developer tools found"** — Setup builds only with Apple's own tools in `/usr/bin`; the message
names the Intel-only tool it found. Install Apple's Command Line Tools (`xcode-select --install`), then build again.

**"Apple's developer tools won't run until the Xcode license is accepted"** — you have full Xcode, and it has not been
opened since it was installed or updated. Open Xcode once and accept the license, or run
`sudo xcodebuild -license accept` in Terminal, then build again.

**"Apple's Command Line Tools are not installed, or a macOS update removed them"** — in Setup click **Install…**, or run
`xcode-select --install` in Terminal. A major macOS update can remove them; install them again the same way.

**Developer tools missing, too old, or unable to compile** — Setup builds the game with Apple's Command Line Tools
(clang and the macOS SDK; full Xcode is not needed). Install or update them in **System Settings > General > Software
Update**, or run `xcode-select --install`, then build again. The macOS SDK must be version 14 or newer.

**"The PC folder has no ... directory" / "is missing ..."** — choose the installed game folder that holds `speed.exe`
together with `GLOBAL`, `TRACKS`, `CARS`, `FRONTEND` and `SOUND`, or pick `speed.exe` in that folder. A `.zip` or `.7z` of the folder works too. A PC installer is not enough. For original PC discs, choose **PC disc images** (an `.iso` or a mounted/inserted disc). Older discs need the official 1.3 patch and a compatible 1.3 `speed.exe`. For `.rar` or `.bin/.cue`, unpack it to a folder first. Optional Xbox 360 files (`.iso`, extracted folder, `.zip` or `.7z`) add features; they do not replace the PC game. Stuck? Ask on [Discord](https://discord.gg/d6HfMwdM9v) or [GitHub](https://github.com/elforeign/nfs-most-wanted-mac/issues).

**"The .zip contains an unsafe path" / "a link" / "is far larger than the game" / "can't be read"** (or the same for a
`.7z`) — Setup only unpacks an archive that holds plain game files. Download it again, or unzip it yourself and choose the folder.

**"Disc N of the set ... was not found"** — Setup looks for the other discs beside the one you chose. It shows a
**Disc N** row for each disc it couldn't find: choose that disc there, from any folder and under any name (Setup reads
each disc's number from the disc itself). Or keep the `.iso` images of all your discs in one folder.

**"Missing: official 1.3 update. Choose it above."** — your discs have an earlier version of the PC game than 1.3.
Also choose the game's official 1.3 update (`nfsmwpatch1.3.exe`) in the **1.3 update** row. Setup applies it to its own
copy of the game; it only reads the update and never runs it.

**"Missing: a speed.exe that has the version 1.3 code (the one on the discs and in the update is copy-protected). Choose it above."** — the `speed.exe` on your discs and in the 1.3 update is
copy-protected, and Setup can't translate copy-protected code. In the **speed.exe** row, choose a `speed.exe` that has
the 1.3 code; Setup checks it the same way as for a game folder.

**"Setup can't read this update" / "does not match its checksum"** — the chosen file is not the official 1.3 update,
or it is damaged. Download the update again.

**"The disc image is truncated" / "not a disc image Setup can read"** — the image is incomplete (for example an
interrupted download) or in a format Setup doesn't read. Make the image again, or mount it in Finder and choose the
mounted disc.

**The `speed.exe` can't be used** — use a `speed.exe` that matches the supported PC 1.3 executable fingerprint.
Setup checks the executable layout, code and loaded data. The log shows which parts differ. Setup changed nothing.

**"Not enough space on ...: N GB more needed"** or **"There is not enough free disk space ..."** — free that much space
on the volume the message names, then press **Retry**. Setup needs about 16 GB while it builds from a PC game folder,
and more with an Xbox 360 copy; the Review page shows the amount for your choices.

**"macOS did not allow Setup to read ..."** — open **System Settings > Privacy & Security > Files and Folders** and
allow Setup (or Terminal, if you run `setup.sh`) to use that folder. For an external drive, allow **Removable Volumes**.
Or move the game out of Desktop, Documents and Downloads, then try again.

**"Another Setup is using this work or cache folder"** — wait for the other Setup to finish, or quit it.

**Xbox 360 copy rejected** — Setup says what is wrong with it. It supports the USA and the European (PAL) retail
discs ("not a supported NFS:MW retail build" means another release, or a modified or damaged executable). Choose your
own complete Xbox 360 copy (a folder or an `.iso`), or leave it out to build the PC game alone. Xbox 360 features are
then absent.

**"Your 3.x saves were not copied into this version"** — a 3.x save was damaged or incomplete, so Setup copied none of them
and changed nothing. The message names the 3.x folder. With the game closed you can copy a career folder (it holds a
file of the same name) into `~/Library/Application Support/NFSMW-port/NFS Most Wanted/` yourself, or start a new
career. Setup never copies over saves 4.0.0 already has.

**"Dependency checksum failed" or "Setup source is missing ..."** — the downloaded Setup is damaged or incomplete.
Download it again from the releases page.

## Playing

**The game won't open, or quits at once** — look at the newest log in `~/Library/Logs/NFSMW-port/`
(`NFSMW.log`; the run before is `NFSMW.previous.log`). It begins with the app version and your Mac. Please include it
when you report a problem.

**The F10 menu doesn't open** — on a Mac keyboard press **Fn+F10**. On a DualSense or DualShock 4, click the touchpad.

**Check for updates says nothing new** — it asks GitHub only when you press it. "No answer: press to retry" means it
could not reach GitHub.

**Update now failed or asks for sources** — choose **Open log** in Display & performance > Online. Keep the original PC and any selected Xbox 360 sources and `~/Library/Application Support/NFSMW-Setup/source-choices.json`. If missing or moved, run the latest Setup and select them again, then retry. In 4.2.0, the update check only opens the browser: quit the game and run the new Setup manually.

**The triggers shift gears** — on PlayStation bind Accelerate to R2 and Brake to L2 in the game's **Options > Controls** (Control type 1 uses your saved bindings). See **Help > Guide** for layouts and DualSense effects.

**Discord status doesn't show** — it is off by default; switch it on in F10 > Display & performance > Online. Discord
must be running on the same Mac.

## Reporting a bug

Open an issue on [GitHub](https://github.com/elforeign/nfs-most-wanted-mac/issues), or ask in #help on the
[Discord](https://discord.gg/d6HfMwdM9v). Include your Mac model and macOS version, what you did, what you expected,
and the game's log (and Setup's log if Setup failed).
Please do **not** attach game files or saves that contain game data.
