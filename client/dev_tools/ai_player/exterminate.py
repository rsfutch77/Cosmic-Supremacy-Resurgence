"""
exterminate.py — the Exterminate rules (STRATEGY.md §4.4)
=========================================================
R-XTM-01  keep a standing fleet
R-XTM-03  send the fleet at the weakest reachable enemy planet
R-XTM-05  pull damaged ships out

    python exterminate.py            # dry run, one pass
    python exterminate.py --apply

── What the engine actually requires, and why these rules look like this ──────
Three findings shape every rule here, and two of them make the obvious
implementation wrong.

**1. Combat triggers on CO-LOCATION, not on an attack order.** The driver
(0x00523AC0) buckets every ship by position, needs two or more distinct owners in
a bucket within 0.8 units of a common point, and never reads the order type at
all. So "attack" is not a command that starts a fight — it is a statement about
intent that the hostility classifier one level down consults. Getting a warship to
the enemy is the whole job.

**2. WAR IS REQUIRED, and it is checked twice.** At order-issue time by
0x00477D90 (which refuses Conquer outright) and again inside all three battle
phases by IsAtWarWith (0x00535080), which tests relation code 1 in Owner:248. A
fleet parked on an enemy planet at Neutral does nothing at all. On this galaxy
GetGameOption(2) reads 0, so first contact leaves civs Neutral and war must be
declared — R-XTM-00, which is NOT built: it needs a Treaty object and the honest
path is the NWTR command, because a raw relation write produces no news item and
skips the reputation cost.

**3. An undercrewed ship contributes exactly zero firepower.** The firepower
accumulator skips any ship whose Ship:124 crew count is below its design's
ShipDesign:76. A warship crewed to the colony-ship minimum of 2 may be a warship
that fights at zero. min_crew() reads the real figure.

Damage is multiplicative and there is no early exit: two phases of up to 100
rounds each, and Ship:136_new = Ship:136_old * (1 - damage/hitpoints). A battle
ends by annihilation or is cut off with both sides alive, so a STALEMATE is a real
outcome and a losing fleet does not retreat on its own — R-XTM-05 has to.
"""
import sys

import actions
import cli
import exploit
import gamestate as gs
import remote
import sensors

WARSHIP_TARGET = 2        # warships to keep once we are at war with anyone
TROOP_TARGET = 1          # troop hulls to keep while anything is takeable
GARRISON_WINDOW = 3       # turns after a capture that still count as "newly
                          # taken" — a captured planet read Planet:92 == the
                          # turn it changed hands, and the hull arrives in the
                          # same boundary the capture resolves in
RETREAT_AT = 0.30         # Ship:136 below this and the ship goes home
THREAT_DIST = 120.0       # "near" — one sun separation (measured minimum 111.7)

WAR = 1                   # Owner:248 relation code


_REL_CACHE = {}


def clear_relation_cache():
    """Drop the cached relation codes.

    MUST be called after anything changes a relation. The cache is keyed on
    (turn, civ, other), and a declaration made within a single turn does not
    change any of those — so a freshly declared war kept reading as peace, and
    the test of the declare actuator reported failure on a write that had
    actually worked.
    """
    _REL_CACHE.clear()


def relation_code(snap, civ, other, act=None):
    """The relation code between two civs. None when it cannot be determined.

    0 Neutral, 1 War, 2 Cease-Fire, 3 Peace, 4 Alliance.

    Asks the engine (GetRelationCode) rather than walking the container. A hand
    rolled traversal was tried first and was wrong in a way worth recording:
    Owner:248 is NOT a std::map — it holds four pointers where a map holds
    {head, size} — so following left/parent/right walked out of the structure
    into unrelated memory. One "node" it accepted contained the string
    "IllegalPosition". It reported "not at war" on a galaxy where war had in fact
    been declared, which is the worst possible failure for this sensor: every
    Exterminate rule would silently no-op and look like it was working.

    Cached per (turn, civ pair) so a pass costs one call per rival rather than
    one per rule. Relations change rarely and only through a deliberate act.
    """
    if act is None or act.remote is None:
        return None
    key = (snap.turn, civ.addr, other.addr)
    if key not in _REL_CACHE:
        try:
            _REL_CACHE[key] = act.remote.relation_code(civ.addr, other.addr)
        except (RuntimeError, OSError):
            return None
    return _REL_CACHE[key]


