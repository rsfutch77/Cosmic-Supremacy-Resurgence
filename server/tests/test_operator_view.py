"""
test_operator_view.py , the operator page says what is true and writes nothing
==============================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_operator_view.py
    ... test_operator_view.py --demo server\\galaxy_demo

Run against a **copy** of a real archived galaxy rather than a galaxy made up
here. A view that renders a store nobody has ever played proves very little: it
would pass with an empty roster, no archive and no deadline, which is exactly
the case every interesting line of the view is about. The default source is
`server\\galaxy_demo`, which holds turns 7 to 11 of a three-civ galaxy with four
real archive records. It is copied into a temporary directory first, and the
original is never opened for writing, because the referee or another tool may
be using it.

What is checked, and what would have failed each check, is in the name of each
line. Three of them are controls: a check that cannot fail says nothing, and
the overdue, the refusal and the write-protection checks each have a paired
case that must come out the other way.

The HTTP half runs `turn_server.py` on the loopback address, which is what shows
that the view reads a store through `open_store` without caring which kind it
got. Firebase is not exercised here: it needs the emulator, `test_store_equivalence`
already covers the store interface against it, and the only thing this module
adds above that interface is arithmetic.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools')):
    if d not in sys.path:
        sys.path.insert(0, d)

import operator_view as ov
import turn_store

PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def fingerprint(root: str) -> dict:
    """Every file under a store, by content. The evidence for "never writes".

    Content rather than mtime, because a write that replaced a file with the
    same bytes would still be a write this view is not allowed to make, and
    because a copy's mtimes are not stable across filesystems.
    """
    out = {}
    for base, _dirs, files in os.walk(root):
        for name in sorted(files):
            path = os.path.join(base, name)
            with open(path, 'rb') as fh:
                out[os.path.relpath(path, root)] = hashlib.sha256(
                    fh.read()).hexdigest()
    return out


class CountingStore:
    """A store that records which store methods the view reached for.

    The H6 finding is about which method gets called, not about what comes
    back, so counting is the only way to test it: a view that called
    `submissions(turn)` would produce exactly the same page and cost a download
    per player per refresh on Firebase.
    """

    def __init__(self, inner):
        self.inner = inner
        self.calls = {}

    def __getattr__(self, name):
        attr = getattr(self.inner, name)
        if not callable(attr):
            return attr

        def counted(*a, **kw):
            self.calls[name] = self.calls.get(name, 0) + 1
            return attr(*a, **kw)

        return counted


# ── the copy every check runs against ────────────────────────────────────────
def prepare(demo: str, tmp: str, gid: str = 'sandbox') -> str:
    """Copy the demo galaxy, and give the current turn a real submission.

    The archive ends at turn 10 and the galaxy stands at turn 11, so nobody has
    submitted for the turn in progress and the roster half of the view would be
    vacuous. One civ submits the turn's own blob, which is a submission the
    store would accept, leaving the other civ genuinely outstanding.
    """
    root = os.path.join(tmp, 'galaxies', gid)
    shutil.copytree(demo, root)
    store = turn_store.TurnStore(root)
    turn, _ = store.current()
    store.submit(store.civs()[0], turn, store.turn_blob(turn))
    return root


def set_deadline(root: str, deadline: float) -> None:
    """Move a copied galaxy's clock. Never called on anything but the copy."""
    store = turn_store.TurnStore(root)
    state = store.state()
    state['deadline'] = deadline
    store._put_state(state)


