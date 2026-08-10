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

**Also built:** Sustain (S-food), Exploit (R-XPL-01 jobs, -02 facilities, -04 research,
-05/-06 crew, -08 hurry), Explore (R-EXP-01/-02) and Exterminate (R-XTM-00 declare war,
-01 fleet, -03 attack, -05 withdraw). Fifteen rules are wired into `ai.py`'s `RULES` list;
that list is the authority on what actually runs, not this paragraph.

**Every civ can now be played by this controller** (Aug 2026): `duel.py` runs one decision pass
per civ per turn on a shared turn clock, with per-civ fog of war and a rotating move order. It
needed no new actuator. **And a galaxy is no longer limited to the two civs the engine
generates** — `server/dev_tools/inject_civ.py` adds a whole player to a save blob, confirmed live
with a third civ that owned a homeworld and played four turns. See §6.6.

**A full game has been played end to end** (Aug 2026): the AI expanded to 9 planets, out-scored
the rival 33k to 3.5k, explored 105 of 108 systems with three self-built scouts, declared war,
built and crewed a bomber, sent it 624 units and fought at turn 114 — losing both warships in a
single turn boundary. Every X now runs unattended; none of them is finished.

`[ ]` **Still specification, none of these has a `run_*` function:** S-04 insolvency,
R-EXP-03 record arrivals (partly subsumed by `sensors._discover`), R-EXP-04 recall damaged
scouts, R-XPN-04 over-expansion, R-XPL-03 wealth mode, R-XPL-07 liquidation, R-XTM-02 defend,
R-XTM-04 conquer. Each is marked at its own rule below.

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
   including things the player has not discovered. **RESOLVED — the AI keeps its own
   discovery set** (`sensors.History.discovered`), grown only by reaching a sun or by a
   scan report, and now persisted across restarts. `--vision all` still exists as an
   explicit opt-out for testing; it is a different game and should not be the default.

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
| Recruitment rate | `Planet:100` byte 3 | percent of the food SURPLUS diverted to military; the rest goes to citizen growth. Ceiling is per planet: **40 with no military camp, +20 per camp** — confirmed in the UI at 60% with one camp |
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
| `researchRate` | Δ`Owner:12` per turn — **net of science wastage**, see below |
| `incomeRate` | Δ`Owner:8` per turn |
| `popGrowth[planet]` | Δ population count per turn |

**Science wastage caps the value of scientists, and we do not model it.** From the
official manual (`cosmicsupremacy.com/wiki/manual/research-screen`):

```
Science-Surplus = Output * (0.7 + 0.3 / (1 + 1e-8 * Output^2))
Science-Wastage = Output - Science-Surplus
```

Wastage is invisible below ~1,000 points/turn, reaches 15% at 10,000, 27% at 30,000 and
is hard-capped at 30%. Corruption is applied per planet first, then wastage on the total.

`[ ]` **R-XPL-01 has no scientist ceiling.** The job mix assigns scientists by a flat
percentage, so a large empire keeps adding them into a 30% loss. The marginal value of a
scientist falls continuously above 1,000 output; the rule should stop well before the cap
and put those citizens somewhere with linear returns. Not implemented — the trigger needs
per-planet science output, which we do not read yet.

Deltas are only meaningful when nothing else changed that turn, so the AI records the
snapshot pair alongside the actions it took, and treats a delta as stale for one turn
after it acts on that planet.

---

## 3. Actuators — the button-click modules

One module per UI action. Grade is how confident we are it works today.

| # | Action | Mechanism | Grade |
|---|---|---|---|
| A | **Set a citizen's job** | write job id to `Planet:144` element `+0` | **READY** — plain int, no allocation, same field `set_population.py` already writes |
| B | **Select the next building** | write type id to `Planet:284` | **READY** — paired with C in `actions.build_facility`; farms and shipyards were built repeatedly over a full game |
| C | **Start constructing / switch to wealth** | wealth: point `Planet:296` at the singleton `0x0080B540` and set `Planet:344 = 0`. Build: point `Planet:296` at the planet's own embedded `Facility` | **READY** — both directions. The build direction never needed an allocation: the `Facility` object is embedded in the planet, so `actions.build_facility` points at it rather than making one |
| D | **Queue a ship** | write a `ShipDesign*` (`tag-12`) to `Planet:296`, set `Planet:344` | **READY** — `actions.queue_ship`. Ship builds point at the shared design; no per-build object exists and the engine registers the finished `Ship` itself |
| D′ | **Stop building / generate wealth** | `Planet:296` → `PilingUpWealth` `0x0080B540`, clear `Planet:344` | **READY** — `actions.set_wealth_mode` |
| I | **Design a ship** | `operator new(0x118)` + default ctor `0x00546EC0`, `fit_parts`, `set_design_owner` | **PARKED** — the object is created, tagged, owned and parts fit, but it is never REGISTERED (zero `tag-12` refs vs three per UI design) and its stat block is never computed. Both are done by the design editor's save path; report `[ ]` find the design editor's save path |
| E | **Retarget an existing order** | rewrite `Ship:48+16/20/24`, the route leg, progress, then `Ship:52`/`Ship:76` | **READY** — `actions.retarget_order` |
| E′ | **Originate an order** | engine's own `operator new` + order constructor, via `remote.py` | **READY** — `actions.create_order`; see §6a Route 2 |
| E″ | **Move an order between ships** | detach donor, attach recipient | **READY** — `actions.transfer_order` |
| F | **Set research topic** | write `Owner:144` **and** `Owner:152` | **READY** — confirmed writable |
| G | **Set recruitment rate** | write byte 3 of `Planet:100` | **READY** — `actions.set_recruitment`. Driven live at 0 / 20 / 60 % with the military store responding each time; the ceiling in §4.3 R-XPL-05 is honoured by `max_recruitment` |
| H | ~~Assign a governor~~ | — | **DROPPED** — the AI manages planets itself and leaves `Planet:496` unset |
| J | **Sell a facility** | call `PlanetProperties::SellFacility` `0x004F8580` (`__thiscall`, `ret 8`, ECX = `planet_tag + 88`) | **READY** — `actions.sell_facility`. Confirmed live twice: a military camp paid exactly **75** and was removed. `price = round(facilityValue * 0.05) * count`; the 0.05 is the double at `0x753CF8` |
| K | **Hurry the current build** | call `PlanetProperties::HurryProduction` `0x004EFFF0` (`__thiscall`, ECX = `planet_tag + 88`) | **READY** — `actions.hurry_production`. `cost = 4 * (total - progress)`; needs `progress/total >= 0.5`, `cost <= Owner:8`, production kind != 7 and `Planet:300 == 0`. Confirmed live: cash moved by exactly the quoted price and progress jumped to its total |
| L | **Order a ship at a TARGET OBJECT** | engine `ShipCommand` ctor `0x004E9BA0` + `Ship::SetCommand` `0x004D9DC0` | **DO NOT USE — KILLS THE CLIENT.** `actions.set_command` crashes the game at the NEXT TURN BOUNDARY, for Move (1) as well as Attack (4), even with the route built first. Reproduced from a restore point; see §4.4. Nothing uses it. Kept only because it is still the one known way to populate `Ship:56` |
| M | **Move a ship to a target, route only** | `create_order` + `retarget_order`, and nothing else | **READY** — `exterminate.send_to`. The replacement for L. Reuses `Ship:48` when the ship has one, so it allocates nothing per turn. `Ship:56` stays the null node and the ship navigates correctly anyway |
| N | **Conscript a citizen** | `ChangeCitizenJobs` `0x00574180` (`__cdecl`, 4 args) with job id 3, priced by `GetDraftCost` `0x00516060` | **READY** — `actions.conscript`. Confirmed live: citizen count −1, stationed military +1, and cash down by exactly the quoted 115. The cost getter was validated first against a formula derived from the disassembly — six readings across two civs, all exact |

