"""
joins.py: the seat a stranger asked for

    import joins

    blob, done = joins.apply(store, turn, new_turn, blob, log=log)  # the referee
    store.publish(new_turn, blob)
    joins.commit(store, turn, new_turn, done, log=log)

    python joins.py ../uidemo/sandbox          # what is waiting, changing nothing

At a turn boundary the referee adds each requested civ to the blob the tick
produced and seats the player on the roster, so a player who asks during turn N
plays at turn N+1. The launcher writes the request, a JSON object:

    {"name": "Ada", "uid": null, "build": "0.1.0+dev",
     "requested_at": 1790486818.25, "requested_turn": 11}

Requests and answers are kept by the store (`store.join_requests()`,
`store.answer_join()`); a folder holds them at `<store>/joins/<uid or name>.json`
and `joins/done/`. An answer holds the request, the outcome, and for a granted
one the planet and system. Answers are read back by key, never listed.

`resolve_turn` runs abandonment before joins, so a join sees the planets a
reclaim freed.

`apply` changes only bytes. `commit` runs after `publish` and does every
durable write, so a referee killed between them leaves an empire with no roster
entry, which the next boundary refuses and an operator can settle. The seat map
(`seats`, `{uid: civ}`, Firebase galaxies only) is written in the same
`update_state` merge as the roster.

One request is never granted twice: an answered request is consumed, a name on
the roster is refused, and `inject_civ.add_civ` refuses a name already in the
blob. Every call into the injection is guarded, and a refused join leaves the
blob unchanged; the player is told through `put_note`.
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
for _d in (HERE, os.path.join(HERE, 'dev_tools'),
           os.path.join(os.path.dirname(HERE), 'client', 'dev_tools')):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import inject_civ as icv
import turn_store

# Where a request and its answer sit in a galaxy that is a folder.
JOIN_DIR = turn_store.JOIN_DIR
DONE_DIR = turn_store.JOIN_DONE_DIR

# The state key recording each granted seat (turn, uid, build, planet, system).
JOINED_KEY = turn_store.JOINED_KEY

NAME_LIMIT = 15   # the engine's civ name buffer, in characters

# A reclaimed civ's `OWNR` shell stays in the blob. When its player rejoins under
# the same name, the shell is renamed to this.
VACATED = 'Vacated {oid}'

# The engine's name for a system nobody has named.
UNNAMED = 'Unnamed'

GRANTED, REFUSED = 'granted', 'refused'

# The relay's seat map on a Firebase galaxy document, `{uid: civ}`. Not a state
# field: `FirebaseTurnStore.state` leaves it out and the relay never serves it.
SEATS_KEY = 'seats'

# The characters of a Firebase uid (as `seat_tool.UID_RE`). A uid is a map key
# under `seats`, so anything else could write outside the map.
UID_CHARS = frozenset('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
                      '0123456789')


def request_dir(store) -> str:
    """Where this galaxy's join requests are, or '' when it is not a folder."""
    root = getattr(store, 'root', None)
    return os.path.join(root, JOIN_DIR) if isinstance(root, str) else ''


def pending(store, log=print) -> list:
    """Every join request waiting on this galaxy, oldest first. Each carries
    `key`, the uid or else the name, which the answer is filed under."""
    return list(store.join_requests(log=log) or [])


def _civ(blob, name: str):
    return next((o for o in icv.owner_records(blob) if o['name'] == name), None)


def _holdings(blob, oid: int):
    """(planet ids, ship ids) one civ holds. A reclaimed shell holds neither."""
    import inject_ship as ish
    return ([p['id'] for p in icv.planet_records(blob) if p['owner'] == oid],
            [s['id'] for s in ish.ship_records(blob) if s['owner'] == oid])


