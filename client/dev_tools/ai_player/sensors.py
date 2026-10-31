"""
sensors.py: turn-over-turn observation (STRATEGY.md §2.5).
"""


import json
import math
import os


def _dist(a, b):
    """Local copy to avoid an import cycle with gamestate."""
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


# Beside the source in a checkout. CS_AI_STATE_DIR overrides it for a frozen
# build, whose unpack directory is deleted on exit (R-XTM-01); the launcher
# points it at its own data directory.
STATE_DIR = (os.environ.get("CS_AI_STATE_DIR")
             or os.path.join(os.path.dirname(os.path.abspath(__file__)), "state"))


def _seg_dist(a, b, p):
    """Distance from point p to the segment a-b."""
    ab = [y - x for x, y in zip(a, b)]
    n = sum(c * c for c in ab)
    if n == 0:
        return _dist(a, p)
    t = max(0.0, min(1.0, sum((q - x) * c for x, q, c in zip(a, p, ab)) / n))
    return _dist([x + t * c for x, c in zip(a, ab)], p)


def _fingerprint(snap):
    """A stable id for this galaxy: a hash of sorted (sun id, rounded position)."""
    key = tuple(sorted((s.id,
                        round(s.pos[0], 1), round(s.pos[1], 1),
                        round(s.pos[2], 1)) for s in snap.suns))
    return f"{len(snap.suns)}_{abs(hash(key)) & 0xFFFFFFFF:08x}"


# Attributes a rule keeps from one pass to the next. Saved with the readings so
# a loop that runs one pass per process decides as a long-running one does.
CARRIED = ("scientist_share", "_banker_share", "_our_cash_effect", "raid",
           "deficit_since", "liquidated_turn", "_mil_stall", "_rate_written",
           "_ship_seen")
# Of those, the ones keyed by planet or ship id, which JSON stores as strings.
CARRIED_BY_ID = ("_mil_stall", "_rate_written", "_ship_seen")


def _by_id(d):
    """A JSON object keyed by id, with its keys back as ints."""
    return {int(k): v for k, v in d.items()}


