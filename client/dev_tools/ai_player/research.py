"""
research.py — the technology table, and what a player is allowed to select
==========================================================================
R-XPL-04 used to pick "the lowest id not already completed". That is not a move
a human can make. The tech tree has prerequisites, the UI enforces them, and the
FIELD WE WRITE DOES NOT — so the AI selected Astro Engineering (id 2) on a civ
that had never researched Advanced Magnetism (id 12), and the engine happily
researched it. Caught in the client's own tech screen: Astro Engineering showing
complete above an unfinished prerequisite.

That is cheating by STRATEGY.md §1.1, and it is the interesting kind: not a
field a human cannot touch, but a legal field written to a value the UI would
have refused. The no-cheating rule covers both.

── Where this table comes from, and how far to trust it ───────────────────────
READ OUT OF THE RUNNING CLIENT by research_dump.py, which walks the 80 records at
[0x00857F08] -- the table the engine itself selected for this client's content
version. Regenerate with `python research_dump.py --emit`, check with
`python research_dump.py --diff`.

This replaced a table extracted statically from 0x00857F10, the copy used when
the content version is >= 688, on the theory that the two differed. They do not,
and the reasoning that said they did is worth recording because it was wrong in
an instructive way:

    cost = round(scale * costFactor * multiplier), so with multiplier fixed at 1
    the RATIO of two costs is independent of the unknown scale. Live, id 2 cost
    4800 and id 3 cost 2400, a ratio of 2.0, while the static table gave factors
    3.0 and 1.0, a ratio of 3.0. That looked like proof of drift.

The live table gives factors 3.0 and 1.0 too -- identical, along with all 80
names, all 80 factors and every exclusion list. The ratio argument was sound; its
premise was not. `multiplier` was marked *inferred* as 1 by the analysis that
produced it, and it is not 1.

── The cost model, SOLVED ─────────────────────────────────────────────────────

    cost = round(SCALE * costFactor) * (number of researches already completed)

CONFIRMED VERBATIM BY THE OFFICIAL MANUAL, which states it as
`Research-Cost = Difficulty-Factor * Number-Of-Already-Researched-Techs * 800`
(cosmicsupremacy.com wiki, manual/research-screen, via web.archive.org).
Derived independently first, then matched -- so the engine, four live
observations and the documentation all agree.

Recovered by asking the engine rather than guessing: GetScale (0x0054A6F0)
returns 800 on this game, and GetCost (0x0054B140) called with multiplier=1
returns round(800 * factor). The UI price is that figure times 4 on a civ with
exactly 4 completed researches. Checked against four independent observations,
two of them historical:

    Astro Engineering   factor 3.0, 2 completed -> 2400*2 =  4800  observed 4800
    Cold Fusion         factor 1.0, 3 completed ->  800*3 =  2400  observed 2400
    Advanced Networking factor 1.0, 4 completed ->  800*4 =  3200  observed 3200
    Particle Eng.       factor 8.0, 4 completed -> 6400*4 = 25600  observed 25600

TWO THINGS THE MANUAL ADDS THAT THE BINARY DID NOT.

1. "You are free to change the technology that you are researching at any time
   with no penalty. All of the research that your empire has accumulated will be
   reapplied to your new technology." So R-XPL-04 correcting an illegal topic
   mid-flight costs nothing -- the stockpile is never lost.

2. Selecting a tech DEEP in the tree is legal: "all prerequisite technologies
   will be researched automatically along the way, in the order of least cost
   technology first". That refines what our cheat actually was. Choosing Astro
   Engineering without Advanced Magnetism is a legal CLICK; what a human could
   never get is Astro Engineering COMPLETED while Advanced Magnetism is not,
   because the engine would have researched and charged for the chain first.
   selectable() is therefore slightly stricter than the UI -- it cannot express
   a multi-step goal -- but it is right about outcomes, which is what matters.

THE STRATEGIC CONSEQUENCE IS COUNTERINTUITIVE AND NOT YET ACTED ON. Because the
multiplier is the count at the moment of purchase, the same tech costs more the
later you buy it, and total spend over a planned set is minimised by buying the
HIGHEST-factor techs FIRST -- the opposite of the cheapest-first tiebreak
available() still uses. See the [ ] in STRATEGY.md 4.3 R-XPL-04.

   Note the engine's own auto-path takes the WORST order here: the manual says
   prerequisites are researched "least cost technology first", which pairs the
   smallest factors with the smallest multipliers and maximises the total. An
   AI that walks a chain deliberately, highest factor first, pays strictly less
   than one that names the deep target and lets the engine fill in.


Real differences between the two tables, for the record: two prerequisite lists
where the static extraction deduped a repeated entry (55, 59), and trait deltas
on four tier-3+ doctrines (54, 59, 62, 67). Nothing that touched a decision.


── The rules the tree imposes ─────────────────────────────────────────────────
* Prerequisites are AND for technologies and OR for doctrines. The engine picks
  the predicate on the record's EXCLUSION COUNT (0x0054B9E0), so "has exclusions"
  is the real class marker. An empty prerequisite list is satisfied either way.

* Doctrines come in mutually exclusive tiers. Taking one permanently forecloses
  its rivals, so ALLOW_DOCTRINES is off by default: a one-way door is not
  something a heuristic should walk through on "it was the cheapest".
* Ids 73-76 are empty stubs and must never be selected.
"""

