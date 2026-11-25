"""
test_ai_war_needs_contact.py: the bot declares war only on a civ it has met
==========================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_ai_war_needs_contact.py

R-XTM-00 declares war through the engine, which makes first contact itself,
so the rule checks contact first as the declare-war button does. Met means a
relation record in Owner:248. A rival planet in a discovered system is in
sight but not met, and the referee drops a declaration on it ("this civ has
not met ..."), as it did for Beta against a civ that joined in a system Beta
had already discovered.

The galaxy and the engine are stand-ins, so no client is needed.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, 'client', 'dev_tools', 'ai_player'))

import exterminate

PASS, FAIL = [], []


def check(name, got, want):
    (PASS if got == want else FAIL).append(name)
    print(f'  {"ok  " if got == want else "FAIL"}  {name}')
    if got != want:
        print(f'          wanted {want!r}, got {got!r}')


class Civ:
    def __init__(self, name, addr):
        self.civ_name, self.addr, self.id = name, addr, addr // 16


class Planet:
    def __init__(self, pid, owner):
        self.id, self.owner = pid, owner


class Snap:
    def __init__(self, civs, planets, turn=211):
        self.civs, self.planets, self.turn = civs, planets, turn


class History:
    """Every planet is in a discovered system."""

    def can_see(self, _snap, _planet):
        return True


class Remote:
    """The engine's relation records, keyed by (civ addr, other addr)."""

    def __init__(self, met=(), broken=False):
        self.met, self.broken, self.asked = set(met), broken, 0

    def relation_record(self, civ_tag, other_tag):
        self.asked += 1
        if self.broken:
            raise RuntimeError('remote thread failed')
        return 0x01000000 if (civ_tag, other_tag) in self.met else None

    def relation_code(self, _civ_tag, _other_tag):
        return 0                       # Neutral, also when there is no record

    def war_protection(self, _other_tag):
        return False, 50, 0


class Act:
    def __init__(self, remote):
        self.remote, self.declared = remote, []

    def is_crewed(self, _ship):
        return True

    def declare_war(self, civ, other):
        self.declared.append((civ.civ_name, other.civ_name))
        return True


BETA = Civ('Beta', 0x1000)
INVADERS = Civ('SpaceInvaders', 0x2000)
SNAP = Snap([BETA, INVADERS], [Planet(138, INVADERS)])


def run(act):
    """One R-XTM-00 pass for Beta with two warships staged; (declared, log)."""
    exterminate.clear_relation_cache()
    lines = []
    saved = (exterminate.staging_planet, exterminate.warships,
             exterminate.at_planet)
    exterminate.staging_planet = lambda *_a: 'rally'
    exterminate.warships = lambda *_a: ['w1', 'w2']
    exterminate.at_planet = lambda *_a: True
    try:
        exterminate.run_xtm00(SNAP, BETA, act, History(), log=lines.append)
    finally:
        (exterminate.staging_planet, exterminate.warships,
         exterminate.at_planet) = saved
    return act.declared, lines


def case_in_sight_but_not_met():
    print('a rival planet in a discovered system, no first contact')
    declared, lines = run(Act(Remote()))
    check('no declaration', declared, [])
    check('the refusal names the reason',
          any('not met' in s for s in lines), True)


def case_met():
    print('a rival Beta has met')
    declared, _ = run(Act(Remote(met={(BETA.addr, INVADERS.addr)})))
    check('war is declared', declared, [('Beta', 'SpaceInvaders')])


def case_contact_unreadable():
    print('the relation record cannot be read')
    declared, lines = run(Act(Remote(broken=True)))
    check('no declaration on a failed read', declared, [])
    check('the refusal says contact cannot be read',
          any('cannot be read' in s for s in lines), True)
    declared, _ = run(Act(None))
    check('no declaration without an engine', declared, [])


def case_cache():
    print('contact is cached per turn and cleared with the relation codes')
    exterminate.clear_relation_cache()
    rem = Remote()
    act = Act(rem)
    check('unmet', exterminate.has_met(SNAP, BETA, INVADERS, act), False)
    rem.met.add((BETA.addr, INVADERS.addr))
    check('cached within the turn',
          exterminate.has_met(SNAP, BETA, INVADERS, act), False)
    check('one engine read', rem.asked, 1)
    exterminate.clear_relation_cache()
    check('read again after a clear',
          exterminate.has_met(SNAP, BETA, INVADERS, act), True)


def main():
    case_in_sight_but_not_met()
    case_met()
    case_contact_unreadable()
    case_cache()
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
