"""
test_store_equivalence.py: three stores, one interface, the same answers
=========================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_store_equivalence.py
    ... test_store_equivalence.py --firebase firebase://demo-cs/g1?bucket=b
    ... test_store_equivalence.py --only firebase --firebase <spec> --keep

The same sequence runs against the directory, HTTP and Firebase stores and the
answers are compared: start, publish, submit, one player's own submission, the
submission list, the archive, the notes, missing turns, joins and the factory.

The HTTP run is also checked against the directory underneath the service, and
runs with and without a token.

The Firebase run uses an explicit `--firebase` spec, or the emulator:

    firebase emulators:start --only firestore,storage
    set FIRESTORE_EMULATOR_HOST=127.0.0.1:8080
    set STORAGE_EMULATOR_HOST=http://127.0.0.1:9199

With those set and no `--firebase`, the run uses a `demo-` project and a galaxy
named for the clock. Whatever it creates it deletes, unless `--keep`.

The test blobs carry a turn number and a `KNPL` whose trailing dword the
canonical form masks.
"""
import argparse
import os
import struct
import sys
import threading
import time
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools')):
    if d not in sys.path:
        sys.path.insert(0, d)

import canonical
import save_parser as sp
import turn_store
from turn_store import HttpTurnStore, TurnStore, open_store

PASS, FAIL = [], []

TURN = 7


def section(tag, payload, version=0):
    """One section of the save format: the tag, the size, the payload."""
    head = (len(payload) & sp.SIZE_MASK) | (version << sp.VERSION_SHIFT)
    return tag + struct.pack('<I', head) + payload


def make_blob(turn, filler=b'.'):
    """A blob that carries a turn number and a field the canonical form masks.

    `start` reads the turn out of the blob when it is not given one.
    """
    return section(b'SAVE',
                   section(b'GLOB', struct.pack('<I', turn) + filler * 60) +
                   section(b'KNPL', bytes([0xAA]) * 8))


BLOB1 = make_blob(TURN)
BLOB2 = make_blob(TURN + 1)
BLOB3 = make_blob(TURN + 2)
MINE = make_blob(TURN, b'o')
MINE2 = make_blob(TURN, b'p')
THEIRS = make_blob(TURN, b'n')

# A civ name with a dot in it. The Firebase archive record is one JSON string
# because a Firestore map key may not be any string a player typed.
DOTTED = 'Player.One'

# Two join requests, in the shape `release/launcher.py` writes one. Bo asked
# first and is lodged second, so the order must come from `requested_at`.
# A request is filed under the uid when there is one and under the name
# otherwise. Bo's dotted name has to be escaped into a Firestore document id.
ADA = {'name': 'Ada', 'uid': 'uid-ada', 'build': '0.1.0+dev',
       'requested_at': 200.0, 'requested_turn': TURN}
BO = {'name': 'Bo.1', 'uid': None, 'build': '0.1.0+dev',
      'requested_at': 100.0, 'requested_turn': TURN}


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def raises(fn, *a):
    """What a call raised, by class name, or None. Missing things differ."""
    try:
        fn(*a)
    except Exception as exc:                                # noqa: BLE001
        return type(exc).__name__
    return None


