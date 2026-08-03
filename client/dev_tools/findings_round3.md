# Findings: static analysis, Round 3

Method as in Round 2: capstone over `client/CosmicSupremacy_Resurgence.exe` (32-bit,
image base 0x00400000), VA -> file offset through the PE section table. **No process was
attached, read, or launched at any point.** Every claim below is "the binary says",
never "the game did".

* **CONFIRMED** — the instruction bytes say it, and I quote them.
* **inferred** — consistent with the bytes, not fully proven. Called out every time.

Prerequisite reading: `client/dev_tools/findings_production_path.md` (Round 2). Offset
frames from Round 2 are reused and held up everywhere they were tested:

> `Planet:N` == `PlanetProperties + (N - 88)` (PlanetProperties embedded at Planet
> allocation `+0x60`); `Owner:N` == `owner_primary + (N + 52)` (primary = tag-52);
> `Ship:N` == `ship_allocation + (N + 8)`; `ShipDesign:N` == `ShipDesignData + (N - 4)`.

**Round 3 corrects two Round 2 claims.** Both are called out where they occur and
collected in section 4.

---

## Objective 1 — the research table this client actually uses

### 1.1 The initialiser, and the extraction

Round 2 established (and live cross-check confirmed) that `0x0054A650` selects one of
six tables by the content version at `[0x0080AA00]`, and that version 565 selects the
80-record table at **0x0085D410**. That table is built by the static initialiser
**0x0072E430** — the immediate sibling of 0x00729210, starting right where it ends.
CONFIRMED by the reference census: 0x0072E430 makes **2422** references into
0x0085D410..0x0086291F, against 239 for the atexit-destructor thunk block at
0x0070B3E0 and 1 for the array-destruct registration.

Same extraction technique as Round 2 — interpret the initialiser instruction by
instruction, tracking immediate register loads, `mov [abs], reg|imm`,
`fld`/`fldz`/`fld1` + `fstp [abs]`, `std::string::assign` (0x00404AB0, `ecx` = the
destination string, pushed `char*`), and `memset` (0x006712C0) for the zero tails.
All 80 records came out with every field populated (0x00 id, 0x04 flags, 0x08 factor,
three strings, prerequisite counts and lists, effect count and effects, exclusion count
and list). Record layout is unchanged from Round 2 section 2.2.

### 1.2 The diff against 0x00857F10 — only four records differ

**The two tables are the same table with four doctrine tweaks.** No name, no
`costFactor`, no prerequisite, no exclusion and no unlock differs anywhere. The only
changes are civilization-trait deltas on four doctrines. CONFIRMED (79/80 records
byte-identical in every extracted field; the fourth column is the live table):

| id | name | 0x00857F10 (newest) | 0x0085D410 (**live**) |
|---|---|---|---|
| 54 | Doctrine: Stronghold | Trait 30 **-15**, Trait 8 +15, Trait 38 **+5**, Trait 1 +2, Trait 9 **-15** | Trait 30 **-10**, Trait 8 +15, Trait 38 **+10**, Trait 1 +2, Trait 9 **-5** |
| 59 | Doctrine: Propaganda | Trait 35 **+85**, Trait 4 +7, Trait 3 **+2**, **Trait 14 +15** | Trait 35 **+100**, Trait 3 **+7**, Trait 4 +7 *(Trait 14 dropped)* |
| 62 | Doctrine: Habitat | Trait 38 +20, Trait 30 **-10**, **Trait 2 -15**, **Trait 19 -15** | Trait 38 +20, **Trait 2 +7**, Trait 30 **-15**, **Trait 5 -15** |
| 67 | Doctrine: Golden Age | Trait 43 **+75**, **Trait 44 +35** | Trait 43 **+100** *(Trait 44 dropped)* |

So for **cost and prerequisites — the two things asked about — the tables are
identical.** Round 2's table was not wrong about those; it was the wrong table only in
these four doctrine effect rows.

### 1.3 Hard check (c): the ratio is 3.0 in both tables, so this is case (d)

| table | id 2 `Astro Engineering` | id 3 `Cold Fusion` | ratio |
|---|---|---|---|
| 0x00857F10 | factor **3.0** | factor **1.0** | **3.0** |
| 0x0085D410 (live) | factor **3.0** | factor **1.0** | **3.0** |

The live observation (id 2 = 4800, id 3 = 2400) fixes the ratio at 2.0. **No table in
the binary produces 2.0**, so a single constant scale cannot explain both observations.
That falsifies the "multiplier is 1" inference Round 2 explicitly flagged, and sends us
to 1.4.

### 1.4 Case (d): the multiplier is the **completed-research count**

CONFIRMED. `GetCost` is `0x0054B140`, `__thiscall`, `ret 4`, computing
`round(scale x costFactor x multiplier)`. Reading its call sites, the multiplier is
always the number of entries in the completed-research vector. From the research tick
`0x00545610`:

```
005456cd  8b86a4000000  mov eax, dword ptr [esi + 0xa4]   ; Owner:112  _Mylast
005456d3  2b86a0000000  sub eax, dword ptr [esi + 0xa0]   ; Owner:108  _Myfirst
005456df  c1f803        sar eax, 3                        ; /8 -> COMPLETED COUNT
005456e2  8d9ec4000000  lea ebx, [esi + 0xc4]             ; Owner:144, the current topic record
005456e8  50            push eax                          ; <-- the multiplier
005456e9  8bcb          mov ecx, ebx
005456eb  e8505a0000    call 0x54b140                     ; GetCost(completedCount)
```

The same shape appears at every other site: 0x0043A2BE, 0x005456CD, 0x0053E206 and
0x0053E1BC all compute `([owner+0xa4] - [owner+0xa0]) >> 3` and push it. `esi` is the
Owner primary — `+0xa0`/`+0xa4` are `Owner:108`/`Owner:112`, the completed-research
vector of 8-byte `[id, cost]` records, so the quotient is its element count.

