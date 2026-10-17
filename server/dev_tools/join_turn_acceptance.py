"""
join_turn_acceptance.py: a join through the referee, in a client that ran
===========================================================================
    python join_turn_acceptance.py run
    python join_turn_acceptance.py run --name Ada --turns 2
    python join_turn_acceptance.py load --dat join_turn_work/played.dat

Checks that a joined galaxy closes turns and loads in a real client.
`server/tests/test_joins.py` covers the join pass's decisions with the engine
stubbed.

What this does and what counts as a failure:

    request   the join request is written by `release/launcher.py`'s own
              `send_join_request`, not by a shape this file made up. Failure:
              the launcher will not write to this store, or `joins.pending`
              cannot read what it wrote.
    close     `referee.resolve_turn` closes a real turn with a real client on
              a copy of a real galaxy. Failure: the tick raises, the turn does
              not advance, or the newcomer is not in the published blob and on
              the roster.
    measure   against a **before** read from the same store: the newcomer owns
              exactly one planet that was free, no incumbent's planets, ships
              or `OWNR` bytes moved, the `GLXY` civ count matches the `OWNR`
              records and the high-water id matches the largest id. Failure:
              any of them.
    play      the published galaxy is closed again, by the referee, with the
              newcomer on the roster. Failure: the second tick does not
              advance.
    load      the published blob, stamped for the civ that joined, opens in a
              client that was not already holding it. Failure: the client
              exits during the load, or the galaxy never becomes readable.

Requirements, both checked late: `cs_server.py` listening on 127.0.0.1:8888
(the capture arrives over its `savegame` endpoint), and the machine's one game
client, taken through `game_cycle`'s lock.

The store under test is a copy made per run; `server/uidemo` and
`server/galaxy_demo` are only read.
"""
import argparse
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.dirname(HERE)
REPO = os.path.dirname(SERVER)
CLIENT_DEV = os.path.join(REPO, 'client', 'dev_tools')
for d in (HERE, SERVER, CLIENT_DEV, os.path.join(CLIENT_DEV, 'ai_player'),
          os.path.join(REPO, 'release')):
    if d not in sys.path:
        sys.path.insert(0, d)

import save_parser as sp
import inject_civ as icv
import inject_design as idg
import inject_ship as ish
import set_blob_player
import joins
import referee
import turn_store

WORK = os.path.join(SERVER, 'join_turn_work')
FIXTURE = os.path.join(SERVER, 'galaxy_demo')
PASS, FAIL = [], []


