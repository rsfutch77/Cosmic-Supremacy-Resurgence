# AI Player, Strategy Specification

An external process that plays the local player's civ by reading and writing the
running client's memory, using only fields a human could have changed by clicking.
This document is the **rulebook**: what the AI senses, what it may touch, and the
if/then rules for each X of 4X. It is written in the vocabulary of
`ejbo_annotations.json` (`Class:offset`) so every rule is traceable to a confirmed
field or explicitly marked as blocked.

**Built and running:** the turn loop (`ai.py`), sensing (`gamestate.py`), the actuators for
jobs, research, ship orders and ship production (`actions.py`, `remote.py`), and the whole
Expand cycle, R-XPN-01 build, R-XPN-02 dispatch, R-XPN-03 bootstrap (`expand.py`), plus
`survey.py` and `build.py`. Verified over a full game: the AI founded colonies on turns 4 and
5 unattended and led the rival 3730 to 1666 on score by turn 12.

**Also built:** Sustain (S-food), Exploit (R-XPL-01 jobs, -02 facilities, -04 research,
-05/-06 crew, -08 hurry), Explore (R-EXP-01/-02) and Exterminate (R-XTM-00 declare war,
-01 fleet, -03 attack, -05 withdraw). Fifteen rules are wired into `ai.py`'s `RULES` list;
that list is the authority on what actually runs, not this paragraph.

**Every civ can now be played by this controller** (Aug 2026): `duel.py` runs one decision pass
per civ per turn on a shared turn clock, with per-civ fog of war and a rotating move order. It
needed no new actuator. **And a galaxy is no longer limited to the two civs the engine
generates**, `server/dev_tools/inject_civ.py` adds a whole player to a save blob, confirmed live
with a third civ that owned a homeworld and played four turns. See §6.6.

**A full game has been played end to end** (Aug 2026): the AI expanded to 9 planets, out-scored
the rival 33k to 3.5k, explored 105 of 108 systems with three self-built scouts, declared war,
built and crewed a bomber, sent it 624 units and fought at turn 114, losing both warships in a
single turn boundary. Every X now runs unattended; none of them is finished.

[x] **Every rule now has a `run_*` function.** R-XPL-07 liquidation was the last
(`exploit.run_liquidate`). S-04 insolvency is covered by R-XPL-03's stopgap, R-EXP-04 by
R-XTM-05, and R-EXP-03 by `sensors.History._discover`.

**Open questions about the game itself belong in
`CosmicSupremacy_Reconstruction_Report.md` as [ ] items**, not here, this document
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

[ ] **THE TURN COUNTER IS NOT A RECORD OF PASSES TAKEN, and treating it as one
will produce confident nonsense.** The engine advances turns against elapsed
time, so a host that sleeps comes back to a BURST of resolved turns that the
controller was never awake for: one run showed 29 turns elapsed against 12
decision passes, and the machine had been suspended for most of it. The same
signature, turns outrunning passes, is also what a controller too slow for its
turn length looks like, and the two are indistinguishable after the fact.

Consequences, both learned the hard way:
* **Never infer a rule's behaviour from a turn range that spans a gap.** Deltas
  in `sensors.History` are normalised per turn, which hides it further.
* **A per-turn measurement must be checked for contiguity**, not just length,
  the `Planet:368` traces were trustworthy precisely because their turn numbers
  ran 285..312 with no holes.

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
   outputs. `set_facility.py` and `set_population.py` exist and work, they are
   *diagnostics*, not actuators, and the AI must not call them.
2. **No illegal values.** A citizen job must be a real job id; a selected building
   must be a type the civ has unlocked (`Owner:172`); a population must not exceed
   `Planet:104 >> 16` / 10. **A queued SHIP DESIGN must be one every fitted part
   is researched for**, see below.
   **OWNING A DESIGN STOPPED BEING PROOF THAT IT IS LEGAL** (Aug 2026), and every
   selector here assumed otherwise. A design the UI produced can only contain parts
   the civ had already unlocked, so filtering on owner and fitted parts was
   sufficient for as long as designs only ever came from the design editor. Single
   Player breaks that: the AI cannot create a design at all (§3, actuator I), so
   its designs are SEEDED INTO THE GALAXY at turn 0, and `b1` carries a fusion
   bomb from the first turn while no weapon is unlocked until Cold Fusion.
   Measured on the seeded galaxy, `BadGuy` owning `f1` and `b1` with completed
   research `{0, 1}` and **no weapon unlocked at all**.

   `research.design_legal(design, done)` is the gate, and
   `research.illegal_parts` names what is missing so a rule can log the blocker
   rather than the flatly wrong "this civ has no armed design". It is applied in
   `explore.scout_designs`, `exterminate.warship_designs`, `exterminate.run_xtm07`
   and `expand.run_xpn01`. Shields are deliberately unchecked: the design object
   exposes a shield STAT (`ShipDesign:72`) but no fitted-shield vector is read, and
   a check against a list we do not read would pass everything while looking like
   a gate.

3. **No omniscience.** Client memory holds all 538 planets and every `Owner`,
   including things the player has not discovered. **RESOLVED, the AI keeps its own
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
| Space | `Planet:104 >> 16` | max pop = space / 10 |
| Food stored | `Planet:112`, cap `Planet:116` | |
| Construction progress | `Planet:120` | |
| Population | `Planet:144`/`148`/`152` | 16-byte elems; `+0` = job id, `+12` low16 = turn added |
| Population count | `(148-144)/16`, mirrored `Planet:352` low16 | |
| Stationed military | `Planet:168`/`172`/`176` | 16-byte elems, the same record as a citizen; `+0` is the job id and reads 3, `+12` low16 turn added |
| Built facilities | `Planet:204` map, walk to `[typeId, count]` | 0 farm, 2 shipyard, 4 university, 6 military camp, 8 defence agency, 9 light turret |
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
| Order type | `Ship:52` | 0 none, 1 move, 2 scout, 3 colonize, 4 attack, 5 conquer, 7 move-near |
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
| `researchRate` | Δ`Owner:12` per turn, **net of science wastage**, see below |
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

[ ] **R-XPL-01 has no scientist ceiling.** The job mix assigns scientists by a flat
percentage, so a large empire keeps adding them into a 30% loss. The marginal value of a
scientist falls continuously above 1,000 output; the rule should stop well before the cap
and put those citizens somewhere with linear returns. Not implemented, the trigger needs
per-planet science output, which we do not read yet.

Deltas are only meaningful when nothing else changed that turn, so the AI records the
snapshot pair alongside the actions it took, and treats a delta as stale for one turn
after it acts on that planet.

---

## 3. Actuators, the button-click modules

One module per UI action. Grade is how confident we are it works today.

| # | Action | Mechanism | Grade |
|---|---|---|---|
| A | **Set a citizen's job** | write job id to `Planet:144` element `+0` | **READY**, plain int, no allocation, same field `set_population.py` already writes |
| B | **Select the next building** | write type id to `Planet:284` | **READY**, paired with C in `actions.build_facility`; farms and shipyards were built repeatedly over a full game |
| C | **Start constructing / switch to wealth** | wealth: point `Planet:296` at the singleton `0x0080B540` and set `Planet:344 = 0`. Build: point `Planet:296` at the planet's own embedded `Facility` | **READY**, both directions. The build direction never needed an allocation: the `Facility` object is embedded in the planet, so `actions.build_facility` points at it rather than making one |
| D | **Queue a ship** | write a `ShipDesign*` (`tag-12`) to `Planet:296`, set `Planet:344` | **READY**, `actions.queue_ship`. Ship builds point at the shared design; no per-build object exists and the engine registers the finished `Ship` itself |
| D′ | **Stop building / generate wealth** | `Planet:296` → `PilingUpWealth` `0x0080B540`, clear `Planet:344` | **READY**, `actions.set_wealth_mode` |
| I | **Design a ship** | `operator new(0x118)` + default ctor `0x00546EC0`, `fit_parts`, `set_design_owner` | **PARKED, but the save path is now located**, see below. The object is created, tagged, owned and parts fit, and is never REGISTERED (zero `tag-12` refs vs three per UI design) |
| E | **Retarget an existing order** | rewrite `Ship:48+16/20/24`, the route leg, progress, then `Ship:52`/`Ship:76` | **READY**, `actions.retarget_order` |
| E′ | **Originate an order** | engine's own `operator new` + order constructor, via `remote.py` | **READY**, `actions.create_order`; see §6a Route 2 |
| E″ | **Move an order between ships** | detach donor, attach recipient | **READY**, `actions.transfer_order` |
| F | **Set research topic** | write `Owner:144` **and** `Owner:152` | **READY**, confirmed writable |
| G | **Set recruitment rate** | write byte 3 of `Planet:100` | **READY**, `actions.set_recruitment`. Driven live at 0 / 20 / 60 % with the military store responding each time; the ceiling in §4.3 R-XPL-05 is honoured by `max_recruitment` |
| H | ~~Assign a governor~~ |, | **DROPPED**, the AI manages planets itself and leaves `Planet:496` unset |
| J | **Sell a facility** | call `PlanetProperties::SellFacility` `0x004F8580` (`__thiscall`, `ret 8`, ECX = `planet_tag + 88`) | **READY**, `actions.sell_facility`. Confirmed live twice: a military camp paid exactly **75** and was removed. `price = round(facilityValue * 0.05) * count`; the 0.05 is the double at `0x753CF8` |
| K | **Hurry the current build** | call `PlanetProperties::HurryProduction` `0x004EFFF0` (`__thiscall`, ECX = `planet_tag + 88`) | **READY**, `actions.hurry_production`. `cost = 4 * (total - progress)`; needs `progress/total >= 0.5`, `cost <= Owner:8`, production kind != 7 and `Planet:300 == 0`. Confirmed live: cash moved by exactly the quoted price and progress jumped to its total |
| L | **Order a ship at a TARGET OBJECT** | engine `ShipCommand` ctor `0x004E9BA0` + `Ship::SetCommand` `0x004D9DC0` | **DO NOT USE, KILLS THE CLIENT.** `actions.set_command` crashes the game at the NEXT TURN BOUNDARY, for Move (1) as well as Attack (4), even with the route built first. Reproduced from a restore point; see §4.4. Nothing uses it. Kept only because it is still the one known way to populate `Ship:56` |
| M | **Move a ship to a target, route only** | `create_order` + `retarget_order`, and nothing else | **READY**, `exterminate.send_to`. The replacement for L. Reuses `Ship:48` when the ship has one, so it allocates nothing per turn. `Ship:56` stays the null node and the ship navigates correctly anyway |
| N | **Conscript a citizen** | `ChangeCitizenJobs` `0x00574180` (`__cdecl`, 4 args) with job id 3, priced by `GetDraftCost` `0x00516060` | **READY**, `actions.conscript`. Confirmed live: citizen count −1, stationed military +1, and cash down by exactly the quoted 115. The cost getter was validated first against a formula derived from the disassembly, six readings across two civs, all exact |
| N′ | **Conscript straight onto a ship** | rewrite the citizen list without the drafted records; hand those records to a fresh crew buffer; debit `GetDraftCost` | **READY**, `actions.conscript_to_crew`. Confirmed live twice, and it is what finally unblocked Exterminate. See below |

**Actuator N′ exists because the planet's garrison was a staging area that only ever
failed.** N puts a unit in `Planet:168` and R-XPL-06 takes it straight back out the moment
a ship wants it, so a crew-bound unit never needed to stand on the planet at all, and
that vector is engine-owned, fills up, and then `_append_military` rightly refuses to grow
it. Measured: a finished hull sat grounded for ~100 turns behind
`"military vector is full (0 bytes spare, need 16)"` while the civ held 18k cash and had
nothing else in its way.

