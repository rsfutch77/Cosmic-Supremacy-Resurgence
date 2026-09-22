"""
turn_store.py , where a galaxy's turns and submissions live
===========================================================
    from turn_store import TurnStore
    store = TurnStore(root)
    store.start(blob, civs=["DemoPlayer", "Neighbor"], turn_seconds=3600)

    turn, deadline = store.current()          # what a player should be playing
    blob = store.turn_blob(turn)              # the state for that turn
    store.submit("DemoPlayer", turn, mine)    # what that player handed back
    store.publish(turn + 1, next_blob)        # the referee moves the clock on

Three processes need to agree about one galaxy: a player's launcher, another
player's launcher, and the referee that computes turns. They agree through this,
and nothing else. Keeping that agreement in one small interface is the point,
because the deployment this is heading for puts the referee on another host and
the shared state in Firebase. A Firebase adapter replaces this class and the
callers do not change.

Three implementations exist. `TurnStore` is a directory, which is enough for
one machine and for a shared folder between two. `HttpTurnStore` talks to
`turn_server.py`, which is enough for a referee on another host.
`FirebaseTurnStore`, in `firebase_store.py`, is Firestore and Cloud Storage,
which is what a beta played by strangers needs, because the referee reaches it
outbound and nobody learns where the referee is. `open_store` takes any of the
three, so a caller is given a string and never learns which it got.

`FirebaseTurnStore` is the referee's transport and cannot be a player's: it
builds Google Cloud admin clients, and a player has no Google Cloud credential.
So a player's launcher keeps speaking `HttpTurnStore`, and what answers is
either `turn_server.py` on a LAN or the relay function in `functions/`, which
holds the admin credential, verifies a Firebase ID token, and hands back signed
URLs. `HttpTurnStore` carries the token and speaks to both.

The layout is a directory, which is enough for one machine and for a shared
folder between two:

    state.json              turn number, deadline, turn length, the civ roster
    turns/0007.b64          the authoritative state for turn 7
    submissions/0007/X.b64  what civ X handed back for turn 7
    notes/0007/X.txt        what the referee refused from civ X's turn 7
    archive/0007.json       what the referee did, once it has done it

The notes are the only thing here that travels from the referee to one named
player. Every refusal was already logged, on the server, where the player whose
order was dropped never sees it: in the first two-machine rehearsal a system
rename and a conscription were both accepted by the client, both dropped by the
merge, and both simply gone the next turn with nothing to look at. The store is
the only thing both sides touch, so the note goes here and `player_turn.follow`
prints it.

Every write goes to a temporary file and is then renamed, so a reader never sees
half a blob. That matters more than it looks: readers here are polling loops, and
a torn read would look like a corrupt galaxy rather than a race.

**That is true of the contents and not of the file's existence, and only on a
local volume.** This paragraph originally claimed the rename made the store safe
for concurrent readers, which is wrong over SMB: the redirector may implement
replace-over-existing as delete then rename, so a reader on another machine sees
the file briefly absent, or gets a sharing violation opening one that is pending
delete. Measured, not reasoned about, by the second machine, whose player loop
died on `state.json` at the moment this machine's referee republished it. There
is no cross-machine atomic replace to reach for, so readers retry, and the
retry has to catch `PermissionError` as well as `FileNotFoundError`, because on
Windows the sharing violation is the *common* outcome of that race.

The deadline is an absolute epoch time rather than a countdown, so a launcher
that was closed and reopened, or a second machine whose clock differs a little,
still agrees about when the turn is due. It is the referee's to move, and nobody
else writes it.
"""
import json
import os
import struct
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'dev_tools'))

import save_parser as sp

# How long a reader waits for a `state.json` that is missing because someone is
# replacing it. See TurnStore.state.
STATE_RETRY_SECONDS = 2.0


# How long a writer keeps retrying a replace that a reader is holding open. See
# _atomic_write: this is the other half of STATE_RETRY_SECONDS, and it is the
# side that was missing.
WRITE_RETRY_SECONDS = 10.0


