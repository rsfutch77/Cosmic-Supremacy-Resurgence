# Fast Simulator for AI Training

A standalone turn simulator that runs many times faster than the original client. Training
runs in the simulator; a few games per policy are evaluated on the real client.

The simulator does not need to be byte-exact. It needs the rules that decide who dominates
to be close enough that a policy trained on it still wins on the real client.

---

## 1. Shape

- **Headless core**, Python first. Fidelity is the hard part, and the existing parsers,
  stat tables and `merge_orders.market_total` are already Python. Hot loops move to NumPy or
  a compiled module once a profile says where the time goes.
- **Starts from a blob.** Every client capture and every archived tick is a starting
  state, so the simulator never needs its own galaxy generator.
- **One observation and action interface for both backends.** The policy reads the same
  observation from simulator state and from a client blob, and emits the same orders. The
  client backend turns those orders into what `duel.py` or the referee already applies.
- **Optional viewer**, a small local page that draws the map, planets and fleets from
  simulator state, for checking a run by eye. Not needed for training.

## 1a. Reward: indestructible dominance

**Dominance is a stronger army now and an economy that grows it faster than anyone else's,
both at once.** A civ of farmers with a large share of planets or population falls to one
bomber fleet. A huge fleet its economy cannot maintain falls to a civ that can build a
winning fleet quickly. In-game score is no guide: it comes only from destroying enemy units,
so it ignores expansion and rewards farming battles.

Two measures per civ, each as a share of the galaxy's total:

- **Fleet strength `S`.** Effective firepower times effective hitpoints, the manual's terms:
  firepower raised by rank and split light/heavy against the rest of the galaxy's mix,
  hitpoints raised by shields and lowered by condition. In the manual's combat each side
  takes the other's hitpoints off at its own firepower per round, so the side with the
  larger product destroys the other first. Better parts raise both factors, so a high-tech
  fleet outscores a larger low-tech one. Only the part of the fleet the civ's income can
  keep paying upkeep for counts.
- **Surge capacity `G`.** The strength the civ could add in `K` turns: its production after
  corruption, spent on the best design its research allows (most strength per production
  point), limited by the ship resources it mines or can buy, and by the upkeep its income
  can carry. `K` is about how long a rival fleet takes to arrive, so it counts only ships
  that would be ready in time.

**Dominance `D = sqrt(share(S) × share(G))`.** The geometric mean is high only when both
are high, and zero when either is. A sum would let one carry the other.

- **Terminal reward:** `D` at the horizon. An eliminated civ has `D = 0`.
- **Per-turn shaping:** potential-based on `D` (the next turn's `D` less this turn's), which
  speeds learning without changing which policy is best.
- **Reward sees everything; observation does not.** `D` is computed from full state during
  training. The policy only sees what fog allows.
- **Reported, not trained:** population share, planet share, game score, fame.

`D` is a proxy. Self-play in the simulator is the real test: a civ with a high `D` has to
actually win the wars it gets into, and a civ that reads high but loses means `D` is wrong.

## 2. Tick order

The manual's `tick` page gives the order, which the simulator follows:

1. Governors, then admirals (skipped: the policy replaces both)
2. Research
3. Planets: production, population growth, food, income
4. Ship movement
5. Combat
6. Transport admirals unload, then governors and admirals again (skipped)

The market's per-tick decay sits beside the score rebuild at `0x56D24A`.

## 3. Rules, by module

| Module | Source | Status |
|---|---|---|
| Facilities, ship parts, upkeep | Stat Tables §3 to §6, read from the client | Known |
| Production, income, food | Stat Tables, manual (`production-and-queuing`, `food-storage-size-calculation`) | Formulas in the manual; check against the oracle |
| Population growth, max population | Memory report (`Planet:104`, `Planet:144`), measured series | Max population known; growth rate to fit from the oracle |
| Corruption | Manual `corruption`: empire-wide from planet count and reputation, scaled per planet by population | Formula in the manual |
| Loyalty | Manual `loyalty-corruption`: military and Propaganda Offices raise it, conquest lowers it, rebellion below 30% | Qualitative; the math needs fitting. Not located in memory |
| Reputation | Manual `combat-preconditions`, `score-reputation-and-rank` | Not an objective, but it feeds corruption (below) |
| Research | `ai_player/research.py`, manual `science-wastage` | Cost model open: re-dump the table on a released-version client |
| Market | `v0.5-market-sales`: `RSMA`, quote formula, 1,000-unit decay per tick | Solved, in `main` |
| Movement | Speed from thrust over mass (`ShipDesign:36/40`), ETA derived (`Ship:48`) | Mass term to fit |
| Colonisation | `ROUT`; the target planet changes hands two ticks after the order | Known |
| Combat | Manual `combat-overview`, `ship-vs.-ship-combat`, `planetary-defense`, `conquering-planet` | See §4 |
| Score | Stat Tables §7, battle score from the score list | Known |
| Diplomacy | War only (`Treaty` kind 3). Alliances and other treaties skipped | Enough for v1 |
| Scanning and fog | Enemy firepower, HP and troops need a scan or battle report | Needed so the policy does not train on information it lacks on the client |

