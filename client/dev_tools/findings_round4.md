# Findings: static analysis, Round 4

Method as before: capstone over `client/CosmicSupremacy_Resurgence.exe`, VA -> file offset
through the PE section table. **No process was attached, read, or launched.** Every claim
is "the binary says", never "the game did".

* **CONFIRMED** — the instruction bytes say it, and I quote them.
* **inferred** — consistent with the bytes, not fully proven. Called out every time.

Prior rounds: `findings_production_path.md` (R1/R2), `findings_round3.md` (R3). Offset
frames from those rounds are reused and held up everywhere they were tested.

**Round 4 corrects four Round 3 statements.** All are collected in section 5; the most
important is that Round 3's caution about treaties was justified — **there is a war check
in the combat path**, and Round 3 simply had not found it.

---

## 0. A structural unlock that underpins everything below

`ShipProperties` — the 44-byte record the reconstruction report already documented as the
element type of `Fleet:116` — **is also embedded in every `Ship`, at Ship allocation
`+0x70`**. Round 3 spotted the sub-object and guessed at one member; the battle code
proves the whole layout, and it lines up with three separately-derived annotations.

CONFIRMED, from the Ship constructor 0x004E8CA0:

```
004e8ca3  c706048b7600    mov dword ptr [esi], 0x768b04         ; vftable .?AVShip@@
004e8ce6  c74670008a7500  mov dword ptr [esi + 0x70], 0x758a00  ; vftable .?AVBattleProperties@@
```

and from the battle code's use of the same record (`esi` = a participant):

| record offset | Ship annotation | what the battle code does with it | already annotated as |
|---|---|---|---|
| +0x00 | `Ship:104` | vftable; slot 1 is the participant-kind getter | — (new) |
| +0x04 | `Ship:108` | `[[esi+4]] - 4` -> the `ShipDesign` at tag-12 | "SHIP DESIGN LINK" ✓ |
| +0x14/+0x18/+0x1c | `Ship:124/128/132` | `([esi+0x18]-[esi+0x14])>>4` = **crew count** | "crew records, 16-byte" ✓ |
| +0x20 | `Ship:136` | `fld dword ptr [esi+0x20]` = **condition**, and where it is written | "CONDITION 0.0..1.0" ✓ |

Three independent annotations landing on the right members of one sub-object is about as
strong a cross-check as static analysis gets. It also explains the report's note that the
condition "sits at +32 of each `Fleet:116` member record" — it is the *same record type*,
once embedded in a `Ship` and once stored by value in a `Fleet`.

Participant kinds, from vftable slot 1 of each class (CONFIRMED, each is a two-instruction
constant getter):

| kind | class | vftable | slot-1 body |
|---|---|---|---|
| **0** | `ShipProperties` | 0x00768C3C | `0x0049A190: xor eax,eax; ret` |
| **1** | `BattleCalculatorPlanetaryDefense` | 0x00758A14 | `0x004C6B10: mov eax,1; ret` |
| 2 | `BattleCalculatorShipProperties` | 0x00758AAC | `0x0043CBC0: mov eax,2; ret` |
| 3 | `BattleCalculatorPlanetProperties` | 0x00758AB8 | `0x00446970: mov eax,3; ret` |
| — | `BattleProperties` (abstract base) | 0x00758A00 | purecall |

**Only kinds 0 and 1 occur in real combat.** Kinds 2 and 3 are the Battle Calculator UI's
synthetic participants — a hypothetical stack of N ships of a design, and a hypothetical
planet. That is why the damage code has branches reading `count x strength` pairs at
`+0x30/+0x38`: those are the numbers a human types into the calculator. Anything derived
from a kind-2 or kind-3 branch says nothing about real combat.

---

## Objective 1 — the battle simulator

### 1.0 The real call chain (corrects Round 3)

Round 3 named `0x0051C0F0` "the battle simulator proper" and said it recursed. Both are
wrong, and the cause was an over-read: my function-end heuristic (ret-followed-by-int3)
ran past several unpadded function boundaries. Scanning every direct `call` target in
`.text` gives the true entries — 0x0051C0F0 ends at **0x0051D0BF**, and 0x0051D0C0,
0x0051D3D9, 0x0051D950, 0x0051DD50, 0x0051DE30, 0x0051DEE0, 0x0051DFB0 and 0x0051E170 are
separate functions. The two `call 0x51c0f0` instructions Round 3 read as recursion belong
to **0x0051DFB0**, a different function.

What `0x0051C0F0` actually is: **"total up one side's firepower"** (plus an optional text
breakdown). `0x0051D0C0` is its hitpoint counterpart. Their heaviest user is 0x0051DFB0,
which is the Battle Calculator's summary builder. They are also called by the resolver, so
Round 3's field list (section 3.3) is still right about *which* fields matter.

The real chain, CONFIRMED by direct-call edges:

```
0x0056D130  turn pipeline, stage 17
  -> 0x00523AC0  ComputeBattles      (bucket ships by position, >= 2 owners, radius 0.8)
     -> 0x00520570 / 0x00520700 / 0x00520AE0   the three battle phases
        -> 0x0051E8F0   GetOrCreateBattle   (operator new(0x110); a Battle is 272 bytes)
        -> 0x0051E560   THE ROUND LOOP
           -> 0x0051E170   resolve one exchange (both directions)
              -> 0x0051C0F0  side firepower totals
              -> 0x0051D0C0  side hitpoint totals
              -> 0x0051BB30  APPLY DAMAGE  <-- writes Ship:136
```

