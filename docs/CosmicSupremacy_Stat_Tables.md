## 1. Game Stat Tables

Component, facility and formula stats recovered from the official wiki manual, joined
to the object ids the client actually uses. 

## 2. Source 

`tools/wayback/cosmicsupremacy_mirror/cosmicsupremacy.com/wiki/manual/`,
pages `ship-components`, `planetary-modules`, `planetary-defense`, `fame`,
`score-reputation-and-rank`, `hyperspace-energy-grid`, `command-centers`. The mirror is
gitignored, so a fresh clone does not carry it. The same pages are published under
`site/public/wiki/manual/`.

---

## 3. Ship components

Units is the hitpoint contribution. Prod cost is production, and the resource columns are
in addition to it. Firepower is three separate columns in the source, against light ships,
heavy ships, and planets, which is the `ShipDesign:60/64/68` triple.

### Engines (`ShipDesign:176`)

| Id | Name | Units | Space | Prod cost | Upkeep | Thrust | Metal | Deut | Radio | Cryst | Exot |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | Nuclear Drive | 5 | 10 | 120 | 1 | 270 | 4 | - | 6 | - | - |
| 1 | Fusion Drive | 10 | 20 | 450 | 2 | 820 | 5 | 15 | - | - | - |
| 2 | Quantum Drive | 40 | 80 | 2400 | 8 | 4450 | 40 | - | - | 40 | - |
| 3 | Gravity Drive | 70 | 120 | 4500 | 12 | 9200 | 30 | - | 90 | - | - |
| 4 | Anti-Matter Drive | 20 | 30 | 1300 | 3 | 3200 | 5 | 25 | - | 10 | - |
| 5 | Dark-Matter Drive | 50 | 60 | 3000 | 7 | 9000 | - | - | 50 | - | 10 |
| 6 | Singularity Drive | 280 | 280 | 16000 | 35 | 60000 | 100 | - | - | - | 180 |

### Weapons, light (`ShipDesign:200`)

| Id | Name | Units | Space | Prod cost | Upkeep | FP light | FP heavy | FP planet | Metal | Deut | Radio | Cryst | Exot |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | Mass Driver | 12 | 20 | 300 | 2 | 35 | 10 | 0 | 20 | - | - | - | - |
| 1 | Pulse Laser | 6 | 10 | 180 | 1 | 30 | 0 | 0 | 4 | - | 6 | - | - |
| 2 | Beam Laser | 48 | 80 | 1900 | 10 | 285 | 50 | 0 | 30 | 50 | - | - | - |
| 3 | Proton Laser | 30 | 50 | 1500 | 7 | 240 | 50 | 0 | 10 | - | 40 | - | - |

### Weapons, heavy

Same id space as the light weapons.

| Id | Name | Units | Space | Prod cost | Upkeep | FP light | FP heavy | FP planet | Metal | Deut | Radio | Cryst | Exot |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 4 | Photon Cannon | 24 | 30 | 600 | 4 | 20 | 50 | 0 | 30 | - | - | - | - |
| 5 | Ion-Pulse Cannon | 96 | 120 | 3250 | 18 | 125 | 350 | 0 | 40 | 80 | - | - | - |
| 6 | Proton Torpedo | 64 | 80 | 2650 | 13 | 0 | 435 | 0 | 20 | - | 60 | - | - |
| 7 | Anti-Matter Torpedo | 32 | 40 | 1350 | 6 | 0 | 260 | 0 | 10 | 30 | - | - | - |
| 8 | Particle Cannon | 160 | 200 | 8700 | 39 | 500 | 1550 | 0 | - | 140 | - | 60 | - |
| 9 | Wormhole Infiltrator | 48 | 60 | 3000 | 13 | 0 | 850 | 0 | - | - | 30 | - | 30 |

### Weapons, bombs

Same id space again. Id 10 is the Fusion Bomb, matching the observed value.