# ── checks ───────────────────────────────────────────────────────────────────
def check_read_only(root: str):
    print('read-only: the store cannot be written through this view')
    store = ov.read_only(turn_store.TurnStore(root))
    for name in ('start', 'publish', 'submit', 'archive', 'put_note',
                 '_put_state', '_atomic_write', 'delete_everything'):
        try:
            getattr(store, name)
            blocked = False
        except ov.ReadOnlyError:
            blocked = True
        check(f'read-only: {name}() is not reachable', blocked)
    # The control. An allowlist that blocked everything would pass the line
    # above and make the whole view useless, so the reads have to be reachable.
    for name in ('state', 'civs', 'has_submitted', 'archive_record'):
        try:
            getattr(store, name)
            reachable = True
        except ov.ReadOnlyError:
            reachable = False
        check(f'read-only CONTROL: {name}() is still reachable', reachable)

    try:
        store.root = 'somewhere else'
        settable = True
    except ov.ReadOnlyError:
        settable = False
    check('read-only: an attribute cannot be assigned either', settable, False)

    print('read-only: a full report leaves the store byte for byte unchanged')
    before = fingerprint(root)
    ov.galaxy_report(turn_store.TurnStore(root))
    ov.render_html(ov.reports(root))
    after = fingerprint(root)
    check('read-only: no file changed', after, before)
    check('read-only: no file appeared or vanished',
          sorted(after), sorted(before))


def check_no_bulk_download(root: str):
    print('efficiency: who has submitted is asked per civ, not by listing')
    inner = turn_store.TurnStore(root)
    counting = CountingStore(inner)
    report = ov.galaxy_report(counting)
    civs = inner.civs()
    check('efficiency: has_submitted called once per civ',
          counting.calls.get('has_submitted'), len(civs))
    check('efficiency: submissions() was never called',
          'submissions' in counting.calls, False)
    check('efficiency: turn_blob() was never called, the page needs no blob',
          'turn_blob' in counting.calls, False)
    check('efficiency CONTROL: the page still knows who submitted',
          bool(report['submitted']))
    # The proxy is what makes the line above hold in a year's time.
    proxied = ov.read_only(inner)
    try:
        proxied.submissions
        blocked = False
    except ov.ReadOnlyError:
        blocked = True
    check('efficiency: submissions() is off the proxy, so reaching for it '
          'fails loudly', blocked)


def check_contents(root: str):
    print('content: the page matches what is actually in the store')
    store = turn_store.TurnStore(root)
    turn, deadline = store.current()
    r = ov.galaxy_report(store, gid='sandbox')

    check('content: the turn is the store\'s turn', r['turn'], turn)
    check('content: the deadline is the store\'s deadline', r['deadline'],
          deadline)
    check('content: the roster is the store\'s roster',
          [c['name'] for c in r['civs']], store.civs())
    check('content: one civ has submitted', r['submitted'],
          [store.civs()[0]])
    check('content: the rest are waiting', r['waiting'], store.civs()[1:])
    check('content: submitted and waiting together are the whole roster',
          sorted(r['submitted'] + r['waiting']), sorted(store.civs()))

    rec = store.archive_record(turn - 1)
    check('content: the last tick is the newest archive record',
          r['last_tick']['turn'], turn - 1)
    check('content: it reports the recorded close time',
          r['last_tick']['closed_at'], rec['closed_at'])
    check('content: it reports the turn that tick published',
          r['last_tick']['published'], rec['published'])
    check('content: and the civs that tick was missing',
          r['last_tick']['missing'], rec['missing'])
    check('content: every archived turn the demo holds is shown',
          [h['turn'] for h in r['history']],
          [turn - 1, turn - 2, turn - 3, turn - 4])
    check('content: no duration is claimed, because none is recorded',
          r['last_tick']['measured_seconds'], None)
    check('content: the derived lateness is present instead',
          isinstance(r['last_tick']['late_by'], float))
    check('content: and the oldest row has none, having nothing before it',
          r['history'][-1]['late_by'], None)
    check('content: the reading is JSON, for anything else that wants it',
          isinstance(json.dumps(r), str))


