# AI Player — Strategy Specification

An external process that plays the local player's civ by reading and writing the
running client's memory, using only fields a human could have changed by clicking.
This document is the **rulebook**: what the AI senses, what it may touch, and the
if/then rules for each X of 4X. It is written in the vocabulary of
`ejbo_annotations.json` (`Class:offset`) so every rule is traceable to a confirmed
field or explicitly marked as blocked.

**Built and running:** the turn loop (`ai.py`), sensing (`gamestate.py`), the actuators for
jobs, research, ship orders and ship production (`actions.py`, `remote.py`), and the whole
Expand cycle — R-XPN-01 build, R-XPN-02 dispatch, R-XPN-03 bootstrap (`expand.py`), plus
`survey.py` and `build.py`. Verified over a full game: the AI founded colonies on turns 4 and
5 unattended and led the rival 3730 to 1666 on score by turn 12.

**Still specification:** Sustain (§4.0), Explore (§4.1), Exploit (§4.3) and Exterminate (§4.4).

**Open questions about the game itself belong in
`CosmicSupremacy_Reconstruction_Report.md` as `[ ]` items**, not here — this document
holds decisions about how the AI should behave. Where a rule is blocked on an unknown,
it cites the report item rather than restating it.

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

**Identity.** "Our civ" is the `Owner` whose name at `Owner:-44` matches a configured
string. Remember `Owner` is multiple-inheritance: the allocation starts at **tag-52**,
reference nodes store **tag-8**, and the known-players vector stores **tag-52**. Any
owner comparison must accept both forms or it will silently match nothing.

### 1.1 The no-cheating rule

The AI writes only fields that a UI action could have written, to values the UI could
have produced. Concretely, three constraints, in order of how easy they are to violate
by accident:

1. **No free resources.** Never write `Owner:8` (cash), `Owner:12` (research
   stockpile), `Owner:1128` (resource stocks), `Planet:112` (food), `Planet:120`
   (construction progress), or a facility count in the `Planet:204` map. These are
   outputs. `set_facility.py` and `set_population.py` exist and work — they are
   *diagnostics*, not actuators, and the AI must not call them.
2. **No illegal values.** A citizen job must be a real job id; a selected building
   must be a type the civ has unlocked (`Owner:172`); a population must not exceed
   `Planet:104 >> 16` / 10.
3. **No omniscience.** Client memory holds all 538 planets and every `Owner`,
   including things the player has not discovered. See §6 — this is an open decision.

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
| Unlocked facility types | `Owner:172`/`176`/`180` | 8-byte `[vftable, typeId]` — what we *may* build |
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
| Space | `Planet:104 >> 16` | max pop = space / 10 |
| Food stored | `Planet:112`, cap `Planet:116` | |
| Construction progress | `Planet:120` | |
| Population | `Planet:144`/`148`/`152` | 16-byte elems; `+0` = job id, `+12` low16 = turn added |
| Population count | `(148-144)/16`, mirrored `Planet:352` low16 | |
| Stationed military | `Planet:168`/`172`/`176` | 16-byte elems; `+0` upkeep, `+12` low16 turn added |
| Built facilities | `Planet:204` map, walk to `[typeId, count]` | 0 farm, 2 shipyard, 4 university, 6 military camp, 8 defence agency, 9 light turret |
| Distinct facility types | `Planet:208` | == size of the `:204` map |
| Selected building | `Planet:284` | type id, *last selected*, not current activity |
| Current production | `Planet:296` → RTTI | `Facility` / `PilingUpWealth` (`0x0080B540`) / `Scan` / `ShipProduction` |
| Building now? | `Planet:344` | 1 = constructing, 0 = wealth |
| Recruitment rate | `Planet:100` byte 3 | percent, 0–40 without a military base ⚠ |
| Governor | `Planet:496` | node[0] = `Governor` |
| Colonised on turn | `Planet:92` | 0 is ambiguous — disambiguate with `Planet:40` |

### 2.3 Ship-level

