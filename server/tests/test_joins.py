"""
test_joins.py , a stranger asking for a seat at a turn boundary
===============================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_joins.py

J3. The launcher writes a join request and nothing consumed it; `server/joins.py`
is what consumes it, inside `referee.resolve_turn`, on the blob the tick
produced. Everything here is offline: the injection is bytes in and bytes out
and the engine tick is stubbed, exactly as `test_bad_submissions.py` stubs it.
What is **not** tested here is that a joined galaxy loads and ticks in a real
client, which needs the game and is `server/dev_tools/join_turn_acceptance.py`.

What a check has to do to be worth having
------------------------------------------
"a civ appears" proves almost nothing, so no check in this file stops there.
Every join is measured against a **before** taken from the same run:

  * the newcomer owns **exactly one** planet, and it was free before;
  * every incumbent owns **exactly** the planets and ships it owned before, and
    its `OWNR` bytes are unchanged, so a join that quietly moved somebody
    else's empire fails rather than passing for having added a civ;
  * the `GLXY` civ count agrees with the number of `OWNR` records and the
    high-water object id agrees with the largest id in the blob, which is what
    K2 measured over twelve joins and is the pair that decides whether the
    engine will read the blob at all;
  * the blob still parses as a section tree.

And a galaxy where nothing changed cannot show a join worked, so the refusal
cases are each run against a **control galaxy in the same shape that does have
room**, and the success cases assert the roster grew rather than that it did
not shrink.

The conditions are built rather than borrowed
----------------------------------------------
No galaxy on disk is full, so the no-room case fills one. No galaxy on disk has
a wiped civ in it, so the rejoin case wipes one with `wipe_civ`, which is what
`abandonment.reclaim` calls, rather than by editing a roster to look like it.
The margin case leaves exactly one free planet and puts it next door to a
claimed one, because on every galaxy in this repository the margin never binds
and a test that did not force it would be asserting nothing.
"""
import json
import os
import shutil
import struct
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools'),
          os.path.join(ROOT, 'client', 'dev_tools'),
          os.path.join(ROOT, 'release')):
    if d not in sys.path:
        sys.path.insert(0, d)

import save_parser as sp
import inject_civ as icv
import inject_design as idg
import inject_ship as ish
import wipe_civ
import joins
import abandonment
import referee
import player_turn
import turn_store
from turn_store import TurnStore

GALAXY = os.path.join(ROOT, 'client', 'SinglePlayerGalaxy.dat')
ROSTER = ['DemoPlayer', 'BadGuy']
TURN = 11
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


# ── reading a galaxy ─────────────────────────────────────────────────────────
def set_turn(blob, turn):
    """The same blob carrying a different turn number, so a stubbed tick can
    advance the clock the way a real one does."""
    tree = sp.parse_blob(blob)
    glob = next(tree[0].find('GLOB'))
    out = bytearray(blob)
    struct.pack_into('<I', out, glob.payload, turn)
    return bytes(out)


def civ_count(blob):
    return struct.unpack_from('<I', blob, icv.civ_count_at(blob))[0]


def high_water(blob):
    return struct.unpack_from('<I', blob, idg.HIGH_WATER_ID)[0]


def largest_id(blob):
    return idg.max_object_id(blob)


def holdings(blob):
    """{civ name: (sorted planet ids, sorted ship ids, OWNR bytes)}.

    The `OWNR` bytes are in it because ownership by planet is not the only
    thing a join could disturb: the clone is spliced in after the last `OWNR`
    and every enclosing section is widened, which is precisely the operation
    that could move or truncate a neighbour.
    """
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


def systems_known(blob, name):
    """The systems one civ has entered, or None when it has no table.

    `inject_civ` clones the donor's `OWNR` whole and `EXSY` is inside it, so
    without `joins.forget_inherited_map` a newcomer starts holding the donor's
    map. The donor is seat one, which in a live galaxy is a player still in the
    game.
    """
    import exsy
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    at = {r['off']: r['name'] for r in icv.owner_records(blob)}
    for ownr in glxy.find('OWNR'):
        if at.get(ownr.start) != name:
            continue
        sec = next(ownr.find('EXSY'), None)
        if sec is None:
            return None
        return exsy.systems_known(bytes(blob[sec.payload:sec.end]))
    return None


