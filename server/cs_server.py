"""
cs_server.py: Cosmic Supremacy local stub server.

Answers the game client's HTTP protocol in place of the original
cosmicsupremacy.com backend. The patched EXE connects to 127.0.0.1:8888.

Usage (set CSPORT to change the port):
    python cs_server.py

Protocol:
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

# Directory for the log, save captures, governor blobs and the loadgame injection
# file. Defaults to this file's directory. A frozen build must set it, because its
# module directory is a temporary extraction that is deleted on exit.
DATA_DIR = os.environ.get('CS_DATA_DIR') or os.path.dirname(os.path.abspath(__file__))
os.makedirs(DATA_DIR, exist_ok=True)
LOGFILE = os.path.join(DATA_DIR, 'cs_server.log')

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

DEMO_USERID   = 1
DEMO_PASSHASH = 'abcdef'   # from DemoGalaxy_local.csgalaxy token
DEMO_GALAXY_ID = 0

# Civ name and coat of arms per user, { userid_str: {'civname': str, 'coaid': str} },
# kept in DATA_DIR so a name chosen in one session is there in the next.
CIV_FILE = os.path.join(DATA_DIR, 'civs.json')
TUTORIAL_FILE = os.path.join(DATA_DIR, 'tutorial.json')
_civ_state: 'dict | None' = None

def _default_civ() -> dict:
    return {'civname': 'DemoEmpire', 'coaid': '0'}

def _civs() -> dict:
    global _civ_state
    if _civ_state is None:
        try:
            import json as _json
            with open(CIV_FILE, 'r', encoding='utf-8') as f:
                got = _json.load(f)
            _civ_state = got if isinstance(got, dict) else {}
        except (OSError, ValueError):
            # Absent or corrupt reads as no civs, which serves the default.
            _civ_state = {}
    return _civ_state

def _get_civ(userid: str) -> dict:
    return dict(_default_civ(), **_civs().get(userid, {}))

def _set_civ(userid: str, **fields):
    import json as _json
    civs = _civs()
    civs[userid] = dict(_get_civ(userid), **fields)
    tmp = CIV_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        _json.dump(civs, f, indent=2, sort_keys=True)
    os.replace(tmp, CIV_FILE)

# Every savegame POST is written to saves/ verbatim (base64 as sent) with a .json
# sidecar of the other fields. Each save gets its own sequence number and none is
# overwritten.
SAVE_DIR    = os.path.join(DATA_DIR, 'saves')
INJECT_BLOB = os.path.join(DATA_DIR, 'loadgame_blob.b64')


def _raw_data_field(raw_body: str) -> 'str | None':
    """
    Pull data= out of the unparsed POST body and percent-decode it.

    The request format is  ...&version=%d&data=%s  with data last, so everything
    after the first '&data=' is the field. unquote leaves a literal '+' intact,
    which base64 needs.
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
    # The whole request body is kept as well.
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


# The client lists saves by slot, each once under the name the player typed. The
# index maps slot -> the most recent capture for that slot.
SAVE_INDEX = os.path.join(SAVE_DIR, 'index.json')


def _read_index() -> dict:
    try:
        import json as _json
        with open(SAVE_INDEX, 'r', encoding='utf-8') as f:
            idx = _json.load(f)
        return idx if isinstance(idx, dict) else {}
    except (OSError, ValueError):
        # A corrupt or absent index reads as empty, which is a valid list.
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

    gameid=-1 is the client's new-slot sentinel, and the client treats negative
    IDs from savegamelist as invalid, so a save must not be stored under -1.
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


# The governor library: one entry per saved governor, kept across galaxies.
# The client sends govid -1 for a new name and an existing id to overwrite, and
# never reads an id back, so the server numbers them.
GOV_DIR   = os.path.join(DATA_DIR, 'governors')
GOV_INDEX = os.path.join(GOV_DIR, 'index.json')
GOV_VERSION = 2       # what the client sends; loadgov decompresses only above 1