> **cost = round(scale x costFactor x completedResearchCount)**

**The scale this implies is 800, and it is self-consistent across all three live
observations.** The count is read *before* the completion append (both reads at
0x005456CD and 0x00545703 precede the `push_back` at 0x00545731 — CONFIRMED), so at the
moment a topic completes the multiplier is the count *before* it is added:

| live observation | factor | count at completion | `800 x factor x count` |
|---|---|---|---|
| doctrine 43 `Mobility` recorded as `[43, 1600]` | 1.0 | 2 (the granted techs 0 and 1) | **1600** ✓ |
| id 2 `Astro Engineering` = 4800 | 3.0 | 2 | **4800** ✓ |
| id 3 `Cold Fusion` = 2400 | 1.0 | 3 (0, 1 and id 2) | **2400** ✓ |

Three for three, with one free parameter. The formula shape is **CONFIRMED** from the
instruction bytes; `scale = 800` is **inferred** — it is a three-point fit, and the
scale itself is a per-game config value (`0x0054A6F0` returns
`0x004D4320(0x15)->[+0x28]` on a version >= 150 client; `0x004D4320` is
`GetGameOption(int)` over an array of 0x6C-byte records at
`[0x00853EC4]`..`[0x00853EC8]`, built at runtime, so it is not readable from the file
image). Recalibrate with `scale = observedCost / (costFactor x countAtCompletion)` if a
future game disagrees.

**The recorded cost is trustworthy for calibration.** The tick writes GetCost's own
output into the record it appends:

```
0054571e  e81d5a0000  call 0x54b140            ; cost = GetCost(count)
00545723  294640      sub dword ptr [esi + 0x40], eax   ; Owner:12 stockpile -= cost
00545726  89442418    mov dword ptr [esp + 0x18], eax   ; store cost into the copy
00545731  e85a0ff3ff  call 0x476690                     ; push_back {id, cost} -> Owner:108
```

`owner_primary + 0x40` = `Owner:12`, the research stockpile — and the subtraction (not
a zeroing) is exactly the "carries the remainder" behaviour already in the report.

### 1.5 Consequence the AI must model: research gets more expensive as you research

Because the multiplier is the completed count and the cost is recomputed **every turn**
(0x005456EB runs on each tick before the affordability test), the price of the currently
selected topic **rises by `scale x costFactor` each time anything completes**. The Nth
item of factor `f` costs `800 x f x N`. Two practical effects:

* Cheap-first is not merely cheap, it is compounding: every early completion inflates
  everything after it. Ordering matters far more than Round 2's static table suggested.
* A topic selected and left running gets more expensive mid-flight if something else
  completes. There is no locked-in price.

**inferred** but mechanically forced by the code: with the tick recomputing the cost
each turn, total spend to reach a set S of items is
`800 x sum over the chosen order of (factor_i x position_i)`, so **researching low-factor
items first is strictly worse for total spend on a fixed target set**, and high-factor
items should be taken as early as their prerequisites allow.

### 1.6 The live table (0x0085D410, 80 records, content version 565)

`cost@800` is `round(costFactor x 800)` — the cost of that item as the **1st**
completion; multiply by the completed count at the time. `mode` is the prerequisite
combination rule from section 2.3 (`AND` for technologies, `OR` for doctrines).