def check_overdue(root: str, tmp: str):
    print('overdue: a turn past its deadline is called out, and one that is '
          'not is not')
    late = os.path.join(tmp, 'late')
    shutil.copytree(root, late)
    set_deadline(late, time.time() - 7200)
    r = ov.galaxy_report(turn_store.TurnStore(late))
    check('overdue: seconds_left is negative', r['seconds_left'] < 0)
    check('overdue: it is reported as a problem',
          any('overdue' in p for p in r['problems']))
    check('overdue: and the text says so rather than printing a minus sign',
          'OVERDUE' in ov.render_text([r]))

    # The control. Without it, a view that said OVERDUE unconditionally would
    # pass every line above.
    early = os.path.join(tmp, 'early')
    shutil.copytree(root, early)
    set_deadline(early, time.time() + 7200)
    r2 = ov.galaxy_report(turn_store.TurnStore(early))
    check('overdue CONTROL: a turn with time left is not overdue',
          any('overdue' in p for p in r2['problems']), False)
    check('overdue CONTROL: and the text says how long is left',
          'left' in ov.render_text([r2]))


def check_refusals(root: str, tmp: str):
    print('refusals: what the referee dropped reaches the page')
    with_notes = os.path.join(tmp, 'refused')
    shutil.copytree(root, with_notes)
    store = turn_store.TurnStore(with_notes)
    turn, _ = store.current()
    rec = store.archive_record(turn - 1)
    rec['refused'] = {'Neighbor': ['rename of system 12 is not this civ\'s',
                                   'conscription on a planet it does not own']}
    rec['ignored'] = ['Stranger']
    store.archive(turn - 1, rec)

    r = ov.galaxy_report(store)
    check('refusals: the count and the first reason are shown',
          any('2 order(s) refused from Neighbor' in p for p in r['problems']))
    check('refusals: a submission from outside the roster is called out',
          any('Stranger' in p and 'ignored' in p for p in r['problems']))
    # The control: the same galaxy without those fields must be clean of them.
    clean = ov.galaxy_report(turn_store.TurnStore(root))
    check('refusals CONTROL: an untouched galaxy reports neither',
          any('refused' in p or 'ignored' in p for p in clean['problems']),
          False)


def check_closed_and_reclaimed(root: str, tmp: str):
    """A galaxy the operator ended, and a seat the referee took back.

    Both are states a player sees and the operator has to be able to confirm,
    and both live in the galaxy's own state rather than in any listing, so a
    view that read only the directory would show a closed galaxy as open.
    """
    print('operator state: a closed galaxy and a reclaimed seat')
    ended = os.path.join(tmp, 'ended')
    shutil.copytree(root, ended)
    store = turn_store.TurnStore(ended)
    if not hasattr(store, 'close'):
        print('  SKIPPED, this store has no close(); K5 is not in the tree')
        return
    store.close('the beta finished on 1 November')
    store.update_state({turn_store.RECLAIMED_KEY: {
        'Neighbor': {'turn': 9, 'missed': 12, 'at': time.time()}}})

    r = ov.galaxy_report(store)
    check('operator state: the galaxy reads as closed', r['status'],
          turn_store.CLOSED)
    check('operator state: with the reason the operator gave',
          r['closed_reason'], 'the beta finished on 1 November')
    check('operator state: the reclaimed seat is listed',
          sorted(r['reclaimed']), ['Neighbor'])
    text = ov.render_text([r])
    check('operator state: the text says the galaxy is closed and why',
          'closed' in text and '1 November' in text)
    check('operator state: and names the seat and the misses behind it',
          'Neighbor, seat taken back at turn 9 after 12 missed turn(s)' in text)
    page = ov.render_html([r])
    check('operator state: the page says the same', '1 November' in page
          and 'seats taken back' in page)

    # The control: an untouched galaxy must read as open with nobody removed,
    # or the two checks above would pass on a view that said it of everything.
    clean = ov.galaxy_report(turn_store.TurnStore(root))
    check('operator state CONTROL: an untouched galaxy is open',
          clean['status'], turn_store.OPEN)
    check('operator state CONTROL: and has no reason and nobody removed',
          (clean['closed_reason'], clean['reclaimed']), (None, {}))
    check('operator state CONTROL: and its text carries neither line',
          'seat taken back' in ov.render_text([clean]), False)


