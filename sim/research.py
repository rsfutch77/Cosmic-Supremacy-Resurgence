"""
The research step of a tick: a civ's banked points finish its topic.

Costs are the manual's: round(800 x the technology's factor) x the number of
technologies already completed. The factors are client/dev_tools/ai_player/
research.py's table; capture probes at the released content version confirmed
every one they reached, 31 technologies (docs/Fast_Sim_Plan.md).

A civ finishes at most one technology a tick. With a goal deeper in the tree
the topic walks the goal's missing prerequisites, cheapest available first and
the lower id on a tie; topic and goal both clear when the goal is done.
"""

from . import traits

COST_SCALE = 800
NO_TOPIC = -1

# id: (name, costFactor, prerequisites (satisfy ANY for a doctrine), excluded-by ids)
TECHS = {
     0: ('Nuclear', 0.0, (), ()),
     1: ('Space Travel', 0.0, (), ()),
     2: ('Astro Engineering', 3.0, (12,), ()),
     3: ('Cold Fusion', 1.0, (0,), ()),
     4: ('Tachyon Physics', 3.2, (13,), ()),
     5: ('Robotics Engineering', 15.0, (22,), ()),
     6: ('Gravity Controller', 9.0, (7,), ()),
     7: ('Quantum Fields', 2.8, (3, 12), ()),
     8: ('Anti-Matter Fission', 25.0, (15,), ()),
     9: ('Dark-Matter Control', 24.0, (41,), ()),
    10: ('Subspace Controller', 35.0, (9,), ()),
    11: ('Hyperspace Controller', 47.0, (10,), ()),
    12: ('Advanced Magnetism', 1.0, (1,), ()),
    13: ('Photon Mechanics', 2.0, (3,), ()),
    14: ('Plasma Physics', 11.0, (4,), ()),
    15: ('Proton Controller', 17.0, (14,), ()),
    16: ('Nano Mechanics', 34.0, (40,), ()),
    17: ('Superstring Theory', 51.0, (16,), ()),
    18: ('Advanced Networking', 1.0, (1,), ()),
    19: ('Artificial Intelligence', 14.0, (24,), ()),
    20: ('Advanced Tactics', 24.0, (19,), ()),
    21: ('Deep-Space Scanning', 32.0, (20,), ()),
    22: ('Particle Engineering', 8.0, (2,), ()),
    23: ('Basic Scanning', 3.6, (18,), ()),
    24: ('Advanced Scanning', 10.0, (23,), ()),
    25: ('Wormhole Controller', 49.0, (42,), ()),
    26: ('Magnetic Monopoles', 62.0, (25,), ()),
    27: ('Planetary Defense Lvl 1', 1.0, (), ()),
    28: ('Planetary Defense Lvl 2', 2.5, (27,), ()),
    29: ('Planetary Defense Lvl 3', 5.0, (28,), ()),
    30: ('Planetary Defense Lvl 4', 10.0, (29,), ()),
    31: ('Planetary Defense Lvl 5', 17.0, (30,), ()),
    32: ('Planetary Defense Lvl 6', 27.0, (31,), ()),
    33: ('Planetary Defense Lvl 7', 41.0, (32,), ()),
    34: ('Planetary Defense Lvl 8', 57.0, (33,), ()),
    35: ('Mining', 6.0, (2,), ()),
    36: ('Propaganda', 2.0, (18,), ()),
    37: ('Robo Mining', 17.0, (5, 35), ()),
    38: ('Bunker', 1.5, (27,), ()),
    39: ('Banking', 2.0, (18,), ()),
    40: ('Advanced Manufacturing', 22.0, (5,), ()),
    41: ('Anti-Matter Control', 16.0, (6,), ()),
    42: ('Dark-Matter Fission', 36.0, (8,), ()),
    43: ('Doctrine: Mobility', 1.0, (), (44, 45, 46)),
    44: ('Doctrine: Colonization', 1.0, (), (43, 45, 46)),
    45: ('Doctrine: Reproduction', 1.0, (), (43, 44, 46)),
    46: ('Doctrine: Metallurgy', 1.0, (), (43, 44, 45)),
    47: ('Doctrine: Knowledge', 2.7, (43, 44), (48, 49)),
    48: ('Doctrine: Manufacturing', 2.3, (43, 44, 45, 46), (47, 49)),
    49: ('Doctrine: Trade', 2.0, (45, 46), (47, 48)),
    50: ('Doctrine: Stealth', 6.5, (47, 48, 43), (54, 55, 56)),
    51: ('Planetary Fortress', 8.0, (29,), ()),
    52: ('Biological Warfare', 3.0, (13,), ()),
    53: ('Cloaking', 11.0, (24,), ()),
    54: ('Doctrine: Stronghold', 7.5, (47, 48), (50, 55, 56)),
    55: ('Doctrine: Finance', 7.0, (47, 48, 48, 49), (50, 54, 56)),
    56: ('Doctrine: Elite', 6.0, (48, 49), (50, 54, 55)),
    57: ('Doctrine: Intelligence', 15.0, (50, 54, 47), (58, 59, 60)),
    58: ('Doctrine: Versatility', 16.0, (50, 54), (57, 59, 60)),
    59: ('Doctrine: Propaganda', 15.5, (50, 54, 54, 55, 56), (57, 58, 60)),
    60: ('Doctrine: Juggernaut', 16.0, (54, 55, 56), (57, 58, 59)),
    61: ('Doctrine: Invisibility', 27.0, (50,), (62, 63, 64)),
    62: ('Doctrine: Habitat', 30.0, (57, 58, 59, 60), (61, 63, 64)),
    63: ('Doctrine: Police State', 29.0, (57, 58, 59, 60), (61, 62, 64)),
    64: ('Doctrine: Dreadnought', 28.0, (60,), (61, 62, 63)),
    65: ('Doctrine: Lightweight', 45.0, (61, 62, 63, 64), (66, 67, 68)),
    66: ('Doctrine: Bio-Armor', 47.0, (61, 62, 63, 64), (65, 67, 68)),
    67: ('Doctrine: Golden Age', 50.0, (61, 62, 63, 64), (65, 66, 68)),
    68: ('Doctrine: Titan', 46.0, (64,), (65, 66, 67)),
    69: ('Hyperspace-Energy Lvl 1', 9.0, (29,), ()),
    70: ('Hyperspace-Energy Lvl 2', 15.0, (69,), ()),
    71: ('Hyperspace-Energy Lvl 3', 25.0, (70,), ()),
    72: ('Hyperspace-Energy Lvl 4', 37.0, (71,), ()),
    77: ('Artificial Wormholes', 13.0, (19,), ()),
    78: ('Command Center', 20.0, (20,), ()),
    79: ('Surveillance Scanning', 19.0, (19,), ()),
}


