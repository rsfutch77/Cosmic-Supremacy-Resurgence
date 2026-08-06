# Brief: static analysis, Round 8

Same ground rules as every round. Capstone over
`client/CosmicSupremacy_Resurgence.exe`, VA → file offset through the PE section table.
**Do not attach to, read, or launch any process.** Mark every claim CONFIRMED (you quote
the bytes) or inferred.

**Do not edit** `client/dev_tools/ai_player/`, `client/dev_tools/ejbo_annotations.json`,
or `CosmicSupremacy_Memory_Reconstruction_Report.md`. Write your results to
`client/dev_tools/findings_round8.md`.

---

## 0. A correction you need to make first, and the habit behind it

**Round 6 §1.3 inverted the two branches of `0x00538450`, and Round 7 inherited the
error.** Your own disassembly listing was correct — it was the prose label attached to it
that was backwards. From the bytes you yourself quoted:

```
00538450  803da0f1860000  cmp  byte ptr [0x0086F1A0], 0
00538457  7405            je   0x53845E        ; taken when the byte IS ZERO
00538459  e972fdffff      jmp  0x5381D0        ; fall-through: byte != 0  -> THE RAMP
0053845E  8b81c8020000    mov  eax, [ecx+0x2c8]  ; = Owner:660
00538464  c3              ret
```

`je` is jump-if-equal. The byte being **zero** takes the jump to the `Owner:660` return;
the byte being **non-zero** falls through into the game-age ramp. Round 6 asserted the
opposite mapping and then concluded:

> "on the offline branch the throttle is inert, `pct = 0`, and recruitment is never damped"

That conclusion is **false for this client, and it was the single most expensive wrong
claim of the project.** Live measurement: our client reads `pct = 38%` at turn 110 with
`Owner:716 = 0`, exactly `turn − 72`, which is the `0x005381D0` ramp with
`GameOption(4)->[+0x30] == 0`. The empire suffocated for ~40 turns while we looked
everywhere except the function you had already correctly disassembled.

Note what was and was not wrong, because the distinction is the point:

* The **formula** for the ramp was right, and it is what let us confirm the fix.
* The claim that **nothing writes `Owner:660` non-zero** was right.
* The claim that `[0x0086F1A0] != 0` means "offline" appears right — our client is offline
  and demonstrably takes the ramp.
* Only the **byte-value → branch** mapping was inverted, and it flipped the conclusion
  from "this throttles us to death" to "this is inert."

**The habit to change:** when a conclusion is load-bearing — "this mechanism is inert",
"this path is unreachable", "nothing writes this" — restate the conditional branch in
terms of the concrete flag value and the concrete formula reached, in one sentence, next
to the bytes. `je` after `cmp x, 0` goes to the target when `x == 0`. Half of these
inversions would be caught by writing that sentence out.

Also: **Round 7's open item 1 is moot.** The recruitment stall was this throttle, not a
partial-dword write clobbering the rate byte at `PP+0x0F`. The rate byte was reading
correctly the whole time. Do not spend Round 8 on it.

---

## 1. The activity triad — `Owner:712 / 716 / 720`

We have neutralised the decay and want the mechanism fully mapped, because it is the one
place the AI simulates something a server would normally supply.

What we already have, CONFIRMED live and by our own read of `0x00543B90` (turn stage 26):

```
00543CDE  cmp  byte ptr [esi+0x2FC], bl   ; Owner:712 — the latched activity flag
00543CE4  je   0x543CF2                   ;   zero -> no refresh
00543CE6  mov  eax, [0x008578E8]          ; current turn
00543CEB  dec  eax
00543CEC  mov  [esi+0x300], eax           ; Owner:716 = turn - 1
00543CF2  mov  cl, byte ptr [esi+0x304]   ; Owner:720 — next turn's flag
00543CF8  mov  byte ptr [esi+0x2FC], cl   ; Owner:712 = Owner:720
```

Setting `Owner:720 = 1` is therefore self-sustaining, and we do exactly that, on every
civ. Verified: inactivity went 38% → 0% across two turn boundaries and recruitment
resumed.

**(a) Who sets `Owner:720` in normal play?** Find every writer of `+0x304` on an `Owner`.
We expect a command handler — the thing that fires when a real client connects or submits
its turn. If it is a command handler, name the four-character command tag. This is the
claim we most want confirmed: it is what makes our write "the input a connected client
supplies" rather than a fabrication.

**(b) Every reader of `Owner:716` (`+0x300`).** We know `0x005381D0` reads it and
`0x00543B90` writes it. Anything else? Specifically, does it feed score, victory
conditions, or an auto-surrender/idle-kick path? If an absent player is eventually
*removed* rather than merely starved, we want to know before we run a long AI-vs-AI game.

**(c) Is the triad serialised?** Round 6 noted `Owner:660` travels in the save blob via
`0x0053C240`. Do `+0x2FC / +0x300 / +0x304`? If they do, we can set activity by editing a
save instead of patching a live process, which is strictly better — it survives
save/close/reload, which is now our standard cycle.

**(d) State `[0x0086F1A0]`'s polarity explicitly, with the evidence.** Which value is
offline, and what writes it. Round 6 used this global to anchor several conclusions and we
would like it nailed down rather than assumed.

---

## 2. Facility upkeep — where is it charged?

This is our longest-standing block: R-XPL-07 (sell buildings to survive an income
collapse) cannot decide *what* to sell without knowing what each facility actually costs
per turn.

We have the facility definition table (`facilities.py` reads it live: type id, name,
build cost, two strength fields, defence class). We do **not** have a per-turn upkeep.

* Find the turn-stage function that debits `Owner:8` (cash) for buildings, if one exists.
* Determine whether upkeep is a field on the facility definition, a function of the
  facility type, or folded into a net income figure computed elsewhere.
* If there is **no** per-facility upkeep and buildings only cost cash at build time, say
  so plainly — that is an equally useful answer and it kills the rule rather than
  blocking it.

Useful anchor: `0x004F8580` is the sell-facility engine call (we call it successfully and
it credits 75 cash for a military camp), so the facility→cash relationship is computed
somewhere near it.

---

## 3. `0x004F3100` — what does `CanBuildFacility` compare against?

We gate every facility build on the civ's unlocked-types vector (`Owner:172/176/180`)
after being caught building things we had not researched. That gate is ours, not the
engine's. `0x004F3100` looks like the engine's own legality check.

Decode its comparison: what set does it consult, and does it check anything beyond
research — a per-planet limit, a prerequisite facility, a terrain or space requirement?
If there is a per-planet cap on a type (e.g. one shipyard per planet) we want it, because
our build rule currently has no such concept.

---

## 4. Lower priority, only if 1–3 close early

* `0x004430F0`'s reputation forecast formula (Round 7 open item 3). Still open, still low
  value — nothing consumes the result. Take it only as filler.
* Which turn stage calls `0x004F4BF0` (Round 7 open item 2). Matters for when we sample
  `Planet:124`.

---

## What we would rather have

Three closed answers and an honest "I could not close this" on the fourth beats four
confident-sounding ones. The inverted branch above cost real time precisely because it
read as settled. If a claim rests on a branch you did not trace both ways, say so in the
line where you make it.
