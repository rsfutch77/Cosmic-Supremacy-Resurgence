"""
scan_endpoint.py: the launcher answers a client's scan through its galaxy

A client carrying `patch_scan_online.py` connects to `127.0.0.1:8890` when the
player scans, sends `DOSC` wrapped in `CMND` (a `u32` length, then the section
deflated), and blocks until one answer comes back. The endpoint files the scan
with the galaxy's store, waits for the referee, and answers:

  * `SUCC` holding the referee's answer, then marks the scan delivered, which
    is what charges it;
  * `FAIL` holding a reason, which the client shows in its Warn box
    (`0x0056E930`), when the referee refused the scan or none can be asked;
  * nothing, by closing the socket, when the player or the time limit gives
    up. The scan is cancelled first, so it is never charged, and the client
    warns of a lost connection.

The client's wait matches no request id, so a connection carries one scan.
"""
from __future__ import annotations

import contextlib
import socket
import struct
import threading
import time
import zlib

PORT = 8890                 # patch_scan_online.SCAN_PORT
LIMIT = 90.0                # seconds the launcher waits on the referee
POLL = 1.0                  # seconds between reads of the scan's record
DELIVER_TRIES = 3           # a delivered mark lost to an outage is retried
MAX_FRAME = 64 * 1024       # far more than a DOSC of 256 targets
SIZE_MASK = 0x3FFFFFF       # a section header: version << 26 | length

NOT_PLAYING = ('Scans are answered by the referee only while a multiplayer '
               'galaxy is played from the launcher.')

SCAN_NAMES = {0: 'planetary', 1: 'system', 2: 'route', 3: 'fleet',
              4: 'military', 5: 'technology', 6: 'energy grid',
              7: 'leader name'}


def section(tag: bytes, version: int, payload: bytes) -> bytes:
    return tag + struct.pack('<I', (version << 26) | len(payload)) + payload


def succ(body: bytes) -> bytes:
    """The answer the client applies: `SUCC` v0, not wrapped in `CMND`."""
    return section(b'SUCC', 0, body)


def fail(text: str) -> bytes:
    """A refusal the client shows: `FAIL` v0 holding a `u32`-length string."""
    raw = text.encode('latin-1', 'replace')
    return section(b'FAIL', 0, struct.pack('<I', len(raw)) + raw)


def recv_exact(conn, n: int) -> bytes:
    out = b''
    while len(out) < n:
        got = conn.recv(n - len(out))
        if not got:
            raise EOFError('the client closed the connection')
        out += got
    return out


def read_section(conn):
    """(tag, version, payload) of the next section on the socket."""
    head = recv_exact(conn, 8)
    word = struct.unpack_from('<I', head, 4)[0]
    size = word & SIZE_MASK
    if size > MAX_FRAME:
        raise ValueError(f'a {size}-byte section is past the cap')
    return head[:4], word >> 26, recv_exact(conn, size)


def dosc_of(tag: bytes, payload: bytes):
    """The `DOSC` payload inside a `CMND`, or None for any other command."""
    if tag != b'CMND' or len(payload) < 4:
        return None
    raw = struct.unpack_from('<I', payload)[0]
    if raw > MAX_FRAME:
        return None
    inner = zlib.decompressobj().decompress(payload[4:], MAX_FRAME)
    if inner[:4] != b'DOSC' or len(inner) < 8:
        return None
    size = struct.unpack_from('<I', inner, 4)[0] & SIZE_MASK
    return inner[8:8 + size]


class Outcome:
    """How a scan ended: `answered` (with `body`), `refused` (with `reason`),
    `gave_up` (with `reason`) or `failed` (with `reason`)."""

    def __init__(self, kind, body=None, reason='', scan_id=None):
        self.kind, self.body, self.reason, self.scan_id = (kind, body, reason,
                                                           scan_id)

    def __repr__(self):
        return f'Outcome({self.kind!r}, {self.reason!r})'


