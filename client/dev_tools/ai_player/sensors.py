"""
sensors.py — turn-over-turn observation (STRATEGY.md §2.5)
==========================================================
The engine's formulas are not in memory. Food output per farmer, income per
citizen, production per worker — none of it is stored anywhere we can read, and
the civ-trait fields only give modifiers to unknown bases. So the honest way to
know whether a planet is gaining or losing food is to look at it twice.

`History` holds one previous snapshot's worth of numbers and reports the deltas.
Everything it returns is per turn, normalised by however many turns actually
elapsed, because a loop that misses a tick would otherwise read a double-size
delta as a doubled rate.

TWO RULES THIS ENFORCES, both from hard experience elsewhere in this project:

  * A delta is meaningless until there IS a previous observation. Every accessor
    returns None rather than 0 in that case, so a rule cannot mistake "no data"
    for "no change" — the same distinction that made the ShipDesign -1 sentinel
    dangerous.
  * A delta is STALE for one turn after we act on that planet. Changing a
    citizen's job changes the very quantity being measured, and the effect only
    shows up at the next turn boundary. Rules mark what they touched via
    `mark_acted`, and this refuses to report a delta for it on the next pass.
"""


import math


def _dist(a, b):
    """Local copy so sensors does not import gamestate and create a cycle."""
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))

class History:
    def __init__(self):
        self.prev = None          # {"turn", "planets": {id: {...}}, "civ": {...}}
        self._acted = set()       # planet ids touched since the last observation
        self._stale = set()       # planet ids whose next delta is untrustworthy
        self.discovered = set()   # sun ids whose planets we are allowed to see

    # -- recording -------------------------------------------------------
    def observe(self, snap, civ):
        """Take the current reading. Call once per pass, before the rules run."""
        cur = {
            "turn": snap.turn,
            "planets": {p.id: {"food": p.food or 0,
                               "pop": len(p.population),
                               "progress": p.build_progress or 0}
                        for p in snap.owned_planets(civ)},
            "civ": {"cash": civ.cash or 0, "research": civ.research or 0},
        }
        self._stale, self._acted = self._acted, set()
        self._cur, self._prev_for_pass = cur, self.prev
        self.prev = cur
        self._discover(snap, civ)
        return self

    # -- fog of war ------------------------------------------------------
    def _discover(self, snap, civ):
        """Grow the set of systems this civ is entitled to see inside.

        Client memory holds every planet in the galaxy, discovered or not, so an
        AI that reads it directly is cheating: in the real game a system's
        planets are only revealed once a ship reaches its SUN, which is why the
        scout order targets a star rather than a planet. Picking a colony target
        in a system nobody has visited is exactly that cheat, and the AI did it
        until this was added.

        A system counts as discovered when we hold a planet in it, when one of
        our ships is inside it, or when a scan report names it. Nothing here
        ever forgets — discovery is permanent, as it is in the game.
        """
        for p in snap.owned_planets(civ):
            s, _r = snap.nearest_sun(p)
            if s is not None:
                self.discovered.add(s.id)
        for sh in snap.owned_ships(civ):
            s, r = snap.nearest_sun(sh)
            # Inside the system, not merely closest to it: the widest orbit
            # measured is 39.6 and the tightest gap between stars is 111.7.
            if s is not None and r <= 55.0:
                self.discovered.add(s.id)
        for rep in civ.vec(320, 4):
            import struct as _s
            ptr = _s.unpack("<I", rep)[0]
            raw = snap.read(ptr + 16, 12) if 0x10000 < ptr < 0x7FFF0000 else None
            if raw and len(raw) == 12:
                xyz = _s.unpack("<fff", raw)
                near = min(snap.suns, key=lambda q: _dist(xyz, q.pos))
                if _dist(xyz, near.pos) <= 55.0:
                    self.discovered.add(near.id)

    def can_see(self, snap, planet):
        """True if `planet` sits in a system we have discovered."""
        s, _r = snap.nearest_sun(planet)
        return s is not None and s.id in self.discovered

    def mark_acted(self, planet_id):
        self._acted.add(planet_id)

    # -- reporting -------------------------------------------------------
    @property
    def turns(self):
        p = getattr(self, "_prev_for_pass", None)
        if not p:
            return None
        d = self._cur["turn"] - p["turn"]
        return d if d > 0 else None

    def _delta(self, planet_id, key):
        p = getattr(self, "_prev_for_pass", None)
        n = self.turns
        if not p or not n or planet_id in self._stale:
            return None
        a, b = p["planets"].get(planet_id), self._cur["planets"].get(planet_id)
        if a is None or b is None:
            return None                # newly founded, or lost
        return (b[key] - a[key]) / n

    def food_delta(self, planet_id):
        """Food gained per turn. Negative means the colony is starving."""
        return self._delta(planet_id, "food")

    def pop_growth(self, planet_id):
        return self._delta(planet_id, "pop")

    def production_rate(self, planet_id):
        return self._delta(planet_id, "progress")

    def _civ_delta(self, key):
        p = getattr(self, "_prev_for_pass", None)
        n = self.turns
        if not p or not n:
            return None
        return (self._cur["civ"][key] - p["civ"][key]) / n

    def income_rate(self):   return self._civ_delta("cash")
    def research_rate(self): return self._civ_delta("research")

    def summary(self):
        n = self.turns
        if not n:
            return "no previous observation yet; deltas available next turn"
        inc, res = self.income_rate(), self.research_rate()
        bits = [f"over {n} turn(s): income {inc:+.0f}/turn, "
                f"research {res:+.0f}/turn"]
        for pid in sorted(self._cur["planets"]):
            fd = self.food_delta(pid)
            bits.append(f"#{pid} food "
                        + ("stale" if fd is None else f"{fd:+.0f}/turn"))
        return "  ".join(bits)
