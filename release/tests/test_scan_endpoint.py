"""
test_scan_endpoint.py: the launcher's scan endpoint, against a stand-in client

    python release\\tests\\test_scan_endpoint.py

A socket plays the patched client: it sends `DOSC` wrapped in `CMND` and reads
the one answer. A folder galaxy is the store, and a thread plays the referee
by moving the scan's record. No game client and no window.
"""
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import zlib

import pathlib
REPO = str(pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, os.path.join(REPO, "release"))
sys.path.insert(0, os.path.join(REPO, "server"))
sys.path.insert(0, os.path.join(REPO, "server", "dev_tools"))

import save_parser as sp                                        # noqa: E402
import scan_endpoint as se                                      # noqa: E402
import turn_store                                               # noqa: E402

FIX = os.path.join(REPO, "server", "tests", "fixtures", "unruled")
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def command(payload, tag=b'DOSC'):
    """What the client sends: the section deflated inside `CMND`."""
    inner = se.section(tag, 0, payload)
    return se.section(b'CMND', 0, struct.pack('<I', len(inner))
                      + zlib.compress(inner))


def client(port, sent, timeout=10):
    """Send one command and return (tag, payload) of the answer, or None when
    the endpoint closed without one."""
    with socket.create_connection(('127.0.0.1', port), timeout=timeout) as c:
        c.sendall(sent)
        try:
            tag, _v, payload = se.read_section(c)
        except (EOFError, ConnectionResetError):
            # Windows may end a closed connection with a reset.
            return None
        return tag, payload


def fail_text(answer):
    tag, payload = answer
    n = struct.unpack_from('<I', payload)[0]
    return tag, payload[4:4 + n].decode('latin-1')


def referee(store, do, delay=0.3):
    """A thread that moves the first asked scan once it appears."""
    def run():
        end = time.time() + 10
        while time.time() < end:
            waiting = store.scans_waiting()
            if waiting:
                time.sleep(delay)
                do(waiting[0]['id'])
                return
            time.sleep(0.05)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def main():
    d4 = sp.load_any(os.path.join(FIX, 'd4.b64'))
    request = turn_store.scan_request(0, [384])
    with tempfile.TemporaryDirectory() as root:
        store = turn_store.TurnStore(root)
        store.start(d4, ['GoodGuy', 'BadGuy'], turn_seconds=3600)
        turn = store.current()[0]
        where = [(store, 'GoodGuy', turn)]
        events = []
        ep = se.ScanEndpoint(lambda: where[0], port=0, limit=5.0,
                             log=lambda *_: None,
                             on_event=lambda k, **f: events.append(k))
        ep.start()
        try:
            run(ep, store, turn, request, where, events)
        finally:
            ep.stop()
            # The last scan's cancel may still be writing into the folder.
            end = time.time() + 10
            while ep.busy and time.time() < end:
                time.sleep(0.1)

    window()

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


def window():
    """The launcher's half: what the scan window shows for each event, and
    what its buttons do. A stub carries only what the methods read."""
    import queue
    import launcher as L
    from fake_view import FakeView

    print('the launcher\'s scan window')
    app = L.Launcher.__new__(L.Launcher)
    app.view = FakeView()
    app.msgs = queue.Queue()
    app.said = []
    app.say = app.said.append
    app.mp_thread = threading.Thread(target=lambda: time.sleep(5),
                                     daemon=True)
    app.mp_thread.start()
    app.mp_store, app.mp_civ, app.mp_turn = object(), 'GoodGuy', 180
    app.mp_reopen = threading.Event()
    abandoned = []
    app.scan_endpoint = type('E', (), {'abandon': lambda s: abandoned.append(1)})()

    app._on_scan('asked', {'type': 0})
    check('a scan asked opens the window with Give up',
          (app.view.scan['title'], app.view.scan['buttons']),
          ('Scanning: planetary scan', ['give_up']))
    app._on_scan('waiting', {'seconds': 7.4})
    check('    and counts the seconds', app.view.scan['seconds'], 7.4)
    app.on_scan_press('give_up')
    check('Give up asks the endpoint to give up', abandoned, [1])
    check('    and takes the button away while it does',
          app.view.scan['buttons'], [])
    app._on_scan('gave_up', {'reason': 'You gave up on the scan; it stays '
                                       'in stock.'})
    check('a scan given up offers a restart while a galaxy is played',
          app.view.scan['buttons'], ['restart', 'close'])
    check('    and says the scan stays in stock',
          'stays in stock' in app.view.scan['text'], True)
    app.on_scan_press('close')
    check('Close closes the window', app.view.scan, None)
    app._on_scan('asked', {'type': 5})
    app._on_scan('answered', {})
    check('an answer closes the window', app.view.scan, None)
    app._on_scan('asked', {'type': 6})
    app._on_scan('refused', {'reason': 'turn 179 has closed'})
    check('a refusal closes it too, the game showing the reason',
          app.view.scan, None)
    check('    and the log keeps the reason', app.said[-1],
          'scans: turn 179 has closed')
    check('the session is the galaxy being played',
          app._scan_session()[1:], ('GoodGuy', 180))
    app.mp_turn = None
    check('    and none before a turn is known', app._scan_session(), None)
    app._on_scan('gave_up', {'reason': 'x'})
    check('with no galaxy played, a give-up offers Close alone',
          app.view.scan['buttons'], ['close'])


