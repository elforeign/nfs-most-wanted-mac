# Changelog

## 3.0.3 — the right resolution for your screen, 120 Hz on any screen, and a crash guard

- **Auto resolution sees your screen:** Auto (and the list's own entry for your screen) now reads the screen the game
  is on. Before, it never got the screen's size and fell back to 1280x720 on every Mac.
- **MacBook Pro sizes below the notch:** 3024x1890 (14") and 3456x2160 (16") are in the resolution list, and on a
  MacBook Pro with a notch "your screen" is the area below it, which full screen fills pixel for pixel. Thanks to
  **Simonxdd**, whose pull request (#10) added the two sizes.
- **120 Hz on the screen the game is on:** the frame pacing reads the refresh of the screen the window is on, not
  only the main display's, so a 120 Hz TV next to a 60 Hz main display (or a ProMotion panel) is paced at 120.
- **Start-up crash (#8):** on some Macs something writes into the game's sound buffer object about two seconds after
  start. That object now sits on memory that refuses writes: the write is stopped, the game goes on, and the log
  names the code that tried (a line starting "heap: a write to the dsound buffer object").

## 3.0.2 — a start-up crash, steadier 60 fps and controller prompts

- **Start-up crash (#8):** on some Macs the game quit about two seconds after it started (seen with a monitor that is
  also the sound output). While the game's sound starts, a few bytes are written just outside the memory they belong
  to, and here that memory sat right against the sound buffer's own record. Every sound buffer and game thread stack
  now has a spare margin around it, so those bytes land on nothing. If it happens, the log says where (a line starting
  "heap guard").
- **Steadier frame pacing:** with a 60 fps limit on a 120 Hz or 60 Hz display, the game now hands the display one new
  frame per refresh slot, on the display's own clock. Before, its separate timer drifted against the display, so now
  and then two frames met in one slot and one was dropped (about one every few seconds): a reading just under 60 and
  a slight unevenness. The log notes once a minute how many frames were shown, dropped and repeated.
- **Controller prompts no longer blank:** with a controller, the menus switched to PlayStation button symbols, which
  come from the optional XtendedInput file (`XtendedInputButtons.tpk` in the game's GLOBAL folder). Without that file
  the button bar was empty; now the PC prompts stay, and the symbols show when the file is there (or in DataMods).

## 3.0.1 — Discord status

- **Discord status** (Interface → Discord status, **off** by default): while the Discord app is open, your Discord
  profile shows "Playing NFS Most Wanted Native", whether you are driving or in the menus, your time played and a
  "Play it on a Mac" button for your friends. It talks only to the Discord app on your Mac; off shares nothing, and
  switching it off clears the status at once. Quitting never waits on Discord. Thanks to **prex-xo**, whose pull
  request (#9) this is ported from.
- **Updates keep your HD textures and prepared shaders:** the Setup app ticks *Make HD textures* and *Prepare the
  shaders* when an earlier build made them, and remembers your last choice (an untick stays unticked). The HD textures
  are reused; the shaders are prepared again for the new version. Before, an update with the boxes unticked built the
  game without them.
- **Security updates for the build tools**: Pillow 12.3.0 and pytest 9.0.3 (the GitHub security alerts on Pillow
  11.3.0 and pytest 8.4.2). Setup downloads the new versions the next time it runs; nothing else changes.

## 3.0.0 — HD textures made on your Mac

- **One look:** the *Look* setting is retired; Definitive is the only look. F10 → Graphics → **Golden colour filter**
  (Off / On, Off by default) gives the Xbox 360's warm colour, and wet roads, car reflections, glass, water and rain
  drops keep their Xbox 360 versions as their own choices. A profile saved on the *Xbox 360* or *PC* look opens in
  Definitive once (the log says so; from the Xbox 360 look the golden colour filter stays on).
- **Setup: playable in about 10 minutes**: the HD textures and the shader warm-up are optional steps, off by default;
  tick them in the Setup app, or run it again later with them ticked (the build is reused).
- **HD textures made by Setup** (2.1 work): the optional "Make HD textures" step (about 1¼ hours on an M5 Pro) builds the Remaster HD
  pack on your Mac from your own game files, then the road surface detail and the sunlit wall relief from it.
- **HD HUD**: one switch (Interface → HD HUD, on): sharp HUD, minimap and fonts made by Setup from the game's own art
  in a few minutes, with the gauges and icons redrawn as vector art (our own). Setup's "Make the HD HUD" checkbox.
- **Settings menu by what you want to change**: pages Graphics, Display, Audio, Interface, Gameplay, Mods, Controls;
  Graphics → Advanced by effect; every setting says what it does, what you see, its cost and a slower-Mac tip; about
  twenty fine-tuning rows no longer listed (saved values kept). The settings guide follows the menu.
- **Wall sun sheen** (Graphics → Advanced, on): in low sun, concrete, brick and stone walls catch a soft highlight, and
  bare metal (railings, guard rails, poles, girders, storm grates) a sharper glint; painted metal, shade, night and the
  mirror are unchanged. Needs the HD textures' road surface detail. The shader warm-up covers it.
- **Mac preset** (Graphics, Auto by default): Auto picks Full, Balanced or Light from your Mac's chip and screen size;
  Balanced and Light upscale the picture and turn off the costliest effects on slower Macs. Your own settings are
  kept and come back with Full.
- **Data mods** (Mods → Data mods, on): game files you put in the `DataMods` folder (next to `Mods` in your profile
  folder) replace the game's own while it runs: cars, tracks, menus, sounds. Your game folder is never changed, and
  Launch copies your saves first whenever that folder changes.
- **A little less work per frame**: the game's main thread does about 0.2 ms less per frame (hooked calls, the draw
  path, and a few of the game's busiest routines rewritten natively; each change has its own off switch).
- **Fixed: a crash about two seconds after start** on Macs whose sound goes to a monitor (or another output that
  takes a moment to start): the game no longer restarts its sound when the output is slow to begin (issue #8).
- **Shader warm-up after the packs**: the warm-up now runs with the HD pack and materials loaded, so their shaders are
  prepared too.
- **Fixed: the shader warm-up could skip itself**: its offscreen drives pressed menu keys on a timer, so on a Mac
  where the game reached its title sooner than expected the keys landed on the wrong screens and the warm-up gave up
  ("the first drive did not reach a race"). Each key now waits for the screen it belongs to and stays down until
  the game has read it.

## 2.0.4 — the Xbox 360 front end

- **The Xbox 360 Stuff Pack's front end, optional**: the Xbox 360 menus, title and loading art and garages, from your
  own download of the Stuff Pack. Put the unzipped *Easy Installation* folder in your game folder (or choose it in the
  Setup app) and build. Only the pack's front-end files are used, each one checked; the setup lists everything it
  leaves out. Your game folder is never changed.
- **F10 → Xbox 360 → Xbox 360 front end** turns it off (the PC menus come back at the next start); the *PC* look
  starts with the PC front end too.
- **Shader warm-up in the setup**: the setup now prepares the game's shaders on your Mac (about 35 minutes, offscreen
  Quick Races, no window), so the first drive no longer stutters while they are made. Optional (`--no-shader-seed`, or
  the checkbox in the Setup app).
- **Mods folder for texture mods**: drop a texture mod's folder (`.dds` files: HUD art, fonts, logos, road and
  building textures) into `~/Library/Application Support/NFS Most Wanted Native/Mods` and start the game; no rebuild.
  Files replace the game textures with the same name, in every Look; several mods load in A to Z order and a folder
  starting with `_` is skipped. `Mods/mods-report.txt` lists what loaded and why anything did not. The setup makes a
  list of your game's texture names (numbers and sizes only) so the report can check names and shapes. F10 →
  Graphics → Advanced → **Player mods** turns it off. See the companion guide's *Optional mods*.
- **Saves and the Stuff Pack**: installing the Xbox 360 Stuff Pack over the game folder replaces its gameplay data,
  and the game then refuses careers saved with the original files ("appears to be damaged"). The setup now says so
  when it sees this; the guide says how to get those careers back. The 2.0.4 front end (taken from the download)
  does not change gameplay data.
- **Your saves are copied before each new build is first played**: `Save Backups/` in your profile folder keeps the
  ten newest copies; the guide says how to bring one back.
- **If the game won't start, it says why**: a message with the reason and a **Show Log** button when the start stops
  before the game or the game quits within its first seconds; every start's log now begins with the app version,
  macOS and Mac model, for reports.
- **Locked game files no longer block an update**: if some files in your game folder have the Finder *Locked* flag,
  the app's own copies are unlocked, and the setup unlocks an earlier app before replacing it, so building again
  works. To drag an app built by 2.0.3 or earlier to the Trash, see the troubleshooting guide. Your game folder is
  not changed.

## 2.0.3 — the game's own movies

- **The PC game's own movies play**: the EA logo, the PSA and the intro at start-up, and the story scenes, tutorials
  and Blacklist videos in the career, from the `MOVIES` folder of your game. They never played before: the game asks
  for the US (`_ntsc`) movie names, and most PC copies have the European (`_pal`) ones, so it found nothing. The app
  now plays either, natively (the same movie player as the Xbox 360 movies, now with the EA movie format), with the
  picture zoomed to fill a widescreen display as on PC.
- **Converted Xbox 360 movies** (the community *Xbox 360 Converted Movies* pack, in your game's `MOVIES` folder) play
  too, fitted to the screen, with the EA HD bumper at start-up.
- Your own Xbox 360 disc movies (2.0.2) still come first for every title they cover.

## 2.0.2 — Xbox 360 movies

- **Your own Xbox 360 movies, optional.** Put your copy of the US Xbox 360 disc's movies in an `X360-Movies` folder
  in your game folder (or choose it in the Setup app) and build: the game plays the Xbox 360 intro with the EA HD
  bumper and the story scenes from them. Only the exact original files are
  used (each one's size and checksum are checked); the movies are not part of this download.
- Without them, the boot keeps the PC game's own movie order (2.0 asked for the Xbox 360-only EA HD bumper there). The
  PC game's own movies still don't play in this version; the guide now says so.
- Setup's image library (Pillow) updated to 12.3.0 (security fixes).

## 2.0.1 — fixes

- **Upscaling no longer darkens the picture.** With *Upscaling* on (Quality or Performance), the mid-tones were about
  10 % darker than at native resolution: ambient occlusion read a stair-stepped copy of the upscaled scene's depth
  and shaded every slope (roads, walls). The depth copy is now smooth on surfaces and sharp at edges; the upscaled
  picture matches native brightness. Native resolution is unchanged.
- **A rare crash at start-up is fixed** (about 1 start in 900, a few seconds after the sound starts; the game closed
  without a report): the list of the game's threads could be changed while another thread was reading it.
- **Updating rebuilds the game from scratch once.** The Setup kept the earlier version's compiled files to save time,
  and in some cases parts of an earlier build were mixed into the new one (the likely cause of a black screen with a
  nearly empty F10 menu after updating to 2.0.0). A new version now never reuses them.
- A crash message now also names where in the app's own code the fault happened, to make reports easier to follow.

## 2.0.0 — Definitive Edition

- **Look** (Definitive / Xbox 360 / PC) replaces *Overall look*; every finished improvement follows it, and each keeps
  its own switch under Advanced.
- **Definitive look, extended:** smooth leaves and fences, soft particles, natural wet roads, road sun sheen, car
  reflections, building glass, water, wall depth, ambient occlusion, sun rays, lamp glow, softer
  shadows, garage floor shadows, denser tyre smoke and the Stuff Pack's extra smoke layers; a golden colour filter
  (Auto / On / Off).
- **Day and weather cycles** (60 and 20 minutes; both optional; the weather starts at a random point each session).
- **Real HDR** picture, tone-mapped to your TV's range; with HDR on, 4x anti-aliasing runs as 2x.
- **Anti-aliasing** is Off / 2x / 4x (default Off; a saved 4x from 1.0 is moved to Off once).
- Settings menu in seven pages (Graphics, Display, Audio, Xbox 360, PC, Mods, Controls); an Xbox 360 page; rumble
  style, cobbles and metal rumble and a crash fade; Dolby Digital on by default for new profiles. Settings still in
  development are listed, marked *(in development)*, and stay off.
- More Extra Options: always rain, all police on the minimap, bounty / prologue / Busted-scene fixes, one-lap
  percent, HUD on look-back, subtitles, heat keys, World Map in races, no catch-up.
- Retired: the scene-depth blur switch (always on), mirror glow, grass blades.
- Setup: the rebuilt effects are now assembled at setup from `effects/` (nothing prebuilt in the repository), the
  light pools also cover the tunnel props, and the Xbox 360 Stuff Pack's texture list is converted for the new
  *Xbox 360 textures* setting.
- Known issues: the first minutes of driving after a new build may hitch once per new shader; rain at 4K with HDR and
  2x anti-aliasing is near the GPU's limit on an M5 Pro; the mouse pointer is offset after resizing the window to
  another aspect ratio. No HD texture pack yet (a later release makes it on your Mac).

## 1.0.3 — setup fix for newer Pythons

- **Setup stopped with the pypi.org message on Macs with Python 3.13 or later** (for example from Homebrew), although
  the internet was fine (#5): one build tool has no ready-made package for those Pythons. The setup now uses Python
  3.11 or 3.12 (or downloads its own 3.12), remakes the environment an earlier run left, installs ready-made packages
  only, and says plainly whether the network or something else stopped it (the full output is in `build/pip.log`).
- The Setup app has a **Copy the log** button for bug reports.

## 1.0.2 — change any button

- **Remap your controller in the game's own menu:** Options → Controls, press R2 for the Controller page, pick an
  action and press the button you want. Changes stay (1.0.1 put its own layout back, so a remapped button returned
  as "Button 1") and are saved with your profile. Circle puts this port's layout back.
- The Controls screen names your controller's buttons (*Cross / A*, *R2 / RT*, *Left Stick Up*) and, on the keyboard
  page, your keys (it showed none).
- A trigger is a full analog pedal for any action you give it.
- The Setup app shows its Read Me itself (**Read Me** button), for Macs that will not open the text file on the disk
  image.

## 1.0.1 — setup fixes

- **Setup on Macs with an Intel-only Python** (for example an old Homebrew in `/usr/local` from an Intel Mac): the setup
  stopped at Ghidra with "unable to load libxcrun ... need 'x86_64'" on macOS 27, whose Command Line Tools have no Intel
  half. It now uses only a native Apple Silicon Python (or its own), and rebuilds an environment an earlier run made.
- **Blocked connection to pypi.org:** the setup retries longer and, if the package index stays unreachable, says so
  plainly (VPN, proxy or firewall app) instead of pip's "No matching distribution found". Checking the game folder
  (`--check-only`) no longer downloads anything.
- **Older FFmpeg installed elsewhere** (for example in `/usr/local/include`): the build always uses the bundled FFmpeg's
  own headers (thanks DarthMDev).

If 1.0 stopped partway for you, open the new Setup and click **Build** again: it continues where it can.

## 1.0.0 — first public release

The native Mac port of Need for Speed: Most Wanted (2005), with the setup kit that builds it from the player's own
PC game folder. See the README for the feature list.

- **Setup app:** a small Mac app (on the release page) that builds the game from your own game folder: choose the
  folder, click Build, then open the game. It installs Apple's Command Line Tools if needed, shows the progress and
  keeps the Mac awake. `./setup.sh` in Terminal does the same.
- Rebuilt post-process effects (glow, auto brightness, motion blur, depth of field, edge darkening, shadow lift)
  run on the port's own effects host and need only the PC game.
- Light pools are built at setup from the PC game's own lamps and effects.
- Optional: TexWizard texture packs (for example the Xbox 360 Stuff Pack's textures) and the XenonEffects sparks
  and light trails, converted by the setup from the player's own copies.
- Skip the music track while driving (T, or L3 on a controller), as Extra Options' SkipTrackAnywhere; both on by
  default.
- Extra Options camera and screenshot keys (all off by default): freeze camera (F9 or Pause), free camera
  (Backspace), light keys (H, O), save/load positions; plus unlock everything and barrier removal.
- Fix: a draw using a volume texture with a level-of-detail clamp no longer shares its state version with the
  pipeline, so the render queue can no longer carry its raised mip level into the next draw (or reuse an older
  table for it).
