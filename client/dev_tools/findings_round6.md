# Findings: static analysis, Round 6

Method as before: capstone over `client/CosmicSupremacy_Resurgence.exe`, VA -> file offset
through the PE section table. **No process was attached, read, or launched.** Every claim
is "the binary says", never "the game did".

* **CONFIRMED** — the instruction bytes say it, and I quote them.
* **inferred** — consistent with the bytes, not fully proven. Called out every time.

Prior rounds: `findings_production_path.md` (R1/R2), `findings_round3.md`,
`findings_round4.md`, `findings_round5.md`.

**Round 6 corrects one Round 5 claim** (section 2.0) and **closes two open items**
(R5 #3 and #4). The live facts supplied in the brief are used as given and are marked as
such where they matter.

---

## Objective 1 — why military recruitment stops

### 1.1 (a) What writes `Planet:124` — turn stage 13, second pass

CONFIRMED. Stage 13 (`0x004C9930`) makes **two** passes over the global planet array:

```
004c994e  8b0cb1  mov ecx, dword ptr [ecx + esi*4]   ; planets[i]
004c9951  e85a470200  call 0x4ee0b0                  ; pass 1: production-queue tick (R5)
...
004c997f  8b0cb1  mov ecx, dword ptr [ecx + esi*4]   ; planets[i] again
004c9982  8b01    mov eax, dword ptr [ecx]
004c9984  8b5024  mov edx, dword ptr [eax + 0x24]    ; Planet vftable slot +0x24
004c9987  ffd2    call edx
```

`Planet` vftable (0x00768DDC) slot `+0x24` is **`0x004EE0C0`**, a thunk
`add ecx,0x60; jmp 0x004FA490` — so the real work is **`0x004FA490`**, the planet's
per-turn **economic tick**. Its tail is the whole answer:

```
004fa5f9  e82282ffff  call 0x4f2820                   ; facility upkeep for this planet
004fa5fe  2bf8        sub edi, eax                    ; income -= facility upkeep
004fa60f  01783c      add dword ptr [eax + 0x3c], edi ; Owner:8 += net income
004fa612  8b7e54      mov edi, dword ptr [esi + 0x54]
004fa615  2b7e50      sub edi, dword ptr [esi + 0x50]
004fa61f  c1ff04      sar edi, 4                      ; stationed-military COUNT (Planet:168)
004fa62a  e801bd0100  call 0x516330                   ; per-unit military upkeep
004fa633  0fafef      imul ebp, edi
004fa63d  29683c      sub dword ptr [eax + 0x3c], ebp ; Owner:8 -= militaryUpkeep x count
004fa642  8b4020      mov eax, dword ptr [eax + 0x20] ; PP vftable slot +0x20
004fa645  8d4c241c    lea ecx, [esp + 0x1c]           ; out2
004fa649  51          push ecx
004fa64a  8d542428    lea edx, [esp + 0x28]           ; out1
004fa64e  52          push edx
004fa651  ffd0        call eax                        ; = 0x004F1580, the surplus splitter
004fa653  8b4c2424    mov ecx, dword ptr [esp + 0x24] ; out1 = FOOD share
004fa657  014e18      add dword ptr [esi + 0x18], ecx ; Planet:112 += food share
004fa65a  e831010300  call 0x52a790                   ; content version >= 150 ?
004fa661  7407        je 0x4fa66a                     ;   no -> military store not touched
004fa663  8b54241c    mov edx, dword ptr [esp + 0x1c] ; out2 = MILITARY share
004fa667  015624      add dword ptr [esi + 0x24], edx ; Planet:124 += military share
```

**Both the food store and the military store are fed from the same call, splitting one
number.** That single fact already explains the shape of your observation: food rising
while military is frozen is only possible if the *military share* specifically was zeroed
after the split, not if the surplus itself were zero.

### 1.2 (b)+(c) The growth formula, and every way it becomes zero

The producer is `PlanetProperties` vftable slot **`+0x20` = `0x004F1580`**, `__thiscall`,
`ret 8`, two `int*` out-params. This is also the function behind the UI's `[esp+0x3c]`:
`0x0042AFD8` calls the same slot with `&[esp+0x44]` and `&[esp+0x3c]`, and in the callee
`arg2` receives the military share — so **`[esp+0x3c]` is the military share**, which is
why `[esp+0x3c] == 0` selects *"No Military Is Recruited"*. (c) and (b) are the same
question, as you suspected.

CONFIRMED, in order:

```
; --- population
004f1592  eax = [esi+0x3c] - [esi+0x38]      ; the citizen vector (Planet:144/148)
004f1598  test eax, 0xfffffff0
004f159d  jne -> edi = eax >> 4              ; edi = citizen COUNT
004f159f  else  edi = [esi+0x104]            ; fallback: PP+0x104 = Planet:348

; --- food demand
004f15b0  ebx = virtual[0x10]()              ; the Owner
004f15cd  lea ecx, [ebx + 0x30c]             ; the civ-trait container (Owner:728)
004f15d3  call 0x5163d0                      ; per-citizen food need
004f15dc  imul ebx, edi                      ; DEMAND = need x citizens

; --- food produced, and the surplus
004f15df  edx = virtual[0x1c]
004f15e6  call edx                            ; edi = FOOD PRODUCED this turn
004f15ea  2bfb  sub edi, ebx                  ; SURPLUS = produced - demand
004f15ec  78 21 js 0x4f160f                   ; *** SURPLUS < 0  ->  military share = 0 ***

; --- the split
004f15ee  edx = virtual[0x60]
004f15f5  call edx                            ; the RECRUITMENT RATE
004f15f9  imul ecx, edi                       ; rate x surplus
004f15fc  ...0x51EB851F ; sar edx,5            ; / 100
004f160f  (negative-surplus path) eax = 0
004f1619  ecx = edi - eax                     ; food share  = surplus - military share
004f161d  mov [ebx], eax                      ; arg2 = MILITARY share
004f161f  mov [edx], ecx                      ; arg1 = FOOD share

; --- and then a second, empire-wide deduction
004f1621  eax = virtual[0x10]()               ; the Owner
004f162a  test eax,eax ; je <return>           ; unowned -> stop here
004f1639  e8126e0400  call 0x538450            ; ecx = Owner -> a PERCENTAGE
004f163e  esi = [ebx]                          ; the military share
004f1642  0fafc8  imul ecx, eax                ; share x pct
004f164d  fdiv qword ptr [0x751898]            ; / 100.0   (0x751898 = 100.0)
004f1668  fadd/fsub qword ptr [0x750360]       ; +/- 0.5 -> round
004f1673  2bf0  sub esi, eax                   ; share -= round(share x pct / 100)
004f1678  8933  mov dword ptr [ebx], esi        ; write it back
```

Supporting getters, all CONFIRMED:

* **rate** — PP slot `+0x60` = `0x00446B20`: `movsx eax, byte ptr [ecx+0xf]; ret`.
  `PP+0x0F` is **byte 3 of `Planet:100`** — the field we write. Note **`movsx`**: it is read
  as a *signed* byte.
* **per-citizen food need** — `0x005163D0`: content version < 150 -> `1`; unowned -> `10`;
  otherwise **`10 + GetCivTrait(0)`**. (R2 recorded `Doctrine: Reproduction` as
  `Trait 0 = -1`, i.e. 9 food per citizen — consistent.)
* **food produced** — PP slot `+0x1C` = `0x004F0800` (not decoded further).

> **militaryShare = round_down( (foodProduced − (10 + trait0) × citizens) × rate / 100 )**
> **then militaryShare −= round( militaryShare × pct / 100 )**, where `pct = 0x00538450(owner)`.

**The complete list of ways it becomes zero:**

| # | condition | evidence |
|---|---|---|
| 1 | `foodProduced <= foodDemand` — **no food surplus this turn** | `js` at 0x004F15EC |
| 2 | `rate == 0` | `imul` by the rate |
| 3 | `surplus × rate < 100` (integer truncation) | the `/100` |
| 4 | **`pct == 100` from `0x00538450`** — an empire-wide throttle | 0x004F1673 |
| 5 | planet has no owner (share is computed but the deduction path returns early; unowned planets are not recruiting anyway) | 0x004F162A |
| 6 | content version < 150 — the store is never written at all | `je` at 0x004FA661 |

**Conditions 1-3 and 6 are excluded by your measurements. That leaves #4**, and #4 is the
only candidate that is *empire-wide* (an `Owner` field), which matches two planets freezing
identically and simultaneously while their food behaviour differed completely.

### 1.3 What `0x00538450` returns — and which branch your client is on

```
00538450  803da0f1860000  cmp byte ptr [0x86f1a0], 0
00538457  7405            je 0x53845e
00538459  e972fdffff      jmp 0x5381d0          ; the "online" formula
0053845e  8b81c8020000    mov eax, dword ptr [ecx + 0x2c8]   ; = Owner:660
00538464  c3              ret
```

`[0x0086F1A0]` is the same global R4/R5 used to identify the offline branch of every
command handler. So:

**Branch A — `[0x0086F1A0] != 0` (offline): `pct = Owner:660`.**
`owner_primary + 0x2c8` = **`Owner:660`**. I searched all of `.text`: it is written in
exactly **two** places, `0x005466DE` (inside the Owner constructor `0x00546430`) and
`0x00545BE1` — and the latter sits in a run of field zeroing
(`[+0x2c0] = ebx; [+0x2c4] = bl; [+0x2c8] = ebx`) that is plainly initialisation. **Nothing
in the binary ever sets it to a non-zero value.** So on the offline branch the throttle is
inert, `pct = 0`, and recruitment is never damped. (It is read back at `0x0053C40E` inside
`0x0053C240`, the `DATA`/`SERV`/`PRIV`/`CLIE` **serialiser** — so it travels in the save
blob and could arrive non-zero from a server.)

**Branch B — `[0x0086F1A0] == 0` (server-connected): `pct` is a game-age ramp.**
`0x005381D0`, CONFIRMED:

```
005381d0  cmp dword ptr [0x80aa00], 0x25   ; content version < 37  -> return 0
005381dc  cmp byte ptr [0x86f45c], 0       ; != 0 -> return 0
005381e5  cmp byte ptr [0x86f4f8], 0       ; != 0 -> return 0
005381ef  push 4 ; call 0x4d4320           ; GameOption(4)->[+0x30]  (read twice)
00538206  neg edx ; sbb edx,edx ; and edx,0x4e ; add edx,0x48
                                            ; offset  = 0x48 (72) if option 0, else 0x96 (150)
0053820c  eax = [0x8578e8]                  ; the CURRENT TURN
0053821b  sub eax, edx                      ; turn - offset
0053821d  sub eax, dword ptr [esi + 0x300]  ; - Owner:716
00538224  imul eax, eax, 0x64               ; x 100
00538227  neg ecx ; sbb ecx,ecx ; and ecx,0x32 ; add ecx,0x64
                                            ; divisor = 100 if option 0, else 150
00538232  idiv ecx
00538234  clamp to [0, 100]
```

> **pct = clamp( (currentTurn − offset − `Owner:716`) × 100 / divisor , 0, 100 )**, with
> `offset` 72 or 150 and `divisor` 100 or 150 depending on `GameOption(4)->[+0x30]`.

This is monotonic in the turn number: **zero early, ramping to 100, then saturated.** At
`pct = 100` the military share is driven to exactly zero while the food share is untouched.

**inferred, but this is the reading that fits every one of your measurements**: two planets
freeze at the same moment regardless of their individual food, rate, garrison or
population, stay frozen for 30+ turns, and had been advancing earlier in the same game at
the same rates. A saturating empire-wide multiplier is the only mechanism in this function
that does that. `Owner:716` is presumably the turn the civ entered the galaxy
(**inferred** — it is only ever compared, never decoded further here).

**How to confirm it in one read, without me guessing:** read `[0x0086F1A0]`. If it is
non-zero you are on branch A and I am wrong — look again at conditions 1-3 with the exact
`0x004F0800` food-produced value. If it is zero you are on branch B; then read
`[0x008578E8]` (turn), `Owner:716`, and `GameOption(4)->[+0x30]`, and the formula above
should evaluate to 100 on both frozen planets' owner.

**If it is branch B, this is not a bug you can work around by tuning the rate** — the
throttle multiplies whatever the rate produces. Crew has to come from conscription
(R2 §1.1) rather than recruitment past that point in a galaxy's life.

### 1.4 (d) The maximum rate — partially answered, and the important half is CONFIRMED

**The consumption side applies no clamp at all.** The growth formula reads the rate through
`0x00446B20`, which is `movsx eax, byte ptr [ecx+0xf]; ret` and nothing else — no
comparison against a maximum, no reference to the military-camp count. So:

* Writing a rate **above** the UI's displayed maximum is used verbatim by the formula.
  Your self-imposed cap of 40 is **not required** by the engine.
* Because the read is `movsx`, **byte values >= 128 are negative**, which makes
  `rate × surplus / 100` negative and the military share negative — and `0x004FA667` is a
  plain `add`, so `Planet:124` would go *down*. **Keep the byte in 0..127.** That is the
  real constraint.

**Where the displayed maximum is computed I did not find.** I checked the neighbouring
`PlanetProperties` slots — `+0x64` = `0x00446B30` is `mov eax,[ecx+0x1c]` (`PP+0x1C` =
`Planet:116`, the max **food** storage, not a rate) — and a constant-pattern search for a
"+20 per camp on a base of 40" computation returned only CRT functions. The formatting is
done inside `0x0042AE70` at 0x0042B7B0/0x0042B834 via `'%d%%\n%s'`; the next step is to
trace the second `%d` there. It is a UI-only number and, given the paragraph above, it does
not gate anything — so I stopped rather than spend the round on it.

---

## Objective 2 — upkeep

### 2.0 Correction to Round 5

**Round 5 read `def[+0x14]` as upkeep. That is wrong** — you established live that it is the
**production build cost** (military camp 1500, farm 200). My reasoning was that `+0x14` had
a halving game option and the build gate used a different field, and I marked the
conclusion inferred; but "halvable" is at least as consistent with a build cost, and I
should have weighted the coincidence lower. The real upkeep field is `+0x18`, below.

### 2.1 (a)+(b) Facility upkeep — `def[+0x18]`, charged in cash at stage 13

`0x004F2820`, called from the planet tick immediately before the owner is credited.
CONFIRMED:

```
004f2829  8b4174  mov eax, dword ptr [ecx + 0x74]   ; Planet:204 tree root
004f2831  8d715c  lea esi, [ecx + 0x5c]             ; the facility-map container
          for each node:
004f285b      mov dword ptr [esp+0x18], 0x752ba4    ; build a stack Facility
004f2863      mov eax, dword ptr [ecx + 0x10]       ; node+0x10 = the facility TYPE ID
004f2871      mov edi, dword ptr [ecx + 0x14]       ; node+0x14 = the COUNT
004f2878      call 0x529cb0                         ; -> the definition record
004f287d      mov eax, dword ptr [eax + 0x18]       ; *** def[+0x18] ***
004f2880      imul eax, edi                         ; x count
004f2887      add ebx, eax
004f289f  return ebx
```

> **facilityUpkeep(planet) = sum over built facility types of `def[+0x18] × count`**, and it
> is subtracted from that planet's income *before* the net is added to `Owner:8`
> (`0x004FA5FE: sub edi, eax`).

`def[+0x18]` is also what `Facility` vftable slot 2 (`0x0041A140`) returns, so it has a
first-class accessor — consistent with being a displayed per-facility figure.

**Read it live**: you already have the 21 x 0x8C table at 0x00807DB0, so
`def[+0x18]` per facility id is a direct read. That unblocks R-XPL-07: rank liquidation
candidates by `def[+0x18] × count` descending.

### 2.2 Stationed military upkeep — also cash, also stage 13

CONFIRMED, in the same tick (quoted in 1.1):

```
004fa612  edi = ([esi+0x54] - [esi+0x50]) >> 4    ; the Planet:168 stationed count
004fa62a  call 0x516330                           ; per-unit upkeep
004fa63d  sub dword ptr [eax + 0x3c], ebp         ; Owner:8 -= perUnit x count
```

and `0x00516330` is:

```
00516333  call 0x52a790                    ; version >= 150 ?
0051633c  xor eax, eax ; ret               ;   no -> 0
00516340  push 0x20 ; call 0x516180        ; GetCivTrait(32)
00516349  add eax, 3                       ; +3
```

> **military upkeep = 3 + `CivTrait(32)` cash per stationed unit per turn.**

(R2 recorded `Doctrine: Elite` granting `Trait 32 +1` — so Elite makes garrisons *more*
expensive, 4/unit. Worth knowing before taking it.)

### 2.3 (c) Ship upkeep, the aggregate, and predicting repossession

`ShipDesign:48` (the annotated upkeep) is read by `0x005485D0`, whose only caller is
`0x004A92B0` — a **ship-design-editor** function, i.e. the displayed figure. So ship upkeep
is **not** charged from the planet tick; it is charged at the empire level.

The aggregate lives in **`0x0053C690`**, which `0x0053F390` calls with **eight
out-parameters** — that is the upkeep breakdown the UI's *"Facilities-Upkeep\t%s\t(%d)"*
line reads one component of. I did not decode which out-param is which; that is the
remaining piece of Objective 2 and it is one targeted read.

What I did decode is the thing you actually asked for — **the bankruptcy predicate**.
Turn **stage 42** (`0x00543F70`) walks every owner and calls `0x0053F390(&turnsLeft)`.
CONFIRMED:

```
0053f3c1  e8cad2ffff  call 0x53c690         ; ecx = TOTAL per-turn upkeep
0053f3c8  8b463c      mov eax, dword ptr [esi + 0x3c]    ; Owner:8 treasury
0053f3d9  85c0 ; 7d09 test eax,eax ; jge
   ; treasury >= 0:
0053f3e6      lea edx, [ecx + ecx*4]         ; upkeep x 5
0053f3e9      lea eax, [eax + edx*2]         ; treasury + upkeep x 10
0053f3ec      test eax, eax
   ; treasury < 0:
0053f3dd      eax = |treasury| ; cmp ecx, eax
0053f3ee  0f9cc3  setl bl                    ; bl = "money problem"
0053f3f5  if problem:
0053f400      fidiv                          ; treasury / (-upkeep)
0053f40f      call 0x4246e0
0053f417      mov dword ptr [esi], eax       ; *out = turns until ruin
0053f41a  return bl
```

> **The warning fires when `treasury + 10 × totalUpkeep < 0`** — i.e. fewer than ten turns
> of reserve — **and the out-param is the estimated number of turns until bankruptcy,
> `treasury / upkeep`.**

That is a directly usable predictor. Compute `totalUpkeep` yourself as
`sum over planets of ( facilityUpkeep + 3+trait32 per stationed unit ) + ship upkeep`, and
you can see the repossession coming ten turns out instead of discovering it. The
repossession itself ("ships and facilities get lost", 0x007500AD) is downstream of this
predicate; I did not locate the seizure loop.

---

## Objective 3 — `0x004F3100` settled

**It is not treasury and not population. It is SPACE.** `__thiscall`, `ret 0xC`, three
`int*` out-params. CONFIRMED:

```
004f310e  ffd2    call edx                          ; PP vftable +0x18 = 0x004F1140 = SPACE getter
004f3114  98      cwde                              ; the getter returns a 16-bit value
004f3115  8901    mov dword ptr [ecx], eax          ; out1 = TOTAL planet space
004f3117  8b5374  mov edx, dword ptr [ebx + 0x74]   ; Planet:204 tree root
004f311f  8d735c  lea esi, [ebx + 0x5c]             ; the facility-map container
          for each node:
004f3146      mov edi, dword ptr [edx + 0x14]       ; the COUNT
004f3149      lea ecx, [edx + 0xc]                  ; the embedded Facility
004f314e      mov eax, dword ptr [edx + 0x18]       ; Facility vftable slot 6 = def[+0x1C]
004f3151      call eax
004f3153      imul eax, edi                         ; def[+0x1C] x count
004f315a      add ebp, eax
004f317a  2bd5    sub edx, ebp                      ; total space - occupied space
004f317d  8910    mov dword ptr [eax], edx          ; out2 = FREE SPACE
004f317f  0fbf4b14 movsx ecx, word ptr [ebx + 0x14] ; PP+0x14 = Planet:108
004f3189  890a    mov dword ptr [edx], ecx          ; out3
```

> **`def[+0x1C]` is the facility's SPACE footprint**, and `0x004F3100` returns
> (total space, **free** space, `Planet:108`).

`CanBuildFacility` (`0x004F5030`) compares the candidate's `def[+0x1C]` against
`0x004F3100`'s second out-param — so the gate R4 mislabelled "cost <= treasury" is
actually **"does this facility still fit in the planet's remaining space?"**

Your current guess (required population — farm 7, shipyard 25, military camp 14, defence
agency 26) should be **relabelled as space footprint**. The values are the same numbers;
only the meaning changes, and the meaning matters: it is consumed against the planet's
space (`Planet:104` high half, per R1), and building competes with **population capacity**,
which is `space/10` (R4 §1.5). The report even has the confirming UI string,
*"Space per Facility: \t<auto>\x0c<Space>%d"* at 0x007554DA — it was sitting there the
whole time.

**This also closes R5 open item #4.** `PP+0x5c` is the facility-map **container wrapper**
and `PP+0x74` (= `Planet:204`) is its tree root — both `0x004F3100` and `0x004F2820` use
exactly that pairing (`[ebx+0x74]` as root, `ebx+0x5c` as container). So the container at
`0x0044BB10`'s `this+0x5c` in R5 §2 *is* the `Planet:204` facility map. Confirmed.

---

## 4. Summary table — Round 6

| address | conv | args | what it is | status |
|---|---|---|---|---|
| **0x004FA490** | `__thiscall` PP | 0 | **the planet economic tick**: income, upkeep, food/military split; writes `Planet:112`, `Planet:124`, `Owner:8` | CONFIRMED |
| 0x004EE0C0 | `__thiscall` Planet | 0 | thunk `add ecx,0x60; jmp 0x004FA490`; **Planet vftable slot +0x24**, invoked by stage 13 | CONFIRMED |
| **0x004F1580** | `__thiscall` PP | 2 (`ret 8`) | **the food-surplus splitter**; arg2 = military share, arg1 = food share | CONFIRMED |
| 0x00446B20 | `__thiscall` PP | 0 | recruitment rate = `movsx byte [PP+0xF]`, **no clamp** | CONFIRMED |
| 0x005163D0 | `__thiscall` traits | 1 (`ret 4`) | per-citizen food need = `10 + CivTrait(0)` | CONFIRMED |
| **0x00538450** | `__thiscall` Owner | 0 | the recruitment throttle: `Owner:660` offline, else 0x005381D0 | CONFIRMED |
| **0x005381D0** | `__thiscall` Owner | 0 | `clamp((turn − offset − Owner:716) × 100 / divisor, 0, 100)` | CONFIRMED |
| **0x004F2820** | `__thiscall` PP | 0 | **facility upkeep** = sum `def[+0x18] × count` | CONFIRMED |
| 0x00516330 | `__thiscall` traits | 0 | per-stationed-unit upkeep = `3 + CivTrait(32)` | CONFIRMED |
| **0x004F3100** | `__thiscall` PP | 3 (`ret 0xC`) | **(total space, free space, `Planet:108`)**; `def[+0x1C]` is a space footprint | CONFIRMED |
| **0x0053F390** | `__thiscall` Owner | 1 (`ret 4`) | **bankruptcy predicate**: `treasury + 10 × upkeep < 0`; out = turns to ruin | CONFIRMED |
| 0x0053C690 | `__thiscall` Owner | 8 | the total-upkeep aggregator / breakdown | CONFIRMED (identity only) |
| 0x004F4BF0 | `__thiscall` PP | 0 | food store clamp + starvation handling (`Planet:112` vs `Planet:116`) | CONFIRMED (identity) |

### Field meanings established or corrected

* **`def[+0x14]` = production build cost** (your live result; supersedes R5's "upkeep").
* **`def[+0x18]` = per-turn cash upkeep**, per facility instance.
* **`def[+0x1C]` = space footprint** (supersedes both R4's "cost vs treasury" and the
  current "required population" guess).
* **`Owner:660`** (`owner_primary + 0x2c8`) — the offline recruitment throttle percentage;
  initialised to 0 and never set non-zero by any code in the binary, but serialised.
* **`Owner:716`** (`owner_primary + 0x300`) — subtracted from the turn number in the ramp;
  **inferred** to be the turn the civ joined.
* **`PP+0x5c`** — the facility-map container wrapper whose tree root is `Planet:204`.
* Constants: `[0x00751898] = 100.0`, `[0x00750360] = 0.5`, turn counter `[0x008578E8]`,
  offline flag `[0x0086F1A0]`, ramp suppressors `[0x0086F45C]` / `[0x0086F4F8]`.

### What to do next, in priority order

1. **Read `[0x0086F1A0]`.** It decides whether the freeze explanation is the age ramp
   (branch B) or something still unexplained (branch A). Everything in 1.3 hinges on it and
   it is a single dword.
2. If branch B: evaluate `clamp((turn − 72 − Owner:716) × 100 / 100, 0, 100)` for the frozen
   civ and check it reads 100. If it does, recruitment is over for that galaxy and crew must
   come from conscription.
3. Read `def[+0x18]` for all 21 facility ids out of 0x00807DB0 and wire R-XPL-07 to it.
4. Decode `0x0053C690`'s eight out-params to get the upkeep breakdown, and find the
   seizure loop downstream of `0x0053F390`.
5. Trace the second `%d` at 0x0042B7B0 for the displayed maximum rate — cosmetic, since the
   engine does not clamp; the real limit is the signed byte, 0..127.