def run(ep, store, turn, request, where, events):
    print('a scan answered')
    referee(store, lambda sid: store.move_scan(sid, 'answered',
                                               answer=b'\x01\0\0\0REPORT'))
    got = client(ep.port, command(request))
    check('the client gets SUCC holding the answer', got,
          (b'SUCC', b'\x01\0\0\0REPORT'))
    rec = store._scans()[-1]
    check('the scan was filed as the client asked',
          (rec['civ'], rec['turn'], rec['type'], rec['targets']),
          ('GoodGuy', turn, 0, [384]))
    time.sleep(0.2)
    check('and is marked delivered once handed over', rec['id'] in
          [r['id'] for r in store.delivered_scans(turn)])
    check('the window heard it asked, waited and answered',
          [k for k in events if k != 'waiting'], ['asked', 'answered'])

    print('a scan refused')
    events.clear()
    referee(store, lambda sid: store.move_scan(
        sid, 'refused', reason=f'turn {turn - 1} has closed'))
    got = client(ep.port, command(request))
    check('the client gets FAIL with the referee\'s reason', fail_text(got),
          (b'FAIL', f'The referee refused the scan: turn {turn - 1} has '
                    f'closed.'))
    check('and nothing is charged',
          len(store.delivered_scans(turn)), 1)

    print('a scan given up on')
    events.clear()
    threading.Timer(0.5, ep.abandon).start()
    t = time.time()
    got = client(ep.port, command(request))
    check('the player\'s button closes the connection with no answer',
          got, None)
    check('    within a couple of seconds', time.time() - t < 3, True)
    gone = store._scans()[-1]
    check('    and the scan is cancelled', gone['state'], 'cancelled')
    late = store.move_scan(gone['id'], 'answered', answer=b'late')
    check('    so an answer that comes after is not taken', late['state'],
          'cancelled')
    check('    and the window heard it given up', events[-1], 'gave_up')

    print('a referee that never answers')
    ep.limit = 1.0
    got = client(ep.port, command(request))
    check('the launcher\'s limit closes the connection', got, None)
    check('    and cancels the scan', store._scans()[-1]['state'],
          'cancelled')
    ep.limit = 5.0

    print('what the endpoint will not take')
    where[0] = None
    check('a scan with no galaxy played is answered FAIL, saying so',
          fail_text(client(ep.port, command(request))),
          (b'FAIL', se.NOT_PLAYING))
    where[0] = (store, 'GoodGuy', turn)
    check('another command is answered FAIL',
          client(ep.port, command(b'\0' * 8, tag=b'SAVE'))[0], b'FAIL')
    check('a request that does not read is refused with the reason',
          fail_text(client(ep.port, command(b'\x01\0\0\0')))[1]
          .startswith('The scan request did not read'), True)
    n = len(store._scans())
    holder = socket.create_connection(('127.0.0.1', ep.port))
    holder.sendall(command(request))
    time.sleep(0.3)
    check('a second client while one waits is turned away',
          fail_text(client(ep.port, command(request))),
          (b'FAIL', 'Another scan is still waiting on the referee.'))
    ep.abandon()
    holder.close()
    time.sleep(1.5)
    check('    and files nothing of its own', len(store._scans()), n + 1)


if __name__ == '__main__':
    sys.exit(main())
