# AI Player, Strategy Specification

An external process that plays the local player's civ by reading and writing the
running client's memory, using only fields a human could have changed by clicking.
This document is the **rulebook**: what the AI senses, what it may touch, and the
if/then rules for each X of 4X. It is written in the vocabulary of
`ejbo_annotations.json` (`Class:offset`) so every rule is traceable to a confirmed
field or explicitly marked as blocked.

---

## 1. Operating model

**The AI is a turn-driven controller, not a bot in the loop.** It wakes on a turn
boundary, snapshots memory, decides, writes, and sleeps until the turn counter
moves. One decision pass per turn.

| Concern | Mechanism |
|---|---|
| Clock | turn counter at `.data 0x008578E8`; wake on change |
| Snapshot | `ejbo_viewer.ViewerState.scan()` → tagged objects with per-class field reads |
| Untagged objects | resolve a pointer's first dword against the RTTI map (`Planet:296` → production class) |
| Write | `ejbo_viewer.write_bytes` |
| Turn driving in test | `advance_turns.py` shortens turn length so a game runs in minutes |

### 1.1 The no-cheating rule

The AI writes only fields that a UI action could have written, to values the UI could
have produced. Concretely, three constraints, in order of how easy they are to violate
by accident:

1. **No free resources.** 
2. **No illegal values.**
3. **No omniscience.**

---

## 2. Sensors

Everything the AI reads. Fields marked ⚠ are unconfirmed or derived by observation
rather than read directly.

### 2.1 Civ-level (`Owner`, ours and rivals)

| Quantity | Field | Notes |
|---|---|---|
| Civ name | `Owner:-44` / len `Owner:-28` | identity |
| Registry key | `Owner:-16` | stable per-civ id, better join key than the name |
| Cash | `Owner:8` | |
| Research stockpile | `Owner:12` | accrues every turn regardless of topic |
| Score | `Owner:28` | rival strength proxy |
| Completed research | `Owner:108`/`112`/`116` | 8-byte `[id, cost]`; techs and doctrines share it |
| Current topic | `Owner:144` (+ mirror `Owner:152`) | `-1` = none |
| Unlocked facility types | `Owner:172`/`176`/`180` | 8-byte `[vftable, typeId]`, what we *may* build |
| Unlocked scan types | `Owner:200` | 2 = route, 3 = fleet |
| Scan reports | `Owner:320`/`324`/`328` | ptr each; `+4` type, `+12` turn, `+16/20/24` target XYZ, `+28` scanned civ |
| Golden age turns | `Owner:744` | 9999 = none ⚠ |
| Food cost per citizen | `10 + Owner:748` | UI renders base 10 + bonus |
| Income per citizen | `10 + Owner:752` | same rendering |
| Output bonuses | `Owner:756/760/764/768` | food / production / science / mining, percent |
| Resource stocks | `Owner:1128`/`1132`/`1136` | `[resId, amount]`, 0=metal 1=deut 2=radio 3=crystal 4=exotics |

### 2.2 Planet-level

| Quantity | Field | Notes |
|---|---|---|
| Owner | `Planet:40` | node[0] = `Owner` at tag-8; null node `0x00857C54` = unowned |
| Name | `Planet:52` / len `Planet:68` | len 0 ⇒ never colonised |
| Position | `Planet:4/8/12` | X / Z / Y |
| Space | `Planet:104 >> 16` | max pop = total space / 10, where total = space + round(space × trait 38 / 100), the planet-space trait (`Planet.total_space`); confirmed live by raising trait 38 |
| Food stored | `Planet:112`, cap `Planet:116` | |
| Construction progress | `Planet:120` | |
| Population | `Planet:144`/`148`/`152` | 16-byte elems; `+0` = job id, `+12` low16 = turn added |
| Population count | `(148-144)/16`, mirrored `Planet:352` low16 | |
| Stationed military | `Planet:168`/`172`/`176` | 16-byte elems, the same record as a citizen; `+0` is the job id and reads 3, `+12` low16 turn added |
| Built facilities | `Planet:204` map, walk to `[typeId, count]` | 0 farm, 2 shipyard, 4 university, 6 military camp, 8 defence agency, 9 light turret, 13 mine, 14 robo mine |
| Resource deposit | `Planet:412` vector, one `[resId, basePerMiner]` record | the planet's one resource; a miner yields base + 40% per mine + 100% per robo mine, then the mining trait (5) (`Planet.resource`, `exploit.miner_yield`) |
| Distinct facility types | `Planet:208` | == size of the `:204` map |
| Selected building | `Planet:284` | type id, *last selected*, not current activity |
| Current production | `Planet:296` → RTTI | `Facility` / `PilingUpWealth` (`0x0080B540`) / `Scan` / `ShipProduction` |
| Building now? | `Planet:344` | 1 = constructing, 0 = wealth |
| Recruitment rate | `Planet:100` byte 3 | percent of the food SURPLUS diverted to military; the rest goes to citizen growth. Ceiling is per planet: **40 with no military camp, +20 per camp**, confirmed in the UI at 60% with one camp |
| Governor | `Planet:496` | node[0] = `Governor` |
| Colonised on turn | `Planet:92` | 0 is ambiguous, disambiguate with `Planet:40` |

### 2.3 Ship-level

