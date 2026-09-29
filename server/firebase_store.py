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
that keeps it there. So turn blobs are Cloud Storage objects, and the clock,
the roster and the archive are Firestore documents, which is what Firestore is
good at: one small document, read often, replaced atomically.

**A submission is a Firestore document**, since H13. It is a player's upload,
the relay caps it well under the document ceiling before it is stored, and it
is written every time a player's state changes during a turn. As a Cloud
Storage object each of those writes was a Class A operation against 5,000 a
month for the whole project, which capped the beta at about 27 players; as a
document it is one write against 20,000 a day. The document holds the
compressed bytes rather than the base64 wire form, because base64 would spend a
third of the document on nothing.

    Firestore
      beta/<galaxy>                     the clock, the roster, the hash, and
                                        the directory's row for this galaxy
      beta/<galaxy>/archive/<turn>      what the referee recorded for a turn
      beta/<galaxy>/submissions/0007~X  what civ X handed back for turn 7
      beta/<galaxy>/joins/<key>         a seat a stranger has asked for
      beta/<galaxy>/joins_done/<key>    what the worker decided about it

    Cloud Storage
      beta/<galaxy>/turns/0007.b64            the state for turn 7
      beta/<galaxy>/submissions/0007/X.b64    a submission made before H13
      beta/<galaxy>/notes/0007/X.txt          what the referee refused from X

Everything sits under one prefix so it cannot collide with the website the same
Firebase project hosts.

**A civ name is never a Firestore field name.** A civ name is a username a
player typed. Firestore restricts what a document id and a field name may
contain, so an archive record keyed by civ name is stored as one JSON string
rather than as a map, which also keeps it identical to what `referee` wrote,
and notes keep the directory store's own layout as objects. A submission
document's id carries the civ name escaped by `join_doc_id`, one to one, and
the name itself is a field of the document, which is what every reader takes
it from.

Objects hold the same base64 wire bytes the directory store writes to disk, so
an object downloaded here and a file copied out of a directory store decode
through the same `save_parser.decode_save`, and a galaxy moves between the two
by copying. Base64 costs a third more egress than the compressed bytes would;
at tens of kilobytes and six turns a day that is not worth a second encoding in
the tree.

A join request is a document and not an object
-----------------------------------------------
It is the one thing here that is neither a blob nor part of the clock, and the
choice is the same one the split above is: what does this thing fail at. A
request is a few hundred bytes of structure, and the question asked of it is
"what is waiting, oldest first", which is a query over a small collection
rather than a fetch of something named.

Cost decides it as well, and decides it the same way. H5 measured Cloud Storage
Class A operations as the binding free allowance, 5,000 a month against
Firestore's 20,000 writes a day, and a request kept as an object would spend a
Class A to lodge it, another to list for it at every turn boundary, and a copy
and a delete to move it aside once it is answered. Two documents and a batched
delete spend none of that. And the batch buys a property a bucket cannot offer
at all: the request leaves and the answer arrives together, so a worker killed
mid-write leaves one of the two states a reader can understand.

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
import base64
import json
import os
import string
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'dev_tools'))

import save_parser as sp
# The status keys and the closed-galaxy refusal are the store interface's and
# not this implementation's, so they come from where the interface is written
# down. `turn_store` reaches back here only inside `open_store`, so importing
# it at the top of this module is not a cycle.
import turn_store

# The prefix everything this module writes lives under, in both services. The
# project also hosts a public website, and a name that could be mistaken for
# the website's is not worth the minute it saves.
PREFIX = 'beta'

