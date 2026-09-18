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

These are the intended released stats, **read out of the client's own definition
tables**, not transcribed from the manual. Section 4a covers which modes select them. `components.py` dumps them.

Six categories, one table each, laid out exactly like the facility table of section 4 and
selected by content version the same way (section 4a). Names are stored in the record, so
the ids below are the record's own position, which is what a `ShipDesign` part vector
stores. Every id order previously derived from the manual's page order is confirmed
correct by this read, all 43 records.

Units is the hitpoint contribution. Cost is production, and the resource columns are in
addition to it. Weapons carry three separate firepower values, against light ships, heavy
ships and planets, which is the `ShipDesign:60/64/68` triple.

### Engines (`ShipDesign:176`)

| Id | Name | Units | Space | Cost | Upkeep | Thrust | Metal | Deut | Radio | Cryst | Exot |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | Nuclear Drive | 5 | 10 | 120 | 1 | 270 | 4 | - | 6 | - | - |
| 1 | Fusion Drive | 10 | 20 | 450 | 2 | 820 | 5 | 15 | - | - | - |
| 2 | Quantum Drive | 40 | 80 | 2400 | 8 | 4450 | 40 | - | - | 40 | - |
| 3 | Gravity Drive | 70 | 120 | 4500 | 12 | 9200 | 30 | - | 90 | - | - |
| 4 | Anti-Matter Drive | 20 | 30 | 1300 | 3 | 3200 | - | 20 | - | 10 | - |
| 5 | Dark-Matter Drive | 50 | 60 | 3000 | 7 | 9000 | - | - | 50 | - | 10 |
| 6 | Singularity Drive | 280 | 280 | 16000 | 35 | 60000 | 100 | - | - | - | 180 |

### Weapons (`ShipDesign:200`)

One id space across light weapons, heavy weapons and bombs. Ids 0-3 are the light weapons, 4-9 the heavy, 10-14 the bombs.

| Id | Name | Units | Space | Cost | Upkeep | FP light | FP heavy | FP planet | Metal | Deut | Radio | Cryst | Exot |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | Mass Driver | 12 | 20 | 300 | 2 | 35 | 10 | 0 | 20 | - | - | - | - |
| 1 | Pulse Laser | 6 | 10 | 180 | 1 | 30 | 0 | 0 | 4 | - | 6 | - | - |
| 2 | Beam Laser | 48 | 80 | 1900 | 10 | 285 | 50 | 0 | 30 | 50 | - | - | - |
| 3 | Proton Laser | 30 | 50 | 1500 | 7 | 240 | 50 | 0 | 10 | - | 40 | - | - |
| 4 | Photon Cannon | 24 | 30 | 600 | 4 | 20 | 50 | 0 | 30 | - | - | - | - |
| 5 | Ion-Pulse Cannon | 96 | 120 | 3250 | 18 | 125 | 350 | 0 | 40 | 80 | - | - | - |
| 6 | Proton Torpedo | 64 | 80 | 2650 | 13 | 0 | 435 | 0 | 20 | - | 60 | - | - |
| 7 | Anti-Matter Torpedo | 32 | 40 | 1350 | 6 | 0 | 260 | 0 | 10 | 30 | - | - | - |
| 8 | Particle Cannon | 160 | 200 | 8700 | 39 | 500 | 1550 | 0 | - | 140 | - | 60 | - |
| 9 | Wormhole Infiltrator | 48 | 60 | 3000 | 13 | 0 | 850 | 0 | - | - | 30 | - | 30 |
| 10 | Fusion Bomb | 50 | 50 | 750 | 4 | 0 | 0 | 500 | 10 | 40 | - | - | - |
| 11 | Plasma Bomb | 200 | 200 | 3550 | 20 | 0 | 0 | 2700 | - | 40 | 160 | - | - |
| 12 | Anti-Matter Bomb | 80 | 80 | 1650 | 10 | 0 | 0 | 1350 | - | 60 | - | 20 | - |
| 13 | Dark-Matter Bomb | 120 | 120 | 3000 | 18 | 0 | 0 | 2700 | - | - | 90 | - | 30 |
| 14 | Planet Buster | 250 | 250 | 7500 | 43 | 0 | 0 | 7500 | - | - | - | 50 | 200 |

### Chassis (`ShipDesign:128`)

For a chassis the Space column is space PROVIDED, not consumed. Chassis 0-2 are the light size class and 3-5 the heavy, which is what the turrets' two firepower columns discriminate on.

| Id | Name | Units | Space | Cost | Upkeep | Crew | Metal | Deut | Radio | Cryst | Exot |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | Shuttle | 20 | 120 | 225 | 2 | 1-2 | 60 | - | - | - | - |
| 1 | Corvette | 80 | 210 | 600 | 4 | 1-3 | 105 | - | - | - | - |
| 2 | Frigate | 180 | 400 | 1950 | 12 | 1-4 | 200 | - | - | - | - |
| 3 | Destroyer | 400 | 650 | 5700 | 20 | 3-7 | 200 | - | - | 125 | - |
| 4 | Cruiser | 900 | 1050 | 10800 | 45 | 4-10 | 275 | - | - | 250 | - |
| 5 | Battleship | 1800 | 1500 | 27000 | 100 | 5-14 | 300 | - | - | 450 | - |