| Quantity | Field | Notes |
|---|---|---|
| Owner | `Ship:40` | writable, but out of bounds for us |
| Position | `Ship:4/8/12` | |
| In orbit of | `Ship:44` | stored, not derived from position |
| Order object | `Ship:48` | plain struct; `+16/20/24` = destination XYZ |
| Order type | `Ship:52` | 0 hold, 1 move, 2 scout, 3 colonize, 4 attack, 5 conquer, 6 bio-bomb, 7 move-near, 8 create wormhole (the engine's own name table at `0x7FC898`) |
| Wormhole countdown | `Ship:61` | turns left on a type-8 order, 11 at issue; 0 for every other type. Re-issuing restarts it, and on completion the ship becomes a `Wormhole` and is deleted. Nothing else reads it |
| Pending-orders flag | `Ship:76` | 1 on local civ's ordered ships ⚠ |
| Design | `Ship:108` | node[0] = `ShipDesign` |
| Condition | `Ship:136` | 0.0-1.0; the only per-ship damage state |

### 2.4 Design-level (`ShipDesign`), used to classify ships by role

| Quantity | Field |
|---|---|
| Name / len | `ShipDesign:8` / `:24` |
| Speed | `ShipDesign:36` |
| Build cost | `ShipDesign:44`, upkeep `:48` |
| HP base / effective | `ShipDesign:52` / `:56` |
| Firepower (3 slots) | `ShipDesign:60/64/68` |
| Shield | `ShipDesign:72` |
| Metal / radioactives cost | `ShipDesign:92` / `:100` |
| Fitted modules | `ShipDesign:224`, `[vftable, id]`, 0 colony, 1 troop bay, 2 pilot cabin |
| Fitted weapons | `ShipDesign:200`, 0 mass driver, 10 fusion bomb |
| Fitted chassis | `ShipDesign:128`, 0 shuttle, 1 corvette |

**Role classification** (the AI's own taxonomy, derived from the above):

```
COLONY  = 0 in modules(:224)
TROOP   = 1 in modules(:224)
WARSHIP = max(:60, :64, :68) > 0
SCOUT   = not COLONY and not TROOP and not WARSHIP     # unarmed hull, pick fastest :36
```

A design can be both WARSHIP and TROOP; conquest wants TROOP, escort wants WARSHIP.

### 2.5 Derived-by-observation sensors

The engine's formulas are not in memory. These are computed by differencing two
consecutive turn snapshots and are the honest substitute:

| Quantity | How |
|---|---|
| `foodDelta[planet]` | Δ`Planet:112` per turn, **the** signal for the food rules |
| `productionRate[planet]` | Δ`Planet:120` per turn while `Planet:344 == 1` |
| `scienceRate` | Δ(`Owner:12` + Σ cost in `Owner:108`) per turn, **net of science wastage**, see below. The cost term undoes a completion, which subtracts the topic's cost from `Owner:12` |
| `incomeRate` | Δ`Owner:8` per turn |
| `popGrowth[planet]` | Δ population count per turn |

---

## 3. Actuators, the button-click modules

One module per UI action. Grade is how confident we are it works today.

| # | Action | Mechanism | Grade |
|---|---|---|---|
| A | **Set a citizen's job** | write job id to `Planet:144` element `+0` | **READY**, plain int, no allocation, same field `set_population.py` already writes |
| B | **Select the next building** | write type id to `Planet:284` | **READY**, paired with C in `actions.build_facility`; farms and shipyards were built repeatedly over a full game |
| C | **Start constructing / switch to wealth** | engine `SetProduction` `0x004F8290`, `__thiscall(item, resetOnFail)` `ret 8` on planet tag + 88, with the planet's embedded `Facility` (tag + 280) or the `PilingUpWealth` singleton `0x0080B540`; then `Planet:344` | **READY**, `actions.build_facility` / `set_wealth_mode`, through the engine since Oct 2026 (see D). Confirmed live: a farm build accepted and pointed at the embedded `Facility` |
| D | **Queue a ship** | engine `SetProduction` with the `ShipDesign*` (tag - 12), reset-on-fail 0; then `Planet:344 = 1` | **READY**, `actions.queue_ship`. **Until Oct 2026 this was a raw `Planet:296` write, which skipped the ship's resource cost**: `SetProduction` is the only place the cost is checked and taken (`0x00512E80`), and where it is refunded when production changes away. Every AI ship until then was built without paying resources, and any later engine production change refunded a charge never made. Confirmed live after the fix: with 30 metal a `t1` (162) was refused; after buying 200 it was accepted and charged 162 metal and 30 deuterium; switching to wealth refunded both exactly |
| D′ | **Stop building / generate wealth** | `SetProduction(PilingUpWealth, 1)`, clear `Planet:344` | **READY**, `actions.set_wealth_mode`; refunds a ship's reserved resources, which a raw write lost |
| I | **Design a ship** | `operator new(0x118)` + default ctor `0x00546EC0`, `fit_parts`, `set_design_owner`, `register_design` | **REGISTERED, not yet built from**, see below. The object is created, tagged, owned, parts fit, and entered in the civ's design list `Owner:84` by the engine's own calls. Not yet queued, built or saved |
| E | **Retarget an existing order** | rewrite `Ship:48+16/20/24`, the route leg, progress, then `Ship:52`/`Ship:76` | **READY**, `actions.retarget_order` |
| E′ | **Originate an order** | engine's own `operator new` + order constructor, via `remote.py` | **READY**, `actions.create_order`; see §6a Route 2 |
| E″ | **Move an order between ships** | detach donor, attach recipient | **READY**, `actions.transfer_order` |
| F | **Set research topic** | write `Owner:144` **and** `Owner:152` | **READY**, confirmed writable |
| G | **Set recruitment rate** | write byte 3 of `Planet:100` | **READY**, `actions.set_recruitment`. Driven live at 0 / 20 / 60 % with the military store responding each time; the ceiling in §4.3 R-XPL-05 is honoured by `max_recruitment` |
| H | ~~Assign a governor~~ |, | **DROPPED**, the AI manages planets itself and leaves `Planet:496` unset |
| J | **Sell a facility** | call `PlanetProperties::SellFacility` `0x004F8580` (`__thiscall`, `ret 8`, ECX = `planet_tag + 88`) | **READY**, `actions.sell_facility`. Confirmed live twice: a military camp paid exactly **75** and was removed. `price = round(facilityValue * 0.05) * count`; the 0.05 is the double at `0x753CF8` |
| K | **Hurry the current build** | call `PlanetProperties::HurryProduction` `0x004EFFF0` (`__thiscall`, ECX = `planet_tag + 88`) | **READY**, `actions.hurry_production`. `cost = 4 * (total - progress)`; needs `progress/total >= 0.5`, `cost <= Owner:8`, production kind != 7 and `Planet:300 == 0`. Confirmed live: cash moved by exactly the quoted price and progress jumped to its total |
| L | **Order a ship at a TARGET OBJECT** | engine `ShipCommand` ctor `0x004E9BA0` + `Ship::SetCommand` `0x004D9DC0` | **WORKS, unused.** The old crash was our stub: it pushed one argument to a `ret 8` function and returned to address 0. Fixed and verified over five boundaries (§4.4). It makes a ship ESCORT one of our own ships turn after turn; aimed at an enemy or a planet the engine drops the target after one turn, so it is no pursuit tool |
| M | **Move a ship to a target, route only** | `create_order` + `retarget_order`, and nothing else | **READY**, `exterminate.send_to`. The replacement for L. Reuses `Ship:48` when the ship has one, so it allocates nothing per turn. `Ship:56` stays the null node and the ship navigates correctly anyway |
| N | **Conscript a citizen** | `ChangeCitizenJobs` `0x00574180` (`__cdecl`, 4 args) with job id 3, priced by `GetDraftCost` `0x00516060` | **READY**, `actions.conscript`. Confirmed live: citizen count −1, stationed military +1, and cash down by exactly the quoted 115. The cost getter was validated first against a formula derived from the disassembly, six readings across two civs, all exact |
| N′ | **Conscript straight onto a ship** | rewrite the citizen list without the drafted records; hand those records to a fresh crew buffer; debit `GetDraftCost` | **READY**, `actions.conscript_to_crew`. Confirmed live twice, and it is what finally unblocked Exterminate. See below |
| O | **Buy or sell a resource** | engine `BuyResource` `0x005125D0` / `SellResource` `0x00512560`, `__thiscall(index, qty)` `ret 8` on Owner tag + 1116; quotes from `0x00512090` / `0x00511EF0` | **READY**, `actions.market_buy` / `market_sell`. Confirmed live: 100 deuterium cost exactly the quoted 1,200 and moved the next quote to 1,208; 600 metal sold for 585. Tag + 1104, one static reading's off-by-12, crashed the client in `GetTrait`; `remote.market_trade` now checks the owner back-pointer first |
| P | **Produce and use a scan** | produce: `Planet:292` = type, `SetProduction` on the embedded `Scan` (planet tag + 288); held: `std::map<type,count>` at `Owner:64` (`Owner:68` is the map's size, not a count), getter `0x0053B8F0`; use: `UseScans` `0x0054E840`, `__cdecl(out, targets, Owner primary, type)` | **READY**, `actions.queue_scan` / `use_scan`, `remote.scan_count`. Confirmed live: a route scan produced on #82, held 1, used on BadGuy's #213, one report appended to `Owner:320` and the inventory back to 0. The engine checks the inventory and the target class; one scan per report |

---

## 4. The rules

Evaluated once per turn in the order below. Earlier rules win; a planet or ship
claimed by one rule is not reconsidered by a later one.

### 4.0 Sustain, keep colonies alive

**S-01, Famine: reassign to farmers**
```
WHEN   foodDelta[P] < 0  AND  Planet:112[P] < 2 turns of deficit
THEN   for each citizen in Planet:144[P] whose +0 is not 0, cheapest role first
       (banker 6 → scientist 2 → miner 5 → worker 1), set +0 = 0 (farmer)
       until foodDelta[P] >= 0 is projected
LIMIT  reassign at most ceil(pop/4) citizens per turn, the delta is only
       re-measurable next turn, so over-correcting is not recoverable this turn
USES   A
```

**S-02, Famine: build a farm**, IMPLEMENTED, folded into R-XPL-02 (`exploit.run_facilities`)
```
WHEN   foodDelta[P] < 0  AND  farm count in Planet:204[P] < ceil(pop / 4)
THEN   Planet:284[P] = 0 (farm); ensure Planet:344[P] == 1
USES   B, C
```

**S-03, Surplus: release farmers**
```
WHEN   foodDelta[P] > 0  AND  Planet:112[P] >= 0.9 * Planet:116[P]
THEN   reassign farmers to the role the current phase wants (§4.3 R-XPL-01)
LIMIT  never below the farmer count that made foodDelta >= 0
USES   A
```

**S-04, Insolvency**, COVERED by R-XPL-03's stopgap (§4.3, Cash policy), not by bankers.
A player covers a short treasury with wealth mode until banking centers come online; bankers
are for a population that is already thriving. The original specification, kept for the
record:
```
WHEN   incomeRate < 0  AND  Owner:8 < 3 turns of burn
THEN   reassign workers/scientists to banker (6) on the highest-pop planet;
       suspend all rules that add ship upkeep (ShipDesign:48) this turn
USES   A
```

### 4.1 Explore

**R-EXP-01, Keep dedicated scouts in the field**
```
WHEN   count(our ships of role SCOUT) < SCOUT_TARGET   (initial: 3), not counting
       a scout slower than SCOUT_OBSOLETE (0.5) of the fastest scout design we can build
THEN   queue the FASTEST scout design (max ShipDesign:36) at a free shipyard
USES   D
```

**R-EXP-02, Send idle scouts to the nearest unvisited system**
```
WHEN   ship S has role SCOUT and Ship:52 == 0
THEN   pick the Sun with the smallest distance from Ship:4/8/12 that is not in
       visitedSystems; write Ship:52 = 2 and the Sun's Sun:4/8/12 into
       Ship:48 +16/20/24; set Ship:76 = 1
NOTE   scout targets a SUN, not a planet, this is the one order that does
USES   E
```

**R-EXP-03, Record arrivals**, SUBSUMED by `sensors.History._discover`
```
WHEN   ship S had order 2 last turn and Ship:52 == 0 now
THEN   add that Sun to visitedSystems; add its planets to the known set
USES   none (read-only)
```
There is no `run_exp03`: `_discover` runs every pass and adds any sun a ship is currently
inside (within 55.0), which covers arrival without needing to watch for the order clearing.

**R-EXP-04, Recall damaged scouts**, IMPLEMENTED as part of R-XTM-05 (§4.4)
```
WHEN   Ship:136 < SCOUT_RETREAT_AT (0.5) on a SCOUT
THEN   as R-XTM-05: withdraw to the nearest shipyard and hold there until repaired
USES   M
```
One rule, `exterminate.run_xtm05`, with the threshold chosen by role
(`exterminate.retreat_threshold`). The caveat on R-XTM-05 applies: a ship that dies inside
one turn boundary is never observed damaged, so this only helps against attrition, not
against being caught.

### 4.2 Expand

**R-XPN-01, Keep a colony ship available**, IMPLEMENTED in `expand.py`
```
WHEN   count(our COLONY ships THAT CAN SETTLE) < COLONY_SHIP_TARGET
AND    there is at least one known colonisable target (R-XPN-02)
THEN   queue the COLONY design at the best shipyard planet
USES   D
```

**R-XPN-02, Score and pick a colony target**, IMPLEMENTED in `expand.py`
```
targets = visible planets where Planet:40 resolves to the null node 0x00857C54
score(T) = (Planet:104[T] >> 16)                    # space
         - TURN_WEIGHT * ceil(dist(ship, T) / ShipDesign:36)
         + SAME_SYSTEM_BONUS if T shares a Sun with an owned planet
THEN   retarget the idle COLONY ship's order to Planet:4/8/12 of argmax score,
       Ship:52 = 3
USES   E   (and E′ until a first order exists)
```

**R-XPN-03, New colony bootstrap**, IMPLEMENTED in `expand.py`
```
WHEN   Planet:92[P] == currentTurn (colonised this turn)
THEN   set every citizen's +0 to 0 (farmer); Planet:284[P] = 0 (farm)
USES   A, B
```
No governor is assigned, the AI does all planet management itself, so `Planet:496` is
left unset everywhere and actuator H is not used.

**R-XPN-04, Do not over-expand**, IMPLEMENTED as `exterminate.war_yard`
```
WHEN   R-XTM-01 wants another warship (exterminate.fleet_wants_ships)
THEN   the staging shipyard is kept for warships: R-XPN-01 colony ships,
       R-EXP-01 scouts and R-XPL-02 facilities use the other yards
```
R-XTM-01 builds at the staging yard whenever it is free, so new warships start where the
fleet gathers. Light turrets are not built on shipyard planets at all: a turret occupies the
yard for many turns, and the fleet defends the yard.

### 4.3 Exploit

**R-XPL-01, Job mix by phase**

The target job distribution on a planet, applied after Sustain has taken its farmers:

| Phase | Trigger | Remaining citizens go to |
|---|---|---|
| Growth | turn < 50, or colonies < 3 | worker 1 (60%), scientist 2 (40%) |
| Economy | income-limited (S-04 fired recently) | banker 6 (50%), worker 1 (50%) |
| War | any rival at war, or R-XTM-01 armed | worker 1 (70%), the rest as Growth; miners in every phase only where R-XPL-11 wants them |

```
WHEN   a planet's actual job histogram differs from the phase target by > 1 citizen
THEN   reassign the smallest number of citizens to close the gap
LIMIT  ceil(pop/4) reassignments per planet per turn
USES   A
```
The scientist share is held under the science wastage ceiling, §2.5: above about 7,600
total output it steps down and the surplus scientists become workers.

**R-XPL-02, Facility build order**
```
priority, first match among types research has unlocked (`exploit.buildable_types`):
  defence ag. 8   if the civ has none, R-XTM-10 wants a scan type, and this is
                  its planet without a shipyard that has the most workers;
                  ahead of farms, which a big planet nearly always wants
  farm 0          if there is none, the store is under 25%, or the surplus is
                  below GROWTH_MARGIN_PER_POP per citizen
                  (never past MAX_FARMS, 5)
  military camp 6 if there is none and pop >= 6
  light turret 9  if turrets < BORDER_TURRETS and a visible rival planet is
                  within BORDER_DIST
  shipyard 2      if there is none here, the civ has < MAX_SHIPYARDS, and
                  pop >= SHIPYARD_POP
  factory 1       if there is none and workers >= FACTORY_WORKERS
  university 4    if there is none and pop >= 8
  banking ctr 16  if there is none and pop >= BANK_POP
  mine 13         if there is none and miners >= MINE_MINERS (R-XPL-11)
THEN   Planet:284[P] = that id, Planet:344[P] = 1
USES   B, C
```
`exploit.wanted_facility` is the authority. One defence agency is built, for scans. Factory and
banking center were added with the cash policy: "scaled correctly, there is always something
worth building", and a planet that had built a farm, camp and university otherwise sat idle.

**R-XPL-10, Buy what a ship lacks**, IMPLEMENTED as `exploit.provision`, called by every ship-queueing rule through `exploit.queue_ship`
```
WHEN   a rule is about to queue design D and Owner:1128 holds less of some
       resource than D's cost (ShipDesign:92 + 4 * resId, getter 0x00548990)
THEN   buy each shortfall, rounded up to the UI's 100-unit lot, with actuator O,
       if the engine's quotes fit in cash above cash.reserve(); else skip
USES   O, then D
```
A ship's cost is taken once, at queue time, by the engine (actuator D). Prices are one market
shared by every civ: each 100 bought raises that resource's price by 1 per lot for everyone,
permanently (only a net-sold position decays, by 1,000 units a turn), and selling lowers it
toward a third of base. Facilities and scans cost no resources.

**R-XPL-11, Mine what is short**, IMPLEMENTED as `exploit.mining_plan`, feeding R-XPL-01's targets
```
FOR    each resource our costliest legal design uses,
       target = MINE_STOCK_SHIPS x that design's cost
WHEN   stock < target
THEN   miners on our planets of that resource, best yield first, until they
       refill the gap within MINE_REFILL_TURNS; at most MINER_SHARE of a
       planet's non-farmers, and never below WORKER_FLOOR workers
WHEN   target <= stock < MINE_RELEASE x target:  planets keep their miners
WHEN   above that:                               miners go back to work
AND    R-XPL-02 builds a mine (13) where MINE_MINERS or more work
USES   A, then B/C for the mine
```
Only miners produce resources (plus a flat trait-41 metal bonus); a planet has exactly one
deposit, and a resource with no planet of ours is bought (R-XPL-10). One miner on a base-10
metal world makes 10 a turn, more than a banker's income at the market's buy price.

**R-XPL-03, Wealth mode**, IMPLEMENTED in `exploit.run_wealth`
```
WHEN   a planet is idle and R-XPL-02 has nothing worth building there
THEN   generate wealth (Planet:296 -> 0x0080B540, Planet:344 = 0)
WHEN   cash.short(): income < 0 and cash + SHORT_HORIZON turns of it is inside the reserve
THEN   switch ONE more planet a turn: the most workers among those building a
       facility that is not yet half done
KEEP   a planet in wealth while income is short, or while it has nothing to build;
       above WEALTH_RELEASE_INCOME, release one a turn to R-XPL-02
USES   C
```
Runs after the ship rules and before R-XPL-02. A ship rule may take a wealth planet, since
`Planet:344` is 0 there, and military production outranks banking. Release is one a turn
because income that is positive only because of wealth would otherwise release every planet
at once and go straight back negative.

**R-XPL-04, Always be researching**
```
WHEN   Owner:144 == -1
THEN   pick the cheapest uncompleted item (id not in Owner:108) whose effect
       matches the phase; write it to BOTH Owner:144 and Owner:152
NOTE   the stockpile accrues regardless, so leaving this at -1 is pure waste
USES   F
```

**R-XPL-05, Recruitment**
```
WHEN   a ship parked at P needs crew and P has a military camp
THEN   raise the rate toward max_recruitment(P) if P is comfortably fed,
       otherwise to RECRUIT_RATE_LEAN so growth is not starved
WHEN   nothing at P needs crew
THEN   lower it to 0, the diversion costs food and buys nothing
USES   G
```

**R-XPL-06, Load crew onto a waiting ship**, IMPLEMENTED in `exploit.run_crew_load`
```
WHEN   a ship in orbit of owned planet P has fewer crew than ShipDesign:76
       AND P has that many trained military units stationed
THEN   move the units from P onto the ship
USES   load_crew
```

**R-XPL-08, Hurry a build with surplus cash**, IMPLEMENTED in `exploit.run_hurry`
```
WHEN   a build is >= 50% done (the engine's own limit), Planet:300 == 0, income >= 0
THEN   call HurryProduction by tier (military at war, expansion, other), cheapest
       first within a tier, spending everything above cash.reserve()
USES   K
```

**R-XPL-09, Conscript crew for a grounded ship**, IMPLEMENTED in
`exploit.run_conscript`, and it does the migration by FIELD WRITE rather than by
calling `ChangeCitizenJobs`. See §7 for why, and for the wrong diagnosis it took
to get there.
```
WHEN   a ship is BUILT, short of ShipDesign:76 crew, in orbit of our planet P,
       P has fewer trained units than it needs, and cash above cash.reserve();
       a warship or troop hull only while the civ is at war
THEN   draft ONE citizen on P (cheapest role first, never a farmer)
USES   N
```

**R-XPL-07, Liquidate to survive a collapse**, IMPLEMENTED in `exploit.run_liquidate`
```
WHEN   incomeRate < 0 for LIQUIDATE_AFTER (3) turns
       AND cash + RECOVERY_HORIZON (5) turns of it would be below 0
       AND the last sale's effect has been read (one turn)
THEN   sell facilities in SELL_ORDER until the upkeep shed covers the deficit,
       each type from the planet holding most of it
KEEP   farms, shipyards and banking centers are never sold; defence only outside
       a war; a type sold on a planet is not rebuilt there (R-XPL-02) until income
       is positive and cash is back above the reserve
USES   J
```

### 4.4 Exterminate

**R-XTM-00, Declare war first**, IMPLEMENTED in `exterminate.run_xtm00`, and it is a hard
precondition. Confirmed live: the relation went Neutral → War in *both* civs' records and
R-XTM-03 then engaged. Gated behind `ALLOW_DECLARE_WAR`, which is on for AI-vs-AI galaxies.

```
WHEN   at least WARSHIP_TARGET crewed warships are at the staging planet (R-XTM-08)
AND    a planet of civ C is in our discovery set
AND    relation code for C in Owner:248 != 1 (War)
THEN   declare war on C
USES   actions.declare_war
```

**R-XTM-01, Maintain a standing fleet**
```
WHEN   in contact or at war, and fewer than WARSHIP_TARGET warships (built or
       queued) or no anti-planet or no anti-ship class among them
THEN   queue the missing class
WHEN   the fleet beats no known defended target (R-XTM-03's margin) and is
       below FLEET_CAP
THEN   queue the design that most raises the margin against the easiest one
USES   D
```
Design stats are read from the design, and from the component tables (`components.py`)
while a design's own stat block is still uncomputed, which it is until a ship of it is built.

**R-XTM-02, Defend**, IMPLEMENTED in `exterminate.run_xtm02`
```
WHEN   at war, and enemy ships or fleets are in scan range of our planets or ships,
       grouped by the owned planet nearest each, and a group is a fleet, two or
       more ships, or any hull above shuttle class
WEIGH  with a military scan in stock and none of the group from the last
       MILITARY_FRESH turns, scan each enemy stack in it (one scan per stack);
       weigh the defenders against the scans' exact firepower and hitpoints;
       with no military scan, fleet-scan a fleet in the group and price each
       hull class it lists as our own strongest anti-ship design of that hull
       (pessimistic: 80 incoming against a true 40 for a b1 + f1); else the
       newest battle report of that civ's ships
THEN   hold idle warships already at that planet, and send idle or staged
       warships that can reach it within DEFEND_REACH turns, unless the
       defenders' margin is below DEFEND_MARGIN
USES   P, M
```
A military report (`intel.military_scans`) has one 124-byte entry per ship: effective
hitpoints +0x58, condition +0x60, firepower light/heavy/bomb +0x68/+0x6C/+0x70, and a hull
text at +0x34 that names the chassis (0-2 light, 3-5 heavy). Read live against an `f1`
(42 hp, 35/10/0) and a `b1` (80 hp, 0/0/500), both exact. It takes Fleets as well as Ships.

| id | Scan | Cost | Target | Reveals | Unlocked by |
|---|---|---|---|---|---|
| 0 | Planetary | 9,000 | enemy Planet | a copy of its PlanetProperties: citizens, stationed military, facilities, production | Advanced Scanning (#24) |
| 1 | System | 20,000 | Sun | 8 turns of vision | Deep-Space Scanning (#21) |
| 2 | Route | 3,500 | Ship/Fleet | its route | Basic Scanning (#23) |
| 3 | Fleet | 5,000 | Fleet | ship count and per-class counts | Basic Scanning (#23) |
| 4 | Military | 16,000 | Ship/Fleet | weapons, shields, units, speed, rank, condition (as text) | Advanced Tactics (#20) |
| 5-7 | Technology, Energy grid, Leader name | 10,000-20,000 | Planet | civ-level | #24, #19, #79 |

**R-XTM-03, Raid**, IMPLEMENTED in `exterminate.run_xtm03`
```
WHEN   at war, and at least WARSHIP_TARGET crewed warships are at the staging planet
THEN   for each visible enemy planet with a known defence (a battle report),
       margin = (time for the defence to clear our fleet) / (time for us to clear it)
       send every staged warship at the nearest planet with margin >= STRENGTH_MARGIN
HOLD   warships at an enemy planet that fought there this turn or last stay
USES   M
```
The margin follows the manual's battle rules: bombs are the only damage to planetary defence,
light and heavy firepower hit ships at half against the other class, and our hulls are light
classes, so enemy heavy firepower hits them at half. A planet with nothing on it produces no
battle, so only defended targets are raided; an undefended one is R-XTM-04's to take.

**R-XTM-09, Probe**, IMPLEMENTED in `exterminate.run_xtm09`
```
WHEN   at war, a visible enemy planet has no fresh intel, and fewer than
       PROBES_IN_FLIGHT scouts are heading to one
THEN   send the nearest free SCOUT hull to it with M
NOTE   a probe in flight is claimed each pass so R-EXP-02 does not retask it
USES   M
```
Its battle report is the intel. If nothing fires on it, the planet is quiet.

**R-XTM-08, Rally idle warships**, IMPLEMENTED in `exterminate.run_xtm08`
```
WHEN   a crewed warship is not claimed by an earlier rule, has order 0, 1 or 2,
       and is not at or heading to the staging planet
THEN   order it to move (1) to the staging planet
STAGE  the shipyard planet nearest any rival planet in our discovery set;
       before contact, the most populous shipyard planet
USES   M
```
Staging is a shipyard because warships are built there and repaired there (8% of the damage
a turn for heavy hulls, 15% for light, per the manual). A frontier colony far from the yards
keeps the fleet in transit: a `b1` makes 3.4 a turn, so 350 units is a hundred turns.
Runs after R-XTM-03, so ships committed to a raid are already claimed, and after R-XTM-05,
so a withdrawing ship is never redirected.

**R-XTM-04, Conquer**, IMPLEMENTED in `exterminate.run_xtm04`, and it has taken a planet
```
WHEN   a TROOP ship carries troops beyond its flight crew, at war, and some enemy
       planet is undefended and unguarded (guarded_planets)
SCAN   with a planetary scan in stock, scan the nearest candidate that has no
       report from the last SCAN_FRESH turns (one scan a pass)
SKIP   a candidate whose newest report, of any age, shows a garrison >= the
       troops aboard, or planetary-defence facilities (9, 10, 11, 19); a
       garrison does not shrink without a fight
THEN   conquer (Ship:52 = 5) the nearest candidate left; with no report, as before
USES   P, M
```
A planetary report (`intel.planetary_scans`) is a copy of the target's PlanetProperties, so
its stationed military is exact as of the scan. BadGuy's HQ read 46.

**R-XTM-10, Keep scans in stock**, IMPLEMENTED in `exterminate.run_xtm10`
```
WHEN   in contact, and research has unlocked a type in SCAN_STOCK (planetary 0,
       fleet 3, military 4) that the civ holds fewer than one of
THEN   an idle planet with a Central Defence Agency produces it
USES   P
```

**R-XTM-06, Garrison a planet just taken**, IMPLEMENTED in `exterminate.run_xtm06`
```
WHEN   a TROOP ship is in orbit of a planet we took within GARRISON_WINDOW turns
       (Planet:92 is rewritten on CAPTURE, not only on founding)
THEN   unload everything above ShipDesign:76 onto it, keeping enough to fly home
USES   N′ (actions.unload_crew)
```
The capture lands every unit above `ShipDesign:76` and they stay as the garrison
(§4.3, R-XPL-09). One alone is not enough: planet #41 was taken with a single unit and
was back in enemy hands five turns later, which is why a troop hull carries
`INVASION_TROOPS` (3). So this rule only finds work for a second hull reaching a planet
the first has taken.

**R-XTM-07, Keep a troop transport**, IMPLEMENTED in `exterminate.run_xtm07`
```
WHEN   at war, some visible enemy planet has weakness 0, and we hold fewer than
       TROOP_TARGET troop hulls, and none is already built and awaiting crew
THEN   queue the civ's TROOP design at the largest free shipyard
USES   D
```
R-XTM-04 can only spend hulls somebody built, and R-XTM-01 queues warships only,
the first two troop ships in this project were made by hand.

**R-XTM-05, Retreat**, IMPLEMENTED in `exterminate.run_xtm05`, and covers R-EXP-04
```
WHEN   Ship:136 < RETREAT_AT (0.3), or SCOUT_RETREAT_AT (0.5) for a scout
THEN   send it with M to the nearest owned shipyard planet (any owned planet when
       there is none); ships are deleted on death, not marked, so a ship that
       vanishes from the scan was destroyed
HOLD   a ship at that planet below REPAIRED_AT (0.9) is claimed every pass, so
       nothing sends it out again until the yard has repaired it
USES   M
```
The hold is what makes the withdrawal mean anything: without it a ship repaired to 0.31
was free for R-XTM-03 the next pass. It also holds a ship that was never withdrawn, e.g.
a warship staged at the yard at 0.85, for the turn or two its repair takes.

---

## 5. Constants to tune

Generated by `constants.py --write`; edit the code, not this table.

<!-- constants:begin -->
| Name | Value | Module | Meaning |
|---|---|---|---|
| `TORN_FRACTION` | 0.5 | `ai.py` | A snapshot taken during turn resolution can show part of an empire. A civ read below this fraction of last turn's planet count is re-read. |
| `RESERVE_TURNS` | 10 | `cash.py` | turns of upkeep never spent on anything discretionary |
| `MIN_RESERVE` | 1000 | `cash.py` | the reserve is never smaller than this |
| `UNIT_UPKEEP` | 3 | `cash.py` | Upkeep of one stationed military unit, the Planet:168 record's +0, which read 3 on every unit the memory report observed. Crew aboard a ship are left to the ship's own ShipDesign:48. |
| `SHORT_HORIZON` | 5 | `cash.py` | The treasury is short when income is negative and this many turns of it would take cash inside the reserve. R-XPL-03 and R-XPL-01 both act on it. |
| `SURPLUS_AT` | 0.50 | `exploit.py` | Fraction of food capacity a colony must already hold before a farmer is released. |
| `GROWTH_MARGIN_PER_POP` | 10.0 | `exploit.py` | Food gained per turn, per citizen, that must remain after releasing a farmer. Growth is fed by the surplus, so a merely positive delta stops it. |
| `MIN_FARMERS` | 1 | `exploit.py` | Never drop below this many farmers, whatever the food reading says. |
| `SCIENTIST_SHARE` | 0.5 | `exploit.py` | of non-farmers, in the Growth phase |
| `REASSIGN_CAP_DIV` | 4 | `exploit.py` | at most ceil(pop / 4) job changes per planet per turn |
| `BANKER_POP_SHARE` | 0.75 | `exploit.py` | of its population cap a planet must hold to take bankers |
| `SCIENCE_MARGIN_FLOOR` | 0.75 | `exploit.py` | A scientist is kept while one more point of science output still banks at least this much of a point after wastage. 0.75 is where a scientist stops out-producing a worker at the default homeworld rates, 40 science to 30 production, and is reached at about 7,600 output, 11% wastage. |
| `SCIENCE_STEP` | 0.05 | `exploit.py` | how fast the scientist share may move per turn |
| `SCIENCE_RELEASE` | 0.8 | `exploit.py` | of the ceiling; below it the share climbs back |
| `CASH_THIN` | 1000 | `exploit.py` | a reserve this small deserves a decent income behind it |
| `THIN_INCOME` | 20 | `exploit.py` | ...and "decent" means at least this per turn |
| `WORKER_FLOOR` | 2 | `exploit.py` | never strip a planet below this many workers |
| `MINE_STOCK_SHIPS` | 2 | `exploit.py` | keep each resource stocked for this many of our costliest designs |
| `MINE_REFILL_TURNS` | 10 | `exploit.py` | miners enough to refill the gap in this many turns |
| `MINE_RELEASE` | 1.5 | `exploit.py` | above this multiple of the target, miners go back to work |
| `MINER_SHARE` | 0.5 | `exploit.py` | of a planet's non-farmers, at most |
| `MINE_MINERS` | 3 | `exploit.py` | miners a mine is worth building for |
| `CREW_PER_COLONY_SHIP` | 2 | `exploit.py` | fallback only; see min_crew() below |
| `INVASION_TROOPS` | 3 | `exploit.py` | Crew above ShipDesign:76 a troop hull carries. A capture lands every one of them and they stay as the garrison: 3 aboard over a flight crew of 2 landed 1, and 5 aboard landed 3. One alone was retaken within five turns. |
| `RECRUIT_RATE_LEAN` | 20 | `exploit.py` | Recruiting flat out starves growth, so a planet that is not comfortably fed trains at this slower rate instead of the maximum. |
| `FARM_PER_POP` | 4 | `exploit.py` | citizens per farm before another is wanted |
| `MAX_FARMS` | 5 | `exploit.py` | farms return less each; players stop around five |
| `SHIPYARD_POP` | 10 | `exploit.py` | population a second shipyard is worth building at |
| `FACTORY_WORKERS` | 4 | `exploit.py` | workers a factory is worth building for |
| `BANK_POP` | 10 | `exploit.py` | population a banking center is worth building at |
| `MAX_SHIPYARDS` | 2 | `exploit.py` | shipyards a civ builds at most |
| `BORDER_DIST` | 120.0 | `exploit.py` | a planet this close to a visible rival planet is a border planet |
| `BORDER_TURRETS` | 2 | `exploit.py` | light turrets wanted on a border planet |
| `LIQUIDATE_AFTER` | 3 | `exploit.py` | turns of negative income before anything is sold |
| `RECOVERY_HORIZON` | 5 | `exploit.py` | and only when this many more turns of it would take cash below 0 |
| `TURN_WEIGHT` | 10.0 | `expand.py` | score points charged per turn of travel |
| `SAME_SYSTEM_BONUS` | 30.0 | `expand.py` | shared defence, no exposed transit |
| `COLONY_SHIP_TARGET` | 1 | `expand.py` | colony ships to keep in existence |
| `SYSTEM_RADIUS` | 55.0 | `explore.py` | A ship is "in" a system within this radius. Widest orbit is 39.6 and the tightest gap between two stars is 111.7. |
| `ENOUGH_KNOWN_TARGETS` | 3 | `explore.py` | Colony ships stop scouting once this many free planets are known. |
| `SCOUT_TARGET` | 3 | `explore.py` | Dedicated scouts to keep in the field. Cheap hulls, so several is normal. |
| `SCOUT_OBSOLETE` | 0.5 | `explore.py` | A scout slower than this fraction of the fastest scout design we can build no longer counts toward SCOUT_TARGET, so a faster engine gets built and flown. |
| `WARSHIP_TARGET` | 2 | `exterminate.py` | staged warships before declaring war or raiding |
| `FLEET_CAP` | 8 | `exterminate.py` | warships R-XTM-01 builds toward a target it cannot beat |
| `STRENGTH_MARGIN` | 1.5 | `exterminate.py` | a target must take this many times longer to clear us |
| `DEFEND_MARGIN` | 1.0 | `exterminate.py` | defenders must clear the enemy's last-seen fleet this much faster |
| `PROBES_IN_FLIGHT` | 1 | `exterminate.py` | unarmed hulls sent for a battle report at once |
| `PLANET_SCAN_RANGE` | 30.0 | `exterminate.py` | a planet sees other civs' ships this close (stock client) |
| `SHIP_SCAN_RANGE` | 30.0 | `exterminate.py` | a ship whose scanner range cannot be read |
| `DEFEND_REACH` | 12 | `exterminate.py` | turns; defenders farther out than this stay home |
| `ARRIVE_WITHIN` | 0.8 | `exterminate.py` | a ship this close to its target has arrived (battle bucket) |
| `TROOP_TARGET` | 1 | `exterminate.py` | troop hulls to keep while anything is takeable |
| `GARRISON_WINDOW` | 3 | `exterminate.py` | turns after a capture (Planet:92) that count as newly taken |
| `RETREAT_AT` | 0.30 | `exterminate.py` | Ship:136 below this and the ship goes home |
| `SCOUT_RETREAT_AT` | 0.50 | `exterminate.py` | a scout is worth more alive than at its target |
| `REPAIRED_AT` | 0.90 | `exterminate.py` | a withdrawn ship stays at the shipyard until this |
| `AT_PLANET` | 1.0 | `exterminate.py` | a ship within this distance of a planet is at it |
| `GUARD_MEMORY` | 10 | `exterminate.py` | turns a guard seen orbiting a planet keeps it off the troop target list |
| `MILITARY_SCAN` | 4 | `exterminate.py` |  |
| `MILITARY_FRESH` | 3 | `exterminate.py` | turns a military scan of a moving stack is current |
| `FLEET_SCAN` | 3 | `exterminate.py` |  |
| `PLANETARY_SCAN` | 0 | `exterminate.py` |  |
| `SCAN_FRESH` | 20 | `exterminate.py` | turns before a scanned target is scanned again |
| `AT_PLANET` | 1.0 | `intel.py` | a report this close to a planet is about it |
| `MAX_AGE` | 40 | `intel.py` | turns; older intel is treated as unknown |
| `SCAN_REPORTS` | 320 | `intel.py` | Scan reports, Owner:320/324: pointers to report objects. Every report has +4 type, +0xC turn, +0x10..0x18 target position. A planetary scan (type 0) holds a copy of the target's PlanetProperties at +0x28, so the target's Planet:N reads at report + 0x28 + (N - 88). |
| `PLANETARY` | 0 | `intel.py` |  |
| `MILITARY` | 4 | `intel.py` | A military scan (type 4) holds one 124-byte entry per ship in the stack: +0x08 a stats text, +0x34 a hull text naming the chassis, +0x50 base and +0x58 effective hitpoints, +0x60 condition (float), +0x68/+0x6C/+0x70 firepower light / heavy / bomb. Matched against an f1's own stat block. |
| `MILITARY_ENTRY` | 124 | `intel.py` |  |
| `HEAVY_CHASSIS` | 3 | `intel.py` | chassis 0-2 are light hulls, 3-5 heavy |
| `FLEET` | 3 | `intel.py` | A fleet scan (type 3) holds one 52-byte entry per fleet: +0x04 ship count, +0x18 a text listing each hull class, e.g. "(with 1 x <ShipShuttleSmall> Shuttle-Class, 1 x <ShipCorvetteSmall>Corvette-Class Ships)": only the last class carries "Ships". The class counts are read from that text; +0x08 holds the same counts as four uint16s by chassis id 0-3 (1, 1, 0, 0 for that fleet). |
| `FLEET_ENTRY` | 52 | `intel.py` |  |
<!-- constants:end -->

## 7. Engine calls vs. field writes

### Ranked candidates

| Candidate | Address | Why it matters |
|---|---|---|
| ~~**Read the live research table**~~ | `0x0054AA70` id→record, `0x0054B140` cost, `0x0054B200` prereq-at-index, `0x0054B7D0` exclusion test | **DONE, without an engine call.** `research_dump.py` reads the table the startup builder writes at `0x00857F08` straight from memory, and `research.py` is regenerated from it (`--emit`, `--diff`), so the ≥688 table is gone. These four calls are no longer needed |
| ~~**`Ship::SetCommand`**~~ | `0x004D9DC0` | **DONE; the crash was our stub, fixed and verified live** (§4.4). Writing `Ship:52` alone is fine: with `Ship:56` null the ship follows its route. The order-type-8 / `Ship:61 = 0x0B` observation is explained: type 8 is Create Wormhole and `Ship:61` its 11-turn countdown (§2.3). Not worth an actuator: it needs the Wormhole Generator module (30,000 and 1,000 exotics), consumes the ship, and what a wormhole does is not yet known |
| ~~**`ChangeCitizenJobs`**~~ | `0x00574180` | **DONE, actuator N.** Conscription works and R-XPL-09 uses it. Note it is still *not* used to replace actuator A: setting an ordinary job id by field write remains cheaper than a remote thread, and only job 3 goes through the engine, because only job 3 costs money |
| ~~**`GetTrait`**~~ | `0x00516180` | **DONE, without an engine call.** `research.trait` computes it from memory and matched the engine on all 46 traits with distinct probe values written to the base block; `remote.trait` is kept as the check |
| ~~**available space**~~ | `0x004F1140` (PP vftable slot 6) | **Understood.** It returns FREE space, `space + round(space × trait38 / 100) - Planet:108 low word`, and is the only reader of trait 38. `Planet.total_space` reproduces the first two terms |
| ~~**`GetDraftCost`**~~ | `0x00516060` | **DONE.** `remote.draft_cost`. Validated read-only before the mutating call, per the rule below: six readings across two civs at three counts, every one matching `sum(5*(T+k)+105)` computed independently from a snapshot. That match is also what confirmed `T` counts ship crew as well as stationed units |