(Round 3 listed `0x00520860` as a phase; it is not a function entry — it is inside
0x00520700. The third phase is **0x00520AE0**.)

### 1.1 (a) The round loop — two phases, 100 rounds each

`0x0051E560`, `__cdecl`, 1 arg (a vector of Battle*). CONFIRMED:

```
0051e576  c744242c00000000  mov dword ptr [esp + 0x2c], 0     ; round = 0
0051e57e  <outer loop head>
0051e5a8  e8638ffaff        call 0x4c7510                     ; shuffle the battle list
0051e5c8  <inner loop over the battles>
0051e5fb  d986f0000000      fld dword ptr [esi + 0xf0]        ; battle+0xF0, a float arg
0051e63a  e831fbffff        call 0x51e170                     ; ONE EXCHANGE
0051e63f  0844245b          or byte ptr [esp + 0x5b], al      ; accumulate "something happened"
0051e644  e867bdffff        call 0x51a3b0                     ; any live combatant left?
0051e64e  7406              je 0x51e656
0051e650  ff86e8000000      inc dword ptr [esi + 0xe8]        ;   yes -> battle+0xE8++
0051e678  e94bffffff        jmp 0x51e5c8                      ; next battle
0051e67d  8b44242c ; 40     mov eax,[esp+0x2c] ; inc eax      ; round++
0051e686  83f864            cmp eax, 0x64                     ; 100
0051e689  0f82effeffff      jb 0x51e57e                       ; loop
```

then a **second, structurally identical loop** over a different inner body
(0x0051E69B..0x0051E7B9) with the same bound:

```
0051e7b6  83f864            cmp eax, 0x64
0051e7b9  0f82dcfeffff      jb 0x51e69b
0051e7bf  8a44242b          mov al, byte ptr [esp + 0x2b]     ; return the accumulated flag
```

So: **two sequential phases, each running exactly up to 100 rounds**, with the battle list
re-shuffled at the top of every round. There is **no early break on "battle over"** — the
"anyone alive" test `0x0051A3B0` only increments `battle+0xE8`. **inferred**: once a side
is annihilated its firepower and hitpoint totals are zero, so the remaining rounds are
arithmetic no-ops; the loop burns iterations rather than exiting. That means **a battle
either ends by annihilation or is cut off at the 100-round cap with both sides alive** —
which is what an Exterminate strategy needs to know: a stalemate is a real outcome.

`0x0051A3B0`, `__cdecl` 1 arg, is the liveness test and it is unambiguous:

```
0051a3ec  ffd0        call eax             ; participant kind
0051a3ee  85c0 ; 7513 test eax,eax ; jne   ; kind 0 (a real ship)?
0051a3f2  d9ee        fldz
0051a3f4  d85e20      fcomp dword ptr [esi + 0x20]   ; condition > 0 ?
0051a401  b001        mov al, 1            ; -> TRUE, someone is alive
```

### 1.2 (d) The crew gate — SETTLED, and it exists

Round 3 marked this open and said not to encode a rule. **There is a hard crew gate, and
an undercrewed ship contributes exactly zero firepower.** CONFIRMED, in the firepower
accumulation loop of 0x0051C0F0:

```
0051c397  8b7e18      mov edi, dword ptr [esi + 0x18]
0051c39e  2b7e14      sub edi, dword ptr [esi + 0x14]
0051c3a6  c1ff04      sar edi, 4                       ; edi = CREW COUNT (Ship:124 vector)
0051c3a1  8b4604      mov eax, dword ptr [esi + 4]     ; the design reference node
0051c3b4  8d4810      lea ecx, [eax + 0x10]            ; -> ShipDesignData
0051c3b7  e824cd0200  call 0x5490e0                    ; the ShipDesign:76 getter
0051c3bc  3bf8        cmp edi, eax
0051c3be  0f9cc1      setl cl                          ; cl = (crewCount < value)
0051c3c1  385d28      cmp byte ptr [ebp + 0x28], bl    ; arg8
0051c3c4  7411        je 0x51c3d7
0051c3c6  d9ee ; d85c2410  fldz ; fcomp [esp+0x10]     ; condition == 0 ?
0051c3d1  0f8405010000 je 0x51c4dc                     ;   -> SKIP this ship
0051c3d7  3acb        cmp cl, bl
0051c3d9  0f85fd000000 jne 0x51c4dc                    ; UNDERCREWED -> SKIP this ship
```

`0x51c4dc` is past every firepower accumulation, so a skipped ship adds nothing to any
pool. Two gates, therefore:

* **crew count must be >= the design's `ShipDesign:76` value**, else zero firepower;
* when the caller's 8th argument is set, **condition must be > 0** as well.