def _read_gov_index() -> dict:
    try:
        import json as _json
        with open(GOV_INDEX, 'r', encoding='utf-8') as f:
            idx = _json.load(f)
        return idx if isinstance(idx, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_gov_index(idx: dict):
    import json as _json
    os.makedirs(GOV_DIR, exist_ok=True)
    tmp = GOV_INDEX + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        _json.dump(idx, f, indent=2, sort_keys=True)
    os.replace(tmp, GOV_INDEX)


def _gov_id(params: dict) -> int:
    try:
        return int(params.get('govid', ['-1'])[0])
    except ValueError:
        return -1


def _governor_action(action: str, params: dict, raw_body: str):
    idx = _read_gov_index()

    # Reply: <id>#SPC#<name>#SPC#<extra line>...#NEXT#...DONE, or a bare DONE.
    # Extra fields show as grey lines under the name.
    if action == 'govlist':
        recs = [f"{k}#SPC#{v['name']}#SPC#Saved {v.get('saved', '')}"
                for k, v in sorted(idx.items(), key=lambda kv: int(kv[0]))]
        log(f'  -> govlist: {len(recs)} governor(s)')
        return 200, 'text/plain', '#NEXT#'.join(recs + ['DONE'])

    govid = _gov_id(params)

    # Request: govid, govname, version, data (base64 of the zlib'd Governor).
    if action == 'savegov':
        name = _clean_field(params.get('govname', [''])[0].strip("'"))
        raw = _raw_data_field(raw_body) if raw_body else None
        data = raw if raw is not None else params.get('data', [''])[0]
        try:
            version = int(params.get('version', [str(GOV_VERSION)])[0])
        except ValueError:
            version = GOV_VERSION
        if str(govid) not in idx:
            govid = _allocate_gameid(idx)
        os.makedirs(GOV_DIR, exist_ok=True)
        with open(os.path.join(GOV_DIR, f'{govid}.b64'), 'w', encoding='ascii',
                  errors='replace') as f:
            f.write(data)
        idx[str(govid)] = {'name': name or f'Governor {govid}', 'version': version,
                           'saved': datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}
        _write_gov_index(idx)
        log(f'  -> savegov: govid={govid} name={name!r} version={version} '
            f'{len(data)} chars')
        # The client checks for DONE (0x4a0c3f).
        return 200, 'text/plain', 'DONE'

    # Reply: DONE#VER#<6 digits>#DATA#<base64>. Anything else reads as a failure.
    if action == 'loadgov':
        entry = idx.get(str(govid))
        path = os.path.join(GOV_DIR, f'{govid}.b64')
        if entry is None or not os.path.exists(path):
            log(f'  -> loadgov: govid={govid} not in the library')
            return 200, 'text/plain', 'ERROR'
        with open(path, 'r', encoding='ascii', errors='replace') as f:
            data = f.read()
        log(f'  -> loadgov: govid={govid} {entry["name"]!r}')
        return 200, 'text/plain', (f'DONE#VER#{entry.get("version", GOV_VERSION):06d}'
                                   f'#DATA#{data}')

    # delgov: the client drops the entry from its list on DONE.
    idx.pop(str(govid), None)
    _write_gov_index(idx)
    try:
        os.remove(os.path.join(GOV_DIR, f'{govid}.b64'))
    except OSError:
        pass            # already gone, which is what was asked
    log(f'  -> delgov: govid={govid}')
    return 200, 'text/plain', 'DONE'


def _clean_field(text: str) -> str:
    """Drop '#' from a player-supplied name, so it cannot form the #SPC# or
    #NEXT# list delimiters."""
    return text.replace('#', '').strip()


def handle_action(action: str, params: dict, raw_body: str = '') -> tuple[int, str, str]:
    """
    Returns (http_status, content_type, body).

    raw_body is the undecoded POST body, which savegame reads (see _raw_data_field).
    """

    if action == 'testconnection':
        return 200, 'text/plain', 'READY'

    # Request: userid=<int>&pass=<password>. Any login is accepted and answered
    # with the passhash the game sends on later requests.
    if action == 'login':
        return 200, 'text/plain', DEMO_PASSHASH

    if action == 'getplayerfame':
        return 200, 'text/plain', '0'

    # Response: civname#SPC#coaid#NEXT#DONE. FUN_0x497f93 parses it with
    # 0x5e3de0 into 0x20-byte records; [entry+0x14] is the coaid. An empty coaid
    # leaves the civ unconfigured, and FUN_0x496830 ([esi+0x4988]) then shows the
    # "Customize Your Home World" popup every tick.
    if action == 'listcivnames':
        userid = params.get('userid', ['1'])[0]
        civ = _get_civ(userid)
        civname = civ['civname']
        coaid   = civ['coaid']
        body = f'{civname}#SPC#{coaid}#NEXT#DONE'
        log(f'  -> listcivnames: userid={userid} civname={civname!r} coaid={coaid} ({len(body)} bytes)')
        return 200, 'text/plain', body

    if action == 'uploadcivname':
        # Request: action=uploadcivname&userid=<n>&passhash='<h>'&civname='<name>'
        # The game does not check the response body.
        userid  = params.get('userid', ['1'])[0]
        civname = _clean_field(params.get('civname', [''])[0].strip("'"))
        if civname:
            try:
                _set_civ(userid, civname=civname)
                log(f'  -> uploadcivname: userid={userid} civname={civname!r} (persisted)')
            except OSError as e:
                # Still answered OK: the game does not check, and the name holds
                # for this session.
                _civs()[userid] = dict(_get_civ(userid), civname=civname)
                log(f'  !! uploadcivname: userid={userid} civname={civname!r} not written: {e}')
        else:
            log(f'  -> uploadcivname: userid={userid} (no civname param, ignored)')
        return 200, 'text/plain', 'OK'

    # Response: coaid#NEXT#DONE, one coaid per record. The game calls getcoa only
    # for coaids listed here.
    if action == 'listcoa':
        userid = params.get('userid', ['1'])[0]
        coaid  = _get_civ(userid)['coaid']
        body   = f'{coaid}#NEXT#DONE'
        log(f'  -> listcoa: userid={userid} coaid={coaid} ({len(body)} bytes)')
        return 200, 'text/plain', body

    if action == 'getcoa':
        # A 1x1 placeholder PNG.
        import base64
        empty_png = base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='
        )
        return 200, 'image/png', empty_png

    if action == 'uploadcoa':
        return 200, 'text/plain', 'OK'

    # The client does strncmp(response, "DONE", 4) at 0x0048b350 and shows
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
        # A bare DONE is an empty list. An empty body shows
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
        # Response: DONE#VER#000000#DATA#<base64>. INJECT_BLOB, when present, is
        # served ahead of any saved slot. An empty DATA makes the client generate
        # its own galaxy.
        gameid = params.get('gameid', ['0'])[0]

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
                # Indexed but unreadable: fall through to the empty blob.
                log(f'  !! loadgame: slot {gameid} indexed as {entry["file"]} '
                    f'but unreadable: {exc}')

        log(f'  -> loadgame: gameid={gameid} not in the save index, returning empty')
        return 200, 'text/plain', 'DONE#VER#000000#DATA#'

    if action in ('savegov', 'govlist', 'loadgov', 'delgov'):
        return _governor_action(action, params, raw_body)

    # Sent once, when the last Tutorial step is shown. The launcher reads the
    # file to mark the Tutorial completed.
    if action == 'passedtutorial':
        userid = params.get('userid', ['0'])[0]
        import json as _json
        try:
            tmp = TUTORIAL_FILE + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                _json.dump({'passed': True, 'userid': userid,
                            'at': datetime.datetime.now().isoformat(timespec='seconds')},
                           f, indent=2)
            os.replace(tmp, TUTORIAL_FILE)
            log(f'  -> passedtutorial: userid={userid} (recorded)')
        except OSError as e:
            log(f'  !! passedtutorial: userid={userid} not recorded: {e}')
        return 200, 'text/plain', 'OK'

    if action == 'entertestbedgalaxy':
        # Called when loading a TEBE-type galaxy pass file.
        # POST body: userid=<int>&pass=<16× base64-encoded credential tokens>
        # Each token decodes to "TEBE <server_ip> <playerid> <hexpass> <playername>",
        # one per player slot in the testbed galaxy.
        #
        # The client treats a response without "OK|" as an error. The patched exe
        # nops the testbed TLS-tree init in loadgame (file offset 0x16ccfa, call
        # 0x542850), so the standard save loader runs and 'OK|0' is enough to join.
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

    _log_unknown_action(action, params)
    return 200, 'text/plain', 'OK'