def check_unreadable(tmp: str):
    print('failure: a store that cannot be read is a row, not a crash')
    empty = os.path.join(tmp, 'empty')
    os.makedirs(empty, exist_ok=True)
    r = ov.galaxy_report(turn_store.TurnStore(empty))
    check('failure: an empty folder reports no galaxy rather than raising',
          r['error'], 'no galaxy here yet')
    check('failure: and says which of the two causes it could be',
          any('wrong spec' in p for p in r['problems']))

    missing = os.path.join(tmp, 'not-there-at-all')
    r2 = ov.galaxy_report(turn_store.TurnStore(missing))
    check('failure: a path that does not exist is also a row',
          r2['error'] is not None)
    check('failure: main() exits 2 on a galaxy it cannot read',
          ov.main([missing]), 2)

    # A spec that open_store itself refuses, which is what a Firebase spec on a
    # machine with no Google credentials does. It has to be a row too, because
    # a traceback tells the operator nothing about the other galaxies.
    real = turn_store.open_store

    def refuse(spec, **kw):
        raise RuntimeError('no credentials on this machine')

    try:
        turn_store.open_store = refuse
        rows = ov.reports('firebase://nowhere/sandbox')
    finally:
        turn_store.open_store = real
    check('failure: a spec open_store refuses is a row, not a traceback',
          len(rows), 1)
    check('failure: and the row says why',
          'no credentials' in (rows[0]['error'] or ''))
    check('failure: the page still renders from it',
          'no credentials' in ov.render_html(rows))


def check_never_ticked(tmp: str):
    print('fresh: a galaxy that has never ticked says so')
    root = os.path.join(tmp, 'fresh')
    demo = turn_store.TurnStore(os.path.join(tmp, 'galaxies', 'sandbox'))
    turn_store.TurnStore(root).start(demo.turn_blob(demo.current()[0]),
                                     ['DemoPlayer'], turn_seconds=14400)
    r = ov.galaxy_report(turn_store.TurnStore(root))
    check('fresh: no archive record is found', r['last_tick'], None)
    check('fresh: and that is reported as a problem',
          any('nothing here has been through the referee'
              in p for p in r['problems']))
    check('fresh: nobody has submitted yet', r['submitted'], [])
    check('fresh: the whole roster is waiting', r['waiting'], ['DemoPlayer'])


def check_html(root: str):
    print('page: the html is self-contained and holds what the text holds')
    rows = ov.reports(root)
    page = ov.render_html(rows, refresh=60)
    store = turn_store.TurnStore(root)
    for civ in store.civs():
        check(f'page: {civ} appears', civ in page)
    check('page: the turn number appears', f'>{rows[0]["turn"]}<' in page)
    check('page: it refreshes itself', 'http-equiv="refresh"' in page)
    for forbidden in ('src=', '@import', 'http://', 'https://'):
        check(f'page: nothing external, no {forbidden}',
              forbidden in page, False)
    check('page: it is a complete document',
          page.startswith('<!doctype html>') and page.endswith('</html>'))


