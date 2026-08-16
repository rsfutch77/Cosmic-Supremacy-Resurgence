"""
actions.py — Actuators: the button clicks the AI is allowed to make
==================================================================
Every write to the game goes through this module, and every one of them is a
field a human could have changed by clicking. See STRATEGY.md §1.1 for the
no-cheating rule; the short version is that this module must never write cash,
research, food, construction progress, resource stocks or facility counts.

Dry-run is the default everywhere. An Actuator with dry_run=True logs the exact
address, old value and new value of every intended write and performs none of
them, which is how a rule gets reviewed before it is trusted.

── The order object ───────────────────────────────────────────────────────────
Ship:48 points at a 96-byte plain struct (no vftable). Measured layout:

    +0          1 on the one order observed; meaning unknown
    +4/8/12     origin XYZ (the ship's position when the order was issued)
    +16/20/24   TARGET XYZ — exact: a scout order's matched a Sun to 0.0000
    +28         intrusive list head; its nodes point back at order+28
    +40/44/48   ROUTE vector, one 28-byte leg [originXYZ, destXYZ, length].
                length is the euclidean distance between the leg's own two
                triples, confirmed to 4 decimals, and ETA = ceil(length/speed)
    +52         second intrusive list head, nodes point back at order+52;
                one node carries a ShipChassis vftable
    +80/84/88   origin XYZ again
    +92         reference node -> the ORIGIN planet

Size was pinned by the NT heap block headers either side: (0x67610791,
0x88001300) at -8 and a matching-shape pair at +96, with the next block's
vftable at +104.

THE ORDER OBJECT IS NEVER FABRICATED. Its route vector and both lists are
separate heap allocations the engine owns and will eventually free; handing its
allocator a VirtualAllocEx pointer is a crash rather than the harmless leak that
set_population.py accepts. Three legitimate ways to get one, all CONFIRMED:

  create_order()    Allocate from the engine's OWN heap with its operator new and
                    run its OWN order constructor, through remote.py. Originates
                    an order with no user interaction whatsoever.
  transfer_order()  Move an existing order between ships. Allocates nothing.
  retarget_order()  Rewrite the plain floats and ints inside one. Allocates
                    nothing.
"""
import struct

import gamestate as gs
import remote

# Order-object field offsets, from the layout above.
ORD_ORIGIN   = 4
ORD_TARGET   = 16
ORD_ROUTE    = 40      # begin/end/cap at +40/+44/+48
ORD_PROGRESS = 76      # float: distance travelled along the route so far
ORD_ORIGIN2  = 80
ORD_SIZE     = 96
ROUTE_STRIDE = 28      # [ox,oy,oz, dx,dy,dz, length]

# Ship fields
SHIP_ORDER_PTR  = 48
SHIP_ORDER_TYPE = 52
SHIP_HAS_ORDERS = 76


