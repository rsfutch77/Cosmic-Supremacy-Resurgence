"""
firebase_store.py , a galaxy's turns and submissions, in Firebase
=================================================================
    from turn_store import open_store
    store = open_store('firebase://cs-resurgence/sandbox')

    store.start(blob, civs=['DemoPlayer'], turn_seconds=14400)
    turn, deadline = store.current()
    blob = store.turn_blob(turn)
    store.submit('DemoPlayer', turn, mine)

The third implementation of the `turn_store` interface, alongside `TurnStore`
(a directory) and `HttpTurnStore` (a service). It exists for one property: the
referee opens outbound connections only, so a beta played by strangers never
learns the operator's home address and the operator opens no inbound port.

Two services, because they fail in different directions
-------------------------------------------------------
A Firestore document is capped at 1 MiB and a galaxy blob has no ceiling worth
betting on: today's is a few tens of kilobytes and the format carries no rule
that keeps it there. So blobs and submissions are Cloud Storage objects, and
the clock, the roster and the archive are Firestore documents, which is what
Firestore is good at: one small document, read often, replaced atomically.

    Firestore
      beta/<galaxy>                     the clock, the roster, the hash, and
                                        the directory's row for this galaxy
      beta/<galaxy>/archive/<turn>      what the referee recorded for a turn

    Cloud Storage
      beta/<galaxy>/turns/0007.b64            the state for turn 7
      beta/<galaxy>/submissions/0007/X.b64    what civ X handed back
      beta/<galaxy>/notes/0007/X.txt          what the referee refused from X

Everything sits under one prefix so it cannot collide with the website the same
Firebase project hosts.

**Anything keyed by a civ name is a Storage object and not a Firestore field.**
A civ name is a username a player typed. Firestore restricts what a document id
and a field name may contain, and an archive record keyed by civ name would
need escaping that nothing else in the tree does. A Cloud Storage object name
takes arbitrary UTF-8, so submissions and notes keep the directory store's own
layout, and the archive record is stored as one JSON string rather than as a
map, which also keeps it identical to what `referee` wrote.

Objects hold the same base64 wire bytes the directory store writes to disk, so
an object downloaded here and a file copied out of a directory store decode
through the same `save_parser.decode_save`, and a galaxy moves between the two
by copying. Base64 costs a third more egress than the compressed bytes would;
at tens of kilobytes and six turns a day that is not worth a second encoding in
the tree.

What is deliberately absent
---------------------------
`TurnStore.state` retries for two seconds because `os.replace` is not atomic
over SMB and a reader on another machine can catch `state.json` briefly absent.
There is no equivalent here and none is needed: a Firestore document write is
atomic and a read returns a consistent snapshot, so a reader either sees the
galaxy as it was or as it now is, never as missing. See `state` below.

The deadline is an absolute epoch time written by the referee, for the reason
the directory store gives: a launcher that was closed and reopened, and a
machine whose clock differs a little, still agree about when the turn is due.
Firestore's own server timestamp is deliberately not used, because the deadline
would then mean something different on two stores of the same galaxy.

Credentials come from Application Default Credentials, so nothing in the tree
holds a key. Against the Firebase local emulator suite, the two client
libraries read `FIRESTORE_EMULATOR_HOST` and `STORAGE_EMULATOR_HOST`
themselves, so this module needs no emulator branch.
"""
import json
import os
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'dev_tools'))

import save_parser as sp

# The prefix everything this module writes lives under, in both services. The
# project also hosts a public website, and a name that could be mistaken for
# the website's is not worth the minute it saves.
PREFIX = 'beta'

# The fields of the galaxy document that belong to the store. The same document
# carries the directory's row (name, status, created), which `state` does not
# return, so that no caller above the store seam starts depending on a field
# only one of the three implementations has.
STATE_FIELDS = ('turn', 'deadline', 'turn_seconds', 'civs', 'hash')

_BUCKETS = {}


def parse_spec(spec: str):
    """(project, galaxy, bucket, prefix) from `firebase://project/galaxy`.

    The optional query takes `bucket` and `prefix`, which exist for tests and
    for a second galaxy tree in one project. Neither belongs in a config file a
    player edits.
    """
    if not spec.startswith('firebase://'):
        raise ValueError(f'not a firebase store spec: {spec!r}')
    url = urllib.parse.urlparse(spec)
    parts = [p for p in url.path.split('/') if p]
    q = urllib.parse.parse_qs(url.query)
    if not url.netloc:
        raise ValueError(f'no project in {spec!r}')
    return (url.netloc,
            urllib.parse.unquote(parts[0]) if parts else None,
            q.get('bucket', [None])[0],
            q.get('prefix', [PREFIX])[0])