**Reputation still has to be modelled, minimally.** It is not part of the reward, but
empire-wide corruption depends on it, and corruption cuts food, production, mining, research
and income on every planet. A civ that declares war and attacks first loses reputation, so a
simulator without it would overvalue aggression. The manual's rule is small: a civ loses
reputation only if it declared the war and is the aggressor, and only for the first battle.

## 4. Combat

Most of the game is not combat, so combat must not hold up the rest. Three options:

| Option | Speed in training | Fidelity | Cost |
|---|---|---|---|
| Manual formulas (firepower vs hitpoints, light/heavy split, shields, rank, condition) | Fast | Good if the manual matches the binary | Low: the manual spells out the calculation with worked examples |
| Client's Battle Calculator (`BattleCalculatorPage`) as the resolver | Unusable in the training loop: one client per machine | Exact for one round | Driving a UI page |
| Real client battles, results fed back | Same limit | Exact, including targeting order and side effects | Highest |

Proposed: implement the manual formulas, and use the Battle Calculator as the oracle for
them. It resolves a single round with no turn to tick, so it gives many cheap, exact checks.
Real-client battles check what the calculator does not cover: targeting order (unarmed ships
untouched in the July battle), reaping at condition 0, and the side effects in news, score
and population.

## 5. Validation against the client

The engine is deterministic when it loads a blob and ticks once, which is what the referee
does. Ticking many turns in one session (`duel.py`) is not reproducible after a battle, so
the oracle always uses load, tick once, capture.

- **Harness:** blob plus orders, through the client and through the simulator, compared field
  by field per module, with a tolerance on each.
- **Inputs:** the tick archive first, then targeted fixtures for each rule.
- **Scoreboard:** per module, the share of fields within tolerance, so it is clear which rule
  is wrong and when a module is good enough.
- Only one client runs per machine, so oracle runs are batched overnight.
- **Content version:** the tick archive and the test fixtures carry 99999, which selects
  the unreleased stat tables. The oracle stamps 565 into a blob before the client ticks
  it, so both sides use the released tables the simulator carries.

## 5a. Code

`sim/` at the repository root. Tests are standalone scripts in `sim/tests`, collected by
`conftest.py` like `server/tests`.

- `sim/tables.py`: the component and facility tables for the released content version,
  as data. `test_tables.py` checks them against the stat tables document.
- `sim/state.py`: the galaxy state. A design derives units, hitpoints, firepower, thrust,
  space, cost, upkeep and resources from its parts.
- `sim/load.py`: a blob into that state, through the `server/dev_tools` readers.
  `python -m sim.load <blob>` prints a summary. Every one of the 263 blobs on disk loads
  with nothing unread, at about 0.1 s for a two-civ galaxy and 0.5 s for 547 planets.
- `sim/tick.py`: one turn: planets, research, movement, combat, then the activity flag.
  `copy_first=False` ticks in place for the training loop.
- `sim/economy.py`: the planet step: food, corruption and loyalty, births, recruitment and
  training, production and builds, mining, science, income and upkeep (§5b).
- `sim/research.py`: completion, costs and the walk toward a goal, with the technology table.
- `sim/movement.py`: routes, arrival, scouting and colonisation.
- `sim/combat.py`: battles at hostile planets and repair at shipyards.
- `sim/traits.py`: a civ's engine traits, its custom-civ values plus research grants.
- `sim/orders.py`: the actions a civ may take, refused where the client's screens refuse
  them: jobs, facility, ship, Wealth, hurry, research, recruit, crew, move, colonise, scout,
  trade, war and design.
- `sim/reward.py`: dominance `D` (§1a).
- `sim/env.py`: `reset` and `step` for training, with a fog-limited observation.
- `sim/baseline.py`: a scripted civ to train against and to smoke-test with.
- `sim/warlike.py`: the baseline plus a fleet and an invasion on a chosen turn, for
  checking the reward against who actually wins.