def _atomic_write(path: str, data: bytes) -> None:
    """Write, then replace, retrying while a reader holds the target.

    `os.replace` is not atomic over SMB and it is not even reliable: a reader
    with the file open makes the rename fail outright, as WinError 5 on a
    delete-pending handle. With two players polling `state.json` every few
    seconds that is not a rare race, it is most turns.

    This killed two referees before it was understood. Both died at
    `store.publish`, after the turn's blob had been written and before the state
    naming it was, so the galaxy stopped with a turn on disk that nothing
    pointed at. The first death was invisible because that referee's output went
    to DEVNULL; the second was caught two turns into a live rehearsal.

    Retrying rather than failing is right because the write is idempotent: the
    same bytes, to the same path, from a process that already holds the turn it
    is publishing.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f'{path}.{os.getpid()}.tmp'
    with open(tmp, 'wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    deadline = time.time() + WRITE_RETRY_SECONDS
    while True:
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if time.time() >= deadline:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
            time.sleep(0.25)


class TurnStore:
    """One galaxy's turns, submissions and clock."""

    def __init__(self, root: str):
        self.root = os.path.abspath(root)

    # ── paths ────────────────────────────────────────────────────────────────
    @property
    def state_path(self) -> str:
        return os.path.join(self.root, 'state.json')

    def turn_path(self, turn: int) -> str:
        return os.path.join(self.root, 'turns', f'{turn:04d}.b64')

    def submission_dir(self, turn: int) -> str:
        return os.path.join(self.root, 'submissions', f'{turn:04d}')

    def submission_path(self, civ: str, turn: int) -> str:
        return os.path.join(self.submission_dir(turn), f'{civ}.b64')

    def archive_path(self, turn: int) -> str:
        return os.path.join(self.root, 'archive', f'{turn:04d}.json')

    def note_path(self, civ: str, turn: int) -> str:
        return os.path.join(self.root, 'notes', f'{turn:04d}', f'{civ}.txt')

    # ── state ────────────────────────────────────────────────────────────────
    def exists(self) -> bool:
        return os.path.exists(self.state_path)

    def state(self) -> dict:
        """The galaxy's clock and roster, tolerating a writer mid-replace.

        `os.replace` is atomic on a local volume and **is not over SMB**: the
        redirector can implement replace-over-existing as a delete followed by
        a rename, and a reader on another machine sees `state.json` briefly
        absent. Measured live , machine B's player loop died with
        `FileNotFoundError` on `state.json` at the moment machine A's referee
        republished it, in a galaxy that was healthy before and after.

        This cannot be fixed from the writer's side, because there is no
        cross-machine atomic replace to reach for. So the reader retries: a
        file that is missing because someone is writing it reappears in
        milliseconds, and one that is missing because the galaxy is not there
        stays missing and still raises.

        A turn is 1800 seconds and this waits at most 2, so a caller that polls
        the clock cannot be pushed off its deadline by the retry.
        """
        deadline = time.time() + STATE_RETRY_SECONDS
        while True:
            try:
                with open(self.state_path, encoding='utf-8') as f:
                    return json.load(f)
            except (FileNotFoundError, PermissionError, ValueError):
                # All three, and the second is the one that matters most.
                # Windows reports an open against a delete-pending file as a
                # sharing violation, so the common outcome of this race is
                # `PermissionError`, not `FileNotFoundError`. Catching only the
                # obvious one left 3 reads in 300 still failing under a churning
                # writer; catching this one took it to 0.
                #
                # ValueError covers the narrower window where the file is
                # present but half written, which yields a partial JSON object
                # rather than no file. That is not a state worth acting on
                # either.
                #
                # A share that genuinely refuses us still raises, two seconds
                # later, and `exists()` stays honest throughout.
                if time.time() >= deadline:
                    raise
                time.sleep(0.05)

    def _put_state(self, state: dict) -> None:
        _atomic_write(self.state_path,
                      json.dumps(state, indent=2).encode('utf-8'))

    def current(self):
        """(turn, deadline) for the turn players should be playing now."""
        s = self.state()
        return s['turn'], s['deadline']

    def seconds_left(self) -> float:
        """How long players have. Negative once the turn is overdue."""
        return self.state()['deadline'] - time.time()

    def civs(self) -> list:
        return list(self.state().get('civs', []))

    # ── turns ────────────────────────────────────────────────────────────────
    def start(self, blob: bytes, civs, turn_seconds: int = 3600,
              turn: int = None) -> int:
        """Publish the first turn and start the clock.

        `turn` defaults to the turn number the blob itself carries, so a galaxy
        picked up mid-game keeps its own numbering rather than restarting at 1.
        """
        if turn is None:
            turn = turn_of(blob)
        import canonical
        _atomic_write(self.turn_path(turn), sp.encode_save(blob))
        self._put_state({
            'turn': int(turn),
            'deadline': time.time() + turn_seconds,
            'turn_seconds': int(turn_seconds),
            'civs': list(civs),
            'hash': canonical.canonical_hash(blob),
        })
        os.makedirs(self.submission_dir(turn), exist_ok=True)
        return turn

    def turn_blob(self, turn: int) -> bytes:
        return sp.load_any(self.turn_path(turn))

    def has_turn(self, turn: int) -> bool:
        return os.path.exists(self.turn_path(turn))

    def publish(self, turn: int, blob: bytes, turn_seconds: int = None) -> None:
        """The referee's move: a new turn exists, and the clock restarts."""
        import canonical
        state = self.state()
        seconds = turn_seconds or state['turn_seconds']
        _atomic_write(self.turn_path(turn), sp.encode_save(blob))
        state.update(turn=int(turn), deadline=time.time() + seconds,
                     turn_seconds=int(seconds),
                     hash=canonical.canonical_hash(blob))
        self._put_state(state)
        os.makedirs(self.submission_dir(turn), exist_ok=True)

    # ── submissions ──────────────────────────────────────────────────────────
    def submit(self, civ: str, turn: int, blob: bytes) -> str:
        """Hand a player's state back. Writing twice replaces, which is right:
        a player may save several times in a turn and the last one is their
        intent."""
        path = self.submission_path(civ, turn)
        _atomic_write(path, sp.encode_save(blob))
        return path

    def has_submitted(self, civ: str, turn: int) -> bool:
        return os.path.exists(self.submission_path(civ, turn))

    def submission(self, civ: str, turn: int):
        """What one civ handed back for this turn, or None when they have not.

        A player's launcher needs its own submission and nobody else's, and it
        needs it every poll. Reaching it through `submissions` cost a download
        per player per poll on a store that charges per operation, and it is
        refused outright by the rule that stops one player reading another
        player's orders. Absence is an ordinary answer here for the reason it
        is for `has_submitted`: most polls happen before the player has played.

        Only a missing file answers None. A file that is present and
        unreadable still raises, which is what `submissions` did and what the
        caller needs: the guard above this decides whether there is a
        submission worth protecting, and answering None for a file that exists
        would let it overwrite one.
        """
        try:
            return sp.load_any(self.submission_path(civ, turn))
        except FileNotFoundError:
            return None

    def submissions(self, turn: int) -> dict:
        """{civ: blob} for everyone who handed something back for this turn.

        The referee's view, at the turn boundary. A caller after one player's
        own submission wants `submission`.
        """
        out = {}
        d = self.submission_dir(turn)
        if not os.path.isdir(d):
            return out
        for name in sorted(os.listdir(d)):
            if not name.endswith('.b64'):
                continue
            out[name[:-4]] = sp.load_any(os.path.join(d, name))
        return out

    # ── archive ──────────────────────────────────────────────────────────────
    def archive(self, turn: int, record: dict) -> None:
        _atomic_write(self.archive_path(turn),
                      json.dumps(record, indent=2).encode('utf-8'))

    def archive_record(self, turn: int):
        """What the referee recorded for a turn, or None.

        Callers ask for the record rather than the path, because a store that is
        not a directory has no path to give them.
        """
        path = self.archive_path(turn)
        if not os.path.exists(path):
            return None
        with open(path, encoding='utf-8') as f:
            return json.load(f)

    # ── notes ────────────────────────────────────────────────────────────────
    def put_note(self, civ: str, turn: int, lines) -> None:
        """Leave one player the referee's reasons for refusing part of a turn.
        """
        _atomic_write(self.note_path(civ, turn),
                      '\n'.join(lines).encode('utf-8'))

    def note(self, civ: str, turn: int) -> list:
        """Those reasons, or [] when the turn was taken whole.

        No note is the ordinary case, so a missing file is an answer rather
        than a fault. `PermissionError` is caught with it for the reason
        `state` catches it: over SMB, a reader opening a file the writer is
        replacing gets a sharing violation, and a player being told nothing was
        refused when something was is the failure this exists to prevent.
        """
        try:
            with open(self.note_path(civ, turn), encoding='utf-8') as f:
                return [line for line in f.read().split('\n') if line]
        except (FileNotFoundError, PermissionError, UnicodeDecodeError):
            return []


