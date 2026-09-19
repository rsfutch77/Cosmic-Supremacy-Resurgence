"""
turn_server.py , serve a turn store over HTTP
==============================================
    python turn_server.py server/galaxy_demo --port 8899
    python turn_server.py server/galaxy_demo --port 8899 --host 0.0.0.0

A `TurnStore` is a directory, which is enough for one machine and for a shared
folder between two. It is not enough for a referee on another host, and it will
never be enough for a hosted galaxy. This puts the same interface behind a URL,
so `open_store("http://host:8899")` and `open_store("some/dir")` are
interchangeable to every caller.

That is the whole point of the exercise: the seam. A Firebase adapter replaces
this service and `HttpTurnStore` keeps working, or replaces `HttpTurnStore` and
this service is no longer needed. Nothing above the store changes either way.

    GET  /state                     the turn number, deadline, roster
    GET  /turn/<n>                  that turn's blob, decompressed
    POST /turn/<n>?seconds=<s>      publish it and restart the clock
    POST /start?civ=A&civ=B&...     publish the first turn
    GET  /submissions/<n>           which civs have handed something back
    GET  /submission/<n>/<civ>      one submission, decompressed
    POST /submission/<n>/<civ>      hand one back
    GET  /archive/<n>               what the referee recorded for that turn
    POST /archive/<n>               record it

Blobs move as raw decompressed bytes rather than the base64 the directory holds,
because the encoding is the directory's business and a caller that has to know
it is a caller the Firebase adapter would break.

**There is no authentication.** Anyone who can reach the port can publish a turn
or submit as any civ. That is deliberate for a closed beta among people who know
each other, and it is the first thing that has to change before a galaxy is open
to strangers. The ownership rules in `merge_orders.py` still hold, so the worst
a stranger can do through this door is submit nonsense as someone else, not
acquire their ships.
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

from turn_store import TurnStore

STORE = None
VERBOSE = True


def log(msg):
    if VERBOSE:
        print(msg, flush=True)


class Handler(BaseHTTPRequestHandler):
    server_version = 'CosmicTurnStore/1'

    def log_message(self, fmt, *args):
        pass                                    # we log what matters ourselves

    # ── replies ──────────────────────────────────────────────────────────────
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

    # ── routing ──────────────────────────────────────────────────────────────
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
                return self._json(sorted(STORE.submissions(int(parts[1]))))
            if len(parts) == 3 and parts[0] == 'submission':
                n, civ = int(parts[1]), parts[2]
                subs = STORE.submissions(n)
                if civ not in subs:
                    return self._fail(404, f'{civ} has not submitted for {n}')
                return self._blob(subs[civ])
            if len(parts) == 2 and parts[0] == 'archive':
                rec = STORE.archive_record(int(parts[1]))
                if rec is None:
                    return self._fail(404, f'no archive for turn {parts[1]}')
                return self._json(rec)
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
                STORE.submit(civ, n, body)
                log(f'  {civ} submitted for turn {n}, {len(body):,} bytes')
                return self._json({'turn': n, 'civ': civ})
            if len(parts) == 2 and parts[0] == 'archive':
                STORE.archive(int(parts[1]), json.loads(body or b'{}'))
                log(f'  archived turn {parts[1]}')
                return self._json({'turn': int(parts[1])})
        except ValueError as exc:
            return self._fail(400, str(exc))
        except Exception as exc:                            # noqa: BLE001
            return self._fail(500, f'{type(exc).__name__}: {exc}')
        self._fail(404, f'no route for {self.path}')


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