### Modules (`ShipDesign:224`)

| Id | Name | Units | Space | Cost | Upkeep |  | Metal | Deut | Radio | Cryst | Exot |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | Colony Module | 20 | 90 | 150 | 4 |  | 90 | - | - | - | - |
| 1 | Troop Bay | 30 | 90 | 450 | 10 |  | 90 | - | - | - | - |
| 2 | Large Pilot Cabin | 50 | 100 | 600 | 10 |  | 100 | - | - | - | - |
| 3 | Cloaking Device | 20 | 30 | 2400 | 5 |  | - | - | - | 90 | - |
| 4 | Bio Bombs | 100 | 100 | 4200 | 14 |  | - | - | 100 | - | 100 |
| 5 | Wormhole Generator | 300 | 200 | 30000 | 12 |  | - | 80 | 100 | 100 | 1000 |

### Scanners (`ShipDesign:152`)

| Id | Name | Units | Space | Cost | Upkeep | Range | Metal | Deut | Radio | Cryst | Exot |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | Neutron Scanner | 5 | 0 | 100 | 1 | 30 | 2 | - | - | - | - |
| 1 | Tachyon Scanner | 10 | 10 | 300 | 2 | 50 | 2 | - | 8 | - | - |
| 2 | Subspace Scanner | 15 | 20 | 600 | 5 | 70 | - | - | - | 20 | - |
| 3 | Hyperspace Scanner | 30 | 20 | 1200 | 10 | 100 | 10 | - | - | - | 10 |

### Shields

No fitted-shield vector is read from a design yet, only the `ShipDesign:72` shield stat.

| Id | Name | Units | Space | Cost | Upkeep | Strength | Metal | Deut | Radio | Cryst | Exot |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | Magneto Shield | 20 | 20 | 450 | 2 | 10 | 20 | - | - | - | - |
| 1 | Quantum Shield | 50 | 30 | 1000 | 4 | 25 | - | - | - | 30 | - |
| 2 | Anti-Grav Shield | 80 | 40 | 1650 | 6 | 50 | - | - | 40 | - | - |
| 3 | Gaussian Shield | 350 | 140 | 7350 | 24 | 250 | 80 | - | - | 60 | - |
| 4 | Warp Shield | 200 | 80 | 5100 | 15 | 200 | - | - | 60 | - | 20 |

### Field map

Shared by every category: `+0x00` id, `+0x04` units, `+0x0C` production cost, `+0x10`
**upkeep as a float** where every other field is an int. Then a category-specific block,
then five int resource costs in the canonical order metal, deuterium, radioactives,
crystal, exotics.

| Category | Stride | Category block | Resources | Name |
|---|---|---|---|---|
| engines, scanners, shields | `0x8C` | `+0x08` space, `+0x14` thrust / range / strength | `+0x18` | `+0x30` |
| weapons | `0x94` | `+0x08` space, `+0x14/+0x18/+0x1C` firepower light / heavy / planet | `+0x20` | `+0x38` |
| chassis | `0x94` | `+0x14` space provided, `+0x18/+0x1C` crew min and max | `+0x20` | `+0x38` |
| modules | `0xA8` | `+0x08` space | `+0x14` | `+0x4C` |

### Where the manual is wrong

The manual matches the client on 43 records and every field but five numbers. The client
wins on all five; none of the manual's values appears in **any** of the superseded tables
in the client, so these are errors in the wiki rather than an older content version.

| Component | Field | Client | Manual |
|---|---|---|---|
| Anti-Matter Drive | metal | 0 | 5 |
| Anti-Matter Drive | deuterium | 20 | 25 |
| Plasma Bomb | cost | 3550 | 2700 |
| Anti-Matter Bomb | cost | 1650 | 1350 |
| Dark-Matter Bomb | cost | 3000 | 2700 |

Four of the five make bombs and the anti-matter drive cheaper than they really are, so a
plan costed from the manual would come out under budget.

## 4. Planetary facilities

These are the intended released stats. Section 4a covers which modes actually select this table today, because two of them do not.

The client holds a facility definition table of 21 records at 0x8C bytes each, ids 0 to
20. Every column below is read straight out of that table by `facilities.py`, including
**the facility names, which are stored in the record** as an MSVC `std::string` at `+0x30`.
That makes the id map a direct read rather than a derivation, and it confirmed the
grants-table derivation 21 out of 21. The only wording difference is id 8, really
"Central Defense Agency" where the code uses the shorthand "defence agency".

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
| 9 | Light Turret | 27 Planetary Defense Lvl 1 | 3 | 600 | 8 | 1000 units, 400/100 firepower |  |
| 10 | Heavy Turret | 30 Planetary Defense Lvl 4 | 6 | 3000 | 32 | 2000 units, 200/1000 firepower |  |
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