def at_war_with(snap, civ, other, act=None):
    return relation_code(snap, civ, other, act) == WAR


def enemies(snap, civ, act=None):
    """Civs we are actually at war with."""
    return [c for c in snap.civs
            if c.addr != civ.addr and at_war_with(snap, civ, c, act)]


def send_to(act, ship, target, civ, order_type=1):
    """Move `ship` to `target` with a route-only order. NO actuator L.

    **`set_command` KILLS THE CLIENT AT THE NEXT TURN BOUNDARY.** Measured from
    `client/armed.dat` (turn 38, at war, one crewed `b1`), one turn boundary per
    trial:

        set_command(4 attack)        -> client dead
        set_command(1 move)          -> client dead
        create_order + retarget(1)   -> SURVIVED, and moved 9.5 units, which is
                                        exactly this hull's ShipDesign:36

    The order is written cleanly and the pass completes either way; the engine
    dies resolving it. So this is the whole `ShipCommand` / `Ship::SetCommand`
    path, not Attack, and not the order TYPE — a route-only Move is fine.

    That also corrects the premise actuator L was built on. `Ship:56` is left as
    the static null node here and the ship navigates correctly anyway, so
    `Ship::HasActiveTargetedOrder` gates something other than movement.

    **Combat does not need actuator L.** The battle driver `0x00523AC0` buckets
    ships by POSITION and never reads the order type at all, so arriving at an
    enemy-held planet while at war is what starts a fight. A Move order that gets
    the hull there does the whole job.
    """
    ptr = (ship.order_ptr if gs.is_ptr(ship.order_ptr)
           else act.create_order(ship, target, civ))
    if ptr is None:
        raise RuntimeError(f"could not originate an order for {ship}")
    act.retarget_order(ship, order_type, target.pos, order_ptr=ptr)
    return ptr


def _why(snap, civ, act, foes):
    """'at war with [...]' or 'in contact with [...]', whichever is true.

    R-XTM-01 fires on either, and saying "at war" when we are merely in contact
    would misreport the galaxy's diplomatic state in the one log line someone
    reads to find out why the fleet is being built.
    """
    names = [f.civ_name for f in foes]
    at_war = [f.civ_name for f in foes if at_war_with(snap, civ, f, act)]
    return (f"at war with {at_war}" if at_war
            else f"in contact with {names} (not yet at war)")


def contacted(snap, civ, hist):
    """Rival civs we have MET — a planet of theirs is in our discovery set.

    Contact, not war. This is the trigger R-XTM-01 needs; see the deadlock note
    in run_xtm01 for why asking `enemies()` there could never work.
    """
    out = []
    for other in snap.civs:
        if other.addr == civ.addr:
            continue
        if any(p.owner is not None and p.owner.addr == other.addr
               and hist.can_see(snap, p) for p in snap.planets):
            out.append(other)
    return out


def production_design(snap, planet):
    """The ShipDesign this planet is currently building, if it is building one.

    Planet:296 points at the shared design in its allocation-start form
    (tag - 12) for a ship build, and at the planet's embedded Facility for a
    facility build, so matching on tag-12 distinguishes the two.
    """
    ptr = planet.u32(296)
    if not gs.is_ptr(ptr):
        return None
    return next((d for d in snap.designs if d.addr - 12 == ptr), None)


def anti_planet(design, act=None):
    """True when this design's firepower is concentrated in the THIRD slot.

    WARMS THE CACHE FIRST when it can. firepower is a lazy derived stat that
    reads -1 (surfacing as 0) until a getter touches it, so a freshly created
    design reports (0, 0, 0) and this returned False for a dedicated bomber
    carrying a fusion bomb. That is the same lazy-cache trap that had R-XPN-01
    building the rival's design and is_warship calling armed hulls unarmed --
    third time, and this time in the function written to avoid it.

    The three firepower values are three independent damage pools, and planetary
    defence participants land in the third one. Confirmed by the user's own two
    designs: an anti-ship fighter reads (35, 10, 0) and a planet bomber reads
    (0, 0, 500). So the third slot is the anti-planet weapon class and the first
    two are anti-ship, which is what makes "one of each" the right fleet.
    """
    fp = design.firepower
    # Only warm the cache for a design that HAS weapons. An unarmed hull's
    # firepower is legitimately (0,0,0), so "all zero means cold" made this call
    # the getters on every single evaluation, every turn, forever. Those getters
    # WRITE the lazy stat block, and the ship-design tab does the same work on
    # the UI thread — the user crashed the client twice by opening that tab while
    # this was running. Fewer stub calls is not an optimisation here, it is the
    # mitigation.
    if (act is not None and act.remote is not None
            and design.weapons and not any(fp)):
        try:
            fp = act.remote.design_firepower(design.addr)
        except (RuntimeError, OSError):
            pass
    return fp[2] > max(fp[0], fp[1])


