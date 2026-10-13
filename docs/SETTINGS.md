# Settings

Every setting of the native app, written from `mods/core/nfsmw/mod.toml` by `tools/setup_kit/settings_doc.py` (do not edit by hand).

Open the settings menu with **F10** (Fn+F10 on a laptop), the controller's touchpad button, or Back+Start. Each row says when a change takes effect. The same keys can be set in `NativeOptions.ini` in your profile folder (see the companion guide).

## Graphics

### Picture

| Setting | Key | Default | Takes effect | What it does |
|---|---|---|---|---|
| Look | `look` | Definitive | after Apply | Definitive: every finished Remaster improvement on (the best look). Xbox 360: the console look this port rebuilt, without the Remaster features. PC: the original PC game. Your Advanced settings are kept. Some parts (textures, wet roads, wall relief, shadows) change after a restart. |
| Detail level | `quality` | Custom | after Apply | How detailed the world looks. Higher looks better and works the computer harder. |
| Time of day | `time_of_day` | Day cycle | after a restart | Day cycle: the light moves slowly from sunrise to sunset while you play, using the game's own lighting. Original keeps the fixed look. Restart to apply. |
| Weather | `weather` | Weather cycle | after a restart | Weather cycle: a slow natural cycle of clear sky, overcast, rain and roads drying out. Original keeps the game's own weather. Restart to apply. |

### Advanced: Remaster features (follow Look)

| Setting | Key | Default | Takes effect | What it does |
|---|---|---|---|---|
| Remaster HD textures | `remaster_textures` | on | after a restart | Uses the Remaster HD texture pack when one is installed: sharper, more detailed textures made from the originals. 2.0.0 has no pack yet (the Setup app will make it on your Mac in a later release), so the game's own textures show. Restart to apply. Follows Look: Xbox 360 and PC show the original. |
| Player mods (textures from your Mods folder) | `player_mods` | on | after a restart | Textures you put in the Mods folder next to your saves replace the game's and ours, matched by file name (the launcher's Manage screen has Open mods folder). An empty folder changes nothing. Works in every Look. Restart to apply. What loaded and what did not is listed in Mods/mods-report.txt. |
| Smooth leaves and fences | `remaster_a2c` | on | after a restart | Smooths the jagged edges of leaves, fences, wires and grass with multisampling (alpha-to-coverage). Needs Smooth edges at 2x or 4x. Restart to apply. Follows Look: Xbox 360 and PC show the original. |
| Soft particles | `soft_particles` | on | after a restart | Smoke and spray fade out softly where they touch the road, walls and cars, instead of ending in a hard line. Restart to apply. Follows Look: Xbox 360 and PC show the original. |
| Wet roads | `wet_roads` | Natural | after a restart | Natural: wet roads look like real wet asphalt, darker, with an even film, soft puddles and reflections that move with the rain. Xbox 360 adds the console's rain splashes that ripple the reflections, with calmer reflections while it rains. Original keeps the game's bright bands. Restart to apply. Look Xbox 360 uses the Xbox 360 wet road; PC shows the original. |
| Sunlit wall relief | `pbr_facades` | Relief | after a restart | Relief: five concrete and stone wall textures get real surface relief in direct sun - joints and pits catch the light on the sun side - with the same overall brightness. A pilot (relief made from the HD textures): it needs the Remaster HD texture pack, which 2.0.0 does not have yet, so for now the walls look original. Shade, night, roads and the mirror are unchanged. Original keeps the game's look. Restart to apply. Follows Look: Xbox 360 and PC show the original. |
| Road sun sheen | `road_sheen` | on | after a restart | A smooth, natural sheen on the road toward the sun, from measured asphalt values (rough when dry, smoother when wet), instead of the game's golden glitter. Restart to apply. Follows Look: Xbox 360 and PC show the original. |
| Car reflections | `car_reflections` | Natural | after a restart | Natural: car paint reflects the sky like a real clear coat, rising smoothly toward the edges, never above a full mirror. Xbox 360: the console paint, with metallic flakes up close, a sharp sun hot spot and glowing bright reflections. Restart to apply. Look Xbox 360 uses the Xbox 360 paint; PC shows the original. |
| Front-end floor shadows | `fe_floor_shadows` | on | after a restart | In the garage and menus the car also casts a real shadow on the floor from the scene's own light, around the dark patch under it. Restart to apply. Follows Look: Xbox 360 and PC show the original. |
| Water | `natural_water` | Natural | after a restart | Water surfaces get moving ripples, a sky reflection that grows at grazing angles and a sharp sun glint. Natural is physically based; Xbox 360 follows the 360 water look. Restart to apply. Look Xbox 360 uses the Xbox 360 water; PC shows the original. |
| Wall depth (parallax) | `wall_parallax` | on | after a restart | Brick, stone and other normal-mapped walls gain depth: the surface shifts with the view using the height stored in the texture, like the Xbox 360 parallax. Only on textures that store a height. Restart to apply. Follows Look: Xbox 360 and PC show the original. |
| Glass | `natural_glass` | Natural | after a restart | Natural: windows reflect like real glass, little straight on so you see into them, more at a glancing angle. Xbox 360: the console windows, an even grey reflection a little stronger at an angle, with a sharp sun glint. Restart to apply. Look Xbox 360 uses the Xbox 360 glass; PC shows the original. |
| Softer shadows | `soft_shadows` | on | after a restart | Smooths the jagged edges of sun shadows with a wider filter. Restart to apply. Follows Look: Xbox 360 and PC show the original. |
| Sun rays | `sun_rays` | on | at once | Soft rays and glare around the sun when it is in view, on clear days. Uses the new rebuilt effects. Follows Look: Xbox 360 and PC show the original. |
| Haze and low fog (in development) | `atmosphere` | off | after a restart | Distant things fade into a soft haze, lighter and bluer far away, warm and bright toward the sun; at dawn and dusk a low mist lies over the bay and low ground and hills rise out of it. Uses the game's own sky colours. In development: off until approved. Restart to apply. Follows Look: Xbox 360 and PC show the original. |
| Lamp glow | `lamp_glow` | on | at once | A soft glow in the air around nearby lamps, where it is dark (tunnels, night). Needs Light pools from lamps. Follows Look: Xbox 360 and PC show the original. |
| Ambient occlusion | `ssao` | on | at once | Soft contact shadows where surfaces meet: under cars, in corners, kerbs and doorways. Uses the new rebuilt effects. Follows Look: Xbox 360 and PC show the original. |
| Vehicle lights light the world (in development) | `vehicle_lights` | off | at once | Headlights and police light bars light the road and walls around them, mostly in tunnels and at dusk. Needs Light pools from lamps. In development: off until approved. Follows Look: Xbox 360 and PC show the original. |

