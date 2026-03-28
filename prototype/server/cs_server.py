"""
cs_server.py — Cosmic Supremacy local stub server
==================================================
Replaces the original cosmicsupremacy.com backend so the patched EXE can run
locally.  All requests are logged to cs_server.log so you can see exactly what
the game sends and reverse-engineer the expected response format.

Usage (Windows, run as Administrator OR use port > 1024 and set CSPORT):
    python cs_server.py

The patched EXE connects to 127.0.0.1:8888 for everything.
Double-click DemoGalaxy_local.csgalaxy to load the demo galaxy.

Protocol notes (from binary analysis):
  • HTTP/1.0 POST to /clientinterface.php?
  • Content-Type: application/x-cosmicsupremacy
  • Body:  action=<name>&userid=<int>&passhash='<hash>'&...
  • Login: action=login&userid=<int>&pass=<password>
  • Galaxy data is base64 or proprietary encoded (TBD — discover via logs)

Savegame capture:
  Each savegame POST writes two files:
    save_game_<gameid>.dat     — raw binary blob (latest save)
    save_game_<gameid>_t<turn>_<timestamp>.dat   — per-turn archive
    save_game_<gameid>_t<turn>_<timestamp>.hex   — hex+ASCII dump for analysis
"""

import http.server
import urllib.parse
import datetime
import json
import os
import sys
import textwrap

PORT = int(os.environ.get('CSPORT', 8888))
LOGFILE = os.path.join(os.path.dirname(__file__), 'cs_server.log')

# ── Web UI served at GET / (the game opens a browser here on first run) ───────
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
    <p>Local Stub Server</p>
    <p style="margin-top:1em;">Click below to download the Demo Galaxy pass file,
    then drag it onto <strong>CosmicSupremacy_patched.exe</strong>.</p>
    <a class="btn" href="/enter-demo">Enter Demo Galaxy</a>
    <p class="note">This downloads <em>GalaxyPass.csgalaxy</em> &mdash;
    save it next to the .exe and drag it in.</p>
  </div>
