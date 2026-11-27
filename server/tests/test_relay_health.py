"""
test_relay_health.py: the relay's health route, which an uptime check polls
============================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_relay_health.py

No emulator: the galaxy listing is replaced with rows this test writes, so
only the route and the overdue rule run.
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'), os.path.join(ROOT, 'functions')):
    if d not in sys.path:
        sys.path.insert(0, d)

os.environ.setdefault('CS_RELAY_PROJECT', 'demo-cs-resurgence')

import relay                                                    # noqa: E402
import turn_store                                               # noqa: E402

PASS, FAIL = [], []
HOUR = 3600


def check(name, got, want):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def row(gid, past, length=2 * HOUR, **extra):
    """A listing row whose deadline was `past` seconds ago."""
    r = {'id': gid, 'turn': 12, 'deadline': time.time() - past,
         'turn_seconds': length}
    r.update(extra)
    return r


ROWS = []
relay.listing = lambda: ROWS


def ask(path, method='GET'):
    code, _headers, body = relay.handle(method, path, {}, b'')
    return code, json.loads(body)


def test_rule():
    print('the overdue rule')
    now = time.time()
    late = relay.overdue([row('a', 2 * HOUR + 60)], now)
    check('a turn past its deadline by more than its length is late',
          [r['id'] for r in late], ['a'])
    check('and says by how long', late[0]['overdue_seconds'] >= 2 * HOUR + 60,
          True)
    check('a turn past its deadline by less than its length is not',
          relay.overdue([row('a', 2 * HOUR - 60)], now), [])
    check('a turn not yet due is not',
          relay.overdue([row('a', -HOUR)], now), [])
    check('a closed galaxy is never late',
          relay.overdue([row('a', 10 * HOUR, status=turn_store.CLOSED)], now),
          [])
    check('a row with no deadline is passed over',
          relay.overdue([{'id': 'a', 'turn_seconds': HOUR}], now), [])
    check('a row with no turn length is passed over',
          relay.overdue([{'id': 'a', 'deadline': now - 10 * HOUR}], now), [])


def test_route():
    print('the route')
    ROWS[:] = [row('galaxyone', 60), row('sandbox', 30)]
    code, body = ask('/_health')
    check('every galaxy on time answers 200 with no token', code, 200)
    check('and counts the galaxies it judged', body['galaxies'], 2)
    check('and names none late', body['overdue'], [])
    check('a HEAD is answered too', relay.handle('HEAD', '/_health', {}, b'')[0],
          200)

    ROWS[:] = [row('galaxyone', 5 * HOUR), row('sandbox', 30)]
    code, body = ask('/_health')
    check('one galaxy late answers 503', code, 503)
    check('and names it', [r['id'] for r in body['overdue']], ['galaxyone'])
    check('one galaxy asked by name answers for it alone',
          ask('/_health/sandbox')[0], 200)
    check('the late one asked by name answers 503',
          ask('/_health/galaxyone')[0], 503)
    check('a galaxy that is not listed is 404',
          ask('/_health/nosuch')[0], 404)
    check('a POST is not a route', ask('/_health', 'POST')[0], 404)
    check('nothing deeper is a route', ask('/_health/sandbox/state')[0], 404)

    print('every other route still needs a token')
    check('the listing', relay.handle('GET', '/', {}, b'')[0], 401)
    check("a galaxy's state", relay.handle('GET', '/sandbox/state', {}, b'')[0],
          401)


test_rule()
test_route()
print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
sys.exit(1 if FAIL else 0)