# The fields of the galaxy document that belong to the store. The same document
# carries the directory's row as well, and `name` and `created` stay out of the
# state so that no caller above the store seam starts depending on a field only
# one of the three implementations has.
#
# `status`, `closed_reason` and `reclaimed` are in for the opposite reason: all
# three stores carry them now. A launcher is handed a store spec and may never
# be handed a directory, so a galaxy that has ended and a seat that was taken
# back have to be answerable by the store the player is already holding.
#
# **An allowlist drops a field added later in silence**, which is what happened
# to the two at the end. `joins.commit` writes `joined` and
# `dev_tools/join_turn_acceptance.py` reads it back to check a live join, and
# on a Firebase galaxy it read nothing; L2's minimum-build gate reads
# `min_build` out of the state a launcher already has, and on a Firebase galaxy
# every build passes it. Neither was a missing write. A field this store is
# asked to carry belongs in this tuple, and the equivalence test now writes one
# and reads it back through all three so the next one cannot be dropped
# quietly.
STATE_FIELDS = ('turn', 'deadline', 'turn_seconds', 'civs', 'hash',
                turn_store.STATUS_KEY, turn_store.CLOSED_REASON_KEY,
                turn_store.RECLAIMED_KEY, turn_store.JOINED_KEY,
                turn_store.MIN_BUILD_KEY)

# Who has handed the current turn back, as `{"<turn>": [civ, ...]}` on the
# galaxy document. A launcher asks this every poll. When submissions were
# Cloud Storage objects the only other place the answer lived was a bucket
# listing, a Class A operation: at the Games page's 120-second poll that was
# about 21,900 a month from one open launcher, against the 5,000 a month the
# bucket's allowance carries. Now it would be a query, one read per player who
# has submitted. Read from here it rides the document read the relay makes for
# every request anyway.
#
# Keyed by turn so that a commit racing a publish lands under the turn it was
# for and cannot be read as a submission for the next one. `publish` replaces
# the whole map with the new turn's empty list, so it holds one or two keys and
# never grows. Civ names are values here and never keys, which keeps this
# module's rule about names a player typed.
#
# Not in `STATE_FIELDS`: it is answered through `submitted_civs`, which all
# three stores already have, and the directory store has no such field.
SUBMITTED_KEY = 'submitted'

# Where submissions live, one document per turn and civ. See the module
# docstring and the plan's H13.
SUBMISSION_COLLECTION = 'submissions'

# The first turn whose submissions are documents only, on the galaxy document.
# `start` writes it, and `publish` writes it once on a galaxy that lacks it,
# which is a galaxy started before H13. A turn before it, or any turn of a
# galaxy without it, was played while submissions were Cloud Storage objects,
# so a submission not found as a document is looked for as an object. That is
# what lets the relay and the worker change over in the middle of a turn
# without losing a submission already uploaded for it.
SUBMISSIONS_FROM_KEY = 'submissions_from'

# How many turns of submissions are kept as documents, the turn being played
# included. `publish` deletes the ones older than that. Firestore's free
# allowance stores 1 GiB, and a submission is about 10 KB compressed, so every
# submission kept for the life of a galaxy fills it within months at a hundred
# players. 18 turns is three days at four-hour turns, long enough for
# `referee --verify` to recompute any recent turn.
SUBMISSION_TURNS_KEPT = 18

# The largest compressed submission this store will write. A Firestore
# document is capped at 1 MiB, id and fields included; this leaves room for
# both. The relay's own cap on the body is lower, so nothing it accepts
# reaches this; `submit` is the path that can.
MAX_SUBMISSION_DATA = 900 * 1024

# The longest document id Firestore accepts, in bytes.
MAX_DOC_ID = 1500

# The two subcollections a join lives in: waiting, and answered. Two rather
# than one document with a flag on it, because "what is waiting" is then a
# plain stream of a small collection. One collection filtered by a flag and
# ordered by when the player asked is a composite index to create and keep,
# which is a deploy step for a question a handful of documents can answer.
JOIN_COLLECTION = 'joins'
JOIN_DONE_COLLECTION = 'joins_done'

# What a Firestore document id may carry as itself. A request key is a uid when
# the player has one and a name they typed when they do not, and a document id
# may not hold a slash, may not be `.` or `..`, and may not be spelled
# `__like_this__`. Escaping `.` and `_` along with the obvious characters rules
# out all three without a special case for each.
ID_SAFE = frozenset(string.ascii_letters + string.digits + '-~')

