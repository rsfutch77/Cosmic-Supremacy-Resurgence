"""
A civ's traits as the engine applies them (GetTrait 0x00516180): its
custom-civ trait values (CVTR, engine traits 0 to 9), plus every completed
technology's trait grant, plus 50 on the four output traits in a golden age.

Engine trait t is Owner:(748 + 4t). The names are the ones the client's trait
list showed for each offset (client/dev_tools/ejbo_annotations.json). Where a
trait applies is checked against a capture only where its comment says so.

GRANTS is client/dev_tools/ai_player/research.py's table, read from a client
at a content version other than the released one.
"""
FACILITY, TRAIT = 0, 11                 # grant kinds
NAMES = {
    0: 'food eaten per citizen, added to 10',
    1: 'income per citizen, added to 10',
    2: 'food output %', 3: 'production output %', 4: 'science output %', 5: 'mining output %',
    6: 'ship speed %', 7: 'ship firepower %', 8: 'planetary defence firepower %',
    9: 'stationed military training, effect not found: training ignores it',
    10: 'ship shield strength, points', 11: 'ship units %', 12: 'ship cloaking %',
    13: 'ship scanner range %', 14: 'ship production cost %', 15: 'ship upkeep %',
    16: 'ship hull space %', 17: 'ship hitpoints %', 18: 'light ship speed %',
    19: 'heavy ship firepower %', 20: 'battleship hull space %', 21: 'battleship cost and upkeep %',
    22: 'ship repair per turn %', 23: 'colony module production cost %',
    24: 'cloaking module production cost %', 25: 'scan production cost %',
    26: 'farm production cost %', 27: 'factory production cost %',
    28: 'university production cost %', 29: 'mine production cost %',
    30: 'planetary defence building production cost %', 31: 'military training speed %',
    32: 'military upkeep, credits a soldier added to 3', 33: 'military food cost %', 34: 'military-induced loyalty %',
    35: 'propaganda office loyalty %', 36: 'population loyalty %', 37: 'income per banker %',
    38: 'planet space %', 39: 'science wastage, points', 40: 'Wealth output %',
    41: 'extra metal per turn', 42: 'market price %', 43: 'golden age', 44: 'dark age',
}
SHIP_SPEED, LIGHT_SHIP_SPEED = 6, 18
PLANET_SPACE = 38
OUTPUTS = (2, 3, 4, 5)
GOLDEN_AGE_BONUS = 50
GOLDEN_AGE, GOLDEN_AGE_TURNS = 67, 100     # the doctrine, and the 100 its grant carries