</body>
</html>
"""

# ── Hard-coded stub responses ─────────────────────────────────────────────────
# These are best-guess responses based on known endpoint names.
# Edit these as you discover what the game actually expects from the logs.

DEMO_USERID   = 1
DEMO_PASSHASH = 'abcdef'   # from DemoGalaxy_local.csgalaxy token
DEMO_GALAXY_ID = 0

def handle_action(action: str, params: dict) -> tuple[int, str, str]:
    """
    Returns (http_status, content_type, body).
    Add real response formats here as you discover them from cs_server.log.
    """

    # ── Connection health check ───────────────────────────────────────────────
    if action == 'testconnection':
        return 200, 'text/plain', 'READY'

    # ── Login / auth ──────────────────────────────────────────────────────────
    # Game sends: userid=<int>&pass=<password>
    # Expected response: likely the passhash string the game uses for future requests
    if action == 'login':
        # Accept any login; return the demo passhash
        return 200, 'text/plain', DEMO_PASSHASH

    # ── Player fame ───────────────────────────────────────────────────────────
    if action == 'getplayerfame':
        return 200, 'text/plain', '0'

    # ── Civ names ─────────────────────────────────────────────────────────────
    if action == 'listcivnames':
        return 200, 'text/plain', 'DemoEmpire'

    if action == 'uploadcivname':
        return 200, 'text/plain', 'OK'

    # ── Coat of arms ─────────────────────────────────────────────────────────
    if action == 'listcoa':
        return 200, 'text/plain', ''

    if action == 'getcoa':
        # Return empty 1x1 PNG as placeholder
        import base64
        empty_png = base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='
        )
        return 200, 'image/png', empty_png

    if action == 'uploadcoa':
        return 200, 'text/plain', 'OK'

    # ── Save / load game ──────────────────────────────────────────────────────
    # Game sends: userid=...&passhash=...&gameid=...&gamename=...&turn=...&version=...&data=...
    if action == 'savegame':
        gameid   = params.get('gameid',   ['0'])[0]
        gamename = params.get('gamename', ['unknown'])[0].strip("'")
        turn     = params.get('turn',     ['0'])[0]
        # data= is URL-decoded by parse_qs; re-encode to bytes via latin-1
        # (the game sends a binary blob percent-encoded; latin-1 is byte-for-byte)
        data_str   = params.get('data', [''])[0]
        data_bytes = data_str.encode('latin-1')

        server_dir = os.path.dirname(__file__)
        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')

        # Latest save (overwritten each turn — used by loadgame)
        latest_path = os.path.join(server_dir, f'save_game_{gameid}.dat')
        with open(latest_path, 'wb') as f:
            f.write(data_bytes)

        # Per-turn archive + hex dump for analysis
        archive_base = os.path.join(server_dir, f'save_game_{gameid}_t{turn}_{ts}')
        with open(archive_base + '.dat', 'wb') as f:
            f.write(data_bytes)
        with open(archive_base + '.hex', 'w', encoding='utf-8') as f:
            f.write(_hexdump(data_bytes, label=f'gameid={gameid} name={gamename} turn={turn}'))

        log(f'  -> savegame: gameid={gameid} name={gamename} turn={turn} '
            f'{len(data_bytes)} bytes')
        log(f'     latest:  {latest_path}')
        log(f'     hexdump: {archive_base}.hex')
        return 200, 'text/plain', 'OK'

    if action == 'savegamelist':
        # Scan for latest save files (save_game_<digits>.dat) and report them.
        server_dir = os.path.dirname(__file__)
        entries = []
        for fname in sorted(os.listdir(server_dir)):
            if fname.startswith('save_game_') and fname.endswith('.dat'):
                core = fname[len('save_game_'):-len('.dat')]
                if core.isdigit():           # only "latest" files, not archives
                    entries.append(f'{core} DemoGalaxy 0')
        result = '\n'.join(entries) if entries else ''
        log(f'  -> savegamelist: {len(entries)} saves')
        return 200, 'text/plain', result

    if action == 'loadgame':
        gameid    = params.get('gameid', [str(DEMO_GALAXY_ID)])[0]
        save_path = os.path.join(os.path.dirname(__file__), f'save_game_{gameid}.dat')
        if os.path.exists(save_path):
            data_bytes = open(save_path, 'rb').read()
            log(f'  -> loadgame: gameid={gameid} returning {len(data_bytes)} bytes')
            return 200, 'text/plain', data_bytes.decode('latin-1')
        log(f'  -> loadgame: gameid={gameid} no save found, returning empty')
        return 200, 'text/plain', ''

    # ── Governor settings ─────────────────────────────────────────────────────
    if action == 'savegov':
        govid    = params.get('govid',   ['0'])[0]
        govname  = params.get('govname', [''])[0].strip("'")
        data_str = params.get('data',    [''])[0]
        gov_path = os.path.join(os.path.dirname(__file__), f'save_gov_{govid}.dat')
        with open(gov_path, 'wb') as f:
            f.write(data_str.encode('latin-1'))
        log(f'  -> savegov: govid={govid} name={govname} {len(data_str)} bytes')
        return 200, 'text/plain', 'OK'

    if action == 'govlist':
        return 200, 'text/plain', ''

    if action == 'loadgov':
        govid    = params.get('govid', ['0'])[0]
        gov_path = os.path.join(os.path.dirname(__file__), f'save_gov_{govid}.dat')
        if os.path.exists(gov_path):
            return 200, 'text/plain', open(gov_path, 'rb').read().decode('latin-1')
        return 200, 'text/plain', ''

    # ── Tutorial / test-bed ───────────────────────────────────────────────────
    if action == 'passedtutorial':
        return 200, 'text/plain', 'OK'

    if action == 'entertestbedgalaxy':
        # Called when loading a TEBE-type galaxy pass file.
        # POST body: userid=<int>&pass=<16× base64-encoded credential tokens>
        #
        # Each token decodes to: "TEBE <server_ip> <playerid> <hexpass> <playername>"
        # The 16 tokens correspond to the 16 player slots in the testbed galaxy.
        #
        # We return 'OK' to acknowledge. The game displays this as a dialog;
        # after the user clicks OK further requests may follow — watch the log.
        import base64 as _b64
        userid   = params.get('userid', ['?'])[0]
        pass_raw = params.get('pass', [''])[0]
        # The pass field = .csgalaxy raw bytes × 16 (one copy per player slot).
        # Figure out one token = pass_raw / 16.
        one_len  = len(pass_raw) // 16
        one_b64  = pass_raw[:one_len]
        try:
            one_decoded = _b64.b64decode(one_b64).decode('utf-8', errors='replace')
        except Exception:
            one_decoded = '(decode error)'
        log(f'  -> entertestbedgalaxy: userid={userid}, pass={len(pass_raw)} chars = 16×{one_len}')
        log(f'     one-token decoded ({len(one_decoded)} chars): {repr(one_decoded[:120])}')
        # Binary analysis shows the game calls strstr(response, "OK|") to detect success.
        # Returning plain "OK" (no pipe) → strstr returns NULL → game treats response as
        # an error message string, displays it in a dialog, then closes.
        #
        # The correct format is "OK|<token>" where <token> is the data passed to the
        # galaxy-init function (FUN_0058aafb). We try a minimal galaxy ID first.
        # If the game makes follow-up requests (getgalaxydata, loadgame, etc.) after
        # receiving this, those will appear in the log — keep the server running.
        return 200, 'text/plain', 'OK|0'

    # ── Unknown action: log and return empty OK ───────────────────────────────
    log(f'  [?] UNKNOWN action={action!r} - returning empty OK')
    return 200, 'text/plain', 'OK'


# ── Hex dump helper ───────────────────────────────────────────────────────────
def _hexdump(data: bytes, label: str = '', width: int = 16) -> str:
    """Return a classic hex+ASCII dump string for binary analysis."""
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


# ── Logging ───────────────────────────────────────────────────────────────────
_log_fh = None

def log(msg: str):
    global _log_fh
    if _log_fh is None:
        _log_fh = open(LOGFILE, 'a', buffering=1, encoding='utf-8')
    ts = datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]
    line = f'[{ts}] {msg}'
    print(line)
    _log_fh.write(line + '\n')


# ── HTTP handler ──────────────────────────────────────────────────────────────
class CSHandler(http.server.BaseHTTPRequestHandler):
    server_version = 'CosmicSupremacy/1.0'
    protocol_version = 'HTTP/1.0'

    def log_message(self, fmt, *args):
        pass  # suppress default logging; we do our own

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body   = self.rfile.read(length).decode('latin-1') if length else ''
        params = urllib.parse.parse_qs(body, keep_blank_values=True)

        # Action can be in URL query string OR POST body — check both.
        # The game puts action= in the URL query string for most POST calls.
        url_qs = {}
        if '?' in self.path:
            url_qs = urllib.parse.parse_qs(self.path.split('?', 1)[1], keep_blank_values=True)
        action = (url_qs.get('action') or params.get('action') or ['<none>'])[0]
        # Merge URL params into body params (body wins on collision)
        merged_params = {**url_qs, **params}

        log(f'POST {self.path}')
        log(f'  Content-Type={self.headers.get("Content-Type","")}  Content-Length={length}')
        log(f'  action={action}')
        # Log body in 400-char chunks so nothing is lost (params can be large blobs)
        CHUNK = 400
        if len(body) <= CHUNK:
            log(f'  body: {body}')
        else:
            for ci, start in enumerate(range(0, len(body), CHUNK)):
                tag = 'body' if ci == 0 else 'body+'
                log(f'  {tag}: {body[start:start+CHUNK]}')

        status, ctype, resp_body = handle_action(action, merged_params)

        if isinstance(resp_body, str):
            resp_bytes = resp_body.encode('latin-1')
        else:
            resp_bytes = resp_body  # already bytes (e.g. PNG)

        log(f'  <- {status}  {len(resp_bytes)} bytes  {repr(resp_bytes[:80])}')
        self._send(status, ctype, resp_bytes)

    def do_GET(self):
        log(f'GET {self.path}')

        # ── Web UI routes (browser opened by the game on first run) ──────────
        path = self.path.split('?')[0].rstrip('/')

        if path in ('', '/index.html', '/index.htm'):
            resp_bytes = WEB_INDEX.encode('utf-8')
            self._send(200, 'text/html; charset=utf-8', resp_bytes)
            return

        if path == '/enter-demo':
            # Serve DemoGalaxy_local.csgalaxy as a file download
            galaxy_path = os.path.join(os.path.dirname(__file__), 'DemoGalaxy_local.csgalaxy')
            if os.path.exists(galaxy_path):
                resp_bytes = open(galaxy_path, 'rb').read()
            else:
                resp_bytes = b''
            log(f'  <- serving DemoGalaxy_local.csgalaxy ({len(resp_bytes)} bytes)')
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Disposition', 'attachment; filename="GalaxyPass.csgalaxy"')
            self.send_header('Content-Length', str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)
            return

        if path == '/favicon.ico':
            self._send(204, 'text/plain', b'')
            return

        # ── Game API GET requests (action= parameter) ─────────────────────────
        params = {}
        if '?' in self.path:
            qs     = self.path.split('?', 1)[1]
            params = urllib.parse.parse_qs(qs, keep_blank_values=True)
        action = params.get('action', ['<none>'])[0]

        status, ctype, resp_body = handle_action(action, params)
        if isinstance(resp_body, str):
            resp_bytes = resp_body.encode('latin-1')
        else:
            resp_bytes = resp_body

        log(f'  <- {status}  {len(resp_bytes)} bytes')
        self._send(status, ctype, resp_bytes)

    def _send(self, status, ctype, body_bytes):
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    server = http.server.HTTPServer(('0.0.0.0', PORT), CSHandler)
    log(f'Cosmic Supremacy stub server listening on port {PORT}')
    log(f'All traffic logged to: {LOGFILE}')
    log(f'Start: double-click DemoGalaxy_local.csgalaxy (after starting patched EXE)')
    log('-' * 60)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log('Server stopped.')