| id | name | factor | cost@800 | prereq | mode | grants | excludes |
|---|---|---|---|---|---|---|---|
| 0 | Nuclear | 0 | 0 | - | - | Scanner 0, Engine 0 | - |
| 1 | Space Travel | 0 | 0 | - | - | Chassis 0, Facility 0, Module 0, Facility 2 | - |
| 2 | Astro Engineering | 3 | 2400 | 12 | AND | Facility 1, Chassis 1, Module 2 | - |
| 3 | Cold Fusion | 1 | 800 | 0 | AND | Engine 1, Weapon 0, Weapon 10 | - |
| 4 | Tachyon Physics | 3.2 | 2560 | 13 | AND | Weapon 2 | - |
| 5 | Robotics Engineering | 15 | 12000 | 22 | AND | Chassis 3 | - |
| 6 | Gravity Controller | 9 | 7200 | 7 | AND | Engine 3, Shield 2 | - |
| 7 | Quantum Fields | 2.8 | 2240 | 3,12 | AND | Engine 2, Shield 1 | - |
| 8 | Anti-Matter Fission | 25 | 20000 | 15 | AND | Weapon 12, Weapon 7 | - |
| 9 | Dark-Matter Control | 24 | 19200 | 41 | AND | Engine 5 | - |
| 10 | Subspace Controller | 35 | 28000 | 9 | AND | Shield 3, Engine 6 | - |
| 11 | Hyperspace Controller | 47 | 37600 | 10 | AND | Shield 4 | - |
| 12 | Advanced Magnetism | 1 | 800 | 1 | AND | Shield 0, Module 1 | - |
| 13 | Photon Mechanics | 2 | 1600 | 3 | AND | Weapon 1, Weapon 4 | - |
| 14 | Plasma Physics | 11 | 8800 | 4 | AND | Weapon 5, Weapon 11 | - |
| 15 | Proton Controller | 17 | 13600 | 14 | AND | Weapon 6, Weapon 3 | - |
| 16 | Nano Mechanics | 34 | 27200 | 40 | AND | Chassis 4 | - |
| 17 | Superstring Theory | 51 | 40800 | 16 | AND | Chassis 5 | - |
| 18 | Advanced Networking | 1 | 800 | 1 | AND | Facility 4, Facility 6 | - |
| 19 | Artificial Intelligence | 14 | 11200 | 24 | AND | Facility 5, Scan 6 | - |
| 20 | Advanced Tactics | 24 | 19200 | 19 | AND | Facility 7, Scan 4, Scanner 2 | - |
| 21 | Deep-Space Scanning | 32 | 25600 | 20 | AND | Scanner 3, Scan 1 | - |
| 22 | Particle Engineering | 8 | 6400 | 2 | AND | Chassis 2 | - |
| 23 | Basic Scanning | 3.6 | 2880 | 18 | AND | Facility 8, Scanner 1, Scan 2, Scan 3 | - |
| 24 | Advanced Scanning | 10 | 8000 | 23 | AND | Scan 0, Scan 5 | - |
| 25 | Wormhole Controller | 49 | 39200 | 42 | AND | Weapon 9 | - |
| 26 | Magnetic Monopoles | 62 | 49600 | 25 | AND | Weapon 14 | - |
| 27 | Planetary Defense Lvl 1 | 1 | 800 | - | - | PlanDef 0, Facility 9 | - |
| 28 | Planetary Defense Lvl 2 | 2.5 | 2000 | 27 | AND | PlanDef 1 | - |
| 29 | Planetary Defense Lvl 3 | 5 | 4000 | 28 | AND | PlanDef 2, Facility 11 | - |
| 30 | Planetary Defense Lvl 4 | 10 | 8000 | 29 | AND | PlanDef 3, Facility 10 | - |
| 31 | Planetary Defense Lvl 5 | 17 | 13600 | 30 | AND | PlanDef 4 | - |
| 32 | Planetary Defense Lvl 6 | 27 | 21600 | 31 | AND | PlanDef 5 | - |
| 33 | Planetary Defense Lvl 7 | 41 | 32800 | 32 | AND | PlanDef 6 | - |
| 34 | Planetary Defense Lvl 8 | 57 | 45600 | 33 | AND | PlanDef 7 | - |
| 35 | Mining | 6 | 4800 | 2 | AND | Facility 13 | - |
| 36 | Propaganda | 2 | 1600 | 18 | AND | Facility 12 | - |
| 37 | Robo Mining | 17 | 13600 | 5,35 | AND | Facility 14 | - |
| 38 | Bunker | 1.5 | 1200 | 27 | AND | Facility 15 | - |
| 39 | Banking | 2 | 1600 | 18 | AND | Facility 16 | - |
| 40 | Advanced Manufacturing | 22 | 17600 | 5 | AND | Facility 3 | - |
| 41 | Anti-Matter Control | 16 | 12800 | 6 | AND | Engine 4 | - |
| 42 | Dark-Matter Fission | 36 | 28800 | 8 | AND | Weapon 13, Weapon 8 | - |
| 43 | Doctrine: Mobility | 1 | 800 | - | OR | Trait 18 +20, Trait 10 -10 | 44,45,46 |
| 44 | Doctrine: Colonization | 1 | 800 | - | OR | Trait 23 -50, Trait 33 -15 | 43,45,46 |
| 45 | Doctrine: Reproduction | 1 | 800 | - | OR | Trait 26 -30, Trait 0 -1 | 43,44,46 |
| 46 | Doctrine: Metallurgy | 1 | 800 | - | OR | Trait 41 +50, Trait 5 +15, Trait 29 -30 | 43,44,45 |
| 47 | Doctrine: Knowledge | 2.7 | 2160 | 43,44 | OR | Trait 28 -30, Trait 39 -7, Trait 13 +20 | 48,49 |
| 48 | Doctrine: Manufacturing | 2.3 | 1840 | 43,44,45,46 | OR | Trait 27 -30, Trait 3 +5 | 47,49 |
| 49 | Doctrine: Trade | 2 | 1600 | 45,46 | OR | Trait 42 +50, Trait 37 +15, Trait 1 +4, Trait 6 +5, Trait 2 +5 | 47,48 |
| 50 | Doctrine: Stealth | 6.5 | 5200 | 47,48,43 | OR | Trait 12 +5, Trait 24 -75, Trait 17 +10, Trait 7 +15, Trait 19 -15 | 54,55,56 |
| 51 | Planetary Fortress | 8 | 6400 | 29 | AND | Facility 17 | - |
| 52 | Biological Warfare | 3 | 2400 | 13 | AND | Module 4 | - |
| 53 | Cloaking | 11 | 8800 | 24 | AND | Module 3 | - |
| 54 | Doctrine: Stronghold | 7.5 | 6000 | 47,48 | OR | Trait 30 -10, Trait 8 +15, Trait 38 +10, Trait 1 +2, Trait 9 -5 | 50,55,56 |
| 55 | Doctrine: Finance | 7 | 5600 | 47,48,48,49 | OR | Trait 37 +75, Trait 5 -7 | 50,54,56 |
| 56 | Doctrine: Elite | 6 | 4800 | 48,49 | OR | Trait 31 +70, Trait 9 +20, Trait 32 +1 | 50,54,55 |
| 57 | Doctrine: Intelligence | 15 | 12000 | 50,54,47 | OR | Trait 18 +15, Trait 13 +30, Trait 12 +5, Trait 39 -5, Trait 25 -75, Trait 45 +1 | 58,59,60 |
| 58 | Doctrine: Versatility | 16 | 12800 | 50,54 | OR | Trait 40 +150, Trait 16 +5, Trait 14 -5, Trait 6 +5 | 57,59,60 |
| 59 | Doctrine: Propaganda | 15.5 | 12400 | 50,54,54,55,56 | OR | Trait 35 +100, Trait 3 +7, Trait 4 +7 | 57,58,60 |
| 60 | Doctrine: Juggernaut | 16 | 12800 | 54,55,56 | OR | Trait 17 +25, Trait 15 -10 | 57,58,59 |
| 61 | Doctrine: Invisibility | 27 | 21600 | 50 | OR | Trait 12 +10, Trait 16 +25, Trait 17 +5, Trait 19 -80, Trait 20 -50 | 62,63,64 |
| 62 | Doctrine: Habitat | 30 | 24000 | 57,58,59,60 | OR | Trait 38 +20, Trait 2 +7, Trait 30 -15, Trait 5 -15 | 61,63,64 |
| 63 | Doctrine: Police State | 29 | 23200 | 57,58,59,60 | OR | Trait 34 +75, Trait 31 +50, Trait 7 +25 | 61,62,64 |
| 64 | Doctrine: Dreadnought | 28 | 22400 | 60 | OR | Trait 19 +30, Trait 17 +20 | 61,62,63 |
| 65 | Doctrine: Lightweight | 45 | 36000 | 61,62,63,64 | OR | Trait 11 -25, Trait 15 -30 | 66,67,68 |
| 66 | Doctrine: Bio-Armor | 47 | 37600 | 61,62,63,64 | OR | Trait 22 +7, Trait 17 +5, Trait 9 +10, Trait 8 +10 | 65,67,68 |
| 67 | Doctrine: Golden Age | 50 | 40000 | 61,62,63,64 | OR | Trait 43 +100 | 65,66,68 |
| 68 | Doctrine: Titan | 46 | 36800 | 64 | OR | Trait 20 +25, Trait 21 -5 | 65,66,67 |
| 69 | Hyperspace-Energy Lvl 1 | 9 | 7200 | 29 | AND | HypEnergy 0, Facility 18, Facility 19 | - |
| 70 | Hyperspace-Energy Lvl 2 | 15 | 12000 | 69 | AND | HypEnergy 1 | - |
| 71 | Hyperspace-Energy Lvl 3 | 25 | 20000 | 70 | AND | HypEnergy 2 | - |
| 72 | Hyperspace-Energy Lvl 4 | 37 | 29600 | 71 | AND | HypEnergy 3 | - |
| 73 | (unused stub) | 0 | 0 | - | - | - | - |
| 74 | (unused stub) | 0 | 0 | - | - | - | - |
| 75 | (unused stub) | 0 | 0 | - | - | - | - |
| 76 | (unused stub) | 0 | 0 | - | - | - | - |
| 77 | Artificial Wormholes | 13 | 10400 | 19 | AND | Module 5 | - |
| 78 | Command Center | 20 | 16000 | 20 | AND | Facility 20 | - |
| 79 | Surveillance Scanning | 19 | 15200 | 19 | AND | Scan 7 | - |

