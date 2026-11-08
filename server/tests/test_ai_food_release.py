"""
test_ai_food_release.py: surplus farmers go back to work
=========================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_ai_food_release.py

The food store is the growth meter, so S-03 releases farmers by the measured
surplus, and at the population ceiling by what the citizens eat. S-01 restores
enough farmers to cover a deficit. R-XPL-02 builds nothing where no worker
could advance it.

The planets are stand-ins, so no client is needed.
"""
import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, 'client', 'dev_tools', 'ai_player'))

import exploit                                                  # noqa: E402
from exploit import FARMER, WORKER, SCIENTIST, FARM             # noqa: E402

PASS, FAIL = [], []


def check(name, got, want):
    (PASS if got == want else FAIL).append(name)
    print(f'  {"ok  " if got == want else "FAIL"}  {name}')
    if got != want:
        print(f'          wanted {want!r}, got {got!r}')


class Planet:
    def __init__(self, pid, farmers, workers=0, scientists=0, max_pop=100,
                 food=0, food_cap=1000, rate=36, farms=1):
        self.id = pid
        self.population = ([FARMER] * farmers + [WORKER] * workers
                           + [SCIENTIST] * scientists)
        self.max_pop = max_pop
        self.food, self.food_cap, self.food_rate = food, food_cap, rate
        self.facilities = {FARM: farms} if farms else {}
        self.owner = None
        self.building_now = False

    @property
    def jobs(self):
        return collections.Counter(self.population)


class Snap:
    designs = []

    def __init__(self, planets):
        self.planets = planets

    def owned_planets(self, _civ):
        return self.planets


class Civ:
    completed = []


class Act:
    def __init__(self):
        self.built = []

    def set_job(self, p, i, job):
        p.population[i] = job

    def claim_planet(self, _p):
        pass

    def production_claimed(self, _p):
        return False

    def build_facility(self, p, fac):
        self.built.append((p.id, fac))


class Hist:
    scientist_share = None

    def __init__(self, deltas):
        self.deltas = deltas

    def food_delta(self, pid):
        return self.deltas.get(pid)

    def mark_acted(self, _pid):
        pass


def food(planet, delta):
    exploit.run_food(Snap([planet]), Civ(), Act(), Hist({planet.id: delta}),
                     log=lambda _m='': None)
    return planet.jobs[FARMER]


def main():
    print('S-03 releases by the surplus, whatever the store reads')
    # 36 a farmer, one farm: 45. Margin 10 x 22 = 220, so 719 spare is 15
    # farmers, capped at ceil(22 / 4) = 6.
    check("Beta's #185, +939 at an empty store", food(
        Planet(185, 20, 2, max_pop=40, food=8, food_cap=3880), 939), 14)
    check('a surplus inside one farmer of the margin holds', food(
        Planet(1, 20, 2, max_pop=40, food=3000, food_cap=3880), 250), 20)
    check('no reading and not full: waits', food(
        Planet(2, 20, 2, max_pop=40, food=3800, food_cap=3880), None), 20)
    check('never below one farmer', food(
        Planet(3, 2, max_pop=40, food=0, food_cap=80), 500), 1)

    print('at the ceiling a full store releases by what the citizens eat')
    # 26 eat 260; 6 farmers at 45 cover it, so 18 spare, capped at 7.
    check("Beta's #189, full at 26 of 26", food(
        Planet(189, 24, 2, max_pop=26, food=5040, food_cap=5040), None), 17)
    check('exactly fed: one goes anyway, S-01 brings it back', food(
        Planet(4, 6, 20, max_pop=26, food=5040, food_cap=5040), 0), 5)

    print('S-01 restores farmers enough for the deficit')
    check('-100 at 45 a farmer takes three', food(
        Planet(5, 10, 5, max_pop=40, food=500, food_cap=3000), -100), 13)
    check('scientists go before workers', (lambda p: (food(p, -40), p.jobs))(
        Planet(6, 10, 3, 1, max_pop=40, food=500, food_cap=3000))[1][SCIENTIST],
        0)

    print('released farmers follow the job mix')
    p = Planet(7, 20, 0, max_pop=40, food=0, food_cap=3880)
    food(p, 939)
    check('two workers first, then scientists', (p.jobs[WORKER],
                                                  p.jobs[SCIENTIST]), (3, 2))

    print('R-XPL-02 needs a worker and reads no shortage from the store')
    act = Act()
    exploit.run_facilities(Snap([Planet(8, 25, farms=0)]), Civ(), act,
                           log=lambda _m='': None)
    check('no worker, no farm queued', act.built, [])
    act = Act()
    exploit.run_facilities(Snap([Planet(9, 20, 2, farms=0)]), Civ(), act,
                           log=lambda _m='': None)
    check('with workers, the farm is queued', act.built, [(9, FARM)])
    p = Planet(10, 20, 2, max_pop=40, food=0, food_cap=3880)
    check('an empty store is not hunger', exploit.wanted_facility(
        Snap([p]), Civ(), p, {FARM}, Hist({10: 939})), None)
    check('a surplus under the margin is', exploit.wanted_facility(
        Snap([p]), Civ(), p, {FARM}, Hist({10: 100})), FARM)

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