def run_sequence(store, label, turn=TURN, writer=None):
    """The same checks against whichever store this is.

    Returns what the store ended up holding, so the runs can be compared.
    """
    print(f'{label}: an empty store')
    check(f'{label}: exists() is False before a galaxy is started',
          store.exists(), False)
    check(f'{label}: state() on an empty store raises FileNotFoundError',
          raises(store.state), 'FileNotFoundError')

    print(f'{label}: start')
    started = store.start(BLOB1, ['DemoPlayer', 'Neighbor'], turn_seconds=1800)
    check(f'{label}: start takes the turn number from the blob', started, turn)
    check(f'{label}: exists() is True once it has', store.exists(), True)
    state = store.state()
    check(f'{label}: state holds exactly the five fields the interface names',
          sorted(state), ['civs', 'deadline', 'hash', 'turn', 'turn_seconds'])
    check(f'{label}: current() is the started turn', store.current()[0], turn)
    check(f'{label}: the deadline is turn_seconds away',
          round(store.seconds_left() / 60), 30)
    check(f'{label}: civs() is the roster it was started with',
          store.civs(), ['DemoPlayer', 'Neighbor'])
    check(f'{label}: the state carries the blob canonical hash',
          state['hash'], canonical.canonical_hash(BLOB1))

    print(f'{label}: turns')
    check(f'{label}: the turn blob comes back as it went in',
          store.turn_blob(turn), BLOB1)
    check(f'{label}: has_turn is True for a turn that was published',
          store.has_turn(turn), True)
    check(f'{label}: has_turn is False for one that was not',
          store.has_turn(turn + 5), False)
    check(f'{label}: a missing turn raises FileNotFoundError',
          raises(store.turn_blob, turn + 5), 'FileNotFoundError')

    print(f'{label}: submissions')
    check(f'{label}: nobody has submitted yet',
          store.has_submitted('DemoPlayer', turn), False)
    # A missing submission is None on every store, not an exception.
    check(f'{label}: a submission nobody has made is None',
          store.submission('DemoPlayer', turn), None)
    check(f'{label}: and asking for it raises nothing',
          raises(store.submission, 'DemoPlayer', turn), None)
    where = store.submit('DemoPlayer', turn, MINE)
    check(f'{label}: submit names where it landed',
          bool(where) and 'DemoPlayer' in where, True)
    check(f'{label}: has_submitted is True once they have',
          store.has_submitted('DemoPlayer', turn), True)
    check(f'{label}: the submission comes back as it went in',
          store.submissions(turn), {'DemoPlayer': MINE})
    check(f'{label}: and one civ own submission is those same bytes',
          store.submission('DemoPlayer', turn), MINE)
    store.submit('DemoPlayer', turn, MINE2)
    check(f'{label}: submitting twice replaces rather than joins',
          store.submissions(turn), {'DemoPlayer': MINE2})
    check(f'{label}: and the replacement is what one civ read gives back',
          store.submission('DemoPlayer', turn), MINE2)
    check(f'{label}: the names-only accessor names the one who has submitted',
          store.submitted_civs(turn), ['DemoPlayer'])
    store.submit('Neighbor', turn, THEIRS)
    check(f'{label}: another civ joins the list',
          store.submissions(turn), {'DemoPlayer': MINE2, 'Neighbor': THEIRS})
    check(f'{label}: and joins the names, sorted',
          store.submitted_civs(turn), ['DemoPlayer', 'Neighbor'])
    check(f'{label}: the names are exactly the keys of the blobs',
          store.submitted_civs(turn), sorted(store.submissions(turn)))
    check(f'{label}: a turn nobody submitted for names nobody',
          store.submitted_civs(turn + 1), [])
    check(f'{label}: each civ reads their own submission',
          (store.submission('DemoPlayer', turn),
           store.submission('Neighbor', turn)), (MINE2, THEIRS))
    check(f'{label}: a civ who never submitted is None',
          store.submission('Stranger', turn), None)
    check(f'{label}: a submission is per turn',
          store.submission('DemoPlayer', turn + 1), None)
    check(f'{label}: a turn nobody submitted for lists nothing',
          store.submissions(turn + 1), {})

    print(f'{label}: notes')
    check(f'{label}: a turn nothing was refused on has no note',
          store.note('DemoPlayer', turn), [])
    reasons = ['system 193: rename DROPPED, not a majority',
               'people DROPPED, 9 served and 10 came back']
    store.put_note('DemoPlayer', turn, reasons)
    check(f'{label}: a note comes back as it was written',
          store.note('DemoPlayer', turn), reasons)
    check(f'{label}: and only to the player it was left for',
          store.note('Neighbor', turn), [])
    check(f'{label}: notes are per turn',
          store.note('DemoPlayer', turn + 1), [])

    print(f'{label}: archive')
    check(f'{label}: an unarchived turn records None',
          store.archive_record(turn), None)
    record = {'turn': turn, 'published': turn + 1,
              'submitted': ['DemoPlayer', 'Neighbor'], 'missing': [],
              'refused': {DOTTED: list(reasons)},
              'hash_in': canonical.canonical_hash(BLOB1),
              'hash_out': canonical.canonical_hash(BLOB2),
              'hash_submissions': {DOTTED: canonical.canonical_hash(MINE2)}}
    store.archive(turn, record)
    check(f'{label}: the archive record comes back as it was written',
          store.archive_record(turn), record)

    print(f'{label}: publish')
    before = store.state()['deadline']
    time.sleep(0.01)
    store.publish(turn + 1, BLOB2)
    check(f'{label}: the clock moves to the published turn',
          store.current()[0], turn + 1)
    check(f'{label}: publishing restarts the clock',
          store.state()['deadline'] > before, True)
    check(f'{label}: publishing keeps the roster',
          store.civs(), ['DemoPlayer', 'Neighbor'])
    check(f'{label}: publishing keeps the turn length',
          store.state()['turn_seconds'], 1800)
    check(f'{label}: the published blob comes back as it went in',
          store.turn_blob(turn + 1), BLOB2)
    check(f'{label}: and the state carries its hash',
          store.state()['hash'], canonical.canonical_hash(BLOB2))
    store.publish(turn + 2, BLOB3, turn_seconds=14400)
    check(f'{label}: a publish may change the turn length',
          store.state()['turn_seconds'], 14400)
    check(f'{label}: the older turn is still there',
          store.turn_blob(turn), BLOB1)
    check(f'{label}: and so is its archive record',
          store.archive_record(turn), record)

    run_joins(store, label)
    run_state_fields(store, label, writer or store)

    return {
        'turn': store.current()[0],
        'turn_seconds': store.state()['turn_seconds'],
        'civs': store.civs(),
        'hash': store.state()['hash'],
        'blob': store.turn_blob(store.current()[0]),
        'submissions': store.submissions(turn),
        'submitted_civs': store.submitted_civs(turn),
        'submission': store.submission('DemoPlayer', turn),
        'submission_missing': store.submission('Stranger', turn),
        'note': store.note('DemoPlayer', turn),
        'archive': store.archive_record(turn),
        'joins': store.join_requests(),
        'join_request': store.join_request('Bo.1'),
        'join_answer': store.join_answer('uid-ada'),
        'join_answer_missing': store.join_answer('Bo.1'),
        'joined': store.state().get(turn_store.JOINED_KEY),
        'min_build': store.state().get(turn_store.MIN_BUILD_KEY),
    }


