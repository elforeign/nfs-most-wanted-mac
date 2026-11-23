# Getting started

**Best of all worlds:** The PC game on your Mac, with console-inspired bloom, colour and car detail already included, plus more Xbox 360 features from your own console files. Setup builds from your own files on your Mac; nothing is uploaded or meant to be shared.

## What you need

| | |
|---|---|
| **Mac** | Apple Silicon (M1 or later) with **macOS 14.5 or later** (provisional). |
| **Xcode Command Line Tools** | Apple's free developer tools. Setup offers to install them; in Terminal, `xcode-select --install`. |
| **Disk space** | About 12 GB free while Setup builds; the installed game then uses about 9 GB (3.5 GB without the Xbox 360 copy). |

## What you can use

- Choose your own installed PC game folder, or pick its `speed.exe`. You need version 1.3.
- Or choose a `.zip` or `.7z` of that folder, or your original PC discs as `.iso` images or mounted/inserted discs. For older discs, choose the official 1.3 patch and a compatible 1.3 `speed.exe` when Setup asks; the disc executable is copy-protected.
- Optional: add your own Xbox 360 files as an `.iso`, extracted folder, `.zip` or `.7z` for more console features. They do not replace the PC game.
- Have a `.rar` or `.bin/.cue`? Unpack it to a folder first.

Stuck? See [Troubleshooting](TROUBLESHOOTING.md) or ask on [Discord](https://discord.gg/d6HfMwdM9v).

This project does not provide, link to, or explain how to obtain any game file. Use your own copy.

## Set up

1. Download the Setup disk image from the
   [latest release](https://github.com/elforeign/nfs-most-wanted-mac/releases/latest) and open it.
2. Open **NFS Most Wanted Native Setup**. If macOS says it can’t verify Setup, click **Done**.
   Go to **System Settings > Privacy & Security > Open Anyway** for Setup.
   Confirm that you want to open Setup.
3. Choose your PC game using an option above. For discs, Setup finds the other discs beside disc 1 and asks for any it can't find. Optionally add your Xbox 360 files and choose where the app goes.
4. If asked, install Apple's Command Line Tools and wait for them.
5. Click **Build**. You see each step and its progress. The first build compiles the whole game on your Mac, so it
   takes a while. **Cancel** stops it and leaves anything already installed as it was.
6. When it finishes, click **Open**.

If something fails, Setup says which step failed and what to do. **Show Log** opens the full log.

## Added cars (optional)

Use your own installed PC game folder for **Carbon**, **ProStreet** or **Undercover**.
For **Carbon on Xbox 360**, use an extracted game folder, an `.iso`, or a `.zip` or `.7z`
containing one of those.

1. In Most Wanted Setup, go to **Extras from your other games**, just after the Xbox 360 step.
   If Most Wanted is already installed, open **Manage installed extras…** on Setup’s welcome screen.
2. Under **Later-game source**, click **Choose…**. Tick the cars you want, click **Retry check**,
   then **Import selected extras**. Repeat for each source you want to use.
3. In Most Wanted, open **F10 > Cars & cutscenes** (Fn+F10 on some Mac keyboards), turn on
   **Cars from later games**, then quit and reopen the game.

The cars appear in Quick Race car selection and the Career car lot. Each uses the price and
Blacklist unlock requirement of the Most Wanted car it’s based on.
Setup keeps verified car inputs with your installed data, so deleting its cache leaves
those inputs available for updates and additional imports.

## Export one app for backup

Quit the game, open Setup and choose **Export all-in-one app…** (also available after a build).
Choose your installed game app, then a new `.app` name and location. Setup copies the app and every installed
asset root into its resources, then signs and verifies the completed bundle. You can move or rename that app;
it uses its own game files even if an old game-folder preference remains. Missing embedded files require a new export.

On the same APFS volume, Setup uses clones that initially share file blocks with the install. Other filesystems
and volumes use copies checked with SHA-256. The result shows elapsed time, allocated file blocks (including
shared blocks), and the observed change in volume free space (including metadata and other disk activity).
Allow room for a complete copy when backing up to another volume; clones do not make that backup free.

Exports display **Re-export from Setup to update**. Build the new version with Setup, then choose the old export
again and press **Update this export**: Setup replaces it only after the new copy is complete and signed. Exports
carry the cars you added. **For a rollback copy, export under a different name** and keep the previous export.
**Saves and settings stay in `~/Library/Application Support/NFSMW-port/`** and need their own backup.
An app backup alone does not include your careers. Exports are personal backups containing your game files.

Terminal equivalent:

```sh
./setup.sh --export-all-in-one --app "/Applications/NFS Most Wanted Native.app" --dest "/Volumes/Backup/NFS Most Wanted All-in-one.app" --events
```

## The Xbox 360 features

With your own Xbox 360 copy, Setup can add its world, music, police radio, cutscene sound, vinyls, textures and movies. Setup reports what it could add. Console-inspired looks are already included even without those files. Setup only reads your copy; it never provides these files.

## Playing

- **F10** (Fn+F10 on a Mac keyboard) opens the settings menu; so does the touchpad click on a DualSense or DualShock 4.
  The same key closes it. Up/Down and Left/Right change things; Esc closes it.
- Rows marked with a restart symbol take effect after **Restart game** (Display & performance).
- **Help > Guide** explains the settings, look presets and controls.
- **PlayStation pedals:** bind Accelerate to R2 and Brake to L2 in the game's Options > Controls.
- **Online free roam:** not available in 4.5; it moves to a shared server with room codes in a later update.
- **Display & performance > Online:** *Check for updates* asks GitHub when you press it. *Update now* prepares an available update for the next launch. *Discord status* is off by default.

## Where things are

- Your saves and settings: `~/Library/Application Support/NFSMW-port/`
- The game's logs: `~/Library/Logs/NFSMW-port/`
- Setup’s saved choices: `~/Library/Application Support/NFSMW-Setup/source-choices.json`. Keep this file for Update now, along with the original PC and any selected Xbox 360 sources.
- Setup’s cache: `~/Library/Caches/NFSMW-Setup/` (can be deleted after building; later builds may need to download or prepare files again).
- Added-car repair inputs: `~/Library/Application Support/NFSMW-port/car-inputs/`. Keep these with the installed cars; clearing Setup’s cache leaves them intact.
- Setup's logs: `~/Library/Logs/NFSMW-Setup/`

## Updating

In 4.3, use **F10 > Display & performance > Online > Check for updates**, then **Update now** when offered. Follow the status, quit and reopen when ready. Cancel update stops it before the next launch; Retry update starts again after a failure or cancellation.

Keep Setup’s saved source choices and the sources they point to. If they moved or were deleted, run the latest Setup and select them again. Careers and settings are kept.
Updates automatically refresh selected extra cars when their conversion tools or approvals change.
If a car refresh fails, the previous verified cars stay active and the app update proceeds.

**From 4.2.0 or earlier:** download the newer Setup from the releases page, quit the game and build again manually. The 4.2.0 update check opens a browser; it cannot prepare updates. Your game folder is only read, never changed.

## Coming from 3.x

4.0.0 is a new native recompilation and runtime, not an update of 3.x. Download the latest Setup and build again.

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