# id: (name, costFactor, prerequisites (satisfy ANY), excluded-by ids)
# Live values, read from this client's own table. Regenerate with research_dump.py.
TECHS = {
     0: ('Nuclear', 0.0, (), ()),
     1: ('Space Travel', 0.0, (), ()),
     2: ('Astro Engineering', 3.0, (12,), ()),
     3: ('Cold Fusion', 1.0, (0,), ()),
     4: ('Tachyon Physics', 3.2, (13,), ()),
     5: ('Robotics Engineering', 15.0, (22,), ()),
     6: ('Gravity Controller', 9.0, (7,), ()),
     7: ('Quantum Fields', 2.8, (3, 12), ()),
     8: ('Anti-Matter Fission', 25.0, (15,), ()),
     9: ('Dark-Matter Control', 24.0, (41,), ()),
    10: ('Subspace Controller', 35.0, (9,), ()),
    11: ('Hyperspace Controller', 47.0, (10,), ()),
    12: ('Advanced Magnetism', 1.0, (1,), ()),
    13: ('Photon Mechanics', 2.0, (3,), ()),
    14: ('Plasma Physics', 11.0, (4,), ()),
    15: ('Proton Controller', 17.0, (14,), ()),
    16: ('Nano Mechanics', 34.0, (40,), ()),
    17: ('Superstring Theory', 51.0, (16,), ()),
    18: ('Advanced Networking', 1.0, (1,), ()),
    19: ('Artificial Intelligence', 14.0, (24,), ()),
    20: ('Advanced Tactics', 24.0, (19,), ()),
    21: ('Deep-Space Scanning', 32.0, (20,), ()),
    22: ('Particle Engineering', 8.0, (2,), ()),
    23: ('Basic Scanning', 3.6, (18,), ()),
    24: ('Advanced Scanning', 10.0, (23,), ()),
    25: ('Wormhole Controller', 49.0, (42,), ()),
    26: ('Magnetic Monopoles', 62.0, (25,), ()),
    27: ('Planetary Defense Lvl 1', 1.0, (), ()),
    28: ('Planetary Defense Lvl 2', 2.5, (27,), ()),
    29: ('Planetary Defense Lvl 3', 5.0, (28,), ()),
    30: ('Planetary Defense Lvl 4', 10.0, (29,), ()),
    31: ('Planetary Defense Lvl 5', 17.0, (30,), ()),
    32: ('Planetary Defense Lvl 6', 27.0, (31,), ()),
    33: ('Planetary Defense Lvl 7', 41.0, (32,), ()),
    34: ('Planetary Defense Lvl 8', 57.0, (33,), ()),
    35: ('Mining', 6.0, (2,), ()),
    36: ('Propaganda', 2.0, (18,), ()),
    37: ('Robo Mining', 17.0, (5, 35), ()),
    38: ('Bunker', 1.5, (27,), ()),
    39: ('Banking', 2.0, (18,), ()),
    40: ('Advanced Manufacturing', 22.0, (5,), ()),
    41: ('Anti-Matter Control', 16.0, (6,), ()),
    42: ('Dark-Matter Fission', 36.0, (8,), ()),
    43: ('Doctrine: Mobility', 1.0, (), (44, 45, 46)),
    44: ('Doctrine: Colonization', 1.0, (), (43, 45, 46)),
    45: ('Doctrine: Reproduction', 1.0, (), (43, 44, 46)),
    46: ('Doctrine: Metallurgy', 1.0, (), (43, 44, 45)),
    47: ('Doctrine: Knowledge', 2.7, (43, 44), (48, 49)),
    48: ('Doctrine: Manufacturing', 2.3, (43, 44, 45, 46), (47, 49)),
    49: ('Doctrine: Trade', 2.0, (45, 46), (47, 48)),
    50: ('Doctrine: Stealth', 6.5, (47, 48, 43), (54, 55, 56)),
    51: ('Planetary Fortress', 8.0, (29,), ()),
    52: ('Biological Warfare', 3.0, (13,), ()),
    53: ('Cloaking', 11.0, (24,), ()),
    54: ('Doctrine: Stronghold', 7.5, (47, 48), (50, 55, 56)),
    55: ('Doctrine: Finance', 7.0, (47, 48, 48, 49), (50, 54, 56)),
    56: ('Doctrine: Elite', 6.0, (48, 49), (50, 54, 55)),
    57: ('Doctrine: Intelligence', 15.0, (50, 54, 47), (58, 59, 60)),
    58: ('Doctrine: Versatility', 16.0, (50, 54), (57, 59, 60)),
    59: ('Doctrine: Propaganda', 15.5, (50, 54, 54, 55, 56), (57, 58, 60)),
    60: ('Doctrine: Juggernaut', 16.0, (54, 55, 56), (57, 58, 59)),
    61: ('Doctrine: Invisibility', 27.0, (50,), (62, 63, 64)),
    62: ('Doctrine: Habitat', 30.0, (57, 58, 59, 60), (61, 63, 64)),
    63: ('Doctrine: Police State', 29.0, (57, 58, 59, 60), (61, 62, 64)),
    64: ('Doctrine: Dreadnought', 28.0, (60,), (61, 62, 63)),
    65: ('Doctrine: Lightweight', 45.0, (61, 62, 63, 64), (66, 67, 68)),
    66: ('Doctrine: Bio-Armor', 47.0, (61, 62, 63, 64), (65, 67, 68)),
    67: ('Doctrine: Golden Age', 50.0, (61, 62, 63, 64), (65, 66, 68)),
    68: ('Doctrine: Titan', 46.0, (64,), (65, 66, 67)),
    69: ('Hyperspace-Energy Lvl 1', 9.0, (29,), ()),
    70: ('Hyperspace-Energy Lvl 2', 15.0, (69,), ()),
    71: ('Hyperspace-Energy Lvl 3', 25.0, (70,), ()),
    72: ('Hyperspace-Energy Lvl 4', 37.0, (71,), ()),
    77: ('Artificial Wormholes', 13.0, (19,), ()),
    78: ('Command Center', 20.0, (20,), ()),
    79: ('Surveillance Scanning', 19.0, (19,), ()),
}

