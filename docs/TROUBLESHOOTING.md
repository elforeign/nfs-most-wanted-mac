# Troubleshooting

## Setup

**"Apple could not verify" when opening the Setup app** — it is not notarized by Apple (that needs a paid developer
account). Click **Done**, then **System Settings → Privacy & Security → Open Anyway**. You only need to do this once.
The game app it builds is made on your Mac and opens normally.

**The Setup app stopped with an error** — click **Show the log**; the last lines of `setup.log` say why. Building
again continues from the one-time analysis if that finished.

**"older than this game needs" (the compiler)** — every Mac builds the game with its own Apple compiler (the Xcode
Command Line Tools). Since 3.0.4 the setup log names it (`toolchain: Apple clang ...`) and says whether it is the one
the release was tested with; another recent one builds normally. One older than Apple clang 16 (macOS 15 SDK) stops
the setup: update the Command Line Tools in **System Settings → General → Software Update**, or run
`xcode-select --install`, then build again. Command Line Tools 16 need macOS 14.5 (Sonoma) or later: on an older
macOS, update macOS first (the Setup app itself needs 14.5 from 3.0.4 on).

**"This speed.exe can't be used"** — the port is a translation of the PC Black Edition's `speed.exe` (version 1.3,
English), so it needs that exe from your game. Other versions and languages contain different code, and the app
would not work with them. The setup changed nothing.

**"game folder incomplete"** — point the setup at the folder that contains `speed.exe` together with `GLOBAL`,
`TRACKS`, `CARS`, `FRONTEND`, `MOVIES` and `SOUND`. A disc image or installer is not enough; it must be an installed
game folder.

**The analysis step is slow** — it runs once (a few minutes on a recent Mac, longer on older ones); later setups reuse it. If it was interrupted,
run the setup again: it starts the analysis over cleanly.

**"Failed to establish a new connection" or "No matching distribution found for capstone"** — the setup could not
reach the Python package index (pypi.org) to install its build tools. This is the network, not your game folder.
Check the connection, then look for anything that filters connections: a VPN, a proxy, a firewall app such as Little
Snitch or LuLu, or a content filter. Allow Python and *NFS Most Wanted Native Setup* to connect (or pause the filter)
and click **Build** again. To check, open https://pypi.org/simple/capstone/ in a browser: if it does not load, the
network is blocking it. Running `./setup.sh` from Terminal is another way round a filter that only blocks the app.

**"unable to load libxcrun ... need 'x86_64'" or "Process 'command 'xcrun'' finished with non-zero exit value 1"**
— the setup was run by an Intel-only Python (often an old Homebrew in `/usr/local`, copied over from an Intel Mac),
which runs under Rosetta; the Command Line Tools on macOS 27 have no Intel half. Update to the latest setup: it now
skips Intel Pythons (using its own Apple Silicon Python instead) and rebuilds the environment an earlier run made.
Then click **Build** again. Nothing needs to be uninstalled.

**"The setup could not install its build tools"**, or the pypi.org message on a Mac whose internet works — up to
1.0.2 the setup used a Python 3.13 or later when it found one (Homebrew installs them), and one of its build tools
has no ready-made package for those, so installing it failed. 1.0.3 and later use Python 3.11 or 3.12, or download
their own 3.12, and remake the environment an earlier run left. Update the Setup app and click **Build** again;
nothing needs to be uninstalled. The full output is in `build/pip.log` (the Setup app's **Copy the log** button
copies what it showed).

**Download of Ghidra or Java failed** — check the internet connection and run the setup again. If a download is
blocked on your network, `./setup.sh --help` shows how to point it at copies you downloaded yourself (they are
checked against the same hashes).

**Build errors** — make sure the Xcode Command Line Tools are installed (`xcode-select --install`) and up to date,
then run `./setup.sh` again. If it still fails, open an issue with the last 50 lines of `build/setup.log`.

## Playing

**The game won't open, or quits at once** — since 2.0.4 the app says why in a message (**Show Log** opens the log in
Finder). Every start writes a log in `Logs/` in `~/Library/Application Support/NFS Most Wanted Native/`, beginning
with the app version, your macOS and your Mac; please include it when you report a problem. A crash also leaves a
report in `~/Library/Logs/DiagnosticReports` (a file starting `SpeedRecomp`).

**Black screen or crash at start** — check `Logs/` in `~/Library/Application Support/NFS Most Wanted Native/`.
A line starting `mods: rejected` means a settings file was edited by hand and is invalid; remove the line or the
file, and the defaults return.

