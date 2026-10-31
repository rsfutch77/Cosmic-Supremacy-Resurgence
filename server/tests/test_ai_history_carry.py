"""
test_ai_history_carry.py: a loop run one pass per process keeps its history
==========================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_ai_history_carry.py

`bot_seats` builds a fresh `ai.Loop` for every turn. Each case here builds a
fresh `sensors.History` per pass the same way and checks what the next pass
reads: the food delta that frees farmers, the planets it distrusts, and the
controllers a rule keeps between passes.

The galaxy is a stand-in, so no client is needed.
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, 'client', 'dev_tools', 'ai_player'))

import sensors

PASS, FAIL = [], []


def check(name, got, want):
    (PASS if got == want else FAIL).append(name)
    print(f'  {"ok  " if got == want else "FAIL"}  {name}')
    if got != want:
        print(f'          wanted {want!r}, got {got!r}')


class Sun:
    def __init__(self, sid, pos):
        self.id, self.pos = sid, pos


class Planet:
    def __init__(self, pid, food, pop):
        self.id, self.food, self.population = pid, food, [0] * pop
        self.build_progress, self.military = 0, 0

    def u32(self, _off):
        return 0


class Civ:
    def __init__(self, name='Beta', cash=1000, research=1480):
        self.civ_name, self.cash, self.research = name, cash, research
        self.completed = []

    def vec(self, _off, _size):
        return []


class Snap:
    SUNS = [Sun(1, (0.0, 0.0, 0.0)), Sun(2, (500.0, 0.0, 0.0))]

    def __init__(self, turn, food):
        """`food` maps planet id to its food store this turn."""
        self.turn, self.suns = turn, self.SUNS
        self.planets = [Planet(pid, f, 10) for pid, f in sorted(food.items())]

    def owned_planets(self, _civ):
        return self.planets

    def owned_ships(self, _civ):
        return []

    def nearest_sun(self, _planet):
        return self.suns[0], 0.0


def one_pass(turn, food, rules=None, end=True, persist=True):
    """A pass in a fresh process: a new History, observe, rules, end."""
    hist = sensors.History(persist=persist)
    hist.observe(Snap(turn, food), Civ())
    seen = {pid: hist.food_delta(pid) for pid in food}
    if rules:
        rules(hist)
    if end:
        hist.end_pass()
    return hist, seen


def fresh_dir():
    sensors.STATE_DIR = tempfile.mkdtemp(prefix='ai_carry_')
    return sensors.STATE_DIR


def case_delta_across_processes():
    print('a later turn reads the food delta from the last process')
    fresh_dir()
    _h, first = one_pass(10, {7: 100, 8: 50})
    check('first pass has no delta', first, {7: None, 8: None})
    _h, second = one_pass(11, {7: 106, 8: 47})
    check('second pass has both deltas', second, {7: 6.0, 8: -3.0})


def case_acted_planet_is_stale():
    print('a planet acted on last pass is distrusted next pass')
    fresh_dir()
    one_pass(10, {7: 100, 8: 50})
    one_pass(11, {7: 106, 8: 47}, rules=lambda h: h.mark_acted(7))
    _h, seen = one_pass(12, {7: 110, 8: 45})
    check('acted planet is stale, the other is not', seen, {7: None, 8: -2.0})


def case_rerun_repeats_the_pass():
    print('a rerun of the same turn starts where that pass started')
    fresh_dir()
    one_pass(10, {7: 100})

    def step(h):
        h.scientist_share = 0.3
        h.mark_acted(7)
    _h, first = one_pass(11, {7: 106}, rules=step)
    hist, again = one_pass(11, {7: 106})
    check('rerun reads the same delta', again, first)
    check('rerun sees the share from before the pass', hist.scientist_share,
          None)
    _h, later = one_pass(12, {7: 111})
    check('the next turn follows the rerun, not the first pass', later,
          {7: 5.0})


def case_controllers_round_trip():
    print('controllers come back with their types and ids')
    fresh_dir()

    def set_all(h):
        h.scientist_share = 0.25
        h._banker_share = 0.1
        h.note_cash_effect(-235)
        h.note_rate_written(186, 20)
        h.note_military_stall(186, False)
        h.raid = {'target': 42, 'arrive': 14, 'members': [214, 221]}
        h.wealth_stopgap = {185}
        h.liquidated = {186: {3, 5}}
        h.deficit_since = 9
        h.liquidated_turn = 10
    one_pass(10, {186: 100}, rules=set_all)
    hist, _ = one_pass(11, {186: 104})
    check('scientist share', hist.scientist_share, 0.25)
    check('banker share', hist._banker_share, 0.1)
    check('cash effect excluded from income', hist._our_cash_effect, -235)
    check('rate written, by planet id', hist.rate_survived(186, 20),
          (20, True))
    check('military stall, by planet id', hist._mil_stall, {186: 1})
    check('raid plan', hist.raid,
          {'target': 42, 'arrive': 14, 'members': [214, 221]})
    check('wealth stopgap planets', hist.wealth_stopgap, {185})
    check('liquidated facilities, by planet id', hist.liquidated, {186: {3, 5}})
    check('deficit and sale turns', (hist.deficit_since, hist.liquidated_turn),
          (9, 10))


def case_older_save_carries_nothing():
    print('an earlier turn is an older save, and nothing carries')
    fresh_dir()
    one_pass(10, {7: 100}, rules=lambda h: setattr(h, 'scientist_share', 0.3))
    one_pass(11, {7: 106}, rules=lambda h: setattr(h, 'scientist_share', 0.2))
    hist, seen = one_pass(5, {7: 80})
    check('no delta', seen, {7: None})
    check('no carried share', hist.scientist_share, None)


def case_pass_that_died_resumes_from_start():
    print('a pass that died before its end resumes from its start')
    fresh_dir()
    one_pass(10, {7: 100})
    one_pass(11, {7: 106}, end=False)
    _h, seen = one_pass(12, {7: 110})
    check('delta over the two turns since the last finished pass', seen,
          {7: 5.0})


def case_unreadable_carry():
    print('an unreadable carry is ignored, and the map still loads')
    d = fresh_dir()
    hist, _ = one_pass(10, {7: 100})
    with open(hist._path) as f:
        data = json.load(f)
    data['carry']['end']['prev']['planets'] = 'not a map'
    with open(hist._path, 'w') as f:
        json.dump(data, f)
    hist, seen = one_pass(11, {7: 106})
    check('no delta', seen, {7: None})
    check('map still loaded', hist.discovered, {1})
    shutil.rmtree(d)


def case_no_persist_writes_nothing():
    print('a History that does not persist writes no file')
    d = fresh_dir()
    one_pass(10, {7: 100}, persist=False)
    check('state directory is empty', os.listdir(d), [])


def case_one_process_unchanged():
    print('one History across passes still reads its own last pass')
    fresh_dir()
    hist = sensors.History()
    for turn, food in ((10, 100), (11, 104)):
        hist.observe(Snap(turn, {7: food}), Civ())
        hist.end_pass()
    check('delta in process', hist.food_delta(7), 4.0)


def main():
    case_delta_across_processes()
    case_acted_planet_is_stale()
    case_rerun_repeats_the_pass()
    case_controllers_round_trip()
    case_older_save_carries_nothing()
    case_pass_that_died_resumes_from_start()
    case_unreadable_carry()
    case_no_persist_writes_nothing()
    case_one_process_unchanged()
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