| Quantity | Field | Notes |
|---|---|---|
| Owner | `Ship:40` | writable, but out of bounds for us |
| Position | `Ship:4/8/12` | |
| In orbit of | `Ship:44` | stored, not derived from position |
| Order object | `Ship:48` | plain struct; `+16/20/24` = destination XYZ |
| Order type | `Ship:52` | 0 none, 1 move, 2 scout, 3 colonize, 4 attack, 5 conquer, 7 move-near |
| Pending-orders flag | `Ship:76` | 1 on local civ's ordered ships ⚠ |
| Design | `Ship:108` | node[0] = `ShipDesign` |
| Condition | `Ship:136` | 0.0–1.0; the only per-ship damage state |

### 2.4 Design-level (`ShipDesign`) — used to classify ships by role

| Quantity | Field |
|---|---|
| Name / len | `ShipDesign:8` / `:24` |
| Speed | `ShipDesign:36` |
| Build cost | `ShipDesign:44`, upkeep `:48` |
| HP base / effective | `ShipDesign:52` / `:56` |
| Firepower (3 slots) | `ShipDesign:60/64/68` |
| Shield | `ShipDesign:72` |
| Metal / radioactives cost | `ShipDesign:92` / `:100` |
| Fitted modules | `ShipDesign:224` — `[vftable, id]`, 0 colony, 1 troop bay, 2 pilot cabin |
| Fitted weapons | `ShipDesign:200` — 0 mass driver, 10 fusion bomb |
| Fitted chassis | `ShipDesign:128` — 0 shuttle, 1 corvette |

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
| `foodDelta[planet]` | Δ`Planet:112` per turn — **the** signal for the food rules |
| `productionRate[planet]` | Δ`Planet:120` per turn while `Planet:344 == 1` |
| `researchRate` | Δ`Owner:12` per turn |
| `incomeRate` | Δ`Owner:8` per turn |
| `popGrowth[planet]` | Δ population count per turn |

Deltas are only meaningful when nothing else changed that turn, so the AI records the
snapshot pair alongside the actions it took, and treats a delta as stale for one turn
after it acts on that planet.

---

## 3. Actuators — the button-click modules

One module per UI action. Grade is how confident we are it works today.

| # | Action | Mechanism | Grade |
|---|---|---|---|
| A | **Set a citizen's job** | write job id to `Planet:144` element `+0` | **READY** — plain int, no allocation, same field `set_population.py` already writes |
| B | **Select the next building** | write type id to `Planet:284` | **NEEDS TEST** — confirmed as *the* selection field, never written to. May be display-only unless paired with C |
| C | **Start constructing / switch to wealth** | wealth: point `Planet:296` at the singleton `0x0080B540` and set `Planet:344 = 0`. Build: `Planet:296` needs a live `Facility` production object | **PARTIAL** — wealth direction is a pointer we already know; the build direction needs an object we cannot allocate |
| D | **Queue a ship** | write a `ShipDesign*` (`tag-12`) to `Planet:296`, set `Planet:344` | **READY** — `actions.queue_ship`. Ship builds point at the shared design; no per-build object exists and the engine registers the finished `Ship` itself |
| D′ | **Stop building / generate wealth** | `Planet:296` → `PilingUpWealth` `0x0080B540`, clear `Planet:344` | **READY** — `actions.set_wealth_mode` |
| I | **Design a ship** | `operator new(0x118)` + default ctor `0x00546EC0`, `fit_parts`, `set_design_owner` | **PARKED** — the object is created, tagged, owned and parts fit, but it is never REGISTERED (zero `tag-12` refs vs three per UI design) and its stat block is never computed. Both are done by the design editor's save path; report `[ ]` find the design editor's save path |
| E | **Retarget an existing order** | rewrite `Ship:48+16/20/24`, the route leg, progress, then `Ship:52`/`Ship:76` | **READY** — `actions.retarget_order` |
| E′ | **Originate an order** | engine's own `operator new` + order constructor, via `remote.py` | **READY** — `actions.create_order`; see §6a Route 2 |
| E″ | **Move an order between ships** | detach donor, attach recipient | **READY** — `actions.transfer_order` |
| F | **Set research topic** | write `Owner:144` **and** `Owner:152` | **READY** — confirmed writable |
| G | **Set recruitment rate** | write byte 3 of `Planet:100` | **NEEDS TEST** — report `[ ]` write-test the recruitment rate |
| H | ~~Assign a governor~~ | — | **DROPPED** — the AI manages planets itself and leaves `Planet:496` unset |