_BUCKETS = {}


def join_doc_id(key: str) -> str:
    """A document id for a request key, still readable when the key is a uid.

    An anonymous uid is letters and digits and comes through untouched, which
    is what an operator sees in the Firestore console when they ask what is
    waiting on a galaxy. Anything else is escaped byte by byte, so the mapping
    is one to one and no two keys can land on one document.

    This is the one place a caller-supplied string becomes a Firestore id, and
    it is the exception to this module's own rule that anything keyed by a name
    a player typed is a Storage object. The rule is kept where it was written
    for: a submission and a note are named for a civ, and a civ name is
    unbounded, arbitrary and read by the referee. A request key is bounded by
    `turn_store.MAX_JOIN_KEY`, is a uid on every path a stranger reaches, and
    is escaped here rather than hoped about.
    """
    return ''.join(chr(b) if chr(b) in ID_SAFE else f'%{b:02X}'
                   for b in key.encode('utf-8'))


def submitted_of(doc: dict, turn: int):
    """The civs a galaxy document records as having submitted for `turn`.

    None when the document says nothing about that turn, which is a past turn,
    or a galaxy last published by a store that predates the field. None is not
    an empty list: an empty list is the document saying nobody has, and None
    sends the caller to the bucket to find out.

    Only the turn the document is on is answered. A commit that read turn N
    and landed just after the publish of N+1 writes a key for N holding only
    that one civ, and a past turn read from it would be missing everyone who
    committed before the publish.
    """
    doc = doc or {}
    if doc.get('turn') is None or int(doc['turn']) != int(turn):
        return None
    by_turn = doc.get(SUBMITTED_KEY)
    if not isinstance(by_turn, dict):
        return None
    civs = by_turn.get(str(int(turn)))
    if not isinstance(civs, list):
        return None
    return sorted(str(c) for c in civs)


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
        self._creds = None

    def __repr__(self):
        return f'FirebaseTurnStore({self.project}:{self.prefix}/{self.galaxy})'

    # ── clients ──────────────────────────────────────────────────────────────
    # Built on first use rather than in __init__. Importing the google client
    # libraries costs about a second, most of it grpc, and `open_store` is
    # called by tools that then go on to talk to a directory.
    def credentials(self):
        """Default credentials, billed to this galaxy's own project.

        A user credential carries a quota project, and it is whatever project
        `gcloud auth application-default login` was last run against. Requests
        are attributed to that project rather than to the one holding the
        bucket, so an operator whose ADC points somewhere else gets every
        billable write refused with

            403 the billing account for the owning project is disabled in
                state absent

        which names neither the project at fault nor the credential. That cost
        an afternoon and a wrong diagnosis: this project's billing was never
        the problem, and a `gcloud storage cp` of the same object succeeded
        throughout, because gcloud does not use ADC.

        Pinning it here means the store bills the project it is reading and
        writing, whoever is logged in. A service account key carries no quota
        project and is unaffected either way.
        """
        if self._creds is None:
            import google.auth
            creds, _ = google.auth.default()
            if hasattr(creds, 'with_quota_project'):
                creds = creds.with_quota_project(self.project)
            self._creds = creds
        return self._creds

    @property
    def fs(self):
        if self._fs is None:
            from google.cloud import firestore
            self._fs = firestore.Client(project=self.project,
                                        credentials=self.credentials())
        return self._fs

    @property
    def gcs(self):
        if self._gcs is None:
            from google.cloud import storage
            self._gcs = storage.Client(project=self.project,
                                       credentials=self.credentials())
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

    def join_doc(self, key: str):
        return self.doc.collection(JOIN_COLLECTION).document(join_doc_id(key))

    def join_done_doc(self, key: str):
        return self.doc.collection(JOIN_DONE_COLLECTION).document(
            join_doc_id(key))

    @property
    def submission_collection(self):
        return self.doc.collection(SUBMISSION_COLLECTION)

    def submission_doc(self, civ: str, turn: int):
        """The document one civ's submission for one turn lives in.

        The turn leads the id so the console lists a turn's submissions
        together. `~` cannot appear in the turn half, so the id splits one way.
        """
        name = f'{int(turn):04d}~{join_doc_id(civ)}'
        if len(name.encode('utf-8')) > MAX_DOC_ID:
            raise ValueError(f'a civ name this long cannot name a submission '
                             f'({len(civ)} characters)')
        return self.submission_collection.document(name)

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

    def update_state(self, fields: dict) -> dict:
        """Merge fields into the galaxy document and return the state after.

        A merge names the fields it changes, so an operator closing a galaxy
        cannot roll back a turn the referee published between the read and the
        write. The directory store has to read and write the whole file and
        carries that race; this one does not.
        """
        self.doc.set(dict(fields), merge=True)
        return self.state()

    # ── status ───────────────────────────────────────────────────────────────
    def status(self) -> str:
        return turn_store.status_of(self.state())

    def is_closed(self) -> bool:
        return self.status() == turn_store.CLOSED

    def closed_reason(self):
        return self.state().get(turn_store.CLOSED_REASON_KEY)

    def close(self, reason: str = None) -> dict:
        fields = {turn_store.STATUS_KEY: turn_store.CLOSED}
        if reason:
            fields[turn_store.CLOSED_REASON_KEY] = reason
        return self.update_state(fields)

    def reopen(self) -> dict:
        """Undo a close, and take the reason with it so a galaxy closed twice
        cannot report the first reason."""
        from google.cloud import firestore
        self.doc.set({turn_store.STATUS_KEY: turn_store.OPEN,
                      turn_store.CLOSED_REASON_KEY: firestore.DELETE_FIELD},
                     merge=True)
        return self.state()

    def reclaimed(self, civ: str = None):
        return turn_store.reclaimed_of(self.state(), civ)

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
            SUBMITTED_KEY: {str(int(turn)): []},
            SUBMISSIONS_FROM_KEY: int(turn),
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

        The merge names its fields rather than merging everything, because
        `SUBMITTED_KEY` has to be replaced whole: merged, the map would keep
        every past turn's list and grow for the life of the galaxy.

        A galaxy started before H13 is given `SUBMISSIONS_FROM_KEY` here, at
        the first turn this code publishes, and submissions older than
        `SUBMISSION_TURNS_KEPT` turns are deleted once the new turn is out.
        """
        import canonical
        snap = self.doc.get()
        doc = (snap.to_dict() or {}) if snap.exists else {}
        seconds = turn_seconds or doc['turn_seconds']
        self._put(self.turn_object(turn), sp.encode_save(blob), 'text/plain')
        fields = {
            'turn': int(turn),
            'deadline': time.time() + seconds,
            'turn_seconds': int(seconds),
            'hash': canonical.canonical_hash(blob),
            SUBMITTED_KEY: {str(int(turn)): []},
        }
        if doc.get(SUBMISSIONS_FROM_KEY) is None:
            fields[SUBMISSIONS_FROM_KEY] = int(turn)
        self.doc.set(fields, merge=list(fields))
        self.prune_submissions(int(turn) - SUBMISSION_TURNS_KEPT + 1)

    # ── submissions ──────────────────────────────────────────────────────────
    def submit(self, civ: str, turn: int, blob: bytes) -> str:
        """Hand a player's state back. Writing twice replaces, which is right:
        a player may save several times in a turn and the last one is their
        intent.

        A closed galaxy refuses, from the one document read that also tells
        `write_submission` whether the civ is already recorded. The relay does
        not call this: it makes its own checks on the document it has read and
        calls `write_submission`, and it refuses a closed galaxy itself.
        """
        snap = self.doc.get()
        doc = (snap.to_dict() or {}) if snap.exists else {}
        if turn_store.status_of(doc) == turn_store.CLOSED:
            raise turn_store.GalaxyClosed(
                f'this galaxy is closed and is not taking submissions '
                f'({self.project}/{self.galaxy})')
        self.write_submission(civ, turn, blob, doc=doc)
        ref = self.submission_doc(civ, turn)
        return f'firestore://{self.project}/{ref.path}'

    def write_submission(self, civ: str, turn: int, blob: bytes,
                         doc: dict = None) -> str:
        """Store one civ's submission as a document, and say its tag.

        No check on what the bytes are; the relay has made them before it gets
        here. One write for the document, and a second in the same batch to
        record the civ on the galaxy document only when `doc`, the galaxy
        document as the caller read it, does not already record them. So a
        player's second and later uploads in a turn are one write each.

        Raises `SubmissionTooLarge` for a submission that would not fit in a
        document, which the relay's own cap makes unreachable through it.
        """
        data = base64.b64decode(sp.encode_save(blob))
        if len(data) > MAX_SUBMISSION_DATA:
            raise turn_store.SubmissionTooLarge(
                f'this submission is {len(data):,} bytes compressed and a '
                f'stored one may be {MAX_SUBMISSION_DATA:,}')
        tag = turn_store.submission_tag(blob)
        batch = self.fs.batch()
        batch.set(self.submission_doc(civ, turn), {
            'turn': int(turn),
            'civ': civ,
            'data': data,
            'tag': tag,
            'bytes': len(blob),
            'at': time.time(),
        })
        if doc is None or civ not in (submitted_of(doc, turn) or []):
            from google.cloud import firestore
            batch.set(self.doc, {SUBMITTED_KEY: {
                str(int(turn)): firestore.ArrayUnion([civ])}}, merge=True)
        batch.commit()
        return tag

    def submission_record(self, civ: str, turn: int):
        """The stored document for one civ and turn, or None.

        `data` is the compressed blob and `tag` its `submission_tag`. The relay
        serves this without inflating it, and answers a caller that already
        holds the tag without sending it at all.
        """
        snap = self.submission_doc(civ, turn).get()
        return (snap.to_dict() or {}) if snap.exists else None

    def before_documents(self, turn: int, doc: dict = None) -> bool:
        """Whether `turn` may have submissions stored as Storage objects.

        True for a turn before `SUBMISSIONS_FROM_KEY` and for every turn of a
        galaxy that does not carry it. `doc` is the galaxy document when the
        caller has it, and it is read here when not, which is only on a path
        that has already missed a document.
        """
        if doc is None:
            snap = self.doc.get()
            doc = (snap.to_dict() or {}) if snap.exists else {}
        since = doc.get(SUBMISSIONS_FROM_KEY)
        return since is None or int(turn) < int(since)

    def record_submitted(self, civ: str, turn: int) -> None:
        """Note on the galaxy document that `civ` has submitted for `turn`.

        `write_submission` does this in its own batch. This is the same write
        on its own, for a submission that became a document some other way.
        An array union, so two players committing at the same moment cannot
        drop each other. A turn the document no longer records gets a key of
        its own, which `submitted_of` never reads and the next `publish`
        removes.
        """
        from google.cloud import firestore
        self.doc.set({SUBMITTED_KEY: {
            str(int(turn)): firestore.ArrayUnion([civ])}}, merge=True)

    def forget_submitted(self, civ: str, turn: int) -> None:
        """Undo `record_submitted`, for a submission that has been removed."""
        from google.cloud import firestore
        self.doc.set({SUBMITTED_KEY: {
            str(int(turn)): firestore.ArrayRemove([civ])}}, merge=True)

    def has_submitted(self, civ: str, turn: int) -> bool:
        """Whether the submission exists, asked of where it is stored.

        One Firestore read. The referee asks this of the turn it is closing,
        and `abandonment` wants the answer the referee merges from rather than
        the galaxy document's record of it.
        """
        if self.submission_doc(civ, turn).get().exists:
            return True
        return (self.before_documents(turn)
                and self._has(self.submission_object(civ, turn)))

    def submission(self, civ: str, turn: int):
        """What one civ handed back for this turn, or None when they have not.

        One document named outright, so one Firestore read that touches
        nothing but that civ's own submission, which is what makes it
        survivable on the path a launcher polls.
        """
        rec = self.submission_record(civ, turn)
        if rec is not None:
            return turn_store.inflate_capped(bytes(rec['data']))
        if not self.before_documents(turn):
            return None
        data = self._get(self.submission_object(civ, turn))
        if data is None:
            return None
        return turn_store.decode_capped(data)

    def submitted_civs(self, turn: int, doc: dict = None) -> list:
        """Who has handed something back, by name and without the blobs.

        The current turn is answered from the galaxy document, one Firestore
        read and no Storage operation, which is what makes it safe on a path a
        launcher polls. `doc` is that document when the caller has already read
        it, which the relay has, so its `/submissions` route costs nothing
        beyond the read it makes for every request.

        Any other turn is `listed_civs`: a past turn, asked by an operator
        rather than a poll, or a galaxy last published before the document
        carried the field.
        """
        if doc is None:
            snap = self.doc.get()
            doc = (snap.to_dict() or {}) if snap.exists else {}
        known = submitted_of(doc, turn)
        if known is not None:
            return known
        return self.listed_civs(turn, doc=doc)

    def _documents(self, turn: int, fields=None):
        """The stored submission documents for one turn, as snapshots."""
        from google.cloud.firestore_v1.base_query import FieldFilter
        q = self.submission_collection.where(
            filter=FieldFilter('turn', '==', int(turn)))
        if fields is not None:
            q = q.select(fields)
        return list(q.stream())

    def _objects(self, turn: int):
        """(civ, Blob) for each pre-H13 Storage object under this turn."""
        start = self.submission_prefix(turn)
        out = []
        for blob in self.gcs.list_blobs(self.bucket, prefix=start):
            name = blob.name[len(start):]
            if name.endswith('.b64') and '/' not in name:
                out.append((name[:-4], blob))
        return out

    def listed_civs(self, turn: int, doc: dict = None) -> list:
        """Who has a stored submission for this turn, from the submissions.

        The ground truth `submitted_civs` stands in for. A query that reads
        only the `civ` field, one Firestore read per civ found, and for a turn
        from before the submissions were documents a bucket listing as well,
        one Class A operation.
        """
        out = {str((s.to_dict() or {}).get('civ'))
               for s in self._documents(turn, fields=['civ'])}
        if self.before_documents(turn, doc):
            out.update(civ for civ, _blob in self._objects(turn))
        return sorted(out)

    def submissions(self, turn: int) -> dict:
        """{civ: blob} for everyone who handed something back for this turn.

        Read from the submission documents rather than from `SUBMITTED_KEY`,
        because the documents are what gets merged. The referee reads this
        once a turn: one Firestore read per player. A launcher asks
        `has_submitted`, `submission` or `submitted_civs`, none of which reads
        anybody else's orders.

        A turn from before the submissions were documents is read from the
        bucket as well, and a document wins over an object for the same civ,
        because the document can only be the newer of the two.
        """
        out = {}
        for snap in self._documents(turn):
            rec = snap.to_dict() or {}
            out[str(rec.get('civ'))] = turn_store.inflate_capped(
                bytes(rec['data']))
        if self.before_documents(turn):
            for civ, blob in self._objects(turn):
                if civ not in out:
                    out[civ] = turn_store.decode_capped(
                        blob.download_as_bytes())
        return dict(sorted(out.items()))

    def prune_submissions(self, before: int) -> int:
        """Delete every submission document for a turn before `before`.

        Called by `publish`. One read per document found and one delete each,
        in batches under Firestore's 500 writes a batch. The objects of a turn
        before the submissions were documents are left where they are.
        """
        from google.cloud.firestore_v1.base_query import FieldFilter
        snaps = list(self.submission_collection.where(
            filter=FieldFilter('turn', '<', int(before))).select(
                ['turn']).stream())
        for i in range(0, len(snaps), 400):
            batch = self.fs.batch()
            for snap in snaps[i:i + 400]:
                batch.delete(snap.reference)
            batch.commit()
        return len(snaps)

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

    # ── joins ────────────────────────────────────────────────────────────────
    def request_join(self, req: dict) -> str:
        """Lodge a join request, and say where it landed.

        Lodging twice replaces, because the document id is the key: a player
        who presses Join again is one request waiting rather than two, which is
        what the directory store gets from writing the same file name.

        The key is written into the document as well as being its id. The id is
        escaped, so a request read back cannot recover a key that needed
        escaping from the id alone, and the key is what an answer is filed
        under and what a player reads their own answer back by.
        """
        problem = turn_store.join_problem(req)
        if problem:
            raise ValueError(problem)
        key = turn_store.join_key(req)
        rec = turn_store.join_record(req)
        rec['key'] = key
        self.join_doc(key).set(rec)
        return (f'{self.project}/{self.prefix}/{self.galaxy}/'
                f'{JOIN_COLLECTION}/{join_doc_id(key)}')

    def join_requests(self, log=None) -> list:
        """Every request waiting on this galaxy, oldest first.

        Streamed and sorted here rather than ordered by Firestore, for a reason
        that is quiet enough to be worth writing down: a query with an
        `order_by` returns only the documents that carry the field it orders
        on, so a request written without a `requested_at` would be invisible
        rather than last, and a request that cannot be seen is a player waiting
        forever. The collection holds one document per player waiting, which is
        a handful, so the sort costs nothing and is the sort the other two
        stores use.
        """
        out = []
        for snap in self.doc.collection(JOIN_COLLECTION).stream():
            data = snap.to_dict() or {}
            problem = turn_store.join_problem(data)
            if problem:
                turn_store.skipped(log, snap.id, problem)
                continue
            rec = turn_store.join_record(data)
            rec['key'] = data.get('key') or turn_store.join_key(data)
            out.append(rec)
        return sorted(out, key=turn_store.join_order)

    def join_request(self, key: str):
        """The one request filed under this key, or None while there is none.

        One named document read. A launcher waiting on a boundary polls this,
        so it is the read that must not be a listing, which is the same
        reasoning `submission` carries about not listing a turn's submissions
        to find one player's.
        """
        snap = self.join_doc(key).get()
        if not snap.exists:
            return None
        data = snap.to_dict() or {}
        if turn_store.join_problem(data):
            return None
        rec = turn_store.join_record(data)
        rec['key'] = data.get('key') or key
        return rec

    def answer_join(self, key: str, answer: dict) -> None:
        """Consume one request and record what was decided, in one write.

        A batch, so the request leaves and the answer arrives together. The
        directory store cannot do that: it writes the answer and then removes
        the file, and a worker killed between the two leaves both. Here there
        is no such window, so a request cannot be read a second time after it
        has been answered.

        Answering one that is already answered writes the answer again rather
        than refusing. A worker that is retrying cannot tell whether the batch
        it lost landed, and the second write of the same answer is the same
        answer.

        The answer is a map and not a JSON string, unlike the archive record
        beside it, because every key in it is a field name this tree chose:
        `outcome`, `reason`, `planet`, `system`. The archive record is keyed by
        civ name in two places, which is what a Firestore map key may not be.
        """
        batch = self.fs.batch()
        batch.set(self.join_done_doc(key), dict(answer))
        batch.delete(self.join_doc(key))
        batch.commit()

    def join_answer(self, key: str):
        """What was decided about one request, or None while it is waiting."""
        snap = self.join_done_doc(key).get()
        return snap.to_dict() if snap.exists else None

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
        for name in ('archive', SUBMISSION_COLLECTION, JOIN_COLLECTION,
                     JOIN_DONE_COLLECTION):
            for snap in self.doc.collection(name).stream():
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