def _log_unknown_action(action: str, params: dict):
    """Log an unrecognised action between rows of '!' (grep '!!!' cs_server.log)."""
    sep = '!' * 60
    log(sep)
    log(f'  [NEW ACTION?]  action={action!r}')
    interesting = {k: v for k, v in params.items()
                   if k not in ('userid', 'passhash', 'action')}
    if interesting:
        for k, vs in interesting.items():
            v0 = vs[0] if isinstance(vs, list) else str(vs)
            log(f'    param {k!r} = {repr(v0[:120])}')
    log(f'  -> returning empty OK  (add handler in handle_action() if needed)')
    log(sep)



_log_fh = None

def log(msg: str):
    global _log_fh
    if _log_fh is None:
        _log_fh = open(LOGFILE, 'a', buffering=1, encoding='utf-8')
    ts = datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]
    line = f'[{ts}] {msg}'
    # A windowed frozen build has sys.stdout == None.
    if sys.stdout is not None:
        try:
            print(line)
        except (OSError, ValueError, AttributeError):
            pass
    _log_fh.write(line + '\n')


# A request is refused when it carries a foreign page's marks: a Host that is not
# a loopback name (DNS rebinding), an Origin that is not this server, or a
# Sec-Fetch-Site other than same-origin or none. The client and tools pass all three.
LOOPBACK_NAMES = frozenset({'localhost', '127.0.0.1', '[::1]'})