| Id | Name | Units | Space | Prod cost | Upkeep | FP light | FP heavy | FP planet | Metal | Deut | Radio | Cryst | Exot |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 10 | Fusion Bomb | 50 | 50 | 750 | 4 | 0 | 0 | 500 | 10 | 40 | - | - | - |
| 11 | Plasma Bomb | 200 | 200 | 2700 | 20 | 0 | 0 | 2700 | - | 40 | 160 | - | - |
| 12 | Anti-Matter Bomb | 80 | 80 | 1350 | 10 | 0 | 0 | 1350 | - | 60 | - | 20 | - |
| 13 | Dark-Matter Bomb | 120 | 120 | 2700 | 18 | 0 | 0 | 2700 | - | - | 90 | - | 30 |
| 14 | Planet Buster | 250 | 250 | 7500 | 43 | 0 | 0 | 7500 | - | - | - | 50 | 200 |

### Chassis (`ShipDesign:128`)

| Id | Name | Units | Prod cost | Upkeep | Space | Crew | Class | Metal | Deut | Radio | Cryst | Exot |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | Shuttle | 20 | 225 | 2 | 120 | 1-2 | Light | 60 | - | - | - | - |
| 1 | Corvette | 80 | 600 | 4 | 210 | 1-3 | Light | 105 | - | - | - | - |
| 2 | Frigate | 180 | 1950 | 12 | 400 | 1-4 | Light | 200 | - | - | - | - |
| 3 | Destroyer | 400 | 5700 | 20 | 650 | 3-7 | Heavy | 200 | - | - | 125 | - |
| 4 | Cruiser | 900 | 10800 | 45 | 1050 | 4-10 | Heavy | 275 | - | - | 250 | - |
| 5 | Battleship | 1800 | 27000 | 100 | 1500 | 5-14 | Heavy | 300 | - | - | 450 | - |

### Modules (`ShipDesign:224`)

The description column is omitted here; the manual page carries it.

| Id | Name | Units | Space | Prod cost | Upkeep | Metal | Deut | Radio | Cryst | Exot |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | Colony Module | 20 | 90 | 150 | 4 | 90 | - | - | - | - |
| 1 | Troop Bay | 30 | 90 | 450 | 10 | 90 | - | - | - | - |
| 2 | Large Pilot Cabin | 50 | 100 | 600 | 10 | 100 | - | - | - | - |
| 3 | Cloaking Device | 20 | 30 | 2400 | 5 | - | - | - | 90 | - |
| 4 | Bio Bombs | 100 | 100 | 4200 | 14 | - | - | 100 | - | 100 |
| 5 | Wormhole Generator | 300 | 200 | 30000 | 12 | - | 80 | 100 | 100 | 1000 |

### Scanners (`ShipDesign:152`)

| Id | Name | Units | Space | Prod cost | Upkeep | Range | Metal | Deut | Radio | Cryst | Exot |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | Neutron Scanner | 5 | 0 | 100 | 1 | 30 | 2 | - | - | - | - |
| 1 | Tachyon Scanner | 10 | 10 | 300 | 2 | 50 | 2 | - | 8 | - | - |
| 2 | Subspace Scanner | 15 | 20 | 600 | 5 | 70 | - | - | - | 20 | - |
| 3 | Hyperspace Scanner | 30 | 20 | 1200 | 10 | 100 | 10 | - | - | - | 10 |

### Shields

No fitted-shield vector is read from a design yet, only the `ShipDesign:72` shield stat.

| Id | Name | Units | Space | Prod cost | Upkeep | Strength | Metal | Deut | Radio | Cryst | Exot |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | Magneto Shield | 20 | 20 | 450 | 2 | 10 | 20 | - | - | - | - |
| 1 | Quantum Shield | 50 | 30 | 1000 | 4 | 25 | - | - | - | 30 | - |
| 2 | Anti-Grav Shield | 80 | 40 | 1650 | 6 | 50 | - | - | 40 | - | - |
| 3 | Gaussian Shield | 350 | 140 | 7350 | 24 | 250 | 80 | - | - | 60 | - |
| 4 | Warp Shield | 200 | 80 | 5100 | 15 | 200 | - | - | 60 | - | 20 |
## 4. Planetary facilities

