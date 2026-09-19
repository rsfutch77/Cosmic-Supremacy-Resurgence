"""
fog_bisect.py , how much galaxy can be removed before a client stops loading
============================================================================
    python fog_bisect.py server/fog_work/fix80.dat --civ Alice

`project_blob.py` produces a blob the client will not open: Alice's view, one
system kept, never becomes readable. Dropping a single system is fine. This
finds where between the two it stops working, because "it does not load" is not
yet a cause and guessing at one wasted three launches already.

Binary search on the number of systems dropped, always dropping from the same
end of the same ordered list so that a failing count is reproducible rather
than depending on which systems the search happened to pick.

`launch` is given 45 seconds rather than its usual 180. A successful load here
takes about 15, so 45 is ample, and a failure costs a quarter of what it did.
That matters because the search spends most of its time on failures.
"""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'), HERE,
          os.path.join(ROOT, 'client', 'dev_tools'),
          os.path.join(ROOT, 'client', 'dev_tools', 'ai_player')):
    if d not in sys.path:
        sys.path.insert(0, d)

import save_parser as sp
import set_blob_player
import project_blob as pb
import game_cycle as gc

WORK = os.path.join(ROOT, 'server', 'fog_work')


def drop_n(blob, order, n):
    """Drop the first `n` system ids of `order`, correcting the object count."""
    tree = sp.parse_blob(blob)
    save = tree[0]
    glxy = next(save.find('GLXY'))
    objects, = struct.unpack_from('<I', blob, save.payload)
    systems = pb._systems(blob, tree)
    victims = [systems[i] for i in order[:n]]
    removed = sum(1 + len(list(s.find('PLNT'))) for s in victims)
    starts = {s.start for s in victims}

    out = bytearray()
    pos = glxy.payload
    for c in glxy.children:
        if c.start not in starts:
            continue
        out += blob[pos:c.start]
        pos = c.end
    out += blob[pos:glxy.end]

    nb = sp.replace_payload(blob, tree, glxy, bytes(out))
    nb = bytearray(nb)
    struct.pack_into('<I', nb, save.payload, objects - removed)
    return bytes(nb)


def loads(blob, civ, timeout=45):
    """Does a client open this blob?"""
    stamped = set_blob_player.set_player(blob, civ, log=lambda *a: None)
    dat = os.path.join(WORK, 'bisect.dat')
    with open(dat, 'wb') as f:
        f.write(stamped)
    gc.close_client()
    try:
        snap = gc.launch(dat, timeout=timeout, exe='player',
                         purpose='fog bisect')
        ok = snap.local_civ() is not None
    except SystemExit:
        ok = False
    finally:
        gc.close_client()
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('file')
    ap.add_argument('--civ', default='Alice')
    ap.add_argument('--timeout', type=int, default=45)
    a = ap.parse_args()

    blob = sp.load_any(a.file)
    owned, entered = pb.knowledge(blob, a.civ)
    systems = pb._systems(blob)
    # A stable order: everything this civ has no claim to, lowest id first.
    order = sorted(s for s in systems if s not in (owned | entered))
    print(f'{len(systems)} system(s); {a.civ} keeps {sorted(owned | entered)}; '
          f'{len(order)} droppable')

    tried = {}

    def test(n):
        if n in tried:
            return tried[n]
        out = drop_n(blob, order, n)
        ok = loads(out, a.civ, a.timeout)
        tried[n] = ok
        print(f'  drop {n:>2} -> {len(out):>6,} bytes  '
              f'{"LOADS" if ok else "fails"}')
        return ok

    lo, hi = 0, len(order)          # lo known good, hi assumed bad
    if test(hi):
        print(f'\nall {hi} can be dropped; nothing to bisect')
        return
    if not test(1):
        print('\neven one system cannot be dropped from this blob')
        return
    lo = 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if test(mid):
            lo = mid
        else:
            hi = mid
    print(f'\nlargest that loads: {lo}    smallest that fails: {hi}')
    print(f'the systems in the failing step: {order[lo:hi]}')


if __name__ == '__main__':
    main()
