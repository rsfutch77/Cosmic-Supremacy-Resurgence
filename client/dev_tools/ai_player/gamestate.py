"""
gamestate.py — Read-only snapshot of the running game for the AI player
=======================================================================
Wraps ejbo_viewer's raw EJBO scan in the entity model the strategy rules are
written against (see STRATEGY.md §2). Everything here is a READ; no method in
this module writes to the game. Actuators live in actions.py.

Three details from the annotations shape this file:

  * Owner is MULTIPLE INHERITANCE. Its allocation starts at tag-52, the
    reference-node idiom stores tag-8, and the .data known-players vector stores
    tag-52. Any owner lookup has to accept both or it matches nothing.
  * ShipDesign is also multiple-inheritance, front 12, so a design reference node
    may hold tag-8 or tag-12. Same treatment.
  * The read window is PER CLASS (ejbo_viewer.CLASS_EXTENTS). An offset outside a
    class's window is absent from its field list, not zero — hence off() returning
    None rather than raising.

Systems are NOT stored: no field links a Planet to its Sun. Membership here is
nearest-sun by distance, which is a heuristic. Snapshot.system_report() prints the
grouping's radii so the assumption can be checked against a real galaxy before any
rule leans on it.
"""
import collections
import math
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ejbo_viewer as ev

# ── .data statics (stable across launches) ─────────────────────────────────
TURN_COUNTER     = 0x008578E8
LOCAL_PLAYER     = 0x00857904   # ref node -> the human civ's Owner

# PlanetProperties is embedded in the Planet allocation at +0x60, which is
# planet_tag + 88 — the same frame the annotations call Planet:88. Every
# __thiscall on a PlanetProperties takes ECX = planet_tag + this.
PLANET_PROPERTIES = 88

# Ship:52 order types the UI can issue. 6 (Bio bomb) and 8 are real but the AI
# has no use for them: bio-bombing requires war and is a civilian-killing weapon,
# and 8 is unidentified beyond carrying Ship:61 = 0x0B.
ORDER_TYPES = {0: "none", 1: "move", 2: "scout", 3: "colonize", 4: "attack",
               5: "conquer", 6: "bio bomb", 7: "move near"}

# The three that ALSO require Ship:56, the order's target-object reference node
# (Ship::HasActiveTargetedOrder, 0x004D9880). Colonize and Scout carry their
# target as coordinates instead, which is why the coordinate-only writer works
# for them and not for these.
TARGETED_ORDER_TYPES = (1, 4, 7)
NULL_NODE        = ev.NULL_REF_NODE      # 0x00857C54
PILING_UP_WEALTH = 0x0080B540            # shared "generating wealth" singleton
FACILITY_VFTABLE = 0x00752BA4

# ── Id tables (all CONFIRMED in the annotations unless marked) ─────────────
# 3 = crew, first observed on a ship's crew vector (Ship:124). 4 is still
# unobserved.
JOBS       = {0: "farmer", 1: "worker", 2: "scientist", 3: "crew",
              5: "miner", 6: "banker"}
CREW_JOB   = 3
FACILITIES = {0: "farm", 2: "shipyard", 4: "university", 6: "military camp",
              8: "defence agency", 9: "light turret"}
ORDERS     = {0: "none", 1: "move", 2: "scout", 3: "colonize", 4: "attack",
              5: "conquer", 7: "move-near"}
MODULES    = {0: "colony module", 1: "troop bay", 2: "large pilot cabin"}
CHASSIS    = {0: "shuttle", 1: "corvette"}
RESOURCES  = {0: "metal", 1: "deuterium", 2: "radioactives", 3: "crystal",
              4: "exotics"}

PTR_LO, PTR_HI = 0x00010000, 0x7FFF0000


def is_ptr(v):
    return v is not None and PTR_LO < v < PTR_HI


