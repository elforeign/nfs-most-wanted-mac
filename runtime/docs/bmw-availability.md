# Stock BMW availability

## Decoded premise

The subject is stock `bmwm3gtre46` KIT00, not `bmwm3gtr`.
The installed `GLOBAL/GLOBALB.BUN` preset at `0x24BFA0` names
`BMWM3GTRE46`, `M3GTRCAREERSTART`, frontend `23CC4A4E`, and scripted
pvehicle `39DAE0F8`. Its ordinary stock pvehicle is `4E085FB3`.
The separate preset at `0x243CF0` names `BMWM3GTR`, `CE_GTRSTREET`,
frontend `81C2FEA6`, and pvehicle `CD88083F`.

Read-only PC vault decoding gives these frontend layout fields:

| Collection | FE_ATTRIB.bin layout | Category +38 | Cost +4C | Available +58 | UnlockedAt +59 |
| --- | --- | --- | --- | --- | --- |
| `bmwm3gtre46` / `23CC4A4E` | `9048` | 1 | 500000 | 0 | 15 |
| `bmwm3gtr` / `81C2FEA6` | `BA9C` | 1 | 0 | 0 | 15 |
| `997s` / `3C4DA7DA` | `9D64` | 1 | 75000 | 1 | 6 |

Both BMW pvehicles have PlayerUsable=1. Native FEPlayerCarDB::Default
(`00590D40`, named PC `speed_005891a0.c:8390`) already enumerates them.
FECarRecord::Default (`005815B0`, `speed_00577af0.c:9338`) converts
category 1 / Available=0 into flags `00020020`, rather than ordinary
stock flags `00020001`. MatchesFilter (`0056F180`,
`speed_0056d9b0.c:2457`) requires both a matching category and a matching
low flag. Both the career lot and Quick Race Stock use `000F0001`:
UIQRCarSelect::Setup (`007C2780`, `speed_007b4020.c:12563,12571`),
then RefreshCarList (`007BF7B0`, same file:10159).

CareerUnlocker (`0058A5F0`, `speed_005891a0.c:1383`) and the ordinary
QuickRaceUnlocker path (`0058A0D0`, same file:1106) accept UnlockedAt
15 throughout an active career (rank 15 down to 1). No unlock-all flag
or bonus award is needed for the stock record. A profile without a career
starts at rank 16 (`CareerSettings::Default`, `0057F4D0`, named PC
`speed_00577af0.c:7110`); the authored threshold 15 would still lock the
new stock choice in that profile's Quick Race list. The separate CE_GTRSTREET
bonus handle `363A1FEA` is explicitly listed by `007B4F60` and gated by
IsCollectorsEdition in `0058A0D0`; that bonus path stays native.

The added-car catalog handles new identities, assets and sparse type slots.
Its menu policy copies a price donor's frontend fields
(`car85.c:1320`), and reconciliation inserts missing stock rows.
This BMW already has both its retail identity and its stock row; adding
a catalog reservation would duplicate it and create an unnecessary save
dependency. Use the existing native row and frontend price instead.

## Behavior and switch

`NFS_BMW_AVAILABLE` defaults **on**. Set it to `off` (or `0`) and restart
to restore retail availability. There is no added-car pack requirement.

One hook before UIQRCarSelect::Setup normalizes only the canonical BMW's
existing stock row between unavailable and ordinary stock, before Setup
derives the remembered selection's filter. The live retail 997 Carrera S
frontend supplies its **$75,000** price: the same category and a normal
sport-coupe price tier. Its menu unlock threshold is 16, including profiles
without a career. Profile ranks, category, PlayerUsable and the separate
Black Edition BMW stay native. Missing price data hides the stock choice.
Native list construction, unlocking, affordability, purchase, ownership
allocation and cash spending follow the ordinary car path.

Each price lookup constructs and destroys scoped native frontend instances.
No pointer survives a frontend vault unload; the next Setup uses its new
layouts. The shared BMW frontend cost changes; its Available field stays 0.
Only canonical stock rows with flags `00020020` or `00020001` and no linked
customization/career record are normalized. The scripted starter's pvehicle,
special handles, preset rows, owned cars and linked pools are untouched.

The hook runs after the initial FE database CRC is established
(`cFrontendDatabase::Default`, named PC `speed_00598f50.c:18718`), so that
CRC and the native save format remain unchanged. Existing saves need no
migration, added-car metadata or new attribute identities. Off also hides
a stock row saved while On. If that disabled stock row was remembered in
Quick Race, its selection resets to the native Stock-tab fallback; a
remembered owned car is retained.

A bought BMW remains owned, customized and selectable in the career garage
and Quick Race's career-car list with the switch Off. Its retail asset and
saved links remain installed. Off restores the authored frontend cost
($500,000), including native valuations derived from GetCost; it does not
remove or convert the bought car. The career starter and its story records
retain their original identities and flags in both switch states.

## Offline proof

`runtime/bmw_availability_test.py` executes pinned native Setup,
RefreshCarList, filtering, unlockers, purchase notification, cash spending,
career/profile serialization and loading, plus production public save
admission. Attribute lookup, rendering, list memory/sorting and career-car
allocation are contract doubles. Synthetic frontend fields are the decoded
retail values above; no real profile or game process is used.

It checks both player slots, all career ranks and profiles without a career, remembered stock/owned
selections, the original missing behavior, wrong-asset/price/owned-row
controls (including the old blank-profile lock), frontend layout replacement, and the same bought-car save loaded
On and Off. The game check still needs a career purchase, Quick Race
selection and captures of the stock BMW.

The original gives **0/32** positive cases in each normal stock list; the
hook gives **32/32** in each. Off gives **0/32** in each, with the bought
car retained in owned-car lists. All four targeted mutations are rejected.