def run_state_fields(store, label, writer):
    """A field the interface names survives being written and read back.

    `FirebaseTurnStore.state` returns an allowlist, so each field read from the
    galaxy state must be on it.
    """
    print(f'{label}: the fields a galaxy carries')
    seated = {'Ada': {'turn': TURN + 1, 'uid': 'uid-ada', 'planet': 6,
                      'system': 'Tau Ceti', 'build': '0.1.0+dev', 'at': 1.0}}
    writer.update_state({turn_store.JOINED_KEY: seated,
                         turn_store.MIN_BUILD_KEY: '0.2.0'})
    state = store.state()
    check(f'{label}: the record of who was seated reads back',
          state.get(turn_store.JOINED_KEY), seated)
    check(f'{label}: and the minimum build L2 gates on',
          state.get(turn_store.MIN_BUILD_KEY), '0.2.0')
    check(f'{label}: and the clock is where it was',
          state['turn'], store.current()[0])


def run_joins(store, label):
    """Join requests and answers round-trip through whichever store this is."""
    print(f'{label}: joins')
    check(f'{label}: a galaxy nobody has asked to join has nothing waiting',
          store.join_requests(), [])
    check(f'{label}: and nothing waiting under a key nobody used',
          store.join_request('uid-ada'), None)
    check(f'{label}: and nothing decided under one either',
          store.join_answer('uid-ada'), None)

    where = store.request_join(ADA)
    check(f'{label}: request_join names where it landed',
          bool(where) and 'uid-ada' in where, True)
    waiting = store.join_requests()
    check(f'{label}: one request is waiting', len(waiting), 1)
    check(f'{label}: carrying the name the player typed',
          waiting[0]['name'], 'Ada')
    check(f'{label}: the uid J4 binds a seat to', waiting[0]['uid'], 'uid-ada')
    check(f'{label}: the turn they asked during',
          waiting[0]['requested_turn'], TURN)
    # A key taken from the name would give one player two requests after a
    # rename.
    check(f'{label}: and filed under the uid rather than the name',
          waiting[0]['key'], 'uid-ada')

    store.request_join(BO)
    # Order comes from `requested_at`, which decides who gets the last free
    # planet. Listing order would put Ada first.
    check(f'{label}: two requests come back oldest first',
          [r['name'] for r in store.join_requests()], ['Bo.1', 'Ada'])
    # A Firestore document id may not be a lone dot or `__like_this__`, so the
    # key is escaped.
    check(f'{label}: a request with no uid is filed under the name',
          [r['key'] for r in store.join_requests()], ['Bo.1', 'uid-ada'])

    store.request_join(dict(ADA, build='0.1.1+dev'))
    check(f'{label}: pressing Join again replaces rather than joining',
          len(store.join_requests()), 2)
    check(f'{label}: and the replacement is what is read back',
          store.join_request('uid-ada')['build'], '0.1.1+dev')
    # The launcher polls the named read and the worker reads the list, so the
    # two must agree.
    check(f'{label}: a named read is the record the listing gives',
          store.join_request('uid-ada'),
          [r for r in store.join_requests() if r['key'] == 'uid-ada'][0])
    check(f'{label}: and a key nobody lodged under is None',
          store.join_request('uid-nobody'), None)

    answer = {'key': 'uid-ada', 'name': 'Ada', 'uid': 'uid-ada',
              'build': '0.1.1+dev', 'requested_at': 200.0,
              'requested_turn': TURN, 'turn': TURN + 1, 'outcome': 'granted',
              'reason': '', 'planet': 6, 'sun': 3, 'system': 'Tau Ceti',
              'answered_at': 300.0}
    store.answer_join('uid-ada', answer)
    # A request still waiting after it was granted would be granted again at
    # the next boundary.
    check(f'{label}: an answered request stops waiting',
          [r['key'] for r in store.join_requests()], ['Bo.1'])
    check(f'{label}: and is not there under its own key either',
          store.join_request('uid-ada'), None)
    check(f'{label}: the answer reads back whole',
          store.join_answer('uid-ada'), answer)
    check(f'{label}: and only under the key it was filed under',
          store.join_answer('Bo.1'), None)

    store.answer_join('uid-ada', answer)
    # A worker killed after the write retries it, so answering is idempotent.
    check(f'{label}: answering twice is not an error',
          store.join_answer('uid-ada'), answer)
    check(f'{label}: and does not put the request back',
          [r['key'] for r in store.join_requests()], ['Bo.1'])

    check(f'{label}: a request filed under nothing is refused',
          raises(store.request_join, {'name': '  '}), 'ValueError')
    # Refused by every store, so a galaxy copied between a folder and Firebase
    # holds the same requests.
    check(f'{label}: and one filed under a name that cannot be a file is too',
          raises(store.request_join, {'name': 'a/b'}), 'ValueError')
    check(f'{label}: neither of which is now waiting',
          [r['key'] for r in store.join_requests()], ['Bo.1'])

    # `joins.pending` is the worker's read. Imported here because `joins`
    # pulls in the injection tools.
    import joins
    check(f'{label}: joins.pending reads what this store is holding',
          [r['key'] for r in joins.pending(store, log=lambda *a: None)],
          ['Bo.1'])
    check(f'{label}: and gives back exactly what the store gave it',
          joins.pending(store, log=lambda *a: None), store.join_requests())