**This reinterprets `ShipDesign:76`.** The report calls it a "PAYLOAD-DELIVERY FLAG"
reading 2 on Colony Ship and troop, 1 on corvette and the weapon-only designs. Here the
engine compares the ship's crew count against it and treats "fewer than this" as
non-functional. Read as **minimum crew required**, every observed value fits, and it
explains the report's own note that "a colony ship needs only the minimum" — the minimum
is 2, which is exactly what `:76` reads on the Colony Ship. Corroborated a second time in
`0x004E9FB0`, which clamps the effective crew to `min(crewCount, get76())` before scoring
it. **inferred** (the code establishes the comparison, not the designer's intent), but it
is a much better fit than "payload flag", and `ShipDesign:80` (7 on the personnel designs,
2 otherwise) remains the natural companion as crew *capacity*.

### 1.3 (b) The damage model

**The three firepower values do not combine. They are three independent damage pools.**
CONFIRMED — each is fetched by its own getter, scaled by the same per-ship multiplier, and
added to a *different* accumulator:

```
0051c48d  e82ec80200  call 0x548cc0     ; ShipDesign:60  light
0051c4a0  d84c2418    fmul [esp+0x18]   ;   x per-ship multiplier
0051c4a4  dc00 ; dd18 fadd/fstp [eax]   ;   -> accumulator at [esp+0x50]
0051c4a8  e873c90200  call 0x548e20     ; ShipDesign:64  heavy
0051c4bb  d84c2418    fmul [esp+0x18]   ;   -> accumulator at [esp+0x54]
0051c4c3  e8b8ca0200  call 0x548f80     ; ShipDesign:68  third
0051c4d0  d84c2418    fmul [esp+0x18]   ;   -> accumulator at [esp+0x64]
```

**The per-ship multiplier is a crew-experience rank bonus of 0% to 90%.** CONFIRMED
arithmetic, with every constant read out of `.rdata`:

```
0051c3e1  call 0x4e9fb0                  ; x = crew experience score (see below)
0051c3e6  fadd qword [0x7526a8]          ; + 1e-4        (guards log(0))
0051c3f4  call 0x671710                  ; log(x + 1e-4)
0051c405  fld qword [0x7526b0]           ; 2.0
0051c40b  call 0x671710                  ; log(2.0)
0051c418  fdivr                          ; -> log2(x + 1e-4)
0051c41c  call 0x6710d0                  ; ftol
0051c421  cmp eax,9 ; jg  -> clamp 9
0051c426  cmp eax,0 ; jl  -> clamp 0     ; rank = clamp(.., 0, 9)
0051c43e  fild
0051c447  fmul qword [0x75c710]          ; x 0.1
0051c44d  fadd qword [0x7526a0]          ; + 1.0
```

> **multiplier = 1.0 + 0.1 x clamp(floor(log2(crewExperience)), 0, 9)**

`0x004E9FB0` computes `crewExperience` by walking the ship's own crew records
(`Ship:124`, 16 bytes each), clamped to at most `ShipDesign:76` records, summing a
per-record value via `0x00412E60` against a threshold at `[0x007515D8]`. **inferred**: the
per-record value is the report's already-verified military experience
`0.2 x (currentTurn - turnAdded)`; I did not decode 0x00412E60. Either way the *shape* is
CONFIRMED: veteran crew is worth up to +90% firepower, and it is logarithmic, so the first
few turns of crew age buy most of the benefit.

**Survivability and how losses are computed.** `0x0051BB30` first classifies every target
into three counts (matching the three pools), then per pool:

```
0051bc47  d9e8        fld1
0051bc58  dd4530      fld qword ptr [ebp + 0x30]     ; incoming damage for this pool
0051bc5b  dc7518      fdiv qword ptr [ebp + 0x18]    ;   / this pool's total hitpoints
0051bc60  d8d1        fcom st(1)                     ; ratio vs 1.0
0051bc67  7a17        jp 0x51bc80                    ; ratio >= 1.0 -> survivors = 0 (wipe)
0051bc69  dee9        fsubp st(1)                    ; 1.0 - ratio
0051bc6b  dc0d98187500 fmul qword ptr [0x751898]     ; x 100.0
0051bc71  dc0de0ce7600 fmul qword ptr [0x76cee0]     ; x 0.9
0051bc77  e854541500  call 0x6710d0                  ; -> integer
0051bc93  e898f3ffff  call 0x51b030                  ; distribute over the pool
0051bc9a  e8c1d1ffff  call 0x518e60
```

> **poolSurvival% = round(100 x 0.9 x (1 - damage / poolHitpoints))**, and
> `damage >= poolHitpoints` wipes the pool.

The `x 0.9` is a flat 10% haircut on survival even in a one-sided fight — so a fight is
never completely free. CONFIRMED constants: `[0x00751898] = 100.0`, `[0x0076CEE0] = 0.9`.

The pool hitpoints come from `ShipDesign:52` (base) and `ShipDesign:56` (effective) via
`0x005473F0` and `0x00547FC0`. **The shield does not appear separately in the combat
path** — Round 3 already found `ShipDesign:72` (shield) is reached only from the report
builder. That is consistent with R2's reading that `:56` is the shield-adjusted value:
`:56` is what combat divides by, and the shield is folded in by the getter, not by the
resolver. **inferred**, but it is the only reading consistent with both rounds' call
graphs.

Cross-pool leakage: at 0x0051C022 and 0x0051C052 a pool's damage is multiplied by
`[0x00750360] = 0.5` before being applied to a target selected by the *other* pool's
classifier. **inferred**: weapon classes hit off-class targets at half effect. The
constant and the multiply are CONFIRMED; the exact pairing is not.

### 1.4 (c) Where `Ship:136` is written — found

Round 3 could not find the store because it searched for `ship+0x80`; with section 0's
layout the condition is at **`ShipProperties + 0x20`**, and the store is two instructions
inside the damage applicator. CONFIRMED, at `0x0051C09F` (and the twin at `0x0051C0AB`),
inside `0x0051BB30`:

```
0051c078  d9ee        fldz
0051c07a  d8d2        fcom st(2)                 ; is this ship's hitpoint total > 0 ?
0051c081  7a2d        jp 0x51c0b0                ;   no -> leave the condition alone
0051c083  d9ca        fxch st(2)
0051c085  def1        fdivrp st(1)               ; damage / hitpoints
0051c087  d9e8        fld1
0051c089  dee1        fsubrp st(1)               ; 1 - damage/hitpoints
0051c08b  d84c241c    fmul dword ptr [esp + 0x1c] ; x the ship's PREVIOUS condition
0051c09a  c644240f01  mov byte ptr [esp + 0xf], 1  ; mark "changed"
0051c09f  d95e20      fstp dword ptr [esi + 0x20]  ; <-- Ship:136
```

> **`Ship:136_new = Ship:136_old x (1 - damageTaken / effectiveHitpoints)`**

Multiplicative, so damage compounds across rounds and across turns — a ship at 0.63 that
takes another 50% share ends at 0.315, never resetting. That matches the report's observed
spread (1.0 -> 0.0056 and 1.0 -> 0.6301 in one battle) far better than any additive model:
0.0056 is what ~100 rounds of small multiplicative losses look like.

`0x0051BB30` is `__cdecl` and takes a large argument list (the two side totals, the three
pool damage doubles, the three target vectors and an out-pointer); it is called twice per
exchange from `0x0051E170`, once per direction. Its return in `al` is the "anything
changed" flag.

---

## Objective 2 — the hostility predicate

### 2.1 (a) Return polarity — it is not a bool

`0x0051A190` is `__cdecl`, 2 args `(vector<Participant*>* list, Owner* candidate)`, and it
returns an **int with three meanings**. CONFIRMED — `ebp` is the return value and is
written at exactly three places:

```
0051a1c1  bd06000000  mov ebp, 6          ; default: 6
...
0051a34f  33ed        xor ebp, ebp        ; 0  <- a hostile actor was found
0051a36a  33ed        xor ebp, ebp        ; 0  <- forced hostile by game option (below)
0051a370  bd01000000  mov ebp, 1          ; 1  <- a Planet is among the participants
0051a39c  8bc5        mov eax, ebp        ; return
```

The `mov ebp,1` is the last write and is unconditional on `ebx` (the `dynamic_cast<Planet*>`
result), so **1 wins over 0 whenever a planet is present**. Reading it as a boolean, as
Round 3 tentatively did, is wrong.

### 2.2 (b) Yes — war gates combat. Round 3's caution was correct.

Round 3 said "I did not find a treaty lookup on this path, but do not build on its
absence". Good instinct: **the predicate is `0x00535080`, and all three battle phases call
it.** CONFIRMED by the direct-call edges — `0x005205EC` (in 0x00520570), `0x005209A6`
(in 0x00520700) and `0x00520C26` (in 0x00520AE0).

```
__thiscall  bool IsAtWarWith(Owner* other)         ; 0x00535080, ret 4
00535089  e8b2fbffff  call 0x534c40                ; is there a relation record for `other`?
00535090  7511        jne 0x5350a3
00535092  33c0 ...    xor eax,eax ; cmp eax,1 ; sete cl   ; no record -> FALSE
005350a6  e895f2ffff  call 0x534340                ; find the record
005350ab  8b4004      mov eax, dword ptr [eax + 4]
005350b0  83f801      cmp eax, 1
005350b3  0f94c1      sete cl                      ; record[+4] == 1  -> TRUE
```

`ecx` is a diplomacy container at `owner_primary + 0x12c` = **`Owner:248`**, holding one
relation record per other civ with the relation code at `+4`. **Relation code 1 = at
war**, which is CONFIRMED indirectly but firmly: the order-target validator (section 4.3)
uses this exact call to gate Conquer and refuses with the literal string *"Conquering only
allowed on planets that we are at war with"*. `0x00534D90` and `0x005350C0` are sibling
predicates testing other relation codes (allied, etc. — not decoded).

**So the answer to the blocking question is: yes, a war state is required — and it is
checked in two independent places.** At order-issue time by the UI (section 4.3), and again
inside the battle phases by `0x00535080`. An AI that wants an Exterminate rule to actually
kill things must be at war (`Owner:248` relation code 1 with the target civ), not merely
issue an Attack order.

One escape hatch, CONFIRMED: `0x0051A190` also consults a game option and can force
hostility regardless of orders:

```
0051a35a  6a1f        push 0x1f
0051a35c  e8bf9ffbff  call 0x4d4320        ; GetGameOption(31)
0051a364  80783000    cmp byte ptr [eax + 0x30], 0
0051a368  7402        je 0x51a36c
0051a36a  33ed        xor ebp, ebp         ; -> hostile
```

**inferred**: this is the galaxy's free-for-all / no-diplomacy setting. With it on,
co-location is enough and order type does not matter. Read `GetGameOption(31)->[+0x30]`
before assuming a war declaration is needed.

For completeness, the order-type test Round 3 found is intact and is the *other* route to
hostility: `0x00561D00` (`return [this+0x3c]` = `Ship:52`) compared against **4 (Attack)**
and **5 (Conquer)** only, then the order's target owner resolved via `0x004D9870` and
compared against the candidate. **Order type 8 is still not attack.**

### 2.3 (c) Planetary defence participates as kind 1

CONFIRMED by three independent facts:

1. `0x0051A190` `dynamic_cast`s each participant to `Planet*` (type descriptor 0x007FAA4C)
   and returns **1** when one is present — the planet is a first-class participant.
2. `0x00523AC0` locates the object at each battle bucket's position within radius 0.8
   (`[0x00767D2C] = 0.8f`) and type-checks it, so the planet at the orbit point is pulled
   in automatically.
3. The participant kind **1** is `BattleCalculatorPlanetaryDefense`, and in the damage
   applicator kind 1 is counted into the third pool alongside kind 3
   (`0x0051BBA0..0x0051BC23: inc dword ptr [esp+0x18]`).

`0x00523AC0` also runs a per-planet pre-pass before any ship work —
`0x00523B32: mov ecx,[planets+esi*4]; add ecx,0x60; call 0x4f1bc0` — i.e. a
`PlanetProperties` method on every planet in the global array. **inferred**: arming or
resetting planetary defence for the turn. `0x004F1BC0` is a one-instruction thunk to
`0x004F1B70`; not decoded.

**Turrets specifically**: the light turret is `Facility 9` (R2 section 2.7) and planetary
defence level comes from research effect kind 10 (`PlanetaryDefenseLvl`, 8 levels, ids
27-34). The bridge from those to the kind-1 participant's strength is `[esi+0x7c]` and the
7-argument virtual at `[vft+0x80]` called in `0x0051C0F0` at 0x0051D207/0x0051D23A. Not
decoded — **open**, and the natural next target.

---

## Objective 3 — the turn pipeline, `0x0056D130`

57 direct calls, in execution order. `instrs` is the callee's instruction count (a size
proxy). "iterates" is CONFIRMED from references to the unique global container addresses
`[0x008553C4/C8]` (all planets) and `[0x00854628/2C]` (all ships). Strings are the
callee's own log/news literals — the strongest naming evidence available without reading
each one. Purpose is **inferred** wherever it is not backed by a string or a prior round.

