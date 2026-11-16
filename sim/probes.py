"""
Doctrine probes: engine ticks in which civs take doctrines one a tick, so the
oracle can check each doctrine's trait grants against what the engine did.

    python -m sim.probes sim/corpus --run-game

Each probe starts from a corpus blob and gives each listed civ a path of
doctrines, one per tier. Before every tick a civ's topic and goal are set to
the next doctrine on its path and its research store to 5,000,000, so the
doctrine is paid for that tick. After the paths run out, `TAIL` more ticks
show the effects settle. The pairs land in the corpus like any capture.

The eight paths take every one of the 23 doctrines at least once.

    python -m sim.probes sim/corpus --traits --run-game

The trait probes test what a quiet galaxy does not: the staged battle, invasion
and rollout captures, ticked with doctrines written in as already completed,
prerequisites and all, so that the battle or build of the first tick meets
their firepower, hitpoints, shields, units, cloaking, ground strength and cost
traits. The pairs land as capture_traits_<probe>_t<turn>.
"""
import argparse
import json
import os
import struct
import sys
import time

from . import load as sl

STORE = 5_000_000
TAIL = 4
PROBES = {                              # name: (corpus blob, {civ: doctrine path})
    'beta': ('capture_serve_Beta_t0083.before.dat', {
        238: [45, 49, 55, 59, 62, 66], 210: [44, 48, 56, 60, 63, 65],
        195: [46, 49, 56, 60, 64, 68], 202: [47, 50, 61, 67]}),
    'q7': ('capture_tier5_q7_t0112.before.dat', {
        656: [43, 47, 54, 60, 64, 68], 660: [43, 47, 50, 57, 63]}),
    'single': ('capture_SinglePlayerGalaxy_t0010.before.dat', {
        191: [43, 48, 54, 58, 62], 195: [46, 49, 55, 59, 63]}),
}


TRAIT_PROBES = {                        # name: (corpus blob, {civ: doctrines completed}, ticks)
    'dreadnought': ('capture_battle_heavy_t0123.before.dat', {656: [43, 47, 54, 60, 64]}, 2),
    'stealth': ('capture_battle_heavy_t0123.before.dat', {656: [43, 47, 50, 57, 63]}, 2),
    'lightweight': ('capture_battle_heavy_t0123.before.dat', {660: [44, 48, 56, 60, 63, 65]}, 2),
    'invasion': ('capture_live_invade_t0125.before.dat', {656: [44, 48, 56], 660: [43, 47, 54]}, 3),
    'costs': ('capture_rollout_q7_t0113.before.dat', {656: [44, 48, 58], 660: [43, 47, 50]}, 2),
}