class CountingStore(TurnStore):
    """A directory store that records every submission blob it reads.

    Names-only reads are checked by what they fetch, since fetching every blob
    also produces the right names.
    """

    def __init__(self, root):
        TurnStore.__init__(self, root)
        self.fetched = []

    def submission(self, civ, turn):
        self.fetched.append(civ)
        return TurnStore.submission(self, civ, turn)

    def submissions(self, turn):
        d = self.submission_dir(turn)
        if os.path.isdir(d):
            self.fetched.extend(sorted(n[:-4] for n in os.listdir(d)
                                       if n.endswith('.b64')))
        return TurnStore.submissions(self, turn)


def run_fetch_counts(tmp):
    """Names-only reads fetch no blobs, in the directory store and over HTTP.

    The HTTP half is counted through the store `turn_server` is holding.
    """
    import turn_server
    print('names-only reads fetch no blobs')
    root = os.path.join(tmp, 'galaxy_counts')
    store = CountingStore(root)
    store.start(BLOB1, ['DemoPlayer', 'Neighbor'], turn_seconds=1800)
    store.submit('DemoPlayer', TURN, MINE)
    store.submit('Neighbor', TURN, THEIRS)

    store.fetched = []
    names = store.submitted_civs(TURN)
    check('directory: submitted_civs answers the names',
          names, ['DemoPlayer', 'Neighbor'])
    check('directory: and fetched no submission to do it', store.fetched, [])

    store.fetched = []
    store.submissions(TURN)
    check('directory: the control, submissions does fetch both',
          store.fetched, ['DemoPlayer', 'Neighbor'])

    kept_store, kept_verbose = turn_server.STORE, turn_server.VERBOSE
    turn_server.VERBOSE = False
    httpd = turn_server.serve(root, '127.0.0.1', 0)
    turn_server.STORE = store
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        client = HttpTurnStore(f'http://127.0.0.1:{httpd.server_address[1]}')
        store.fetched = []
        check('http: the service answers /submissions with the names',
              client.submitted_civs(TURN), ['DemoPlayer', 'Neighbor'])
        check('http: and read no submission off disk to do it',
              store.fetched, [])
        store.fetched = []
        check('http: has_submitted rides the same route',
              client.has_submitted('Neighbor', TURN), True)
        check('http: and cost no blob either', store.fetched, [])
        store.fetched = []
        check('http: the control, asking for the blobs does fetch them',
              client.submissions(TURN),
              {'DemoPlayer': MINE, 'Neighbor': THEIRS})
        check('http: two of them', sorted(store.fetched),
              ['DemoPlayer', 'Neighbor'])
    finally:
        httpd.shutdown()
        httpd.server_close()
        turn_server.STORE, turn_server.VERBOSE = kept_store, kept_verbose

    # check_store.py prints only the names, so it is held to the same count.
    import check_store
    counted = CountingStore(root)
    counted.fetched = []
    check('check_store: the tool it was recorded against reads names only',
          counted.submitted_civs(TURN), ['DemoPlayer', 'Neighbor'])
    check('check_store: fetching nothing', counted.fetched, [])
    check('check_store: and the source no longer asks for the blobs',
          'store.submissions(' in inspect_source(check_store), False)


