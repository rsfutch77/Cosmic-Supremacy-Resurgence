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
                               "progress": p.build_progress or 0,
                               # Planet:124, the MILITARY FOOD STORE. A unit
                               # appears when it reaches Planet:128 (the food
                               # cost, 300 by default). Tracked because a
                               # recruitment rate can be set, look correct in
                               # the UI, and move this by nothing at all --
                               # which is what "No Military Is Recruited"
                               # means and what wasted a hundred turns.
                               "milstore": p.u32(124) or 0,
                               # Needed to undo the store's WRAP: when a unit
                               # is recruited the store drops by the cost, and
                               # a raw difference reads as a large negative.
                               "military": p.military or 0,
                               "milcost": p.u32(128) or 0}
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

    STALL_TURNS = 4       # consecutive non-advancing turns before believing it

    def note_rate_written(self, planet_id, pct):
        """Remember the recruitment rate we WROTE, to compare next turn.

        The AI writes the rate just after a turn boundary, so every reading it
        takes is of its own fresh write. If the engine were clearing the byte
        during the tick, the tick would always see 0 while we always saw 40 --
        and the store would never advance, which is exactly the unexplained
        stall. Comparing what we wrote LAST turn against what is there BEFORE we
        write this turn is the one measurement that separates those cases.
        """
        if not hasattr(self, "_rate_written"):
            self._rate_written = {}
        self._rate_written[planet_id] = pct

    def rate_survived(self, planet_id, observed):
        """(wrote, survived) for the rate written last turn, or (None, None)."""
        wrote = getattr(self, "_rate_written", {}).get(planet_id)
        if wrote is None:
            return None, None
        return wrote, observed == wrote

    def note_military_stall(self, planet_id, advancing):
        """Count consecutive turns a planet's military store failed to advance.

        Needed because a SINGLE non-advancing turn means nothing. The store
        drops by the cost when a unit is recruited, and a unit can be produced
        and then carried off as crew in the same turn, so any one-turn test can
        be fooled from both directions. This detector fired twice on perfectly
        healthy planets before it was made to require persistence.
        """
        if not hasattr(self, "_mil_stall"):
            self._mil_stall = {}
        self._mil_stall[planet_id] = 0 if advancing else             self._mil_stall.get(planet_id, 0) + 1
        return self._mil_stall[planet_id]

    def military_stalled(self, planet_id):
        return getattr(self, "_mil_stall", {}).get(planet_id, 0) >= self.STALL_TURNS

    def military_rate(self, planet_id):
        """Food per turn flowing into this planet's military store.

        Corrected for the WRAP. Recruiting a unit subtracts the cost from the
        store, so a raw difference across that turn is hugely negative — 259
        became 2 on a 300-cost planet. The first version of this sensor read
        that as "not moving" and reported a healthy planet as permanently
        stalled, one turn after reporting a correct 1-turn ETA. The units
        produced have to be added back before the rate means anything.
        """
        prev = getattr(self, "_prev_for_pass", None)
        if not prev or not self.turns:
            return None
        a = prev["planets"].get(planet_id)
        b = self._cur["planets"].get(planet_id)
        if a is None or b is None:
            return None
        made = max(0, b["military"] - a["military"])
        cost = b["milcost"] or a["milcost"] or 0
        return (b["milstore"] - a["milstore"] + made * cost) / self.turns

    # ── the banker share, a slow controller with a deadband ────────────────
    # The first attempt was bang-bang: negative income converted every scientist
    # to a banker, positive income converted them all back, and the empire
    # oscillated every single turn. The user watched it flip-flopping. A binary
    # switch cannot express "some bankers", which is the only stable answer.
    BANKER_STEP = 0.05      # how fast the share may move per turn
    BANKER_MAX = 0.60       # of the non-farming citizens
    COMFORTABLE_INCOME = 150   # only give bankers back above this, not at zero

    def banker_share(self, income):
        """Nudge the empire's banker share toward whatever balances the books.

        The DEADBAND between 0 and COMFORTABLE_INCOME is the point: an income of
        +10/turn is not a reason to sack the treasury staff, and reacting to it
        is what produced the oscillation. The share only rises when income is
        actually negative and only falls when there is real headroom.
        """
        cur = getattr(self, "_banker_share", 0.0)
        if income is None:
            return cur
        if income < 0:
            cur = min(self.BANKER_MAX, cur + self.BANKER_STEP)
        elif income > self.COMFORTABLE_INCOME:
            cur = max(0.0, cur - self.BANKER_STEP)
        self._banker_share = cur
        return cur

    def note_cash_effect(self, amount):
        """Record what our own actions did to the treasury this turn.

        Called by the loop after the rules have run. income_rate() subtracts it
        back out so the sensor measures the ECONOMY rather than our spending.
        """
        self._our_cash_effect = amount

    def _civ_delta(self, key):
        p = getattr(self, "_prev_for_pass", None)
        n = self.turns
        if not p or not n:
            return None
        return (self._cur["civ"][key] - p["civ"][key]) / n

    def income_rate(self):
        """Cash per turn from the economy, excluding our own spending.

        Owner:8 moves for two reasons: the empire earns, and we spend. Only the
        first is a signal. Hurrying a build is a deliberate purchase, and
        counting it as lost income made R-XPL-08 oscillate — spend, see
        "negative income", refuse, recover, spend again.
        """
        d = self._civ_delta("cash")
        if d is None:
            return None
        ours = getattr(self, "_our_cash_effect", 0) or 0
        n = self.turns or 1
        return d - ours / n
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
