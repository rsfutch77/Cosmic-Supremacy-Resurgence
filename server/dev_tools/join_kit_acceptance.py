"""
join_kit_acceptance.py , a joiner's starting kit, in a client that ran
=======================================================================
    python join_kit_acceptance.py run
    python join_kit_acceptance.py run --name Ada --turns 3
    python join_kit_acceptance.py load --dat join_work/kit/played.dat

`server/tests/test_starting_kit.py` covers what the kit is and proves it
field by field against a civ `make_multiplayer_galaxy` adds at generation, and
none of it says the resulting galaxy loads. This does the other half.

It is `join_turn_acceptance.py` with the kit's questions added rather than a
second harness: the join still has to arrive through the referee, and the two
things most likely to break are the two things that file already measures.

What this does and what counts as a failure:

    control   the same turn of the same galaxy closed with no join waiting.
              Failure: it does not close, or it reaches a different turn.
    close     the referee closes the turn with the join, on a real client.
              Failure: the tick raises, or the newcomer is not in the blob.
    kit       the newcomer's planets, population, stationed military,
              recruitment, production queue and points, credits, research
              topic, design book, hulls and hull records match a civ built by
              `make_multiplayer_galaxy` at generation. Failure: any field
              differing, which is what the old behaviour did on nine of them.
    untouched every incumbent's planets, ships and `OWNR` bytes byte-identical
              to the control run. Failure: any difference, which would mean the
              kit took something from somebody.
    play      two more turns close. Failure: a short tick, which is what a blob
              the engine half-read looks like.
    client    the published blob, stamped for the newcomer, opens in a client
              that was not already holding it, and the engine's own objects are
              read back rather than the blob's: one planet, two ships, the
              starting bank, no research topic, one design. Failure: the client
              exiting during the load, the D1 shape, or the engine disagreeing
              with the blob about what the newcomer has.

The last one is the point of the file. `inject_ship` into a mid-game galaxy is
a path nobody had exercised: K2's twelve joins were given nothing, and the ship
sections are the part of a blob the engine is known to rewrite on its own.

Requirements, both of which fail late rather than early: `cs_server.py`
listening on 127.0.0.1:8888, and the machine's one game client, taken through
`game_cycle`'s advisory lock. A server already on 8888 is used and never
killed, because a packaged launcher hosting its own outranks a checkout one.

Nothing here touches a galaxy anybody else is using. The store under test is a
copy made per run, and `server/uidemo` and `server/galaxy_demo` are read only.
"""
import argparse
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
import merge_orders as mo
import make_multiplayer_galaxy as mmg
import set_blob_player
import joins
import referee
import turn_store

# Under `join_work`, which `.gitignore` already covers, rather than a new
# directory beside it that it does not: a harness that leaves untracked
# scratch in the tree is a harness whose output somebody else commits.
WORK = os.path.join(SERVER, 'join_work', 'kit')
FIXTURE = os.path.join(SERVER, 'galaxy_demo')
REFERENCE = os.path.join(REPO, 'client', 'SinglePlayerGalaxy.dat')
PASS, FAIL = [], []


def log(msg=''):
    print(msg, flush=True)