STUBS = (73, 74, 75, 76)

# ── What each research actually GRANTS ────────────────────────────────────────
# Effect kinds, from the 13-way jump table at 0x0054ACBC. Each case writes a
# distinctive vftable constant, which is what pins the type. Kinds 7 and 8 have
# no case and fall through to the default.
FACILITY, CHASSIS, MODULE, WEAPON, ENGINE, SHIELD, SCANNER = 0, 1, 2, 3, 4, 5, 6
SCAN, PLANDEF, TRAIT, HYPENERGY = 9, 10, 11, 12

KIND_NAME = {
    FACILITY: "Facility", CHASSIS: "Chassis", MODULE: "Module",
    WEAPON: "Weapon", ENGINE: "Engine", SHIELD: "Shield",
    SCANNER: "Scanner", SCAN: "Scan", PLANDEF: "PlanetaryDefense",
    TRAIT: "Trait", HYPENERGY: "HyperspaceEnergy",
}

# The id spaces these land in are the ones already annotated:
#   Facility -> Owner:172 / Planet:204 / Planet:284      Chassis -> ShipDesign:128
#   Module   -> ShipDesign:224                           Weapon  -> ShipDesign:200
#   Engine   -> ShipDesign:176                           Scanner -> ShipDesign:152
#   Scan     -> Owner:200                                Trait   -> Owner:932+4*id
#
# The facility ids agree with observation: the report's confirmed 0 farm,
# 2 shipyard, 4 university, 6 military camp, 8 defence agency, 9 light turret
# all appear as Facility grants here, and the space runs to at least 20, not the
# 9 we knew.
#
# THIS TABLE IS THE FACILITY LEGALITY GATE. A civ may build a facility type only
# once it has completed a research that grants it. That is not obvious from
# observation, and it was got wrong once, expensively:
#
#   A civ with completed research {0, 1, 2, 3} -- which unlocks only facilities
#   {0, 1, 2} -- was seen with military camps (6) on three planets and a light
#   turret (9) on the homeworld. The wrong conclusion, drawn first, was "so
#   availability is not research-gated". The right one is that THE HOMEWORLD
#   SHIPS WITH ONE MILITARY CAMP AND ONE LIGHT TURRET as a starting grant, and
#   the camps on the other two planets were built by this AI through a memory
#   write the UI would have refused.
#
# A STANDING FACILITY IS PROOF OF NOTHING. It may be a starting grant, or it may
# be our own earlier cheat looking like evidence -- which is exactly how one
# illegal build laundered itself into a rule that authorised more of them.
#
# tid: ((kind, object id, value), ...)   value is only meaningful for TRAIT.
GRANTS = {
     0: ((6, 0, 0), (4, 0, 0)),
     1: ((1, 0, 0), (0, 0, 0), (2, 0, 0), (0, 2, 0)),
     2: ((0, 1, 0), (1, 1, 0), (2, 2, 0)),
     3: ((4, 1, 0), (3, 0, 0), (3, 10, 0)),
     4: ((3, 2, 0),),
     5: ((1, 3, 0),),
     6: ((4, 3, 0), (5, 2, 0)),
     7: ((4, 2, 0), (5, 1, 0)),
     8: ((3, 12, 0), (3, 7, 0)),
     9: ((4, 5, 0),),
    10: ((5, 3, 0), (4, 6, 0)),
    11: ((5, 4, 0),),
    12: ((5, 0, 0), (2, 1, 0)),
    13: ((3, 1, 0), (3, 4, 0)),
    14: ((3, 5, 0), (3, 11, 0)),
    15: ((3, 6, 0), (3, 3, 0)),
    16: ((1, 4, 0),),
    17: ((1, 5, 0),),
    18: ((0, 4, 0), (0, 6, 0)),
    19: ((0, 5, 0), (9, 6, 0)),
    20: ((0, 7, 0), (9, 4, 0), (6, 2, 0)),
    21: ((6, 3, 0), (9, 1, 0)),
    22: ((1, 2, 0),),
    23: ((0, 8, 0), (6, 1, 0), (9, 2, 0), (9, 3, 0)),
    24: ((9, 0, 0), (9, 5, 0)),
    25: ((3, 9, 0),),
    26: ((3, 14, 0),),
    27: ((10, 0, 0), (0, 9, 0)),
    28: ((10, 1, 0),),
    29: ((10, 2, 0), (0, 11, 0)),
    30: ((10, 3, 0), (0, 10, 0)),
    31: ((10, 4, 0),),
    32: ((10, 5, 0),),
    33: ((10, 6, 0),),
    34: ((10, 7, 0),),
    35: ((0, 13, 0),),
    36: ((0, 12, 0),),
    37: ((0, 14, 0),),
    38: ((0, 15, 0),),
    39: ((0, 16, 0),),
    40: ((0, 3, 0),),
    41: ((4, 4, 0),),
    42: ((3, 13, 0), (3, 8, 0)),
    43: ((11, 18, 20), (11, 10, -10)),
    44: ((11, 23, -50), (11, 33, -15)),
    45: ((11, 26, -30), (11, 0, -1)),
    46: ((11, 41, 50), (11, 5, 15), (11, 29, -30)),
    47: ((11, 28, -30), (11, 39, -7), (11, 13, 20)),
    48: ((11, 27, -30), (11, 3, 5)),
    49: ((11, 42, 50), (11, 37, 15), (11, 1, 4), (11, 6, 5), (11, 2, 5)),
    50: ((11, 12, 5), (11, 24, -75), (11, 17, 10), (11, 7, 15), (11, 19, -15)),
    51: ((0, 17, 0),),
    52: ((2, 4, 0),),
    53: ((2, 3, 0),),
    54: ((11, 30, -10), (11, 8, 15), (11, 38, 10), (11, 1, 2), (11, 9, -5)),
    55: ((11, 37, 75), (11, 5, -7)),
    56: ((11, 31, 70), (11, 9, 20), (11, 32, 1)),
    57: ((11, 18, 15), (11, 13, 30), (11, 12, 5), (11, 39, -5), (11, 25, -75), (11, 45, 1)),
    58: ((11, 40, 150), (11, 16, 5), (11, 14, -5), (11, 6, 5)),
    59: ((11, 35, 100), (11, 3, 7), (11, 4, 7)),
    60: ((11, 17, 25), (11, 15, -10)),
    61: ((11, 12, 10), (11, 16, 25), (11, 17, 5), (11, 19, -80), (11, 20, -50)),
    62: ((11, 38, 20), (11, 2, 7), (11, 30, -15), (11, 5, -15)),
    63: ((11, 34, 75), (11, 31, 50), (11, 7, 25)),
    64: ((11, 19, 30), (11, 17, 20)),
    65: ((11, 11, -25), (11, 15, -30)),
    66: ((11, 22, 7), (11, 17, 5), (11, 9, 10), (11, 8, 10)),
    67: ((11, 43, 100),),
    68: ((11, 20, 25), (11, 21, -5)),
    69: ((12, 0, 0), (0, 18, 0), (0, 19, 0)),
    70: ((12, 1, 0),),
    71: ((12, 2, 0),),
    72: ((12, 3, 0),),
    77: ((2, 5, 0),),
    78: ((0, 20, 0),),
    79: ((9, 7, 0),),
}

