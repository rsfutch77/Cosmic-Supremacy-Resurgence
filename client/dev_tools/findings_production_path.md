# Findings: static analysis, Round 2

Round 1 of this file (the facility-production path and the ship-design SAVE path) was
merged into the reconstruction report and the file dropped in commit `5bcd4ca`, so this
is a fresh file carrying Round 2 only. Same method as before: capstone over
`client/CosmicSupremacy_Resurgence.exe`, VA -> file offset through the section table,
no process touched. Same discipline:

* **CONFIRMED** — the instruction bytes say it, and I quote them.
* **inferred** — consistent with the bytes, not fully proven. Called out every time.

Nothing here has been executed. Every claim is "the binary says", never "the game did".

Round 1's offset frame is used throughout and held up everywhere it was tested:

> `Planet:N` == `PlanetProperties + (N - 88)`, because `Planet` embeds a
> `PlanetProperties` sub-object at Planet allocation `+0x60` (0x198 bytes).
> Every `lea ecx,[reg+0x60]` below is "the PlanetProperties of this Planet".

---

## Round 2, Objective 1 — conscription

### 1.0 What conscription actually is

It is not an append to the military vector. **A citizen and a stationed military unit
live in two different vectors of the same 16-byte record, and conscription is a job
change: set a citizen's job id to 3 and hand the whole list back to the engine, which
then migrates every job-3 record out of the citizen vector into the military vector.**

