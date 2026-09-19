"""
fog_acceptance.py , does an order from a FILTERED client still merge?
=====================================================================
    python fog_acceptance.py --drop 3

The test that decides D1. "A filtered blob loads" is a much weaker claim than
it sounds: the thing a player's client has to do is **accept an order and hand
it back in a form the referee can apply against the unfiltered galaxy**. A
filter that quietly changes what comes back would not fail loudly. It would
produce a subtly wrong galaxy.

Under B2 a filtered blob never has to tick , a player's client runs the build
without T1-T5 and its clock is held , so this deliberately does not test that.

Two runs, and the control is the point:

    control   serve the UNFILTERED state, let the AI issue orders, capture,
              merge against the authoritative blob
    filtered  serve the FILTERED state, same AI, same civ, capture, merge
              against the SAME authoritative blob

Running only the filtered half would show that something merged, and leave
"would it have merged anyway, and identically" unanswered. A4's lesson: the
available conclusion was the wrong one because nobody ran the control.

**Nothing here touches the turn store.** No submission is made. The galaxy is
live and this is an experiment beside it, not in it.
"""
import argparse
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
import filter_blob
import order_diff
import merge_orders
import player_turn
import set_blob_player
from turn_store import open_store

WORK = os.path.join(ROOT, 'server', 'fog_work')
AI = os.path.join(ROOT, 'client', 'dev_tools', 'ai_player', 'ai.py')


def run_ai(civ, timeout=240):
    """One pass of the order generator against the running client."""
    r = subprocess.run(
        [sys.executable, AI, '--civ', civ, '--apply', '--vision', 'known',
         '--stall', '0', '--wait-for-client', '60'],
        capture_output=True, text=True, timeout=timeout,
        cwd=os.path.dirname(AI),
        env={**os.environ, 'PYTHONUNBUFFERED': '1',
             'CS_AI_STATE_DIR': os.path.join(WORK, 'ai_state')})
    tail = [l for l in (r.stdout or '').splitlines() if l.strip()][-6:]
    return r.returncode, tail


def one_run(label, to_serve, authoritative, civ):
    """Serve, let the AI act, capture, and report what merged."""
    print(f'\n=== {label} ===')
    print(f'  serving {len(to_serve):,} bytes')
    served_path = player_turn.serve(to_serve, civ, hold=player_turn.HOLD_SECONDS,
                                    log=lambda m: print('   ', m))
    served = open(served_path, 'rb').read()

    code, tail = run_ai(civ)
    print(f'  ai exit {code}')
    for l in tail:
        print('    ', l[:100])

    capture = player_turn.collect(f'fog{label[:6]}', log=lambda m: print('   ', m))
    got = sp.load_any(capture)
    print(f'  captured {len(got):,} bytes')

    lines = []
    mine, theirs, galaxy = order_diff.compare(served, got, civ,
                                              log=lines.append)
    print(f'  order_diff vs what was served: {mine} of {civ}\'s, '
          f'{theirs} other civs\', {galaxy} galaxy-level')
    for l in lines:
        t = l.strip()
        if t:
            print('     ', t[:100])

    merged_log = []
    merged = merge_orders.merge(authoritative, [(civ, got)],
                                log=merged_log.append)
    print(f'  merge against the UNFILTERED authoritative blob:')
    for l in merged_log:
        print('     ', l.strip()[:100])
    print(f'  merged result {len(merged):,} bytes')
    player_turn.close()
    return {'served': served, 'capture': got, 'merged': merged,
            'mine': mine, 'theirs': theirs, 'galaxy': galaxy,
            'merge_log': merged_log}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--drop', type=int, default=3)
    ap.add_argument('--civ', default=None)
    a = ap.parse_args()

    import json
    cfg = json.load(open(os.path.join(ROOT, 'release', 'data',
                                      'multiplayer.json'), encoding='utf-8'))
    civ = a.civ or cfg['civ']
    store = open_store(cfg['store'])
    turn, _ = store.current()
    base = store.turn_blob(turn)
    os.makedirs(WORK, exist_ok=True)
    print(f'galaxy turn {turn}, {len(base):,} bytes, civ {civ}')
    print('THE STORE IS NOT WRITTEN TO BY THIS SCRIPT')

    filtered = filter_blob.drop_systems(base, a.drop, log=lambda m: print(' ', m))
    open(os.path.join(WORK, 'base.dat'), 'wb').write(base)
    open(os.path.join(WORK, 'filtered.dat'), 'wb').write(filtered)

    control = one_run('control', base, base, civ)
    test = one_run('filtered', filtered, base, civ)

    print('\n=== verdict ===')
    print(f'  control  {control["mine"]} order(s) of {civ}\'s, '
          f'merged {len(control["merged"]):,} bytes')
    print(f'  filtered {test["mine"]} order(s) of {civ}\'s, '
          f'merged {len(test["merged"]):,} bytes')
    same = control['merged'] == test['merged']
    print(f'  merged results identical: {same}')
    if not same:
        print('  NOT identical. That is not automatically a failure , the AI '
              'may legitimately choose differently when it can see less , but '
              'it does mean this run cannot show the filter is transparent.')
    print(f'  filtered run leaked other civs\' changes: '
          f'{bool(test["theirs"] or test["galaxy"])}')


if __name__ == '__main__':
    main()
