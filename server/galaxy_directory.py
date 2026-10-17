"""
galaxy_directory.py: the list of galaxies above the store

    from galaxy_directory import open_directory
    d = open_directory('firebase://cs-resurgence')

    for g in d.galaxies(player='DemoPlayer'):
        print(g.name, g.status, g.turn, g.players, g.joined)

    store = d.store('sandbox')          # and now the store interface as usual

A directory lists galaxies and hands out store specs for `open_store`. A row
carries a player count and a `joined` flag, never the roster.

Status is `forming` (no first turn published), `open` or `closed`. A closed
galaxy stays listed and readable. `set_status` writes into the galaxy's own
state as well as the listing, and a row takes the store's status over the
listing's.

`LocalGalaxyDirectory` is a folder of galaxies, or a `galaxies.json` naming
their store specs. `FirebaseGalaxyDirectory` is one Firestore collection in
which each galaxy's row is its state document. `HttpGalaxyDirectory` reads the
list from the relay.
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
OPEN, CLOSED = turn_store.OPEN, turn_store.CLOSED

# Lists galaxies a folder does not contain. Without it, a folder's galaxies are
# its subdirectories.
INDEX = 'galaxies.json'


class Galaxy(typing.NamedTuple):
    """One row of the list, and everything the Games tab shows.

    `turn_seconds` is the galaxy's turn length, for judging how late a deadline
    is. `closed_reason` is the operator's sentence for a closed galaxy, and None
    for any other.
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
    """A row from a galaxy's state. No state means forming. The store's own
    status wins over the listing's."""
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
            # An unreachable store is listed as forming so the rest still show.
            state = None
        return _row(gid, e['name'], e['status'], state, player, e['store'])

    def store(self, gid: str):
        e = self._entries().get(gid)
        if not e:
            raise FileNotFoundError(f'no galaxy {gid} in {self.root}')
        return turn_store.open_store(e['store'])

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
        """Set the status in the listing and in the galaxy's state. A galaxy with
        no state yet takes it in the listing alone."""
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
    """Galaxies as one Firestore collection, one document each. The document is
    the store's own state document, so a listing is one query."""

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
        """The store spec for one galaxy, carrying whatever is not the default."""
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

    def register(self, gid: str, name: str = None, status: str = OPEN,
                 store: str = None):
        """Give a galaxy a name and a status, whether or not it has a turn yet.
        A merge, so the rest of the document is untouched."""
        self.fs.collection(self.prefix).document(gid).set(
            {'name': name or gid, 'status': status or OPEN}, merge=True)
        return self.store(gid)

    def set_status(self, gid: str, status: str, reason: str = None) -> None:
        """One write, because the row and the state are the same document."""
        from google.cloud import firestore
        fields = {'status': status}
        if status == CLOSED:
            if reason:
                fields[turn_store.CLOSED_REASON_KEY] = reason
        else:
            # Reopening clears the close reason.
            fields[turn_store.CLOSED_REASON_KEY] = firestore.DELETE_FIELD
        self.fs.collection(self.prefix).document(gid).set(fields, merge=True)


class HttpGalaxyDirectory:
    """Galaxies from the relay, for a launcher that holds no Google credential.
    Each row's store is a URL back through the same relay."""

    def __init__(self, base: str, timeout: float = 30.0, token=None):
        self.base = base.rstrip('/')
        # An HttpTurnStore used for its transport only (bearer header, redirect
        # handling, 404 as an answer).
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

    def galaxies(self, player: str = None) -> list:
        listing = self.http.get_json('/') or {}
        return [self._one(row, player) for row in listing.get('galaxies', [])]

    def galaxy(self, gid: str, player: str = None):
        """One galaxy, found by listing them, since `/state` carries no name."""
        for g in self.galaxies(player=player):
            if g.id == gid:
                return g
        return None

    def store(self, gid: str):
        return turn_store.open_store(self._spec(gid),
                                     token=self.http.token)

    # Registering and status changes are operator-only, and the relay refuses them.
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
    `token` reaches the HTTP directory only and is ignored by the others."""
    if spec.startswith('http://') or spec.startswith('https://'):
        return HttpGalaxyDirectory(spec, token=token)
    if spec.startswith('firebase://'):
        import firebase_store
        project, _galaxy, bucket, prefix = firebase_store.parse_spec(spec)
        return FirebaseGalaxyDirectory(project, prefix=prefix, bucket=bucket)
    return LocalGalaxyDirectory(spec)