### Advanced: Effects

| Setting | Key | Default | Takes effect | What it does |
|---|---|---|---|---|
| Road sheen in rain % | `road_sheen_rain` | 50 | after a restart | How strong the road sun sheen is on a fully wet road. 100 is as strong as the physics gives; lower keeps rain from looking too shiny. Restart to apply. |
| Ambient occlusion strength % | `ssao_strength` | 100 | at once | How strong the ambient occlusion is. 100 is the tuned look. |
| Golden colour filter | `golden_filter` | Auto | at once | The warm gold colour grade of the original game. Auto follows the Look: on in Xbox 360 and PC (in PC it is the game's own filter), off in Definitive. On or Off sets it yourself in every Look. |
| Rebuilt effects | `clean_effects` | on | at once | The rebuilt glow, auto brightness, edge darkening, motion blur, depth of field, sun rays and lamp glow. Off: none of these (the plain picture). The sliders below adjust them. |
| Sun rays strength % | `sun_rays_strength` | 100 | at once | How strong the sun rays and glare are. 100 is the tuned look. |
| Haze strength % | `atmosphere_strength` | 100 | at once | How thick the haze and low fog are. 100 is the tuned look; 0 turns them off. |
| Lamp glow strength % | `lamp_glow_strength` | 100 | at once | How strong the lamp glow is. 100 is the tuned look. |
| Auto brightness % | `hybrid_exposure` | 50 | at once | Like your eyes adjusting: tunnels get a bit brighter and bright daylight a bit darker. 0 turns it off. |
| Darker screen edges % | `hybrid_vignette` | 0 | at once | Gently darkens the corners of the picture, like a camera lens. 0 is off. |
| Brighter shadows % | `hybrid_shadows` | 0 | at once | Lifts the darkest parts of the picture so you can see detail in shadows. Black stays black. 0 is off. |
| Motion blur | `motion_blur` | on | at once | The picture streaks at the edges of the screen when you drive fast, for a sense of speed. The centre stays sharp. |
| Motion blur strength % | `motion_blur_strength` | 10 | at once | How strong the speed streaks are. 100 is the console's strength. Lower it if fast driving feels too blurry. |
| Movie-style background blur | `cinematic_dof` | on | at once | In cut-scenes and race-start camera shots, things the camera is not looking at go soft, like in a film. Never while you drive. |
| Background blur strength % | `dof_strength` | 30 | at once | How soft the background gets in cut-scenes. 100 is the console's strength. |
| Lit smoke and fog (in development) | `lit_smoke` | off | at once | Smoke, steam and fog take the sun's light: thin edges glow when you look toward the sun, thick billows are darker underneath, and in shade they stay soft and grey. Road-flare smoke glows red near the flare. Fog fades softly into the road and as you drive through it. In development: off until approved. Follows Look: Xbox 360 and PC show the original. |

### Advanced: Day and weather cycle

| Setting | Key | Default | Takes effect | What it does |
|---|---|---|---|---|
| Length of a day (minutes) | `tod_day_minutes` | 60 | after a restart | How many minutes of play one sunrise-to-sunset day takes with Day cycle on. Restart to apply. |
| Weather cycle length (minutes) | `weather_cycle_minutes` | 20 | after a restart | Minutes of play for one full weather cycle with Weather cycle on (clear, overcast, rain, drying). Each session starts at a random point of the cycle, so it can already be raining. Restart to apply. |

### Advanced: Mirror

| Setting | Key | Default | Takes effect | What it does |
|---|---|---|---|---|
| Mirror: sharp HD picture | `mirror_hd` | on | after a restart | Draws the rear-view mirror in high resolution instead of a blurry low-res picture. Restart to apply. |
| Mirror: show all scenery | `mirror_full_scenery` | on | at once | The mirror shows all the buildings and trees behind you, not a cut-down version. |
| Mirror: world shadows | `mirror_shadows` | off | at once | Shows shadows of buildings and trees in the mirror. Costs some speed. |
| Mirror: car shadows | `mirror_car_shadows` | on | at once | Cars behind you cast shadows in the mirror. |
| Mirror: headlights and police lights | `mirror_lights` | on | at once | Shows headlights and police light bars in the mirror. |
| Mirror: knocked-over objects | `mirror_props` | on | at once | Cones, signs and debris you knocked over also show in the mirror. |
| Mirror: keep it while looking back | `mirror_look_back` | off | at once | Keeps the mirror on screen when you use Look Back. |
| Mirror: match the main view (in development) | `mirror_grade` | off | at once | Gives the rear-view mirror the main view's brightness and shadow lift, so it no longer looks flat or too bright. Needs Mirror: keep sky detail. In development: off until approved. Follows Look: Xbox 360 and PC show the original. |
| Mirror: glass (in development) | `mirror_glass` | 0 | at once | Makes the mirror look like real glass: a slight cool tint, darker edges and a thin highlight along the top. 0 is the original. In development: off until approved. Follows Look: Xbox 360 and PC show the original. |
| Mirror: keep sky detail | `mirror_neutral_exposure` | on | at once | Stops the sky in the mirror from washing out to white. |
| Mirror: smooth edges (in development) | `mirror_msaa` | Off | after a restart | Smooths the jagged edges in the rear-view mirror with 4x multisampling, like the main picture (and smooths its leaves and fences). In development: off until approved. Restart to apply. Follows Look: Xbox 360 and PC show the original. |
| Mirror: light pools (in development) | `mirror_pools` | off | at once | Street lamps light the road and walls in the rear-view mirror too, as in the main picture. In development: off until approved. Follows Look: Xbox 360 and PC show the original. |
| Mirror: field of view % | `mirror_fov` | 100 | at once | How much the rear-view mirror shows. 100 is the original; lower is a flatter, closer glass, higher a wider panoramic mirror. |
| Mirror: HDR brightness | `mirror_hdr` | on | at once | With HDR on, bright lights in the mirror shine like the main picture. |

### Advanced: Rain

| Setting | Key | Default | Takes effect | What it does |
|---|---|---|---|---|
| Rain: amount % | `rain_amount` | 100 | after a restart | How many raindrops there are. 100 is normal. Restart to apply. |
| Rain: visibility % | `rain_intensity` | 45 | after a restart | How visible each raindrop is. Restart to apply. |
| Rain: wet road shine % | `rain_reflection` | 100 | after a restart | How shiny and reflective wet roads look. Restart to apply. |
| Rain: drop size | `rain_size` | 100 | after a restart | How big the raindrops are. 100 is normal. Restart to apply. |
| Rain: falling speed | `rain_speed` | 300 | after a restart | How fast the rain falls. Restart to apply. |
| Rain: sideways drift | `rain_crossing` | 200 | after a restart | How much the rain drifts sideways. Restart to apply. |
| Rain: heaviness | `rain_gravity` | 35 | after a restart | How heavily the rain drops. Restart to apply. |

### Advanced: Engine and experiments

| Setting | Key | Default | Takes effect | What it does |
|---|---|---|---|---|
| Remove the golden haze | `mod_lighting` | on | after a restart | Removes the heavy gold/sepia colour, blur and glow that both the PC and Xbox 360 versions have, for a cleaner picture. Off brings back the PC game's gold tint. Restart to apply. |
| Fast scenery hiding (leave on) | `preculler` | on | at once | The game's own trick for not drawing what you cannot see. Leave on for speed. |

## Display

### Screen

| Setting | Key | Default | Takes effect | What it does |
|---|---|---|---|---|
| Widescreen | `widescreen` | on | after a restart | Fills a wide screen instead of a square-ish picture with black bars. Leave on. Takes effect after a restart. |
| Picture width (0 = auto) | `width` | 0 | after a restart | The width of the picture in pixels. 0 matches your screen automatically, which is best. Restart to apply. |
| Picture height (0 = auto) | `height` | 0 | after a restart | The height of the picture in pixels. 0 matches your screen automatically, which is best. Restart to apply. |
| How much you see to the sides | `fov_scaling` | Xbox 360 | after a restart | Original: the PC game. Xbox 360: a little wider, like the console. Exact: matched to your screen shape. Restart to apply. |
| Menu size % | `fe_scale` | 100 | after a restart | Makes the game's menus bigger or smaller. 100 is normal. Restart to apply. |

### Sharpness and upscaling

| Setting | Key | Default | Takes effect | What it does |
|---|---|---|---|---|
| Smooth edges (anti-aliasing) | `antialiasing` | Off | after Apply | Removes jagged, stair-step edges with multisampling (MSAA). Off is fastest and at 4K edges are already small; 2x smooths them for a little GPU time, 4x smoothest for more. Also smooths leaves and fences (Smooth leaves and fences). The detail level does not change it. With HDR on, 4x runs as 2x (the two together stutter at 4K). |
| Upscaling (MetalFX) (in development) | `upscale` | Off | after a restart | Draws the 3D picture at a lower resolution and sharpens it up to your screen with Apple's MetalFX; menus and the HUD stay sharp. Quality is close to native; Performance is faster. In development: off until approved. Restart to apply. |

### HDR

| Setting | Key | Default | Takes effect | What it does |
|---|---|---|---|---|
| HDR (for HDR TVs) | `hdr_output` | off | at once | On an HDR TV, lets bright things like the sun, lights and chrome shine brighter than normal white; everything else looks as it does with HDR off. The TV switches to its HDR mode. Leave off on a normal screen. With HDR on, Smooth edges at 4x runs as 2x (the two together stutter at 4K). |
| HDR: how bright highlights get % | `hdr_highlights` | 60 | at once | Only with HDR on. How much brighter the brightest things get. Higher is punchier. |
| HDR: run at 60 Hz | `hdr_refresh_60` | on | at once | Only with HDR on. Keeps the TV at 60 Hz, which avoids sparkles on some TVs. Turn off if your TV is fine at 120 Hz. |

## Audio

### Audio

| Setting | Key | Default | Takes effect | What it does |
|---|---|---|---|---|
| 5.1 surround sound | `surround_audio` | off | at the next screen or load | Sends 5.1 surround sound to your speakers or receiver. |
| Dolby Digital (for TVs and receivers) | `audio_dolby_digital` | on | at the next screen or load | Sends surround as Dolby Digital over HDMI. Use this if 5.1 sound comes out of the wrong speakers. |
| Fix swapped centre/subwoofer | `audio_center_lfe_swap` | off | at the next screen or load | Swaps the centre and subwoofer channels, for setups that mix them up. |

## Xbox 360

### Xbox 360 looks

| Setting | Key | Default | Takes effect | What it does |
|---|---|---|---|---|
| Front-end car shadow off | `fe_shadows` | off | after a restart | In the garage and menus the car has no dark ground shadow under it, as on the Xbox 360. Restart to apply. |
| Xbox 360 textures | `x360_textures` | on | after a restart | Uses the sharper Xbox 360 textures for roads, buildings and cars (needs the texture pack). Restart to apply. |
| Xbox 360 front end | `x360_frontend` | on | after a restart | The Xbox 360 menus, title and loading art and garages from the Xbox 360 Stuff Pack (when Setup added it). Off: the PC front end (and your own game folder's FrontB/GlobalB files, if a mod changed them). Restart to apply. |
| Xbox 360 sky | `x360_sky` | Xbox 360 | after a restart | Xbox 360: the console sky and clouds, our own version of the console sky shader. Original: the PC sky. Restart to apply. |
| Xbox 360 start screen | `splash_xbox` | on | after a restart | Shows the Xbox 360 start screen with the EA and HD logos (when the USA videos are installed). Restart to apply. |

### Sparks and smoke

| Setting | Key | Default | Takes effect | What it does |
|---|---|---|---|---|
| Sparks and light trails | `xbox_particles` | on | after a restart | Sparks when you scrape walls, and light trails behind fast cars. Needs the XenonEffects texture pack. Restart to apply. |
| Denser tyre smoke | `denser_smoke` | on | at once | More smoke from each tyre when you skid, slide or do a burnout, as on the Xbox 360. Follows Look: Xbox 360 on, PC off. |
| Stuff Pack smoke | `stuffpack_smoke` | on | at once | Adds the community Stuff Pack's extra tyre-smoke layers on asphalt: darker and fuller smoke when you skid, slide or spin the wheels. Works together with Denser tyre smoke. Follows Look: Xbox 360 on, PC off. |

### Light pools

| Setting | Key | Default | Takes effect | What it does |
|---|---|---|---|---|
| Light pools from lamps | `clean_pools` | on | after a restart | Street lamps and tunnel lights cast soft pools of light on the road, walls and terrain. Off: no light pools at all. Restart to apply. |
| Light pools brightness % | `clean_pools_strength` | 100 | at once | How bright the light pools are. 100 is the tuned look, higher is brighter, 0 hides them. |
| Light pools on tunnel props | `clean_pools_props` | off | after a restart | Adds local lamp lighting to eligible props (tunnel fans, pipes, gantries) in the main view. May increase stutter in busy scenes. Needs Light pools from lamps. Restart to apply. |
| Street lamps off in daylight | `lamp_daylight` | off | at once | Outdoor street lamps fade out while the sun is up and come on at dawn and dusk; tunnel lamps stay on. Their glow follows. Needs Light pools from lamps. |

## PC

### Extra Options

| Setting | Key | Default | Takes effect | What it does |
|---|---|---|---|---|
| Better top detail presets | `mod_hq_preset` | on | with the game's video settings | Makes the game's highest detail presets use this port's higher quality settings. |
| Car detail: force level | `force_car_lod` | -1 | after a restart | Forces one detail level for car models: -1 lets the game decide, 0 is the most detailed (from Extra Options). Restart to apply. |
| Wheel detail: force level | `force_tire_lod` | -1 | after a restart | Forces one detail level for wheels: -1 lets the game decide, 0 is the most detailed (from Extra Options). Restart to apply. |
| Always rain | `always_rain` | off | after a restart | Makes it rain all the time (the game's own rain switch, from Extra Options). Restart to apply. |
| Subtitles | `subtitles` | off | after a restart | Shows English subtitles in the story scenes (from Extra Options). Restart to apply. |

### Keys, camera and HUD

| Setting | Key | Default | Takes effect | What it does |
|---|---|---|---|---|
| Pause key freezes the camera | `freeze_camera_key` | off | after a restart | While driving, the Pause/Break key freezes and unfreezes the camera, for screenshots (from Extra Options). Restart to apply. |
| Keep the HUD when looking back | `hud_on_look_back` | off | after a restart | The HUD stays on screen while you look back, as in Underground 2 (from Extra Options). Restart to apply. |
| Light keys (H and O) | `light_keys` | off | after a restart | While driving, H switches your headlights and O your police lights on and off (from Extra Options). Restart to apply. |
| Free camera (Backspace) | `debug_camera` | off | after a restart | While driving, Backspace switches to the game's free debug camera and back, for screenshots (from Extra Options). Restart to apply. |

## Mods

### Career and customization

| Setting | Key | Default | Takes effect | What it does |
|---|---|---|---|---|
| Black Edition extras | `black_edition` | on | after a restart | Adds the Black Edition cars, races and content. Restart to apply. |
| Race beaten Blacklist rivals again | `replay_blacklist` | on | after a restart | Lets you race Blacklist rivals you already beat. Restart to apply. |
| Special vinyls | `special_vinyls` | on | after a restart | Adds the special vinyls category in the paint shop. Restart to apply. |
| Max performance button | `max_perf_shop` | on | after a restart | Adds a button in the shop to buy all performance upgrades at once. Restart to apply. |
| Rival rewards to pick (of 6) | `marker_count` | 6 | after a restart | How many reward markers you get to pick after beating a Blacklist rival. Restart to apply. |
| Longer profile names | `long_profile_names` | on | after a restart | Allows profile names up to 15 characters. Restart to apply. |
| Helicopter takedown bounty | `helicopter_bounty` | on | after a restart | Taking down a police helicopter gives a 100,000 bounty and an announcement (a community fix; not on the Xbox 360). |
| New career: $10,000 bonus | `ug2_save_bonus` | on | for new careers | A new career starts with the $10,000 bonus the game gives for an Underground 2 save. Applies to new careers. |
| New career: extra starting cash | `starting_cash` | $0 | for new careers | Extra money at the start of a new career. Applies to new careers. |
| Choose police heat levels | `heat_level_override` | off | at the next screen or load | On: police heat stays between the two levels below. Heat 6-10 send up to 8 police cars, like heat 5 (the game's own settings for those levels are leftovers). |
| Heat level: lowest | `heat_level_min` | 1 | at the next screen or load | The lowest police heat level when 'Choose police heat levels' is on. |
| Heat level: highest | `heat_level_max` | 10 | at the next screen or load | The highest police heat level when 'Choose police heat levels' is on. |
| Unlock everything | `unlock_all` | off | after a restart | Unlocks all cars, parts, events and areas, and lets you play hidden tracks (from Extra Options). Restart to apply. |
| Remove glowing barriers | `remove_neon_barriers` | off | after a restart | Removes the glowing barriers that close off locked areas and routes (from Extra Options). Restart to apply. |
| Open the Old Bridge | `remove_old_bridge_barrier` | off | after a restart | Removes the barrier that closes the road to the Old Bridge (from Extra Options). Restart to apply. |
| Minimap: show all police | `show_nonpursuit_cops` | off | after a restart | Also shows police cars that are not chasing you on the minimap (from Extra Options). Restart to apply. |
| Save and load positions | `hot_positions` | off | after a restart | While driving, Left Shift plus 1 to 5 saves where you are and Left Ctrl plus 1 to 5 takes you back there (from Extra Options). Restart to apply. |
| Fix: bounty at heat 10 | `zero_bounty_fix` | on | after a restart | Fixes the zero bounty for takedowns at heat 10 and the over-long combo timer (from Extra Options). Restart to apply. |
| Fix: prologue forced again | `dday_fix` | on | after a restart | Stops the game sending a career that is already under way back to the prologue (from Extra Options). Restart to apply. |
| Fix: unused Busted scenes | `busted_nis_fix` | on | after a restart | Lets the Busted cut-scenes for heat 2 and 3 play, as intended (from Extra Options). Restart to apply. |
| One-lap races show percent | `percent_1lap` | on | after a restart | Shows COMPLETED percent instead of LAP 1/1 in one-lap races (from Extra Options). Restart to apply. |
| Heat keys (F7 and Page Up) | `heat_keys` | off | after a restart | While driving, F7 arms the heat keys and Page Up then steps the heat level between the lowest and highest heat settings (from Extra Options). Restart to apply. |
| World Map in races | `world_map_anywhere` | on | after a restart | Lets you open the World Map from the pause menu during races and pursuits too (from Extra Options). Restart to apply. |
| Skip music track while driving | `skip_track_anywhere` | on | after a restart | The skip-track key works while driving too, not only in the menus (from Extra Options). Restart to apply. |
| No catch-up | `no_catch_up` | off | after a restart | Turns off the opponents' catch-up, so racers no longer slow down or speed up to stay near you. This changes how races play (from Extra Options). Restart to apply. |

### Verified fixes and compatibility

| Setting | Key | Default | Takes effect | What it does |
|---|---|---|---|---|
| Fit movies to the screen | `movie_fit` | on | at the next screen or load | Shows the full widescreen movies without cutting off the sides. |

### Diagnostics

| Setting | Key | Default | Takes effect | What it does |
|---|---|---|---|---|
| Log my position | `position_log` | off | at once | Writes where you are to the log, so you can report a spot to fix. |
| Log memory use | `memory_log` | on | at once | Writes the game's memory use to the log once a minute. |
| Log scenery pop-in | `scenery_trace` | off | after a restart | Writes scenery pop-in and flicker to the log, for bug reports. Restart to apply. |
| Log controller input | `input_trace` | off | at once | Writes controller and keyboard input to the log, for bug reports. |
| Game speed (keep 60) | `sim_rate` | 60 | after a restart | How many times a second the game updates. Keep it at 60. Restart to apply. |

### Other

| Setting | Key | Default | Takes effect | What it does |
|---|---|---|---|---|
| Skip intro movies | `skip_movies` | off | after a restart | Skips the movies (logos and story videos). Restart to apply. |
| Skip cut-scenes | `skip_nis` | off | after a restart | Skips the in-game cut-scenes. Restart to apply. |
| Discord status | `discord_status` | on | at once | Shows Need for Speed: Most Wanted, whether you are driving or in the menus, and your time played on your Discord profile, when the Discord app is open. Off clears it at once. |

## Controls

### Controller

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

### Controller rumble

| Setting | Key | Default | Takes effect | What it does |
|---|---|---|---|---|
| Rumble: crashes % | `impact_rumble` | 100 | at once | How hard the controller shakes when you hit something. 0 turns it off. |
| Rumble: driving % | `surface_rumble` | 100 | at once | Shaking from the road surface, wheelspin, nitrous and gear shifts. 0 turns it off. |
| Rumble: cobbles and metal | `paved_surface_rumble` | on | at once | Also rumble on cobblestones, stone and metal surfaces (the Xbox 360 does not). |
| Rumble style | `rumble_model` | Xbox 360 | at once | Xbox 360: rumbles like the console. PC wheel adaptation: the PC version's steering-wheel feedback turned into rumble. |
| Rumble: crash fade % | `collision_rumble_fade` | 50 | at once | How long the rumble lasts after a crash or scrape. 100 is the Xbox 360; lower is shorter. |
| Rumble: ignore tiny shakes % | `rumble_floor` | 1 | at once | Very weak rumble below this is skipped, so the controller does not buzz constantly. 0 plays everything. |

## Not in the menu

Kept so that older profiles load cleanly, or fixed by the port; the menu does not list them.

| Key | Value | Why |
|---|---|---|
| `msaa` | -1 | The anti-aliasing setting of 1.0 (-1 the game's own, 0 off, 1 4x). Read once and carried over to Smooth edges as Off; then reset to -1. |
| `spray_fill` | 1 | Rain spray behind cars drawn from 1/K of the particles, each denser. 1 = as the game (larger values showed no speed gain). |
| `settings_version` | 0 | The last one-time settings migration done (1: Xbox 360 sky 1 -> 2). Not a choice. |
| `grass` | Off | Retired in 2.0: always off, whatever was saved (no visible gain); the code stays for later. |
| `upscale_temporal` | off | Not built yet (MetalFX temporal upscaling, planned); always off. |
| `appearance` | Definitive (default) | Definitive: the console-style look this port is built around. PC - Original: the PC game exactly as it was. Custom: Definitive with your own changes below. |
| `dof_scene_depth` | on | Retired from the menu in 2.0: always on (off only lost the blur in some rain frames). |
| `x360_exposure` | on | Retired from the menu in 2.0: always on (off also broke the HDR highlight mark). |