Note ids 55 and 59 show a duplicated prerequisite (48 and 54 respectively). That is real
— it is list A concatenated with list B, and B repeats a member of A. Harmless under OR.

---

## Objective 2 — are research prerequisites enforced, and where?

### 2.1 Everything funnels through one function: `0x0053DF80`

The prerequisite walker `0x0054B260` has **exactly two callers in the whole binary**:
`0x0053E102` (inside 0x0053DF80) and `0x0054B456` (its own recursion). CONFIRMED by an
exhaustive direct-call scan. The exclusion check `0x0054B7D0` has three: 0x0053E0BD
(inside 0x0053DF80) plus 0x00453723 / 0x0045391E, which are the research-tree UI control.

So `0x0053DF80` is **the single non-UI choke point**:

```
__thiscall  void SetResearchTopic(researchRecord* req)      ; 0x0053DF80, ret 4, ecx = Owner primary
```

It is reached from exactly two places:

* **the `STRS` command handler `0x00573A40`** — the "set research" path a UI click takes.
  `__cdecl`, arg1 = a pointer to an 8-byte `{id, cost}` record. Online it sends
  section tag **"STRS"** (`push 0x53525453`) carrying the topic id and applies the
  server's reply. Offline (`[0x0086F1A0] != 0`) it first checks the id **exists in the
  filtered topic list**:

  ```
  00573b6e  e8fd6dfdff  call 0x54a970          ; the per-game filtered topic container
  00573ba5  3910        cmp dword ptr [eax], edx    ; scan for an element whose id == requested
  00573ba9  83c008      add eax, 8
  00573bae  75f5        jne 0x573ba5
  00573bb0  85c9        test ecx, ecx ; je <bail>
  00573bbb  e8c0a3fcff  call 0x53df80          ; then hand it to SetResearchTopic
  ```

  That is an **existence** check, not a prerequisite check.
* **the research tick `0x00545610`**, at 0x0054573F, immediately after a completion — to
  auto-pick what to research next.

### 2.2 The COMPLETE path has no prerequisite check at all

CONFIRMED, and this is the direct explanation of the observed behaviour. The whole of
the tick's completion gate is two tests:

```
005456eb  e8505a0000  call 0x54b140                     ; cost = GetCost(completedCount)
005456f0  39442410    cmp dword ptr [esp + 0x10], eax
005456f4  0f8c5f0100  jl  <done>                        ; stockpile < cost -> nothing happens
005456fa  833bff      cmp dword ptr [ebx], -1
005456fd  0f8456fe..  je  <done>                        ; topic id == -1 -> nothing happens
          ... subtract cost, append {id, cost} to Owner:108, pick the next topic
```

**Stockpile >= cost, and topic id != -1. That is the entire validation.** No call to
0x0054B9E0, 0x0054B260 or 0x0054B7D0 anywhere on this path. So a topic id written
straight into `Owner:144` completes on schedule regardless of prerequisites, exclusions,
or whether the id is even in the game's filtered list. Our AI's id-2-without-id-12
completion is not a fluke; it is the documented behaviour of this code path.

### 2.3 `0x0054B9E0` decoded — **AND for technologies, OR for doctrines**

This settles Objective 2(b), and it **corrects Round 2**, which inferred a global OR
from the data shape.

```
__thiscall  bool ArePrerequisitesMet(container* completedWrapper)   ; 0x0054B9E0, ret 4
```
`ecx` = the `{id, cost}` record. It branches on the record's **exclusion count**:

```
0054b9f9  83bc08f800000000  cmp dword ptr [eax + ecx + 0xf8], 0   ; exclusionCount
0054ba01  0f84f0000000      je  0x54baf7                          ; == 0 -> BRANCH B
```

**Branch A — records WITH exclusions (the doctrines), 0x0054BA07..0x0054BAE9: OR.**
For each prerequisite in turn it scans the completed vector, and:

```
0054baaa  3bd1  cmp edx, ecx
0054baac  0f8549010000  jne 0x54bbfb     ; scan did NOT run off the end -> FOUND -> return TRUE
0054bab2  43    inc ebx                  ; not found -> try the next prerequisite
0054bae5  3bd9  cmp ebx, ecx ; jb <loop>
0054bae9  32c0  xor al, al ; ret 4       ; none of them found -> FALSE
```

**Branch B — records WITHOUT exclusions (the technologies), 0x0054BAF7..: AND.**
Same scan, opposite polarity:

```
0054bbaf  3bc1  cmp eax, ecx
0054bbb1  0f8432ffffff  je 0x54bae9      ; scan ran off the end -> NOT FOUND -> return FALSE
0054bbb7  47    inc edi                  ; found -> require the next one too
0054bbf3  3bf8  cmp edi, eax ; jb <loop>
0054bbfb  b001  mov al, 1 ; ret 4        ; every prerequisite found -> TRUE
```

Exits are unambiguous: `0x0054BBFB` is `mov al,1; ret 4` and `0x0054BAE9` is
`xor al,al; ret 4`. Both branches index the prerequisite list through `0x0054B200`,
which presents list A followed by list B as **one flat list** — so the A/B split is pure
storage and irrelevant to the predicate.

This resolves the paradox Round 2 used to argue for OR. Id 48 `Doctrine: Manufacturing`
listing all four mutually exclusive tier-1 doctrines is fine, because doctrines are OR.
And id 7 `Quantum Fields` requiring `[3, 12]` genuinely needs **both** Cold Fusion and
Advanced Magnetism — the thematically sensible reading Round 2 talked itself out of.
Same for id 37 `Robo Mining` needing both Robotics Engineering and Mining.

### 2.4 `0x0054B7D0` decoded — exclusions cause outright rejection

```
__thiscall  bool IsSelectable(container* completed, container* plan)   ; 0x0054B7D0, ret 8
```
For each id in the record's exclusion list it scans the completed vector; if any is
present it returns **FALSE** (`0x0054B8A5: xor al,al; ret 8`). If the record has no
prerequisites at all it returns TRUE (`0x0054B89C: mov al,1; ret 8`); otherwise it runs a
second pass over the *plan queue* argument.

At the call site the polarity is decisive:

```
0053e0bd  e80ed70000  call 0x54b7d0
0053e0c2  84c0        test al, al
0053e0c4  744b        je 0x53e111
...
0053e111  8b742414    mov esi, dword ptr [esp + 0x14]
0053e115  81c6cc000000 add esi, 0xcc
0053e11b  c706ffffffff mov dword ptr [esi], 0xffffffff   ; staged topic = -1  == rejected
```

**So on the legal path, taking a second doctrine in the same exclusive tier is refused
and the selection becomes "nothing".** Answering 2(c): if you *write* a second tier-1
doctrine id into `Owner:144` directly, **nothing stops you** — 0x0054B7D0 is on the
select path only, and 2.2 shows the completion path checks neither exclusions nor
prerequisites.

### 2.5 `0x0054B260` is not a validator — it is a dependency expander

```
__thiscall  void ExpandPrerequisites(completed*, scratch*, planQueue*)  ; 0x0054B260, ret 0xC
```
It recurses into itself at 0x0054B456. `0x0053DF80` calls it **only when the requested
topic's prerequisites are not met**, and then appends the requested topic last:

```
0053e0c9  e812d90000  call 0x54b9e0                  ; prereqs met?
0053e0d8  84c0        test al, al
0053e0da  740d        je 0x53e0e9
0053e0dc  ... [Owner:152] = req                      ; met -> stage it directly
0053e0e9  ... [Owner:152] = req                      ; not met:
0053e0f4  83f9ff      cmp ecx, -1 ; je <done>
0053e102  e859d10000  call 0x54b260                  ;   expand the missing chain into the plan
0053e10a  e88185f3ff  call 0x476690                  ;   then push_back the target LAST
```

and then promotes from the plan queue — picking **the cheapest entry whose prerequisites
are already met**:

```
0053e191  bd00ca9a3b  mov ebp, 0x3b9aca00            ; best = 1e9
0053e1b3  e828d80000  call 0x54b9e0                  ; is this plan entry researchable now?
0053e1d8  e863cf0000  call 0x54b140                  ; cost = GetCost(completedCount)
0053e1dd  3bc5        cmp eax, ebp ; jge <skip>      ; keep the cheapest
0053e1fd  8911        mov dword ptr [ecx], edx       ; -> Owner:144 (id)
0053e203  894104      mov dword ptr [ecx + 4], eax   ; -> Owner:148 (cost)
```

So the UI does not reject an unreachable topic — **it builds a research plan and works
towards it**, starting from the cheapest currently-legal step. That is a far more useful
model of the game than "prerequisites are enforced", and it is what an external
controller should imitate.

New Owner offsets this exposes (all CONFIRMED from the code above, `Owner:N` =
`owner_primary + N + 52`):

| annotation | code offset | meaning |
|---|---|---|
| `Owner:96` | +0x94 | completed-research container **wrapper** (its vector is `Owner:108/112/116`) |
| `Owner:120` | +0xAC | **research plan queue** wrapper; vector at `Owner:132/136` |
| `Owner:144` | +0xC4 | **current topic id** |
| `Owner:148` | +0xC8 | current topic's cost as last computed |
| `Owner:152` | +0xCC | staged/pending topic record; `-1` means "rejected / nothing selected" |

### 2.6 Verdict (2d): it is an exploit, and here is the narrowest legal rule

**Plainly: no, a human at the UI cannot achieve "write any research id into the topic
field".** A human's click goes through `0x00573A40` -> `0x0053DF80`, which (a) requires
the id to exist in the per-game filtered topic list, (b) refuses it outright if an
exclusive sibling is already taken, and (c) if its prerequisites are unmet, does **not**
select it — it selects the cheapest legal step towards it instead. Writing `Owner:144`
directly skips all three. Since the completion path validates nothing, the write
completes a topic the UI would never have started. That is an exploit, not a shortcut.

