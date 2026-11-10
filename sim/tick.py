"""
One simulated turn, in the order the manual's tick page gives, except that
research follows the planets because the engine banks a turn's science before
it checks the topic:

    planets (production, growth, food, income), research, ship movement, combat

Governors and admirals run before and after in the client; the policy stands in
for both, so the simulator runs neither. Each step is a module that mutates the
galaxy in place. Steps not written yet are absent, so the tick is the null tick
for them and the oracle scores what they leave unchanged.
"""
import copy

from . import combat
from . import economy
from . import movement
from . import research

STEPS = (                           # (name, function(galaxy)), in tick order
    ('planets', economy.planets),
    # The turn's science is banked before a topic is checked: a store of
    # 4706 with 407 coming in paid a 4800 topic in the same tick.
    ('research', research.civs),
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
    return g
