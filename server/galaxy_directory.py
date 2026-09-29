"""
galaxy_directory.py , the list of galaxies above the store
==========================================================
    from galaxy_directory import open_directory
    d = open_directory('firebase://cs-resurgence')

    for g in d.galaxies(player='DemoPlayer'):
        print(g.name, g.status, g.turn, g.players, g.joined)

    store = d.store('sandbox')          # and now the store interface as usual

`open_store` addresses exactly one galaxy, and nothing below this knows there
could be more than one. A launcher's Games tab needs what a store cannot
answer: which galaxies exist, what they are called, whether they are open, and
whether this player already has a seat in one.

Kept as a separate interface rather than folded into the store, because the
directory store and the HTTP store are what development and the two-machine LAN
test run on, and neither of them should have to grow a listing to keep working.
A directory hands out store specs; what a spec opens is `open_store`'s problem
and stays that way.

The row, and what is deliberately not in it
-------------------------------------------
A `Galaxy` carries a player count and a `joined` flag, and never the roster. A
launcher that could list the roster turns "is there a seat for me" into a way
to enumerate who is playing, which is the reason J4 gives for refusing a name
without naming who holds it. The seat check a launcher does today reads the
roster through the store, so this is a property of the directory only, and
closing the store's own path is J4's to do.

Status is one of three words. `forming` means no first turn has been published
yet, which is a fact about the store rather than a setting. `open` and `closed`
are the operator's, and a closed galaxy stays listed and readable, because a
launcher pointed at one has to be able to say so rather than fail (K5).

`set_status` writes the operator's word into the galaxy's own state as well as
into the listing, and a row takes the store's word over the listing's when the
store has one. A launcher is given a store spec, joins with it, and from then on
never needs the directory again, so a galaxy that was closed only in the listing
would go on taking that player's turns. The two cannot be allowed to disagree,
and the one the player reads is the one that decides.

Two implementations
-------------------
`LocalGalaxyDirectory` is a folder of galaxies, or a `galaxies.json` naming
their store specs, which is what lets a local directory list a galaxy that is
served over HTTP from the other machine. `FirebaseGalaxyDirectory` is one
Firestore collection, where a galaxy's row and its clock are the same document,
so listing every galaxy costs one query and reading one costs one document.
That matters more than it looks: the launcher polls this, and Firestore meters
reads.
"""
import json
import os
import sys
import typing

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import turn_store

FORMING = 'forming'
# The operator's two words are the store's, not this module's copy of them: a
# listing that said `closed` where a state said something else would be two
# spellings of one fact.
OPEN, CLOSED = turn_store.OPEN, turn_store.CLOSED

# The file that lets a folder list galaxies it does not itself contain. Without
# it a folder's galaxies are its subdirectories, which is the layout every
# existing tool already makes by hand.
INDEX = 'galaxies.json'


class Galaxy(typing.NamedTuple):
    """One row of the list, and everything the Games tab shows.

    `turn_seconds` is the galaxy's turn length, carried because a deadline on
    its own cannot say whether a turn is merely due or has stopped being
    closed. A referee a minute past a four-hour deadline is ordinary and one a
    minute past a fifteen-minute deadline is most of a turn late, so the reader
    needs the length to judge the lateness by. It comes out of the same state
    document the rest of the row does and costs no read of its own, which is
    the reason it is here rather than asked for separately.

    `closed_reason` is the operator's sentence for a closed galaxy, and None
    for any other, so a launcher can say why a galaxy ended from the listing it
    already holds. It is dropped from a galaxy that is not closed, because a
    store that reopens without clearing it would otherwise show a reopened
    galaxy's old reason.
    """
    id: str
    name: str
    status: str
    turn: int
    deadline: float
    players: int
    joined: bool
    store: str
    turn_seconds: int = None
    closed_reason: str = None

    def as_dict(self) -> dict:
        return dict(self._asdict())


def _row(gid, name, status, state, player, spec):
    """A row from a galaxy's state, or from its absence.

    A galaxy whose store has no state yet is forming rather than missing: the
    operator registers a galaxy before the first turn is published, and a row
    that vanished in between would be read as the galaxy having failed.

    The store's own status wins over the listing's when it has one, because the
    store is what a launcher reads once it has joined. A row that still said
    `open` about a galaxy whose state says `closed` would send a player into a
    galaxy that will refuse their turn.
    """
    if not state:
        return Galaxy(gid, name, FORMING, None, None, 0, False, spec)
    civs = list(state.get('civs', []))
    status = state.get(turn_store.STATUS_KEY) or status
    reason = (state.get(turn_store.CLOSED_REASON_KEY) if status == CLOSED
              else None)
    return Galaxy(gid, name, status, state.get('turn'), state.get('deadline'),
                  len(civs), bool(player) and player in civs, spec,
                  state.get('turn_seconds'), reason)