# The only two trait slots pinned to a meaning so far. Both pin themselves by
# agreeing with the doctrine that grants them, which is decent evidence:
# GetSpeed (0x005480A0) applies traits 6 and 18, and Doctrine: Mobility grants
# trait 18 +20; the PlanetProperties space getter (0x004F1140) applies trait 38,
# and Doctrine: Habitat grants trait 38 +20.
TRAIT_NAME = {18: "ship speed %", 38: "planet space %"}

# Facility ids we have confirmed by observation. The rest of the space (up to
# 20) is granted by the table but not yet identified in the UI.
FACILITY_NAME = {0: "farm", 2: "shipyard", 4: "university", 6: "military camp",
                 8: "defence agency", 9: "light turret"}

# A doctrine choice is permanent and locks out its whole tier. Off by default.
ALLOW_DOCTRINES = False


def name(tid):
    rec = TECHS.get(tid)
    return rec[0] if rec else f"id {tid}"


def is_doctrine(tid):
    return name(tid).startswith("Doctrine:")


def is_doctrine_record(tid):
    """The engine's own test: a record with exclusions is a doctrine.

    0x0054B9E0 branches on `cmp dword [rec+0xF8], 0` -- the exclusion count --
    and runs two different predicates. That, not the name, is the real class
    marker, so this uses the same signal rather than matching on "Doctrine:".
    """
    rec = TECHS.get(tid)
    return bool(rec) and bool(rec[3])


