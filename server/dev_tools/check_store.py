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
component that cannot be reached is named, with the operating system's own
error rather than a bare "missing".

**A host spelled as an address and the same host spelled by name are different
SMB targets.** Windows keeps sessions per target, so a machine holding an
authenticated session to `\\POWERHOUSE1` falls back to anonymous for
`\\192.168.0.4` and is refused, while the other machine, which reaches the
share locally, sees nothing wrong at all. That cost a round of wrong guesses
between two machines, so when a UNC target fails this tries the other spelling
and says whether it works.
"""
import argparse
import json
import os
import socket
import subprocess
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


# Windows error codes worth translating, because the number alone sends people
# to the wrong fix.
WINERR = {
    3: 'that path does not exist on the share, so the store has probably not '
       'been created there yet',
    5: 'access denied',
    53: 'network path not found, the host did not answer',
    64: 'the specified network name is no longer available',
    67: 'the share name was not found on that host',
    86: 'the network password is not correct',
    1326: 'the user name or password is incorrect, which for a share usually '
          'means this machine has no authenticated session to that target',
    1219: 'multiple connections to a server by the same user are not allowed, '
          'an existing session to the other spelling of this host is in the way',
}


def explain(exc):
    code = getattr(exc, 'winerror', None) or getattr(exc, 'errno', None)
    why = WINERR.get(code)
    return f'error {code}' + (f', {why}' if why else f': {exc}')


def unc_target(p):
    """The host component of a UNC path, or None."""
    if not p.startswith(os.sep * 2):
        return None
    rest = p[2:].split(os.sep)
    return rest[0] if rest and rest[0] else None


def other_spellings(host):
    """The same host spelled the other way, for a second attempt."""
    out = []
    try:
        socket.inet_aton(host)
        is_ip = True
    except OSError:
        is_ip = False
    try:
        if is_ip:
            name = socket.gethostbyaddr(host)[0]
            out.append(name.split('.')[0])
        else:
            out.append(socket.gethostbyname(host))
    except Exception:
        pass
    return [o for o in out if o and o.lower() != host.lower()]


def sessions():
    """Existing SMB sessions, so a session to another spelling is visible."""
    try:
        r = subprocess.run(['net', 'use'], capture_output=True, text=True,
                           timeout=15)
    except Exception:
        return []
    out = []
    for line in (r.stdout or '').splitlines():
        if os.sep * 2 in line:
            out.append(line.strip())
    return out


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
            err = None
        except OSError as exc:
            ok, err = False, exc
        # os.path.exists swallows the reason, so ask again when it says no
        if not ok and err is None and stem.startswith(os.sep * 2):
            try:
                os.listdir(stem)
                ok = True
            except OSError as exc:
                err = exc
        print(f'  {"ok" if ok else "NO":<7} {stem}'
              + (f'   {explain(err)}' if err is not None else ''))
        if not ok:
            _suggest(stem)
            return False
    return True


def _suggest(stem):
    """When a UNC component fails, try the host spelled the other way."""
    host = unc_target(stem)
    if host is None:
        return
    live = sessions()
    if live:
        print('\n  existing SMB sessions on this machine:')
        for s in live:
            print(f'    {s}')
    alts = other_spellings(host)
    if not alts:
        print(f'\n  {host} has no other spelling to try.')
        return
    tail = stem[2 + len(host):]
    for alt in alts:
        cand = os.sep * 2 + alt + tail
        try:
            os.listdir(cand)
            reachable = True
        except OSError as exc:
            reachable, why = False, explain(exc)
        if reachable:
            print(f'\n  BUT {cand} WORKS.')
            print('  A host spelled as an address and the same host spelled by '
                  'name are different\n  SMB targets, and Windows keeps '
                  'sessions per target. Point the config at the\n  spelling '
                  'that works.')
        else:
            print(f'\n  {cand} also fails: {why}')


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
            print('\nThe first NO line above is the problem, and its error '
                  'says which kind:\n'
                  '  the host      unreachable, or no session to that '
                  'spelling of it\n'
                  '  the share     a wrong name, or no permission on it\n'
                  '  a subfolder   the store has not been created there')
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