def refusal(name: str, roster, blob) -> str:
    """Why this name cannot be seated in this galaxy, or ''.

    Only reasons about the name; whether there is room is the injection's. A
    name already on the roster is refused, never merged into that seat.
    """
    if len(name) > NAME_LIMIT:
        return (f'The name {name!r} is longer than the {NAME_LIMIT} characters '
                f'this game allows, so no seat was made. Join again with a '
                f'shorter name.')
    problem = turn_store.name_problem(name)
    if problem:
        # Refused here so it is answered once, not failed in the injection each tick.
        return (f'The name {name!r} cannot be used: {problem}. No seat was '
                f'made. Join again with another name.')
    if any(c == name for c in roster):
        return (f'A player called {name!r} is already in this galaxy, so no '
                f'seat was made for you. Join again with a different name.')
    if any(str(c).lower() == name.lower() for c in roster):
        return (f'This galaxy already has a seat spelled the same way as '
                f'{name!r} apart from its capitals, and names are matched '
                f'exactly. Join again with a different name.')
    return ''


def vacated_name(blob, oid: int) -> str:
    """A name for a wiped civ's shell that nothing else in the galaxy holds."""
    taken = {o['name'] for o in icv.owner_records(blob)}
    base = VACATED.format(oid=oid)[:NAME_LIMIT]
    if base not in taken:
        return base
    for n in range(2, 100):
        tail = str(n)
        cand = base[:NAME_LIMIT - len(tail) - 1] + '.' + tail
        if cand not in taken:
            return cand
    raise ValueError('no free name for the shell')


def _vacate(blob, name: str, oid: int, log=print):
    """Rename a wiped civ's shell so the player who lost it can use the name.
    The shell keeps its object id and every reference to it."""
    import make_multiplayer_galaxy as mmg
    fresh = vacated_name(blob, oid)
    out = mmg.rename_civ(blob, name, fresh, log=lambda *a: None)
    log(f'  joins: the wiped shell of {name!r} (object {oid}) is now '
        f'{fresh!r}, so the name is free again')
    return out


def forget_inherited_map(blob, name: str, log=print):
    """Empty a newly added civ's `EXSY`, the explored map it inherited from its
    donor's cloned `OWNR`. Only this civ's table is touched; the engine writes
    the row for its own system on load.
    """
    import save_parser as sp
    import exsy

    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    at = {r['off']: r['name'] for r in icv.owner_records(blob)}
    for ownr in glxy.find('OWNR'):
        if at.get(ownr.start) != name:
            continue
        sec = next(ownr.find('EXSY'), None)
        if sec is None:
            return blob
        try:
            known = exsy.systems_known(bytes(blob[sec.payload:sec.end]))
        except Exception as exc:                            # noqa: BLE001
            log(f'  joins: {name}: EXSY unreadable, left alone ({exc})')
            return blob
        if not known:
            return blob
        out = sp.replace_payload(blob, tree, sec, exsy.build([]))
        log(f'  joins: {name} forgot {len(known)} system(s) inherited from the '
            f'donor')
        return out
    return blob


def home_system(blob, planet_id: int):
    """(sun object id, label) for the planet a newcomer was given. An unnamed
    system is labelled 'an unnamed system'."""
    import merge_orders as mo
    for sys_rec in icv.system_records(blob):
        if planet_id not in sys_rec['planets']:
            continue
        named = mo.systems(blob).get(sys_rec['id'])
        name = ''
        if named and named[2]:
            raw = named[2][1]
            name = raw.decode('ascii', 'replace') if isinstance(raw, bytes) \
                else str(raw)
        if not name or name == UNNAMED:
            return sys_rec['id'], 'an unnamed system'
        return sys_rec['id'], name
    return None, ''


def welcome_note(name: str, turn: int, system: str, planet_id: int) -> list:
    """What a player who has just been seated is told, including their system."""
    return [
        f'You joined this galaxy as {name} at turn {turn}.',
        f'Your homeworld is {name}\'s HQ, planet #{planet_id}, in {system}.',
        'It is the only thing you own. Everything else is yours to build.',
    ]


def refusal_note(name: str, reason: str) -> list:
    """What a player whose join could not be granted is told."""
    return [f'Your request to join this galaxy as {name} was not granted.',
            reason]


