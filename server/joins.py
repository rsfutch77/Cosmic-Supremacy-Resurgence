"""
joins.py , the seat a stranger asked for
=========================================
    import joins

    blob, done = joins.apply(store, turn, new_turn, blob, log=log)  # the referee
    store.publish(new_turn, blob)
    joins.commit(store, turn, new_turn, done, log=log)

    python joins.py ../uidemo/sandbox          # what is waiting, changing nothing

The launcher writes a join request and stops. This is the other end: the worker
reads the request at a turn boundary, adds the civ to the blob the tick
produced, and seats the player on the roster. A player who asks during turn N is
playing at turn N+1, which is J3, and the whole reason it happens here rather
than in the launcher is that the galaxy belongs to the referee. A join applied
to a copy a player holds is a join that lasts until the next turn is published
over it.

The request, and who owns its shape
------------------------------------
`release/launcher.py` writes it and this reads it, so the shape is a contract
between two files that are owned by different people. It is a JSON object:

    {"name": "Ada", "uid": null, "build": "0.1.0+dev",
     "requested_at": 1790486818.25, "requested_turn": 11}

Unchanged from what the launcher already writes, which is the whole of why it
is unchanged: two live galaxies in `server/uidemo` hold requests in that form,
written by the launcher's own button, and a shape agreed by changing the file
that produces it is a shape nobody can test today.

**Where it is kept is the store's and not this module's.** A folder holds it at
`<store>/joins/<uid or name>.json` and the answer at `joins/done/`, which is
what J3 shipped; a Firebase galaxy holds both as documents under the galaxy,
and a LAN service passes them through to the folder behind it. This module asks
`store.join_requests()` and `store.answer_join()` and never learns which it is
talking to, which is the same seam every other galaxy state crosses. J3 read
the folder itself, so the beta galaxy, which is Firebase, could not take a join
at all.

The answer, which the launcher reads to tell a refused player why, holds the
request it answers, the outcome, and for a granted one the planet and system
the player landed in.
It is read back by key, never listed: a list of who is trying to join a galaxy
is the enumeration F4 and J4 both refuse to hand a launcher.

Why the order in `resolve_turn` is abandonment first
-----------------------------------------------------
Both run on the blob the tick produced, before it is published. Abandonment
goes first for two reasons and neither is preference. A reclaim frees planets
and a name, so a join in the same tick sees the room the reclaim made, which is
the state a full sandbox is actually in. And a wipe needs an uncolonised planet
to take a blank `PLPR` from, which is the one resource a join consumes, so
joining first can push a reclaim into its slow archive-walking fallback or
leave a seat held that should have gone. The reverse order buys nothing: a civ
seated this turn has a missed streak of one and cannot be warned or reclaimed
by the same tick.

Why the roster is written after the blob is published
------------------------------------------------------
The two have to agree or the galaxy is broken in one of two ways, and they are
not equally bad. A civ in the roster and not in the galaxy is a player the
launcher admits and whose every submission the referee then drops as "not a civ
in this galaxy", which reads to them as the game refusing their orders, turn
after turn, with nothing to fix. A civ in the galaxy and not in the roster is a
player the launcher turns away, once, with a refusal an operator can answer by
adding the name.

So `apply` only changes bytes, `commit` runs after `publish` and does every
durable write, and a referee killed between them leaves the recoverable half of
that pair. It is not self-repairing and is deliberately not: the next boundary
refuses the still-waiting request rather than seating whoever asked, because a
rule that seats a name already in the galaxy hands an empire to anybody who
types it in a galaxy whose roster was ever edited by hand.

Applying one join twice is the failure that matters
----------------------------------------------------
It is a second empire for one player, and unlike a lost join it cannot be
noticed by the player it happened to. Three things stop it and they are
deliberately independent. An answered request is consumed, which on a folder is
a move into `joins/done/` and in Firebase is a delete batched with the answer.
A name already on the roster is refused. And `inject_civ.add_civ` refuses a
name already in the blob, which is the check that still holds when the other
two have been defeated by a store restored from a backup.

The third is what carries a worker killed between `publish` and `commit`. The
request is still waiting, the roster does not name the seat, and the galaxy
does hold the empire, so the next boundary refuses the request for a civ of
that name standing on the board that no roster claims, and says in the log that
a roster and a galaxy which disagree are the operator's to settle. One empire,
not two.

A join that cannot be granted changes nothing
----------------------------------------------
`pick_homeworld` raises `SystemExit` for a galaxy with no free planet and again
for one whose furthest free planet is inside the crowding margin. Inside a tick
that ends the turn for everybody, which is the class of failure N2 exists to
stop, so every call into the injection is guarded and a refusal leaves the blob
exactly as it arrived. The player is told through `put_note`, the same path a
refused order uses.
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

# Where a request and its answer sit in a galaxy that is a folder, for the
# operator tools that read the files themselves. Taken from the store rather
# than spelled again here, because the store is what writes them and two
# spellings of one path is a way for the two to disagree.
JOIN_DIR = turn_store.JOIN_DIR
DONE_DIR = turn_store.JOIN_DONE_DIR

# What a seat granted here is recorded as, in the galaxy's own state, beside
# `reclaimed`. It is a record and not an enforcement: J4 binds a seat to the
# uid that claimed it, and the binding cannot be checked by anything until
# something writes it down. This is the thing that writes it down. The name is
# the store's, for the reason the two above are: a field a store does not know
# about is a field one of them silently drops.
JOINED_KEY = turn_store.JOINED_KEY

# The engine's name buffer, which `add_civ` also refuses past. Checked here as
# well so a request that is too long is answered with a sentence rather than
# with the injection's `SystemExit`.
NAME_LIMIT = 15

# What a wiped civ's shell is renamed to when the player who lost it rejoins
# under the same name. A reclaim keeps the `OWNR` , that is K4's shell, and it
# is kept because deleting it creates a dangling reference , so the name is
# still in the galaxy and `add_civ` would refuse it. The shell is not the
# player's empire any more and the name is theirs, so the shell gives it up.
VACATED = 'Vacated {oid}'

# What the engine calls a system nobody has named yet, which in a young galaxy
# is most of them. See home_system.
UNNAMED = 'Unnamed'

GRANTED, REFUSED = 'granted', 'refused'


# ── reading what is waiting ──────────────────────────────────────────────────
def request_dir(store) -> str:
    """Where this galaxy's join requests are, or '' when it is not a folder.

    For an operator and for the acceptance tools, which read the files
    themselves. Nothing in this module reaches a request this way any more:
    every store answers `join_requests` and `answer_join`, so a galaxy in
    Firebase takes a join the same way a folder does and this module cannot
    tell which it is holding.
    """
    root = getattr(store, 'root', None)
    return os.path.join(root, JOIN_DIR) if isinstance(root, str) else ''


def pending(store, log=print) -> list:
    """Every join request waiting on this galaxy, oldest first.

    The store's own answer, whichever store it is. Ordered by when the player
    asked, so two requests that can only both be granted if there is room for
    both are settled by who asked first rather than by how a listing happened
    to come back. The key is what the request is filed under, the uid when the
    player has one and their name when they do not, and it is what the answer
    is filed under in turn.
    """
    return list(store.join_requests(log=log) or [])


# ── deciding ─────────────────────────────────────────────────────────────────
def _civ(blob, name: str):
    return next((o for o in icv.owner_records(blob) if o['name'] == name), None)


def _holdings(blob, oid: int):
    """(planet ids, ship ids) one civ holds.

    Both halves, because the question they answer is whether anything of this
    civ is still on the board. A reclaim leaves the `OWNR` shell and takes
    every planet and every ship, which is K4's whole design, so a civ with
    neither is wreckage and a civ with either is somebody's.
    """
    import inject_ship as ish
    return ([p['id'] for p in icv.planet_records(blob) if p['owner'] == oid],
            [s['id'] for s in ish.ship_records(blob) if s['owner'] == oid])


def refusal(name: str, roster, blob) -> str:
    """Why this name cannot be seated in this galaxy, or ''.

    Only the reasons that are about the name. Whether the galaxy has room is
    the injection's to answer, because the answer is the placement and asking
    it twice would be two policies that can disagree.

    A clash with a name already on the roster is refused rather than merged
    into that seat. The launcher refuses it at the button, so a request that
    gets this far is one where the seat was taken between the click and the
    boundary, and handing the second player the first player's empire is the
    one outcome worse than telling them to pick another name.
    """
    if len(name) > NAME_LIMIT:
        return (f'The name {name!r} is longer than the {NAME_LIMIT} characters '
                f'this game allows, so no seat was made. Join again with a '
                f'shorter name.')
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

    `make_multiplayer_galaxy.rename_civ` is the operation, and it is the same
    one that file already performs on a live blob when a wanted name is held by
    a civ about to give it up. The shell keeps its object id, its dead row on
    the diplomacy list and every reference to it, so nothing dangles; all it
    loses is the name, which belongs to the player and not to the wreckage.
    """
    import make_multiplayer_galaxy as mmg
    fresh = vacated_name(blob, oid)
    out = mmg.rename_civ(blob, name, fresh, log=lambda *a: None)
    log(f'  joins: the wiped shell of {name!r} (object {oid}) is now '
        f'{fresh!r}, so the name is free again')
    return out