**Stutter on the first drive** — Metal prepares shaders the first time it sees each one. The setup's shader warm-up
(step 6, off unless you tick *Prepare the shaders now* or pass `--shader-seed`) prepares them ahead; if the setup log
says "shader warm-up: skipped", run the setup again with it ticked. The game's log ends with a "shader warm final"
line: its "compiled in the draw" count is how many shaders were still prepared while you played (3.0.4). Without it the stutter settles after a few minutes and does not come back on later launches.

**The HD textures step takes hours** — that is expected: Make HD textures upscales a few thousand textures on the
GPU (about 1¼ hours on an M5 Pro, much longer on a base M1). You can stop it (Stop in the Setup app) and run the
setup again later with the option on: it carries on where it stopped. If the setup log says "HD textures: skipped",
the lines before it say why (no internet for the one-time downloads, not enough free disk space: it needs about
10 GB); the app is made without the pack and the game shows its own textures. To add the pack later, run Setup again
with *Make HD textures now* ticked (or `--hd-textures`).

**The colours changed after 3.0 (the Look setting is gone)** — 3.0 has one look, Definitive. A profile saved on the
*Xbox 360* or *PC* look opened in Definitive once; the game log says `Look Xbox 360 moved to Definitive` (or `look
saved value 2 is no longer offered` for the PC look). For the console's warm colour turn on F10 → Graphics → *Golden
colour filter*; wet roads, car reflections, glass, water and rain drops each offer their Xbox 360 version under
Advanced.

**Low frame rate at 4K** — lower *Smooth edges* to *Off*, or set the game's resolution to 2560x1440 in its Video
menu. On a TV, the TV's own refresh setting matters: 60 Hz is right for this game.

**Controller does nothing** — connect it before starting a race, check it in System Settings → Game Controllers, and
make sure the F10 menu is closed (the game ignores the controller while it is open).

**A button change on the Controls screen comes back as "Button 1"** — that was 1.0.1 and earlier, which wrote its
own layout over the screen's changes. Update to the latest version and set the buttons again (see *Change the
buttons* in the companion guide).

**"Read Me First" on the disk image will not open** — some Macs refuse to open a text file from a disk image. The
same text is in the Setup app (**Read Me** button, top right), and the guides are on the project's GitHub page.

**No sound in 5.1, or voices from the wrong speakers** — use *Stereo* in the game's Audio Mode, or turn on *Dolby
Digital (for TVs and receivers)* in F10 → Audio. Some TV/soundbar HDMI chains scramble 6-channel PCM.

**Xbox 360 textures not used** — the setup log says what it found (`Xbox 360 textures:`), and the game log says
`Xbox texture map absent` when the setup found none. The folder must be `TRACKS/TexWizardX360/` in the game folder
the setup was run on, and the setup must be run again after adding it. F10 → Graphics → Advanced → *Xbox 360 textures* must be
on.

**A short hitch the first time you see something new** — each new shader is prepared the first time it is needed and
then kept in your profile folder (`ShaderWarm/`), so it happens once per build. The setup can prepare them
ahead (step 6, the shader warm-up; off by default since 3.0); if the setup log says "shader warm-up: skipped", it did
not, and the first drive hitches once as before.

**An old app can't be moved to the Trash ("locked")** — an app built before 2.0.4 can hold copies of game files
that have the Finder *Locked* flag (building again with 2.0.4 or later unlocks and replaces it by itself). In Terminal: `chflags -R nouchg "/Applications/NFS Most Wanted
Native.app"` (or wherever the app is), then delete it or build again. Apps built by 2.0.4 or later don't need this.

**Sparks and light trails missing** — the log line starting `core.nfsmw: sparks and light trails` says why; usually
`XenonEffects.tpk` wasn't in the game folder's `scripts` folder when the setup ran (add it and build again).

**Light pools missing** — F10 → Graphics → Advanced → *Light pools from lamps* must be on;
the log line starting `core.nfsmw: light pools` says whether the setup built them.

## Reporting a bug

Open an issue with: your Mac model and macOS version, what you did, what you expected, and the log from `Logs/`
(or ask in #help on the [Discord](https://discord.gg/d6HfMwdM9v)). **Settings → Mods → Help → Save bug report** in the game
gathers the logs and the setup log for you. The log's `[host] built with clang ...` line and the setup log's
`toolchain:` line say which compiler built your copy: please keep them in.
Please do **not** attach game files or saves that contain game data.