A caution I want on the record: I first tried to attribute *field* reads/writes per stage
by matching member offsets, and the result was garbage — small offsets like `+0x18`,
`+0x20`, `+0x3c` occur on every class, so the signature matched everywhere. **That table
is not in this document because it was not sound.** What is below is only what the globals,
strings and call edges support.

| # | site | target | instrs | iterates | purpose / evidence |
|---|---|---|---|---|---|
| 0 | 0056d13a | 004037b0 | 24 | | UI/frame prologue on `[0x0086EFDC]` |
| 1 | 0056d13f | 00401b70 | 37 | | prologue |
| 2 | 0056d14e | 004cc0a0 | 722 | | galaxy generation extras — string *"created %d super planets"*; runs only when `[0x008578E8] == 0` |
| 3 | 0056d153 | 00543af0 | 50 | | calls 0x00542850 (visibility) and 0x00541810 |
| 4 | 0056d158 | 004dafe0 | 52 | SHIPS | per-ship: calls 0x004E6610 / 0x004DA4A0 / 0x004DA700 (order + route maintenance) |
| 5 | 0056d15d | 005bbff0 | 702 | PLANETS | **governor scripts** — *"executing governor scripts..."*, *"...done (took %.3f sec)"* |
| 6 | 0056d165 | 005a77f0 | 197 | | admiral/fleet rules pass (arg −1); uses the ship container |
| 7 | 0056d170 | 004df950 | 501 | SHIPS | ship arrival / order completion (arg out-param); the heaviest ship pass |
| 8-9 | 0056d181/19a | 00651689 | — | | `operator delete` on the temporaries from stage 7 |
| 10 | 0056d19f | 0052a720 | 9 | | tiny; calls 0x005E3360 |
| 11 | 0056d1a4 | 00545610 | 201 | | **research tick** (R3) — *"Research complete"*, *"Our scientists have just finished researching '%s'."* |
| 12 | 0056d1a9 | 004db130 | 64 | SHIPS | per-ship pass, no sub-calls of note |
| 13 | 0056d1ae | 004c9930 | 131 | PLANETS | per-planet `0x004EE0B0` |
| 14 | 0056d1b3 | 00540e60 | 63 | | per-owner pass |
| 15 | 0056d1b9 | 004cf640 | 1363 | | **movement / order execution** (arg 0). Uses the ship container and `objectAtPoint` |
| 16 | 0056d1be | 004cdeb0 | 78 | SHIPS | per-ship `0x004D9880` / `0x004CCAC0` / `0x004CC960` |
| **17** | **0056d1c3** | **00523ac0** | **493** | **PLANETS** | **COMBAT** (R3) — *"computing battles..."* |
| 18 | 0056d1c8 | 004cfd70 | 788 | | post-combat resolution; uses ship container + `objectAtPoint` + `GetGameOption` |
| 19 | 0056d1cf | 004cf640 | 1363 | | **movement / order execution, second pass** (arg 1) |
| 20 | 0056d1d8 | 005a77f0 | 197 | | admiral/fleet rules, second pass (args 1, −1) |
| 21 | 0056d1dd | 004d95c0 | 2 | | fetch the global ship container |
| 22 | 0056d1e3 | 00518fd0 | 120 | | takes the ship container as an argument |
| 23 | 0056d1e8 | 004c99f0 | 54 | SHIPS | per-ship `0x004C6A10` / `0x0044FCC0` |
| 24 | 0056d1ed | 0055ad20 | 35 | | treaty/diplomacy tick (0x0055Axxxx is the Treaty range) |
| 25 | 0056d1f2 | 0052f230 | 287 | | sandbox term limit — *"Approaching end of Sandbox Galaxy"* |
| 26 | 0056d1f7 | 00543b90 | 247 | | colonise/conquer caps — *"Reached Colonization Limit"*, *"Reached Conquering Limit"* |
| 27 | 0056d1fc | 004c99b0 | 77 | PLANETS | per-planet `0x004EE0D0` |
| 28 | 0056d201 | 00541050 | 547 | | team map sharing — *"sharing galaxy-map among team members..."* |
| 29 | 0056d20c | 004df950 | 501 | SHIPS | ship arrival pass, second run |
| 30-31 | 0056d21d/236 | 00651689 | — | | `operator delete` |
| 32 | 0056d23b | 004db410 | 188 | SHIPS | per-ship; calls 0x005A79B0 / 0x004C9550 |
| 33 | 0056d240 | 0052dfe0 | 93 | | calls 0x0053E890 / 0x0052D6D0 |
| 34 | 0056d245 | 0052f630 | 295 | | end-of-game / pacing — *"no end-turn set"*, *"game ends at turn %d"*, *"Game Progress Rate at turn %d"* |
| 35 | 0056d24a | 00511c30 | 137 | | leaf, no calls |
| 36 | 0056d252 | 005a77f0 | 197 | | admiral/fleet rules, third pass (args 1, 0) |
| 37 | 0056d257 | 005bbff0 | 702 | PLANETS | **governor scripts, second pass** |
| 38 | 0056d25f | 005a77f0 | 197 | | admiral/fleet rules, fourth pass |
| 39 | 0056d26a | 004df950 | 501 | SHIPS | ship arrival pass, third run |
| 40-41 | 0056d27b/294 | 00651689 | — | | `operator delete` |
| 42 | 0056d29c | 00543f70 | 198 | | economy warnings — *"Your empire is running low on money"*, *"Failed to build Ship(s) due to missing Ship-Resources"*, *"next turn"* |
| 43 | 0056d2a1 | 004ff8c0 | 373 | | wormhole upkeep — *"Wormhole lost"*, *"Wormhole disconnected"* |
| 44 | 0056d2a6 | 004daf70 | 35 | SHIPS | per-ship `0x004D9F80` |
| 45 | 0056d2ab | 0052f990 | 40 | | calls 0x0052E110 / 0x0052E290 / 0x0052E6A0 / `GetGameOption` |
| 46 | 0056d2b0 | 004db080 | 63 | SHIPS | per-ship `0x0041A190` / `0x004E9E60` |
| 47 | 0056d2b5 | 004eec00 | 36 | PLANETS | per-planet `0x004F03C0` |
| 48 | 0056d2ba | 005415c0 | 77 | | per-owner; `dynamic_cast` + 0x00551320 |
| 49 | 0056d2bf | 0052d0e0 | 411 | | profiling dump — *"SpeedTicks.txt"*, *"c:\SpeedTicks.txt"* |
| 50 | 0056d2c4 | 0052a8d0 | 8 | | `GetLocalOwner` (R1) |
| 51 | 0056d2cb | 00542850 | 980 | PLANETS | **visibility / fog of war** — *"visibility calculation for owner '%s' took %.3lf sec"* |
| 52 | 0056d2d0 | 005668c0 | 1 | | stub |
| 53 | 0056d2da | 00480040 | 9 | | calls 0x0048A8E0 / 0x004857E0 |
| 54 | 0056d2df | 004034e0 | 38 | | UI notify epilogue (0x004586A0, the listener walk from R2) |
| 55 | 0056d2eb | 00406880 | 223 | | galaxy-kind banner — *" - Tutorial Galaxy"*, *" - Demo Galaxy"*, *" - Test-Bed Galaxy"* |
| 56 | 0056d2f8 | 004828e0 | 164 | | epilogue |