def grant(blob, civ, techs):
    """The civ's completed research with `techs` added, each record the
    technology and what it cost, as research writes them."""
    from . import research as rs
    mo, icv = sl.mo, sl.icv
    done = list(mo.completed_research(blob, civ) or [])
    owpr = mo._owpr(blob, civ)
    at = owpr.payload + mo.OWPR_MAP_COUNT_OFF
    m = struct.unpack_from('<I', blob, at)[0]
    n_at = at + 4 + m * mo.OWPR_RECORD
    n = struct.unpack_from('<I', blob, n_at)[0]
    records = b''
    for t in techs:
        if t in done:
            continue
        records += struct.pack('<II', t, rs.cost(t, done))
        done.append(t)
    end = n_at + 4 + n * mo.OWPR_RECORD
    blob = icv.splice(blob, end, end, records)
    out = bytearray(blob)
    struct.pack_into('<I', out, n_at, n + len(records) // mo.OWPR_RECORD)
    return bytes(out)


def run_traits(corpus, names, log=print, progress=print):
    sys.path.insert(0, os.path.join(sl.ROOT, 'server'))
    import referee
    import set_blob_player as sbp
    quiet = lambda *_a: None                                    # noqa: E731
    for name in names:
        source, grants, ticks = TRAIT_PROBES[name]
        blob = sbp.set_content_version(open(os.path.join(corpus, source), 'rb').read(), log=quiet)
        for civ, techs in grants.items():
            blob = grant(blob, civ, techs)
        for _step in range(ticks):
            turn = sl.icv.turn_of(blob)
            stem = f'capture_traits_{name}_t{turn:04d}'
            started = time.time()
            try:
                after = referee.tick(blob, turns=1, log=log)
            except (Exception, SystemExit) as exc:              # noqa: BLE001
                progress(f'FAILED {stem}: {exc}')
                break
            open(os.path.join(corpus, stem + '.before.dat'), 'wb').write(blob)
            open(os.path.join(corpus, stem + '.after.dat'), 'wb').write(after)
            json.dump({'source': 'trait probe', 'from': source, 'orders_between': False,
                       'completed': {str(c): t for c, t in grants.items()}},
                      open(os.path.join(corpus, stem + '.json'), 'w'), indent=1)
            progress(f'captured {stem} in {time.time() - started:.0f}s')
            blob = after
    progress('DONE')


def stage(blob, civ, topic):
    mo, icv = sl.mo, sl.icv
    rec = next(r for r in icv.owner_records(blob) if r['oid'] == civ)
    out = bytearray(blob)
    struct.pack_into('<I', out, rec['id_at'] + mo.CREDITS_AFTER_ID + sl.RESEARCH_AFTER_CREDITS, STORE)
    word = struct.pack('<i', topic)
    return mo.set_research(bytes(out), civ, (word, word))


def run(corpus, names, log=print, progress=print):
    sys.path.insert(0, os.path.join(sl.ROOT, 'server'))
    import referee
    import set_blob_player as sbp
    quiet = lambda *_a: None                                    # noqa: E731
    for name in names:
        source, paths = PROBES[name]
        blob = sbp.set_content_version(open(os.path.join(corpus, source), 'rb').read(), log=quiet)
        steps = max(len(p) for p in paths.values()) + TAIL
        for step in range(steps):
            for civ, path in paths.items():
                if step < len(path):
                    blob = stage(blob, civ, path[step])
            turn = sl.icv.turn_of(blob)
            stem = f'capture_doctrine_{name}_t{turn:04d}'
            started = time.time()
            try:
                after = referee.tick(blob, turns=1, log=log)
            except (Exception, SystemExit) as exc:              # noqa: BLE001
                progress(f'FAILED {stem}: {exc}')
                break
            open(os.path.join(corpus, stem + '.before.dat'), 'wb').write(blob)
            open(os.path.join(corpus, stem + '.after.dat'), 'wb').write(after)
            json.dump({'source': 'doctrine probe', 'from': source, 'step': step,
                       'orders_between': False,
                       'doctrines': {str(c): p[step] for c, p in paths.items() if step < len(p)}},
                      open(os.path.join(corpus, stem + '.json'), 'w'), indent=1)
            progress(f'captured {stem} in {time.time() - started:.0f}s')
            blob = after
    progress('DONE')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('corpus')
    ap.add_argument('--only', nargs='*')
    ap.add_argument('--traits', action='store_true', help='the trait probes, not the doctrine paths')
    ap.add_argument('--log')
    ap.add_argument('--run-game', action='store_true',
                    help='required: this starts the shared client')
    args = ap.parse_args(argv)
    if not args.run_game:
        raise SystemExit('the probes start the shared client; pass --run-game once nobody '
                         'else is using the game')
    sink = open(args.log, 'a', encoding='utf-8') if args.log else None
    log = (lambda *a: print(*a, file=sink, flush=True)) if sink else print
    if args.traits:
        run_traits(args.corpus, args.only or list(TRAIT_PROBES), log=log,
                   progress=lambda m: print(m, flush=True))
    else:
        run(args.corpus, args.only or list(PROBES), log=log, progress=lambda m: print(m, flush=True))


if __name__ == '__main__':
    main()
