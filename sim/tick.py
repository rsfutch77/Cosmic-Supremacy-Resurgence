"""
One simulated turn, in the order the manual's tick page gives, except that the
planets' science is banked before the research step, which the engine does:

    science, research, planets (production, growth, food, income), movement,
    combat, then what each civ saw

Governors and admirals run before and after in the client; the policy stands in
for both, so the simulator runs neither. Each step is a module that mutates the
galaxy in place. Steps not written yet are absent, so the tick is the null tick
for them and the oracle scores what they leave unchanged.
"""
import copy

from . import combat
from . import economy
from . import fog
from . import movement
from . import research

STEPS = (                           # (name, function(galaxy)), in tick order
    # The turn's science is banked before a topic is checked: a store of
    # 4706 with 407 coming in paid a 4800 topic in the same tick. A doctrine
    # bought then already raises that tick's food, income and metal.
    ('science', economy.bank_science),
    ('research', research.civs),
    ('planets', economy.planets),
    ('movement', movement.ships),
    ('combat', combat.battles),
    ('activity', economy.activity),
)


def tick(galaxy, steps=None, copy_first=True):
    """The galaxy one turn later, as a new object unless `copy_first` is off,
    when it is ticked in place, which the training loop does to save the copy."""
    g = copy.deepcopy(galaxy) if copy_first else galaxy
    for _name, step in (STEPS if steps is None else steps):
        step(g)
    g.turn += 1
    if steps is None:
        # What each civ saw is stamped with the new turn, as EXSY rows are.
        fog.update(g)
    return g