**What this tells an external controller about write timing** — the practical point of the
objective:

* **Combat is stage 17, and it sits between two movement passes** (15 and 19). Ships move,
  fight, then move again. A write that arrives after stage 19 will not be acted on until
  the next turn's stage 15.
* **The research tick (11) runs before combat (17)**, so a research topic written this turn
  is charged and possibly completed before any battle.
* **Governor scripts run twice (5 and 37)** — once before everything and once near the end.
  Anything a governor rule would do to a planet happens at stage 5, i.e. *before* the
  production and combat stages. An AI competing with a governor should expect to be
  overwritten at stage 5.
* **Admiral/fleet rules run four times** (6, 20, 36, 38) with different argument pairs,
  bracketing movement and combat.
* **Fog of war is recomputed at stage 51, second-to-last.** So visibility read by an
  external controller reflects the *end* of the previous turn, which is consistent with
  the report's fog-of-war finding.

I have not decoded the individual purposes of stages 12, 13, 16, 22, 23, 27, 32, 33, 35,
44, 46, 47 and 48 beyond what they iterate and call. They are small, and each is one
targeted read away.

---

## Objective 4 — what the UI validates that a raw write does not

The pattern is now proven five times. For each field: the legal setter, what it checks,
and how badly a raw write can be abused.

