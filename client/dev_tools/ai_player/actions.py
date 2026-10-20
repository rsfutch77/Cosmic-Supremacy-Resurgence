"""actions.py: actuators, the button clicks the AI is allowed to make."""
import struct

import gamestate as gs
import remote

# Order-object field offsets.
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
        # Elevated access is needed only to construct engine objects, so it is
        # acquired lazily.
        self._remote = remote_
        self._remote_factory = remote_factory
        # Every rule in a pass reads the same snapshot, so the sets below record
        # what earlier rules in this pass already changed.
        # Ships committed to an order this pass.
        self.claimed_ships = set()
        # Ships crewed this pass (the snapshot still shows Ship:124 empty).
        self.crewed_ships = set()
        # Planets whose citizens a rule has rewritten this pass, so S-food and
        # R-XPL-01 do not undo each other.
        self.claimed_planets = set()
        # Planets whose production was set this pass. Planet:296 and Planet:344
        # hold one build, so a second write would replace the first.
        self.claimed_production = set()
        # Signed effect of this pass's actions on the treasury (negative when
        # spending), so the income sensor can exclude it.
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

        Rules filter on this when choosing where to build. The raise in
        _claim_production is a backstop and aborts the whole rule.
        """
        return planet.id in self.claimed_production

    def _claim_production(self, planet, what):
        if planet.id in self.claimed_production:
            raise ValueError(
                f"{planet} already had its production set this pass; "
                f"{what} would silently replace it. Production is one slot.")
        self.claimed_production.add(planet.id)

    def keep_production(self, planet):
        """Claim a planet's production slot without changing it."""
        self._claim_production(planet, "keeping its current production")

    def claim_planet(self, planet):
        self.claimed_planets.add(planet.id)

    def planet_claimed(self, planet):
        return planet.id in self.claimed_planets

    def is_crewed(self, ship):
        """Crewed per the snapshot, or crewed by a rule earlier this pass."""
        return ship.crewed or ship.id in self.crewed_ships

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

    def _live_cash(self, civ):
        """Owner:8 (cash) as it stands in the process right now.

        `civ.cash` is fixed at snapshot time, so every cash write must be
        computed from this read.
        """
        b = self.snap.read(civ.addr + 8, 4)
        if b is None or len(b) != 4:
            return civ.cash
        return struct.unpack("<i", b)[0]

    def _live_vec(self, obj, off, stride):
        """A vector's records, with both its extents and its data read live.

        `Obj.vec` takes begin/end from the snapshot and only the payload live,
        so a second write to one vector in the same pass must read through
        here.
        """
        base = obj.addr + off
        begin, end = self.snap.rd32(base), self.snap.rd32(base + 4)
        if not gs.is_ptr(begin) or end is None or end <= begin:
            return []
        span = end - begin
        if span % stride or span > 1 << 20:
            return []
        data = self.snap.read(begin, span)
        return [] if data is None else [data[i:i + stride]
                                        for i in range(0, span, stride)]

    def live_military(self, planet):
        """Stationed units on `planet` now, including any drafted this pass."""
        return len(self._live_vec(planet, self.PLANET_MILITARY,
                                  self.CITIZEN_STRIDE))

    def _live_ptr(self, obj, off):
        """One of a vector's three pointers as it stands now in the process."""
        return self.snap.rd32(obj.addr + off)

    def _xyz(self, addr, xyz, what):
        return self._write(addr, struct.pack("<fff", *xyz), what)

    # retarget_order writes an order across several disjoint regions. A
    # half-applied retarget, with the route naming one destination and
    # Ship:52/56 another, crashes the client, so these helpers let a caller
    # undo it.
    def _order_bytes(self, ship, ord_ptr):
        """Read back every region retarget_order is about to overwrite."""
        if not gs.is_ptr(ord_ptr):
            return []
        spans = [(ord_ptr + ORD_TARGET, 12), (ord_ptr + ORD_ORIGIN, 12),
                 (ord_ptr + ORD_ORIGIN2, 12), (ord_ptr + ORD_PROGRESS, 4),
                 (ord_ptr + ORD_ROUTE + 4, 8),   # route end and cap
                 (ship.addr + SHIP_ORDER_TYPE, 4),
                 (ship.addr + SHIP_HAS_ORDERS, 4)]
        b = self.snap.rd32(ord_ptr + ORD_ROUTE)
        if gs.is_ptr(b):
            spans.append((b, ROUTE_STRIDE))
        out = []
        for addr, size in spans:
            data = self.snap.read(addr, size)
            # A region that cannot be read cannot be restored.
            if data is None or len(data) != size:
                self.log(f"  [warn] could not snapshot 0x{addr:08X}+{size} for "
                         f"rollback; a failure here will leave a torn order")
                continue
            out.append((addr, data))
        return out

    def _restore_order_bytes(self, saved, ship):
        """Put an order back the way it was after a failed set_command."""
        if not saved:
            return
        if self.dry_run:
            return
        self.log(f"  [rollback] restoring {ship}'s previous order; a torn "
                 f"order (route to one target, type naming another) is what "
                 f"crashes the client at the next turn boundary")
        for addr, data in saved:
            if not gs.ev.write_bytes(self.snap.h, addr, data):
                # The process is usually already gone at this point.
                self.log(f"  [rollback] FAILED at 0x{addr:08X}; {ship} may be "
                         f"left in an inconsistent state")

    def _require_same_turn(self, ship):
        """Abort if the turn rolled over since the snapshot was taken."""
        now = self.snap.rd32(gs.TURN_COUNTER)
        if now != self.snap.turn:
            raise StaleTurn(
                f"turn moved {self.snap.turn} -> {now} while ordering {ship}; "
                f"every coordinate in this order came from the old turn's "
                f"snapshot and the ship has since moved")

    # A: set a citizen's job (Planet:144 element +0).
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

    # D: queue a ship (Planet:296 + Planet:344).
    def queue_ship(self, planet, design):
        """Start building a ship on a planet.

        No production object is allocated. `Planet:296` points at the shared
        `ShipDesign` in its allocation-start form (tag - 12, the
        multiple-inheritance primary vftable), and Planet:344 is set to 1.
        """
        if design.type != "ShipDesign":
            raise ValueError(f"{design} is not a ShipDesign")
        # Not gated on the derived stat block, a lazy cache set to -1 whenever
        # parts change. A design is unbuildable only when it lacks parts.
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

    # C: start a facility build (three plain writes).
    PLANET_EMBEDDED_FACILITY = 280   # Facility{vftable, typeId} lives here

    def build_facility(self, planet, type_id):
        """Start building a facility. Nothing is allocated.

        Every planet carries a permanent `Facility{vftable, typeId}` embedded
        at Planet:280, written by the PlanetProperties constructor. Selecting a
        building writes its type id to Planet:284; starting the build points
        Planet:296 at the embedded object.

        This skips the engine's CanBuildFacility check (0x004F5030), so the
        caller must only ask for a type the civ can build. Owner:172 reads
        empty even on civs with facilities and is not a reliable gate.
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

    # I': fit parts to a design.
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
        """Populate an empty part vector on a design.

        A default-constructed design has a chassis and a scanner but no engine,
        so its speed and cost stay at -1 and it cannot be built.

        The buffer comes from the engine's own `operator new`, so the engine's
        `operator delete` can free it. Only a null vector is filled; replacing
        an engine-owned buffer would mean freeing memory this code did not
        allocate.
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

    # I'': give a design an owner.
    DESIGN_OWNER = 260

    def set_design_owner(self, design, civ):
        """Point ShipDesign:260 at a civ's owner reference node.

        A default-constructed design carries the static null node 0x00857C54;
        UI-made designs carry a node resolving to the civ's Owner. The node is
        reused from another design of the same civ, because a hand-made node is
        not registered with the engine.
        """
        def owner_of(d):
            node = d.u32(self.DESIGN_OWNER)
            if not gs.is_ptr(node) or node == gs.NULL_NODE:
                return None
            return self.snap.civ_of(self.snap.rd32(node))

        # Compared by address: Snapshot.refresh() rebuilds every wrapper.
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

    # I: create a ship design.
    def create_design(self, name, civ=None):
        """Default-construct a new ShipDesign and register it with `civ`.

        Created, owned and registered live; never yet queued or built from.

        The engine allocates 280 bytes and its no-argument constructor at
        0x00546EC0 initialises them, reaching the EJBO-tagging base class. The
        result has empty part vectors and needs fit_parts before it is
        buildable.

        The one-argument constructor at 0x005470A0 is
        ShipDesign::ShipDesign(Stream*), a deserializer for the save format's
        DSGN section; it is not a copy constructor.

        Every construction is verified before any byte is written into it.
        """
        if len(name) > 15:
            raise ValueError(f"{name!r} is longer than the 15-char SSO buffer; "
                             f"a longer name would move the string off-heap and "
                             f"turn ShipDesign:8 into a pointer")
        # The UI requires a unique design name.
        clash = [d for d in self.snap.designs if d.design_name == name]
        if clash:
            raise ValueError(
                f"a design named {name!r} already exists (#{clash[0].id}); the "
                f"UI enforces unique names, so pick another")
        if self.dry_run:
            self.log(f"  [WOULD CREATE] default-constructed ShipDesign {name!r} "
                     f"(no parts; see create_design docstring)")
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

        # The default constructor leaves ShipDesign:260 as the static null
        # node, so an owner is attached here.
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
                    # Not fatal: the design exists and can be inspected unowned.
                    self.log(f"  [warn] could not set the owner: {e}")
                else:
                    self.register_design(fresh, civ)
        return block

    def civ_design_ids(self, civ):
        """Ids in a civ's design list, Owner:84, a vector of reference nodes.

        Each node is the value slot of a node in the reference map, so the id
        is the key 4 bytes before it. Read live: registering reallocates the
        vector, so the snapshot's copy of Owner:84 goes stale.
        """
        rd = self.snap.rd32
        begin, end = rd(civ.addr + 84), rd(civ.addr + 88)
        nodes = [rd(begin + 4 * i) for i in range((end - begin) // 4)]
        return [rd(n - 4) for n in nodes]

    # I''': register a design with its civ.
    def register_design(self, design, civ):
        """Put a design in its civ's design list.

        Constructing a design enters it in the global object map only. A civ's
        designs are the ones its Owner:84 vector holds a reference node to;
        a design missing from it has no reference node at all.
        """
        if design.id in self.civ_design_ids(civ):
            self.log(f"  {design} is already in {civ.civ_name!r}'s design list")
            return True
        if self.dry_run:
            self.log(f"  [WOULD REGISTER] {design} with {civ.civ_name!r}")
            return True
        node = self.remote.register_design(design.id, civ.addr)
        ok = design.id in self.civ_design_ids(civ)
        self.log(f"  registered {design} with {civ.civ_name!r}, node "
                 f"0x{node:08X}" if ok else
                 f"  [warn] {design} is not in {civ.civ_name!r}'s design list "
                 f"after registering it")
        return ok

    def set_wealth_mode(self, planet):
        """Stop building and generate wealth instead.

        Every idle planet points Planet:296 at one static `PilingUpWealth`
        singleton.
        """
        self._claim_production(planet, "generating wealth")
        self.log(f"  {planet} -> generating wealth")
        ok = self._u32(planet.addr + 296, gs.PILING_UP_WEALTH,
                       "Planet:296 -> PilingUpWealth singleton")
        return self._u32(planet.addr + 344, 0, "Planet:344 build-active -> 0") and ok

    # J: move a trained unit from a planet onto a ship as crew.
    SHIP_CREW      = 124      # begin/end/cap at 124/128/132
    PLANET_MILITARY = 168     # begin/end/cap at 168/172/176
    CITIZEN_LIST   = 144      # begin/end/cap at 144/148/152
    CITIZEN_STRIDE = 16

    def load_crew(self, planet, ship, count=2):
        """Move `count` stationed military units into a ship's crew vector.

        Military units, crew members and citizens share one 16-byte record:
        [jobId, flags, owner-node, turnAdded]. The ship's crew buffer comes
        from the engine's own operator new; the planet's military vector
        shrinks by walking `end` back, which frees nothing. The crew vector at
        Ship:124/128/132 is the only crew state; there is no separate count.
        """
        self._require_sane(planet.addr + self.PLANET_MILITARY,
                           f"{planet}'s military vector")
        self._require_sane(ship.addr + self.SHIP_CREW, f"{ship}'s crew vector")
        mil = self._live_vec(planet, self.PLANET_MILITARY, self.CITIZEN_STRIDE)
        if len(mil) < count:
            raise ValueError(f"{planet} has {len(mil)} trained unit(s), "
                             f"need {count}")
        aboard = self._live_vec(ship, self.SHIP_CREW, self.CITIZEN_STRIDE)
        if self.dry_run:
            self.log(f"  [WOULD LOAD] {count} unit(s) from {planet} onto {ship} "
                     f"({len(aboard)} aboard, {len(mil)} trained, "
                     f"{len(mil) - count} would remain)")
            self.crewed_ships.add(ship.id)
            return None
        if self.remote is None:
            raise RuntimeError("load_crew needs a remote.Remote to allocate")

        # Take from the end so the surviving records stay contiguous from begin.
        taken = mil[-count:]
        buf = self._add_ship_crew(ship, taken)

        mb = self._live_ptr(planet, self.PLANET_MILITARY)
        new_end = mb + (len(mil) - count) * self.CITIZEN_STRIDE
        self._u32(planet.addr + self.PLANET_MILITARY + 4, new_end,
                  f"planet military end -> {len(mil) - count} unit(s)")
        self.crewed_ships.add(ship.id)
        self.log(f"  moved {count} unit(s) from {planet} to {ship} "
                 f"({len(aboard)} -> {len(aboard) + count} aboard)")
        return buf

    # A std::vector is three pointers into one allocation.
    VEC_SANE_SPAN = 64 * 16          # 64 records is far past anything real

    def vector_is_sane(self, base, what=""):
        """begin/end/cap consistent, and pointing into the same allocation?

        Returns (ok, reason). Callers refuse to write to a vector that fails.
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
        # begin and end in different heap regions means a corrupt vector.
        if (begin >> 24) != (end >> 24):
            return False, (f"begin 0x{begin:08X} and end 0x{end:08X} are in "
                           f"different heaps; this vector is already corrupt")
        return True, f"{span // self.CITIZEN_STRIDE} record(s)"

    def _require_sane(self, base, what):
        ok, why = self.vector_is_sane(base, what)
        if not ok:
            raise ValueError(f"refusing to touch {what}: {why}")
        return True

    def _set_ship_crew(self, ship, records, n, allow_replace=False):
        """Point a ship's crew vector at a fresh engine buffer holding `records`.

        Repointing a crew vector the engine already owns crashes the client, so
        a crew vector is only filled when empty. `allow_replace` is for a caller
        that has proven it owns the buffer; nothing sets it.
        """
        old = self._live_ptr(ship, self.SHIP_CREW)
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

    def _add_ship_crew(self, ship, records):
        """Add 16-byte crew records to a ship, never repointing a live buffer.

        An empty crew vector gets a fresh engine buffer. An allocated one is
        written only into its spare capacity, which is how a troop hull that
        has landed its troops is refilled. Returns the buffer's begin.
        """
        begin = self._live_ptr(ship, self.SHIP_CREW)
        if not gs.is_ptr(begin):
            return self._set_ship_crew(ship, b"".join(records), len(records))
        end = self._live_ptr(ship, self.SHIP_CREW + 4)
        cap = self._live_ptr(ship, self.SHIP_CREW + 8)
        blob = b"".join(records)
        if end is None or cap is None or cap - end < len(blob):
            raise ValueError(
                f"{ship}'s crew buffer has "
                f"{((cap or 0) - (end or 0)) // self.CITIZEN_STRIDE} spare "
                f"record(s), need {len(records)}; repointing one the engine "
                f"owns is what crashed the client")
        aboard = (end - begin) // self.CITIZEN_STRIDE
        self._write(end, blob, f"{len(records)} crew record(s) into spare capacity")
        self._u32(ship.addr + self.SHIP_CREW + 4, end + len(blob),
                  f"crew end -> {aboard + len(records)} aboard")
        return begin

    def _append_military(self, planet, records):
        """Add 16-byte units to a planet's stationed-military vector.

        Uses spare capacity when the buffer has it. An empty vector gets a
        fresh buffer from the engine's own `operator new`. A full engine-owned
        vector is refused.
        """
        base = planet.addr + self.PLANET_MILITARY
        self._require_sane(base, f"{planet}'s military vector")
        mil = self._live_vec(planet, self.PLANET_MILITARY, self.CITIZEN_STRIDE)
        begin = self._live_ptr(planet, self.PLANET_MILITARY)
        end   = self._live_ptr(planet, self.PLANET_MILITARY + 4)
        cap   = self._live_ptr(planet, self.PLANET_MILITARY + 8)
        blob  = b"".join(records)
        need  = len(blob)

        if gs.is_ptr(begin) and cap and end and cap - end >= need:
            self._write(end, blob, f"{len(records)} unit(s) into spare capacity")
            self._u32(base + 4, end + need,
                      f"military end -> {len(mil) + len(records)} unit(s)")
            return
        if gs.is_ptr(begin):
            # An engine-owned military vector must not be repointed: the
            # engine also appends to it on recruitment.
            raise ValueError(
                f"{planet}'s military vector is full "
                f"({(cap or 0) - (end or 0)} bytes spare, need {need}) and "
                f"already engine-owned; growing it is what crashed the client")
        # An empty vector has no owner yet, so a fresh buffer is safe.
        size = len(blob)
        buf = self.remote.operator_new(size)
        self._write(buf, blob, f"{len(records)} unit(s) into a fresh buffer")
        self._u32(base,     buf,        "military begin")
        self._u32(base + 4, buf + size, "military end")
        self._u32(base + 8, buf + size, "military cap")

    def unload_crew(self, ship, planet, count=1, keep=None):
        """Garrison troops from a ship onto a planet; the reverse of load_crew.

        `keep` is the flight crew minimum (`ShipDesign:76`) the ship retains.
        The planet side uses spare capacity in the engine's buffer only.
        """
        self._require_sane(ship.addr + self.SHIP_CREW, f"{ship}'s crew vector")
        self._require_sane(planet.addr + self.PLANET_MILITARY,
                           f"{planet}'s military vector")
        aboard = self._live_vec(ship, self.SHIP_CREW, self.CITIZEN_STRIDE)
        if keep is not None and len(aboard) - count < keep:
            raise ValueError(
                f"{ship} carries {len(aboard)}; unloading {count} would leave "
                f"{len(aboard) - count} against a flight minimum of {keep}, "
                f"stranding it in hostile space")
        if count <= 0 or len(aboard) < count:
            raise ValueError(f"{ship} carries {len(aboard)}, cannot unload "
                             f"{count}")
        mil = self._live_vec(planet, self.PLANET_MILITARY, self.CITIZEN_STRIDE)
        if self.dry_run:
            self.log(f"  [WOULD GARRISON] {count} unit(s) from {ship} onto "
                     f"{planet} ({planet.military} stationed -> "
                     f"{planet.military + count})")
            return None
        if self.remote is None:
            raise RuntimeError("unload_crew needs a remote.Remote to allocate")

        landed = b"".join(aboard[-count:])
        begin = self._live_ptr(planet, self.PLANET_MILITARY)
        end   = self._live_ptr(planet, self.PLANET_MILITARY + 4)
        cap   = self._live_ptr(planet, self.PLANET_MILITARY + 8)
        need  = count * self.CITIZEN_STRIDE
        if gs.is_ptr(begin) and cap is not None and end is not None \
                and cap - end >= need:
            self._write(end, landed, f"{count} unit(s) into spare capacity")
            self._u32(planet.addr + self.PLANET_MILITARY + 4, end + need,
                      f"planet military end -> {len(mil) + count} unit(s)")
        else:
            # A planet's military vector must not be reallocated here. The
            # engine appends to it on recruitment through its own allocator,
            # so a buffer from this code leaves begin and end in different
            # allocations. Ship crew vectors are safe because only this code
            # grows them.
            raise ValueError(
                f"{planet}'s military vector has no spare capacity "
                f"({(cap - end) if (cap and end) else '?'} bytes free, need "
                f"{need}); growing an engine-owned vector it also appends to is "
                f"what corrupted one and crashed the client")

        # The ship side only shrinks: walk `end` back, no allocation.
        cb = self._live_ptr(ship, self.SHIP_CREW)
        self._u32(ship.addr + self.SHIP_CREW + 4,
                  cb + (len(aboard) - count) * self.CITIZEN_STRIDE,
                  f"crew end -> {len(aboard) - count} aboard")
        self.log(f"  garrisoned {count} unit(s) from {ship} onto {planet}")
        return count

    # J: sell a facility (engine call).
    def sell_facility(self, planet, type_id, count=1):
        """Sell `count` of a facility type, crediting the civ's treasury.

        Goes through PlanetProperties::SellFacility, which prices the building,
        credits Owner:8 and removes the units, since STRATEGY.md 1.1 forbids
        writing the treasury. Refused on the online path: the call site at
        0x0056EF91 is guarded by the offline flag at 0x0086F1A0, and in
        multiplayer the server owns the transaction.
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
        civ = planet.owner
        before = self._live_cash(civ) if civ else None
        ok = self.remote.sell_facility(planet.addr, type_id, count)
        after = self._live_cash(civ) if civ else None
        # The proceeds are ours, not income: next turn's reading excludes them.
        paid = (after - before) if None not in (before, after) else 0
        self.cash_effect += paid
        self.log(f"  sold {count} x {name} on {planet} for {paid} "
                 f"(engine returned {ok})")
        self.writes.append((planet.addr, b"", b"", f"sold {count} x {name}"))
        return ok

    # K: hurry the current production (engine call).
    HURRY_MIN_FRACTION = 0.5

    def hurry_state(self, planet):
        """(cost, done, total, fraction) for this planet's current build.

        Every number comes from the engine: the cost function at 0x00555660
        and the PlanetProperties total-production virtual at vftable slot 0x74.
        cost is -1 when the production cannot be hurried, as for a planet
        generating wealth (production kind 7).
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

        Engine call, since it debits Owner:8 (STRATEGY.md 1.1). The
        preconditions checked here are ones the engine enforces; checking them
        first avoids a remote thread for a call that would refuse.
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
                f"refuses below {self.HURRY_MIN_FRACTION:.0%}; "
                f"'the production needs to be at least half finished'")
        civ = self.snap.civ_of(self.snap.rd32(planet.u32(40)))             if gs.is_ptr(planet.u32(40)) else None
        # Live cash: the engine debits Owner:8 on every hurry.
        cash = self._live_cash(civ) if civ else None
        if cash is not None and cost > cash:
            raise ValueError(f"{planet} hurry costs {cost}, cash is {cash}")
        if self.dry_run:
            self.log(f"  [WOULD HURRY] {planet} at {frac:.0%} "
                     f"({done}/{total}) for {cost} credits")
            return None
        ok = self.remote.hurry_production(planet.addr)
        # The callee returns AL, so only the low byte of EAX is meaningful.
        ok = None if ok is None else bool(ok & 0xFF)
        self.log(f"  hurried {planet} for {cost} credits (engine says {ok})")
        self.cash_effect -= cost
        self.writes.append((planet.addr, b"", b"", f"hurried for {cost}"))
        return ok

    # N: conscript a citizen into the military (engine call).
    # Never a farmer, so drafts do not undo S-01's farm assignments.
    DRAFT_PREFERENCE = (6, 2, 5, 1)        # banker, scientist, miner, worker

    def draft_cost(self, civ, count=1):
        """The engine's own price for `count` conscripts. Read-only."""
        if self.remote is None:
            return None
        return self.remote.draft_cost(civ.addr, count)

    def conscript(self, planet, civ):
        """Turn one citizen into a stationed military unit, at the engine's price.

        The price comes from the engine's GetDraftCost, which depends on
        empire-wide military including ship crew. One draft per call: removing
        a citizen shifts every index above it.
        """
        if self.remote is None and not self.dry_run:
            raise RuntimeError("conscription needs a remote.Remote")
        # The index must come from a live read. The engine shifts the citizen
        # vector itself (CommitPopulation erases and memmoves, and
        # auto-conscripts population above space/10).
        self._require_sane(planet.addr + self.CITIZEN_LIST,
                           f"{planet}'s citizen vector")
        # `planet.population` is the snapshot's extent over live bytes.
        live = [struct.unpack_from("<I", r, 0)[0]
                for r in self._live_vec(planet, self.CITIZEN_LIST,
                                        self.CITIZEN_STRIDE)]
        if live != planet.population:
            self.log(f"  [!] {planet}'s citizen list moved since the snapshot "
                     f"({len(planet.population)} -> {len(live)}); using the "
                     f"live one")
        jobs = live
        pick = next((jobs.index(w) for w in self.DRAFT_PREFERENCE if w in jobs),
                    None)
        if pick is None:
            raise ValueError(f"{planet} has no draftable citizen; every one "
                             f"is a farmer, and drafting those starves it")

        cost = self.draft_cost(civ, 1)
        cash = self._live_cash(civ)
        if cost is not None and cash is not None and cost > cash:
            raise ValueError(f"a conscript costs {cost}, cash is {cash}; "
                             f"the engine would refuse")

        if self.dry_run:
            self.log(f"  [WOULD DRAFT] citizen {pick} "
                     f"({gs.JOBS.get(jobs[pick], jobs[pick])}) on {planet} "
                     f"for {cost} credits")
            return None
        if cost is None:
            raise RuntimeError("cannot draft without the engine's price")

        # The migration is done by field writes. `ChangeCitizenJobs` runs
        # CommitPopulation on a remote thread, reallocating both containers
        # while the main thread may walk them, and crashes the client (§7). A
        # conscript is one 16-byte record moving from the citizen vector to the
        # military vector with its job id set to 3.
        recs = self._live_vec(planet, self.CITIZEN_LIST, self.CITIZEN_STRIDE)
        if pick >= len(recs):
            raise ValueError(f"citizen {pick} no longer exists on {planet}")
        moved = bytearray(recs[pick])
        struct.pack_into("<I", moved, 0, remote.DRAFT_JOB)

        cbegin = self._live_ptr(planet, self.CITIZEN_LIST)
        kept = b"".join(recs[:pick] + recs[pick + 1:])
        self._write(cbegin, kept, f"citizen list minus #{pick}")
        self._u32(planet.addr + self.CITIZEN_LIST + 4, cbegin + len(kept),
                  f"citizen end -> {len(recs) - 1}")
        self._append_military(planet, [bytes(moved)])

        # A narrow §1.1 exception: Owner:8 is only debited, by the price the
        # engine quoted for this draft.
        cash = self._live_cash(civ)
        self._u32(civ.addr + 8, cash - cost,
                  f"{civ.civ_name} cash {cash} -> {cash - cost} "
                  f"(the engine's own price for this draft)")
        self.cash_effect -= cost
        if self.remote is not None:
            self.remote.invalidate_draft_cost()
        self.log(f"  drafted citizen {pick} on {planet} for {cost} "
                 f"(migrated by field write, not by ChangeCitizenJobs)")
        return pick

    MIN_COLONY_POP = 4     # citizens a colony keeps when farmers are drafted

    def conscript_to_crew(self, planet, ship, count, civ,
                          allow_farmers=False):
        """Draft citizens straight onto a ship, bypassing Planet:168.

        Citizens and crew share one 16-byte record, so the citizen list is
        rewritten without the drafted records and those records fill a fresh
        crew buffer, or the spare capacity of the ship's existing one.
        `GetDraftCost`
        prices all `count` drafts and the treasury is debited by that number,
        as in `conscript`. Crew drafted this way never joins the planet's
        garrison.
        """
        if count <= 0:
            raise ValueError("conscript_to_crew needs a positive count")
        if self.remote is None and not self.dry_run:
            raise RuntimeError("conscription needs a remote.Remote")

        # Checked before anything is taken from the planet.
        existing = self._live_ptr(ship, self.SHIP_CREW)
        if gs.is_ptr(existing):
            end = self._live_ptr(ship, self.SHIP_CREW + 4)
            cap = self._live_ptr(ship, self.SHIP_CREW + 8)
            spare = ((cap - end) // self.CITIZEN_STRIDE
                     if end is not None and cap is not None else 0)
            if spare < count:
                raise ValueError(
                    f"{ship} already has a crew vector (0x{existing:08X}) with "
                    f"room for {spare} more, need {count}; repointing a vector "
                    f"the engine owns is what crashed the client")

        self._require_sane(planet.addr + self.CITIZEN_LIST,
                           f"{planet}'s citizen vector")
        # Live read: the engine shifts this vector itself.
        recs = self._live_vec(planet, self.CITIZEN_LIST, self.CITIZEN_STRIDE)
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

        # `allow_farmers` is R-XPL-09's judgement that the colony has no use
        # for a farmer, typically a planet at its population cap with a food
        # surplus. It is not a famine signal.
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
                f"{ship} needs; the rest are farmers"
                + (f", and it has only {len(jobs)} citizen(s), at or near the "
                   f"{self.MIN_COLONY_POP} this rule will not take a colony "
                   f"below" if allow_farmers else
                   ", and drafting those starves the colony"))

        cost = self.draft_cost(civ, count)
        cash = self._live_cash(civ)
        if cost is not None and cash is not None and cost > cash:
            raise ValueError(f"{count} conscript(s) cost {cost}, cash is "
                             f"{cash}; the engine would refuse")

        if self.dry_run:
            who = ", ".join(f"{i} ({gs.JOBS.get(jobs[i], jobs[i])})"
                            for i in picks)
            self.log(f"  [WOULD DRAFT TO CREW] {count} citizen(s) on {planet} "
                     f"-> {ship} for {cost} credits: {who}")
            self.crewed_ships.add(ship.id)
            return None
        if cost is None:
            raise RuntimeError("cannot draft without the engine's price")

        # Both vectors are rewritten from one live read, so several drafts per
        # call are safe. CommitPopulation never runs (§7), and the citizen
        # vector's capacity at Planet:144+8 is left as it stands.
        drafted = []
        for i in picks:
            rec = bytearray(recs[i])
            struct.pack_into("<I", rec, 0, remote.DRAFT_JOB)
            drafted.append(bytes(rec))

        keep = [r for i, r in enumerate(recs) if i not in picks]
        cbegin = self._live_ptr(planet, self.CITIZEN_LIST)
        kept = b"".join(keep)
        self._write(cbegin, kept, f"citizen list minus {count}")
        self._u32(planet.addr + self.CITIZEN_LIST + 4, cbegin + len(kept),
                  f"citizen end -> {len(keep)}")

        self._add_ship_crew(ship, drafted)

        # Re-read live: `civ.cash` holds the balance the pass opened with.
        cash = self._live_cash(civ)
        self._u32(civ.addr + 8, cash - cost,
                  f"{civ.civ_name} cash {cash} -> {cash - cost} "
                  f"(the engine's price for {count} draft(s))")
        self.cash_effect -= cost
        self.crewed_ships.add(ship.id)
        # The draft price depends on empire-wide military, which just rose.
        if self.remote is not None:
            self.remote.invalidate_draft_cost()
        self.log(f"  drafted {count} citizen(s) on {planet} directly onto "
                 f"{ship} for {cost}; the planet's military vector was never "
                 f"involved")
        return picks

    # L: set a ship's command, target node included (engine call).
    # Order types that consult Ship:56. Ship::HasActiveTargetedOrder
    # (0x004D9880) requires one of these types and a live Ship:56, and a ship
    # that has one is moved as an escort of its target (STRATEGY.md §4.4).
    TARGETED_ORDER_TYPES = (1, 4, 7)      # Move, Attack, MoveNear

    def set_command(self, ship, order_type, target, civ=None):
        """Give a ship an order that names a target object, not just coordinates.

Aimed at one of our own ships it is an escort: the engine re-aims the
        order at the leader every turn. Aimed at a planet or an enemy ship the
        engine drops the target node at the first turn boundary, so it is not
        a pursuit tool; `exterminate.send_to` re-aiming each pass is
        (STRATEGY.md §4.4). Verified live over five boundaries since the stub
        fix that ended its crash.

        ShipCommand's constructor turns the target into a reference node via
        GetReferenceNode(objectId), and Ship::SetCommand writes Ship:52/56/60/61.
        The 16-byte ShipCommand comes from the engine's operator new and leaks;
        SetCommand copies out of it.
        """
        if order_type not in gs.ORDER_TYPES:
            raise ValueError(f"order type {order_type} is not one the UI issues")
        if target is None:
            raise ValueError("a targeted order needs a target object")
        if not self.is_crewed(ship):
            raise ValueError(
                f"{ship} has no crew; the engine cancels a crewless ship's "
                f"orders at the next turn boundary, so this would leak an "
                f"allocation every turn and never move")
        if self.dry_run:
            self.log(f"  [WOULD COMMAND] {ship} -> type {order_type} "
                     f"({gs.ORDER_TYPES[order_type]}) targeting {target}, "
                     f"via ShipCommand + Ship::SetCommand so Ship:56 is set")
            return None
        if self.remote is None:
            raise RuntimeError("set_command needs a remote.Remote")

        # Order object and route first. SetCommand writes four fields and builds
        # no route; the engine's own callers run Ship::SetOrder before it
        # (0x004CD090, 0x004D0FB3). A route that disagrees with Ship:52/56
        # crashes the client at the next turn boundary.
        new_ptr = None
        if not gs.is_ptr(ship.order_ptr):
            if civ is None:
                raise ValueError(
                    f"{ship} has no order object and no civ was passed, so "
                    f"one cannot be originated; pass civ= to set_command")
            new_ptr = self.create_order(ship, target, civ)
            if new_ptr is None:
                raise RuntimeError(f"could not originate an order for {ship}")

        # Between retarget_order and SetCommand the route names the new target
        # while Ship:52/56 still describe the old order. The bytes are saved so
        # a failure in that window can be rolled back.
        saved = self._order_bytes(ship, new_ptr if new_ptr else ship.order_ptr)
        try:
            # Route and coordinates only. SetCommand writes Ship:52 and Ship:56
            # together; a targeted type with a null node can crash the engine.
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
                    f"ShipCommand resolved to the STATIC NULL NODE, so the "
                    f"target id did not resolve; installing it would give a "
                    f"targeted order the engine treats as untargeted")
            # If the turn rolled over, the coordinates came from the previous
            # turn's snapshot; roll back rather than install.
            self._require_same_turn(ship)
            self.remote.ship_set_command(ship.addr, block)
        except Exception:
            self._restore_order_bytes(saved, ship)
            raise
        got_type = self.snap.rd32(ship.addr + SHIP_ORDER_TYPE)
        got_node = self.snap.rd32(ship.addr + 56)
        self.log(f"  {ship} order type {got_type}, Ship:56 -> 0x{got_node:08X} "
                 f"(target {target})")
        self.writes.append((ship.addr, b"", b"",
                            f"SetCommand type {order_type} at {target}"))
        return got_type == order_type and got_node == node

    # M: declare war through the engine, as the declare-war button does.
    WAR, NEUTRAL = 1, 0
    RELATION_NAMES = {0: "Neutral", 1: "War", 2: "Cease-Fire", 3: "Peace",
                      4: "Alliance"}

    def declare_war(self, civ, other):
        """Declare war from `civ` on `other`; True when both records read War.

        The engine registers a war Treaty naming `civ` as the declarer, makes
        first contact and writes War into both civs' relation records. Callers
        check `remote.war_protection` first, as the UI's validator does.
        """
        if other.addr == civ.addr:
            raise ValueError("a civ cannot declare war on itself")
        if self.dry_run:
            self.log(f"  [WOULD DECLARE WAR] {civ.civ_name!r} -> "
                     f"{other.civ_name!r}, through the engine")
            return None
        if self.remote is None:
            raise RuntimeError("declare_war needs a remote.Remote")
        treaty = self.remote.declare_war(civ.addr, civ.id, other.addr, other.id)
        self.log(f"  war treaty 0x{treaty:08X}: {civ.civ_name!r} declares war "
                 f"on {other.civ_name!r}")
        codes = []
        for a, b in ((civ, other), (other, civ)):
            rec = self.remote.relation_record(a.addr, b.addr)
            codes.append(self.snap.rd32(rec + remote.RELATION_CODE_OFF)
                         if rec else None)
        return all(c == self.WAR for c in codes)

    # G: set a planet's recruitment rate (Planet:100 byte 3).
    # The UI maximum is per planet: 40% plus 20% per military camp. The engine
    # does not clamp (the getter is `movsx eax, byte ptr [ecx+0xf]`), so the
    # cap is enforced here, and the byte must stay under 128 or it reads
    # negative.
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

        Only that byte is written; bytes 0-2 are separate fields (likely
        loyalty). Values above max_recruitment are refused.
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

    # F: set the research topic (Owner:144 and :152 together).
    def set_research(self, civ, topic_id):
        ok = self._u32(civ.addr + 144, topic_id, f"{civ.civ_name} topic -> {topic_id}")
        return self._u32(civ.addr + 152, topic_id,
                         f"{civ.civ_name} topic mirror -> {topic_id}") and ok

    # E: order a ship.
    def retarget_order(self, ship, order_type, target_xyz, order_ptr=None,
                       set_type=True):
        """Point an existing order at a new destination.

        Writes the target triple, the route leg (origin = the ship's current
        position, dest = the target, length = the distance), the order type,
        and the local-civ pending-orders flag. Nothing is allocated.
        """
        if order_type not in gs.ORDERS:
            raise ValueError(f"order type {order_type} is not one the UI issues")
        # order_ptr lets a caller aim an order it just created, before a rescan.
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

        # Route leg. Rewritten in place only; never grown, so nothing allocates.
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

        # Progress must be reset, or the ship resumes from however far the
        # order had already travelled.
        self._f32(ord_ptr + ORD_PROGRESS, 0.0, "route progress -> 0")

        # set_type=False leaves Ship:52 alone, for callers that set the type
        # and target node together through Ship::SetCommand. With Ship:56 the
        # null node the type is harmless: HasActiveTargetedOrder reads false
        # and the ship follows the route written here.
        if set_type:
            self._u32(ship.addr + SHIP_ORDER_TYPE, order_type,
                      f"order type -> {name}")
        self._u32(ship.addr + SHIP_HAS_ORDERS, 1, "pending-orders flag")
        return True

    def create_order(self, ship, target, civ):
        """Originate an order object for a ship that has none.

        Route 2 of STRATEGY.md §6a: the engine's own allocator and constructor
        build the object.

            order = operator new(0x60)                       0x0065165A
            order_ctor(ECX=order, ship, target, owner, f, 1, 0)   0x004E8420

        `f` is what the ship's vftable[0x30] getter returns. Origin and target
        go in as allocation starts (EJBO tag - 8) and the Owner as its tag-52
        primary form; the tag-8 owner form crashes the client.

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

        Nothing is allocated or freed. `Ship:40` (owner) and `Ship:48` (order)
        are independent, so an order is not bound to the ship that created it.
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
        """ETA is not stored: the engine derives it as ceil(distance / speed)."""
        d = ship.design
        if not d or not d.speed or d.speed <= 0:
            return None
        import math
        return math.ceil(length / d.speed)

    def summary(self):
        verb = "would perform" if self.dry_run else "performed"
        return f"{verb} {len(self.writes)} write(s)"


class NeedsEngineOrder(Exception):
    """Raised when a ship has no order object (Ship:48 is null) and none can
    be made. create_order() needs a remote.Remote."""

    def __init__(self, ship):
        super().__init__(
            f"{ship} has no order object (Ship:48 is null) and this Actuator "
            f"has no remote.Remote to create one with. Construct the Actuator "
            f"with remote_=remote.Remote(pid).")
        self.ship = ship


class StaleTurn(Exception):
    """Raised when the turn rolled over in the middle of issuing an order.

    The order's coordinates came from the pass's snapshot and are wrong once
    the ship has moved. The caller rolls back and the next pass re-decides."""