def counters_agree(blob):
    """The two fields the engine reads back, against what the blob holds."""
    return (civ_count(blob) == len(icv.owner_records(blob))
            and high_water(blob) == largest_id(blob))


def parses(blob) -> bool:
    try:
        sp.parse_blob(blob)
        return True
    except Exception:                                       # noqa: BLE001
        return False


def joined_cleanly(label, before, after, newcomers):
    """Every structural claim a granted join has to satisfy, in one place."""
    was, now = holdings(before), holdings(after)
    check(f'{label}: the blob still parses', parses(after))
    check(f'{label}: the civ count and high-water id agree with the blob',
          counters_agree(after))
    check(f'{label}: the newcomer(s) are in the blob',
          sorted(set(now) - set(was)), sorted(newcomers))
    for name in newcomers:
        check(f'{label}: {name} owns exactly one planet',
              len(now.get(name, ([], [], b''))[0]), 1)
        check(f'{label}: {name}\'s planet was free before',
              now[name][0][0] in free_planets(before))
    for name in was:
        check(f'{label}: {name} kept exactly its planets',
              now[name][0], was[name][0])
        check(f'{label}: {name} kept exactly its ships',
              now[name][1], was[name][1])
        check(f'{label}: {name}\'s OWNR is byte-identical',
              now[name][2], was[name][2])


# ── building galaxies that hold the condition ────────────────────────────────
def fill_every_planet(blob, owner_name, leave=0):
    """Give every uncolonised planet an owner, leaving `leave` of them free.

    The late sandbox K4 names, which no archived galaxy has reached. Only the
    owner dword is written; what matters is what `pick_homeworld` can still
    find, and nothing here is loaded in a client.

    `leave` keeps the planets **closest to something already claimed**, which
    is what makes the margin bindable: the maximin pick then has only a planet
    next door to an incumbent to offer, and the margin is what refuses it.
    """
    civ = next(o for o in icv.owner_records(blob) if o['name'] == owner_name)
    claimed = [p['pos'] for p in icv.planet_records(blob) if p['owner']]
    free = [p for p in icv.planet_records(blob)
            if not p['owner'] and p['nlen'] == 0]
    free.sort(key=lambda p: min(icv.dist(p['pos'], c) for c in claimed))
    keep = {p['id'] for p in free[:leave]}
    buf = bytearray(blob)
    for p in free:
        if p['id'] in keep:
            continue
        struct.pack_into('<I', buf, p['payload'] + wipe_civ.PLNT_OWNER_OFF,
                         civ['oid'])
    return bytes(buf)


def fresh_store(blob, roster=None, turn=TURN, seconds=1800):
    root = tempfile.mkdtemp(prefix='joins_')
    store = TurnStore(root)
    store.start(blob, list(roster or ROSTER), turn_seconds=seconds, turn=turn)
    return store


def write_request(store, name, uid=None, at=None, turn=TURN):
    """A join request exactly as the launcher writes one.

    `release/launcher.py` is imported rather than imitated, because the shape
    is a contract between two files nobody owns both of, and a test that wrote
    its own JSON would pass forever after the launcher changed. The import is
    headless: the launcher builds no Tk until something calls for a window.
    """
    import launcher
    req = launcher.join_request(name, uid, '0.1.0+dev', turn=turn, now=at)
    return launcher.send_join_request(store, req)


