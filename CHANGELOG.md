# Changelog

## 4.5.0

- **Your own later-game cars:** point Setup at your own Need for Speed Carbon PC or Xbox 360 game files and tick the cars you want from the approved list (default unticked; a missing source is skipped by name). Added cars have their own engine sound by default.
- **Stock BMW M3 GTR:** buyable in the career car lot ($75,000) and selectable in Quick Race.
- **Underglow:** Customize > Visual has a Light row and each colour name shows its colour. It works on every car you can own. The light on the road is Off by default; Road pool Measured or Stronger is your choice.
- **Adaptive triggers:** Options > Controls > Adaptive triggers On / Off (default On); Off keeps plain rumble.
- **Smoother play:** shaders and known pipeline states are prepared at load instead of mid-frame (free-roam stutter 370 ms to 32 ms in our test), and the car-draw path is cheaper (+11 fps at 4K in our test). Tyre smoke renders at half resolution by default (4K smoke pass 4.9 to 1.4 ms). The Xbox 360 far-shadow fade is the default.
- **Look defaults:** Particles Physical, Glow behind signs On, Clear headlamp glass and Traffic detail On.
- **Sound:** a short reverb crossfade at environment changes is On by default (F10 > Audio turns it off).
- **Replay buffer:** F10 can keep the last 30 seconds in memory and save them as a clip on a key or controller chord (Off by default).
- **Live streaming:** F10 > Display > Live streaming sends the game picture and sound to YouTube. Your stream key stays in memory only, is masked, and is never written to disk or reports; **Forget stream key** clears it.
- **Setup:** choose to skip the Xbox 360 arrest scenes, HD textures and menu art (all ticked by default), and a smaller Xbox 360 shader footprint (about 380 MB less installed). Setup installs prebuilt libraries instead of compiling them on your Mac: about 76 s instead of 99 s on a quiet Mac, with the source build as the fallback. The source and licences ship beside them.
- **Movies:** they play even when only another edition's movie files are installed.
- **Fixed:** quitting no longer waits on a replay buffer nobody saved (the 5 s freeze on quit is gone); the hitch during police pursuits is gone; each report attaches only its own screenshot, and a full input queue drops input instead of ending the game.

## 4.4.0

- **Video clips with sound:** F10 > Display > Video clip records the game picture and its sound (stereo AAC; HEVC or H.264; 1080p, 1440p or 4K; works with HDR on) to Movies/NFSMW.
- **Physical car materials** (F10 > Picture, default Off): tinted glass and chrome from measured real-world values. **Particles: Physical** (F10 > World) adds soft edges and scene lighting to smoke and dust.
- **Full screen: Auto:** Native on the Mac's own screen, Borderless on other displays.
- **Gameplay:** Junkman parts after the career (Unlimited by default) and Add cash for a started career.
- **Build from your PC game discs, no Windows needed:** Setup accepts the PC discs as `.iso` images (or mounted in Finder) with the game's official 1.3 update. It installs the game from the discs into its work folder and applies the update without running it, checking every file. The `speed.exe` on the discs and in the update is copy-protected, so a `speed.exe` with the 1.3 code is still needed. **Update now** remembers all three.
- **European (PAL) Xbox 360 disc:** Setup now takes the PAL retail Xbox 360 copy as well as the USA one for the Xbox 360 features. Its executable is told apart by its hash and read with its own address tables; its differing shaders are pinned too. Also fixes reading an Xbox 360 executable whose compressed data ends right after its last code.
- **Export all-in-one app** in Setup: a movable backup of the game and its files (saves and settings are not included). Reinstalls reuse the prepared Xbox 360 files.
- **Reports** can be posted as a GitHub issue after you confirm the exact text; screenshot sharing starts Off.
- **Changed:** the F10 Controls > Triggers selector is gone; bind Accelerate to R2 and Brake to L2 in the game's Options > Controls for pedals.
- **Online free roam is not available in 4.4:** it moves to a shared server with room codes in a later update; 4.3's Host/Join is retired.

## 4.3.0

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

## 4.2.0

- **HDR output** (optional, Off by default): F10 > Picture > HDR output > On, then Restart game. On a screen with HDR it uses brighter highlights; otherwise it stays SDR. **HDR status** shows what the game is using.
- **Setup: choosing the game folder:** pick the game folder, `speed.exe` itself, or a `.zip` of the folder. Before, Choose could stay greyed out once you were inside the game folder.
- **Xbox and other controllers:** controllers that are not PlayStation pads now start with the Xbox layout (RT gas, LT brake). A layout you already picked is kept; PlayStation controllers keep the PS2 layout.
- **DualSense / DualShock triggers:** shifting with R2 / L2 (the PS2 layout) now happens at half travel instead of the first touch. If you mapped gas or brake to R2 / L2, they respond to how far you press them.
- Split-screen and LAN (beta) are unchanged from 4.1.0.

## 4.1.0

- **Contact shadows:** soft shading where cars and objects meet the ground.
- **Soft smoke:** smoke and dust fade gently where they meet the road, walls and cars instead of cutting off in a hard line.
- **Sharper car reflections:** finer detail in the paint's reflections, using about 120 MB more memory; F10 > Cars & cutscenes > Car reflections > Standard switches back (after Restart game).
- **No freeze at the start of the first race:** shaders are prepared while the game loads.
- **Better problem reports:** when the game slows down, the diagnostic log records whether another app was using the graphics chip or the chip was running slower.
- **On-screen keyboard for controller players:** when the game asks for a name, a keyboard appears: Cross types, Circle deletes, Square accepts, Triangle cancels (A / B / X / Y on Xbox controllers).
- A small car-paint shading fix (matches the Xbox 360 version).
- **In development, for testing:** two-player split-screen (one Mac, two controllers) and LAN play (F10 > Gameplay > LAN play (beta)). Tested so far on one Mac.

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