class History:
    """Turn-over-turn observation, plus the persistent fog-of-war map and intel.

    Discovery is permanent, as in the game: the set is saved to
    `state/discovered_<galaxy>_<civ>.json` and reloaded on construction, with
    the intel this civ has gathered. The game empties a battle report's text
    a few turns after the battle, so intel is kept here once read.

    The same file carries the last pass: the reading it compared against, the
    planets it distrusted, and the `CARRIED` controllers, as they stood at the
    pass's start and end. A loop started fresh resumes from the end on a later
    turn and from the start on the same turn, so a rerun repeats the pass
    instead of stacking on it. An earlier turn is an older save, and nothing
    carries.
    """

    def __init__(self, snap=None, civ_name=None, persist=True):
        self.prev = None          # {"turn", "planets": {id: {...}}, "civ": {...}}
        self._acted = set()       # planet ids touched since the last observation
        self._stale = set()       # planet ids whose next delta is untrustworthy
        self.discovered = set()   # sun ids whose planets we are allowed to see
        # Planet id -> the newest battle report read there:
        # {"turn", "planet_name", "sides": {name: {...}}}.
        self.intel = {}
        # Enemy planet id -> turn one of our ships sat there at war unfired on.
        self.quiet = {}
        # The raid in progress, {"target", "arrive" turn, "members" ship ids}, or None.
        self.raid = None
        # Ship id -> (turn, position) at the last pass, for the swept segment.
        self._ship_seen = {}
        # R-XPL-01's scientist share under the wastage ceiling; None until
        # the rule first runs.
        self.scientist_share = None
        self._path = None
        self._persisted = None    # what is already on disk, to avoid rewriting
        self._want_persist = persist
        self._carry_in = None     # the last pass as loaded from disk
        self._resumed = False     # whether this process has applied it
        self._start = None        # this pass as it began, for a rerun
        self._end = None          # this pass as it ended, for the next turn
        if persist and snap is not None:
            self._bind(snap, civ_name)

    def _bind(self, snap, civ_name):
        """Choose the file for this galaxy+civ and merge whatever is already in it.

        Must run before `_discover` so the loaded set and new sightings union.
        """
        self._path = os.path.join(
            STATE_DIR,
            f"discovered_{_fingerprint(snap)}_{civ_name or 'unknown'}.json")
        self._load()

    def _load(self):
        try:
            with open(self._path) as f:
                data = json.load(f)
            self.discovered |= {int(i) for i in data.get("discovered", [])}
            for k, v in data.get("intel", {}).items():
                self.intel.setdefault(int(k), v)
            for k, v in data.get("quiet", {}).items():
                self.quiet.setdefault(int(k), v)
            carry = data.get("carry")
            self._carry_in = carry if isinstance(carry, dict) else None
            self._persisted = self._state()
        except (OSError, ValueError, TypeError):
            # A missing or corrupt file means nothing explored yet.
            pass

    def _state(self):
        state = {"discovered": sorted(self.discovered),
                 "intel": {str(k): v for k, v in sorted(self.intel.items())},
                 "quiet": {str(k): v for k, v in sorted(self.quiet.items())}}
        if self._start is not None:
            state["carry"] = {"turn": self._cur["turn"], "start": self._start,
                              "end": self._end}
        return state

    def _carry(self, prev, stale):
        """The pass state a later pass resumes from, as JSON."""
        out = {"prev": prev, "stale": sorted(stale)}
        for name in CARRIED:
            if hasattr(self, name):
                out[name] = getattr(self, name)
        if hasattr(self, "wealth_stopgap"):
            out["wealth_stopgap"] = sorted(self.wealth_stopgap)
        if hasattr(self, "liquidated"):
            out["liquidated"] = {k: sorted(v)
                                 for k, v in self.liquidated.items()}
        # The round trip is also the copy, so later rules cannot change it.
        return json.loads(json.dumps(out))

    def _resume(self, turn):
        """Apply the loaded pass for a loop reading `turn`."""
        rec = self._carry_in or {}
        saved = rec.get("turn")
        if not isinstance(saved, int) or not isinstance(turn, int) or saved > turn:
            return
        # A pass that died before its end resumes from its start.
        state = (rec.get("end") if saved < turn else None) or rec.get("start")
        if not isinstance(state, dict):
            return
        try:
            prev = state.get("prev")
            if prev is not None:
                prev = dict(prev, planets=_by_id(prev["planets"]))
            acted = {int(i) for i in state.get("stale", [])}
            carried = {name: state[name] for name in CARRIED if name in state}
            for name in CARRIED_BY_ID:
                if name in carried:
                    carried[name] = _by_id(carried[name])
            if "wealth_stopgap" in state:
                carried["wealth_stopgap"] = {int(i)
                                             for i in state["wealth_stopgap"]}
            if "liquidated" in state:
                carried["liquidated"] = {int(k): set(v) for k, v
                                         in state["liquidated"].items()}
        except (KeyError, ValueError, TypeError, AttributeError):
            return                        # an unreadable pass carries nothing
        # `observe` moves `_acted` into `_stale` and `prev` into the pass.
        self.prev, self._acted = prev, acted
        for name, value in carried.items():
            setattr(self, name, value)

    def save(self):
        """Write the map and intel, only when they changed."""
        state = self._state()
        if self._path is None or state == self._persisted:
            return
        try:
            os.makedirs(STATE_DIR, exist_ok=True)
            tmp = self._path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(state, f)
            os.replace(tmp, self._path)   # atomic; a killed loop cannot truncate it
            self._persisted = state
        except OSError:
            pass                          # never take the loop down over the map

    def observe(self, snap, civ):
        """Take the current reading. Call once per pass, before the rules run."""
        cur = {
            "turn": snap.turn,
            "planets": {p.id: {"food": p.food or 0,
                               # The store's size, which a birth spends.
                               "cap": p.food_cap or 0,
                               "pop": len(p.population),
                               "progress": p.build_progress or 0,
                               # Planet:124, the military food store. A unit
                               # appears when it reaches Planet:128, the cost.
                               "milstore": p.u32(124) or 0,
                               # The store drops by the cost per recruit;
                               # these undo that wrap.
                               "military": p.military or 0,
                               "milcost": p.u32(128) or 0}
                        for p in snap.owned_planets(civ)},
            # A completion subtracts the topic's cost from Owner:12 and
            # records that cost in Owner:108, so adding the costs back gives
            # every point the civ has ever banked, unbroken by completions.
            "civ": {"cash": civ.cash or 0, "research": civ.research or 0,
                    "science": (civ.research or 0)
                               + sum(cost for _tid, cost in civ.completed)},
        }
        if self._path is None and self._want_persist:
            self._bind(snap, civ.civ_name)
        if not self._resumed:
            self._resumed = True
            self._resume(snap.turn)
        self._stale, self._acted = self._acted, set()
        self._cur, self._prev_for_pass = cur, self.prev
        self.prev = cur
        self._start = self._carry(self._prev_for_pass, self._stale)
        self._end = None
        self._discover(snap, civ)
        self.save()
        return self

    def end_pass(self):
        """Record the pass as it ended and save it. Call after the rules."""
        if self._start is not None:
            self._end = self._carry(self.prev, self._acted)
        self.save()

    def _discover(self, snap, civ):
        """Grow the set of systems this civ is entitled to see inside.

        Memory holds every planet, so reading undiscovered ones is cheating. A
        system is discovered when we hold a planet in it, one of our ships is
        inside it, or a scan report names it.
        """
        for p in snap.owned_planets(civ):
            s, _r = snap.nearest_sun(p)
            if s is not None:
                self.discovered.add(s.id)
        seen, self._ship_seen = self._ship_seen, {}
        suns = [x for x in snap.suns if None not in x.pos]
        for sh in snap.owned_ships(civ):
            pos = sh.pos
            if None in pos:
                continue
            self._ship_seen[sh.id] = (snap.turn, pos)
            # Inside the system: widest orbit 39.6, tightest star gap 111.7.
            # A ship sampled once a turn can fly through a system between two
            # samples, so the line it swept since the last turn counts. Only
            # one turn's travel: over several, the route may have turned.
            turn, start = seen.get(sh.id, (None, pos))
            if turn is None or snap.turn - turn != 1:
                start = pos
            for x in suns:
                if _seg_dist(start, pos, x.pos) <= 55.0:
                    self.discovered.add(x.id)
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

    @property
    def turns(self):
        p = getattr(self, "_prev_for_pass", None)
        if not p:
            return None
        d = self._cur["turn"] - p["turn"]
        return d if d > 0 else None

    def _pair(self, planet_id):
        """(last reading, this reading, turns between) for a planet, or None."""
        p = getattr(self, "_prev_for_pass", None)
        n = self.turns
        if not p or not n or planet_id in self._stale:
            return None
        a, b = p["planets"].get(planet_id), self._cur["planets"].get(planet_id)
        if a is None or b is None:
            return None                # newly founded, or lost
        return a, b, n

    def _delta(self, planet_id, key):
        pair = self._pair(planet_id)
        if pair is None:
            return None
        a, b, n = pair
        return (b[key] - a[key]) / n

    def food_delta(self, planet_id):
        """Food gained per turn. Negative means the colony is starving.

        A birth empties the store by its size, so each citizen born puts that
        size back. Food that rose is left alone, since a citizen can also
        arrive without being born.
        """
        pair = self._pair(planet_id)
        if pair is None:
            return None
        a, b, n = pair
        food, born = b["food"] - a["food"], b["pop"] - a["pop"]
        if born > 0 and food < 0:
            # A reading saved before the store size was kept falls back to now.
            food += born * (a.get("cap") or b.get("cap") or 0)
        return food / n

    def pop_growth(self, planet_id):
        return self._delta(planet_id, "pop")

    def production_rate(self, planet_id):
        return self._delta(planet_id, "progress")

    STALL_TURNS = 4       # consecutive non-advancing turns before believing it

    def note_rate_written(self, planet_id, pct):
        """Remember the recruitment rate we wrote, to check next turn whether
        the engine kept it."""
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
        """Count consecutive turns a planet's military store failed to advance."""
        if not hasattr(self, "_mil_stall"):
            self._mil_stall = {}
        self._mil_stall[planet_id] = 0 if advancing else             self._mil_stall.get(planet_id, 0) + 1
        return self._mil_stall[planet_id]

    def military_stalled(self, planet_id):
        return getattr(self, "_mil_stall", {}).get(planet_id, 0) >= self.STALL_TURNS

    def military_rate(self, planet_id):
        """Food per turn flowing into this planet's military store."""
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

    # Banker share: a slow controller with a deadband.
    BANKER_STEP = 0.05      # how fast the share may move per turn
    BANKER_MAX = 0.60       # of the non-farming citizens
    COMFORTABLE_INCOME = 150   # only give bankers back above this, not at zero

    def banker_share(self, income):
        """Nudge the empire's banker share toward whatever balances the books.

        Rises on negative income, falls above COMFORTABLE_INCOME, holds between.
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
        """Record what our own actions did to the treasury this turn."""
        self._our_cash_effect = amount

    def _civ_delta(self, key):
        p = getattr(self, "_prev_for_pass", None)
        n = self.turns
        if not p or not n:
            return None
        return (self._cur["civ"][key] - p["civ"][key]) / n

    def income_rate(self):
        """Cash per turn from the economy, excluding our own spending (R-XPL-08)."""
        d = self._civ_delta("cash")
        if d is None:
            return None
        ours = getattr(self, "_our_cash_effect", 0) or 0
        n = self.turns or 1
        return d - ours / n
    def science_rate(self):
        """Research banked per turn, net of science wastage. Unlike the raw
        Owner:12 delta it does not dip by a topic's cost when one completes."""
        return self._civ_delta("science")

    def summary(self):
        n = self.turns
        if not n:
            return "no previous observation yet; deltas available next turn"
        inc, res = self.income_rate(), self.science_rate()
        bits = [f"over {n} turn(s): income {inc:+.0f}/turn, "
                f"research {res:+.0f}/turn"]
        for pid in sorted(self._cur["planets"]):
            fd = self.food_delta(pid)
            bits.append(f"#{pid} food "
                        + ("stale" if fd is None else f"{fd:+.0f}/turn"))
        return "  ".join(bits)
