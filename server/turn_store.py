"""
turn_store.py: where a galaxy's turns and submissions live
===========================================================
    from turn_store import TurnStore
    store = TurnStore(root)
    store.start(blob, civs=["DemoPlayer", "Neighbor"], turn_seconds=3600)

    turn, deadline = store.current()          # what a player should be playing
    blob = store.turn_blob(turn)              # the state for that turn
    store.submit("DemoPlayer", turn, mine)    # what that player handed back
    store.publish(turn + 1, next_blob)        # the referee moves the clock on

A player's launcher, other players' launchers and the referee agree about one
galaxy only through this interface. `TurnStore` is a directory (one machine or a
shared folder), `HttpTurnStore` talks to `turn_server.py` or the relay function
in `functions/`, and `FirebaseTurnStore` (`firebase_store.py`) is Firestore and
Cloud Storage, used by the referee only because it needs admin credentials.
`open_store` takes a spec string for any of the three.

The directory layout:

    state.json              turn number, deadline, turn length, the civ roster
    turns/0007.b64          the authoritative state for turn 7
    submissions/0007/X.b64  what civ X handed back for turn 7
    notes/0007/X.txt        what the referee refused from civ X's turn 7
    archive/0007.json       what the referee did, once it has done it
    joins/X.json            a seat a stranger has asked for
    joins/done/X.json       what the worker decided about it

Writes go to a temporary file and are renamed, so contents are never torn. Over
SMB the file can be briefly absent or locked during the rename, so readers retry
on `FileNotFoundError` and `PermissionError`.

The deadline is an absolute epoch time, written only by the referee.

`status` is set by the operator. A closed galaxy refuses submissions and stays
readable. `reclaimed` records seats taken back for missed turns (which turn, how
many missed) so a refusal can explain itself.
"""
import base64
import hashlib
import json
import os
import struct
import sys
import threading
import time
import urllib.parse
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'dev_tools'))

import save_parser as sp

STATE_RETRY_SECONDS = 2.0    # reader wait for a state.json being replaced


WRITE_RETRY_SECONDS = 10.0   # writer retry while a reader holds the target

# How long one `/state` answer serves `HttpTurnStore`'s state accessors. Must
# stay under `player_turn.POLL_FLOOR` so every scheduled poll reaches the
# service.
STATE_SECONDS = 1.5

# The largest a submission may decompress to. Equal to
# `referee.MAX_SUBMISSION_BYTES`, written out because `referee` imports this
# module; the two must agree.
MAX_DECODED_BYTES = 8 * 1024 * 1024

# A galaxy's status key and values. A state with no status is open.
STATUS_KEY = 'status'
OPEN, CLOSED = 'open', 'closed'

# Optional fields read by a launcher explaining a refusal.
CLOSED_REASON_KEY = 'closed_reason'
RECLAIMED_KEY = 'reclaimed'

# Optional state fields: who was seated at a boundary (written by
# `joins.commit`), and the oldest build allowed to play. Defined here so
# `firebase_store.STATE_FIELDS` can list them.
# `release/launcher.py` holds its own `MIN_BUILD_KEY`; the two must agree.
JOINED_KEY = 'joined'
MIN_BUILD_KEY = 'min_build'

# Player name rules: the engine's civ name is a 15-character ASCII buffer, and a
# folder store names files for the civ, so Windows-forbidden characters and a
# trailing full stop are refused. `release/launcher.py` `validate_player_name`
# checks the same rules.
NAME_LIMIT = 15
NAME_FORBIDDEN = '\\/:*?"<>|'


