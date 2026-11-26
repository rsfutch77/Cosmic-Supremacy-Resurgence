"""
replay_endpoint.py: the launcher serves the client's own galaxy replay

A pass naming port 4710 starts the client in replay mode (`[0x0086F1A2]`). It
connects to the pass's host and logs in, then steps through turns with the
replay screen's previous, next and slider:

  * `RQLG` then `LGIN`, each answered `SUCC` holding a `u32`-length string;
    after `LGIN` the newest turn goes out as `SAVE`;
  * `RQTN` with a `u32` turn, answered with that turn as `SAVE`, or `FAIL`,
    which the client titles `Turn Data Not Available`. Turn 0 is the newest;
  * `RQSV`, a resync, answered with the newest turn;
  * `ALIV`, which needs no answer.

Each turn is the player's own civ's view, read through the store
(`turn_store.replay_view`), so the replay shows what that civ saw. The client
offers next while the turn is below the second `u32` of `GLOB`, which every
save carries as -1, so each replay save is stamped with the newest turn.

The login reads the pass file's raw text repeated 16 times and goes on only at
512 characters, so the pass file is exactly 32 characters (`pass_text`).
"""
from __future__ import annotations

import base64
import contextlib
import socket
import struct
import threading
import zlib

from scan_endpoint import fail, read_section, succ

PORT = 4710                 # the client's replay port (0x00577EE4)
HOST = '127.0.0.1'
PASS_LENGTH = 32            # 512 / 16 copies (0x0057A8F4)
SIZE_MASK = 0x3FFFFFF
MAX_FRAME = 64 * 1024       # a command, never a save, comes from the client
EMPTY = succ(struct.pack('<I', 0))


def pass_text(host: str = HOST, port: int = PORT) -> bytes:
    """A pass file the replay login accepts: 32 characters of base64."""
    head = f'{host} {port} x '.encode('ascii')
    room = PASS_LENGTH // 4 * 3 - len(head)
    if room < 1:
        raise ValueError(f'{host}:{port} leaves no room for a player in a '
                         f'{PASS_LENGTH}-character pass')
    text = base64.b64encode(head + b'Replay'.ljust(room, b'.')[:room])
    assert len(text) == PASS_LENGTH
    return text


def stamp_last_turn(blob: bytes, last: int) -> bytes:
    """`blob` with `GLOB`'s last turn set to `last`. The top-level sections
    are walked, so a tag inside another section's bytes is never touched."""
    out = bytearray(blob)
    at = 12                     # SAVE's header, then a u32 ahead of GSET
    while at + 8 <= len(out):
        tag = bytes(out[at:at + 4])
        if not tag.isalpha():
            break
        size = struct.unpack_from('<I', out, at + 4)[0] & SIZE_MASK
        if tag == b'GLOB':
            if size < 8:
                break
            struct.pack_into('<i', out, at + 12, int(last))
            return bytes(out)
        at += 8 + size
    raise ValueError('the save has no GLOB section')


def command(conn):
    """(tag, payload) of the client's next command, unwrapped from `CMND`."""
    tag, _v, payload = read_section(conn)
    if tag == b'CMND' and len(payload) >= 4:
        raw = zlib.decompressobj().decompress(payload[4:], MAX_FRAME + 8)
        if len(raw) < 8:
            raise ValueError('a CMND too short for a section')
        size = struct.unpack_from('<I', raw, 4)[0] & SIZE_MASK
        return raw[:4], raw[8:8 + size]
    return tag, payload


class ReplayEndpoint:
    """The socket a replay client connects to, served on a thread.

    `session()` returns `(store, civ, newest turn)` for the galaxy being
    replayed, or None. `log` hears each step.
    """

    def __init__(self, session, port=PORT, host=HOST, log=print):
        self.session, self.port, self.host, self.log = session, port, host, log
        self.sock = None
        self.conns = set()
        self.lock = threading.Lock()

    def start(self) -> bool:
        """Listen. False when the port is held."""
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
            self.log(f'replay: port {self.port} is held ({exc})')
            return False
        self.sock = s
        self.port = s.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True,
                         name='replay-endpoint').start()
        return True

    def stop(self) -> None:
        sock, self.sock = self.sock, None
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.close()
        with self.lock:
            conns, self.conns = list(self.conns), set()
        for c in conns:
            with contextlib.suppress(OSError):
                c.close()

    def _accept(self):
        while self.sock is not None:
            try:
                conn, _addr = self.sock.accept()
            except OSError:
                return
            with self.lock:
                self.conns.add(conn)
            threading.Thread(target=self._serve_one, args=(conn,),
                             daemon=True, name='replay').start()

    def _serve_one(self, conn):
        try:
            with contextlib.closing(conn):
                self._serve(conn)
        except (OSError, EOFError, ValueError, zlib.error) as exc:
            self.log(f'replay: the connection ended ({exc})')
        finally:
            with self.lock:
                self.conns.discard(conn)

    def _serve(self, conn):
        where = self.session()
        newest = where[2] if where else None
        while True:
            tag, payload = command(conn)
            if tag == b'RQLG':
                conn.sendall(EMPTY)
            elif tag == b'LGIN':
                conn.sendall(EMPTY)
                if where is None:
                    self.log('replay: a client logged in with no galaxy to '
                             'replay')
                    return
                conn.sendall(self._turn(where, newest))
            elif tag == b'RQTN' and len(payload) >= 4:
                turn = struct.unpack_from('<i', payload)[0] or newest
                conn.sendall(self._turn(where, turn))
            elif tag == b'RQSV':
                conn.sendall(self._turn(where, newest))
            elif tag != b'ALIV':
                self.log(f'replay: {tag!r} needs no answer here')

    def _turn(self, where, turn) -> bytes:
        """`turn` as `SAVE`, or `FAIL` saying why not."""
        import turn_store
        store, civ, newest = where
        try:
            blob = store.replay_view(civ, int(turn))
            out = stamp_last_turn(blob, newest)
        except turn_store.NotServed as exc:
            self.log(f'replay: {civ} turn {turn}: {exc}')
            return fail(f'Turn {turn} is not available: {exc}.')
        except Exception as exc:                            # noqa: BLE001
            self.log(f'replay: {civ} turn {turn} could not be read ({exc})')
            return fail(f'Turn {turn} could not be read.')
        self.log(f'replay: {civ} turn {turn} sent ({len(out):,} bytes)')
        return out
