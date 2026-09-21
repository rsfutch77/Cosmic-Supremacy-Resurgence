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

FORMING, OPEN, CLOSED = 'forming', 'open', 'closed'

# The file that lets a folder list galaxies it does not itself contain. Without
# it a folder's galaxies are its subdirectories, which is the layout every
# existing tool already makes by hand.
INDEX = 'galaxies.json'


class Galaxy(typing.NamedTuple):
    """One row of the list, and everything the Games tab shows."""
    id: str
    name: str
    status: str
    turn: int
    deadline: float
    players: int
    joined: bool
    store: str

    def as_dict(self) -> dict:
        return dict(self._asdict())


def _row(gid, name, status, state, player, spec):
    """A row from a galaxy's state, or from its absence.

    A galaxy whose store has no state yet is forming rather than missing: the
    operator registers a galaxy before the first turn is published, and a row
    that vanished in between would be read as the galaxy having failed.
    """
    if not state:
        return Galaxy(gid, name, FORMING, None, None, 0, False, spec)
    civs = list(state.get('civs', []))
    return Galaxy(gid, name, status, state.get('turn'), state.get('deadline'),
                  len(civs), bool(player) and player in civs, spec)


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

    def set_status(self, gid: str, status: str) -> None:
        entries = self._entries()
        if gid not in entries:
            raise FileNotFoundError(f'no galaxy {gid} in {self.root}')
        entries[gid]['status'] = status
        self._write_index(entries)


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

    def set_status(self, gid: str, status: str) -> None:
        self.fs.collection(self.prefix).document(gid).set({'status': status},
                                                          merge=True)


def open_directory(spec: str):
    """A directory from a folder path or a `firebase://project`, whichever.

    The mirror of `open_store`, and for the same reason: a caller is handed a
    string and never learns which kind it got.
    """
    if spec.startswith('firebase://'):
        import firebase_store
        project, _galaxy, bucket, prefix = firebase_store.parse_spec(spec)
        return FirebaseGalaxyDirectory(project, prefix=prefix, bucket=bucket)
    return LocalGalaxyDirectory(spec)