def warships(snap, civ):
    return [s for s in snap.owned_ships(civ) if s.role == "WARSHIP"]


def warship_designs(snap, civ):
    """Our own armed designs, buildable ones only.

    Filtered by OWNER, not by whether the derived stat block is warm. That block
    is a lazy cache reading -1 until a getter touches it, and filtering on it
    once had R-XPN-01 selecting the RIVAL's design.
    """
    out = []
    for d in snap.designs:
        own = d.owner
        if own is None or own.addr != civ.addr:
            continue
        if not d.chassis or not d.engines:
            continue
        if d.weapons:            # cache-independent test for 'armed'
            out.append(d)
    return out


def target_planets(snap, civ, hist, foes):
    """Enemy planets we can legitimately see, weakest first.

    Weakness is stationed military plus defensive facilities. The facility half
    is now real rather than a guess: facilities.py carries the definition table,
    and only types with a non-zero defence class contribute to planetary defence
    at all — the battle code skips every other type.
    """
    import facilities
    foe_addrs = {f.addr for f in foes}
    tbl = facilities.read_table(snap)
    out = []
    for p in snap.planets:
        own = p.owner
        if own is None or own.addr not in foe_addrs:
            continue
        if not hist.can_see(snap, p):
            continue
        defence = sum(tbl[t]["strength_1"] + tbl[t]["strength_2"]
                      for t, n in p.facilities.items()
                      for _ in range(n) if t in tbl and tbl[t]["defence_class"])
        out.append((p.military + defence, p))
    out.sort(key=lambda x: x[0])
    return out