The client holds a facility definition table of 21 records at 0x8C bytes each, ids 0 to
20. Every column below is read straight out of that table by `facilities.py`, including
**the facility names, which are stored in the record** as an MSVC `std::string` at `+0x30`.
That makes the id map a direct read rather than a derivation. The names confirmed the
grants-table derivation 21 out of 21, the only wording difference being id 8, whose real
name is "Central Defense Agency" where the code uses the shorthand "defence agency".

Where two values are shown as `a / b`, they are content-version dependent; see section 4a.
`a` is what a modern galaxy uses and `b` is what the manual documents.

| Id | Facility | Unlocked by | Space | Cost | Upkeep | Effect | 1/planet |
|---|---|---|---|---|---|---|---|
| 0 | Farm | 1 Space Travel | 7 | 200 | 7 | +25% food per farmer |  |
| 1 | Factory | 2 Astro Engineering | 7 | 400 | 10 | +20% production per worker |  |
| 2 | Shipyard | 1 Space Travel | 25 | 500 | 17 | builds and repairs ships | yes |
| 3 | Automated Factory | 40 Advanced Manufacturing | 11 | 1800 | 18 | +50% production per worker |  |
| 4 | University | 18 Advanced Networking | 9 | 700 | 16 | +25% science per scientist |  |
| 5 | Science Lab | 19 Artificial Intelligence | 15 | 2200 | 24 | +60% science per scientist |  |
| 6 | Military Camp | 18 Advanced Networking | 14 | 1500 | 12 | +20% max recruitment rate |  |
| 7 | Military Academy | 20 Advanced Tactics | 34 | 9000 | 28 | military rank up to 6 | yes |
| 8 | Central Defense Agency | 23 Basic Scanning | 26 | 6000 | 30 | allows scans to be built | yes |
| 9 | Light Turret | 27 Planetary Defense Lvl 1 | 2 / 3 | 400 / 600 | 8 | 1000 units, 400/100 FP |  |
| 10 | Heavy Turret | 30 Planetary Defense Lvl 4 | 4 / 6 | 2000 / 3000 | 32 | 2000 units, 200/1000 FP |  |
| 11 | Shield Generator | 29 Planetary Defense Lvl 3 | 14 | 2000 | 45 | 3000 units, +40% shield |  |
| 12 | Propaganda Office | 36 Propaganda | 9 | 1200 | 12 | +10% loyalty for 10 citizens |  |
| 13 | Mine | 35 Mining | 6 | 600 | 13 | +40% resources per miner |  |
| 14 | Robo Mine | 37 Robo Mining | 12 | 1300 | 20 | +100% resources per miner |  |
| 15 | Bunker | 38 Bunker | 5 | 700 | 10 | +100% defence, shelters 4 |  |
| 16 | Banking Center | 39 Banking | 12 | 1800 | 17 | +40% income per citizen |  |
| 17 | Planetary Fortress | 51 Planetary Fortress | 8 | 1200 | 15 | +200% defence, shelters 8 |  |
| 18 | Hyperspace Transmitter | 69 Hyperspace-Energy Lvl 1 | 9 | 2500 | 20 | generates 2000 TW |  |
| 19 | Hyperspace Receiver | 69 Hyperspace-Energy Lvl 1 | 16 | 8000 | 50 | 100 units, boosts PD | yes |
| 20 | Command Center | 78 Command Center | 10 | 1500 | 13 | +30% firepower in orbit |  |

Upkeep is per turn. Effect summarises the `+0x20` bonus magnitude joined to the manual's
description of what it does. "1/planet" marks the facilities that may only be built once
per planet, which are exactly the ones exempt from the cost escalation in section 5.

### Field map