Every published value here matches the wiki manual exactly. Three did not come from the
manual at all and are reads: the Command Center's space, cost and upkeep, which the manual
never printed.

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

## 4a. Content versions, and where the other tables are

The table address is not fixed. `[0x0080AA00]` holds a content version, and the picker at
`0x00529CB0` uses it to choose among **eight** facility tables before returning
`base + id * 0x8C`. All eight are present and populated in a running client.

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

This is almost certainly a galaxy-freeze mechanism. A galaxy that had been running for
months should not have its economy rewritten when the developer shipped a client update,
so a galaxy carries the content version it started under and keeps the stat table that
goes with it. New galaxies get the new one.

**Two shipped modes currently select the WRONG table.** Measured by launching each mode
and reading `[0x0080AA00]` and the picker's answer:

| Mode | Started as | Content version | Table | Correct |
|---|---|---|---|---|
| Tutorial, Demo | `CosmicSupremacy.exe` + `.csgalaxy` | 171 | `0x00807DB0` | yes |
| Sandbox | `CosmicSupremacy_Resurgence.exe` + `.csgalaxy` | 565 | `0x00807DB0` | yes |
| **Single Player** | `CosmicSupremacy_Resurgence.exe` + `SinglePlayerGalaxy.dat` | **99999** | `0x008066B0` | **no** |
| **Multiplayer** | `CosmicSupremacy_Player.exe` + a served turn blob | **99999** | `0x008066B0` | **no** |

### Where the version actually comes from

Not the EXE, and not the entry action. **The save blob carries it.** In the `GLOB` section
it is the dword immediately before the trailing local-player id, the one
`set_blob_player.set_player` rewrites. In `client/SinglePlayerGalaxy.dat` that field sits at
file offset 900 and reads 99999.

That splits the modes cleanly:

- A `.csgalaxy` pass file carries no blob, so the client keeps whatever the version already
  is: the `.data` image default of 565, or 171 where the Tutorial and Demo entry path calls
  the setter at `0x0052A7C4`. Both are below 639, so both get the released table.
- A `.dat` blob push takes the version **from the blob**. Every blob this project has
  generated was captured from a TestBed session, so they all carry 99999, and 99999 selects
  the unreleased table.

Single Player and Multiplayer are both blob-push modes, which is why both are wrong.

**The fix is one dword, and it is verified.** Patching that field from 99999 to 565 in a
copy of `SinglePlayerGalaxy.dat` and launching it moved the client to `0x00807DB0` with the
Light Turret at 600/3 and the Heavy Turret at 3000/6. It belongs in two places: the shipped
`SinglePlayerGalaxy.dat`, and whatever authors or stamps a blob server-side, so that every
galaxy served to a player carries a content version below 639.

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

The stats are closed. Both the facility table and all six component tables are read out of
the running client by `facilities.py` and `components.py`, names included, and they agree
with the manual everywhere except the five numbers in section 3 where the client wins.
Nothing in sections 3 to 6 is a derivation any more.

Two of the earlier open items closed along the way, and are recorded here because the
wrong answers were written down first:

- **Command Center upkeep is 13.** The manual has no stat block for that facility at all.
- **Citizen job ids.** A stationed military unit and a ship crew member are the same
  16-byte citizen record with **job 3**, confirmed by drafting a citizen and reading
  `Planet:168` back as job id 3. The UI string table at `0x0035209D` lists exactly six
  jobs, Military / Banker / Scientist / Miner / Worker / Farmer, which are ids 3/6/2/5/1/0.
  **Id 4 is in no table and no UI list.** An earlier revision of this document guessed id 4
  was "military"; that was wrong, because 3 already is.

What is left is not stat recovery:

1. **Stamp a content version below 639 into every served blob.** See section 4a. This is
   a live bug, not a theoretical one: Single Player and Multiplayer both run at 99999 today
   and get the unreleased stat tables. The field is one dword in the blob's `GLOB` section,
   the fix is verified, and it wants doing in two places, the shipped
   `client/SinglePlayerGalaxy.dat` and the server-side blob authoring path.
2. **Research cost model.** `research.cost` implements the manual's
   `round(800 * costFactor) * completed-count`, but `research.available` still calls the
   cost model unsolved, and `research.py`'s table was extracted at a different content
   version from the one the released client runs. The research table is selected by the
   same setter through a pointer at `0x00857F08`, so the version ladder is the likely
   explanation for the table having looked wrong. Re-dumping it against a released-version
   client is the obvious next step, and `research_dump.py` already exists to do it.
3. **Player-Rank thresholds above rank 2**, and the ranked-galaxy fame bonus. Neither is in
   the manual and neither is in the client, since both were server-side. Ours to choose.