**Actuator J is the first actuator that is a whole engine TRANSACTION rather than a field
write, and the reason matters beyond this one button.** Selling pays cash, and §1.1
forbids writing `Owner:8`. A sale that credited itself would be arithmetically identical
to inventing money, with no way to distinguish a correct payout from a generous one. The
engine prices the building, credits the treasury and removes the units in one call, so
the number is its own. It refuses on the networked path — the call site at `0x0056EF91`
is guarded by the offline flag `0x0086F1A0`, and in multiplayer the server owns the
transaction, so a local apply would desync rather than cheat.

Generalising: **an engine call inherits whatever validation the engine does; a field
write inherits none.** Every cheat found so far — research prerequisites, facility
unlocks — came from writing a legal field to a value the UI would have refused. Where an
engine function performs the *whole* action, calling it is both less code and safer than
reproducing it. See §8.

**Sequencing is no longer a constraint.** D, B and C are all READY, so ship production and
facility building both work; that paragraph described a blocked state that no longer exists.

`[ ]` **Two actuators are built but unused, and one of them matters.** `sell_facility` (J)
is called by nothing because R-XPL-07 was never built, and `transfer_order` (E″) is
superseded by `create_order`. J is the more interesting of the two — it works, it is the
only priced engine transaction we have, and the rule that would use it is blocked on a
facility-upkeep sensor (§4.3 R-XPL-07).

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
| Attack | 4 | an enemy planet's XYZ — issued live, but see §4.4: issuing it through actuator L crashes the client, and Move (1) reaches the same outcome because combat is positional. On a fleet the order lives on `Fleet:52` |
| Conquer | 5 | an **enemy-owned** planet's XYZ — `[ ]` never issued; do not assume it survives a turn boundary until tested |

---

## 4. The rules

Evaluated once per turn in the order below. Earlier rules win; a planet or ship
claimed by one rule is not reconsidered by a later one. **Sustain runs before the four
X's** — a starving colony undoes any amount of expansion — but it is a precondition
layer, not a fifth strategy.

Each rule cites the fields it reads and the actuator it needs. Every rule carries its
implementation status on its heading — IMPLEMENTED with the function that runs it, or
`[ ]` NOT BUILT. **`ai.py`'s `RULES` list is the authority**; a heading here that disagrees
with it is a bug in this document. No rule is blocked on a missing actuator any more, which
is why the old `[BLOCKED: x]` marker no longer appears.

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

**S-02 — Famine: build a farm** — IMPLEMENTED, folded into R-XPL-02 (`exploit.run_facilities`)
```
WHEN   foodDelta[P] < 0  AND  farm count in Planet:204[P] < ceil(pop / 4)
THEN   Planet:284[P] = 0 (farm); ensure Planet:344[P] == 1
USES   B, C
```

**S-03 — Surplus: release farmers**
```
WHEN   foodDelta[P] > 0  AND  Planet:112[P] >= 0.9 * Planet:116[P]
THEN   reassign farmers to the role the current phase wants (§4.3 R-XPL-01)
LIMIT  never below the farmer count that made foodDelta >= 0
USES   A
```

**S-04 — Insolvency** — `[ ]` NOT BUILT
```
WHEN   incomeRate < 0  AND  Owner:8 < 3 turns of burn
THEN   reassign workers/scientists to banker (6) on the highest-pop planet;
       suspend all rules that add ship upkeep (ShipDesign:48) this turn
USES   A
```
`[ ]` Never triggered in testing because income has been strongly positive all game
(11k–20k cash idle by turn 150), so this is untested rather than merely unwritten — the
failure it guards against has not been seen. It is also the trigger for R-XPL-01's Economy
phase, which therefore never fires either.

### 4.1 Explore

**R-EXP-01 — Keep dedicated scouts in the field**
```
WHEN   count(our ships of role SCOUT) < SCOUT_TARGET   (initial: 3)
THEN   queue the FASTEST scout design (max ShipDesign:36) at a free shipyard
USES   D
```

**A SCOUT IS A CHASSIS AND ENGINES AND NOTHING ELSE.** This is how the game is
actually played, and it is worth stating as doctrine because the AI's own taxonomy
buries it: `SCOUT` is currently defined by *absence* (not colony, not troop, not armed),
so anything unarmed drifts into the role — including colony ships, which are slow,
expensive and needed elsewhere. A purpose-built scout is the opposite: strip the modules
and weapons, spend the whole hull on engines, and it is both the cheapest ship in the
empire and by far the fastest.

Speed compounds three ways, which is why this matters more than it looks:
* **Contact time.** Under fog of war, expansion and Exterminate are both gated on
  finding things. A galaxy is 108 systems; a colony ship doing the looking at 13.5/turn
  took ~500 turns to find one enemy planet.
* **It improves with research.** Engine ids come from the tech tree — Engine 1 arrives
  with Cold Fusion (id 3), which the AI now researches early anyway for its first weapon,
  and later engines from Quantum Fields, Gravity Controller, Anti-Matter Control and on
  up. A scout design should be REBUILT whenever a faster engine unlocks, not fitted once.
* **Cheapness means several.** Three scouts fanning out cover ground three times as fast,
  and losing one costs almost nothing.

`[ ]` **Rebuild the scout design when a faster engine is researched.** Designs are
synthesised through `game_cycle.py`, so this is mechanical: on unlocking a new engine id,
mint a replacement scout and let R-EXP-01 build that one instead. Nothing does this yet.

**MEASURED.** Synthesised hulls, shuttle chassis, nothing fitted but engines, speed read
through the engine's own getter:

| engines | engine id | `ShipDesign:36` |
|---|---|---|
| 1 | 0 | 9.00 |
| 2 | 0 | 15.43 |
| 2 | **1** | **41.00** |
| 4 | 1 | 54.67 |
| 6 | 1 | 61.50 |

For comparison: the Colony Ship (3× engine 0 plus a colony module) makes **13.5**, the
fighter f1 (1× engine 0, mass driver) **6.43**, and the bomber b1 (1× engine 0, fusion
bomb) **3.38**. The `f1`/`b1` now in use carry **engine 1** instead and make about **10**,
measured from the bomber's actual per-turn movement.

`[ ]` **A warship at 10/turn is a 62-turn trip across this galaxy**, which is most of what
made Exterminate slow: war was declared around turn 100 and the fleet did not arrive until
turn 114, by which time the rest of the empire had moved on. Warship designs should spend
more hull on engines than the current one-engine minimum, and the §4.4 strength test should
account for the fact that a slow fleet arrives piecemeal.

Three things fall out, and they change how the AI should play:

* **The engine TECH matters more than the engine count.** Going from two engine-0s to two
  engine-1s more than doubles speed, 15.4 to 41.0, while doubling the count from 2 to 4
  adds only a third. Cold Fusion — already researched early for the first weapon — is what
  makes a fast scout possible.
* **Returns diminish but never reverse**, so there is no count that is actively wrong; 6
  engines still beats 4. The right number is an economic question about hull cost, not a
  speed question.
* **Payload is expensive in speed.** b1 carries the same single engine as probe1 and moves
  at a THIRD of its speed. A ship that has to arrive quickly should carry nothing it does
  not need, and a warship will always be slow.

A scout at 61.5 covers the galaxy roughly **4.5x faster than the colony ships that were
doing the exploring**, which is the difference between surveying a galaxy in tens of turns
and hundreds.

**R-EXP-02 — Send idle scouts to the nearest unvisited system**
```
WHEN   ship S has role SCOUT and Ship:52 == 0
THEN   pick the Sun with the smallest distance from Ship:4/8/12 that is not in
       visitedSystems; write Ship:52 = 2 and the Sun's Sun:4/8/12 into
       Ship:48 +16/20/24; set Ship:76 = 1
NOTE   scout targets a SUN, not a planet — this is the one order that does
USES   E
```