A citizen, a stationed unit and a crew member are the **same 16-byte record**, so the move
is legal in one hop. It **fills an empty crew vector only**, the Aug 2026 crash was
`_set_ship_crew` repointing a vector the engine already owned, and filling an empty one
with the whole complement in a single allocation is the path in every arm of that bisect
that survived. Partially-crewed ships still go the long way round.

It debits the engine's own quoted price for the whole batch, the same narrow §1.1 exception
N documents: a debit at a number we did not invent. The trade-off is deliberate, crew
taken this way never appears in the garrison, so N′ cannot conscript for DEFENCE. That
stays R-XPL-05's slider, and no rule defends a planet yet.

### Actuator I, the design-editor save path, located but not yet called (Aug 2026)

Found statically with `client/dev_tools/xrefs.py`, which was validated first by
rediscovering `SellFacility`'s documented call site `0x0056EF91` before being pointed at
anything unknown. **Every address below is a candidate to confirm, not a fact**: the tool
decodes only `E8 rel32` and 32-bit absolute operands, so it is blind to virtual dispatch,
and nothing here has been executed yet.

**The `ShipDesign` constructor `0x00546EC0` has exactly TWO callers**, and both are
preceded by `operator new`, which is what makes them real construction sites rather than
byte sequences that decode like one:

| Caller | Enclosing fn | Reached from | Also calls | Reading |
|---|---|---|---|---|
| `0x00546855` | **`0x00546430`** | `0x004CE890`, itself called twice from `0x004D06F0` | `0x00537BF0` ×3 | **the design editor's save path** |
| `0x00576C97` | `0x00576B80` | `0x004A5D40`, which has **no direct callers** | `0x0054A410`, the speed getter `0x005480A0` | deserialisation, reached by virtual dispatch, which is what a response/load handler looks like |

**`0x00546430` calls `0x00537BF0` exactly three times, with the constructor between the
first and second, against the three `tag-12` references a UI design carries and a
synthesised one does not.** That numerical agreement is the reason to believe this is the
registration, and it is also exactly the kind of "two things agree so they must be the
same thing" reasoning the memory report warns cost it two wrong conclusions. It wants
confirming by execution, not by counting.

**`0x00537BF0` is `reference_node_for(EJBO*)`**, hand-decoded from its first bytes:

```
83 EC 08        sub  esp, 8
57              push edi
8B 7C 24 10     mov  edi, [esp+0x10]     ; one stack argument
85 FF           test edi, edi
75 0A           jnz  ...
B8 54 7C 85 00  mov  eax, 0x00857C54     ; the null node, already documented in §2.2
5F 83 C4 08 C3  pop/add/ret              ; cdecl, caller cleans up
```

It has **419 call sites**, so it is a general "give me the reference node for this object"
helper rather than anything design-specific, which is why the three calls inside
`0x00546430` are interesting and the function itself is not.

[ ] **Next step, and it is a measurement, not more reading.** `find_refs.py --designs`
already reports where a registered design's references live; run it against a design built
by actuator I and diff the two. The difference IS the registration, and it names the three
slots `0x00546430` writes without having to trust the static reading at all.

[ ] The editor path does **not** call the speed getter while the load path does, so where
the stat block gets computed on the editor path is still unknown. `remote.design_speed`
already warms that cache on demand, so this may not need solving at all.

**Actuator J is the first actuator that is a whole engine TRANSACTION rather than a field
write, and the reason matters beyond this one button.** Selling pays cash, and §1.1
forbids writing `Owner:8`. A sale that credited itself would be arithmetically identical
to inventing money, with no way to distinguish a correct payout from a generous one. The
engine prices the building, credits the treasury and removes the units in one call, so
the number is its own. It refuses on the networked path, the call site at `0x0056EF91`
is guarded by the offline flag `0x0086F1A0`, and in multiplayer the server owns the
transaction, so a local apply would desync rather than cheat.

Generalising: **an engine call inherits whatever validation the engine does; a field
write inherits none.** Every cheat found so far, research prerequisites, facility
unlocks, came from writing a legal field to a value the UI would have refused. Where an
engine function performs the *whole* action, calling it is both less code and safer than
reproducing it. See §8.

**Sequencing is no longer a constraint.** D, B and C are all READY, so ship production and
facility building both work; that paragraph described a blocked state that no longer exists.

[x] **Two actuators were built but unused.** `transfer_order` (E″) is superseded by
`create_order`. `sell_facility` (J), the only priced engine transaction we have, is now
called by R-XPL-07 (§4.3), and adds the sale's proceeds to the pass's own cash effect so the
next income reading does not count them as income.

**The order object cannot be fabricated**, this was measured, not assumed. Its payload
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
moved **exactly 13.5000 units, `ShipDesign:36` to four decimals, along the new
bearing**. The engine treats the leg as authoritative input, not a cache it recomputes.

**Lazy allocation is dead.** Writing `Ship:52 = 3` on a ship with `Ship:48 == 0` and
advancing a full turn left the client running, the ship stationary, and both `Ship:48`
and `Ship:76` still zero. The engine null-checks the pointer rather than dereferencing
it, the write is *safe* but *inert*.

**Route 2 solves it. SOLVED AND IN PRODUCTION, see §6a.**

**Order targets differ by type** and this matters:

| Order | `Ship:52` | Destination is |
|---|---|---|
| Move | 1 | a `Planet`'s XYZ |
| Scout | 2 | a **`Sun`'s** XYZ (a system, not a planet) |
| Colonize | 3 | a planet's XYZ, **CONFIRMED**, matched a planet to 0.0000 |
| Attack | 4 | an enemy planet's XYZ, issued live, but see §4.4: issuing it through actuator L crashes the client, and Move (1) reaches the same outcome because combat is positional. On a fleet the order lives on `Fleet:52` |
| Conquer | 5 | an **enemy-owned** planet's XYZ, issued route-only through `exterminate.send_to` and survives turn boundaries, see §4.4 R-XTM-04 |

---

## 4. The rules

Evaluated once per turn in the order below. Earlier rules win; a planet or ship
claimed by one rule is not reconsidered by a later one. **Sustain runs before the four
X's**, a starving colony undoes any amount of expansion, but it is a precondition
layer, not a fifth strategy.

Each rule cites the fields it reads and the actuator it needs. Every rule carries its
implementation status on its heading, IMPLEMENTED with the function that runs it, or
[ ] NOT BUILT. **`ai.py`'s `RULES` list is the authority**; a heading here that disagrees
with it is a bug in this document. No rule is blocked on a missing actuator any more, which
is why the old `[BLOCKED: x]` marker no longer appears.

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
[ ] Never triggered in testing because income has been strongly positive all game
(11k-20k cash idle by turn 150), so this is untested rather than merely unwritten, the
failure it guards against has not been seen. It is also the trigger for R-XPL-01's Economy
phase, which therefore never fires either.

### 4.1 Explore

**R-EXP-01, Keep dedicated scouts in the field**
```
WHEN   count(our ships of role SCOUT) < SCOUT_TARGET   (initial: 3), not counting
       a scout slower than SCOUT_OBSOLETE (0.5) of the fastest scout design we can build
THEN   queue the FASTEST scout design (max ShipDesign:36) at a free shipyard
USES   D
```

**A SCOUT IS A CHASSIS AND ENGINES AND NOTHING ELSE.** This is how the game is
actually played, and it is worth stating as doctrine because the AI's own taxonomy
buries it: `SCOUT` is currently defined by *absence* (not colony, not troop, not armed),
so anything unarmed drifts into the role, including colony ships, which are slow,
expensive and needed elsewhere. A purpose-built scout is the opposite: strip the modules
and weapons, spend the whole hull on engines, and it is both the cheapest ship in the
empire and by far the fastest.

Speed compounds three ways, which is why this matters more than it looks:
* **Contact time.** Under fog of war, expansion and Exterminate are both gated on
  finding things. A galaxy is 108 systems; a colony ship doing the looking at 13.5/turn
  took ~500 turns to find one enemy planet.
* **It improves with research.** Engine ids come from the tech tree, Engine 1 arrives
  with Cold Fusion (id 3), which the AI now researches early anyway for its first weapon,
  and later engines from Quantum Fields, Gravity Controller, Anti-Matter Control and on
  up. A scout design should be REBUILT whenever a faster engine unlocks, not fitted once.
* **Cheapness means several.** Three scouts fanning out cover ground three times as fast,
  and losing one costs almost nothing.