- `sim/viewer.py`: `python -m sim.viewer <blob> --turns N -o run.html` writes a replay page.
- `sim/traits.py`: all 46 engine traits by name, from `client/dev_tools/ejbo_annotations.json`.
- `sim/renown.py`: score, reputation and crew experience from battles.
- `sim/fog.py`: what each civ has seen, refreshed as the client refreshes `EXSY`.
- `sim/scans.py`: scans built at a Central Defense Agency and spent for reports.
- `sim/oracle.py`: the harness of §5. `archive` pairs referee inputs one turn apart,
  `capture` ticks a blob on the client, chained with `--chain`, and `score` compares the
  simulator's tick with the engine's per module. The corpus is `sim/corpus/`, kept out of
  git. A capture takes about 10 s, and ticking the same blob twice gave byte-identical
  output.

**What a tick changes**, from 29 archive pairs (`score --null`, November 2026): the share
of fields an unchanged galaxy still matches after one engine tick.

| Module | Fields | Unchanged | Most moved |
|---|---|---|---|
| economy | 968 | 80.6% | food 89, credits 58, metal 21, progress 10 |
| growth | 352 | 68.8% | population history 85, citizens 19, crew 3, military 3 |
| research | 102 | 100% | |
| market | 145 | 100% | |
| movement | 340 | 86.2% | position 25, route progress 14, orbit 5, order 3 |
| colonisation | 4870 | 99.9% | owner 6 |
| combat | 170 | 96.5% | ship gone 3 |
| orders | 657 | 94.5% | jobs 20, builds 7 (players' orders between the pair) |

Food, credits and population are where the first rules pay. No research completed and no
market trade happened across these pairs, so they cannot test those modules; captured pairs
from a longer game will.

A design's `SDPR` holds six part lists, each a `u32` count then `u32` part ids: chassis,
scanners, engines, weapons, modules, shields. The sixth has been empty in all 1,081
designs on disk.

## 5b. Rules measured

**Food, corruption and births** match every owned planet in the 29 archive pairs exactly,
26 distinct planet-turns (November 2026). `R` rounds half away from zero.

```
farmer yield  y = b + R(25 × farms × b / 100), then y + R(food trait × y / 100)
                  b the planet's base food rate, PLPR+4; food trait CVTR index 2
gross         farmers × y
corruption    empire = 100 × atan(planets × 0.025) / (π/2) × (1.5^((rep − 25) / 25) − 1)
              planet = empire × min(1, pop / (0.75 × max pop)), a negative percent
              max pop = (space + R(space × space bonus / 100) − lost space) // 10
after corr    c = gross − R(gross × −planet / 100)
inactivity    p = (turn + 1 − 72 − last active turn) / 2 percent, from SERV
food made     c − R(c × p / 100)
store         + food made − 10 × pop
birth         a full store makes a farmer and carries the excess
store size    40 + 40 × floor((n − 1)^1.5), n the average of the population history
              entries since the last birth, rounded up (Reconstruction Report, the
              settled block)
recruitment   share = trunc(surplus × rate / 100) leaves the civilian store; the military
              store gets share less the inactivity cut at the full rate, 2p, and makes a
              soldier each time it reaches PLPR+28 (300)
```

Reputation reads as 0 for these civs, none of which has fought. The formula is the manual's
corruption page, with planets counted per civ.

Inactivity in food runs at half the tooltip's point a turn. It was fitted on the turn-42
chain, where nobody plays and the penalty starts at turn 72: 36 planet-turns from 68 to 85
match exactly. At about 20%, on the war blobs, one planet comes out 1 food high in 33 of 60
turns, a rounding step not yet found. A civ the policy plays is active every turn and never
reaches it.

Recruitment matches. The civilian store's 1 to 3 food shortfall on the turn-110 recruiting
planet was the loyalty bonus from its stationed soldiers (below). With that in, both stores
match on 67 of 72 recruiting planet-turns, and the other 5 are 1 food off on an idle civ.

**Score on the 214 captured pairs**, simulator tick against the null tick:

| Field | Null misses | Simulator misses |
|---|---|---|
| food | 704 | 89 |
| citizens | 103 | 3 |
| population history | 595 | 64 |
| military | 4 | 0 |
| jobs, a new farmer | 103 | 3 |
| credits | 427 | 75 |
| production progress | 155 | 0 |
| ship resources | 105 | 0 |
| ship positions | 164 | 6 |

The 89 food misses are the two planets above. The 6 position misses are two ships the
engine destroyed in battle at turn 110, which the simulator, having no combat yet, moves.

**Production, mining and income** match every active civ exactly, and idle civs to within a
credit or two (November 2026). They run before food, on the citizens the turn began with.

```
worker yield  y = b + R(20 × factories × b / 100) + R(50 × automated × b / 100),
                  then y + R(production trait × y / 100); b at PLPR+5, trait CVTR 3
miner yield   the same shape on the planet's ore rate, mines +40%, robo mines +100%,
                  mining trait CVTR 5; the ore type and rate sit just before ENLI
made          workers × y, less corruption, less the idle cut i = 0.9% a turn
mined         miners × yield the same way, into the civ's store of that ore
progress      + made; a facility costs (4 + (3 + k) × k) × base / 4 with k already
              built, the excess carries, and an empty queue falls to Wealth
Wealth        R(made / 4) credits instead of progress
income        10 a citizen + 50 a banker, banking centres +40% each, less corruption
              and the idle cut at 1/144 a turn, taken in one rounding
credits       + income + Wealth − upkeep of the facilities held at the start of the turn,
              − each ship's design upkeep − 3 a crew member
```

The idle cuts start where food's does, 72 turns after the civ was last active. Mining pins
production's rate: one miner at rate 10 falls to 9 at 6 idle turns and to 7 at 28, and 0.9%
reproduces all 34 production steps of the idle war-blob civ where 1/110 and 1/111 do not.
Income's 1/144 is fitted, and lands 1 or 2 credits off on some idle turns. A civ the policy
plays never meets either. Stationed soldiers cost nothing in any capture.

Not covered yet: ship and scan completion, queues past their first item, hurrying, and
science, none of which a chained capture exercises.

**Movement** matches every ship the engine kept, on every pair (November 2026).

```
speed         thrust / hull units, the design screen's figure; ship-speed trait CVTR 6
              as a percent, untested
route         straight legs in ROUT; each leg ends 1.02 radii off its body's centre,
              the radius at SUN and PLNT payload +20
progress      + speed each tick; the position is that far along the legs, wherever the
              ship stood before
arrival       when progress reaches the route's length the ship sits at the body's
              centre and orbits it
scout         on arrival it enters the system (EXSY) and sets out for the nearest
              system its civ has not entered, starting at progress 0 from the centre
```

A scout carries its `ROUT` with the has-orders byte at 0, which is why the loader missed it
before. The three scouts in the corpus chose four systems inside captures, and each was the
nearest one their civ had not entered. The Single Player scout's first target was set before
turn 0 and is not the nearest.

A finished move leaves the ship in orbit of its target with no order. A finished colonise
order founds the colony in the same tick (below). A ship's `ROUT` holds two leg lists: the
route still to fly, which progress runs along, and the path already flown. A fleet's ships
share one order and move at the slowest member's speed.

Speed is thrust over units, raised by the civ's ship-speed trait as a percent: engine trait
18, the custom trait at `CVTR` 6 plus research grants. A +13 trait moved ships 1.13 times as
far, and +9 with Doctrine: Mobility's +20 moved them 1.29 times.

**Order-driven captures** (November 2026): 168 more engine ticks with no failures. 55 turns
of the referee archive's join galaxy, with two colonisations and a finished move. 20 turns
of the live Beta galaxy, with six civs, governors, shared production queues, ship builds
and hurries. Fixtures with ship and scan builds. Four research probes, in which each civ
held 5,000,000 research points and a deep goal, so every tick paid for one technology.

**Colonisation.** The ship lands its crew as farmers and is gone. The planet takes the
civ, an empty store sized for the crew, no facilities, Wealth, one history slot, and the
next turn as its founding.

**Research.** The research store sits 4 bytes after credits in the civ's record and grows
by the turn's science: scientists times the planet's science rate, universities +25% and
science labs +60% each, engine trait 4, corruption, and inactivity at food's half rate.
Science is banked before the topic is checked: a store of 4,706 with 407 coming in paid a
4,800 topic in the same tick. A civ finishes at most one technology a tick. The cost is
`round(800 x factor) x technologies done`, and the probes confirmed the factor of every
technology they reached, 31 of them. With a goal deeper in the tree, the topic walks the
goal's missing prerequisites, cheapest available first and the lower id on a tie, and topic
and goal both clear when the goal is done. A doctrine needs one doctrine from each tier
its list names: the probe went Mobility, then Knowledge, then Stealth. A civ whose last
`OWNR` word (`Owner:4`) is 0 gains no research at all.

**Ship builds.** A ship costs its design's production, plus, for a design with a colony
module, 100 for every planet and colony ship the civ has, less one. That fits four builds
exactly and one at 100 over. Resources are charged when the build is ordered, not when it
finishes. A finished ship appears in orbit with no crew, full condition and no order, under
the next object id, and the planet falls to Wealth with the excess. Its upkeep starts the
next tick. A ship build's design is the payload of the `SHIP` section that ends `PROD`,
whose length varies with its queue links. Hurrying costs 4 credits per point left and fixes
progress at the cost, so the item finishes the next tick with that tick's production carried
over: the Beta governor's three hurries charged exactly 1,660, 852 and 1,228.

**Facilities a planet finishes work from the next tick**: food, mining and upkeep use the
facilities the turn began with.

**Stationed soldiers.** Each costs 3 credits a turn, as a crew member does. A soldier's
experience is the 16-bit float in bytes 1 and 2 of its record. It grows 0.1 a turn, doubled
by a Military Camp and again by an Academy, up to 2^4 or 2^6. Rank is 1 at 2 experience and
doubles from there. Soldiers raise a loyal planet's output by the manual's formula: with `n`
soldiers, each counting 1 + 0.2 x rank, the effective number is
`n x (0.2 + 0.8 / (1 + 0.0005 n^2)) / 2`. That gives a bonus of 10% x effective /
population, added to the planet's corruption. Four soldiers on 24 farmers gave the 20 food
the engine made beyond the simulator's earlier figure, and the war blobs' recruitment
mismatch was this bonus.

**Combat.** The manual's mechanics, checked against the one battle in the corpus. Each side
fires a hundredth of its firepower in each of 100 sub-rounds. Light fire hits light ships
first and heavy ships at half effect, and heavy fire the reverse. A planet's turrets fire
with the defenders. Unarmed ships, which includes bombers and ships short of crew, are hit
last. Bombs wear down the planet's defence hitpoints. The defenders killed the armed
fighter in its 8th sub-round, and its 0.4 a sub-round took 3.2 off a 42-unit defender,
leaving 0.9238, as the engine had it. The simulator aims at one target at a time, weakest
first, where the engine spreads fire at random. A light ship at its own shipyard mends 15%
a turn, from the tick after the battle. Rank adds 10% firepower per rank of the crew's
experience over the crew the ship needs.

**Governors** move jobs and set builds at the end of a tick, after production. They are
policy, which the agent replaces, so their choices show up as `orders` misses.

**Score on all 411 pairs**, simulator against no change at all:

| Module | Fields | No change | Simulator |
|---|---|---|---|
| economy | 15,482 | 77.4% | 98.6% |
| growth | 9,708 | 76.9% | 96.1% |
| research | 5,164 | 83.1% | 98.4% |
| movement | 7,888 | 83.2% | 99.8% |
| colonisation | 100,179 | 100.0% | 100.0% |
| combat | 3,944 | 98.7% | 99.8% |
| orders | 11,394 | 95.6% | 99.1% |

What remains, by cause:
- **Population history:** 364 misses in the referee galaxies, whose rewritten homeworlds
  keep the history differently.
- **Credits:** 144 misses, all idle civs, 1 to 3 credits each.
- **Research points:** 84 misses, idle-civ rounding and doctrine science effects.
- **Food:** 55 misses, idle-civ rounding.
- **Jobs and builds:** governor choices.

A simulated tick takes about 11 ms on a 547-planet galaxy and 3 ms on the Single Player
galaxy when it copies the galaxy first, and about 0.3 ms on the Single Player galaxy in
place, as the training loop runs it. The engine's tick through the client takes about 10 s.

**Conquest**, from the manual, with no capture to check it. A planet whose defence
hitpoints are bombed to nothing loses its turrets and shield generators. Once no defending
ship is left in orbit (an unmanned one still counts), the soldiers aboard ships with a Troop
Bay land, everyone beyond the crew the ship needs to fly. Pilots of other ships never land:
the ship components page says a Troop Bay "allows you to conquer hostile planets" and seats
5 troopers, and a Large Pilot Cabin seats 4 more crew, who only raise the ship's rank. Each soldier has 1 health and 2 firepower, and each rank adds 10% health and 20%
firepower. Bunkers and fortresses add 100% and 200% for the best-ranked soldiers. The
defenders shoot first in each of five sub-rounds, and the winning troops stay as the
planet's soldiers.

**First check of the reward** (`sim/warlike.py` against the baseline on the Single Player
galaxy, 16 games of 400 turns, about 5 s in all). An eight-ship fleet bombed out a colony's
turret and landed 8 troopers. Against the 21 soldiers the baseline's 10% recruitment had
stationed there, the landing failed. With no garrison, the same attack took the planet at
turn 213. In both games D rated the attacker far above the defender, 0.698 against 0.003.
So **D as defined ignores troops and garrisons, which decide conquests**: a civ with
soldiers on every planet is harder to destroy than its fleet suggests, and a fleet without
troop capacity cannot take anything. The baseline also builds warships it never crews, and
those count for nothing in `S` although they must be destroyed before a landing.

**Ground strength in D** (November 2026). D is now the geometric mean of three shares:
fleet strength `S`, ground strength `T` (soldiers' firepower times health, on planets and
aboard troop ships, by the ground combat rules) and surge `G`. Ten games with troop ships:
landings took ungarrisoned colonies three times in four and failed against 10% recruitment
every time, and an attacker without troopers took nothing. **D still rated the attacker near
0.78 and the defender near 0.008 in every game**, because a defender with no crewed fleet
has a share of `S` near zero, and a geometric mean multiplies that into everything. D
measures standing across the galaxy; whether a given attack succeeds is a pairwise question
of troops against garrison that shares do not answer. The options are in the open items.

**Traits.** The client's trait list named all 46 engine traits by offset
(`client/dev_tools/ejbo_annotations.json`), and `sim/traits.py` applies each where its name
says. The custom-civ traits are engine traits 0 to 9 in order, so ship speed is engine 6 for
every ship, and engine 18 is light ships only. Measured: science wastage is trait 39, and
Doctrine: Knowledge's -7 turned the probe's 40 science into 43. A golden age adds 50 to the
food, production, science and mining traits, and the probe's 40 science became 64 (40 x 1.5
x 1.07). The golden age's turns left are the `u32` at `CVTR` payload `+12`, which read 99,
98, 97 on the turns after the doctrine.

