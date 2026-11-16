"""
Score the simulator against the trait probes (sim/probes.py --traits).

    py -3.14 -m sim.trait_check [corpus]

A referee tick on these galaxies runs the engine two turns, so each pair is
ticked as many times as its turns differ, then compared field by field as
the oracle does. Prints each probe's misses by module and field, with an
example of each.
"""
import glob
import os
import sys

from . import load as sl
from . import oracle
from . import tick as tk


def score(corpus):
    out = {}
    for before in sorted(glob.glob(os.path.join(corpus, 'capture_traits_*.before.dat'))):
        name = os.path.basename(before)[len('capture_traits_'):-len('.before.dat')]
        g0 = sl.load_path(before)
        g1 = sl.load_path(before.replace('.before.dat', '.after.dat'))
        g = g0
        for _t in range(g1.turn - g0.turn):
            g = tk.tick(g)
        out[name] = oracle.compare(oracle.snapshot(g), oracle.snapshot(g1))
    return out


def main():
    corpus = sys.argv[1] if len(sys.argv) > 1 else os.path.join(sl.ROOT, 'sim', 'corpus')
    for name, modules in score(corpus).items():
        total = sum(r['fields'] for r in modules.values())
        miss = sum(r['fields'] - r['match'] for r in modules.values())
        print(f'{name}: {total - miss} of {total} fields match')
        for m, r in modules.items():
            for field, n in r['misses'].most_common():
                entity, got, want = r['examples'][field]
                print(f'    {m:12} {field:20} {n:3}  e.g. {entity}: sim {got!r}, engine {want!r}')


if __name__ == '__main__':
    main()
