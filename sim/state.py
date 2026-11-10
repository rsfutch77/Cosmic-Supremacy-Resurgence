"""
The simulator's galaxy state: the subset of a save blob the turn rules read
and write, as plain Python objects keyed by the engine's object ids.
"""
from dataclasses import dataclass, field

from . import tables

JOBS = {0: 'farmer', 1: 'worker', 2: 'scientist', 3: 'military', 5: 'miner',
        6: 'banker'}
STANCES = {0: 'neutral', 1: 'war', 2: 'cease-fire', 3: 'peace', 4: 'alliance'}
ORDERS = {0: 'none', 1: 'move', 3: 'colonise'}


@dataclass
class Design:
    oid: int
    owner: int
    name: str
    parts: dict                     # category -> [part id], tables.CATEGORIES order
    inactive: bool = False

    def _all(self):
        for cat in tables.CATEGORIES:
            table = tables.TABLES[cat]
            for pid in self.parts.get(cat, ()):
                yield cat, table[pid]

    @property
    def chassis(self):
        ids = self.parts.get('chassis') or [None]
        return ids[0]

    @property
    def heavy(self):
        return self.chassis is not None and self.chassis not in tables.LIGHT_CHASSIS

    @property
    def units(self):
        return sum(p.units for _c, p in self._all())

    @property
    def shield(self):
        """Shield strength in percent of units."""
        return sum(p.stat for c, p in self._all() if c == 'shields')

    @property
    def hitpoints(self):
        """Units raised by shields, at full condition."""
        return self.units * (100 + self.shield) / 100

    @property
    def firepower(self):
        """(vs light ships, vs heavy ships, vs planets), before rank."""
        fp = [0, 0, 0]
        for c, p in self._all():
            if c == 'weapons':
                fp = [a + b for a, b in zip(fp, p.fp)]
        return tuple(fp)

    @property
    def thrust(self):
        return sum(p.stat for c, p in self._all() if c == 'engines')

    @property
    def space_used(self):
        return sum(p.space for c, p in self._all() if c != 'chassis')

    @property
    def space(self):
        return sum(p.space for c, p in self._all() if c == 'chassis')

    @property
    def cost(self):
        return sum(p.cost for _c, p in self._all())

    @property
    def upkeep(self):
        return sum(p.upkeep for _c, p in self._all())

    @property
    def resources(self):
        out = [0] * len(tables.RESOURCES)
        for _c, p in self._all():
            out = [a + b for a, b in zip(out, p.resources)]
        return tuple(out)


@dataclass
class Order:
    kind: int                       # ORDERS
    target: tuple = None            # x, z, y
    progress: float = 0.0
    legs: list = field(default_factory=list)    # [(origin, dest, length)]
    ref: int = 0                    # object behind the order, 0 for a point
    flown: list = field(default_factory=list)   # legs already behind the ship


@dataclass
class Ship:
    oid: int
    owner: int
    pos: tuple                      # x, z, y
    design: int
    crew: list                      # [(job, owner)]
    condition: float                # 1.0 undamaged
    orbit: int = 0                  # planet id, 0 in open space
    order: Order = None
    admiral: int = 0
    fleet: int = 0                  # the fleet it is in, 0 for none
    crew_xp: list = field(default_factory=list)     # experience, one per crew member


@dataclass
class Fleet:
    oid: int
    owner: int
    pos: tuple
    name: str
    ships: list                     # ship ids, in order
    orbit: int = 0
    order: Order = None
    admiral: int = 0


@dataclass
class Production:
    kind: str                       # 'WLTH', 'FCLT', 'SCAN' or 'SHIP'
    item: int = None                # facility type or design id
    progress: int = 0               # production points toward the item
    hurried: bool = False
    queue: int = 0                  # production queue id, 0 for none
    entry: int = 0                  # the queue entry being built


@dataclass
class Planet:
    oid: int
    owner: int                      # 0 for unowned
    system: int                     # the SUN's object id
    pos: tuple
    name: str
    rates: tuple = None             # PLPR+4..+6, food, production and science per worker
    ore: int = None                 # the ship resource it mines, tables.RESOURCES index
    ore_rate: int = None            # ship resource per miner
    citizens: list = field(default_factory=list)    # [(job, owner)]
    military: list = field(default_factory=list)    # [(job, owner)]
    military_xp: list = field(default_factory=list)  # experience, one per soldier
    facilities: dict = field(default_factory=dict)  # type -> count
    food: tuple = None              # (store, capacity)
    recruit: int = None             # percent of food diverted to recruitment
    base_space: int = None
    lost_space: int = None          # battle damage
    production: Production = None
    founded: int = None             # turn
    pop_history: list = None        # 8 words, newest first; bit 12 marks pre-growth
    history_len: int = 0            # filled slots
    military_cost: int = None       # food per soldier
    military_food: int = None       # the military store
    governor: int = 0

    def jobs(self):
        out = {}
        for job, _owner in self.citizens:
            out[job] = out.get(job, 0) + 1
        return out


@dataclass
class Civ:
    oid: int
    name: str
    credits: int = None
    research: int = None            # points banked toward the topic, Owner:12
    uid: int = None                 # Owner:4; 0 leaves research unticked
    goal: int = None                # second research field, the topic aimed for
    resources: list = None          # tables.RESOURCES order
    completed: list = None          # technology ids
    topic: int = None               # technology being researched, -1 none
    traits: tuple = None
    active_turn: int = None         # SERV baseline, the turn last seen active
    active: bool = False            # SERV flag, set while active this turn
    explored: set = field(default_factory=set)      # EXSY, system ids entered
    stances: dict = field(default_factory=dict)     # other civ -> STANCES code


@dataclass
class Treaty:
    oid: int
    proposer: int
    recipient: int
    kind: int
    status: int
    length: int
    start: int


@dataclass
class Galaxy:
    turn: int
    content_version: int
    market: list                    # units the market holds, tables.RESOURCES order
    high_water: int                 # highest object id in use
    suns: dict                      # sun id -> pos
    radius: dict                    # sun or planet id -> radius
    civs: dict
    planets: dict
    ships: dict                     # every ship, in a fleet or not
    fleets: dict
    designs: dict
    treaties: dict
    gaps: list = field(default_factory=list)    # what the loader could not read

    def civ_planets(self, civ):
        return [p for p in self.planets.values() if p.owner == civ]

    def civ_ships(self, civ):
        return [s for s in self.ships.values() if s.owner == civ]
