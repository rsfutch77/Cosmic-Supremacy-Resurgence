"""
test_scan.py: a scan request read, and an answer built from a capture
======================================================================
    python server\\tests\\test_scan.py

Uses `fixtures/unruled/sc1`, `d4` after GoodGuy's planetary scan of BadGuy's
HQ (384) and route scan of ship 677, made by the client offline. No client
is started.
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools')):
    if d not in sys.path:
        sys.path.insert(0, d)

import save_parser as sp                                        # noqa: E402
import merge_orders as mo                                       # noqa: E402
import scan                                                     # noqa: E402

FIX = os.path.join(HERE, 'fixtures', 'unruled')
GOOD = 656
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def refused(payload):
    try:
        scan.parse_request(payload)
    except scan.ScanError:
        return True
    return False


def sections(body, at):
    """[(tag, whole section)] from `at` to the end of `body`."""
    out = []
    while at < len(body):
        n = struct.unpack_from('<I', body, at + 4)[0] & sp.SIZE_MASK
        out.append((body[at:at + 4], body[at:at + 8 + n]))
        at += 8 + n
    return out


def main():
    print('the request')
    seen = bytes.fromhex('01000000' '80010000' '00000000')
    check('the 12 bytes the client sent read as a planetary scan of 384',
          scan.parse_request(seen), (0, [384]))
    check('    and build back the same',
          scan.build_request(0, [384]), seen)
    check('two targets of a route scan round-trip',
          scan.parse_request(scan.build_request(2, [677, 678])), (2, [677, 678]))
    check('a short request is refused', refused(b'\x01\x00\x00\x00'))
    check('a count past the bytes is refused',
          refused(struct.pack('<III', 2, 384, 0)))
    check('trailing bytes are refused', refused(seen + b'\x00'))
    check('an unknown scan type is refused',
          refused(struct.pack('<III', 1, 384, 8)))
    check('an absurd target count is refused',
          refused(struct.pack('<I', 100000) + b'\x00' * 8))

    print('the answer')
    sc1 = sp.load_any(os.path.join(FIX, 'sc1.b64'))
    reports = mo.reports_of(sc1, GOOD)
    body = scan.answer_body(sc1, 'GoodGuy', 1)
    check('the count is the reports made', struct.unpack_from('<I', body)[0], 1)
    parts = sections(body, 4)
    check('then the last report, the scanner\'s OWPR and its KNPL',
          [t for t, _ in parts], [b'SCRO', b'OWPR', b'KNPL'])
    check('    and the report is the capture\'s own bytes',
          parts[0][1], reports[-1])
    both = sections(scan.answer_body(sc1, 'GoodGuy', 2), 4)
    check('two reports come in the order made',
          [t for t, _ in both][:2], [b'SCPL', b'SCRO'])
    none = scan.answer_body(sc1, 'GoodGuy', 0)
    check('a scan that made nothing answers a count of 0',
          (struct.unpack_from('<I', none)[0], [t for t, _ in sections(none, 4)]),
          (0, [b'OWPR', b'KNPL']))
    try:
        scan.answer_body(sc1, 'GoodGuy', 3)
        check('more reports than the civ holds is refused', False)
    except scan.ScanError:
        check('more reports than the civ holds is refused', True)

    store_and_worker()
    turn_close()

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


def set_turn(blob, turn):
    """The same blob carrying a different turn number."""
    tree = sp.parse_blob(blob)
    glob = next(tree[0].find('GLOB'))
    out = bytearray(blob)
    struct.pack_into('<I', out, glob.payload, turn)
    return bytes(out)


def turn_close():
    """`resolve_turn` with the engine stubbed: the delivered scans' reports go
    into the blob it ticks, and the scans go to the tick to replay."""
    import tempfile
    import turn_store
    import referee
    import player_turn

    d4 = sp.load_any(os.path.join(FIX, 'd4.b64'))
    pl, ro = mo.reports_of(sp.load_any(os.path.join(FIX, 'sc1.b64')), GOOD)
    print('the turn close')
    with tempfile.TemporaryDirectory() as root:
        store = turn_store.TurnStore(root)
        store.start(d4, ['GoodGuy', 'BadGuy'], turn_seconds=3600)
        turn = store.current()[0]
        ids = []
        for kind, targets, rep in ((0, [384], pl), (2, [677], ro)):
            sid = store.ask_scan('GoodGuy', turn, kind, targets)
            store.move_scan(sid, 'answered', answer=struct.pack('<I', 1) + rep)
            store.move_scan(sid, 'delivered')
            ids.append(sid)
        gone = store.ask_scan('GoodGuy', turn, 0, [384])
        store.move_scan(gone, 'answered', answer=struct.pack('<I', 1) + pl)
        store.move_scan(gone, 'cancelled')
        view = store.served_turn('GoodGuy', turn)
        # The client holds every answer it was handed, and one more report
        # nobody answered.
        sub = mo.set_reports(view, GOOD, mo.reports_of(view, GOOD)
                             + [pl, ro, ro])
        store.submit('GoodGuy', turn, sub)

        seen = {}

        def fake_tick(blob, turns=1, secs=10, save_dir=None, log=print, **kw):
            seen['blob'], seen['scans'] = blob, kw.get('scans')
            return set_turn(blob, turn + 1)

        real_tick, real_ready = referee.tick, player_turn.save_path_ready
        referee.tick = fake_tick
        player_turn.save_path_ready = lambda: (True, '')
        try:
            referee.resolve_turn(store, log=lambda *_: None)
            check('the tick gets the delivered reports, in order',
                  mo.reports_of(seen['blob'], GOOD), [pl, ro])
            check('    and the delivered scans to replay, not the cancelled',
                  seen['scans'], [('GoodGuy', 0, [384]), ('GoodGuy', 2, [677])])
            notes = store.note('GoodGuy', turn)
            check('the report nobody answered is refused by name',
                  [n for n in notes if 'answered it online' in n],
                  ['1 scan report(s) DROPPED: a scan is carried only when the '
                   'referee answered it online; the scans stay in stock'])
            check('the archive names the scans replayed',
                  store.archive_record(turn)['scans'], ids)
            check('a rerun replays the same scans and agrees',
                  referee.verify_turn(store, turn, log=lambda *_: None), True)
            check('    and gave the tick the same scans', seen['scans'],
                  [('GoodGuy', 0, [384]), ('GoodGuy', 2, [677])])
        finally:
            referee.tick, player_turn.save_path_ready = real_tick, real_ready


def store_and_worker():
    """A folder store's scans, and the worker answering them with the client
    run replaced, so no client starts."""
    import tempfile
    import turn_store
    import referee_worker as rw

    d4 = sp.load_any(os.path.join(FIX, 'd4.b64'))
    with tempfile.TemporaryDirectory() as root:
        store = turn_store.TurnStore(root)
        store.start(d4, ['GoodGuy', 'BadGuy'], turn_seconds=3600)
        turn = store.current()[0]

        print('a folder store')
        sid = store.ask_scan('GoodGuy', turn, 0, [384])
        check('an asked scan is waiting',
              [r['id'] for r in store.scans_waiting()], [sid])
        check('a scan of an unknown type is refused before it is filed',
              refused_by(store.ask_scan, 'GoodGuy', turn, 8, [384]), True)
        check('a name that cannot be a civ is refused',
              refused_by(store.ask_scan, 'a/b', turn, 0, [384]), True)
        check('a player cannot deliver a scan that has no answer',
              store.move_scan(sid, 'delivered')['state'], 'asked')

        print('the worker')
        log = []
        made = []

        def fake_answer(blob, civ, kind, targets, earlier=(), save_dir=None,
                        log=print):
            made.append((civ, kind, list(targets), list(earlier)))
            return b'answer-%d' % len(made)

        real = scan.answer
        scan.answer = fake_answer
        try:
            rw.answer_scans([rw.Watched(root, store)], log=log.append)
            check('answers a waiting scan with what the scan run gave',
                  (store.scan(sid)['state'], store.scan(sid)['answer']),
                  ('answered', b'answer-1'))
            check('    run on the civ, type and targets asked',
                  made[0], ('GoodGuy', 0, [384], []))
            store.move_scan(sid, 'delivered')
            two = store.ask_scan('GoodGuy', turn, 2, [677])
            rw.answer_scans([rw.Watched(root, store)], log=log.append)
            check('replays the civ\'s delivered scans first',
                  made[1][3], [(0, [384])])
            check('    and answers the second', store.scan(two)['state'],
                  'answered')
            gone = store.ask_scan('GoodGuy', turn, 0, [384])
            store.move_scan(gone, 'cancelled')
            rw.answer_scans([rw.Watched(root, store)], log=log.append)
            check('a cancelled scan is not run', len(made), 2)
            old = store.ask_scan('GoodGuy', turn - 1, 0, [384])
            rw.answer_scans([rw.Watched(root, store)], log=log.append)
            check('a scan of a closed turn is refused with the reason',
                  (store.scan(old)['state'], store.scan(old)['reason']),
                  ('refused', f'turn {turn - 1} has closed'))

            def cancelled_meanwhile(blob, civ, kind, targets, earlier=(),
                                    save_dir=None, log=print):
                store.move_scan(late, 'cancelled')
                return b'late'

            scan.answer = cancelled_meanwhile
            late = store.ask_scan('GoodGuy', turn, 0, [384])
            rw.answer_scans([rw.Watched(root, store)], log=log.append)
            check('a scan cancelled while it ran stays cancelled',
                  store.scan(late)['state'], 'cancelled')

            def broken(*a, **kw):
                raise RuntimeError('the client did not start')

            scan.answer = broken
            bad = store.ask_scan('GoodGuy', turn, 0, [384])
            rw.answer_scans([rw.Watched(root, store)], log=log.append)
            check('a scan run that fails is refused, not left waiting',
                  store.scan(bad)['state'], 'refused')
        finally:
            scan.answer = real
        check('only the delivered scan counts',
              [r['id'] for r in store.delivered_scans(turn, 'GoodGuy')], [sid])
        check('pruning drops earlier turns only',
              (store.prune_scans(turn), store.scan(old), store.scan(sid)['id']),
              (1, None, sid))
        over_lan(root, store, turn)


def over_lan(root, store, turn):
    """A player's scans through `turn_server.py`."""
    import threading
    import turn_server
    from turn_store import HttpTurnStore

    print('over a LAN')
    kept_store, kept_verbose = turn_server.STORE, turn_server.VERBOSE
    turn_server.VERBOSE = False
    httpd = turn_server.serve(root, '127.0.0.1', 0)
    turn_server.STORE = store
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        player = HttpTurnStore(f'http://127.0.0.1:{httpd.server_address[1]}')
        sid = player.ask_scan('BadGuy', turn, 5, [656])
        check('a player files a scan', store.scan(sid)['targets'], [656])
        check('    and reads it back', player.scan(sid)['state'], 'asked')
        check('a scan for a turn not being played is refused',
              refused_http(player.ask_scan, 'BadGuy', turn + 1, 5, [656]), 409)
        store.move_scan(sid, 'answered', answer=b'\x00\xff')
        check('the answer arrives as bytes', player.scan(sid)['answer'],
              b'\x00\xff')
        check('a player cannot answer one',
              refused_http(player.move_scan, sid, 'answered'), 'ValueError')
        check('the player marks it delivered',
              player.move_scan(sid, 'delivered')['state'], 'delivered')
        check('a scan that is not there reads as none',
              player.scan('0' * 32), None)
    finally:
        httpd.shutdown()
        httpd.server_close()
        turn_server.STORE, turn_server.VERBOSE = kept_store, kept_verbose


def refused_http(fn, *a):
    """The HTTP status a call was refused with, or the exception's name."""
    import urllib.error
    try:
        fn(*a)
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception as exc:                                # noqa: BLE001
        return type(exc).__name__
    return None


def refused_by(fn, *a):
    try:
        fn(*a)
    except ValueError:
        return True
    return False


if __name__ == '__main__':
    sys.exit(main())
