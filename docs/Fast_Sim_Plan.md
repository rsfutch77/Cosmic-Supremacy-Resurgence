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

A design's `SDPR` holds six part lists, each a `u32` count then `u32` part ids: chassis,
scanners, engines, weapons, modules, shields. The sixth has been empty in all 1,081
designs on disk.

## 6. Open items

- [ ] Stamp a content version below 639 into served blobs (Stat Tables §8.1), so the
      simulator and the client use the same released tables
- [ ] Re-dump the research table on a released-version client and settle the cost model
      (Stat Tables §8.2)
- [ ] Check that `D` predicts who wins: pit civs of known `D` against each other in the
      simulator and confirm the higher one wins most wars (§1a)
- [ ] Choose `K` from measured fleet travel times between rival homeworlds
- [ ] Decide whether planetary defence counts toward `S`, and at what weight: it holds a
      planet but cannot attack
- [ ] Find what the engine does when a civ cannot pay upkeep, with an oracle run, so `S`
      discounts an unpaid fleet the way the game does
- [ ] Set a speed target after profiling the first economy-only tick
- [x] Build the blob loader for the simulator's state subset: `GLOB`, owners, planets,
      ships and fleets, designs, treaties
- [ ] Confirm the `SHPR` tail `f32` is condition, and the sixth `SDPR` part list is shields.
      Both are open items in the Reconstruction Report (the fleets list and `DSGN`); until
      then the loader reads them that way
- [ ] Locate the research stockpile (`Owner:12`) and score in the blob, which the loader
      does not read yet
- [ ] Build the oracle harness and scoreboard (§5)
- [ ] Economy module: production, food, income, upkeep, growth, corruption
- [ ] Research and market modules
- [ ] Movement and colonisation modules
- [ ] Combat from the manual, checked against the Battle Calculator (§4)
- [ ] Minimal reputation and loyalty, fitted against conquest captures
- [ ] Fog: what each civ's observation may contain, matching the client's scan rules
- [ ] Shared observation and action interface, client backend included
- [ ] Viewer page for checking a run by eye
- [ ] First real-client evaluation of a sim-trained policy