The narrowest rule that keeps the AI inside what a human could do, using only fields we
already read:

1. Let `done` = the id set in `Owner:108`.
2. Reject `id` if `id in done`; reject ids 73-76 (empty stubs); reject any id not in the
   80-record table.
3. Reject `id` if any member of its **exclusion list** is in `done`.
4. Require prerequisites by class:
   * `exclusionCount == 0` (technology): **every** prerequisite must be in `done`.
   * `exclusionCount != 0` (doctrine): **at least one** prerequisite must be in `done`
     (or the list is empty).
5. Then write `Owner:144 = id`. Optionally also write `Owner:148`; the tick recomputes
   the cost anyway.

If you want to imitate the UI exactly rather than merely stay legal, replace step 4 with
the planner: when prerequisites are unmet, choose instead the cheapest id that passes
2-4 and lies on a path to the target — that is what `0x0053DF80` does, and it also gets
you the compounding-cost ordering from 1.5 for free.

---

## Objective 3 — combat

### 3.1 (a) Where combat resolves — CONFIRMED

**`0x00523AC0`.** It is the combat driver, and it names itself in its own log lines:

```
00523af3  68d8d77600  push 0x76d7d8      ; "computing battles..."
005240a1  ...         push 0x76d7b0      ; "computing battles done (took %.3f sec)"
```

It is **stage 16 of the turn pipeline**, `0x0056D130`, called at `0x0056D1C3`:

```
0056d1a4  e86784fdff  call 0x545610      ; research tick (see 2.2)
0056d1a9  e882dff6ff  call 0x4db130
0056d1ae  e87dc7f5ff  call 0x4c9930
0056d1b3  e8a83cfdff  call 0x540e60
0056d1b9  e88224f6ff  call 0x4cf640
0056d1be  e8ed0cf6ff  call 0x4cdeb0
0056d1c3  e8f868fbff  call 0x523ac0      ; <-- COMBAT
0056d1c8  e8a32bf6ff  call 0x4cfd70
```

`0x0056D130` is itself the top of the turn processing: a flat sequence of ~40 stage
calls, and the research tick 0x00545610's only caller. That makes 0x0056D130 the
**reading order anchor for everything turn-related** — it is worth annotating stage by
stage in a future round.

Before the ship work, the driver runs a per-planet pre-pass:

```
00523b32  8b0cb1      mov ecx, dword ptr [ecx + esi*4]   ; planets[i]  (global array [0x008553C4]..[0x008553C8])
00523b35  83c160      add ecx, 0x60                      ; -> its PlanetProperties
00523b38  e883e0fcff  call 0x4f1bc0                      ; per-planet battle prep
```

**inferred**: 0x004F1BC0 resets or arms planetary defence for the turn. Not decoded.

### 3.2 (b) What decides engagement — proximity, not an order type

**The driver never reads the order type.** CONFIRMED by exhaustive scan: across all 493
instructions of 0x00523AC0 there is **not one** access at `+0x38`, `+0x3c` or `+0x40` —
i.e. it never touches `Ship:48` (order object), `Ship:52` (order type) or `Ship:56`
(order target). What it does instead is bucket every ship by **position**:

```
00523b52  e8695afbff  call 0x4d95c0             ; the global ship container
00523c7c  8b3ca8      mov edi, dword ptr [eax + ebp*4]    ; ships[i]
00523c7f  8b4730      mov eax, dword ptr [edi + 0x30]     ; Ship:40 owner node
00523c82  8b00        mov eax, dword ptr [eax]
00523c8e  83c0d4      add eax, -0x2c                      ; -> Owner primary
00523ca0  8d470c      lea eax, [edi + 0xc]                ; Ship:4/8/12  POSITION
00523cab  e860fcffff  call 0x523910                       ; bucket by (position, owner)
00523cd4  8939        mov dword ptr [ecx], edi            ; push_back the ship into that bucket
```

Then it walks the buckets (an MSVC `std::map`; the `byte [node+0x39]` tests are `_Isnil`)
and gates on the number of distinct owners present:

```
00523e26  837f2802    cmp dword ptr [edi + 0x28], 2
00523e2a  0f82c1010000 jb 0x523ff4               ; fewer than 2 -> no battle here, skip
```

Each bucket is also matched to the object at that location within a fixed radius:

```
00523c01  d9052c7d7600 fld dword ptr [0x767d2c]  ; = 0.800000011920929
00523c0b  e8905efaff   call 0x4c9aa0             ; find the object at this point within 0.8
00523c1d  ...          [obj][+8]() == 1          ; type check
00523c30  8b4530       mov eax, dword ptr [ebp + 0x30]    ; its owner
```

So: **a battle is a location where ships of two or more owners are co-located (within
0.8 units of a common point), and the planet at that point participates.** That matches
the report's "the leg endpoints sit ~1.6 units off the planet centre" geometry — arriving
ships end up inside the bucket radius of the planet they orbit.

**Order type re-enters one level down, as a hostility/role test, not as a trigger.**
`0x0051A190` — called from battle phase `0x00520570`, and used as a two-argument
predicate over combatants — `dynamic_cast`s each participant to `DynamicSpaceObject` and
to `Planet` (RTTI descriptors 0x007FADF8 `.?AVDynamicSpaceObject@@` and 0x007FAA4C
`.?AVPlanet@@`), then:

```
0051a260  8bce        mov ecx, esi
0051a262  e8997a0400  call 0x561d00         ; 0x561d00 is: mov eax,[ecx+0x3c] ; ret   -> Ship:52
0051a267  83f804      cmp eax, 4            ; ATTACK
0051a26a  7410        je 0x51a27c
0051a26c  8bce        mov ecx, esi
0051a26e  e88d7a0400  call 0x561d00
0051a273  83f805      cmp eax, 5            ; CONQUER
0051a276  0f85d5000000 jne 0x51a351         ; neither -> not a hostile actor by this route
0051a27c  8bce        mov ecx, esi
0051a27e  e8edf5fbff  call 0x4d9870         ; the order's target object
0051a28e  8b4030      mov eax, dword ptr [eax + 0x30]
0051a291  8b00        mov eax, dword ptr [eax]
0051a297  83c0d4      add eax, -0x2c        ; the target's owner
0051a2f7  3b442434    cmp eax, dword ptr [esp + 0x34]    ; == the owner under test?
```

`0x00561D00` is a one-line getter, `return [this+0x3c]`, and `ship_allocation + 0x3c` is
`Ship:52` — the order type. CONFIRMED.

**Only order types 4 (Attack) and 5 (Conquer) are tested. Order type 8 is not among
them, so type 8 is NOT attack.** That closes the Round 2 lead. What type 8 is remains
open; the only other thing known about it is that the `ShipCommand` constructor sets byte
`Ship:61 = 0x0B` for it (Round 2 section 3.2).

**inferred, and flagged as such**: the exact boolean 0x0051A190 returns, and whether a
treaty/war state further filters participation, is not decoded. What is CONFIRMED is
(i) the driver's engagement rule is positional with a >= 2-owner gate, (ii) the driver
consults no order field, and (iii) the classifier one level down keys on `Ship:52`
values 4 and 5 and resolves the order's target owner. I did **not** find a treaty lookup
on this path, but I also did not exhaustively search 0x0051C0F0 (see 3.3), so "no
war-declaration requirement" is **not** established — do not build on it.

### 3.3 (c) Which ShipDesign fields feed damage and survivability — CONFIRMED

`0x0051C0F0` is the battle simulator proper: a large recursive function (it calls itself)
that pulls every combat stat off the designs. A call-graph scan to depth 3 from the
driver and from each of the three battle phases (0x00520570, 0x00520700, 0x00520860)
reaches exactly this set of the Round 2 lazy getters:

| annotation | getter | role |
|---|---|---|
| `ShipDesign:52` base hitpoints | 0x005473F0 | survivability |
| `ShipDesign:56` effective hitpoints | 0x00547FC0 | survivability (shield-adjusted) |
| `ShipDesign:60` light firepower | 0x00548CC0 | damage |
| `ShipDesign:64` heavy firepower | 0x00548E20 | damage |
| `ShipDesign:68` third firepower | 0x00548F80 | damage |
| `ShipDesign:76` payload-delivery flag | 0x005490E0 | participation/role |

All six are reached through the hub `0x0051C0F0`, and `:52`/`:56` also via `0x0051D0C0`.
`ShipDesign:72` (shield) and `ShipDesign:80` (crew capacity) are reached only from the
**report** builder 0x00520D40 (via 0x004D9A20 and 0x004D98B0) — consistent with `:56`
already having the shield folded in, which is exactly what the report's
base-vs-effective reading says.

Two consequences worth stating:

* **All three firepower slots matter**, not just the first. A design's combat value is
  the triple, and the report's warning that an uncomputed design "looks unarmed" applies
  directly here: `0x0051C0F0` calls the getters, so a design with the -1 sentinel will be
  computed on demand — but only if its `ShipDesign:260` owner link is valid (Round 2
  section 3.2), or `GetSpeed`'s null-page path is one call away.
* **`Ship:136` condition is the per-ship damage state** (already in the report). I did
  not find where combat writes it: a search for float stores to `ship+0x80` found only
  library code in 0x006Exxxx, so it is written through a helper or as an integer move.
  Open.

Supporting structural finding, CONFIRMED: **`Ship` embeds a `BattleProperties`
sub-object at Ship allocation `+0x70` = `Ship:104`**, from the Ship constructor at
0x004E8CA0:

```
004e8ca3  c706048b7600    mov dword ptr [esi], 0x768b04         ; vftable .?AVShip@@
004e8ce6  c74670008a7500  mov dword ptr [esi + 0x70], 0x758a00  ; vftable .?AVBattleProperties@@
```

**inferred**: `Ship:108` (the design reference node) is therefore the first data member
of that sub-object, which would make sense — a ship's battle properties are derived from
its design. Standalone `BattleProperties` are also constructed at 0x00446A40, 0x00446A50,
0x00447180, 0x004E05F0, 0x004EA1B0 and 0x004F6230.

### 3.4 (d) Crew requirement for combat — partially answered, and the honest answer is "not found"

`0x004D98B0` is `__thiscall`, `ret 0x10` (4 out-parameters), and it is the crew/capacity
summariser: it walks the ship's crew through virtual slots `[vft+0x2c]` (count) and
`[vft+0x6c]` (item) and consults `ShipDesign:76` and `ShipDesign:80`. It is the same
helper Round 2 found inside the conscription cost function.

But **it is not called by `0x0051C0F0`** — the simulator's direct-call list does not
contain it. It is reached from the driver only via the battle *report* path. And I found
no crew-count gate in the driver itself. So on the evidence I have:

* There is **no** crew precondition visible at the engagement or simulation level
  analogous to the colony ship's minimum of 2. **inferred from absence, which is weak
  evidence** — 0x0051C0F0 is ~3000 bytes and I did not read it line by line.
* The indirect argument is stronger and already in the report: a crewless ship has its
  orders cancelled at the turn boundary and never moves, so it never reaches a contested
  location in the first place. Crew is therefore a *de facto* combat precondition through
  movement, whether or not the combat code checks it.

Treat 3.4 as **open**. Do not encode a combat crew rule from this round.

### 3.5 Reading order for the next combat round

In priority order, with what each should yield:

1. **`0x0051C0F0`** — the simulator. Read the round loop and the damage application. This
   is where the actual formula, the shield interaction and any crew or condition gate
   live. Everything else in 3.x is scaffolding around it.