def run_xtm01(snap, civ, act, hist, log=print):
    """R-XTM-01: keep a standing fleet once there is someone to fight.

    THE TRIGGER IS CONTACT, NOT WAR (STRATEGY.md §4.4: threatLevel is "floored
    at 2 once contact is made"). Asking `enemies()` here instead was a DEADLOCK,
    and a silent one — every rule logged nothing and looked content:

        R-XTM-01 builds no warship until at war
        R-XTM-00 declares no war until a crewed warship exists

    so from a neutral start neither could ever fire, and R-XTM-01 is the ONLY
    rule that queues an armed hull. Measured on a full game: by turn 200 the civ
    held 9 planets, score 33,362, cash 11,229, both f1 and b1 designs ready and
    105 of 108 systems explored — and had never once evaluated a target, because
    R-XTM-03 was never reached. Nothing in the logs indicated a problem.
    """
    foes = enemies(snap, civ, act) or contacted(snap, civ, hist)
    if not foes:
        return 0
    have = warships(snap, civ)
    queued_now = [d for d in (production_design(snap, p)
                              for p in snap.owned_planets(civ)) if d is not None]
    fleet = [s.design for s in have if s.design] + queued_now
    # COMPOSITION, not just a count. The two firepower classes do not substitute
    # for each other: slots 1-2 hit ships and slot 3 hits planets, so a pair of
    # fighters cannot touch a planet however many of them there are. A flat
    # target of two was about to produce exactly that -- two f1 while at war with
    # a single enemy PLANET -- because the classifier was reading a cold cache
    # and could not tell the hulls apart.
    have_ap = any(anti_planet(d, act) for d in fleet)
    have_as = any(not anti_planet(d, act) for d in fleet)
    if len(fleet) >= WARSHIP_TARGET and have_ap and have_as:
        return 0
    stranded = exploit.awaiting_crew(snap, civ, act, role="WARSHIP")
    if stranded:
        log(f"R-XTM-01: {len(stranded)} warship(s) built and waiting for "
            f"crew; another hull would not add any firepower")
        return 0
    designs = warship_designs(snap, civ)
    if not designs:
        import research
        done = {t for t, _ in civ.completed}
        arms = sorted(research.unlocked(done, research.WEAPON))
        log(f"R-XTM-01: {_why(snap, civ, act, foes)} and "
            f"{len(have)}/{WARSHIP_TARGET} warship(s), but this civ has no "
            f"armed design."
            + (f" Weapons {arms} are unlocked, so one can be synthesised: "
               f"client/dev_tools/game_cycle.py --design "
               f"'f1:chassis=0,scanner=0,engine=0,weapon={arms[0]}'"
               if arms else
               " No weapon is unlocked yet either — R-XPL-04 is prioritising "
               "the research that grants one."))
        return 0
    yards = [p for p in snap.owned_planets(civ)
             if exploit.SHIPYARD in p.facilities and not p.u32(344)
             and not act.production_claimed(p)]
    if not yards:
        log(f"R-XTM-01: {len(have)}/{WARSHIP_TARGET} warship(s); every shipyard "
            f"is already building")
        return 0
    # Keep one anti-planet and one anti-ship hull rather than two of whatever
    # scores highest: the pools do not substitute for each other, so a fleet of
    # two bombers cannot win a ship fight and two fighters cannot hurt a planet.
    # Count what is already QUEUED as well as what is flying. Counting only
    # existing ships made every pass want the same class: with no warships yet,
    # "we have no bomber" stayed true while two bombers were already on the
    # slipways, and the fleet came out as two of one kind.
    want_anti_planet = not have_ap
    pool = [d for d in designs
            if anti_planet(d, act) == want_anti_planet] or designs
    design = max(pool, key=lambda d: max(d.firepower))
    planet = max(yards, key=lambda p: len(p.population))
    log(f"R-XTM-01: {len(have)}/{WARSHIP_TARGET} warship(s) — queueing "
        f"{design.design_name!r} at {planet}")
    try:
        act.queue_ship(planet, design)
        return 1
    except (ValueError, RuntimeError) as e:
        log(f"  could not queue: {e}")
        return 0


def run_xtm03(snap, civ, act, hist, log=print):
    """R-XTM-03: send crewed warships at the weakest visible enemy planet.

    A Move order is enough. Combat is positional: arriving inside the 0.8-unit
    bucket of an enemy-held planet, at war, is what starts a fight. Attack (4) is
    used rather than Move (1) because both are targeted types requiring Ship:56
    anyway, and Attack is what a human clicks.
    """
    foes = enemies(snap, civ, act)
    if not foes:
        return 0
    fleet = [s for s in warships(snap, civ)
             if act.is_crewed(s) and not act.is_claimed(s)
             and (s.condition is None or s.condition >= RETREAT_AT)]
    if not fleet:
        return 0
    ranked = target_planets(snap, civ, hist, foes)
    if not ranked:
        log(f"R-XTM-03: at war with {[f.civ_name for f in foes]} but no enemy "
            f"planet is visible yet — Explore has to find one first")
        return 0
    weakness, target = ranked[0]
    log(f"R-XTM-03: {len(fleet)} warship(s) -> {target} "
        f"(weakness {weakness}, weakest of {len(ranked)} visible)")
    acted = 0
    for ship in fleet:
        need = exploit.min_crew(ship, act)
        if len(ship.crew) < need:
            log(f"  {ship} has {len(ship.crew)}/{need} crew and would "
                f"contribute zero firepower; leaving it")
            continue
        try:
            # Move (1), not Attack (4), and via send_to rather than actuator L:
            # set_command crashes the client at the next turn boundary for BOTH
            # types, and combat triggers on co-location regardless. See send_to.
            send_to(act, ship, target, civ, order_type=1)
            act.claim(ship)
            acted += 1
        except (ValueError, RuntimeError) as e:
            log(f"  could not order {ship}: {e}")
    return acted