# technology id: ((kind, object id, value), ...)
GRANTS = {
     0: ((6, 0, 0), (4, 0, 0)),
     1: ((1, 0, 0), (0, 0, 0), (2, 0, 0), (0, 2, 0)),
     2: ((0, 1, 0), (1, 1, 0), (2, 2, 0)),
     3: ((4, 1, 0), (3, 0, 0), (3, 10, 0)),
     4: ((3, 2, 0),),
     5: ((1, 3, 0),),
     6: ((4, 3, 0), (5, 2, 0)),
     7: ((4, 2, 0), (5, 1, 0)),
     8: ((3, 12, 0), (3, 7, 0)),
     9: ((4, 5, 0),),
    10: ((5, 3, 0), (4, 6, 0)),
    11: ((5, 4, 0),),
    12: ((5, 0, 0), (2, 1, 0)),
    13: ((3, 1, 0), (3, 4, 0)),
    14: ((3, 5, 0), (3, 11, 0)),
    15: ((3, 6, 0), (3, 3, 0)),
    16: ((1, 4, 0),),
    17: ((1, 5, 0),),
    18: ((0, 4, 0), (0, 6, 0)),
    19: ((0, 5, 0), (9, 6, 0)),
    20: ((0, 7, 0), (9, 4, 0), (6, 2, 0)),
    21: ((6, 3, 0), (9, 1, 0)),
    22: ((1, 2, 0),),
    23: ((0, 8, 0), (6, 1, 0), (9, 2, 0), (9, 3, 0)),
    24: ((9, 0, 0), (9, 5, 0)),
    25: ((3, 9, 0),),
    26: ((3, 14, 0),),
    27: ((10, 0, 0), (0, 9, 0)),
    28: ((10, 1, 0),),
    29: ((10, 2, 0), (0, 11, 0)),
    30: ((10, 3, 0), (0, 10, 0)),
    31: ((10, 4, 0),),
    32: ((10, 5, 0),),
    33: ((10, 6, 0),),
    34: ((10, 7, 0),),
    35: ((0, 13, 0),),
    36: ((0, 12, 0),),
    37: ((0, 14, 0),),
    38: ((0, 15, 0),),
    39: ((0, 16, 0),),
    40: ((0, 3, 0),),
    41: ((4, 4, 0),),
    42: ((3, 13, 0), (3, 8, 0)),
    43: ((11, 18, 20), (11, 10, -10)),
    44: ((11, 23, -50), (11, 33, -15)),
    45: ((11, 26, -30), (11, 0, -1)),
    46: ((11, 41, 50), (11, 5, 15), (11, 29, -30)),
    47: ((11, 28, -30), (11, 39, -7), (11, 13, 20)),
    48: ((11, 27, -30), (11, 3, 5)),
    49: ((11, 42, 50), (11, 37, 15), (11, 1, 4), (11, 6, 5), (11, 2, 5)),
    50: ((11, 12, 5), (11, 24, -75), (11, 17, 10), (11, 7, 15), (11, 19, -15)),
    51: ((0, 17, 0),),
    52: ((2, 4, 0),),
    53: ((2, 3, 0),),
    54: ((11, 30, -10), (11, 8, 15), (11, 38, 10), (11, 1, 2), (11, 9, -5)),
    55: ((11, 37, 75), (11, 5, -7)),
    56: ((11, 31, 70), (11, 9, 20), (11, 32, 1)),
    57: ((11, 18, 15), (11, 13, 30), (11, 12, 5), (11, 39, -5), (11, 25, -75), (11, 45, 1)),
    58: ((11, 40, 150), (11, 16, 5), (11, 14, -5), (11, 6, 5)),
    59: ((11, 35, 100), (11, 3, 7), (11, 4, 7)),
    60: ((11, 17, 25), (11, 15, -10)),
    61: ((11, 12, 10), (11, 16, 25), (11, 17, 5), (11, 19, -80), (11, 20, -50)),
    62: ((11, 38, 20), (11, 2, 7), (11, 30, -15), (11, 5, -15)),
    63: ((11, 34, 75), (11, 31, 50), (11, 7, 25)),
    64: ((11, 19, 30), (11, 17, 20)),
    65: ((11, 11, -25), (11, 15, -30)),
    66: ((11, 22, 7), (11, 17, 5), (11, 9, 10), (11, 8, 10)),
    67: ((11, 43, 100),),
    68: ((11, 20, 25), (11, 21, -5)),
    69: ((12, 0, 0), (0, 18, 0), (0, 19, 0)),
    70: ((12, 1, 0),),
    71: ((12, 2, 0),),
    72: ((12, 3, 0),),
    77: ((2, 5, 0),),
    78: ((0, 20, 0),),
    79: ((9, 7, 0),),
}


def value(civ, t):
    """Engine trait `t` for a civ, in its own units."""
    if civ is None:
        return 0
    base = civ.traits[t] if civ.traits and t < len(civ.traits) else 0
    if t in OUTPUTS and getattr(civ, 'golden_age', 0) > 0:
        base += GOLDEN_AGE_BONUS
    if t in (43, 44):
        return base
    return base + sum(v for tech in (civ.completed or ())
                      for kind, oid, v in GRANTS.get(tech, ()) if kind == TRAIT and oid == t)


def pct(civ, t, amount):
    """`amount` raised or lowered by trait `t` as a percent."""
    v = value(civ, t)
    return amount * (100 + v) / 100 if v else amount
