"""
alliance_acceptance.py: an alliance the merge accepts survives a real tick
==========================================================================
    python alliance_acceptance.py run
    python alliance_acceptance.py run --waronly 1

Starts from `server/tests/fixtures/diplomacy/d1.b64`, turn 180, where GoodGuy
has proposed an alliance (treaty 679) to BadGuy. BadGuy's submission accepts
it the way the client does, `merge_orders.merge` takes the step, and
`referee.tick` advances the result one turn in the machine's game client.

What counts as a failure:

    merge   the acceptance is refused, or the merged galaxy does not hold
            treaty 679 accepted for 100 turns from turn 180 with both
            stances at Alliance.
    tick    the turn does not advance, or treaty 679 or either stance is
            not as the merge left it.

`--waronly` writes that value into the galaxy's `GSET` before the tick, to
see what the engine does with an alliance under each value.

Requirements: `cs_server.py` on 127.0.0.1:8888 (one already there is used and
left alone) and the machine's game client, through `game_cycle`'s lock. Take
the client lease first: `lease.py take client <you> "<purpose>" --minutes 10`.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.dirname(HERE)
REPO = os.path.dirname(SERVER)
CLIENT_DEV = os.path.join(REPO, 'client', 'dev_tools')
for d in (HERE, SERVER, CLIENT_DEV, os.path.join(CLIENT_DEV, 'ai_player')):
    if d not in sys.path:
        sys.path.insert(0, d)

import save_parser as sp                                        # noqa: E402
import merge_orders as mo                                       # noqa: E402
import referee                                                  # noqa: E402
from join_turn_acceptance import ensure_server                  # noqa: E402

FIXTURE = os.path.join(SERVER, 'tests', 'fixtures', 'diplomacy', 'd1.b64')
WORK = os.path.join(SERVER, 'alliance_work')
GOOD, BAD = 656, 660
TREATY = 679
PASS, FAIL = [], []


def log(msg=''):
    print(msg, flush=True)


def check(name, got, want):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    log(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        log(f'          wanted {want!r}, got {got!r}')


def alliance(blob):
    """(status, length, start) of treaty 679 and both stances, or None."""
    record = (mo.treaties(blob) or {}).get(TREATY)
    t = mo.read_trdt(record) if record else None
    st = mo.stances(blob)
    return ((t['status'], t['length'], t['start']) if t else None,
            (st.get((GOOD, BAD)), st.get((BAD, GOOD))))


def accepted(served):
    """BadGuy's submission: treaty 679 accepted on the served turn."""
    turn = referee.turn_of(served)
    record = mo.treaties(served)[TREATY]
    return mo.set_treaties(served, [mo.with_trdt(record, mo.ACCEPTED,
                                                 mo.BINDS_FOR[4], turn)])


def run(waronly, save_dir):
    import game_cycle as gc

    served = sp.load_any(FIXTURE)
    turn = referee.turn_of(served)
    log(f'=== an alliance through the merge and a tick, waronly {waronly} ===')
    notes = {}
    merged = mo.merge(served, [('BadGuy', accepted(served))], log=lambda *_: None,
                      notes=notes)
    check('the acceptance is taken', notes.get('BadGuy'), None)
    want = ((mo.ACCEPTED, mo.BINDS_FOR[4], turn), (4, 4))
    check('merged: accepted for 100 turns, both stances Alliance',
          alliance(merged), want)
    merged = sp.set_gset(merged, {'waronly': waronly})
    check('the galaxy carries the waronly asked for',
          sp.gset_of(merged)['waronly']['value'], waronly)

    os.makedirs(WORK, exist_ok=True)
    proc = ensure_server()
    try:
        ticked = referee.tick(merged, turns=1, work_dir=WORK,
                              save_dir=save_dir, log=log)
    finally:
        try:
            gc.close_client()
        except Exception as exc:                            # noqa: BLE001
            log(f'  could not close the client, {exc}')
        gc.release_client_lock()
        if proc is not None:
            proc.terminate()
            log(f'  stopped the cs_server this run started (pid {proc.pid})')
    out = os.path.join(WORK, f'ticked_waronly{waronly}.dat')
    with open(out, 'wb') as fh:
        fh.write(ticked)
    log(f'  ticked galaxy: {out}')
    check('the turn advanced', referee.turn_of(ticked), turn + 1)
    check('ticked: the alliance and both stances as merged',
          alliance(ticked), want)
    check('ticked: the galaxy still carries the waronly asked for',
          sp.gset_of(ticked)['waronly']['value'], waronly)

    log(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('run')
    r.add_argument('--waronly', type=int, choices=(0, 1), default=0)
    r.add_argument('--save-dir', default=None,
                   help='where captures land, which is the data directory of '
                        'whoever is hosting cs_server')
    a = ap.parse_args()
    return run(a.waronly, a.save_dir)


if __name__ == '__main__':
    sys.exit(main())
