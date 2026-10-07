# Changelog

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
