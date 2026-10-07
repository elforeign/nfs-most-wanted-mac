# Settings

Every setting of the native app, written from `mods/core/nfsmw/mod.toml` by `tools/setup_kit/settings_doc.py` (do not edit by hand).

Open the settings menu with **F10** (Fn+F10 on a laptop), the controller's touchpad button, or Back+Start. Each row says when a change takes effect. The same keys can be set in `NativeOptions.ini` in your profile folder (see the companion guide).

## Graphics

| Setting | Key | Default | Takes effect | What it does |
|---|---|---|---|---|
| Detail level | `quality` | Custom | after Apply | How detailed the world looks. Higher looks better and works the computer harder. |
| Smooth edges (anti-aliasing) | `msaa` | Game's setting | after Apply | Removes jagged, stair-step edges. 4x MSAA looks smoothest and costs a little speed. |
| Widescreen | `widescreen` | on | after a restart | Fills a wide screen instead of a square-ish picture with black bars. Leave on. Takes effect after a restart. |
| Picture width (0 = auto) | `width` | 0 | after a restart | The width of the picture in pixels. 0 matches your screen automatically, which is best. Restart to apply. |
| Picture height (0 = auto) | `height` | 0 | after a restart | The height of the picture in pixels. 0 matches your screen automatically, which is best. Restart to apply. |
| How much you see to the sides | `fov_scaling` | Wider | after a restart | Original: the PC game. Wider: a little more to the sides. Exact: matched to your screen shape. Restart to apply. |
| Menu size % | `fe_scale` | 100 | after a restart | Makes the game's menus bigger or smaller. 100 is normal. Restart to apply. |
| HDR (for HDR TVs) | `hdr_output` | off | at once | On an HDR TV, lets bright things like the sun, lights and chrome shine brighter than normal white. Leave off on a normal screen. |
| HDR: how bright highlights get % | `hdr_highlights` | 60 | at once | Only with HDR on. How much brighter the brightest things get. Higher is punchier. |
| HDR: run at 60 Hz | `hdr_refresh_60` | on | at once | Only with HDR on. Keeps the TV at 60 Hz, which avoids sparkles on some TVs. Turn off if your TV is fine at 120 Hz. |

## Graphics: look and effects

| Setting | Key | Default | Takes effect | What it does |
|---|---|---|---|---|
| Overall look | `appearance` | Definitive (default) | after Apply | Definitive: the cleaner, console-inspired look this port is built around. PC - Original: the PC game exactly as it was. Custom: Definitive with your own changes below. |
| Rebuilt effects | `clean_effects` | on | at once | The glow, auto brightness, edge darkening, motion blur and depth of field. Off shows the plain picture. |
| Auto brightness % | `hybrid_exposure` | 50 | at once | Like your eyes adjusting: tunnels get a bit brighter and bright daylight a bit darker. 0 turns it off. |
| Darker screen edges % | `hybrid_vignette` | 0 | at once | Gently darkens the corners of the picture, like a camera lens. 0 is off. |
| Brighter shadows % | `hybrid_shadows` | 0 | at once | Lifts the darkest parts of the picture so you can see detail in shadows. Black stays black. 0 is off. |
| Motion blur | `motion_blur` | on | at once | The picture streaks at the edges of the screen when you drive fast, for a sense of speed. The centre stays sharp. |
| Motion blur strength % | `motion_blur_strength` | 10 | at once | How strong the speed streaks are. 100 is full strength. Lower it if fast driving feels too blurry. |
| Movie-style background blur | `cinematic_dof` | on | at once | In cut-scenes and race-start camera shots, things the camera is not looking at go soft, like in a film. Never while you drive. |
| Background blur strength % | `dof_strength` | 30 | at once | How soft the background gets in cut-scenes. 100 is full strength. |
| Light pools from lamps | `clean_pools` | on | after a restart | Street lamps and tunnel lights cast soft pools of light on the road and walls (built by the setup from your game). Restart to apply. |
| Light pools brightness % | `clean_pools_strength` | 100 | at once | How bright the light pools are. 100 is the tuned look, higher is brighter, 0 hides them. |
| Sparks and light trails | `xbox_particles` | on | after a restart | Sparks when you scrape walls, and light trails behind fast cars. Needs XenonEffects.tpk from the XenonEffects mod (the setup copies it). Restart to apply. |
| Blur: use the scene's own depth (advanced) | `dof_scene_depth` | on | at once | Leave on. Lets motion blur and background blur know how far away things are, even when it rains. |

## Visual details

