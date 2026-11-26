"""
The training loop's view of a game: reset, then step with each civ's orders.

    env = Env.from_blob('client/SinglePlayerGalaxy.dat')
    obs = env.reset()
    obs, rewards, done, info = env.step({civ: [('jobs', planet, {0: 4, 1: 3}), ...]})

Orders are sim/orders.py's actions. A civ that orders anything, or is listed as
played, counts as active, so the engine's inactivity penalty never reaches it.
The reward is the change in dominance D over the step (potential-based
shaping), and D itself on the last step.

The observation is what the client would show the civ: everything it owns,
every system it has looked at as it was when it last looked (sim/fog.py,
measured against EXSY), other civs' ships its scanners reach, and its scan
reports and news.
"""
import copy

from . import fog
from . import load as sl
from . import orders as od
from . import reward as rw
from . import tick as tk


class Env:
    def __init__(self, galaxy, played=None, horizon=200, k=rw.K_TURNS, observe=True, reward=True):
        self.start = galaxy
        self.observing = observe     # False: reset and step return no observations
        self.rewarding = reward      # False: step leaves D alone and returns no rewards
        self.played = list(played or galaxy.civs)
        self.horizon, self.k = horizon, k
        self.g = None

    @classmethod
    def from_blob(cls, path, **kw):
        return cls(sl.load_path(path), **kw)

    def reset(self):
        self.g = copy.deepcopy(self.start)
        self.t = 0
        self.d = rw.dominance(self.g, self.played) if self.rewarding else None
        return self.observations()

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
        if not self.rewarding:
            alive = {p.owner for p in self.g.planets.values()} | {s.owner for s in self.g.ships.values()}
            done = self.t >= self.horizon or sum(1 for c in self.played if c in alive) <= 1
            return self.observations(), None, done, info
        d = rw.dominance(self.g, self.played)
        done = self.t >= self.horizon or sum(1 for v in d.values() if v > 0) <= 1
        rewards = {c: (d[c] if done else d[c] - self.d[c]) for c in self.played}
        self.d = d
        return self.observations(), rewards, done, info

    def observations(self):
        if not self.observing:
            return None
        return {c: observe(self.g, c) for c in self.played}


def observe(g, civ):
    c = g.civs[civ]
    mine = [p for p in g.planets.values() if p.owner == civ]
    ships = [s for s in g.ships.values() if s.owner == civ]
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
        'known_planets': [{'id': oid, 'system': sid, 'seen': turn, 'owner': owner,
                           'population': pop, 'pos': g.planets[oid].pos,
                           'rates': g.planets[oid].rates, 'ore': g.planets[oid].ore,
                           'ore_rate': g.planets[oid].ore_rate}
                          for sid, (turn, rows) in c.seen.items()
                          for oid, (owner, pop) in rows.items() if owner != civ],
        'contacts': [{'id': s.oid, 'owner': s.owner, 'design': s.design, 'pos': s.pos,
                      'orbit': s.orbit} for s in fog.visible_ships(g, civ)],
        'scans': dict(c.scans), 'intel': list(c.intel[-20:]), 'news': list(c.news[-20:]),
        'score': c.score, 'reputation': c.reputation,
        'suns': {sid: pos for sid, pos in g.suns.items()},
    }
