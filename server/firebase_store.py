"""
firebase_store.py: a galaxy's turns and submissions, in Firebase
=================================================================
    from turn_store import open_store
    store = open_store('firebase://cs-resurgence/sandbox')

    store.start(blob, civs=['DemoPlayer'], turn_seconds=14400)
    turn, deadline = store.current()
    blob = store.turn_blob(turn)
    store.submit('DemoPlayer', turn, mine)

The `turn_store` interface on Firestore and Cloud Storage. The referee opens
outbound connections only, so it needs no inbound port.

Turn blobs are Cloud Storage objects, since a Firestore document is capped at
1 MiB. The clock, roster, archive, submissions, notes and joins are Firestore
documents. A submission document holds the compressed bytes, not base64.

    Firestore
      beta/<galaxy>                     the clock, the roster, the hash, and
                                        the directory's row for this galaxy
      beta/<galaxy>/archive/<turn>      what the referee recorded for a turn
      beta/<galaxy>/submissions/0007~X  what civ X handed back for turn 7
      beta/<galaxy>/notes/0007~X        what the referee refused from X
      beta/<galaxy>/joins/<key>         a seat a stranger has asked for
      beta/<galaxy>/joins_done/<key>    what the worker decided about it

    Cloud Storage
      beta/<galaxy>/turns/0007.b64            the state for turn 7
      beta/<galaxy>/submissions/0007/X.b64    a submission from older galaxies
      beta/<galaxy>/notes/0007/X.txt          a note from older galaxies

A civ name is never a Firestore field name or raw document id. Archive records
keyed by civ are stored as one JSON string; submission and note ids carry the
name escaped by `join_doc_id`, and the name itself is a document field.

Objects hold the same base64 wire bytes the directory store writes, so a galaxy
moves between the two by copying.

The deadline is an absolute epoch time written by the referee, not a Firestore
server timestamp, so it means the same thing in every store.

Credentials come from Application Default Credentials. The client libraries
read `FIRESTORE_EMULATOR_HOST` and `STORAGE_EMULATOR_HOST` themselves.
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
# `turn_store` imports this module only inside `open_store`, so this is not a
# cycle.
import turn_store

PREFIX = 'beta'   # root of everything this module writes, in both services

# The galaxy document fields that belong to the store state. `name` and
# `created` belong to the directory row on the same document and are left out.
STATE_FIELDS = ('turn', 'deadline', 'turn_seconds', 'civs', 'hash',
                turn_store.STATUS_KEY, turn_store.CLOSED_REASON_KEY,
                turn_store.RECLAIMED_KEY, turn_store.JOINED_KEY,
                turn_store.MIN_BUILD_KEY)

# Who has submitted, as `{"<turn>": [civ, ...]}` on the galaxy document. Keyed
# by turn so a commit racing a publish lands under its own turn; `publish`
# replaces the whole map. Answered through `submitted_civs`, not `STATE_FIELDS`.
SUBMITTED_KEY = 'submitted'

SUBMISSION_COLLECTION = 'submissions'   # one document per turn and civ
NOTE_COLLECTION = 'notes'               # the referee's notes, kept as long

# The first turn whose submissions are documents only. Earlier turns, or any
# turn of a galaxy without the field, also look for a Storage object.
SUBMISSIONS_FROM_KEY = 'submissions_from'

# Turns of submissions kept as documents, the current one included; `publish`
# deletes older ones. Long enough for `referee --verify` on a recent turn.
SUBMISSION_TURNS_KEPT = 18

# The largest compressed submission this store writes, leaving room under the
# 1 MiB document cap for the id and other fields.
MAX_SUBMISSION_DATA = 900 * 1024

MAX_DOC_ID = 1500   # longest Firestore document id, in bytes

# Join subcollections: waiting and answered.
JOIN_COLLECTION = 'joins'
JOIN_DONE_COLLECTION = 'joins_done'

# Characters a document id carries unescaped. Escaping `.` and `_` rules out
# `.`, `..` and `__reserved__` ids.
ID_SAFE = frozenset(string.ascii_letters + string.digits + '-~')

_BUCKETS = {}


def join_doc_id(key: str) -> str:
    """A document id for a request key, still readable when the key is a uid.

    Characters outside `ID_SAFE` are escaped byte by byte as `%XX`, so the
    mapping is one to one. A uid passes through unchanged.
    """
    return ''.join(chr(b) if chr(b) in ID_SAFE else f'%{b:02X}'
                   for b in key.encode('utf-8'))


def submitted_of(doc: dict, turn: int):
    """The civs a galaxy document records as having submitted for `turn`.

    None (not []) when the document does not record that turn, which sends the
    caller to the submissions themselves. Only the document's current turn is
    answered, since a past turn's key may be incomplete.
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

    The optional query takes `bucket` and `prefix`, for tests and for a second
    galaxy tree in one project.
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

    Tries `<project>.firebasestorage.app` then `<project>.appspot.com`, unless
    `FIREBASE_STORAGE_BUCKET` is set. The answer is cached for the process.
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
            continue
    # Nothing answered: the operation that needs the bucket reports the error.
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

    # Clients are built on first use; the Google libraries are slow to import.
    def credentials(self):
        """Default credentials with the quota project pinned to this project.

        Without the pin, requests are attributed to the ADC login's quota
        project and can fail with a misleading billing 403. A service account
        needs `roles/serviceusage.serviceUsageConsumer` to name a quota project.
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
        return self.submission_collection.document(self._civ_turn_id(civ,
                                                                     turn))

    def note_doc(self, civ: str, turn: int):
        """The document the referee's note to one civ for one turn lives in."""
        return self.doc.collection(NOTE_COLLECTION).document(
            self._civ_turn_id(civ, turn))

    @staticmethod
    def _civ_turn_id(civ: str, turn: int) -> str:
        name = f'{int(turn):04d}~{join_doc_id(civ)}'
        if len(name.encode('utf-8')) > MAX_DOC_ID:
            raise ValueError(f'a civ name this long cannot name a document '
                             f'({len(civ)} characters)')
        return name

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

    def _put(self, name: str, data: bytes, content_type: str) -> None:
        self.bucket.blob(name).upload_from_string(data,
                                                  content_type=content_type)

    def _get(self, name: str):
        """The object's bytes, or None when it is not there."""
        from google.api_core import exceptions
        try:
            return self.bucket.blob(name).download_as_bytes()
        except exceptions.NotFound:
            return None

    def _has(self, name: str) -> bool:
        return self.bucket.blob(name).exists()

    def exists(self) -> bool:
        return self.doc.get().exists

    def state(self) -> dict:
        """The galaxy's clock and roster. A document read is a consistent
        snapshot, so no retry is needed."""
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
        Only the named fields change, so a concurrent publish is not lost."""
        self.doc.set(dict(fields), merge=True)
        return self.state()

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
        """Undo a close, clearing the closed reason."""
        from google.cloud import firestore
        self.doc.set({turn_store.STATUS_KEY: turn_store.OPEN,
                      turn_store.CLOSED_REASON_KEY: firestore.DELETE_FIELD},
                     merge=True)
        return self.state()

    def reclaimed(self, civ: str = None):
        return turn_store.reclaimed_of(self.state(), civ)

    def start(self, blob: bytes, civs, turn_seconds: int = 3600,
              turn: int = None) -> int:
        """Publish the first turn and start the clock.

        `turn` defaults to the turn number the blob carries.
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

        Merges only the fields it names, so status and name survive and
        `SUBMITTED_KEY` is replaced whole. Sets `SUBMISSIONS_FROM_KEY` on a
        galaxy that lacks it, then prunes submissions older than
        `SUBMISSION_TURNS_KEPT` turns.
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

    def submit(self, civ: str, turn: int, blob: bytes) -> str:
        """Hand a player's state back. A later submission replaces an earlier
        one. Raises `GalaxyClosed` on a closed galaxy. The relay calls
        `write_submission` directly.
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

        The bytes are not checked here. The civ is added to `SUBMITTED_KEY` in
        the same batch unless `doc` (the galaxy document as the caller read it)
        already records them. Raises `SubmissionTooLarge` past
        `MAX_SUBMISSION_DATA`.
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

        `data` is the compressed blob and `tag` its `submission_tag`.
        """
        snap = self.submission_doc(civ, turn).get()
        return (snap.to_dict() or {}) if snap.exists else None

    def before_documents(self, turn: int, doc: dict = None) -> bool:
        """Whether `turn` may have submissions stored as Storage objects.

        True for a turn before `SUBMISSIONS_FROM_KEY` and for every turn of a
        galaxy without it. `doc` is the galaxy document, read here if omitted.
        """
        if doc is None:
            snap = self.doc.get()
            doc = (snap.to_dict() or {}) if snap.exists else {}
        since = doc.get(SUBMISSIONS_FROM_KEY)
        return since is None or int(turn) < int(since)

    def record_submitted(self, civ: str, turn: int) -> None:
        """Note on the galaxy document that `civ` has submitted for `turn`.

        An array union, so concurrent commits do not drop each other.
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
        """Whether the submission exists, checked against the stored submission
        rather than `SUBMITTED_KEY`."""
        if self.submission_doc(civ, turn).get().exists:
            return True
        return (self.before_documents(turn)
                and self._has(self.submission_object(civ, turn)))

    def submission(self, civ: str, turn: int):
        """What one civ handed back for this turn, or None when they have not."""
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

        The current turn is answered from the galaxy document (`doc`, if the
        caller already read it). Any other turn falls back to `listed_civs`.
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
        """(civ, Blob) for each submission stored as an object under this turn."""
        start = self.submission_prefix(turn)
        out = []
        for blob in self.gcs.list_blobs(self.bucket, prefix=start):
            name = blob.name[len(start):]
            if name.endswith('.b64') and '/' not in name:
                out.append((name[:-4], blob))
        return out

    def listed_civs(self, turn: int, doc: dict = None) -> list:
        """Who has a stored submission for this turn, from the submissions.

        Queries the `civ` field of the documents, plus a bucket listing for a
        turn that may have objects.
        """
        out = {str((s.to_dict() or {}).get('civ'))
               for s in self._documents(turn, fields=['civ'])}
        if self.before_documents(turn, doc):
            out.update(civ for civ, _blob in self._objects(turn))
        return sorted(out)

    def submissions(self, turn: int) -> dict:
        """{civ: blob} for everyone who handed something back for this turn.

        Read from the submission documents, plus objects for a turn that may
        have them. A document wins over an object for the same civ.
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
        """Delete every submission and note document for a turn before
        `before`, and say how many went.

        Deletes in batches under Firestore's 500-write limit. Storage objects
        are left alone.
        """
        from google.cloud.firestore_v1.base_query import FieldFilter
        snaps = []
        for coll in (self.submission_collection,
                     self.doc.collection(NOTE_COLLECTION)):
            snaps += list(coll.where(
                filter=FieldFilter('turn', '<', int(before))).select(
                    ['turn']).stream())
        for i in range(0, len(snaps), 400):
            batch = self.fs.batch()
            for snap in snaps[i:i + 400]:
                batch.delete(snap.reference)
            batch.commit()
        return len(snaps)

    def archive(self, turn: int, record: dict) -> None:
        """Record what the referee did, as one JSON string.

        `refused` and `hash_submissions` are keyed by civ name, which cannot be
        a Firestore map key.
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

    def put_note(self, civ: str, turn: int, lines) -> None:
        """Leave one player the referee's reasons for refusing part of a turn."""
        self.note_doc(civ, turn).set({
            'turn': int(turn),
            'civ': civ,
            'lines': [str(line) for line in lines],
        })

    def note(self, civ: str, turn: int) -> list:
        """Those reasons, or [] when the turn was taken whole.

        Falls back to a Storage object for a turn that may have one.
        """
        snap = self.note_doc(civ, turn).get()
        if snap.exists:
            return [line for line in (snap.to_dict() or {}).get('lines') or []
                    if line]
        if not self.before_documents(turn):
            return []
        data = self._get(self.note_object(civ, turn))
        if not data:
            return []
        return [line for line in data.decode('utf-8').split('\n') if line]

    def request_join(self, req: dict) -> str:
        """Lodge a join request, and say where it landed.

        The document id is the escaped key, so lodging twice replaces. The key
        is also stored as a field, since the escaped id does not round-trip.
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

        Sorted here, not with `order_by`: Firestore's `order_by` omits
        documents missing the field, which would hide a request without
        `requested_at`.
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
        """The one request filed under this key, or None while there is none."""
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

        A batch, so the request leaves and the answer arrives together.
        Answering again rewrites the answer, so a retry is safe. The answer is
        stored as a map because its keys are fixed field names.
        """
        batch = self.fs.batch()
        batch.set(self.join_done_doc(key), dict(answer))
        batch.delete(self.join_doc(key))
        batch.commit()

    def join_answer(self, key: str):
        """What was decided about one request, or None while it is waiting."""
        snap = self.join_done_doc(key).get()
        return snap.to_dict() if snap.exists else None

    def delete_everything(self) -> int:
        """Remove this galaxy from both services, and say how many things went.

        For tests and smoke checks. Not part of the store interface.
        """
        n = 0
        for blob in self.gcs.list_blobs(self.bucket,
                                        prefix=self._object() + '/'):
            blob.delete()
            n += 1
        for name in ('archive', SUBMISSION_COLLECTION, NOTE_COLLECTION,
                     JOIN_COLLECTION,
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
