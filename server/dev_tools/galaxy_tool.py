"""
galaxy_tool.py , the operator ends a galaxy and starts the next one (K5)
========================================================================
    python galaxy_tool.py firebase://cs-resurgence list
    python galaxy_tool.py firebase://cs-resurgence close sandbox --reason "Season one is over."
    python galaxy_tool.py firebase://cs-resurgence reopen sandbox
    python galaxy_tool.py firebase://cs-resurgence start season2 --name "Season Two" \\
        --generate fresh.b64 --player Alice --player Bob --replaces sandbox
    python galaxy_tool.py firebase://cs-resurgence start season2 ... --dry-run

A galaxy has no season timer. The operator calls one over and starts another,
and this is the whole of that from a command line. It runs with the operator's
administrator credentials, the ones the referee holds, against a galaxy
directory: a `firebase://project` spec, or a folder of galaxies for development.
The relay has no route for any of it, on purpose.

Closing
-------
`close` writes `status: closed` and the operator's reason into the galaxy's own
state, through `galaxy_directory.set_status`, so the listing and the store
cannot disagree. Nothing is deleted. Turns, submissions, notes and the archive
all still read; the stores refuse a submission (`GalaxyClosed`), the relay
refuses an upload ticket with the reason in the refusal, the referee worker
idles, and `joins` and `abandonment` leave a closed galaxy alone. A launcher
reads the status and the reason out of `/state` and says the galaxy has ended
(`launcher.closed_problem`). The reason is required, because "closed" on its
own reads to a player as a fault rather than a decision. After the write the
tool reads the current turn and its archive back, so a close that left the
galaxy unreadable is reported rather than assumed away.

Starting
--------
`start` registers a galaxy in the directory, which is what makes the Galaxies
page list it, and publishes its first turn with the roster. The galaxy is one
of two things:

  --generate FILE   a freshly generated galaxy, made into one civ per player by
                    `make_multiplayer_galaxy.build`
  --blob FILE       a galaxy already in the shape wanted; every --player has to
                    be a civ in it, since a roster naming a civ the galaxy does
                    not hold fails every submission from that player

It refuses an id that already has a turn, so a typo cannot publish over a
running galaxy. `--warn` and `--reclaim` set the abandonment thresholds (K3),
and `--seat-claim first-use` opens a Firebase galaxy's roster to first-use
claims, which is off by default for the reason `relay.claim_seat` gives.

`--replaces OLD` closes OLD once the new galaxy is published, and not before,
so a start that fails leaves the old galaxy open and players are never left
with nothing to play. Its reason defaults to a sentence naming the new galaxy.

The referee worker ticks one galaxy, named by its `--store`. A fresh galaxy is
not ticked until the worker is pointed at it; the tool prints that command and
does not run it.

`--dry-run` on any write checks everything it can, including building a
generated galaxy in memory, says what it would do, and writes nothing.
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.dirname(HERE)
for _d in (SERVER, HERE, os.path.join(os.path.dirname(SERVER), 'client',
                                      'dev_tools')):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import abandonment                                              # noqa: E402
import galaxy_directory as gd                                   # noqa: E402
import turn_store                                               # noqa: E402

# The beta's turn length.
TURN_SECONDS = 14400

# What a replaced galaxy tells its players when the operator gives no reason.
REPLACED = 'This galaxy has ended. {name} has started in its place.'

SEAT_CLAIMS = ('first-use',)


class Refused(Exception):
    """An operator request this tool declined, with the reason as its text."""


# ── reading ──────────────────────────────────────────────────────────────────
def is_firebase(directory) -> bool:
    return isinstance(directory, gd.FirebaseGalaxyDirectory)


def row(directory, gid: str):
    g = directory.galaxy(gid)
    if g is None:
        raise Refused(f'there is no galaxy {gid} in {directory!r}')
    return g


def readable(store) -> dict:
    """What still reads after a close: the current turn and its archive."""
    turn = store.current()[0]
    out = {'turn': turn, 'turn_bytes': len(store.turn_blob(turn))}
    record = store.archive_record(turn - 1) if turn > 0 else None
    out['archive_turn'] = turn - 1 if record is not None else None
    return out


# ── closing ──────────────────────────────────────────────────────────────────
def check_close(directory, gid: str, reason: str):
    """The row being closed, or Refused. Changes nothing."""
    if not (reason or '').strip():
        raise Refused('a galaxy is closed with a reason, which its players '
                      'are shown; pass --reason')
    g = row(directory, gid)
    if g.status == gd.CLOSED:
        raise Refused(f'{gid} is already closed')
    return g


def close(directory, gid: str, reason: str, dry_run: bool = False,
          out=None) -> dict:
    out = out or sys.stdout
    g = check_close(directory, gid, reason)
    if dry_run:
        print(f'would close {gid} ({g.name}, turn {g.turn}, {g.players} '
              f'player(s)) with the reason: {reason}', file=out)
        return {'gid': gid, 'dry_run': True}
    directory.set_status(gid, gd.CLOSED, reason=reason.strip())
    store = directory.store(gid)
    done = {'gid': gid, 'dry_run': False,
            'status': row(directory, gid).status}
    if store.exists() and store.status() != gd.CLOSED:
        raise Refused(f'{gid} was written closed and reads back as '
                      f'{store.status()}')
    print(f'closed {gid} ({g.name}): {reason.strip()}', file=out)
    if store.exists():
        try:
            done.update(readable(store))
        except Exception as exc:                            # noqa: BLE001
            print(f'  WARNING: {gid} is closed and its current turn does not '
                  f'read back: {type(exc).__name__}: {exc}', file=out)
        else:
            print(f'  turn {done["turn"]} still reads, '
                  f'{done["turn_bytes"]:,} bytes; archive of turn '
                  f'{done["archive_turn"]} '
                  f'{"reads" if done["archive_turn"] is not None else "absent"}',
                  file=out)
    return done


def reopen(directory, gid: str, dry_run: bool = False, out=None) -> dict:
    out = out or sys.stdout
    g = row(directory, gid)
    if g.status != gd.CLOSED:
        raise Refused(f'{gid} is {g.status}, not closed')
    if dry_run:
        print(f'would reopen {gid} ({g.name})', file=out)
        return {'gid': gid, 'dry_run': True}
    directory.set_status(gid, gd.OPEN)
    print(f'reopened {gid} ({g.name})', file=out)
    return {'gid': gid, 'dry_run': False}


# ── starting ─────────────────────────────────────────────────────────────────
def first_blob(players, blob_path: str = None, generate: str = None,
               log=None) -> bytes:
    """The first turn of the new galaxy, or Refused."""
    import save_parser as sp
    import inject_civ as icv
    quiet = log or (lambda *a, **k: None)
    if bool(blob_path) == bool(generate):
        raise Refused('a galaxy starts from exactly one of --blob and '
                      '--generate')
    if len(players) < 2:
        raise Refused('a galaxy needs at least two players')
    if len(set(players)) != len(players):
        raise Refused('player names have to be distinct')
    path = blob_path or generate
    if not os.path.exists(path):
        raise Refused(f'no file {path}')
    try:
        blob = sp.load_any(path)
    except Exception as exc:                                # noqa: BLE001
        raise Refused(f'{path} is not a galaxy: {exc}')
    if generate:
        import make_multiplayer_galaxy as mmg
        try:
            blob = mmg.build(blob, list(players), log=quiet)
        except SystemExit as exc:
            raise Refused(f'{path} could not be made into a galaxy for '
                          f'{list(players)}: {exc}')
    civs = [o['name'] for o in icv.owner_records(blob)]
    missing = [p for p in players if p not in civs]
    if missing:
        raise Refused(f'{path} holds no civ called {missing}; its civs are '
                      f'{civs}')
    return blob


def check_start(directory, gid: str, name: str, players, seat_claim=None,
                warn=None, reclaim=None):
    """Refused, or nothing. Changes nothing."""
    if not gid or '/' in gid or '\\' in gid or gid.startswith(('.', '_')):
        raise Refused(f'{gid!r} cannot be a galaxy id')
    g = directory.galaxy(gid)
    if g is not None and g.turn is not None:
        raise Refused(f'{gid} already has a turn ({g.turn}); a galaxy is '
                      f'started once, so pick a new id')
    if seat_claim and seat_claim not in SEAT_CLAIMS:
        raise Refused(f'seat claim {seat_claim!r} is not one of {SEAT_CLAIMS}')
    if seat_claim and not is_firebase(directory):
        raise Refused('a seat claim is a relay setting, and only a Firebase '
                      'galaxy is served through the relay')
    for label, value in (('--warn', warn), ('--reclaim', reclaim)):
        if value is not None and value < 0:
            raise Refused(f'{label} is a count of turns; 0 turns it off')


def start(directory, gid: str, name: str, players, blob_path: str = None,
          generate: str = None, turn_seconds: int = TURN_SECONDS,
          warn: int = None, reclaim: int = None, seat_claim: str = None,
          replaces: str = None, reason: str = None, dry_run: bool = False,
          out=None) -> dict:
    out = out or sys.stdout
    name = name or gid
    check_start(directory, gid, name, players, seat_claim, warn, reclaim)
    if replaces:
        if replaces == gid:
            raise Refused('a galaxy cannot replace itself')
        reason = (reason or '').strip() or REPLACED.format(name=name)
        check_close(directory, replaces, reason)
    if turn_seconds <= 0:
        raise Refused('--turn-seconds is the length of a turn and has to be '
                      'positive')
    blob = first_blob(players, blob_path, generate)
    first = turn_store.turn_of(blob)

    what = (f'{gid} ({name}), turn {first}, {turn_seconds}s turns, roster '
            f'{list(players)}')
    if warn is not None or reclaim is not None:
        what += f', warn after {warn}, reclaim after {reclaim}'
    if seat_claim:
        what += f', seat claim {seat_claim}'
    if dry_run:
        print(f'would start {what}', file=out)
        if replaces:
            print(f'would then close {replaces} with the reason: {reason}',
                  file=out)
        return {'gid': gid, 'dry_run': True, 'turn': first}

    store = directory.register(gid, name=name, status=gd.OPEN)
    turn = store.start(blob, list(players), turn_seconds=turn_seconds)
    fields = {}
    if warn is not None:
        fields[abandonment.WARN_KEY] = int(warn)
    if reclaim is not None:
        fields[abandonment.RECLAIM_KEY] = int(reclaim)
    if seat_claim:
        fields['seat_claim'] = seat_claim
    if fields:
        store.update_state(fields)
    print(f'started {what}', file=out)
    spec = directory.galaxy(gid).store
    done = {'gid': gid, 'dry_run': False, 'turn': turn, 'store': spec}
    if replaces:
        done['closed'] = close(directory, replaces, reason, out=out)
    print(f'\nthe referee worker ticks one galaxy; to tick this one, point it '
          f'here:\n  python server/referee_worker.py --store "{spec}"',
          file=out)
    return done


# ── the command line ─────────────────────────────────────────────────────────
def cmd_list(directory, out) -> int:
    rows = directory.galaxies()
    if not rows:
        print(f'no galaxies in {directory!r}', file=out)
    for g in rows:
        turn = '-' if g.turn is None else g.turn
        print(f'  {g.id:<20} {g.status:<8} turn {turn:<5} {g.players} '
              f'player(s)  {g.name}', file=out)
    return 0


def main(argv=None, out=None) -> int:
    out = out or sys.stdout
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('directory', help='firebase://project, or a folder of '
                                      'galaxies')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('list', help='every galaxy, with its status and turn')
    c = sub.add_parser('close', help='end a galaxy, keeping it readable')
    c.add_argument('galaxy')
    c.add_argument('--reason', required=True,
                   help='shown to every player of the galaxy')
    c.add_argument('--dry-run', action='store_true')
    r = sub.add_parser('reopen', help='undo a close')
    r.add_argument('galaxy')
    r.add_argument('--dry-run', action='store_true')
    s = sub.add_parser('start', help='register a galaxy and publish turn one')
    s.add_argument('galaxy')
    s.add_argument('--name', help='what the Galaxies page calls it')
    s.add_argument('--player', action='append', default=[],
                   help='a seat on the roster, repeatable')
    s.add_argument('--generate', help='a freshly generated galaxy capture')
    s.add_argument('--blob', help='a galaxy already holding each --player')
    s.add_argument('--turn-seconds', type=int, default=TURN_SECONDS)
    s.add_argument('--warn', type=int, help='missed turns before a warning')
    s.add_argument('--reclaim', type=int, help='missed turns before a reclaim')
    s.add_argument('--seat-claim', choices=SEAT_CLAIMS)
    s.add_argument('--replaces', help='close this galaxy once the new one is '
                                      'published')
    s.add_argument('--reason', help='with --replaces, what its players are '
                                    'shown')
    s.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)
    try:
        directory = gd.open_directory(a.directory)
        if isinstance(directory, gd.HttpGalaxyDirectory):
            raise Refused('a galaxy is opened and closed with the '
                          'administrator\'s credentials, not through the '
                          'relay; pass firebase://project')
        if a.cmd == 'list':
            return cmd_list(directory, out)
        if a.cmd == 'close':
            close(directory, a.galaxy, a.reason, dry_run=a.dry_run, out=out)
            return 0
        if a.cmd == 'reopen':
            reopen(directory, a.galaxy, dry_run=a.dry_run, out=out)
            return 0
        start(directory, a.galaxy, a.name, a.player, blob_path=a.blob,
              generate=a.generate, turn_seconds=a.turn_seconds, warn=a.warn,
              reclaim=a.reclaim, seat_claim=a.seat_claim,
              replaces=a.replaces, reason=a.reason, dry_run=a.dry_run,
              out=out)
        return 0
    except Refused as exc:
        print(f'refused: {exc}', file=out)
        return 2


if __name__ == '__main__':
    sys.exit(main())
