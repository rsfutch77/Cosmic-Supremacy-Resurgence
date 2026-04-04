"""
cs_server.py — Cosmic Supremacy dual-protocol local server
============================================================
Handles BOTH protocols the game uses on the same port:

1. HTTP POST to /clientinterface.php  — for actions like testconnection,
   savegame, listcoa, uploadcivname, etc. (15 known actions)

2. Binary TCP — for galaxy data transfer and periodic sync.
   Uses 4-byte big-endian magic headers:
     Client sends: RQLG (request galaxy), LGIN (login), STCO (state/sync)
     Server sends: SAVE (galaxy data), SUCC (success), FAIL (failure)

Architecture (from binary analysis of CosmicSupremacy.exe):
  - HTTP goes through HTTP_send_galaxy (VA 0x579DE0) → clientinterface.php
  - Binary goes through raw TCP sockets (VA 0x5F3650 connect, 0x562340/0x562470 send)
  - Both use the same host:port (localhost:8888 in debug mode)
  - Binary connection is established during galaxy validation (VA 0x562EA0)
  - Galaxy work function (VA 0x57A6B0) sends RQLG+galaxy_type, expects SAVE response
  - State 10 (VA 0x57A4E0) checks response for 0x53415645 ("SAVE") magic

The server auto-detects protocol by peeking at the first bytes of each connection.

Usage:
    python cs_server.py

Logs everything to cs_server.log for protocol analysis.
"""

import socketserver
import threading
import urllib.parse
import datetime
import struct
import json
import os
import sys
import io

PORT = int(os.environ.get('CSPORT', 8888))
LOGFILE = os.path.join(os.path.dirname(__file__), 'cs_server.log')

