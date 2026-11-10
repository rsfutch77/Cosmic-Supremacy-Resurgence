"""
A scripted aggressor for self-play: the baseline's economy, plus a fleet and
an invasion on a chosen turn.

It researches Cold Fusion for the Mass Driver and the Fusion Bomb, designs a
fighter and a bomber on the Shuttle chassis, builds `fighters` and `bombers`,
crews every ship to its seats so each lands one trooper, and on `attack_turn`
declares war and sends the fleet at the enemy's most populous planet, timing
each ship's departure so they arrive together.

It is a probe for the reward, not an opponent to train against for long:
docs/Fast_Sim_Plan.md asks whether the civ with the higher dominance wins.
"""
import math

from . import baseline
from . import movement as mv
from . import orders as od

COLD_FUSION = 3
FIGHTER = {'chassis': [0], 'engines': [0, 0], 'weapons': [0]}                # Mass Driver
BOMBER = {'chassis': [0], 'engines': [0, 0], 'weapons': [10]}                # Fusion Bomb


class Warlike:
    def __init__(self, civ, attack_turn, fighters, bombers):
        self.civ, self.attack_turn = civ, attack_turn
        self.want = {'fighter': fighters, 'bomber': bombers}
        self.designs = {}
        self.launch = None          # {ship: turn to leave}, set when the attack starts
        self.target = None

    def act(self, g):
        civ, c = self.civ, g.civs[self.civ]
        out = [a for a in baseline.act(g, civ) if a[0] not in ('ship', 'research')]
        if COLD_FUSION not in (c.completed or ()):
            if c.topic != COLD_FUSION:
                out.append(('research', COLD_FUSION))
        elif c.topic is None or c.topic < 0:
            out += [a for a in baseline.act(g, civ) if a[0] == 'research']
        if COLD_FUSION in (c.completed or ()) and not self.designs:
            for name, parts in (('fighter', FIGHTER), ('bomber', BOMBER)):
                ok, _ = od.design(g, civ, name, parts)
                if ok:
                    self.designs[name] = g.high_water
        mine = [p for p in g.planets.values() if p.owner == civ]
        fleet = self.fleet(g)
        for p in mine:
            if p.recruit != od.RECRUIT_BASE:
                out.append(('recruit', p.oid, od.RECRUIT_BASE))
            prod = p.production
            if self.designs and p.facilities.get(od.SHIPYARD) and (prod is None or prod.kind == 'WLTH'):
                for name in ('bomber', 'fighter'):
                    if len(fleet[name]) < self.want[name]:
                        out = [a for a in out if not (a[0] == 'facility' and a[1] == p.oid)]
                        out.append(('ship', p.oid, self.designs[name]))
                        break
        for s in fleet['fighter'] + fleet['bomber']:
            here = g.planets.get(s.orbit)
            seats = 2 - len(s.crew)
            if here is not None and here.owner == civ and seats > 0 and here.military:
                out.append(('crew', s.oid, min(seats, len(here.military))))
        out += self.attack(g, fleet)
        return out

    def fleet(self, g):
        out = {'fighter': [], 'bomber': []}
        for s in g.ships.values():
            for name, oid in self.designs.items():
                if s.owner == self.civ and s.design == oid:
                    out[name].append(s)
        return out

    def attack(self, g, fleet):
        ships = fleet['fighter'] + fleet['bomber']
        if self.launch is None:
            ready = (g.turn >= self.attack_turn and len(fleet['fighter']) >= self.want['fighter']
                     and len(fleet['bomber']) >= self.want['bomber']
                     and all(len(s.crew) == 2 for s in ships))
            enemies = [p for p in g.planets.values() if p.owner and p.owner != self.civ]
            if not ready or not enemies:
                return []
            self.target = max(enemies, key=lambda p: len(p.citizens))
            eta = {s.oid: math.dist(s.pos, self.target.pos) / mv.speed(g, s) for s in ships}
            longest = max(eta.values())
            self.launch = {oid: g.turn + int(longest - t) for oid, t in eta.items()}
            out = [('war', self.target.owner)]
        else:
            out = []
        for s in ships:
            if s.oid in self.launch and self.launch[s.oid] <= g.turn and s.order is None \
                    and s.orbit != self.target.oid:
                out.append(('move', s.oid, self.target.oid))
        return out
