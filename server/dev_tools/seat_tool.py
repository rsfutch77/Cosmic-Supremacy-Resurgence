"""
seat_tool.py , the operator's hand on a galaxy's seat map
=========================================================
    python seat_tool.py firebase://cs-resurgence/sandbox list
    python seat_tool.py firebase://cs-resurgence/sandbox candidates
    python seat_tool.py firebase://cs-resurgence/sandbox rebind DemoPlayer <uid>
    python seat_tool.py firebase://cs-resurgence/sandbox rebind DemoPlayer <uid> --dry-run
    python seat_tool.py firebase://cs-resurgence/sandbox bind Newcomer <uid>

A seat is a `seats` entry on the galaxy document, `{uid: civ}`, and the relay
lets a Firebase uid act as a civ only when that entry says so (J4). The uid is
per data directory rather than per person, so a player who reinstalls Windows,
moves the install or runs a second copy arrives with a uid that holds nothing
and is refused their own empire. This moves the seat to the new uid.

It runs with the operator's administrator credentials, the same ones the
referee uses, and never through the relay. No player route writes a seat
except the opt-in first-use claim, and that stays true.

How the operator learns the new uid
-----------------------------------
The player cannot see their uid, and the file that holds it, `fb_identity.json`,
also holds a refresh token that is a credential. Nobody asks a player for that
file. `candidates` answers the question from the operator's side instead: it
lists every Firebase sign-in that holds no seat in this galaxy, most recently
active first, with when it was created and when it last refreshed a token. A
launcher refreshes on start and hourly while it runs, so a player who has just
been refused on a fresh install is the unseated sign-in created and refreshed
a few minutes ago. Sign-ins seated in another galaxy are marked, because a
player who plays two galaxies is not the stranger being looked for.

Why rebind and not release
--------------------------
A release, deleting the seat and letting the next first-use claim take it, was
considered and not built. Under `seat_claim: 'first-use'` it hands an
established empire to whichever sign-in submits as that civ first, and the
roster is public, so it is a land grab of the one thing a player is trying to
get back. Under the default, where first-use is off, a released civ is played
by nobody until the operator binds it anyway. A rebind names the uid, happens
in one transaction and leaves no window.

What it refuses
---------------
  * a civ that is not on the roster, which includes one that was reclaimed
  * a civ that no uid holds, because there is no seat to move
  * a target uid that already holds a different seat in this galaxy
  * a target uid that is not a well-formed Firebase uid
  * a target uid that Firebase Auth does not know, since a typo would lock the
    player out a second time (`--no-auth-check` skips this)

A target that already holds this very civ changes nothing and says so.

`bind` is the same write for a civ that no uid holds, and refuses a held one.
A join granted at a boundary binds its own seat (`joins.commit`), so `bind` is
for the cases that leaves unseated: a referee killed between publishing the
new civ and committing it, a civ seeded into the roster by hand, and a joiner
whose sign-in was already playing another civ. `joined` names the uid that
asked, and a request still waiting names it too.

The last move of each seat is kept on the document under `seats_rebound`, which
is not in the store's state allowlist and so is never served to a player.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.dirname(HERE)
for _d in (SERVER, HERE):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import firebase_store                                           # noqa: E402

# Where the last move of each seat is recorded on the galaxy document.
REBOUND_KEY = 'seats_rebound'

# A Firebase uid is letters and digits: 28 for an anonymous user, and never
# anything else from the providers this project uses. Checked because the uid
# becomes part of a Firestore field path, where a dot or a slash would write
# somewhere other than `seats`.
UID_RE = re.compile(r'^[A-Za-z0-9]{1,128}$')

IDENTITY_TOOLKIT = 'https://identitytoolkit.googleapis.com'
ADMIN_SCOPE = 'https://www.googleapis.com/auth/cloud-platform'


class SeatRefused(Exception):
    """An operator request this tool declined, with the reason as its text."""


# ── the galaxy document ──────────────────────────────────────────────────────
def open_galaxy(spec: str):
    store = firebase_store.open_firebase_store(spec)
    if not store.galaxy:
        raise SeatRefused(f'{spec} names no galaxy')
    return store


def galaxy_doc(store) -> dict:
    snap = store.doc.get()
    if not snap.exists:
        raise SeatRefused(f'there is no galaxy {store.galaxy} in '
                          f'{store.project}')
    return snap.to_dict() or {}


def seats(doc: dict) -> dict:
    """`{uid: civ}` as the relay reads it."""
    return dict(doc.get('seats') or {})


def holder_of(doc: dict, civ: str):
    """The uid that holds `civ`, or None."""
    for uid, held in seats(doc).items():
        if held == civ:
            return uid
    return None


def check_rebind(doc: dict, galaxy: str, civ: str, new_uid: str,
                 bind: bool = False):
    """The uid the seat moves from, or SeatRefused. Pure, so the transaction
    and a dry run apply exactly the same rules.

    With `bind`, the civ must be one nobody holds and the answer is None.
    """
    if not UID_RE.match(new_uid or ''):
        raise SeatRefused(f'{new_uid!r} is not a Firebase uid')
    roster = [str(c) for c in (doc.get('civs') or [])]
    if civ not in roster:
        near = next((c for c in roster if c.lower() == civ.lower()), None)
        hint = f'; the roster has {near}' if near else ''
        raise SeatRefused(f'{civ} is not a civ in {galaxy}{hint}')
    old = holder_of(doc, civ)
    if old is None and not bind:
        raise SeatRefused(f'no sign-in holds {civ} in {galaxy}, so there is '
                          f'no seat to move; `bind` seats a civ nobody holds')
    if old is not None and bind and old != new_uid:
        raise SeatRefused(f'{civ} in {galaxy} is held by {old}; `rebind` '
                          f'moves a held seat')
    other = seats(doc).get(new_uid)
    if other is not None and other != civ:
        raise SeatRefused(f'{new_uid} already plays {other} in {galaxy}; a '
                          f'sign-in holds one seat')
    return old


def rebind(store, civ: str, new_uid: str, dry_run: bool = False,
           bind: bool = False) -> dict:
    """Move `civ`'s seat to `new_uid`, or with `bind` seat a civ nobody holds.
    Returns what was done.

    In a transaction, so a first-use claim landing in the same second cannot
    leave the civ held twice or the target holding two seats.
    """
    def done(old, dry):
        return {'civ': civ, 'from': old, 'to': new_uid,
                'changed': old != new_uid, 'dry_run': dry}

    if dry_run:
        doc = galaxy_doc(store)
        return done(check_rebind(doc, store.galaxy, civ, new_uid, bind), True)

    from google.cloud import firestore
    from google.cloud.firestore_v1.field_path import FieldPath

    @firestore.transactional
    def _move(tx, ref):
        snap = ref.get(transaction=tx)
        if not snap.exists:
            raise SeatRefused(f'there is no galaxy {store.galaxy} in '
                              f'{store.project}')
        old = check_rebind(snap.to_dict() or {}, store.galaxy, civ, new_uid,
                           bind)
        if old == new_uid:
            return old
        # A civ name is whatever a player typed and may hold a dot, so its
        # path is quoted rather than joined. A uid was checked to be plain.
        fields = {f'seats.{new_uid}': civ,
                  FieldPath(REBOUND_KEY, civ).to_api_repr():
                      {'from': old, 'to': new_uid, 'at': time.time()}}
        if old is not None:
            fields[f'seats.{old}'] = firestore.DELETE_FIELD
        tx.update(ref, fields)
        return old

    return done(_move(store.fs.transaction(), store.doc), False)


# ── Firebase Auth, read as an administrator ──────────────────────────────────
def _auth_base(project: str):
    """(url prefix, headers) for the Identity Toolkit admin API.

    The Auth emulator accepts `Bearer owner` as an administrator. Against the
    real service the request carries the operator's default credentials,
    billed to the galaxy's project for the reason `FirebaseTurnStore.credentials`
    gives.
    """
    emu = os.environ.get('FIREBASE_AUTH_EMULATOR_HOST')
    if emu:
        return (f'http://{emu}/identitytoolkit.googleapis.com/v1/projects/'
                f'{project}', {'Authorization': 'Bearer owner'})
    import google.auth
    import google.auth.transport.requests
    creds, _ = google.auth.default(scopes=[ADMIN_SCOPE])
    if hasattr(creds, 'with_quota_project'):
        creds = creds.with_quota_project(project)
    creds.refresh(google.auth.transport.requests.Request())
    headers = {'Authorization': f'Bearer {creds.token}',
               'x-goog-user-project': project}
    return f'{IDENTITY_TOOLKIT}/v1/projects/{project}', headers


def _auth_call(project: str, path: str, body: dict = None) -> dict:
    base, headers = _auth_base(project)
    data = None
    if body is not None:
        data = json.dumps(body).encode('utf-8')
        headers = dict(headers, **{'Content-Type': 'application/json'})
    req = urllib.request.Request(f'{base}/{path}', data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b'{}')


def auth_user(project: str, uid: str):
    """What Firebase Auth holds for one uid, or None when it holds nothing."""
    out = _auth_call(project, 'accounts:lookup', {'localId': [uid]})
    users = out.get('users') or []
    return users[0] if users else None


def auth_users(project: str, limit: int = 5000) -> list:
    """Every Firebase Auth user in the project, up to `limit`."""
    users, token = [], None
    while len(users) < limit:
        q = {'maxResults': min(1000, limit - len(users))}
        if token:
            q['nextPageToken'] = token
        out = _auth_call(project, 'accounts:batchGet?' +
                         urllib.parse.urlencode(q))
        users.extend(out.get('users') or [])
        token = out.get('nextPageToken')
        if not token or not out.get('users'):
            break
    return users


def _epoch(value):
    """An Auth timestamp as epoch seconds. `createdAt` and `lastLoginAt` are
    milliseconds in a string; `lastRefreshAt` is an RFC 3339 time."""
    if value in (None, ''):
        return None
    try:
        return int(value) / 1000.0
    except (TypeError, ValueError):
        pass
    try:
        return datetime.datetime.fromisoformat(
            str(value).replace('Z', '+00:00')).timestamp()
    except ValueError:
        return None


def seated_elsewhere(store) -> dict:
    """`{uid: [(galaxy, civ), ...]}` across every other galaxy in the project."""
    out = {}
    for snap in store.fs.collection(store.prefix).stream():
        if snap.id == store.galaxy:
            continue
        for uid, civ in ((snap.to_dict() or {}).get('seats') or {}).items():
            out.setdefault(uid, []).append((snap.id, civ))
    return out


def candidates(store, users: list = None, elsewhere: dict = None) -> list:
    """Sign-ins holding no seat in this galaxy, most recently active first.

    Each row is `{uid, created, last_active, elsewhere}`, times as epoch
    seconds. `last_active` is the later of the last sign-in and the last token
    refresh, which is the last time that install's launcher was running.
    """
    doc = galaxy_doc(store)
    held = seats(doc)
    users = auth_users(store.project) if users is None else users
    elsewhere = seated_elsewhere(store) if elsewhere is None else elsewhere
    rows = []
    for u in users:
        uid = u.get('localId')
        if not uid or uid in held:
            continue
        times = [t for t in (_epoch(u.get('lastLoginAt')),
                             _epoch(u.get('lastRefreshAt'))) if t]
        rows.append({'uid': uid, 'created': _epoch(u.get('createdAt')),
                     'last_active': max(times) if times else None,
                     'elsewhere': elsewhere.get(uid, [])})
    rows.sort(key=lambda r: r['last_active'] or 0, reverse=True)
    return rows


# ── the command line ─────────────────────────────────────────────────────────
def _when(epoch) -> str:
    if not epoch:
        return '-'
    return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(epoch))


def cmd_list(store, out) -> int:
    doc = galaxy_doc(store)
    held = seats(doc)
    by_civ = {civ: uid for uid, civ in held.items()}
    moved = doc.get(REBOUND_KEY) or {}
    print(f'{store.galaxy}: seat_claim {doc.get("seat_claim") or "off"}',
          file=out)
    for civ in [str(c) for c in (doc.get('civs') or [])]:
        uid = by_civ.pop(civ, None)
        line = f'  {civ:<16} {uid or "(no seat)"}'
        if civ in moved:
            line += (f'  rebound {_when(moved[civ].get("at"))} from '
                     f'{moved[civ].get("from")}')
        print(line, file=out)
    for civ, uid in sorted(by_civ.items()):
        print(f'  {civ:<16} {uid}  (not on the roster)', file=out)
    return 0


def cmd_candidates(store, out, limit: int) -> int:
    rows = candidates(store)
    print(f'sign-ins holding no seat in {store.galaxy}, most recently active '
          f'first:', file=out)
    if not rows:
        print('  none', file=out)
    for r in rows[:limit]:
        also = ', '.join(f'{c} in {g}' for g, c in r['elsewhere'])
        print(f'  {r["uid"]}  created {_when(r["created"])}  last active '
              f'{_when(r["last_active"])}' + (f'  plays {also}' if also
                                               else ''), file=out)
    if len(rows) > limit:
        print(f'  ... and {len(rows) - limit} more', file=out)
    return 0


def cmd_rebind(store, out, civ, uid, dry_run, auth_check,
               bind=False) -> int:
    if auth_check and UID_RE.match(uid or ''):
        if auth_user(store.project, uid) is None:
            raise SeatRefused(f'Firebase Auth in {store.project} has no user '
                              f'{uid}; check the uid, or pass --no-auth-check')
    done = rebind(store, civ, uid, dry_run=dry_run, bind=bind)
    was = done['from'] or 'nobody'
    if not done['changed']:
        print(f'{uid} already holds {civ} in {store.galaxy}; nothing changed',
              file=out)
    elif dry_run:
        print(f'would move {civ} in {store.galaxy} from {was} to {uid}; '
              f'nothing written', file=out)
    elif done['from'] is None:
        print(f'seated {civ} in {store.galaxy} on {uid}, which held no seat '
              f'here before.', file=out)
    else:
        print(f'moved {civ} in {store.galaxy} from {was} to {uid}. '
              f'{was} now holds no seat here.', file=out)
    return 0


def main(argv=None, out=None) -> int:
    out = out or sys.stdout
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('galaxy', help='firebase://project/galaxy')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('list', help='every civ on the roster and its uid')
    c = sub.add_parser('candidates',
                       help='sign-ins with no seat here, newest activity first')
    c.add_argument('--limit', type=int, default=20)
    for name, what in (('rebind', 'move a held seat to another uid'),
                       ('bind', 'seat a civ nobody holds on a uid')):
        r = sub.add_parser(name, help=what)
        r.add_argument('civ')
        r.add_argument('uid')
        r.add_argument('--dry-run', action='store_true')
        r.add_argument('--no-auth-check', action='store_true',
                       help='skip checking the uid exists in Firebase Auth')
    args = ap.parse_args(argv)
    try:
        store = open_galaxy(args.galaxy)
        if args.cmd == 'list':
            return cmd_list(store, out)
        if args.cmd == 'candidates':
            return cmd_candidates(store, out, args.limit)
        return cmd_rebind(store, out, args.civ, args.uid, args.dry_run,
                          not args.no_auth_check, bind=args.cmd == 'bind')
    except (SeatRefused, ValueError) as exc:
        print(f'refused: {exc}', file=out)
        return 2


if __name__ == '__main__':
    sys.exit(main())
