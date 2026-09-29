"""
test_store_equivalence.py , three stores, one interface, the same answers
=========================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_store_equivalence.py
    ... test_store_equivalence.py --firebase firebase://demo-cs/g1?bucket=b
    ... test_store_equivalence.py --only firebase --firebase <spec> --keep

`open_store` hands a caller a directory, a service or Firebase and the caller
never learns which. That is only true while the three behave the same, and the
places they can quietly stop are not the obvious ones: what a missing turn
raises, whether a second submission replaces or joins, whether publishing keeps
the roster, what `state` holds, and what an archive record survives being
stored as.

So the same sequence runs against each of them and the answers are compared,
rather than each implementation being tested against its own expectations. The
sequence is the F1 sequence: start, publish, submit, one player's own
submission, the submission list, the archive, the notes, missing turns and the
factory, and now the seat a stranger asked for.

Joins are here because of how J3 went wrong, which is the failure this file
exists to catch. The launcher wrote a request into the store's directory and
the worker read the directory back, so the feature worked, completely, against
one of the three implementations and against nothing else. The beta galaxy is
Firebase. A test that lodges a request and reads it back through one store
proves nothing about the other two, so `run_joins` runs against each of them
and the result goes into the summary the three are compared on.

The HTTP run is made against a directory this test can also read, so the
service's answers are checked against the directory underneath it as well as
against the other implementations. Firebase has nothing underneath it to
compare with, which is the point of comparing the three summaries.

The HTTP run happens twice over, without a token and with one. A launcher that
holds a Firebase identity has to be able to talk to a LAN service that does not
verify one, and a launcher that holds none has to behave exactly as it did
before identities existed. What the relay function does with a token it has
verified is `test_relay_function.py`'s to check.

The Firebase run needs somewhere to run against, and takes it either way:

    firebase emulators:start --only firestore,storage
    set FIRESTORE_EMULATOR_HOST=127.0.0.1:8080
    set STORAGE_EMULATOR_HOST=http://127.0.0.1:9199

With those set and no `--firebase`, the run uses a `demo-` project, which the
emulator suite treats as offline-only, and a galaxy named for the clock so two
runs cannot collide. Whatever it creates it deletes, unless `--keep`.

The blobs are built here rather than loaded, because two of the things under
test read inside one: `start` takes the turn number from the blob when it is
not told one, and the state carries the blob's canonical hash. Ninety-six bytes
of real framing gives both, and a `KNPL` whose trailing dword the canonical
form masks, so a hash that agreed for the wrong reason would show. The save
format itself is exercised against real captures elsewhere.
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

    `start` reads the turn out of the blob when it is not given one, which is
    the path the referee uses and the only path the HTTP store offers, so a
    test blob that cannot be parsed would quietly test something narrower.
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

# A civ name with a dot in it, which is why the Firebase archive record is one
# JSON string and not a Firestore map: a map key may not be any string a player
# typed, and this is a name a player could type.
DOTTED = 'Player.One'

# Two join requests, in the shape `release/launcher.py` writes one. Bo asked
# first and Ada second, and they are lodged the other way round, so an order
# that came from the listing rather than from `requested_at` shows.
#
# Ada has a uid and Bo has none, which is the difference between a beta galaxy
# and a folder: a request is filed under the uid when there is one and under
# the name when there is not. Bo's name carries a dot, which is what decides
# whether a store can key a request by a name a player typed at all: a
# Firestore document id may not be a dot and may not be spelled `__like_this__`,
# so the key is escaped into an id rather than used as one.
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


# ── the sequence ─────────────────────────────────────────────────────────────
def run_sequence(store, label, turn=TURN, writer=None):
    """The same checks against whichever store this is.

    Returns what the store ended up holding, so the three runs can be compared
    against each other rather than only against their own expectations.
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
    # A submission that is not there is None rather than an exception, on all
    # three. `player_turn` asks this every poll and the ordinary answer before
    # a player has played is that there is nothing, so absence has to be an
    # answer and not a fault, the way it is for `has_submitted`.
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
    # The point of the accessor: each civ gets their own bytes and nobody
    # else's, without a listing that a storage rule would have to grant.
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


