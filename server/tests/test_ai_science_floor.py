"""
test_ai_science_floor.py: a civ keeps a scientist, so its research never stops
==============================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_ai_science_floor.py

Research comes only from scientists. R-XPL-01 keeps two workers a planet, which
on a farming planet with two non-farmers leaves no scientist, so each civ keeps
SCIENCE_FLOOR of them anyway. The food rule's released farmer follows the same
worker floor, and a draft takes scientists last.

The planets are stand-ins, so no client is needed.
"""
import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, 'client', 'dev_tools', 'ai_player'))

import actions                                                  # noqa: E402
import exploit                                                  # noqa: E402
from exploit import FARMER, WORKER, SCIENTIST                   # noqa: E402

PASS, FAIL = [], []


def check(name, got, want):
    (PASS if got == want else FAIL).append(name)
    print(f'  {"ok  " if got == want else "FAIL"}  {name}')
    if got != want:
        print(f'          wanted {want!r}, got {got!r}')


def quiet(_msg=''):
    pass


class Planet:
    def __init__(self, pid, farmers, workers=0, scientists=0):
        self.id = pid
        self.population = ([FARMER] * farmers + [WORKER] * workers
                           + [SCIENTIST] * scientists)
        self.max_pop = 100
        self.resource = (None, 0)
        self.facilities = {}

    @property
    def jobs(self):
        return collections.Counter(self.population)


class Civ:
    completed, stocks = [], {}


class Snap:
    designs = []

    def __init__(self, planets):
        self.planets = planets

    def owned_planets(self, _civ):
        return self.planets


class Act:
    remote = None

    def __init__(self, claimed=()):
        self.claimed = set(claimed)

    def planet_claimed(self, p):
        return p.id in self.claimed

    def set_job(self, p, i, job):
        p.population[i] = job


class Hist:
    COMFORTABLE_INCOME = 150
    scientist_share = None

    def science_rate(self):
        return None

    def income_rate(self):
        return 100

    def banker_share(self, _rate):
        return 0

    def mark_acted(self, _pid):
        pass


def jobs_after(planets, claimed=()):
    exploit.run_jobs(Snap(planets), Civ(), Act(claimed), Hist(), log=quiet)
    return {p.id: (p.jobs[WORKER], p.jobs[SCIENTIST]) for p in planets}


def main():
    print('a civ with no scientist takes one where it has the most non-farmers')
    check('Sir Blorpington from turn 116', jobs_after(
        [Planet(118, 9), Planet(121, 9, 1), Planet(122, 14, 2)]),
        {118: (0, 0), 121: (1, 0), 122: (1, 1)})
    check('a claimed planet is not the one', jobs_after(
        [Planet(121, 9, 1), Planet(122, 14, 2)], claimed={122}),
        {121: (0, 1), 122: (2, 0)})
    check('no non-farmer anywhere, nothing to take', jobs_after(
        [Planet(118, 9)]), {118: (0, 0)})

    print('the floor keeps the scientist it has')
    check("the civ's only scientist stays", jobs_after(
        [Planet(185, 14, 1, 1), Planet(189, 21, 1)]),
        {185: (1, 1), 189: (1, 0)})
    check('of two, one goes back to work', jobs_after(
        [Planet(185, 14, 1, 1), Planet(189, 21, 1, 1)]),
        {185: (2, 0), 189: (1, 1)})
    check('above the floor, the share decides as before', jobs_after(
        [Planet(185, 14, 2, 2)]), {185: (2, 2)})

    print("the food rule's released farmer follows the worker floor")
    check('one worker: the next is a worker',
          exploit.preferred_job(Planet(1, 9, 1)), WORKER)
    check('two workers: the next is a scientist',
          exploit.preferred_job(Planet(1, 9, 2)), SCIENTIST)

    print('a draft takes scientists last')
    check('scientist is the last choice',
          actions.Actuator.DRAFT_PREFERENCE[-1], SCIENTIST)
    check('worker comes before it',
          actions.Actuator.DRAFT_PREFERENCE.index(WORKER)
          < actions.Actuator.DRAFT_PREFERENCE.index(SCIENTIST), True)

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
