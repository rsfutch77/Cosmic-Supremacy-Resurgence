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
