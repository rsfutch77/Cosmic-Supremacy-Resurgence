"""
test_dice_seed.py: each turn's dice are seeded, archived and reused
===================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_dice_seed.py

The referee seeds the client's `rand()` before a turn advances
(`referee.turn_seed`, `rand_state.seed`), archives the seed the tick ran
with, and `verify_turn` reruns the turn with that same seed. The engine tick
is stubbed here, as `test_end_vote.py` does; the seeding of a real client is
checked by hand with `client/dev_tools/rand_state.py`.

Uses turn 9 of `server/galaxy_demo`.
"""
import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools'),
          os.path.join(ROOT, 'client', 'dev_tools')):
    if d not in sys.path:
        sys.path.insert(0, d)

import save_parser as sp                                        # noqa: E402
import player_turn                                              # noqa: E402
import referee                                                  # noqa: E402
from turn_store import TurnStore                                # noqa: E402

GALAXY = os.path.join(ROOT, 'server', 'galaxy_demo')
TURN = 9
ROSTER = ['DemoPlayer', 'Neighbor', 'BadGuy']
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def quiet(*_a, **_k):
    pass


def set_turn(blob, turn):
    tree = sp.parse_blob(blob)
    glob = next(tree[0].find('GLOB'))
    out = bytearray(blob)
    struct.pack_into('<I', out, glob.payload, turn)
    return bytes(out)


def stubbed(seen, applies=True):
    """A tick that records the seed it was given and reports it applied."""
    def fake_tick(blob, turns=1, secs=10, save_dir=None, log=print,
                  seed=None, applied=None, **kw):
        seen.append(seed)
        if applied is not None:
            applied['seed'] = seed if applies else None
        return set_turn(blob, referee.turn_of(blob) + 1)
    return fake_tick


def test_seed():
    print('the seed')
    blob = TurnStore(GALAXY).turn_blob(TURN)
    a = referee.turn_seed(blob)
    check('it is a u32', 0 <= a < 2 ** 32)
    check('the same blob gives the same seed', referee.turn_seed(blob), a)
    check('another turn gives another', referee.turn_seed(set_turn(blob, TURN + 1)) != a)
    other = bytearray(blob)
    other[-20] ^= 0xFF
    check('another merge of the turn gives another',
          referee.turn_seed(bytes(other)) != a)


def test_no_client():
    print('\nwith no client to seed')
    import rand_state
    real = rand_state.find_pid
    rand_state.find_pid = lambda *a, **k: (None, None)
    lines = []
    try:
        got = referee.seed_dice(0x1234, log=lines.append)
    finally:
        rand_state.find_pid = real
    check('nothing is seeded', got, None)
    check('and the log says the dice start as a fresh load',
          any('fresh load' in ln for ln in lines))


def test_referee(root):
    print('\nthe referee archives the seed and verify reuses it')
    served = TurnStore(GALAXY).turn_blob(TURN)
    store = TurnStore(root)
    store.start(served, ROSTER, turn_seconds=1800, turn=TURN)
    seen = []
    real_tick, real_ready = referee.tick, player_turn.save_path_ready
    referee.tick = stubbed(seen)
    player_turn.save_path_ready = lambda: (True, '')
    try:
        referee.resolve_turn(store, log=quiet)
        rec = store.archive_record(TURN)
        check('the tick was given a seed', seen[0] is not None)
        check('the seed is the merged turn\'s', seen[0],
              referee.turn_seed(served))
        check('the archive holds it', rec.get('rand_seed'), seen[0])
        referee.verify_turn(store, TURN, recompute=True, log=quiet)
        check('verify_turn ticks again with the archived seed', seen[-1],
              rec['rand_seed'])
        rec['rand_seed'] = None
        store.archive(TURN, rec)
        referee.verify_turn(store, TURN, recompute=True, log=quiet)
        check('a turn archived unseeded is rerun unseeded', seen[-1], None)
    finally:
        referee.tick, player_turn.save_path_ready = real_tick, real_ready

    print('\na tick that could not seed')
    root2 = root + '_unseeded'
    store = TurnStore(root2)
    store.start(served, ROSTER, turn_seconds=1800, turn=TURN)
    referee.tick = stubbed([], applies=False)
    player_turn.save_path_ready = lambda: (True, '')
    try:
        referee.resolve_turn(store, log=quiet)
        check('is archived as unseeded', store.archive_record(TURN).get(
            'rand_seed', 'missing'), None)
    finally:
        referee.tick, player_turn.save_path_ready = real_tick, real_ready


def main():
    if not os.path.isdir(GALAXY):
        print(f'no fixture at {GALAXY}')
        return 2
    tmp = tempfile.mkdtemp(prefix='dice_')
    try:
        test_seed()
        test_no_client()
        test_referee(os.path.join(tmp, 'g'))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
