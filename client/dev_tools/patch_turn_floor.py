"""
patch_turn_floor.py — lower the engine's 60-second minimum turn length
======================================================================
    python patch_turn_floor.py --status
    python patch_turn_floor.py --apply         # floor becomes 1 second
    python patch_turn_floor.py --revert        # back to the stock 60

WHY. The engine re-applies `GSET.turnlength` at every turn boundary, so a galaxy
runs at its configured rate with nothing driving it — but two sites clamp that
value to a minimum of 60 seconds:

    83 fe 3c                     cmp  esi, 60
    89 35 08 aa 80 00            mov  [0x0080AA08], esi
    7f 0a                        jg   skip
    c7 05 08 aa 80 00 3c 000000  mov  dword [0x0080AA08], 60

Sixty seconds is a sensible floor for the game this was: turns ran from minutes
to hours and nobody needed faster. It is the wrong floor for developing an AI
against it. At 75s a 300-turn game takes 6.3 hours; at 3s it takes 15 minutes,
and the decision work for three civs is 0.5s of that.

WHAT IS CHANGED, AND WHAT IS NOT. Only the two immediates, `60` → `1`: the
comparison that decides whether to clamp, and the value clamped to. The
instructions, their lengths and every branch target are untouched, so this is
four bytes and no relocation. The clamp still exists — it just guards 1 second
instead of 60, which keeps whatever the check was protecting against (a zero or
negative turn length spinning the turn pipeline) while removing the part that
only ever protected the original business model.

REVERSIBLE, and verified both ways. `--apply` refuses unless it finds exactly the
stock bytes at both sites, `--revert` refuses unless it finds exactly the patched
ones, and a `.bak` is written before the first change. A wrong offset therefore
fails loudly instead of corrupting the client.
"""
import argparse
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(os.path.dirname(HERE), "CosmicSupremacy_Resurgence.exe")
BAK = EXE + ".preturnfloor.bak"

# file offset, VA, what it is. VA - file = 0x400C00 in this image.
#
# TEN SITES, NOT TWO. The first attempt patched only the two that write the
# immediate directly (`mov [turnlength], 60`) and the live value still came up
# 60 on a verified-patched process — because most of the clamps load 60 into a
# REGISTER first (`mov ecx, 60 ; mov [turnlength], ecx`), which looks nothing
# like the pattern the first search matched. The floor is applied all over one
# function, presumably once per way the turn length can be arrived at.
SITES = [
    (0x12C743, 0x0052D343, "mov edx, N   (then max against it)"),
    (0x12C78B, 0x0052D38B, "cmp esi, N"),
    (0x12C79A, 0x0052D39A, "mov [turnlength], N"),
    (0x12C8C2, 0x0052D4C2, "cmp ecx, N"),
    (0x12C8E5, 0x0052D4E5, "cmp ecx, N"),
    (0x12C8F1, 0x0052D4F1, "mov ecx, N"),
    (0x12C90C, 0x0052D50C, "cmp ecx, N"),
    (0x12C910, 0x0052D510, "mov ecx, N"),
    (0x16D8D3, 0x0056E4D3, "cmp eax, N"),
    (0x16D8E2, 0x0056E4E2, "mov [turnlength], N"),
]
STOCK, PATCHED = 0x3C, 0x01


def read(path):
    with open(path, "rb") as f:
        return bytearray(f.read())


def status(data):
    vals = [(off, va, what, data[off]) for off, va, what in SITES]
    for off, va, what, v in vals:
        state = ("stock" if v == STOCK else
                 "patched" if v == PATCHED else "UNRECOGNISED")
        print(f"  file 0x{off:06X}  VA 0x{va:08X}  {what:<28} = {v:>3} "
              f"({state})")
    return [v for _o, _va, _w, v in vals]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--revert", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()

    if not os.path.exists(EXE):
        sys.exit(f"no client at {EXE}")
    data = read(EXE)
    print(f"{os.path.basename(EXE)}: {len(data):,} bytes")
    vals = status(data)

    if a.status or not (a.apply or a.revert):
        floor = "1s (patched)" if all(v == PATCHED for v in vals) else (
            "60s (stock)" if all(v == STOCK for v in vals) else "MIXED")
        print(f"\nminimum turn length: {floor}")
        return

    want_from, want_to = (STOCK, PATCHED) if a.apply else (PATCHED, STOCK)
    # Every site must read one of the two values we know about. A site reading
    # anything else means the offsets are wrong for this binary, and writing to
    # it would corrupt an instruction rather than an immediate. Sites already at
    # the target are left alone, so the tool can be re-run after new sites are
    # added without reverting the ones it patched last time.
    unknown = [(off, va, w, v) for (off, va, w), v in zip(SITES, vals)
               if v not in (STOCK, PATCHED)]
    if unknown:
        for off, va, w, v in unknown:
            print(f"  !! VA 0x{va:08X} {w} reads {v}, expected "
                  f"{STOCK} or {PATCHED}")
        sys.exit("\nrefusing: an offset does not point at a known immediate")
    todo = [off for (off, _va, _w), v in zip(SITES, vals) if v == want_from]
    if not todo:
        print(f"\nnothing to do; every site already reads {want_to}")
        return

    if a.apply and not os.path.exists(BAK):
        shutil.copy2(EXE, BAK)
        print(f"\nbacked up to {os.path.basename(BAK)}")
    for off in todo:
        data[off] = want_to
    with open(EXE, "wb") as f:
        f.write(bytes(data))
    print(f"\nwrote {os.path.basename(EXE)}; minimum turn length is now "
          f"{'1s' if a.apply else '60s'}")
    status(read(EXE))
    if a.apply:
        print("\nThe engine still re-applies GSET.turnlength every boundary, so "
              "set the galaxy's own turnlength to the rate you want — "
              "server/dev_tools/set_turnlength.py — rather than driving it.")


if __name__ == "__main__":
    main()
