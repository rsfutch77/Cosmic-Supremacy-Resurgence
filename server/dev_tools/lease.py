"""
lease.py , one holder at a time for the machine's shared resources
==================================================================
    python lease.py take client "Lane A" "join acceptance" --minutes 20
    python lease.py release client "Lane A"
    python lease.py who
    python lease.py wait client "Lane A" "join acceptance" --minutes 20

Several agents work on this machine at once, and some things here have exactly
one of: the game client, the live Firebase project, the relay deploy, the
scheduled referee task. Each is taken by creating a lease file with exclusive
create, so two takers cannot both succeed, and released by deleting it.

    %TEMP%\\cosmic_leases\\<resource>.json   holder, purpose, started, until

A lease is never broken by another taker. One past its `until` by more than
`STALE_MINUTES` is reported as stale by `who`, and the coordinator decides.

**The live referee holds the client around every tick without taking a lease.**
It runs unattended from the scheduled task and reads its next deadline from the
galaxy, so `take client` refuses inside `TICK_MARGIN_MINUTES` either side of the
deadline the worker last recorded in `server\\worker_work\\worker_*.json`.

**Port 8888 is the referee worker's for as long as it runs.** Its `cs_server`
is where captures land. A client tool that needs a server there uses that one
and never kills or rebinds it, so the port is not leased separately.
"""
import argparse
import glob
import json
import os
import sys
import time

LEASES = os.path.join(os.environ.get('TEMP') or os.path.expanduser('~'),
                      'cosmic_leases')
RESOURCES = ('client', 'firebase-live', 'relay-deploy', 'referee-task')
STALE_MINUTES = 30
TICK_MARGIN_MINUTES = 10

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER_STATUS = os.path.join(os.path.dirname(HERE), 'worker_work',
                             'worker_*.json')


def path_of(resource):
    if resource not in RESOURCES:
        raise SystemExit(f'unknown resource {resource!r}; one of {RESOURCES}')
    return os.path.join(LEASES, f'{resource}.json')


def read(resource):
    try:
        with open(path_of(resource), encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return None


def next_tick():
    """The referee's next deadline as epoch seconds, or None if unknown."""
    best = None
    for p in glob.glob(WORKER_STATUS):
        try:
            with open(p, encoding='utf-8') as f:
                s = json.load(f)
            due = float(s['updated']) + float(s['seconds_left'])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        best = due if best is None else min(best, due)
    return best


def tick_conflict(minutes, now=None):
    """Why a client lease of this length would overlap a tick, or None."""
    now = time.time() if now is None else now
    due = next_tick()
    if due is None:
        return None
    margin = TICK_MARGIN_MINUTES * 60
    if now - margin < due < now + minutes * 60 + margin:
        return (f'the referee ticks at {time.strftime("%H:%M", time.localtime(due))}'
                f', inside this lease or {TICK_MARGIN_MINUTES} minutes of it')
    return None


def take(resource, holder, purpose, minutes):
    """True when the lease was taken; prints why when it was not."""
    os.makedirs(LEASES, exist_ok=True)
    if resource == 'client':
        why = tick_conflict(minutes)
        if why:
            print(f'refused: {why}')
            return False
    now = time.time()
    rec = {'resource': resource, 'holder': holder, 'purpose': purpose,
           'started': now, 'until': now + minutes * 60,
           'started_at': time.strftime('%H:%M:%S', time.localtime(now)),
           'until_at': time.strftime('%H:%M:%S',
                                     time.localtime(now + minutes * 60))}
    try:
        fd = os.open(path_of(resource), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        cur = read(resource) or {}
        print(f'refused: {resource} is held by {cur.get("holder")!r} for '
              f'{cur.get("purpose")!r} until {cur.get("until_at")}')
        return False
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(rec, f, indent=2)
    print(f'taken: {resource} by {holder!r} until {rec["until_at"]}')
    return True


def release(resource, holder):
    cur = read(resource)
    if cur is None:
        print(f'{resource} was not held')
        return True
    if cur.get('holder') != holder:
        print(f'refused: {resource} is held by {cur.get("holder")!r}, '
              f'not {holder!r}')
        return False
    os.remove(path_of(resource))
    print(f'released: {resource} by {holder!r}')
    return True


def who():
    now = time.time()
    for r in RESOURCES:
        cur = read(r)
        if cur is None:
            print(f'{r:14} free')
            continue
        late = (now - cur.get('until', now)) / 60
        flag = f'  STALE by {late:.0f} min' if late > STALE_MINUTES else ''
        print(f'{r:14} {cur.get("holder")!r} for {cur.get("purpose")!r} '
              f'until {cur.get("until_at")}{flag}')
    due = next_tick()
    if due:
        print(f'referee next tick {time.strftime("%H:%M", time.localtime(due))}'
              f' (client refused {TICK_MARGIN_MINUTES} min either side)')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('take', 'wait'):
        p = sub.add_parser(name)
        p.add_argument('resource')
        p.add_argument('holder')
        p.add_argument('purpose')
        p.add_argument('--minutes', type=float, default=20)
        p.add_argument('--poll', type=float, default=30)
    p = sub.add_parser('release')
    p.add_argument('resource')
    p.add_argument('holder')
    sub.add_parser('who')
    a = ap.parse_args()
    if a.cmd == 'take':
        return 0 if take(a.resource, a.holder, a.purpose, a.minutes) else 1
    if a.cmd == 'wait':
        while not take(a.resource, a.holder, a.purpose, a.minutes):
            time.sleep(a.poll)
        return 0
    if a.cmd == 'release':
        return 0 if release(a.resource, a.holder) else 1
    who()
    return 0


if __name__ == '__main__':
    sys.exit(main())
