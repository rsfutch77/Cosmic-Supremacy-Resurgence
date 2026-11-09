"""
The client as oracle: score the simulator's tick against the engine's.

A pair is a blob before one engine tick and the blob after it. The scorer loads
both, runs the simulator's tick on the first, and compares the result with the
second field by field, grouped by the module whose rules decide the field.

    python -m sim.oracle score <corpus>             the simulator against the engine
    python -m sim.oracle score <corpus> --null      what a tick changes at all
    python -m sim.oracle archive <tick dir> <corpus>
    python -m sim.oracle capture <corpus> <blob>... --run-game

A corpus is a directory of `<name>.before.dat` and `<name>.after.dat`, with a
`<name>.json` saying where the pair came from.

`archive` pairs consecutive referee inputs one turn apart. Players' merged
orders sit between the two, so the `orders` module and anything a trade or
reassignment moves are not the engine's alone in those pairs.

`capture` makes clean pairs: it stamps the released content version, ticks the
blob once through `referee.tick` and keeps both sides. It starts the shared
client, so it needs `--run-game`, `cs_server.py` on port 8888 and nobody else
using the game. A running packaged launcher also owns port 8888 and would take
the capture.
"""
import argparse
import collections
import glob
import json
import math
import os
import sys

from . import load as sl
from . import tick as st_tick

MODULES = ('economy', 'growth', 'research', 'market', 'movement',
           'colonisation', 'combat', 'orders')
FLOAT_TOLERANCE = 1e-3              # relative, for positions, progress, condition
MISSING = '<missing>'


def snapshot(g):
    """{(module, entity, field): value} for everything the tick may move."""
    out = {}

    def put(module, entity, field, value):
        out[(module, entity, field)] = value

    for i, held in enumerate(g.market or []):
        put('market', 'market', f'held {i}', held)
    for civ in g.civs.values():
        e = f'civ {civ.oid}'
        put('economy', e, 'credits', civ.credits)
        for i, r in enumerate(civ.resources or []):
            put('economy', e, f'resource {i}', r)
        put('research', e, 'completed', tuple(civ.completed or ()))
        put('orders', e, 'topic', civ.topic)
        for other, code in civ.stances.items():
            put('orders', e, f'stance {other}', code)
    for p in g.planets.values():
        e = f'planet {p.oid}'
        put('colonisation', e, 'owner', p.owner)
        if not p.owner and not p.citizens:
            continue
        put('growth', e, 'citizens', len(p.citizens))
        put('growth', e, 'military', len(p.military))
        put('growth', e, 'history', tuple(p.pop_history or ()))
        put('orders', e, 'jobs', tuple(sorted(p.jobs().items())))
        put('orders', e, 'recruit', p.recruit)
        put('economy', e, 'food', p.food)
        put('economy', e, 'facilities', tuple(sorted(p.facilities.items())))
        put('economy', e, 'lost space', p.lost_space)
        if p.production:
            put('orders', e, 'builds', (p.production.kind, p.production.item))
            put('economy', e, 'progress', p.production.progress)
    for s in g.ships.values():
        e = f'ship {s.oid}'
        put('combat', e, 'exists', True)
        put('combat', e, 'condition', s.condition)
        put('colonisation', e, 'owner', s.owner)
        put('growth', e, 'crew', len(s.crew))
        put('movement', e, 'pos', s.pos)
        put('movement', e, 'orbit', s.orbit)
        put('movement', e, 'order', s.order.kind if s.order else 0)
        put('movement', e, 'progress', s.order.progress if s.order else None)
        put('orders', e, 'fleet', s.fleet)
    for d in g.designs.values():
        put('orders', f'design {d.oid}', 'exists', True)
    return out


def _close(a, b):
    if isinstance(a, float) and isinstance(b, float):
        return math.isclose(a, b, rel_tol=FLOAT_TOLERANCE, abs_tol=FLOAT_TOLERANCE)
    if isinstance(a, tuple) and isinstance(b, tuple) and len(a) == len(b):
        return all(_close(x, y) for x, y in zip(a, b))
    return a == b


def compare(got, want):
    """{module: {'fields': n, 'match': n, 'misses': Counter(field: n),
    'examples': {field: (entity, got, want)}}}."""
    out = {m: {'fields': 0, 'match': 0, 'misses': collections.Counter(),
               'examples': {}} for m in MODULES}
    for key in set(got) | set(want):
        module, entity, field = key
        a, b = got.get(key, MISSING), want.get(key, MISSING)
        row = out[module]
        row['fields'] += 1
        if _close(a, b):
            row['match'] += 1
        else:
            row['misses'][field] += 1
            row['examples'].setdefault(field, (entity, a, b))
    return out


def merge(total, part):
    for m, row in part.items():
        t = total.setdefault(m, {'fields': 0, 'match': 0,
                                 'misses': collections.Counter(), 'examples': {}})
        t['fields'] += row['fields']
        t['match'] += row['match']
        t['misses'].update(row['misses'])
        for f, ex in row['examples'].items():
            t['examples'].setdefault(f, ex)
    return total