class Actuator:
    """Collects and optionally performs writes. One per decision pass."""

    def __init__(self, snap, dry_run=True, log=print, remote_=None,
                 remote_factory=None):
        self.snap = snap
        self.dry_run = dry_run
        self.log = log
        self.writes = []
        # Elevated access is needed ONLY to construct engine objects. Most
        # passes never do, so it is acquired lazily: a turn that just queues a
        # build or reassigns a citizen should not be opening a handle with
        # PROCESS_ALL_ACCESS at all.
        self._remote = remote_
        self._remote_factory = remote_factory
        # Ships already committed by an earlier rule THIS PASS. Every rule in a
        # pass reads the SAME snapshot, so a ship ordered by one rule still
        # looks idle to the next one — Expand sent a colony ship to a planet and
        # Explore, reading the stale order type, redirected it to a star in the
        # same pass. That repeated for eight turns and expansion stalled. Rule
        # ORDER cannot fix it; something has to record the claim.
        self.claimed_ships = set()
        # Ships crewed THIS PASS. Same stale-snapshot problem as claimed_ships:
        # R-XPL-06 loads crew, then R-XPN-02 reads Ship:124 from the snapshot,
        # still sees an empty vector and refuses to order the ship. It sorts
        # itself out next turn, so it costs a turn per ship rather than
        # deadlocking -- but it made crew loading look like it had failed.
        self.crewed_ships = set()
        # Planets whose CITIZENS a rule has already rewritten this pass. S-food
        # owns farmers and R-XPL-01 owns everyone else, but they read the same
        # snapshot: if S-food converts a banker to a farmer to stop a famine,
        # R-XPL-01 still sees a banker at that index and would convert it
        # straight back, undoing the famine fix every single turn.
        self.claimed_planets = set()
        # Planets whose PRODUCTION a rule has already set this pass. Planet:296
        # and Planet:344 hold exactly one build, so a second write silently
        # replaces the first: R-XTM-01 queued a warship at the homeworld and
        # R-XPN-01 queued a colony ship over the top of it in the same pass,
        # and the log read as though both had been ordered. Enforced HERE rather
        # than in each rule, because every rule that builds goes through these
        # two actuators and none of them can be relied on to remember.
        self.claimed_production = set()
        # Net effect OUR OWN actions had on the treasury this pass, signed:
        # negative when we spend. The income sensor differences Owner:8 across
        # turns, so without this our spending reads as a collapsing economy —
        # hurrying 1,184 credits made the next turn report income -742/turn and
        # R-XPL-08 refused to hurry "into a falling balance" that was entirely
        # its own purchase.
        self.cash_effect = 0

    @property
    def remote(self):
        if self._remote is None and self._remote_factory is not None:
            self._remote = self._remote_factory()
        return self._remote

    def claim(self, ship):
        """Mark a ship as committed for the rest of this pass."""
        self.claimed_ships.add(ship.id)

    def is_claimed(self, ship):
        return ship.id in self.claimed_ships

    def production_claimed(self, planet):
        """Has a rule already set this planet's production this pass?

        Rules should FILTER on this when choosing where to build. The raise in
        _claim_production stays as a backstop for anything that does not, but a
        backstop that fires aborts the whole rule: R-XPN-01 hit it, propagated a
        ValueError to the loop, and skipped every other shipyard it might have
        used that turn.
        """
        return planet.id in self.claimed_production

    def _claim_production(self, planet, what):
        if planet.id in self.claimed_production:
            raise ValueError(
                f"{planet} already had its production set this pass; "
                f"{what} would silently replace it. Production is one slot.")
        self.claimed_production.add(planet.id)

    def claim_planet(self, planet):
        self.claimed_planets.add(planet.id)

    def planet_claimed(self, planet):
        return planet.id in self.claimed_planets

    def is_crewed(self, ship):
        """Crewed per the snapshot, OR crewed by a rule earlier this pass."""
        return ship.crewed or ship.id in self.crewed_ships

    # -- primitive ------------------------------------------------------
    def _write(self, addr, data, what):
        old = self.snap.read(addr, len(data)) or b""
        self.writes.append((addr, old, data, what))
        tag = "WOULD WRITE" if self.dry_run else "WRITE"
        self.log(f"  [{tag}] 0x{addr:08X} {old.hex()} -> {data.hex()}   {what}")
        if self.dry_run:
            return True
        ok = gs.ev.write_bytes(self.snap.h, addr, data)
        if not ok:
            self.log(f"  [FAILED] 0x{addr:08X}  {what}")
        return ok

    def _u32(self, addr, value, what):
        return self._write(addr, struct.pack("<I", value & 0xFFFFFFFF), what)

    def _f32(self, addr, value, what):
        return self._write(addr, struct.pack("<f", value), what)

    def _xyz(self, addr, xyz, what):
        return self._write(addr, struct.pack("<fff", *xyz), what)

    # -- A: set a citizen's job (Planet:144 element +0) ------------------
    def set_job(self, planet, index, job_id):
        """Change one citizen's job. A plain int in an existing vector element:
        no allocation, no structural change."""
        if job_id not in gs.JOBS:
            raise ValueError(f"job id {job_id} is not one a human could pick")
        begin = planet.u32(144)
        if not gs.is_ptr(begin):
            raise ValueError(f"{planet} has no citizen vector")
        if not 0 <= index < len(planet.population):
            raise IndexError(f"{planet} has no citizen {index}")
        return self._u32(begin + index * 16, job_id,
                         f"{planet} citizen {index} -> {gs.JOBS[job_id]}")

    # -- D: queue a ship (Planet:296 + Planet:344) ----------------------
    def queue_ship(self, planet, design):
        """Start building a ship on a planet.

        A ship build does NOT allocate a production object. `Planet:296` points
        straight at the shared `ShipDesign` in its ALLOCATION-START form
        (tag - 12, the multiple-inheritance primary vftable) — confirmed by
        reading it while a scout was queued and decoding the target in place as
        the design itself, down to its name string and speed. That makes this
        the cheapest actuator in the set: two plain writes to long-lived
        objects, no allocation, none of the lifetime hazards ship orders had.

        Contrast a FACILITY build, where `Planet:296` points at a freshly
        heap-allocated `Facility` — that direction is still not solved.
        """
        if design.type != "ShipDesign":
            raise ValueError(f"{design} is not a ShipDesign")
        # NOT gated on the derived stat block. That block is a lazy cache set
        # to -1 whenever parts change; a design reading -1 is perfectly
        # buildable and the UI shows real numbers for it by calling the getters.
        # What actually makes a design unbuildable is having no parts.
        if not design.chassis:
            raise ValueError(f"{design} has no chassis; the UI could not have "
                             f"produced it and it is not buildable")
        if not design.engines:
            raise ValueError(f"{design} has no engine, so nothing built from it "
                             f"could move")
        # A human can only queue ships where there is a shipyard (facility 2).
        if 2 not in planet.facilities:
            raise ValueError(f"{planet} has no shipyard, so the UI could not "
                             f"queue a ship there")
        self.log(f"  queue {design.design_name!r} at {planet}")
        self._claim_production(planet, f"queueing {design.design_name!r}")
        ok = self._u32(planet.addr + 296, design.addr - 12,
                       f"Planet:296 -> ShipDesign {design.design_name!r} (tag-12)")
        return self._u32(planet.addr + 344, 1, "Planet:344 build-active -> 1") and ok

    # -- C: start a facility build (three plain writes) -----------------
    PLANET_EMBEDDED_FACILITY = 280   # Facility{vftable, typeId} lives here

    def build_facility(self, planet, type_id):
        """Start building a facility. Nothing is allocated.

        A planet is BORN carrying a valid `Facility{vftable, typeId}` embedded at
        Planet:280, written once by the PlanetProperties constructor and
        resident for the planet's whole life. Selecting a building overwrites
        its type id at Planet:284; starting the build points Planet:296 at the
        embedded object. That is the entire mechanism, and it is why Planet:284
        survives a switch to generating wealth — it is a field of a permanent
        object, not of a production object that comes and goes.

        Safer than the ship actuator: the target is inside the planet, so
        nothing can be freed underneath it.

        THIS SKIPS THE ENGINE'S OWN CanBuildFacility CHECK (0x004F5030), so the
        caller must not ask for a type the civ cannot actually build. Owner:172
        is documented as the unlocked-type list but reads EMPTY on a civ with
        three facilities standing, so it cannot be trusted as that gate — the
        rules use evidence instead, meaning a type already built somewhere.
        """
        if type_id not in gs.FACILITIES:
            raise ValueError(f"{type_id} is not a known facility type")
        if planet.u32(self.PLANET_EMBEDDED_FACILITY) != gs.FACILITY_VFTABLE:
            raise ValueError(
                f"{planet} has no embedded Facility at +280 (read "
                f"0x{planet.u32(self.PLANET_EMBEDDED_FACILITY):08X}); refusing "
                f"to point production at something that is not one")
        if planet.building_now:
            raise ValueError(f"{planet} is already building; replacing it would "
                             f"discard {planet.build_progress} progress")
        name = gs.FACILITIES[type_id]
        self.log(f"  build {name} at {planet}")
        ok = self._u32(planet.addr + 284, type_id, f"Planet:284 -> {name}")
        self._claim_production(planet, f"building facility type {type_id}")
        ok = self._u32(planet.addr + 296,
                       planet.addr + self.PLANET_EMBEDDED_FACILITY,
                       "Planet:296 -> the planet's embedded Facility") and ok
        return self._u32(planet.addr + 344, 1, "Planet:344 build-active -> 1") and ok

    # -- I': fit parts to a design --------------------------------------
    # The five part categories sit at a 24-byte stride, each a std::vector of
    # 8-byte [vftable, subtypeId] records.
    PART_VECTORS = {
        "chassis":  (128, 0x007515E0),   # ShipChassis   0 shuttle, 1 corvette
        "scanners": (152, 0x00763028),   # ShipScanner   0 neutron
        "engines":  (176, 0x00763054),   # ShipEngine    0 nuclear, 1 fusion
        "weapons":  (200, 0x007589A8),   # ShipWeapon    0 mass driver, 10 fusion bomb
        "modules":  (224, 0x007573A0),   # ShipModule    0 colony, 1 troop bay, 2 cabin
    }

    def fit_parts(self, design, category, ids):
        """Populate an EMPTY part vector on a design.

        A default-constructed design arrives with a chassis and a scanner but
        NO ENGINE, which is why its speed and cost stay at the -1 sentinel and
        why it cannot be built. Fitting an engine is what turns it into a real
        design.

        The buffer comes from the engine's OWN `operator new`, so when the
        vector is eventually destroyed its `operator delete` is the matching
        half of the pair. That is the same reasoning that makes create_order
        safe, and the reason a VirtualAllocEx buffer would not be.

        ONLY fills a vector that is currently null. Growing or replacing an
        existing engine-owned buffer would mean freeing memory we did not
        allocate, which is exactly the hazard this avoids.
        """
        if category not in self.PART_VECTORS:
            raise ValueError(f"{category!r} is not one of "
                             f"{sorted(self.PART_VECTORS)}")
        off, vftable = self.PART_VECTORS[category]
        begin, end = design.u32(off), design.u32(off + 4)
        if gs.is_ptr(begin) or (end and end != begin):
            raise ValueError(
                f"{design} already has a {category} vector at 0x{begin:08X}; "
                f"this only populates an empty one, because replacing an "
                f"engine-owned buffer would mean freeing memory we did not "
                f"allocate")
        if not ids:
            raise ValueError("no part ids given")
        if self.dry_run:
            self.log(f"  [WOULD FIT] {category}={ids} on {design} "
                     f"(operator new({len(ids) * 8}) + vector begin/end/cap)")
            return None
        if self.remote is None:
            raise RuntimeError("fit_parts needs a remote.Remote to allocate")

        size = len(ids) * 8
        buf = self.remote.operator_new(size)
        payload = b"".join(struct.pack("<II", vftable, i) for i in ids)
        self._write(buf, payload, f"{category} records {ids}")
        self._u32(design.addr + off,     buf,        f"{category} begin")
        self._u32(design.addr + off + 4, buf + size, f"{category} end")
        self._u32(design.addr + off + 8, buf + size, f"{category} cap")
        self.log(f"  fitted {len(ids)} {category[:-1] if len(ids) == 1 else category}"
                 f" to {design}")
        return buf

    # -- I'': give a design an owner ------------------------------------
    DESIGN_OWNER = 260

    def set_design_owner(self, design, civ):
        """Point ShipDesign:260 at a civ's owner reference node.

        A default-constructed design carries the static null node 0x00857C54
        here, while every design made through the UI carries a real node
        resolving to its civ's Owner — confirmed across four designs, two civs.
        That is the difference between a design the game considers yours and one
        that belongs to nobody, and is the likeliest reason a constructed design
        does not appear in the UI's design list.

        The node is REUSED from another design of the same civ, never
        fabricated. That is the same rule the report gives for Ship:40: take the
        node from something that already has one, because a hand-made node is
        not registered anywhere the engine knows about.
        """
        def owner_of(d):
            node = d.u32(self.DESIGN_OWNER)
            if not gs.is_ptr(node) or node == gs.NULL_NODE:
                return None
            return self.snap.civ_of(self.snap.rd32(node))

        # Compare by ADDRESS, not identity. Snapshot.refresh() rebuilds every
        # wrapper, so a Civ handed in by a caller is a different object from the
        # one in the current snapshot even though both describe the same Owner —
        # an `is` check here could never match after a refresh.
        donor = next((d for d in self.snap.designs
                      if d.addr != design.addr
                      and (owner_of(d) is not None)
                      and owner_of(d).addr == civ.addr), None)
        if donor is None:
            seen = {d.design_name: (owner_of(d).civ_name if owner_of(d) else None)
                    for d in self.snap.designs}
            raise ValueError(
                f"no existing design of {civ.civ_name!r} to take an owner node "
                f"from; fabricating one is not safe. Design owners seen: {seen}")
        node = donor.u32(self.DESIGN_OWNER)
        self.log(f"  owner node 0x{node:08X} (from {donor.design_name!r})")
        return self._u32(design.addr + self.DESIGN_OWNER, node,
                         f"ShipDesign:260 -> Owner {civ.civ_name!r}")

    # -- I: create a ship design ----------------------------------------
    def create_design(self, name, civ=None):
        """Default-construct a new ShipDesign. UNTESTED — see the warning below.

        The engine allocates 280 bytes and its own constructor at 0x00546EC0
        initialises them; that constructor takes no arguments and reaches the
        EJBO-tagging base class, so the result should be a properly tagged,
        enumerable design.

        THE RESULT HAS NO PARTS. All five part vectors (chassis, scanners,
        engines, weapons, modules) come back empty, and a design with no chassis
        or engine is almost certainly not buildable. Populating them means
        growing engine-owned vectors, which is not solved. So this produces a
        valid object, not yet a usable design.

        There is NO CLONE PATH. The one-argument constructor at 0x005470A0 was
        mistaken for a copy constructor; it is ShipDesign::ShipDesign(Stream*),
        a deserializing constructor for the save format's DSGN section. Passing
        it a design crashed the client.

        Every construction is VERIFIED before a single byte is written into the
        result — that check is the whole lesson from the crash.
        """
        if len(name) > 15:
            raise ValueError(f"{name!r} is longer than the 15-char SSO buffer; "
                             f"a longer name would move the string off-heap and "
                             f"turn ShipDesign:8 into a pointer")
        # The UI requires a unique design name before it will save one, so a
        # duplicate is a value a human could not have produced. It also makes
        # every later lookup by name ambiguous.
        clash = [d for d in self.snap.designs if d.design_name == name]
        if clash:
            raise ValueError(
                f"a design named {name!r} already exists (#{clash[0].id}); the "
                f"UI enforces unique names, so pick another")
        if self.dry_run:
            self.log(f"  [WOULD CREATE] default-constructed ShipDesign {name!r} "
                     f"(no parts — see create_design docstring)")
            return None
        if self.remote is None:
            raise RuntimeError("create_design needs a remote.Remote")

        block = self.remote.operator_new(remote.DESIGN_SIZE)
        self.remote.construct_design(block)

        ok, why = self.remote.verify_design(block)
        if not ok:
            self.log(f"  [ABORT] construction did not take: {why}")
            self.log(f"  0x{block:08X} is leaked but NOT written to, and the "
                     f"client is untouched. Nothing further is safe here.")
            return None
        self.log(f"  constructed and verified: {why} at 0x{block:08X}")

        tag = block + 12          # ShipDesign front is 12: -12 vft, -8 vft, -4 id
        raw = name.encode("ascii").ljust(16, b"\x00")
        self._write(tag + 8, raw, f"design name -> {name!r}")
        self._u32(tag + 24, len(name), "name _Mysize")
        self._u32(tag + 28, 15, "name _Myres (SSO)")

        # A design with no owner belongs to nobody. The default constructor
        # leaves ShipDesign:260 as the static null node, so attaching an owner
        # is part of creating a design, not an optional extra.
        if civ is not None:
            self.snap.refresh()
            fresh = next((d for d in self.snap.designs if d.addr == tag), None)
            if fresh is None:
                self.log("  [warn] the new design is not enumerable, so no "
                         "owner node could be attached")
            else:
                try:
                    self.set_design_owner(fresh, civ)
                except ValueError as e:
                    # Loud, but not fatal: the design exists and is worth
                    # inspecting even unowned, and raising here would strand it
                    # AND skip everything the caller wanted to do next.
                    self.log(f"  [warn] could not set the owner: {e}")
        return block

    def set_wealth_mode(self, planet):
        """Stop building and generate wealth instead.

        The reverse of queue_ship, and the one production direction that was
        always reachable: every idle planet shares one static `PilingUpWealth`
        singleton, so this is a known-constant pointer write.
        """
        self.log(f"  {planet} -> generating wealth")
        ok = self._u32(planet.addr + 296, gs.PILING_UP_WEALTH,
                       "Planet:296 -> PilingUpWealth singleton")
        return self._u32(planet.addr + 344, 0, "Planet:344 build-active -> 0") and ok

    # -- J: move a trained unit from a planet onto a ship as crew -------
    SHIP_CREW      = 124      # begin/end/cap at 124/128/132
    PLANET_MILITARY = 168     # begin/end/cap at 168/172/176
    CITIZEN_LIST   = 144      # begin/end/cap at 144/148/152
    CITIZEN_STRIDE = 16

    def load_crew(self, planet, ship, count=2):
        """Move `count` stationed military units into a ship's crew vector.

        CONFIRMED many times since — scouts, warships and troop hulls have all
        been crewed this way and flown. It now also TOPS UP an already-crewed
        ship, which it used to refuse; see `_set_ship_crew` for what that costs.
        The reasoning it rests on:

        A stationed military unit, a ship's crew member and a planet citizen all
        appear to be the SAME 16-byte record — [jobId, flags, owner-node,
        turnAdded]. Crew read job id 3 and flags 0x004F0000, which is the exact
        citizen shape, and Planet:168's elements read 3 in the same slot. The
        annotation calls that slot "upkeep, 3 on all units", which now looks like
        a misread of the job id, since every unit having the same upkeep and
        every unit having job 3 are indistinguishable from one sample.

        So loading crew is a MOVE between two vectors of identical records:

          * the ship's crew buffer comes from the engine's own operator new, so
            new/delete stay a matched pair — the same rule fit_parts follows
          * the planet's military vector is SHRUNK by walking `end` back, which
            frees nothing and keeps its capacity, exactly what set_population.py
            does to reduce a population safely

        A crewed ship differed from an identical uncrewed one at 124/128/132 and
        nowhere else, so there is no separate crew count to keep in step.
        """
        self._require_sane(planet.addr + self.PLANET_MILITARY,
                           f"{planet}'s military vector")
        self._require_sane(ship.addr + self.SHIP_CREW, f"{ship}'s crew vector")
        mil = planet.vec(self.PLANET_MILITARY, self.CITIZEN_STRIDE)
        if len(mil) < count:
            raise ValueError(f"{planet} has {len(mil)} trained unit(s), "
                             f"need {count}")
        aboard = ship.vec(self.SHIP_CREW, self.CITIZEN_STRIDE)
        if self.dry_run:
            self.log(f"  [WOULD LOAD] {count} unit(s) from {planet} onto {ship} "
                     f"({len(aboard)} aboard, {len(mil)} trained, "
                     f"{len(mil) - count} would remain)")
            self.crewed_ships.add(ship.id)
            return None
        if self.remote is None:
            raise RuntimeError("load_crew needs a remote.Remote to allocate")

        # Take from the END so the surviving records stay contiguous from begin.
        taken = b"".join(mil[-count:])
        buf = self._set_ship_crew(ship, b"".join(aboard) + taken,
                                  len(aboard) + count)

        mb = planet.u32(self.PLANET_MILITARY)
        new_end = mb + (len(mil) - count) * self.CITIZEN_STRIDE
        self._u32(planet.addr + self.PLANET_MILITARY + 4, new_end,
                  f"planet military end -> {len(mil) - count} unit(s)")
        self.crewed_ships.add(ship.id)
        self.log(f"  moved {count} unit(s) from {planet} to {ship} "
                 f"({len(aboard)} -> {len(aboard) + count} aboard)")
        return buf

    # A std::vector is three pointers into ONE allocation. If they ever stop
    # agreeing, anything that walks begin..end walks across unrelated memory.
    VEC_SANE_SPAN = 64 * 16          # 64 records is far past anything real

    def vector_is_sane(self, base, what=""):
        """begin/end/cap consistent, and pointing into the same allocation?

        THIS EXISTS BECAUSE WE CORRUPTED ONE. A planet's military vector was
        found with `begin` at 0x0AD4F928 and `end` at 0x02D99268 — two different
        heap regions — and the engine crashed walking it a turn later, reading
        0x02DBF00C. The write that exposed it was ours, computing a new `end`
        from a `begin` that no longer belonged to the same buffer as the `end`
        it replaced.

        A vector we did not put in that state is one we must not write to: the
        damage is already done and adding to it destroys the evidence as well as
        the client. So this refuses rather than repairs.
        """
        begin = self.snap.rd32(base)
        end   = self.snap.rd32(base + 4)
        cap   = self.snap.rd32(base + 8)
        if begin is None or end is None or cap is None:
            return False, "could not read the vector"
        if begin == 0 and end == 0:
            return True, "empty"
        if not (gs.is_ptr(begin) and gs.is_ptr(end) and gs.is_ptr(cap)):
            return False, (f"not all pointers: begin 0x{begin:08X} "
                           f"end 0x{end:08X} cap 0x{cap:08X}")
        if not (begin <= end <= cap):
            return False, (f"out of order: begin 0x{begin:08X} "
                           f"end 0x{end:08X} cap 0x{cap:08X}")
        span = end - begin
        if span % self.CITIZEN_STRIDE:
            return False, (f"span {span} is not a whole number of "
                           f"{self.CITIZEN_STRIDE}-byte records")
        if span > self.VEC_SANE_SPAN:
            return False, f"span {span} is implausibly large"
        # The give-away in the crash we had: begin and end in different 64 KB
        # regions of very different heaps.
        if (begin >> 24) != (end >> 24):
            return False, (f"begin 0x{begin:08X} and end 0x{end:08X} are in "
                           f"different heaps — this vector is already corrupt")
        return True, f"{span // self.CITIZEN_STRIDE} record(s)"

    def _require_sane(self, base, what):
        ok, why = self.vector_is_sane(base, what)
        if not ok:
            raise ValueError(f"refusing to touch {what}: {why}")
        return True

    def _set_ship_crew(self, ship, records, n, allow_replace=False):
        """Point a ship's crew vector at a fresh engine buffer holding `records`.

        REPOINTING A CREW VECTOR THE ENGINE ALREADY OWNS CRASHES THE CLIENT, and
        that is measured, not feared. This method was written to "top up" a
        crewed ship by allocating a bigger buffer and abandoning the old one, on
        the reasoning that a leak is bounded and harmless. The original code had
        refused to do exactly that, saying growing an engine-owned buffer would
        mean freeing memory we did not allocate. It was right.

        The bisect that found it, 45 turns per arm from one checkpoint:

            conscription on, crew loading on   -> client dies, ~30 turns later
            conscription on, crew loading OFF  -> survives 45/45, 19 drafts

        Conscription was blamed first, and it is innocent: it merely PRODUCES the
        units, which is what makes the loader run at all. With drafting off there
        was nothing to load and the crash went away, which framed the wrong
        suspect for three arms.

        So a crew vector is only ever FILLED WHEN EMPTY. Filling an empty one is
        the path every game before this session used, and it is in every arm that
        survives. `allow_replace` exists for a caller that has proven it owns the
        buffer; nothing sets it today.
        """
        old = ship.u32(self.SHIP_CREW)
        if gs.is_ptr(old) and not allow_replace:
            raise ValueError(
                f"{ship} already has a crew vector (0x{old:08X}); repointing "
                f"one the engine owns is what crashed the client. Load the full "
                f"complement when the ship is empty, or leave it alone")
        size = n * self.CITIZEN_STRIDE
        buf = self.remote.operator_new(size)
        self._write(buf, records, f"{n} crew record(s)")
        self._u32(ship.addr + self.SHIP_CREW,     buf,        "crew begin")
        self._u32(ship.addr + self.SHIP_CREW + 4, buf + size, "crew end")
        self._u32(ship.addr + self.SHIP_CREW + 8, buf + size, "crew cap")
        if gs.is_ptr(old):
            self.log(f"  [leak] abandoned the old crew buffer 0x{old:08X}; "
                     f"freeing an engine allocation is not something we do")
        return buf

    def _append_military(self, planet, records):
        """Add 16-byte units to a planet's stationed-military vector.

        Uses spare capacity when the engine's buffer has it, and otherwise
        reallocates through the ENGINE'S OWN `operator new` and repoints the
        three pointers. That reallocation is legitimate precisely because the
        buffer comes from the engine's allocator: when recruitment later grows
        the vector, the engine frees a block its own allocator handed out.

        The abandoned block leaks, which is the same bounded trade `load_crew`
        makes for a ship's crew vector — and that path runs in every arm of the
        crash bisect that SURVIVES, which is what makes it credible rather than
        merely argued.
        """
        base = planet.addr + self.PLANET_MILITARY
        self._require_sane(base, f"{planet}'s military vector")
        mil = planet.vec(self.PLANET_MILITARY, self.CITIZEN_STRIDE)
        begin = planet.u32(self.PLANET_MILITARY)
        end   = planet.u32(self.PLANET_MILITARY + 4)
        cap   = planet.u32(self.PLANET_MILITARY + 8)
        blob  = b"".join(records)
        need  = len(blob)

        if gs.is_ptr(begin) and cap and end and cap - end >= need:
            self._write(end, blob, f"{len(records)} unit(s) into spare capacity")
            self._u32(base + 4, end + need,
                      f"military end -> {len(mil) + len(records)} unit(s)")
            return
        if gs.is_ptr(begin):
            # Same rule as a ship's crew vector, and for the same measured
            # reason: repointing a vector the engine already owns is what
            # crashed the client. A planet's military vector is worse than a
            # ship's, because recruitment appends to it — the engine and this
            # code would be trading ownership of one buffer every few turns.
            raise ValueError(
                f"{planet}'s military vector is full "
                f"({(cap or 0) - (end or 0)} bytes spare, need {need}) and "
                f"already engine-owned; growing it is what crashed the client")
        # An EMPTY vector belongs to nobody yet, so this is the one safe case.
        size = len(blob)
        buf = self.remote.operator_new(size)
        self._write(buf, blob, f"{len(records)} unit(s) into a fresh buffer")
        self._u32(base,     buf,        "military begin")
        self._u32(base + 4, buf + size, "military end")
        self._u32(base + 8, buf + size, "military cap")

    def unload_crew(self, ship, planet, count=1, keep=None):
        """Garrison troops FROM a ship onto a planet — the reverse of load_crew.

        Why the AI needs it: a freshly conquered planet is disloyal and in civil
        disorder, and the repair is occupying infantry. The invasion already
        leaves one unit behind as the garrison; putting the rest of the hull's
        troops down is what turns a captured planet into a held one.

        `keep` guards the flight crew — unload past `ShipDesign:76` and the ship
        is stranded wherever it happens to be, which is by definition inside
        hostile territory.

        The planet side APPENDS, and appending is the direction a vector cannot
        do for free. When the engine's buffer has spare capacity the records go
        in place and only `end` moves; otherwise the whole vector is reallocated
        and the old block is abandoned, same trade as _set_ship_crew.
        """
        self._require_sane(ship.addr + self.SHIP_CREW, f"{ship}'s crew vector")
        self._require_sane(planet.addr + self.PLANET_MILITARY,
                           f"{planet}'s military vector")
        aboard = ship.vec(self.SHIP_CREW, self.CITIZEN_STRIDE)
        if keep is not None and len(aboard) - count < keep:
            raise ValueError(
                f"{ship} carries {len(aboard)}; unloading {count} would leave "
                f"{len(aboard) - count} against a flight minimum of {keep}, "
                f"stranding it in hostile space")
        if count <= 0 or len(aboard) < count:
            raise ValueError(f"{ship} carries {len(aboard)}, cannot unload "
                             f"{count}")
        mil = planet.vec(self.PLANET_MILITARY, self.CITIZEN_STRIDE)
        if self.dry_run:
            self.log(f"  [WOULD GARRISON] {count} unit(s) from {ship} onto "
                     f"{planet} ({planet.military} stationed -> "
                     f"{planet.military + count})")
            return None
        if self.remote is None:
            raise RuntimeError("unload_crew needs a remote.Remote to allocate")

        landed = b"".join(aboard[-count:])
        begin = planet.u32(self.PLANET_MILITARY)
        end   = planet.u32(self.PLANET_MILITARY + 4)
        cap   = planet.u32(self.PLANET_MILITARY + 8)
        need  = count * self.CITIZEN_STRIDE
        if gs.is_ptr(begin) and cap is not None and end is not None \
                and cap - end >= need:
            self._write(end, landed, f"{count} unit(s) into spare capacity")
            self._u32(planet.addr + self.PLANET_MILITARY + 4, end + need,
                      f"planet military end -> {len(mil) + count} unit(s)")
        else:
            # DO NOT REALLOCATE A PLANET'S MILITARY VECTOR. This branch used to,
            # and it is what corrupted one: our buffer became `begin`, then the
            # engine's own push_back on the next recruited unit reallocated
            # through ITS allocator and left the three pointers straddling two
            # allocations — begin at 0x0AD4F928 with end at 0x02D99268. A turn
            # later the engine walked begin..end across unrelated memory and
            # died reading 0x02DBF00C.
            #
            # A ship's crew vector survives the same treatment because nothing
            # but us ever grows it. A planet's does not: recruitment appends to
            # it, so the engine and this code end up fighting over who owns the
            # buffer, and the engine always wins because it frees.
            raise ValueError(
                f"{planet}'s military vector has no spare capacity "
                f"({(cap - end) if (cap and end) else '?'} bytes free, need "
                f"{need}); growing an engine-owned vector it also appends to is "
                f"what corrupted one and crashed the client")

        # The ship side only SHRINKS, which needs no allocation at all: walk
        # `end` back and the surviving records stay contiguous from `begin`.
        cb = ship.u32(self.SHIP_CREW)
        self._u32(ship.addr + self.SHIP_CREW + 4,
                  cb + (len(aboard) - count) * self.CITIZEN_STRIDE,
                  f"crew end -> {len(aboard) - count} aboard")
        self.log(f"  garrisoned {count} unit(s) from {ship} onto {planet}")
        return count

    # -- J: sell a facility (engine call) --------------------------------
    def sell_facility(self, planet, type_id, count=1):
        """Sell `count` of a facility type, crediting the civ's treasury.

        A player can sell a building, so an AI should be able to. The use is not
        tidying up: it is a recovery move. A civ that loses the planets funding
        its expensive buildings faces a cascade -- upkeep it can no longer pay
        against income it no longer has -- and liquidating those buildings is
        how a human trades a long-term asset for immediate solvency instead of
        surrendering.

        THIS GOES THROUGH THE ENGINE, and that is not incidental. Selling pays
        cash, and STRATEGY.md 1.1 forbids writing the treasury: a sale that
        credited itself would be arithmetically identical to inventing money,
        and we would have no way to tell a correct payout from a generous one.
        PlanetProperties::SellFacility prices the building, credits Owner:8 and
        removes the units in one call, so the payout is the engine's number.

        Refuses on the online path. The call site at 0x0056EF91 is guarded by
        the offline flag at 0x0086F1A0 -- in multiplayer the server owns the
        transaction, and applying it locally would desync rather than cheat.
        """
        have = planet.facilities.get(type_id, 0)
        if have < count:
            raise ValueError(
                f"{planet} has {have} of facility type {type_id}, cannot sell "
                f"{count}")
        name = gs.FACILITIES.get(type_id, f"type {type_id}")
        if self.dry_run:
            self.log(f"  [WOULD SELL] {count} x {name} on {planet} "
                     f"({have} standing) via the engine's own SellFacility")
            return None
        if self.remote is None:
            raise RuntimeError("sell_facility needs a remote.Remote")
        flag = self.snap.read(remote.OFFLINE_FLAG, 1)
        if not flag or flag[0] == 0:
            raise RuntimeError(
                "the offline flag at 0x0086F1A0 is clear, so this client is on "
                "the networked path; the server owns the sale and applying it "
                "locally would desync")
        ok = self.remote.sell_facility(planet.addr, type_id, count)
        self.log(f"  sold {count} x {name} on {planet} (engine returned {ok})")
        self.writes.append((planet.addr, b"", b"", f"sold {count} x {name}"))
        return ok

    # -- K: hurry the current production (engine call) --------------------
    HURRY_MIN_FRACTION = 0.5

    def hurry_state(self, planet):
        """(cost, done, total, fraction) for this planet's current build.

        Every number comes from the engine, not from us: the cost function at
        0x00555660 and the PlanetProperties total-production virtual at vftable
        slot 0x74. cost is -1 when the production cannot be hurried at all,
        which is what a planet generating wealth (production kind 7) returns.
        """
        if self.remote is None:
            return None
        vft = self.snap.rd32(planet.addr + gs.PLANET_PROPERTIES)
        if not gs.is_ptr(vft):
            return None
        slot = self.snap.rd32(vft + 0x74)
        cost = self.remote.hurry_cost(planet.addr)
        total = self.remote.production_total(planet.addr, slot)
        done = self.remote.production_done(planet.addr)
        frac = (done / total) if total else 0.0
        return cost, done, total, frac

    def hurry_production(self, planet):
        """Spend cash to complete this planet's build now.

        This is the one use for cash we have actually found. Ships and
        facilities accumulate PRODUCTION, and cash sat unspent at 46,000 while
        builds crawled -- hurrying converts the idle balance into finished
        buildings at 4 credits per unit of remaining production.

        Engine call, for the same reason as sell_facility: it debits Owner:8,
        which STRATEGY.md 1.1 forbids us to write. Every precondition below is
        one the engine itself enforces, checked here only to avoid spawning a
        remote thread for a call that would refuse.
        """
        st = self.hurry_state(planet)
        if st is None:
            raise RuntimeError("hurry needs a remote.Remote to price the build")
        cost, done, total, frac = st
        if cost is None or cost < 0:
            raise ValueError(f"{planet} has nothing hurryable "
                             f"(cost {cost}; a planet piling up wealth "
                             f"returns -1)")
        if planet.u32(300):
            raise ValueError(f"{planet} has Planet:300 set, which the engine "
                             f"treats as already hurried")
        if frac < self.HURRY_MIN_FRACTION:
            raise ValueError(
                f"{planet} is {frac:.0%} built ({done}/{total}); the engine "
                f"refuses below {self.HURRY_MIN_FRACTION:.0%} — "
                f"'the production needs to be at least half finished'")
        civ = self.snap.civ_of(self.snap.rd32(planet.u32(40)))             if gs.is_ptr(planet.u32(40)) else None
        cash = civ.cash if civ else None
        if cash is not None and cost > cash:
            raise ValueError(f"{planet} hurry costs {cost}, cash is {cash}")
        if self.dry_run:
            self.log(f"  [WOULD HURRY] {planet} at {frac:.0%} "
                     f"({done}/{total}) for {cost} credits")
            return None
        ok = self.remote.hurry_production(planet.addr)
        # The callee returns AL, so only the low byte is meaningful — the
        # upper 24 bits of EAX are whatever the function last had there.
        ok = None if ok is None else bool(ok & 0xFF)
        self.log(f"  hurried {planet} for {cost} credits (engine says {ok})")
        self.cash_effect -= cost
        self.writes.append((planet.addr, b"", b"", f"hurried for {cost}"))
        return ok

    # -- N: conscript a citizen into the military (engine call) ------------
    # Never a farmer. Food is the one thing that actually kills a colony, and
    # S-01 exists to put citizens back onto it — drafting one straight off the
    # farms would have the two rules fighting each other every turn.
    DRAFT_PREFERENCE = (6, 2, 5, 1)        # banker, scientist, miner, worker

    def draft_cost(self, civ, count=1):
        """The engine's own price for `count` conscripts. Read-only."""
        if self.remote is None:
            return None
        return self.remote.draft_cost(civ.addr, count)

    def conscript(self, planet, civ):
        """Turn ONE citizen into a stationed military unit, at the engine's price.

        The crew bottleneck this exists to break: a ship with no crew cannot
        move and the engine CANCELS its order at the next boundary, so every
        order rule is gated on crew, and recruitment delivers roughly one unit
        per ten turns. Meanwhile cash accumulates with nothing to buy — 6,121
        on a civ whose scouts were grounded, against 115 for a conscript.

        Engine call, for the STRATEGY.md §7 reason and more sharply than usual:
        the price depends on empire-wide military INCLUDING ship crew, and it
        debits Owner:8, which §1.1 forbids us to write. A draft we priced
        ourselves would be indistinguishable from a free one.

        The engine refuses outright when the cost exceeds the treasury — `jg`
        before any list write, so a rejected draft changes nothing at all. The
        checks here only avoid spawning a remote thread for a call that would
        refuse.

        ONE per call, and that is a correctness bound rather than caution: the
        engine ERASES the drafted record and memmoves the tail down, so every
        index above it shifts. A second index taken from the same snapshot would
        name a different citizen than the one it was chosen for. A rule that
        wants two drafts takes them on two turns, against two snapshots.
        """
        if self.remote is None and not self.dry_run:
            raise RuntimeError("conscription needs a remote.Remote")
        # THE INDEX MUST COME FROM A LIVE READ, NOT FROM THE SNAPSHOT. The
        # snapshot is taken once per turn and shared by every civ, so by the
        # time this runs the planet's citizen vector may have moved underneath
        # it — and the engine shifts that vector itself: CommitPopulation erases
        # each drafted record and memmoves the tail, and auto-conscripts surplus
        # population above space/10 while it is in there. An index chosen from a
        # stale list names a citizen who has shifted or gone.
        self._require_sane(planet.addr + self.CITIZEN_LIST,
                           f"{planet}'s citizen vector")
        live = [struct.unpack_from("<I", r, 0)[0]
                for r in planet.vec(self.CITIZEN_LIST, self.CITIZEN_STRIDE)]
        if live != planet.population:
            self.log(f"  [!] {planet}'s citizen list moved since the snapshot "
                     f"({len(planet.population)} -> {len(live)}); using the "
                     f"live one")
        jobs = live
        pick = next((jobs.index(w) for w in self.DRAFT_PREFERENCE if w in jobs),
                    None)
        if pick is None:
            raise ValueError(f"{planet} has no draftable citizen — every one "
                             f"is a farmer, and drafting those starves it")

        cost = self.draft_cost(civ, 1)
        if cost is not None and civ.cash is not None and cost > civ.cash:
            raise ValueError(f"a conscript costs {cost}, cash is {civ.cash}; "
                             f"the engine would refuse")

        if self.dry_run:
            self.log(f"  [WOULD DRAFT] citizen {pick} "
                     f"({gs.JOBS.get(jobs[pick], jobs[pick])}) on {planet} "
                     f"for {cost} credits")
            return None
        if cost is None:
            raise RuntimeError("cannot draft without the engine's price")

        # THE MIGRATION, BY HAND. `ChangeCitizenJobs` did all of this and is
        # PROVEN to kill the client about 30 turns later — a five-arm bisect put
        # it beyond doubt (§7). What it does that our writes do not is run
        # `CommitPopulation` on a REMOTE THREAD, mutating and reallocating the
        # two containers while the main thread may be walking them.
        #
        # So the engine still sets the PRICE — `GetDraftCost` is read-only and
        # runs in every arm that survives — and we perform the transfer with the
        # plain field writes the bisect exonerated. A conscript is, in the
        # engine's own terms, one 16-byte record moving from the citizen vector
        # to the military vector with its job id set to 3.
        recs = planet.vec(self.CITIZEN_LIST, self.CITIZEN_STRIDE)
        if pick >= len(recs):
            raise ValueError(f"citizen {pick} no longer exists on {planet}")
        moved = bytearray(recs[pick])
        struct.pack_into("<I", moved, 0, remote.DRAFT_JOB)

        cbegin = planet.u32(self.CITIZEN_LIST)
        kept = b"".join(recs[:pick] + recs[pick + 1:])
        self._write(cbegin, kept, f"citizen list minus #{pick}")
        self._u32(planet.addr + self.CITIZEN_LIST + 4, cbegin + len(kept),
                  f"citizen end -> {len(recs) - 1}")
        self._append_military(planet, [bytes(moved)])

        # WRITING Owner:8 AT ALL IS A §1.1 EXCEPTION, and a narrow one. The rule
        # forbids it because inventing money is indistinguishable from earning
        # it — which is an argument about CREDITING. This only ever debits, and
        # only by the number the engine itself quoted for this exact draft a few
        # microseconds earlier. The alternative is not "do it honestly", it is
        # "take the citizen for free", which is the actual cheat.
        cash = civ.cash
        self._u32(civ.addr + 8, cash - cost,
                  f"{civ.civ_name} cash {cash} -> {cash - cost} "
                  f"(the engine's own price for this draft)")
        self.cash_effect -= cost
        if self.remote is not None:
            self.remote.invalidate_draft_cost()
        self.log(f"  drafted citizen {pick} on {planet} for {cost} "
                 f"(migrated by field write, not by ChangeCitizenJobs)")
        return pick

    # Never strip a colony to nothing, however hungry it is: a planet that
    # cannot work is worse than a ship that cannot fly.
    MIN_COLONY_POP = 4

    def conscript_to_crew(self, planet, ship, count, civ,
                          allow_farmers=False):
        """Draft citizens straight onto a ship, skipping the planet entirely.

        THE PLANET'S MILITARY VECTOR IS THE BOTTLENECK, AND NOTHING NEEDS IT.
        `conscript` moves a citizen into `Planet:168` and `load_crew` moves it
        out again the moment a ship wants it, so the stationed-military vector is
        a staging area a crew-bound unit never has to visit. It is also the step
        that fails: it is engine-owned, it fills up, and `_append_military`
        rightly refuses to grow it — "military vector is full (0 bytes spare,
        need 16)" is what left a finished bomber grounded for ~100 turns with
        18k cash in the bank and nothing else in its way.

        A citizen, a stationed unit and a crew member are the SAME 16-byte
        record (see `load_crew`), so the move is legal in one hop: rewrite the
        citizen list without the drafted records, and hand those records to a
        fresh crew buffer.

        WHY THIS DOES NOT REOPEN THE AUG 2026 CRASH. That crash was
        `_set_ship_crew` repointing a crew vector the engine already owned, and
        the rule that came out of it is "fill a crew vector only when it is
        EMPTY". This fills an empty one, in a single allocation, with the ship's
        whole complement — the exact path present in every arm of the bisect
        that survived. It refuses outright if the ship already has crew, rather
        than topping up.

        WHAT IT COSTS THE CIV, so this is not a free army: the engine's own
        `GetDraftCost` prices all `count` drafts and the treasury is debited by
        that number, the same narrow §1.1 exception `conscript` documents — a
        debit at a price we did not invent. The player-visible outcome matches
        what a human gets by drafting and then loading; only the staging hop is
        skipped.

        TRADE-OFF, DELIBERATE: crew taken this way never appears in the planet's
        garrison, so this cannot conscript for DEFENCE. That is the recruitment
        slider's job (R-XPL-05), and no rule defends a planet yet anyway.
        """
        if count <= 0:
            raise ValueError("conscript_to_crew needs a positive count")
        if self.remote is None and not self.dry_run:
            raise RuntimeError("conscription needs a remote.Remote")

        # Empty-only, checked before anything is taken from the planet: a
        # half-done draft that then cannot deliver its crew would delete
        # citizens for nothing.
        existing = ship.u32(self.SHIP_CREW)
        if gs.is_ptr(existing):
            raise ValueError(
                f"{ship} already has a crew vector (0x{existing:08X}); this "
                f"path only ever fills an empty one, because repointing a "
                f"vector the engine owns is what crashed the client")

        self._require_sane(planet.addr + self.CITIZEN_LIST,
                           f"{planet}'s citizen vector")
        # Live read, never the snapshot: the engine shifts this vector itself
        # (CommitPopulation erases and memmoves, and auto-conscripts surplus
        # population), so an index chosen from a stale list names someone else.
        recs = planet.vec(self.CITIZEN_LIST, self.CITIZEN_STRIDE)
        jobs = [struct.unpack_from("<I", r, 0)[0] for r in recs]

        picks = []
        for want in self.DRAFT_PREFERENCE:
            for i, job in enumerate(jobs):
                if job == want and i not in picks:
                    picks.append(i)
                    if len(picks) == count:
                        break
            if len(picks) == count:
                break

        # A FARMER IS DRAFTABLE WHEN THE COLONY HAS NO USE FOR THEM, which is a
        # judgement about the planet, not about food alone. R-XPL-09 makes it
        # and passes it down, because only the rule has the population cap and
        # the food history (STRATEGY.md §2.5) in front of it.
        #
        # DO NOT READ `allow_farmers` AS "THE PLANET IS STARVING". The case that
        # actually matters is a planet at its POPULATION CAP running a food
        # SURPLUS: growth has stopped, the surplus has nowhere to go, and the
        # drafted citizen is replaced by growth that was being discarded. A
        # food-negative colony qualifies too, for the opposite reason. Treating
        # the flag as a famine signal would invert the common case.
        if len(picks) < count and allow_farmers:
            spare = len(jobs) - len(picks) - self.MIN_COLONY_POP
            for i, job in enumerate(jobs):
                if spare <= 0 or len(picks) == count:
                    break
                if i not in picks:
                    picks.append(i)
                    spare -= 1

        if len(picks) < count:
            raise ValueError(
                f"{planet} can spare {len(picks)} of the {count} citizen(s) "
                f"{ship} needs — the rest are farmers"
                + (f", and it has only {len(jobs)} citizen(s), at or near the "
                   f"{self.MIN_COLONY_POP} this rule will not take a colony "
                   f"below" if allow_farmers else
                   ", and drafting those starves the colony"))

        cost = self.draft_cost(civ, count)
        if cost is not None and civ.cash is not None and cost > civ.cash:
            raise ValueError(f"{count} conscript(s) cost {cost}, cash is "
                             f"{civ.cash}; the engine would refuse")

        if self.dry_run:
            who = ", ".join(f"{i} ({gs.JOBS.get(jobs[i], jobs[i])})"
                            for i in picks)
            self.log(f"  [WOULD DRAFT TO CREW] {count} citizen(s) on {planet} "
                     f"-> {ship} for {cost} credits: {who}")
            self.crewed_ships.add(ship.id)
            return None
        if cost is None:
            raise RuntimeError("cannot draft without the engine's price")

        # Both vectors are rewritten from one live read, so no index is used
        # after the list beneath it has shifted — the hazard that makes
        # `conscript` strictly one-per-call does not arise here.
        drafted = []
        for i in picks:
            rec = bytearray(recs[i])
            struct.pack_into("<I", rec, 0, remote.DRAFT_JOB)
            drafted.append(bytes(rec))

        keep = [r for i, r in enumerate(recs) if i not in picks]
        cbegin = planet.u32(self.CITIZEN_LIST)
        kept = b"".join(keep)
        self._write(cbegin, kept, f"citizen list minus {count}")
        self._u32(planet.addr + self.CITIZEN_LIST + 4, cbegin + len(kept),
                  f"citizen end -> {len(keep)}")

        self._set_ship_crew(ship, b"".join(drafted), count)

        cash = civ.cash
        self._u32(civ.addr + 8, cash - cost,
                  f"{civ.civ_name} cash {cash} -> {cash - cost} "
                  f"(the engine's price for {count} draft(s))")
        self.cash_effect -= cost
        self.crewed_ships.add(ship.id)
        # The draft price is a function of empire-wide military and we just
        # raised it. A stale price would under-quote the NEXT draft this pass.
        if self.remote is not None:
            self.remote.invalidate_draft_cost()
        self.log(f"  drafted {count} citizen(s) on {planet} directly onto "
                 f"{ship} for {cost} — the planet's military vector was never "
                 f"involved")
        return picks

    # -- L: set a ship's command, target node included (engine call) -------
    # Order types that consult Ship:56. 0x004D9880 Ship::HasActiveTargetedOrder
    # requires the type to be one of these AND Ship:56 to be non-null, so an
    # order of these types written without a target node is very likely inert.
    TARGETED_ORDER_TYPES = (1, 4, 7)      # Move, Attack, MoveNear

    def set_command(self, ship, order_type, target, civ=None):
        """Give a ship an order that names a target OBJECT, not just coordinates.

        *** DO NOT USE. THIS KILLS THE CLIENT. ***

        Measured 2026-08-06 from `client/armed.dat`, one turn boundary per trial:
        `set_command(4)` and `set_command(1)` BOTH crashed the client while the
        engine resolved the turn, while `create_order` + `retarget_order(1)`
        survived and moved the ship exactly `ShipDesign:36` units. The order is
        written cleanly and the pass completes; the death is at resolution.

        The premise below is also wrong on its own terms: a route-only order
        leaves `Ship:56` as the static null node and the ship navigates fine, so
        `Ship::HasActiveTargetedOrder` is not what makes a ship move. Nothing in
        the AI needs `Ship:56` today — combat triggers on co-location and the
        battle driver never reads the order type.

        Kept, unused, because the ShipCommand/SetCommand call pair is still the
        only known way to populate `Ship:56` and the crash is not understood yet.
        Use `exterminate.send_to` instead. See STRATEGY.md §4.4.

        `retarget_order` writes Ship:52 and the order's coordinate triple, which
        is right for Colonize (3) and Scout (2) — neither consults Ship:56, and
        both were confirmed to navigate correctly. It is NOT right for Move (1),
        Attack (4) or MoveNear (7): those are exactly the types
        Ship::HasActiveTargetedOrder tests, and it also requires Ship:56 to be
        non-null. Our writer leaves the static null node there.

        So this goes through the engine instead. ShipCommand's constructor turns
        a target object into the reference node via GetReferenceNode(objectId),
        and Ship::SetCommand writes Ship:52/56/60/61 as the single value they
        are. Nothing else we have can populate Ship:56 — a hand-made node is not
        registered anywhere the engine knows about, which is the same reason
        set_design_owner reuses a node rather than fabricating one.

        The 16-byte ShipCommand comes from the engine's own operator new so the
        engine may legally free it. It is a plain value type and SetCommand
        copies out of it, so nothing retains a pointer; it simply leaks, which is
        the same trade create_order already accepts.
        """
        if order_type not in gs.ORDER_TYPES:
            raise ValueError(f"order type {order_type} is not one the UI issues")
        if target is None:
            raise ValueError("a targeted order needs a target object")
        if not self.is_crewed(ship):
            raise ValueError(
                f"{ship} has no crew — the engine cancels a crewless ship's "
                f"orders at the next turn boundary, so this would leak an "
                f"allocation every turn and never move")
        if self.dry_run:
            self.log(f"  [WOULD COMMAND] {ship} -> type {order_type} "
                     f"({gs.ORDER_TYPES[order_type]}) targeting {target}, "
                     f"via ShipCommand + Ship::SetCommand so Ship:56 is set")
            return None
        if self.remote is None:
            raise RuntimeError("set_command needs a remote.Remote")

        # ORDER OBJECT AND ROUTE FIRST. SetCommand only writes four fields; it
        # does NOT build a route. The engine's own callers run Ship::SetOrder
        # before it (0x004CD090 and 0x004D0FB3 both do), and skipping that step
        # CRASHED THE CLIENT: a ship under a scout order was given
        # SetCommand(Move, planet), so Ship:52 and Ship:56 described a move to a
        # planet while the order object still held the route to a SUN. The write
        # itself looked perfect — order type 1, a real target node — and the
        # client died at the next turn boundary with EIP 0.
        #
        # retarget_order already builds a correct single-leg route and is
        # confirmed end to end (a retargeted colonize order moved exactly
        # ShipDesign:36 units along the new bearing). So establish a consistent
        # order first, then let SetCommand add the target node the coordinate
        # writer cannot produce.
        new_ptr = None
        if not gs.is_ptr(ship.order_ptr):
            if civ is None:
                raise ValueError(
                    f"{ship} has no order object and no civ was passed, so "
                    f"one cannot be originated; pass civ= to set_command")
            new_ptr = self.create_order(ship, target, civ)
            if new_ptr is None:
                raise RuntimeError(f"could not originate an order for {ship}")
        # Route and coordinates only. The TYPE is deliberately not written
        # here — SetCommand writes Ship:52 and Ship:56 as the single value
        # they are, and any window where the type says Attack while the
        # target node is null is a window the engine can crash in.
        self.retarget_order(ship, order_type, target.pos, order_ptr=new_ptr,
                            set_type=False)

        block = self.remote.operator_new(remote.SHIPCOMMAND_SIZE)
        if not gs.is_ptr(block):
            raise RuntimeError("the engine's operator new refused the "
                               "ShipCommand block")
        # Allocation start, not tag: the constructor reads the object id at
        # target[+4], which is tag-4 for Planet, Ship and Sun alike.
        self.remote.make_ship_command(block, order_type, target.addr - 8)
        node = self.snap.rd32(block + 4)
        if not gs.is_ptr(node):
            raise RuntimeError(f"ShipCommand built at 0x{block:08X} has no "
                               f"target node; refusing to install it")
        if node == gs.NULL_NODE:
            raise RuntimeError(
                f"ShipCommand resolved to the STATIC NULL NODE, so the target "
                f"id did not resolve — installing it would give a targeted "
                f"order the engine treats as untargeted")
        self.remote.ship_set_command(ship.addr, block)
        got_type = self.snap.rd32(ship.addr + SHIP_ORDER_TYPE)
        got_node = self.snap.rd32(ship.addr + 56)
        self.log(f"  {ship} order type {got_type}, Ship:56 -> 0x{got_node:08X} "
                 f"(target {target})")
        self.writes.append((ship.addr, b"", b"",
                            f"SetCommand type {order_type} at {target}"))
        return got_type == order_type and got_node == node

    # -- M: declare war (engine-assisted, symmetric) ----------------------
    # The relation record is SYMMETRIC: the engine's own applier writes the same
    # code into BOTH civs' Owner:248 in one call. Writing only our own side
    # leaves an inconsistent galaxy, and since the battle phases ask *a* civ's
    # map, which side gets asked would decide whether combat happens at all.
    WAR, NEUTRAL = 1, 0
    RELATION_NAMES = {0: "Neutral", 1: "War", 2: "Cease-Fire", 3: "Peace",
                      4: "Alliance"}

    # Reputation for declaring war. The UI quantifies a figure before confirming
    # and we have NOT recovered its formula, so this is a placeholder that must
    # be set deliberately rather than left to default silently. Applying nothing
    # is not neutral: it is a free war, which in an AI-vs-AI galaxy would be an
    # advantage shared by whoever uses this code and denied to anyone who does
    # not. Tracked as a [ ] in STRATEGY.md.
    WAR_REPUTATION_DELTA = None

    def declare_war(self, civ, other, reputation_delta=None):
        """Move two civs to War, both directions, and apply the standing cost.

        Uses the engine for the parts that have invariants — creating the
        relation record (`first_contact`, which no-ops when one exists) and
        adjusting the clamped standing score — and writes only the relation code
        itself, which is a plain int inside a record the engine just built.

        NEWS IS NOT GENERATED. The UI path emits a "Declares War" item and this
        does not. For an AI-vs-AI galaxy that is acceptable — no human is reading
        the news tab — but it means the victim gets no notification, so AIs have
        to learn about wars by reading relations rather than by being told.
        """
        if other.addr == civ.addr:
            raise ValueError("a civ cannot declare war on itself")
        if self.dry_run:
            self.log(f"  [WOULD DECLARE WAR] {civ.civ_name!r} -> "
                     f"{other.civ_name!r}, both directions")
            return None
        if self.remote is None:
            raise RuntimeError("declare_war needs a remote.Remote")

        for a, b in ((civ, other), (other, civ)):
            self.remote.first_contact(a.addr, b.addr)
        wrote = []
        for a, b in ((civ, other), (other, civ)):
            rec = self.remote.relation_record(a.addr, b.addr)
            if rec is None:
                raise RuntimeError(
                    f"no relation record for {a.civ_name!r} -> {b.civ_name!r} "
                    f"even after first contact; refusing to half-declare")
            before = self.snap.rd32(rec + remote.RELATION_CODE_OFF)
            self._u32(rec + remote.RELATION_CODE_OFF, self.WAR,
                      f"{a.civ_name!r} -> {b.civ_name!r}: "
                      f"{self.RELATION_NAMES.get(before, before)} -> War")
            wrote.append(rec)

        delta = (self.WAR_REPUTATION_DELTA if reputation_delta is None
                 else reputation_delta)
        if delta:
            for a, b in ((civ, other), (other, civ)):
                self.remote.add_reputation(a.addr, b.addr, delta)
            self.log(f"  applied {delta:+d} reputation both ways")
        else:
            self.log("  [!] NO reputation change applied — the UI's figure is "
                     "not known, so declaring war is currently FREE. Fine while "
                     "every civ runs this code; not fine against a human.")
        return all(self.snap.rd32(r + remote.RELATION_CODE_OFF) == self.WAR
                   for r in wrote)

    # -- G: set a planet's recruitment rate (Planet:100 byte 3) ---------
    # The maximum is PER PLANET and depends on military camps. Confirmed in the
    # UI: a planet with ONE camp offers up to 60%, with the remaining 40% going
    # to citizen growth. The report's "0-40" was the no-camp base, and each camp
    # adds 20 (the game's own string says so). We had been capping every planet
    # at 40, so a planet with a camp was recruiting at two thirds of the rate a
    # player would have used.
    #
    # The engine applies whatever byte is written with NO clamp of its own -- the
    # getter is `movsx eax, byte ptr [ecx+0xf]` and nothing else -- so respecting
    # the maximum is entirely our responsibility, and it is a 1.1 obligation
    # rather than a safety one. The movsx also means the byte must stay under
    # 128, or the rate reads NEGATIVE and the military store would go DOWN.
    RECRUITMENT_BASE_MAX = 40     # no military camp
    RECRUITMENT_PER_CAMP = 20     # each camp raises the ceiling by this
    RECRUITMENT_HARD_MAX = 100    # and never above the UI's own 0..100 range

    def max_recruitment(self, planet):
        camps = planet.facilities.get(6, 0)
        return min(self.RECRUITMENT_HARD_MAX,
                   self.RECRUITMENT_BASE_MAX + self.RECRUITMENT_PER_CAMP * camps)

    def set_recruitment(self, planet, percent):
        """Set the military recruitment rate, a percentage in byte 3 of
        Planet:100.

        Only that one byte moves. Bytes 0-2 read 100/100/100 on every planet
        including uncolonised ones and are the leading loyalty candidate, so
        writing the dword wholesale would clobber three fields to change one.

        The UI was observed offering 0 to 40, so this refuses anything outside
        that: a value the interface could not produce is exactly what the
        no-cheating rule forbids. A military base is suspected to raise the cap
        — a planet with one read 60 — but that is unproven, so the ceiling stays
        at what has actually been seen.
        """
        ceiling = self.max_recruitment(planet)
        if not 0 <= percent <= ceiling:
            raise ValueError(
                f"{percent}% is outside the 0..{ceiling} range the "
                f"UI was seen to offer; a higher cap with a military base is "
                f"suspected but unproven")
        raw = self.snap.read(planet.addr + 100, 4)
        if not raw or len(raw) != 4:
            raise ValueError(f"cannot read Planet:100 on {planet}")
        cur = raw[3]
        if cur == percent:
            return True
        new = bytes(raw[:3]) + bytes([percent])
        self.log(f"  {planet} recruitment {cur}% -> {percent}%")
        return self._write(planet.addr + 100, new,
                           f"Planet:100 byte 3 recruitment -> {percent}%")

    # -- F: set the research topic (Owner:144 and :152 together) --------
    def set_research(self, civ, topic_id):
        ok = self._u32(civ.addr + 144, topic_id, f"{civ.civ_name} topic -> {topic_id}")
        return self._u32(civ.addr + 152, topic_id,
                         f"{civ.civ_name} topic mirror -> {topic_id}") and ok

    # -- E: order a ship ------------------------------------------------
    def retarget_order(self, ship, order_type, target_xyz, order_ptr=None,
                       set_type=True):
        """Point an EXISTING order at a new destination.

        Writes, in order: the target triple, the route leg (origin = the ship's
        current position, dest = the target, length = the distance between them),
        the order type, and the local-civ pending-orders flag. Every one is a
        plain float or int already inside allocated memory.
        """
        if order_type not in gs.ORDERS:
            raise ValueError(f"order type {order_type} is not one the UI issues")
        # order_ptr lets a caller aim an order it just created, without having
        # to re-scan the process for the ship's updated Ship:48.
        ord_ptr = order_ptr if order_ptr is not None else ship.order_ptr
        if not gs.is_ptr(ord_ptr):
            raise NeedsEngineOrder(ship)

        name = gs.ORDERS[order_type]
        self.log(f"  order {ship} -> {name} at "
                 f"({target_xyz[0]:.2f}, {target_xyz[1]:.2f}, {target_xyz[2]:.2f})")

        origin = ship.pos
        length = gs.dist(origin, target_xyz)

        self._xyz(ord_ptr + ORD_TARGET, target_xyz, "order target XYZ")
        self._xyz(ord_ptr + ORD_ORIGIN, origin, "order origin XYZ")
        self._xyz(ord_ptr + ORD_ORIGIN2, origin, "order origin XYZ (copy)")

        # Route leg. Rewritten in place only — never grown, so nothing allocates.
        b, e = self.snap.rd32(ord_ptr + ORD_ROUTE), self.snap.rd32(ord_ptr + ORD_ROUTE + 4)
        if gs.is_ptr(b) and e and e - b >= ROUTE_STRIDE:
            if e - b != ROUTE_STRIDE:
                self.log(f"  [note] route holds {(e-b)//ROUTE_STRIDE} legs; "
                         f"rewriting the first and truncating to one")
                self._u32(ord_ptr + ORD_ROUTE + 4, b + ROUTE_STRIDE, "route end -> 1 leg")
                self._u32(ord_ptr + ORD_ROUTE + 8, b + ROUTE_STRIDE, "route cap -> 1 leg")
            self._write(b, struct.pack("<fffffff", *origin, *target_xyz, length),
                        f"route leg, length {length:.4f} "
                        f"(ETA {self.eta(ship, length)} turns)")
        else:
            self.log(f"  [warn] no usable route vector at +{ORD_ROUTE}; "
                     f"the engine may recompute it, or the order may not move")

        # Progress MUST be reset or the ship resumes from however far the order
        # had already travelled. Transferring a half-flown order without this
        # made a ship cover 27.0 units in one turn at speed 13.5 — its 13.5 of
        # inherited progress plus one turn — and it looked like a speed bug.
        self._f32(ord_ptr + ORD_PROGRESS, 0.0, "route progress -> 0")

        # set_type=False leaves Ship:52 ALONE, for callers that will set the
        # order type and its target node together through Ship::SetCommand.
        # Writing the type here first is what crashed the client on the very
        # first attack order: the ship briefly held order type 4 with Ship:56
        # still the static null node, which is exactly the state
        # Ship::HasActiveTargetedOrder refuses to accept for types 1, 4 and 7.
        if set_type:
            self._u32(ship.addr + SHIP_ORDER_TYPE, order_type,
                      f"order type -> {name}")
        self._u32(ship.addr + SHIP_HAS_ORDERS, 1, "pending-orders flag")
        return True

    def create_order(self, ship, target, civ):
        """Originate an order object for a ship that has none. CONFIRMED.

        Route 2 of STRATEGY.md §6a, and the answer to the question that blocked
        both the AI player and multiplayer: the engine will not build an order
        lazily and one cannot be fabricated, so instead we call the engine's own
        allocator and its own constructor and let it build the object properly.

            order = operator new(0x60)                       0x0065165A
            order_ctor(ECX=order, ship, target, owner, f, 1, 0)   0x004E8420

        `f` is whatever the ship's own vftable[0x30] getter returns, so the
        value matches what the engine would have passed itself.

        POINTER FORMS MATTER: origin and target go in as ALLOCATION STARTS
        (EJBO tag - 8) and the Owner as its tag-52 primary form. Passing the
        tag-8 owner that the reference-node idiom stores is the easiest way to
        crash the client here.

        Returns the new order's address, already written into Ship:48.
        """
        if gs.is_ptr(ship.order_ptr):
            raise ValueError(f"{ship} already has an order object at "
                             f"0x{ship.order_ptr:08X}")
        if self.dry_run:
            self.log(f"  [WOULD CREATE] engine-built order for {ship} "
                     f"targeting {target} (operator new + ctor via remote)")
            return None
        if self.remote is None:
            raise NeedsEngineOrder(ship)

        order = self.remote.operator_new(remote.ORDER_SIZE)
        f_bits = self.remote.ship_speed_float(ship.addr - 8)
        self.remote.construct_order(order,
                                    origin_alloc=ship.addr - 8,
                                    target_alloc=target.addr - 8,
                                    owner_alloc=civ.addr - 52,
                                    f_bits=f_bits)
        self.log(f"  created engine-built order 0x{order:08X} for {ship}")
        self._u32(ship.addr + SHIP_ORDER_PTR, order, "Ship:48 -> new order")
        return order

    def transfer_order(self, donor, recipient):
        """Move an order object from one ship to another.

        PROVEN end to end: a colony ship handed another ship's order object was
        retargeted, navigated to a different planet and founded the colony.
        Nothing is allocated and nothing is freed — the object is simply
        referenced by a different ship, and the donor is detached FIRST so the
        two never hold it at once. `Ship:40` (owner) and `Ship:48` (order) are
        independent, so an order is not bound to the ship that created it.

        This does NOT originate an order. It relocates the one you have, and a
        completed order goes away with it, so the supply only ever shrinks.
        """
        ord_ptr = donor.order_ptr
        if not gs.is_ptr(ord_ptr):
            raise NeedsEngineOrder(donor)
        if gs.is_ptr(recipient.order_ptr):
            raise ValueError(f"{recipient} already holds an order object; "
                             f"transferring onto it would leak that one")
        self.log(f"  transfer order 0x{ord_ptr:08X}: {donor} -> {recipient}")
        # Detach first: the object must never be referenced twice, or the engine
        # frees it once per referencing ship.
        self._u32(donor.addr + SHIP_ORDER_TYPE, 0, "donor order type -> none")
        self._u32(donor.addr + SHIP_HAS_ORDERS, 0, "donor pending flag -> 0")
        self._u32(donor.addr + SHIP_ORDER_PTR, 0, "donor releases the order")
        self._u32(recipient.addr + SHIP_ORDER_PTR, ord_ptr, "recipient takes it")
        return True

    @staticmethod
    def eta(ship, length):
        """ETA is not stored — the engine derives it as ceil(distance / speed)."""
        d = ship.design
        if not d or not d.speed or d.speed <= 0:
            return None
        import math
        return math.ceil(length / d.speed)

    def summary(self):
        verb = "would perform" if self.dry_run else "performed"
        return f"{verb} {len(self.writes)} write(s)"


class NeedsEngineOrder(Exception):
    """Raised when a ship has no order object and none can be made.

    Since Route 2 this is no longer a dead end: create_order() builds one by
    calling the engine's own allocator and constructor. It is raised only when
    the Actuator has no remote.Remote to do that with, or in a dry run where
    nothing may be allocated."""

    def __init__(self, ship):
        super().__init__(
            f"{ship} has no order object (Ship:48 is null) and this Actuator "
            f"has no remote.Remote to create one with. Construct the Actuator "
            f"with remote_=remote.Remote(pid).")
        self.ship = ship