def donor_template(store, blob, before: int, log=print):
    """An earlier turn of this galaxy to clone a newcomer from, or None.

    Needed only when seat one of `blob` holds no planet or no ship is left (see
    `inject_civ.pick_donor` and `inject_ship.add_ship`). The earliest turn is
    tried first, and a turn that cannot be read is skipped.
    """
    import abandonment
    import inject_ship as ish
    owners = icv.owner_records(blob)
    if not owners:
        return None
    one = icv.seat_one(owners)['oid']
    if (any(p['owner'] == one for p in icv.planet_records(blob))
            and ish.ship_records(blob)):
        return None
    for turn in reversed(abandonment.candidate_turns(before)):
        try:
            if not store.has_turn(turn):
                continue
            old = store.turn_blob(turn)
        except (OSError, ValueError):
            continue
        then = icv.seat_one(icv.owner_records(old))['oid']
        if (any(p['owner'] == then for p in icv.planet_records(old))
                and ish.ship_records(old)):
            log(f'  joins: seat one holds no planet or no ship is left; '
                f'newcomers are cloned from turn {turn}')
            return old
    log('  joins: seat one holds no planet or no ship is left, and no '
        'earlier turn has both')
    return None


def _grant(store, blob, req: dict, roster, turn: int, log=print,
           template=None):
    """One request against one blob. Returns (blob, outcome).

    Nothing raises out of here. The injection refuses with `SystemExit`, which
    inside a tick would end the turn for every player.
    """
    name = req['name'].strip()
    out = {'key': req.get('key'), 'name': name, 'uid': req.get('uid'),
           'build': req.get('build'), 'requested_at': req.get('requested_at'),
           'requested_turn': req.get('requested_turn'), 'turn': int(turn)}

    why = refusal(name, roster, blob)
    if why:
        out.update(outcome=REFUSED, reason=why)
        return blob, out

    standing = _civ(blob, name)
    if standing is not None:
        planets, ships = _holdings(blob, standing['oid'])
        if planets or ships:
            # An empire under this name is on the board with nobody seated on it.
            # The asker is refused and the operator resolves it.
            out.update(outcome=REFUSED,
                       reason=(f'This galaxy already holds an empire called '
                               f'{name!r} that is not on its roster, so no '
                               f'seat was made. Ask whoever runs the galaxy, '
                               f'or join again with a different name.'))
            log(f'  joins: REFUSED {name}, the galaxy holds a civ of that '
                f'name holding {len(planets)} planet(s) and {len(ships)} '
                f'ship(s) and the roster does not name it; a roster and a '
                f'galaxy that disagree are for the operator to settle')
            return blob, out
        try:
            blob = _vacate(blob, name, standing['oid'], log=log)
        except (SystemExit, Exception) as exc:              # noqa: BLE001
            out.update(outcome=REFUSED,
                       reason=(f'This galaxy still holds a civ called {name!r} '
                               f'that could not be set aside, so no seat was '
                               f'made. Join again with a different name.'))
            log(f'  joins: could not vacate the shell of {name}, '
                f'{type(exc).__name__}: {exc}')
            return blob, out

    try:
        joined, planet = icv.add_civ(blob, name, template=template,
                                     log=lambda *a: None)
    except (SystemExit, Exception) as exc:                  # noqa: BLE001
        out.update(outcome=REFUSED,
                   reason=(f'This galaxy had no room to put a new empire in: '
                           f'{exc}. Nothing was changed. Try another galaxy, '
                           f'or ask whoever runs this one.'))
        log(f'  joins: {name} could not be added, {type(exc).__name__}: {exc}')
        return blob, out

    try:
        joined = forget_inherited_map(joined, name, log=log)
    except Exception as exc:                                # noqa: BLE001
        # The join stands; the failure is logged.
        log(f'  joins: {name} kept the donor\'s explored map, '
            f'{type(exc).__name__}: {exc}')

    sun, system = home_system(joined, planet)
    out.update(outcome=GRANTED, reason='', planet=planet, sun=sun,
               system=system)
    log(f'  joins: seated {name} on planet #{planet} in {system}')
    return joined, out