def prereqs_met(tid, done):
    """AND for technologies, OR for doctrines.

    This is NOT a global OR, which is what this module assumed until Round 3 of
    the static analysis decoded 0x0054B9E0. Getting it wrong made the gate too
    PERMISSIVE: Quantum Fields lists [3, 12] and needs BOTH, but an OR reading
    called it selectable on a civ holding only Cold Fusion.

    That error was visible in the game and we nearly explained it away. The UI
    priced Quantum Fields at 3200 rather than the 8960 its own factor implies,
    because it had silently built a plan and quoted the cheapest legal step --
    Advanced Magnetism at 800 x 1.0 x 4. The number was evidence of the AND, and
    it was briefly attributed to a misread instead.

    An EMPTY list is satisfied either way. The disassembly of the OR branch
    reads as though zero prerequisites falls through to FALSE, but the tier-1
    doctrines (43-46) have no prerequisites and are demonstrably selectable from
    turn one, so an empty list cannot mean "never". Treated as vacuously true,
    and flagged here because it is the one place this function departs from a
    literal reading of the bytes.
    """
    rec = TECHS.get(tid)
    if rec is None:
        return False
    if not rec[2]:
        return True
    if is_doctrine_record(tid):
        return any(p in done for p in rec[2])
    return all(p in done for p in rec[2])