**Consequence for sequencing:** the Explore and Expand rules that only *move* existing
ships are reachable now via E; everything that *creates* a ship waits on D. The build
rules are reachable as far as choosing what to build (B) and stopping a build (C), but
starting one may not be.

**The order object cannot be fabricated** — this was measured, not assumed. Its payload
is exactly 96 bytes (heap headers at −8 and +96 bracket it), and it owns three *separate*
engine allocations: a route vector at `+40/44/48` holding 28-byte legs
`[originXYZ, destXYZ, length]`, and two intrusive list heads at `+28` and `+52` whose
nodes point back into the object. The engine's allocator will eventually free those. A
`VirtualAllocEx` substitute is therefore a **crash**, not the harmless leak
`set_population.py` accepts for the citizen vector.

Rewriting the floats and ints *inside* an engine-created order allocates nothing, which
is what actuator E does. `length` is confirmed to be the euclidean distance between its
own leg endpoints (599.9075 vs. a computed 599.907) and is the input to
`ETA = ceil(distance / ShipDesign:36)`.

**E is validated end to end.** A colonize order was retargeted from Planet #234 to #232
by rewriting only `+16/20/24` and the single route leg, and one turn later the ship had
moved **exactly 13.5000 units — `ShipDesign:36` to four decimals — along the new
bearing**. The engine treats the leg as authoritative input, not a cache it recomputes.

**Lazy allocation is dead.** Writing `Ship:52 = 3` on a ship with `Ship:48 == 0` and
advancing a full turn left the client running, the ship stationary, and both `Ship:48`
and `Ship:76` still zero. The engine null-checks the pointer rather than dereferencing
it — the write is *safe* but *inert*.

**Route 2 solves it. SOLVED AND IN PRODUCTION — see §6a.**

**Order targets differ by type** and this matters:

| Order | `Ship:52` | Destination is |
|---|---|---|
| Move | 1 | a `Planet`'s XYZ |
| Scout | 2 | a **`Sun`'s** XYZ (a system, not a planet) |
| Colonize | 3 | a planet's XYZ — **CONFIRMED**, matched a planet to 0.0000 |
| Attack | 4 | destination unobserved; on a fleet the order lives on `Fleet:52` |
| Conquer | 5 | an **enemy-owned** planet's XYZ |

---

## 4. The rules

Evaluated once per turn in the order below. Earlier rules win; a planet or ship
claimed by one rule is not reconsidered by a later one. **Sustain runs before the four
X's** — a starving colony undoes any amount of expansion — but it is a precondition
layer, not a fifth strategy.

Each rule cites the fields it reads and the actuator it needs. `[BLOCKED: x]` marks a
rule whose actuator is not yet available.

### 4.0 Sustain — keep colonies alive

**S-01 — Famine: reassign to farmers**
```
WHEN   foodDelta[P] < 0  AND  Planet:112[P] < 2 turns of deficit
THEN   for each citizen in Planet:144[P] whose +0 is not 0, cheapest role first
       (banker 6 → scientist 2 → miner 5 → worker 1), set +0 = 0 (farmer)
       until foodDelta[P] >= 0 is projected
LIMIT  reassign at most ceil(pop/4) citizens per turn — the delta is only
       re-measurable next turn, so over-correcting is not recoverable this turn
USES   A
```

**S-02 — Famine: build a farm**
```
WHEN   foodDelta[P] < 0  AND  farm count in Planet:204[P] < ceil(pop / 4)
THEN   Planet:284[P] = 0 (farm); ensure Planet:344[P] == 1
USES   B, C   [BLOCKED: C build direction]
```

**S-03 — Surplus: release farmers**
```
WHEN   foodDelta[P] > 0  AND  Planet:112[P] >= 0.9 * Planet:116[P]
THEN   reassign farmers to the role the current phase wants (§4.3 R-XPL-01)
LIMIT  never below the farmer count that made foodDelta >= 0
USES   A
```

**S-04 — Insolvency**
```
WHEN   incomeRate < 0  AND  Owner:8 < 3 turns of burn
THEN   reassign workers/scientists to banker (6) on the highest-pop planet;
       suspend all rules that add ship upkeep (ShipDesign:48) this turn
USES   A
```

### 4.1 Explore

**R-EXP-01 — Keep a scout in the field**
```
WHEN   count(our ships of role SCOUT) < SCOUT_TARGET   (initial: 2)
THEN   queue the cheapest SCOUT design (min ShipDesign:44) at the planet with
       a shipyard (type 2 in Planet:204) and the highest productionRate
USES   D   [BLOCKED]
```

