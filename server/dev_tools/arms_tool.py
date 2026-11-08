"""
arms_tool.py: approve or refuse the coats of arms players have uploaded
=======================================================================
    python server/dev_tools/arms_tool.py firebase://cs-resurgence list
    python server/dev_tools/arms_tool.py firebase://cs-resurgence list --all
    python server/dev_tools/arms_tool.py firebase://cs-resurgence show 12
    python server/dev_tools/arms_tool.py firebase://cs-resurgence approve 12 13
    python server/dev_tools/arms_tool.py firebase://cs-resurgence refuse 14

The relay keeps each upload (`POST /<galaxy>/arms`) in `<prefix>_arms`,
pending. Its owner sees it at once; every other player's client is answered
`FAIL` for it until it is approved, and asks again, so an approval shows
without touching any galaxy. A refused upload is shown to nobody, its owner
included, and the referee no longer lets it be chosen. Either decision can be
reversed.

`list` prints the pending uploads, oldest first, and `--all` every upload.
`show` writes one upload's PNG to `arms_<id>.png`, or to `--out`, to look at.
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.dirname(HERE)
for _d in (SERVER, HERE):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import firebase_store                                           # noqa: E402

# The fields `list` reads; the image is left out.
SUMMARY_FIELDS = ['id', 'uid', 'galaxy', 'civ', 'status', 'bytes', 'width',
                  'height', 'uploaded_at', 'decided_at']

DECISIONS = {'approve': firebase_store.ARMS_APPROVED,
             'refuse': firebase_store.ARMS_REFUSED}


class ArmsRefused(Exception):
    """A request this tool declined, with the reason as its text."""


def open_project(spec: str):
    """(Firestore client, prefix) for `firebase://project`."""
    project, _galaxy, bucket, prefix = firebase_store.parse_spec(spec)
    # A store is opened only for its Firestore client; the galaxy name is a
    # placeholder.
    store = firebase_store.FirebaseTurnStore(project, '_arms', bucket=bucket,
                                             prefix=prefix)
    return store.fs, store.prefix


def when(ts) -> str:
    try:
        return datetime.datetime.fromtimestamp(
            float(ts), datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    except (TypeError, ValueError):
        return '?'


def summary_line(rec: dict) -> str:
    """One line about one upload, for `list`."""
    uid = str(rec.get('uid') or '')
    return (f'{rec.get("id"):>5}  {rec.get("status") or "?":8}  '
            f'{when(rec.get("uploaded_at"))}  code {uid[:8] or "?"}  '
            f'{rec.get("galaxy") or "?"}/{rec.get("civ") or "?"}  '
            f'{rec.get("width")}x{rec.get("height")}  '
            f'{rec.get("bytes") or 0:,} bytes')


def list_arms(fs, prefix: str, every: bool = False) -> list:
    """Summary records, oldest first; only the pending ones unless `every`."""
    from google.cloud.firestore_v1.base_query import FieldFilter
    q = firebase_store.arms_collection(fs, prefix)
    if not every:
        q = q.where(filter=FieldFilter('status', '==',
                                       firebase_store.ARMS_PENDING))
    rows = [s.to_dict() or {} for s in q.select(SUMMARY_FIELDS).stream()]
    return sorted(rows, key=lambda r: int(r.get('id') or 0))


def arms_record(fs, prefix: str, arms_id: int) -> dict:
    snap = firebase_store.arms_collection(fs, prefix).document(
        str(int(arms_id))).get()
    if not snap.exists:
        raise ArmsRefused(f'there is no coat of arms {arms_id}')
    return snap.to_dict() or {}


def decide(fs, prefix: str, arms_id: int, status: str) -> str:
    """Approve or refuse one upload, and return the status it had.

    The upload and its owner's refused list change in one transaction, since
    the referee reads only the owner's list.
    """
    from google.cloud import firestore
    ref = firebase_store.arms_collection(fs, prefix).document(
        str(int(arms_id)))

    @firestore.transactional
    def _put(tx):
        snap = ref.get(transaction=tx)
        if not snap.exists:
            raise ArmsRefused(f'there is no coat of arms {arms_id}')
        rec = snap.to_dict() or {}
        owner = firebase_store.arms_owner_doc(fs, prefix, rec['uid'])
        change = (firestore.ArrayUnion if status == firebase_store.ARMS_REFUSED
                  else firestore.ArrayRemove)
        tx.update(ref, {'status': status, 'decided_at': time.time()})
        tx.set(owner, {'refused': change([int(arms_id)])}, merge=True)
        return rec.get('status')

    return _put(fs.transaction())


def main(argv=None, out=None) -> int:
    out = out or sys.stdout
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('project', help='firebase://project')
    sub = ap.add_subparsers(dest='cmd', required=True)
    ls = sub.add_parser('list', help='the uploads waiting, oldest first')
    ls.add_argument('--all', action='store_true', help='every upload')
    sh = sub.add_parser('show', help='write one upload to a PNG file')
    sh.add_argument('id', type=int)
    sh.add_argument('--out', help='the file to write, arms_<id>.png if not '
                                  'given')
    for verb in DECISIONS:
        p = sub.add_parser(verb, help=f'{verb} uploads by id')
        p.add_argument('ids', type=int, nargs='+')
    args = ap.parse_args(argv)
    try:
        fs, prefix = open_project(args.project)
        if args.cmd == 'list':
            rows = list_arms(fs, prefix, args.all)
            for rec in rows:
                print(summary_line(rec), file=out)
            if not rows:
                print('no uploads' if args.all else 'no uploads waiting',
                      file=out)
            return 0
        if args.cmd == 'show':
            rec = arms_record(fs, prefix, args.id)
            path = args.out or f'arms_{args.id}.png'
            with open(path, 'wb') as f:
                f.write(bytes(rec.pop('png')))
            print(f'{summary_line(rec)}\nwritten to {path}', file=out)
            return 0
        status = DECISIONS[args.cmd]
        for arms_id in args.ids:
            was = decide(fs, prefix, arms_id, status)
            print(f'coat of arms {arms_id}: {was} -> {status}', file=out)
        return 0
    except (ArmsRefused, ValueError) as exc:
        print(f'refused: {exc}', file=out)
        return 2


if __name__ == '__main__':
    sys.exit(main())
