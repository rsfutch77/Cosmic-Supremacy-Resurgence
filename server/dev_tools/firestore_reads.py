"""
firestore_reads.py: the project's Firestore reads per UTC day or hour
=====================================================================
    python server/dev_tools/firestore_reads.py
    python server/dev_tools/firestore_reads.py --days 14
    python server/dev_tools/firestore_reads.py --hours 48

Reads Cloud Monitoring, the same source as the 5 October 2026 figure for
`h3check` (16,763 reads): `firestore.googleapis.com/document/read_count`
summed over each UTC day for the whole project, beside the relay's
`run.googleapis.com/request_count`. A day or hour with no relay request had
no launcher open, so its reads are the referees' own.

The last line answers the referee's poll todo: the newest whole UTC day with
no relay request, and whether it read under DONE_UNDER documents. Idle hours
in the window, scaled to a day, are printed as an extrapolation only.

Nothing is written. It needs `monitoring.timeSeries.list` on the project,
through `gcloud auth print-access-token` or application default credentials.
"""
from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
import urllib.parse
import urllib.request

PROJECT = 'cs-resurgence'
DONE_UNDER = 2000  # the todo's bar for one idle galaxy's day

READS = 'firestore.googleapis.com/document/read_count'
WRITES = 'firestore.googleapis.com/document/write_count'
DELETES = 'firestore.googleapis.com/document/delete_count'
RELAY = 'run.googleapis.com/request_count'

API = 'https://monitoring.googleapis.com/v3/projects/{}/timeSeries'


def token() -> str:
    try:
        out = subprocess.run('gcloud auth print-access-token', shell=True,
                             capture_output=True, text=True, timeout=60)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    import google.auth
    import google.auth.transport.requests
    creds, _ = google.auth.default(
        scopes=['https://www.googleapis.com/auth/monitoring.read'])
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


def stamp(t: datetime.datetime) -> str:
    return t.strftime('%Y-%m-%dT%H:%M:%SZ')


def series(tok, project, metric, start, end, step) -> dict:
    """Sum of `metric` per `step` seconds, keyed by the bucket's start."""
    query = {
        'filter': f'metric.type="{metric}"',
        'interval.startTime': stamp(start),
        'interval.endTime': stamp(end),
        'aggregation.alignmentPeriod': f'{step}s',
        'aggregation.perSeriesAligner': 'ALIGN_SUM',
        'aggregation.crossSeriesReducer': 'REDUCE_SUM',
    }
    url = API.format(project) + '?' + urllib.parse.urlencode(query)
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {tok}'})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = json.load(r)
    got = {}
    for s in body.get('timeSeries', []):
        for p in s.get('points', []):
            got[p['interval']['startTime']] = int(p['value'].get('int64Value', 0))
    return got


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--project', default=PROJECT)
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--days', type=int, default=7,
                   help='whole UTC days to list, plus today so far')
    g.add_argument('--hours', type=int,
                   help='whole UTC hours to list instead of days')
    a = ap.parse_args(argv)

    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    if a.hours:
        step = 3600
        end = now.replace(minute=0, second=0, microsecond=0)
        start = end - datetime.timedelta(hours=a.hours)
    else:
        step = 86400
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = today - datetime.timedelta(days=a.days)
        # Today's partial bucket is listed but never counted as a whole day.
        end = today + datetime.timedelta(days=1)

    tok = token()
    cols = [series(tok, a.project, m, start, end, step)
            for m in (READS, WRITES, DELETES, RELAY)]

    print(f'{"UTC " + ("hour" if a.hours else "day"):<22}{"reads":>8}'
          f'{"writes":>8}{"deletes":>8}{"relay":>8}')
    idle_days = []
    idle_hour_reads = []
    t = start
    while t < end:
        key = stamp(t)
        reads, writes, deletes, relay = (c.get(key, 0) for c in cols)
        whole = t + datetime.timedelta(seconds=step) <= now
        mark = '' if whole else '  so far'
        if whole and relay == 0:
            mark = '  idle'
            if a.hours:
                idle_hour_reads.append(reads)
            else:
                idle_days.append((key[:10], reads))
        label = key[:13] + ':00' if a.hours else key[:10]
        print(f'{label:<22}{reads:>8,}{writes:>8,}{deletes:>8,}{relay:>8,}{mark}')
        t += datetime.timedelta(seconds=step)

    print()
    if a.hours:
        if idle_hour_reads:
            per_day = sum(idle_hour_reads) / len(idle_hour_reads) * 24
            print(f'{len(idle_hour_reads)} idle hours, about {per_day:,.0f} '
                  f'reads a day if every hour were like them (extrapolated)')
        else:
            print('No idle hour in the window: a launcher was open every hour.')
    elif idle_days:
        day, reads = idle_days[-1]
        verdict = 'under' if reads < DONE_UNDER else 'not under'
        print(f'Newest idle day {day}: {reads:,} reads, {verdict} {DONE_UNDER:,}.')
    else:
        print('No idle day in the window: a launcher was open every day, so '
              'no day measures the referee alone.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
