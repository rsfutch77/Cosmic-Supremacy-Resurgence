"""
test_outages.py: an outage is named, and orders are kept through it
=====================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_outages.py

`turn_store.outage_of` names what a failed store call ran into: this
machine's network, the galaxy's server, or the file storage a turn is fetched
from. `HttpTurnStore` records the host a request was at when it failed, so a
redirect to Storage is told apart from the relay.

When the final send of a turn fails on an outage, `follow` keeps the capture
and sends it again until it lands or the turn closes, since the relay takes
orders until the turn changes. A refusal is not retried.

Network checks use a closed local port and a `.invalid` name, so they reach
nothing outside this machine. The turn loop runs on `test_turn_cadence`'s fake
clock, client and store.
"""
import os
import socket
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (HERE, os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools')):
    if d not in sys.path:
        sys.path.insert(0, d)

import test_turn_cadence as cadence                             # noqa: E402
import turn_store                                               # noqa: E402
from turn_store import (HttpTurnStore, OUTAGE_NETWORK,          # noqa: E402
                        OUTAGE_SERVICE, OUTAGE_STORAGE, outage_of)

PASS, FAIL = cadence.PASS, cadence.FAIL
check = cadence.check


def url_error(reason, host=None):
    exc = urllib.error.URLError(reason)
    if host:
        exc.cs_host = host
    return exc


def http_error(code, host=None):
    exc = urllib.error.HTTPError('https://x/', code, 'msg', {}, None)
    if host:
        exc.cs_host = host
    return exc


def test_names():
    print('what each failure is called')
    check('a failed name lookup is the network',
          outage_of(url_error(socket.gaierror(11001, 'getaddrinfo failed'))),
          OUTAGE_NETWORK)
    check('so is a timeout',
          outage_of(url_error(TimeoutError('timed out'))), OUTAGE_NETWORK)
    check('and a read that timed out after connecting',
          outage_of(TimeoutError('timed out')), OUTAGE_NETWORK)
    unreachable = OSError(10051, 'unreachable network')
    unreachable.winerror = 10051
    check('and an unreachable network',
          outage_of(url_error(unreachable)), OUTAGE_NETWORK)
    check('a refused connection is the galaxy server',
          outage_of(url_error(ConnectionRefusedError(10061, 'refused'))),
          OUTAGE_SERVICE)
    check('as is an HTTP 503', outage_of(http_error(503)), OUTAGE_SERVICE)
    check('and a 429', outage_of(http_error(429)), OUTAGE_SERVICE)
    check('a refused connection at Storage is Storage',
          outage_of(url_error(ConnectionResetError(10054, 'reset'),
                              host='storage.googleapis.com')), OUTAGE_STORAGE)
    check('as is a 503 from Storage',
          outage_of(http_error(503, host='storage.googleapis.com')),
          OUTAGE_STORAGE)
    check('a 409 is a refusal, not an outage', outage_of(http_error(409)),
          None)
    check('nor is a 403', outage_of(http_error(403)), None)
    check('nor anything that is not a connection',
          outage_of(RuntimeError('did not land')), None)


def test_real_connections():
    print('the store says where a request failed')
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    store = HttpTurnStore(f'http://127.0.0.1:{port}/g', timeout=5)
    try:
        store.current()
        got = None
    except OSError as exc:
        got = exc
    check('nothing listening is the galaxy server', outage_of(got),
          OUTAGE_SERVICE)
    check('and the store recorded the host', getattr(got, 'cs_host', None),
          '127.0.0.1')
    store = HttpTurnStore('http://cs-resurgence-test.invalid/g', timeout=5)
    try:
        store.current()
        got = None
    except OSError as exc:
        got = exc
    check('a name that does not resolve is the network', outage_of(got),
          OUTAGE_NETWORK)

    print('a redirect to Storage is remembered')
    handler = turn_store._drop_auth_on_redirect('https://relay.example')()
    req = urllib.request.Request('https://relay.example/g/turns/3')
    signed = 'https://storage.googleapis.com/bucket/turn?sig=1'
    new = handler.redirect_request(req, None, 302, 'Found', {}, signed)
    check('the first request knows where it was sent on to',
          getattr(req, 'cs_last_url', None), signed)
    again = handler.redirect_request(new, None, 302, 'Found', {},
                                     signed + '&b=2')
    check('and a second redirect updates the first request',
          (getattr(req, 'cs_last_url', None), again.cs_first is req),
          (signed + '&b=2', True))


class Outage(cadence.Store):
    """A store whose submit fails with `error` from `start` to `end`, in
    seconds from the start of the turn."""

    window = (None, None)
    error = None

    def submit(self, civ, turn, blob):
        start, end = self.window
        since = self.clock.now() - (self.deadline - self.seconds)
        if self.turn != turn:
            raise http_error(409)
        if start is not None and start <= since < end:
            raise Outage.error
        super().submit(civ, turn, blob)


def run(window, error, edits_at=(30, 580), **kw):
    """`cadence.run_turn` against an `Outage` store. The edit at 580 s comes
    after the interim upload, so the deadline has something new to send."""
    saved = cadence.Store
    Outage.window, Outage.error = window, error
    cadence.Store = Outage
    try:
        return cadence.run_turn(edits_at=edits_at, **kw)
    finally:
        cadence.Store = saved


def kinds(r):
    return [k for _t, k, _f in r.states
            if k in ('retrying', 'submitted', 'lost', 'waiting')]


def test_retried():
    print('a final send through an outage')
    offline = url_error(socket.gaierror(11001, 'getaddrinfo failed'))
    r = run((590, 640), offline, seconds=600.0, next_turn_at=900)
    check('the failed send says it is retrying, then lands',
          [k for k in kinds(r) if k != 'submitted'][:1], ['retrying'])
    retry = [f for _t, k, f in r.states if k == 'retrying']
    check('and names the network', retry[0].get('outage') if retry else None,
          OUTAGE_NETWORK)
    check('the orders reach the store once the network is back',
          r.store.subs.get(('DemoPlayer', 7)), r.final)
    check('and the turn is not called lost',
          [k for k in kinds(r) if k == 'lost'], [])

    r = run((590, 2000), offline, seconds=600.0, next_turn_at=900)
    check('still down when the turn closes, the turn is lost',
          kinds(r)[-1:], ['lost'])
    lost = [f for _t, k, f in r.states if k == 'lost']
    check('and says the turn closed first',
          'closed before' in (lost[0].get('error') or '') if lost else False,
          True)

    r = run((590, 2000), http_error(403), seconds=600.0, next_turn_at=900)
    check('a refusal is lost at once and not retried',
          ('retrying' in kinds(r), kinds(r)[-1:]), (False, ['lost']))

    print('the game closed early while offline')
    r = run((0, 200), offline, seconds=600.0, close_at=100,
            next_turn_at=900)
    check('the orders are sent once the network is back',
          r.store.subs.get(('DemoPlayer', 7)), lambda b: b is not None
          and b.endswith(b'ORDER'))
    check('and nothing is lost', [k for k in kinds(r) if k == 'lost'], [])


if __name__ == '__main__':
    test_names()
    test_real_connections()
    test_retried()
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    for n in FAIL:
        print('  FAILED:', n)
    sys.exit(1 if FAIL else 0)