**R-EXP-03 — Record arrivals** — SUBSUMED by `sensors.History._discover`
```
WHEN   ship S had order 2 last turn and Ship:52 == 0 now
THEN   add that Sun to visitedSystems; add its planets to the known set
USES   none (read-only)
```
There is no `run_exp03`: `_discover` runs every pass and adds any sun a ship is currently
inside (within 55.0), which covers arrival without needing to watch for the order clearing.

`[ ]` **Discovery is sampled once per turn, so a fly-through is invisible.** A scout that
passes through a system and out the other side between two boundaries is never recorded as
having been there. Nothing has been seen to go wrong, but with fast scouts (61.5/turn
against a 39.6 orbit radius) it is arithmetically possible to skip a system entirely.
Recording the swept SEGMENT rather than the sampled point would close it.

**R-EXP-04 — Recall damaged scouts** — `[ ]` NOT BUILT
```
WHEN   Ship:136 < 0.5
THEN   order 1 (move) to the nearest owned planet's XYZ
USES   M
```
`[ ]` Now cheap to write — `exterminate.send_to` is exactly this actuator and R-XTM-05 is
the same rule with a different threshold. The two should probably be one rule parameterised
by threshold and role rather than two near-copies. Note the caveat on R-XTM-05: a ship that
dies inside one turn boundary is never observed damaged, so this only helps against
attrition, not against being caught.

### 4.2 Expand

**R-XPN-01 — Keep a colony ship available** — IMPLEMENTED in `expand.py`
```
WHEN   count(our ships of role COLONY) == 0
AND    there is at least one known colonisable target (R-XPN-02)
AND    Owner:8 >= ShipDesign:44 of the colony design
THEN   queue the COLONY design at the best shipyard planet
USES   D
```

**SOLVED — the recruitment stall is the "Inactivity" throttle, and it is a RAMP ON THE TURN
NUMBER.** The UI's Food Calculation tooltip names it: an `Inactivity: -N%` line that grows
over the life of a galaxy. It is `0x00538450`, and the formula is

```
pct = clamp( (currentTurn - offset - Owner:716) * 100 / divisor , 0, 100 )
      offset/divisor = 72/100 when GetGameOption(4)->[+0x30] == 0, else 150/150
```

On this galaxy `Owner:716` is 0 and the option is 0, so **pct = turn - 72**: first visible
around turn 73, 88% at turn 160, and **100% at turn 172, after which recruitment is
impossible at any slider setting.**

It is applied TWICE, at different weights, which is why the arithmetic never closed:

* **to food production, at HALF weight**, inside `0x004F0800` — the tooltip's `-47%` on a
  648 production figure;
* **to the military share, at FULL weight**, in the splitter `0x004F1580` — and *after* the
  food share has already been computed from the UNREDUCED military share.

That second point is the whole mystery. Measured live by the user at surplus 171, rate 60%:

```
military share before the throttle = 171 x 0.60 = 102.6
food share = 171 - 102.6           = 68.4   -> UI showed 69   ✓
military credited = 102.6 x (1 - 0.88)      -> UI showed 12   ✓
90 food is simply DESTROYED
```

So raising the slider visibly takes food from citizens and gives almost none of it to the
military. Nothing was broken; the throttle was eating it.

**Why three rounds of static analysis missed it: the branch labels were inverted.**

```
0x00538450:  cmp byte ptr [0x86f1a0], 0
             je  -> Owner:660      ; taken when the byte is ZERO
                 -> the turn ramp  ; taken when NON-zero
```

R6 and R7 both read non-zero as the `Owner:660` path, found that nothing ever writes
`Owner:660`, and closed the hypothesis as inert. This client reads the byte as **1**, which
takes the ramp. R7 then argued the throttle could not produce "food rising, military flat"
because it also reduces food — true, but at *half* weight and *before* the split, so it
does exactly that.

**NEUTRALISED — by setting the flag the engine already looks for, not by forging its
timer.** The engine refreshes the baseline itself, in `0x00543B90` (turn stage 26):

```
cmp  byte ptr [esi + 0x2fc], bl    ; Owner:712 — the ACTIVITY FLAG
je   skip                          ;   zero -> no refresh
mov  eax, [0x008578E8] ; dec eax
mov  [esi + 0x300], eax            ; Owner:716 = turn - 1
skip:
mov  cl, byte ptr [esi + 0x304]    ; Owner:720 — next turn's flag
mov  byte ptr [esi + 0x2fc], cl    ; Owner:712 = Owner:720
```

`Owner:720` is the "this player is present" signal and `Owner:712` is it latched for the
current turn, so **setting `Owner:720 = 1` once is self-sustaining** — the engine copies it
forward and writes its own value into `Owner:716` every turn thereafter. `set_activity.py`
does exactly that and nothing else.

Writing `Owner:716` directly would have worked too and is the wrong thing: it forges the
engine's own bookkeeping with a number we invented. Supplying the input a connected client
supplies, and letting the engine compute, is both smaller and honest.

**It marks EVERY civ, and the tool refuses to do one.** The decay models an ABSENT player
and in an AI-vs-AI galaxy nobody is absent — but clearing it only for ours would be a plain
cheat worth a compounding food and recruitment advantage. Confirmed live: both civs read
`active=1` and `pct=0%` together.

Measured on the live galaxy at turn 110, where the penalty stood at 38%:

```
turn 110  base=0    pct=38%   store 199/300
turn 112  base=111  pct=0%    store 226      (+27/turn, the old throttled rate)
turn 113  base=112  pct=0%    store 291      (+65/turn)
turn 114  base=113  pct=0%    store  56      <- wrapped; a unit was recruited
```

**CREW IS A PRECONDITION FOR EVERY ORDER RULE.** A ship with no crew cannot move,
and the engine CANCELS the order at the next turn boundary — it frees the order object
and clears `Ship:48`, so a controller that does not check will re-issue an order every
turn forever and leak one order object each time. Observed over twelve turns on a
colony ship whose order was otherwise perfect. Crew comes from conscripting a citizen
for cash, or from raising the recruitment rate (`Planet:100` byte 3) and waiting for a
unit to train, then loading crew onto the ship. A colony ship needs only the minimum.

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

**R-XPN-04 — Do not over-expand** — `[ ]` NOT BUILT
```
WHEN   count(owned planets) * COLONY_UPKEEP_EST > incomeRate
THEN   suppress R-XPN-01 until income recovers
```
`[ ]` **This is the rule that starves Exterminate, and the mechanism is not income.** With
531 unowned planets in the galaxy R-XPN-01 always has a target, so colony ships are queued
forever and compete for the one shipyard. Ships get first refusal over facilities in the
rule order, but *within* ships the order is warships → colony → scouts, so expansion
outranks scouting indefinitely. The trigger above (income) never fires because income stays
strongly positive; the real constraint is SHIPYARD TIME and crew, not cash. Either add a
second shipyard target, or cap concurrent colony ships, or charge expansion against the
fleet the AI is trying to build.

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
USES   B, C
```
`[ ]` **The priority list above is not what the code does.** `exploit.run_facilities` gates
on `buildable_types` (research-unlocked) and builds farms and shipyards; the university,
military-camp, light-turret and defence-agency clauses are not all implemented, and the
border-planet test that light turret needs (`nearest enemy < THREAT_DIST`) has no
implementation at all. Reconcile the list with the code, or trim the list to what is real.

`[ ]` **`MAX_SHIPYARDS = 2` exists in the code but not in this spec**, and the spec's
"shipyard 2 if the civ has no shipyard anywhere" says one. Two shipyards were in fact built
in the last game, which is the code winning. §5 should carry the constant.

**R-XPL-03 — Wealth mode when there is nothing worth building** — `[ ]` NOT WIRED
```
WHEN   every priority in R-XPL-02 is satisfied  AND  no ship is queued
THEN   point Planet:296[P] at 0x0080B540 and set Planet:344[P] = 0
USES   C   (this direction is available — it is a known static pointer)
```
`[ ]` The actuator (`actions.set_wealth_mode`) is READY and called from `exploit`, but
there is no `run_xpl03` in `ai.py`'s `RULES`, so there is no rule deciding *when* a planet
should switch to wealth. Planets that have built everything currently sit idle rather than
banking cash. Low value while cash is already the resource we cannot spend — which is
itself the argument for building the rule only after R-XPL-07 or conscription gives cash
somewhere to go.

**R-XPL-04 — Always be researching**
```
WHEN   Owner:144 == -1
THEN   pick the cheapest uncompleted item (id not in Owner:108) whose effect
       matches the phase; write it to BOTH Owner:144 and Owner:152