def name_problem(name) -> str:
    """Why `name` cannot be a player name, or ''."""
    if not isinstance(name, str) or not name.strip():
        return 'a player name is required'
    if name != name.strip():
        return 'a player name has no leading or trailing spaces'
    if len(name) > NAME_LIMIT:
        return f'a player name is at most {NAME_LIMIT} characters'
    if any(not (' ' <= c <= '~') for c in name):
        return 'a player name is printable ASCII only'
    if any(c in NAME_FORBIDDEN for c in name):
        return f'a player name cannot contain any of {NAME_FORBIDDEN}'
    if name.endswith('.'):
        return 'a player name cannot end in a full stop'
    return ''


class GalaxyClosed(RuntimeError):
    """A submission refused because the operator has ended this galaxy.

    Not a `ValueError`: a malformed submission is worth retrying and this one
    never is.
    """


class SubmissionTooLarge(ValueError):
    """A submission refused for expanding past `MAX_DECODED_BYTES`.

    A `ValueError` so the referee's per-civ fallback drops just that submission.
    """


def decode_capped(raw, limit: int = MAX_DECODED_BYTES) -> bytes:
    """`save_parser.decode_save`, refusing a payload that inflates past a cap.

    Decompresses incrementally so the cap applies during the read. The size
    dword is checked first but not trusted.
    """
    if isinstance(raw, str):
        raw = raw.encode('ascii')
    return inflate_capped(base64.b64decode(raw), limit)


def inflate_capped(decoded: bytes, limit: int = MAX_DECODED_BYTES) -> bytes:
    """`decode_capped` for bytes already out of base64: the size dword and the
    zlib stream, the form the Firebase store keeps."""
    if len(decoded) < 4:
        raise ValueError('a submission is at least a four-byte size header')
    expected = struct.unpack_from('<I', decoded, 0)[0]
    if expected > limit:
        raise SubmissionTooLarge(
            f'this submission says it is {expected:,} bytes decompressed, over '
            f'the {limit:,} byte limit')
    engine = zlib.decompressobj()
    blob = engine.decompress(decoded[4:], limit + 1)
    if len(blob) > limit:
        raise SubmissionTooLarge(
            f'this submission expands past the {limit:,} byte limit and was '
            f'refused while it was being read')
    if not engine.eof:
        # Under the cap but unfinished: a truncated payload. Same error as
        # `save_parser.decode_save` raises.
        raise zlib.error('incomplete or truncated stream')
    if len(blob) != expected:
        raise ValueError(f'size mismatch: header says {expected}, '
                         f'got {len(blob)}')
    return blob


def load_capped(path: str, limit: int = MAX_DECODED_BYTES) -> bytes:
    """`save_parser.load_any` for a submission, with the cap applied.

    The file is read with one byte of headroom, never whole, so an oversized
    raw blob is refused without being held.
    """
    with open(path, 'rb') as f:
        raw = f.read(limit + 1)
        over = bool(f.read(1)) or len(raw) > limit
    if raw[:4] in sp.KNOWN_TAGS:
        if over:
            raise SubmissionTooLarge(
                f'this submission is over the {limit:,} byte limit')
        return raw
    if over:
        # A wire form this long is refused without inflating it.
        raise SubmissionTooLarge(
            f'this submission is over the {limit:,} byte limit before it has '
            f'been decompressed')
    return decode_capped(raw.strip(), limit)


def status_of(state) -> str:
    """`open` or `closed` for a galaxy's state. Absent means open."""
    value = (state or {}).get(STATUS_KEY)
    return CLOSED if value == CLOSED else OPEN


def reclaimed_of(state, civ: str = None):
    """Seats taken back, or one of them, or None when that seat was not.

    Keyed by civ name, holding `{turn, missed, at}`. A caller asking about one
    name gets only that record, so it cannot enumerate other players.
    """
    taken = (state or {}).get(RECLAIMED_KEY) or {}
    if not isinstance(taken, dict):
        return None if civ else {}
    return taken.get(civ) if civ else dict(taken)


# A join is a request the launcher lodges; the worker (`server/joins.py`)
# applies it at the next turn boundary. The request shape is defined here so
# all three stores read the same request.

