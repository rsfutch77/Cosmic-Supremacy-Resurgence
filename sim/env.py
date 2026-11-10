"""
The training loop's view of a game: reset, then step with each civ's orders.

    env = Env.from_blob('client/SinglePlayerGalaxy.dat')
    obs = env.reset()
    obs, rewards, done, info = env.step({civ: [('jobs', planet, {0: 4, 1: 3}), ...]})

Orders are sim/orders.py's actions. A civ that orders anything, or is listed as
played, counts as active, so the engine's inactivity penalty never reaches it.
The reward is the change in dominance D over the step (potential-based
shaping), and D itself on the last step.

The observation is fog-limited, approximately: everything the civ owns, the
planets of every system it has entered as they stand now, and other civs'
ships within its ships' scanner range or in orbit of its planets. The client
shows a system as it was when last seen; that staleness is not modelled.
"""
import copy
import math

from . import load as sl
from . import orders as od
from . import reward as rw
from . import tables as tb
from . import tick as tk


class Env:
    def __init__(self, galaxy, played=None, horizon=200, k=rw.K_TURNS):
        self.start = galaxy
        self.played = list(played or galaxy.civs)
        self.horizon, self.k = horizon, k
        self.g = None

    @classmethod
    def from_blob(cls, path, **kw):
        return cls(sl.load_path(path), **kw)

    def reset(self):
        self.g = copy.deepcopy(self.start)
        self.t = 0
        self.d = rw.dominance(self.g, self.played)
        return {c: observe(self.g, c) for c in self.played}

    def step(self, actions):
        info = {c: [] for c in self.played}
        for civ, acts in (actions or {}).items():
            for a in acts:
                ok, why = od.apply(self.g, civ, a)
                if not ok:
                    info.setdefault(civ, []).append((a, why))
        for civ in self.played:
            self.g.civs[civ].active = True
        self.g = tk.tick(self.g, copy_first=False)
        self.t += 1
        d = rw.dominance(self.g, self.played)
        done = self.t >= self.horizon or sum(1 for v in d.values() if v > 0) <= 1
        rewards = {c: (d[c] if done else d[c] - self.d[c]) for c in self.played}
        self.d = d
        return {c: observe(self.g, c) for c in self.played}, rewards, done, info


def scan_range(g, ship):
    d = g.designs.get(ship.design)
    if d is None:
        return 0
    return max((tb.SCANNERS[i].stat for i in d.parts.get('scanners') or ()), default=0)


def observe(g, civ):
    c = g.civs[civ]
    mine = [p for p in g.planets.values() if p.owner == civ]
    ships = [s for s in g.ships.values() if s.owner == civ]
    seen_systems = set(c.explored) | {p.system for p in mine}
    eyes = [(s.pos, scan_range(g, s)) for s in ships]
    held = {p.oid for p in mine}

    def visible(s):
        return s.orbit in held or any(math.dist(s.pos, at) <= r for at, r in eyes)

    return {
        'turn': g.turn,
        'civ': {'credits': c.credits, 'resources': list(c.resources or ()),
                'research': c.research, 'topic': c.topic, 'goal': c.goal,
                'completed': list(c.completed or ()), 'stances': dict(c.stances)},
        'planets': [{'id': p.oid, 'system': p.system, 'pos': p.pos, 'rates': p.rates,
                     'ore': p.ore, 'ore_rate': p.ore_rate, 'jobs': p.jobs(),
                     'soldiers': len(p.military), 'facilities': dict(p.facilities),
                     'food': p.food, 'recruit': p.recruit, 'space': p.base_space,
                     'production': p.production and (p.production.kind, p.production.item,
                                                     p.production.progress,
                                                     p.production.hurried)}
                    for p in mine],
        'ships': [{'id': s.oid, 'design': s.design, 'pos': s.pos, 'orbit': s.orbit,
                   'crew': len(s.crew), 'condition': s.condition,
                   'order': s.order and (s.order.kind, s.order.target, s.order.progress)}
                  for s in ships],
        'designs': [d.oid for d in g.designs.values() if d.owner == civ and not d.inactive],
        'known_planets': [{'id': p.oid, 'system': p.system, 'pos': p.pos, 'owner': p.owner,
                           'population': len(p.citizens), 'rates': p.rates}
                          for p in g.planets.values()
                          if p.owner != civ and p.system in seen_systems],
        'contacts': [{'id': s.oid, 'owner': s.owner, 'design': s.design, 'pos': s.pos,
                      'orbit': s.orbit}
                     for s in g.ships.values() if s.owner != civ and visible(s)],
        'suns': {sid: pos for sid, pos in g.suns.items()},
    }