NOTE   the stockpile accrues regardless, so leaving this at -1 is pure waste
USES   F
```
**Implemented in `exploit.py`, gated by `research.py`.** The full 80-record tree is read
out of the running client by `research_dump.py`; the rule only ever selects a topic
`research.selectable()` approves, and corrects an illegal one already in flight.

```
Research-Cost = costFactor * (researches already completed) * 800
```
Confirmed three ways: derived from the engine (`GetScale` `0x0054A6F0` = 800, `GetCost`
`0x0054B140`), matched against four live observations, and stated verbatim in the manual.

`[ ]` **The cheapest-first tiebreak is the wrong direction.** Because the multiplier is
the completed count *at the moment of purchase*, a fixed set of techs costs least when the
HIGHEST-factor ones are bought FIRST. `available()` still orders by value then cheapest.
Changing it is a strategy decision, not a bug fix, so it is left explicit.

`[ ]` **One cost reading does not fit** — Quantum Fields (factor 2.8, 4 completed) should
show 8960 and was reported as 3200, which is exactly `800 * 1.0 * 4`, the price of
Advanced Magnetism — a member of its OR-prerequisite list. Two candidates: a misread, or
the UI displaying the next step of a chain rather than the target. A third, raised by the
user and worth taking seriously: **this civ's tree is inconsistent** because it holds
Astro Engineering without Advanced Magnetism, so any path-finder walking prerequisites may
behave oddly on exactly this node. Free test: the number should become `2240 * 5 = 11200`
once the current research completes, and `800 * 1.0 * 5 = 4000` if it is showing a
prerequisite instead.

**R-XPL-05 — Recruitment**
```
WHEN   a ship parked at P needs crew and P has a military camp
THEN   raise the rate toward max_recruitment(P) if P is comfortably fed,
       otherwise to RECRUIT_RATE_LEAN so growth is not starved
WHEN   nothing at P needs crew
THEN   lower it to 0 — the diversion costs food and buys nothing
USES   G
```
**The rate is a SPLIT, not a throttle.** It is the percentage of the planet's food
*surplus* diverted into the military store, and the remainder goes to citizen growth —
confirmed both in the splitter `0x004F1580` (`foodShare = surplus - militaryShare`) and in
the UI, which shows 60% to military and 40% to growth on a one-camp planet. So recruiting
always costs growth directly, which is why the rule backs off on a poorly-fed planet.

**The ceiling is per planet: 40 with no camp, +20 per camp.** We capped every planet at a
flat 40, so a planet with a camp was recruiting at two thirds of the rate a player would
have used. The engine applies whatever byte is written with **no clamp of its own** — the
getter is `movsx eax, byte ptr [ecx+0xf]` and nothing more — so honouring the ceiling is a
§1.1 obligation rather than a safety check. The `movsx` also means the byte must stay below
128, or the rate reads negative and the military store would count *down*.

**R-XPL-06 — Load crew onto a waiting ship** — IMPLEMENTED in `exploit.run_crew_load`
```
WHEN   a ship in orbit of owned planet P has fewer crew than ShipDesign:76
       AND P has that many trained military units stationed
THEN   move the units from P onto the ship
USES   load_crew
```
`[ ]` **Written in code before it was written here** — it, and R-XPL-08 below, were never
specified. Both now exist; this entry is the spec catching up.

`[ ]` **No priority between competing ships.** `run_crew_load` walks `owned_ships` in scan
order, so a colony ship wanting 2 crew and a scout wanting 1 compete on iteration order
rather than on what the empire needs. With recruitment at ~1 unit per 10 turns this decided
which rule made progress, and nothing chose it deliberately.

**R-XPL-08 — Hurry a build with surplus cash** — IMPLEMENTED in `exploit.run_hurry`
```
WHEN   a build is >= 50% done, Planet:300 == 0, and cash above a floor
THEN   call HurryProduction, cheapest first, within a per-turn budget
USES   K
```
`[ ]` **The budget rule is arbitrary** — a 5000 floor and a fraction of the remainder, both
invented. It routinely declined every eligible build ("35552 would exceed the turn's budget")
while sitting on 20k cash it had no other use for. Wants a real policy, ideally one that
knows what else the cash is for.

**R-XPL-09 — Conscript crew for a grounded ship** — IMPLEMENTED in `exploit.run_conscript`
```
WHEN   a ship is BUILT, short of ShipDesign:76 crew, in orbit of our planet P,
       P has fewer trained units than it needs, and cash > DRAFT_CASH_FLOOR
THEN   draft ONE citizen on P (cheapest role first, never a farmer)
USES   N
```
**This replaces a timer with a purchase, and the timer was setting the pace of the whole
game.** R-XPL-05 raises the recruitment rate and waits; recruitment delivers roughly one
unit per ten turns, so a scout finished on turn 30 sat on the pad until turn 40 and
Explore → contact → Exterminate all ran at that speed. Measured on the fresh galaxy: both
civs reached turn 41 with a second scout built and grounded while holding 6,121 and 3,144
cash with nothing to spend it on. A conscript costs **115**.

The price is `5 * (T + k) + 105` where `T` is the civ's military across the WHOLE empire,
counting ship crew — so it is cheap early and rises as the fleet grows, which is the right
shape for a rule that exists to unblock the first ships.

**ONE per pass per planet**, and that is correctness rather than caution: the engine erases
the drafted record and memmoves the tail down, so every citizen index above it shifts and a
second index taken from the same snapshot names a different citizen than the one it was
chosen for.

**Never drafts a farmer.** Food is the one thing that kills a colony and S-01 exists to put
citizens back on it; drafting off the farms would have the two rules fighting every turn.

`[ ]` **The loader still waits a turn.** R-XPL-09 runs before R-XPL-06, but the pass's
snapshot was taken before the draft, so the new unit is invisible until the next pass and
crew loads one turn later. Re-reading the planet after a draft would close it.

`[ ]` **`DRAFT_CASH_FLOOR` (500) is invented**, like R-XPL-08's budget. It is there so
drafting cannot empty the treasury the build rules also spend from, and nothing has tuned it.

**R-XPL-07 — Liquidate to survive a collapse** — `[ ]` NOT BUILT
```
WHEN   incomeRate < 0 for N consecutive turns
       AND projected Owner:8 hits 0 within RECOVERY_HORIZON turns
       AND the deficit is attributable to facility upkeep we can shed
THEN   sell facilities, cheapest-contribution-first, until incomeRate >= 0
KEEP   never sell the last farm on a planet, a shipyard while a ship is queued,
       or anything on a planet that is not itself part of the problem