class LocalGalaxyDirectory:
    """Galaxies in a folder, or named by a `galaxies.json` in one."""

    def __init__(self, root: str):
        self.root = os.path.abspath(root)

    def __repr__(self):
        return f'LocalGalaxyDirectory({self.root})'

    @property
    def index_path(self) -> str:
        return os.path.join(self.root, INDEX)

    def _entries(self) -> dict:
        """{id: {name, status, store}} from the index, or from the folder."""
        try:
            with open(self.index_path, encoding='utf-8') as f:
                listed = json.load(f).get('galaxies', [])
        except (FileNotFoundError, PermissionError, ValueError):
            listed = []
        out = {}
        for e in listed:
            gid = e['id']
            out[gid] = {
                'name': e.get('name') or gid,
                'status': e.get('status') or OPEN,
                'store': e.get('store') or os.path.join(self.root, gid),
            }
        if not os.path.isdir(self.root):
            return out
        for name in sorted(os.listdir(self.root)):
            path = os.path.join(self.root, name)
            if name in out or not os.path.isdir(path):
                continue
            if not os.path.exists(os.path.join(path, 'state.json')):
                continue
            out[name] = {'name': name, 'status': OPEN, 'store': path}
        return out

    def _write_index(self, entries: dict) -> None:
        turn_store._atomic_write(self.index_path, json.dumps(
            {'galaxies': [dict(id=gid, **e) for gid, e in
                          sorted(entries.items())]}, indent=2).encode('utf-8'))

    # ── reading ──────────────────────────────────────────────────────────────
    def galaxies(self, player: str = None) -> list:
        out = []
        for gid, e in sorted(self._entries().items()):
            out.append(self._one(gid, e, player))
        return out

    def galaxy(self, gid: str, player: str = None):
        e = self._entries().get(gid)
        return self._one(gid, e, player) if e else None

    def _one(self, gid, e, player):
        store = turn_store.open_store(e['store'])
        try:
            state = store.state() if store.exists() else None
        except OSError:
            # An unreachable share or a service that is down is a galaxy this
            # machine cannot see rather than one that does not exist, and the
            # list has to survive it to show the others.
            state = None
        return _row(gid, e['name'], e['status'], state, player, e['store'])

    def store(self, gid: str):
        e = self._entries().get(gid)
        if not e:
            raise FileNotFoundError(f'no galaxy {gid} in {self.root}')
        return turn_store.open_store(e['store'])

    # ── writing ──────────────────────────────────────────────────────────────
    def register(self, gid: str, name: str = None, status: str = OPEN,
                 store: str = None):
        entries = self._entries()
        e = entries.get(gid, {})
        entries[gid] = {
            'name': name or e.get('name') or gid,
            'status': status or e.get('status') or OPEN,
            'store': store or e.get('store') or os.path.join(self.root, gid),
        }
        os.makedirs(self.root, exist_ok=True)
        self._write_index(entries)
        return turn_store.open_store(entries[gid]['store'])

    def set_status(self, gid: str, status: str, reason: str = None) -> None:
        """The operator's word, into the listing and into the galaxy itself.

        A galaxy with no state yet takes the listing's word alone, which is the
        `forming` case: there is nothing to write it into, and `register` will
        carry it forward when the first turn is published.
        """
        entries = self._entries()
        if gid not in entries:
            raise FileNotFoundError(f'no galaxy {gid} in {self.root}')
        entries[gid]['status'] = status
        self._write_index(entries)
        store = turn_store.open_store(entries[gid]['store'])
        if not store.exists():
            return
        if status == CLOSED:
            store.close(reason=reason)
        else:
            store.reopen()


class FirebaseGalaxyDirectory:
    """Galaxies as one Firestore collection, one document each.

    The document is the store's own state document. A row therefore costs no
    read the store was not already paying for, and a listing is one query
    rather than a query plus a read per galaxy.
    """

    def __init__(self, project: str, prefix: str = None, bucket: str = None):
        import firebase_store
        self.project = project
        self.prefix = (prefix or firebase_store.PREFIX).strip('/')
        self.bucket = bucket
        self._fs = None

    def __repr__(self):
        return f'FirebaseGalaxyDirectory({self.project}:{self.prefix})'

    @property
    def fs(self):
        if self._fs is None:
            from google.cloud import firestore
            self._fs = firestore.Client(project=self.project)
        return self._fs

    def _spec(self, gid: str) -> str:
        """The store spec for one galaxy, carrying whatever is not the default.

        A caller is handed this and passes it to `open_store`, so a launcher
        holds a galaxy the same way whichever directory listed it.
        """
        import urllib.parse

        import firebase_store
        q = []
        if self.bucket:
            q.append(f'bucket={self.bucket}')
        if self.prefix != firebase_store.PREFIX:
            q.append(f'prefix={self.prefix}')
        spec = f'firebase://{self.project}/{urllib.parse.quote(gid)}'
        return spec + ('?' + '&'.join(q) if q else '')

    def _one(self, gid, data, player):
        data = data or {}
        state = data if 'turn' in data else None
        return _row(gid, data.get('name') or gid, data.get('status') or OPEN,
                    state, player, self._spec(gid))

    # ── reading ──────────────────────────────────────────────────────────────
    def galaxies(self, player: str = None) -> list:
        out = []
        for snap in self.fs.collection(self.prefix).stream():
            out.append(self._one(snap.id, snap.to_dict(), player))
        return sorted(out, key=lambda g: g.id)

    def galaxy(self, gid: str, player: str = None):
        snap = self.fs.collection(self.prefix).document(gid).get()
        return self._one(gid, snap.to_dict(), player) if snap.exists else None

    def store(self, gid: str):
        return turn_store.open_store(self._spec(gid))

    # ── writing ──────────────────────────────────────────────────────────────
    def register(self, gid: str, name: str = None, status: str = OPEN,
                 store: str = None):
        """Give a galaxy a name and a status, whether or not it has a turn yet.

        A merge, so registering a galaxy the referee has already started does
        not touch its clock, and starting a galaxy the operator has already
        named does not touch its name.
        """
        self.fs.collection(self.prefix).document(gid).set(
            {'name': name or gid, 'status': status or OPEN}, merge=True)
        return self.store(gid)

    def set_status(self, gid: str, status: str, reason: str = None) -> None:
        """One write, because the row and the state are the same document.

        The local directory has to write twice and keep the two agreeing; here
        there is only one place for the word to be, which is the arrangement
        that made this document the state document in the first place.
        """
        from google.cloud import firestore
        fields = {'status': status}
        if status == CLOSED:
            if reason:
                fields[turn_store.CLOSED_REASON_KEY] = reason
        else:
            # A reason outlives its close otherwise, and a galaxy reopened and
            # closed again would report the first one.
            fields[turn_store.CLOSED_REASON_KEY] = firestore.DELETE_FIELD
        self.fs.collection(self.prefix).document(gid).set(fields, merge=True)