def check_http(root: str):
    """The same galaxy read over HTTP, which is what proves the seam holds."""
    print('seam: the same galaxy through open_store over HTTP')
    import turn_server
    turn_server.VERBOSE = False
    httpd = turn_server.serve(root, '127.0.0.1', 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{httpd.server_address[1]}'
    try:
        direct = ov.galaxy_report(turn_store.TurnStore(root))
        over_http = ov.reports(base)[0]
        check('seam: the store resolved to the HTTP one',
              over_http['kind'], 'HttpTurnStore')
        for key in ('turn', 'deadline', 'submitted', 'waiting'):
            check(f'seam: {key} agrees between the folder and HTTP',
                  over_http[key], direct[key])
        check('seam: the last tick agrees',
              over_http['last_tick']['closed_at'],
              direct['last_tick']['closed_at'])
        # Compared by count and by the phrase each one opens with. The full
        # text carries a countdown read a few milliseconds apart on each side,
        # and a check that fails when a duration rolls over a decimal place is
        # a check that fails for the wrong reason.
        check('seam: the same number of problems',
              len(over_http['problems']), len(direct['problems']))
        check('seam: and the same problems',
              [p[:24] for p in over_http['problems']],
              [p[:24] for p in direct['problems']])
    finally:
        httpd.shutdown()
        httpd.server_close()


def check_served_page(root: str):
    print('serve: the page comes back over loopback and writes nothing')
    import urllib.request
    before = fingerprint(root)
    httpd = ov.serve(root, port=0)
    check('serve: it binds the loopback address only',
          httpd.server_address[0], '127.0.0.1')
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(
                f'http://127.0.0.1:{httpd.server_address[1]}/',
                timeout=10) as resp:
            body = resp.read().decode('utf-8')
            code = resp.status
        check('serve: it answers 200', code, 200)
        check('serve: with the galaxy in it',
              turn_store.TurnStore(root).civs()[0] in body)
    finally:
        httpd.shutdown()
        httpd.server_close()
    check('serve: the store is unchanged after being served',
          fingerprint(root), before)


def check_directory(tmp: str):
    print('directory: every galaxy a directory lists gets a row')
    root = os.path.join(tmp, 'galaxies')
    rows = ov.reports(root, directory=True)
    check('directory: the sandbox is listed',
          [r['id'] for r in rows], ['sandbox'])
    check('directory: the row carries the galaxy status', rows[0]['status'],
          'open')

    import galaxy_directory
    d = galaxy_directory.open_directory(root)
    d.register('gone', name='Unreachable', store=os.path.join(tmp, 'nowhere'))
    rows = ov.reports(root, directory=True)
    check('directory: a galaxy whose store is missing is still a row',
          sorted(r['id'] for r in rows), ['gone', 'sandbox'])
    gone = [r for r in rows if r['id'] == 'gone'][0]
    check('directory: and it carries the error rather than dropping out',
          gone['error'] is not None)
    sandbox = turn_store.TurnStore(os.path.join(root, 'sandbox'))
    check('directory CONTROL: the healthy galaxy still reads beside it',
          [r for r in rows if r['id'] == 'sandbox'][0]['turn'],
          sandbox.current()[0])

    print('directory: it cannot be written through either')
    ro = ov.read_only_directory(d)
    for name in ('register', 'set_status'):
        try:
            getattr(ro, name)
            blocked = False
        except ov.ReadOnlyError:
            blocked = True
        check(f'directory: {name}() is not reachable', blocked)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--demo',
                    default=os.path.join(ROOT, 'server', 'galaxy_demo'),
                    help='a real archived galaxy to copy')
    ap.add_argument('--keep', action='store_true',
                    help='leave the temporary copy behind')
    a = ap.parse_args()

    if not os.path.exists(os.path.join(a.demo, 'state.json')):
        print(f'no galaxy at {a.demo}. This run proves nothing; point --demo '
              f'at a store with real archived turns.')
        return 1

    tmp = tempfile.mkdtemp(prefix='opview_')
    print(f'copy of {a.demo} at {tmp}\n')
    try:
        root = prepare(a.demo, tmp)
        check_read_only(root)
        check_no_bulk_download(root)
        check_contents(root)
        check_overdue(root, tmp)
        check_refusals(root, tmp)
        check_closed_and_reclaimed(root, tmp)
        check_unreadable(tmp)
        check_never_ticked(tmp)
        check_html(root)
        check_http(root)
        check_served_page(root)
        check_directory(tmp)

        print('\noriginal: the galaxy this was copied from was not touched')
        check('original: still where it was',
              os.path.exists(os.path.join(a.demo, 'state.json')))
    finally:
        if not a.keep:
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print(f'\nkept {tmp}')

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    for name in FAIL:
        print(f'  failed: {name}')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
