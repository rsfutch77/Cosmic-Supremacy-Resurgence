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
                  an armed ship; measured once: 2 x 545 / 590 = 1.85 on the turn-110
                  battle's two defending fighters, and nothing for its colony ship

The engine ignores reputation: one save ticked with every relation of a civ
at 0, +100 and -100, set by the engine's own AddReputation, came out the same
to the credit. The simulator still applies the manual's reputation term to
corruption (economy.empires), a rule the engine does not have. Scores start at
0, since the save's score is not read.
"""
import math

SCORE_PER_UNIT = 0.5
BREAK_EVEN = 0.8
REPUTATION_PER_DOUBLING = 5
SIGNIFICANT = 0.005             # share of the enemy's physical power
EXPERIENCE_PER_RATIO = 2


def ratio(g, me, enemy):
    mine, theirs = g.civs[me].score, g.civs[enemy].score
    return max(theirs, 1.0) / (BREAK_EVEN * max(mine, 1.0))


def gain(g, me, enemy, units):
    """Score for destroying `units` of the enemy's."""
    rep = g.civs[enemy].reputation
    bonus = 1 + (-rep / 100 if rep < 0 else 0)
    return SCORE_PER_UNIT * units * ratio(g, me, enemy) * bonus


def battle(g, attacker, defender, lost_by_attacker, lost_by_defender,
           defender_power, attacker_power):
    if not (lost_by_attacker or lost_by_defender):
        return
    first = frozenset((attacker, defender)) not in g.fought
    a_ratio, d_ratio = ratio(g, attacker, defender), ratio(g, defender, attacker)
    if lost_by_defender:
        g.civs[attacker].score += gain(g, attacker, defender, lost_by_defender)
    if lost_by_attacker:
        g.civs[defender].score += gain(g, defender, attacker, lost_by_attacker)
    if not first:
        return
    g.fought.add(frozenset((attacker, defender)))
    if (attacker, defender) in g.declared:
        rep = REPUTATION_PER_DOUBLING * math.log2(a_ratio)
        if rep < 0 or lost_by_defender >= SIGNIFICANT * defender_power:
            g.civs[attacker].reputation += rep
    if lost_by_attacker:
        rep = REPUTATION_PER_DOUBLING * math.log2(d_ratio)
        if rep > 0 and lost_by_attacker >= SIGNIFICANT * attacker_power:
            g.civs[defender].reputation += rep


def experience(home, away, planet_fire=0.0):
    """Every crew member of an armed ship gains 2 x the other side's total
    firepower over its own side's."""
    fire = [sum(sum(u.fire) for u in home) + planet_fire, sum(sum(u.fire) for u in away)]
    for side, own, other in ((home, fire[0], fire[1]), (away, fire[1], fire[0])):
        if not own:
            continue
        add = EXPERIENCE_PER_RATIO * other / own
        for u in side:
            s = u.ship
            if u.hp > 1e-9 and s.crew and sum(u.fire):
                s.crew_xp = [x + add for x in (s.crew_xp or [0.0] * len(s.crew))]