**R-EXP-02 — Send idle scouts to the nearest unvisited system**
```
WHEN   ship S has role SCOUT and Ship:52 == 0
THEN   pick the Sun with the smallest distance from Ship:4/8/12 that is not in
       visitedSystems; write Ship:52 = 2 and the Sun's Sun:4/8/12 into
       Ship:48 +16/20/24; set Ship:76 = 1
NOTE   scout targets a SUN, not a planet — this is the one order that does
USES   E
```

**R-EXP-03 — Record arrivals**
```
WHEN   ship S had order 2 last turn and Ship:52 == 0 now
THEN   add that Sun to visitedSystems; add its planets to the known set
       (this is the AI's own bookkeeping — see §6)
USES   none (read-only)
```

**R-EXP-04 — Recall damaged scouts**
```
WHEN   Ship:136 < 0.5
THEN   order 1 (move) to the nearest owned planet's XYZ
USES   E
```

### 4.2 Expand

**R-XPN-01 — Keep a colony ship available** — IMPLEMENTED in `expand.py`
```
WHEN   count(our ships of role COLONY) == 0
AND    there is at least one known colonisable target (R-XPN-02)
AND    Owner:8 >= ShipDesign:44 of the colony design
THEN   queue the COLONY design at the best shipyard planet
USES   D   [BLOCKED]
```

**R-XPN-02 — Score and pick a colony target** — IMPLEMENTED in `expand.py`
```
targets = visible planets where Planet:40 resolves to the null node 0x00857C54
score(T) = (Planet:104[T] >> 16)                    # space
         - TURN_WEIGHT * ceil(dist(ship, T) / ShipDesign:36)
         + SAME_SYSTEM_BONUS if T shares a Sun with an owned planet
THEN   retarget the idle COLONY ship's order to Planet:4/8/12 of argmax score,
       Ship:52 = 3
USES   E   (and E′ until a first order exists)
```
**Distance is charged in turns, not raw units.** The first draft charged raw distance and
was badly miscalibrated for the real galaxy: coordinates run to ~1130, the two closest
suns are 111 apart, and a colony ship moves 13.5/turn, so the nearest neighbouring system
is already an 8-turn trip while `space` only spans 156..450. Raw distance swamped space
about 4:1 and the rule degenerated to "always take the closest rock". At `TURN_WEIGHT =
10` a measured galaxy ranks home-system planets 288/260/204 at 2–3 turns while a bigger
but distant planet (space 282, 15 turns) scores 132 — close enough to compete if it is
good enough, which is the intended behaviour.

`space` is used rather than `max_pop` because `max_pop = space/10` truncates and throws
away the tie-break between, say, 288 and 281.

Colonize (`3`) is **CONFIRMED**: a real colonize order read `3` at `Ship:52` and its
`+16/20/24` matched the target planet's coordinates to 0.0000, with the nearest sun 28.2
away. The earlier inference from move and conquer was right.

**R-XPN-03 — New colony bootstrap** — IMPLEMENTED in `expand.py`
```
WHEN   Planet:92[P] == currentTurn (colonised this turn)
THEN   set every citizen's +0 to 0 (farmer); Planet:284[P] = 0 (farm)
USES   A, B
```
No governor is assigned — the AI does all planet management itself, so `Planet:496` is
left unset everywhere and actuator H is not used.

**R-XPN-04 — Do not over-expand**
```
WHEN   count(owned planets) * COLONY_UPKEEP_EST > incomeRate
THEN   suppress R-XPN-01 until income recovers
```

### 4.3 Exploit

**R-XPL-01 — Job mix by phase**

The target job distribution on a planet, applied after Sustain has taken its farmers:

| Phase | Trigger | Remaining citizens go to |
|---|---|---|
| Growth | turn < 50, or colonies < 3 | worker 1 (60%), scientist 2 (40%) |
| Economy | income-limited (S-04 fired recently) | banker 6 (50%), worker 1 (50%) |
| War | any rival at war, or R-XTM-01 armed | worker 1 (70%), miner 5 (30%) |