# ── Object wrapper ─────────────────────────────────────────────────────────
class Obj:
    """One EJBO-tagged object. Field access is by the offset the annotations
    use, so code reads the same way the annotation file does: p.u32(104)."""

    def __init__(self, snap, raw):
        self.snap = snap
        self.raw  = raw
        self.addr = raw["addr"]
        self.id   = raw["id"]
        self.type = raw["type"]
        self.name = raw["name"]
        self._f   = {f["offset"]: f for f in raw["fields"]}

    def __repr__(self):
        n = f" {self.name!r}" if self.name else ""
        return f"<{self.type} #{self.id}{n} @0x{self.addr:08X}>"

    # -- primitive reads; None when the offset is outside the class window --
    def u32(self, off):
        f = self._f.get(off)
        return None if f is None else f["u32"]

    def i32(self, off):
        f = self._f.get(off)
        return None if f is None else f["i32"]

    def f32(self, off):
        """Decode from the raw bytes. The viewer's own 'f32' key is a formatted
        string that is blank whenever the value does not look float-ish, so it
        cannot be used for a field known to be a float."""
        f = self._f.get(off)
        if f is None:
            return None
        return struct.unpack("<f", bytes.fromhex(f["raw"]))[0]

    def byte(self, off, n):
        f = self._f.get(off)
        return None if f is None else bytes.fromhex(f["raw"])[n]

    def sso(self, off):
        """MSVC std::string in its small-buffer form: 16 chars, then _Mysize."""
        size = self.u32(off + 16)
        if size is None or not 0 <= size <= 15:
            return ""
        b = b"".join(bytes.fromhex(self._f[off + k]["raw"])
                     for k in range(0, 16, 4) if off + k in self._f)
        return b[:size].decode("latin-1", "replace")

    def vec(self, off, stride):
        """std::vector at [off, off+4, off+8] -> list of `stride`-byte records."""
        b, e = self.u32(off), self.u32(off + 4)
        if not is_ptr(b) or e is None or e <= b:
            return []
        span = e - b
        if span % stride or span > 1 << 20:
            return []
        data = self.snap.read(b, span)
        return [] if data is None else [data[i:i + stride]
                                        for i in range(0, span, stride)]

    def node_target(self, off):
        """Reference-node idiom: the field points at a node whose first dword is
        the target's allocation start. Returns that start, or None for the static
        null node."""
        v = self.u32(off)
        if not is_ptr(v) or v == NULL_NODE:
            return None
        t = self.snap.rd32(v)
        return t if is_ptr(t) else None


# ── Entities ───────────────────────────────────────────────────────────────
class Civ(Obj):
    @property
    def civ_name(self):  return self.sso(-44)
    @property
    def key(self):       return self.u32(-16)          # stable registry key
    @property
    def cash(self):      return self.i32(8)
    @property
    def research(self):  return self.i32(12)
    @property
    def score(self):     return self.i32(28)
    @property
    def topic(self):     return self.i32(144)          # -1 = none

    @property
    def completed(self):
        """[(techId, cost)] — technologies and doctrines share this vector."""
        return [struct.unpack("<Ii", r) for r in self.vec(108, 8)]

    @property
    def unlocked_facilities(self):
        """Facility TYPE ids this civ may build, from the [vftable, id] vector."""
        return [struct.unpack_from("<I", r, 4)[0] for r in self.vec(172, 8)]

    @property
    def stocks(self):
        """{resourceId: amount} — 0 metal, 1 deuterium, 2 radioactives,
        3 crystal, 4 exotics."""
        return {struct.unpack_from("<I", r, 0)[0]: struct.unpack_from("<i", r, 4)[0]
                for r in self.vec(1128, 8)}

    # Both pointer forms of this object, for owner-link matching.
    @property
    def starts(self):    return (self.addr - 8, self.addr - 52)