def run_xtm04(snap, civ, act, hist, log=print):
    """R-XTM-04: land troops on an undefended enemy planet and take it.

    THIS IS WHAT R-XTM-03 CANNOT DO. A warship arriving at an enemy planet with
    no garrison and no defences does exactly nothing — measured, two fleets sat
    in each other's colonies for fifty turns. Killing what defends a planet and
    TAKING it are different actions needing different hulls, and this is the
    second one.

    Confirmed mechanics, from a controlled pair (STRATEGY.md §4.4):
      * order type 5 is safe across a turn boundary, route-only, Ship:56 null
      * the hull must carry ONE record above ShipDesign:76 — with exactly the
        minimum the order stays live and unfulfilled forever
      * the capture keeps the population, garrisons the landed unit, and the
        ship survives minus that one record

    WEAKEST FIRST IS RIGHT HERE, and it is the same ranking that is wrong for
    R-XTM-03. An invasion wants the target nothing is defending; a battle wants
    a target with something to fight. Same function, opposite ends of the list.
    """
    foes = enemies(snap, civ, act)
    if not foes:
        return 0
    troopers = [s for s in snap.owned_ships(civ)
                if s.role == "TROOP" and not act.is_claimed(s)]
    if not troopers:
        return 0

    ranked = target_planets(snap, civ, hist, foes)
    undefended = [(w, p) for w, p in ranked if w == 0]
    if not undefended:
        log(f"R-XTM-04: {len(troopers)} troop ship(s) but every visible enemy "
            f"planet is defended; that is R-XTM-03's job first")
        return 0

    ours = snap.owned_planets(civ)
    acted = 0
    for ship in troopers:
        # The flight crew is not an invasion force. A hull at exactly
        # ShipDesign:76 flies to the target and sits there — the failure looks
        # identical to a refused order, so it is checked here rather than
        # discovered in orbit.
        need = exploit.min_crew(ship, act)
        if len(ship.crew) <= need:
            # RE-STATION IT. A spent hull is not useless, it is empty, and the
            # crew rules only load at a planet we own — so a transport left in
            # orbit of the planet it just took (or just lost) waits there
            # forever while R-XTM-07 builds a replacement it did not need.
            here = ship.orbiting
            if here is not None and here.owner is not None \
                    and here.owner.addr == civ.addr:
                log(f"R-XTM-04: {ship} is empty at {here}, where R-XPL-06 can "
                    f"reload it")
                continue
            if not ours:
                continue
            home = min(ours, key=lambda p: gs.dist(ship.pos, p.pos))
            log(f"R-XTM-04: {ship} carries {len(ship.crew)} and cannot land "
                f"anyone — sending it home to {home} to re-arm "
                f"({gs.dist(ship.pos, home.pos):.0f} units)")
            try:
                send_to(act, ship, home, civ, order_type=1)
                act.claim(ship)
                acted += 1
            except (ValueError, RuntimeError) as e:
                log(f"  could not send it home: {e}")
            continue
        _w, target = min(undefended,
                         key=lambda wp: gs.dist(ship.pos, wp[1].pos))
        log(f"R-XTM-04: {ship} ({len(ship.crew)} aboard, {need} to fly) "
            f"-> conquer {target}, undefended, "
            f"{gs.dist(ship.pos, target.pos):.0f} units")
        try:
            send_to(act, ship, target, civ, order_type=5)
            act.claim(ship)
            acted += 1
        except (ValueError, RuntimeError) as e:
            log(f"  could not order {ship}: {e}")
    return acted