def run_scan(store, civ, turn, payload, give_up, on_event=None, limit=LIMIT,
             poll=POLL, clock=time.monotonic):
    """File one scan and wait for the referee. Returns an `Outcome`.

    `give_up` is a `threading.Event`; set, the scan is cancelled. A record
    that cannot be read is read again until the limit, since an outage
    should not cost the scan.
    """
    def say(kind, **facts):
        if on_event:
            on_event(kind, **facts)

    import turn_store
    try:
        kind, targets = turn_store.parse_scan_request(payload)
    except ValueError as exc:
        return Outcome('refused', reason=f'The scan request did not read: {exc}')
    try:
        sid = store.ask_scan(civ, turn, kind, targets)
    except Exception as exc:                                # noqa: BLE001
        return Outcome('failed', reason=f'The scan could not be sent to the '
                                        f'galaxy: {exc}')
    started = clock()
    say('asked', scan_id=sid, type=kind, targets=targets)

    def cancel(why):
        try:
            rec = store.move_scan(sid, turn_store.SCAN_CANCELLED)
        except Exception:                                   # noqa: BLE001
            # The answer is never handed over, so it is never charged.
            rec = None
        if rec and rec.get('state') == turn_store.SCAN_ANSWERED:
            return None
        return Outcome('gave_up', reason=why, scan_id=sid)

    while True:
        if give_up.is_set():
            out = cancel('The scan was cancelled; it stays in stock, so you '
                         'can scan again.')
            if out:
                return out
        waited = clock() - started
        if waited >= limit:
            out = cancel(f'The referee did not answer within {limit:.0f} '
                         f'seconds; the scan stays in stock.')
            if out:
                return out
        try:
            rec = store.scan(sid)
        except Exception:                                   # noqa: BLE001
            rec = {'state': turn_store.SCAN_ASKED}
        state = (rec or {}).get('state')
        if state == turn_store.SCAN_ANSWERED:
            return Outcome('answered', body=rec['answer'], scan_id=sid)
        if state == turn_store.SCAN_REFUSED:
            return Outcome('refused', reason=f'The referee refused the scan: '
                                             f'{rec.get("reason")}.',
                           scan_id=sid)
        if state == turn_store.SCAN_CANCELLED:
            return Outcome('gave_up', reason='The scan was cancelled; it '
                                             'stays in stock.', scan_id=sid)
        say('waiting', scan_id=sid, seconds=waited)
        give_up.wait(poll)


def deliver(store, scan_id, tries=DELIVER_TRIES, wait=1.0) -> bool:
    """Mark a scan delivered. A mark that never lands leaves the scan
    uncharged, which costs the referee a scan and the player nothing."""
    import turn_store
    for i in range(tries):
        try:
            rec = store.move_scan(scan_id, turn_store.SCAN_DELIVERED)
            return (rec or {}).get('state') == turn_store.SCAN_DELIVERED
        except Exception:                                   # noqa: BLE001
            if i + 1 < tries:
                time.sleep(wait)
    return False