| Setting | Key | Default | Takes effect | What it does |
|---|---|---|---|---|
| Mirror: sharp HD picture | `mirror_hd` | on | after a restart | Draws the rear-view mirror in high resolution instead of a blurry low-res picture. Restart to apply. |
| Mirror: show all scenery | `mirror_full_scenery` | on | at once | The mirror shows all the buildings and trees behind you, not a cut-down version. |
| Mirror: world shadows | `mirror_shadows` | off | at once | Shows shadows of buildings and trees in the mirror. Costs some speed. |
| Mirror: car shadows | `mirror_car_shadows` | on | at once | Cars behind you cast shadows in the mirror. |
| Mirror: headlights and police lights | `mirror_lights` | on | at once | Shows headlights and police light bars in the mirror. |
| Mirror: knocked-over objects | `mirror_props` | on | at once | Cones, signs and debris you knocked over also show in the mirror. |
| Mirror: keep it while looking back | `mirror_look_back` | off | at once | Keeps the mirror on screen when you use Look Back. |
| Mirror: keep sky detail | `mirror_neutral_exposure` | on | at once | Stops the sky in the mirror from washing out to white. |
| Mirror: HDR brightness | `mirror_hdr` | on | at once | With HDR on, bright lights in the mirror shine like the main picture. |
| Mirror: glow on lights | `mirror_bloom` | off | at once | Bright lights in the mirror glow softly. |
| Rain: amount % | `rain_amount` | 100 | after a restart | How many raindrops there are. 100 is normal. Restart to apply. |
| Rain: visibility % | `rain_intensity` | 45 | after a restart | How visible each raindrop is. Restart to apply. |
| Rain: wet road shine % | `rain_reflection` | 100 | after a restart | How shiny and reflective wet roads look. Restart to apply. |
| Rain: drop size | `rain_size` | 100 | after a restart | How big the raindrops are. 100 is normal. Restart to apply. |
| Rain: falling speed | `rain_speed` | 300 | after a restart | How fast the rain falls. Restart to apply. |
| Rain: sideways drift | `rain_crossing` | 200 | after a restart | How much the rain drifts sideways. Restart to apply. |
| Rain: heaviness | `rain_gravity` | 35 | after a restart | How heavily the rain drops. Restart to apply. |
| Car detail: force level | `force_car_lod` | -1 | after a restart | Forces one detail level for car models: -1 lets the game decide, 0 is the most detailed (from Extra Options). Restart to apply. |
| Wheel detail: force level | `force_tire_lod` | -1 | after a restart | Forces one detail level for wheels: -1 lets the game decide, 0 is the most detailed (from Extra Options). Restart to apply. |
| Pause or F9 freezes the camera | `freeze_camera_key` | off | after a restart | While driving, Pause/Break or F9 (Fn+F9 on a MacBook) freezes and unfreezes the camera, for screenshots and cinematic shots (from Extra Options). Restart to apply. |
| Free camera (Backspace) | `debug_camera` | off | after a restart | While driving, Backspace switches to the game's free debug camera and back, for screenshots (from Extra Options). Restart to apply. |
| Light keys (H, O) | `light_keys` | off | after a restart | While driving, H switches your headlights and O your police lights (on a police car) on and off (from Extra Options). Restart to apply. |
| Texture pack | `texture_pack` | on | after a restart | Uses a TexWizard texture pack installed in your game folder, such as the Xbox 360 Stuff Pack textures (the setup finds it). Restart to apply. |
| Remove the golden haze | `mod_lighting` | on | after a restart | Removes the heavy gold/sepia colour grade, blur and bloom of the PC version, for a cleaner picture. Turn it off to bring the PC golden look back. Restart to apply. |
| Better top detail presets | `mod_hq_preset` | on | with the game's video settings | Makes the game's highest detail presets use this port's higher quality settings. |
| Fast scenery hiding (leave on) | `preculler` | on | at once | The game's own trick for not drawing what you cannot see. Leave on for speed. |

## Controls

| Setting | Key | Default | Takes effect | What it does |
|---|---|---|---|---|
| PlayStation-style buttons | `ps2_controls` | on | at once | The default controller layout: Cross accelerates and Triangle goes back, like the PlayStation version. Change any driving button in the game's Options > Controls. |
| Triggers as pedals | `trigger_pedals` | off | at once | R2 accelerates and L2 brakes (both analog), like most modern racing games. |
| L3 skips music track | `l3_skip_track` | on | at once | Clicking the left stick skips to the next music track, like the T key. |
| PlayStation button icons | `ps_icons` | on | after a restart | Shows PlayStation button symbols in the menus. Restart to apply. |
| Shift up button | `shift_up` | 7 | at once | Controller button for shifting up (manual gears). -1 turns it off. 7 is R2. See the guide for other numbers. |
| Shift down button | `shift_down` | 6 | at once | Controller button for shifting down (manual gears). -1 turns it off. 6 is L2. See the guide for other numbers. |
| Shift up: second button | `shift_up_secondary` | -1 | at once | An extra button for shifting up. -1 turns it off. |
| Shift down: second button | `shift_down_secondary` | -1 | at once | An extra button for shifting down. -1 turns it off. |

## Controller rumble

| Setting | Key | Default | Takes effect | What it does |
|---|---|---|---|---|
| Rumble: crashes % | `impact_rumble` | 100 | at once | How hard the controller shakes when you hit something. 0 turns it off. |
| Rumble: driving % | `surface_rumble` | 100 | at once | Shaking from the road surface, wheelspin, nitrous and gear shifts. 0 turns it off. |
| Rumble: ignore tiny shakes % | `rumble_floor` | 1 | at once | Very weak rumble below this is skipped, so the controller does not buzz constantly. 0 plays everything. |

