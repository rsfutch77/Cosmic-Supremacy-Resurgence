"""
test_scan_types.py: every scan type, from the scan button and from the referee
==============================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_scan_types.py

Starts the real game twice, so it is left out of suite loops. It takes the
client lease and refuses to run while someone else holds it. It uses the
capture server on 127.0.0.1:8888, starting `cs_server.py` there only when the
port is free and stopping only that process.

The galaxy is `unruled/d4` with two of every scan in GoodGuy's stock and a
civ name for BadGuy, since a leader name scan needs one. In one client
GoodGuy scans BadGuy through the scan button's own routine (`0x00576D40`),
offline, as a player without the patch would. In another the referee runs the
same scans (`scan.run_scans`). Each report must come out the same, apart from
the 8 bytes at the planetary report's `PLPR` payload +15 that the planet copy
never writes.

A fleet scan needs a `Fleet` target, and no galaxy here holds one, so both
sides make no report and keep the scan.
"""
import os
import socket
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SERVER = os.path.join(ROOT, 'server')
for d in (SERVER, os.path.join(SERVER, 'dev_tools'),
          os.path.join(ROOT, 'client', 'dev_tools'),
          os.path.join(ROOT, 'client', 'dev_tools', 'ai_player')):
    if d not in sys.path:
        sys.path.insert(0, d)

import fog                                                      # noqa: E402
import game_cycle as gc                                         # noqa: E402
import lease                                                    # noqa: E402
import merge_orders as mo                                       # noqa: E402
import player_turn                                              # noqa: E402
import remote                                                   # noqa: E402
import save_parser as sp                                        # noqa: E402
import scan                                                     # noqa: E402

FIX = os.path.join(HERE, 'fixtures', 'unruled')
WORK = os.path.join(SERVER, 'referee_work')
HOLDER = 'test_scan_types'
GOOD, BAD = 656, 660
PORT = 8888

# Each type's target in d4: BadGuy's planet 384, its sun 383, ship 662 (the
# one on a route), and ships 677 and 678 at 384.
TARGETS = {0: 384, 1: 383, 2: 662, 3: 677, 4: 678, 5: 384, 6: 384, 7: 384}
REPORT_TAGS = {0: b'SCPL', 1: b'SCSY', 2: b'SCRO', 3: None, 4: b'SCML',
               5: b'SCTE', 6: b'SCEG', 7: b'SCLN'}
PLPR_LEFTOVER = (15, 23)        # PLPR payload bytes the planet copy skips

PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def log(_msg):
    pass


def stock(blob, civ='GoodGuy'):
    """{scan type: count} in a civ's OWPR."""
    ow = fog._sections(fog._owners(blob, sp.parse_blob(blob))[civ][1])[b'OWPR']
    pay = blob[ow.payload:ow.end]
    n = struct.unpack_from('<I', pay, 8)[0]
    return dict(struct.unpack_from('<II', pay, 12 + 8 * i) for i in range(n))


def with_stock(blob, civ, counts):
    tree = sp.parse_blob(blob)
    ow = fog._sections(fog._owners(blob, tree)[civ][1])[b'OWPR']
    pay = bytes(blob[ow.payload:ow.end])
    n = struct.unpack_from('<I', pay, 8)[0]
    held = dict(struct.unpack_from('<II', pay, 12 + 8 * i) for i in range(n))
    held.update(counts)
    pairs = b''.join(struct.pack('<II', k, held[k]) for k in sorted(held))
    new = pay[:8] + struct.pack('<I', len(held)) + pairs + pay[12 + 8 * n:]
    return fog._apply(blob, tree, [(ow.payload, ow.end, new)])


def galaxy():
    d4 = sp.load_any(os.path.join(FIX, 'd4.b64'))
    blob = with_stock(d4, 'GoodGuy', {t: 2 for t in TARGETS})
    return mo.set_civ_identity(blob, BAD, b'Badlands', 0, -1)