**Doctrine probes** (November 2026, `sim/probes.py`). There were 29 engine ticks in three
galaxies, eight civs, and every one of the 23 doctrines was taken at least once, one a tick,
each paid from a 5,000,000-point store. Two captures advanced two turns in one tick and are
skipped by the oracle. Findings:

- **A doctrine shapes the tick it is bought in.** Doctrine: Trade's +4 a citizen paid 32
  credits that tick, its +5% food added 10, Stronghold's +2 a citizen paid 16, Habitat's +7%
  food added 10, and Metallurgy's +50 metal arrived at once. Science, though, is banked
  before the topic is paid. So the tick runs science, then research, then the rest of the
  planet step.
- **Trait 32 is a flat amount**: Doctrine: Elite's +1 makes a stationed soldier cost 4 credits
  a turn instead of 3. A civ with 2 soldiers paid exactly 2 more.
- **Training speed is trait 31 as a percent**, nothing else: Elite's +70 trained at 0.34 a
  turn with a Military Camp (0.2 x 1.7) and Police State's +50 more at 0.44. Trait 9 left
  training alone: the client's trait list names it ground-combat strength (below).
- **Ship units (trait 11) set speed**: Lightweight's -25 units moved a Colony Ship a third
  faster, exactly the engine's 5.085 more a turn.
