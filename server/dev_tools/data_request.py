"""
data_request.py: everything the project holds about one player
================================================================
    python server/dev_tools/data_request.py firebase://cs-resurgence find --code 1a2b3c4d
    python server/dev_tools/data_request.py firebase://cs-resurgence find --uid <uid>
    python server/dev_tools/data_request.py firebase://cs-resurgence find --code 1a2b3c4d --out answer.json

The operator's half of a player's request for their data (Privacy_Checklist
PRIV-4). Reads with the operator's administrator credentials and writes
nothing to the project.

A player code is the first eight characters of a uid, shown under the
launcher's title. It names a sign-in and is not a credential, so a request is
answered only once the player has pressed Send Log on the install the code
belongs to: that log arrives under the install's own sign-in, which no one
else can send. `find` lists the player's logs, so the operator sees it there.

What `find` reads, per uid the code matches:
  * Firebase Auth: the email, the sign-in providers, when the account was made
    and last signed in
  * every galaxy document: each field that names the uid (its seat, its
    sign-in provider, a seat move, a join record)
  * each galaxy's join request and its answer, filed under the uid
  * the launcher logs it sent, by id and arrival time
  * its coats of arms: the owner record and each upload's galaxy, civ and
    status

A seated civ's name and messages are also inside every turn, view and archive
record of its galaxy, in every player's copy. `find` names those civs and the
galaxies they are in, but does not extract the turns.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.dirname(HERE)
for _d in (SERVER, HERE):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import firebase_store                                           # noqa: E402
import log_tool                                                 # noqa: E402
import seat_tool                                                # noqa: E402

# A code is the start of a uid: letters and digits, case-sensitive.
CODE_RE = re.compile(r'^[A-Za-z0-9]{6,128}$')
# The Auth fields a player is told about. The password hash and salt are never
# read out.
AUTH_FIELDS = ('localId', 'email', 'emailVerified', 'displayName',
               'providerUserInfo', 'createdAt', 'lastLoginAt', 'lastRefreshAt')


class RequestRefused(Exception):
    """A request this tool declined, with the reason as its text."""


def when(value) -> str:
    epoch = seat_tool._epoch(value)
    if epoch is None:
        return '?'
    return datetime.datetime.fromtimestamp(
        epoch, datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')


def mentions(value, uid: str, path: str = '') -> list:
    """Every `(path, value)` in a document where `uid` is a key or a value.

    A uid sits in a galaxy document as a key (`seats`, `seat_providers`) or as
    a value (`seats_rebound`, `joined`), at any depth.
    """
    out = []
    if isinstance(value, dict):
        for k, v in value.items():
            here = f'{path}.{k}' if path else str(k)
            if k == uid:
                out.append((here, v))
            else:
                out.extend(mentions(v, uid, here))
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            out.extend(mentions(v, uid, f'{path}[{i}]'))
    elif value == uid:
        out.append((path, value))
    return out


def uids_for(code: str, auth_uids, galaxy_docs: dict, log_uids) -> list:
    """Every uid starting with `code`, wherever it was seen.

    A guest's Auth record can be gone while its seat or logs remain, so the
    galaxies and the logs are searched as well as Auth.
    """
    seen = set(u for u in auth_uids if u.startswith(code))
    for doc in galaxy_docs.values():
        for key in ('seats', 'seat_providers'):
            seen.update(u for u in (doc.get(key) or {}) if u.startswith(code))
    seen.update(u for u in log_uids if u and u.startswith(code))
    return sorted(seen)


def open_project(spec: str):
    project, _galaxy, bucket, prefix = firebase_store.parse_spec(spec)
    store = firebase_store.FirebaseTurnStore(project, '_data_request',
                                             bucket=bucket, prefix=prefix)
    return store


def galaxy_docs(store) -> dict:
    """`{galaxy id: document}` for every galaxy in the project."""
    return {snap.id: snap.to_dict() or {}
            for snap in store.fs.collection(store.prefix).stream()}


def find(store, code: str = None, uid: str = None) -> dict:
    """What the project holds for the uids a code or a uid names."""
    if uid is not None:
        if not seat_tool.UID_RE.match(uid):
            raise RequestRefused(f'{uid!r} is not a uid')
        code = uid
    elif code is None or not CODE_RE.match(code):
        raise RequestRefused(f'{code!r} is not a player code; it is the '
                             f'first eight characters of a uid, letters and '
                             f'digits')
    docs = galaxy_docs(store)
    logs = log_tool.list_logs(store.fs, store.prefix, limit=log_tool.CODE_SCAN,
                              code=code[:8])
    if uid is not None:
        uids = [uid]
    else:
        auth_uids = [u.get('localId') for u in
                     seat_tool.auth_users(store.project)]
        uids = uids_for(code, auth_uids, docs,
                        [rec.get('uid') for _id, rec in logs])
    answer = {'asked': code, 'uids': []}
    for u in uids:
        answer['uids'].append(about(store, u, docs, logs))
    return answer


def about(store, uid: str, docs: dict, logs: list) -> dict:
    """What the project holds for one uid."""
    auth = seat_tool.auth_user(store.project, uid)
    out = {'uid': uid,
           'auth': ({k: auth[k] for k in AUTH_FIELDS if k in auth}
                    if auth else None),
           'galaxies': [], 'logs': [], 'arms': None}
    for gid, doc in sorted(docs.items()):
        found = mentions(doc, uid)
        ref = store.fs.collection(store.prefix).document(gid)
        key = firebase_store.join_doc_id(uid)
        join = ref.collection(firebase_store.JOIN_COLLECTION).document(key).get()
        done = ref.collection(firebase_store.JOIN_DONE_COLLECTION).document(
            key).get()
        if not (found or join.exists or done.exists):
            continue
        out['galaxies'].append({
            'galaxy': gid,
            'civ': (doc.get('seats') or {}).get(uid),
            'fields': [[p, v] for p, v in found],
            'join_request': join.to_dict() if join.exists else None,
            'join_answer': done.to_dict() if done.exists else None,
        })
    for log_id, rec in logs:
        if rec.get('uid') == uid:
            out['logs'].append({'id': log_id, **rec})
    owner = firebase_store.arms_owner_doc(store.fs, store.prefix, uid).get()
    if owner.exists:
        rec = owner.to_dict() or {}
        uploads = []
        for i in rec.get('ids') or []:
            snap = firebase_store.arms_collection(store.fs, store.prefix) \
                .document(str(i)).get(field_paths=['galaxy', 'civ', 'status',
                                                   'uploaded_at'])
            uploads.append({'id': i, **(snap.to_dict() or {})}
                           if snap.exists else {'id': i, 'missing': True})
        out['arms'] = {'owner': rec, 'uploads': uploads}
    return out


def report(answer: dict, out) -> None:
    """The answer as text, for the operator to read before sending it."""
    print(f'asked: {answer["asked"]}', file=out)
    if not answer['uids']:
        print('  nothing is held under this code', file=out)
    for a in answer['uids']:
        print(f'\nuid {a["uid"]}', file=out)
        auth = a['auth']
        if auth is None:
            print('  Firebase Auth: no record', file=out)
        else:
            providers = [p.get('providerId') for p in
                         auth.get('providerUserInfo') or []] or ['anonymous']
            print(f'  Firebase Auth: {auth.get("email") or "no email"}, '
                  f'{", ".join(providers)}, made {when(auth.get("createdAt"))}, '
                  f'last sign-in {when(auth.get("lastLoginAt"))}', file=out)
        for g in a['galaxies']:
            print(f'  galaxy {g["galaxy"]}: civ {g["civ"] or "(no seat)"}',
                  file=out)
            for path, value in g['fields']:
                print(f'    {path} = {json.dumps(value, default=str)}',
                      file=out)
            if g['join_request']:
                print('    a join request is waiting', file=out)
            if g['join_answer']:
                print(f'    join answer: '
                      f'{json.dumps(g["join_answer"], default=str)}', file=out)
        if a['galaxies']:
            civs = [f'{g["civ"]} in {g["galaxy"]}' for g in a['galaxies']
                    if g['civ']]
            if civs:
                print(f'  the name and messages of {", ".join(civs)} are also '
                      f'in every turn and archive record of those galaxies',
                      file=out)
        print(f'  launcher logs: {len(a["logs"])}', file=out)
        for rec in a['logs']:
            print(f'    {rec["id"]}  {log_tool.when(rec.get("received_at"))}  '
                  f'{rec.get("why") or "?"}', file=out)
        if a['arms']:
            print(f'  coats of arms: {len(a["arms"]["uploads"])}', file=out)
            for up in a['arms']['uploads']:
                print(f'    {json.dumps(up, default=str)}', file=out)


def main(argv=None, out=None) -> int:
    out = out or sys.stdout
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('project', help='firebase://project')
    sub = ap.add_subparsers(dest='cmd', required=True)
    f = sub.add_parser('find', help='everything held for one player')
    who = f.add_mutually_exclusive_group(required=True)
    who.add_argument('--code', help='the player code the launcher shows')
    who.add_argument('--uid', help='a whole uid')
    f.add_argument('--out', help='also write the answer as JSON to this file')
    args = ap.parse_args(argv)
    try:
        store = open_project(args.project)
        answer = find(store, code=args.code, uid=args.uid)
    except (RequestRefused, seat_tool.SeatRefused, log_tool.LogRefused) as exc:
        print(f'refused: {exc}', file=sys.stderr)
        return 2
    report(answer, out)
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as fh:
            json.dump(answer, fh, indent=2, default=str)
        print(f'\nwritten to {args.out}', file=out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