```
WHEN   a planet's actual job histogram differs from the phase target by > 1 citizen
THEN   reassign the smallest number of citizens to close the gap
LIMIT  ceil(pop/4) reassignments per planet per turn
USES   A
```

**R-XPL-02 — Facility build order**
```
priority, first unbuilt/under-target that is unlocked in Owner:172 wins:
  farm 0          if farms < ceil(pop / 4)
  shipyard 2      if the civ has no shipyard anywhere
  university 4    if universities < 1 and phase == Growth
  military camp 6 if phase == War and camps < 1
  light turret 9  if this planet is a border planet (nearest enemy < THREAT_DIST)
  defence agency 8 if scan types in Owner:200 is empty
THEN   Planet:284[P] = that id, Planet:344[P] = 1
USES   B, C   [BLOCKED: C build direction]
```

**R-XPL-03 — Wealth mode when there is nothing worth building**
```
WHEN   every priority in R-XPL-02 is satisfied  AND  no ship is queued
THEN   point Planet:296[P] at 0x0080B540 and set Planet:344[P] = 0
USES   C   (this direction is available — it is a known static pointer)
```

**R-XPL-04 — Always be researching**
```
WHEN   Owner:144 == -1
THEN   pick the cheapest uncompleted item (id not in Owner:108) whose effect
       matches the phase; write it to BOTH Owner:144 and Owner:152
NOTE   the stockpile accrues regardless, so leaving this at -1 is pure waste
USES   F
```
⚠ We have no tech-tree table yet: ids and costs are only learnable from `Owner:108`
records after the fact. Until one exists, this rule picks by id order and records
`[id, cost]` pairs to build the table empirically.

**R-XPL-05 — Recruitment**
```
WHEN   phase == War  AND  Planet:100[P] byte 3 < 40
THEN   raise it toward 40
WHEN   phase != War  AND  foodDelta[P] < 0
THEN   lower it toward 0 (military costs food — Owner:880)
USES   G   [NEEDS TEST]
```

### 4.4 Exterminate

**R-XTM-01 — Maintain a standing fleet**
```
WHEN   sum(our WARSHIP count) < threatLevel
       where threatLevel = count of known enemy warships within THREAT_DIST
                           of any owned planet, floored at 2 once contact is made
THEN   queue the best WARSHIP design affordable in Owner:8 and Owner:1128
       (rank by max(:60,:64,:68) / :44, filter by metal :92 and radioactives :100
       against stock)
USES   D   [BLOCKED]
```

**R-XTM-02 — Defend**
```
WHEN   an enemy ship is within THREAT_DIST of owned planet P
THEN   order every idle WARSHIP within reach to move (1) to P's XYZ;
       if already there and the enemy is adjacent, order attack (4)
USES   E   ⚠ attack order semantics unobserved
```

**R-XTM-03 — Raid**
```
WHEN   our WARSHIP count >= threatLevel + RAID_MARGIN
THEN   pick the weakest known enemy planet
       weakness = stationed military count (Planet:168 span / 16)
                + turret count from Planet:204 type 9
       order the raid group to move (1) then attack (4)
USES   E
```

**R-XTM-04 — Conquer**
```
WHEN   a TROOP ship exists and its target planet's weakness == 0
THEN   Ship:52 = 5 (conquer), destination = that planet's Planet:4/8/12
NOTE   conquer is confirmed to target an enemy-owned planet's exact XYZ
USES   E
```

**R-XTM-05 — Retreat**
```
WHEN   Ship:136 < 0.3
THEN   order 1 (move) to the nearest owned planet; ships are deleted on death,
       not marked, so a ship that vanishes from the scan was destroyed
USES   E
```

---

## 5. Constants to tune

| Name | Initial | Meaning |
|---|---|---|
| `SCOUT_TARGET` | 2 | scouts kept in the field |
| `TURN_WEIGHT` | 10.0 | colony-target score charged per **turn** of travel |
| `SAME_SYSTEM_BONUS` | 30 | prefer filling out a system we already hold |
| `THREAT_DIST` | 120.0 | "near" — one sun separation (measured min 111.7) |
| `RAID_MARGIN` | 3 | surplus warships before going on the offensive |
| `FARM_PER_POP` | 4 | citizens per farm target |
| `REASSIGN_CAP` | pop/4 | job changes per planet per turn |