def forget_inherited_map(blob, name: str, log=print):
    """Empty a newly added civ's `EXSY`, which it inherited from its donor.

    `inject_civ` clones a donor's `OWNR` whole, and `EXSY` is the explored map
    inside it, so a civ added off seat one starts knowing every system seat one
    has entered. In a generated galaxy that is one row and
    `make_multiplayer_galaxy.reset_exsy` clears it for everybody at creation.
    In a galaxy fifty turns old it is the donor's whole map, including where
    people live, handed to a stranger who has not left home, and the donor is a
    player who is still in the game.

    Only this civ's table is touched. `reset_exsy` clears every civ's, which is
    right at creation and would take every incumbent's map here.

    Emptying rather than pruning, because a civ that has just been created has
    entered nothing: the engine writes the row for its own system on load,
    measured at generation and the reason that file empties the table too.
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
    """(sun object id, what to call it) for the planet a newcomer was given.

    A system name is what a player is told, because "you are in Tau Ceti" is
    something they can find on their own map and a planet object id is not. In
    a galaxy where nobody has named anything the name is the engine's
    placeholder, and a player told they are in "Unnamed" has been told nothing,
    so that case says so plainly. The planet name in the same sentence is the
    findable half either way.

    The label rather than the raw name, because the note and the seat record
    both take it from here and a player reading one and an operator reading the
    other should not be given two different answers.
    """
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
    """What a player who has just been seated is told.

    The system is the point of it. J3 asks that a newcomer is told where they
    landed rather than having to find their own homeworld on a map of a galaxy
    they have never seen, and the system name is the thing the map is labelled
    with.
    """
    return [
        f'You joined this galaxy as {name} at turn {turn}.',
        f'Your homeworld is {name}\'s HQ, planet #{planet_id}, in {system}.',
        'It is the only thing you own. Everything else is yours to build.',
    ]


def refusal_note(name: str, reason: str) -> list:
    """What a player whose join could not be granted is told."""
    return [f'Your request to join this galaxy as {name} was not granted.',
            reason]


# ── applying ─────────────────────────────────────────────────────────────────
def _grant(store, blob, req: dict, roster, turn: int, log=print):
    """One request against one blob. Returns (blob, outcome).

    Nothing raises out of here. Every way the injection can refuse is a
    `SystemExit` , no free planet, nothing outside the crowding margin, a name
    the blob already holds , and a `SystemExit` inside a tick ends the turn for
    every player in the galaxy, which is exactly what N2 was written to stop.
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
            # An empire under this name is on the board and nobody is seated on
            # it. That is not a case to guess at: seating the asker would hand
            # a stranger whatever is there on the strength of a name they
            # typed, and it is reachable without any fault of the referee's,
            # because a roster edited by hand is all it takes. It is the
            # operator's to resolve, and the log says so in the one place they
            # will look.
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
        joined, planet = icv.add_civ(blob, name, log=lambda *a: None)
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
        # A newcomer keeping the donor's map is a cheat and is not worth
        # losing the seat over, so this is reported and the join stands.
        log(f'  joins: {name} kept the donor\'s explored map, '
            f'{type(exc).__name__}: {exc}')

    sun, system = home_system(joined, planet)
    out.update(outcome=GRANTED, reason='', planet=planet, sun=sun,
               system=system)
    log(f'  joins: seated {name} on planet #{planet} in {system}')
    return joined, out