USES   J
```
The motivating case is not tidying up: a civ that **loses the planets funding its
expensive buildings** faces a cascade — upkeep it can no longer pay against income it no
longer has. Liquidating is how a human trades a long-term asset for immediate solvency
instead of surrendering. It is the economic mirror of R-XTM-05 retreat.

`[ ]` **Blocked on an upkeep sensor, which is the whole difficulty.** The trigger needs
to know what each facility costs per turn, and we cannot compute it. `incomeRate` is
derived by differencing `Owner:8` across turns (§2.5), which gives the *net* figure and
cannot attribute the drain to individual buildings — so "which facility do I sell first"
has no answer yet. `ShipDesign:48` gives ship upkeep; there is no known facility
equivalent. Two ways in, neither attempted:
  * find the per-turn upkeep accumulator in the binary, the way `SellFacility` was found
    by anchoring on the treasury field
  * measure it: sell one facility on an otherwise-idle planet and difference `incomeRate`
    across the boundary — cheap now that actuator J works, but only yields the types we
    happen to own

`[ ]` Decide whether selling should also be available as a *funding* move (sell to afford
something better) or strictly as a solvency backstop. Only the latter is specified here.

### 4.4 Exterminate

**R-XTM-00 — Declare war first** — IMPLEMENTED in `exterminate.run_xtm00`, and it is a hard
precondition. Confirmed live: the relation went Neutral → War in *both* civs' records and
R-XTM-03 then engaged. Gated behind `ALLOW_DECLARE_WAR`, which is on for AI-vs-AI galaxies.
```
WHEN   any R-XTM rule wants to attack (4), conquer (5) or bio-bomb (6) civ C
AND    relation code for C in Owner:248 != 1 (War)
THEN   declare war on C before issuing the order
USES   a diplomacy actuator that does not exist yet
```
**War is checked in two independent places**, so an Attack order without it accomplishes
nothing: at order-issue time by `0x00477D90` (which refuses Conquer and Bio-bomb outright),
and again inside all three battle phases by `IsAtWarWith` `0x00535080`.

`Owner:248` is the relation container, one record per civ, code at `+0x04`:
**0 Neutral, 1 War, 2 Cease-Fire, 3 Peace, 4 Alliance** (from the game's own string table at
`0x00776DA4`).

**Static analysis suggested we might already be at war and need none of this. Checked live,
we are not.** First contact (`0x00534F90`) only defaults to War when
`GetGameOption(2) > 1`; on this galaxy that option reads **0**, which takes the branch that
leaves both civs **Neutral** — matching the user's report that a fresh game requires an
explicit declaration. `GetGameOption(29)` (force initial war) and `GetGameOption(31)`
(force combat hostility) also read 0. Read all three before assuming anything about the
starting relation; a differently configured galaxy genuinely would start at war.

**The relation record is SYMMETRIC and is written as such — DONE.** `0x0055BD40` writes the
same code into *both* civs' `Owner:248` in one call, and `actions.declare_war` does the same:
the live log shows two writes, `'GoodGuy' -> 'BadGuy'` and `'BadGuy' -> 'GoodGuy'`, both
Neutral → War. Since the battle phases query *a* civ's map, writing one side only would have
made combat depend on which side was asked.

`[ ]` **NO REPUTATION COST IS APPLIED, so declaring war is currently FREE.** The actuator
says so itself at runtime. That is tolerable while every civ in the galaxy runs this same
code and pays the same nothing, and it is a straightforward §1.1 violation the moment a human
is on the other side. The UI quantifies the figure before confirming; we do not know it.

`[ ]` **Prefer the command path over a raw write, and this is a fairness decision, not a
technical one.** The `NWTR` handler `0x0056F310` validates nothing, but the surrounding path
generates a **news item** ("Declares War") and applies a **reputation** change that the UI
quantifies before confirming. A raw relation write is silent: no notification to the victim,
no reputation cost. That is the first case found where bypassing the UI is an *advantage*
rather than merely unvalidated, so it is squarely a §1.1 violation. All the human-facing
rules live in the UI validator `0x004BDE50` — build-up-phase protection, teammate
protection, and the confirmations.

**R-XTM-01 — Maintain a standing fleet**
```
WHEN   sum(our WARSHIP count) < threatLevel
       where threatLevel = count of known enemy warships within THREAT_DIST
                           of any owned planet, floored at 2 once contact is made
THEN   queue the best WARSHIP design affordable in Owner:8 and Owner:1128
       (rank by max(:60,:64,:68) / :44, filter by metal :92 and radioactives :100
       against stock)
USES   D
```

**FIXED — the trigger is CONTACT, and gating it on WAR was a silent deadlock.** The
implementation opened with `if not enemies(): return 0`, which is narrower than the rule
above ("floored at 2 **once contact is made**") and closes the Exterminate loop on itself:

```
R-XTM-01 queues no warship until at war
R-XTM-00 declares no war until a CREWED WARSHIP exists
```

R-XTM-01 is the only rule that queues an armed hull, so from a Neutral start neither could
ever fire and the AI could not reach a battle at all. **Nothing in the logs said so** —
both rules return 0 before logging, so a game with no Exterminate output looks identical to
a peaceful one. Measured over a full run: at turn 200 the civ held 9 planets, score 33,362,
cash 11,229, `f1` and `b1` both ready and 105 of 108 systems explored, and R-XTM-03 had
never once been reached. `contacted()` now supplies the contact set and
`enemies() or contacted()` is the gate. Attack itself stays war-gated in R-XTM-03, which is
correct — the engine checks `IsAtWarWith` in all three battle phases.

**FIXED — the discovery set now survives a restart, which this rule depends on.**
`sensors.History.discovered` was rebuilt each pass from where our planets and ships are *now*
plus scan reports, and never written down. A fresh process on the same galaxy recovered 7
systems out of 105 explored, so contact — and therefore the whole Exterminate chain — was
lost by restarting the controller alone, with nothing changed in the game.

It is now persisted to `ai_player/state/discovered_<galaxy>_<civ>.json`, keyed by a galaxy
FINGERPRINT (sun count plus a hash of every sun's id and rounded position) as well as the civ
name, because one machine plays several galaxies and a map belongs to one player. The file is
loaded before `_discover` runs so a reload and this pass's sightings union, written only when
the set actually grew, and replaced atomically so a killed loop cannot truncate it. Verified
across two processes: one saved, the next reloaded and `contacted()` returned the rival.

Persisting our own discoveries is the OPPOSITE of the omniscience worry in §6.1 — the bug was
handing the AI a fresh fog of war on every restart, which it then re-explored at full speed.

### Actuator L crashes the client — R-XTM-03 and R-XTM-05 do not use it (Aug 2026)

**`set_command` kills the client at the NEXT TURN BOUNDARY, for Move as well as Attack.**
The order is written cleanly and the pass completes; the engine dies resolving the turn.
Measured from `client/armed.dat` (turn 38, at war, one crewed `b1`), one boundary per trial:

| what was issued | survived the boundary |
|---|---|
| `set_command(4 attack)` | **no** |
| `set_command(1 move)` | **no** |
| `create_order` + `retarget_order(1 move)` | **yes** — moved 9.5 units, exactly `ShipDesign:36` |

So it is the `ShipCommand` / `Ship::SetCommand` pair, not Attack, and not the order TYPE.

**This also refutes the premise actuator L was built on.** A route-only order leaves `Ship:56`
as the static null node `0x00857C54` and the ship navigates correctly regardless, so
`Ship::HasActiveTargetedOrder` gates something other than movement. Nothing the AI does needs
`Ship:56`: combat triggers on CO-LOCATION and the battle driver `0x00523AC0` never reads the
order type at all, so a Move order that delivers the hull does the whole job.

Both rules now go through `exterminate.send_to`, which reuses `Ship:48` when the ship already
has one — verified over 8 turns as **zero** order allocations and **zero** ShipCommand
allocations, with the bomber closing 624.3 → 544.2 on BadGuy's HQ.

`[ ]` **Why SetCommand is fatal is still unknown**, and it is the only known way to populate
`Ship:56`, so the function is kept and marked do-not-use rather than deleted. Two unreconciled
observations for whoever picks it up: in the harness SetCommand appears to be a NO-OP (the ship
still read type 1 with a null `Ship:56` afterwards), while in the 400-turn run at turn 405 it
DID apply — type 4, real node — and crashed anyway. The difference was that order object had
been created a turn earlier rather than in the same pass.

**~~ATTACK ORDERS NEED `Ship:56`~~ — WRONG, and it was the premise for actuator L.** The
claim was that `Ship::HasActiveTargetedOrder` (`0x004D9880`) requires order type 1/4/7 **and**
a non-null `Ship:56`, so a coordinate-only Attack order would be inert. Measured: a route-only
Move order leaves `Ship:56` as the static null node `0x00857C54` and the ship navigates
correctly regardless. Whatever that function gates, it is not movement, and it is not combat —
the battle driver `0x00523AC0` buckets by position and never reads the order type. Superseded
by the actuator-L section above; kept here only so the old reasoning is not re-derived.

`[ ]` **What `Ship::HasActiveTargetedOrder` actually gates is still unknown.** It is called
from somewhere, and `Ship:56` is populated by the UI path, so both exist for a reason we have
not found. Worth knowing before assuming a null `Ship:56` is harmless in every situation
rather than just the ones tested (Move and Attack toward an enemy planet).

**R-XTM-02 — Defend** — `[ ]` NOT BUILT
```
WHEN   an enemy ship is within THREAT_DIST of owned planet P
THEN   order every idle WARSHIP within reach to move (1) to P's XYZ
USES   M
```
`[ ]` Still not built, but no longer untestable: `duel.py` (§6.6) supplies the opponent this
rule needs. It will not attack until it has an armed design, so the order is designs for the
second civ, then this rule. Note the rule no longer needs an "order attack (4)" step: arriving
is the whole action.

**R-XTM-03 — Raid** — IMPLEMENTED in `exterminate.run_xtm03`
```
WHEN   our WARSHIP count >= threatLevel + RAID_MARGIN
THEN   pick the weakest visible enemy planet
       weakness = stationed military (Planet:168 span / 16)
                + defensive facilities, from facilities.read_table
       send the fleet with M (Move); co-location at war starts the fight