def cost(tech, completed):
    rec = TECHS.get(tech)
    if rec is None:
        return None
    return round(COST_SCALE * rec[1]) * len(completed)


DOCTRINE_TIERS = ((43, 44, 45, 46), (47, 48, 49), (50, 54, 55, 56),
                  (57, 58, 59, 60), (61, 62, 63, 64), (65, 66, 67, 68))


def is_doctrine(tech):
    return TECHS[tech][0].startswith('Doctrine:')


def groups(tech):
    """A technology's prerequisites as groups that must all be met, any one
    member meeting a group. A technology needs every prerequisite; a doctrine
    needs one from each doctrine tier its list names, which is how the capture
    took Mobility, then Knowledge, then Stealth."""
    pre = TECHS[tech][2]
    if not is_doctrine(tech):
        return [(p,) for p in pre]
    out = []
    for tier in DOCTRINE_TIERS:
        members = tuple(dict.fromkeys(p for p in pre if p in tier))
        if members:
            out.append(members)
    return out


def available(tech, done):
    """Prerequisites met (all of them for a technology, any for a doctrine)
    and not locked out by a rival doctrine."""
    rec = TECHS.get(tech)
    if rec is None or tech in done or any(x in done for x in rec[3]):
        return False
    return all(any(p in done for p in group) for group in groups(tech))


def needed(goal, done, out=None):
    """The goal and every missing prerequisite under it, taking the first
    listed member of a group none of whose members is done."""
    out = set() if out is None else out
    if goal in done or goal in out or goal not in TECHS:
        return out
    out.add(goal)
    for group in groups(goal):
        if not any(p in done for p in group):
            needed(group[0], done, out)
    return out


def next_topic(goal, done):
    open_now = [t for t in needed(goal, done) if available(t, done)]
    return min(open_now, key=lambda t: (TECHS[t][1], t)) if open_now else NO_TOPIC


def civs(g):
    """Finish each civ's topic when its bank covers the price, then aim at the
    next step toward its goal."""
    for civ in g.civs.values():
        if not civ.uid or civ.topic is None or civ.topic < 0:
            continue
        if civ.research is None or civ.completed is None:
            continue
        goal = civ.goal if civ.goal is not None and civ.goal >= 0 else civ.topic
        if not available(civ.topic, civ.completed):
            civ.topic = next_topic(goal, civ.completed)
            if civ.topic < 0:
                continue
        price = cost(civ.topic, civ.completed)
        if price is None or civ.research < price:
            continue
        civ.research -= price
        civ.completed.append(civ.topic)
        if civ.topic == traits.GOLDEN_AGE:
            # The probe read 99 the turn after and took the +50 from then on.
            civ.golden_age = traits.GOLDEN_AGE_TURNS - 1
        civ.topic = next_topic(goal, civ.completed)
        civ.goal = goal if civ.topic >= 0 else NO_TOPIC