def locked_out(tid, done):
    """A mutually exclusive rival has already been taken."""
    rec = TECHS.get(tid)
    return bool(rec) and any(x in done for x in rec[3])


def selectable(tid, done, allow_doctrines=ALLOW_DOCTRINES):
    """Could a human click this topic right now?"""
    if tid in done or tid in STUBS or tid not in TECHS:
        return False
    if not allow_doctrines and is_doctrine(tid):
        return False
    return prereqs_met(tid, done) and not locked_out(tid, done)


# ── What the AI should want ───────────────────────────────────────────────────
# THIS IS A POLICY, NOT A FINDING. Nothing in the binary says an engine is worth
# more than a weapon; that judgement comes from what this AI currently does. It
# expands, exploits and explores, and it has no combat rules at all, so:
#
#   * ENGINE is the single highest-value kind. Speed shortens every colonisation
#     hop, and expansion rate is what the score actually tracks.
#   * FACILITY is next, but only for the six ids we can recognise -- an unknown
#     facility is a guess, and R-XPL-02 would not know to build it anyway.
#   * SCANNER and SCAN serve Explore, which is the real bottleneck under fog of
#     war, and they do it legitimately.
#   * CHASSIS and MODULE are mid: bigger hulls and more module slots, both of
#     which feed better colony ships, but the mapping is not yet pinned.
#   * WEAPON, SHIELD and PLANDEF score low in the BASE table, but weapons are
#     no longer optional: Exterminate exists, and a design cannot carry a
#     weapon the civ has not researched. A civ with no unlocked weapon
#     cannot build an armed ship at all, however much it wants one. That is
#     handled by a BOOST rather than by raising the base weight, because it
#     is a threshold and not a preference -- the first weapon is critical
#     and the fifth is not.
KIND_VALUE = {
    ENGINE: 10, FACILITY: 6, SCANNER: 5, SCAN: 5, CHASSIS: 4, MODULE: 4,
    HYPENERGY: 2, SHIELD: 1, WEAPON: 1, PLANDEF: 1, TRAIT: 3,
}
KNOWN_FACILITY_BONUS = 4      # a facility we can actually recognise and build


