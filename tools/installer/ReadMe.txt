NFS Most Wanted Native - Setup

This app builds the native Mac version of Need for Speed: Most Wanted (2005) on your Mac, from YOUR OWN copy of the PC game.
It contains no game files. Everything happens on your Mac; nothing is uploaded.

Optional later-game cars: appearance review accepted 10 PC and 9 Xbox 360
conversions: 997GT3RS, BRERA, CCX, CORVETTEZ06, EUROPA, JAGUARXK, MR2,
R32 and R8 on both platforms, plus 300C on PC. JAGUARXK is visible in race
and showroom on both platforms.
240SX remains unapproved on both platforms because its logo is a placeholder.
997TT, CAMARON, G35, MAZDASPEED3 and MUSTANGSHLBYN on both, plus 300C on
Xbox 360, failed conversion at review time. Later fixes need new render evidence.
These reviewed conversions are not available for import in this package yet.
Recognition alone does not approve a car.
With an approved package, use Extras to import or manage your own files.
Keep retained generations: if a career needs removed cars, restore its verified
generation or reimport identical inputs with the same package, then restart.
Do not delete the career or its dependency files to bypass a refusal.

You need:
  - An Apple Silicon Mac (M1 or later) with macOS @MIN_MACOS@ or later.
  - Your own installed PC game folder: Need for Speed: Most Wanted, version 1.3, the folder with speed.exe.
    (Building from a PC disc image is not available yet.)
  - About 12 GB free while Setup runs (measured: 9.4 GB at most with a game folder and an Xbox 360 copy, 2.8 GB
    more from the .zip) and some time: the first build compiles the whole game on your Mac. Afterwards the game
    uses about 9 GB (about 3.5 GB without the Xbox 360 copy); a rebuild also keeps the version it replaced.
  - Optional: your own Xbox 360 copy of the game, as a folder or an .iso, to add the Xbox 360 features.
    Without it the PC game alone is used and those features are simply absent. Setup lists them for you.

Opening the app the first time:
  This app is not notarized by Apple, so macOS blocks it the first time. Open it once, click Done,
  then go to System Settings > Privacy & Security and click "Open Anyway" next to its name.

Then:
  1. Install Apple's Command Line Tools if asked (free).
  2. Choose your game folder, optionally your Xbox 360 copy, and where the app should go.
  3. Click Build. Cancel stops it and leaves anything already installed as it was.
  4. When it finishes, click Open.

If something fails, Setup says which step, what to do, and Show Log opens the full log.

Your game data and settings: ~/Library/Application Support/NFSMW-port/
Setup's own files (safe to delete after building): ~/Library/Application Support/NFSMW-Setup/
Logs: ~/Library/Logs/NFSMW-Setup/

This project is not affiliated with or endorsed by Electronic Arts.
