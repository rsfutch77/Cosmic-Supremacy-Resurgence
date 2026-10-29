"""
activity.py: a civ whose player took the turn is not inactive

    import activity

    blob = activity.mark_present(blob, ["DemoPlayer", "BadGuy"], log=log)

    python activity.py ../galaxies/sandbox/turns/0080.b64     # every civ's state

The food tooltip's "Inactivity: -N%" is the engine winding down an absent
player. It grows a point a turn from 72 turns after the civ's baseline, and the
baseline is refreshed only at a turn boundary that finds the civ's activity
flag set. The client never sets that flag, so a served galaxy decays every civ
from turn 73.

The flag and the baseline are saved in the civ's `OWNR > DATA > SERV`:

    SERV v5   u32 (Owner:652) ; u32 baseline (Owner:716) ; u8 flag (Owner:712)

The referee sets the flag for each civ whose submission it took. The engine
then writes the baseline itself, `turn - 1`, and clears the flag again, so a
civ that stops playing starts to decay 72 turns after its last turn.
"""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for _d in (HERE, os.path.join(HERE, 'dev_tools')):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import save_parser as sp

SERV_BASELINE = 4       # u32, the turn the civ was last seen active
SERV_FLAG = 8           # u8, 1 while the civ is active this turn
SERV_MIN_VERSION = 3    # the first version that saves the flag
OFFSET = 72             # turns of grace, with the galaxy option at its default
DIVISOR = 100           # turns from 0% to 100% after the grace


def _servs(blob: bytes, tree=None):
    """{civ name: SERV section} for every civ in the blob."""
    tree = tree or sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    out = {}
    for o in glxy.children:
        if o.tag != b'OWNR':
            continue
        n = struct.unpack_from('<I', blob, o.payload)[0]
        name = blob[o.payload + 4:o.payload + 4 + n].decode('latin-1')
        serv = next(o.find('SERV'), None)
        if serv is not None:
            out[name] = serv
    return out


def state(blob: bytes) -> dict:
    """{civ name: (baseline, flag)} as saved, for each civ with a SERV."""
    out = {}
    for name, serv in _servs(blob).items():
        if serv.version < SERV_MIN_VERSION or serv.size <= SERV_FLAG:
            continue
        out[name] = (struct.unpack_from('<I', blob, serv.payload + SERV_BASELINE)[0],
                     blob[serv.payload + SERV_FLAG])
    return out


def penalty(turn: int, baseline: int) -> int:
    """The tooltip's Inactivity percentage, with the galaxy option at its
    default."""
    return max(0, min(100, (turn - OFFSET - baseline) * 100 // DIVISOR))


def mark_present(blob: bytes, civs, log=print) -> bytes:
    """The blob with each named civ's activity flag set.

    A civ with no SERV, or one too old to carry the flag, is logged and left.
    Only the flag byte changes, so no section size moves.
    """
    servs = _servs(blob)
    out = bytearray(blob)
    for civ in civs:
        serv = servs.get(civ)
        if serv is None:
            log(f"  activity: {civ} has no SERV; not marked")
            continue
        if serv.version < SERV_MIN_VERSION or serv.size <= SERV_FLAG:
            log(f"  activity: {civ}'s SERV v{serv.version} has no flag; "
                f"not marked")
            continue
        out[serv.payload + SERV_FLAG] = 1
    return bytes(out)


def main():
    import turn_store
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('blob', help='a .b64 or .dat galaxy')
    a = ap.parse_args()
    blob = sp.load_any(a.blob)
    turn = turn_store.turn_of(blob)
    print(f"turn {turn}")
    print(f"  {'civ':16s} {'baseline':>9s} {'flag':>5s} {'inactivity':>11s}")
    for name, (baseline, flag) in state(blob).items():
        print(f"  {name:16s} {baseline:9d} {flag:5d} "
              f"{penalty(turn, baseline):10d}%")


if __name__ == '__main__':
    main()
