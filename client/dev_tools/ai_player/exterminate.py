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