def inspect_source(module) -> str:
    """A module's own text, for asserting on what it calls.

    `check_store.main` needs a config, a path and a console, so its calls are
    checked in the source.
    """
    import inspect
    return inspect.getsource(module)


def run_directory(tmp):
    root = os.path.join(tmp, 'galaxy_dir')
    return run_sequence(TurnStore(root), 'directory')


def run_http(tmp):
    """The service, in front of a directory this test can also read."""
    import turn_server
    root = os.path.join(tmp, 'galaxy_http')
    os.makedirs(root, exist_ok=True)
    turn_server.VERBOSE = False
    httpd = turn_server.serve(root, '127.0.0.1', 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        base = f'http://127.0.0.1:{httpd.server_address[1]}'
        # The service refuses operator writes, so the state fields are written
        # through the directory and read back through the service.
        # `state_seconds=0` so that write is seen at once; test_state_reads.py
        # covers the window.
        summary = run_sequence(HttpTurnStore(base, state_seconds=0), 'http',
                               writer=TurnStore(root))
        run_token_checks(base, root, summary['turn'])
    finally:
        httpd.shutdown()
        httpd.server_close()

    print('http: the directory underneath agrees')
    under = TurnStore(root)
    check('http: the directory sees the same turn',
          under.current()[0], summary['turn'])
    check('http: the directory sees the same hash',
          under.state()['hash'], summary['hash'])
    check('http: the directory holds the same blob bytes',
          under.turn_blob(summary['turn']), summary['blob'])
    check('http: the directory sees the same submissions',
          under.submissions(7), summary['submissions'])
    check('http: the directory holds the same one submission',
          under.submission('DemoPlayer', 7), summary['submission'])
    check('http: the directory holds the same archive record',
          under.archive_record(7), summary['archive'])
    # Joins written through the service land in the folder behind it.
    check('http: the directory sees the request that is still waiting',
          [r['key'] for r in under.join_requests()],
          [r['key'] for r in summary['joins']])
    check('http: and the answer the service filed',
          under.join_answer('uid-ada'), summary['join_answer'])
    return summary


def run_token_checks(base, root, turn):
    """The same service, spoken to by a launcher that holds an identity.

    This service ignores tokens, so a store with or without one must work
    against it. The service's log shows whether the header arrived.
    """
    print('http: a token rides along and is not required')
    import turn_server
    lines = []
    kept_log, kept_verbose = turn_server.log, turn_server.VERBOSE
    turn_server.log = lines.append
    turn_server.VERBOSE = True
    try:
        plain = HttpTurnStore(base)
        tokened = HttpTurnStore(base, token=lambda: 'an.id.token')
        empty = HttpTurnStore(base, token=lambda: None)
        check('http: a tokened store reads the same state',
              tokened.state(), plain.state())
        check('http: and the same turn blob',
              tokened.turn_blob(turn), plain.turn_blob(turn))
        check('http: and a store whose token callable answers None does too',
              empty.state(), plain.state())

        mine = make_blob(turn, b'k')
        tokened.submit('Tokened', turn, mine)
        check('http: a tokened submit lands as itself',
              TurnStore(root).submission('Tokened', turn), mine)
        check('http: and the service saw an identity offered',
              any('Tokened' in ln and 'identity offered' in ln
                  for ln in lines), True)
        plain.submit('Plain', turn, mine)
        check('http: an untokened submit lands too',
              TurnStore(root).submission('Plain', turn), mine)
        check('http: and the service saw no identity with it',
              any('Plain' in ln and 'identity offered' in ln
                  for ln in lines), False)
        empty.submit('Absent', turn, mine)
        check('http: a token callable answering None sends no header either',
              any('Absent' in ln and 'identity offered' in ln
                  for ln in lines), False)

        # Like the relay, the service refuses a body that is not a save of the
        # turn before it stores anything.
        check('http: the service refuses a submission that is not a save',
              raises(post_junk, base, turn), 'HTTPError')
        check('http: and does not leave it for the referee to read',
              TurnStore(root).submission('Junk', turn), None)
        check('http: and refuses one whose turn is not the turn being played',
              raises(post_stale, base, turn), 'HTTPError')
        check('http: and does not leave that either',
              TurnStore(root).submission('Stale', turn), None)
        # A refused body leaves the earlier submission standing.
        plain.submit('Standing', turn, mine)
        check('http: a refused body over a good submission raises',
              raises(plain._post, f'/submission/{turn}/Standing',
                     b'not a save at all'), 'HTTPError')
        check('http: and the good one still stands',
              TurnStore(root).submission('Standing', turn), mine)
    finally:
        turn_server.log, turn_server.VERBOSE = kept_log, kept_verbose

    print('http: a service that predates the upload ticket')
    old_style = serve_without_tickets(root)
    try:
        legacy = f'http://127.0.0.1:{old_style.server_address[1]}'
        mine = make_blob(turn, b'j')
        HttpTurnStore(legacy).submit('Legacy', turn, mine)
        # `submit` falls back when the service has no ticket route.
        check('http: a submission still lands when there is no ticket route',
              TurnStore(root).submission('Legacy', turn), mine)
    finally:
        old_style.shutdown()
        old_style.server_close()


def serve_without_tickets(root):
    """The same service with the ticket route taken out, on its own port."""
    import turn_server

    class Older(turn_server.Handler):
        def do_GET(self):
            parts, _q = self._parts()
            if parts[:2] == ['upload', 'submission']:
                return self._fail(404, 'no route for ' + self.path)
            return turn_server.Handler.do_GET(self)

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Older)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def post_junk(base, turn):
    """Put bytes that are not a save where a submission goes, then commit."""
    store = HttpTurnStore(base)
    store._post(f'/submission/{turn}/Junk', b'not a save at all')
    store._post(f'/commit/submission/{turn}/Junk', b'')


