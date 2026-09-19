"""
check_store.py , can this machine see the galaxy, and if not, why
=================================================================
    python check_store.py                       # read the launcher's config
    python check_store.py <path-or-url>
    python check_store.py --civ Neighbor <path>

"No galaxy in that folder" has several causes that look identical from the
launcher: the path is wrong, the share is unreachable, the credentials are
missing, the store has not been created yet, or the config's backslashes were
eaten somewhere between being typed and being parsed. This separates them.

Run it on the machine that is failing. It reports what the config actually
holds, byte for byte, then walks the path from the top down so the first
component that cannot be reached is named.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, 'server'))

import turn_store


def show_config(data_dir):
    path = os.path.join(data_dir, 'multiplayer.json')
    print(f'config: {path}')
    if not os.path.exists(path):
        print('  MISSING. The launcher needs this file to know which galaxy '
              'and which civ.')
        return None
    raw = open(path, encoding='utf-8').read()
    print(f'  raw bytes: {raw.strip()!r}')
    try:
        cfg = json.loads(raw)
    except ValueError as exc:
        print(f'  NOT VALID JSON: {exc}')
        print('  a Windows path needs each backslash doubled in JSON, so a UNC '
              'path starts with four')
        return None
    print(f'  parsed store: {cfg.get("store")!r}')
    print(f'  parsed civ:   {cfg.get("civ")!r}')
    return cfg


def walk_path(p):
    """Name the first component that cannot be reached."""
    p = os.path.abspath(p)
    if p.startswith('\\\\'):
        parts = p.split(os.sep)
        # \\ host share rest...
        stems, acc = [], os.sep * 2 + parts[2]
        if len(parts) > 3:
            acc = os.sep * 2 + os.path.join(parts[2], parts[3])
            stems.append(acc)
            for extra in parts[4:]:
                acc = os.path.join(acc, extra)
                stems.append(acc)
    else:
        stems, acc = [], ''
        for part in p.split(os.sep):
            acc = (part + os.sep) if not acc else os.path.join(acc, part)
            stems.append(acc)
    for stem in stems:
        try:
            ok = os.path.exists(stem)
        except Exception as exc:
            print(f'  {"error":<7} {stem}   ({exc})')
            return False
        print(f'  {"ok" if ok else "MISSING":<7} {stem}')
        if not ok:
            return False
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('store', nargs='?',
                    help='a directory or base URL; default is the config')
    ap.add_argument('--civ')
    ap.add_argument('--data-dir',
                    default=os.path.join(REPO, 'release', 'data'))
    a = ap.parse_args()

    spec, civ = a.store, a.civ
    if spec is None:
        cfg = show_config(a.data_dir)
        if cfg is None:
            return 1
        spec, civ = cfg.get('store'), civ or cfg.get('civ')
        print()

    print(f'store spec: {spec!r}')
    store = turn_store.open_store(spec)
    print(f'  resolved to {store.__class__.__name__}')

    if isinstance(store, turn_store.TurnStore):
        print(f'  root: {store.root!r}')
        print('  walking the path:')
        if not walk_path(store.root):
            print('\nThe first MISSING line above is the problem. If it is the '
                  'share itself,\nthis machine cannot reach it: check the host '
                  'is on, that you can open it\nin Explorer, and that a VPN is '
                  'not capturing the local network.')
            return 1
    else:
        print(f'  base: {store.base}')

    try:
        ok = store.exists()
    except Exception as exc:
        print(f'  exists() raised {type(exc).__name__}: {exc}')
        return 1
    print(f'  exists(): {ok}')
    if not ok:
        print('\nThe path is reachable but holds no galaxy. Either the referee '
              'has not\npublished a first turn yet, or this is the wrong '
              'folder.')
        return 1

    turn, _deadline = store.current()
    civs = store.civs()
    print(f'\ngalaxy: turn {turn}, {store.seconds_left():.0f}s left')
    print(f'  roster: {civs}')
    print(f'  submitted for this turn: {sorted(store.submissions(turn)) or "(none)"}')
    if civ:
        print(f'  playing as {civ!r}: '
              f'{"in the roster" if civ in civs else "NOT IN THE ROSTER"}')
        if civ not in civs:
            print('  a civ outside the roster can play, but the referee will '
                  'not wait for it\n  and merge_orders will ignore its '
                  'submissions')
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
