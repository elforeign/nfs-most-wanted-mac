# Settings

Every setting of the native app: what it does, what you see when you change it, what it costs and a tip for slower Macs. Written from `mods/core/nfsmw/mod.toml` by `tools/setup_kit/settings_doc.py` (do not edit by hand), so it always matches the game.

Open the settings menu with **F10** (Fn+F10 on a laptop), the controller's touchpad button, or Back+Start. The menu has the same pages and sections as this guide, and shows each setting's text at the bottom. Costs are the extra time each frame takes on the graphics chip at 4K (lower is faster; 16.7 ms is one frame at 60 fps). The same keys can be set in `NativeOptions.ini` in your profile folder (see the companion guide).

**Pages:** [Graphics](#graphics) · [Display](#display) · [Audio](#audio) · [Interface](#interface) · [Gameplay](#gameplay) · [Mods](#mods) · [Controls](#controls)

## Start here: speed and picture

If the game does not keep a smooth 60 fps, change these first, in this order:

- **Mac preset** (`mac_preset`, Auto (this Mac) by default): Lowers the picture cost for this Mac. Auto picks by chip and screen. Full: your own settings. Balanced: upscaling Quality, smooth edges 2x max. Light: upscaling Performance; smooth edges, ambient occlusion, softer shadows, lamp glow, denser smoke off. Saved values stay.
- **Upscaling (MetalFX) (in development)** (`upscale`, Off by default): Draws the 3D picture at a lower resolution and sharpens it up to your screen with Apple MetalFX; menus and HUD stay sharp. Quality looks close to full resolution; Performance is faster and a little softer. Cost: saves GPU time. Weaker Mac: Performance. In development: off until approved.
- **Smooth edges (anti-aliasing)** (`antialiasing`, Off by default): Smooths jagged edges on wires, fences and car outlines. 2x removes most stair-steps; 4x nearly all, at a distance too. Cost: 2x +1.5 ms, 4x +3.6 ms GPU at 4K; with HDR, 4x runs as 2x. Weaker Mac: Off, and use Upscaling instead.
- **HDR (for HDR TVs)** (`hdr_output`, off by default): Sends a high-dynamic-range picture: brighter sun, headlights and reflections, deeper darks. Needs an HDR display; wrong on an SDR one (washed out). Cost: +1.6-1.9 ms GPU at 4K. Pair with 2x smooth edges at most; 4x runs as 2x.
- **Detail level** (`quality`, Custom by default): How much detail the game draws: world, cars, shadows and effects (the game's own Video options). Lower levels look plainer and run faster; Custom keeps your own choices from the Video menu. Cost: Low to High by level. Weaker Mac: try the Mac preset or Upscaling first.

## Graphics

### Picture

| Setting | Choices | Default | Takes effect | What it does |
|---|---|---|---|---|
| **Mac preset** `mac_preset` | Auto (this Mac) / Full / Balanced / Light | Auto (this Mac) | after a restart | Lowers the picture cost for this Mac. Auto picks by chip and screen. Full: your own settings. Balanced: upscaling Quality, smooth edges 2x max. Light: upscaling Performance; smooth edges, ambient occlusion, softer shadows, lamp glow, denser smoke off. Saved values stay. |
| **Detail level** `quality` | Low / Medium / High / Ultra / Custom | Custom | after Apply | How much detail the game draws: world, cars, shadows and effects (the game's own Video options). Lower levels look plainer and run faster; Custom keeps your own choices from the Video menu. Cost: Low to High by level. Weaker Mac: try the Mac preset or Upscaling first. |
| **Time of day** `time_of_day` | Original (fixed) / Day cycle | Day cycle | after a restart | Day cycle: the light moves slowly from sunrise to sunset while you play, with the game's own lighting. Original keeps the game's fixed light. Cost: Low. |
| **Weather** `weather` | Original / Weather cycle | Weather cycle | after a restart | Weather cycle: clear sky, overcast, rain and drying roads come and go slowly while you play. Original keeps the game's own weather. Cost: rain adds 1.5-2.6 ms of main-thread time while the road is wet. |
| **Golden colour filter** `golden_grade` | Off / On | Off | at once | Gives the picture the warm gold colour grade of the original game, as on the Xbox 360. Off shows natural colours (the default). Cost: Low. |
| **Motion blur** `motion_blur` | Off / On | on | at once | The edges of the screen streak when you drive fast, for a sense of speed; the centre stays sharp. Off keeps the whole picture sharp. Needs Picture effects on. Cost: Low. |
| **Movie-style background blur** `cinematic_dof` | Off / On | on | at once | In cut-scenes and race-start camera shots, what the camera is not looking at goes soft, like in a film. Never while you drive. Off keeps everything sharp. Needs Picture effects on. Cost: Low. |

### Advanced: Lighting and shadows

| Setting | Choices | Default | Takes effect | What it does |
|---|---|---|---|---|
| **Ambient occlusion** `ssao` | Off / On | on | at once | Darkens corners, under cars and where walls meet the road, so objects sit in the scene instead of floating. Off looks flatter. Needs Picture effects on. Cost: about +0.7 ms GPU at 4K. Weaker Mac: off (the Light preset does this). |
| **Ambient occlusion strength %** `ssao_strength` | 0 to 200, steps of 10 | 100 | at once | How dark the ambient occlusion shading is. 100 is the tuned look; lower is lighter, higher is darker. |
| **Softer shadows** `soft_shadows` | Off / On | on | after a restart | Sun shadows get soft, smooth edges instead of jagged, stair-stepped ones. Off shows the harder original edges. Cost: Medium. Weaker Mac: off (the Light preset does this). |
| **Sun rays** `sun_rays` | Off / On | on | at once | Soft rays and glare around the sun when it is in view on clear days. Off: no rays or glare. Needs Picture effects on. Cost: Low. |
| **Sun rays strength %** `sun_rays_strength` | 0 to 200, steps of 10 | 100 | at once | How strong the sun rays and glare are. 100 is the tuned look; lower is subtler, higher brighter. |
| **Lamp glow** `lamp_glow` | Off / On | on | at once | A soft glow hangs in the air around nearby lamps where it is dark (tunnels, night). Off: lamps light the road but the air stays clear. Needs Light pools from lamps. Cost: Low. Weaker Mac: off (the Light preset does this). |
| **Lamp glow strength %** `lamp_glow_strength` | 0 to 200, steps of 10 | 100 | at once | How strong the glow around lamps is. 100 is the tuned look; lower is subtler, higher brighter. |
| **Vehicle lights light the world (in development)** `vehicle_lights` | Off / On | off | at once | Headlights and police light bars light up the road and walls around them, mostly in tunnels and at dusk. Off: car lights glow but light nothing around them. Needs Light pools from lamps. Cost: Low. In development: off until approved. |
| **Light pools from lamps** `clean_pools` | Off / On | on | after a restart | Street lamps and tunnel lights cast soft pools of light on the road, walls and terrain. Off: no light pools at all, so night streets look darker and flatter. Cost: about +0.4 ms GPU at 4K. |
| **Light pools brightness %** `clean_pools_strength` | 0 to 200, steps of 10 | 100 | at once | How bright the light pools from lamps are. 100 is the tuned look; higher is brighter, 0 hides them. |
| **Garage floor shadows** `fe_floor_shadows` | Off / On | on | after a restart | In the garage and menus the car also casts a real shadow on the floor from the scene's light, around the dark patch under it. Off: only the dark patch. Cost: Low (menus only). |
| **Hide the garage car shadow** `fe_shadows` | Off / On | off | after a restart | On: in the garage and menus the car has no dark ground patch under it, as on the Xbox 360. Off (the default) keeps the PC game's dark patch. |
| **Clear picture (no PC haze)** `mod_lighting` | Off / On | on | after a restart | Removes the heavy gold-sepia tint, blur and glow that both the PC and Xbox 360 versions lay over the picture, for a clean, clear image. Off brings back the PC game's gold haze. Cost: Low. |

### Advanced: Textures, surfaces and reflections

| Setting | Choices | Default | Takes effect | What it does |
|---|---|---|---|---|
| **Remaster HD textures** `remaster_textures` | Off / On | on | after a restart | Sharper, more detailed textures made on your Mac from your own game's originals, on roads, buildings and cars. Off shows the original, softer textures. Needs Make HD textures in Setup (run Setup again to add them later). Cost: Low (uses more memory). |
| **Road surface detail** `material_sets` | Off / On | On | after a restart | Dry roads show real surface detail: grain and cracks in low sun, a tight sun sheen on smooth patches and a wide one on rough asphalt. Off: flatter roads. Needs HD textures and Road sun sheen on, and a Mac with 16 GB of memory or more. Cost: Low. |
| **Sunlit wall relief** `pbr_facades` | Original / Relief | Relief | after a restart | Relief: five concrete and stone wall textures get real depth in direct sun; joints and pits catch the light on the sunny side. Original keeps flat walls. Shade, night, roads and the mirror are unchanged. Needs HD textures on. Cost: Low. |
| **Wall depth (parallax)** `wall_parallax` | Off / On | on | after a restart | Brick, stone and similar walls gain depth: the surface shifts as you move past, as on the Xbox 360. Off: flat walls. Only on textures that carry height detail. Cost: Low. |
| **Wall sun sheen** `wall_sheen` | Off / On | On | after a restart | Sunlit walls and metal get a soft sun highlight: tight on smooth stone and metal, wide and faint on brick and concrete. Shade, night, roads and the mirror are unchanged. Needs Road surface detail on. Cost: Low. |
| **Road sun sheen** `road_sheen` | Off / On | on | after a restart | The road shines naturally toward the sun, rough when dry and smoother when wet. Off brings back the game's golden glitter on the asphalt. Cost: Low. |
| **Road sheen in rain %** `road_sheen_rain` | 0 to 100, steps of 10 | 50 | after a restart | How strong the road sun sheen is on a fully wet road. 100 is as strong as real wet asphalt; lower keeps rain from looking too shiny. 50 is the tuned look. |
| **Wet roads** `wet_roads` | Original / Natural / Xbox 360 | Natural | after a restart | Natural (the default): wet roads look like real wet asphalt, darker, with soft puddles and reflections that move with the rain. Xbox 360 adds the console's splashes that ripple the reflections. Original keeps the game's bright bands. Cost: Low. |
| **Car reflections** `car_reflections` | Off / Natural / Xbox 360 | Natural | after a restart | Natural (the default): car paint reflects the sky like a real clear coat, stronger toward the edges. Xbox 360: the console paint, with metallic flakes up close, a sharp sun hot spot and glowing reflections. Off: the PC game's paint. Cost: Low. |
| **Glass** `natural_glass` | Off / Natural / Xbox 360 | Natural | after a restart | Natural (the default): windows reflect like real glass, little straight on so you see inside, more at a glancing angle. Xbox 360: an even grey reflection with a sharp sun glint. Off: the PC game's windows. Cost: Low. |
| **Water** `natural_water` | Off / Natural / Xbox 360 | Natural | after a restart | Water gets moving ripples, a sky reflection that grows at low angles and a sharp sun glint. Natural (the default) looks like real water; Xbox 360 follows the console's water. Off: the PC game's water. Cost: Low. |
| **Screen-space reflections (in development)** `ssr` | Off / On | off | after a restart | Car paint, chrome, windows and water also reflect what is on screen around them, such as buildings and other cars. Off: the normal reflections only. Cost: Medium. In development: off until approved. |
| **Reflections strength %** `ssr_strength` | 0 to 200, steps of 10 | 100 | after a restart | How strong the screen-space reflections are. 100 is the tuned look; lower is subtler. |
| **Smooth leaves and fences** `remaster_a2c` | Off / On | on | after a restart | Smooths the jagged edges of leaves, fences, wires and grass. Off leaves them grainy. Works only with Smooth edges at 2x or 4x. Cost: Low (part of Smooth edges). |
| **Xbox 360 textures** `x360_textures` | Off / On | on | after a restart | Uses the sharper Xbox 360 textures for roads, buildings and cars. Off: the PC textures, softer in places. Needs the texture pack. Cost: Low. |

### Advanced: Camera and colour effects

| Setting | Choices | Default | Takes effect | What it does |
|---|---|---|---|---|
| **Picture effects (glow, blur, sun rays)** `clean_effects` | Off / On | on | at once | The glow, auto brightness, edge darkening, motion blur, background blur, sun rays and lamp glow. Off: none of these, a plain and flatter picture. The sliders below tune them. Cost: about +1.9 ms GPU at 4K. |
| **Motion blur strength %** `motion_blur_strength` | 5 to 100, steps of 5 | 10 | at once | How strong the speed streaks are. 100 is the Xbox 360's strength; lower it if fast driving feels too blurry. |
| **Background blur strength %** `dof_strength` | 5 to 100, steps of 5 | 30 | at once | How soft the background gets in cut-scenes. 100 is the Xbox 360's strength; lower keeps more in focus. |
| **Haze and low fog (in development)** `atmosphere` | Off / On | off | after a restart | Distant things fade into a soft haze, bluer far away and warm toward the sun; at dawn and dusk a low mist lies over the bay and low ground. Off: clear air to the horizon. Cost: Low. In development: off until approved. |
| **Haze strength %** `atmosphere_strength` | 0 to 200, steps of 10 | 100 | at once | How thick the haze and low fog are. 100 is the tuned look; 0 turns them off. |
| **Scenery fade-in** `scenery_fade` | Off / On | On | after a restart | Buildings, trees and fences fade in over about 0.3 s instead of popping into view. You notice it most at speed on long straights. Off: the original pop-in. Cost: +0.2-0.8 ms GPU at 4K. Fine on every Mac. |

### Advanced: Sky, weather and rain

| Setting | Choices | Default | Takes effect | What it does |
|---|---|---|---|---|
| **Length of a day (minutes)** `tod_day_minutes` | 10 to 240 | 60 | after a restart | How many minutes of play one sunrise-to-sunset day takes with Day cycle on. Lower is a faster sky; higher a slower one. |
| **Weather cycle length (minutes)** `weather_cycle_minutes` | 10 to 240 | 20 | after a restart | Minutes of play for one full weather cycle with Weather cycle on: clear, overcast, rain, drying. Each session starts at a random point, so it can already be raining. |
| **Rain: drops on the screen** `rain_drops` | Original / Xbox 360 | Xbox 360 | after a restart | Xbox 360 (the default): soft drops on the camera lens that bend and shade the picture behind them, in four shapes. Original: the PC game's bright round drop. Cost: about +0.5 ms while it rains. |
| **Xbox 360 sky** `x360_sky` | Original / Xbox 360 | Xbox 360 | after a restart | Xbox 360 (the default): the console's sky and clouds, rebuilt for the Mac. Original: the PC game's sky. Cost: Low. |
| **Always rain** `always_rain` | Off / On | off | after a restart | Makes it rain all the time, on every road. Off: rain comes only with the weather. From Extra Options. Cost: rain adds 1.5-2.6 ms of main-thread time while the road is wet. |

### Advanced: Smoke and sparks

| Setting | Choices | Default | Takes effect | What it does |
|---|---|---|---|---|
| **Soft particles** `soft_particles` | Off / On | on | after a restart | Smoke and spray fade out softly where they touch the road, walls and cars, instead of ending in a hard line. Off shows the hard edges. Cost: about +0.7 ms GPU at 4K in a burnout. |
| **Sparks and light trails** `xbox_particles` | Off / On | on | after a restart | Sparks fly when you scrape walls, and fast cars leave light trails. Off: no sparks or trails. Needs the XenonEffects texture pack. Cost: Low. |
| **Denser tyre smoke** `denser_smoke` | Off / On | on | at once | More smoke from each tyre when you skid, slide or do a burnout, as on the Xbox 360. Off: the thinner PC smoke. Cost: Low to Medium in a burnout. Weaker Mac: off (the Light preset does this). |
| **Stuff Pack smoke** `stuffpack_smoke` | Off / On | on | at once | Adds extra tyre-smoke layers on asphalt from the community Stuff Pack: darker, fuller smoke when you skid, slide or spin the wheels. Works with Denser tyre smoke. Cost: Low. |
| **Lit smoke and fog (in development)** `lit_smoke` | Off / On | off | at once | Smoke, steam and fog take the sun's light: thin edges glow toward the sun, thick billows are darker underneath, and road-flare smoke glows red. Off: evenly lit smoke. Cost: Low. In development: off until approved. |

### Advanced: Rear-view mirror

| Setting | Choices | Default | Takes effect | What it does |
|---|---|---|---|---|
| **Mirror: sharp HD picture** `mirror_hd` | Off / On | on | after a restart | Draws the rear-view mirror at high resolution instead of a blurry low-resolution picture. Off: the original blurry mirror. Cost: about 0 ms. |
| **Mirror: show all scenery** `mirror_full_scenery` | Off / On | on | at once | The mirror shows all the buildings and trees behind you. Off: a cut-down version. Cost: about 0 ms. |
| **Mirror: world shadows** `mirror_shadows` | Off / On | off | at once | Draws building and tree shadows in the rear-view mirror too. Subtle, as the mirror is small. Cost: about 1.4 ms of main-thread time, the most expensive mirror row. Weaker Mac: off. |
| **Mirror: car shadows** `mirror_car_shadows` | Off / On | on | at once | Cars behind you cast shadows in the mirror. Off: cars without shadows in the mirror. Cost: about 0 ms. |
| **Mirror: headlights and police lights** `mirror_lights` | Off / On | on | at once | Headlights and police light bars show in the mirror, so you see who is coming at night. Cost: about 0 ms. |
| **Mirror: knocked-over objects** `mirror_props` | Off / On | on | at once | Cones, signs and debris you knocked over also show in the mirror. Cost: about 0 ms. |
| **Mirror: keep it while looking back** `mirror_look_back` | Off / On | off | at once | Keeps the rear-view mirror on screen while you use Look Back. Off hides it while you look back. |
| **Mirror: keep sky detail** `mirror_neutral_exposure` | Off / On | on | at once | Stops the sky in the mirror from washing out to white, so clouds and sky colour stay visible. Cost: about 0 ms. |
| **Mirror: field of view %** `mirror_fov` | 80 to 130, steps of 5 | 100 | at once | How much the rear-view mirror shows. 100 is the original; lower is a closer, flatter view, higher a wider panoramic mirror. |
| **Mirror: HDR brightness** `mirror_hdr` | Off / On | on | at once | With HDR on, bright lights in the mirror shine as in the main picture. Off: the mirror stays at normal brightness. Cost: about 0 ms. |

### Advanced: Detail limits and engine

| Setting | Choices | Default | Takes effect | What it does |
|---|---|---|---|---|
| **Skip unseen scenery (leave on)** `preculler` | Off / On | on | at once | Skips drawing scenery you cannot see, the game's own speed trick. Nothing changes on screen. Leave on: off costs about 1.3 ms of main-thread time. |

## Display

### Screen

| Setting | Choices | Default | Takes effect | What it does |
|---|---|---|---|---|
| **Widescreen** `widescreen` | Off / On | on | after a restart | Fills a wide screen with the picture instead of a narrower picture with black bars at the sides. Leave on. |
| **Picture width (0 = auto)** `width` | 0 to 16384 | 0 | after a restart | The width of the 3D picture in pixels. 0 matches your screen, which is best. Weaker Mac: keep 0 and use Upscaling instead. |
| **Picture height (0 = auto)** `height` | 0 to 16384 | 0 | after a restart | The height of the 3D picture in pixels. 0 matches your screen, which is best. Weaker Mac: keep 0 and use Upscaling instead. |
| **How much you see to the sides** `fov_scaling` | Original / Xbox 360 / Exact | Xbox 360 | after a restart | How much of the road you see to the sides. Xbox 360 (the default): a little wider, like the console. Original: the PC game's view. Exact: matched to your screen's shape. |
| **Menu size %** `fe_scale` | 50 to 150 | 100 | after a restart | Makes the game's menus bigger or smaller. 100 is normal; raise it if menu text is hard to read on a TV. |
| **Brightness** `brightness` | -5 to 5 | 0 | at once | Makes the shadows and mid-tones a little lighter (+) or darker (-), like a TV's brightness; black and white stay as they are, and the menus are not changed. 0 shows the picture as it is. Cost: none. |

### Sharpness and upscaling

| Setting | Choices | Default | Takes effect | What it does |
|---|---|---|---|---|
| **Smooth edges (anti-aliasing)** `antialiasing` | Off / 2x / 4x | Off | after Apply | Smooths jagged edges on wires, fences and car outlines. 2x removes most stair-steps; 4x nearly all, at a distance too. Cost: 2x +1.5 ms, 4x +3.6 ms GPU at 4K; with HDR, 4x runs as 2x. Weaker Mac: Off, and use Upscaling instead. |
| **Upscaling (MetalFX) (in development)** `upscale` | Off / Quality (1.5x) / Performance (2x) | Off | after a restart | Draws the 3D picture at a lower resolution and sharpens it up to your screen with Apple MetalFX; menus and HUD stay sharp. Quality looks close to full resolution; Performance is faster and a little softer. Cost: saves GPU time. Weaker Mac: Performance. In development: off until approved. |

### HDR

| Setting | Choices | Default | Takes effect | What it does |
|---|---|---|---|---|
| **HDR (for HDR TVs)** `hdr_output` | Off / On | off | at once | Sends a high-dynamic-range picture: brighter sun, headlights and reflections, deeper darks. Needs an HDR display; wrong on an SDR one (washed out). Cost: +1.6-1.9 ms GPU at 4K. Pair with 2x smooth edges at most; 4x runs as 2x. |
| **HDR: how bright highlights get %** `hdr_highlights` | 0 to 100, steps of 10 | 60 | at once | Only with HDR on. How much brighter than white the brightest things get, like the sun and headlights. Higher is punchier, lower calmer. |
| **HDR: run at 60 Hz** `hdr_refresh_60` | Off / On | on | at once | Only with HDR on. Keeps the TV at 60 Hz, which avoids sparkles on some TVs. Turn off if your TV looks fine at 120 Hz. |

## Audio

### Audio

| Setting | Choices | Default | Takes effect | What it does |
|---|---|---|---|---|
| **5.1 surround sound** `surround_audio` | Off / On | off | at the next screen or load | Sends 5.1 surround sound to your speakers or receiver. Off: stereo. |
| **Mute in background** `mute_in_background` | Off / On | off | at once | Silences the game while its window is not in front (another app is active), and brings the sound back when you return. Off keeps the sound playing. Cost: none. |
| **Dolby Digital (for TVs and receivers)** `audio_dolby_digital` | Off / On | on | at the next screen or load | Sends surround as Dolby Digital over HDMI to a TV or receiver. Turn off if 5.1 sound comes out of the wrong speakers. |
| **Fix swapped centre/subwoofer** `audio_center_lfe_swap` | Off / On | off | at the next screen or load | Swaps the centre and subwoofer channels, for setups where voices come from the subwoofer. |

## Interface

### HUD and minimap

| Setting | Choices | Default | Takes effect | What it does |
|---|---|---|---|---|
| **HD HUD** `hud_hd` | Off / On | on | after a restart | The race HUD, minimap and fonts in sharp HD, made from the game's own art, with the gauges and icons redrawn as crisp outlines. Off shows the original, softer HUD. Cost: Low. |
| **Keep the HUD when looking back** `hud_on_look_back` | Off / On | off | after a restart | The HUD stays on screen while you look back, as in Underground 2. Off hides it while you look back. From Extra Options. |
| **Minimap: show all police** `show_nonpursuit_cops` | Off / On | off | after a restart | The minimap also shows police cars that are not chasing you. Off shows only your pursuers. From Extra Options. |
| **One-lap races show percent** `percent_1lap` | Off / On | on | after a restart | One-lap races show COMPLETED percent instead of LAP 1/1. From Extra Options. |
| **World Map in races** `world_map_anywhere` | Off / On | on | after a restart | Lets you open the World Map from the pause menu during races and pursuits too, not only while roaming. From Extra Options. |
| **Discord status** `discord_status` | Off / On | off | at once | Shows on your Discord profile that you are playing, whether you are driving or in the menus, and your time played, for your friends to see, while the Discord app is open. Off (the default) shares nothing; switching off clears it at once. Cost: none. |

### Menus, movies and subtitles

| Setting | Choices | Default | Takes effect | What it does |
|---|---|---|---|---|
| **Xbox 360 front end** `x360_frontend` | Off / On | on | after a restart | The Xbox 360 menus, title and loading art and garages from the Xbox 360 Stuff Pack, when Setup added it. Off: the PC menus (and any menu mod in your game folder). |
| **Xbox 360 start screen** `splash_xbox` | Off / On | off | after a restart | Shows the Xbox 360 start screen (dark, the logo to the right). Off: the PC start screen, the BMW picture with the EA and HD logos. |
| **Fit movies to the screen** `movie_fit` | Off / On | on | at the next screen or load | Shows the full widescreen movies without cutting off the sides. |
| **Skip intro movies** `skip_movies` | Off / On | off | after a restart | Skips the movies at start-up and in the story (logos and story videos). |
| **Skip cut-scenes** `skip_nis` | Off / On | off | after a restart | Skips the in-game cut-scenes. |
| **Subtitles** `subtitles` | Off / On | off | after a restart | Shows English subtitles in the story scenes. From Extra Options. |
| **Longer profile names** `long_profile_names` | Off / On | on | after a restart | Allows profile names up to 15 characters. |

## Gameplay

### Career and rewards

| Setting | Choices | Default | Takes effect | What it does |
|---|---|---|---|---|
| **Settings menu pauses the game** `pause_in_menu` | Off / On | on | at once | The game stops while this settings menu (F10) is open: race clock, traffic, police and music wait, and carry on when you close it. Off keeps the game running behind the menu. Cost: none. |
| **Black Edition extras** `black_edition` | Off / On | on | after a restart | Adds the Black Edition cars, races and content. |
| **Race beaten Blacklist rivals again** `replay_blacklist` | Off / On | on | after a restart | Lets you race Blacklist rivals you already beat. |
| **Special vinyls** `special_vinyls` | Off / On | on | after a restart | Adds the special vinyls category in the paint shop. |
| **Max performance button** `max_perf_shop` | Off / On | on | after a restart | Adds a button in the shop that buys all performance upgrades at once. |
| **Rival rewards to pick (of 6)** `marker_count` | 1 to 6 | 6 | after a restart | How many reward markers you pick after beating a Blacklist rival. 6 is the most. |
| **Helicopter takedown bounty** `helicopter_bounty` | Off / On | on | after a restart | Taking down a police helicopter gives a 100,000 bounty and an announcement. A community fix; the Xbox 360 does not have it. |
| **New career: $10,000 bonus** `ug2_save_bonus` | Off / On | on | for new careers | A new career starts with the $10,000 bonus the game gives for an Underground 2 save. |
| **New career: extra starting cash** `starting_cash` | 0 to 100000000, steps of 100000 | $0 | for new careers | Extra money at the start of a new career. 0 is the normal start. |

### Police and heat

| Setting | Choices | Default | Takes effect | What it does |
|---|---|---|---|---|
| **Choose police heat levels** `heat_level_override` | Off / On | off | at the next screen or load | On: police heat stays between the lowest and highest levels below. Heat 6 to 10 send up to 8 police cars, like heat 5. |
| **Heat level: lowest** `heat_level_min` | 1 to 10 | 1 | at the next screen or load | The lowest police heat level when Choose police heat levels is on. |
| **Heat level: highest** `heat_level_max` | 1 to 10 | 10 | at the next screen or load | The highest police heat level when Choose police heat levels is on. |
| **No catch-up** `no_catch_up` | Off / On | off | after a restart | Turns off the opponents' catch-up, so racers no longer slow down or speed up to stay near you. This changes how races play. From Extra Options. |

### Unlocks and shortcuts

| Setting | Choices | Default | Takes effect | What it does |
|---|---|---|---|---|
| **Unlock everything** `unlock_all` | Off / On | off | after a restart | Unlocks all cars, parts, events and areas, and lets you play hidden tracks. From Extra Options. |
| **Remove glowing barriers** `remove_neon_barriers` | Off / On | off | after a restart | Removes the glowing barriers that close off locked areas and routes. From Extra Options. |
| **Open the Old Bridge** `remove_old_bridge_barrier` | Off / On | off | after a restart | Removes the barrier that closes the road to the Old Bridge. From Extra Options. |
| **Save and load positions** `hot_positions` | Off / On | off | after a restart | While driving, Left Shift plus 1 to 5 saves where you are and Left Ctrl plus 1 to 5 takes you back there. From Extra Options. |
| **Skip music track while driving** `skip_track_anywhere` | Off / On | on | after a restart | The skip-track key works while driving too, not only in the menus. From Extra Options. |

### Fixes (leave on)

| Setting | Choices | Default | Takes effect | What it does |
|---|---|---|---|---|
| **Fix: bounty at heat 10** `zero_bounty_fix` | Off / On | on | after a restart | Fixes the zero bounty for takedowns at heat 10 and the over-long combo timer. From Extra Options. |
| **Fix: prologue forced again** `dday_fix` | Off / On | on | after a restart | Stops the game sending a career that is already under way back to the prologue. From Extra Options. |
| **Fix: unused Busted scenes** `busted_nis_fix` | Off / On | on | after a restart | Lets the Busted cut-scenes for heat 2 and 3 play, as intended. From Extra Options. |

## Mods

### Your mods

| Setting | Choices | Default | Takes effect | What it does |
|---|---|---|---|---|
| **Player mods (textures from your Mods folder)** `player_mods` | Off / On | on | after a restart | Textures you put in the Mods folder next to your saves replace the game's and ours, matched by file name (Manage in the launcher has Open mods folder). An empty folder changes nothing. A report in the Mods folder lists what loaded. |
| **Data mods (changed game files)** `data_mods` | Off / On | on | after a restart | Game files in your DataMods folder (beside Mods) replace the game's own while it runs: cars, tracks, menus, sounds, data. Your game folder is never changed and no program files are used. A mod that changes the career data can change which careers load; your saves are backed up. |

### Diagnostics

| Setting | Choices | Default | Takes effect | What it does |
|---|---|---|---|---|
| **Log my position** `position_log` | Off / On | off | at once | Writes where you are to the log, so you can report a spot that needs fixing. |
| **Log memory use** `memory_log` | Off / On | on | at once | Writes the game's memory use to the log once a minute. |
| **Log controller input** `input_trace` | Off / On | off | at once | Writes controller and keyboard input to the log, for bug reports. |

## Controls

### Controller

| Setting | Choices | Default | Takes effect | What it does |
|---|---|---|---|---|
| **PlayStation-style buttons** `ps2_controls` | Off / On | on | at once | The default controller layout: Cross accelerates and Triangle goes back, like the PlayStation version. Change any driving button in the game's Options, Controls. |
| **Triggers as pedals** `trigger_pedals` | Off / On | off | at once | R2 accelerates and L2 brakes, both analog, like most modern racing games. Off: the face-button layout. |
| **Stick deadzone** `stick_deadzone` | 0 to 30 | 8 | at once | How far a stick must move before the game sees it, in percent. Raise it if the car steers by itself with the stick at rest; lower it for a quicker response. 8 is the default. Cost: none. |
| **Trigger deadzone** `trigger_deadzone` | 0 to 30 | 0 | at once | How far a trigger must be pressed before it counts, in percent; past it the full range is kept. Raise it if the car creeps forward or brakes with the triggers released. 0 is the default. Cost: none. |
| **L3 skips music track** `l3_skip_track` | Off / On | on | at once | Clicking the left stick skips to the next music track, like the T key. |
| **PlayStation button icons** `ps_icons` | Off / On | on | after a restart | Shows PlayStation button symbols in the menus when you use a controller. Needs XtendedInputButtons.tpk (the XtendedInput mod) in your game folder's GLOBAL folder; without it, or Off, the PC game's prompts show. |
| **Shift up button** `shift_up` | -1 to 11 | 7 | at once | Controller button for shifting up with manual gears. 7 is R2; -1 turns it off. The settings guide lists the other numbers. |
| **Shift down button** `shift_down` | -1 to 11 | 6 | at once | Controller button for shifting down with manual gears. 6 is L2; -1 turns it off. The settings guide lists the other numbers. |
| **Shift up: second button** `shift_up_secondary` | -1 to 11 | -1 | at once | An extra controller button for shifting up. -1 turns it off. |
| **Shift down: second button** `shift_down_secondary` | -1 to 11 | -1 | at once | An extra controller button for shifting down. -1 turns it off. |

### Controller rumble

| Setting | Choices | Default | Takes effect | What it does |
|---|---|---|---|---|
| **Rumble: crashes %** `impact_rumble` | 0 to 200 | 100 | at once | How hard the controller shakes when you hit something. 100 is normal; 0 turns it off. |
| **Rumble: driving %** `surface_rumble` | 0 to 200 | 100 | at once | Shaking from the road surface, wheelspin, nitrous and gear shifts. 100 is normal; 0 turns it off. |
| **Rumble: cobbles and metal** `paved_surface_rumble` | Off / On | on | at once | Also rumbles on cobblestones, stone and metal surfaces. Off matches the Xbox 360, which stays still there. |
| **Rumble style** `rumble_model` | PC wheel adaptation / Xbox 360 | Xbox 360 | at once | Xbox 360 (the default): rumbles like the console. PC wheel adaptation: the PC version's steering-wheel feedback turned into rumble. |
| **Rumble: crash fade %** `collision_rumble_fade` | 10 to 100, steps of 10 | 50 | at once | How long the rumble lasts after a crash or scrape. 100 is the Xbox 360; lower is shorter. |
| **Rumble: minimum strength %** `rumble_floor` | 0 to 10 | 1 | at once | Rumble weaker than this is skipped, so the controller does not buzz all the time. 0 plays everything. |

### Keyboard keys

| Setting | Choices | Default | Takes effect | What it does |
|---|---|---|---|---|
| **Pause key freezes the camera** `freeze_camera_key` | Off / On | off | after a restart | While driving, the Pause/Break key freezes and unfreezes the camera, for screenshots. From Extra Options. |
| **Light keys (H and O)** `light_keys` | Off / On | off | after a restart | While driving, H switches your headlights and O your police lights on and off. From Extra Options. |
| **Free camera (Backspace)** `debug_camera` | Off / On | off | after a restart | While driving, Backspace switches to the game's free camera and back, for screenshots. From Extra Options. |
| **Heat keys (F7 and Page Up)** `heat_keys` | Off / On | off | after a restart | While driving, F7 arms the heat keys and Page Up then steps the heat level between the lowest and highest heat settings. From Extra Options. |

## Not in the menu

Kept so that older profiles load cleanly, fixed by the port, or tuning most players never need; the menu does not list them and your saved value is kept.

| Key | Value | Why |
|---|---|---|
| `look` | Definitive | Retired: Definitive is the only look. A profile saved on Xbox 360 moves to Definitive once (logged), with the golden colour filter kept on. Not a choice. |
| `msaa` | -1 | The anti-aliasing setting of 1.0 (-1 the game's own, 0 off, 1 4x). Read once and carried over to Smooth edges as Off; then reset to -1. |
| `spray_fill` | 1 | Rain spray behind cars drawn from 1/K of the particles, each denser. 1 = as the game (larger values showed no speed gain). |
| `settings_version` | 0 | The last one-time settings migration done (1: Xbox 360 sky 1 -> 2; 2: the Xbox 360 look retired). Not a choice. |
| `grass` | Off | Retired in 2.0: always off, whatever was saved (no visible gain); the code stays for later. |
| `upscale_temporal` | off | Not built yet (MetalFX temporal upscaling, planned); always off. |
| `appearance` | Definitive (default) | Definitive: the console-style look this port is built around. Custom: Definitive with your own changes below. |
| `golden_filter` | Auto | The golden filter before the Look was retired (Auto followed the Look). Read once and moved to Golden colour filter. Not a choice. |
| `hud_vector` | on | Merged into HD HUD (one switch since 3.0). Not a choice: the vector gauges and icons load whenever HD HUD is on. |
| `hybrid_exposure` | 50 | Like your eyes adjusting: tunnels get a bit brighter and bright daylight a bit darker. 0 turns it off. |
| `hybrid_vignette` | 0 | Gently darkens the corners of the picture, like a camera lens. 0 is off. |
| `hybrid_shadows` | 0 | Lifts the darkest parts of the picture so you can see detail in shadows. Black stays black. 0 is off. |
| `clean_pools_props` | off | Adds local lamp lighting to eligible props (tunnel fans, pipes, gantries) in the main view. May increase stutter in busy scenes. Needs Light pools from lamps. Restart to apply. |
| `lamp_daylight` | off | Outdoor street lamps fade out while the sun is up and come on at dawn and dusk; tunnel lamps stay on. Their glow follows. Needs Light pools from lamps. |
| `dof_scene_depth` | on | Retired from the menu in 2.0: always on (off only lost the blur in some rain frames). |
| `mirror_grade` | off | Gives the rear-view mirror the main view's brightness and shadow lift, so it no longer looks flat or too bright. Needs Mirror: keep sky detail. In development: off until approved. |
| `mirror_glass` | 0 | Makes the mirror look like real glass: a slight cool tint, darker edges and a thin highlight along the top. 0 is the original. In development: off until approved. |
| `mirror_msaa` | Off | Smooths the jagged edges in the rear-view mirror with 4x multisampling, like the main picture (and smooths its leaves and fences). In development: off until approved. Restart to apply. |
| `mirror_pools` | off | Street lamps light the road and walls in the rear-view mirror too, as in the main picture. In development: off until approved. |
| `rain_amount` | 100 | How many raindrops there are. 100 is normal. Restart to apply. |
| `rain_intensity` | 45 | How visible each raindrop is. Restart to apply. |
| `rain_reflection` | 100 | How shiny and reflective wet roads look. Restart to apply. |
| `rain_size` | 100 | How big the raindrops are. 100 is normal. Restart to apply. |
| `rain_speed` | 300 | How fast the rain falls. Restart to apply. |
| `rain_crossing` | 200 | How much the rain drifts sideways. Restart to apply. |
| `rain_gravity` | 35 | How heavily the rain drops. Restart to apply. |
| `x360_exposure` | on | Retired from the menu in 2.0: always on (off also broke the HDR highlight mark). |
| `mod_hq_preset` | on | Makes the game's highest detail presets use this port's higher quality settings. |
| `force_car_lod` | -1 | Forces one detail level for car models: -1 lets the game decide, 0 is the most detailed (from Extra Options). Restart to apply. |
| `force_tire_lod` | -1 | Forces one detail level for wheels: -1 lets the game decide, 0 is the most detailed (from Extra Options). Restart to apply. |
| `scenery_trace` | off | Writes scenery pop-in and flicker to the log, for bug reports. Restart to apply. |
| `sim_rate` | 60 | How many times a second the game updates. Keep it at 60. Restart to apply. |