- With those, the ten doctrines of the Single Player probe and the six of the Beta probe's
  cleanest civ match the engine on every field on every tick. In the governor-free q7 probe
  only the idle civ's known 1 to 5 credits of rounding remains. One Beta civ holding Elite,
  Juggernaut and Lightweight stays 1 to 3 credits high, which fits Elite's +1 also reaching
  its 3 crew members. The other Beta civ that holds Elite has a crewed ship too and does not
  fit that. It is small enough to leave.
- **No dark age follows a golden age.** A golden age staged at 2 turns left counted 2, 1, 0
  and stayed at 0; science fell from 635 a turn to 430, and the simulator with no penalty
  matched the engine for the four turns after. No technology grants trait 44.

**Every doctrine trait is applied.** The client's string table holds the trait list in
trait order, each with its tooltip, which names the traits the offsets left unclear: 9 is
the ground-combat strength of troops and stationed soldiers, 33 the food to recruit a
soldier, 16 and 20 hull space, 45 anonymous scanning. Each is applied as its tooltip says:

| trait | doctrines | applied |
|---|---|---|
| 9 ground strength % | Stronghold -5, Elite +20, Bio-Armor +10 | health and fire of every soldier in `combat.ground_side` |
| 16 hull space %, 20 battleship hull space % | Versatility +5, Invisibility +25 and -50, Titan +25 | `orders.hull_space`, added like the speed traits |
| 33 recruitment food % | Colonization -15 | a soldier costs 300 food, 255 at -15, which every corpus planet shows |
| 38 planet space % | Stronghold +10, Habitat +20 | already in the facility rules' space bonus |
| 42 market price % | Trade +50 | already in the server's market quote |
| 45 anonymous scanning | Intelligence | a spotted scan never names the scanner |