USES   M
```
`[ ]` **"WEAKEST" SELECTS THE ONE TARGET THAT CANNOT PRODUCE A BATTLE — measured Aug 2026 on
the AI-vs-AI galaxy.** Both civs reached war with crewed warships, both fleets flew, both
ARRIVED, and nothing happened for fifty turns:

| ship | owner | in orbit of | planet's owner | defences |
|---|---|---|---|---|
| #690, #691 | GoodGuy | #41 | BadGuy | military 0, no turret |
| #689 | BadGuy | #43 | GoodGuy | military 0, no turret |

The rule ranks by `weakness = stationed military + defensive facilities` and takes the
minimum, so on any civ that has expanded it picks a bare colony — weakness 0, nothing aboard
to fight. The fleet parks in orbit permanently, the order completes, and the next pass
re-issues an order whose origin already equals its target.

**This also corrects the doctrine above.** "Co-location at war starts the fight" is not
enough: there must be something to fight. The one battle this project has ever seen was
against a homeworld of weakness 500, and it happened only because the rival held exactly ONE
planet, so weakest and strongest were the same object. That made a broken heuristic look
correct.

Two fixes, and they are different rules rather than alternatives:
* **An undefended enemy planet should be CONQUERED, not stared at** — that is R-XTM-04, which
  is not built and needs a TROOP design plus order type 5. Arriving and doing nothing is the
  *correct* behaviour for a Move order; the gap is that the AI has no way to take a planet.
* **A battle needs a defended target or an enemy fleet.** If the goal is combat, rank by
  "can I beat it" rather than by "is it weak", which is the same strength test the `[ ]` below
  asks for from the other direction.

`[ ]` **The two fleets passed each other.** Each civ raided the other's colony simultaneously
and neither intercepted, because nothing looks at where the enemy's ships are going. R-XTM-02
(defend) is the missing half.

`[ ]` **There is no "too strong to attack" test, and it fed hulls into a meat grinder.**
The rule picks the WEAKEST visible enemy planet, but with one enemy planet in view the weakest
is also the only one. It sent a shuttle-chassis bomber at a homeworld of weakness 500, lost it,
queued a replacement, and sent that too. It needs to compare its own firepower against the
target's defence and decline — a raid it cannot win is worse than no raid, because it also
feeds the enemy score.

`[ ]` **`RAID_MARGIN` is in §5 but not in the code**, which uses `WARSHIP_TARGET = 2` and
attacks as soon as anything is crewed. The margin was meant to stop exactly the trickle-feed
above.

`[ ]` **The fleet is dispatched piecemeal.** Each warship is sent the moment it is crewed, so
they arrive one at a time and are defeated one at a time. Ships travel at very different speeds
(bomber ~10/turn); a rendezvous or a wait-for-N-ships gate is the obvious fix and does not
exist.

**R-XTM-04 — Conquer** — `[ ]` NOT BUILT, but two of its three unknowns are now closed

**ORDER TYPE 5 IS SAFE — issued for the first time, Aug 2026.** The worry recorded below was
that Conquer might do what Move and Attack did through actuator L and kill the client at the
next boundary. It does not. A `t1` troop hull was given order 5 through `exterminate.send_to`
(route-only, `Ship:56` left null) and the client resolved eight boundaries with the order
live, navigating at its design speed the whole way.

**AN INVASION COSTS ONE UNIT ABOVE THE FLIGHT CREW — CONFIRMED, and by a controlled pair.**
Two identical `t1` hulls were sent at the same planet:

| | #704 | #705 |
|---|---|---|
| aboard on arrival | 2 — exactly `ShipDesign:76` | 3 |
| result | seven turns parked, order still 5, nothing | **captured on arrival** |
| aboard afterwards | 2 | 2 |

So `ShipDesign:76` is the crew needed to FLY, and an invasion spends one record ON TOP of it.
With only the minimum the order stays live and unfulfilled — the engine is waiting for troops,
not refusing the order. That is why the earlier attempt looked like a failure and was not one.

**What a capture does**, read off the planet before and after:
* ownership moves to the attacker, and the **population survives intact** (17 before, 17 after)
* the landed unit becomes the garrison: `Planet:168` went 0 → 1
* the ship is **NOT consumed** — unlike a colony ship it stays, minus the one unit
* the order clears to 0, the same way a completed Move does

`[ ]` The target's military camp was in its facility map before the capture and gone after
(`{6:1, 4:1, 0:7}` → `{4:1, 0:7}`). Fifteen turns passed between the two readings, so this is
NOT established as a consequence of conquest — check it across a single boundary before
believing it.

**Crew and troops share one container.** A troop hull was diffed against a scout across the
whole `Ship` window: there is exactly one 16-byte-record vector, `Ship:124/128/132`, holding
two records of job id 3. There is no second "troops" vector, which fits the report's finding
that a citizen, a stationed military unit and a ship's crew member are the same record.

**There is no separate troop-loading action in the UI** — confirmed by the user at the
client. Crewing and loading troops are the same thing, which is why they share the
`Ship:124` container. So no engine call has to be found for this: the AI only has to load
MORE than the minimum.

`[ ]` **`load_crew` cannot top up a crewed ship, so the extra must go in at FIRST load.** It
refuses when `Ship:124` is already a pointer, by design — growing the engine-owned buffer
would mean freeing memory we did not allocate. That is not a blocker now that the requirement
is known, because it allocates the whole buffer in one call: R-XPL-06 just has to ask for
`ShipDesign:76 + troops` on a TROOP hull instead of `ShipDesign:76`. Topping up a hull that has
already spent its troops still needs solving, and that is what a second invasion needs.

```
WHEN   a TROOP ship exists and its target planet's weakness == 0
THEN   Ship:52 = 5 (conquer), destination = that planet's Planet:4/8/12
NOTE   conquer is confirmed to target an enemy-owned planet's exact XYZ
USES   E
```
`[ ]` Blocked on ONE thing now rather than two: a TROOP design exists and is buildable by both
civs (`t1`, injected once Advanced Magnetism unlocked module 1), and order 5 is proven safe.
What remains is what an invasion actually requires aboard.

`[ ]` **Explore steals troop hulls.** `run_exp02` took a freshly crewed `t1` and sent it to a
sun 409 units away, because nothing in Exterminate claims a troop ship and the first rule that
wants an idle hull gets it. The same defect the SCOUT-by-absence taxonomy has always had, now
biting the most expensive hull in the empire.
```
WHEN   a TROOP ship exists and its target planet's weakness == 0
THEN   Ship:52 = 5 (conquer), destination = that planet's Planet:4/8/12
NOTE   conquer is confirmed to target an enemy-owned planet's exact XYZ
USES   E
```
`[ ]` Blocked on two things, neither started: no TROOP design has ever been synthesised
(module id 1, troop bay), and Conquer (5) is a targeted order type we have never issued — given
what Move and Attack did through actuator L, assume nothing about whether it survives a turn
boundary until it is tested from a restore point.

**R-XTM-05 — Retreat** — IMPLEMENTED in `exterminate.run_xtm05`
```
WHEN   Ship:136 < 0.3
THEN   send it to the nearest owned planet with M; ships are deleted on death,
       not marked, so a ship that vanishes from the scan was destroyed