def close_turn(store, blob_turn=None):
    """`resolve_turn` with the engine stubbed. Returns the turn it published.

    The stub advances the turn number and changes nothing else, so everything
    the published blob holds beyond that came from the merge, the abandonment
    pass or the join pass, and the join pass is what this file is about.
    """
    def fake_tick(blob, turns=1, secs=10, save_dir=None, log=print, **kw):
        return set_turn(blob, (blob_turn or turn_store.turn_of(blob)) + 1)

    real_tick, real_ready = referee.tick, player_turn.save_path_ready
    referee.tick = fake_tick
    player_turn.save_path_ready = lambda: (True, '')
    try:
        return referee.resolve_turn(store, log=lambda *a: None)
    finally:
        referee.tick, player_turn.save_path_ready = real_tick, real_ready


def answer(store, key):
    """The answer filed for one request, or None."""
    path = os.path.join(joins.request_dir(store), joins.DONE_DIR,
                        f'{key}.json')
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


# ── the tests ────────────────────────────────────────────────────────────────
def test_shape(blob):
    print('\nthe request the launcher writes is the request that is read')
    store = fresh_store(blob)
    where = write_request(store, 'Ada', uid='uid-ada', at=100.0)
    check('the launcher wrote it under joins/',
          os.path.dirname(where), joins.request_dir(store))
    check('and named the file by the uid, not the name',
          os.path.basename(where), 'uid-ada.json')
    waiting = joins.pending(store, log=lambda *a: None)
    check('one request is waiting', len(waiting), 1)
    check('carrying the name the player typed', waiting[0]['name'], 'Ada')
    check('the uid, which is what J4 will bind a seat to',
          waiting[0]['uid'], 'uid-ada')
    check('and the turn they asked during', waiting[0]['requested_turn'], TURN)

    write_request(store, 'Bo', uid='uid-bo', at=50.0)
    check('two requests come back oldest first',
          [r['name'] for r in joins.pending(store, log=lambda *a: None)],
          ['Bo', 'Ada'])


def test_a_join_lands(blob):
    print('\na join asked during turn N is playable at turn N+1')
    store = fresh_store(blob)
    write_request(store, 'Ada', uid='uid-ada')
    before = store.turn_blob(TURN)

    new_turn = close_turn(store)
    check('the turn closed and moved on', new_turn, TURN + 1)
    after = store.turn_blob(new_turn)
    joined_cleanly('joined', before, after, ['Ada'])

    check('the roster holds the newcomer', 'Ada' in store.civs())
    check('and nobody was dropped from it', set(ROSTER) <= set(store.civs()))
    check('the galaxy grew by exactly one civ',
          len(icv.owner_records(after)) - len(icv.owner_records(before)), 1)

    note = store.note('Ada', new_turn)
    check('the newcomer has a note on the turn they will play', bool(note))
    check('naming the planet they landed on',
          any('#' in line for line in note))
    system = store.state()[joins.JOINED_KEY]['Ada']['system']
    check('and naming the system, which is what a map is labelled with',
          any(system in line for line in note))
    check('the seat records the uid that claimed it, which is J4\'s half',
          store.state()[joins.JOINED_KEY]['Ada']['uid'], 'uid-ada')

    # The donor is seat one, which is a player still in the game, and the clone
    # carries its explored map. The control is the donor's own table: an
    # assertion that the newcomer knows nothing would pass on a galaxy where
    # nobody knows anything.
    donor = icv.seat_one(icv.owner_records(before))['name']
    check('the donor really has a map to inherit',
          bool(systems_known(before, donor)))
    check('and the newcomer did not inherit it',
          systems_known(after, 'Ada'), [])
    check('while the donor kept its own',
          systems_known(after, donor), systems_known(before, donor))

    check('the request has been taken out of joins/',
          joins.pending(store, log=lambda *a: None), [])
    filed = answer(store, 'uid-ada')
    check('and an answer is filed in its place', bool(filed))
    check('saying it was granted', (filed or {}).get('outcome'), joins.GRANTED)

    rec = store.archive_record(TURN)
    check('the archive records who joined at this boundary',
          [j['name'] for j in (rec or {}).get('joined', [])], ['Ada'])


