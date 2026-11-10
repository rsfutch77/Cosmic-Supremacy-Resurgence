"""
One simulated turn, in the order the manual's tick page gives:

    research, planets (production, growth, food, income), ship movement, combat

Governors and admirals run before and after in the client; the policy stands in
for both, so the simulator runs neither. Each step is a module that mutates the
galaxy in place. Steps not written yet are absent, so the tick is the null tick
for them and the oracle scores what they leave unchanged.
"""
import copy

from . import economy
from . import movement

STEPS = (                           # (name, function(galaxy)), in tick order
    ('planets', economy.planets),
    ('movement', movement.ships),
    ('activity', economy.activity),
)


def tick(galaxy, steps=None):
    """The galaxy one turn later, as a new object."""
    g = copy.deepcopy(galaxy)
    for _name, step in (STEPS if steps is None else steps):
        step(g)
    g.turn += 1
    return g