# Directory-store folders for requests and answers. `release/launcher.py` holds
# its own `JOIN_DIR`; the two must agree.
JOIN_DIR = 'joins'
JOIN_DONE_DIR = 'done'

# The longest request key, so a key is always a valid file name or document id.
MAX_JOIN_KEY = 200


def join_key(req) -> str:
    """What one request is filed under: the uid, or the name when there is none.

    All three stores and the relay use this to decide which request a second
    Join replaces.
    """
    uid = req.get('uid') if isinstance(req, dict) else None
    key = uid if isinstance(uid, str) and uid.strip() else (
        req.get('name') if isinstance(req, dict) else None)
    return str(key or '').strip()


def join_problem(rec) -> str:
    """Why this is not a join request, or ''.

    Stores set an invalid request aside without raising, so one cannot stop a
    turn. All three stores use this rule.
    """
    if not isinstance(rec, dict):
        return 'the record does not hold a join request'
    name = rec.get('name')
    if not isinstance(name, str) or not name.strip():
        return 'the request names no player'
    key = join_key(rec)
    if not key:
        return 'the request has nothing to file it under'
    if len(key) > MAX_JOIN_KEY:
        return (f'the request is filed under more than {MAX_JOIN_KEY} '
                f'characters')
    if any(c in key for c in '/\\\0') or key in ('.', '..'):
        # A key must be usable as a file name in a directory store, so a
        # galaxy can be copied between store kinds.
        return 'the request is filed under a name that cannot be a file'
    return ''


def join_record(req) -> dict:
    """A request as it is stored: what the launcher wrote and nothing else.

    Drops `key` and `path`, which stores add when reading a request back.
    """
    return {k: v for k, v in dict(req).items() if k not in ('key', 'path')}


def join_order(rec):
    """The sort a waiting list is in: when the player asked, then their key.

    Oldest first, ties broken by key, so every store gives the same order.
    """
    at = rec.get('requested_at')
    ok = isinstance(at, (int, float)) and not isinstance(at, bool)
    return (at if ok else 0.0, str(rec.get('key') or ''))


def skipped(log, what, why: str) -> None:
    """Log that something in `joins/` was not a request and was left alone."""
    if log:
        log(f'  joins: ignoring {what}, {why}')