## Career and Extra Options

| Setting | Key | Default | Takes effect | What it does |
|---|---|---|---|---|
| Save/load positions (Shift/Ctrl + 1-5) | `hot_positions` | off | after a restart | While driving, Left Shift + 1-5 saves where you are and Left Ctrl + 1-5 jumps back there: the game's own hidden feature (from Extra Options). Restart to apply. |
| Black Edition extras | `black_edition` | on | after a restart | Adds the Black Edition cars, races and content. Restart to apply. |
| Race beaten Blacklist rivals again | `replay_blacklist` | on | after a restart | Lets you race Blacklist rivals you already beat. Restart to apply. |
| Special vinyls | `special_vinyls` | on | after a restart | Adds the special vinyls category in the paint shop. Restart to apply. |
| Max performance button | `max_perf_shop` | on | after a restart | Adds a button in the shop to buy all performance upgrades at once. Restart to apply. |
| Rival rewards to pick (of 6) | `marker_count` | 6 | after a restart | How many reward markers you get to pick after beating a Blacklist rival. Restart to apply. |
| Longer profile names | `long_profile_names` | on | after a restart | Allows profile names up to 15 characters. Restart to apply. |
| Helicopter takedown bounty | `helicopter_bounty` | on | after a restart | Taking down a police helicopter gives a 100,000 bounty and an announcement (a community fix). |
| New career: $10,000 bonus | `ug2_save_bonus` | on | for new careers | A new career starts with the $10,000 bonus the game gives for an Underground 2 save. Applies to new careers. |
| New career: extra starting cash | `starting_cash` | $0 | for new careers | Extra money at the start of a new career. Applies to new careers. |
| Skip music track while driving | `skip_track_anywhere` | on | after a restart | The skip-track key (T, or L3 on a controller) works while driving too, not only in the menus (from Extra Options). Restart to apply. |
| Unlock everything | `unlock_all` | off | after a restart | Unlocks all cars, parts, events and areas, and lets you play hidden tracks (from Extra Options). Restart to apply. |
| Remove glowing barriers | `remove_neon_barriers` | off | after a restart | Removes the glowing barriers that close off locked areas and routes (from Extra Options). Restart to apply. |
| Open the Old Bridge | `remove_old_bridge_barrier` | off | after a restart | Removes the barrier that closes the road to the Old Bridge (from Extra Options). Restart to apply. |
| Choose police heat levels | `heat_level_override` | off | at the next screen or load | On: police heat stays between the two levels below. Heat 6-10 send up to 8 police cars, like heat 5 (the game's own settings for those levels are leftovers). |
| Heat level: lowest | `heat_level_min` | 1 | at the next screen or load | The lowest police heat level when 'Choose police heat levels' is on. |
| Heat level: highest | `heat_level_max` | 10 | at the next screen or load | The highest police heat level when 'Choose police heat levels' is on. |

## Audio

| Setting | Key | Default | Takes effect | What it does |
|---|---|---|---|---|
| 5.1 surround sound | `surround_audio` | off | at the next screen or load | Sends 5.1 surround sound to your speakers or receiver. |
| Dolby Digital (for TVs and receivers) | `audio_dolby_digital` | off | at the next screen or load | Sends surround as Dolby Digital over HDMI. Use this if 5.1 sound comes out of the wrong speakers. |
| Fix swapped centre/subwoofer | `audio_center_lfe_swap` | off | at the next screen or load | Swaps the centre and subwoofer channels, for setups that mix them up. |

## Fixes

| Setting | Key | Default | Takes effect | What it does |
|---|---|---|---|---|
| Fit movies to the screen | `movie_fit` | on | at the next screen or load | Shows the full widescreen movies without cutting off the sides. |

## Diagnostics

| Setting | Key | Default | Takes effect | What it does |
|---|---|---|---|---|
| Log my position | `position_log` | off | at once | Writes where you are to the log, so you can report a spot to fix. |
| Log memory use | `memory_log` | on | at once | Writes the game's memory use to the log once a minute. |
| Log scenery pop-in | `scenery_trace` | off | after a restart | Writes scenery pop-in and flicker to the log, for bug reports. Restart to apply. |
| Log controller input | `input_trace` | off | at once | Writes controller and keyboard input to the log, for bug reports. |
| Game speed (keep 60) | `sim_rate` | 60 | after a restart | How many times a second the game updates. Keep it at 60. Restart to apply. |

## Other

| Setting | Key | Default | Takes effect | What it does |
|---|---|---|---|---|
| Skip intro movies | `skip_movies` | off | after a restart | Skips the movies (logos and story videos). Restart to apply. |
| Skip cut-scenes | `skip_nis` | off | after a restart | Skips the in-game cut-scenes. Restart to apply. |