def test_nothing_waiting_changes_nothing(blob):
    print('\na galaxy nobody asked to join is left alone')
    store = fresh_store(blob)
    before = store.turn_blob(TURN)
    new_turn = close_turn(store)
    after = store.turn_blob(new_turn)
    check('the roster is unchanged', store.civs(), ROSTER)
    check('the civ count is unchanged', civ_count(after), civ_count(before))
    check('and no join is recorded',
          (store.archive_record(TURN) or {}).get('joined'), [])


def test_no_room(blob):
    print('\na galaxy with no free planet refuses and closes the turn anyway')
    full = fill_every_planet(blob, 'BadGuy')
    check('the galaxy under test really has no free planet',
          free_planets(full), [])
    store = fresh_store(full)
    write_request(store, 'Ada')
    before = store.turn_blob(TURN)

    new_turn = close_turn(store)
    check('the turn still closed for everybody else', new_turn, TURN + 1)
    after = store.turn_blob(new_turn)
    check('the galaxy is untouched',
          set_turn(after, TURN), before)
    check('the roster is unchanged', store.civs(), ROSTER)
    check('the civ count is unchanged', civ_count(after), civ_count(before))
    note = store.note('Ada', TURN)
    check('the player is told, through the note path', bool(note))
    check('and told it was about room',
          any('room' in line for line in note))
    check('the request is consumed rather than retried every turn',
          joins.pending(store, log=lambda *a: None), [])
    check('the answer says refused',
          (answer(store, 'Ada') or {}).get('outcome'), joins.REFUSED)

    # The control. Without it a `joins` that refused everything would pass
    # every check above.
    room = fresh_store(blob)
    write_request(room, 'Ada')
    joined = room.turn_blob(close_turn(room))
    check('the same request into a galaxy with room is granted',
          'Ada' in store.civs() or 'Ada' in room.civs())
    check('and that galaxy really did grow',
          len(icv.owner_records(joined)), len(icv.owner_records(blob)) + 1)


def test_margin(blob):
    print('\na galaxy whose only free planet is next door refuses too')
    tight = fill_every_planet(blob, 'BadGuy', leave=1)
    check('exactly one planet is free', len(free_planets(tight)), 1)
    spacing = icv.system_spacing(tight)
    left = next(p for p in icv.planet_records(tight)
                if p['id'] == free_planets(tight)[0])
    nearest = min(icv.dist(left['pos'], p['pos'])
                  for p in icv.planet_records(tight) if p['owner'])
    check('and it is inside the galaxy\'s own crowding margin',
          nearest < spacing)

    store = fresh_store(tight)
    write_request(store, 'Ada')
    before = store.turn_blob(TURN)
    new_turn = close_turn(store)
    after = store.turn_blob(new_turn)
    check('the turn closed', new_turn, TURN + 1)
    check('the galaxy is untouched', set_turn(after, TURN), before)
    check('the roster is unchanged', store.civs(), ROSTER)
    check('and the player is told', bool(store.note('Ada', TURN)))


def test_name_already_seated(blob):
    print('\na name already in the galaxy is refused, not merged into')
    store = fresh_store(blob)
    write_request(store, 'BadGuy')
    before = store.turn_blob(TURN)
    new_turn = close_turn(store)
    after = store.turn_blob(new_turn)
    check('no second empire was made',
          len(icv.owner_records(after)), len(icv.owner_records(before)))
    check('the roster is unchanged', store.civs(), ROSTER)
    check('BadGuy kept exactly what it had',
          holdings(after)['BadGuy'][0], holdings(before)['BadGuy'][0])
    check('and the asker is told to pick another name',
          any('different name' in line
              for line in store.note('BadGuy', TURN)))

    print('  a name differing only in capitals is refused the same way')
    other = fresh_store(blob)
    write_request(other, 'badguy')
    grown = other.turn_blob(close_turn(other))
    check('still no second empire',
          len(icv.owner_records(grown)), len(icv.owner_records(blob)))
    check('and the roster is unchanged', other.civs(), ROSTER)