# A non-loopback CSHOST is the LAN setup: the Host check is off, the other checks apply.
_LAN = os.environ.get('CSHOST', '127.0.0.1') not in ('127.0.0.1', 'localhost', '::1')


def _host_name(value: str) -> str:
    """The name part of a Host header or an Origin's authority, lower-cased."""
    value = value.strip().lower()
    if value.startswith('['):
        return value[:value.find(']') + 1]
    return value.split(':', 1)[0]


def foreign_reason(headers) -> 'str | None':
    """Why a request comes from a foreign page, or None when it may be served."""
    host = headers.get('Host')
    if host is not None and not _LAN and _host_name(host) not in LOOPBACK_NAMES:
        return f'Host {host!r} is not a loopback name'
    origin = headers.get('Origin')
    if origin is not None:
        authority = urllib.parse.urlsplit(origin).netloc
        if _host_name(authority) not in LOOPBACK_NAMES:
            return f'Origin {origin!r} is not this server'
    site = headers.get('Sec-Fetch-Site')
    if site is not None and site.lower() not in ('same-origin', 'none'):
        return f'Sec-Fetch-Site {site!r}'
    return None


class CSHandler(http.server.BaseHTTPRequestHandler):
    server_version = 'CosmicSupremacy/1.0'
    protocol_version = 'HTTP/1.0'

    def log_message(self, fmt, *args):
        pass  # replaced by log()

    def _refused(self) -> bool:
        """Answer 403 to a request from a foreign page. True when it was refused."""
        reason = foreign_reason(self.headers)
        if reason is None:
            return False
        log(f'{self.command} {self.path} refused: {reason}')
        self._send(403, 'text/plain', b'FORBIDDEN')
        return True

    def do_POST(self):
        if self._refused():
            return
        length = int(self.headers.get('Content-Length', 0))
        body   = self.rfile.read(length).decode('latin-1') if length else ''
        params = urllib.parse.parse_qs(body, keep_blank_values=True)

        # action= is usually in the URL query string, sometimes in the body.
        url_qs = {}
        if '?' in self.path:
            url_qs = urllib.parse.parse_qs(self.path.split('?', 1)[1], keep_blank_values=True)
        action = (url_qs.get('action') or params.get('action') or ['<none>'])[0]
        # Body wins on collision.
        merged_params = {**url_qs, **params}

        log(f'POST {self.path}')
        log(f'  Content-Type={self.headers.get("Content-Type","")}  Content-Length={length}')
        log(f'  action={action}')
        # Body is logged in 400-char chunks. Save blobs are written to saves/ in
        # full, so only their head is logged.
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
        if self._refused():
            return
        log(f'GET {self.path}')

        path = self.path.split('?')[0].rstrip('/')

        if path in ('', '/index.html', '/index.htm'):
            resp_bytes = WEB_INDEX.encode('utf-8')
            self._send(200, 'text/html; charset=utf-8', resp_bytes)
            return

        if path == '/enter-demo':
            # DemoGalaxy_local.csgalaxy as a download, from CS_GALAXY_DIR (the
            # release's galaxies folder) or else DATA_DIR.
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


if __name__ == '__main__':
    # Binds loopback only, which avoids a Windows Firewall prompt. Set CSHOST to
    # serve a client on another machine.
    HOST = os.environ.get('CSHOST', '127.0.0.1')
    server = http.server.HTTPServer((HOST, PORT), CSHandler)
    log(f'Cosmic Supremacy stub server listening on {HOST}:{PORT}')
    log(f'All traffic logged to: {LOGFILE}')
    log(f'Start: double-click DemoGalaxy_local.csgalaxy (after starting patched EXE)')
    log('-' * 60)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log('Server stopped.')
