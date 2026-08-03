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


class History:
    def __init__(self):
        self.prev = None          # {"turn", "planets": {id: {...}}, "civ": {...}}
        self._acted = set()       # planet ids touched since the last observation
        self._stale = set()       # planet ids whose next delta is untrustworthy

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
        return self

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