# ── Web UI served at GET / ───────────────────────────────────────────────────
WEB_INDEX = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Cosmic Supremacy - Local Server</title>
  <style>
    body { background: #050a1a; color: #a0c8ff; font-family: Arial, sans-serif;
           display: flex; flex-direction: column; align-items: center;
           justify-content: center; min-height: 100vh; margin: 0; }
    h1   { color: #00aaff; font-size: 2em; margin-bottom: 0.2em; }
    p    { color: #6090b0; margin: 0.3em 0; }
    .box { border: 1px solid #1a3a6a; background: #080f22; padding: 2em 3em;
           border-radius: 8px; text-align: center; max-width: 480px; }
    a.btn { display: inline-block; margin-top: 1.5em; padding: 0.7em 2em;
            background: #0055cc; color: #fff; text-decoration: none;
            border-radius: 4px; font-size: 1.1em; border: 1px solid #0077ff; }
    a.btn:hover { background: #0077ff; }
    .note { margin-top: 1.2em; font-size: 0.85em; color: #405070; }
  </style>
</head>
<body>
  <div class="box">
    <h1>Cosmic Supremacy</h1>
    <p>Dual-Protocol Local Server</p>
    <p style="margin-top:1em;">Choose a galaxy type, then drag the downloaded file
    onto <strong>CosmicSupremacy_patched18.exe</strong>.</p>
    <a class="btn" href="/enter-sandbox">Sandbox Galaxy</a>
    <a class="btn" href="/enter-testbed" style="background:#336;">TestBed Galaxy</a>
    <a class="btn" href="/enter-demo" style="background:#336;">Demo Galaxy</a>
    <p class="note">Downloads a <em>.csgalaxy</em> pass file &mdash;
    save it next to the .exe and drag it in.</p>
  </div>
</body>
</html>
"""

# ── Stub responses ────────────────────────────────────────────────────────────
DEMO_USERID   = 1
DEMO_PASSHASH = 'abcdef'
DEMO_GALAXY_ID = 0

# ── In-memory civ state ──────────────────────────────────────────────────────
_civ_state: dict = {}

def _default_civ() -> dict:
    return {'civname': 'DemoEmpire', 'coaid': '0'}

def _get_civ(userid: str) -> dict:
    return _civ_state.get(userid, _default_civ())

def _next_gameid(server_dir: str) -> int:
    existing = set()
    for fname in os.listdir(server_dir):
        if fname.startswith('save_game_') and fname.endswith('.dat'):
            core = fname[len('save_game_'):-len('.dat')]
            try:
                existing.add(int(core))
            except ValueError:
                pass
    n = 0
    while n in existing:
        n += 1
    return n


# ── Binary protocol magic values (big-endian 4-byte codes) ───────────────────
MAGIC_RQLG = 0x52514C47  # "RQLG" - Request Galaxy (client → server)
MAGIC_LGIN = 0x4C47494E  # "LGIN" - Login (client → server)
MAGIC_STCO = 0x5354434F  # "STCO" - State/sync (client → server, periodic)
MAGIC_SAVE = 0x53415645  # "SAVE" - Galaxy data response (server → client)
MAGIC_SUCC = 0x53554343  # "SUCC" - Success (server → client)
MAGIC_FAIL = 0x4641494C  # "FAIL" - Failure (server → client)
MAGIC_TOUR = 0x524F5554  # "TOUR" - Tutorial data (server → client)

MAGIC_NAMES = {
    MAGIC_RQLG: 'RQLG', MAGIC_LGIN: 'LGIN', MAGIC_STCO: 'STCO',
    MAGIC_SAVE: 'SAVE', MAGIC_SUCC: 'SUCC', MAGIC_FAIL: 'FAIL',
    MAGIC_TOUR: 'TOUR',
}


# ── Logging ──────────────────────────────────────────────────────────────────
_log_lock = threading.Lock()
_log_fh = None

def log(msg: str):
    global _log_fh
    with _log_lock:
        if _log_fh is None:
            _log_fh = open(LOGFILE, 'a', buffering=1, encoding='utf-8')
        ts = datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]
        line = f'[{ts}] {msg}'
        print(line)
        _log_fh.write(line + '\n')


# ── Hex dump helper ──────────────────────────────────────────────────────────
def _hexdump(data: bytes, label: str = '', width: int = 16) -> str:
    lines = []
    if label:
        lines.append(f'# {label}')
        lines.append(f'# {len(data)} bytes total')
        lines.append('')
    for i in range(0, len(data), width):
        chunk = data[i:i + width]
        hex_part  = ' '.join(f'{b:02x}' for b in chunk)
        hex_part  = f'{hex_part:<{width * 3 - 1}}'
        ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        lines.append(f'{i:08x}  {hex_part}  |{ascii_part}|')
    return '\n'.join(lines) + '\n'


# ══════════════════════════════════════════════════════════════════════════════
#  HTTP ACTION HANDLER (same as before)
# ══════════════════════════════════════════════════════════════════════════════

def handle_action(action: str, params: dict) -> tuple:
    """Returns (http_status, content_type, body)."""

    if action == 'testconnection':
        return 200, 'text/plain', 'READY'

    if action == 'login':
        return 200, 'text/plain', DEMO_PASSHASH

    if action == 'getplayerfame':
        return 200, 'text/plain', '0'

    if action == 'listcivnames':
        userid = params.get('userid', ['1'])[0]
        civ = _get_civ(userid)
        body = f'{civ["civname"]}#SPC#{civ["coaid"]}#NEXT#DONE'
        log(f'  -> listcivnames: userid={userid} civname={civ["civname"]!r} coaid={civ["coaid"]}')
        return 200, 'text/plain', body

    if action == 'uploadcivname':
        userid  = params.get('userid', ['1'])[0]
        civname = params.get('civname', [''])[0].strip("'")
        if civname:
            civ = _civ_state.setdefault(userid, _default_civ())
            civ['civname'] = civname
            log(f'  -> uploadcivname: userid={userid} civname={civname!r}')
        return 200, 'text/plain', 'OK'

    if action == 'listcoa':
        userid = params.get('userid', ['1'])[0]
        coaid  = _get_civ(userid)['coaid']
        body   = f'{coaid}#NEXT#DONE'
        log(f'  -> listcoa: userid={userid} coaid={coaid}')
        return 200, 'text/plain', body

    if action == 'getcoa':
        import base64
        empty_png = base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='
        )
        return 200, 'image/png', empty_png

    if action == 'uploadcoa':
        return 200, 'text/plain', 'OK'

    if action == 'savegame':
        gameid   = params.get('gameid',   ['0'])[0]
        gamename = params.get('gamename', ['unknown'])[0].strip("'")
        server_dir = os.path.dirname(__file__)
        if gameid == '-1':
            gameid = str(_next_gameid(server_dir))
            log(f'  -> savegame: allocated new gameid={gameid}')
        turn     = params.get('turn',     ['0'])[0]
        data_str   = params.get('data', [''])[0]
        data_bytes = data_str.encode('latin-1')
        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        latest_path = os.path.join(server_dir, f'save_game_{gameid}.dat')
        with open(latest_path, 'wb') as f:
            f.write(data_bytes)
        archive_base = os.path.join(server_dir, f'save_game_{gameid}_t{turn}_{ts}')
        with open(archive_base + '.dat', 'wb') as f:
            f.write(data_bytes)
        with open(archive_base + '.hex', 'w', encoding='utf-8') as f:
            f.write(_hexdump(data_bytes, label=f'gameid={gameid} name={gamename} turn={turn}'))
        log(f'  -> savegame: gameid={gameid} name={gamename} turn={turn} {len(data_bytes)} bytes')
        return 200, 'text/plain', 'DONE'

    if action == 'savegamelist':
        server_dir = os.path.dirname(__file__)
        entries = []
        for fname in sorted(os.listdir(server_dir)):
            if fname.startswith('save_game_') and fname.endswith('.dat'):
                core = fname[len('save_game_'):-len('.dat')]
                try:
                    n = int(core)
                    if n >= 0:
                        entries.append(f'{core}#SPC#Save {core}#SPC#0')
                except ValueError:
                    pass
        if not any(e.startswith('0#SPC#') for e in entries):
            entries.insert(0, '0#SPC#Save 1#SPC#0')
        result = '#NEXT#'.join(entries) + '#NEXT#DONE'
        log(f'  -> savegamelist: {len(entries)} slot(s)')
        return 200, 'text/plain', result

    if action == 'loadgame':
        gameid    = params.get('gameid', [str(DEMO_GALAXY_ID)])[0]
        save_path = os.path.join(os.path.dirname(__file__), f'save_game_{gameid}.dat')
        if os.path.exists(save_path):
            data_bytes = open(save_path, 'rb').read()
            data_str   = data_bytes.decode('latin-1')
            log(f'  -> loadgame: gameid={gameid} returning {len(data_bytes)} bytes')
            return 200, 'text/plain', 'DONE#VER#000000#DATA#' + data_str
        log(f'  -> loadgame: gameid={gameid} no save found')
        return 200, 'text/plain', 'DONE#VER#000000#DATA#'

    if action == 'savegov':
        govid    = params.get('govid',   ['0'])[0]
        govname  = params.get('govname', [''])[0].strip("'")
        data_str = params.get('data',    [''])[0]
        gov_path = os.path.join(os.path.dirname(__file__), f'save_gov_{govid}.dat')
        with open(gov_path, 'wb') as f:
            f.write(data_str.encode('latin-1'))
        log(f'  -> savegov: govid={govid} name={govname} {len(data_str)} bytes')
        return 200, 'text/plain', 'DONE'

    if action == 'govlist':
        return 200, 'text/plain', 'DONE'

    if action == 'loadgov':
        govid    = params.get('govid', ['0'])[0]
        gov_path = os.path.join(os.path.dirname(__file__), f'save_gov_{govid}.dat')
        if os.path.exists(gov_path):
            gov_data = open(gov_path, 'rb').read().decode('latin-1')
            return 200, 'text/plain', 'DONE#VER#000000#DATA#' + gov_data
        return 200, 'text/plain', 'DONE#VER#000000#DATA#'

    if action == 'passedtutorial':
        return 200, 'text/plain', 'OK'

    if action == 'entertestbedgalaxy':
        import base64 as _b64
        userid   = params.get('userid', ['?'])[0]
        pass_raw = params.get('pass', [''])[0]
        one_len  = len(pass_raw) // 16 if len(pass_raw) >= 16 else len(pass_raw)
        one_b64  = pass_raw[:one_len]
        try:
            one_decoded = _b64.b64decode(one_b64).decode('utf-8', errors='replace')
        except Exception:
            one_decoded = '(decode error)'
        log(f'  -> entertestbedgalaxy: userid={userid}, pass={len(pass_raw)} chars')
        log(f'     one-token decoded: {repr(one_decoded[:120])}')
        return 200, 'text/plain', 'OK|0'

    # ── Unknown action ────────────────────────────────────────────────────────
    sep = '!' * 60
    log(sep)
    log(f'  [NEW ACTION?]  action={action!r}')
    interesting = {k: v for k, v in params.items()
                   if k not in ('userid', 'passhash', 'action')}
    if interesting:
        for k, vs in interesting.items():
            v0 = vs[0] if isinstance(vs, list) else str(vs)
            log(f'    param {k!r} = {repr(v0[:120])}')
    log(f'  -> returning empty OK')
    log(sep)
    return 200, 'text/plain', 'OK'


# ══════════════════════════════════════════════════════════════════════════════
#  BINARY PROTOCOL HANDLER
# ══════════════════════════════════════════════════════════════════════════════

def handle_client_update_check(data: bytes, addr) -> bytes:
    """Handle the ClientUpdateCheck text protocol.

    The game sends this BEFORE the binary RQLG protocol, via vtable[2] dispatch
    in 0x563310. Format:
        ClientUpdateCheck V1.2\r\n
        <version_number>\r\n

    The game reads the response as text. If the response indicates "no update
    needed", the game proceeds to the RQLG connection. If the response is
    unexpected, it shows the response text as a popup message.

    Strategy: echo back the client's version number to indicate "you're current".
    """
    text = data.decode('latin-1', errors='replace').strip()
    lines = text.split('\r\n')
    log(f'  CLIENT UPDATE CHECK:')
    for i, line in enumerate(lines):
        log(f'    line[{i}]: {line!r}')

    # Extract version number from line 2
    client_version = lines[1] if len(lines) >= 2 else '0'
    log(f'  client version: {client_version}')

    # The standalone ClientUpdateCheck (VA 0x56C770) compares the response
    # against specific keywords: "UpToDate", "Corrupt", "Update", "Patch".
    # "UpToDate" sets [0x86EFAD]=1 and proceeds past the connection popup.
    # Any other response (including empty) causes the game to stay on the popup.
    response_text = 'UpToDate\r\n'
    log(f'  -> responding with UpToDate (no update needed)')
    return response_text.encode('latin-1')


def handle_binary_connection(sock, addr):
    """Handle a persistent binary TCP connection from the game client.

    With CMND-skip patch (Patch H), the game sends RAW stream-format messages:
      Bytes 0-3: magic (big-endian, e.g. RQLG = 52 51 4C 47)
      Bytes 4-7: flags/size dword (upper 6 bits = flags, lower 26 bits = payload size)
      Bytes 8+:  payload data

    Total message size = 8 + (lower_26_bits of dword at offset 4)

    The server response must use the same format.
    """
    log(f'BINARY TCP: new connection from {addr}')
    sock.settimeout(120.0)

    buf = b''
    msg_count = 0

    try:
        while True:
            try:
                chunk = sock.recv(4096)
            except (TimeoutError, OSError):
                log(f'BINARY TCP [{addr}]: timeout or socket error, closing')
                break

            if not chunk:
                log(f'BINARY TCP [{addr}]: connection closed by client')
                break

            buf += chunk
            msg_count += 1

            log(f'BINARY TCP [{addr}]: received {len(chunk)} bytes (total buf: {len(buf)})')
            log(f'  hex: {chunk[:128].hex(" ")}')
            log(f'  ascii: {repr(chunk[:128])}')

            # Save raw data for analysis
            server_dir = os.path.dirname(__file__)
            ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            dump_path = os.path.join(server_dir, f'binary_recv_{ts}.hex')
            with open(dump_path, 'w', encoding='utf-8') as f:
                f.write(_hexdump(buf, label=f'binary recv from {addr}, msg #{msg_count}, full buf'))

            # Check for text-based ClientUpdateCheck
            if buf.startswith(b'ClientUpdateCheck'):
                response = handle_client_update_check(buf, addr)
                if response is not None:
                    log(f'  -> sending {len(response)} bytes response')
                    sock.sendall(response)
                buf = b''
                continue

            # Need at least 8 bytes for stream header
            if len(buf) < 8:
                log(f'  waiting for more data (have {len(buf)}, need 8)')
                continue

            # Parse stream header
            # Magic is big-endian (byte-swapped by 0x5E6260)
            magic = struct.unpack('>I', buf[:4])[0]
            # flags_size is LITTLE-ENDIAN (native x86, written by 0x5E6260 as dword)
            flags_size = struct.unpack('<I', buf[4:8])[0]
            flags = (flags_size >> 26) & 0x3F
            payload_size = flags_size & 0x03FFFFFF
            total_msg_size = 8 + payload_size
            magic_name = MAGIC_NAMES.get(magic, f'UNKNOWN(0x{magic:08X})')

            log(f'  STREAM HEADER: magic={magic_name} flags=0x{flags:02X} payload_size={payload_size} total={total_msg_size}')
            log(f'  raw header bytes: {buf[:8].hex(" ")}')

            # Sanity check: if payload_size is unreasonably large (placeholder
            # 0x3FFFFFF from un-flushed mode-0 headers), use available data
            if payload_size > 0x100000:  # > 1MB is suspicious
                actual_payload = len(buf) - 8
                log(f'  WARNING: payload_size={payload_size} seems like placeholder, using actual={actual_payload}')
                payload_size = actual_payload
                total_msg_size = 8 + payload_size

            # Wait for complete message
            if len(buf) < total_msg_size:
                log(f'  waiting for more data (have {len(buf)}, need {total_msg_size})')
                continue

            # Extract payload
            payload = buf[8:total_msg_size]
            log(f'  payload ({len(payload)} bytes): {payload[:64].hex(" ") if payload else "(empty)"}')

            # Handle the message
            response = handle_binary_message(magic, payload, flags, addr)

            if response is not None:
                log(f'  -> sending {len(response)} bytes response')
                log(f'     hex: {response[:128].hex(" ")}')

                resp_path = os.path.join(server_dir, f'binary_sent_{ts}.hex')
                with open(resp_path, 'w', encoding='utf-8') as f:
                    f.write(_hexdump(response, label=f'binary response to {addr}'))

                try:
                    sock.sendall(response)
                except OSError as e:
                    log(f'BINARY TCP [{addr}]: send error: {e}')
                    break

            # Consume processed message, keep any remainder
            buf = buf[total_msg_size:]

    except Exception as e:
        log(f'BINARY TCP [{addr}]: exception: {e}')
        import traceback
        log(traceback.format_exc())
    finally:
        try:
            sock.close()
        except Exception:
            pass
        log(f'BINARY TCP [{addr}]: connection handler done')


def build_stream_response(magic: int, payload: bytes, flags: int = 0) -> bytes:
    """Build a stream-format response: [magic 4B BE][flags|size 4B LE][payload].

    Wire format (matches client's 0x5E6260 encoding):
      - 4-byte magic in BIG-ENDIAN (byte-swapped by client's 0x5E6260)
      - 4-byte flags/size in LITTLE-ENDIAN (native x86 dword from 0x5E6260)
      - payload_size bytes of data

    The game's receive loop (0x561A00) reads the magic, byte-swaps it back
    to native for comparison. The flags/size is read as a native LE dword.

    After receive, 0x561310 checks if magic == CMND. If not CMND, the data
    is used as-is (no decryption needed). This is our raw mode.
    """
    payload_size = len(payload)
    flags_size = ((flags & 0x3F) << 26) | (payload_size & 0x03FFFFFF)
    header = struct.pack('>I', magic) + struct.pack('<I', flags_size)
    return header + payload


def handle_binary_message(magic: int, payload: bytes, flags: int, addr) -> bytes | None:
    """Process a binary protocol message and return the stream-format response.

    Args:
        magic: 4-byte magic from stream header (e.g. MAGIC_RQLG)
        payload: the payload bytes AFTER the 8-byte stream header
        flags: upper 6 bits from the flags/size dword
        addr: client address for logging
    """

    if magic == MAGIC_RQLG:
        # Request Galaxy — sent by galaxy work function (VA 0x57A6B0)
        # Payload: galaxy_type (4 bytes, big-endian)
        galaxy_type = None
        if len(payload) >= 4:
            galaxy_type = struct.unpack('>I', payload[:4])[0]
        log(f'  RQLG: Request Galaxy — galaxy_type={galaxy_type} flags=0x{flags:02X}')
        log(f'  payload ({len(payload)} bytes): {payload.hex(" ") if payload else "(empty)"}')
        log(f'     *** RQLG RECEIVED — TCP BINARY PROTOCOL WORKING ***')

        # Respond with SAVE in stream format.
        # The game reads SAVE magic, then expects galaxy data as payload.
        # For now, send a minimal payload. We can expand this later
        # once we understand the galaxy data format.
        # Use flags=1 to match what the client sends.
        galaxy_data = b'\x00' * 4  # minimal placeholder
        response = build_stream_response(MAGIC_SAVE, galaxy_data, flags=1)
        log(f'  -> responding with SAVE ({len(response)} bytes)')
        return response

    elif magic == MAGIC_LGIN:
        log(f'  LGIN: Login request')
        log(f'  payload ({len(payload)} bytes): {payload.hex(" ") if payload else "(empty)"}')

        response = build_stream_response(MAGIC_SUCC, b'', flags=0)
        log(f'  -> responding with SUCC')
        return response

    elif magic == MAGIC_STCO:
        log(f'  STCO: State sync ({len(payload)} bytes)')
        log(f'  payload: {payload[:64].hex(" ") if payload else "(empty)"}')

        server_dir = os.path.dirname(__file__)
        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        state_path = os.path.join(server_dir, f'state_sync_{ts}.dat')
        with open(state_path, 'wb') as f:
            f.write(payload)

        response = build_stream_response(MAGIC_SUCC, b'', flags=0)
        return response

    else:
        log(f'  UNKNOWN BINARY: magic=0x{magic:08X} flags=0x{flags:02X}')
        log(f'  payload ({len(payload)} bytes): {payload[:256].hex(" ") if payload else "(empty)"}')

        server_dir = os.path.dirname(__file__)
        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        unk_path = os.path.join(server_dir, f'unknown_binary_{ts}.dat')
        with open(unk_path, 'wb') as f:
            f.write(payload)

        response = build_stream_response(MAGIC_SUCC, b'', flags=0)
        return response


# ══════════════════════════════════════════════════════════════════════════════
#  DUAL-PROTOCOL CONNECTION HANDLER
# ══════════════════════════════════════════════════════════════════════════════

class DualProtocolHandler(socketserver.BaseRequestHandler):
    """Handles each incoming TCP connection.

    Peeks at the first bytes to detect protocol:
    - If starts with 'POST ', 'GET ', 'HEAD ', 'PUT ' → HTTP
    - Otherwise → Binary TCP protocol
    """

    def handle(self):
        sock = self.request
        addr = self.client_address
        log(f'CONNECTION ACCEPTED [{addr}] (before protocol detect)')

        try:
            # Peek at the first bytes to detect protocol
            # MSG_PEEK leaves data in the recv buffer for the actual handler
            import socket as _socket
            sock.settimeout(30.0)
            first_bytes = sock.recv(8, _socket.MSG_PEEK)

            if not first_bytes:
                log(f'CONNECTION [{addr}]: empty peek (instant close)')
                return

            # Check if it looks like HTTP
            if first_bytes[:4] in (b'POST', b'GET ', b'HEAD', b'PUT '):
                # HTTP request — handle with the HTTP machinery
                handle_http_connection(sock, addr)
            elif first_bytes[:4] == b'Clie':
                # Raw TCP ClientUpdateCheck (text protocol, not HTTP-wrapped)
                log(f'PROTOCOL DETECT [{addr}]: raw ClientUpdateCheck')
                handle_binary_connection(sock, addr)
            else:
                # Binary protocol — log the detection
                magic_hex = first_bytes[:4].hex() if len(first_bytes) >= 4 else first_bytes.hex()
                log(f'PROTOCOL DETECT [{addr}]: binary (first bytes: {magic_hex})')
                handle_binary_connection(sock, addr)

        except Exception as e:
            log(f'CONNECTION [{addr}]: error in protocol detection: {e}')
            import traceback
            log(traceback.format_exc())


def handle_http_connection(sock, addr):
    """Handle an HTTP connection by parsing the request manually.

    We can't easily use http.server.BaseHTTPRequestHandler with raw sockets,
    so we parse HTTP/1.0 requests ourselves.
    """
    try:
        # Read the full HTTP request
        sock.settimeout(10.0)
        raw = b''
        while b'\r\n\r\n' not in raw:
            chunk = sock.recv(4096)
            if not chunk:
                return
            raw += chunk
            if len(raw) > 65536:
                log(f'HTTP [{addr}]: request too large, dropping')
                return

        # Split headers from body
        header_end = raw.index(b'\r\n\r\n')
        header_bytes = raw[:header_end]
        body_start = raw[header_end + 4:]

        # Parse request line
        header_lines = header_bytes.decode('latin-1').split('\r\n')
        request_line = header_lines[0]
        parts = request_line.split(' ', 2)
        if len(parts) < 3:
            log(f'HTTP [{addr}]: malformed request line: {request_line!r}')
            return

        method, path, http_version = parts

        # Parse headers
        headers = {}
        for line in header_lines[1:]:
            if ':' in line:
                key, val = line.split(':', 1)
                headers[key.strip().lower()] = val.strip()

        # Read remaining body if Content-Length indicates more
        content_length = int(headers.get('content-length', '0'))
        body = body_start
        while len(body) < content_length:
            remaining = content_length - len(body)
            chunk = sock.recv(min(remaining, 4096))
            if not chunk:
                break
            body += chunk

        body_str = body.decode('latin-1')

        # Route the request
        if method == 'GET':
            handle_http_get(sock, addr, path, headers)
        elif method == 'POST':
            handle_http_post(sock, addr, path, headers, body_str)
        else:
            log(f'HTTP [{addr}]: unsupported method {method}')
            send_http_response(sock, 405, 'text/plain', b'Method Not Allowed')

    except Exception as e:
        log(f'HTTP [{addr}]: error: {e}')
        import traceback
        log(traceback.format_exc())


def handle_http_get(sock, addr, path, headers):
    """Handle HTTP GET requests."""
    log(f'GET {path}')

    clean_path = path.split('?')[0].rstrip('/')

    # Web UI
    if clean_path in ('', '/index.html', '/index.htm'):
        resp_bytes = WEB_INDEX.encode('utf-8')
        send_http_response(sock, 200, 'text/html; charset=utf-8', resp_bytes)
        return

    # Galaxy pass download routes
    galaxy_routes = {
        '/enter-sandbox': ('../client/SandboxGalaxy_local.csgalaxy', 'SandboxGalaxy.csgalaxy'),
        '/enter-testbed': ('../client/TestBedGalaxy_local.csgalaxy', 'TestBedGalaxy.csgalaxy'),
        '/enter-demo':    ('../client/DemoGalaxy_local.csgalaxy', 'DemoGalaxy.csgalaxy'),
    }
    if clean_path in galaxy_routes:
        rel_path, download_name = galaxy_routes[clean_path]
        galaxy_path = os.path.join(os.path.dirname(__file__), rel_path)
        if os.path.exists(galaxy_path):
            resp_bytes = open(galaxy_path, 'rb').read()
        else:
            resp_bytes = b''
        log(f'  <- serving {download_name} ({len(resp_bytes)} bytes)')
        extra_headers = {
            'Content-Disposition': f'attachment; filename="{download_name}"',
        }
        send_http_response(sock, 200, 'application/octet-stream', resp_bytes, extra_headers)
        return

    if clean_path == '/favicon.ico':
        send_http_response(sock, 204, 'text/plain', b'')
        return

    # Game API GET requests
    params = {}
    if '?' in path:
        qs = path.split('?', 1)[1]
        params = urllib.parse.parse_qs(qs, keep_blank_values=True)
    action = params.get('action', ['<none>'])[0]

    status, ctype, resp_body = handle_action(action, params)
    if isinstance(resp_body, str):
        resp_bytes = resp_body.encode('latin-1')
    else:
        resp_bytes = resp_body

    log(f'  <- {status}  {len(resp_bytes)} bytes')
    send_http_response(sock, status, ctype, resp_bytes)


def handle_http_post(sock, addr, path, headers, body_str):
    """Handle HTTP POST requests."""
    content_type = headers.get('content-type', '')
    clean_path = path.split('?')[0]

    log(f'POST {path}')
    log(f'  Content-Type={content_type}  Content-Length={len(body_str)}')

    # ── ClientUpdateCheck (version check protocol) ─────────────────────
    # Can arrive at /update.cs OR /clientinterface.php or any path
    # Detect by checking the body content
    if clean_path == '/update.cs' or body_str.startswith('ClientUpdateCheck'):
        log(f'  CLIENT UPDATE CHECK via HTTP (path={clean_path}):')
        lines = body_str.strip().split('\r\n')
        for i, line in enumerate(lines):
            log(f'    line[{i}]: {line!r}')
        client_version = lines[1] if len(lines) >= 2 else '0'
        log(f'  client version: {client_version}')
        # The standalone ClientUpdateCheck compares against keywords:
        # "UpToDate", "Corrupt", "Update", "Patch". Must respond "UpToDate".
        log(f'  -> responding with UpToDate (no update needed)')
        send_http_response(sock, 200, 'text/plain', b'UpToDate')
        return

    # ── Standard game API (action-based) ─────────────────────────────────
    params = urllib.parse.parse_qs(body_str, keep_blank_values=True)

    # Action can be in URL query string OR POST body
    url_qs = {}
    if '?' in path:
        url_qs = urllib.parse.parse_qs(path.split('?', 1)[1], keep_blank_values=True)
    action = (url_qs.get('action') or params.get('action') or ['<none>'])[0]
    merged_params = {**url_qs, **params}

    log(f'  action={action}')

    CHUNK = 400
    if len(body_str) <= CHUNK:
        log(f'  body: {body_str}')
    else:
        for ci, start in enumerate(range(0, len(body_str), CHUNK)):
            tag = 'body' if ci == 0 else 'body+'
            log(f'  {tag}: {body_str[start:start+CHUNK]}')

    status, ctype, resp_body = handle_action(action, merged_params)
    if isinstance(resp_body, str):
        resp_bytes = resp_body.encode('latin-1')
    else:
        resp_bytes = resp_body

    log(f'  <- {status}  {len(resp_bytes)} bytes  {repr(resp_bytes[:80])}')
    send_http_response(sock, status, ctype, resp_bytes)


def send_http_response(sock, status, content_type, body_bytes, extra_headers=None):
    """Send an HTTP/1.0 response over the socket."""
    status_text = {200: 'OK', 204: 'No Content', 404: 'Not Found', 405: 'Method Not Allowed'}
    status_str = status_text.get(status, 'OK')

    response = f'HTTP/1.0 {status} {status_str}\r\n'
    response += f'Content-Type: {content_type}\r\n'
    response += f'Content-Length: {len(body_bytes)}\r\n'
    response += 'Server: CosmicSupremacy/2.0\r\n'
    response += 'Connection: close\r\n'
    if extra_headers:
        for k, v in extra_headers.items():
            response += f'{k}: {v}\r\n'
    response += '\r\n'

    try:
        sock.sendall(response.encode('latin-1') + body_bytes)
    except OSError as e:
        log(f'HTTP send error: {e}')


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN SERVER
# ══════════════════════════════════════════════════════════════════════════════

class DualProtocolServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == '__main__':
    log('=' * 60)
    log(f'Cosmic Supremacy DUAL-PROTOCOL server starting on port {PORT}')
    log(f'  HTTP:   handles all 15 game actions (testconnection, savegame, etc.)')
    log(f'  Binary: handles RQLG/LGIN/STCO galaxy data protocol')
    log(f'  Log:    {LOGFILE}')
    log(f'  Web:    http://127.0.0.1:{PORT}/')
    log(f'')
    log(f'  Use CosmicSupremacy_patched18.exe with SandboxGalaxy_local.csgalaxy')
    log(f'  (patched18 has rewritten direct TCP connection to localhost:8888)')
    log('=' * 60)

    server = DualProtocolServer(('0.0.0.0', PORT), DualProtocolHandler)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log('Server stopped.')