The reward uses the same trait-adjusted firepower, hitpoints, ship cost and upkeep as
combat and the economy, so a doctrine that strengthens a fleet raises D as well.

The probes did not exercise, because no battle, build or loyalty change happened while a
doctrine was held: firepower, hitpoints, shields, cloaking, scanner range, hull space,
ground strength, building, scan and ship cost modifiers, repair and the loyalty traits.
These follow their tooltips.

**Loyalty of conquered planets**, from the manual. A conquered planet's people stay loyal
to their old ruler in proportion to how long it held them, up to 100 turns. Loyalty grows 1
point a turn plus 10% of the planet's effective soldiers per citizen, and a citizen born
there is wholly loyal. A resisting planet loses its shortfall below 100% from its output,
and below 30% for more than three turns it may rebel back. The client's loyalty field was
never found, so a loaded planet reads 100%. Propaganda offices raise a loyal planet's output
10% for 10 citizens each.

**Score, reputation and crew experience**, from the manual. Crew experience is measured:
the turn-110 battle's defending fighters gained 1.847 each, 2 x 545 / 590, which is every kind
of firepower the enemy had over every kind its own side had, its turret included, and the
unarmed colony ship's crew gained nothing. Score and reputation follow the manual's page.

**Reputation raises or lowers corruption**, the manual's formula, as the original server
applied it. The client keeps reputation per relation and never reads it in play (Memory
Reconstruction Report), so a loaded civ starts at 0, the value every corruption fit so far
assumed, and only battles in the simulator move it. The score ratio divides by 80% of one's
own score. So attacking a civ of one's own size gains about 1.6 reputation (5 x log2 1.25),
and only attacking a civ under 80% of one's own score loses it.

