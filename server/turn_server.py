"""
turn_server.py: serve a turn store over HTTP

    python turn_server.py server/galaxy_demo --port 8899
    python turn_server.py server/galaxy_demo --port 8899 --host 0.0.0.0

Puts a directory `TurnStore` behind a URL, so `open_store("http://host:8899")`
and `open_store("some/dir")` are interchangeable to every caller.

    GET  /state                     the turn number, deadline, roster
    GET  /turn/<n>                  that turn's blob, decompressed
    POST /turn/<n>?seconds=<s>      publish it and restart the clock
    POST /start?civ=A&civ=B&...     publish the first turn
    GET  /submissions/<n>           which civs have handed something back
    GET  /submission/<n>/<civ>      one submission, decompressed
    POST /submission/<n>/<civ>      hand one back
    GET  /upload/submission/<n>/<civ>   where to put one, and how
    POST /commit/submission/<n>/<civ>   check what was put there
    GET  /archive/<n>               what the referee recorded for that turn
    POST /archive/<n>               record it
    GET  /note/<n>/<civ>            what the referee refused from that civ
    POST /note/<n>/<civ>            leave that note
    GET  /joins                     the seats strangers have asked for
    POST /join                      ask for one
    GET  /join/<key>                the one request filed under that key
    GET  /join/<key>/answer         what was decided about it
    POST /join/<key>                file that decision and consume the request

Blobs move as raw decompressed bytes.

Routes must match the relay in `functions/`. The upload ticket here asks for
the raw blob and names a commit route that checks what was stored; the relay's
asks for base64 and names no commit. `HttpTurnStore.submit` follows either.

There is no authentication: anyone who can reach the port can publish a turn or
submit as any civ. An `Authorization` header is accepted and not verified;
`functions/relay.py` verifies tokens. `GET /joins` has no relay counterpart,
since the relay answers a caller only about its own request.
"""
import argparse
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'dev_tools'))

import turn_store
from turn_store import TurnStore, check_save

STORE = None
VERBOSE = True

MAX_SUBMISSION_BYTES = 1024 * 1024   # the relay's submission body ceiling, carried in the ticket


def log(msg):
    if VERBOSE:
        print(msg, flush=True)