def _same_host(a: str, b: str) -> bool:
    pa, pb = urllib.parse.urlparse(a), urllib.parse.urlparse(b)
    return (pa.scheme, pa.netloc) == (pb.scheme, pb.netloc)


def _drop_auth_on_redirect():
    """A redirect handler that does not carry the caller's token off-site.

    The relay answers a blob route with a redirect to a signed Cloud Storage
    URL, so the bytes move between the launcher and Storage and never through
    the relay. `urllib` copies every request header onto the redirected request,
    the `Authorization` header included, and Cloud Storage reads an
    `Authorization` header it was not given in the signature as a credential to
    authenticate, refuses it, and answers 401 on a URL that is perfectly valid.

    So the header is dropped whenever the redirect leaves the host it was minted
    for. That is also the right thing on its own terms: a Firebase ID token is a
    bearer credential for the relay, and no other host should see one.
    """
    import urllib.request

    class Handler(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            new = super().redirect_request(req, fp, code, msg, headers, newurl)
            if new is not None and not _same_host(req.full_url, newurl):
                new.headers = {k: v for k, v in new.headers.items()
                               if k.lower() != 'authorization'}
            return new

    return Handler


class HttpTurnStore:
    """The same interface, over `turn_server.py` or the relay function.

    Blobs move as raw decompressed bytes across this interface. The base64 the
    directory holds is the directory's business, and a caller that had to know
    about it is a caller the next adapter would break. What changes below the
    interface is where those bytes come from: `turn_server.py` hands them over
    itself, and the relay function hands over a signed Cloud Storage URL, which
    serves the base64 wire form the store keeps. `_blob` decodes either, by the
    same rule `save_parser.load_any` uses for a file that may be in either form.

    `token` is a callable rather than a string because an ID token expires in an
    hour and a launcher outlives that. Asking for one per request lets the
    holder refresh without this store, or its owner, knowing that it did. It
    returns None when there is no identity yet, and then no header is sent and
    this behaves exactly as it did before there was one.
    """

    def __init__(self, base: str, timeout: float = 30.0, token=None):
        self.base = base.rstrip('/')
        self.timeout = timeout
        self.token = token
        self._opener = None

    # -- plumbing --
    def _url(self, path: str) -> str:
        """A ticket may name an absolute URL or a path back on this service."""
        if path.startswith('http://') or path.startswith('https://'):
            return path
        return self.base + path

    def _headers(self, url: str) -> dict:
        """The Authorization header, when there is a token and it is ours.

        Scoped to this store's own base for the reason `_drop_auth_on_redirect`
        gives: a signed URL carries its own authorisation and a bearer token
        sent with it is refused.
        """
        if self.token is None or not _same_host(url, self.base):
            return {}
        tok = self.token()
        return {'Authorization': f'Bearer {tok}'} if tok else {}

    def _open(self, req):
        import urllib.request
        if self._opener is None:
            self._opener = urllib.request.build_opener(
                _drop_auth_on_redirect())
        return self._opener.open(req, timeout=self.timeout)

    def _get(self, path, want_json=True):
        import urllib.error
        import urllib.request
        url = self._url(path)
        req = urllib.request.Request(url, method='GET')
        for k, v in self._headers(url).items():
            req.add_header(k, v)
        try:
            with self._open(req) as r:
                body = r.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise
        return json.loads(body) if want_json else body

    def _blob(self, path):
        """A blob route's bytes, in whichever form the far end serves them.

        None stays None: a turn nobody published and a submission nobody made
        are ordinary answers on this interface.
        """
        data = self._get(path, want_json=False)
        if data is None or data[:4] in sp.KNOWN_TAGS:
            return data
        return sp.decode_save(data.strip())

    def _send(self, path, body=b'', method='POST', headers=None,
              want_json=True):
        import urllib.request
        url = self._url(path)
        req = urllib.request.Request(url, data=body, method=method)
        sent = dict(headers or {})
        sent.setdefault('Content-Type', 'application/octet-stream')
        sent.update(self._headers(url))
        for k, v in sent.items():
            req.add_header(k, v)
        with self._open(req) as r:
            out = r.read()
        return json.loads(out) if want_json and out else out

    def _post(self, path, body=b'', want_json=True):
        return self._send(path, body, 'POST', want_json=want_json)

    # -- state --
    def exists(self) -> bool:
        return self._get('/state') is not None

    def state(self) -> dict:
        s = self._get('/state')
        if s is None:
            raise FileNotFoundError(f'no galaxy at {self.base}')
        return s

    def current(self):
        s = self.state()
        return s['turn'], s['deadline']

    def seconds_left(self) -> float:
        return self.state()['deadline'] - time.time()

    def civs(self) -> list:
        return list(self.state().get('civs', []))

    # -- turns --
    def start(self, blob: bytes, civs, turn_seconds: int = 3600,
              turn: int = None) -> int:
        q = '&'.join([f'seconds={int(turn_seconds)}'] +
                     [f'civ={urllib.parse.quote(c)}' for c in civs])
        return self._post(f'/start?{q}', blob)['turn']

    def turn_blob(self, turn: int) -> bytes:
        blob = self._blob(f'/turn/{turn}')
        if blob is None:
            raise FileNotFoundError(f'no turn {turn} at {self.base}')
        return blob

    def has_turn(self, turn: int) -> bool:
        return self._get(f'/turn/{turn}', want_json=False) is not None

    def publish(self, turn: int, blob: bytes, turn_seconds: int = None) -> None:
        q = f'?seconds={int(turn_seconds)}' if turn_seconds else ''
        self._post(f'/turn/{turn}{q}', blob)

    # -- submissions --
    def submit(self, civ: str, turn: int, blob: bytes) -> str:
        """Hand a player's state back, by whichever route the far end offers.

        Two shapes, one code path. `turn_server.py` takes the bytes itself and
        its ticket says so. The relay function will not carry a blob at all: it
        authorises the write and hands back a signed Cloud Storage URL, so the
        bytes go to Storage directly and the function stays the control plane.
        Asking for the ticket first is what lets one store speak to both, and
        what will let the far end change again without a new launcher.

        A service that predates the ticket route answers 404 and the bytes are
        posted as they always were. A service that has the route and refuses
        this write answers 403, which raises rather than quietly falling back:
        a refusal is an answer and retrying it another way would bury it.
        """
        path = f'/submission/{turn}/{urllib.parse.quote(civ)}'
        ticket = self._get(f'/upload{path}')
        if ticket is None:
            self._post(path, blob)
            return f'{self.base}/submission/{turn}/{civ}'
        body = sp.encode_save(blob) if ticket.get('encoding') == 'b64' else blob
        self._send(ticket['url'], body,
                   method=ticket.get('method', 'PUT'),
                   headers=ticket.get('headers'), want_json=False)
        if ticket.get('commit'):
            self._post(ticket['commit'], b'')
        return f'{self.base}/submission/{turn}/{civ}'

    def has_submitted(self, civ: str, turn: int) -> bool:
        return civ in (self._get(f'/submissions/{turn}') or [])

    def submission(self, civ: str, turn: int):
        """One civ's submission, or None when there is not one.

        `_get` turns the service's 404 into None, which is the same answer the
        directory store gives for a file that is not there.
        """
        return self._blob(f'/submission/{turn}/{urllib.parse.quote(civ)}')

    def submissions(self, turn: int) -> dict:
        out = {}
        for civ in (self._get(f'/submissions/{turn}') or []):
            blob = self.submission(civ, turn)
            if blob is not None:
                out[civ] = blob
        return out

    # -- archive --
    def archive(self, turn: int, record: dict) -> None:
        self._post(f'/archive/{turn}',
                   json.dumps(record).encode('utf-8'), want_json=False)

    def archive_record(self, turn: int):
        return self._get(f'/archive/{turn}')

    # -- notes --
    def put_note(self, civ: str, turn: int, lines) -> None:
        self._post(f'/note/{turn}/{urllib.parse.quote(civ)}',
                   '\n'.join(lines).encode('utf-8'), want_json=False)

    def note(self, civ: str, turn: int) -> list:
        body = self._get(f'/note/{turn}/{urllib.parse.quote(civ)}',
                         want_json=False)
        if not body:
            return []
        return [line for line in body.decode('utf-8').split('\n') if line]


def open_store(spec: str, token=None):
    """A store from a directory path, a base URL or a Firebase project.

        some/dir                            a directory, or a share
        http://host:8899                    turn_server.py on another host
        https://host/relay/sandbox          the relay function in front of it
        firebase://cs-resurgence/sandbox    Firestore and Cloud Storage

    `token` is the caller's zero-argument source of a Firebase ID token and
    reaches the HTTP store only. The Firebase store authenticates with Google
    Cloud credentials a player does not have, and a directory authenticates with
    the filesystem, so a token means nothing to either: passing one to a spec
    that is not a URL is quietly ignored rather than refused, because a caller
    holding an identity should not have to know which store its config named.

    `firebase_store` is imported here rather than at the top of the module
    because it pulls in the google client libraries, about a second of import
    mostly spent in grpc, and the launcher and every dev tool open a directory.
    """
    if spec.startswith('http://') or spec.startswith('https://'):
        return HttpTurnStore(spec, token=token)
    if spec.startswith('firebase://'):
        import firebase_store
        return firebase_store.open_firebase_store(spec)
    return TurnStore(spec)


def turn_of(blob: bytes) -> int:
    """The turn number a blob carries, from GLOB payload +0."""
    tree = sp.parse_blob(blob)
    glob = next(tree[0].find('GLOB'))
    return struct.unpack_from('<I', blob, glob.payload)[0]


def check_save(blob: bytes, turn: int = None) -> int:
    """Refuse bytes that are not a save of this galaxy's current turn.

    Raises `ValueError` naming what was wrong, and returns the turn the blob
    carries when there was nothing wrong with it.

    Two callers want this and they want it for different reasons. The relay
    function cannot see a submission's bytes, because they go to Cloud Storage
    directly, so it checks the object once after it lands; `turn_server.py`
    checks the same thing at the same point so that the two services refuse the
    same submissions. Without it the first thing to read a bad submission is the
    referee, at the turn boundary, where one player's junk stops everyone's
    turn.

    The check is deliberately shallow: the wire form decodes, the section tree
    parses, and the turn number in `GLOB` is the one being played. It does not
    ask whether the orders inside are legal, which is `merge_orders`' job and
    needs the rest of the galaxy to answer.
    """
    if not blob:
        raise ValueError('an empty submission is not a save')
    try:
        carried = turn_of(blob)
    except Exception as exc:                                # noqa: BLE001
        raise ValueError(f'not a save blob: {type(exc).__name__}: {exc}')
    if turn is not None and carried != turn:
        raise ValueError(f'this save is turn {carried}, not turn {turn}')
    return carried