class Design(Obj):
    """ShipDesign.

    THE DERIVED STATS ARE A LAZY CACHE, NOT A VALIDITY FLAG. `-1` in a field's
    own type (0xFFFFFFFF for ints, 0xBF800000 for the speed float) means "not
    cached", and the engine writes it across the whole block whenever parts
    change or a design is copied — `ShipDesignData::InvalidateDerivedStats` at
    0x005471D0. Each stat then has a getter shaped
    `if (cached == -1) { compute; cache; } return cached`, e.g. speed at
    0x005480A0, and the UI shows correct numbers for a -1 design precisely
    because the UI calls them.

    So a design reading -1 IS buildable. Accessors return None to keep the
    sentinel out of arithmetic, but None here means "ask the engine", not
    "broken" — treating it as broken froze the AI after every load, since a load
    leaves the block invalidated. remote.design_speed warms it.
    """

    @staticmethod
    def _n(v):
        return None if v is None or v == -1 else v

    @property
    def design_name(self): return self.sso(8)
    @property
    def speed(self):       return self._n(self.f32(36))
    @property
    def cost(self):        return self._n(self.i32(44))
    @property
    def upkeep(self):      return self._n(self.i32(48))
    @property
    def hp(self):          return self._n(self.i32(56))   # effective, shield in
    @property
    def shield(self):      return self._n(self.i32(72))
    @property
    def space(self):       return self._n(self.i32(84))
    @property
    def cost_metal(self):  return self._n(self.i32(92))
    @property
    def cost_radio(self):  return self._n(self.i32(100))

    @property
    def stats_cached(self):
        """Whether the derived stat block is warm.

        NOT a validity test. The block is a LAZY CACHE: the engine writes -1
        across it whenever parts change or a design is copied, and each stat has
        a getter of the form `if (cached == -1) { compute; cache; } return`. A
        design reading -1 is perfectly buildable — the UI shows correct numbers
        for exactly that design because the UI calls those getters.

        Gating rules on this was wrong and froze the AI after every load, since
        a load leaves the block invalidated. Use remote.design_speed to warm it.
        """
        return self.speed is not None

    # Kept as an alias so nothing silently changes meaning; prefer stats_cached.
    computed = stats_cached

    @property
    def firepower(self):
        """(light, heavy, third) — the UI's #/#/# display. An uncomputed design
        reads (-1,-1,-1), which must not be mistaken for an unarmed hull."""
        return tuple(self._n(self.i32(o)) or 0 for o in (60, 64, 68))

    def _parts(self, off):
        return [struct.unpack_from("<I", r, 4)[0] for r in self.vec(off, 8)]

    @property
    def chassis(self):  return self._parts(128)
    @property
    def scanners(self): return self._parts(152)
    @property
    def engines(self):  return self._parts(176)
    @property
    def weapons(self):  return self._parts(200)
    @property
    def modules(self):  return self._parts(224)

    # -- role taxonomy (ours, derived; see STRATEGY.md §2.4) ----------------
    @property
    def owner(self):
        """The civ this design belongs to — ShipDesign:260, a reference node.

        Designs are PER CIV and every civ's are visible in memory, so anything
        choosing a design to build must filter on this. Without it a rule will
        happily pick the rival's design, which is both wrong and not something a
        player could do.
        """
        return self.snap.civ_of(self.node_target(260))

    @property
    def is_colony(self): return 0 in self.modules
    @property
    def is_troop(self):  return 1 in self.modules
    @property
    def is_warship(self):
        """Armed per its FITTED WEAPONS, not per its firepower numbers.

        firepower reads (0,0,0) on a design whose derived stat block is cold,
        because the block holds -1 until a getter warms it. Testing
        max(firepower) > 0 therefore called a real warship unarmed whenever its
        cache had not been touched — the same lazy-cache trap that had R-XPN-01
        selecting the rival's colony design.

        ShipDesign:200 is a parts vector, not a derived stat, so it is true
        whatever the cache is doing. Use firepower to RANK armed designs, never
        to decide whether a design is armed.
        """
        return bool(self.weapons) or max(self.firepower) > 0

    @property
    def role(self):
        if self.is_colony:  return "COLONY"
        if self.is_troop:   return "TROOP"
        if self.is_warship: return "WARSHIP"
        return "SCOUT"


class Ship(Obj):
    @property
    def pos(self):        return (self.f32(4), self.f32(8), self.f32(12))
    @property
    def order_type(self): return self.i32(52)
    @property
    def order_ptr(self):  return self.u32(48)
    @property
    def condition(self):  return self.f32(136)
    @property
    def owner(self):      return self.snap.civ_of(self.node_target(40))
    @property
    def design(self):     return self.snap.design_of(self.node_target(108))
    @property
    def orbiting(self):   return self.snap.planet_of(self.node_target(44))
    @property
    def role(self):
        d = self.design
        return d.role if d else "?"
    @property
    def idle(self):       return (self.order_type or 0) == 0

    @property
    def crew(self):
        """Crew aboard — 16-byte records at Ship:124, the same shape as the
        Planet:144 citizens, each carrying job id 3.

        A SHIP WITH NO CREW CANNOT MOVE, and the engine cancels any order it is
        given at the next turn boundary: it frees the order object and clears
        Ship:48. A rule that does not check this re-issues an order every turn
        forever and leaks one engine allocation each time, while every log line
        reads like success.
        """
        return [struct.unpack_from("<I", r, 0)[0] for r in self.vec(124, 16)]

    @property
    def crewed(self):     return len(self.crew) > 0