That is the mechanism the reconstruction report already half-saw ("a stationed military
unit, a ship's crew member and a planet citizen are the same 16-byte record"). The code
makes it exact. CONFIRMED, in `PlanetProperties::CommitPopulation` 0x004F5280:

```
004f528f  8d772c    lea esi, [edi + 0x2c]           ; esi = citizen container (Planet:132)
          ; walk i = count-1 down to 0 over the vector at esi+0xc/+0x10 (Planet:144/148)
004f52ba  833c2803  cmp dword ptr [eax + ebp], 3    ; citizens[i].jobId == 3 ?
004f52be  0f859e..  jne <skip>
004f52d4  03c5      add eax, ebp
004f52d6  50        push eax                        ; &citizens[i]
004f52d7  8d4f44    lea ecx, [edi + 0x44]           ; military container (Planet:156)
004f52da  e88162..  call 0x4cb560                   ; push_back  -> Planet:168 vector
          ; then memmove the tail down 16 bytes and [esi+0x10] -= 0x10  (erase)
004f534a  834610f0  add dword ptr [esi + 0x10], -0x10
```

So the two containers are:

| annotation | PP offset | what |
|---|---|---|
| `Planet:132` | +0x2C | citizen container **wrapper**; its vector is 12 bytes in |
| `Planet:144/148/152` | +0x38/+0x3C/+0x40 | the citizen vector (already annotated) |
| `Planet:156` | +0x44 | stationed-military container **wrapper** |
| `Planet:168/172/176` | +0x50/+0x54/+0x58 | the military vector (already annotated) |

`Planet:132` and `Planet:156` are new. They are the same "object holding a vector 12
bytes into itself" wrapper the order object's `+28`/`+52` heads use — the setters take
the wrapper, not the vector.

### 1.1 `ChangeCitizenJobs` — 0x00574180  ← **the conscription function**

```
__cdecl  void ChangeCitizenJobs(Planet* p, std::vector<int>* indices,
                                int newJobId, bool bFromMilitaryList)
```

* **Convention**: `__cdecl`. Plain `ret` at 0x0057449D, args caller-cleaned.
* **Argument count**: 4. Not from `ret N` (there is no `ret N` — it is cdecl); taken
  from the two call sites, which each push exactly four and then `add esp, 0x10`
  equivalent via the epilogue. Cross-checked against the frame: the handler reads
  `[esp+0x44]`, `[esp+0x48]`, `[esp+0x4c]`, `[esp+0x50]`, which with the prologue
  accounting (`push -1`/`push handler`/`push fs`/`sub 0x20`/4 pushes/cookie = 0x40)
  are entry `[esp+4]`, `[esp+8]`, `[esp+0xc]`, `[esp+0x10]`.
* **Two call sites, both proving the types** — 0x0041C55C (in 0x0041C4B0) and
  0x0042F89B (in 0x0042F750). Both are UI handlers and both push in the same order
  (last pushed = arg1):

```
0041c554  push ebx                 ; arg4  bFromMilitaryList
0041c555  push esi                 ; arg3  newJobId   (tested `cmp esi, 3` just above)
0041c556  lea eax, [esp + 0x1c]
0041c55a  push eax                 ; arg2  &vector<int> of selected indices
0041c55b  push edi                 ; arg1  Planet*
0041c55c  call 0x574180
```

* **arg1 = `Planet*`** — CONFIRMED four ways inside the callee: `[ebx+0x60]` is passed
  as `ecx` to PlanetProperties methods (0x004F6840, 0x004F0EC0, 0x00419F70);
  `[ebx+0x30]` is dereferenced and adjusted by `-0x2c` to reach the Owner primary,
  which is the `Planet:40` owner-node idiom; `[ebx+4]` is sent on the wire as the
  object id (`Planet:-4`); `[ebx+0x8c]` and `[ebx+0xa4]` are PP+0x2C and PP+0x44, the
  two containers above.
* **arg2 = `std::vector<int>*`** — CONFIRMED: read as `_Myfirst`/`_Mylast` at
  `[ebp+0xc]`/`[ebp+0x10]`, `sar 2` for 4-byte elements, and each element is used as an
  **index** into the 16-byte record vector:

```
005743e7  8b0488    mov eax, dword ptr [eax + ecx*4]   ; idx = indices[k]
005743ef  3bc7      cmp eax, edi                       ; bounds-check vs record count
005743f3  c1e004    shl eax, 4                         ; *16
005743f6  03c6      add eax, esi
005743fd  8930      mov dword ptr [eax], esi           ; record.jobId = newJobId
```

* **arg3 = `int newJobId`** — CONFIRMED: written to the record's first dword at
  0x005743FD, and compared against 3 at 0x00574309 and 0x00574386 to gate the cost.
* **arg4 = `bool bFromMilitaryList`** — CONFIRMED: selects the source container
  (`[ebx+0xa4]` military when non-zero, `[ebx+0x8c]` citizens when zero, at
  0x00574362 / 0x0057436A) and selects the setter at the end (0x004F6870 vs 0x004F6840).

**What it writes**, in order:

1. Enters a global lock/notify pair `0x0052A770` / `0x0052A9B0` around everything.
2. **Online branch** (`[0x0086F1A2] != 1` plus the session checks): sends the change
   to the server instead of applying it. `BeginSection` with tag word `0x4348435A`,
   which by the round-1 byte-swap rule reads **"CHCZ"** on the wire, then the planet's
   object id, two u32s and a byte via `WriteRaw` 0x005E5E10, `EndSection`, flush, await
   reply, and re-read the planet from the reply. So in multiplayer the **server** owns
   conscription.
3. **Offline branch** (`[0x0086F1A0] != 0`) — the local path:
   * **cost**, only if all three of: `0x0052A790()` is true, `arg4 == 0` (drafting from
     the citizen list), and `arg3 == 3`:

     ```
     0057430f  eax = (indices._Mylast - indices._Myfirst) >> 2    ; how many to draft
     00574315  ecx = Owner primary   (from Planet:40 node, -0x2c)
     00574328  push eax ; add ecx, 0x30c ; call 0x516060           ; total cost
     00574344  3b423c    cmp eax, dword ptr [edx + 0x3c]           ; vs Owner:8 treasury
     00574347  0f8f14..  jg  <abort entirely>
     00574358  29413c    sub dword ptr [ecx + 0x3c], eax           ; treasury -= cost
     ```

     `owner_primary + 0x3c` = tag-52 + 60 = tag + 8 = **`Owner:8`**, exactly the
     treasury field the objective expected. Note `jg`: cost must be **<=** treasury,
     and on failure **nothing at all happens** — the abort is before any list write.
   * copies the source container to a local (0x0041B9C0), writes `newJobId` into each
     selected record, and hands the local list back:
     `0x004F6870(&local)` if `arg4`, else `0x004F6840(&local, 0)`.
   * frees the local, then broadcasts "planet changed" with `0x004033C0(planet)`.

**Population is not separately decremented.** It falls out of the migration: the
record leaves the `Planet:144` vector, so the citizen count drops by one and the
military count rises by one. The `Planet:352` population ring reads its slot 0 from
that same count.

### 1.2 The two list setters

```
__thiscall  void PlanetProperties::SetCitizenList (container* src, bool bNotify)   0x004F6840
__thiscall  void PlanetProperties::SetMilitaryList(container* src)                 0x004F6870
```

Both CONFIRMED, and both are tiny:

```
004f6840  mov eax,[esp+4] ; push eax ; lea ecx,[esi+0x2c] ; call 0x42d8c0   ; copy into Planet:132
          mov ecx,esi ; call 0x4f5280                                       ; run the job-3 migration
          cmp byte [esp+0xc],0 ; je .. ; push 0 ; push 0 ; call 0x4f0ee0    ; optional notify
          ret 8                                       -> 2 args

004f6870  mov eax,[esp+4] ; push eax ; lea ecx,[esi+0x44] ; call 0x42d8c0   ; copy into Planet:156
          mov ecx,esi ; call 0x4f5280
          ret 4                                        -> 1 arg
```

`ecx` for both is the **PlanetProperties**, i.e. `planet_allocation + 0x60` =
`planet_tag + 88`. `0x0042D8C0` is the container assign; `0x004F5280` is the migration
in 1.0. **inferred**: `bNotify` on 0x004F6840 drives a UI refresh only — 0x004F0EE0 is
the same helper the production path calls after a change.

Three governor rules call 0x004F6840 the same way, which is good corroboration that it
is the general "here is the planet's new population" entry point:
`GovernorRuleConvertPopulation` slot 9 (0x005C0A20 -> 0x005C0C84),
`GovernorRuleDraftStationedMilitary` slot 9 (0x005C1930 -> 0x005C1C5D),
`GovernorRuleSustainPlanet` slot 9 (0x005C31A0 -> 0x005C330A).

### 1.3 The conscription cost formula — 0x00516060

```
__thiscall  int GetDraftCost(int count)        ; ecx = Owner_primary + 0x30c
```
`ret 4`, 1 arg. CONFIRMED, and it is fully derivable, which is the useful part:

1. It sums `T`, the civ's **existing military across the whole empire**: it walks the
   global planet array (`[0x008553C4]`..`[0x008553C8]`, 4-byte elements), keeps the
   planets whose owner matches, and adds `([planet+0xb4] - [planet+0xb0]) / 16` — which
   is PP+0x54 minus PP+0x50, i.e. the **`Planet:168` military count**. It then walks the
   global ship array (`[0x00854628]`..`[0x0085462C]`) and adds each owned ship's crew
   count via 0x004D98B0.
2. The cost of the k-th additional unit (k 0-based) is **`5 * (T + k) + 105`**, and the
   function returns the sum over `k = 0 .. count-1`.

The loop is written as an unrolled pair with a `5*ebp + 0x6e` / `5*ebp + 0x69` seed
(110 and 105) stepping `+0xa` per pair. I verified the closed form against the emitted
code by hand for count = 1, 2, 3 and 4:

| count | code produces | `sum(5*(T+k)+105)` |
|---|---|---|
| 1 | `5T+105` | `5T+105` |
| 2 | `(5T+110)+(5T+105)` | `10T+215` |
| 3 | `+ 5(T+2)+105` | `15T+330` |
| 4 | `(5T+110)+(5T+120)+(5T+105)+(5T+115)` | `20T+450` |

All four match. **So the first conscript in a fresh empire with 3 stationed units costs
120, and the price rises by 5 per unit already owned, empire-wide, counting ship crew.**
That makes conscription cheap early and is exactly the number an AI needs to decide
between cash and the ~25-turn recruitment path.

`Owner_primary + 0x30c` = `Owner:728` — see 2.5, it is the civ-trait container, and this
function only uses it to reach the Owner (`[container+0]` is the Owner primary).

### 1.4 `0x0052A790` — the gate that makes conscription free

```
0052a790  33c0                   xor eax, eax
0052a792  813d00aa800096000000   cmp dword ptr [0x80aa00], 0x96
0052a79c  0f9dc0                 setge al
0052a79f  c3                     ret
```

`__cdecl`, 0 args: `return [0x0080AA00] >= 150`. `[0x0080AA00]` is a **content/data
version number** — 0x0054A650 branches on it at 5, 10, 57, 150, 565 and 688 to pick
between six different research tables (see 2.1), so that is what it is. When it is
false, `ChangeCitizenJobs` skips the whole cost block and **conscription is free**.
An external controller that wants to predict the charge must read that dword first.
**inferred** that it is a content version rather than something else, from the ladder of
thresholds it gates; **CONFIRMED** as the exact numeric test.

### 1.5 One hazard worth knowing: surplus population is auto-conscripted

The second half of `0x004F5280` is not part of the requested change at all — it is a
population cap enforcer, and it will conscript for you:

```
004f5372  ebx = citizen count
004f537a  eax = [PP vftable + 0x18] ; call         ; -> available space (see below)
004f5388  b867666666 ; imul ; sar edx,2            ; /10   (the standard /10 sequence)
004f5399  3bd9 ; 0f8c..  cmp ebx, ecx ; jl <done>  ; while (count-1 >= space/10)
004f53c3  c7042803000000  mov dword ptr [eax + ebp], 3   ; force job 3
004f53e4  e87761fdff      call 0x4cb560                  ; and move it to the military vector
```

`PlanetProperties` vftable slot 6 (`0x00758E8C + 0x18`) is `0x004F1140`, which reads
`movsx edi, word ptr [esi+0x12]` — PP+0x12 is the **high half of `Planet:104`, i.e.
planet space** — applies civ trait 38 as a percentage bonus, and subtracts the word at
PP+0x14 (`Planet:108`). So the divisor is space/10, which independently re-derives the
report's CONFIRMED `maxPopulation = floor(space/10)` from the instruction bytes rather
than from observation. **Any external write that inflates the `Planet:144` vector past
`space/10` will have the surplus silently turned into military at the next commit.**

### 1.6 Actuator sketch

Cheapest correct call is `ChangeCitizenJobs` itself: `__cdecl` with four args, so it
needs a stub (CreateRemoteThread passes only one). Or reproduce it by hand:

```
cost = 5*T + 105                       # T = empire military + ship crew, per 1.3
if [0x0080AA00] >= 150 and cost > Owner:8:  abort
Owner:8 -= cost                        # only if [0x0080AA00] >= 150
citizens = copy of the Planet:132 container
citizens.records[idx].jobId = 3
SetCitizenList(&citizens, 0)           # 0x004F6840, __thiscall on planet_tag+88
```

A TLS-reachability scan over `ChangeCitizenJobs`' call graph to depth 4 found TLS access
only inside the CRT allocator around `operator new` (0x651643..0x651983) — nothing like
the `_tls_index` / `fs:[0x2c]` pattern at 0x005D17E0 that crashed the injected LoadGame.
`0x004F6840` and `0x004F6870` reach almost nothing. **Static finding, not a runtime
confirmation.**

Note the container copy is the awkward part: `0x0042D8C0` allocates, so the honest
external route is to let the engine allocate by calling the setter with a container you
built from an engine `operator new`, exactly as `set_population.py` already does for the
citizen vector.

---

## Round 2, Objective 2 — the technology and doctrine table

### 2.1 Where it lives

Research definitions are a **flat array of 0x110-byte (272) records indexed directly by
research id**. CONFIRMED at the id-to-definition mapper, `0x0054AA70`:

```
0054aa70  56          push esi
0054aa71  8bf1        mov esi, ecx
0054aa73  e8f8feffff  call 0x54a970            ; lazily build the table
0054aa78  8b06        mov eax, dword ptr [esi] ; [this] = the research id
0054aa7a  69c010010000 imul eax, eax, 0x110
0054aa80  0305087f8500 add eax, dword ptr [0x857f08]
0054aa86  5e ; c3      ret                     ; __thiscall, 0 args
```

`ecx` is the 8-byte record from `Owner:108` — so the report's reading of those elements
as `[id, cost]` is right, and `[id]` is the array index.

`0x0054A650` picks one of **six** tables by the content version at `[0x0080AA00]`.
CONFIRMED, and independently cross-checked twice: once by the base addresses tiling
exactly (`base + count*0x110 == next base` for all six), and once against the CRT
array-destruct registrations at 0x0074BF60..0x0074C06D, each of which pushes
`(base, 0x110, count, dtor 0x54a810)`:

| `[0x0080AA00]` | count | base |
|---|---|---|
| < 5 | 25 | 0x0086D200 |
| 5 .. 9 | 25 | 0x0086B770 |
| 10 .. 56 | 27 | 0x00869AC0 |
| 57 .. 149 | 27 | 0x00867E10 |
| **150 .. 564** | **80** | **0x00862910** |
| 565 .. 687 | 80 | 0x0085D410 |
| **>= 688** | **80** | **0x00857F10** |

All six live past `.data`'s raw size (0x00829400), i.e. in the zero-filled tail, so they
are **not in the file image** — they are built at startup by the static initialiser
`0x00729210` (3601 instructions, 2256 references into the newest table). Everything in
2.3 was recovered by interpreting that initialiser instruction by instruction:
tracking immediate register loads, `mov [abs], reg|imm`, `fld`/`fldz`/`fld1` + `fstp
[abs]`, `std::string::assign` (0x00404AB0, with `ecx` = the destination string and the
pushed `char*`), and `memset` (0x006712C0) for the zero tails. So the table below is
**CONFIRMED from the initialiser's own bytes**, not read out of a running process.

The table dumped is the newest one, **0x00857F10, 80 records** — the one a
version >= 688 client uses. If `[0x0080AA00]` reads below 688 on your client, use
0x0085D410 or 0x00862910; those are also 80 records and are very likely the same content
at a different patch level, but I did not diff them.

### 2.2 Record layout (0x110 bytes)

| offset | meaning | status |
|---|---|---|
| +0x00 | `int id` (equals the array index) | CONFIRMED |
| +0x04 | `int flags` — bit 1 and bit 2 are content filters tested in 0x0054A970 | CONFIRMED |
| +0x08 | `float costFactor` | CONFIRMED |
| +0x0C | `std::string name` (0x20) | CONFIRMED |
| +0x2C | `std::string` — empty on all 80 records | CONFIRMED |
| +0x4C | `std::string description` (0x20) | CONFIRMED |
| +0x6C | `int countA` | CONFIRMED |
| +0x70 .. +0x7C | prerequisite list A, 4 slots | CONFIRMED |
| +0x80 | `int countB` | CONFIRMED |
| +0x84 .. +0x90 | prerequisite list B, 4 slots | CONFIRMED |
| +0x94 | `int effectCount` | CONFIRMED |
| +0x98 .. +0xF7 | `effect[8]`, 12 bytes each: `{int kind, int param, int value}` | CONFIRMED |
| +0xF8 | `int exclusionCount` | CONFIRMED |
| +0xFC .. +0x108 | mutually-exclusive research ids, 4 slots | CONFIRMED |
| +0x10C | spare, 0 on all 80 | CONFIRMED |

The A/B prerequisite split is CONFIRMED by the accessor `0x0054B200`
(`__thiscall int GetPrerequisiteAt(int index)`, `ret 4`), which presents them as **one
flat list**: index `< countA` reads `rec + 0x70 + index*4`, otherwise
`rec + 0x84 + (index - countA)*4`. The effect layout is CONFIRMED by 0x0054AA90 reading
`kind` at `rec+0x98+12k`, `param` at `rec+0x9C+12k` and `value` at `rec+0xA0+12k`, and
by the maximum observed `effectCount` being 6 — which keeps the 8 effect slots clear of
+0xF8.

### 2.3 Cost

```
__thiscall  int Research::GetCost(int multiplier)      0x0054B140    ret 4, 1 arg
```
Modern branch (`[0x0080AA00] >= 8`), CONFIRMED:

```
0054b193  call 0x54a6f0                 ; scale
0054b19c  fild dword ptr [esp+4]        ; st0 = scale
0054b1a0  fld  dword ptr [esi+8]        ; rec.costFactor
0054b1a3  fimul dword ptr [esp+0xc]     ; *= multiplier
0054b1a7  fmulp st(1)                   ; scale * factor * multiplier
          ; then +/- 0.5 and ftol -> round-half-away-from-zero
```

> **cost = round(scale x costFactor x multiplier)**

`0x0054A6F0` returns `scale`: 0 if `[0x008578F5] != 0`; else on a version >= 150 client
it is a **per-game config value**, `0x004D4320(0x15)->[+0x28]`; else 25, or 100/50 by
version. So `scale` is not statically knowable — but it is trivially **calibrated from
one observation**: `scale = observedCost / costFactor`.

That calibration is already done for you. The report records a doctrine completing as
`[43, 1600]`; id 43 is `Doctrine: Mobility` with `costFactor = 1.0`, giving
**scale = 1600**. And the objective's own observation that id 2 cost 4800 falls straight
out: id 2 is `Astro Engineering`, `costFactor = 3.0`, `3.0 x 1600 = 4800`. **Two
independent live observations both reproduce exactly**, which is as strong a check on
the extraction as I can make without the process.

**One discrepancy to re-check in your logs.** The objective says "ids 2 and 3 landed at
4800 and 2400". Id 2 = 4800 matches perfectly. Id 3 is `Cold Fusion`, `costFactor = 1.0`,
so it should be **1600**, not 2400. The only 2400 in the whole table is **id 38,
`Bunker`** (`costFactor = 1.5`). So either the second pick was not id 3, or `scale` moved
between the two. Worth one look at the log before trusting the number — the table itself
is internally consistent and validates against two other observations.

The `multiplier` argument: every call site I checked passes a per-topic multiplier that
is 1 in the ordinary case, and I did **not** enumerate all of them. **inferred** that it
is 1 for normal research; if a cost ever comes out as a clean multiple of the table
value, that is where it came from.

### 2.4 What each research grants — effect kinds

The 13-way jump table at `0x0054ACBC` (from the description formatter 0x0054AA90, which
constructs the granted object on the stack to ask it for its name) maps
`effect.kind` -> object type. CONFIRMED by the vftable constant each case writes:

| kind | grants | vftable | id space |
|---|---|---|---|
| 0 | **Facility** | 0x00752BA4 | same ids as `Owner:172` / `Planet:204` / `Planet:284` |
| 1 | ShipChassis | 0x007515E0 | `ShipDesign:128` element ids |
| 2 | ShipModule | 0x007573A0 | `ShipDesign:224` |
| 3 | ShipWeapon | 0x007589A8 | `ShipDesign:200` |
| 4 | ShipEngine | 0x00763054 | `ShipDesign:176` |
| 5 | ShipShield | 0x007589D4 | (`ShipDesign:72` is the resulting value) |
| 6 | ShipScanner | 0x00763028 | `ShipDesign:152` |
| 7, 8 | no case — falls to the default | — | — |
| 9 | **Scan** | 0x00752B6C | same ids as `Owner:200` |
| 10 | PlanetaryDefenseTechLevel | 0x00758A0C | level index |
| 11 | **CivilizationTrait** (a numeric modifier) | 0x00751598 | 0..45, see 2.5 |
| 12 | HyperspaceEnergyTechLevel | 0x0077031C | level index |

This lands exactly on the id spaces the objective hoped for: kind 0 keys into
`Owner:172` and kind 9 into `Owner:200`.

**Cross-validation against live observations, which is the strongest evidence the
extraction is right.** The report's CONFIRMED facility ids are
`0 farm, 2 shipyard, 4 university, 6 military camp, 8 defence agency, 9 light turret`.
The table grants them as: Facility 0 and Facility 2 from id 1 `Space Travel`;
Facility 4 and Facility 6 from id 18 `Advanced Networking`; Facility 8 from id 23
`Basic Scanning`; Facility 9 from id 27 `Planetary Defense Lvl 1`. All six line up, and
the report's observation that `Owner:172` held exactly `{0, 2, 4, 6}` is precisely a civ
holding Space Travel plus Advanced Networking and nothing else. It also explains the
report's puzzle that "id 8 appeared alongside a university and a defence agency" — the
defence agency needs `Basic Scanning`, which needs `Advanced Networking`, which is what
grants the university.

The facility id space also runs **further than the report knew: up to at least 20**
(ids 1, 3, 5, 7, 10-20 all appear as grants below).

### 2.5 Civilization traits — the 46-slot modifier cache

`Owner_primary + 0x30c` = **`Owner:728`** is a small container whose `[+0]` points back
at the Owner. It caches **46 (0x2E) signed dwords at container+0xCC**, i.e.
**`Owner:932 .. Owner:1112`**, and rebuilds them lazily from the completed-research
vector. CONFIRMED in `0x00516180` (`__thiscall int GetTrait(int traitId)`, `ret 4`):

```
00516188  cmp dword ptr [ebp+0xcc], -1     ; -1 == cache invalid
0051619e  mov ecx, 0x2e ; rep stosd         ; zero all 46 slots
005161a5  edi = [ebp]                       ; the Owner
005161a8  esi = [edi+0xa0], [edi+0xa4]      ; Owner:108 completed-research vector (8-byte elts)
          for each completed record:
005161e6      call 0x54aa70 ; add eax,0x94  ; -> &effectCount
00516203      cmp dword [ebx+eax+4], 0xb    ; effect.kind == 11 ?
00516229      eax = effect.param            ; trait slot
00516220      edi = effect.value            ; signed delta
0051622d      add dword ptr [ebp + eax*4 + 0xcc], edi
```

So doctrine effects are **additive integer deltas on 46 numbered civilization traits**,
and the whole set is readable directly out of `Owner:932..1112` — with the caveat that
slot 0 holding -1 means "not yet computed" (and note `Doctrine: Reproduction` sets
trait 0 to -1, which is an amusing collision I have not chased).

Two trait slots are already pinned, and they pin themselves by agreeing with the
doctrine names, which I take as good corroboration of the whole extraction:

* **trait 18 = ship speed, percent.** Round 1 found `ShipDesignData::GetSpeed`
  (0x005480A0) applying traits 6 and 0x12 via this same function. Trait 18 is 0x12, and
  `Doctrine: Mobility` grants `trait 18 +20`. A mobility doctrine giving +20% speed is
  not a coincidence.
* **trait 38 = planet space, percent.** 1.5 found the PlanetProperties space getter
  (0x004F1140) applying trait 0x26 = 38, and `Doctrine: Habitat` grants `trait 38 +20`.

I did **not** extract names for all 46 traits. They are formatted by `0x005159F0`, a
long switch on `[trait+4]` with special cases at 43/44/45 — a mechanical but sizeable
extraction, and a clean follow-up if the AI ends up needing to weight them by name
rather than by observed effect.

### 2.6 Prerequisites are an OR, not an AND

The prerequisite list (A then B, `countA + countB` entries) is satisfied by **any one**
member, not all. I did not fully decode the recursive walker `0x0054B260`, so this is
**inferred** — but the data makes AND impossible, which is close to a proof:

* Ids 43, 44, 45, 46 are pairwise **mutually exclusive** (each one's exclusion list is
  the other three, CONFIRMED at +0xF8). Id 48 `Doctrine: Manufacturing` lists all four
  as prerequisites. Under AND that is unsatisfiable, so it is not AND.
* Same for id 62, whose prerequisites are 57, 58, 59, 60 — also a mutually exclusive
  group of four.
* Two records list a **duplicate**: id 55 has `47,48` + `48,49`, and id 59 has `50,54` +
  `54,55,56`. A duplicate is meaningless in an AND and harmless in an OR.

The linear chains confirm the direction is prerequisite and not child-link: id 28
requires 27, 29 requires 28, ... 34 requires 33; 70 requires 69, 71 requires 70, 72
requires 71; and the root `Nuclear` (id 0) has none. CONFIRMED.

**Consequence for R-XPL-04: "lowest id not in `Owner:108`" is broken.** With the granted
starting techs 0 and 1, the lowest unresearched id is 2 `Astro Engineering`, which
requires id 12 `Advanced Magnetism`. The genuinely available first picks are:
**3** (needs 0), **12** (needs 1), **18** (needs 1), **27** (needs nothing), and the
tier-1 doctrines **43/44/45/46** (need nothing, but pick one and the other three are
locked out forever). Cheapest useful openings are 3, 12, 18 and 27 at
`1.0 x scale` each.

### 2.7 The table

`cost@1600` is `round(costFactor x 1600)` — substitute your own calibrated `scale`.
`prereq(any)` is list A followed by list B; satisfy any one entry.
`grants` uses the kind names from 2.4; the number is the granted object's id, and for
`Trait` the signed delta follows.

| id | name | factor | cost@1600 | prereq(any) | grants | excludes |
|---|---|---|---|---|---|---|
| 0 | Nuclear | 0 | 0 | - | Scanner 0, Engine 0 | - |
| 1 | Space Travel | 0 | 0 | - | Chassis 0, Facility 0, Module 0, Facility 2 | - |
| 2 | Astro Engineering | 3 | 4800 | 12 | Facility 1, Chassis 1, Module 2 | - |
| 3 | Cold Fusion | 1 | 1600 | 0 | Engine 1, Weapon 0, Weapon 10 | - |
| 4 | Tachyon Physics | 3.2 | 5120 | 13 | Weapon 2 | - |
| 5 | Robotics Engineering | 15 | 24000 | 22 | Chassis 3 | - |
| 6 | Gravity Controller | 9 | 14400 | 7 | Engine 3, Shield 2 | - |
| 7 | Quantum Fields | 2.8 | 4480 | 3,12 | Engine 2, Shield 1 | - |
| 8 | Anti-Matter Fission | 25 | 40000 | 15 | Weapon 12, Weapon 7 | - |
| 9 | Dark-Matter Control | 24 | 38400 | 41 | Engine 5 | - |
| 10 | Subspace Controller | 35 | 56000 | 9 | Shield 3, Engine 6 | - |
| 11 | Hyperspace Controller | 47 | 75200 | 10 | Shield 4 | - |
| 12 | Advanced Magnetism | 1 | 1600 | 1 | Shield 0, Module 1 | - |
| 13 | Photon Mechanics | 2 | 3200 | 3 | Weapon 1, Weapon 4 | - |
| 14 | Plasma Physics | 11 | 17600 | 4 | Weapon 5, Weapon 11 | - |
| 15 | Proton Controller | 17 | 27200 | 14 | Weapon 6, Weapon 3 | - |
| 16 | Nano Mechanics | 34 | 54400 | 40 | Chassis 4 | - |
| 17 | Superstring Theory | 51 | 81600 | 16 | Chassis 5 | - |
| 18 | Advanced Networking | 1 | 1600 | 1 | Facility 4, Facility 6 | - |
| 19 | Artificial Intelligence | 14 | 22400 | 24 | Facility 5, Scan 6 | - |
| 20 | Advanced Tactics | 24 | 38400 | 19 | Facility 7, Scan 4, Scanner 2 | - |
| 21 | Deep-Space Scanning | 32 | 51200 | 20 | Scanner 3, Scan 1 | - |
| 22 | Particle Engineering | 8 | 12800 | 2 | Chassis 2 | - |
| 23 | Basic Scanning | 3.6 | 5760 | 18 | Facility 8, Scanner 1, Scan 2, Scan 3 | - |
| 24 | Advanced Scanning | 10 | 16000 | 23 | Scan 0, Scan 5 | - |
| 25 | Wormhole Controller | 49 | 78400 | 42 | Weapon 9 | - |
| 26 | Magnetic Monopoles | 62 | 99200 | 25 | Weapon 14 | - |
| 27 | Planetary Defense Lvl 1 | 1 | 1600 | - | PlanDef 0, Facility 9 | - |
| 28 | Planetary Defense Lvl 2 | 2.5 | 4000 | 27 | PlanDef 1 | - |
| 29 | Planetary Defense Lvl 3 | 5 | 8000 | 28 | PlanDef 2, Facility 11 | - |
| 30 | Planetary Defense Lvl 4 | 10 | 16000 | 29 | PlanDef 3, Facility 10 | - |
| 31 | Planetary Defense Lvl 5 | 17 | 27200 | 30 | PlanDef 4 | - |
| 32 | Planetary Defense Lvl 6 | 27 | 43200 | 31 | PlanDef 5 | - |
| 33 | Planetary Defense Lvl 7 | 41 | 65600 | 32 | PlanDef 6 | - |
| 34 | Planetary Defense Lvl 8 | 57 | 91200 | 33 | PlanDef 7 | - |
| 35 | Mining | 6 | 9600 | 2 | Facility 13 | - |
| 36 | Propaganda | 2 | 3200 | 18 | Facility 12 | - |
| 37 | Robo Mining | 17 | 27200 | 5,35 | Facility 14 | - |
| 38 | Bunker | 1.5 | 2400 | 27 | Facility 15 | - |
| 39 | Banking | 2 | 3200 | 18 | Facility 16 | - |
| 40 | Advanced Manufacturing | 22 | 35200 | 5 | Facility 3 | - |
| 41 | Anti-Matter Control | 16 | 25600 | 6 | Engine 4 | - |
| 42 | Dark-Matter Fission | 36 | 57600 | 8 | Weapon 13, Weapon 8 | - |
| 43 | Doctrine: Mobility | 1 | 1600 | - | Trait 18 +20, Trait 10 -10 | 44,45,46 |
| 44 | Doctrine: Colonization | 1 | 1600 | - | Trait 23 -50, Trait 33 -15 | 43,45,46 |
| 45 | Doctrine: Reproduction | 1 | 1600 | - | Trait 26 -30, Trait 0 -1 | 43,44,46 |
| 46 | Doctrine: Metallurgy | 1 | 1600 | - | Trait 41 +50, Trait 5 +15, Trait 29 -30 | 43,44,45 |
| 47 | Doctrine: Knowledge | 2.7 | 4320 | 43,44 | Trait 28 -30, Trait 39 -7, Trait 13 +20 | 48,49 |
| 48 | Doctrine: Manufacturing | 2.3 | 3680 | 43,44,45,46 | Trait 27 -30, Trait 3 +5 | 47,49 |
| 49 | Doctrine: Trade | 2 | 3200 | 45,46 | Trait 42 +50, Trait 37 +15, Trait 1 +4, Trait 6 +5, Trait 2 +5 | 47,48 |
| 50 | Doctrine: Stealth | 6.5 | 10400 | 47,48,43 | Trait 12 +5, Trait 24 -75, Trait 17 +10, Trait 7 +15, Trait 19 -15 | 54,55,56 |
| 51 | Planetary Fortress | 8 | 12800 | 29 | Facility 17 | - |
| 52 | Biological Warfare | 3 | 4800 | 13 | Module 4 | - |
| 53 | Cloaking | 11 | 17600 | 24 | Module 3 | - |
| 54 | Doctrine: Stronghold | 7.5 | 12000 | 47,48 | Trait 30 -15, Trait 8 +15, Trait 38 +5, Trait 1 +2, Trait 9 -15 | 50,55,56 |
| 55 | Doctrine: Finance | 7 | 11200 | 47,48,48,49 | Trait 37 +75, Trait 5 -7 | 50,54,56 |
| 56 | Doctrine: Elite | 6 | 9600 | 48,49 | Trait 31 +70, Trait 9 +20, Trait 32 +1 | 50,54,55 |
| 57 | Doctrine: Intelligence | 15 | 24000 | 50,54,47 | Trait 18 +15, Trait 13 +30, Trait 12 +5, Trait 39 -5, Trait 25 -75, Trait 45 +1 | 58,59,60 |
| 58 | Doctrine: Versatility | 16 | 25600 | 50,54 | Trait 40 +150, Trait 16 +5, Trait 14 -5, Trait 6 +5 | 57,59,60 |
| 59 | Doctrine: Propaganda | 15.5 | 24800 | 50,54,54,55,56 | Trait 35 +85, Trait 4 +7, Trait 3 +2, Trait 14 +15 | 57,58,60 |
| 60 | Doctrine: Juggernaut | 16 | 25600 | 54,55,56 | Trait 17 +25, Trait 15 -10 | 57,58,59 |
| 61 | Doctrine: Invisibility | 27 | 43200 | 50 | Trait 12 +10, Trait 16 +25, Trait 17 +5, Trait 19 -80, Trait 20 -50 | 62,63,64 |
| 62 | Doctrine: Habitat | 30 | 48000 | 57,58,59,60 | Trait 38 +20, Trait 30 -10, Trait 2 -15, Trait 19 -15 | 61,63,64 |
| 63 | Doctrine: Police State | 29 | 46400 | 57,58,59,60 | Trait 34 +75, Trait 31 +50, Trait 7 +25 | 61,62,64 |
| 64 | Doctrine: Dreadnought | 28 | 44800 | 60 | Trait 19 +30, Trait 17 +20 | 61,62,63 |
| 65 | Doctrine: Lightweight | 45 | 72000 | 61,62,63,64 | Trait 11 -25, Trait 15 -30 | 66,67,68 |
| 66 | Doctrine: Bio-Armor | 47 | 75200 | 61,62,63,64 | Trait 22 +7, Trait 17 +5, Trait 9 +10, Trait 8 +10 | 65,67,68 |
| 67 | Doctrine: Golden Age | 50 | 80000 | 61,62,63,64 | Trait 43 +75, Trait 44 +35 | 65,66,68 |
| 68 | Doctrine: Titan | 46 | 73600 | 64 | Trait 20 +25, Trait 21 -5 | 65,66,67 |
| 69 | Hyperspace-Energy Lvl 1 | 9 | 14400 | 29 | HypEnergy 0, Facility 18, Facility 19 | - |
| 70 | Hyperspace-Energy Lvl 2 | 15 | 24000 | 69 | HypEnergy 1 | - |
| 71 | Hyperspace-Energy Lvl 3 | 25 | 40000 | 70 | HypEnergy 2 | - |
| 72 | Hyperspace-Energy Lvl 4 | 37 | 59200 | 71 | HypEnergy 3 | - |
| 73 | (unused stub) | 0 | 0 | - | - | - |
| 74 | (unused stub) | 0 | 0 | - | - | - |
| 75 | (unused stub) | 0 | 0 | - | - | - |
| 76 | (unused stub) | 0 | 0 | - | - | - |
| 77 | Artificial Wormholes | 13 | 20800 | 19 | Module 5 | - |
| 78 | Command Center | 20 | 32000 | 20 | Facility 20 | - |
| 79 | Surveillance Scanning | 19 | 30400 | 19 | Scan 7 | - |

Ids 73-76 are empty stubs with `flags = 1` (the other content-filter bit); every named
record from 43 up carries `flags = 2`. **inferred**: the flag bits gate which records the
UI offers per content version, since 0x0054A970 tests them with
`test byte [rec+4], 1` / `test byte [rec+4], 2` against two booleans it derives from
`[0x0080AA00]` and a config record.

Descriptions are in the table too (record +0x4C) — I have them extracted and can dump
them if the AI wants to key off text, but they are prose and I left them out here.

### 2.8 Exclusive doctrine groups

CONFIRMED from +0xF8, and checked with `0x0054B7D0`
(`__thiscall`, `ret 8`) which walks the exclusion list and tests each id for membership
in the owner's completed vector — i.e. "is a rival doctrine already taken":

| tier | mutually exclusive set — pick exactly one |
|---|---|
| 1 | 43 Mobility / 44 Colonization / 45 Reproduction / 46 Metallurgy |
| 2 | 47 Knowledge / 48 Manufacturing / 49 Trade |
| 3 | 50 Stealth / 54 Stronghold / 55 Finance / 56 Elite |
| 4 | 57 Intelligence / 58 Versatility / 59 Propaganda / 60 Juggernaut |
| 5 | 61 Invisibility / 62 Habitat / 63 Police State / 64 Dreadnought |
| 6 | 65 Lightweight / 66 Bio-Armor / 67 Golden Age / 68 Titan |

These are one-way doors and the AI should treat them as such: a doctrine choice at tier
1 permanently forecloses three others and constrains the whole ladder above it.

---

## Round 2, Objective 3 — `Ship:56` and `Fleet:56`

**Resolved.** `Ship:52` and `Ship:56` are not independent fields — they are the first
two members of a **`ShipCommand` value** that is written into the ship as a unit, and
`Ship:56` is **the order's target OBJECT, held as a reference node**.

### 3.1 `Ship::SetCommand` — 0x004D9DC0

```
004d9dc0  8b442404  mov eax, dword ptr [esp + 4]      ; the ShipCommand
004d9dc4  8b10      mov edx, dword ptr [eax]
004d9dc6  89513c    mov dword ptr [ecx + 0x3c], edx   ; Ship:52  order type
004d9dc9  8b5004    mov edx, dword ptr [eax + 4]
004d9dcc  895140    mov dword ptr [ecx + 0x40], edx   ; Ship:56  <-- THIS
004d9dcf  0fb65008  movzx edx, byte ptr [eax + 8]
004d9dd3  885144    mov byte ptr [ecx + 0x44], dl     ; Ship:60  byte
004d9dd6  0fb65009  movzx edx, byte ptr [eax + 9]
          ...                                          ; Ship:61  byte
```

`__thiscall`, `ecx` = the ship (the field block is on the base `Ship`/`Fleet` share, so
the same offsets apply to `Fleet:56`). Callers pass `ecx = esi` where `esi` is the Ship
they just called `Ship::SetOrder` 0x004DA450 on — e.g. 0x004CD090 and 0x004D0FB3, both
of which run `SetOrder`, check its result, build a `ShipCommand` and then call this.

Ship code offsets: `+0x3c` is `Ship:52`, which the report already CONFIRMED as the order
type; that anchors `+0x40 = Ship:56` with no ambiguity.

### 3.2 Why it is always the static null node

Three `ShipCommand` constructors exist, all `__thiscall`, and **all three compute
`[this+4]` the same way**:

```
; 0x004E9BA0  ShipCommand(int orderType, EJBO* target)          ret 8
004e9ba7  8906      mov dword ptr [esi], eax          ; [+0] = orderType
004e9ba9  8b44240c  mov eax, dword ptr [esp + 0xc]    ; target
004e9bad  85c0      test eax, eax
004e9baf  7405      je  0x4e9bb6
004e9bb1  8b4004    mov eax, dword ptr [eax + 4]      ; target->objectId  (alloc+4 = tag-4)
004e9bb4  eb02      jmp 0x4e9bb8
004e9bb6  33c0      xor eax, eax                      ; no target -> id 0
004e9bb8  50        push eax
004e9bb9  e832e00400 call 0x537bf0                    ; GetReferenceNode(id)
004e9bc0  894604    mov dword ptr [esi + 4], eax      ; -> Ship:56
004e9bcb  833e08    cmp dword ptr [esi], 8            ; orderType == 8 ?
004e9bd2  c6460900  mov byte ptr [esi + 9], 0
004e9be2  c646090b  mov byte ptr [esi + 9], 0xb       ;   then byte 9 = 0x0B
```

Siblings: `0x004E9BF0(int type, byte, word)` — `ret 0xC`, hard-codes target 0; and
`0x004E9C30(int type, EJBO* target, float)` — `ret 0xC`, identical target handling.

Round 1 established that `0x00537BF0(0)` returns the **static null node 0x00857C54**.
So `Ship:56` reads that node exactly when the target argument is null — and **every call
site I was able to evaluate passes null**: 0x0045A5BA (`push 0; push esi` with esi
zeroed the instruction before), 0x00475A7E (`push 0; push 0`), 0x004CD082
(`push 0; push 0`), 0x004D0FA3 (`push ebx; push 2` with `xor ebx,ebx` above),
0x0047C2C6 (`push edi; push 7` where `edi` is the zero used in the adjacent null test).

**That is the answer to the open `[ ]`.** The slot is not unused and not a mystery: it
is the order's target-object reference, and it is null on every instance ever observed
because the engine routes targets through the order object's coordinate triple
(`Ship:48` +16/+20/+24) rather than through an object reference. The one call site that
passes a non-zero target — 0x0047C321, `mov edx,[0x0082A8DC]; push esi; push edx` — I
could not prove `esi` non-null there, so I am **not** claiming a populated case exists in
this build.

### 3.3 It is persisted, and rebased on load

`0x004E9C90`'s neighbourhood is the reader for the same slot, and it does something
already familiar:

```
004e9c9b  0305587c8500  add eax, dword ptr [0x857c58]   ; rebase the id
004e9ca6  e845df0400    call 0x537bf0                   ; then resolve to a node
004e9cb2  8901          mov dword ptr [ecx], eax
```

`[0x00857C58]` is the **same id-rebase global** the report documented for `ROUT`'s
reference field. So `Ship:56` travels in the save blob as a plain object id and is
rebased on load, exactly like the route's origin-planet reference. That is a useful
consistency check on the report's "injected ids may need to be written relative to that
base" note — it is not specific to `ROUT`, it is how the engine resolves every persisted
object reference.

### 3.4 Practical consequence

* `Ship:56` (and `Fleet:56`) can be **left alone** by an external controller. It is
  inert for every order type the report has catalogued, because those all carry
  coordinates.
* Do **not** write `Ship:52` without going through the pair. `Ship::SetCommand` writes
  `Ship:52`, `Ship:56`, `Ship:60` and `Ship:61` together, and the constructor sets
  `Ship:61 = 0x0B` when the order type is 8. Order type 8 is unaccounted for in the
  report's list (`0..7` are known, 6 and >7 open) — so **there is an order type 8 and it
  is distinguished by a byte at `Ship:61`**. Worth a line in the report as a new lead.
* Two more small annotations fall out: **`Ship:60`** is a byte, and **`Ship:61`** is a
  byte that reads `0x0B` for order type 8 and 0 otherwise.

---

## Summary table — Round 2

| address | conv | args | what it is | status |
|---|---|---|---|---|
| **0x00574180** | `__cdecl` | 4 (`ret`, caller-cleaned) — `(Planet*, vector<int>* indices, int newJobId, bool fromMilitary)` | **conscription / any citizen job change** | CONFIRMED |
| 0x004F6840 | `__thiscall` PP | 2 (`ret 8`) — `(container*, bool notify)` | SetCitizenList; writes `Planet:132` | CONFIRMED |
| 0x004F6870 | `__thiscall` PP | 1 (`ret 4`) — `(container*)` | SetMilitaryList; writes `Planet:156` | CONFIRMED |
| 0x004F5280 | `__thiscall` PP | 0 (`ret`) | migrates job-3 records `Planet:144` -> `Planet:168`; then enforces `space/10` by force-conscripting the surplus | CONFIRMED |
| 0x00516060 | `__thiscall` `Owner:728` | 1 (`ret 4`) — `(int count)` | draft cost; `sum(5*(T+k)+105)` | CONFIRMED |
| 0x0052A790 | `__cdecl` | 0 | `[0x0080AA00] >= 150`; false => conscription free | CONFIRMED |
| 0x004F1140 | `__thiscall` PP | 0 | available space; PP vftable slot 6 | CONFIRMED |
| **0x0054AA70** | `__thiscall` | 0 (`ret`) | research id -> definition record (`*0x110 + [0x857F08]`) | CONFIRMED |
| 0x0054A650 | `__cdecl` | 1 (`ret`) — `(int* outCount)` | picks one of six version-specific tables | CONFIRMED |
| 0x0054A970 | `__cdecl` | 0 (`ret`) | lazily builds/filters the table; writes `[0x857F08]` | CONFIRMED |
| **0x0054B140** | `__thiscall` | 1 (`ret 4`) — `(int multiplier)` | research cost = `round(scale x factor x multiplier)` | CONFIRMED |
| 0x0054A6F0 | `__cdecl` | 0 (`ret`) | the cost `scale` (per-game config on modern builds) | CONFIRMED |
| 0x0054B200 | `__thiscall` | 1 (`ret 4`) — `(int index)` | prerequisite at index, over A then B | CONFIRMED |
| 0x0054B7D0 | `__thiscall` | 2 (`ret 8`) | tests the mutual-exclusion list | CONFIRMED |
| 0x0054AA90 | `__thiscall` | 2 (`ret 8`) | effect description; source of the kind table | CONFIRMED |
| 0x00516180 | `__thiscall` `Owner:728` | 1 (`ret 4`) — `(int traitId)` | civ-trait value, lazily rebuilt from `Owner:108` | CONFIRMED |
| **0x004D9DC0** | `__thiscall` Ship | 1 (`ret 4`) — `(ShipCommand*)` | writes `Ship:52/56/60/61` as a unit | CONFIRMED |
| 0x004E9BA0 | `__thiscall` | 2 (`ret 8`) — `(int type, EJBO* target)` | ShipCommand ctor; `Ship:56 = GetReferenceNode(target id)` | CONFIRMED |
| 0x004E9BF0 | `__thiscall` | 3 (`ret 0xC`) | ShipCommand ctor, no target | CONFIRMED |
| 0x004E9C30 | `__thiscall` | 3 (`ret 0xC`) — `(int, EJBO*, float)` | ShipCommand ctor with a float | CONFIRMED |

### New offsets for the annotation merge

* **`Planet:132`** — citizen-list container wrapper (PP+0x2C). The `Planet:144` vector
  sits 12 bytes into it. This, not the vector, is what the engine's setters take.
* **`Planet:156`** — stationed-military container wrapper (PP+0x44); `Planet:168` sits
  12 bytes into it.
* **`Owner:8`** — treasury, CONFIRMED as the field conscription debits
  (`owner_primary+0x3c`).
* **`Owner:728`** — civilization-trait container (`owner_primary+0x30c`); `[+0]` points
  back at the Owner.
* **`Owner:932 .. Owner:1112`** — the 46 signed civilization-trait values, cached and
  rebuilt from `Owner:108`. `Owner:932` reading -1 means the cache is invalid.
  **Trait 18 = ship speed %** and **trait 38 = planet space %** are pinned; the rest are
  numbered only.
* **`Ship:56` / `Fleet:56`** — the order's target-object reference node; the static null
  node 0x00857C54 whenever the order has no object target, which is every case observed.
  Persisted as a plain object id and rebased by `[0x00857C58]` on load.
* **`Ship:60`, `Ship:61`** — the remaining two bytes of the `ShipCommand`; `Ship:61`
  reads 0x0B for order type 8.
* **`Ship:52`** — add the note that there **is** an order type 8, and that this field
  must be written together with `Ship:56/60/61`.
* Facility type ids run to at least **20**, not 9 — see the `grants` column in 2.7.
* Research/doctrine ids are indexes into an 80-entry table; ids **73-76 are empty
  stubs** and must never be selected.
