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

    @property
    def remote(self):
        if self._remote is None and self._remote_factory is not None:
            self._remote = self._remote_factory()
        return self._remote

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
        if not design.computed:
            raise ValueError(f"{design} is not computed yet (-1 sentinels); "
                             f"queueing it would build from garbage")
        # A human can only queue ships where there is a shipyard (facility 2).
        if 2 not in planet.facilities:
            raise ValueError(f"{planet} has no shipyard, so the UI could not "
                             f"queue a ship there")
        self.log(f"  queue {design.design_name!r} at {planet}")
        ok = self._u32(planet.addr + 296, design.addr - 12,
                       f"Planet:296 -> ShipDesign {design.design_name!r} (tag-12)")
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

    # -- F: set the research topic (Owner:144 and :152 together) --------
    def set_research(self, civ, topic_id):
        ok = self._u32(civ.addr + 144, topic_id, f"{civ.civ_name} topic -> {topic_id}")
        return self._u32(civ.addr + 152, topic_id,
                         f"{civ.civ_name} topic mirror -> {topic_id}") and ok

    # -- E: order a ship ------------------------------------------------
    def retarget_order(self, ship, order_type, target_xyz, order_ptr=None):
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

        self._u32(ship.addr + SHIP_ORDER_TYPE, order_type, f"order type -> {name}")
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
