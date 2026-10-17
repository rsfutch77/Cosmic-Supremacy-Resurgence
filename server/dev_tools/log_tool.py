"""
log_tool.py: read the launcher logs players have sent
======================================================
    python server/dev_tools/log_tool.py firebase://cs-resurgence list
    python server/dev_tools/log_tool.py firebase://cs-resurgence list --code 1a2b3c4d
    python server/dev_tools/log_tool.py firebase://cs-resurgence show <id>
    python server/dev_tools/log_tool.py firebase://cs-resurgence show <id> --out report.log
    python server/dev_tools/log_tool.py firebase://cs-resurgence prune --dry-run

The relay stores each log a launcher sends (`POST /_logs`) as a Firestore
document in `<prefix>_logs` and never serves them back. This reads them with
the operator's administrator credentials.

`list` prints the newest first: id, arrival time, the sender's support code
(the first eight characters of its uid, shown in the launcher), why it was
sent, build, galaxy, civ, turn and size. `--code` keeps one player code's
logs. `show` prints one log's text whole. `prune` deletes logs and daily
counts older than `LOG_KEEP_DAYS`.
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys
import time
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.dirname(HERE)
for _d in (SERVER, HERE):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import firebase_store                                           # noqa: E402

# Copies of `relay.LOG_COLLECTION`, `relay.LOG_QUOTA_COLLECTION` and
# `relay.LOG_KEEP_DAYS`; `test_log_upload.py` checks they agree.
LOG_SUFFIX = '_logs'
LOG_QUOTA_SUFFIX = '_log_quota'
LOG_KEEP_DAYS = 14

# The fields `list` reads; the text is left out.
SUMMARY_FIELDS = ['uid', 'received_at', 'why', 'build', 'galaxy', 'civ',
                  'turn', 'bytes']

# How far back `list --code` looks for one sign-in's logs.
CODE_SCAN = 500


class LogRefused(Exception):
    """A request this tool declined, with the reason as its text."""


def open_project(spec: str):
    """(Firestore client, prefix) for `firebase://project`."""
    project, _galaxy, bucket, prefix = firebase_store.parse_spec(spec)
    # A store is opened only for its Firestore client; the galaxy name is a
    # placeholder.
    store = firebase_store.FirebaseTurnStore(project, '_logs', bucket=bucket,
                                             prefix=prefix)
    return store.fs, store.prefix


def collections(fs, prefix: str):
    return (fs.collection(prefix + LOG_SUFFIX),
            fs.collection(prefix + LOG_QUOTA_SUFFIX))


def when(ts) -> str:
    try:
        return datetime.datetime.fromtimestamp(
            float(ts), datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    except (TypeError, ValueError):
        return '?'


def summary_line(log_id: str, rec: dict) -> str:
    """One line about one log, for `list`."""
    uid = str(rec.get('uid') or '')
    where = rec.get('galaxy') or 'no galaxy'
    if rec.get('civ'):
        where += f'/{rec["civ"]}'
    if rec.get('turn') is not None:
        where += f' turn {rec["turn"]}'
    size = rec.get('bytes')
    size = f'{size:,} bytes' if isinstance(size, int) else '? bytes'
    return (f'{log_id}  {when(rec.get("received_at"))}  code {uid[:8] or "?"}'
            f'  {rec.get("why") or "?"}  v{rec.get("build") or "?"}  {where}'
            f'  {size}')


def list_logs(fs, prefix: str, limit: int = 20, code: str = None) -> list:
    """[(id, summary record)], newest first."""
    from google.cloud import firestore
    logs, _quota = collections(fs, prefix)
    q = logs.order_by('received_at', direction=firestore.Query.DESCENDING)
    q = q.select(SUMMARY_FIELDS).limit(CODE_SCAN if code else limit)
    out = []
    for snap in q.stream():
        rec = snap.to_dict() or {}
        if code and not str(rec.get('uid') or '').startswith(code):
            continue
        out.append((snap.id, rec))
        if len(out) >= limit:
            break
    return out


def log_text(fs, prefix: str, log_id: str) -> tuple:
    """(record without its data, text) for one log."""
    logs, _quota = collections(fs, prefix)
    snap = logs.document(log_id).get()
    if not snap.exists:
        raise LogRefused(f'there is no log {log_id}')
    rec = snap.to_dict() or {}
    data = rec.pop('data', None)
    if data is None:
        raise LogRefused(f'log {log_id} carries no text')
    return rec, zlib.decompress(bytes(data)).decode('utf-8', 'replace')


def prune(fs, prefix: str, days: int = LOG_KEEP_DAYS, dry_run: bool = False,
          now: float = None) -> tuple:
    """(logs, daily counts) older than `days`, deleted unless `dry_run`."""
    from google.cloud.firestore_v1.base_query import FieldFilter
    now = time.time() if now is None else now
    cutoff = now - days * 86400
    logs, quota = collections(fs, prefix)
    old = list(logs.where(filter=FieldFilter('received_at', '<', cutoff))
               .select(['received_at']).stream())
    day = datetime.datetime.fromtimestamp(
        cutoff, datetime.timezone.utc).strftime('%Y-%m-%d')
    counts = [s for s in quota.select([]).stream() if s.id < day]
    if not dry_run:
        for snap in old + counts:
            snap.reference.delete()
    return len(old), len(counts)


def main(argv=None, out=None) -> int:
    out = out or sys.stdout
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('project', help='firebase://project')
    sub = ap.add_subparsers(dest='cmd', required=True)
    ls = sub.add_parser('list', help='the logs players have sent, newest first')
    ls.add_argument('--limit', type=int, default=20)
    ls.add_argument('--code', help='only the logs from the sign-in whose uid '
                                   'starts with this player code')
    sh = sub.add_parser('show', help='print one log whole')
    sh.add_argument('id')
    sh.add_argument('--out', help='write the text to this file instead')
    pr = sub.add_parser('prune', help='delete logs past the keep period')
    pr.add_argument('--days', type=int, default=LOG_KEEP_DAYS)
    pr.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)
    try:
        fs, prefix = open_project(args.project)
        if args.cmd == 'list':
            rows = list_logs(fs, prefix, args.limit, args.code)
            for log_id, rec in rows:
                print(summary_line(log_id, rec), file=out)
            if not rows:
                print('no logs' + (f' from player code {args.code}'
                                   if args.code else ''), file=out)
            return 0
        if args.cmd == 'show':
            rec, text = log_text(fs, prefix, args.id)
            if args.out:
                with open(args.out, 'w', encoding='utf-8', newline='\n') as f:
                    f.write(text)
                print(f'{summary_line(args.id, rec)}\nwritten to {args.out}',
                      file=out)
            else:
                print(summary_line(args.id, rec), file=out)
                print(text, file=out, end='' if text.endswith('\n') else '\n')
            return 0
        logs, counts = prune(fs, prefix, args.days, args.dry_run)
        verb = 'would delete' if args.dry_run else 'deleted'
        print(f'{verb} {logs} log(s) and {counts} daily count(s) older than '
              f'{args.days} days', file=out)
        return 0
    except (LogRefused, ValueError) as exc:
        print(f'refused: {exc}', file=out)
        return 2


if __name__ == '__main__':
    sys.exit(main())