def masked(report):
    """A planetary report with its leftover PLPR bytes zeroed."""
    at = report.find(b'PLPR')
    if report[:4] != b'SCPL' or at < 0:
        return report
    lo, hi = at + 8 + PLPR_LEFTOVER[0], at + 8 + PLPR_LEFTOVER[1]
    return report[:lo] + b'\0' * (hi - lo) + report[hi:]


def by_button(blob):
    """The capture after GoodGuy presses every scan, offline."""
    os.makedirs(WORK, exist_ok=True)
    dat = os.path.join(WORK, 'scan_types_button.dat')
    with open(dat, 'wb') as f:
        f.write(blob)
    snap = gc.restart(dat, purpose='scan types: the scan button',
                      wait_for_lock=60)
    try:
        alloc = {o['id']: o['addr'] - 8 for o in snap.state.objects
                 if not o['static']}
        rem = remote.Remote(snap.state.pid, log=log)
        try:
            for t, target in TARGETS.items():
                rem.do_scan(t, [alloc[target]])
        finally:
            rem.close()
        return sp.load_any(player_turn.collect('scan types', log=log))
    finally:
        gc.close_client()
        os.remove(dat)


def port_free(port):
    with socket.socket() as s:
        try:
            s.bind(('127.0.0.1', port))
            return True
        except OSError:
            return False


def main():
    if not lease.take('client', HOLDER, 'every scan type, button and referee',
                      15):
        return 2
    server = None
    try:
        if port_free(PORT):
            server = subprocess.Popen(
                [sys.executable, os.path.join(SERVER, 'cs_server.py')],
                cwd=SERVER, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
            end = time.time() + 10
            while port_free(PORT) and time.time() < end:
                time.sleep(0.2)
        run(galaxy())
    finally:
        if server is not None:
            server.terminate()
            server.wait(10)
        lease.release('client', HOLDER)
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


def run(blob):
    print('the galaxy')
    check('GoodGuy holds two of every scan', stock(blob),
          {t: 2 for t in TARGETS})
    check('BadGuy has a civ name, for the leader name scan',
          mo.civ_identity(blob, BAD)[0], b'Badlands')

    button = by_button(blob)
    referee, made = scan.run_scans(blob, 'GoodGuy',
                                   [(t, [x]) for t, x in TARGETS.items()],
                                   log=log)
    pressed = mo.reports_of(button, GOOD)
    answered = mo.reports_of(referee, GOOD)
    want = [tag for tag in REPORT_TAGS.values() if tag]

    print('each scan makes its report, the same both ways')
    check('the scan button made one report per scan but fleet',
          [r[:4] for r in pressed], want)
    check('the referee made the same, scan by scan',
          made, [1 if REPORT_TAGS[t] else 0 for t in TARGETS])
    for t, name in scan.SCAN_TYPES.items():
        tag = REPORT_TAGS[t]
        if tag is None:
            continue
        a = next(r for r in pressed if r[:4] == tag)
        b = next(r for r in answered if r[:4] == tag)
        check(f'{name} scan of {TARGETS[t]}: the referee\'s report is the '
              f'button\'s', masked(b), masked(a))
    check('    the planetary report differs at most in the leftover bytes',
          masked(pressed[0]) == masked(answered[0]), True)

    print('the stock')
    spent = {t: (1 if REPORT_TAGS[t] else 2) for t in TARGETS}
    check('the button took one of each scan that made a report',
          stock(button), spent)
    check('    and the referee the same', stock(referee), spent)

    print('the answer the launcher hands the client')
    body = scan.answer_body(referee, 'GoodGuy', sum(made))
    n = struct.unpack_from('<I', body)[0]
    check('it counts the reports', n, len(want))
    check('    and holds them in order', body[4:4 + sum(map(len, answered))],
          b''.join(answered))


if __name__ == '__main__':
    sys.exit(main())
