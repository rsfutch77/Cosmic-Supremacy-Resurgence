"""
test_capture_pruning.py: saves/ keeps the newest captures and every saved slot
    server\\.venv\\Scripts\\python.exe server\\tests\\test_capture_pruning.py

The launcher captures the player's turn every five seconds, and each capture
is three files. With CS_KEEP_CAPTURES set, cs_server deletes all but the
newest captures after each save, and never a file a save slot names.
"""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, 'server'))

# Read by cs_server at import.
os.environ['CS_DATA_DIR'] = tempfile.mkdtemp(prefix='cs_prune_')
os.environ['CS_KEEP_CAPTURES'] = '3'
import cs_server as cs                                          # noqa: E402

fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        fails.append(label)


def trio(seq, stamp='20261127_120000', g=0, t=5):
    base = f'save_{seq:03d}_{stamp}_g{g}_t{t}'
    return [base + ext for ext in ('.b64', '.body', '.json')]


def save(gameid='0', name='resident', turn='5'):
    body = (f"userid=0&passhash=''&gameid={gameid}&gamename='{name}'"
            f"&turn={turn}&version=1&data=QUJD")
    params = {'gameid': [gameid], 'gamename': [f"'{name}'"], 'turn': [turn],
              'version': ['1'], 'data': ['QUJD']}
    return cs.handle_action('savegame', params, body)


def captures():
    return sorted(n for n in os.listdir(cs.SAVE_DIR) if n.endswith('.b64'))


def settle():
    """Wait for the prune thread to finish."""
    for _ in range(200):
        if not any(t.name == 'prune-captures' for t in
                   __import__('threading').enumerate()):
            return
        time.sleep(0.01)


print("1. numbering")
check("an empty folder starts at 0", cs._next_seq([]), 0)
check("numbers continue past the highest, not the count",
      cs._next_seq(trio(4) + trio(9) + ['index.json']), 10)

print("\n2. what goes")
names = trio(1) + trio(2) + trio(3) + trio(4) + trio(5) + ['index.json']
doomed = cs.captures_to_prune(names, 2, slots=[])
check("all but the newest two captures go, three files each", len(doomed), 9)
check("the newest stay", any(n.startswith('save_005') or n.startswith('save_004')
                             for n in doomed), False)
check("a file that is not a capture is never named",
      'index.json' in doomed, False)
doomed = cs.captures_to_prune(names, 2, slots=[trio(1)[0]])
check("a capture a save slot names stays however old",
      any(n.startswith('save_001') for n in doomed), False)
check("keeping as many as there are deletes nothing",
      cs.captures_to_prune(names, 5, slots=[]), [])

print("\n3. through savegame")
check("the limit comes from CS_KEEP_CAPTURES", cs.KEEP_CAPTURES, 3)
status, _, body = save(gameid='-1', name='Before war', turn='4')
check("a player's own save is answered", body, 'DONE')
slot = [k for k in cs._read_index() if k != '0']
for i in range(8):
    save()
    settle()
left = captures()
check("eight captures later the folder holds the limit plus the saved slot",
      len(left), 4)
idx = cs._read_index()
check("the player's save is still on disk",
      os.path.exists(os.path.join(cs.SAVE_DIR, idx[slot[0]]['file'])), True)
check("and slot 0 names the newest capture, which is kept",
      idx['0']['file'], left[-1])
check("every capture kept has its sidecars",
      all(os.path.exists(os.path.join(cs.SAVE_DIR, n[:-4] + ext))
          for n in left for ext in ('.body', '.json')), True)
check("no two captures share a number",
      len({n.split('_')[1] for n in left}), len(left))
check("the index survives", os.path.exists(cs.SAVE_INDEX), True)

print()
if fails:
    print(f"FAILED: {len(fails)}")
    for f in fails:
        print(f"  - {f}")
    sys.exit(1)
print("all passed")