def default_bucket(project: str, client) -> str:
    """The project's own Storage bucket, whichever name it was given.

    Firebase named default buckets `<project>.appspot.com` until October 2024
    and `<project>.firebasestorage.app` after it, and a project can only be
    asked which it has. This costs one metadata call the first time a store is
    opened without an explicit bucket, and the answer is kept for the process.
    """
    if project in _BUCKETS:
        return _BUCKETS[project]
    env = os.environ.get('FIREBASE_STORAGE_BUCKET')
    if env:
        _BUCKETS[project] = env
        return env
    names = [f'{project}.firebasestorage.app', f'{project}.appspot.com']
    for name in names:
        try:
            if client.bucket(name).exists():
                _BUCKETS[project] = name
                return name
        except Exception:                                   # noqa: BLE001
            # A bucket this account may not stat is not the bucket wanted, and
            # the other name may still answer.
            continue
    # Nothing answered. Return the current default name rather than raising, so
    # the failure arrives at the operation that needed it and names the object.
    return names[0]


class FirebaseTurnStore:
    """One galaxy's turns, submissions and clock, in Firestore and Storage."""

    def __init__(self, project: str, galaxy: str, bucket: str = None,
                 prefix: str = PREFIX):
        if not galaxy:
            raise ValueError('a firebase store needs a galaxy name')
        self.project = project
        self.galaxy = galaxy
        self.prefix = prefix.strip('/')
        self._bucket_name = bucket
        self._fs = None
        self._gcs = None
        self._bucket = None

    def __repr__(self):
        return f'FirebaseTurnStore({self.project}:{self.prefix}/{self.galaxy})'

    # ── clients ──────────────────────────────────────────────────────────────
    # Built on first use rather than in __init__. Importing the google client
    # libraries costs about a second, most of it grpc, and `open_store` is
    # called by tools that then go on to talk to a directory.
    @property
    def fs(self):
        if self._fs is None:
            from google.cloud import firestore
            self._fs = firestore.Client(project=self.project)
        return self._fs

    @property
    def gcs(self):
        if self._gcs is None:
            from google.cloud import storage
            self._gcs = storage.Client(project=self.project)
        return self._gcs

    @property
    def bucket(self):
        if self._bucket is None:
            name = self._bucket_name or default_bucket(self.project, self.gcs)
            self._bucket = self.gcs.bucket(name)
        return self._bucket

    # ── paths ────────────────────────────────────────────────────────────────
    @property
    def doc(self):
        return self.fs.collection(self.prefix).document(self.galaxy)

    def archive_doc(self, turn: int):
        return self.doc.collection('archive').document(f'{turn:04d}')

    def _object(self, *parts) -> str:
        return '/'.join((self.prefix, self.galaxy) + parts)

    def turn_object(self, turn: int) -> str:
        return self._object('turns', f'{turn:04d}.b64')

    def submission_prefix(self, turn: int) -> str:
        return self._object('submissions', f'{turn:04d}') + '/'

    def submission_object(self, civ: str, turn: int) -> str:
        return self._object('submissions', f'{turn:04d}', f'{civ}.b64')

    def note_object(self, civ: str, turn: int) -> str:
        return self._object('notes', f'{turn:04d}', f'{civ}.txt')

    # ── storage plumbing ─────────────────────────────────────────────────────
    def _put(self, name: str, data: bytes, content_type: str) -> None:
        self.bucket.blob(name).upload_from_string(data,
                                                  content_type=content_type)

    def _get(self, name: str):
        """The object's bytes, or None when it is not there.

        Absence is an ordinary answer for most of what this asks for: a turn
        nobody has published, a submission nobody has made, a note for a turn
        nothing was refused on. The callers that owe an exception raise it
        themselves, so their message reads like the directory store's.
        """
        from google.api_core import exceptions
        try:
            return self.bucket.blob(name).download_as_bytes()
        except exceptions.NotFound:
            return None

    def _has(self, name: str) -> bool:
        return self.bucket.blob(name).exists()

    # ── state ────────────────────────────────────────────────────────────────
    def exists(self) -> bool:
        return self.doc.get().exists

    def state(self) -> dict:
        """The galaxy's clock and roster.

        The directory store retries this for two seconds, because `os.replace`
        is not atomic over SMB and a reader on another machine can see
        `state.json` briefly absent while the referee republishes it. A
        Firestore document write is atomic and a read returns a consistent
        snapshot of the document, so a reader mid-publish sees the turn before
        or the turn after and never nothing. The retry is absent here on
        purpose rather than by oversight.
        """
        snap = self.doc.get()
        if not snap.exists:
            raise FileNotFoundError(
                f'no galaxy {self.galaxy} in {self.project}')
        data = snap.to_dict() or {}
        return {k: data[k] for k in STATE_FIELDS if k in data}

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
        self._put(self.turn_object(turn), sp.encode_save(blob), 'text/plain')
        self.doc.set({
            'turn': int(turn),
            'deadline': time.time() + turn_seconds,
            'turn_seconds': int(turn_seconds),
            'civs': list(civs),
            'hash': canonical.canonical_hash(blob),
            'created': time.time(),
        }, merge=True)
        return turn

    def turn_blob(self, turn: int) -> bytes:
        data = self._get(self.turn_object(turn))
        if data is None:
            raise FileNotFoundError(f'no turn {turn} in {self.galaxy}')
        return sp.decode_save(data)

    def has_turn(self, turn: int) -> bool:
        return self._has(self.turn_object(turn))

    def publish(self, turn: int, blob: bytes, turn_seconds: int = None) -> None:
        """The referee's move: a new turn exists, and the clock restarts.

        A merge rather than a whole-document replace, so the galaxy's name and
        status survive a publish. The directory store reads the state and
        writes it back whole because a file is all or nothing; a Firestore
        write names the fields it changes, which also means a referee cannot
        clobber a status the operator set during the turn.
        """
        import canonical
        seconds = turn_seconds or self.state()['turn_seconds']
        self._put(self.turn_object(turn), sp.encode_save(blob), 'text/plain')
        self.doc.set({
            'turn': int(turn),
            'deadline': time.time() + seconds,
            'turn_seconds': int(seconds),
            'hash': canonical.canonical_hash(blob),
        }, merge=True)

    # ── submissions ──────────────────────────────────────────────────────────
    def submit(self, civ: str, turn: int, blob: bytes) -> str:
        """Hand a player's state back. Writing twice replaces, which is right:
        a player may save several times in a turn and the last one is their
        intent."""
        name = self.submission_object(civ, turn)
        self._put(name, sp.encode_save(blob), 'text/plain')
        return f'gs://{self.bucket.name}/{name}'

    def has_submitted(self, civ: str, turn: int) -> bool:
        return self._has(self.submission_object(civ, turn))

    def submission(self, civ: str, turn: int):
        """What one civ handed back for this turn, or None when they have not.

        One object named outright, so this is one Class B operation and it
        touches nothing but that civ's own object. That is what makes it
        survivable on the path a launcher polls, and it is also what lets the
        storage rule scope a read to the player it belongs to: a listing under
        the turn's prefix could not be granted without granting every player's
        orders with it.
        """
        data = self._get(self.submission_object(civ, turn))
        if data is None:
            return None
        return sp.decode_save(data)

    def submissions(self, turn: int) -> dict:
        """{civ: blob} for everyone who handed something back for this turn.

        Listed from Storage rather than from an index in Firestore. An index
        would be a second thing to keep true, and this is not on the path that
        gets polled: the referee reads it once a turn, while a launcher asks
        `has_submitted` or `submission` about one civ.
        """
        out = {}
        start = self.submission_prefix(turn)
        for blob in self.gcs.list_blobs(self.bucket, prefix=start):
            name = blob.name[len(start):]
            if not name.endswith('.b64') or '/' in name:
                continue
            out[name[:-4]] = sp.decode_save(blob.download_as_bytes())
        return dict(sorted(out.items()))

    # ── archive ──────────────────────────────────────────────────────────────
    def archive(self, turn: int, record: dict) -> None:
        """Record what the referee did, as one JSON string.

        The record is keyed by civ name in two places, `refused` and
        `hash_submissions`, and a Firestore map key is not free to be any
        string a player typed. Holding the record as JSON keeps it exactly what
        `referee` wrote, and nothing queries an archive by field.
        """
        self.archive_doc(turn).set({
            'turn': int(turn),
            'json': json.dumps(record),
        })

    def archive_record(self, turn: int):
        """What the referee recorded for a turn, or None."""
        snap = self.archive_doc(turn).get()
        if not snap.exists:
            return None
        return json.loads((snap.to_dict() or {}).get('json') or 'null')

    # ── notes ────────────────────────────────────────────────────────────────
    def put_note(self, civ: str, turn: int, lines) -> None:
        """Leave one player the referee's reasons for refusing part of a turn.
        """
        self._put(self.note_object(civ, turn),
                  '\n'.join(lines).encode('utf-8'),
                  'text/plain; charset=utf-8')

    def note(self, civ: str, turn: int) -> list:
        """Those reasons, or [] when the turn was taken whole."""
        data = self._get(self.note_object(civ, turn))
        if not data:
            return []
        return [line for line in data.decode('utf-8').split('\n') if line]

    # ── housekeeping ─────────────────────────────────────────────────────────
    def delete_everything(self) -> int:
        """Remove this galaxy from both services, and say how many things went.

        For tests, and for a smoke check against the live project which has to
        leave nothing behind. Not part of the store interface: the referee
        never deletes, and neither does anything a player can reach.
        """
        n = 0
        for blob in self.gcs.list_blobs(self.bucket,
                                        prefix=self._object() + '/'):
            blob.delete()
            n += 1
        for snap in self.doc.collection('archive').stream():
            snap.reference.delete()
            n += 1
        if self.doc.get().exists:
            self.doc.delete()
            n += 1
        return n


def turn_of(blob: bytes) -> int:
    """The turn number a blob carries. One definition, in `turn_store`."""
    import turn_store
    return turn_store.turn_of(blob)


def open_firebase_store(spec: str) -> FirebaseTurnStore:
    project, galaxy, bucket, prefix = parse_spec(spec)
    return FirebaseTurnStore(project, galaxy, bucket=bucket, prefix=prefix)