**Bio-bombing**, from the manual, with no capture to check it. A ship with Bio Bombs takes
a bio-bomb order for a number of runs and sets a turn, inferred as order kind 6 (the order
injector lists 1 to 5 and 7). Each set kills 20 a turn, soldiers first, and soldiers in
bunkers and fortresses are killed 1 in 2 and 1 in 3 as often. Each set blights 5 space, which
recovers 1 a turn, and costs the bomber 0.1 reputation. It also turns the victim's former
people on the bomber's planets 1 loyalty point against it. Bio-bombing comes after
conventional bombing, and invasions wait while any bio-bombing ship is in orbit.

**Fog** (measured on `EXSY`). A civ's row for a system is refreshed every turn it owns a
planet there, and every turn a planet of the system is within the scanner range of one of
its ships: the scout's 30-unit scanner refreshed a system from a planet 19.3 away and not
from 32.6 away. Otherwise the row keeps the turn and the owners and populations it last
saw. A civ whose `Owner:4` is 0 keeps its rows frozen unless it is the civ the client plays
as, read from `GLOB`; its research freezes either way. Scored as the `fog` module on all
pairs, 99.6%. Ships are seen within scanner range, cloaked ones at 40% of it, from the
manual.

**Scans**, from the manual. They're built at a Central Defense Agency for 3,500 to 20,000
production and kept in stock. A planetary scan shows facilities, production, jobs and
soldiers. A technology scan shows research. Military, route and fleet scans show ships. A
system scan gives 8 turns of sight. A scan is spotted half the time, and half of those the
target learns who sent it. Which research unlocks which scan id is the technology table's;
which id is which scan is inferred beyond route (2) and fleet (3).

## 6. Open items

- [ ] Stamp a content version below 639 into served blobs (Stat Tables §8.1), so the
      simulator and the client use the same released tables
- [x] Settle the research cost model: the probes confirmed `round(800 x factor) x done`
      for every one of 31 technologies, so the AI player's table holds at 565
