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

Two implementations exist. `TurnStore` is a directory, which is enough for one
machine and for a shared folder between two. `HttpTurnStore` talks to
`turn_server.py`, which is enough for a referee on another host. `open_store`
takes either, so a caller is given a string and never learns which it got.

The layout is a directory, which is enough for one machine and for a shared
folder between two:

    state.json              turn number, deadline, turn length, the civ roster
    turns/0007.b64          the authoritative state for turn 7
    submissions/0007/X.b64  what civ X handed back for turn 7
    archive/0007.json       what the referee did, once it has done it

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

    def submissions(self, turn: int) -> dict:
        """{civ: blob} for everyone who handed something back for this turn."""
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


class HttpTurnStore:
    """The same interface, over `turn_server.py`.

    Blobs move as raw decompressed bytes. The base64 the directory holds is the
    directory's business, and a caller that had to know about it is a caller the
    next adapter would break.
    """

    def __init__(self, base: str, timeout: float = 30.0):
        self.base = base.rstrip('/')
        self.timeout = timeout

    # -- plumbing --
    def _get(self, path, want_json=True):
        import urllib.error
        import urllib.request
        try:
            with urllib.request.urlopen(self.base + path,
                                        timeout=self.timeout) as r:
                body = r.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise
        return json.loads(body) if want_json else body

    def _post(self, path, body=b'', want_json=True):
        import urllib.request
        req = urllib.request.Request(self.base + path, data=body, method='POST')
        req.add_header('Content-Type', 'application/octet-stream')
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            out = r.read()
        return json.loads(out) if want_json and out else out

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
        blob = self._get(f'/turn/{turn}', want_json=False)
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
        self._post(f'/submission/{turn}/{urllib.parse.quote(civ)}', blob)
        return f'{self.base}/submission/{turn}/{civ}'

    def has_submitted(self, civ: str, turn: int) -> bool:
        return civ in (self._get(f'/submissions/{turn}') or [])

    def submissions(self, turn: int) -> dict:
        out = {}
        for civ in (self._get(f'/submissions/{turn}') or []):
            blob = self._get(f'/submission/{turn}/{urllib.parse.quote(civ)}',
                             want_json=False)
            if blob is not None:
                out[civ] = blob
        return out

    # -- archive --
    def archive(self, turn: int, record: dict) -> None:
        self._post(f'/archive/{turn}',
                   json.dumps(record).encode('utf-8'), want_json=False)

    def archive_record(self, turn: int):
        return self._get(f'/archive/{turn}')


def open_store(spec: str):
    """A store from a directory path or a base URL, whichever this is."""
    if spec.startswith('http://') or spec.startswith('https://'):
        return HttpTurnStore(spec)
    return TurnStore(spec)


def turn_of(blob: bytes) -> int:
    """The turn number a blob carries, from GLOB payload +0."""
    tree = sp.parse_blob(blob)
    glob = next(tree[0].find('GLOB'))
    return struct.unpack_from('<I', blob, glob.payload)[0]
