# Troubleshooting

## Setup

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

**Developer tools missing, too old, or unable to compile** — Setup builds the game with Apple's Command Line Tools
(clang and the macOS SDK; full Xcode is not needed). Install or update them in **System Settings > General > Software
Update**, or run `xcode-select --install`, then build again. The macOS SDK must be version 14 or newer.

**"The PC folder has no ... directory" / "is missing ..."** — choose the installed game folder that holds `speed.exe`
together with `GLOBAL`, `TRACKS`, `CARS`, `FRONTEND` and `SOUND`, or the Black Edition `.zip` that contains that folder. A PC
installer or PC disc image is not enough (an Xbox 360 `.iso` is fine for the Xbox 360 copy).

**"The .zip contains an unsafe path" / "a link" / "is far larger than the game" / "can't be read"** — Setup only
unpacks a `.zip` that holds plain game files. Download it again, or unzip it yourself and choose the folder.

**"Not available yet; choose an installed PC game folder"** — building from a PC disc image is not available yet.
Install the game first and choose its folder, or choose the Black Edition `.zip`. (Your Xbox 360 `.iso` works.)

**The `speed.exe` can't be used** — the port is a translation of the PC game's version 1.3 code. Other versions contain
different code and would not work. Any `speed.exe` that has the 1.3 code is accepted; the log shows the code region
that differs. Setup changed nothing.

**"There is not enough free disk space"** — free some space on the volumes you chose for the app and for Setup's
work folder (about 12 GB while Setup builds), then build again.

**"Another Setup is using this work or cache folder"** — wait for the other Setup to finish, or quit it.

**Xbox 360 copy rejected** — Setup says what is wrong with it. Choose your own complete Xbox 360 copy (a folder or an
`.iso`), or leave it out to build the PC game alone. Xbox 360 features are then absent.

**"Your 3.x saves were not copied into 4.0"** — a 3.x save was damaged or incomplete, so Setup copied none of them
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

**Discord status doesn't show** — it is off by default; switch it on in F10 > Display & performance > Online. Discord
must be running on the same Mac.

## Reporting a bug

Open an issue on [GitHub](https://github.com/elforeign/nfs-most-wanted-mac/issues), or ask in #help on the
[Discord](https://discord.gg/d6HfMwdM9v). Include your Mac model and macOS version, what you did, what you expected,
and the game's log (and Setup's log if Setup failed).
Please do **not** attach game files or saves that contain game data.