class Handler(BaseHTTPRequestHandler):
    server_version = 'CosmicTurnStore/1'

    def log_message(self, fmt, *args):
        pass                                    # replaced by log()

    def _send(self, code, ctype, body: bytes):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, 'application/json',
                   json.dumps(obj).encode('utf-8'))

    def _blob(self, data: bytes):
        self._send(200, 'application/octet-stream', data)

    def _fail(self, code, why):
        log(f'  -> {code} {why}')
        self._json({'error': why}, code)

    def _body(self) -> bytes:
        n = int(self.headers.get('Content-Length') or 0)
        return self.rfile.read(n) if n else b''

    def _bearer(self):
        """The caller's ID token, unverified, or None."""
        head = self.headers.get('Authorization') or ''
        if head.lower().startswith('bearer '):
            return head[7:].strip() or None
        return None

    def _who(self) -> str:
        """A log suffix saying whether an identity was offered."""
        return ', identity offered and not verified' if self._bearer() else ''

    def _parts(self):
        url = urllib.parse.urlparse(self.path)
        parts = [p for p in url.path.split('/') if p]
        return parts, urllib.parse.parse_qs(url.query)

    def do_GET(self):
        parts, _q = self._parts()
        try:
            if parts == ['state']:
                if not STORE.exists():
                    return self._fail(404, 'no galaxy in this store')
                return self._json(STORE.state())
            if len(parts) == 2 and parts[0] == 'turn':
                n = int(parts[1])
                if not STORE.has_turn(n):
                    return self._fail(404, f'no turn {n}')
                return self._blob(STORE.turn_blob(n))
            if len(parts) == 2 and parts[0] == 'submissions':
                # Names only, without reading any submission.
                return self._json(STORE.submitted_civs(int(parts[1])))
            if len(parts) == 3 and parts[0] == 'submission':
                n, civ = int(parts[1]), parts[2]
                blob = STORE.submission(civ, n)
                if blob is None:
                    return self._fail(404, f'{civ} has not submitted for {n}')
                return self._blob(blob)
            if len(parts) == 2 and parts[0] == 'archive':
                rec = STORE.archive_record(int(parts[1]))
                if rec is None:
                    return self._fail(404, f'no archive for turn {parts[1]}')
                return self._json(rec)
            if len(parts) == 4 and parts[:2] == ['upload', 'submission']:
                return self._json(upload_ticket(int(parts[2]), parts[3]))
            if parts == ['joins']:
                return self._json(STORE.join_requests(log=log))
            if len(parts) == 2 and parts[0] == 'join':
                req = STORE.join_request(urllib.parse.unquote(parts[1]))
                if req is None:
                    return self._fail(404, f'no join request is waiting under '
                                           f'{parts[1]}')
                return self._json(req)
            if len(parts) == 3 and parts[0] == 'join' \
                    and parts[2] == 'answer':
                answer = STORE.join_answer(urllib.parse.unquote(parts[1]))
                if answer is None:
                    return self._fail(404, f'nothing has been decided about '
                                           f'{parts[1]}')
                return self._json(answer)
            if len(parts) == 3 and parts[0] == 'note':
                lines = STORE.note(parts[2], int(parts[1]))
                if not lines:
                    return self._fail(404, f'no note for {parts[2]} on turn '
                                           f'{parts[1]}')
                return self._blob('\n'.join(lines).encode('utf-8'))
        except ValueError:
            return self._fail(400, 'a turn number has to be an integer')
        except Exception as exc:                            # noqa: BLE001
            return self._fail(500, f'{type(exc).__name__}: {exc}')
        self._fail(404, f'no route for {self.path}')

    def do_POST(self):
        parts, q = self._parts()
        body = self._body()
        try:
            if parts == ['start']:
                civs = q.get('civ', [])
                seconds = int(q.get('seconds', ['3600'])[0])
                turn = STORE.start(body, civs=civs, turn_seconds=seconds)
                log(f'  started turn {turn}, {seconds}s, roster {civs}')
                return self._json({'turn': turn})
            if len(parts) == 2 and parts[0] == 'turn':
                n = int(parts[1])
                seconds = q.get('seconds')
                STORE.publish(n, body,
                              turn_seconds=int(seconds[0]) if seconds else None)
                log(f'  published turn {n}, {len(body):,} bytes')
                return self._json({'turn': n})
            if len(parts) == 3 and parts[0] == 'submission':
                n, civ = int(parts[1]), parts[2]
                # Checked before it is stored, as the relay does, so a bad body
                # leaves the previous submission standing.
                check_save(body, n)
                STORE.submit(civ, n, body)
                log(f'  {civ} submitted for turn {n}, {len(body):,} bytes'
                    f'{self._who()}')
                return self._json({'turn': n, 'civ': civ})
            if len(parts) == 2 and parts[0] == 'archive':
                STORE.archive(int(parts[1]), json.loads(body or b'{}'))
                log(f'  archived turn {parts[1]}')
                return self._json({'turn': int(parts[1])})
            if len(parts) == 4 and parts[:2] == ['commit', 'submission']:
                n, civ = int(parts[2]), parts[3]
                blob = STORE.submission(civ, n)
                if blob is None:
                    return self._fail(404, f'{civ} has not submitted for {n}')
                try:
                    check_save(blob, n)
                except ValueError:
                    # Reachable only for a file written by something other than
                    # the POST above. It is removed so the referee never reads it.
                    os.remove(STORE.submission_path(civ, n))
                    raise
                log(f'  {civ} committed turn {n}, {len(blob):,} bytes'
                    f'{self._who()}')
                return self._json({'turn': n, 'civ': civ, 'bytes': len(blob)})
            if len(parts) == 3 and parts[0] == 'note':
                n, civ = int(parts[1]), parts[2]
                lines = [ln for ln in body.decode('utf-8').split('\n') if ln]
                STORE.put_note(civ, n, lines)
                log(f'  noted {len(lines)} refusal(s) for {civ} on turn {n}')
                return self._json({'turn': n, 'civ': civ})
            if parts == ['join']:
                req = json.loads(body or b'{}')
                where = STORE.request_join(req)
                log(f'  {req.get("name")} asked to join{self._who()}')
                return self._json({'where': where,
                                   'key': turn_store.join_key(req)})
            if len(parts) == 2 and parts[0] == 'join':
                # The key is unquoted, since `HttpTurnStore` quotes it whole.
                # The submission routes must not unquote: that would rename
                # every stored submission.
                key = urllib.parse.unquote(parts[1])
                STORE.answer_join(key, json.loads(body or b'{}'))
                log(f'  answered the join request filed under {key}')
                return self._json({'key': key})
        except ValueError as exc:
            return self._fail(400, str(exc))
        except Exception as exc:                            # noqa: BLE001
            return self._fail(500, f'{type(exc).__name__}: {exc}')
        self._fail(404, f'no route for {self.path}')


def upload_ticket(turn: int, civ: str) -> dict:
    """Where this service wants a submission put.

    The same shape as the relay's ticket: `url`, `method`, `encoding` (`raw`
    here, `b64` on the relay), `commit` and `max_bytes`.
    """
    quoted = urllib.parse.quote(civ)
    return {
        'url': f'/submission/{turn}/{quoted}',
        'method': 'POST',
        'encoding': 'raw',
        'commit': f'/commit/submission/{turn}/{quoted}',
        'max_bytes': MAX_SUBMISSION_BYTES,
    }


def serve(root: str, host: str = '127.0.0.1', port: int = 8899):
    global STORE
    STORE = TurnStore(root)
    httpd = ThreadingHTTPServer((host, port), Handler)
    log(f'turn store {STORE.root}')
    log(f'listening on http://{host}:{port}')
    if STORE.exists():
        turn, _d = STORE.current()
        log(f'  turn {turn}, {STORE.seconds_left():.0f}s left, '
            f'roster {STORE.civs()}')
    else:
        log('  empty; POST /start to publish a first turn')
    return httpd


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('root', help='the turn store directory to serve')
    ap.add_argument('--host', default='127.0.0.1',
                    help='0.0.0.0 to let another machine reach it')
    ap.add_argument('--port', type=int, default=8899)
    a = ap.parse_args()
    httpd = serve(a.root, a.host, a.port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log('stopped')
    return 0


if __name__ == '__main__':
    sys.exit(main())
