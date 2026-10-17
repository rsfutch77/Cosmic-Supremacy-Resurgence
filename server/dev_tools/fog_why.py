"""
fog_why.py: what the client is actually doing when a filtered blob "fails"
    python fog_why.py                 # serve a known-failing blob and look

Launches the client on a filtered blob known not to load, without waiting for
game state, and lists its windows at intervals with `list_dialogs.py`. On such
a blob the client stays alive behind a modal dialog, which
`game_cycle.launch` reports only as a timeout.
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'), HERE,
          os.path.join(ROOT, 'client', 'dev_tools'),
          os.path.join(ROOT, 'client', 'dev_tools', 'ai_player')):
    if d not in sys.path:
        sys.path.insert(0, d)

import save_parser as sp
import set_blob_player
import fog_bisect as fb
import game_cycle as gc

DEV = os.path.join(ROOT, 'client', 'dev_tools')


def windows():
    r = subprocess.run([sys.executable, os.path.join(DEV, 'list_dialogs.py')],
                       capture_output=True, text=True, cwd=DEV, timeout=60)
    return (r.stdout or '') + (r.stderr or '')


def main():
    blob = sp.load_any(os.path.join(ROOT, 'server', 'fog_work', 'fix80.dat'))
    # [124, 130] is the smallest reliably failing case.
    bad = fb.drop_n(blob, [124, 130], 2)
    stamped = set_blob_player.set_player(bad, 'Alice', log=lambda *a: None)
    dat = os.path.join(ROOT, 'server', 'fog_work', 'why.dat')
    with open(dat, 'wb') as f:
        f.write(stamped)

    gc.close_client()
    print('launching on a blob known to fail, and NOT waiting for state')
    exe = gc.resolve_exe('player')
    proc = subprocess.Popen([exe, dat], cwd=os.path.dirname(exe))
    try:
        for wait in (5, 10, 20, 40):
            time.sleep(wait)
            print(f'\n--- after ~{wait}s ---')
            print(windows().strip()[:2000])
    finally:
        try:
            proc.terminate()
        except Exception:                                   # noqa: BLE001
            pass
        gc.close_client()


if __name__ == '__main__':
    main()