def apply(store, turn: int, new_turn: int, blob: bytes, log=print):
    """Grant every join waiting on this galaxy. The referee's seam.

    Called with the turn that has just been closed, the turn about to be
    published, and the blob the tick produced for it, before `publish`. Returns
    `(blob, outcomes)`, and the caller passes the outcomes to `commit` once the
    blob is published. Nothing durable is written here.

    A closed galaxy grants nothing and consumes nothing, which is what
    `abandonment.enforce` does with the same state and for the same reason: a
    galaxy the operator has ended asks nothing of anybody. Leaving the requests
    where they are rather than refusing them is deliberate, because `reopen`
    exists and a request thrown away is a join a reopened galaxy would have
    granted. The player is not left guessing either way: their launcher reads
    the galaxy's status and says it has ended before it says anything else.
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
    outcomes = []
    for req in waiting:
        blob, out = _grant(store, blob, req, roster, new_turn, log=log)
        if out['outcome'] == GRANTED:
            # Added to the roster this pass and not only in `commit`, so a
            # second request for the same name inside one turn is refused by
            # the same rule that refuses one arriving a turn late.
            roster.append(out['name'])
        outcomes.append(out)
    return blob, outcomes


# ── committing ───────────────────────────────────────────────────────────────
def append_note(store, civ: str, turn: int, lines, log=print) -> None:
    """Add to whatever note that civ already has for that turn.

    `put_note` replaces, and two writers reach one turn's note. A welcome note
    is written for the turn the newcomer is about to play, and the referee
    writes that turn's refusals before the newcomer's launcher reads either, so
    a replace by whichever of them goes second loses the other.

    Failures are the caller's, because the two callers want different things
    from one: the referee already has a line for a note it could not write, and
    this module has its own below.
    """
    store.put_note(civ, turn, list(store.note(civ, turn)) + list(lines))


def _note_or_log(store, civ: str, turn: int, lines, log=print) -> None:
    """The same, with a note that cannot be written not worth a turn."""
    try:
        append_note(store, civ, turn, lines, log=log)
    except OSError as exc:
        log(f'  joins: could not leave {civ} a note about turn {turn}, {exc}')


def _file_answer(store, out: dict, log=print) -> None:
    """Consume the request this outcome answers, and record the answer.

    Consuming it is what stops one request being granted twice. It is not the
    only thing that stops it , the roster check and `add_civ`'s own name check
    both do , and that is the point: this one can fail, on a file another
    process has open or on a network that is down, and the other two still
    hold.

    The store decides how. A folder writes the answer into `joins/done/` and
    removes the request; Firebase deletes the request and writes the answer in
    one batch, so there is no window at all. A failure here is reported rather
    than raised, because the seat has already been published and losing the
    turn over the paperwork would be the worse outcome.
    """
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

    Every durable write is here and every one of them happens after the blob
    holding the new civs has been published, so a referee that dies in the
    middle leaves an empire nobody is seated on, which an operator can answer,
    rather than a roster that lies about what the galaxy holds, which nobody
    can.

    Returns the outcomes it acted on, so a caller can archive them.
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
            # A player who was reclaimed and has come back holds a new empire,
            # so the record of the old one stops being the answer to "what
            # happened to my seat". Left in place it would be read as the
            # current state of a name that is on the roster again.
            taken.pop(out['name'], None)
        fields = {'civs': roster, JOINED_KEY: record}
        if taken != (store.reclaimed() or {}):
            fields[turn_store.RECLAIMED_KEY] = taken
        store.update_state(fields)
        log(f'  joins: roster is now {roster}')

    for out in outcomes:
        if out['outcome'] == GRANTED:
            # The note goes on the turn about to be played rather than the one
            # just closed, because `player_turn.report_refusals` reads the note
            # for a turn the player played and a newcomer played neither the
            # closed turn nor any before it.
            _note_or_log(store, out['name'], new_turn,
                         welcome_note(out['name'], new_turn,
                                      out.get('system') or 'your home system',
                                      out.get('planet')), log=log)
        else:
            # A refused player is on no roster and will play no turn, so the
            # turn this lands on is the one they asked during. It is on disk
            # for the operator either way; see the report for what does not
            # yet read it back to the player.
            _note_or_log(store, out['name'], turn,
                         refusal_note(out['name'], out['reason']), log=log)
        _file_answer(store, out, log=log)
    return list(outcomes)


# ── the operator's dry run ───────────────────────────────────────────────────
def review(store, log=print) -> list:
    """What the next boundary would do with what is waiting, changing nothing.

    The blob it reasons about is the galaxy's current turn rather than the one
    a tick has yet to produce, so a placement it reports can differ from the
    one that happens if the turn colonises the planet it picked. It is a dry
    run for the same reason `abandonment.review` is one: an operator has to be
    able to ask before anything does.
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