USES   M
```
`[ ]` **It cannot fire against a one-boundary wipe, and that is how ships have actually
died.** Damage is multiplicative over up to 200 rounds with no early exit, all inside a single
turn resolution, so both warships went from condition 1.0 to deleted between two samples and
this rule never saw a damaged ship. It only helps a hull tough enough to survive a boundary,
which none of the current designs are. Not a defect in the rule — a reason it is not the
safety net it looks like. The real mitigation is arriving with enough hull, i.e. R-XTM-03's
missing strength test.

---

## 5. Constants to tune

| Name | Initial | Meaning |
|---|---|---|
| `SCOUT_TARGET` | 3 | dedicated scouts kept in the field |
| `TURN_WEIGHT` | 10.0 | colony-target score charged per **turn** of travel |
| `SAME_SYSTEM_BONUS` | 30 | prefer filling out a system we already hold |
| `THREAT_DIST` | 120.0 | "near" — one sun separation (measured min 111.7) |
| `RAID_MARGIN` | 3 | surplus warships before going on the offensive |
| `FARM_PER_POP` | 4 | citizens per farm target |
| `REASSIGN_CAP` | pop/4 | job changes per planet per turn |

`[ ]` **This table has drifted from the code and should be generated from it, not typed.**
Known mismatches: `WARSHIP_TARGET` (2, in `exterminate.py`), `RETREAT_AT` (0.30),
`MAX_SHIPYARDS` (2), `SHIPYARD_POP` (10), `RECRUIT_RATE_LEAN` (20) and `CREW_PER_COLONY_SHIP`
all exist in code and not here, while `RAID_MARGIN` is here and used nowhere. A constant that
appears in only one of the two places is a constant nobody is tuning.

`[ ]` **None of these has actually been tuned.** They are still the initial guesses, apart
from `TURN_WEIGHT`, which was recalibrated once against a real galaxy. Tuning needs many fast
games, which `--drive` now makes possible and nothing has yet used.

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

1. ~~**Fog of war.**~~ **RESOLVED — the AI keeps its own discovery set**, seeded from owned
   planets, grown by ships reaching a sun and by `Owner:320` scan reports, and persisted
   across restarts. `--vision all` remains as an explicit testing opt-out. See §4.4 for the
   persistence design and §4.1 R-EXP-03 for the one remaining hole (a fly-through between
   two samples is never recorded).

2. ~~**Ship production is the critical path.**~~ **RESOLVED — actuator D works.** A ship
   build points `Planet:296` at the shared `ShipDesign` (tag-12); there is no per-build
   object. Colony ships, scouts and warships have all been queued, built and crewed
   unattended over a full game.

3. **Where the AI sits.** `client/dev_tools/ai_player/` as a peer of the other dev
   tools, importing `ejbo_viewer`, is the low-friction choice. If the AI is meant to
   eventually drive a *server-side* civ rather than the client's local player, it
   belongs under `server/` and reads game state from the server's own model instead.
   The spec above assumes the client-side reading.

4. ~~**Governor overlap.**~~ **RESOLVED — no governors.** The AI does all planet
   management itself and leaves `Planet:496` unset everywhere. Actuator H is dropped and
   R-XPN-03 no longer assigns one.

5. ~~**Turn acceleration.**~~ **RESOLVED — `ai.py --drive N`** writes the turn length at
   `0x0080AA08`, re-asserts it whenever the engine resets it (which it does, often), and
   restores the original on exit including on Ctrl-C. Games run at 8–15s per turn; a
   400-turn game takes about an hour. `[ ]` Nothing has yet used it for its actual purpose,
   which was tuning §5 over many games.

6. ~~**Two AI civs, or one AI against the engine's own?**~~ **RESOLVED — two AI civs, both
   driven from one client by `duel.py`.** The engine plays `BadGuy` passively: over 154 turns
   it held one planet, built no warship and declared no war, so R-XTM-02 (defend) could never
   fire and every other rule was measured against an opponent that never contested anything.

   **It needed no new actuator and no memory hack**, which is the opposite of what this item
   assumed. Every module already resolves its civ BY NAME through `gamestate.resolve_civ`
   rather than assuming the local player, and every write is addressed through that civ's own
   objects — the `.data` local-player node at `0x00857904` only chooses the DEFAULT civ, it
   does not gate what may be written. Confirmed live on a turn-154 galaxy: a controller
   pointed at `BadGuy` set its research topic, built it a farm, colonised two planets and took
   it from score 3644 to 5868 in eight turns, with the engine keeping every change across the
   turn boundaries and reverting nothing.

   Two clients is still the *correct* architecture and is still blocked on server-side turn
   sync; one client with two controllers is the testbed, and §1.1 is honoured per civ rather
   than per process:
   * **Separate fog of war** — one `sensors.History` per loop, and the discovery set is
     already persisted per civ, so neither side sees inside a system its own ships have not
     reached. `--vision all` remains an explicit opt-out.
   * **No fixed move order** — whoever passes first claims contested things (a shipyard, a
     colony target) against the other's pre-move state, so `duel.py` alternates the order by
     turn parity instead of letting one civ hold that advantage all game.

   `[ ]` **The engine's AI is still running underneath.** It reverted nothing in eight turns
   and it may simply have nothing to say about a civ whose planets are already busy, but
   nothing has established which of the two is deciding when they disagree. `Owner:360` and
   `Owner:1196` are the candidates to look at first — both read 1 on the local player and 0 on
   the engine's civ, and neither has been identified.

   `[ ]` **`BadGuy` has no synthesised designs**, so R-EXP-01 and R-XTM-01 cannot fire for it:
   it owns only the starting `Colony Ship`, which makes it an expander that can never scout or
   fight. Designs come from the save blob (`game_cycle.py --design`, which needs
   `--like-id <that civ's design>` because it otherwise resolves the local player's). Until
   that is done a duel is expansion-only, and only one side can reach a battle.

   **THE GALAXY IS NO LONGER LIMITED TO TWO CIVS.** `server/dev_tools/inject_civ.py` adds a whole
   player to a save blob — an `OWNR` clone under a new name plus a homeworld built by transplanting
   the donor's `PLPR` onto an uncolonised planet. Confirmed live: a turn-162 capture came back up
   with three `Owner` instances, `'Ceti'` owning the planet it was given, and `duel.py` drove all
   three for four turns with no crash. `--name` is repeatable, so twenty civs is one command. The
   engine derives each civ's registry key from its NAME, so the only uniqueness requirement is the
   name itself. Details and the measured blob layout are in
   `CosmicSupremacy_Reconstruction_Report.md` §2, with the save-blob work rather than the
   memory work.

   `[ ]` **A pass costs a full memory scan, and they are serialised.** Three civs means three scans
   per turn; twenty means twenty, which will not fit in a short driven turn. `duel.py` should share
   ONE snapshot per turn across all controllers instead of re-scanning per civ — the fog of war is
   already applied per civ on top of the shared scan, so nothing about §1.1 depends on scanning
   separately.

   `[ ]` **A torn read got through with three civs.** On turn 166 `GoodGuy` read 1 planet when it
   held 7, and acted on that. `_snapshot_settled` only re-reads when a civ reads ZERO planets, so a
   partial read of a seven-planet empire passes the guard. With more civs each pass starts later
   after the boundary, so this gets more likely, not less. The check should compare against the
   previous turn's count rather than against zero.