2. **`0x00520570` / `0x00520700` / `0x00520860`** — the three phases the driver runs per
   bucket. They share helpers 0x0051E8F0, 0x00535080, 0x00534D90, 0x005350C0. Determining
   which is "form sides", which is "fire", and which is "apply losses" is cheap and makes
   1 much easier to read.
3. **`0x0051A190`** — finish the hostility predicate: its return polarity, and whether a
   treaty state is consulted. This is what decides whether the AI must declare war before
   an Exterminate rule will actually kill anything.
4. **`0x00520D40`** — the battle report builder. It formats `"Battle %s vs %s"`,
   `"(%s Ship%s destroyed)"` and `"%s fire-power:"`, so it enumerates the battle object's
   fields for free; a fast way to name the battle record's layout.
5. **`0x004F1BC0`** — the per-planet pre-pass, for planetary defence.
6. **`0x0056D130`** — annotate all ~40 turn stages. Worth doing once for everything, not
   just combat.

---

## 4. Corrections to Round 2

Two, both from this round's own decoding:

1. **Prerequisites are not a global OR.** Round 2 section 2.6 inferred OR from the data
   shape and said so explicitly. The predicate `0x0054B9E0` branches on the exclusion
   count: **technologies AND, doctrines OR** (section 2.3). Round 2's supporting argument
   — that AND is impossible because some records require mutually exclusive doctrine
   groups — was correct only for the doctrine half of the table.
2. **The `GetCost` multiplier is not 1.** Round 2 section 2.3 marked this inferred; it is
   the **completed-research count** (section 1.4). The knock-on is that Round 2's
   `cost@1600` column is wrong twice over: the scale is 800, and the cost scales with how
   much you have already researched. Use section 1.6.

Round 2's version -> table-base mapping, record layout, effect-kind table, trait cache
location and `Ship:52/56/60/61` findings all survive unchanged.

## 5. Summary table — Round 3

| address | conv | args | what it is | status |
|---|---|---|---|---|
| 0x0072E430 | initialiser | 0 | builds the **live** research table at 0x0085D410 | CONFIRMED |
| **0x0054B140** | `__thiscall` | 1 (`ret 4`) — `(int completedCount)` | research cost = `round(scale x factor x completedCount)` | CONFIRMED |
| **0x00545610** | `__cdecl` | 0 (`ret`) | research tick: stockpile += income, complete if affordable, append to `Owner:108`, re-pick. **No prerequisite check.** | CONFIRMED |
| **0x0053DF80** | `__thiscall` Owner | 1 (`ret 4`) — `(record*)` | the one non-UI topic setter: rejects on exclusion, expands missing prerequisites into `Owner:120`, promotes the cheapest legal step to `Owner:144` | CONFIRMED |
| 0x00573A40 | `__cdecl` | 1 | `STRS` set-research command handler; existence-checks the id then calls 0x0053DF80 | CONFIRMED |
| **0x0054B9E0** | `__thiscall` | 1 (`ret 4`) — `(completed*)` | prerequisites met? **AND** if `exclusionCount==0`, **OR** otherwise | CONFIRMED |
| 0x0054B7D0 | `__thiscall` | 2 (`ret 8`) | selectable? FALSE if any exclusive sibling is completed | CONFIRMED |
| 0x0054B260 | `__thiscall` | 3 (`ret 0xC`) | recursive prerequisite **expander** into the plan queue — not a validator | CONFIRMED |
| 0x0054B200 | `__thiscall` | 1 (`ret 4`) | prerequisite at flat index, A then B | CONFIRMED |
| 0x004D4320 | `__cdecl` | 1 (`ret`) — `(int index)` | `GetGameOption`; 0x6C-byte records at `[0x00853EC4]`; option 0x15 field +0x28 is the research scale | CONFIRMED |
| **0x0056D130** | `__cdecl` | 0 (`ret`) | **the turn pipeline**, ~40 stages | CONFIRMED |
| **0x00523AC0** | `__cdecl` | 0 (`ret`) | **combat driver**; buckets ships by position, needs >= 2 owners, radius 0.8 | CONFIRMED |
| 0x00523910 | `__thiscall` | 1 (`ret 4`) | bucket accessor keyed by (position, owner) | CONFIRMED |
| 0x0051C0F0 | `__cdecl`-ish, ebp frame | many | the battle simulator; reads `ShipDesign:52/56/60/64/68/76` | CONFIRMED (identity), body not decoded |
| 0x0051A190 | 2 args | — | combatant classifier; reads `Ship:52`, tests **4 and 5 only** | CONFIRMED (the tests), polarity inferred |
| 0x00561D00 | `__thiscall` | 0 (`ret`) | `return [this+0x3c]` = `Ship:52` order type | CONFIRMED |
| 0x004D98B0 | `__thiscall` | 4 (`ret 0x10`) | crew/capacity summary; **not** called by the simulator | CONFIRMED |

### New offsets for the annotation merge

* `Owner:96` — completed-research container wrapper (vector at `Owner:108/112/116`).
* `Owner:120` — **research plan queue** wrapper (vector at `Owner:132/136`). The engine
  fills this with missing prerequisites when you select an unreachable topic.
* `Owner:144` — **current research topic id**. `Owner:148` — its cost as last computed.
* `Owner:152` — staged topic record; `-1` means "rejected / nothing selected".
* `Ship:104` — start of an embedded `BattleProperties` sub-object (vftable 0x00758A00);
  `Ship:108`, the design reference node, is its first data member (**inferred**).
* Combat engagement radius: the float at `0x00767D2C` = **0.8**.
* Global planet array: `[0x008553C4]`..`[0x008553C8]`, 4-byte `Planet*`.
  Global ship container: returned by `0x004D95C0`, vector at `+0xc`/`+0x10`.
* Content version global: `[0x0080AA00]` — 565 on this client, which selects the
  0x0085D410 table and makes `0x0052A790` (`>= 150`) true, so conscription is charged.