def _atomic_write(path: str, data: bytes) -> None:
    """Write, then replace, retrying while a reader holds the target.

    Over SMB a reader holding the file open makes `os.replace` fail with
    WinError 5. The write is idempotent, so it is retried for up to
    `WRITE_RETRY_SECONDS`.
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

    def exists(self) -> bool:
        return os.path.exists(self.state_path)

    def state(self) -> dict:
        """The galaxy's clock and roster, tolerating a writer mid-replace.

        Over SMB a replace can leave `state.json` briefly absent, so the read
        retries for up to `STATE_RETRY_SECONDS` before raising.
        """
        deadline = time.time() + STATE_RETRY_SECONDS
        while True:
            try:
                with open(self.state_path, encoding='utf-8') as f:
                    return json.load(f)
            except (FileNotFoundError, PermissionError, ValueError):
                # Windows reports opening a delete-pending file as
                # PermissionError, the usual outcome of this race. ValueError
                # is a half-written file.
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

    def update_state(self, fields: dict) -> dict:
        """Merge fields into the state and return what it now holds.

        Read, merge, write, without a lock: a concurrent publish can lose one
        of the two writes.
        """
        state = self.state() if self.exists() else {}
        state.update(fields)
        self._put_state(state)
        return state

    def status(self) -> str:
        return status_of(self.state())

    def is_closed(self) -> bool:
        return self.status() == CLOSED

    def closed_reason(self):
        return self.state().get(CLOSED_REASON_KEY)

    def close(self, reason: str = None) -> dict:
        """End this galaxy. Submissions stop; everything else still answers."""
        fields = {STATUS_KEY: CLOSED}
        if reason:
            fields[CLOSED_REASON_KEY] = reason
        return self.update_state(fields)

    def reopen(self) -> dict:
        """Undo a close, clearing the closed reason."""
        state = self.state()
        state[STATUS_KEY] = OPEN
        state.pop(CLOSED_REASON_KEY, None)
        self._put_state(state)
        return state

    def reclaimed(self, civ: str = None):
        return reclaimed_of(self.state(), civ)

    def start(self, blob: bytes, civs, turn_seconds: int = 3600,
              turn: int = None) -> int:
        """Publish the first turn and start the clock.

        `turn` defaults to the turn number the blob carries.
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

    def submit(self, civ: str, turn: int, blob: bytes) -> str:
        """Hand a player's state back. A later submission replaces an earlier
        one. Raises `GalaxyClosed` on a closed galaxy.
        """
        if self.is_closed():
            raise GalaxyClosed(f'this galaxy is closed and is not taking '
                               f'submissions ({self.root})')
        path = self.submission_path(civ, turn)
        _atomic_write(path, sp.encode_save(blob))
        return path

    def has_submitted(self, civ: str, turn: int) -> bool:
        return os.path.exists(self.submission_path(civ, turn))

    def submission(self, civ: str, turn: int):
        """What one civ handed back for this turn, or None when they have not.

        Only a missing file answers None. A present but unreadable file raises,
        so a caller cannot mistake it for no submission and overwrite it.
        """
        try:
            return load_capped(self.submission_path(civ, turn))
        except FileNotFoundError:
            return None

    def submitted_civs(self, turn: int) -> list:
        """Who has handed something back, by name and without the blobs."""
        d = self.submission_dir(turn)
        if not os.path.isdir(d):
            return []
        return sorted(n[:-4] for n in os.listdir(d) if n.endswith('.b64'))

    def submissions(self, turn: int) -> dict:
        """{civ: blob} for everyone who handed something back for this turn.
        The referee's view at the turn boundary."""
        out = {}
        d = self.submission_dir(turn)
        if not os.path.isdir(d):
            return out
        for name in sorted(os.listdir(d)):
            if not name.endswith('.b64'):
                continue
            out[name[:-4]] = load_capped(os.path.join(d, name))
        return out

    def archive(self, turn: int, record: dict) -> None:
        _atomic_write(self.archive_path(turn),
                      json.dumps(record, indent=2).encode('utf-8'))

    def archive_record(self, turn: int):
        """What the referee recorded for a turn, or None."""
        path = self.archive_path(turn)
        if not os.path.exists(path):
            return None
        with open(path, encoding='utf-8') as f:
            return json.load(f)

    def put_note(self, civ: str, turn: int, lines) -> None:
        """Leave one player the referee's reasons for refusing part of a turn.
        """
        _atomic_write(self.note_path(civ, turn),
                      '\n'.join(lines).encode('utf-8'))

    def note(self, civ: str, turn: int) -> list:
        """Those reasons, or [] when the turn was taken whole."""
        try:
            with open(self.note_path(civ, turn), encoding='utf-8') as f:
                return [line for line in f.read().split('\n') if line]
        except (FileNotFoundError, PermissionError, UnicodeDecodeError):
            return []

    def join_dir(self) -> str:
        return os.path.join(self.root, JOIN_DIR)

    def join_path(self, key: str) -> str:
        return os.path.join(self.join_dir(), f'{key}.json')

    def join_answer_path(self, key: str) -> str:
        return os.path.join(self.join_dir(), JOIN_DONE_DIR, f'{key}.json')

    def request_join(self, req: dict) -> str:
        """Lodge a join request, and say where it landed.

        Filed under `join_key`, so lodging twice replaces.
        """
        problem = join_problem(req)
        if problem:
            raise ValueError(problem)
        path = self.join_path(join_key(req))
        _atomic_write(path,
                      json.dumps(join_record(req), indent=2).encode('utf-8'))
        return path

    def join_requests(self, log=None) -> list:
        """Every request waiting on this galaxy, oldest first.

        A file in `joins/` that is not a request is reported to `log` and set
        aside, never raised.
        """
        where = self.join_dir()
        if not os.path.isdir(where):
            return []
        out = []
        for entry in sorted(os.listdir(where)):
            if not entry.endswith('.json'):
                continue
            path = os.path.join(where, entry)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, encoding='utf-8') as f:
                    rec = json.load(f)
            except (OSError, ValueError) as exc:
                skipped(log, entry, f'{type(exc).__name__}: {exc}')
                continue
            problem = join_problem(rec)
            if problem:
                skipped(log, entry, problem)
                continue
            rec = join_record(rec)
            rec['key'] = entry[:-len('.json')]
            out.append(rec)
        return sorted(out, key=join_order)

    def join_request(self, key: str):
        """The one request filed under this key, or None while there is none."""
        path = self.join_path(key)
        try:
            with open(path, encoding='utf-8') as f:
                rec = json.load(f)
        except (FileNotFoundError, PermissionError, ValueError):
            return None
        if join_problem(rec):
            return None
        rec = join_record(rec)
        rec['key'] = key
        return rec

    def answer_join(self, key: str, answer: dict) -> None:
        """Consume one request and record what was decided about it.

        The answer is written to `joins/done/` before the request is removed,
        so an interrupted worker never loses an answer.
        """
        _atomic_write(self.join_answer_path(key),
                      json.dumps(dict(answer), indent=2).encode('utf-8'))
        try:
            os.remove(self.join_path(key))
        except FileNotFoundError:
            pass

    def join_answer(self, key: str):
        """What was decided about one request, or None while it is waiting.
        Keyed, so a player cannot list other players' requests."""
        try:
            with open(self.join_answer_path(key), encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, PermissionError, ValueError):
            return None


