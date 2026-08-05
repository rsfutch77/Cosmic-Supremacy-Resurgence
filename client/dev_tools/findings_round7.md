# Findings: static analysis, Round 7

Method as before: capstone over `client/CosmicSupremacy_Resurgence.exe`, VA -> file offset
through the PE section table. **No process was attached, read, or launched.** Every claim
is "the binary says", never "the game did".

* **CONFIRMED** — the instruction bytes say it, and I quote them.
* **inferred** — consistent with the bytes, not fully proven. Called out every time.

Prior rounds: `findings_production_path.md` (R1/R2), `findings_round3.md` ..
`findings_round6.md`. The live facts in the brief are taken as given.

**Two sub-questions I did not close** — Objective 1(a)'s exact formula and Objective 3(c)'s
cycle — are flagged in place rather than papered over. Everything else is settled.

---

## Objective 1 — the reputation cost of declaring war

### 1.1 (b) The complete list of things that move reputation: **one site**

`0x00534D50` (`AddReputation(Owner* other, short delta)`) has **exactly one caller in the
whole binary**. CONFIRMED by an exhaustive direct-call scan:

```
   call 0x004F7147   in func 0x004F6DE0
```

and that function has exactly one caller, which has exactly one caller:

```
stage 18  0x004CFD70   (post-combat resolution, R4/R5 stage table)
   -> 0x004DABD0       at 0x004D017E     ; walks a list, dynamic_cast<Planet*>
      -> 0x004F6DE0    at 0x004DAD1E     ; ecx = planet+0x60 (PlanetProperties)
         -> 0x00534D50 at 0x004F7147     ; lea ecx,[ebp+0x12c] = Owner:248
```

**Reputation is moved by planet conquest resolution, at turn stage 18. Nothing moves it on
declaration.** So the answer to the question behind the objective is:

> **Declaring war costs no reputation — not for us, and not for a human at the UI either.**
> There is no charge to reproduce.