Units: distances are in the same space as `Planet:4/8/12`; a scout's ETA is
`ceil(distance / ShipDesign:36)`, which is how the game derives it (ETA is not stored).
Measured galaxy scale, for calibration: coordinates 0..~1130, 108 suns, 538 planets, 4–6
planets per system, orbit radius ≤ 39.6, minimum sun separation 111.7.

**Systems are not stored.** No field links a `Planet` to its `Sun`, so membership is
nearest-sun. That is safe here rather than merely convenient: a planet would have to sit
55.8 from its own star to be misassigned, 41% beyond the widest orbit observed.
`Snapshot.system_report()` reprints these numbers so the assumption is rechecked on any
new galaxy rather than inherited.

---

## 6. Open decisions — need your call before implementation

1. **Fog of war.** Client memory holds every planet and every `Owner`, discovered or
   not. The rules above say "known planets" and "known enemy ships", which implies the
   AI maintains its own discovery set seeded from the homeworld and grown by
   R-EXP-03 and by `Owner:320` scan reports. That is the honest reading of "not
   cheating", and it is more work. The alternative — let the AI see the whole galaxy —
   is simpler and makes the first version much stronger, but it is a different game.
   **Which?**

2. **Ship production is the critical path.** Three of the four X's need actuator D,
   and `ShipProduction` has never been seen live. Options: (a) RE it first by
   queueing a ship in the UI and reading `Planet:296`, then build the AI around a
   known-good actuator; (b) build the framework and the order-based rules now, stub D,
   and fill it in later. (a) is lower risk, (b) gets something running this week.
   **My recommendation: (a), and it is cheap — one game, one queued ship, one scan.**

3. **Where the AI sits.** `client/dev_tools/ai_player/` as a peer of the other dev
   tools, importing `ejbo_viewer`, is the low-friction choice. If the AI is meant to
   eventually drive a *server-side* civ rather than the client's local player, it
   belongs under `server/` and reads game state from the server's own model instead.
   The spec above assumes the client-side reading.

4. ~~**Governor overlap.**~~ **RESOLVED — no governors.** The AI does all planet
   management itself and leaves `Planet:496` unset everywhere. Actuator H is dropped and
   R-XPN-03 no longer assigns one.

5. **Turn acceleration.** `advance_turns.py` collapses the turn length so a full game
   runs in minutes, which is how the AI gets enough games to tune `TURN_WEIGHT` and the
   rest of §5. The AI's wake-on-turn-change loop works unchanged at any turn length.
   Not yet wired up.

---

## 6a. Originating orders without a human click

This is the blocking problem for both the AI player and multiplayer: a server must be
able to load each player's standing orders into a fresh client with nobody at the
keyboard. Four routes, evaluated.

### What is settled

- **Fabrication is out.** The order object owns three engine allocations (route vector,
  two list-node chains) that its allocator will free.
- **Lazy creation is out.** Writing `Ship:52` with a null `Ship:48` is inert — tested
  over a full turn: no crash, no movement, no allocation.
- **Transfer is in, and proven.** `actions.transfer_order` moves an order between ships
  with no allocation and no free. End to end: detached from one colony ship, attached to
  another, retargeted, navigated, colony founded on turn 3.
- **The movement model is fully solved**, which any of these routes needs:
  `position = leg_origin + route_unit_vector × progress`, verified to 0.000009.

### Route 1 — `loadgame` blob injection ★ recommended

The client already reconstructs an entire galaxy from a `loadgame` response
(`DONE#VER#<6-char>#DATA#<base64>`, parsed at `0x0048b5d0`), allocating every object
with its own allocator. The save format carries a **`ROUT`** section alongside `SHIP`,
`SHCO`, `SHPR` and `DYNO` — so routes are persisted, which means **the engine already has
a non-UI construction path for orders**. That is exactly the capability we need, and it
is the only route that is also the right multiplayer architecture: the server holds
authoritative orders and pushes them on load.