# ── the fields a galaxy document carries ─────────────────────────────────────
def run_state_fields(store, label, writer):
    """A field the interface names survives being written and read back.

    `FirebaseTurnStore.state` returns an allowlist, so a field added to the
    galaxy by one writer and read by another is carried by two stores and
    dropped in silence by the third. Both fields below were in exactly that
    state and neither failure was a missing write: `joins.commit` wrote
    `joined` and `join_turn_acceptance.py` read nothing back from a Firebase
    galaxy, and L2's gate read `min_build` out of a state that could not carry
    it, so every build passed on the one deployment that has strangers in it.

    Checked here rather than in either of those files because the allowlist is
    the store's, and a test that passes against a directory says nothing about
    it, which is how both of them got in.
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


# ── the seat a stranger asked for ────────────────────────────────────────────
def run_joins(store, label):
    """J3's half of the interface, against whichever store this is.

    A join is the only thing a caller with no seat writes, and until now it was
    the only thing only one of the three implementations could take: the
    launcher wrote a file into the store's directory and `HttpTurnStore` and
    `FirebaseTurnStore` had nowhere to put one. The beta galaxy is Firebase, so
    the feature was unreachable by the beta it was built for.

    Every check here is about the round trip rather than about the write
    succeeding, because a write that succeeded and cannot be read back is
    exactly the shape that bug had.
    """
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
    # Filed under the uid and not the name. Fails if the key came from the
    # name, which would give one player two requests after a rename and would
    # let a second install lodge one under a name it does not hold.
    check(f'{label}: and filed under the uid rather than the name',
          waiting[0]['key'], 'uid-ada')

    store.request_join(BO)
    # Fails if the order came from the listing. A directory sorts by file name
    # and Firestore streams in document-id order, and on both of those Ada
    # comes first; only `requested_at` puts Bo there, and who asked first is
    # what decides which of two joins gets the last free planet.
    check(f'{label}: two requests come back oldest first',
          [r['name'] for r in store.join_requests()], ['Bo.1', 'Ada'])
    # A name a player could type, as a key. Fails on Firestore if the key were
    # used as a document id unescaped, because an id may not hold a dot on its
    # own and may not be spelled `__like_this__`.
    check(f'{label}: a request with no uid is filed under the name',
          [r['key'] for r in store.join_requests()], ['Bo.1', 'uid-ada'])

    store.request_join(dict(ADA, build='0.1.1+dev'))
    check(f'{label}: pressing Join again replaces rather than joining',
          len(store.join_requests()), 2)
    check(f'{label}: and the replacement is what is read back',
          store.join_request('uid-ada')['build'], '0.1.1+dev')
    # Fails if the named read and the listing disagreed, which is the H6 shape
    # one step along: the launcher polls the named read and the worker reads
    # the list, and a player must not be told something the worker will not do.
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
    # The three defences against one join being granted twice are independent
    # and this is the first of them. Fails if consuming did not consume: the
    # worker reads this list at every boundary, and a request still in it after
    # it was granted is a second empire for one player.
    check(f'{label}: an answered request stops waiting',
          [r['key'] for r in store.join_requests()], ['Bo.1'])
    check(f'{label}: and is not there under its own key either',
          store.join_request('uid-ada'), None)
    check(f'{label}: the answer reads back whole',
          store.join_answer('uid-ada'), answer)
    check(f'{label}: and only under the key it was filed under',
          store.join_answer('Bo.1'), None)

    store.answer_join('uid-ada', answer)
    # A worker killed after the write and before it knew the write landed
    # retries. Fails if answering twice raised, which inside a tick ends the
    # turn for everybody, or if it put the request back.
    check(f'{label}: answering twice is not an error',
          store.join_answer('uid-ada'), answer)
    check(f'{label}: and does not put the request back',
          [r['key'] for r in store.join_requests()], ['Bo.1'])

    check(f'{label}: a request filed under nothing is refused',
          raises(store.request_join, {'name': '  '}), 'ValueError')
    # Refused by all three rather than escaped by the two that could, because a
    # galaxy moves between a folder and Firebase by copying and a request only
    # one of them can hold would not survive the move.
    check(f'{label}: and one filed under a name that cannot be a file is too',
          raises(store.request_join, {'name': 'a/b'}), 'ValueError')
    check(f'{label}: neither of which is now waiting',
          [r['key'] for r in store.join_requests()], ['Bo.1'])

    # `joins.pending` is the worker's read, and it is the seam J3 left
    # unexercised: it asks a store for `join_requests` and only one of the
    # three had one. Imported here rather than at the top of this file because
    # `joins` pulls in the injection tools, and nothing else in this test needs
    # a save format that can hold a civ.
    import joins
    check(f'{label}: joins.pending reads what this store is holding',
          [r['key'] for r in joins.pending(store, log=lambda *a: None)],
          ['Bo.1'])
    check(f'{label}: and gives back exactly what the store gave it',
          joins.pending(store, log=lambda *a: None), store.join_requests())


# ── what a names-only read is allowed to fetch ───────────────────────────────
class CountingStore(TurnStore):
    """A directory store that records every submission blob it reads.

    Counting the fetches rather than checking the answer is the whole point.
    `sorted(store.submissions(turn))` returns the right names, and returned the
    right names before this was fixed; what was wrong was that it downloaded
    every player's orders to produce them. A test that compared the answer
    would have passed against the bug, which is how the bug lived in
    `check_store.py` and `turn_server.py` long enough to be written down as H6.
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
    """H6, on both sides of the seam: names cost no blobs.

    The service half is measured through the store `turn_server` is holding,
    so what is counted is what the machine serving the route actually read off
    disk. `HttpTurnStore` and `turn_server.py` are a matched pair and F3 is
    what happens when one of them is fixed and the other is not.
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

    # check_store.py prints the names and nothing else, so it is held to the
    # same count. Its own store rather than the launcher's config, because the
    # tool reads a spec when it is given one.
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

    A source-order assertion rather than a stub, because what is being held
    still is a call this test cannot reach: `check_store.main` wants a config,
    a path and a console, and the thing worth pinning is that the expensive
    call is not written there any more.
    """
    import inspect
    return inspect.getsource(module)