class HttpGalaxyDirectory:
    """Galaxies from the relay, for a launcher that holds no Google credential.

    `FirebaseGalaxyDirectory` authenticates as the project and reads the
    collection directly. A player is not the project, so the beta's list has to
    come from the relay, which reads that collection on their behalf and serves
    each galaxy the fields it would serve about itself anyway.

    A row's store is a URL back through this same relay, so a galaxy opened
    from this list is played over the door it was listed through rather than
    over one the player would have no credential for.
    """

    def __init__(self, base: str, timeout: float = 30.0, token=None):
        self.base = base.rstrip('/')
        # An HttpTurnStore for its transport alone. The bearer header, the rule
        # that drops it across a redirect and the convention that 404 is an
        # answer are the same over this route as over a galaxy's, and a second
        # copy of them is a second thing to keep right.
        self.http = turn_store.HttpTurnStore(self.base, timeout=timeout,
                                             token=token)

    def __repr__(self):
        return f'HttpGalaxyDirectory({self.base})'

    def _spec(self, gid: str) -> str:
        import urllib.parse
        return f'{self.base}/{urllib.parse.quote(gid)}'

    def _one(self, row, player):
        row = row or {}
        gid = row.get('id')
        state = row if 'turn' in row else None
        return _row(gid, row.get('name') or gid, row.get(turn_store.STATUS_KEY)
                    or OPEN, state, player, self._spec(gid))

    # ── reading ──────────────────────────────────────────────────────────────
    def galaxies(self, player: str = None) -> list:
        listing = self.http.get_json('/') or {}
        return [self._one(row, player) for row in listing.get('galaxies', [])]

    def galaxy(self, gid: str, player: str = None):
        """One galaxy, found by listing them.

        The listing rather than that galaxy's own `/state`, which would be the
        smaller call, because `/state` is the store's answer and carries no
        name: the name is the directory's field. A row labelled by its id when
        the operator gave it one would be this directory quietly losing
        something the others keep.
        """
        for g in self.galaxies(player=player):
            if g.id == gid:
                return g
        return None

    def store(self, gid: str):
        return turn_store.open_store(self._spec(gid),
                                     token=self.http.token)

    # ── writing ──────────────────────────────────────────────────────────────
    # Naming a galaxy and opening or closing it are the operator's, and the
    # relay refuses them for the same reason it refuses the referee's routes.
    # Refused here rather than left off the class, so a caller that reaches for
    # them is told which directory it is holding.
    def register(self, gid: str, name: str = None, status: str = OPEN,
                 store: str = None):
        raise NotImplementedError(
            'a galaxy is registered by its operator, not through the relay')

    def set_status(self, gid: str, status: str, reason: str = None) -> None:
        raise NotImplementedError(
            'a galaxy is opened and closed by its operator, not through the '
            'relay')


def open_directory(spec: str, token=None):
    """A directory from a folder, a `firebase://project`, or a relay URL.

    The mirror of `open_store`, and for the same reason: a caller is handed a
    string and never learns which kind it got. `token` follows the same rule it
    follows there, reaching the HTTP directory only and being ignored rather
    than refused by the other two, so a caller holding an identity does not
    have to know which kind its config named.
    """
    if spec.startswith('http://') or spec.startswith('https://'):
        return HttpGalaxyDirectory(spec, token=token)
    if spec.startswith('firebase://'):
        import firebase_store
        project, _galaxy, bucket, prefix = firebase_store.parse_spec(spec)
        return FirebaseGalaxyDirectory(project, prefix=prefix, bucket=bucket)
    return LocalGalaxyDirectory(spec)