def grants(tid):
    return GRANTS.get(tid, ())


def describe_grants(tid):
    """Human-readable 'what you get', for the decision log."""
    out = []
    for kind, oid, val in grants(tid):
        if kind == TRAIT:
            label = TRAIT_NAME.get(oid, f"trait {oid}")
            out.append(f"{label} {val:+d}")
        elif kind == FACILITY and oid in FACILITY_NAME:
            out.append(FACILITY_NAME[oid])
        else:
            out.append(f"{KIND_NAME.get(kind, kind)} {oid}")
    return ", ".join(out) or "nothing"


def unlocked(done, kind):
    """The object ids of a given kind this civ has actually unlocked.

    A part can only be fitted to a design once some completed research grants
    it, so this is what decides whether an armed design is even legal. A fresh
    civ holds chassis 0, scanner 0, engine 0 and module 0 from the two granted
    starting techs and NO weapon at all — the first weapon comes with Cold
    Fusion (id 3).
    """
    return {oid for t in done for k, oid, _v in grants(t) if k == kind}


def value(tid, boost=None):
    """How much this AI wants a tech, given what it currently knows how to do."""
    total = 0
    for kind, oid, _val in grants(tid):
        total += KIND_VALUE.get(kind, 0) + (boost or {}).get(kind, 0)
        if kind == FACILITY and oid in FACILITY_NAME:
            total += KNOWN_FACILITY_BONUS
    return total


def unlocked_by(kind, obj_id):
    """Which research grants a given object — 'how do I get a better engine'."""
    return [t for t in sorted(TECHS)
            if any(k == kind and o == obj_id for k, o, _v in grants(t))]


def available(done, allow_doctrines=ALLOW_DOCTRINES, by_value=True,
              boost=None):
    """Every legal pick, best first.

    Ordered by what the tech GRANTS, then by price as a tiebreak, then by id so
    the choice is deterministic. Price is the live costFactor, but the COST
    MODEL is unsolved (see the header), which is why price is only a
    tiebreak rather than driving the choice as it used to.
    """
    ok = [t for t in sorted(TECHS) if selectable(t, done, allow_doctrines)]
    if not by_value:
        return sorted(ok, key=lambda t: (TECHS[t][1], t))
    return sorted(ok, key=lambda t: (-value(t, boost), TECHS[t][1], t))


def explain(tid, done):
    """Why a topic is not selectable — for logging a correction."""
    if tid in STUBS:
        return f"id {tid} is an unused stub"
    if tid not in TECHS:
        return f"id {tid} is not in the tech table"
    if tid in done:
        return f"{name(tid)} is already complete"
    if locked_out(tid, done):
        rival = next(x for x in TECHS[tid][3] if x in done)
        return f"{name(tid)} is locked out by {name(rival)}"
    if not prereqs_met(tid, done):
        req = TECHS[tid][2]
        if is_doctrine_record(tid):
            need = ", ".join(name(p) for p in req)
            return f"{name(tid)} needs any one of: {need}"
        missing = ", ".join(name(p) for p in req if p not in done)
        return f"{name(tid)} needs ALL of {[name(p) for p in req]}; missing {missing}"
    return f"{name(tid)} is selectable"

# GetScale() 0x0054A6F0 on this game. It is a per-game config value
# (0x004D4320(0x15)->[+0x28] on a modern client), so a different galaxy may use a
# different number -- re-read it rather than assuming 800 holds everywhere.
COST_SCALE = 800


def cost(tid, completed_count):
    """What this research costs RIGHT NOW.

    cost = round(SCALE * costFactor) * completed_count. The multiplier is the
    number of researches already completed, so every tech gets more expensive as
    the game goes on and the price depends on WHEN it is bought, not just what
    it is.
    """
    rec = TECHS.get(tid)
    if rec is None:
        return None
    return round(COST_SCALE * rec[1]) * completed_count