class ScanEndpoint:
    """The socket the patched client connects to, served on a thread.

    `session()` returns `(store, civ, turn)` for the galaxy being played, or
    None. `on_event(kind, **facts)` hears `asked`, `waiting`, `answered`,
    `refused`, `gave_up` and `failed`, from the endpoint's thread.
    """

    def __init__(self, session, on_event=None, port=PORT, host='127.0.0.1',
                 limit=LIMIT, log=print):
        self.session, self.on_event = session, on_event
        self.port, self.host, self.limit, self.log = port, host, limit, log
        self.sock = None
        self.thread = None
        self.give_up = threading.Event()
        self.busy = False
        self.lock = threading.Lock()

    def start(self) -> bool:
        """Listen. False when the port is held, so scans stay offline."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        excl = getattr(socket, 'SO_EXCLUSIVEADDRUSE', None)
        if excl is not None:
            with contextlib.suppress(OSError):
                s.setsockopt(socket.SOL_SOCKET, excl, 1)
        try:
            s.bind((self.host, self.port))
            s.listen(2)
        except OSError as exc:
            s.close()
            self.log(f'scans: port {self.port} is held ({exc}); a scan runs '
                     f'on the client\'s own copy')
            return False
        self.sock = s
        self.port = s.getsockname()[1]
        self.thread = threading.Thread(target=self._accept, daemon=True,
                                       name='scan-endpoint')
        self.thread.start()
        return True

    def stop(self) -> None:
        sock, self.sock = self.sock, None
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.close()
        self.abandon()

    def abandon(self) -> None:
        """Give up on the scan being waited on: the player's button."""
        self.give_up.set()

    def _event(self, kind, **facts):
        if self.on_event:
            try:
                self.on_event(kind, **facts)
            except Exception as exc:                        # noqa: BLE001
                self.log(f'scans: the window did not take {kind} ({exc})')

    def _accept(self):
        while self.sock is not None:
            try:
                conn, _addr = self.sock.accept()
            except OSError:
                return
            # One scan at a time: the client blocks on its answer, so a
            # second connection is a second client, refused.
            with self.lock:
                taken = self.busy
                self.busy = True
            if taken:
                threading.Thread(target=refuse, daemon=True, args=(
                    conn, 'Another scan is still waiting on the referee.'),
                    name='scan-refused').start()
                continue
            threading.Thread(target=self._serve_one, args=(conn,),
                             daemon=True, name='scan').start()

    def _serve_one(self, conn):
        try:
            self._serve(conn)
        finally:
            with self.lock:
                self.busy = False

    def _serve(self, conn):
        self.give_up.clear()
        conn.settimeout(30)
        with contextlib.closing(conn):
            try:
                tag, _v, payload = read_section(conn)
            except (OSError, EOFError, ValueError) as exc:
                self.log(f'scans: no request read ({exc})')
                return
            dosc = dosc_of(tag, payload)
            if dosc is None:
                self.log(f'scans: {tag!r} is not a scan; answered FAIL')
                with contextlib.suppress(OSError):
                    answer(conn, fail('Only a scan is sent to the launcher.'))
                return
            where = self.session()
            if where is None:
                self.log('scans: a scan came with no galaxy being played')
                with contextlib.suppress(OSError):
                    answer(conn, fail(NOT_PLAYING))
                self._event('refused', reason=NOT_PLAYING)
                return
            store, civ, turn = where
            out = run_scan(store, civ, turn, dosc, self.give_up,
                           on_event=self._event, limit=self.limit)
            self.log(f'scans: {civ} turn {turn}: {out.kind}'
                     + (f', {out.reason}' if out.reason else ''))
            if out.kind == 'answered':
                try:
                    answer(conn, succ(out.body))
                except OSError as exc:
                    # The client is gone, so the answer was never shown.
                    self.log(f'scans: the answer did not reach the client '
                             f'({exc}); cancelled')
                    run_cancel(store, out.scan_id)
                    self._event('gave_up', reason='The game closed before '
                                                  'the answer arrived.')
                    return
                if not deliver(store, out.scan_id):
                    self.log(f'scans: scan {out.scan_id} was shown but not '
                             f'marked delivered; it will not be charged')
                self._event('answered', scan_id=out.scan_id)
            elif out.kind in ('refused', 'failed'):
                with contextlib.suppress(OSError):
                    answer(conn, fail(out.reason))
                self._event(out.kind, reason=out.reason)
            else:
                # Closing is the give-up: the client warns of a lost
                # connection and carries on.
                self._event('gave_up', reason=out.reason)


def answer(conn, data: bytes) -> None:
    """Send the one answer and end the send side, so it is read before the
    connection closes."""
    conn.sendall(data)
    with contextlib.suppress(OSError):
        conn.shutdown(socket.SHUT_WR)


def refuse(conn, text) -> None:
    """Read the request, answer FAIL and end the send side before closing.
    Closed with the request unread, Windows resets the connection and the
    client never reads the reason."""
    with contextlib.closing(conn), contextlib.suppress(OSError, EOFError,
                                                       ValueError):
        conn.settimeout(5)
        read_section(conn)
        answer(conn, fail(text))


def run_cancel(store, scan_id) -> None:
    import turn_store
    with contextlib.suppress(Exception):
        store.move_scan(scan_id, turn_store.SCAN_CANCELLED)
