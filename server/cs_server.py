"""
cs_server.py — Cosmic Supremacy local stub server
==================================================
Replaces the original cosmicsupremacy.com backend so the patched EXE can run
locally.  Keeps the game open and responsive to its HTTP protocol so that the
external memory-reader tools (ejbo_viewer.py) can inspect live game state.

Usage (Windows, run as Administrator OR use port > 1024 and set CSPORT):
    python cs_server.py

The patched EXE connects to 127.0.0.1:8888 for everything.

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

# Where runtime artifacts live: the log, captured save blobs, governor blobs, and
# the opt-in loadgame injection file.  Defaults to this file's own directory, so
# `python cs_server.py` from a checkout behaves exactly as it always has.  The
# release launcher overrides it because the frozen build imports this module out
# of a temporary extraction directory that Windows deletes when the app exits —
# saves written relative to __file__ there would vanish with it.
DATA_DIR = os.environ.get('CS_DATA_DIR') or os.path.dirname(os.path.abspath(__file__))
os.makedirs(DATA_DIR, exist_ok=True)
LOGFILE = os.path.join(DATA_DIR, 'cs_server.log')

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

# ── Save-blob persistence ─────────────────────────────────────────────────────
# Every savegame POST is written to server/saves/ verbatim (the base64 text
# exactly as it came off the wire, no decoding) plus a small .json sidecar with
# the other POST fields.  Nothing is ever overwritten: each save gets its own
# sequence number so a series of saves can be diffed against each other.
SAVE_DIR    = os.path.join(DATA_DIR, 'saves')
INJECT_BLOB = os.path.join(DATA_DIR, 'loadgame_blob.b64')


def _raw_data_field(raw_body: str) -> 'str | None':
    """
    Pull data= out of the unparsed POST body and percent-decode it.

    The request format is  ...&version=%d&data=%s  with data last, so everything
    after the first '&data=' is the field.

    Why not parse_qs: form decoding also turns '+' into a space, and base64 uses
    '+'.  Measured on a real 38,960-char capture, the client percent-encodes and
    emits '%2B' (748 of them) with no literal '+', so parse_qs would in fact have
    survived this client — but unquote is correct either way, since it decodes
    the escapes without touching a literal '+' should some path ever emit one.
    """
    marker = '&data='
    i = raw_body.find(marker)
    if i < 0:
        if not raw_body.startswith('data='):
            return None
        field = raw_body[5:]
    else:
        field = raw_body[i + len(marker):]
    return urllib.parse.unquote(field)


def _persist_save(gameid: str, gamename: str, turn: str, version: str,
                  data_str: str, raw_body: str = '') -> str:
    os.makedirs(SAVE_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    seq   = len([n for n in os.listdir(SAVE_DIR) if n.endswith('.b64')])
    base  = f'save_{seq:03d}_{stamp}_g{gameid}_t{turn}'
    path  = os.path.join(SAVE_DIR, base + '.b64')

    raw_data = _raw_data_field(raw_body) if raw_body else None
    payload  = raw_data if raw_data is not None else data_str
    with open(path, 'w', encoding='ascii', errors='replace') as f:
        f.write(payload)
    # the whole request body as well, so nothing is lost if the data= slice
    # above turns out to be wrong for some request shape
    with open(os.path.join(SAVE_DIR, base + '.body'), 'w',
              encoding='latin-1') as f:
        f.write(raw_body)
    import json as _json
    with open(os.path.join(SAVE_DIR, base + '.json'), 'w', encoding='utf-8') as f:
        _json.dump({'gameid': gameid, 'gamename': gamename, 'turn': turn,
                    'version': version,
                    'data_len_raw': len(raw_data) if raw_data is not None else None,
                    'data_len_parsed': len(data_str),
                    'used': 'raw' if raw_data is not None else 'parsed',
                    'received': stamp}, f, indent=2)
    return os.path.basename(path)


def _load_injection_blob() -> 'str | None':
    """Return the base64 payload to serve from loadgame, or None for the stub."""
    if not os.path.exists(INJECT_BLOB):
        return None
    with open(INJECT_BLOB, 'r', encoding='ascii') as f:
        return f.read().strip()


# ── Save slots ────────────────────────────────────────────────────────────────
# The capture files above are an append-only forensic record: one per POST,
# never overwritten, so a series of saves can be diffed. They are the wrong
# thing to answer savegamelist with, because the client thinks in *slots* — a
# slot is saved over repeatedly and must appear once, under the name the player
# typed. The index maps slot -> the most recent capture for that slot, which
# keeps both properties: nothing is destroyed, and the list is accurate.
SAVE_INDEX = os.path.join(SAVE_DIR, 'index.json')


def _read_index() -> dict:
    try:
        import json as _json
        with open(SAVE_INDEX, 'r', encoding='utf-8') as f:
            idx = _json.load(f)
        return idx if isinstance(idx, dict) else {}
    except (OSError, ValueError):
        # A corrupt or absent index must not take savegamelist down with it:
        # an empty list is a valid response, a 500 is not.
        return {}


def _write_index(idx: dict):
    import json as _json
    os.makedirs(SAVE_DIR, exist_ok=True)
    tmp = SAVE_INDEX + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        _json.dump(idx, f, indent=2, sort_keys=True)
    os.replace(tmp, SAVE_INDEX)      # atomic; a crash mid-write keeps the old one


def _allocate_gameid(idx: dict) -> int:
    """
    Lowest unused slot number, counting from 1.

    gameid=-1 is the client's "give me a new slot" sentinel, and it treats
    negative IDs from savegamelist as invalid — so a save left under -1 is
    stored but unloadable, which is precisely the reported symptom of a save
    that appears to work and then is not in the list.
    """
    used = set()
    for k in idx:
        try:
            used.add(int(k))
        except ValueError:
            continue
    n = 1
    while n in used:
        n += 1
    return n


def _clean_field(text: str) -> str:
    """
    Strip the protocol's own delimiters out of a player-supplied name.

    The client splits list responses on #SPC# and #NEXT#, so a save called
    "a#NEXT#b" would arrive back as two malformed records. Dropping the '#'
    is enough to make any name safe, since both delimiters require it.
    """
    return text.replace('#', '').strip()


def handle_action(action: str, params: dict, raw_body: str = '') -> tuple[int, str, str]:
    """
    Returns (http_status, content_type, body).
    Add real response formats here as you discover them from cs_server.log.

    raw_body is the undecoded POST body; savegame needs it because form
    decoding mangles base64 (see _raw_data_field).
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

    # ── Save / load game ────────────────────────────────────────────────────
    # savegame now PERSISTS the raw data= field so the blob format (ROUT in
    # particular) can be reverse engineered.  The response is unchanged: the
    # client does strncmp(response, "DONE", 4) at 0x0048b350 and puts up
    # "Failed to save the Save-Game" for anything else.
    if action == 'savegame':
        raw_id   = params.get('gameid',   ['-1'])[0]
        gamename = _clean_field(params.get('gamename', [''])[0].strip("'"))
        turn     = params.get('turn',     ['?'])[0]
        version  = params.get('version',  ['?'])[0]
        data_str = params.get('data',     [''])[0]

        idx = _read_index()
        try:
            gameid = int(raw_id)
        except ValueError:
            gameid = -1
        if gameid < 0:
            gameid = _allocate_gameid(idx)
            log(f'  -> savegame: client sent gameid={raw_id} (new-slot sentinel), '
                f'allocated slot {gameid}')

        path = _persist_save(str(gameid), gamename, turn, version, data_str,
                             raw_body)
        idx[str(gameid)] = {
            'name':    gamename or f'Save {gameid}',
            'turn':    turn,
            'version': version,
            'file':    path,
            'saved':   datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        _write_index(idx)
        log(f'  -> savegame: slot {gameid} name={gamename!r} turn={turn} '
            f'version={version} data={len(data_str)} chars -> {path}')
        return 200, 'text/plain', 'DONE'

    if action == 'savegamelist':
        # <gameid>#SPC#<name>#SPC#<turn>#NEXT#...#NEXT#DONE
        # A bare DONE is a valid empty list; an empty body is not, and puts up
        # "Failed to retrieve list of saved games".
        idx = _read_index()
        if not idx:
            log('  -> savegamelist: no saves yet (empty list)')
            return 200, 'text/plain', 'DONE'
        records = []
        for gid in sorted(idx, key=lambda k: int(k) if k.lstrip('-').isdigit() else 0):
            entry = idx[gid]
            records.append(f'{gid}#SPC#{entry.get("name", "Save " + gid)}'
                           f'#SPC#{entry.get("turn", "0")}')
        body = '#NEXT#'.join(records) + '#NEXT#DONE'
        log(f'  -> savegamelist: {len(records)} save(s): '
            f'{", ".join(r.split("#SPC#")[1] for r in records)}')
        return 200, 'text/plain', body

    if action == 'loadgame':
        # Opt-in injection: if server/loadgame_blob.b64 exists, its contents are
        # served as the DATA payload.  Absent that file the behaviour is the
        # original stub (empty blob → client generates its own galaxy), so a
        # session that is not testing blob injection is unaffected.
        gameid = params.get('gameid', ['0'])[0]

        # The injection file stays the highest priority: it is an explicit
        # opt-in for blob-format work, and a session testing it should not have
        # a real save served instead.
        blob = _load_injection_blob()
        if blob is not None:
            log(f'  -> loadgame: gameid={gameid} serving {INJECT_BLOB} '
                f'({len(blob)} chars)')
            return 200, 'text/plain', 'DONE#VER#000000#DATA#' + blob

        entry = _read_index().get(str(gameid))
        if entry:
            path = os.path.join(SAVE_DIR, entry['file'])
            try:
                with open(path, 'r', encoding='ascii', errors='replace') as f:
                    blob = f.read().strip()
                log(f'  -> loadgame: slot {gameid} {entry.get("name")!r} '
                    f'turn {entry.get("turn")} from {entry["file"]} '
                    f'({len(blob)} chars)')
                return 200, 'text/plain', 'DONE#VER#000000#DATA#' + blob
            except OSError as exc:
                # Indexed but unreadable. Returning the empty blob lets the
                # client build its own galaxy rather than hang on a load.
                log(f'  !! loadgame: slot {gameid} indexed as {entry["file"]} '
                    f'but unreadable: {exc}')

        log(f'  -> loadgame: gameid={gameid} not in the save index, returning empty')
        return 200, 'text/plain', 'DONE#VER#000000#DATA#'

    # ── Governor settings ─────────────────────────────────────────────────────
    if action == 'savegov':
        govid    = params.get('govid',   ['0'])[0]
        govname  = params.get('govname', [''])[0].strip("'")
        data_str = params.get('data',    [''])[0]
        gov_path = os.path.join(DATA_DIR, f'save_gov_{govid}.dat')
        with open(gov_path, 'wb') as f:
            f.write(data_str.encode('latin-1'))
        log(f'  -> savegov: govid={govid} name={govname} {len(data_str)} bytes')
        # Same DONE check pattern as savegame (confirmed by binary analysis at 0x4a0c3f)
        return 200, 'text/plain', 'DONE'

    if action == 'govlist':
        return 200, 'text/plain', 'DONE'

    if action == 'loadgov':
        govid    = params.get('govid', ['0'])[0]
        gov_path = os.path.join(DATA_DIR, f'save_gov_{govid}.dat')
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
    # A windowed (console-less) frozen build has sys.stdout == None, and a bare
    # print() there raises inside the serving thread and kills the request.  The
    # file is the log that matters; the console is a convenience.
    if sys.stdout is not None:
        try:
            print(line)
        except (OSError, ValueError, AttributeError):
            pass
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
        # Log body in 400-char chunks so nothing is lost (params can be large
        # blobs).  Save blobs are the exception: they run to hundreds of KB and
        # are written to server/saves/ in full, so the log only keeps a head.
        CHUNK = 400
        LOG_CAP = 4000 if action in ('savegame', 'savegov') else len(body)
        shown = body[:LOG_CAP]
        if len(shown) <= CHUNK:
            log(f'  body: {shown}')
        else:
            for ci, start in enumerate(range(0, len(shown), CHUNK)):
                tag = 'body' if ci == 0 else 'body+'
                log(f'  {tag}: {shown[start:start+CHUNK]}')
        if len(shown) < len(body):
            log(f'  body… ({len(body) - len(shown)} more chars not logged)')

        status, ctype, resp_body = handle_action(action, merged_params, body)

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
            # The galaxy pass files live with the client, not the server, so look
            # in CS_GALAXY_DIR first (the launcher points this at the release's
            # galaxies folder) and fall back to the data dir.
            resp_bytes = b''
            for base in (os.environ.get('CS_GALAXY_DIR'), DATA_DIR):
                if not base:
                    continue
                galaxy_path = os.path.join(base, 'DemoGalaxy_local.csgalaxy')
                if os.path.exists(galaxy_path):
                    resp_bytes = open(galaxy_path, 'rb').read()
                    break
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