def apply(store, turn: int, new_turn: int, blob: bytes, log=print):
    """Grant every join waiting on this galaxy.

    Called with the turn just closed, the turn about to be published, and the
    blob the tick produced for it, before `publish`. Returns `(blob, outcomes)`
    for `commit` once the blob is published. Nothing durable is written here.
    A closed galaxy grants nothing and leaves its requests waiting.
    """
    if store.is_closed():
        if pending(store, log=lambda *a: None):
            log('  joins: this galaxy is closed, so nothing is being seated; '
                'the requests are left where they are')
        return blob, []

    waiting = pending(store, log=log)
    if not waiting:
        return blob, []

    roster = list(store.civs())
    log(f'  joins: {len(waiting)} request(s) waiting on turn {turn}')
    template = donor_template(store, blob, turn, log=log)
    outcomes = []
    for req in waiting:
        blob, out = _grant(store, blob, req, roster, new_turn, log=log,
                           template=template)
        if out['outcome'] == GRANTED:
            # So a second request for the same name this turn is refused.
            roster.append(out['name'])
        outcomes.append(out)
    return blob, outcomes


def is_uid(uid) -> bool:
    return isinstance(uid, str) and 0 < len(uid) <= 128 and \
        set(uid) <= UID_CHARS


def seat_map(store):
    """`{uid: civ}` for a store that carries seats (a `FirebaseTurnStore`), or
    None. `firebase_store` is looked up in `sys.modules` so it is not imported
    for folder galaxies.
    """
    fb = sys.modules.get('firebase_store')
    if fb is None or not isinstance(store, fb.FirebaseTurnStore):
        return None
    snap = store.doc.get()
    return dict(((snap.to_dict() or {}) if snap.exists else {})
                .get(SEATS_KEY) or {})


def seat_changes(seats: dict, roster, seated, log=print):
    """(bind, drop) for the joins being committed. Pure.

    `seats` is the seat map as it stands, `roster` the roster before this
    commit, and `seated` the granted outcomes. `bind` is `{uid: civ}` to write
    and `drop` the uids whose entry is removed.

      * a uid that already holds a civ still on the roster keeps it, and the
        new civ is left unseated for `seat_tool.py bind`.
      * a uid holding a civ no longer on the roster (reclaimed) is moved to the
        new civ.
      * any other uid still holding the new civ's name loses that entry.

    An outcome without a valid Firebase uid has no seat to bind.
    """
    live = set(roster)
    bind, drop = {}, set()
    for out in seated:
        uid, civ = out.get('uid'), out['name']
        if not is_uid(uid):
            continue
        if uid in bind:
            log(f'  joins: {uid} was granted {bind[uid]} and {civ} at one '
                f'boundary; {civ} is left unseated for the operator')
            continue
        held = seats.get(uid)
        if held == civ:
            continue
        if held is not None and held in live:
            log(f'  joins: {civ} is left unseated: the sign-in that asked for '
                f'it already plays {held}, and a seat is not moved off a civ '
                f'on the roster. `seat_tool.py bind` seats it')
            continue
        bind[uid] = civ
        for other, name in seats.items():
            if name == civ and other != uid:
                drop.add(other)
        live.add(civ)
    return bind, sorted(drop - set(bind))


def seat_fields(bind: dict, drop) -> dict:
    """The `seats` merge for `update_state`, with each dropped uid deleted.

    A nested map, because `set(..., merge=True)` merges it entry by entry and
    does not read dots as paths.
    """
    from google.cloud import firestore
    fields = dict(bind)
    for uid in drop:
        fields[uid] = firestore.DELETE_FIELD
    return {SEATS_KEY: fields}


def append_note(store, civ: str, turn: int, lines, log=print) -> None:
    """Append to the civ's note for that turn (`put_note` replaces). Raises on
    failure."""
    store.put_note(civ, turn, list(store.note(civ, turn)) + list(lines))


def _note_or_log(store, civ: str, turn: int, lines, log=print) -> None:
    """`append_note`, logging a failure instead of raising."""
    try:
        append_note(store, civ, turn, lines, log=log)
    except OSError as exc:
        log(f'  joins: could not leave {civ} a note about turn {turn}, {exc}')