### 4.1 Ranked

| rank | field(s) we write | legal setter | what the setter checks that a write skips | exploit severity |
|---|---|---|---|---|
| **1** | `Ship:52` = 5 (Conquer) or 6 (Bio bomb) | UI order path -> `0x00477D90` | **requires being AT WAR with the target's owner** (`0x00535080` on `Owner:248`) | **Severe.** Conquer and bio-bomb without a declaration, no diplomatic cost, no warning to the victim. Combat itself re-checks war (2.2), so this only pays off in combination — but the order-issue check is the *only* thing standing between an AI and a surprise conquest fleet. |
| **2** | `Owner:144` / `Owner:152` research topic | `0x0053DF80` via `STRS` `0x00573A40` | topic exists in the filtered list; not already done; **no exclusive sibling taken**; prerequisites (AND for tech, OR for doctrine); and if unmet it *plans* instead of selecting | **Severe.** Skip the whole tech tree; take every doctrine in a tier. Already documented in R3 §2.6, and the completion path validates nothing. |
| **3** | `Ship:48` / `Ship:52` / `Ship:76` (any order) | `0x004637F0` (the only caller of the order ctor `0x004E8420`) -> `0x00477D90` | per-type target legality: Colonize (3) requires an **unowned planet, or your own with zero population**; Scout (2) requires the target to be a system; Conquer (5) / Bio bomb (6) require war | **High.** Colonise an inhabited rival planet; scout-order a planet; retarget mid-flight. R1 already proved retargeting works and the engine navigates it. |
| **4** | `Planet:284` / `Planet:296` / `Planet:344` production | `0x004F9890` / `0x004F8290` (R1) | `0x004F5030 CanBuildFacility`: **facility type unlocked** for the civ, and **cost <= treasury**; plus the `Planet:300`/`Planet:344` busy guards the governor rules apply | **High.** Build facilities the civ has not researched, for free, on a planet already building something. |
| **5** | `Planet:144` job ids (conscription) | `0x00574180` (R2) | affordability (`cost <= Owner:8`, aborting before any list write), the confirm gate `0x00425120`, and a clamp on how many military units may be un-drafted | **Medium.** Free conscription, unlimited. Bounded by the population you actually have — and note `0x004F5280` will auto-conscript anything above `space/10` anyway. |
| **6** | `Planet:100` byte 3 (recruitment rate) | `0x005744A0`, the `RTMI` command | a bounds check — the rule UI carries *"CL1\|Value must be between 0 and 100"* | **Low.** The engine already caps the effect elsewhere (the report's own note that a rate above the facility-derived maximum did nothing without a military facility). Writing 100 mostly wastes food. |

### 4.2 The order-target validator — `0x00477D90`

This is the round's most valuable single find for Objective 4. `__cdecl`, returns `al`
(0 = refuse, 1 = allow). It switches on **`[0x0082A8DC]` = the pending order type** — the
same global Round 2 saw the `ShipCommand` factory read. Each branch and its refusal
message, all CONFIRMED:

```
00477db4  a1dca88200  mov eax, dword ptr [0x82a8dc]     ; the order type being issued
00477db9  83f802      cmp eax, 2                        ; SCOUT
   -> target's vftable[8]() must return 2, else refuse (message at 0x0075D8CC)
00477df3  83f803      cmp eax, 3                        ; COLONIZE
   -> target must be a Planet; if it is ours, 0x004F0EC0(planet+0x60) (population) must be 0;
      otherwise it must have no owner.
   -> else: "Colonizing only allowed on planets that do not have an owner,
             or my own planets that have got a population of zero"      [0x0075D858]
00477e71  83f805      cmp eax, 5                        ; CONQUER
00477ec8  8d8f2c010000 lea ecx, [edi + 0x12c]           ; localOwner + 0x12c  = Owner:248
00477ece  e8add10b00  call 0x535080                     ; IsAtWarWith(targetOwner)
00477ed5  0f8558010000 jne <allow>
   -> else: "Conquering only allowed on planets that we are at war with"  [0x0075D81C]
00477ef2  83f806      cmp eax, 6                        ; BIO BOMB  (same war check)
   -> else: "Bio bombing only allowed on planets that we are at war with" [0x0075D7E0]
```

**Bonus finding: order type 6 is BIO BOMB.** The reconstruction report lists `Ship:52`
values 0-5 and 7 and flags 6 as unaccounted for. It is bio-bombing, and it requires war —
which also explains the `BioBombingConfirmationDlg` class (vftable 0x00760658).

`0x004637F0`, the order-issuing UI, is the **only** caller of the order constructor
`0x004E8420` in the whole binary, and it calls `0x00535080` itself at `0x00463B66` as well.
Notably it does **not** call `0x004D98B0` (the crew summariser), which is consistent with
the report's observation that the UI happily issues an order to a crewless ship and the
engine cancels it at the turn boundary.

### 4.3 The general shape

Every one of the five cheats has the same anatomy, and it is worth stating as a rule
rather than a list: **the engine's per-turn code validates resources and nulls, and
nothing else. All game-rule validation lives on the command path** — the `__cdecl`
handlers in 0x0057xxxx that also serialise the action for multiplayer. That is not an
accident: in a networked game the server re-validates, so the client's turn code can
afford to trust its own state.

The operational consequence: **for any new field an actuator wants to write, the honest
test is "find the 0x0057xxxx command handler for it and read its offline branch".** If one
exists, a raw write is bypassing rules. Section 4.1's rank-1 and rank-2 rows are the two
where that matters enough to fix.

---

## 5. Corrections to Round 3

1. **`0x0051C0F0` is not the battle simulator and is not recursive.** It is a side-totals
   function ending at 0x0051D0BF; the resolver is
   `0x0051E560` -> `0x0051E170` -> `0x0051BB30`. Cause: my ret+int3 function-end heuristic
   over-ran unpadded boundaries. Fixed by scanning all direct call targets.
2. **`0x00520860` is not a function entry** — it is inside 0x00520700. The third battle
   phase is `0x00520AE0`.
3. **War does gate combat.** Round 3 said it had not found a treaty lookup and explicitly
   warned against building on that absence. `0x00535080` is called by all three battle
   phases; the warning was right.
4. **`0x0051A190` does not return a bool.** It returns 6 (default), 0 (hostile) or 1 (a
   planet is present), and 1 is written last and unconditionally.

Round 3's cost model, AND/OR split, table extraction, `Ship:52` order-type tests and
combat field list all survive unchanged.

## 6. Summary table — Round 4

| address | conv | args | what it is | status |
|---|---|---|---|---|
| **0x0051E560** | `__cdecl` | 1 | **battle round loop**: two phases, each capped at 100 rounds, list shuffled per round | CONFIRMED |
| 0x0051E170 | `__cdecl` | many | one exchange: totals for both sides, then damage both ways | CONFIRMED |
| **0x0051BB30** | `__cdecl` | many | **apply damage**; writes `Ship:136` at 0x0051C09F | CONFIRMED |
| 0x0051C0F0 | `ebp` frame | 8 | side firepower totals; contains the crew gate and the rank multiplier | CONFIRMED |
| 0x0051D0C0 | `__cdecl` | 4 | side hitpoint totals | CONFIRMED (identity) |
| 0x0051E8F0 | `__cdecl` | ~4 | get-or-create the 0x110-byte Battle object | CONFIRMED |
| 0x0051A3B0 | `__cdecl` | 1 | any live combatant in this group (kind 0 with condition > 0) | CONFIRMED |
| 0x0051A190 | `__cdecl` | 2 | combatant classifier; returns 6 / 0 / 1 | CONFIRMED |
| **0x00535080** | `__thiscall` `Owner:248` | 1 (`ret 4`) | **IsAtWarWith(Owner*)**; relation code 1 = war | CONFIRMED |
| 0x00534D90, 0x005350C0 | `__thiscall` | 1 (`ret 4`) | sibling relation tests (codes other than 1) | CONFIRMED (shape) |
| **0x00477D90** | `__cdecl` | 2 | **order-target validator**: per-type legality incl. the war requirement | CONFIRMED |
| 0x004637F0 | `__thiscall`? | — | the order-issuing UI; sole caller of the order ctor | CONFIRMED |
| 0x005744A0 | `__cdecl` | 2 | `RTMI` set-recruitment-rate command | CONFIRMED |
| 0x004E9FB0 | `__thiscall` ShipProperties | 0 | crew experience score (input to the rank bonus) | CONFIRMED (role), body partly |
| 0x004C6B10 / 0x0043CBC0 / 0x00446970 / 0x0049A190 | `__thiscall` | 0 | participant-kind getters -> 1 / 2 / 3 / 0 | CONFIRMED |

### New offsets and constants

* **`Ship:104`** — start of an embedded `ShipProperties`/`BattleProperties` sub-object;
  `Ship:108` (design), `Ship:124/128/132` (crew vector) and `Ship:136` (condition) are its
  members. Identical record type to a `Fleet:116` element.
* **`Owner:248`** — the diplomacy relation container (`owner_primary + 0x12c`); one record
  per other civ, relation code at `+4`, **1 = at war**.
* **`Ship:52` value 6 = BIO BOMB** (was unaccounted for in the report). Requires war.
* **`ShipDesign:76` is the minimum crew required**, not a payload flag — combat skips a
  ship whose `Ship:124` crew count is below it. (inferred, strongly supported)
* Battle object: **0x110 bytes**; `+0x30` and `+0x60` the two side groups, `+0xE8` a
  round-survival counter, `+0xEC` a state (5 is tested), `+0xF0` a float parameter.
* Combat constants: engagement radius `[0x00767D2C] = 0.8f`; survival haircut
  `[0x0076CEE0] = 0.9`; percentage scale `[0x00751898] = 100.0`; cross-pool factor
  `[0x00750360] = 0.5`; rank step `[0x0075C710] = 0.1`, base `[0x007526A0] = 1.0`, log base
  `[0x007526B0] = 2.0`, log guard `[0x007526A8] = 1e-4`.
* `GetGameOption(31)->[+0x30]` — when non-zero, all co-located foreign ships are hostile
  regardless of orders or war state. (inferred as a free-for-all setting)

### Open, in priority order

1. The kind-1 (planetary defence) strength computation: `[esi+0x7c]` and the 7-argument
   virtual `[vft+0x80]` called at 0x0051D207 / 0x0051D23A. This is what turns
   `PlanetaryDefenseLvl` research and light turrets into combat numbers.
2. `0x00412E60` — the per-crew-record experience value, to turn the rank bonus into an
   exact number of turns.
3. The exact target/pool pairing behind the 0.5 cross-pool factor.
4. Turn stages 12, 13, 16, 22, 23, 27, 32, 33, 35, 44, 46, 47, 48.
