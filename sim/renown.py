"""
Score, reputation and crew experience from battles, the manual's
score-reputation-and-rank page, with no capture to check them against.

    score ratio   the enemy's score over 80% of one's own
    score         half the units destroyed, times that ratio, raised by the
                  enemy's negative reputation as a percent
    reputation    5 x log2 of the ratio, once per pair, at their first battle in
                  which units die: the side that declared the war and attacked
                  gains or loses it; a defender that destroys attackers gains
                  it; a gain needs 0.5% of the enemy's physical power destroyed
    experience    2 x the enemy's firepower over one's own, every kind counted and
                  the planet's turrets with its defenders, to every crew member of
                  an armed ship on a side that destroyed something; 2 x 545 / 590 =
                  1.85 on the turn-110 battle's two defending fighters, and nothing
                  for its colony ship

The engine keeps reputation at Owner:32 and its corruption reads it
(economy.empires). Its rule (0x00518480) clamps the score ratio to 0.1..10
before the log, and through turn 120 scales the figure by the turn over 120.
Live battles fit it to the unit: a defender gained the full figure, 12.759
and 13.522 at turns 123 and 125, and 11.656 of 12.601 in the battle that
ends turn 110 (x 111/120); an attacker lost 0.48 of it at three score ratios
(-2.045, -4.579 and -7.973, the last clamped) and gained it in full (+1.610).
Scores load from the save (Owner:28).
"""
import math
import struct

from . import research as rs
from . import tables as tb

SCORE_PER_UNIT = 0.5
BREAK_EVEN = 0.8
REPUTATION_PER_DOUBLING = 5
REPUTATION_RATIO = (0.1, 10.0)  # the score ratio's clamp before the log
FULL_REPUTATION_TURN = 120      # earlier turns scale it by turn / 120; 60 under game option 1
ATTACKER_LOSS_SHARE = 0.48      # of the manual's loss; a gain is paid in full
SIGNIFICANT = 0.005             # share of the enemy's physical power
EXPERIENCE_PER_RATIO = 2


def ratio(g, me, enemy):
    mine, theirs = g.civs[me].score, g.civs[enemy].score
    return max(theirs, 1.0) / (BREAK_EVEN * max(mine, 1.0))


def reputation(r, turn=FULL_REPUTATION_TURN):
    """The figure at score ratio `r` in a battle the engine resolves on `turn`."""
    lo, hi = REPUTATION_RATIO
    return (REPUTATION_PER_DOUBLING * math.log2(min(hi, max(lo, r)))
            * min(1.0, turn / FULL_REPUTATION_TURN))


def gain(g, me, enemy, units):
    """Score for destroying `units` of the enemy's."""
    rep = g.civs[enemy].reputation
    bonus = 1 + (-rep / 100 if rep < 0 else 0)
    return SCORE_PER_UNIT * units * ratio(g, me, enemy) * bonus


def battle(g, attacker, defender, lost_by_attacker, lost_by_defender,
           defender_power, attacker_power, at_planet=False):
    a_ratio, d_ratio = ratio(g, attacker, defender), ratio(g, defender, attacker)
    turn = g.turn + 1               # combat runs before the tick counts the new turn
    # Each side's reputation comes from its own first battle against the other
    # in which it scores: an attacker that destroyed nothing lost none.
    if lost_by_defender:
        g.civs[attacker].score += gain(g, attacker, defender, lost_by_defender)
        # The manual: moving to a planet one is at war with makes one the aggressor.
        if (attacker, defender) not in g.fought and (at_planet or (attacker, defender) in g.declared):
            g.fought.add((attacker, defender))
            rep = reputation(a_ratio, turn)
            if rep < 0:
                g.civs[attacker].reputation += rep * ATTACKER_LOSS_SHARE
            elif lost_by_defender >= SIGNIFICANT * defender_power:
                g.civs[attacker].reputation += rep
    if lost_by_attacker:
        g.civs[defender].score += gain(g, defender, attacker, lost_by_attacker)
        if (defender, attacker) not in g.fought:
            g.fought.add((defender, attacker))
            rep = reputation(d_ratio, turn)
            if rep > 0 and lost_by_attacker >= SIGNIFICANT * attacker_power:
                g.civs[defender].reputation += rep


def experience(home, away, planet_fire=0.0, home_scored=True, away_scored=True):
    """Every crew member of an armed ship gains 2 x the other side's total
    firepower over its own side's, on a side that destroyed something: a ship,
    or the planet's whole defence."""
    fire = [sum(sum(u.fire) for u in home) + planet_fire, sum(sum(u.fire) for u in away)]
    for side, own, other, scored in ((home, fire[0], fire[1], home_scored),
                                     (away, fire[1], fire[0], away_scored)):
        if not own or not scored:
            continue
        add = EXPERIENCE_PER_RATIO * other / own
        for u in side:
            s = u.ship
            if u.hp > 1e-9 and s.crew and sum(u.fire):
                s.crew_xp = [f16_down(x + add) for x in (s.crew_xp or [0.0] * len(s.crew))]


def f16_down(x):
    """`x` as the half float a soldier record holds, rounded toward zero."""
    h = struct.unpack('<e', struct.pack('<e', x))[0]
    if abs(h) > abs(x):
        bits = struct.unpack('<H', struct.pack('<e', h))[0]
        h = struct.unpack('<e', struct.pack('<H', bits - 1))[0]
    return h


# The engine rebuilds every civ's score at the end of the tick (0x0052DFE0,
# Owner::ComputeScore at 0x0053E890), skipping a civ whose Owner:4 is 0. Score
# is three stored accumulators, one of which gains 10 for each planet held at
# every rebuild, plus terms read from the civ's state then. At content version
# 565 those are 1000 a planet, 10 a citizen, 10 a soldier on a planet or aboard
# a ship, a tenth of each facility's cost and of each ship part's, and half a
# tenth of each completed technology's cost. The simulator adds the change in
# those terms and the planets' 10; battles add their score as they happen.

PLANET_TURN = 10                # the accumulator's gain for each planet, every rebuild
PLANET = 1000
CITIZEN = SOLDIER = 10


def score_terms(g, civ):
    """The state part of a civ's score, as the rebuild reads it."""
    total = 0
    for p in g.planets.values():
        if p.owner != civ:
            continue
        total += PLANET + CITIZEN * len(p.citizens) + SOLDIER * len(p.military)
        total += sum(int(tb.FACILITIES[f].cost / 10) * n for f, n in p.facilities.items()
                     if f < len(tb.FACILITIES))
    for s in g.ships.values():
        if s.owner == civ and s.design in g.designs:
            total += sum(int(part.cost / 10) for _c, part in g.designs[s.design]._all())
            total += SOLDIER * len(s.crew)
    done = g.civs[civ].completed or []
    total += sum(int(int(rs.cost(t, done[:i]) / 10) / 2) for i, t in enumerate(done))
    return total


def score_before(g):
    """Record each civ's state terms as the tick starts."""
    g.score_terms = {c: score_terms(g, c) for c, civ in g.civs.items() if civ.uid}


def score_rebuild(g):
    """The tick's rebuild: 10 for each planet held, and the change in the state
    terms since the tick started."""
    for c, before in g.score_terms.items():
        civ = g.civs[c]
        planets = sum(1 for p in g.planets.values() if p.owner == c)
        civ.score = (civ.score or 0) + PLANET_TURN * planets + score_terms(g, c) - before
    g.score_terms = {}