def _file_answer(store, out: dict, log=print) -> None:
    """Consume the request this outcome answers, and record the answer.
    A failure is logged, not raised, since the seat is already published."""
    key = out.get('key')
    if not key:
        return
    answer = dict(out)
    answer['answered_at'] = time.time()
    try:
        store.answer_join(key, answer)
    except Exception as exc:                                # noqa: BLE001
        log(f'  joins: could not file the answer for {out["name"]}, {exc}; '
            f'the request stays and will be refused as a name already in the '
            f'galaxy next turn')


def commit(store, turn: int, new_turn: int, outcomes, log=print) -> list:
    """Seat the players `apply` placed, and answer the ones it refused.

    Must run after the blob holding the new civs is published. Returns the
    outcomes it acted on.
    """
    if not outcomes:
        return []
    seated = [o for o in outcomes if o['outcome'] == GRANTED]
    if seated:
        state = store.state()
        roster = list(state.get('civs', []))
        record = dict(state.get(JOINED_KEY) or {})
        taken = dict(store.reclaimed() or {})
        for out in seated:
            if out['name'] not in roster:
                roster.append(out['name'])
            record[out['name']] = {
                'turn': int(new_turn), 'uid': out.get('uid'),
                'build': out.get('build'), 'planet': out.get('planet'),
                'system': out.get('system'), 'at': time.time()}
            # A returning reclaimed player's old reclaim record is dropped.
            taken.pop(out['name'], None)
        fields = {'civs': roster, JOINED_KEY: record}
        if taken != (store.reclaimed() or {}):
            fields[turn_store.RECLAIMED_KEY] = taken
        # Seats go in the same write as the roster.
        seats = seat_map(store)
        bind, drop = ({}, []) if seats is None else seat_changes(
            seats, state.get('civs', []), seated, log=log)
        if bind or drop:
            fields.update(seat_fields(bind, drop))
        store.update_state(fields)
        log(f'  joins: roster is now {roster}')
        for uid, civ in bind.items():
            log(f'  joins: seated {civ} on sign-in {uid}')
        for uid in drop:
            log(f'  joins: sign-in {uid} no longer holds {seats.get(uid)}, a '
                f'seat left from before that name was reclaimed')

    for out in outcomes:
        if out['outcome'] == GRANTED:
            # On the turn about to be played, the first one the newcomer plays.
            _note_or_log(store, out['name'], new_turn,
                         welcome_note(out['name'], new_turn,
                                      out.get('system') or 'your home system',
                                      out.get('planet')), log=log)
        else:
            # On the turn the refused player asked during.
            _note_or_log(store, out['name'], turn,
                         refusal_note(out['name'], out['reason']), log=log)
        _file_answer(store, out, log=log)
    return list(outcomes)


def review(store, log=print) -> list:
    """What the next boundary would do with what is waiting, changing nothing.

    Reasons about the current turn's blob, so a reported placement can differ
    from the one the next tick makes.
    """
    waiting = pending(store, log=log)
    if not waiting:
        return []
    if store.is_closed():
        return [dict(r, outcome=REFUSED,
                     reason='this galaxy is closed; the request is left alone')
                for r in waiting]
    blob = store.turn_blob(store.state()['turn'])
    roster = list(store.civs())
    out = []
    for req in waiting:
        blob, got = _grant(store, blob, req, roster, store.state()['turn'] + 1,
                           log=log)
        if got['outcome'] == GRANTED:
            roster.append(got['name'])
        out.append(got)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('store', help='a galaxy: a directory, a URL or a '
                                  'firebase:// spec')
    a = ap.parse_args()

    store = turn_store.open_store(a.store)
    if not store.exists():
        raise SystemExit(f'no galaxy in {a.store}')
    state = store.state()
    print(f'{a.store}: turn {state["turn"]}, status {store.status()}, '
          f'{len(store.civs())} seat(s)')
    rows = review(store)
    if not rows:
        print('  no join requests waiting')
        return 0
    for row in rows:
        where = (f'planet #{row.get("planet")} in {row.get("system")}'
                 if row['outcome'] == GRANTED else row['reason'])
        print(f'  {row["name"]:<20} {row["outcome"]:<8} {where}')
    print('\nnothing was changed; the referee applies these at the next turn '
          'boundary')
    return 0


if __name__ == '__main__':
    sys.exit(main())
