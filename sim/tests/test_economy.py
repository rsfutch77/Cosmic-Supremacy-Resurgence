"""
test_economy.py: the planet step's food, corruption and growth rules
====================================================================
    server\\.venv\\Scripts\\python.exe sim\\tests\\test_economy.py

The food-store sizes are the manual's table (food-storage-size-calculation).
The yields and corruption losses are engine ticks from the referee archive:
planet 139 at turn 13 has 11 citizens, 7 of them farmers, base food rate 62,
one farm, a civ food trait of 28 and space for 35, in a civ of two planets.
Its farmers make 700 food; corruption takes 3 and 110 is eaten, so the store
gains 587, which the engine recorded.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from sim import economy as ec                                   # noqa: E402
from sim import state as st                                     # noqa: E402

PASS, FAIL = [], []
MANUAL_STORE = {1: 40, 2: 80, 3: 120, 4: 240, 5: 360, 6: 480, 7: 600, 8: 760,
                9: 920, 10: 1120, 13: 1680, 20: 3320, 37: 8680, 50: 13760,
                73: 24440}


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def planet(pop, farmers, rate, farms, food, space=350):
    citizens = [(0, 9)] * farmers + [(1, 9)] * (pop - farmers)
    return st.Planet(1, 9, 0, (0, 0, 0), 'p', rates=(rate, 30, 40),
                     citizens=list(citizens), facilities={0: farms} if farms else {},
                     food=food, recruit=0, base_space=space, lost_space=0)


def main():
    for pop, size in MANUAL_STORE.items():
        check(f'store for {pop} citizens is the manual\'s', ec.food_capacity(pop), size)
    check('R rounds half away from zero', (ec.R(2.5), ec.R(-2.5), ec.R(2.49)),
          (3, -3, 2))

    civ = st.Civ(9, 'c', traits=(0, 0, 28, 0, 0, 0, 0, 0, 0, 0), completed=[])
    p = planet(11, 7, 62, 1, (986, 1280))
    check('farmer yield with a farm and the trait', ec.farmer_yield(p, civ), 100)
    empire = ec.empire_corruption(2)
    corruption = ec.planet_corruption(empire, 11, ec.max_pop(p, 0))
    check('max population is space over ten', ec.max_pop(p, 0), 35)
    check('corruption takes 3 of 700', 700 - ec.corrupted(700, corruption), 3)
    ec.food(p, civ, 0, empire)
    check('the store fills and a farmer is born', (p.food, len(p.citizens)),
          ((986 + 587 - 1280, ec.food_capacity(12)), 12))
    check('the new citizen farms for the planet\'s owner', p.citizens[0], (0, 9))

    one = ec.empire_corruption(1)
    small = planet(8, 5, 32, 0, (100, 760), space=300)
    ec.food(small, st.Civ(9, 'c', traits=(0,) * 10, completed=[]), 0, one)
    check('a lone planet loses nothing to rounding', small.food, (180, 760))

    # Planet 384 in the war blobs: 18 citizens fall to 15 and the store size
    # walks down over the following turns, as the engine ticked it.
    old = ec.HISTORY_OLD
    lost = planet(15, 15, 32, 0, (0, 2840))
    lost.pop_history = [16, 18, 18, 18, 17 | old, 17 | old, 17 | old, 17 | old]
    lost.history_len = 8
    sizes = [ec.push_history(lost, False) for _ in range(6)]
    check('a loss walks the store size down', sizes,
          [2600, 2600, 2600, 2600, 2360, 2360])
    check('the ring keeps eight entries, newest first', lost.pop_history,
          [15, 15, 15, 15, 15, 15, 16, 18])
    lost.citizens.insert(0, (0, 9))
    check('a birth sizes the store for the new population',
          ec.push_history(lost, True), ec.food_capacity(16))
    check('a birth flags every older entry',
          [w & old for w in lost.pop_history], [0] + [old] * 7)

    founded = planet(7, 7, 32, 0, (0, 600))
    founded.pop_history, founded.history_len = [7, 0, 0, 0, 0, 0, 0, 0], 1
    ec.push_history(founded, False)
    check('a new colony fills its slots one a turn',
          (founded.history_len, founded.pop_history[:3]), (2, [7, 7, 0]))

    soldiers = planet(17, 7, 72, 0, (0, 2600))
    soldiers.military, soldiers.military_cost, soldiers.military_food = [], 300, 278
    ec.recruit(soldiers, 25)
    check('a full military store makes a soldier and carries the rest',
          (len(soldiers.military), soldiers.military_food), (1, 3))

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
