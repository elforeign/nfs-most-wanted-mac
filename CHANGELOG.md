# Changelog

## 4.3.0 (unreleased)

- **In-game Guide:** open **F10 > Help > Guide**, with keyboard or controller navigation.
- **Look presets:** choose Port, PC retail, Xbox 360 retail or My picks. Picture edits are saved as My picks. The full Xbox 360 look needs your own Xbox 360 files. Both retail presets turn HDR, contact shadows and soft smoke Off.
- **Update now:** download, verify and prepare an update for the next launch, with progress, cancellation, retry and rollback if startup fails. The original sources remembered by Setup must remain available. From 4.2.0, download the new Setup through the browser and run it manually first.
- **HDR Auto:** decides at launch whether to use HDR on the display, with SDR fallback. Restart to re-check display support; HDR status shows the current output.
- **Native full screen:** optional full screen in its own macOS Space so Game Mode can engage. Set Window mode to Full screen and restart; Borderless remains the default. Known issue: in long, busy scenes the graphics chip can stay at its slowest speed in Native; switch back to Borderless if the game slows down.
- **DualSense effects built in:** adaptive triggers, trigger road feedback, lightbar and player indicators are enabled by default on supported controllers.
- **PlayStation Pedals selector:** new Controls > Triggers > Shift/Pedals choice, with Shift the default. Pedals assigns R2 gas, L2 brake/reverse, Cross shift up and Square shift down. Proportional remapped triggers were already supported in 4.2; DualSense pedal resistance now follows the bindings.
- **Online free roam beta:** Off until Host or Join; private join codes, host UDP/firewall permissions and router setup are explained in the Guide. Some networks cannot host; there is no internet relay fallback. Ghost mode is available in F10 or with D-pad Down/G. Sessions have a 60-fps ceiling and show up to six nearby remote cars.
- **Headlamp glass:** Picture offers Retail (default) or Clear covers over the reflectors.
- DualSense effects are cleared on disconnect and game close.
- Reduced F10 redraw work and optimized cubemap mip preparation with identical output.
- Fixed missing Showcase and shoulder-button prompts.
- **Smooth cut-out edges:** optional smoothing for fence, leaf and grass cut-outs, Off by default. Requires anti-aliasing at 2x or higher.
- Contact-shadow shader preparation runs outside rendering to reduce startup pauses.
- **Problem reports:** preview text before opening a GitHub issue draft or copying it for Discord. You submit the post and add attachments yourself.
- Reject malformed HUFF compressed input with overflowing symbol counts.
- Online Host keeps running after an F10 Restart.
- Fixed Stereo and PCM audio dropouts on outputs with a large device buffer (some TVs).
- With Xbox 360 files, cops in the busted scene keep their arms.
- Problem reports keep your own words and clock times; join codes stay masked.

## 4.0.1 — Setup fix

- **Setup accepts your game folder again.** In 4.0.0 the folder picker only enabled Choose for a `.zip`, so a game
  folder (for example the one you used with 3.x) could not be selected
  ([#19](https://github.com/elforeign/nfs-most-wanted-mac/issues/19)). Both a folder and a `.zip` work now.
- Setup's text describes the `.zip` option as a `.zip` of your game folder.

If 4.0.0 is already built and running for you, nothing changes: 4.0.1 only fixes Setup.

## 4.0.0 — a new native version, built on your Mac

4.0.0 replaces 3.x. It is a new native recompilation and runtime, not an update of 3.x, so there is nothing to patch:
download the 4.0.0 Setup and build again.

**What 3.x users should do:** download the 4.0.0 Setup from the
[latest release](https://github.com/elforeign/nfs-most-wanted-mac/releases/latest), run it, and build. Your game folder
is only read. 4.0.0 installs as "NFS Most Wanted Native.app", replacing the 3.x app in place so the Dock icon carries over, and
Setup keeps the 3.x app as "NFS Most Wanted Native 3.x.app" (never deleted) until you are happy with 4.0.0.

**Your 3.x career carries over.** If 4.0.0 has no saves yet, Setup copies your 3.x careers into 4.0.0 (the 3.x files
are only read, never changed) and tells you. Saves already in 4.0.0 are never replaced. Settings are not carried over.
See [Getting started](GETTING_STARTED.md#coming-from-3x).

### New

- **Setup builds the game on your Mac** from your own PC game folder, or a `.zip` of that folder, version 1.3. Any `speed.exe` with the 1.3 code is
  accepted. Setup checks it first and changes nothing if it can't be used. No game files are shipped.
- **Optional Xbox 360 features from your own Xbox 360 copy** (a folder or an `.iso`): world, audio, reverb, music,
  police, nis (cutscenes), vinyls, textures and movies. Setup reports which it could add. Without the copy the PC game
  alone is used.
- **F10 settings menu** (Fn+F10 on a Mac keyboard; touchpad click on a DualSense or DualShock 4) over the game.
- **Discord status** (off by default) and **Check for updates** (only when you press it), in F10 > Display &
  performance > Online. The Discord status is ported from the 3.x work of prex-xo.
- **Controllers:** DualSense and DualShock 4 detected automatically, PlayStation and Xbox layouts, button prompts that
  follow your controller, rumble.
- **Audio:** the game's 5.1 mix, with optional Dolby Digital, Dolby Digital Plus and TrueHD bitstream output.
- Setup bundles its own Python and a pinned FFmpeg 7.1.1 build (LGPL, no GPL or nonfree parts); see
  [NOTICE](NOTICE).

### Needs

Apple Silicon; macOS 14.5 or later (provisional); Xcode Command Line Tools; about 12 GB free (provisional); your own
installed PC game, version 1.3.

### Not available yet

- Building from a PC disc image. Choose an installed game folder or a `.zip` of it. (The Xbox 360 `.iso`
  is supported.)

### Earlier versions

Versions 1.0.0 to 3.0.4 remain on the `legacy-3.x` branch and their release tags.