def test_two_in_one_turn(blob):
    print('\ntwo requests in one turn are two empires, and two of one name is one')
    store = fresh_store(blob)
    write_request(store, 'Ada', uid='uid-ada', at=10.0)
    write_request(store, 'Bo', uid='uid-bo', at=20.0)
    before = store.turn_blob(TURN)
    after = store.turn_blob(close_turn(store))
    joined_cleanly('two joins', before, after, ['Ada', 'Bo'])
    check('both are on the roster', {'Ada', 'Bo'} <= set(store.civs()))
    ids = {o['name']: o['oid'] for o in icv.owner_records(after)}
    check('they have different object ids', ids['Ada'] != ids['Bo'])
    homes = holdings(after)
    check('and different homeworlds', homes['Ada'][0] != homes['Bo'][0])

    print('  the same name asked for twice in one turn')
    clash = fresh_store(blob)
    write_request(clash, 'Ada', uid='first', at=10.0)
    write_request(clash, 'Ada', uid='second', at=20.0)
    grown = clash.turn_blob(close_turn(clash))
    check('exactly one civ called Ada exists',
          [o['name'] for o in icv.owner_records(grown)].count('Ada'), 1)
    check('the galaxy grew by one, not two',
          len(icv.owner_records(grown)), len(icv.owner_records(blob)) + 1)
    check('the roster holds one seat for the name', clash.civs().count('Ada'), 1)
    check('the first to ask got it',
          (answer(clash, 'first') or {}).get('outcome'), joins.GRANTED)
    check('the second was refused',
          (answer(clash, 'second') or {}).get('outcome'), joins.REFUSED)


def test_the_launcher_asks_the_store(blob):
    print('\nthe launcher lodges a request through the store, not into a folder')
    import launcher

    class Recording(TurnStore):
        """A store that says whether it was asked rather than written past."""

        def __init__(self, root):
            TurnStore.__init__(self, root)
            self.asked = []

        def request_join(self, req):
            self.asked.append(req)
            return TurnStore.request_join(self, req)

    root = tempfile.mkdtemp(prefix='joins_')
    store = Recording(root)
    store.start(blob, list(ROSTER), turn_seconds=1800, turn=TURN)
    where = launcher.send_join_request(
        store, launcher.join_request('Ada', 'uid-ada', '0.1.0+dev', turn=TURN))

    # The whole of what `release/launcher.py` has to call, pinned from this
    # side because that file is not this one's to change. It already prefers a
    # store's own `request_join` and falls back to writing the file itself, and
    # the fallback is what a Firebase galaxy fell off: the store had no such
    # method and no `root` either, so the launcher raised `JoinNotAccepted`.
    # Fails if the launcher wrote past the store, which is invisible against a
    # folder and total against the other two.
    check('the launcher asked the store rather than writing the file',
          [r['name'] for r in store.asked], ['Ada'])
    check('and the store put it where the worker reads it',
          os.path.dirname(where), joins.request_dir(store))
    check('which is the same request joins.pending gives back',
          [r['key'] for r in joins.pending(store, log=lambda *a: None)],
          ['uid-ada'])
    shutil.rmtree(root, ignore_errors=True)