[x] **Rebuild the scout design when a faster engine is researched.** Not by minting one
mid-game: `game_cycle.py` saves, closes and relaunches the client to add a design, and in
Single Player that client is the human's. Done instead the way `f1` and `b1` are: the galaxy
is seeded with `probe2` to `probe6`, the same Shuttle scout with each later engine (two where
the Shuttle's 120 space takes them), each locked until its engine is researched. R-EXP-01
already builds the fastest legal design, and a scout slower than half of it stops counting
toward `SCOUT_TARGET`, so faster ones get built while the old ones keep flying.

**Speed is total thrust over total hull units**, measured: probe1 15.43, the Colony Ship
13.5, `f1` 6.43, `b1` 3.38 and the engines-only shuttles below all match, the last having
no scanner. So `explore.design_speed` reads a design's speed from its parts when the lazy
stat block is cold, and the seeded probes make 15.4, 36.4, 68.5, 96.8, 98.5 and 144.0.

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

[x] **A warship at 10/turn is a 62-turn trip across this galaxy**, which is most of what
made Exterminate slow: war was declared around turn 100 and the fleet did not arrive until
turn 114, by which time the rest of the empire had moved on. Warship designs should spend
more hull on engines than the current one-engine minimum, and the §4.4 strength test should
account for the fact that a slow fleet arrives piecemeal. Done in v0.3: `cf1` and `cb1` carry
two Fusion Drives, and R-XTM-03 staggers departures so the fleet arrives together.

Three things fall out, and they change how the AI should play:

* **The engine TECH matters more than the engine count.** Going from two engine-0s to two
  engine-1s more than doubles speed, 15.4 to 41.0, while doubling the count from 2 to 4
  adds only a third. Cold Fusion, already researched early for the first weapon, is what
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

**R-EXP-02, Send idle scouts to the nearest unvisited system**
```
WHEN   ship S has role SCOUT and Ship:52 == 0
THEN   pick the Sun with the smallest distance from Ship:4/8/12 that is not in
       visitedSystems; write Ship:52 = 2 and the Sun's Sun:4/8/12 into
       Ship:48 +16/20/24; set Ship:76 = 1
NOTE   scout targets a SUN, not a planet, this is the one order that does
USES   E
```

**Armed and troop hulls never scout.** `explore.scoutable` drops both roles, so the
standing fleet is not dispersed across the galaxy before contact, and R-EXP-01 builds
dedicated scouts. Idle warships gather at a staging planet instead, see §4.4 R-XTM-08.

**R-EXP-03, Record arrivals**, SUBSUMED by `sensors.History._discover`
```
WHEN   ship S had order 2 last turn and Ship:52 == 0 now
THEN   add that Sun to visitedSystems; add its planets to the known set
USES   none (read-only)
```
There is no `run_exp03`: `_discover` runs every pass and adds any sun a ship is currently
inside (within 55.0), which covers arrival without needing to watch for the order clearing.

[x] **Discovery is sampled once per turn, so a fly-through is invisible.** A scout that
passes through a system and out the other side between two boundaries is never recorded as
having been there. Nothing has been seen to go wrong, but with fast scouts (61.5/turn
against a 39.6 orbit radius) it is arithmetically possible to skip a system entirely.
Done: `sensors.History._discover` counts every system within 55 of the line a ship swept
since the previous turn. Only across one turn, since over several the route may have turned,
and the last positions are in memory, so the first pass after a restart samples points.

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
**"Available" means available, and counting hulls instead froze expansion
outright.** The engine hands one of the two starting colony ships a SCOUT order
on turn 0 and never clears it, and a scout order on a 13.5/turn hull is a
commitment measured in dozens of turns. R-XPN-02 correctly skipped that ship as
busy; this rule counted it, saw 1/1, and declined to build a replacement. Measured
in the launcher's Single Player galaxy: two planets, an idle shipyard and 159
unowned planets, from turn 3 to turn 25, with **neither rule logging anything
wrong**, R-XPN-01 returned 0 before its first log line, exactly the silent-
deadlock signature §4.4 R-XTM-01 records.

It now counts only hulls that could take a colonisation order, using the same
`scout_finished` test R-XPN-02 uses, and says so when it is building a
replacement for one that is away.

**SOLVED, the recruitment stall is the "Inactivity" throttle, and it is a RAMP ON THE TURN
NUMBER.** The UI's Food Calculation tooltip names it: an `Inactivity: -N%` line that grows
over the life of a galaxy. It is `0x00538450`, and the formula is

```
pct = clamp( (currentTurn - offset - Owner:716) * 100 / divisor, 0, 100 )
      offset/divisor = 72/100 when GetGameOption(4)->[+0x30] == 0, else 150/150
```

On this galaxy `Owner:716` is 0 and the option is 0, so **pct = turn - 72**: first visible
around turn 73, 88% at turn 160, and **100% at turn 172, after which recruitment is
impossible at any slider setting.**

It is applied TWICE, at different weights, which is why the arithmetic never closed:

* **to food production, at HALF weight**, inside `0x004F0800`, the tooltip's `-47%` on a
  648 production figure;
* **to the military share, at FULL weight**, in the splitter `0x004F1580`, and *after* the
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
because it also reduces food, true, but at *half* weight and *before* the split, so it
does exactly that.

**NEUTRALISED, by setting the flag the engine already looks for, not by forging its
timer.** The engine refreshes the baseline itself, in `0x00543B90` (turn stage 26):

```
cmp  byte ptr [esi + 0x2fc], bl    ; Owner:712, the ACTIVITY FLAG
je   skip                          ;   zero -> no refresh
mov  eax, [0x008578E8] ; dec eax
mov  [esi + 0x300], eax            ; Owner:716 = turn - 1
skip:
mov  cl, byte ptr [esi + 0x304]    ; Owner:720, next turn's flag
mov  byte ptr [esi + 0x2fc], cl    ; Owner:712 = Owner:720
```

`Owner:720` is the "this player is present" signal and `Owner:712` is it latched for the
current turn, so **setting `Owner:720 = 1` once is self-sustaining**, the engine copies it
forward and writes its own value into `Owner:716` every turn thereafter. `set_activity.py`
does exactly that and nothing else.

Writing `Owner:716` directly would have worked too and is the wrong thing: it forges the
engine's own bookkeeping with a number we invented. Supplying the input a connected client
supplies, and letting the engine compute, is both smaller and honest.

**It marks EVERY civ, and the tool refuses to do one.** The decay models an ABSENT player
and in an AI-vs-AI galaxy nobody is absent, but clearing it only for ours would be a plain
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
and the engine CANCELS the order at the next turn boundary, it frees the order object
and clears `Ship:48`, so a controller that does not check will re-issue an order every
turn forever and leak one order object each time. Observed over twelve turns on a
colony ship whose order was otherwise perfect. Crew comes from conscripting a citizen
for cash, or from raising the recruitment rate (`Planet:100` byte 3) and waiting for a
unit to train, then loading crew onto the ship. A colony ship needs only the minimum.

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
**Distance is charged in turns, not raw units.** The first draft charged raw distance and
was badly miscalibrated for the real galaxy: coordinates run to ~1130, the two closest
suns are 111 apart, and a colony ship moves 13.5/turn, so the nearest neighbouring system
is already an 8-turn trip while `space` only spans 156..450. Raw distance swamped space
about 4:1 and the rule degenerated to "always take the closest rock". At `TURN_WEIGHT =
10` a measured galaxy ranks home-system planets 288/260/204 at 2-3 turns while a bigger
but distant planet (space 282, 15 turns) scores 132, close enough to compete if it is
good enough, which is the intended behaviour.

`space` is used rather than `max_pop` because `max_pop = space/10` truncates and throws
away the tie-break between, say, 288 and 281.

Colonize (`3`) is **CONFIRMED**: a real colonize order read `3` at `Ship:52` and its
`+16/20/24` matched the target planet's coordinates to 0.0000, with the nearest sun 28.2
away. The earlier inference from move and conquer was right.

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

[x] **This is the rule that starves Exterminate, and the mechanism is not income.** With
531 unowned planets in the galaxy R-XPN-01 always has a target, so colony ships are queued
forever and compete for the one shipyard. Ships get first refusal over facilities in the
rule order, but *within* ships the order is warships → colony → scouts, so expansion
outranks scouting indefinitely. The trigger above (income) never fires because income stays
strongly positive; the real constraint is SHIPYARD TIME and crew, not cash. Either add a
second shipyard target, or cap concurrent colony ships, or charge expansion against the
fleet the AI is trying to build. Done as the war yard above; income plays no part.

### 4.3 Exploit

**R-XPL-01, Job mix by phase**

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

**R-XPL-02, Facility build order**
```
priority, first match among types research has unlocked (`exploit.buildable_types`):
  farm 0          if there is none, the store is under 25%, or the surplus is
                  below GROWTH_MARGIN_PER_POP per citizen
  military camp 6 if there is none and pop >= 6
  light turret 9  if turrets < BORDER_TURRETS and a visible rival planet is
                  within BORDER_DIST
  shipyard 2      if there is none here, the civ has < MAX_SHIPYARDS, and
                  pop >= SHIPYARD_POP
  factory 1       if there is none and workers >= FACTORY_WORKERS
  university 4    if there is none and pop >= 8
  banking ctr 16  if there is none and pop >= BANK_POP
THEN   Planet:284[P] = that id, Planet:344[P] = 1
USES   B, C
```
`exploit.wanted_facility` is the authority. The defence agency is not built. Factory and
banking center were added with the cash policy: "scaled correctly, there is always something
worth building", and a planet that had built a farm, camp and university otherwise sat idle.

#### Cash policy (Oct 2026), `cash.py`

Written from the user's own play of the original game, which overrides the invented floors
and budgets the rules had before:

* **Going negative is the danger.** The game sells the civ's ships and facilities until cash
  is positive again. So a **reserve** of `RESERVE_TURNS` turns of upkeep (floor `MIN_RESERVE`)
  is never spent on anything discretionary. Upkeep is estimated as facility upkeep from the
  live definition table (`facilities.py`, `+0x18`) times the counts on our planets, plus
  `ShipDesign:48` for each ship, plus `UNIT_UPKEEP` (3) per stationed military unit, the
  value the memory report reads at `Planet:168` record `+0`. Garrisons were missing at first
  and a military-heavy civ read about half its real upkeep. **The facility part is
  confirmed exactly** (Oct 2026), on the `deficit` checkpoint with no AI running: one turn
  moved cash by -31 twice over; with the military camp sold first it moved -19, with the
  light turret -23, with both -11, so +12, +8 and +20 against table upkeeps of 12 and 8.
  [ ] The ship and garrison parts are not checked. `UNIT_UPKEEP` in particular rests on a
  reading the annotations have since corrected: `Planet:168` record `+0` is a job id, which
  happens to be 3 too, so the real per-unit figure is unknown.
* **Order of claim on the cash above the reserve:** crew for a ship that needs to fly
  (R-XPL-09), then hurrying (R-XPL-08) by tier: military builds while at war, expansion
  (colony ships, shipyards), everything else. Scans are built like production, so they are
  hurried like anything else.
* **A warship built outside a war is not drafted for.** Recruitment crews it for free in
  time; cash goes to ships that need to fly now.
* **Wealth mode is the stopgap for low income** (R-XPL-03), and the late-game answer for a
  dominant civ with nothing left to build. Bankers are not the first stopgap: they are for a
  population that is thriving and producing plenty, and never the whole population.
  Thriving is approximated as `BANKER_POP_SHARE` (75%) of the population cap, a rough
  estimate of when a player starts using them.
* **In a real pinch bankers go on small planets too.** When the treasury is short
  (`cash.short`) and the thriving planets are already at `BANKER_MAX`, or there are none,
  R-XPL-01 adds one banker a pass on the non-thriving planet with the most spare workers,
  never below `WORKER_FLOOR`: "one more push to stay positive". Those bankers are held while
  income is at or below `COMFORTABLE_INCOME` and go back to work above it, so the push does
  not undo itself the next pass. [ ] Exercised against stubs only. Replayed on the `deficit`
  checkpoint it never fired, correctly: while cash was short no planet had more than
  `WORKER_FLOOR` non-farmers to spare, the garrison having taken them. Whether a pinch should
  be allowed below the floor is open.
* **Diplomacy** spends cash in the original game, but not by an AI, so the policy leaves it
  out.
* [ ] **Buying resources on the market** when no planet of ours produces one. The sensors
  exist (`Owner:1128` stocks, prices from `Owner:916`, memory report "Cash and the resource
  market"); the purchase itself has not been located, so this needs a new actuator first,
  found by watching a human buy at the client.

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

**Measured against a real deficit (Oct 2026).** On `duel_fixture2.dat` at turn 36 the user
recruited 21 garrison units on DemoPlayer, leaving 8 citizens: income went from +210 to -31
with 227 cash (checkpoint `deficit`). Replayed twice from that checkpoint, 15 turns each:

| | R-XPL-03 on | R-XPL-03 off (`--without R-XPL-03`) |
|---|---|---|
| lowest cash | 83 | 30 |
| income at turn 51 | +117 | +67 |
| cash at turn 51 | 627 | 386 |

Both recover, because population regrows and income is per citizen, but the stopgap held the
trough well clear of zero and roughly doubled income by the end. It switched two of three
planets on turns 41 and 42; it could not switch earlier because every planet was building
something more than half done. No bankers were placed, correctly: no planet was near its cap.

Two things this showed about the economy, both worth knowing for every cash rule:
* **Income is mostly per citizen, whatever the job**, about 8 a citizen a turn early on.
  Making every citizen a farmer moved income only from +2140 to +1737 on the turn-262 game.
  Overbuilding ships or facilities cannot realistically cause a deficit, since each costs
  5-50 a turn; losing population while keeping the buildings and garrisons does. That is
  R-XPL-07's motivating case.
* **Wealth on a small planet is worth little**, about 15 a turn on a 10-pop homeworld at
  turn 34. It is a stopgap in scale as well as in intent.

[x] **Stopgap planets were held until income reached `WEALTH_RELEASE_INCOME` (150).** In the
measured run both were still in wealth at turn 52 with income +127, while R-XPL-02 had farms
to build. 150 suits a large empire and is high for a three-planet one. Done: a stopgap planet
is released, still one a turn, once income is positive and cash is back above the reserve.

**The engine puts a planet into wealth itself when its build completes**, measured on the
turn-214 fixture: planets the rule never switched read `PilingUpWealth` the pass after a
hurry finished them. So `Planet:296 == PilingUpWealth` does not mean "our stopgap", and the
first version of this rule held every just-finished planet in wealth, releasing one a turn.
Only planets R-XPL-03 switched itself are stopgap planets (`hist.wealth_stopgap`); any other
wealth planet is either given its next build by R-XPL-02 or, with nothing worth building,
left in wealth.

**R-XPL-04, Always be researching**
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

[ ] **The cheapest-first tiebreak is the wrong direction, for a fixed set.** Because the
multiplier is the completed count *at the moment of purchase*, a fixed set of techs costs
least when the HIGHEST-factor ones are bought FIRST. But the tiebreak does not act on a
fixed set: it changes which prerequisites complete and so which branches open. Simulated
over this table with no boosts (Oct 2026), costly-first paid **+59% for the first 10
picks**, -4% at 20, -5% at 30 and **+12% at 45**, and bought a different set at 10, 20 and
45. So the switch, `exploit.RESEARCH_COSTLY_FIRST`, exists and is OFF. A real test is a
`metrics.py` comparison on `duel_fixture2.dat`, since total price ignores how soon each
capability arrives, which is what actually matters.

[ ] **One cost reading does not fit**, Quantum Fields (factor 2.8, 4 completed) should
show 8960 and was reported as 3200, which is exactly `800 * 1.0 * 4`, the price of
Advanced Magnetism, a member of its OR-prerequisite list. Two candidates: a misread, or
the UI displaying the next step of a chain rather than the target. A third, raised by the
user and worth taking seriously: **this civ's tree is inconsistent** because it holds
Astro Engineering without Advanced Magnetism, so any path-finder walking prerequisites may
behave oddly on exactly this node. Free test: the number should become `2240 * 5 = 11200`
once the current research completes, and `800 * 1.0 * 5 = 4000` if it is showing a
prerequisite instead.

**R-XPL-05, Recruitment**
```
WHEN   a ship parked at P needs crew and P has a military camp
THEN   raise the rate toward max_recruitment(P) if P is comfortably fed,
       otherwise to RECRUIT_RATE_LEAN so growth is not starved
WHEN   nothing at P needs crew
THEN   lower it to 0, the diversion costs food and buys nothing
USES   G
```
**The rate is a SPLIT, not a throttle.** It is the percentage of the planet's food
*surplus* diverted into the military store, and the remainder goes to citizen growth,
confirmed both in the splitter `0x004F1580` (`foodShare = surplus - militaryShare`) and in
the UI, which shows 60% to military and 40% to growth on a one-camp planet. So recruiting
always costs growth directly, which is why the rule backs off on a poorly-fed planet.

**The ceiling is per planet: 40 with no camp, +20 per camp.** We capped every planet at a
flat 40, so a planet with a camp was recruiting at two thirds of the rate a player would
have used. The engine applies whatever byte is written with **no clamp of its own**, the
getter is `movsx eax, byte ptr [ecx+0xf]` and nothing more, so honouring the ceiling is a
§1.1 obligation rather than a safety check. The `movsx` also means the byte must stay below
128, or the rate reads negative and the military store would count *down*.

**R-XPL-06, Load crew onto a waiting ship**, IMPLEMENTED in `exploit.run_crew_load`
```
WHEN   a ship in orbit of owned planet P has fewer crew than ShipDesign:76
       AND P has that many trained military units stationed
THEN   move the units from P onto the ship
USES   load_crew
```
[ ] **Written in code before it was written here**, it, and R-XPL-08 below, were never
specified. Both now exist; this entry is the spec catching up.

[x] **No priority between competing ships.** `run_crew_load` walks `owned_ships` in scan
order, so a colony ship wanting 2 crew and a scout wanting 1 compete on iteration order
rather than on what the empire needs. With recruitment at ~1 unit per 10 turns this decided
which rule made progress, and nothing chose it deliberately. Done: R-XPL-06 and R-XPL-09
walk ships in `exploit.CREW_PRIORITY` order, warships, troop ships, colony ships, scouts.

**R-XPL-08, Hurry a build with surplus cash**, IMPLEMENTED in `exploit.run_hurry`
```
WHEN   a build is >= 50% done (the engine's own limit), Planet:300 == 0, income >= 0
THEN   call HurryProduction by tier (military at war, expansion, other), cheapest
       first within a tier, spending everything above cash.reserve()
USES   K
```
[x] **The budget rule was arbitrary**, a 5000 floor and half of the remainder per turn. It
routinely declined every eligible build while sitting on 20k cash. Done: the cash policy
above. It runs after the crew rules now, so conscripts are paid for first.

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
**This replaces a timer with a purchase, and the timer was setting the pace of the whole
game.** R-XPL-05 raises the recruitment rate and waits; recruitment delivers roughly one
unit per ten turns, so a scout finished on turn 30 sat on the pad until turn 40 and
Explore → contact → Exterminate all ran at that speed. Measured on the fresh galaxy: both
civs reached turn 41 with a second scout built and grounded while holding 6,121 and 3,144
cash with nothing to spend it on. A conscript costs **115**.

The price is `5 * (T + k) + 105` where `T` is the civ's military across the WHOLE empire,
counting ship crew, so it is cheap early and rises as the fleet grows, which is the right
shape for a rule that exists to unblock the first ships.

**ONE per pass per planet**, and that is correctness rather than caution: the engine erases
the drafted record and memmoves the tail down, so every citizen index above it shifts and a
second index taken from the same snapshot names a different citizen than the one it was
chosen for.

**Never drafts a farmer, EXCEPT where the colony has no use for them.** The blanket rule
was right in spirit and wrong in the case that mattered. Measured live: a colony ship sat
at a 23-citizen all-farmer planet for a hundred turns while the HQ two systems away held
three spare citizens.

**Two distinct conditions, and CONFLATING THEM GETS THE COMMON CASE BACKWARDS:**

* **At the population cap** (`pop >= space/10`). Growth has stopped, so the drafted citizen
  is replaced by growth that had nowhere to go, the cost is transient. Such a planet is
  typically running a food **SURPLUS**, piling up against a cap it cannot use. This is the
  case that stranded the ship, and a rule gated on famine would never have fired on it.
* **Food-negative with everyone farming.** Production is below consumption, so the average
  farmer eats more than they grow and removing one improves the balance.

`allow_farmers` therefore means "this colony can spare one", NOT "this colony is starving".
`MIN_COLONY_POP = 4` still floors it, because a planet that cannot work is worse than a
ship that cannot fly.

[x] **The loader waited a turn.** R-XPL-09 runs before R-XPL-06, but the pass's snapshot
was taken before the draft, so the new unit was invisible until the next pass. Done: the
loader reads the garrison live (`Actuator.live_military`). The gap was narrower than this
item said, since a hull with an empty crew vector already took its crew straight from the
draft (N′); only an empty hull whose N′ draft failed went through the garrison.

[x] **A partly crewed hull was drafted for and never loaded.** Both ways crew gets aboard
(`load_crew`, `conscript_to_crew`) refuse a ship whose crew vector is already allocated, so
a crew gap on such a ship is permanent. R-XPL-05, -06 and -09 still counted it, which meant
a troop hull that had landed its troops made R-XPL-09 buy conscripts into the garrison
every pass for a load that always failed, and made R-XTM-07 refuse to build a replacement
because a troop hull was "waiting for crew". Done: `exploit.crew_gap` is zero for an
allocated crew vector and every crew rule and `awaiting_crew` uses it; R-XTM-07 does not
count a spent troop hull toward `TROOP_TARGET`, so each invasion now costs a new hull and
the spent one is parked at home by R-XTM-04. Confirmed live on the turn-214 fixture: a
`t1` took planet #120, and for the ten turns after it no conscript was bought for it and
R-XTM-07 wanted a replacement.

[x] **The invasion landed all three troops, not one.** The hull arrived with 5 aboard, 2
to fly, and left with 2, so R-XTM-06 had nothing to garrison. Resolved: it is the earlier
reading that was too narrow. The controlled pair in §4.4 had 3 aboard over a flight crew of
2 and landed 1, which also fits "lands everything above `ShipDesign:76`". So the capture
lands every troop and they stay as the garrison, `INVASION_TROOPS` (3) is the garrison size,
and R-XTM-06 only finds work for a second hull reaching a planet the first has taken.

[x] **`DRAFT_CASH_FLOOR` (500) was invented**, like R-XPL-08's budget. Done: drafting now
spends only cash above `cash.reserve()` (§4.3, Cash policy).

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
`SELL_ORDER` is a judgment, not a measurement, since no per-facility contribution is read:
scans and science first (research waits), then hyperspace and command facilities, then
defence, mines, the military camp and propaganda, and production last. It replaces the
specified "cheapest contribution first" and "only on a planet that is part of the
problem", neither of which has a sensor. The rule runs after R-XPL-01's bankers and
R-XPL-03's wealth, so it acts only on a deficit those could not close. It sells against
the per-type upkeep `cash.upkeep` uses, which is confirmed (§4.3, Cash policy), and a
deficit it does not close is met by the next sale a turn later.

**Measured on the `deficit` checkpoint** with R-XPL-03 off (Oct 2026): income read -62,
-30, -20, -27 from turn 38, and on turn 41, with cash at 88, the rule sold the HQ's light
turret and military camp, all it was allowed to sell there, 20 upkeep, for 95. Income then
read -14, -4, +24, +44 and +140 by turn 56, with R-XPL-01's bankers rising alongside, so
the recovery is not all the sale's. Cash went 227, 165, 135, 115, 88, then 169 after the
sale, and never below 0, so the game sold nothing of its own choosing.
The motivating case is not tidying up: a civ that **loses the planets funding its
expensive buildings** faces a cascade, upkeep it can no longer pay against income it no
longer has. Liquidating is how a human trades a long-term asset for immediate solvency
instead of surrendering. It is the economic mirror of R-XTM-05 retreat.

[x] **No longer blocked: `cash.upkeep` reads per-type facility upkeep** from the
live definition table and multiplies by the counts on each planet, which answers "what does
each facility cost per turn" directly, and the second way in below confirmed it. The
original blocker, kept for the record: **blocked on an upkeep sensor.** The trigger needs
to know what each facility costs per turn, and we cannot compute it. `incomeRate` is
derived by differencing `Owner:8` across turns (§2.5), which gives the *net* figure and
cannot attribute the drain to individual buildings, so "which facility do I sell first"
has no answer yet. `ShipDesign:48` gives ship upkeep; there is no known facility
equivalent. Two ways in, neither attempted:
  * find the per-turn upkeep accumulator in the binary, the way `SellFacility` was found
    by anchoring on the treasury field
  * measure it: sell one facility on an otherwise-idle planet and difference `incomeRate`
    across the boundary, cheap now that actuator J works, but only yields the types we
    happen to own

[ ] Decide whether selling should also be available as a *funding* move (sell to afford
something better) or strictly as a solvency backstop. Only the latter is built.

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
**War is checked in two independent places**, so an Attack order without it accomplishes
nothing: at order-issue time by `0x00477D90` (which refuses Conquer and Bio-bomb outright),
and again inside all three battle phases by `IsAtWarWith` `0x00535080`.

`Owner:248` is the relation container, one record per civ, code at `+0x04`:
**0 Neutral, 1 War, 2 Cease-Fire, 3 Peace, 4 Alliance** (from the game's own string table at
`0x00776DA4`).

**Static analysis suggested we might already be at war and need none of this. Checked live,
we are not.** First contact (`0x00534F90`) only defaults to War when
`GetGameOption(2) > 1`; on this galaxy that option reads **0**, which takes the branch that
leaves both civs **Neutral**, matching the user's report that a fresh game requires an
explicit declaration. `GetGameOption(29)` (force initial war) and `GetGameOption(31)`
(force combat hostility) also read 0. Read all three before assuming anything about the
starting relation; a differently configured galaxy genuinely would start at war.

**The relation record is SYMMETRIC and is written as such, DONE.** `0x0055BD40` writes the
same code into *both* civs' `Owner:248` in one call, and `actions.declare_war` does the same:
the live log shows two writes, `'GoodGuy' -> 'BadGuy'` and `'BadGuy' -> 'GoodGuy'`, both
Neutral → War. Since the battle phases query *a* civ's map, writing one side only would have
made combat depend on which side was asked.

[x] **NO REPUTATION COST IS APPLIED, so declaring war is currently FREE.** Resolved: the
declare-war validator `0x004BDE50` shows that declaring costs reputation only when it breaks an
active treaty, and the AI makes no treaties. Otherwise the figure the UI shows is a forecast:
"You would gain/lose %s reputation for actually attacking this player", applied by the battle
code at the first battle, which the battle reports confirm (the defender gained +1.36).

[x] **Prefer the command path over a raw write, and this is a fairness decision, not a
technical one.** The `NWTR` handler `0x0056F310` validates nothing, but the surrounding path
generates a **news item** ("Declares War") and applies a **reputation** change that the UI
quantifies before confirming. A raw relation write is silent: no notification to the victim,
no reputation cost. That is the first case found where bypassing the UI is an *advantage*
rather than merely unvalidated, so it is squarely a §1.1 violation. All the human-facing
rules live in the UI validator `0x004BDE50`, build-up-phase protection, teammate
protection, and the confirmations.

Done: `actions.declare_war` now does what the declare-war button `0x00442A70` does. It builds a
`Treaty` proposal (`0x00559A60`), names the declarer and the target, sets kind 1
(`0x0055AD90`) and applies it with the offline applier `0x0055C3E0`, which registers the
`Treaty` at `0x0086ED6C`, makes first contact and writes War into both relation records
(`0x0055BD40`). The validator itself shows message boxes, so it is not called from a remote
thread; its build-up check is reproduced in `remote.war_protection`: when game option 4 has
`+0x30` set, a civ that has played fewer than `[0x0076F378]` turns (`0x0052AC40`) may not be
declared on. The teammate rule does not arise, since the AI joins no team.

Checked against a declaration made in the UI of the same client: the registered `Treaty` is
field for field the same (kind 1, type 3, -1, the turn at `+0x20` and `+0x24`), and neither
produces a news item, on the turn or after two boundaries. The "Declares War" news belongs to
the online path, where the declaration goes to the server as `NWTR`.

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

**FIXED, the trigger is CONTACT, and gating it on WAR was a silent deadlock.** The
implementation opened with `if not enemies(): return 0`, which is narrower than the rule
above ("floored at 2 **once contact is made**") and closes the Exterminate loop on itself:

```
R-XTM-01 queues no warship until at war
R-XTM-00 declares no war until a CREWED WARSHIP exists
```

R-XTM-01 is the only rule that queues an armed hull, so from a Neutral start neither could
ever fire and the AI could not reach a battle at all. **Nothing in the logs said so**,
both rules return 0 before logging, so a game with no Exterminate output looks identical to
a peaceful one. Measured over a full run: at turn 200 the civ held 9 planets, score 33,362,
cash 11,229, `f1` and `b1` both ready and 105 of 108 systems explored, and R-XTM-03 had
never once been reached. `contacted()` now supplies the contact set and
`enemies() or contacted()` is the gate. Attack itself stays war-gated in R-XTM-03, which is
correct, the engine checks `IsAtWarWith` in all three battle phases.

**FIXED, the discovery set now survives a restart, which this rule depends on.**
`sensors.History.discovered` was rebuilt each pass from where our planets and ships are *now*
plus scan reports, and never written down. A fresh process on the same galaxy recovered 7
systems out of 105 explored, so contact, and therefore the whole Exterminate chain, was
lost by restarting the controller alone, with nothing changed in the game.

It is now persisted to `ai_player/state/discovered_<galaxy>_<civ>.json`, keyed by a galaxy
FINGERPRINT (sun count plus a hash of every sun's id and rounded position) as well as the civ
name, because one machine plays several galaxies and a map belongs to one player. The file is
loaded before `_discover` runs so a reload and this pass's sightings union, written only when
the set actually grew, and replaced atomically so a killed loop cannot truncate it. Verified
across two processes: one saved, the next reloaded and `contacted()` returned the rival.

Persisting our own discoveries is the OPPOSITE of the omniscience worry in §6.1, the bug was
handing the AI a fresh fog of war on every restart, which it then re-explored at full speed.

### The AI was never slow, the TURNS were bursting (Aug 2026)

**Read this before optimising anything here.** Decision passes were landing 2-3 turns
apart and three separate explanations were proposed, asserted, and measured wrong in turn:
engine calls, then the memory scan, then the snapshot phase. What the numbers actually say,
all from a working build:

```
scan     0.1-0.2s warm, 1.25s cold (126 MB, ~200 objects)
rules    <0.05s per pass, WITH ~20 engine calls in it
engine   ~2.5 ms per remote-thread call
```

A pass costs about **0.2 seconds**. It was never the bottleneck, and the "~68 seconds per
pass" figure an earlier draft of this section carried was not measured, it was
`2.73 turns x 25s` computed while assuming the AI was at fault. Do not repeat that: the
per-pass timing is printed every pass now, so read it.

**The real cause is `advance_turns.py` losing a tug-of-war over the turn length.** The
engine rewrites `0x0080AA08` back to 3600 at every boundary, and that script re-asserts the
short value only AFTER it sees the counter move. So the engine spends most of each interval
on an hour-long turn and then resolves SEVERAL at once to catch up on elapsed time, the
burst §1 already warns about, arriving from the test harness rather than from a sleeping
host. Measured on one galaxy:

| driver | turns with a decision pass |
|---|---|
| `advance_turns.py 12` | 247, 250, 252, 255, 257, 259 |
| `ai.py --drive 12` | 260, 261, 262, 263, 264, 265, 266, 267 |

`ai.py --drive` re-asserts the length *during* the wait, which is the whole difference. **Use
it for any run whose per-turn numbers matter**, and treat `advance_turns.py` as a tool for
advancing a game with no controller attached.

### Engine-call caching (Aug 2026), worth having, not a performance fix

Introduced while chasing the phantom above, and kept on its own merits: 138 of 571 calls in
one run were the firepower getter, which spends three remote threads on a number that
cannot change unless a design is refitted, and nothing here refits designs. Fewer remote
threads into a live client is worth having for its own sake. Measured hit rate in ordinary
play: **23-29%**.

**Two cache lifetimes, because these are two kinds of fact.** Design-level stats (speed,
firepower, min crew) are functions of fitted parts and are read once per process.
Everything else is true of one turn only and is dropped by `Remote.begin_pass()` at the top
of every pass, so a stale reading can never outlive the snapshot it belongs to. Anything
that *mutates* the quantity clears its own entry, `invalidate_draft_cost` after a
conscript, because within a pass we are the thing changing the price.

**Nothing that writes is cached.** A cache on a mutating call is not a speed-up, it is a
silently skipped action.

[ ] **A dead engine handle looks exactly like an idle AI**, and it fooled two rounds of
analysis here. A bad edit left `Remote.__init__` never opening its handle; every engine call
then raised, every rule caught its own exception and moved on, and the loop reported clean
fast passes while doing nothing at all. The logs showed 0% cache hits and "rules 0.0s",
which read as *healthy*. `Remote` now proves itself on construction, see `_selftest`, so
the failure is loud instead of silent.

### Actuator L crashes the client, R-XTM-03 and R-XTM-05 do not use it (Aug 2026)

**`set_command` kills the client at the NEXT TURN BOUNDARY, for Move as well as Attack.**
The order is written cleanly and the pass completes; the engine dies resolving the turn.
Measured from `client/armed.dat` (turn 38, at war, one crewed `b1`), one boundary per trial:

| what was issued | survived the boundary |
|---|---|
| `set_command(4 attack)` | **no** |
| `set_command(1 move)` | **no** |
| `create_order` + `retarget_order(1 move)` | **yes**, moved 9.5 units, exactly `ShipDesign:36` |

So it is the `ShipCommand` / `Ship::SetCommand` pair, not Attack, and not the order TYPE.

**This also refutes the premise actuator L was built on.** A route-only order leaves `Ship:56`
as the static null node `0x00857C54` and the ship navigates correctly regardless, so
`Ship::HasActiveTargetedOrder` gates something other than movement. Nothing the AI does needs
`Ship:56`: combat triggers on CO-LOCATION and the battle driver `0x00523AC0` never reads the
order type at all, so a Move order that delivers the hull does the whole job.

Both rules now go through `exterminate.send_to`, which reuses `Ship:48` when the ship already
has one, verified over 8 turns as **zero** order allocations and **zero** ShipCommand
allocations, with the bomber closing 624.3 → 544.2 on BadGuy's HQ.

[ ] **Why SetCommand is fatal is still unknown**, and it is the only known way to populate
`Ship:56`, so the function is kept and marked do-not-use rather than deleted. Two unreconciled
observations for whoever picks it up: in the harness SetCommand appears to be a NO-OP (the ship
still read type 1 with a null `Ship:56` afterwards), while in the 400-turn run at turn 405 it
DID apply, type 4, real node, and crashed anyway. The difference was that order object had
been created a turn earlier rather than in the same pass.

**~~ATTACK ORDERS NEED `Ship:56`~~, WRONG, and it was the premise for actuator L.** The
claim was that `Ship::HasActiveTargetedOrder` (`0x004D9880`) requires order type 1/4/7 **and**
a non-null `Ship:56`, so a coordinate-only Attack order would be inert. Measured: a route-only
Move order leaves `Ship:56` as the static null node `0x00857C54` and the ship navigates
correctly regardless. Whatever that function gates, it is not movement, and it is not combat,
the battle driver `0x00523AC0` buckets by position and never reads the order type. Superseded
by the actuator-L section above; kept here only so the old reasoning is not re-derived.

[ ] **What `Ship::HasActiveTargetedOrder` actually gates is still unknown.** It is called
from somewhere, and `Ship:56` is populated by the UI path, so both exist for a reason we have
not found. Worth knowing before assuming a null `Ship:56` is harmless in every situation
rather than just the ones tested (Move and Attack toward an enemy planet).

**R-XTM-02, Defend**, IMPLEMENTED in `exterminate.run_xtm02`
```
WHEN   at war, and enemy ships or fleets are in scan range of our planets or ships,
       grouped by the owned planet nearest each, and a group is a fleet, two or
       more ships, or any hull above shuttle class
THEN   hold idle warships already at that planet, and send idle or staged
       warships that can reach it within DEFEND_REACH turns
USES   M
```
**Only what the map shows is used.** A player sees another civ's ships only within scan range,
about 30 units for a planet (`PLANET_SCAN_RANGE`, measured on the stock client) and the
scanner's range for a ship (30 to 100, from the component table). The map shows a ship's hull
class, and for a fleet every class in it but no count. A ship that joins a fleet stops existing
as a `Ship` object, so `gamestate.Fleet` reads the fleet's members at `Fleet:116`.

**A lone shuttle is ignored** because it may be a probe or a decoy, and a rule that answered
every shuttle could be led around by one. Arriving is the whole action: the owner's ships at a
planet always defend, and ships meeting in open space never fight without an Attack order.

[x] **Defence did not weigh the threat.** It sent whatever could arrive, whatever the
attacker's strength. Done: the threat is the newest fresh battle report (`intel.MAX_AGE`) of
that civ's armed ships in a battle away from its own planets (`intel.Intel.fleet`). At home
the owner's side counts its turrets' firepower: `BadGuy`'s HQ read 400/100, a light turret's,
while the planet's own block carries only hitpoints. An unarmed side (a probe) says nothing
about a fleet and is passed over too. When `margin()` of the defenders against it is below
`DEFEND_MARGIN` (1.0) nobody is sent, and ships already at the planet are not held, so
R-XTM-08 gathers them at the staging planet. With no such report the defence goes
ahead as before. A report is about a battle, not the fleet now in view, so this is the last
thing the civ was shown, and it goes stale when the enemy adds or splits ships.

**Enemy strength comes only from battle reports** (`intel.py`). A player sees no enemy
firepower, hitpoints, planetary defence or stationed troops by selecting a ship, fleet or
planet; only a scan or a battle report reveals them. Battle reports are news items in the
civ's own `Owner:344` vector, 120-byte records with the turn, the position and a marked-up
body that gives each side's light, heavy and bombing firepower, ship hitpoints, planet
hitpoints and damage. Each side receives its own copy. Stationed troops are not in it.

**The game empties a report's text a few turns after the battle**, leaving the turn and the
position: both string pointers read null. Each pass copies every still-readable report into
`sensors.History.intel`, which is saved with the discovery set, so a probe's sacrifice is not
lost when its report is.

A planet one of our ships sits at, undamaged, at war, with no report that turn, is recorded as
quiet in `sensors.History.quiet`: turrets fire at any hostile ship in orbit. Intel older than
`intel.MAX_AGE` turns counts as unknown.

[ ] **Switch to scan reports once scans are built.** Probing is how players learned enemy
strength before scans; once the AI can run a planetary or fleet scan, intel should come from
`Owner:320` where a scan is available and R-XTM-09 should stop sacrificing hulls there.

[ ] **Sacrificial probing is a legitimate but gamey source of intel.** A civ that throws many
cheap hulls at every target never needs a scan. `PROBES_IN_FLIGHT` limits the AI to one at a
time; decide later whether the game itself wants a limit.

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

**A raid departs in stages so the fleet arrives together.** Speeds differ (`b1` about 3.4 a
turn, `f1` about 6.4); sent together, an `f1` fought alone for three turns before its `b1`
arrived. The raid fixes an arrival turn from the slowest ship's ETA, `sensors.History.raid`,
and each faster ship waits at the staging planet until its own ETA lands it on that turn.

**Two fleets at war pass each other in open space.** Ships fight there only when one side
has an Attack order, which the AI cannot give, so R-XTM-02 defends by holding the planet.

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

**ORDER TYPE 5 IS SAFE, issued for the first time, Aug 2026.** The worry recorded below was
that Conquer might do what Move and Attack did through actuator L and kill the client at the
next boundary. It does not. A `t1` troop hull was given order 5 through `exterminate.send_to`
(route-only, `Ship:56` left null) and the client resolved eight boundaries with the order
live, navigating at its design speed the whole way.

**AN INVASION COSTS ONE UNIT ABOVE THE FLIGHT CREW, CONFIRMED, and by a controlled pair.**
Two identical `t1` hulls were sent at the same planet:

| | #704 | #705 |
|---|---|---|
| aboard on arrival | 2, exactly `ShipDesign:76` | 3 |
| result | seven turns parked, order still 5, nothing | **captured on arrival** |
| aboard afterwards | 2 | 2 |

So `ShipDesign:76` is the crew needed to FLY, and an invasion spends one record ON TOP of it.
With only the minimum the order stays live and unfulfilled, the engine is waiting for troops,
not refusing the order. That is why the earlier attempt looked like a failure and was not one.

**What a capture does**, read off the planet before and after:
* ownership moves to the attacker, and the **population survives intact** (17 before, 17 after)
* the landed unit becomes the garrison: `Planet:168` went 0 → 1
* the ship is **NOT consumed**, unlike a colony ship it stays, minus the one unit
* the order clears to 0, the same way a completed Move does

[ ] The target's military camp was in its facility map before the capture and gone after
(`{6:1, 4:1, 0:7}` → `{4:1, 0:7}`). Fifteen turns passed between the two readings, so this is
NOT established as a consequence of conquest, check it across a single boundary before
believing it.

**Crew and troops share one container.** A troop hull was diffed against a scout across the
whole `Ship` window: there is exactly one 16-byte-record vector, `Ship:124/128/132`, holding
two records of job id 3. There is no second "troops" vector, which fits the report's finding
that a citizen, a stationed military unit and a ship's crew member are the same record.

**There is no separate troop-loading action in the UI**, confirmed by the user at the
client. Crewing and loading troops are the same thing, which is why they share the
`Ship:124` container. So no engine call has to be found for this: the AI only has to load
MORE than the minimum.

[ ] **`load_crew` cannot top up a crewed ship, so the extra must go in at FIRST load.** It
refuses when `Ship:124` is already a pointer, by design, growing the engine-owned buffer
would mean freeing memory we did not allocate. That is not a blocker now that the requirement
is known, because it allocates the whole buffer in one call: R-XPL-06 just has to ask for
`ShipDesign:76 + troops` on a TROOP hull instead of `ShipDesign:76`. Topping up a hull that has
already spent its troops still needs solving. Until it is, a second invasion uses a second hull:
a spent hull no longer counts toward `TROOP_TARGET` and no crew rule tries to fill it (§4.3,
R-XPL-09).

```
WHEN   a TROOP ship exists and its target planet's weakness == 0
THEN   Ship:52 = 5 (conquer), destination = that planet's Planet:4/8/12
NOTE   conquer is confirmed to target an enemy-owned planet's exact XYZ
USES   E
```
A troop hull is loaded with `ShipDesign:76 + exploit.INVASION_TROOPS` at first load, and
`explore.scoutable` never hands a TROOP hull to R-EXP-02.

[x] **The Single Player galaxy seeded no troop design**, so R-XTM-07 and R-XTM-04 could not
fire against a human. Done: `make_single_player_galaxy.py` seeds `t1`, a Shuttle with one
Fusion Drive and a Troop Bay (Advanced Magnetism), 110 of its 120 space. The design R-XTM-07's
hint used to name carried two drives, 130 space, which the design editor would refuse.

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
the first has taken. See the [ ] on the rule itself, that occupation
repairs loyalty is the user's reading of the client, and the one measurable proxy
(`Planet:368`) turned out to be a plain turn counter that a garrison does not
change.

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

[x] **Shipyards repair, measured (Oct 2026).** Damage was staged on the turn-214 fixture
(`Ship:136` written to 0.45 on a `probe1` and 0.25 on an `f1`). The scout reached the HQ
yard and went 0.45, 0.60, 0.75, 0.90, exactly the manual's 15% a turn for a light hull, and
R-EXP-02 sent it scouting again the pass it reached 0.90.

[ ] **A slow warship's withdrawal is long.** The `f1` makes 6.4 a turn and its nearest yard
was about 260 units off, so it was still 57 out after 40 turns, at 0.25 the whole way. That
is the price of going to a yard rather than the nearest planet; whether a nearer planet to
shelter at first is better is a tuning question, not yet answered.
[ ] **It cannot fire against a one-boundary wipe, and that is how ships have actually
died.** Damage is multiplicative over up to 200 rounds with no early exit, all inside a single
turn resolution, so both warships went from condition 1.0 to deleted between two samples and
this rule never saw a damaged ship. It only helps a hull tough enough to survive a boundary,
which none of the current designs are. Not a defect in the rule, a reason it is not the
safety net it looks like. The real mitigation is arriving with enough hull, i.e. R-XTM-03's
missing strength test.

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
| `CASH_THIN` | 1000 | `exploit.py` | a reserve this small deserves a decent income behind it |
| `THIN_INCOME` | 20 | `exploit.py` | ...and "decent" means at least this per turn |
| `WORKER_FLOOR` | 2 | `exploit.py` | never strip a planet below this many workers |
| `CREW_PER_COLONY_SHIP` | 2 | `exploit.py` | fallback only; see min_crew() below |
| `INVASION_TROOPS` | 3 | `exploit.py` | Crew above ShipDesign:76 a troop hull carries. A capture lands every one of them and they stay as the garrison: 3 aboard over a flight crew of 2 landed 1, and 5 aboard landed 3. One alone was retaken within five turns. |
| `RECRUIT_RATE_LEAN` | 20 | `exploit.py` | Recruiting flat out starves growth, so a planet that is not comfortably fed trains at this slower rate instead of the maximum. |
| `FARM_PER_POP` | 4 | `exploit.py` | citizens per farm before another is wanted |
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
| `AT_PLANET` | 1.0 | `intel.py` | a report this close to a planet is about it |
| `MAX_AGE` | 40 | `intel.py` | turns; older intel is treated as unknown |
<!-- constants:end -->

[x] **This table has drifted from the code and should be generated from it, not typed.**
Known mismatches: `WARSHIP_TARGET` (2, in `exterminate.py`), `RETREAT_AT` (0.30),
`MAX_SHIPYARDS` (2), `SHIPYARD_POP` (10), `RECRUIT_RATE_LEAN` (20) and `CREW_PER_COLONY_SHIP`
all exist in code and not here, while `RAID_MARGIN` is here and used nowhere. A constant that
appears in only one of the two places is a constant nobody is tuning. Done: the table above is
generated from every numeric constant in the rule modules.

Units: distances are in the same space as `Planet:4/8/12`; a scout's ETA is
`ceil(distance / ShipDesign:36)`, which is how the game derives it (ETA is not stored).
Measured galaxy scale, for calibration: coordinates 0..~1130, 108 suns, 538 planets, 4-6
planets per system, orbit radius ≤ 39.6, minimum sun separation 111.7.

**Systems are not stored.** No field links a `Planet` to its `Sun`, so membership is
nearest-sun. That is safe here rather than merely convenient: a planet would have to sit
55.8 from its own star to be misassigned, 41% beyond the widest orbit observed.
`Snapshot.system_report()` reprints these numbers so the assumption is rechecked on any
new galaxy rather than inherited.

---

## 6. Open decisions, need your call before implementation

1. ~~**Fog of war.**~~ **RESOLVED, the AI keeps its own discovery set**, seeded from owned
   planets, grown by ships reaching a sun and by `Owner:320` scan reports, and persisted
   across restarts. `--vision all` remains as an explicit testing opt-out. See §4.4 for the
   persistence design and §4.1 R-EXP-03 for the one remaining hole (a fly-through between
   two samples is never recorded).

2. ~~**Ship production is the critical path.**~~ **RESOLVED, actuator D works.** A ship
   build points `Planet:296` at the shared `ShipDesign` (tag-12); there is no per-build
   object. Colony ships, scouts and warships have all been queued, built and crewed
   unattended over a full game.

3. **Where the AI sits.** `client/dev_tools/ai_player/` as a peer of the other dev
   tools, importing `ejbo_viewer`, is the low-friction choice. If the AI is meant to
   eventually drive a *server-side* civ rather than the client's local player, it
   belongs under `server/` and reads game state from the server's own model instead.
   The spec above assumes the client-side reading.

4. ~~**Governor overlap.**~~ **RESOLVED, no governors.** The AI does all planet
   management itself and leaves `Planet:496` unset everywhere. Actuator H is dropped and
   R-XPN-03 no longer assigns one.

5. ~~**Turn acceleration.**~~ **RESOLVED, `ai.py --drive N`** writes the turn length at
   `0x0080AA08`, re-asserts it whenever the engine resets it (which it does, often), and
   restores the original on exit including on Ctrl-C. Games run at 8-15s per turn; a
   400-turn game takes about an hour.

6. ~~**Two AI civs, or one AI against the engine's own?**~~ **RESOLVED, two AI civs, both
   driven from one client by `duel.py`.** The engine plays `BadGuy` passively: over 154 turns
   it held one planet, built no warship and declared no war, so R-XTM-02 (defend) could never
   fire and every other rule was measured against an opponent that never contested anything.

   **It needed no new actuator and no memory hack**, which is the opposite of what this item
   assumed. Every module already resolves its civ BY NAME through `gamestate.resolve_civ`
   rather than assuming the local player, and every write is addressed through that civ's own
   objects, the `.data` local-player node at `0x00857904` only chooses the DEFAULT civ, it
   does not gate what may be written. Confirmed live on a turn-154 galaxy: a controller
   pointed at `BadGuy` set its research topic, built it a farm, colonised two planets and took
   it from score 3644 to 5868 in eight turns, with the engine keeping every change across the
   turn boundaries and reverting nothing.

   Two clients is still the *correct* architecture and is still blocked on server-side turn
   sync; one client with two controllers is the testbed, and §1.1 is honoured per civ rather
   than per process:
   * **Separate fog of war**, one `sensors.History` per loop, and the discovery set is
     already persisted per civ, so neither side sees inside a system its own ships have not
     reached. `--vision all` remains an explicit opt-out.
   * **No fixed move order**, whoever passes first claims contested things (a shipyard, a
     colony target) against the other's pre-move state, so `duel.py` alternates the order by
     turn parity instead of letting one civ hold that advantage all game.

   [ ] **The engine's AI is still running underneath.** It reverted nothing in eight turns
   and it may simply have nothing to say about a civ whose planets are already busy, but
   nothing has established which of the two is deciding when they disagree. `Owner:360` and
   `Owner:1196` are the candidates to look at first, both read 1 on the local player and 0 on
   the engine's civ, and neither has been identified.

   [x] **Seeded designs differ by galaxy builder.** A civ scouts and fights only with designs
   seeded into the save blob (`game_cycle.py --design`, which needs `--like-id <that civ's
   design>` because it otherwise resolves the local player's). `make_single_player_galaxy.py`
   gives the AI civ `probe1`, `f1` and `b1`; `new_duel_galaxy.py` gives every civ a scout only,
   so a duel on that galaxy has no armed side. `make_single_player_galaxy.py --every-civ` seeds
   the full design list into every civ, which is the armed duel fixture.

   **THE GALAXY IS NO LONGER LIMITED TO TWO CIVS.** `server/dev_tools/inject_civ.py` adds a whole
   player to a save blob, an `OWNR` clone under a new name plus a homeworld built by transplanting
   the donor's `PLPR` onto an uncolonised planet. Confirmed live: a turn-162 capture came back up
   with three `Owner` instances, `'Ceti'` owning the planet it was given, and `duel.py` drove all
   three for four turns with no crash. `--name` is repeatable, so twenty civs is one command. The
   engine derives each civ's registry key from its NAME, so the only uniqueness requirement is the
   name itself. Details and the measured blob layout are in
   `CosmicSupremacy_Reconstruction_Report.md` §2, with the save-blob work rather than the
   memory work.

   **One memory scan per turn serves every civ.** `duel.settled_snapshot` takes a single
   snapshot and each controller's pass reads it; fog of war is applied per civ on top of it.

   [x] **A torn read got through with three civs.** On turn 166 `GoodGuy` read 1 planet when it
   held 7, and acted on that. `_snapshot_settled` only re-reads when a civ reads ZERO planets, so a
   partial read of a seven-planet empire passes the guard. With more civs each pass starts later
   after the boundary, so this gets more likely, not less. The check should compare against the
   previous turn's count rather than against zero. Done: `ai.looks_torn` re-reads a civ below
   half of last turn's planets, in `ai.py` and `duel.py`.

7. [ ] **The client must be relaunched to pick up a code change**, and doing so re-offers
   the customisation popups and (before persistence) discarded the discovery map. Worth
   deciding whether the loop should hot-reload its rule modules instead. The restart is not
   destructive: the popup's commit adds to the current rates rather than resetting them, and
   dismissing it with Decide Later writes nothing at all, so an unattended run that never
   answers the dialog loses nothing. It does mean a run that *does* answer it inflates its own
   homeworld, since each relaunch re-grants the full allowance.

---

## 6a. Originating orders without a human click

This is the blocking problem for both the AI player and multiplayer: a server must be
able to load each player's standing orders into a fresh client with nobody at the
keyboard. Four routes, evaluated.

### What is settled

- **Fabrication is out.** The order object owns three engine allocations (route vector,
  two list-node chains) that its allocator will free.
- **Lazy creation is out.** Writing `Ship:52` with a null `Ship:48` is inert, tested
  over a full turn: no crash, no movement, no allocation.
- **Transfer is in, and proven.** `actions.transfer_order` moves an order between ships
  with no allocation and no free. End to end: detached from one colony ship, attached to
  another, retargeted, navigated, colony founded on turn 3.
- **The movement model is fully solved**, which any of these routes needs:
  `position = leg_origin + route_unit_vector × progress`, verified to 0.000009.

### Route 1, `loadgame` blob injection  recommended

The client already reconstructs an entire galaxy from a `loadgame` response
(`DONE#VER#<6-char>#DATA#<base64>`, parsed at `0x0048b5d0`), allocating every object
with its own allocator. The save format carries a **`ROUT`** section alongside `SHIP`,
`SHCO`, `SHPR` and `DYNO`, so routes are persisted, which means **the engine already has
a non-UI construction path for orders**. That is exactly the capability we need, and it
is the only route that is also the right multiplayer architecture: the server holds
authoritative orders and pushes them on load.

Status: `cs_server.py` stubs `loadgame` to return an empty blob. The format is documented
in git history (`06573cd:prototype/server/save_parser.py`, plus the three "Document save
blob binary format" commits). `ROUT` itself has **no coverage in the report at all**,
only the section marker is known.

**Cheapest way in:** make the server *persist* the `savegame` blob instead of discarding
it, then save a game while a ship is under a known order. We know that order's exact
numbers, origin, destination, length, and progress as a clean multiple of 13.5, so
locating `ROUT`'s layout inside the blob is a search for known floats, not a blind parse.

### Route 2, call the engine's own order constructor  CONFIRMED AND SHIPPED

**This is solved.** Don't imitate the engine, ask it.

```
order = operator new(0x60)                                   0x0065165A
order_ctor(ECX=order, origin, target, owner, f, 1, 0)        0x004E8420
Ship:48 = order ; then retarget_order() aims it
```

| | | |
|---|---|---|
| `0x0065165A` | `operator new(size_t)` | `__cdecl`, one arg, `ret 0`. Reachable **directly** from `CreateRemoteThread`, which passes exactly one stack arg, no stub needed |
| `0x004E8420` | order constructor | `__thiscall`, **six** stack args, `ret 0x18` |
| `vftable+0x30` | float for arg 4 | on the ship; takes no stack args |

Argument count came from the constructor's own `ret 0x18`, cross-checked against all 13
call sites, `0x00479685` shows six unobstructed pushes. Two of its branches,
`0x004DC588` and `0x004DC5FE`, are the scout and colonize paths writing `2` and `3` to
the same order-type local.

**Pointer forms are not interchangeable, and this is the easiest way to crash the
client.** Origin and target go in as **allocation starts** (`tag-8`), which is why the
constructor reads coordinates at `+0xc/0x10/0x14` rather than the `+4/8/12` the
tag-relative annotations use. The Owner must be its **`tag-52`** primary form; the game
derives it with `lea reg,[node0-0x2c]` from the `tag-8` node at `Ship:40`.

Implemented in `remote.py` (stub builder + `CreateRemoteThread` plumbing) and
`actions.create_order`. Verified in game: a colony ship with `Ship:48` null was given a
working order and its route rendered in 3D.

**UI caveat, tracked as a [ ] in the reconstruction report.** The ships tab keeps
showing a ship's old command until a turn boundary passes, then catches up on its own.
The 3D route draws immediately and the ship moves correctly throughout, so this is a
deferred refresh of that list, not a defective order, which also makes the `.data` dirty
flags a weak suspect, since a validity gate would have broken movement too. Cosmetic in
single-player; in multiplayer a player would not see server-pushed orders until the next
turn.

**Report corrected (Aug 2026).** `CosmicSupremacy_Reconstruction_Report.md` labels
`0x0082A900…0x0082A920` as "linked-list head/tail/sentinel of order records". Live, it is
**selection state**: `0x0082A900` held a reference node to the selected `Ship` (`addr-8`)
and `0x0082A918` one to the selected `Sun`. Fixed in the report.

### Route 3, donor pool (works today, limited)

Keep a small pool of UI-created orders and shuffle them between ships. Real but bounded:
it relocates orders, never creates them, and the supply only shrinks, because completing
an order consumes it, for colonize, along with the ship. Viable stopgap for a handful of
concurrent orders; not a foundation.

A partial extension: for Move and Scout, retarget before arrival so the order never
completes and the object is never consumed. Colonize and conquer inherently complete.

### Route 4, harvest orders from AI-civ ships, NOT recommended

The AI civ creates orders every turn with no user action, and `Ship:40` and `Ship:48` are
independent, so one could be detached from an enemy ship and attached to ours. Renewable
and needs no click. **But it leaves the opponent's ship orderless**, which is direct
interference with another player rather than playing our own civ, it fails the §1.1
no-cheating rule. It also assumes an AI civ exists, which in real multiplayer it may not.
Recorded for completeness; not to be built.

### No longer a concern

**Does a newly built ship come with an order object?** It no longer matters. Route 2
originates one for any ship at any time, so a ship that starts at `Ship:48 == 0`, which
every colony ship does, is fully controllable from the moment it exists.

## 7. Engine calls vs. field writes

**Every cheat found so far came from a field write.** Research prerequisites and facility
unlocks were both cases of writing a legal field to a value the UI would have refused,
because the engine does not validate those fields on the path we write. An engine call
inherits whatever checking the engine does; a field write inherits none. That makes the
choice a *correctness* question, not just an ergonomic one.

We have called engine functions since Route 2 (`operator new`, the order constructor, the
`ShipDesign` constructor, the speed getter). What actuator J adds is the first call to a
complete **transaction**, a whole UI action, priced and applied by the engine, rather
than an allocator, a constructor or a getter. That is the pattern worth extending.

### Ranked candidates

| Candidate | Address | Why it matters |
|---|---|---|
| **Read the live research table** | `0x0054AA70` id→record, `0x0054B140` cost, `0x0054B200` prereq-at-index, `0x0054B7D0` exclusion test | `research.py` currently carries a table extracted for content version **≥688** while this client runs **565**, and the two demonstrably differ (see the cost-ratio argument in that file). These calls read the table the client is *actually* using. Read-only, so the safest possible first target, and it retires a known-wrong data source |
| ~~**`Ship::SetCommand`**~~ | `0x004D9DC0` | **WITHDRAWN, this candidate was tried and it kills the client** at the next turn boundary, for Move as well as Attack (§4.4). Writing `Ship:52` alone turns out to be fine: the ship navigates correctly with `Ship:56` null. [ ] The order-type-8 / `Ship:61 = 0x0B` observation is still unexplained and still unused |
| ~~**`ChangeCitizenJobs`**~~ | `0x00574180` | **DONE, actuator N.** Conscription works and R-XPL-09 uses it. Note it is still *not* used to replace actuator A: setting an ordinary job id by field write remains cheaper than a remote thread, and only job 3 goes through the engine, because only job 3 costs money |
| **`GetTrait`** | `0x00516180` | Real civ-modifier values (trait 18 = ship speed %, trait 38 = planet space %) rather than assuming 0. Feeds any speed or max-pop calculation |
| **available space** | `0x004F1140` (PP vftable slot 6) | `max_pop` with the trait bonus applied, instead of our `space/10` approximation |
| ~~**`GetDraftCost`**~~ | `0x00516060` | **DONE.** `remote.draft_cost`. Validated read-only before the mutating call, per the rule below: six readings across two civs at three counts, every one matching `sum(5*(T+k)+105)` computed independently from a snapshot. That match is also what confirmed `T` counts ship crew as well as stationed units |

[ ] **None of the remaining candidates has been attempted**, and the first two are now the
highest-value work in this document. The live research table would retire a data source known
to be for the wrong content version (`research.py` carries ≥688, this client runs 565).
`ChangeCitizenJobs` unlocks **conscription**, which is the answer to the crew bottleneck that
gates every order rule, the civ sat on 11k-20k idle cash for a hundred turns with nothing to
spend it on while waiting ~10 turns per recruit.

### The crash of Aug 2026, RETRACTED DIAGNOSIS, then solved (Aug 2026)

**READ THIS BEFORE THE TABLE BELOW.** The conclusion this section originally
reached, that a mutating engine call on a remote thread kills the client, and
that `ChangeCitizenJobs` is necessary and sufficient, is **WRONG**, and it was
wrong with a five-arm bisect behind it. The real fault was `_set_ship_crew`
repointing a `std::vector` the engine already owned. Conscription was convicted
because it PRODUCES the units that make the crew loader run: every arm that
turned drafting off also stopped the loading, so the crash disappeared for a
reason nobody had tested.

What separated them, finally:

| arm | outcome |
|---|---|
| conscription on, crew loading on | dies ~30 turns later |
| conscription on, crew loading **OFF** | **survives 45/45**, 19 drafts |
| all rules, vectors never repointed | **survives 45/45**, 9 drafts, 10 loads |

**The rule that actually holds: never repoint a container the engine owns.**
Fill a crew or military vector only when it is EMPTY. The code that predated
this session refused to do otherwise and said why; overriding that comment cost
a day, and the comment was the most valuable thing in the file.

[ ] **`Ship::SetCommand` is marked DO-NOT-USE on the same style of reasoning
that failed here**, "the client died after we called it", and deserves
re-testing rather than inheriting this mistake. It may never have been the call.

The original text follows because the experiment shape was right even though the
conclusion was not, and because the arms below are still the evidence that our
FIELD WRITES are innocent:

| arm | outcome |
|---|---|
| every rule running | **client dies** |
| the AI writes nothing at all | survives, 45/45 turns |
| **every memory write, but no mutating engine call** (`--without R-XPL-08 --without R-XPL-09`) | **survives, 45/45 turns** |

So the field writes this project spent months on are FINE, and the calls are not.
The two in the failing arm are `HurryProduction` and `ChangeCitizenJobs`; the
read-only getters (`draft_cost`, `hurry_cost`, the design stats, relation codes)
run in every arm including the ones that survive.

**The failure is delayed and does not look like a crash at the call site.** The
call returns, the pass completes, the game plays on for tens of turns, and then
the engine dies walking a structure. Two different faults were captured:

```
ACCESS_VIOLATION  at exe+0x12E6A   movzx esi, word [ecx]   ecx = 0x02DAF00C
ACCESS_VIOLATION  at exe+0x1133E4  reading 0xFFFFFFFFC5C8190C
```

The second is a FLOAT being dereferenced as a pointer, the same signature as the
object-id confusion the reconstruction report records for the design high-water
bug. A corrupted container is the most likely intermediate.

**IT IS `ChangeCitizenJobs`, CONSCRIPTION, and `HurryProduction` is innocent.**
Two further arms separated them:

| arm | outcome |
|---|---|
| hurry kept, conscription off | survives 45 turns, **95 `HurryProduction` calls** |
| conscription kept, hurry off | **dies at turn 184**, after 8 drafts |

Necessary and sufficient. What distinguishes the two is what they touch:
`ChangeCitizenJobs` runs `CommitPopulation`, which MIGRATES RECORDS BETWEEN THE
CITIZEN AND MILITARY VECTORS AND REALLOCATES THEM, on a remote thread, while the
main thread may be walking those same containers. `HurryProduction` prices a
build and debits the treasury, it touches no container, and 95 calls did
nothing at all.

**R-XPL-09 IS ON and works** (`exploit.ALLOW_CONSCRIPTION = True`). It no longer
calls `ChangeCitizenJobs` at all: the migration is plain field writes, one
16-byte record from the citizen vector to the military vector with its job id set
to 3, while the PRICE still comes from the engine's read-only `GetDraftCost`.
Verified over 45 turns with every rule running, 9 drafts and 10 crew loads.

[x] **A STALE CITIZEN INDEX IS NOT THE CAUSE, tested and falsified.** The
obvious suspicion, and the user's, was that the index came from a snapshot and
named a citizen the engine had already moved: `CommitPopulation` erases the
drafted record, memmoves the tail, and auto-conscripts surplus population above
`space/10` while it is in there. `conscript` now re-reads the citizen vector live
and compares it against the snapshot before choosing. Result: the crash is
unchanged, at the same turn after the same number of drafts, and the comparison
reported **zero** discrepancies, the snapshot index was correct every time.

That leaves the call itself rather than its arguments, which is the stronger
reading of the bisect anyway: `HurryProduction` takes arguments too and 95 of
them did nothing. **It is not WHAT is passed, it is WHERE the call runs.**

[ ] **The fix is probably not "stop calling them".** Conscription is the only
answer to the crew bottleneck and hurrying is the only sink for cash. What the
evidence argues for is calling them ON THE MAIN THREAD, suspend it, point EIP at
a stub, restore, which the reconstruction report already sketches as the
remaining route for `LoadGame`, and which would remove the whole class rather
than these two instances.

[ ] **A FIXTURE AND ITS CANONICAL HASHES ONLY MEAN ANYTHING TOGETHER, and the
conscription fixtures in `client/` predate 4691f57.** Before that commit the
actuator read `Owner:8` and a vector's begin/end from the snapshot rather than
live, so a pass that drafted twice debited one draft's price and removed one
draft's citizens. Crewing two hulls left BadGuy on 6442 with 16 citizens on its
HQ; the same pass now leaves 6187 and 14. Cash and population are both inside
the canonical hash, so every hash taken downstream of those blobs moved with
them.

The trap is that regenerating a pre-4691f57 fixture and comparing it against a
pre-4691f57 hash produces a mismatch that reads exactly like a determinism
failure and is not one. Hashes are evidence only against blobs the same actuator
produced, so publish the two together and say which actuator built them. The
difference is structural as well as numeric: post-fix, `conscript_to_crew`
cannot take four citizens from a planet holding three non-farmers at all, and it
refuses instead. `allow_farmers` is NOT the way to get them back, it is a
judgement only R-XPL-09 is positioned to make (§4.3); draft across more than one
planet.

[ ] **Every earlier "it crashed after we did X" in this document is now
suspect**, including `Ship::SetCommand`. That one was blamed for a crash at the
next turn boundary and marked DO-NOT-USE on that basis. It is a mutating engine
call on a remote thread, so it belongs to this class, and the same experiment
would settle whether the call is fatal or merely the thread it ran on.

### The standing hazard

Stubs run on a `CreateRemoteThread`, **not the game's main thread**. **Something has now gone
wrong**: `Ship::SetCommand` kills the client at the next turn boundary (§4.4), and the cause is
not understood, it may be this hazard or it may be the call itself. Read the rest of this
section as a live risk rather than a precaution. Nothing guarantees safety: a call that takes a lock the main thread holds, or
mutates a container the main thread is walking, can deadlock or corrupt where a field
write would merely have been wrong. Static analysis found no TLS dependency in the
conscription call graph, which is evidence but not proof.

Practical consequences, and the rules that follow from them:
* Prefer **read-only** calls (`getters`, table lookups), they carry the least risk and,
  as the research table shows, often the most value.
* For mutating calls, prefer ones whose call sites show them being invoked as a complete
  unit behind the offline flag `0x0086F1A0`, as `SellFacility` is.
* Validate every new stub on a **side-effect-free getter** before pointing it at anything
  that mutates state, `remote.py` already does this.
* **The design getters are NOT side-effect-free.** `ShipDesign:36/52/56/60/64/68/76` are a
  lazy cache: each getter computes the value and WRITES IT BACK. The ship-design tab does
  the same thing on the UI thread, so calling them while a human has that tab open is two
  threads writing one cache. A crash was traced to exactly that, six speed getters run
  back to back while the user opened the design tab. Read them when the AI is driving
  alone, not while someone is looking.
* `ret N` gives argument **count**, never **type**. Mistaking `0x005470A0` for a copy
  constructor on that basis crashed the client once.

## 7a. Prototype status, is each X actually demonstrated? (Aug 2026)

Written for the question "can this game be shown baseline functional through all
four X, by an AI with nobody at the keyboard". Answered per X, against runs, not
against intentions.

| X | demonstrated | how |
|---|---|---|
| **eXplore** | **yes** | civs build their own scouts, crew them, sweep systems, and keep a per-civ fog of war that survives restarts. A fresh galaxy went from 108 unknown systems to full contact between three civs unaided |
| **eXpand** | **yes** | colony ships built, targets scored and taken; three civs reached 6-13 planets each from one homeworld apiece |
| **eXploit** | **yes** | jobs, farms, shipyards, research, recruitment, crew loading and hurrying all run unattended; research picks legal topics and switches when a capability threshold is unmet |
| **eXterminate** | **partly, see below** | war is declared, warships are built and crewed and sent, a planet has been TAKEN by troops and garrisoned, and ships have fought and been damaged |

**The one gap that matters: combat does not happen in ordinary play.** Every
battle so far was either staged (two fleets pointed at each other) or one-sided (troops landing on an undefended planet). R-XTM-03 ranks
targets by weakness and takes the MINIMUM, so it reliably picks the planet with
nothing on it, the fleet arrives, sits in orbit, and nothing happens. Two civs
did exactly that to each other for fifty turns.

That makes exactly two items critical for a prototype claim, and both are already
specified above:

1. ~~**R-XTM-03 must rank by "can I beat it", not "is it weakest"**~~, **DONE** (§4.4).
   Raids go only at defences a battle report shows the fleet can beat.
2. ~~**R-XTM-02 defend is NOT BUILT**~~, **DONE** (§4.4). Idle and staged warships
   hold a planet that enemy ships come into scan range of.

Everything else outstanding is tuning or economics, not capability: S-04,
R-XPN-04, R-XPL-03. Each is a [ ] on its own rule with a reason. R-XPL-07 is built. (R-EXP-04
is built, as part of R-XTM-05.)

**R-XPL-09 conscription is ON and working**, it buys crew for cash instead of
waiting ten turns per recruit, and the client survives it now that no vector the
engine owns is ever repointed (§7).

## 8. Build order

Steps 1-5 are **DONE**. The AI plays all four X's unattended and has fought a battle.

1. ~~RE `ShipProduction`~~, D works.
2. ~~Framework~~, `ai.py` loop, `sensors.History`, dry-run by default.
3. ~~Actuator modules~~, A, B, C, D, E, E′, F, G, J, K, M all READY. L is fatal, see §3.
4. ~~Explore end to end~~, three self-built scouts swept 105 of 108 systems.
5. ~~Expand, Exploit, Exterminate~~, all running; Exterminate reached combat.

### What comes next, in the order I would do it

1. [x] **Make the fleet survivable.** R-XTM-03's missing strength test plus real hulls
   (corvette chassis, shields, several weapons) via `game_cycle.py --design`. Everything else
   in Exterminate is downstream of not losing every ship on arrival. Test from a saved
   armed fixture (`checkpoint.py`) with `combat_watch.py` alongside, not from a fresh game.
   Done in v0.3: R-XTM-03's margin from battle reports, corvette designs, staged arrival.
2. ~~**Conscription**~~, **DONE**, actuator N and R-XPL-09. Running unattended on the fresh
   galaxy: a grounded scout was drafted for, crewed the next turn and flew, and exploration
   went from roughly one new system per ten turns to five in four. The engine call it needed
   was also the first mutating one attempted since `SetCommand` killed the client, so it was
   built getter-first and tested one draft at a time from a checkpoint.
3. [x] **Stop expansion starving the shipyard**, R-XPN-04, which is about shipyard time
   and crew rather than the income trigger currently specified.
4. [x] **Read the live research table** (§7). Already done: `research.py` was emitted
   from the live table. `research_dump.py --diff` on the turn-214 fixture (Oct 2026) differs
   only in trait values of four doctrine records (54, 59, 62, 67), which the AI never picks.
   The client reported content version 99999, not the 565 this item assumed. Original item:
   retires a table known to be for the wrong
   content version, and it is read-only, so it is the safest engine call available.
5. ~~**A second AI-driven civ**~~, **DONE**, `duel.py` drives both civs on one turn clock
   (§6.6). [x] What remains before a duel is a real test: synthesised designs for the second
   civ, and a FRESH galaxy, the demonstration ran on a turn-154 game the first civ had already
   won 21,591 to 3,644, which measures nothing about the strategy. Done: `client/duel_fixture2.dat`
   is a fresh turn-0 galaxy with both civs armed, and `metrics.py` compares runs on it.
6. [ ] **Buy resources on the market** when no planet of ours produces one (§4.3, Cash
   policy). Not tasked yet. Stocks (`Owner:1128`) and prices (`Owner:916`) are readable; the
   purchase has to be located first, by watching a human buy at the client.
