"""
emulators.py: start the emulator suite on ports that are actually free.

    python functions/emulators.py                  : start, printing the env
    python functions/emulators.py --print-env      : just resolve and print
    python functions/emulators.py --only firestore,storage
    python functions/emulators.py --project demo-other

Each port is the configured one when free, otherwise the next free one above
it. The resolved config is written to `.firebase.emulators.json` beside
`firebase.json` (it must sit here, since `functions.source` resolves relative to
the config) and passed to the CLI with `--config`. It and the env file are
gitignored build products.

Tests read `FIRESTORE_EMULATOR_HOST`, `STORAGE_EMULATOR_HOST` and
`FIREBASE_AUTH_EMULATOR_HOST` and fall back to the documented defaults.
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
# Storage's client library wants a URL; the others want host:port.
ENV_FOR = {
    'firestore': ('FIRESTORE_EMULATOR_HOST', '{host}:{port}'),
    'storage': ('STORAGE_EMULATOR_HOST', 'http://{host}:{port}'),
    'auth': ('FIREBASE_AUTH_EMULATOR_HOST', '{host}:{port}'),
}


def port_is_free(port: int, host: str = '127.0.0.1') -> bool:
    """Whether a listener can bind `port` right now.

    Do not set `SO_REUSEADDR`: on Windows it allows binding a port that is in
    use. `SO_EXCLUSIVEADDRUSE` is the strict Windows flag; a bare bind is strict
    elsewhere.
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

    `taken` holds ports this run has already handed out but nothing listens on.
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


# The storage emulator needs a rules file. firebase.json must not have a
# `storage` key, since it deploys functions only, so the reference goes into the
# generated copy.
STORAGE_RULES = os.path.join(os.path.dirname(HERE), 'server',
                             'beta_storage.rules')
LOCAL_STORAGE_RULES = '.storage.emulator.rules'


def add_storage_rules(cfg):
    """Point the emulator at the deny-all storage rules, if they are there."""
    if not os.path.exists(STORAGE_RULES):
        return cfg
    # The emulator refuses a rules path outside the project directory, so the
    # file is copied beside the generated config (gitignored).
    local = os.path.join(HERE, LOCAL_STORAGE_RULES)
    with open(STORAGE_RULES, encoding='utf-8') as src:
        body = src.read()
    with open(local, 'w', encoding='utf-8', newline=chr(10)) as out:
        out.write(body)
    cfg.setdefault('storage', {}).setdefault('rules', LOCAL_STORAGE_RULES)
    return cfg


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
    # A `demo-` project keeps an emulator run off real resources and makes the
    # Auth emulator mint tokens whose `aud` matches the tests. Without --project
    # the CLI uses the live project from .firebaserc.
    ap.add_argument('--project', default='demo-cs-resurgence',
                    help='the project the emulators serve')
    args = ap.parse_args(argv)

    cfg, ports, env = resolve(host=args.host)
    cfg = add_storage_rules(cfg)
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
           '--config', path, '--project', args.project]
    print('  ' + ' '.join(cmd), file=sys.stderr)
    try:
        return subprocess.call(cmd, cwd=HERE, shell=(os.name == 'nt'))
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    sys.exit(main())
