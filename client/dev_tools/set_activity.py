"""
set_activity.py — stop an offline galaxy decaying from "inactivity"
===================================================================
    python set_activity.py            # show every civ's state
    python set_activity.py --apply    # mark EVERY civ active

WHAT THE GAME DOES
------------------
A galaxy applies an escalating "Inactivity" penalty, visible in the planet Food
Calculation tooltip as `Inactivity: -N%`. It comes from `0x00538450`:

    pct = clamp( (currentTurn - offset - Owner:716) * 100 / divisor, 0, 100 )
          offset/divisor = 72/100 when GetGameOption(4)->[+0x30] == 0, else 150/150

and it is applied TWICE: at half weight to food production (inside `0x004F0800`),
and at FULL weight to the military share of the food surplus (in the splitter
`0x004F1580`) — after the citizens' share has already been computed from the
UNREDUCED military share. So the diverted food is not redistributed, it is
destroyed, and recruitment dies while the food bar still looks healthy.

With `Owner:716 = 0` the penalty is simply `turn - 72`: first visible around turn
73, and 100% at turn 172, after which NO military can be recruited at any slider
setting. Crew gates every order rule the AI has, so an offline galaxy quietly
suffocates a little after turn 170.

WHY THIS WRITES A FLAG AND NOT THE TIMER
----------------------------------------
The engine refreshes the baseline itself, in `0x00543B90` (turn stage 26):

    cmp  byte ptr [esi + 0x2fc], bl    ; Owner:712 — the ACTIVITY FLAG
    je   skip                          ;   zero -> no refresh
    mov  eax, [0x008578E8]             ; current turn
    dec  eax
    mov  [esi + 0x300], eax            ; Owner:716 = turn - 1
  skip:
    mov  cl, byte ptr [esi + 0x304]    ; Owner:720 — next turn's flag
    mov  byte ptr [esi + 0x2fc], cl    ; Owner:712 = Owner:720

So `Owner:720` is the "this player is present" signal and `Owner:712` is it
latched for the current turn. Setting `Owner:720 = 1` is therefore SELF-
SUSTAINING: each turn the engine copies it into `Owner:712`, sees the flag next
turn, and writes its own value into `Owner:716`. It takes two turn boundaries to
take effect.

That is the honest way to do this. Writing `Owner:716` directly would forge the
engine's own bookkeeping with a number we invented; writing the flag supplies the
input a connected client would supply and lets the engine compute the rest.

FAIRNESS
--------
This marks EVERY civ, not ours. The decay models an ABSENT player, and in an
AI-vs-AI galaxy nobody is absent — but applying it only to our own civ would be a
plain cheat, handing us a food and recruitment advantage that compounds every
turn. `--apply` refuses to do one civ.

This is a galaxy-level setup action, not an AI capability, which is why it lives
in dev_tools and not in ai_player: it simulates the server's presence tracking for
a mode the game was not built for.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "ai_player"))
import gamestate as gs

ACTIVE_FLAG = 712      # Owner:712 — latched for this turn
PENDING_FLAG = 720     # Owner:720 — the signal we set
BASELINE = 716         # Owner:716 — the engine's own timer, DO NOT WRITE
DEFAULT_OFFSET = 72    # GetGameOption(4)->[+0x30] == 0
DEFAULT_DIVISOR = 100


def pct_for(turn, baseline, offset=DEFAULT_OFFSET, divisor=DEFAULT_DIVISOR):
    return max(0, min(100, (turn - offset - baseline) * 100 // divisor))


def report(snap):
    print(f"turn {snap.turn}")
    print(f"  {'civ':12s} {'active':>7s} {'pending':>8s} {'baseline':>9s} "
          f"{'inactivity':>11s}")
    for c in snap.civs:
        a = snap.read(c.addr + ACTIVE_FLAG, 1)[0]
        p = snap.read(c.addr + PENDING_FLAG, 1)[0]
        b = c.i32(BASELINE)
        print(f"  {c.civ_name!r:12s} {a:7d} {p:8d} {b:9d} "
              f"{pct_for(snap.turn, b):10d}%")


def apply(snap):
    """Set the pending activity flag on EVERY civ."""
    done = []
    for c in snap.civs:
        addr = c.addr + PENDING_FLAG
        before = snap.read(addr, 1)[0]
        if before == 1:
            print(f"  {c.civ_name!r}: already flagged active")
            done.append(c)
            continue
        if not gs.ev.write_bytes(snap.h, addr, b"\x01"):
            print(f"  {c.civ_name!r}: WRITE FAILED at 0x{addr:08X}")
            continue
        print(f"  {c.civ_name!r}: Owner:720 {before} -> 1 (0x{addr:08X})")
        done.append(c)
    if len(done) != len(snap.civs):
        print("\n  [!] NOT every civ was marked. Leaving it partial would hand "
              "whoever got the flag a compounding food advantage — fix the "
              "failures and re-run before playing on.")
    else:
        print(f"\n  all {len(done)} civ(s) marked active; the engine refreshes "
              f"Owner:716 itself after two turn boundaries")


def main():
    snap = gs.Snapshot()
    report(snap)
    if "--apply" not in sys.argv:
        print("\nre-run with --apply to mark every civ active")
        return
    print()
    apply(snap)


if __name__ == "__main__":
    main()