# ── the three runs ───────────────────────────────────────────────────────────
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
        # The operator's writes are refused over this interface on purpose,
        # so the state fields are written through the directory the service is
        # holding and read back through the service, which is the pair that
        # matters: a launcher reads L2's gate over HTTP and an operator sets it
        # where the galaxy lives.
        summary = run_sequence(HttpTurnStore(base), 'http',
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
    # The join half, through the service and into the folder behind it. Fails
    # if the service had answered out of something of its own rather than
    # writing through, which is the only way a request could be readable over
    # HTTP and invisible to the worker holding the same galaxy as a directory.
    check('http: the directory sees the request that is still waiting',
          [r['key'] for r in under.join_requests()],
          [r['key'] for r in summary['joins']])
    check('http: and the answer the service filed',
          under.join_answer('uid-ada'), summary['join_answer'])
    return summary


def run_token_checks(base, root, turn):
    """The same service, spoken to by a launcher that holds an identity.

    The relay function verifies a Firebase ID token and this service does not,
    which is the difference between a LAN and a beta. What must be true of both
    is that one launcher speaks to either: a store carrying a token has to work
    against a service that ignores one, and a store carrying none has to work
    exactly as it did before tokens existed.

    The service's own log is the witness that the header arrived. Checking that
    a tokened call succeeds proves nothing on its own, because a call with the
    header silently dropped would succeed too, and that is the failure this is
    for: `HttpTurnStore` scopes the header to its own base and a mistake there
    would leave every request unauthenticated against the relay and identical
    against this.
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

        # The relay cannot see a submission's bytes, because they go to Cloud
        # Storage directly, so it checks the object after it lands and removes
        # one that is not a save. This service checks the same thing at the
        # same point and leaves the store in the same state, which is the only
        # way a launcher can treat the two as one.
        check('http: the service refuses a submission that is not a save',
              raises(post_junk, base, turn), 'HTTPError')
        check('http: and does not leave it for the referee to read',
              TurnStore(root).submission('Junk', turn), None)
        check('http: and refuses one whose turn is not the turn being played',
              raises(post_stale, base, turn), 'HTTPError')
        check('http: and does not leave that either',
              TurnStore(root).submission('Stale', turn), None)
    finally:
        turn_server.log, turn_server.VERBOSE = kept_log, kept_verbose

    print('http: a service that predates the upload ticket')
    old_style = serve_without_tickets(root)
    try:
        legacy = f'http://127.0.0.1:{old_style.server_address[1]}'
        mine = make_blob(turn, b'j')
        HttpTurnStore(legacy).submit('Legacy', turn, mine)
        # The fallback the ticket route is allowed to be absent for. Fails if
        # `submit` required a ticket, which would break every launcher pointed
        # at a turn_server that has not been updated.
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
    """Where the Firebase run goes, and whether there is anywhere for it.

    An explicit spec wins, because that is how the live smoke check is aimed.
    Otherwise the emulator, which announces itself through the same environment
    variables the client libraries read, and a `demo-` project, which the
    emulator suite refuses to let reach a real one.
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
    """H9: who has submitted, carried on the galaxy document.

    Firebase only, because only Firebase charges for a listing. What is checked
    is that the document and the bucket never disagree about the current turn,
    and that the document says nothing about any other turn, since a turn it
    answers wrongly is worse than one it sends to the bucket.
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
        check('firebase: the document and the bucket agree',
              store.submitted_civs(TURN), store.listed_civs(TURN))
        check('firebase: CONTROL, and both name the two who submitted',
              store.listed_civs(TURN), ['DemoPlayer', 'Neighbor'])
        store.forget_submitted('Neighbor', TURN)
        check('firebase: forgetting a civ takes it off the document',
              store.submitted_civs(TURN), ['DemoPlayer'])
        store.record_submitted('Neighbor', TURN)

        store.publish(TURN + 1, BLOB2)
        # Fails if publish merged the map rather than replacing it, which
        # would keep every past turn and grow the document for ever.
        check('firebase: publish replaces the record with the new turn alone',
              recorded(), {str(TURN + 1): []})
        check('firebase: a past turn is answered from the bucket',
              store.submitted_civs(TURN), ['DemoPlayer', 'Neighbor'])
        check('firebase: and the new turn from the document, as nobody',
              store.submitted_civs(TURN + 1), [])

        # A galaxy last published by a store without the field, which is every
        # galaxy live when this shipped.
        from google.cloud import firestore
        store.doc.update({fs.SUBMITTED_KEY: firestore.DELETE_FIELD})
        store._put(store.submission_object('Neighbor', TURN + 1),
                   sp.encode_save(THEIRS), 'text/plain')
        check('firebase: a document without the field falls back to the '
              'bucket', store.submitted_civs(TURN + 1), ['Neighbor'])
    finally:
        n = store.delete_everything()
        print(f'  removed {n} object(s) and document(s)')


# ── the factory ──────────────────────────────────────────────────────────────
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
