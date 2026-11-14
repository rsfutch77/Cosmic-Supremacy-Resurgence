# Training a Cosmic Supremacy Player

How a learned player is trained in the fast simulator (docs/Fast_Sim_Plan.md) and judged on
the real client.

---

## 1. Shape: a learned strategist over scripted roles

Robert chose the hybrid on 14 November 2026. A learned **strategist** sets goals every few
turns. Scripted **roles** turn those goals into orders each turn. Roles are replaced by learned
ones one at a time, once the strategist trains well over the scripted ones.

The alternatives, and why not first:

- **One policy for every order** (gsbp's learned general, scaled up). It can learn trade-offs
  across the economy and the fleet with nothing designed in between, but CS games run 200 turns
  or more against Antietam's 20 to 30, and gsbp's policy already drifted on the smaller problem.
- **A learned role for every job from the start.** Several levels learning at once each chase a
  moving target, and every role needs its own reward. gsbp's "share" credit showed how a wrong
  one teaches the wrong thing.

The hybrid's cost: the scripted roles cap how well the player can do until they are replaced,
and the goals set the limit of what the strategist can express.

## 2. Fairness

- The strategist and every role read only what the client shows the civ: its own empire, the
  systems it has looked at as they were then, contacts in scanner range, and its scan reports
  (`sim/env.py` `observe`, `sim/fog.py`).
- **A coach may see everything; the player may not** (gsbp's rule 4). The coach values a turn
  from the full state, for training only. Nothing it reads reaches the strategist or a role.
- Orders go through `sim/orders.py`, which checks them the way the client's screens do.

## 3. The strategist's goals

The strategist chooses every `STRATEGY_EVERY` turns. Each choice is a head that scores its
options, gsbp's way: one small network scores each option's feature row, and the option is
drawn from the softmax while training and taken greedily in play.

| Head | Options |
|---|---|
| Posture | expand, grow, arm, defend, balanced |
| Research line | economy, weapons, hulls, defence, intel |
| War | stay at peace, or declare war on one known rival |
| Fleet mission | guard home, or attack one known enemy planet |

- **Posture** sets what planets build and how citizens work. Expand builds colony ships, grow
  builds farms, factories and science, arm builds warships, defend builds turrets and soldiers.
- **Research line** picks the cheapest technology in that line that unlocks something, and the
  research step walks its prerequisites.
- **War** cannot be undone: the simulator has no peace treaty yet.
- **Fleet mission** targets come from the planets the civ has seen, with what it saw then.

Doctrines are left out of the first goal set: each excludes others, so the choice needs a head
of its own.

## 4. The scripted roles

| Role | Orders |
|---|---|
| Steward | citizens' jobs, recruitment, facility builds |
| Settler | colony ships: build, crew, pick a target, colonise |
| Scientist | the research goal in the chosen line |
| Shipwright | designs from researched parts: a warship, a bomber, a trooper and a colony ship |
| Admiral | warship and trooper builds, crews, gathering, and the mission's launch |

`sim/baseline.py` and `sim/warlike.py` were the starting points. Unlike the baseline, every role
works from what the civ can see. The baseline is a weak opponent: it never builds colony ships,
so it holds 2 or 3 planets for the whole game.

What the roles found about the game:

- **Births and soldiers both come from surplus food.** The store a birth needs grows with the
  population, so the farmers' margin over what the planet eats is the main growth lever, and an
  army needs farms as much as a growing empire does.
- **Soldiers board only at the planet they stand on**, so warships rally at the main shipyard.
- **Troops land only on a turn the planet's defence falls and no defending ship survives**
  (`combat.fight`). Only bomb weapons hurt the defence, and it repairs at once when the
  attackers leave, so bombers and troopers have to arrive with the warships.
- **A conquered planet starts at 100 loyalty less the turns its old owner held it**, so a
  homeworld starts at 0. Below 30 for more than 3 turns it may rebel back each turn, and only
  soldiers raise it quickly: 8 on 20 citizens lost BadGuy's homeworld again within 40 turns.
- **Parts that need a resource the civ holds none of make ships it cannot build.** Quantum
  Fields brought crystal parts to a civ with no crystal and stopped every shipyard, so the
  shipwright uses only parts the civ holds twice the resources for.

## 5. Learning

- **Reward:** dominance D (Fast_Sim_Plan 1a): the change in D each strategist step, and D at the
  horizon. An eliminated civ has D = 0.
- **The coach:** a critic over full-state numbers per civ (shares of S, T and G, planets,
  population, research, the turn's share of the horizon), fitted to TD(lambda) returns.
  Advantages are GAE on its values.
- **Updates:** PPO with a clipped ratio and an entropy bonus, numpy only, as gsbp's trainer.
- **Opponents:** the baseline, the warlike bot and a league of the strategist's past copies.
- **Galaxies:** the Single Player galaxy (2 civs), archived ticks (3 civs) and the Beta galaxy
  (6 civs), with some held out for evaluation only.
- **Judging:** many seeds per match. gsbp's 4-seed evaluations moved by 0.15 between
  checkpoints and could not pick a model.

Python 3.14 has numpy (2.3.5); the server venv the tests use does not. Training runs on 3.14
once the simulator is checked on it.

## 6. Milestones

- [x] **M1. Goals and roles.** `sim/strategy.py`: the goal set, the five roles, and a check that
      each posture and research line changes play the way it says (`sim/tests/test_strategy.py`,
      12 checks). Against the baseline over 150 turns: expand settles 8 to 10 planets where arm and
      defend hold 3, arm with weapons research fields the strongest fleet, defend with defence
      research builds 13 turrets and 6 bunkers. Expanding to turn 90 and then arming at war
      destroys BadGuy's fleet and takes his homeworld at turn 293, and holds it
  - [x] Waves were sized on the ships last seen at the target, which went stale: a wave of 12
        left for 8 seen and met 19. Waves now wait for twice the most ships ever seen there
  - [x] Conquest. A wave of warships, 4 bombers and 4 troopers arrives on one turn; troops land
        the turn the defence falls; emptied troopers refill wherever soldiers stand and land
        again in fours; a taken planet below 60 loyalty gets every full trooper's soldiers
  - [ ] The second landing takes 43 turns to gather while the garrison regrows from 6 to 13:
        a Shuttle trooper lands 5, and the warships' crews compete for the same soldiers
  - [ ] No scan counts the garrison, so landings are sized by attrition, not by intel. A
        planetary scan needs Advanced Scanning and a Central Defense Agency
  - [ ] Credits pile up once production runs out of things to build (40,000 by turn 150 on
        some goals): a market role could turn them into the ship resources the shipwright lacks
  - [ ] Ships built at a second shipyard wait for crew there, since soldiers board only where
        they stand; recruitment does not follow the shipyards
  - [ ] The first scout leaves around turn 30: the capital builds a colony ship first
- [ ] **M2. Fixed-goal sweep.** Play every constant goal against the baseline and the warlike
      bot. It tells whether the goal set holds a winning strategy at all, and whether D ranks
      the goals the way the games end
- [ ] **M3. A learned strategist.** PPO with the coach on the Single Player galaxy against the
      baseline and the warlike bot
- [ ] **M4. Self-play.** A league of past copies, and the 3 and 6-civ galaxies
- [ ] **M5. Learned roles.** Replace one role at a time, each with its own coach
- [ ] **M6. The real client.** The client backend of the order interface (Fast_Sim_Plan open
      item), then a trained player's first games on the client

## 7. Open questions

- [ ] How often the strategist chooses: every 5 turns to start
- [ ] Whether the simulator has enough randomness for many distinct games from one galaxy, or
      starting states must vary instead
- [ ] What the simulator's open items cost a trained player on the client: income under
      reputation, and the 11 battle pairs of 64 that do not match