| Offset | Meaning |
|---|---|
| `+0x00` | facility id |
| `+0x04` | planetary-defence hitpoints contributed, the manual's "Units" |
| `+0x08` | shield strength percent, non-zero only on the shield generator |
| `+0x0C` | light firepower, scaled by `(defenceTechBonus + 100) / 100` |
| `+0x10` | heavy firepower, same scaling |
| `+0x14` | production cost, halved when `GetGameOption(1)` is set |
| `+0x18` | upkeep per turn |
| `+0x1C` | planet space occupied |
| `+0x20` | primary bonus magnitude, percent or TW depending on the effect |
| `+0x24` | 1 when only one may be built per planet |
| `+0x30` | name (`std::string`); `+0x50` repeats it without spaces |

Two fields were previously mislabelled in `facilities.py`. `+0x04` was "defence class";
it is the hitpoint contribution, and non-zero happens to mean "is defensive", which is all
any caller used it for. `+0x1C` was described only as the value `CanBuildFacility` compares
against something from `0x004F3100`; it is planet space, and `0x004F3100` supplies the
planet's free space, so the old note was right without knowing what it had.

## 4a. Content versions, and why the table address is not fixed

**The stats are content-version dependent.** `[0x0080AA00]` holds a content version, and
the picker at `0x00529CB0` uses it to choose among **eight** different facility tables
before returning `base + id * 0x8C`. All eight are present in a running client and all
eight are fully populated with different numbers:

| Selected when | Base |
|---|---|
| version < 8 | `0x0080A370` |
| version < 10 | `0x00809CE0` |
| version < 13 | `0x00809650` |
| version < 57 | `0x00808FC0` |
| a helper at `0x0052A790` returns false | `0x00808930` |
| version >= 639 and `GetGameOption(3)` set | `0x00807230` |
| version < 646 | `0x00807DB0` |
| otherwise | `0x008066B0` |

The version is not a build constant. The binary's `.data` image holds 565, a setter at
`0x0052A7C4` overwrites it at runtime (and picks the research table at `0x00857F08` in the
same breath), and a TestBed galaxy against the stub server runs at **99999**, which selects
`0x008066B0`.

`facilities.py` previously hardcoded `0x00807DB0`, the version 57..645 table, and named the
constant `TABLE_565`. It now ports the ladder. The practical damage was small but real:
the two tables differ in exactly four fields.

| Facility | Field | version < 646 | version >= 646 |
|---|---|---|---|
| Light Turret | cost | 600 | 400 |
| Light Turret | space | 3 | 2 |
| Heavy Turret | cost | 3000 | 2000 |
| Heavy Turret | space | 6 | 4 |

Everything else is identical across the two, and `0x00807230` is byte-identical to
`0x008066B0` over every field read here, so the `GetGameOption(3)` branch cannot change an
answer and is not ported.

**The wiki manual documents the version < 646 stats.** It was last edited in 2012 and
gives 600/3 and 3000/6 for the turrets. That is a useful calibration on the manual
generally: it is accurate for the content version it was written against, and this project
is running a later one.

**This is a Phase 2 decision, not just a bug.** A replacement server chooses what content
version it serves, and that choice changes the game's balance. Nothing yet decides it, and
the stub does not send a version at all, which is why the client sits at 99999.

## 5. Facility cost escalation

Each successive facility on a planet costs more than the last:

```
Build-Cost = (4 + (3 + Already-Existing-Facility-Count) * Already-Existing-Facility-Count)
             * Facility-Base-Production-Cost / 4
```

`Already-Existing-Facility-Count` is the count on that planet. A few facilities are
one-per-planet (Shipyard, Hyperspace Receiver, Military Academy, Central Defense Agency)
and are exempt. `facilities.build_cost` currently returns the raw base cost from the live
table's `+0x14` field and does not apply this formula, so it is the base, not the price of
the next build.

## 6. Planetary defense scaling

Three facilities carry planetary defense, and their strength scales with the Planetary
Defense tech level (techs 27 to 34, levels 1 to 8). Base values at level 1:

| Facility | Units | Light firepower | Heavy firepower |
|---|---|---|---|
| Light Turret | 1000 | 400 | 100 |
| Heavy Turret | 2000 | 200 | 1000 |
| Shield Generator | 3000 | +40% shield strength | . |

