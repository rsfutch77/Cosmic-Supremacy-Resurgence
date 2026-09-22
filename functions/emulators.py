"""
emulators.py , start the emulator suite on ports that are actually free.

    python functions/emulators.py                  , start, printing the env
    python functions/emulators.py --print-env      , just resolve and print
    python functions/emulators.py --only firestore,storage

The Firebase CLI binds whatever `firebase.json` names and fails if something
already holds it. The defaults it ships are popular numbers: 8080 is the first
port a great many things take, and on this machine a torrent client holds it, so
`emulators:start` failed on a clean checkout with an error about Firestore
rather than about the port.

Rather than pick different fixed numbers, which only moves the collision, this
resolves each port at startup: the configured one when it is free, otherwise the
next free one above it. The resolved set is written to `.firebase.emulators.json`
beside `firebase.json`, and the CLI is pointed at that copy with `--config`.

The copy lives in this directory on purpose. `firebase.json` resolves
`functions.source` relative to the config file, so a config written to a
temporary directory would look for the function's code there and find nothing.

`.firebase.emulators.json` and the env file are build products and gitignored.

Tests read `FIRESTORE_EMULATOR_HOST`, `STORAGE_EMULATOR_HOST` and
`FIREBASE_AUTH_EMULATOR_HOST` and fall back to the documented defaults, so a run
started here and a run started by hand on the defaults both work. `--print-env`
exists so a shell can pick the values up without starting anything.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import socket
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, 'firebase.json')
RESOLVED = os.path.join(HERE, '.firebase.emulators.json')
ENV_FILE = os.path.join(HERE, '.firebase.emulators.env')

# Which emulator feeds which environment variable, and how the value is spelled.
# Storage's client library wants a URL and the other two want host:port, which
# is a difference in the libraries rather than in the emulators.
ENV_FOR = {
    'firestore': ('FIRESTORE_EMULATOR_HOST', '{host}:{port}'),
    'storage': ('STORAGE_EMULATOR_HOST', 'http://{host}:{port}'),
    'auth': ('FIREBASE_AUTH_EMULATOR_HOST', '{host}:{port}'),
}


def port_is_free(port: int, host: str = '127.0.0.1') -> bool:
    """Whether a listener can bind `port` right now.

    Binding is the only honest test. A connect that is refused says nothing
    about a socket held in TIME_WAIT or bound to another interface, and both of
    those still fail the emulator.

    **Do not set `SO_REUSEADDR` here.** On Windows it does not mean what it
    means on POSIX: it permits binding a port another socket is actively bound
    to, so the probe succeeds against a port that is genuinely in use and this
    function answers free for everything. The first version did set it, and a
    test holding 8080 and 8081 watched the resolver hand back 8080 anyway.
    `SO_EXCLUSIVEADDRUSE` is the Windows flag that means what POSIX callers
    expect, and a bare bind is the strict test everywhere else.
    """
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        excl = getattr(socket, 'SO_EXCLUSIVEADDRUSE', None)
        if excl is not None:
            try:
                s.setsockopt(socket.SOL_SOCKET, excl, 1)
            except OSError:
                pass
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def free_port_from(preferred: int, taken=(), host: str = '127.0.0.1') -> int:
    """`preferred` when it is free, else the next free port above it.

    `taken` holds ports this run has already handed out, since two emulators
    resolved in the same pass would otherwise both be given the same number:
    nothing is listening on it yet.
    """
    port = preferred
    while port < 65536:
        if port not in taken and port_is_free(port, host):
            return port
        port += 1
    raise SystemExit(f'no free port at or above {preferred}')


def resolve(config_path: str = CONFIG, host: str = '127.0.0.1'):
    """(resolved config dict, {emulator: port}, [(env name, value)])."""
    with open(config_path, encoding='utf-8') as fh:
        cfg = json.load(fh)
    emus = cfg.setdefault('emulators', {})
    ports, taken, env = {}, [], []
    for name in sorted(k for k in emus if isinstance(emus[k], dict)
                       and 'port' in emus[k]):
        want = int(emus[name]['port'])
        got = free_port_from(want, taken, host)
        emus[name]['port'] = got
        ports[name] = got
        taken.append(got)
        if name in ENV_FOR:
            var, shape = ENV_FOR[name]
            env.append((var, shape.format(host=host, port=got)))
    return cfg, ports, env


def write_resolved(cfg, env) -> str:
    """Write the resolved config and the env file, and return the config path."""
    with open(RESOLVED, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(cfg, fh, indent=2)
        fh.write('\n')
    with open(ENV_FILE, 'w', encoding='utf-8', newline='\n') as fh:
        for var, val in env:
            fh.write(f'{var}={val}\n')
    return RESOLVED


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('--only', default='firestore,storage,auth',
                    help='emulators to start, as the CLI spells them')
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--print-env', action='store_true',
                    help='resolve and print, start nothing')
    args = ap.parse_args(argv)

    cfg, ports, env = resolve(host=args.host)
    path = write_resolved(cfg, env)

    for name, port in sorted(ports.items()):
        with open(CONFIG, encoding='utf-8') as fh:
            want = json.load(fh)['emulators'][name]['port']
        moved = '' if port == want else f'  (moved from {want}, in use)'
        print(f'  {name:<10} {port}{moved}', file=sys.stderr)
    for var, val in env:
        print(f'{var}={val}')
    if args.print_env:
        return 0

    for var, val in env:
        os.environ[var] = val
    cmd = ['firebase', 'emulators:start', '--only', args.only,
           '--config', path]
    print('  ' + ' '.join(cmd), file=sys.stderr)
    try:
        return subprocess.call(cmd, cwd=HERE, shell=(os.name == 'nt'))
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    sys.exit(main())