def _same_host(a: str, b: str) -> bool:
    pa, pb = urllib.parse.urlparse(a), urllib.parse.urlparse(b)
    return (pa.scheme, pa.netloc) == (pb.scheme, pb.netloc)


# Hosts other than the store's own that a ticket or redirect may name, https
# only. Anything else is refused.
TRUSTED_BLOB_HOSTS = ('storage.googleapis.com',)


class UntrustedURL(Exception):
    """A ticket or redirect named a host this store does not talk to."""


def _allowed_url(url: str, base: str) -> bool:
    """Whether this store, based at `base`, may send a request to `url`."""
    if _same_host(url, base):
        return True
    p = urllib.parse.urlparse(url)
    return p.scheme == 'https' and (p.hostname or '') in TRUSTED_BLOB_HOSTS


def _drop_auth_on_redirect(base: str = None):
    """A redirect handler that does not carry the caller's token off-site.

    The `Authorization` header is dropped when a redirect leaves the original
    host: Cloud Storage answers 401 to a signed URL that also carries a bearer
    token. With `base`, a redirect to a host `_allowed_url` refuses raises
    `UntrustedURL`.
    """
    import urllib.request

    class Handler(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if base is not None and not _allowed_url(newurl, base):
                raise UntrustedURL(f'refused a redirect to {newurl}')
            new = super().redirect_request(req, fp, code, msg, headers, newurl)
            if new is not None and not _same_host(req.full_url, newurl):
                new.headers = {k: v for k, v in new.headers.items()
                               if k.lower() != 'authorization'}
            return new

    return Handler


class HttpTurnStore:
    """The same interface, over `turn_server.py` or the relay function.

    Blobs cross this interface as raw decompressed bytes. `_blob` decodes
    either wire form (raw or base64) by the rule `save_parser.load_any` uses.

    `token` is a zero-argument callable returning a Firebase ID token, or None
    for no identity, called per request so the holder can refresh it.

    One `/state` answer serves every state accessor asked within
    `state_seconds`. Any write by this store discards it, so a caller sees its
    own writes at once; writes from elsewhere are seen at most `state_seconds`
    late.
    """

    def __init__(self, base: str, timeout: float = 30.0, token=None,
                 state_seconds: float = None):
        self.base = base.rstrip('/')
        self.timeout = timeout
        self.token = token
        self._opener = None
        self.state_seconds = (STATE_SECONDS if state_seconds is None
                              else float(state_seconds))
        # (monotonic time it arrived, the raw body or None for a 404), and a
        # count of writes so an answer fetched across one is not kept.
        self._held = None
        self._writes = 0
        self._held_lock = threading.Lock()
        # (civ, turn) -> (tag, blob) for the submission this store last sent
        # or was sent, so asking for it again can be answered 304 without a
        # body. See `submission`.
        self._known = {}

    def _url(self, path: str) -> str:
        """A ticket may name an absolute URL or a path back on this service.

        An absolute URL must be this service or a trusted blob host over https
        (`_allowed_url`); anything else raises `UntrustedURL`.
        """
        if path.startswith('http://') or path.startswith('https://'):
            if not _allowed_url(path, self.base):
                raise UntrustedURL(f'refused a ticket for {path}')
            return path
        return self.base + path

    def _headers(self, url: str) -> dict:
        """The Authorization header, sent only to this store's own host."""
        if self.token is None or not _same_host(url, self.base):
            return {}
        tok = self.token()
        return {'Authorization': f'Bearer {tok}'} if tok else {}

    def _open(self, req):
        import urllib.request
        if self._opener is None:
            self._opener = urllib.request.build_opener(
                _drop_auth_on_redirect(self.base))
        return self._opener.open(req, timeout=self.timeout)

    def _get(self, path, want_json=True, headers=None, answer=None):
        """One GET, None for a 404.

        `answer`, when given, is a dict that receives the response's `status`
        and `etag`, which is how `submission` tells a 304 from a body.
        """
        import urllib.error
        import urllib.request
        url = self._url(path)
        req = urllib.request.Request(url, method='GET')
        for k, v in dict(headers or {}, **self._headers(url)).items():
            req.add_header(k, v)
        try:
            with self._open(req) as r:
                body = r.read()
                if answer is not None:
                    answer['status'] = r.status
                    answer['etag'] = r.headers.get('ETag')
        except urllib.error.HTTPError as exc:
            if exc.code == 304 and answer is not None:
                answer['status'] = 304
                answer['etag'] = exc.headers.get('ETag')
                return None
            if exc.code == 404:
                return None
            raise
        return json.loads(body) if want_json else body

    def get_json(self, path):
        """One JSON route on this service, or None when it answers 404.
        Public so a galaxy directory can share this store's identity."""
        return self._get(path)

    def _blob(self, path, limit: int = None):
        """A blob route's bytes, in whichever form the far end serves them.

        None for a 404. `limit` caps decompression; it is passed for
        submissions (player uploads) and not for turns.
        """
        return self._decode_blob(self._get(path, want_json=False), limit)

    @staticmethod
    def _decode_blob(data, limit: int = None):
        """`_blob`'s decoding half, for a caller that made the request itself.
        """
        if data is None or data[:4] in sp.KNOWN_TAGS:
            if limit is not None and data is not None and len(data) > limit:
                raise SubmissionTooLarge(
                    f'this submission is over the {limit:,} byte limit')
            return data
        if limit is None:
            return sp.decode_save(data.strip())
        return decode_capped(data.strip(), limit)

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
        # The held state is dropped even on failure, since the write may have
        # landed.
        try:
            with self._open(req) as r:
                out = r.read()
        finally:
            self._forget_state()
        return json.loads(out) if want_json and out else out

    def _post(self, path, body=b'', want_json=True):
        return self._send(path, body, 'POST', want_json=want_json)

    def _forget_state(self):
        with self._held_lock:
            self._held = None
            self._writes += 1

    def _state_body(self):
        """The `/state` body, or None for a 404, fetched at most once a window.

        Raw bytes are kept so each caller parses its own copy. An answer whose
        request overlapped a write is returned but not kept.
        """
        now = time.monotonic()
        with self._held_lock:
            held, writes = self._held, self._writes
        if held is not None and now - held[0] < self.state_seconds:
            return held[1]
        body = self._get('/state', want_json=False)
        if self.state_seconds > 0:
            with self._held_lock:
                if self._writes == writes:
                    self._held = (time.monotonic(), body)
        return body

    def exists(self) -> bool:
        return self._state_body() is not None

    def state(self) -> dict:
        body = self._state_body()
        if body is None:
            raise FileNotFoundError(f'no galaxy at {self.base}')
        return json.loads(body)

    def current(self):
        s = self.state()
        return s['turn'], s['deadline']

    def seconds_left(self) -> float:
        return self.state()['deadline'] - time.time()

    def civs(self) -> list:
        return list(self.state().get('civs', []))

    def update_state(self, fields: dict) -> dict:
        """Refused: state writes are the operator's, made on the store itself.
        Neither `turn_server.py` nor the relay offers a route for them."""
        raise NotImplementedError(
            'a galaxy is closed where it lives, not over the player interface')

    def status(self) -> str:
        return status_of(self.state())

    def is_closed(self) -> bool:
        return self.status() == CLOSED

    def closed_reason(self):
        return self.state().get(CLOSED_REASON_KEY)

    def close(self, reason: str = None) -> dict:
        return self.update_state({STATUS_KEY: CLOSED})

    def reopen(self) -> dict:
        return self.update_state({STATUS_KEY: OPEN})

    def reclaimed(self, civ: str = None):
        return reclaimed_of(self.state(), civ)

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

    def submit(self, civ: str, turn: int, blob: bytes) -> str:
        """Hand a player's state back, by whichever route the far end offers.

        `/upload/...` returns a ticket naming the URL, method, encoding and an
        optional commit route. A 404 means no ticket route, and the bytes are
        posted directly. A 403 raises. A closed galaxy raises `GalaxyClosed`
        here, as in the other stores.
        """
        if self.is_closed():
            raise GalaxyClosed(f'this galaxy is closed and is not taking '
                               f'submissions ({self.base})')
        path = f'/submission/{turn}/{urllib.parse.quote(civ)}'
        self._known.pop((civ, turn), None)
        ticket = self._get(f'/upload{path}')
        if ticket is None:
            self._post(path, blob)
        else:
            body = (sp.encode_save(blob) if ticket.get('encoding') == 'b64'
                    else blob)
            self._send(ticket['url'], body,
                       method=ticket.get('method', 'PUT'),
                       headers=ticket.get('headers'), want_json=False)
            if ticket.get('commit'):
                self._post(ticket['commit'], b'')
        self._known[(civ, turn)] = (submission_tag(blob), blob)
        return f'{self.base}/submission/{turn}/{civ}'

    def has_submitted(self, civ: str, turn: int) -> bool:
        return civ in self.submitted_civs(turn)

    def submitted_civs(self, turn: int) -> list:
        """The names, from `/submissions/<n>`."""
        return sorted(self._get(f'/submissions/{turn}') or [])

    def submission(self, civ: str, turn: int):
        """One civ's submission, or None when there is not one.

        A submission this store already holds is requested with
        `If-None-Match` and its tag; a 304 returns the held bytes. A service
        that ignores the header sends the body.
        """
        path = f'/submission/{turn}/{urllib.parse.quote(civ)}'
        known = self._known.get((civ, turn))
        headers = {'If-None-Match': f'"{known[0]}"'} if known else None
        answer = {}
        data = self._get(path, want_json=False, headers=headers,
                         answer=answer)
        if answer.get('status') == 304 and known:
            return known[1]
        if data is None:
            self._known.pop((civ, turn), None)
            return None
        blob = self._decode_blob(data, limit=MAX_DECODED_BYTES)
        tag = (answer.get('etag') or '').strip('"')
        if tag and tag == submission_tag(blob):
            self._known[(civ, turn)] = (tag, blob)
        return blob

    def submissions(self, turn: int) -> dict:
        out = {}
        for civ in self.submitted_civs(turn):
            blob = self.submission(civ, turn)
            if blob is not None:
                out[civ] = blob
        return out

    def archive(self, turn: int, record: dict) -> None:
        self._post(f'/archive/{turn}',
                   json.dumps(record).encode('utf-8'), want_json=False)

    def archive_record(self, turn: int):
        return self._get(f'/archive/{turn}')

    def put_note(self, civ: str, turn: int, lines) -> None:
        self._post(f'/note/{turn}/{urllib.parse.quote(civ)}',
                   '\n'.join(lines).encode('utf-8'), want_json=False)

    def note(self, civ: str, turn: int) -> list:
        body = self._get(f'/note/{turn}/{urllib.parse.quote(civ)}',
                         want_json=False)
        if not body:
            return []
        return [line for line in body.decode('utf-8').split('\n') if line]

    def request_join(self, req: dict) -> str:
        """Lodge a join request through whichever service is answering.

        The only write a caller with no seat may make. `turn_server.py` accepts
        it from anyone who can reach the port; the relay verifies a Firebase ID
        token and overwrites the uid with the token's.
        """
        problem = join_problem(req)
        if problem:
            raise ValueError(problem)
        out = self._post('/join',
                         json.dumps(join_record(req)).encode('utf-8'))
        return (out or {}).get('where') or f'{self.base}/join'

    def join_requests(self, log=None) -> list:
        """Every request waiting on this galaxy, oldest first.

        `log` is unused; the service reports bad requests itself. Sorted here
        so the order matches the other stores.
        """
        rows = [r for r in (self._get('/joins') or []) if isinstance(r, dict)]
        return sorted((r for r in rows if not join_problem(r)),
                      key=join_order)

    def join_request(self, key: str):
        """The one request filed under this key, or None while there is none."""
        return self._get(f'/join/{urllib.parse.quote(key, safe="")}')

    def answer_join(self, key: str, answer: dict) -> None:
        self._post(f'/join/{urllib.parse.quote(key, safe="")}',
                   json.dumps(dict(answer)).encode('utf-8'), want_json=False)

    def join_answer(self, key: str):
        """What was decided about one request, or None while it is waiting."""
        return self._get(
            f'/join/{urllib.parse.quote(key, safe="")}/answer')


def open_store(spec: str, token=None):
    """A store from a directory path, a base URL or a Firebase project.

        some/dir                            a directory, or a share
        http://host:8899                    turn_server.py on another host
        https://host/relay/sandbox          the relay function in front of it
        firebase://cs-resurgence/sandbox    Firestore and Cloud Storage

    `token` is a zero-argument source of a Firebase ID token, used by the HTTP
    store only and ignored for other specs. `firebase_store` is imported lazily
    because the Google client libraries are slow to import.
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


def submission_tag(blob: bytes) -> str:
    """The tag a stored submission is known by: SHA-256 of the decompressed
    bytes, hex. The relay serves it as the ETag of `/submission/<n>/<civ>`,
    and `HttpTurnStore` sends it back as `If-None-Match`."""
    return hashlib.sha256(blob).hexdigest()


def check_save(blob: bytes, turn: int = None) -> int:
    """Refuse bytes that are not a save of this galaxy's current turn.

    Returns the turn the blob carries; raises `ValueError` naming the problem.
    Checks only that the section tree parses and the `GLOB` turn matches;
    order legality is `merge_orders`' job.
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