def post_stale(base, turn):
    """Submit a real save of the wrong turn, then commit."""
    store = HttpTurnStore(base)
    store._post(f'/submission/{turn}/Stale', make_blob(turn - 3, b'y'))
    store._post(f'/commit/submission/{turn}/Stale', b'')


def firebase_spec(given):
    """Where the Firebase run goes, or None when there is nowhere.

    An explicit spec wins. Otherwise the emulator, if its environment variables
    are set, with a `demo-` project.
    """
    if given:
        return given
    if os.environ.get('CS_FIREBASE_STORE'):
        return os.environ['CS_FIREBASE_STORE']
    if not os.environ.get('FIRESTORE_EMULATOR_HOST'):
        return None
    galaxy = f'equiv_{int(time.time())}'
    return (f'firebase://demo-cs-resurgence/{galaxy}'
            '?bucket=demo-cs-resurgence.firebasestorage.app')


def run_firebase(spec, keep=False):
    store = open_store(spec)
    print(f'firebase: {store!r}')
    try:
        return run_sequence(store, 'firebase')
    finally:
        if keep:
            print(f'  kept {spec}')
        else:
            n = store.delete_everything()
            print(f'  removed {n} object(s) and document(s) from {spec}')


def run_submitted_field(spec):
    """Who has submitted, carried on the Firebase galaxy document.

    The document must agree with the stored submissions for the current turn
    and say nothing about any other turn.
    """
    import firebase_store as fs
    path, sep, query = spec.partition('?')
    store = open_store(f'{path}_submitted{sep}{query}')
    print(f'firebase: who has submitted, on {store!r}')

    def recorded():
        return (store.doc.get().to_dict() or {}).get(fs.SUBMITTED_KEY)

    try:
        store.start(BLOB1, ['DemoPlayer', 'Neighbor'], turn_seconds=1800)
        check('firebase: start records that nobody has submitted',
              recorded(), {str(TURN): []})
        store.submit('DemoPlayer', TURN, MINE)
        store.submit('DemoPlayer', TURN, MINE2)
        check('firebase: a submission is recorded once however often it is '
              'made', recorded(), {str(TURN): ['DemoPlayer']})
        store.submit('Neighbor', TURN, THEIRS)
        check('firebase: the document and the stored submissions agree',
              store.submitted_civs(TURN), store.listed_civs(TURN))
        check('firebase: CONTROL, and both name the two who submitted',
              store.listed_civs(TURN), ['DemoPlayer', 'Neighbor'])
        store.forget_submitted('Neighbor', TURN)
        check('firebase: forgetting a civ takes it off the document',
              store.submitted_civs(TURN), ['DemoPlayer'])
        store.record_submitted('Neighbor', TURN)

        store.publish(TURN + 1, BLOB2)
        # Publish replaces the map, so the document does not grow each turn.
        check('firebase: publish replaces the record with the new turn alone',
              recorded(), {str(TURN + 1): []})
        check('firebase: a past turn is answered from the stored submissions',
              store.submitted_civs(TURN), ['DemoPlayer', 'Neighbor'])
        check('firebase: and the new turn from the document, as nobody',
              store.submitted_civs(TURN + 1), [])

        # A galaxy without either field, with a submission stored as a Storage
        # object.
        from google.cloud import firestore
        store.doc.update({fs.SUBMITTED_KEY: firestore.DELETE_FIELD,
                          fs.SUBMISSIONS_FROM_KEY: firestore.DELETE_FIELD})
        store._put(store.submission_object('Neighbor', TURN + 1),
                   sp.encode_save(THEIRS), 'text/plain')
        check('firebase: a document without the field falls back to the '
              'bucket', store.submitted_civs(TURN + 1), ['Neighbor'])
        # A galaxy started with submissions as documents does not list the
        # bucket.
        store.doc.update({fs.SUBMISSIONS_FROM_KEY: TURN})
        check('firebase: CONTROL, and one started with documents does not',
              store.submitted_civs(TURN + 1), [])
    finally:
        n = store.delete_everything()
        print(f'  removed {n} object(s) and document(s)')