def quiet(_msg=''):
    pass


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    log(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        log(f'          wanted {want!r}, got {got!r}')


# ── reading a galaxy ─────────────────────────────────────────────────────────
def civ_count(blob):
    return struct.unpack_from('<I', blob, icv.civ_count_at(blob))[0]


def high_water(blob):
    return struct.unpack_from('<I', blob, idg.HIGH_WATER_ID)[0]


def civ(blob, name):
    return next(o for o in icv.owner_records(blob) if o['name'] == name)


def homeworld(blob, name):
    oid = civ(blob, name)['oid']
    mine = [p for p in icv.planet_records(blob) if p['owner'] == oid]
    return max(mine, key=lambda p: p['plpr_ln']) if mine else None


def masked_ship(blob, ship, owner_oid):
    """A ship record with its identity blanked, leaving the hull.

    The object id twice, the owner, the position, the orbit and the design are
    what one civ's ship differs from another's by; everything else, the `DYNO`
    and the crew included, is the kind of ship and its condition.
    """
    rec = bytearray(blob[ship['off']:ship['end']])
    icv.replace_u32(rec, owner_oid, 0, 0, len(rec))
    struct.pack_into('<I', rec, 8, 0)
    struct.pack_into('<fff', rec, 12, 0.0, 0.0, 0.0)
    struct.pack_into('<I', rec, ship['dyno'] - ship['off'] + 8, 0)
    struct.pack_into('<I', rec, ship['shpr'] - ship['off'] + 8, 0)
    struct.pack_into('<I', rec, len(rec) - 4, 0)
    return bytes(rec)


def kit_of(blob, name):
    """Every field of the starting kit this project can read, for one civ."""
    o = civ(blob, name)
    hq = homeworld(blob, name)
    plpr = bytes(blob[hq['plpr_payload']:hq['plpr_end']]) if hq else b''
    ships = [s for s in ish.ship_records(blob) if s['owner'] == o['oid']]
    designs = [nm for off, _oid, nm in idg.design_records(blob)
               if o['off'] < off < o['end']]
    return {
        'planets': len([p for p in icv.planet_records(blob)
                        if p['owner'] == o['oid']]),
        'plpr_len': len(plpr),
        'jobs': [c[0] for c in (mo.citizens_of(plpr) or [])],
        'military': len(mo.military_of(plpr)[1] or []),
        'recruit': mo.recruit_of(plpr),
        'progress': mo.progress_of(plpr),
        'prod': icv.planet_prod(blob, hq['id']) if hq else None,
        'credits': mo.credits_of(blob, o['oid']),
        'research': mo.research_of(blob, o['oid']),
        'designs': len(designs),
        'ships': len(ships),
        'hulls': sorted(masked_ship(blob, s, o['oid']) for s in ships),
    }


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


# ── the stub server the capture travels over ─────────────────────────────────
def server_listening(host='127.0.0.1', port=8888, timeout=2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def ensure_server():
    """(process or None). A server already on 8888 is used and never killed."""
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


# ── the run ──────────────────────────────────────────────────────────────────
def reference_kit():
    """What a civ added at galaxy generation starts with.

    Built rather than written down, by running `make_multiplayer_galaxy.build`
    on the tracked fixture, so the thing a joiner is compared against is a civ
    created the way every civ in a new galaxy is created. A list of numbers
    kept here would drift from that function the first time it changed.
    """
    fresh = mmg.build(sp.load_any(REFERENCE), ['Alpha', 'Beta', 'Gamma'],
                      log=quiet)
    return kit_of(fresh, 'Gamma'), kit_of(fresh, 'Alpha')


def prepare(fixture, work, name, uid, seconds):
    """A private copy of a real galaxy with one join request waiting in it."""
    if os.path.exists(work):
        shutil.rmtree(work)
    shutil.copytree(fixture, work)
    store = turn_store.TurnStore(work)
    store.update_state({'deadline': time.time() - 1, 'turn_seconds': seconds})
    state = store.state()
    log(f'  copy of {os.path.basename(fixture)} at turn {state["turn"]}, '
        f'roster {state["civs"]}')

    import launcher
    req = launcher.join_request(name, uid, launcher.build_id(),
                                turn=state['turn'])
    launcher.send_join_request(store, req)
    waiting = joins.pending(store, log=quiet)
    check('the launcher\'s request is one the referee reads',
          [r['name'] for r in waiting], [name])
    return store


def describe_donor(blob, log=log):
    """What seat one holds, which is what a clone used to come out as."""
    seat = icv.seat_one(icv.owner_records(blob))['name']
    k = kit_of(blob, seat)
    log(f'  seat one is {seat!r}: {k["planets"]} planet(s), '
        f'{len(k["jobs"])} citizen(s), {k["designs"]} design(s), '
        f'{k["credits"]} credits, research {k["research"]}')
    return seat, k


def load_check(dat, purpose, name, exe='player', min_suns=11):
    """Open a blob in a fresh client and read the engine's own objects."""
    import game_cycle as gc
    snap = gc.restart(dat, purpose=purpose, wait_for_lock=600.0, exe=exe,
                      min_suns=min_suns)
    import gamestate as gs
    seen = {'turn': snap.turn, 'suns': len(snap.suns)}
    who = gs.resolve_civ(snap, None, quiet=True)
    seen['civ'] = who.civ_name if who else None
    if who is not None:
        seen['planets'] = len(snap.owned_planets(who))
        seen['ships'] = len(snap.owned_ships(who))
        seen['cash'] = who.cash
        seen['topic'] = who.topic
        seen['designs'] = len([d for d in snap.designs if d.owner is who])
        seen['ship_designs'] = sorted(
            {s.design.design_name for s in snap.owned_ships(who)
             if s.design is not None})
    log(f'  the client read: {seen}')
    gc.close_client()
    return seen


def run(fixture, work, name, uid, turns, seconds, save_dir):
    import game_cycle as gc

    log('=== the starting kit, through the referee, in a real client ===')
    ref, seat_one_kit = reference_kit()
    log(f'  the reference civ, as make_multiplayer_galaxy builds one: '
        f'{ref["planets"]} planet(s), {len(ref["jobs"])} citizen(s), '
        f'{ref["designs"]} design(s), {ref["ships"]} hull(s), '
        f'{ref["credits"]} credits')
    check('the reference civ matches seat one of its own galaxy, which is '
          'what levelling at generation means',
          [ref[f] for f in ('planets', 'jobs', 'designs', 'ships', 'hulls',
                            'credits', 'research')],
          [seat_one_kit[f] for f in ('planets', 'jobs', 'designs', 'ships',
                                     'hulls', 'credits', 'research')])

    store = prepare(fixture, work, name, uid, seconds)
    turn = store.state()['turn']
    before = store.turn_blob(turn)
    log(f'  before: {len(icv.owner_records(before))} civ(s), '
        f'{len(free_planets(before))} free planet(s)')
    donor_name, donor_kit = describe_donor(before)
    check('the donor is not at a generated civ\'s start, so the galaxy can '
          'tell a levelled newcomer from a cloned one',
          [donor_kit['jobs'] == ref['jobs'],
           donor_kit['credits'] == ref['credits'],
           donor_kit['research'] == ref['research']],
          [False, False, False])

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
        check('the newcomer is in the published blob',
              name in {o['name'] for o in icv.owner_records(after)})

        log('\n--- what the newcomer starts with, against a generated civ ---')
        got = kit_of(after, name)
        for field in ('planets', 'plpr_len', 'jobs', 'military', 'recruit',
                      'progress', 'prod', 'credits', 'research', 'designs',
                      'ships', 'hulls'):
            check(f'{field} matches the generated civ', got[field], ref[field])
        check('and its world was free before the join',
              homeworld(after, name)['id'] in free_planets(before))
        check('its homeworld carries seat one\'s rate pair, the one field a '
              'newcomer is meant to take from the donor',
              tuple(bytes(after[homeworld(after, name)['plpr_payload']:]
                          )[i] for i in mmg.RATE_OFFSETS),
              tuple(bytes(before[homeworld(before, donor_name)['plpr_payload']:]
                          )[i] for i in mmg.RATE_OFFSETS))

        log('\n--- what the join did to everybody else ---')
        was, now, ctl = holdings(before), holdings(after), holdings(control)
        check('the control run seated nobody', name in ctl, False)
        for who in was:
            check(f'{who} kept exactly its planets', now[who][0], was[who][0])
            check(f'{who} kept exactly its ships', now[who][1], was[who][1])
            check(f'{who} holds the same planets as in the control run',
                  now[who][0], ctl[who][0])
            check(f'{who} holds the same ships as in the control run',
                  now[who][1], ctl[who][1])
            check(f'{who}\'s OWNR is byte-identical to the control run',
                  now[who][2], ctl[who][2])
        check('the GLXY civ count matches the OWNR records',
              civ_count(after), len(icv.owner_records(after)))
        check('the high-water id matches the largest object id',
              high_water(after), idg.max_object_id(after))
        # A SHIP record carries its object id twice and `inject_ship` used to
        # leave the donor's in the second place. Only the newcomer's hulls can
        # say anything about the fix: galaxy_demo already holds a ship this
        # tool cloned before it, and repairing somebody else's ship is not a
        # thing a join is allowed to do.
        stale = [s['id'] for s in ish.ship_records(after)
                 if struct.unpack_from('<I', after, s['end'] - 4)[0] != s['id']]
        check('every hull the join added names itself in both places it '
              'carries an id',
              [s for s in stale
               if s in holdings(after)[name][1]], [])
        if stale:
            log(f'  ships cloned before the fix and left alone: {stale}')
        check('no ship flies a design the galaxy does not hold',
              [s['id'] for s in ish.ship_records(after)
               if s['design'] not in {oid for _o, oid, _n
                                      in idg.design_records(after)}], [])
        check('the roster seats the newcomer', name in store.civs())
        check('and keeps everybody it had',
              sorted(set(roster_before) - set(store.civs())), [])

        played = new_turn
        for _ in range(max(0, turns - 1)):
            log(f'\n--- closing turn {played}, with the newcomer seated ---')
            store.update_state({'deadline': time.time() - 1})
            nxt = referee.resolve_turn(store, save_dir=save_dir, log=log)
            check(f'turn {played} closed and advanced', nxt > played)
            played = nxt
        final = store.turn_blob(played)
        log(f'\n--- after {played - turn} more turn(s) of engine ---')
        end = kit_of(final, name)
        check('the newcomer still owns its world', end['planets'], 1)
        check('and still has its hulls, which the engine deletes when it '
              'wants to', end['ships'], icv.STARTING_SHIPS)
        check('on the one design it was given', end['designs'], 1)
        check('the counters still agree',
              (civ_count(final), high_water(final)),
              (len(icv.owner_records(final)), idg.max_object_id(final)))
        log(f'  population {len(end["jobs"])}, credits {end["credits"]}, '
            f'research {end["research"]}')

        log('\n--- a cold client, stamped for the civ that joined ---')
        os.makedirs(WORK, exist_ok=True)
        dat = os.path.join(WORK, 'played.dat')
        with open(dat, 'wb') as fh:
            fh.write(set_blob_player.set_player(final, name, log=quiet))
        seen = load_check(dat, f'join_kit_acceptance as {name}', name)
        check('the newcomer\'s own client opened the galaxy',
              seen['turn'], played)
        check('as the newcomer', seen.get('civ'), name)
        check('holding the one world the join gave them',
              seen.get('planets'), 1)
        check('and flying the hulls the kit gave them',
              seen.get('ships'), icv.STARTING_SHIPS)
        check('which the engine builds on the one design it owns',
              seen.get('designs'), 1)
        check('every hull on that design',
              len(seen.get('ship_designs') or []), 1)
        # Not the starting bank exactly: the newcomer has been seated for a
        # turn or two by now and the engine has paid it an income, which is
        # the same 200 to 216 step the turn-1 generation captures show. What
        # can be said is that the engine agrees with the blob and that the
        # figure is nothing like the donor's.
        check('the engine agrees with the blob about the newcomer\'s bank',
              seen.get('cash'), mo.credits_of(final, civ(final, name)['oid']))
        check('which is the starting bank and its income, not the donor\'s',
              icv.STARTING_CREDITS <= (seen.get('cash') or 0)
              < donor_kit['credits'])
        check('and no research topic chosen', seen.get('topic'), -1)
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
    for failed in FAIL:
        log(f'  failed: {failed}')
    return 1 if FAIL else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    sub = ap.add_subparsers(dest='cmd', required=True)

    r = sub.add_parser('run')
    r.add_argument('--fixture', default=FIXTURE,
                   help='a turn store to copy; never written to')
    r.add_argument('--work', default=os.path.join(WORK, 'galaxy'))
    r.add_argument('--name', default='Kitted')
    r.add_argument('--uid', default='uid-kitted')
    r.add_argument('--turns', type=int, default=3,
                   help='turns to close, the first being the join\'s')
    r.add_argument('--turn-seconds', type=int, default=60)
    r.add_argument('--save-dir', default=None,
                   help='where captures land, which is the data directory of '
                        'whoever is hosting cs_server')

    l = sub.add_parser('load')
    l.add_argument('--dat', required=True)
    l.add_argument('--name', default=None)
    l.add_argument('--exe', default='player')

    a = ap.parse_args()
    if a.cmd == 'load':
        load_check(a.dat, 'join_kit_acceptance load', a.name, exe=a.exe)
        return 0
    return run(a.fixture, a.work, a.name, a.uid, a.turns, a.turn_seconds,
               a.save_dir)


if __name__ == '__main__':
    sys.exit(main())