def pairs(corpus):
    """[(name, before path, after path, meta)] in name order."""
    out = []
    for before in sorted(glob.glob(os.path.join(corpus, '*.before.dat'))):
        name = os.path.basename(before)[:-len('.before.dat')]
        after = os.path.join(corpus, f'{name}.after.dat')
        meta_path = os.path.join(corpus, f'{name}.json')
        if not os.path.exists(after):
            continue
        meta = json.load(open(meta_path)) if os.path.exists(meta_path) else {}
        out.append((name, before, after, meta))
    return out


def score_pair(before, after, null=False):
    g0, g1 = sl.load_path(before), sl.load_path(after)
    got = g0 if null else st_tick.tick(g0)
    return compare(snapshot(got), snapshot(g1)), g0.gaps + g1.gaps


def score(corpus, null=False, log=print):
    total, gaps, n = {}, [], 0
    for name, before, after, _meta in pairs(corpus):
        part, g = score_pair(before, after, null)
        merge(total, part)
        gaps += [f'{name}: {x}' for x in g]
        n += 1
    log(f'{n} pairs, {"null tick" if null else "simulator tick"}')
    log(f'{"module":<14}{"fields":>8}{"match":>8}{"share":>8}  most missed')
    for m in MODULES:
        row = total.get(m)
        if not row or not row['fields']:
            continue
        share = row['match'] / row['fields']
        worst = ', '.join(f'{f} {c}' for f, c in row['misses'].most_common(4))
        log(f'{m:<14}{row["fields"]:>8}{row["match"]:>8}{share:>8.1%}  {worst}')
    for g in gaps[:10]:
        log(f'  gap: {g}')
    return total


def archive(tick_dir, corpus, log=print):
    """Pair consecutive referee inputs one turn apart from one galaxy."""
    import inject_civ as icv
    os.makedirs(corpus, exist_ok=True)
    rows = []
    for path in sorted(glob.glob(os.path.join(tick_dir, 'tick_*.dat'))):
        blob = sl.sp.load_any(path)
        civs = tuple(sorted(o['oid'] for o in icv.owner_records(blob)))
        rows.append((path, icv.turn_of(blob), civs, blob))
    made = 0
    for (p0, t0, c0, b0), (p1, t1, c1, b1) in zip(rows, rows[1:]):
        if t1 != t0 + 1 or c0 != c1:
            continue
        name = f'archive_t{t0:04d}_{os.path.basename(p0)[5:-4]}'
        open(os.path.join(corpus, f'{name}.before.dat'), 'wb').write(b0)
        open(os.path.join(corpus, f'{name}.after.dat'), 'wb').write(b1)
        json.dump({'source': 'referee archive', 'before': p0, 'after': p1,
                   'orders_between': True},
                  open(os.path.join(corpus, f'{name}.json'), 'w'), indent=1)
        made += 1
    log(f'{made} pairs from {len(rows)} referee inputs')
    return made


def capture(corpus, paths, log=print):
    """Tick each blob once on the client and keep both sides as a pair."""
    sys.path.insert(0, os.path.join(sl.ROOT, 'server'))
    import referee
    import set_blob_player as sbp
    os.makedirs(corpus, exist_ok=True)
    for path in paths:
        blob = sbp.set_content_version(sl.sp.load_any(path), log=log)
        name = f'capture_{os.path.splitext(os.path.basename(path))[0]}'
        after = referee.tick(blob, turns=1, log=log)
        open(os.path.join(corpus, f'{name}.before.dat'), 'wb').write(blob)
        open(os.path.join(corpus, f'{name}.after.dat'), 'wb').write(after)
        json.dump({'source': 'capture', 'from': path, 'orders_between': False},
                  open(os.path.join(corpus, f'{name}.json'), 'w'), indent=1)
        log(f'  {name}: turn {sl.icv.turn_of(blob)} -> {sl.icv.turn_of(after)}')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('score')
    s.add_argument('corpus')
    s.add_argument('--null', action='store_true',
                   help='compare the before side unchanged')
    a = sub.add_parser('archive')
    a.add_argument('tick_dir')
    a.add_argument('corpus')
    c = sub.add_parser('capture')
    c.add_argument('corpus')
    c.add_argument('blobs', nargs='+')
    c.add_argument('--run-game', action='store_true',
                   help='required: this starts the shared client')
    args = ap.parse_args(argv)
    if args.cmd == 'score':
        score(args.corpus, args.null)
    elif args.cmd == 'archive':
        archive(args.tick_dir, args.corpus)
    elif not args.run_game:
        raise SystemExit('capture starts the shared client; pass --run-game '
                         'once nobody else is using the game')
    else:
        capture(args.corpus, args.blobs)


if __name__ == '__main__':
    main()
