"""
test_replay_endpoint.py: the launcher's replay endpoint, against a stand-in client

    python release\\tests\\test_replay_endpoint.py

A socket plays a client in replay mode: it logs in with `RQLG` and `LGIN`, then
asks `RQTN` for turns, each wrapped in `CMND` as the client sends it. A folder
galaxy holds turns 180 to 183 with each civ's views. No game client and no
window.
"""
import base64
import os
import socket
import struct
import sys
import tempfile
import zlib

import pathlib
REPO = str(pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, os.path.join(REPO, "release"))
sys.path.insert(0, os.path.join(REPO, "server"))
sys.path.insert(0, os.path.join(REPO, "server", "dev_tools"))

import save_parser as sp                                        # noqa: E402
import referee                                                  # noqa: E402
import replay_endpoint as re_                                   # noqa: E402
import scan_endpoint as se                                      # noqa: E402
import turn_store                                               # noqa: E402

FIX = os.path.join(REPO, "server", "tests", "fixtures", "diplomacy")
ROSTER = ["GoodGuy", "BadGuy"]
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def command(tag, payload=b''):
    """What the client sends: the section deflated inside `CMND`."""
    inner = se.section(tag, 0, payload)
    return se.section(b'CMND', 0, struct.pack('<I', len(inner))
                      + zlib.compress(inner))


def answer(c):
    """The next section the endpoint sends: (tag, whole section bytes)."""
    tag, v, payload = read_any(c)
    return tag, se.section(tag, v, payload)


def read_any(c):
    head = se.recv_exact(c, 8)
    word = struct.unpack_from('<I', head, 4)[0]
    return head[:4], word >> 26, se.recv_exact(c, word & re_.SIZE_MASK)


def glob_turns(blob):
    """(turn, last turn) from `GLOB`."""
    at = 12
    while True:
        size = struct.unpack_from('<I', blob, at + 4)[0] & re_.SIZE_MASK
        if blob[at:at + 4] == b'GLOB':
            return struct.unpack_from('<ii', blob, at + 8)
        at += 8 + size


def fail_text(section):
    n = struct.unpack_from('<I', section, 8)[0]
    return section[12:12 + n].decode('latin-1')


def run_parts(view):
    print('the pass and the stamp')
    text = re_.pass_text()
    check('the pass file is 32 characters, which the login needs',
          len(text), 32)
    check('and names the replay port on this machine',
          base64.b64decode(text).split()[:2], [b'127.0.0.1', b'4710'])
    stamped = re_.stamp_last_turn(view, 183)
    check('the stamp sets GLOB\'s last turn', glob_turns(stamped)[1], 183)
    diff = [i for i in range(len(view)) if view[i] != stamped[i]]
    check('and changes nothing else', len(diff) <= 4 and len(stamped)
          == len(view))
    try:
        re_.stamp_last_turn(b'SAVE' + struct.pack('<I', 0), 1)
        refused = False
    except ValueError:
        refused = True
    check('a save with no GLOB is refused', refused)


def run_endpoint(store, held):
    print('\nthe endpoint')
    said = []
    ep = re_.ReplayEndpoint(lambda: (store, 'BadGuy', 183), port=0,
                            log=said.append)
    check('it listens', ep.start())
    try:
        with socket.create_connection(('127.0.0.1', ep.port), timeout=10) as c:
            c.sendall(command(b'RQLG', struct.pack('<I', 0xC51337A1)))
            tag, sec = answer(c)
            check('RQLG is answered SUCC holding an empty string',
                  (tag, sec[8:]), (b'SUCC', struct.pack('<I', 0)))
            c.sendall(command(b'LGIN', b'\x0d\0\0\0#Galaxy@Pass#'))
            check('and LGIN', answer(c)[0], b'SUCC')
            tag, sec = answer(c)
            check('then the newest turn comes as SAVE', tag, b'SAVE')
            check('the civ\'s own view, stamped with the newest turn',
                  sec == re_.stamp_last_turn(held['BadGuy'], 183))
            c.sendall(command(b'RQTN', struct.pack('<I', 181)))
            tag, sec = answer(c)
            check('RQTN 181 answers that turn\'s view',
                  (tag, sec == re_.stamp_last_turn(held['BadGuy'], 183)),
                  (b'SAVE', True))
            check('and the client can step forward to 183',
                  glob_turns(sec)[1], 183)
            c.sendall(command(b'ALIV'))
            c.sendall(command(b'RQTN', struct.pack('<I', 0)))
            check('ALIV needs no answer, and RQTN 0 is the newest turn',
                  answer(c)[0], b'SAVE')
            c.sendall(command(b'RQTN', struct.pack('<I', 182)))
            tag, sec = answer(c)
            check('a turn kept for no view of this civ answers FAIL',
                  (tag, fail_text(sec)),
                  (b'FAIL', 'Turn 182 is not available: turn 182 is not kept '
                            'for BadGuy.'))
            c.sendall(command(b'RQTN', struct.pack('<I', 184)))
            tag, sec = answer(c)
            check('and a turn not yet played',
                  (tag, 'has not been played' in fail_text(sec)),
                  (b'FAIL', True))
            c.sendall(command(b'RQSV'))
            check('RQSV, a resync, answers the newest turn', answer(c)[0],
                  b'SAVE')
    finally:
        ep.stop()
    check('each turn sent is logged',
          any('BadGuy turn 181 sent' in s for s in said))

    print('\nno galaxy to replay')
    ep = re_.ReplayEndpoint(lambda: None, port=0, log=said.append)
    ep.start()
    try:
        with socket.create_connection(('127.0.0.1', ep.port), timeout=10) as c:
            c.sendall(command(b'RQLG') + command(b'LGIN'))
            answer(c)
            answer(c)
            try:
                got = read_any(c)
            except (EOFError, ConnectionResetError):
                got = None
            check('the login is answered and the connection closes', got, None)
    finally:
        ep.stop()


def main():
    first = sp.load_any(os.path.join(FIX, "d0.b64"))
    blob = sp.load_any(os.path.join(FIX, "d9.b64"))
    held = referee.views(blob, ROSTER, log=lambda *_: None)
    published = referee.published_views(blob, held)
    run_parts(held['BadGuy'])
    with tempfile.TemporaryDirectory() as tmp:
        store = turn_store.TurnStore(os.path.join(tmp, "galaxy"))
        store.start(first, ROSTER, turn=180)
        store.publish(181, blob, views=published)
        store.publish(182, blob, views={c: v for c, v in published.items()
                                        if c != 'BadGuy'})
        store.publish(183, blob, views=published)
        # The replay opens only once the galaxy has ended.
        store.close()
        run_endpoint(store, held)
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