The two strings in the declare-war validator are *forecasts* of what will happen **if you
subsequently attack**, which is exactly what their wording says ("You would gain ... for
successfully attacking", "you would lose ... for actually attacking"). They are shown at
declaration time and the delta is applied at attack time, in a different stage, by a
different code path.

Supporting detail on the writer, CONFIRMED at 0x004F7111-0x004F7147:

```
004f7111  66017e14  add word ptr [esi + 0x14], di      ; a running per-planet total
004f7115  0fb74614  movzx eax, word ptr [esi + 0x14]
004f7119  0fb74e12  movzx ecx, word ptr [esi + 0x12]
004f711d  663bc8    cmp cx, ax
004f7120  7d05      jge 0x4f7127                       ; clamp to [esi+0x12]
004f712e  66894614  mov word ptr [esi + 0x14], ax
004f7135  8b8424b0040000 mov eax, dword ptr [esp + 0x4b0]  ; the DELTA (an int argument)
004f713c  50        push eax
004f713d  51        push ecx
004f7141  8d8d2c010000   lea ecx, [ebp + 0x12c]        ; Owner:248
004f7147  e804dc0300     call 0x534d50
```

The delta is not a constant — it is an integer **argument** threaded down from
`0x004DABD0`, which reads it from its own caller (`[esp+0x20]` at 0x004DAD11). So the
magnitude is decided in stage 18 from the conquest outcome, not baked in.

### 1.2 (c) The change is applied to **one** side only

CONFIRMED: `0x004F7141` does a single `lea ecx, [ebp + 0x12c]` and a single call. There is
no second `Find`/store pair, unlike the relation-code applier `0x0055BD40` (R5 §1.3), which
demonstrably writes both civs' maps back to back. So reputation is **asymmetric** where the
relation code is symmetric.

Whose record it is — the attacker's opinion of the victim, or the victim's of the
attacker — I did not pin down; `ebp` is an `Owner` primary threaded in from stage 18 and I
did not trace which of the two it is. **inferred**, but the structural point you need is
firm: **write reputation on one side, not both.** (And per 1.4, you need not write it at
all.)

### 1.3 (a) The forecast formula — located, not closed

The two figures come from **`0x004430F0`**, called at `0x004BE1BA` from the declare-war
validator with four arguments, the last being an out-pointer (`lea ecx,[esp+0x1c]` at
0x004BE1AB). The validator then branches on the sign of that float to choose which string
to show:

```
004be224  d9ee      fldz
004be226  d944241c  fld dword ptr [esp + 0x1c]
004be22a  d8d1      fcom st(1)
004be231  753d      jne 0x4be270        ; > 0 -> the "would gain" string, else "would lose"
```

Inside `0x004430F0`, CONFIRMED context:

```
00443147  e884770e00  call 0x52a8d0      ; GetLocalOwner
00443155  e8261f0f00  call 0x535080      ; IsAtWarWith(target)
0044315e  6a1d ; call 0x4d4320           ; GetGameOption(29)
00443174  e8871f0f00  call 0x535100      ; a second relation predicate
00443188  cmp dword ptr [0x85795c], ebx
0044318e  je 0x443591                    ; nothing there -> out = 0.0  (early exit)
```

and the main store, with the arithmetic immediately before it:

```
00443441  d9442430  fld dword ptr [esp + 0x30]     ; a scaling factor V
00443445  d8d1      fcom st(1)                     ; vs 0
0044344e  7a45      jp 0x443495                    ; V <= 0 -> store [esp+0x2c] unscaled
0044345a  d84c242c  fmul dword ptr [esp + 0x2c]    ; V x R
0044346e  dd0598187500  fld qword ptr [0x751898]   ; 100.0
00443477  dcc9      fmul st(1), st(0)
00443485  dee1      fsubrp st(1)                   ; a (100 - x*100) term
...
0044349b  d944242c  fld dword ptr [esp + 0x2c]
004434a6  d918      fstp dword ptr [eax]           ; *out = the figure
```

**I did not close the exact expression.** It is a float built from a per-target scaling
factor and a `100 − x·100` term, aggregated over something walked from `[0x0085795C]` /
`0x00857940` (a global list, **inferred** to be the target's planets — the loop
`dynamic_cast`s and formats per entry). Closing it means tracing `[esp+0x2c]` and
`[esp+0x30]` back through that loop; it is a contained job but it was not the best use of
the round once 1.1 and 1.4 landed, because the answer stopped being load-bearing.

### 1.4 (d) **Nothing consumes reputation.** This is the answer that matters.

I swept for readers three ways and found no consumer in gameplay code:

1. **Every `movsx`/`mov` of a word at `+0x10`** across `.text`. Only three candidates sit
   outside library ranges, and all three are false positives:
   * `0x00534D6A` — inside `AddReputation` itself, for the clamp against 100.
   * `0x004CDB91` — not an instruction boundary (the real instruction at 0x004CDB90 is
     `jbe`); the surrounding code is a vector-span loop.
   * `0x0051DC30` — `mov cx, word ptr [esi+0x10]` in the **battle-report** builder
     `0x0051D950`, where `esi` is a `ShipProperties`-ish record (`[esi+0xc]+0x10` is fed to
     the `ShipDesign` getter `0x005473C0` two instructions earlier). Not a relation record.
2. **Every `word` write to `+0x10`.** The only one in gameplay code is `0x00534D7F`, inside
   the setter.
3. The relation-map rebuild `0x00535290` copies the record wholesale
   (`+0x00 .. +0x1E`, 31 bytes, CONFIRMED at 0x005353AE-0x005353E7) — a bulk copy, not a
   semantic read.

There is **no threshold test, no comparison, no branch anywhere on the reputation value**.
It is accumulated, clamped to 100, carried in the relation record, and displayed.

> **Conclusion: skipping the reputation charge costs nothing mechanically.** Nothing in the
> client's AI, diplomacy, or combat consults it. The value is a scoreboard, not an input.

The honest caveat: this is the *client*. Reputation lives in the relation record and R5
established that record travels in the `DATA`/`SERV`/`PRIV`/`CLIE` sections, so a **server**
could consult it in ways this binary cannot show me. If the fairness concern is about how a
server or other players perceive you rather than about mechanical advantage, that concern
survives. But the "unfair advantage" framing in your code can be downgraded: you are not
gaining a capability, and the human UI does not pay for a declaration either.

---

## Objective 2 — is any reputation consequence on a path we drive?

**No. Answered decisively by the call graph, not by sampling.**

Because `0x00534D50` has exactly one caller and that chain terminates in turn stage 18
(1.1), and because the only other write to the record's `+0x10` is inside the setter
itself (1.4), **no `0x0057xxxx` command handler can touch reputation, on any path.** There
is nothing for us to be quietly skipping. The sweep Round 4 §4.3 did for *validation* has
no analogue here for *reputation* — the surface does not exist.

For completeness, the fields we write and their reputation exposure:

| field we write | command handler bypassed | reputation on that path? |
|---|---|---|
| `Planet:144` job ids | `0x00574180` `CHCZ` | none |
| `Planet:284`/`296`/`344` production | `0x00573510` | none |
| `Planet:100` rate | `0x005744A0` `RTMI` | none |
| `Ship:48`/`52`/`76` orders | `0x004637F0` -> `0x00477D90` | none |
| `Owner:144`/`152` research | `0x00573A40` `STRS` | none |
| relations (`Owner:248`) | `0x0056F310` `NWTR` | none |
| facility sale / hurry | (per R6) | none |

**The real skipped consequence remains the one R5 §1.5 already named: news generation.**
`0x0056F310`'s offline commit calls `0x004FFFF0` to emit the news item; a direct relation
write emits nothing, so other players are not notified. That is the asymmetry worth
recording in your code, not reputation.

---

## Objective 3 — the recruitment stall

### 3.1 (a) Re-read of `0x004F1580`: **no additional zeroing condition exists**

I dumped the function in full — 154 instructions, `0x004F1580..0x004F171F`, `ret 8`. The
modern path (`0x0052A790` true) is `0x004F1592..0x004F1693` and contains, in order and in
its entirety:

1. citizen count (span of the `Planet:144` vector, else `Planet:348`);
2. `0x005163D0` per-citizen need, `imul` by the count -> demand;
3. `virtual[0x1C]` -> the food figure, `sub` -> surplus;
4. `js` on the surplus sign — the only conditional zero;
5. rate via `virtual[0x60]`, `imul`, divide by 100;
6. the two stores written (`[ebx] = military`, `[edx] = food`);
7. `0x00538450` throttle, subtracted from the military share only;
8. return.

**There is no early return, no second clamp, and no branch on anything other than the
surplus sign and the owner-null test.** Round 6's list of six conditions is complete for
the path this client takes. CONFIRMED. So the contradiction is in the *inputs*, not in the
formula — which is where 3(b) pays off.

### 3.2 (b) `0x004F0800` is not "food produced", and Round 6 mislabelled it

This is the substantive correction of the round. `0x004F0800` is `ret 4` — it takes an
**out-parameter** — and it returns a **net** figure after up to three separate percentage
reductions. CONFIRMED:

```
004f0806  8d462c        lea eax, [esi + 0x2c]        ; the citizen container (Planet:132)
004f080c  e87f480200    call 0x515090                ; ebx = count of citizens with job 0 (farmers)
004f0818  8b4234        mov eax, dword ptr [edx + 0x34]
004f081f  ffd0          call eax                     ; edi = per-farmer yield
004f0827  0faffb        imul edi, ebx                ; GROSS = farmers x yield
004f082a  85c0 ; 7402   test eax,eax ; je            ; if the out-ptr is non-null:
004f082e  8938          mov dword ptr [eax], edi     ;    *out = GROSS

; reduction 1 — a percentage, 100 on this content version
004f0830  e85b9f0300    call 0x52a790
004f0839  b864000000    mov eax, 0x64                ; version >= 150 -> 100 (no-op)
004f0840  8a460c        mov al, byte ptr [esi + 0xc] ; else Planet:100 BYTE 0, with a 0x5a test
004f084d  0fafc7        imul eax, edi
004f0858  dc3598187500  fdiv qword ptr [0x751898]    ; /100

; reduction 2 — virtual[0x5c], gated on content version >= 14
004f08a6  8b425c        mov eax, dword ptr [edx + 0x5c]
004f08ab  ffd0          call eax
004f08ad  da4c2414      fimul dword ptr [esp + 0x14]
004f08b1  dc3598187500  fdiv qword ptr [0x751898]
004f08df  2bf8          sub edi, eax

; reduction 3 — *** the SAME throttle as 0x004F1580 ***
004f08fd  e84e7b0400    call 0x538450                ; ecx = the Owner
004f0906  db44240c      fild dword ptr [esp + 0xc]
004f090a  dd0560037500  fld qword ptr [0x750360]     ; 0.5
004f0910  dcc9          fmul st(1), st(0)            ; pct x 0.5
004f091c  da4c2414      fimul dword ptr [esp + 0x14]
004f0920  dc3598187500  fdiv qword ptr [0x751898]
004f0942  2bf8          sub edi, eax                 ; net -= (pct/2)% of it
```

Two consequences, both important:

* **The throttle `0x00538450` reduces the FOOD figure too**, at half weight
  (`pct × 0.5 / 100`). So a non-zero throttle shrinks the surplus, which shrinks *both*
  shares. **A throttle can never produce "food rising, military flat"** — it moves them
  together. That independently confirms your live finding that the throttle is not the
  cause, from the other direction, and closes off Round 6's condition #4 for good rather
  than on the strength of one field read.
* `0x004F1580` calls this with a **null** out-parameter (`push 0` at 0x004F15E2, CONFIRMED),
  so the surplus is computed from the **net** figure. The gross number is only produced for
  other callers — which is where a UI figure and the formula's input can legitimately
  disagree.

### 3.3 New: recruitment lives in the **food** tick, not the economic tick

Round 6 found the accumulate (`add [esi+0x24]`) in `0x004FA490`. The **conversion** —
store to unit — is in a *different* per-planet function, `0x004F4BF0`, which Round 6 had
identified only as the food/starvation handler. CONFIRMED:

```
004f4dd0  e8bb590300  call 0x52a790                  ; content version >= 150 ?
004f4dd7  0f848c000000 je 0x4f4e69                   ;   no -> no recruitment at all
004f4ddd  8b4e24      mov ecx, dword ptr [esi + 0x24] ; Planet:124 store
004f4de0  3b4e28      cmp ecx, dword ptr [esi + 0x28] ; Planet:128 cost
004f4de3  7c36        jl 0x4f4e1b                     ; store < cost -> nothing
004f4de5  8d7e44      lea edi, [esi + 0x44]           ; the military container (Planet:156)
004f4df0: loop:
004f4df0    8b5628    mov edx, dword ptr [esi + 0x28]
004f4df5    295624    sub dword ptr [esi + 0x24], edx ; store -= cost      <-- the wrap
004f4dfd    ffd2      call [PP vftable + 0x10]        ; -> the Owner
004f4e00    6a03      push 3                          ; JOB ID 3 = military
004f4e06    e8c5e60100 call 0x5134d0                  ; build the 16-byte record
004f4e0e    e84d67fdff call 0x4cb560                  ; push_back into Planet:168
004f4e16    3b4628    cmp eax, dword ptr [esi + 0x28]
004f4e19    7dd5      jge 0x4f4df0                    ; while store >= cost
```

So the wrap is a **`while` loop that can mint several units in one turn**, and it is
unconditional given `store >= cost` — no space check, no garrison cap, no cost check. After
the loop it calls `0x004F21C0(first, last, count)` over the `Planet:168` span and, on a
flag, shows message `0x1A`. **No reset of the store anywhere.**

The operational point: **`Planet:124` is written by two different per-planet passes**, and
whether a sampled value looks "frozen" depends on which of them has run. If those two
passes sit in different turn stages, an external reader sampling between them sees a
different number than one sampling after both.

### 3.4 (c) The cycle is **not explained**, and here is what would settle it

I have to be straight with you: nothing I found this round explains a store that halts at
*exactly* 192/300, resumes, wraps, and halts at 192 again. The formula admits only two ways
to be zero on this content version (surplus <= 0, or `rate × surplus < 100`), neither is
value-dependent, and neither is periodic. A fixed point at 192 with cost 300 would require
the per-turn growth to be an exact multiple of 300, which with rate 40 needs surplus 750
and would imply a food share of 450/turn — an order of magnitude off your measured 164.

So one of the premises is wrong, and the two candidates are:

1. **The rate the formula reads is not the rate we wrote.** The getter is
   `0x00446B20: movsx eax, byte ptr [ecx+0xf]` — `PP+0x0F`, the top byte of `Planet:100`.
   If the effective value there were 0 while the UI showed 40, `eax` would be 0 and the
   food share would be the *entire* surplus — which is precisely the shape you measured
   (164 to food, 0 to military). **This is the hypothesis that fits your numbers without
   any additional mechanism**, and it predicts `surplus == 164` exactly.
2. **The 164 is not the food share.** `0x004F0800` writes a gross figure to an out-param
   and returns a net one (3.2); if the number being read as "food store delta" is actually
   sampled from a different quantity, the arithmetic never had to reconcile.

**The one measurement that discriminates**, taken in the same turn on a frozen planet:

* read the byte at `PP+0x0F` directly (i.e. `Planet:100 >> 24`) — **not** the UI's
  displayed rate;
* read `Planet:112` before and after the turn (the food-share delta);
* call the splitter's inputs: citizen count from the `Planet:144` span, and
  `0x004F0800`'s net return.

If `PP+0x0F` reads 0 while the UI shows 40, hypothesis 1 is confirmed and the bug is on the
*write* side — something is clearing or not landing in the top byte, and `Planet:100`'s
low three bytes (100/100/100 per R1) being adjacent makes a partial-dword write a very
plausible culprit. If it reads 40, then `surplus` must be < 3 for truncation to zero it,
and the +164 reading is measuring something else.

I would test hypothesis 1 first: it is one byte read, it needs no new code, and it is the
only explanation on the table that requires no undiscovered mechanism.

---

## 4. Summary table — Round 7

| address | conv | args | what it is | status |
|---|---|---|---|---|
| **0x00534D50** | `__thiscall` `Owner:248` | 2 (`ret 8`) | `AddReputation(other, delta)`; clamps `+0x10` to 100; **one caller in the binary** | CONFIRMED |
| 0x004F6DE0 | `__thiscall` PP | 2 (`ret 8`) | conquest resolution; the sole reputation writer | CONFIRMED |
| 0x004DABD0 | `__cdecl` | 1 | walks conquered planets; sole caller of the above | CONFIRMED |
| 0x004430F0 | `__cdecl` | 4 | computes the **forecast** reputation figure for the declare-war dialog | CONFIRMED (identity); formula open |
| **0x004F1580** | `__thiscall` PP | 2 (`ret 8`) | the surplus splitter; **full 154-instruction re-read: no extra zeroing condition** | CONFIRMED |
| **0x004F0800** | `__thiscall` PP | 1 (`ret 4`) | **not "food produced"**: returns a NET figure after 3 percentage reductions, writes GROSS to an out-param; applies the `0x00538450` throttle at half weight | CONFIRMED |
| **0x004F4BF0** | `__thiscall` PP | 0 | the food tick — **and it contains the recruitment wrap**: `while (store >= cost) { store -= cost; mint job-3 into Planet:168 }` | CONFIRMED |
| 0x00515090 | `__cdecl` | 2 | count citizens of a given job id in a container | CONFIRMED (role) |
| 0x005134D0 | `__thiscall` | 2 | build a 16-byte population record `{jobId, owner}` | CONFIRMED (role) |
| 0x004F21C0 | `__cdecl` | 3 | post-recruitment pass over the `Planet:168` span | CONFIRMED (identity) |

### Corrections to earlier rounds

* **R6 labelled `0x004F0800` "food produced (not decoded further)".** It is a net figure
  after three reductions, one of which is the same throttle applied to the military share.
  The label mattered because it is what made the surplus arithmetic look closed.
* **R6 attributed recruitment to the economic tick `0x004FA490`.** That function only
  *accumulates*; the store-to-unit conversion is in `0x004F4BF0`.

### Open, in priority order

1. **Read `PP+0x0F` directly on a frozen planet** (3.4, hypothesis 1). One byte; it either
   explains the stall completely or eliminates the only mechanism-free explanation.
2. Which turn stage calls `0x004F4BF0`. If it differs from stage 13 (which calls
   `0x004FA490`), the sampling order matters for every `Planet:124` reading we take.
3. Close `0x004430F0`'s formula — trace `[esp+0x2c]` and `[esp+0x30]` through the
   `[0x0085795C]` loop. Low value now that 1.4 shows nothing consumes the result.
4. Which side's record `0x004F6DE0` writes (1.2). Only matters if you decide to apply
   reputation voluntarily.