The level multiplier is a single ladder applied to all three:

| Level | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|---|---|
| Bonus | +0% | +30% | +60% | +120% | +240% | +450% | +750% | +1200% |
| Multiplier | 1.0 | 1.3 | 1.6 | 2.2 | 3.4 | 5.5 | 8.5 | 13.0 |

The manual's Shield Generator table prints the Bonus column as a repeated "+30%" down to
level 7 and then "+1,200%" at level 8, which is a transcription error on that page. Its
own Shield Strength values (40, 52, 64, 88, 136, 220, 340, 520) are 40% times the ladder
above, identical to the turret tables. Use the ladder.

This matches the `facilities.py` field map, where `+0x0C` and `+0x10` are the two defence
strength components scaled by `(defenceTechBonus + 100) / 100`. The two components are
light firepower and heavy firepower, and `defenceTechBonus` is the Bonus row above.

A planet fires back automatically at hostile ships in orbit with no attack order, provided
the two civs are at war. Hitpoints fully repair the instant an attacking fleet withdraws
without finishing the planet.

## 7. Fame and score

Both formulas are needed for Phase 2 key system 3, the Galaxy-Fame leaderboard.

```
Galaxy-Total-Fame  = (Galaxy-Player-Count * 20) * (1 + (Galaxy-Rank * 0.25))
Galaxy-Fame-Points = Galaxy-Total-Fame * Your-Score / Galaxy-Overall-Combined-Score
```

Fame is awarded once, at the moment a galaxy ends. A fixed pool per galaxy is split by
each player's share of the combined score. Player-Rank 1 needs at least 15 fame, Rank 2
needs more than 200. The manual gives no thresholds above Rank 2. Ranked galaxies are said
to award more fame, but the multiplier is not stated beyond the `Galaxy-Rank` term.

Score during play comes from combat. Destroying ship or planetary-defense units awards one
score point per two units destroyed, gated on destroying at least one ship or one defence
facility in the battle, then scaled by

```
Score-Ratio = Defender-Score / (Attacker-Score * 0.8)
```

The 0.8 is a deliberate break-even shift: attacking an equally scored player gives a ratio
of 1.2 rather than 1.0.

## 8. What is still open

The facility side is closed. The table carries its own names, upkeep, bonus magnitudes and
one-per-planet flags, and `facilities.py` now reads all of them from whichever table the
content version selects. Command Center upkeep is **13**, which the manual never published.

1. **Which content version the server should serve.** See section 4a. This is a design
   decision for Phase 2 rather than a gap: the stub sends no version, the client falls back
   to 99999, and that silently picks the cheapest-turret balance. Worth deciding
   deliberately and writing into the server.
2. **Whether the ship-component stats are also version-dependent.** Very likely, by
   symmetry: the research table is chosen the same way (`research_dump.py` calls it "six
   tables"), and the same setter picks it. The component tables in section 3 come from the
   manual, so if they vary they describe the version < 646 content. Nothing has been read
   out of the client to check, and there is no equivalent of `facilities.py` for components
   yet.
3. **Citizen job id 4.** Not resolved by observation. A live scan found only jobs 0, 1 and
   2 on planets and 3 on ship crew, because miner and banker need techs the TestBed galaxy
   has not researched. The inference is **"military"**: the UI string table at `0x0035209D`
   holds a `<MilitaryMed>` / `Military` pair in the same contiguous run as the five civilian
   jobs and their icons, and 4 is the only free id in the citizen range. `gamestate.JOBS[4]`
   carries that caveat in the source.
4. **Research cost model.** `research.cost` implements the manual's
   `round(800 * costFactor) * completed-count`, but `research.available` still calls the
   cost model unsolved, and `research.py`'s table was extracted at a different content
   version from the one this client runs. Both are worth revisiting together.
5. **Player-Rank thresholds above rank 2**, and the ranked-galaxy fame bonus. Neither is in
   the manual and neither is in the client, since both were server-side. Ours to choose.
