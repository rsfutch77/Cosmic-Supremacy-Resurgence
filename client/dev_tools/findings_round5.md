# Findings: static analysis, Round 5

Method as before: capstone over `client/CosmicSupremacy_Resurgence.exe`, VA -> file offset
through the PE section table. **No process was attached, read, or launched.** Every claim
is "the binary says", never "the game did".

* **CONFIRMED** — the instruction bytes say it, and I quote them.
* **inferred** — consistent with the bytes, not fully proven. Called out every time.

Prior rounds: `findings_production_path.md` (R1/R2), `findings_round3.md`,
`findings_round4.md`. Offset frames from those rounds are reused throughout.

**Round 5 corrects one Round 4 claim** (section 3.0) — the user caught it live first, and
they were right.

---

## 0. A note on Objective 3 and who found what

Mid-round the user reported having settled most of Objective 3 themselves against the live
engine:

* facility and ship builds accumulate **production** in `Planet:120`; cash is involved
  only via **hurry**;
* **hurry cost = 4 x (total - progress)**, gated at >= 50% progress;
* `Planet:300` is the **already-hurried** guard (0 before the call, 1 after);
* total production needed = `PlanetProperties` vftable slot `+0x74`; progress getter is
  `0x004F0260`.

Those are theirs, not mine, and I have not duplicated the work. What I add below is a
static cross-check of two of them (both hold), the correction to my Round 4 error, and the
one part they flagged as still open — **upkeep** (section 3.2).

Static cross-checks, both CONFIRMED:

```
004f0260  8b4120  mov eax, dword ptr [ecx + 0x20]   ; PlanetProperties + 0x20 = Planet:120
004f0263  c3      ret                                ; -> progress getter, exactly as reported
```

and `PlanetProperties` vftable slot `+0x74` resolves to **`0x004F0230`**, whose body is
`GetProductionTotal`: it asks the owner twice and then calls
`0x00555810(this+0x5c, [this+0xd0], ownerValue)` — where `[this+0xd0]` is `Planet:296`, the
current production object. So the total is computed *from the production object*, per
planet, per call. Consistent with the user's reading.

---

## 1. Diplomacy

### 1.1 (a) The relation codes — CONFIRMED from the game's own string table

There is a pointer table at **0x00776DA4** holding five state names in code order. This is
not inference; it is the array the UI indexes:

| code | string | address |
|---|---|---|
| **0** | `Neutral` | 0x00776EA0 |
| **1** | `War` | 0x00776EA8 |
| **2** | `Cease-Fire` | 0x00776EAC |
| **3** | `Peace` | 0x00776EB8 |
| **4** | `Alliance` | 0x00776EC0 |

Two adjacent tables complete the picture — the **action** menu at 0x00776DB8
(`Propose Treaty`, `Declare War`, `Offer Cease-Fire`, `Propose Peace Treaty`,
`Propose Military Alliance`) and the **news** phrasing at 0x00776DCC (`Proposes Treaty`,
`Declares War`, `Offers Cease-Fire`, ...). Note the action and news tables are indexed
1:1 with each other but **not** with the state table — `Declare War` is action index 1 and
`War` is state 1 only by coincidence of ordering.

Everything cross-checks:

* `0x00535080` (R4's `IsAtWarWith`) tests `code == 1` — **War** ✓
* `0x005350C0` tests `code == 4` — **Alliance** ✓
* `0x00534D90` returns the **raw code**, and returns **0** when no record exists — so
  "no record" and "Neutral" are the same thing to the engine. CONFIRMED:

```
00534d99  e8a2feffff  call 0x534c40          ; Contains(other)?
00534d9e  84c0 ; 7507 test al,al ; jne
00534da3  33c0        xor eax, eax            ; absent -> 0 (Neutral)
00534dac  e88ff5ffff  call 0x534340           ; Find(other)
00534db1  8b4004      mov eax, dword ptr [eax + 4]   ; -> the raw code
```

The record is keyed by the other civ's **object id** (`other->[+0x30]`, which for an Owner
primary at tag-52 is annotation `Owner:-4`), and `Find` returns `node + 0x14`. Relation
record layout, all CONFIRMED from the accessors that touch each field:

| offset | meaning | accessor |
|---|---|---|
| +0x00 | key (other civ's object id) | — |
| **+0x04** | **relation code**, 0-4 as above | 0x00534D90 (get), 0x0055BD40 (set) |
| +0x09 | byte flag, set to 1 by 0x00534D10 | 0x00534CE0 (get) |
| +0x0A | word counter, `inc` by 0x00534CB0 | — |
| +0x10 | **word, a reputation/standing score clamped to 100** | 0x00534D50 adds, clamps at 0x64 |
| +0x12 | word, `+= 150` conditionally by 0x00534D10 | — |
| +0x18/+0x19 | two bytes copied out by 0x00534410 | 0x00534410 |

### 1.2 The default relation is War, not Neutral

This is the finding I did not expect and it matters for the Exterminate plan.
`0x00534F90` is the **first-contact** function: it fires when two civs meet, skips if a
record already exists, and picks the initial code. CONFIRMED:

```
00534fb3  6a02        push 2 ; call 0x4d4320      ; GetGameOption(2)
00534fbf  83782801    cmp dword ptr [eax + 0x28], 1
00534fc3  7e2e        jle 0x534ff3                ; option <= 1 -> leave code 0 (Neutral)
00534fc5  ...         esi = f([self+0x38]) ; eax = f([other+0x38])   via 0x0057BD80
00534fe4  2bf0        sub esi, eax
00534fe9  f7de        neg esi
00534feb  1bf6        sbb esi, esi
00534fed  83e6fd      and esi, 0xfffffffd
00534ff0  83c604      add esi, 4                  ; equal -> 4 (Alliance); differ -> 1 (War)
00534ff3  6a1d        push 0x1d ; call 0x4d4320   ; GetGameOption(29)
00534ffd  80783000    cmp byte ptr [eax + 0x30], 0
00535001  7405        je 0x535008
00535003  be01000000  mov esi, 1                  ; option set -> force War
00535018  ...         call 0x534a70 / 0x532e60    ; insert the record with code esi
00535038  c6819a01000001  mov byte ptr [ecx + 0x19a], 1   ; Owner:358 "diplomacy dirty"
```

The `neg`/`sbb`/`and`/`add` idiom is a branchless select: **equal -> 4, different -> 1.**
`0x0057BD80` is applied to `owner_primary + 0x38` (= `Owner:4`) and its results are only
compared for equality — **inferred**: a team id. So:

> **On first contact, two civs on the same team default to Alliance (4); two civs on
> different teams default to War (1).** `GetGameOption(2) <= 1` leaves them Neutral (0)
> instead, and `GetGameOption(29)` forces War regardless.

**Operational consequence for R-XTM-03:** in an ordinary multi-team galaxy the AI is
probably *already at war* with every civ it has met, and needs no declaration at all. That
should be checked before building any declare-war actuator — read `Owner:248` for the
target and see whether it already reads 1. This is also the third "free-for-all" game
option in the family (R4 found `GetGameOption(31)` forcing combat hostility;
`GetGameOption(29)` forces the initial relation).

### 1.3 (c) The relation record is symmetric — one call writes both civs

CONFIRMED, and unambiguously. `0x0055BD40` is the applier; `edi` is a **Treaty** object
whose two parties are at `[edi+0xc]` and `[edi+0x10]` (annotations `Treaty:4` / `Treaty:8`
— the tag-8 arithmetic matches the report exactly) and whose new code is at `[edi+0x14]`:

```
0055be9c  51            push ecx                       ; ecx = party B (Owner primary)
0055be9d  8d882c010000  lea ecx, [eax + 0x12c]         ; eax = party A -> A's Owner:248
0055bea3  e89884fdff    call 0x534340                  ; Find(B) in A's map
0055bea8  8b4f14        mov ecx, dword ptr [edi + 0x14]
0055beab  894804        mov dword ptr [eax + 4], ecx   ; A.relations[B] = code

0055bece  51            push ecx                       ; ecx = party A
0055becf  8d882c010000  lea ecx, [eax + 0x12c]         ; eax = party B -> B's Owner:248
0055bed5  e86684fdff    call 0x534340                  ; Find(A) in B's map
0055beda  8b4f14        mov ecx, dword ptr [edi + 0x14]
0055bedd  894804        mov dword ptr [eax + 4], ecx   ; B.relations[A] = SAME code
```

**Same code, both directions, in one call.** There is no "my view / their view" — the
relation is a single shared fact stored twice. An external controller that writes only its
own `Owner:248` produces an inconsistent galaxy: R4 showed the battle phases call
`IsAtWarWith` on *a* civ's map, so which side is asked determines the answer.

**This also resolves a standing open item in the reconstruction report.** The report lists
`Treaty:12` as "? = 1 on a declared war". `Treaty:12` is `[treaty+0x14]`, which is exactly
the value copied into both relation records — so **`Treaty:12` is the relation code, and
its observed value 1 is `War`** ✓, matching the string table. (I did **not** establish what
`Treaty:16` is; the report's reading of 3 there does not fit the 0-4 state enum, so treat
`Treaty:16` as still unknown rather than assuming it is the same enum.)

One more behaviour in the same function: at 0x0055BE5C..0x0055BE77 it reads A's *existing*
code, and if it **was 4 (Alliance)** and the new code is not 4, it sets a local byte — the
"alliance was broken" marker that drives the reputation penalty.

### 1.4 (b) The command handlers

Applying R4 section 4.3's own rule — "find the 0x0057xxxx handler and read its offline
branch" — gives four diplomacy commands, identified by their `BeginSection` tags:

| handler | tag | role |
|---|---|---|
| `0x0056E620` | `TRKP` | keep / accept an existing treaty |
| **`0x0056F310`** | **`NWTR`** | **new treaty — this is the declare-war path** |
| `0x0056F470` | `MDTR` | modify treaty; calls the applier `0x0055BD40` at 0x0056F66B |
| `0x0056FB40` | `DLTR` | delete / cancel treaty |

**`0x0056F310` — `__cdecl`, plain `ret`, 1 stack argument: a `Treaty*`.** CONFIRMED:

```
0056f39e  685254574e  push 0x4e575452           ; 'NWTR'
0056f3a5  e8b66e0700  call 0x5e6260             ; BeginSection(tag, version 1)
0056f3b0  e8dba4feff  call 0x559890             ; write the treaty
0056f3ba  e8616f0700  call 0x5e6320             ; EndSection
          ... flush, await reply ...
0056f3ea  e811c9feff  call 0x55bd00             ; adopt the server's treaty object
0056f3f9  e8625dfcff  call 0x535160             ; insert into localOwner+0x12c (Owner:248)

; ---- OFFLINE BRANCH ----
0056f40f  803da0f1860000  cmp byte ptr [0x86f1a0], 0
0056f416  7422            je <done>
0056f418  8b742420        mov esi, dword ptr [esp + 0x20]   ; the Treaty*
0056f41e  e8bdcffeff      call 0x55c3e0                     ; COMMIT (reaches 0x0055BD40)
0056f425  e8b691feff      call 0x5585e0                     ; -> the acting Owner
0056f42b  c6809a01000001  mov byte ptr [eax + 0x19a], 1      ; Owner:358 dirty flag
0056f432  e8b90bf9ff      call 0x4ffff0                     ; news / notification
```

**The handler validates nothing.** No cooldown, no alliance check, no cost, no
build-up-phase test. Same anatomy as every cheat in R4 §4.3.

All the validation lives in the **UI** function `0x004BDE50`, and its own strings enumerate
it exactly:

| refusal / prompt | string |
|---|---|
| build-up-phase protection | *"You can not yet declare war to this player since this player is still in his build up phase."* (0x00767228) |
| teammate protection | *"You can only declare war to a member of your team that has gone more than 50% inactive."* (0x007671D0) |
| existing-treaty confirm | *"You have got an active %s treaty with %s! Are you sure you want ..."* (0x00767108) |
| reputation gain notice | *"You would gain %s reputation for successfully attacking this pla..."* (0x007670B8) |
| reputation loss warning | *"Warning: You would lose %s reputation for actually attacking thi..."* (0x00767068) |
| final confirm | *"Are you sure you want to declare war to %s?"* (0x00767038) |

The sibling `0x004BD7D0` carries the cancel menu — *Cancel Cease-Fire*, *Cancel Peace*,
*Cancel Military Alliance*, *Cancel Treaty*, *Withdraw*, *Accept* — confirming the four
non-Neutral states are all reachable and all cancellable.

### 1.5 (d) What else the command path does

* **Reputation.** `Owner:248` record `+0x10` is a per-civ standing score, clamped to 100,
  mutated by `0x00534D50(other, delta)`. The declare-war UI quantifies the gain and loss
  before you confirm, so a raw write **skips the reputation consequence entirely** — which
  is a *benefit* to a cheating AI, and therefore worth flagging as a fairness issue rather
  than a capability gap.
* **News.** `0x004FFFF0(treaty)` is called on the offline commit; the news phrasing table
  at 0x00776DCC (`Declares War`, ...) is what it indexes. An AI that writes the relation
  directly produces **no news item**, so other players get no notification — the surprise
  attack is silent. That is the single most abusable part of this path.
* **Dirty flag.** `Owner:358` (`owner_primary + 0x19a`) is set to 1 on any relation change,
  both here and in first contact. **inferred**: drives the diplomacy UI refresh.

---

## 2. Planetary defence strength

The 7-argument virtual R4 flagged is `BattleCalculatorPlanetaryDefense` vftable slot
`+0x80` = **`0x0044BB10`**, `__thiscall`, **`ret 0x1c` = 7 stack args**, of which the first
four are `int*` out-parameters (all zeroed at entry). CONFIRMED.

**It walks the planet's built-facility map and sums per-facility contributions:**

```
0044bb60  8d795c      lea edi, [ecx + 0x5c]           ; the facility map container
0044bb69  ...         standard MSVC tree walk over edi
0044bbb8  8b5e14      mov ebx, dword ptr [esi + 0x14] ; node+0x14 = COUNT of this facility
0044bbbb  83c60c      add esi, 0xc                    ; node+0x0c = the embedded Facility
0044bbc0  e8ebe00d00  call 0x529cb0                   ; -> its definition record
0044bbc5  83780400    cmp dword ptr [eax + 4], 0
0044bbc9  0f84b7010000 je <skip>                      ; def[+4] == 0 -> not a defence facility
0044bbcf  015c2414    add dword ptr [esp + 0x14], ebx ; accumulate the count
```

`{Facility at node+0x0c, count at node+0x14}` is exactly the node shape the report
documents for **`Planet:204`**, so this is the built-facility map. (**inferred**: that the
container at `this+0x5c` *is* `Planet:204`; `BattleCalculatorPlanetaryDefense` shares many
vftable slots with `PlanetProperties`, so it likely derives from it, but `PP+0x5c` would be
`Planet:180` under the R1 frame, not `Planet:204`. The node shape is CONFIRMED; the
container's identity needs one more read. Do not assume the offset.)

**The scaling by research.** At 0x0044BC13 it builds a
`PlanetaryDefenseTechLevel{vftable 0x00758A0C, [0x00829F08]}`, resolves it via
`0x00546D50`, takes `[+4]` as a percentage `L`, and applies it through two getters:

```
; 0x00529E10(L):    result = def[+0x0C] * (L + 100) / 100
; 0x00529E70(L):    result = def[+0x10] * (L + 100) / 100
0052 9e3c  add ecx, 0x64          ; L + 100
0052 9e3f  imul ecx, [eax+0x0c]
0052 9e43  ...0x51EB851F / sar 5  ; divide by 100
```

So, putting it together:

> **planetary defence = for each built facility type with `def[+0x04] != 0`, taking its
> count from `Planet:204`, two strength components `def[+0x0C]` and `def[+0x10]`, each
> scaled by `(planetaryDefenseLevelBonus + 100) / 100`.** The four out-parameters carry
> the resulting component totals into the battle code, where the participant appears as
> **kind 1** and its contribution lands in the third damage pool (R4 §2.3).

`[0x00829F08]` is the global current planetary-defence tech level. The 8 research levels
(ids 27-34, effect kind 10) feed it; the light turret and the other defence buildings are
what supply `def[+0x0C]` / `def[+0x10]`.

### 2.1 The facility definition table — located, sized, field-mapped, values NOT extracted

`0x00529CB0` is `Facility::GetDefinition()`: `[this+4]` is the facility type id, the record
stride is **0x8C (140) bytes**, and the base is selected by content version
`[0x0080AA00]` — the same pattern as the research table. CONFIRMED:

| version | base | entries |
|---|---|---|
| < 8 | 0x0080A370 | 12 |
| 8..9 | 0x00809CE0 | 12 |
| 10..12 | 0x00809650 | 12 |
| 13..56 | 0x00808FC0 | 12 |
| 57..149 | 0x00808930 | 12 |
| >= 639 and `GameOption(3)->[+0x30]` | 0x00807230 | 21 |
| >= 646 | 0x008066B0 | 21 |
| **else (includes our 565)** | **0x00807DB0** | **21** |

Entry counts are CONFIRMED independently, from the CRT array-destruct registrations at
0x0074BD87..0x0074BE77, each pushing `(base, 0x8C, count, dtor 0x0055F120)`. **21 entries
is exactly the facility id range 0..20** that R2's research-grant table implied — a clean
cross-check between two separately-derived tables.

Field map, each named by the accessor that reads it (all CONFIRMED):

| offset | accessor | meaning |
|---|---|---|
| +0x04 | 0x00529ED0, and the `!= 0` test in 0x0044BB10 | **is-a-defence-facility marker / defence class** |
| +0x08 | 0x00529DB0 | — |
| **+0x0C** | 0x00529E10, `x (L+100)/100` | **defence strength component 1** |
| **+0x10** | 0x00529E70, `x (L+100)/100` | **defence strength component 2** |
| **+0x14** | 0x00529D80, `/ (1 + GameOption(1)->[+0x30])` | **the halvable recurring cost — see 3.2** |
| +0x18 | 0x0041A140 (Facility vftable slot 2) | — |
| **+0x1C** | 0x0041A150 (**Facility vftable slot 6** = `[0x00752BBC]`) | the value `CanBuildFacility` compares — see 3.0 |
| +0x20 | 0x0041A160 (slot 7) | — |
| +0x28 | 0x00537D70 | — |

**I did not extract the numeric values.** The table is BSS — the raw file bytes at
0x00807DB0 are zero — and its initialiser is **`0x00724760`** (282 references into the
table region). Unlike the research initialiser, it appears to write through a base register
rather than absolute addresses, so my R3 absolute-address interpreter returned zero
records and I am not going to guess. Two honest options:

1. **Read it live.** You have process access: 21 records of 0x8C at `0x00807DB0`, with the
   field map above. That is a ~3 KB read and gives real numbers immediately.
2. Extend the extractor with register-relative tracking and re-run against 0x00724760.
   Mechanical, maybe an hour.

Option 1 is better value and is why I stopped here rather than half-doing option 2.

---

## 3. Facility cost, production, and upkeep

### 3.0 Correction to Round 4

**Round 4 section 4.1 rank 4 said `CanBuildFacility` (0x004F5030) checks "cost <= treasury".
That was wrong**, and the user identified it live before I did. What 0x004F5030 actually
compares is `def[+0x1C]` (via `Facility` vftable slot 6 = `[0x00752BBC]` = `0x0041A150`)
against a value from `0x004F3100`. I never established that the latter is `Owner:8`, and I
never found any code debiting cash on a build — I should have said so at the time instead
of writing "cost <= treasury" as though it were settled. The user's live result — builds
consume **production** accumulated in `Planet:120`, cash only via **hurry** — stands, and
the R4 row should be read as "the build gate compares a definition field against a
resource-ish value" and nothing more until someone reads `0x004F3100`.

The exploit ranking in R4 §4.1 is unaffected in *order*, but the rank-4 row's stated
mechanism is not to be quoted.

### 3.1 (a)-(c) — deferred to the user's live results

See section 0. Their four findings are the answer to 3(a), 3(b) and 3(c), and my two static
cross-checks agree. I add one detail relevant to 3(c):

**`Planet:344` versus `Planet:300`.** R1/R2 established `Planet:344` (PP+0x100) is the
build-active flag, set only by `0x004EFF50` (which refuses when the production kind is 7 =
PilingUpWealth) and cleared by `SetProduction`. The user has now identified `Planet:300`
(PP+0xd4) as the **already-hurried** guard. That fits the two guards the governor rules
check together (R1 §2.6): a rule refuses to act when either `Planet:300` or `Planet:344`
is non-zero — i.e. "already building" **or** "already hurried this build". So the engine's
notion of "this planet is busy" is the pair, not either one alone.

### 3.2 (d) Upkeep — where it is accumulated

The recurring drain is **`def[+0x14]`**, read through `Facility` vftable **slot 1** =
`0x00529D80`, which halves it under a game option:

```
00529d82  6a01        push 1 ; call 0x4d4320    ; GetGameOption(1)
00529d8b  8a5830      mov bl, byte ptr [eax + 0x30]
00529d93  e818ffffff  call 0x529cb0             ; the definition
00529d98  8b4014      mov eax, dword ptr [eax + 0x14]
00529d9d  84db ; 0f95c1  test bl,bl ; setne cl
00529da5  41          inc ecx                    ; divisor = 1 or 2
00529da6  f7f9        idiv ecx                   ; -> def[+0x14] / divisor
```

That this is **upkeep and not build cost** is supported by two independent facts: the build
gate uses a *different* field (`+0x1C`, slot 6 — section 3.0), and the UI has a distinct
per-turn line *"Facilities-Upkeep\t%s\t(%d)"* (0x007545E3) alongside the warning *"...pay
the upkeep-cost for ships and facilities, hence some of them will get lost!"* (0x007500AD).
Two separate cost fields with two separate accessors, one of which is halvable by a
difficulty-style option, is exactly the shape of build-cost plus upkeep. **inferred**, but
well-supported.

**Where it is accumulated I have not pinned down.** The warning string says unpaid upkeep
causes facilities *and ships* to be **lost**, and turn stage 42 (`0x00543F70`) carries
*"Your empire is running low on money"* — so the charge and the failure handling are almost
certainly in the money stages (42, and `0x0052D0E0` at 49). That is one targeted read away
and is the honest remaining gap.

**For R-XPL-07 (liquidation) this is already enough to act on**, provided you read the
table live per section 2.1: rank candidates by `def[+0x14] / (1 + halveOption)` descending,
skipping any facility with `def[+0x04] != 0` if the planet is a defensive asset you want to
keep. You do not need to know where the charge lands to know what each building drains.

---

## 4. The remaining turn stages

Completing R4 section 3's table. `inner` is the per-object method the stage applies. Purpose
is **inferred** unless a string or a named callee backs it.

| # | stage | inner | one-line purpose | touches our fields? |
|---|---|---|---|---|
| 12 | 0x004DB130 | — | per-ship pass over the global ship vector; no notable callees, 64 instrs | no evidence |
| 13 | 0x004C9930 | `0x004EE0B0` -> `add ecx,0x60; jmp 0x004EFF90` | **ticks the planet's ProductionQueue** at `PP+0x138` (= `Planet:400`) via `0x00512F70`, gated on version >= 150 and the planet having an owner | **yes — the production queue** |
| 16 | 0x004CDEB0 | `0x004D9880`, `0x004CCAC0`, `0x004CC960` | **order maintenance / re-issue**: `0x004CCAC0` calls the **order constructor `0x004E8420` and `Ship::SetOrder`** | **yes — `Ship:48`/`Ship:52`** |
| 22 | 0x00518FD0 | — | takes the ship container as an argument and walks it (120 instrs) | no evidence |
| 23 | 0x004C99F0 | `0x004C6A10` | **rebuilds the spatial index**: `0x004C6A10` is a 3-D cell hash, `(x*K1 + y)*K2 + z` with K1/K2 at `[0x007FC844]`/`[0x007FC848]` | no |
| 27 | 0x004C99B0 | `0x004EE0D0` -> `add ecx,0x60; jmp 0x004F0200` | recomputes `PP+0x28` (= `Planet:128`) from the owner's civ-trait container (`owner+0x30c`, `0x00516410`); defaults to **300** when unowned | **yes — `Planet:128`** |
| 32 | 0x004DB410 | `0x005A79B0`, `0x004C9550` | per-ship pass touching game-level state; `0x004C9550` is `ret 0x10` (4 args) | no evidence |
| 33 | 0x0052DFE0 | `0x0053E890`, `0x0052D6D0` | owner-level pass; `0x0053E890` walks the ship container (476 instrs) | no evidence |
| 35 | 0x00511C30 | — | leaf, no calls, 137 instrs | no |
| 44 | 0x004DAF70 | `0x004D9F80` | per-ship, 97-instruction update | no evidence |
| 46 | 0x004DB080 | `0x0041A190` (`GetOwner`), `0x004E9E60` | per-ship: resolves the owner and calls `0x005150B0` on `ship+8` — a container accumulate | no evidence |
| 47 | 0x004EEC00 | `0x004F03C0` | per-planet: virtual owner getter, then `0x005150B0` on `PP+0x2c` — accumulate into a per-owner total | **`PP+0x2c` is `Planet:132`, the citizen container** — read, not obviously written |
| 48 | 0x005415C0 | `0x00551320` -> `add ecx,0x28; jmp 0x004F03C0` | the same accumulate, applied at `+0x28` of a different object | no evidence |

**Answering the question you actually care about — where our writes get clobbered:**

* **Stage 13 ticks the production queue** and **stage 16 constructs and sets ship orders.**
  Both run *before* combat (17). So a `Planet:296`/`Planet:344` write must land before
  stage 13 to be acted on this turn, and an order written after stage 16 will not be
  re-validated or re-pathed until next turn's stage 16.
* **Stage 27 unconditionally recomputes `Planet:128`** from civ traits every turn — do not
  write that field, it will be overwritten within the turn.
* Nothing in the newly-decoded stages writes `Planet:120`, `Planet:144`, `Planet:284`,
  `Planet:296`, `Planet:344`, `Planet:100`, `Owner:144` or `Owner:152`. Combined with R4's
  table, the clobber risks for our actuators remain: governor scripts (stages 5 and 37),
  the production queue tick (13), order maintenance (16), and the research tick (11).

A useful by-catch, CONFIRMED: `0x004D9880` (stage 16's predicate) is
`Ship::HasActiveTargetedOrder()`:

```
004d9880  8b413c  mov eax, dword ptr [ecx + 0x3c]   ; Ship:52 order type
004d9883  83f801  cmp eax, 1                        ; Move
004d9888  83f807  cmp eax, 7                        ; MoveNear
004d988d  83f804  cmp eax, 4                        ; Attack
004d9892  8b4140  mov eax, dword ptr [ecx + 0x40]   ; Ship:56 target-object node
004d9895  833800  cmp dword ptr [eax], 0            ; must be NON-EMPTY
004d989a  b801000000  mov eax, 1
```

**That independently corroborates R4 §0 and §2: `Ship:56` really is the order's
target-object reference node, and order types 1, 7 and 4 are the ones that use it.** R4
found only null values at every call site it could evaluate; here the engine explicitly
requires it to be non-null for those three types, which means populated cases must exist.
Worth a live check: read `Ship:56` on a ship under a Move order issued through the UI.

---

## 5. Summary table — Round 5

| address | conv | args | what it is | status |
|---|---|---|---|---|
| 0x00534D90 | `__thiscall` `Owner:248` | 1 (`ret 4`) | **GetRelationCode**; 0 when absent | CONFIRMED |
| 0x00535080 | `__thiscall` | 1 (`ret 4`) | `code == 1` (War) | CONFIRMED (R4) |
| 0x005350C0 | `__thiscall` | 1 (`ret 4`) | `code == 4` (Alliance) | CONFIRMED |
| 0x00534D50 | `__thiscall` | 2 (`ret 8`) | add to the standing score at `+0x10`, clamp 100 | CONFIRMED |
| 0x00534C40 / 0x00534340 | `__thiscall` | 1 (`ret 4`) | Contains / Find (returns `node+0x14`) | CONFIRMED |
| **0x00534F90** | `__thiscall` | 1 (`ret 4`) | **first contact**: same team -> 4, else -> 1 | CONFIRMED |
| **0x0055BD40** | `__thiscall`? | 2 (`ret 8`) | **apply relation change to BOTH civs' `Owner:248`** | CONFIRMED |
| 0x0055C3E0 | `__thiscall` | — | offline commit; reaches 0x0055BD40 | CONFIRMED |
| **0x0056F310** | `__cdecl` | 1 (`ret`) | **`NWTR` new-treaty / declare-war handler; validates nothing** | CONFIRMED |
| 0x0056E620 / 0x0056F470 / 0x0056FB40 | `__cdecl` | 1 | `TRKP` / `MDTR` / `DLTR` | CONFIRMED |
| 0x004BDE50 | — | — | the declare-war **UI validator** (all the rules live here) | CONFIRMED |
| 0x004FFFF0 | `__cdecl` | 1 | news / notification for a treaty change | CONFIRMED (role) |
| **0x0044BB10** | `__thiscall` | 7 (`ret 0x1c`) | **planetary defence strength**, 4 out-params | CONFIRMED |
| **0x00529CB0** | `__thiscall` | 0 | **Facility::GetDefinition**; stride 0x8C, 21 entries at 0x00807DB0 | CONFIRMED |
| 0x00529D80 | `__thiscall` | 0 | `def[+0x14] / (1 + option)` — **upkeep** | CONFIRMED (formula), role inferred |
| 0x00529E10 / 0x00529E70 | `__thiscall` | 1 (`ret 4`) | `def[+0x0C]` / `def[+0x10]` scaled by `(L+100)/100` | CONFIRMED |
| 0x0041A150 | `__thiscall` | 0 | `def[+0x1C]`; Facility vftable slot 6, the build gate's field | CONFIRMED |
| 0x004F0230 / 0x004F0260 | `__thiscall` PP | 0 | production total / progress (`Planet:120`) | CONFIRMED |
| 0x004D9880 | `__thiscall` Ship | 0 | has an active targeted order (types 1/7/4 + non-null `Ship:56`) | CONFIRMED |

### New offsets and constants

* **Relation record** in `Owner:248`: `+0x04` code (0 Neutral, 1 War, 2 Cease-Fire,
  3 Peace, 4 Alliance), `+0x10` standing score capped at 100, `+0x09`/`+0x0A`/`+0x12`
  auxiliary counters.
* **`Treaty:12`** = the relation code written into both parties (resolves a report open
  item). `Treaty:16` remains unknown.
* **`Owner:358`** (`owner_primary + 0x19a`) — diplomacy-changed dirty flag.
* **`Owner:4`** (`owner_primary + 0x38`) — feeds `0x0057BD80`, compared only for equality;
  **inferred** team id.
* **`Planet:400`** (`PP + 0x138`) — the planet's `ProductionQueue`, ticked at stage 13.
* **`Planet:128`** (`PP + 0x28`) — recomputed every turn at stage 27 from civ traits,
  default 300. Do not write.
* Facility definition table: **0x00807DB0, 21 x 0x8C** for content version 565;
  initialiser **0x00724760**; destructor 0x0055F120.
* Planetary-defence tech level global: **`[0x00829F08]`**.
* Spatial hash constants: `[0x007FC844]`, `[0x007FC848]`.
* Game options in the "make everyone hostile" family: **2** (relations enabled at all),
  **29** (force initial War), **31** (force combat hostility, R4). Also **1** (halve
  upkeep) and **3** (selects the 0x00807230 facility table).

### Open, in priority order

1. **Facility definition values** — read 21 x 0x8C at 0x00807DB0 live with the field map in
   2.1, or extend the extractor with register-relative tracking for 0x00724760.
2. **Where upkeep is charged** — turn stages 42 (`0x00543F70`) and 49 (`0x0052D0E0`) are the
   candidates; the "facilities will get lost" failure path is there too.
3. **`0x004F3100`** — what `CanBuildFacility` actually compares `def[+0x1C]` against. This
   is the loose end from my Round 4 error.
4. Whether the container at `0x0044BB10`'s `this+0x5c` is `Planet:204`. The node shape
   matches; the offset does not obviously.
5. `Treaty:16`, and the meanings of facility definition fields `+0x08`, `+0x18`, `+0x20`,
   `+0x28`.