def log(msg=''):
    print(msg, flush=True)


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    log(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        log(f'          wanted {want!r}, got {got!r}')


def civ_count(blob):
    return struct.unpack_from('<I', blob, icv.civ_count_at(blob))[0]


def high_water(blob):
    return struct.unpack_from('<I', blob, idg.HIGH_WATER_ID)[0]


def holdings(blob):
    """{civ: (planet ids, ship ids, OWNR bytes)}, the three a join could move."""
    out = {}
    for o in icv.owner_records(blob):
        out[o['name']] = (
            sorted(p['id'] for p in icv.planet_records(blob)
                   if p['owner'] == o['oid']),
            sorted(s['id'] for s in ish.ship_records(blob)
                   if s['owner'] == o['oid']),
            bytes(blob[o['payload']:o['end']]))
    return out


def free_planets(blob):
    return [p['id'] for p in icv.planet_records(blob)
            if not p['owner'] and p['nlen'] == 0]


def server_listening(host='127.0.0.1', port=8888, timeout=2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def ensure_server():
    """(process or None). A server already on 8888 is used and never killed.

    A packaged launcher's server writes captures to its own data directory;
    pass `--save-dir` to say where they land.
    """
    if server_listening():
        log('  a server is already on 8888; using it and leaving it alone')
        return None
    exe = os.path.join(SERVER, 'cs_server.py')
    proc = subprocess.Popen([sys.executable, exe], cwd=SERVER,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess,
                                                  'CREATE_NO_WINDOW', 0))
    for _ in range(40):
        if server_listening():
            log(f'  started cs_server.py on 8888 (pid {proc.pid})')
            return proc
        time.sleep(0.5)
    proc.terminate()
    raise SystemExit('cs_server.py did not come up on 8888')


def prepare(fixture, work, name, uid, seconds):
    """A private copy of a real galaxy with one join request waiting in it."""
    if os.path.exists(work):
        shutil.rmtree(work)
    shutil.copytree(fixture, work)
    store = turn_store.TurnStore(work)
    # Deadline in the past, so the referee closes on demand.
    store.update_state({'deadline': time.time() - 1, 'turn_seconds': seconds})
    state = store.state()
    log(f'  copy of {os.path.basename(fixture)} at turn {state["turn"]}, '
        f'roster {state["civs"]}')

    import launcher
    req = launcher.join_request(name, uid, launcher.build_id(),
                                turn=state['turn'])
    where = launcher.send_join_request(store, req)
    log(f'  the launcher wrote the request to {where}')
    waiting = joins.pending(store, log=log)
    check('the launcher\'s request is one this reads',
          [r['name'] for r in waiting], [name])
    return store


def measure(before, after, control, name):
    """What the join did, against the turn before it and against a control.

    Against `before`, only planet and ship ownership is compared, since a tick
    changes every `OWNR`. `control` is the same turn closed with no join
    request; the engine's turn is deterministic, so `OWNR` bytes must match it.
    """
    was, now = holdings(before), holdings(after)
    ctl = holdings(control)
    check('the newcomer is in the published blob', name in now)
    if name in now:
        check('owning exactly one planet', len(now[name][0]), 1)
        check('and it was a free planet before',
              now[name][0][0] in free_planets(before))
    check('the control run seated nobody', name in ctl, False)
    for civ in was:
        check(f'{civ} kept exactly its planets', now[civ][0], was[civ][0])
        check(f'{civ} kept exactly its ships', now[civ][1], was[civ][1])
        check(f'{civ} holds the same planets as in the control run',
              now[civ][0], ctl[civ][0])
        check(f'{civ} holds the same ships as in the control run',
              now[civ][1], ctl[civ][1])
        check(f'{civ}\'s OWNR is byte-identical to the control run',
              now[civ][2], ctl[civ][2])
    check('the GLXY civ count matches the OWNR records',
          civ_count(after), len(icv.owner_records(after)))
    check('the high-water id matches the largest object id',
          high_water(after), idg.max_object_id(after))
    check('the galaxy grew by exactly one civ over the turn it closed',
          len(icv.owner_records(after)) - len(icv.owner_records(before)), 1)
    check('and by exactly one over the control run',
          len(icv.owner_records(after)) - len(icv.owner_records(control)), 1)


def load_check(dat, purpose, exe='player', min_suns=11):
    """Open a blob in a fresh client and report what it read."""
    import game_cycle as gc
    snap = gc.restart(dat, purpose=purpose, wait_for_lock=600.0, exe=exe,
                      min_suns=min_suns)
    civ = None
    try:
        import gamestate as gs
        civ = gs.resolve_civ(snap, None, quiet=True)
    except Exception:                                       # noqa: BLE001
        pass
    owned = len(snap.owned_planets(civ)) if civ else 0
    log(f'  opened: turn {snap.turn}, {len(snap.suns)} sun(s), local civ '
        f'{civ.civ_name if civ else "?"}, {owned} planet(s)')
    gc.close_client()
    return snap.turn, (civ.civ_name if civ else None), owned


def run(fixture, work, name, uid, turns, seconds, save_dir):
    import game_cycle as gc

    log(f'=== J3: a join written by the launcher, applied by the referee ===')
    store = prepare(fixture, work, name, uid, seconds)
    turn = store.state()['turn']
    before = store.turn_blob(turn)
    log(f'  before: {len(icv.owner_records(before))} civ(s), '
        f'{len(free_planets(before))} free planet(s), civ count '
        f'{civ_count(before)}, high-water {high_water(before)}')

    # The control: the same galaxy, the same turn, no join request.
    control_root = work + '_control'
    if os.path.exists(control_root):
        shutil.rmtree(control_root)
    shutil.copytree(fixture, control_root)
    control_store = turn_store.TurnStore(control_root)
    control_store.update_state({'deadline': time.time() - 1,
                                'turn_seconds': seconds})
    roster_before = list(store.civs())

    proc = ensure_server()
    try:
        log(f'\n--- closing turn {turn} with no join, as the control ---')
        control_turn = referee.resolve_turn(control_store, save_dir=save_dir,
                                            log=log)
        control = control_store.turn_blob(control_turn)

        log(f'\n--- closing turn {turn} with the join, in a real client ---')
        new_turn = referee.resolve_turn(store, save_dir=save_dir, log=log)
        check('the turn advanced', new_turn > turn)
        check('and the control reached the same turn', control_turn, new_turn)
        after = store.turn_blob(new_turn)
        log(f'  after: {len(icv.owner_records(after))} civ(s), civ count '
            f'{civ_count(after)}, high-water {high_water(after)}')

        log('\n--- what the join did to the galaxy ---')
        measure(before, after, control, name)
        check('the roster seats the newcomer', name in store.civs())
        check('and keeps everybody it had',
              sorted(set(roster_before) - set(store.civs())), [])
        check('while the control roster is untouched',
              control_store.civs(), roster_before)

        note = store.note(name, new_turn)
        check('the newcomer is left a note on the turn they will play',
              bool(note))
        seat = (store.state().get(joins.JOINED_KEY) or {}).get(name) or {}
        check('naming the system they landed in',
              bool(seat.get('system')) and
              any(str(seat['system']) in line for line in note))
        log('  the note reads:')
        for line in note:
            log(f'    {line}')

        check('the request has been consumed',
              joins.pending(store, log=lambda *a: None), [])
        done = os.path.join(joins.request_dir(store), joins.DONE_DIR,
                            f'{uid or name}.json')
        check('and answered in joins/done', os.path.exists(done))
        if os.path.exists(done):
            with open(done, encoding='utf-8') as fh:
                log(f'  the answer: {json.dumps(json.load(fh))[:200]}')

        rec = store.archive_record(turn)
        check('the archive names who joined',
              [j['name'] for j in (rec or {}).get('joined', [])], [name])

        played = new_turn
        for _ in range(max(0, turns - 1)):
            log(f'\n--- closing turn {played}, now with the newcomer seated ---')
            store.update_state({'deadline': time.time() - 1})
            nxt = referee.resolve_turn(store, save_dir=save_dir, log=log)
            check(f'turn {played} closed and advanced', nxt > played)
            played = nxt
        final = store.turn_blob(played)
        check('the counters still agree after playing on',
              (civ_count(final), high_water(final)),
              (len(icv.owner_records(final)), idg.max_object_id(final)))
        check('and the newcomer still owns its world',
              len(holdings(final).get(name, ([], [], b''))[0]), 1)

        log('\n--- a cold client, stamped for the civ that joined ---')
        os.makedirs(WORK, exist_ok=True)
        dat = os.path.join(WORK, 'played.dat')
        with open(dat, 'wb') as fh:
            fh.write(set_blob_player.set_player(final, name, log=log))
        got_turn, got_civ, owned = load_check(
            dat, f'join_turn_acceptance J3 as {name}')
        check('the newcomer\'s own client opened the galaxy',
              got_turn, played)
        check('as the newcomer', got_civ, name)
        check('holding the world the join gave them', owned, 1)
    finally:
        try:
            gc.close_client()
        except Exception as exc:                            # noqa: BLE001
            log(f'  could not close the client, {exc}')
        gc.release_client_lock()
        if proc is not None:
            proc.terminate()
            log(f'  stopped the cs_server this run started (pid {proc.pid})')

    log(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    for name_ in FAIL:
        log(f'  failed: {name_}')
    return 1 if FAIL else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    sub = ap.add_subparsers(dest='cmd', required=True)

    r = sub.add_parser('run')
    r.add_argument('--fixture', default=FIXTURE,
                   help='a turn store to copy; never written to')
    r.add_argument('--work', default=os.path.join(WORK, 'galaxy'),
                   help='where the copy goes')
    r.add_argument('--name', default='Joiner')
    r.add_argument('--uid', default='uid-joiner')
    r.add_argument('--turns', type=int, default=2,
                   help='turns to close, the first being the one the join '
                        'lands on')
    r.add_argument('--turn-seconds', type=int, default=60)
    r.add_argument('--save-dir', default=None,
                   help='where captures land, which is the data directory of '
                        'whoever is hosting cs_server')

    l = sub.add_parser('load')
    l.add_argument('--dat', required=True)
    l.add_argument('--exe', default='player')

    a = ap.parse_args()
    if a.cmd == 'load':
        load_check(a.dat, 'join_turn_acceptance load', exe=a.exe)
        return 0
    return run(a.fixture, a.work, a.name, a.uid, a.turns, a.turn_seconds,
               a.save_dir)


if __name__ == '__main__':
    sys.exit(main())