def test_a_worker_killed_before_commit(blob):
    print('\na worker killed after publish seats one empire, not two')
    store = fresh_store(blob)
    write_request(store, 'Ada', uid='uid-ada')

    # `resolve_turn` without its last line. `apply` changes bytes, `publish`
    # makes them the galaxy, and `commit` is every durable write after it; a
    # worker killed in that gap is what the split across `publish` exists for
    # and nothing has ever run it.
    new_turn = TURN + 1
    grown, outcomes = joins.apply(store, TURN, new_turn,
                                  set_turn(store.turn_blob(TURN), new_turn),
                                  log=lambda *a: None)
    check('the join was granted before the crash',
          [o['outcome'] for o in outcomes], [joins.GRANTED])
    store.publish(new_turn, grown)

    names = [o['name'] for o in icv.owner_records(store.turn_blob(new_turn))]
    check('the galaxy holds the newcomer', names.count('Ada'), 1)
    # The recoverable half of the pair, and the reason the order is this way
    # round: a roster naming a civ the galaxy does not hold fails that player's
    # every submission forever, and this one an operator answers with a name.
    check('and the roster does not, which is what a crash here leaves',
          'Ada' in store.civs(), False)
    check('the request is still waiting, because commit never ran',
          [r['key'] for r in joins.pending(store, log=lambda *a: None)],
          ['uid-ada'])
    check('and nothing was answered', store.join_answer('uid-ada'), None)

    second = close_turn(store, blob_turn=new_turn)
    after = store.turn_blob(second)
    names = [o['name'] for o in icv.owner_records(after)]
    # The check this test exists for. Fails if the next boundary granted the
    # still-waiting request: that is a second empire for one player, and unlike
    # a lost join it cannot be noticed by the player it happened to.
    check('the next boundary does not seat a second empire',
          names.count('Ada'), 1)
    check('the counters still agree', counters_agree(after))
    filed = answer(store, 'uid-ada') or {}
    check('the request is answered as a refusal',
          filed.get('outcome'), joins.REFUSED)
    # Refused for the right reason. `add_civ`'s own name check would refuse it
    # too, later and with a message about bytes; this one is the roster and the
    # galaxy disagreeing, which is what an operator has to settle.
    check('naming the galaxy and the roster disagreeing',
          'roster' in filed.get('reason', ''))
    check('and it is no longer waiting',
          joins.pending(store, log=lambda *a: None), [])


def test_asked_twice_across_turns(blob):
    print('\nthe same request seen again next turn is not a second empire')
    store = fresh_store(blob)
    write_request(store, 'Ada', uid='uid-ada')
    first = store.turn_blob(close_turn(store))
    check('seated once', [o['name'] for o in icv.owner_records(first)].count('Ada'), 1)

    # The launcher's Join pressed again, which writes the same file back.
    write_request(store, 'Ada', uid='uid-ada')
    second = store.turn_blob(close_turn(store, blob_turn=TURN + 1))
    check('still exactly one civ called Ada',
          [o['name'] for o in icv.owner_records(second)].count('Ada'), 1)
    check('and one seat', store.civs().count('Ada'), 1)
    check('the counters still agree', counters_agree(second))
    check('the repeat was answered as a refusal',
          (answer(store, 'uid-ada') or {}).get('outcome'), joins.REFUSED)


def test_reclaimed_player_rejoining(blob):
    print('\na player whose seat was reclaimed may come back to a fresh empire')
    wiped, planets, ships = wipe_civ.wipe(blob, 'BadGuy', log=lambda *a: None)
    check('the wipe left the OWNR shell, which is what K4 chose',
          'BadGuy' in [o['name'] for o in icv.owner_records(wiped)])
    check('holding no planets', [p for p in icv.planet_records(wiped)
                                 if p['owner'] == next(
                                     o['oid'] for o in icv.owner_records(wiped)
                                     if o['name'] == 'BadGuy')], [])

    store = fresh_store(wiped, roster=['DemoPlayer'])
    store.update_state({turn_store.RECLAIMED_KEY: {
        'BadGuy': {'turn': 9, 'missed': 12, 'at': time.time()}}})
    write_request(store, 'BadGuy', uid='uid-bad')
    before = store.turn_blob(TURN)
    new_turn = close_turn(store)
    after = store.turn_blob(new_turn)

    names = [o['name'] for o in icv.owner_records(after)]
    check('the name is in the galaxy exactly once', names.count('BadGuy'), 1)
    check('the old shell is still there under a vacated name',
          any(n.startswith('Vacated') for n in names))
    check('the counters agree', counters_agree(after))
    check('the blob still parses', parses(after))
    ids = {o['name']: o['oid'] for o in icv.owner_records(after)}
    was = {o['name']: o['oid'] for o in icv.owner_records(before)}
    check('the empire is a new one, not the wreck revived',
          ids['BadGuy'] != was['BadGuy'])
    check('and it owns one planet',
          len([p for p in icv.planet_records(after)
               if p['owner'] == ids['BadGuy']]), 1)
    check('DemoPlayer lost nothing',
          holdings(after)['DemoPlayer'][0], holdings(before)['DemoPlayer'][0])

    check('the seat is back on the roster', 'BadGuy' in store.civs())
    check('and the old reclaim record no longer answers for it',
          store.reclaimed('BadGuy'), None)

    import launcher
    check('so the launcher lets them in',
          launcher.roster_problem('BadGuy', store.civs(),
                                  store.reclaimed('BadGuy')), None)
    # The control: a name that really was reclaimed and has not rejoined is
    # still refused, and still told why.
    problem = launcher.roster_problem('Ghost', store.civs(),
                                      {'turn': 9, 'missed': 12})
    check('while a seat that was reclaimed and not rejoined still says so',
          bool(problem) and 'took the seat' in problem)