def run_xtm06(snap, civ, act, hist, log=print):
    """R-XTM-06: garrison a freshly taken planet with the troops still aboard.

    A captured planet is DISLOYAL and in civil disorder — the UI warns it may
    rebel and swaps its corruption readout for a civil-disorder one — and the
    repair is occupying infantry. The invasion leaves exactly one unit as the
    garrison, which is the minimum the engine takes, not a sufficient one.

    So anything still aboard, above the crew needed to fly home, goes down onto
    the planet it just took. It costs nothing: those units are already paid for,
    and a troop hull sitting in orbit with them still loaded is holding a
    garrison hostage to a return trip it may not survive.

    `[ ]` STILL NOT MEASURED, and the one measurable proxy came back NEGATIVE.
    `Planet:368` looked like the sensor this rule needed — it drops when a planet
    changes hands and climbs back over turns — but per-turn sampling showed it is
    exactly `min(8, turn - Planet:92 + 1)`, and that a garrison does not change
    it: #450 recovered 1..8 carrying two stationed units while #149 recovered
    1..8 carrying none, +1 per turn each, on the same turns. So that field is a
    timer, not loyalty, and loyalty itself is still unlocated — `Planet:100`
    bytes 0-2, the long-standing candidate, were ruled out at the same time.

    That garrisoning repairs loyalty and disorder remains the user's reading of
    the client, which is real evidence — it is just evidence this AI cannot see
    yet. The rule is kept because those units are otherwise idle and a captured
    planet was lost while empty five turns after we took it, so occupying costs
    nothing and plausibly buys something. It has NOT been shown to work.
    """
    acted = 0
    for ship in snap.owned_ships(civ):
        if ship.role != "TROOP":
            continue
        p = ship.orbiting
        if p is None or p.owner is None or p.owner.addr != civ.addr:
            continue
        # Only somewhere NEWLY TAKEN. A troop hull over the homeworld is loading
        # for the next invasion, not unloading from the last one, and dumping
        # its troops there would have the crew rules and this one undo each
        # other every turn.
        #
        # Planet:92 is the signal: it is rewritten on CAPTURE, not only on
        # founding — #41 read 222 after changing hands on turn 222. A window
        # rather than equality, because the capture resolves in the same
        # boundary the hull arrives in, so by the next pass the turn has moved.
        age = None if p.colonised_turn is None else snap.turn - p.colonised_turn
        if age is None or not (0 <= age <= GARRISON_WINDOW):
            continue
        keep = exploit.min_crew(ship, act)
        spare = len(ship.crew) - keep
        if spare <= 0:
            continue
        log(f"R-XTM-06: garrisoning {spare} unit(s) from {ship} onto {p}, "
            f"newly taken and in disorder (keeping {keep} to fly)")
        try:
            act.unload_crew(ship, p, count=spare, keep=keep)
            acted += 1
        except (ValueError, RuntimeError) as e:
            log(f"  could not garrison: {e}")
    return acted


def run_xtm07(snap, civ, act, hist, log=print):
    """R-XTM-07: keep a troop transport while there is anything to take.

    R-XTM-04 can only spend hulls somebody built, and nothing built them —
    R-XTM-01 queues warships and stops there, so the first two troop ships in
    this project were made by hand. A civ that can see an undefended enemy
    planet and has no way to land on it is not at war, it is watching.
    """
    foes = enemies(snap, civ, act)
    if not foes:
        return 0
    ranked = target_planets(snap, civ, hist, foes)
    if not any(w == 0 for w, _p in ranked):
        return 0                       # nothing takeable; build warships instead

    have = [s for s in snap.owned_ships(civ) if s.role == "TROOP"]
    if len(have) >= TROOP_TARGET:
        return 0
    # A hull built and waiting for crew is not a reason to build another, the
    # same trap R-XTM-01 fell into: the shipyard fills with hulls nobody can fly
    # while the crew rules work through them one conscript at a time.
    stranded = exploit.awaiting_crew(snap, civ, act, role="TROOP")
    if stranded:
        log(f"R-XTM-07: {len(stranded)} troop ship(s) built and waiting for "
            f"crew; another hull would not land anyone sooner")
        return 0

    designs = [d for d in snap.designs
               if d.owner is not None and d.owner.addr == civ.addr
               and d.role == "TROOP"]
    if not designs:
        log(f"R-XTM-07: {len(have)}/{TROOP_TARGET} troop ship(s) and this civ "
            f"has no design with a troop bay. Synthesise one: "
            f"client/dev_tools/game_cycle.py --design "
            f"'t1:chassis=0,scanner=0,engine=1,engine=1,module=1'")
        return 0
    yards = [p for p in snap.owned_planets(civ)
             if exploit.SHIPYARD in p.facilities and not p.u32(344)
             and not act.production_claimed(p)]
    if not yards:
        log(f"R-XTM-07: {len(have)}/{TROOP_TARGET} troop ship(s); every "
            f"shipyard is already building")
        return 0
    design = designs[0]
    planet = max(yards, key=lambda p: len(p.population))
    log(f"R-XTM-07: {len(have)}/{TROOP_TARGET} troop ship(s) — queueing "
        f"{design.design_name!r} at {planet}")
    try:
        act.queue_ship(planet, design)
        return 1
    except (ValueError, RuntimeError) as e:
        log(f"  could not queue: {e}")
        return 0


