"""
cs_server.py — Cosmic Supremacy local stub server
==================================================
Replaces the original cosmicsupremacy.com backend so the patched EXE can run
locally.  Keeps the game open and responsive to its HTTP protocol so that the
external memory-reader tools (ejbo_viewer.py) can inspect live game state.

Usage (Windows, run as Administrator OR use port > 1024 and set CSPORT):
    python cs_server.py

The patched EXE connects to 127.0.0.1:8888 for everything.
Double-click DemoGalaxy_local.csgalaxy to load the demo galaxy.

Protocol notes (from binary analysis):
  • HTTP/1.0 POST to /clientinterface.php?
  • Content-Type: application/x-cosmicsupremacy
  • Body:  action=<name>&userid=<int>&passhash='<hash>'&...
  • Login: action=login&userid=<int>&pass=<password>
"""

import http.server
import urllib.parse
import datetime
import os
import sys

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

# ── In-memory civ state (persists across ticks within a server session) ────────
# Populated by uploadcivname; queried by listcivnames and listcoa.
# Format: { userid_str: {'civname': str, 'coaid': str} }
_civ_state: dict = {}

def _default_civ() -> dict:
    return {'civname': 'DemoEmpire', 'coaid': '0'}

def _get_civ(userid: str) -> dict:
    return _civ_state.get(userid, _default_civ())

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
    # Binary analysis (FUN_0x497f93 / FUN_0x496830):
    #   - FUN_0x496830 checks [esi+0x4988] each tick; if ≥ 0 and in range it
    #     shows the "Customize Your Home World" popup.
    #   - FUN_0x497f93 sends listcivnames, then parses the response with
    #     0x5e3de0 using "#SPC#" as the field delimiter.
    #   - Each parsed record is a 0x20-byte entry; [entry+0x14] is the coaid
    #     field pointer.  If [entry+0x14] == 0 (null/empty), the game never
    #     marks the civ "configured" → popup re-appears every tick.
    #   - Correct format (mirrors savegamelist): civname#SPC#coaid#NEXT#DONE
    #     With a non-empty coaid the game marks the civ configured and
    #     clears the popup trigger.
    if action == 'listcivnames':
        userid = params.get('userid', ['1'])[0]
        civ = _get_civ(userid)
        civname = civ['civname']
        coaid   = civ['coaid']
        body = f'{civname}#SPC#{coaid}#NEXT#DONE'
        log(f'  -> listcivnames: userid={userid} civname={civname!r} coaid={coaid} ({len(body)} bytes)')
        return 200, 'text/plain', body

    if action == 'uploadcivname':
        # Game sends: action=uploadcivname&userid=<n>&passhash='<h>'&civname='<name>'
        # No response-body check in the game (it cleans up and returns after sending).
        # We persist the civname so listcivnames returns it correctly next tick.
        userid  = params.get('userid', ['1'])[0]
        civname = params.get('civname', [''])[0].strip("'")
        if civname:
            civ = _civ_state.setdefault(userid, _default_civ())
            civ['civname'] = civname
            log(f'  -> uploadcivname: userid={userid} civname={civname!r} (persisted)')
        else:
            log(f'  -> uploadcivname: userid={userid} (no civname param, ignored)')
        return 200, 'text/plain', 'OK'

    # ── Coat of arms ─────────────────────────────────────────────────────────
    # Binary analysis: listcoa is parsed in parallel with listcivnames.
    # Format mirrors listcivnames: coaid#NEXT#DONE  (one coaid per line).
    # An empty response means no COA is registered → getcoa is never called
    # → the default COA (coaid=0) is never fetched → some UI elements may be
    # missing.  Return the player's coaid so the game can fetch it via getcoa.
    if action == 'listcoa':
        userid = params.get('userid', ['1'])[0]
        coaid  = _get_civ(userid)['coaid']
        body   = f'{coaid}#NEXT#DONE'
        log(f'  -> listcoa: userid={userid} coaid={coaid} ({len(body)} bytes)')
        return 200, 'text/plain', body

    if action == 'getcoa':
        # Return empty 1x1 PNG as placeholder
        import base64
        empty_png = base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='
        )
        return 200, 'image/png', empty_png

    if action == 'uploadcoa':
        return 200, 'text/plain', 'OK'

    # ── Save / load game (stub) ─────────────────────────────────────────────
    # Game state is now managed via external memory reading/writing, not save
    # blobs.  These stubs keep the game happy when it tries to save/load.
    if action == 'savegame':
        log(f'  -> savegame: accepted (stub, not persisted)')
        return 200, 'text/plain', 'DONE'

    if action == 'savegamelist':
        # Return valid empty list so game doesn't show error dialog
        return 200, 'text/plain', '0#SPC#TestBed Save 1#SPC#0#NEXT#DONE'

    if action == 'loadgame':
        gameid = params.get('gameid', ['0'])[0]
        log(f'  -> loadgame: gameid={gameid} (stub, returning empty)')
        return 200, 'text/plain', 'DONE#VER#000000#DATA#'

    # ── Governor settings ─────────────────────────────────────────────────────
    if action == 'savegov':
        govid    = params.get('govid',   ['0'])[0]
        govname  = params.get('govname', [''])[0].strip("'")
        data_str = params.get('data',    [''])[0]
        gov_path = os.path.join(os.path.dirname(__file__), f'save_gov_{govid}.dat')
        with open(gov_path, 'wb') as f:
            f.write(data_str.encode('latin-1'))
        log(f'  -> savegov: govid={govid} name={govname} {len(data_str)} bytes')
        # Same DONE check pattern as savegame (confirmed by binary analysis at 0x4a0c3f)
        return 200, 'text/plain', 'DONE'

    if action == 'govlist':
        return 200, 'text/plain', 'DONE'

    if action == 'loadgov':
        govid    = params.get('govid', ['0'])[0]
        gov_path = os.path.join(os.path.dirname(__file__), f'save_gov_{govid}.dat')
        if os.path.exists(gov_path):
            gov_data = open(gov_path, 'rb').read().decode('latin-1')
            # Governor load likely uses same DONE#VER#<6>DATA# format as loadgame
            return 200, 'text/plain', 'DONE#VER#000000#DATA#' + gov_data
        return 200, 'text/plain', 'DONE#VER#000000#DATA#'

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
        # ── Why the response format matters (binary analysis) ────────────────────
        # The game's entertestbedgalaxy handler (0x577c00+) consumes the response:
        #   • strstr(response, "OK|") must be non-NULL — else response string is shown
        #     as an error dialog and galaxy join fails.
        #   • 0x576230 dequeues entries from the global pending-response queue at
        #     0x8714b8 (vector of 32-byte entries placed there by the HTTP thread).
        #   • For each entry, it extracts bytes starting at position 9 via 0x5e3a80
        #     and compares them with the credential stored at [0x86f148] (derived from
        #     the TEBE token during pass-file loading).  A credential match increments
        #     the processed-player count.
        #   • If count > 0 → normal testbed init: 0x577160 calls 0x537bf0(slot) to
        #     insert galaxy-slot entries into the map at 0x857c7c; 0x56e7f0 fires;
        #     [0x86f1a0] = 1 (testbed flag set); TLS tree populated.
        #   • If count == 0 → fallback path at 0x577e26: reads [0x86f190] which
        #     0x576230 set to -1 on empty/error response → calls 0x57e7b0 → sets
        #     [0x86f1a0] = 1 WITHOUT populating the TLS tree.
        #   • loadgame (0x56d700) then checks [0x86f1a0]: if set → testbed path →
        #     calls 0x542850 → traverses (empty) TLS RB-tree → throws
        #     "invalid vector<T> argument" → "Failed to load Save-Game".
        #
        # ── Fix: binary patch to CosmicSupremacy_patched.exe ────────────────────
        # File offset 0x16ccfa (VA 0x56d8fa):
        #   BEFORE: E8 51 4F FD FF   call 0x542850  (testbed TLS-tree init)
        #   AFTER:  90 90 90 90 90   nop × 5
        #
        # The patch makes the testbed load-game path skip 0x542850 entirely and
        # proceed directly to 0x541240 (the standard save loader), which works
        # correctly regardless of TLS-tree state — matching the normal-mode path.
        # With this patch, 'OK|0' is sufficient: the galaxy join succeeds and
        # loadgame no longer throws.
        #
        # Without the binary patch, a correct server response would need to supply
        # credential bytes matching [0x86f148] at offset 9+ of each queue entry so
        # 0x576230 returns count > 0 — the full testbed session-setup protocol has
        # not yet been reversed.
        import base64 as _b64
        userid   = params.get('userid', ['?'])[0]
        pass_raw = params.get('pass', [''])[0]
        # The pass field = .csgalaxy raw bytes × 16 (one copy per player slot).
        one_len  = len(pass_raw) // 16
        one_b64  = pass_raw[:one_len]
        try:
            one_decoded = _b64.b64decode(one_b64).decode('utf-8', errors='replace')
        except Exception:
            one_decoded = '(decode error)'
        log(f'  -> entertestbedgalaxy: userid={userid}, pass={len(pass_raw)} chars = 16×{one_len}')
        log(f'     one-token decoded ({len(one_decoded)} chars): {repr(one_decoded[:120])}')
        return 200, 'text/plain', 'OK|0'

    # ── Unknown action: log prominently and return empty OK ──────────────────
    _log_unknown_action(action, params)
    return 200, 'text/plain', 'OK'


# ── Unknown-action highlighter ────────────────────────────────────────────────
def _log_unknown_action(action: str, params: dict):
    """
    Log an unrecognised action with a highly visible separator so it stands out
    in the console / log file when scanning for new server interactions.

    The separator line is a row of '!' characters — easy to grep for:
        grep '!!!' cs_server.log
    """
    sep = '!' * 60
    log(sep)
    log(f'  [NEW ACTION?]  action={action!r}')
    # Log any non-trivial parameters (skip userid / passhash noise)
    interesting = {k: v for k, v in params.items()
                   if k not in ('userid', 'passhash', 'action')}
    if interesting:
        for k, vs in interesting.items():
            v0 = vs[0] if isinstance(vs, list) else str(vs)
            log(f'    param {k!r} = {repr(v0[:120])}')
    log(f'  -> returning empty OK  (add handler in handle_action() if needed)')
    log(sep)



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