class Planet(Obj):
    @property
    def pos(self):          return (self.f32(4), self.f32(8), self.f32(12))
    @property
    def planet_name(self):  return self.sso(52)
    @property
    def space(self):        return (self.u32(104) or 0) >> 16
    @property
    def max_pop(self):      return self.space // 10
    @property
    def food(self):         return self.i32(112)
    @property
    def food_cap(self):     return self.i32(116)
    @property
    def build_progress(self): return self.i32(120)
    @property
    def selected_building(self): return self.i32(284)
    @property
    def building_now(self): return self.i32(344) == 1
    @property
    def recruitment(self):  return self.byte(100, 3)
    @property
    def colonised_turn(self): return self.i32(92)
    @property
    def owner(self):        return self.snap.civ_of(self.node_target(40))
    @property
    def colonised(self):    return self.owner is not None

    @property
    def population(self):
        """[jobId] per citizen — 16-byte records, job at +0."""
        return [struct.unpack_from("<I", r, 0)[0] for r in self.vec(144, 16)]

    @property
    def jobs(self):
        return collections.Counter(self.population)

    @property
    def military(self):
        """Stationed military unit count — 16-byte records at Planet:168."""
        return len(self.vec(168, 16))

    @property
    def facilities(self):
        """{typeId: count} walked out of the Planet:204 map. An empty map points
        all three head slots at itself."""
        head = self.u32(204)
        if not is_ptr(head):
            return {}
        out, seen, stack = {}, set(), [head]
        while stack and len(seen) < 256:
            n = stack.pop()
            if not is_ptr(n) or n in seen:
                continue
            seen.add(n)
            blob = self.snap.read(n, 24)
            if not blob or len(blob) < 24:
                continue
            left, parent, right, vf, tid, cnt = struct.unpack_from("<IIIIII", blob, 0)
            if vf == FACILITY_VFTABLE:
                out[tid] = cnt
            stack.extend((left, parent, right))
        return out

    @property
    def production(self):
        """Name of the class at Planet:296 — the current production mode."""
        p = self.u32(296)
        if p == PILING_UP_WEALTH:
            return "PilingUpWealth"
        if not is_ptr(p):
            return None
        head = self.snap.rd32(p)
        return self.snap.class_at(head) if head else None


class Sun(Obj):
    @property
    def pos(self):        return (self.f32(4), self.f32(8), self.f32(12))
    @property
    def system_name(self): return self.sso(52)


WRAPPERS = {"Owner": Civ, "Planet": Planet, "Ship": Ship,
            "ShipDesign": Design, "Sun": Sun}


def resolve_civ(snap, name=None):
    """The civ a tool should act for, or None with a printed reason.

    Wraps Snapshot.our_civ so every entry point behaves the same: an explicit
    --civ must exist, and with no --civ we take the local player rather than
    guessing at a name that customisation may have changed.
    """
    try:
        return snap.our_civ(name)
    except LookupError as e:
        print(f"error: {e}")
        return None