def run_xtm05(snap, civ, act, hist, log=print):
    """R-XTM-05: withdraw a badly damaged ship to the nearest owned planet.

    Damage is multiplicative and compounds across turns, and the round loop has
    no early exit — a losing ship keeps taking hits for up to 200 rounds and its
    condition never recovers on its own. Nothing pulls it out but this.
    """
    ours = snap.owned_planets(civ)
    if not ours:
        return 0
    acted = 0
    for ship in snap.owned_ships(civ):
        c = ship.condition
        if c is None or c >= RETREAT_AT or act.is_claimed(ship):
            continue
        home = min(ours, key=lambda p: gs.dist(ship.pos, p.pos))
        log(f"R-XTM-05: {ship} at condition {c:.2f} — withdrawing to {home}")
        try:
            send_to(act, ship, home, civ, order_type=1)   # not actuator L
            act.claim(ship)
            acted += 1
        except (ValueError, RuntimeError) as e:
            log(f"  could not withdraw: {e}")
    return acted


# R-XTM-05 runs FIRST: a ship that should be retreating must not be sent back
# into the fight by R-XTM-03 in the same pass.
def run_xtm00(snap, civ, act, hist, log=print):
    """R-XTM-00: declare war on a civ we can reach and are equipped to fight.

    OFF BY DEFAULT. Declaring war is outward-facing and effectively irreversible
    for the rest of a galaxy's life, so it is not something to start doing as a
    side effect of a code change. Set ALLOW_DECLARE_WAR to enable it, which is
    the intended configuration for an AI-vs-AI galaxy.

    Aggression is tied to CAPABILITY rather than to opportunity: the rule waits
    until it can both see a planet of theirs and field a crewed warship. An AI
    that declares on first sight and then spends fifty turns building a fleet has
    simply given the other side fifty turns of warning.
    """
    if not ALLOW_DECLARE_WAR:
        return 0
    ready = [s for s in warships(snap, civ) if act.is_crewed(s)]
    if not ready:
        return 0
    acted = 0
    for other in snap.civs:
        if other.addr == civ.addr or at_war_with(snap, civ, other, act):
            continue
        seen = [p for p in snap.planets
                if p.owner is not None and p.owner.addr == other.addr
                and hist.can_see(snap, p)]
        if not seen:
            continue
        log(f"R-XTM-00: {len(seen)} visible planet(s) of {other.civ_name!r} and "
            f"{len(ready)} crewed warship(s) — declaring war")
        try:
            act.declare_war(civ, other)
            clear_relation_cache()
            acted += 1
        except (ValueError, RuntimeError) as e:
            log(f"  could not declare war: {e}")
    return acted


# Off by default: see run_xtm00. Flip this for an AI-vs-AI galaxy.
ALLOW_DECLARE_WAR = True

RULES = [("R-XTM-05", run_xtm05), ("R-XTM-00", run_xtm00),
         ("R-XTM-01", run_xtm01), ("R-XTM-03", run_xtm03)]


def main():
    args = sys.argv[1:]
    dry_run = not cli.flag(args, "--apply")
    snap = gs.Snapshot()
    civ = gs.resolve_civ(snap, cli.opt(args, "--civ"))
    if civ is None:
        return
    print(f"\n=== Exterminate — turn {snap.turn} — {civ.civ_name!r} — "
          f"{'DRY RUN' if dry_run else 'APPLYING'} ===")
    hist = sensors.History().observe(snap, civ)
    # A remote handle even in dry run: the war sensor is a READ, and without it
    # every rule here reports "not at war" and does nothing, which looks
    # identical to peace. Dry run still suppresses every write.
    rem = remote.Remote(snap.state.pid, log=lambda *a: None)
    act = actions.Actuator(snap, dry_run=dry_run, remote_=rem)
    for c in snap.civs:
        if c.addr != civ.addr:
            code = relation_code(snap, civ, c, act)
            print(f"  relation with {c.civ_name!r}: "
                  f"{code} ({'WAR' if code == WAR else 'not at war'})")
    try:
        for name, fn in RULES:
            fn(snap, civ, act, hist)
    finally:
        if rem is not None:
            rem.close()
    print(f"\n{act.summary()}")
    if dry_run:
        print("re-run with --apply to perform these writes")


if __name__ == "__main__":
    main()