- [x] Add ground strength to D (§5b, "Ground strength in D")
- [ ] Decide how D combines its parts: the geometric mean still rates a fleetless but
      garrisoned civ near zero. Options: weighted sum of shares; pairwise attack and defence
      against each rival; a reputation or score factor (§5b)
- [ ] Repeat the D check with a learned policy
- [ ] Choose `K` from measured fleet travel times between rival homeworlds
- [ ] Decide whether planetary defence counts toward `S`, and at what weight: it holds a
      planet but cannot attack
- [ ] Find what the engine does when a civ cannot pay upkeep, with an oracle run, so `S`
      discounts an unpaid fleet the way the game does
- [ ] Set a speed target: a full tick in place takes about 0.3 ms on the Single Player
      galaxy; with a copy, 11 ms on 547 planets
- [x] Build the blob loader for the simulator's state subset: `GLOB`, owners, planets,
      ships and fleets, designs, treaties
- [x] Confirm the `SHPR` tail `f32` is condition: a ship damaged at turn 110 read 0.9238
- [ ] Confirm the sixth `SDPR` part list is shields (Reconstruction Report, `DSGN`)
- [x] Locate the research store: 4 bytes after credits in the civ's record
- [ ] Locate score and the +10-a-planet-a-turn accumulator in the blob: the civ record after
      the research store holds 0, the completed count and small fields, not either
- [x] Build the oracle harness and scoreboard (§5)
- [x] Capture clean pairs on the client: 214 pairs, from the 35 distinct referee inputs, an
      80-turn chain, four war blobs and the Single Player start, none failed
- [x] Capture a game with research completing: the four probes and the Beta galaxy
- [x] Market trades as an order, priced by `merge_orders.market_total`; the referee settles
      them between ticks, so the engine tick has no market rule to capture
- [ ] Find the inactivity rounding step: idle civs land 1 food off on some turns, and 5 of
      72 recruiting planet-turns; the civilian share itself was the loyalty bonus (§5b)
- [x] Food, corruption, inactivity, births, the store size and recruitment (§5b)
- [x] Production, mining, income and upkeep (§5b)
- [x] Science, ship completion and hurrying (§5b)
- [x] Scans (§5b), with the scan ids beyond route and fleet inferred
- [ ] Queues past their first item: skipped, the agent sets builds each turn
- [x] Doctrine and golden-age effects on science (§5b, "Traits")
- [x] Probe every doctrine (§5b, "Doctrine probes")
- [ ] Probe the doctrine traits a quiet galaxy does not exercise: battles for firepower,
      hitpoints, shields and cloaking, builds for the cost modifiers, a conquest for loyalty
- [x] Find what trait 9 does: ground-combat strength, from the client's trait list
- [x] Apply every doctrine trait (§5b, "Every doctrine trait is applied")
- [x] Colony ship surcharge: the manual gives the fitted formula,
      `(planets + colony ships - 1) x 100`; one build of five came out 100 over
- [ ] Find income's exact idle step: 1/144 a turn leaves idle civs 1 or 2 credits off
- [ ] Read reputation from the relation records (`KNPL` +0x10) into the loader
- [x] Research module (§5b)
- [x] Movement: speed, routes, arrival and the scout's next system (§5b)
- [x] Finished move and colonise orders, and colonisation (§5b)
- [x] Combat from the manual, checked against the corpus's one battle (§5b)
- [ ] More battles: heavy ships, shields, ranked crews, bombing that destroys turrets,
      conquest and ground combat, mid-space attacks; the Battle Calculator can supply them
- [x] Loyalty from stationed soldiers, their training and upkeep (§5b)
- [x] Reputation, score and crew experience (§5b)
- [x] Conquered planets' loyalty and rebellion (§5b), with the rebellion chance invented
- [ ] Find a planet's loyalty in the save, on a conquered planet
- [x] Fog to the client's rules (§5b)
- [x] Bio-bombs (§5b), from the manual
- [ ] Facilities lost to blighted space: the manual says bio-bombing destroys them
      indirectly, and the simulator destroys none
- [x] Observation and action interface on the simulator (`sim/env.py`, `sim/orders.py`)
- [ ] The client backend of the same interface: actions to `merge_orders` submissions
- [x] Viewer page for checking a run by eye (`sim/viewer.py`)
- [ ] Recruitment cap: 20% plus 20% with a Military Camp fits the captured rates, unconfirmed
- [ ] Drafting citizens into soldiers for credits, retiring soldiers, peace treaties
- [ ] First real-client evaluation of a sim-trained policy