def test_closed_galaxy(blob):
    print('\na closed galaxy seats nobody and throws nothing away')
    store = fresh_store(blob)
    write_request(store, 'Ada', uid='uid-ada')
    store.close(reason='the operator called it')
    before = store.turn_blob(TURN)
    new_turn = close_turn(store)
    after = store.turn_blob(new_turn)
    check('the roster is unchanged', store.civs(), ROSTER)
    check('the galaxy is untouched', set_turn(after, TURN), before)
    check('the request is left where it is, because reopen exists',
          [r['name'] for r in joins.pending(store, log=lambda *a: None)],
          ['Ada'])
    check('and nothing was answered', answer(store, 'uid-ada'), None)

    print('  and a galaxy reopened afterwards grants it')
    store.reopen()
    grown = store.turn_blob(close_turn(store, blob_turn=TURN + 1))
    check('the seat is made', 'Ada' in store.civs())
    check('and the galaxy grew',
          len(icv.owner_records(grown)), len(icv.owner_records(blob)) + 1)


def test_a_broken_request_is_not_a_broken_turn(blob):
    print('\na request file that is not a request cannot stall the galaxy')
    store = fresh_store(blob)
    write_request(store, 'Ada', uid='uid-ada')
    where = joins.request_dir(store)
    with open(os.path.join(where, 'garbage.json'), 'w', encoding='utf-8') as f:
        f.write('{ this is not json')
    with open(os.path.join(where, 'nameless.json'), 'w', encoding='utf-8') as f:
        json.dump({'uid': 'x'}, f)
    new_turn = close_turn(store)
    check('the turn closed', new_turn, TURN + 1)
    check('the good request was still granted', 'Ada' in store.civs())
    check('the counters agree', counters_agree(store.turn_blob(new_turn)))


def test_order_against_abandonment(blob):
    print('\nabandonment runs first, so a join sees the room a reclaim made')
    # No free planet at all, and every planet a reclaim is about to hand back
    # belongs to the civ that is about to lose it. There is nowhere for a
    # newcomer to go unless the reclaim goes first.
    full = fill_every_planet(blob, 'BadGuy')
    check('the galaxy starts with no free planet at all',
          free_planets(full), [])
    doomed = set(holdings(full)['BadGuy'][0])

    # The galaxy starts a turn earlier and fills up, which is the shape a real
    # one reaches and the shape a reclaim needs: `wipe_civ` takes its blank
    # PLPR from an uncolonised planet, and a galaxy with none sends
    # `abandonment` back through earlier turns to find one.
    store = fresh_store(blob, turn=TURN - 1)
    store.publish(TURN, full)
    abandonment.set_thresholds(store, warn=1, reclaim=2)
    # An archive that says BadGuy has missed every turn, so the reclaim fires
    # in the same tick as the join.
    for t in range(TURN - 3, TURN):
        store.archive(t, {'turn': t, 'submitted': ['DemoPlayer'],
                          'missing': ['BadGuy']})
    write_request(store, 'Ada')
    new_turn = close_turn(store)
    after = store.turn_blob(new_turn)

    check('the seat was reclaimed', 'BadGuy' not in store.civs())
    check('and the newcomer was seated in the same tick', 'Ada' in store.civs())
    home = holdings(after).get('Ada', ([], [], b''))[0]
    check('the newcomer owns one planet', len(home), 1)
    check('and it is a planet the reclaim freed, which is the whole of why '
          'abandonment goes first', home and home[0] in doomed)
    check('the counters agree', counters_agree(after))
    check('the blob parses', parses(after))

    # What would have failed: the same galaxy with joins first. The order is
    # read out of the source rather than re-run, because running it the other
    # way means editing the referee.
    src = open(os.path.join(ROOT, 'server', 'referee.py'),
               encoding='utf-8').read()
    check('and the referee calls them in that order',
          src.index('abandonment.enforce(store') < src.index('joins.apply('))
    check('with both of them before the publish',
          src.index('joins.apply(') < src.index('store.publish(new_turn'))
    check('and the join commit after it',
          src.index('store.publish(new_turn') < src.index('joins.commit('))