7. `[ ]` **The client must be relaunched to pick up a code change**, and doing so re-offers
   the customisation popups and (before persistence) discarded the discovery map. A restart
   also re-commits the homeworld click record over restored state — the report's data-loss
   bug. Worth deciding whether the loop should hot-reload its rule modules instead.

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

## 7. Engine calls vs. field writes

**Every cheat found so far came from a field write.** Research prerequisites and facility
unlocks were both cases of writing a legal field to a value the UI would have refused,
because the engine does not validate those fields on the path we write. An engine call
inherits whatever checking the engine does; a field write inherits none. That makes the
choice a *correctness* question, not just an ergonomic one.

We have called engine functions since Route 2 (`operator new`, the order constructor, the
`ShipDesign` constructor, the speed getter). What actuator J adds is the first call to a
complete **transaction** — a whole UI action, priced and applied by the engine — rather
than an allocator, a constructor or a getter. That is the pattern worth extending.

### Ranked candidates

| Candidate | Address | Why it matters |
|---|---|---|
| **Read the live research table** | `0x0054AA70` id→record, `0x0054B140` cost, `0x0054B200` prereq-at-index, `0x0054B7D0` exclusion test | `research.py` currently carries a table extracted for content version **≥688** while this client runs **565**, and the two demonstrably differ (see the cost-ratio argument in that file). These calls read the table the client is *actually* using. Read-only, so the safest possible first target, and it retires a known-wrong data source |
| ~~**`Ship::SetCommand`**~~ | `0x004D9DC0` | **WITHDRAWN — this candidate was tried and it kills the client** at the next turn boundary, for Move as well as Attack (§4.4). Writing `Ship:52` alone turns out to be fine: the ship navigates correctly with `Ship:56` null. `[ ]` The order-type-8 / `Ship:61 = 0x0B` observation is still unexplained and still unused |
| ~~**`ChangeCitizenJobs`**~~ | `0x00574180` | **DONE — actuator N.** Conscription works and R-XPL-09 uses it. Note it is still *not* used to replace actuator A: setting an ordinary job id by field write remains cheaper than a remote thread, and only job 3 goes through the engine, because only job 3 costs money |
| **`GetTrait`** | `0x00516180` | Real civ-modifier values (trait 18 = ship speed %, trait 38 = planet space %) rather than assuming 0. Feeds any speed or max-pop calculation |
| **available space** | `0x004F1140` (PP vftable slot 6) | `max_pop` with the trait bonus applied, instead of our `space/10` approximation |
| ~~**`GetDraftCost`**~~ | `0x00516060` | **DONE.** `remote.draft_cost`. Validated read-only before the mutating call, per the rule below: six readings across two civs at three counts, every one matching `sum(5*(T+k)+105)` computed independently from a snapshot. That match is also what confirmed `T` counts ship crew as well as stationed units |

`[ ]` **None of the remaining candidates has been attempted**, and the first two are now the
highest-value work in this document. The live research table would retire a data source known
to be for the wrong content version (`research.py` carries ≥688, this client runs 565).
`ChangeCitizenJobs` unlocks **conscription**, which is the answer to the crew bottleneck that
gates every order rule — the civ sat on 11k–20k idle cash for a hundred turns with nothing to
spend it on while waiting ~10 turns per recruit.

### The standing hazard

Stubs run on a `CreateRemoteThread`, **not the game's main thread**. **Something has now gone
wrong**: `Ship::SetCommand` kills the client at the next turn boundary (§4.4), and the cause is
not understood — it may be this hazard or it may be the call itself. Read the rest of this
section as a live risk rather than a precaution. Nothing guarantees safety: a call that takes a lock the main thread holds, or
mutates a container the main thread is walking, can deadlock or corrupt where a field
write would merely have been wrong. Static analysis found no TLS dependency in the
conscription call graph, which is evidence but not proof.

Practical consequences, and the rules that follow from them:
* Prefer **read-only** calls (`getters`, table lookups) — they carry the least risk and,
  as the research table shows, often the most value.
* For mutating calls, prefer ones whose call sites show them being invoked as a complete
  unit behind the offline flag `0x0086F1A0`, as `SellFacility` is.
* Validate every new stub on a **side-effect-free getter** before pointing it at anything
  that mutates state — `remote.py` already does this.
* **The design getters are NOT side-effect-free.** `ShipDesign:36/52/56/60/64/68/76` are a
  lazy cache: each getter computes the value and WRITES IT BACK. The ship-design tab does
  the same thing on the UI thread, so calling them while a human has that tab open is two
  threads writing one cache. A crash was traced to exactly that — six speed getters run
  back to back while the user opened the design tab. Read them when the AI is driving
  alone, not while someone is looking.
* `ret N` gives argument **count**, never **type**. Mistaking `0x005470A0` for a copy
  constructor on that basis crashed the client once.

## 8. Build order

Steps 1–5 are **DONE**. The AI plays all four X's unattended and has fought a battle.

1. ~~RE `ShipProduction`~~ — D works.
2. ~~Framework~~ — `ai.py` loop, `sensors.History`, dry-run by default.
3. ~~Actuator modules~~ — A, B, C, D, E, E′, F, G, J, K, M all READY. L is fatal, see §3.
4. ~~Explore end to end~~ — three self-built scouts swept 105 of 108 systems.
5. ~~Expand, Exploit, Exterminate~~ — all running; Exterminate reached combat.

### What comes next, in the order I would do it

1. `[ ]` **Make the fleet survivable.** R-XTM-03's missing strength test plus real hulls
   (corvette chassis, shields, several weapons) via `game_cycle.py --design`. Everything else
   in Exterminate is downstream of not losing every ship on arrival. Test from
   `client/armed.dat` with `battle_harness.py`, not from a fresh game.
2. ~~**Conscription**~~ — **DONE**, actuator N and R-XPL-09. Running unattended on the fresh
   galaxy: a grounded scout was drafted for, crewed the next turn and flew, and exploration
   went from roughly one new system per ten turns to five in four. The engine call it needed
   was also the first mutating one attempted since `SetCommand` killed the client, so it was
   built getter-first and tested one draft at a time from a checkpoint.
3. `[ ]` **Stop expansion starving the shipyard** — R-XPN-04, which is about shipyard time
   and crew rather than the income trigger currently specified.
4. `[ ]` **Read the live research table** (§7) — retires a table known to be for the wrong
   content version, and it is read-only, so it is the safest engine call available.
5. ~~**A second AI-driven civ**~~ — **DONE**, `duel.py` drives both civs on one turn clock
   (§6.6). `[ ]` What remains before a duel is a real test: synthesised designs for the second
   civ, and a FRESH galaxy — the demonstration ran on a turn-154 game the first civ had already
   won 21,591 to 3,644, which measures nothing about the strategy.
6. `[ ]` **Tune §5 over many fast games** — `--drive` makes this possible and nothing has
   used it for that yet.