Status: `cs_server.py` stubs `loadgame` to return an empty blob. The format is documented
in git history (`06573cd:prototype/server/save_parser.py`, plus the three "Document save
blob binary format" commits). `ROUT` itself has **no coverage in the report at all** —
only the section marker is known.

**Cheapest way in:** make the server *persist* the `savegame` blob instead of discarding
it, then save a game while a ship is under a known order. We know that order's exact
numbers — origin, destination, length, and progress as a clean multiple of 13.5 — so
locating `ROUT`'s layout inside the blob is a search for known floats, not a blind parse.

### Route 2 — call the engine's own order constructor ★ CONFIRMED AND SHIPPED

**This is solved.** Don't imitate the engine — ask it.

```
order = operator new(0x60)                                   0x0065165A
order_ctor(ECX=order, origin, target, owner, f, 1, 0)        0x004E8420
Ship:48 = order ; then retarget_order() aims it
```

| | | |
|---|---|---|
| `0x0065165A` | `operator new(size_t)` | `__cdecl`, one arg, `ret 0`. Reachable **directly** from `CreateRemoteThread`, which passes exactly one stack arg — no stub needed |
| `0x004E8420` | order constructor | `__thiscall`, **six** stack args, `ret 0x18` |
| `vftable+0x30` | float for arg 4 | on the ship; takes no stack args |

Argument count came from the constructor's own `ret 0x18`, cross-checked against all 13
call sites — `0x00479685` shows six unobstructed pushes. Two of its branches,
`0x004DC588` and `0x004DC5FE`, are the scout and colonize paths writing `2` and `3` to
the same order-type local.

**Pointer forms are not interchangeable, and this is the easiest way to crash the
client.** Origin and target go in as **allocation starts** (`tag-8`) — which is why the
constructor reads coordinates at `+0xc/0x10/0x14` rather than the `+4/8/12` the
tag-relative annotations use. The Owner must be its **`tag-52`** primary form; the game
derives it with `lea reg,[node0-0x2c]` from the `tag-8` node at `Ship:40`.

Implemented in `remote.py` (stub builder + `CreateRemoteThread` plumbing) and
`actions.create_order`. Verified in game: a colony ship with `Ship:48` null was given a
working order and its route rendered in 3D.

**UI caveat, tracked as a `[ ]` in the reconstruction report.** The ships tab keeps
showing a ship's old command until a turn boundary passes, then catches up on its own.
The 3D route draws immediately and the ship moves correctly throughout, so this is a
deferred refresh of that list, not a defective order — which also makes the `.data` dirty
flags a weak suspect, since a validity gate would have broken movement too. Cosmetic in
single-player; in multiplayer a player would not see server-pushed orders until the next
turn.

**Report corrected (Aug 2026).** `CosmicSupremacy_Reconstruction_Report.md` labels
`0x0082A900…0x0082A920` as "linked-list head/tail/sentinel of order records". Live, it is
**selection state**: `0x0082A900` held a reference node to the selected `Ship` (`addr-8`)
and `0x0082A918` one to the selected `Sun`. Fixed in the report.

### Route 3 — donor pool (works today, limited)

Keep a small pool of UI-created orders and shuffle them between ships. Real but bounded:
it relocates orders, never creates them, and the supply only shrinks, because completing
an order consumes it — for colonize, along with the ship. Viable stopgap for a handful of
concurrent orders; not a foundation.

A partial extension: for Move and Scout, retarget before arrival so the order never
completes and the object is never consumed. Colonize and conquer inherently complete.

### Route 4 — harvest orders from AI-civ ships — NOT recommended

The AI civ creates orders every turn with no user action, and `Ship:40` and `Ship:48` are
independent, so one could be detached from an enemy ship and attached to ours. Renewable
and needs no click. **But it leaves the opponent's ship orderless**, which is direct
interference with another player rather than playing our own civ — it fails the §1.1
no-cheating rule. It also assumes an AI civ exists, which in real multiplayer it may not.
Recorded for completeness; not to be built.

### No longer a concern

**Does a newly built ship come with an order object?** It no longer matters. Route 2
originates one for any ship at any time, so a ship that starts at `Ship:48 == 0` — which
every colony ship does — is fully controllable from the moment it exists.

## 7. Build order

1. **RE `ShipProduction`** (open decision 2a) — unblocks D.
2. **Framework**: snapshot / diff / rule-engine / dry-run mode that logs every intended
   write without performing it. Dry-run is the safety net for all of §4.
3. **Actuator modules**, in dependency order: A (jobs) → F (research) → B+C (building)
   → E (orders) → D (ship production) → G, H.
4. **Explore**, end to end, with a real scout — smallest rule set that closes the loop.
5. **Expand**, then **Exploit**, then **Exterminate**.