def check_factory():
    print('the factory hands back the right one')
    import firebase_store
    check('a path is a directory store',
          type(open_store('some/dir')).__name__, 'TurnStore')
    check('a URL is the HTTP store',
          type(open_store('http://host:8899')).__name__, 'HttpTurnStore')
    check('a firebase spec is the Firebase store',
          type(open_store('firebase://proj/gal')).__name__,
          'FirebaseTurnStore')
    check('the spec parses into project, galaxy, bucket and prefix',
          firebase_store.parse_spec(
              'firebase://proj/gal?bucket=b&prefix=beta2'),
          ('proj', 'gal', 'b', 'beta2'))
    check('a spec with no galaxy is refused by the store',
          raises(open_store, 'firebase://proj'), 'ValueError')


def compare(summaries):
    """Every implementation ended up holding the same galaxy."""
    print('the implementations agree')
    names = sorted(summaries)
    first = names[0]
    for other in names[1:]:
        a, b = summaries[first], summaries[other]
        for field in sorted(a):
            check(f'{first} and {other} agree about {field}',
                  a[field] == b[field], True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--firebase', help='a firebase:// spec to run against')
    ap.add_argument('--only', choices=['dir', 'http', 'firebase'],
                    action='append',
                    help='run one implementation rather than all of them')
    ap.add_argument('--keep', action='store_true',
                    help='leave the Firebase galaxy behind')
    a = ap.parse_args()
    want = a.only or ['dir', 'http', 'firebase']

    import tempfile
    tmp = tempfile.mkdtemp(prefix='equiv_')
    summaries = {}
    if 'dir' in want:
        summaries['directory'] = run_directory(tmp)
    if 'http' in want:
        summaries['http'] = run_http(tmp)
    if 'firebase' in want:
        spec = firebase_spec(a.firebase)
        if spec:
            summaries['firebase'] = run_firebase(spec, keep=a.keep)
            run_submitted_field(spec)
        else:
            print('firebase: SKIPPED, no emulator and no --firebase spec. '
                  'This run does not say whether H1 is done.')
    if len(summaries) > 1:
        compare(summaries)
    if 'dir' in want:
        run_fetch_counts(tmp)
    check_factory()

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    if FAIL:
        for name in FAIL:
            print(f'  failed: {name}')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