def test_welcome_note_survives_the_next_turn(blob):
    print('\nthe welcome note is still there when the newcomer reads it')
    store = fresh_store(blob)
    write_request(store, 'Ada')
    new_turn = close_turn(store)
    welcome = store.note('Ada', new_turn)
    check('the note is written for the turn they will play', bool(welcome))

    # `player_turn.report_refusals` reads a turn's note only after the referee
    # has published past it, and the referee writes that turn's refusals on the
    # way. So the writer that decides whether the welcome survives is
    # `resolve_turn` itself, and the only way to ask is to close that turn with
    # something to refuse in it.
    #
    # Bytes that are not a save, which `read_submissions` drops with a note,
    # because it is the refusal that needs no legal orders to produce.
    with open(store.submission_path('Ada', new_turn), 'wb') as fh:
        fh.write(b'not a save')
    close_turn(store, blob_turn=new_turn)
    kept = store.note('Ada', new_turn)
    check('the referee refused something on that turn',
          any('DROPPED' in line for line in kept))
    check('and the welcome note is still in front of it',
          kept[:len(welcome)], welcome)

    # The control: the same refusal on a turn with no welcome note on it reads
    # exactly as it always did, so the change is an addition and not a note
    # that now carries somebody else's lines.
    plain = fresh_store(blob)
    with open(plain.submission_path('DemoPlayer', TURN), 'wb') as fh:
        fh.write(b'not a save')
    close_turn(plain)
    check('a refusal on a turn with nothing else on it is just the refusal',
          all('DROPPED' in line for line in plain.note('DemoPlayer', TURN)))


def main():
    if not os.path.exists(GALAXY):
        print(f'no fixture at {GALAXY}')
        return 2
    blob = set_turn(sp.load_any(GALAXY), TURN)
    names = [o['name'] for o in icv.owner_records(blob)]
    if sorted(names) != sorted(ROSTER):
        print(f'the fixture holds {names}, not {ROSTER}')
        return 2
    print(f'{os.path.basename(GALAXY)}: {len(blob):,} bytes, civs {names}, '
          f'{len(free_planets(blob))} free planet(s)')

    tests = [test_shape, test_the_launcher_asks_the_store,
             test_a_join_lands, test_nothing_waiting_changes_nothing,
             test_no_room, test_margin, test_name_already_seated,
             test_two_in_one_turn, test_a_worker_killed_before_commit,
             test_asked_twice_across_turns,
             test_reclaimed_player_rejoining, test_closed_galaxy,
             test_a_broken_request_is_not_a_broken_turn,
             test_order_against_abandonment,
             test_welcome_note_survives_the_next_turn]
    made = []
    try:
        for fn in tests:
            fn(blob)
    finally:
        for root in made:
            shutil.rmtree(root, ignore_errors=True)
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    for name in FAIL:
        print(f'  failed: {name}')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