def dist(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


# ── Snapshot ───────────────────────────────────────────────────────────────
class Snapshot:
    """One scan of the game. Construct, then read; nothing refreshes itself."""

    def __init__(self, state=None):
        self.state = state or ev.ViewerState()
        if self.state.handle is None and not self.state.connect():
            raise RuntimeError("CosmicSupremacy is not running")
        self.h = self.state.handle
        self.refresh()

    # -- raw access -----------------------------------------------------
    def read(self, addr, n):
        return ev.read_bytes(self.h, addr, n)

    def rd32(self, addr):
        b = self.read(addr, 4)
        return struct.unpack("<I", b)[0] if b and len(b) == 4 else None

    def class_at(self, vftable):
        return ev.resolve_class_name(self.h, vftable, self.state.rtti_cache)

    # -- scan -----------------------------------------------------------
    def refresh(self):
        self.state.scan()
        self.turn = self.rd32(TURN_COUNTER)
        by = collections.defaultdict(list)
        for raw in self.state.objects:
            # Objects inside the module image are static templates (the .data
            # Admiral and Governor), not live entities.
            if raw["static"]:
                continue
            cls = WRAPPERS.get(raw["type"])
            if cls:
                by[raw["type"]].append(cls(self, raw))
        self.civs    = by["Owner"]
        self.planets = by["Planet"]
        self.ships   = by["Ship"]
        self.designs = by["ShipDesign"]
        self.suns    = by["Sun"]

        # Allocation-start -> entity, accepting every pointer form a reference
        # node might carry for that class.
        self._civ_by_start = {}
        for c in self.civs:
            for s in c.starts:
                self._civ_by_start[s] = c
        self._design_by_start = {}
        for d in self.designs:
            self._design_by_start[d.addr - 8]  = d
            self._design_by_start[d.addr - 12] = d
        self._planet_by_start = {p.addr - 8: p for p in self.planets}

    # -- link resolution ------------------------------------------------
    def civ_of(self, start):    return self._civ_by_start.get(start)
    def design_of(self, start): return self._design_by_start.get(start)
    def planet_of(self, start): return self._planet_by_start.get(start)

    # -- convenience ----------------------------------------------------
    def civ(self, name):
        """A civ by the name at Owner:-44."""
        return next((c for c in self.civs if c.civ_name == name), None)

    def local_civ(self):
        """The human player's civ, from the .data local-player node.

        Preferred over matching a name, because the civ name is EDITABLE during
        home-world customisation: a hardcoded 'GoodGuy' silently matches nothing
        the moment a player renames their empire, and every rule then quietly
        does nothing. The pointer is stable and confirmed across separate games.
        """
        node = self.rd32(LOCAL_PLAYER)
        if not is_ptr(node) or node == NULL_NODE:
            return None
        return self.civ_of(self.rd32(node))

    def our_civ(self, name=None):
        """The civ a tool should act for: the named one, else the local player."""
        if name:
            c = self.civ(name)
            if c is None:
                raise LookupError(f"no civ named {name!r}; saw "
                                  f"{[x.civ_name for x in self.civs]}")
            return c
        c = self.local_civ()
        if c is None:
            raise LookupError(f"could not resolve the local player from "
                              f"0x{LOCAL_PLAYER:08X}; pass --civ explicitly")
        return c

    def owned_planets(self, civ):
        return [p for p in self.planets if p.owner is civ]

    def owned_ships(self, civ):
        return [s for s in self.ships if s.owner is civ]

    def unowned_planets(self):
        return [p for p in self.planets if p.owner is None]

    def nearest_sun(self, planet):
        if not self.suns:
            return None, None
        s = min(self.suns, key=lambda x: dist(planet.pos, x.pos))
        return s, dist(planet.pos, s.pos)

    def systems(self):
        """{Sun: [Planet]} by nearest-sun. A heuristic — see the module docstring
        and system_report()."""
        groups = collections.defaultdict(list)
        for p in self.planets:
            s, _ = self.nearest_sun(p)
            if s is not None:
                groups[s].append(p)
        return groups

    def system_report(self):
        """Evidence for or against the nearest-sun grouping: if a system really is
        a tight cluster, every planet's radius is far below the gap to the next
        sun. Prints the numbers rather than asserting them."""
        groups = self.systems()
        sizes  = sorted(len(v) for v in groups.values())
        radii  = [dist(p.pos, s.pos) for s, ps in groups.items() for p in ps]
        gaps = []
        for s in self.suns:
            others = [dist(s.pos, o.pos) for o in self.suns if o is not s]
            if others:
                gaps.append(min(others))
        return {
            "suns": len(self.suns),
            "planets": len(self.planets),
            "grouped_suns": len(groups),
            "planets_per_system_min_med_max": (
                sizes[0], sizes[len(sizes) // 2], sizes[-1]) if sizes else None,
            "max_orbit_radius": max(radii) if radii else None,
            "min_sun_separation": min(gaps) if gaps else None,
        }
