"""
facilities.py — the facility definition table
=============================================
    python facilities.py          # dump the live table

21 records of 0x8C bytes, selected by content version at [0x0080AA00] exactly the
way the research table is. For version 565 the base is 0x00807DB0. The table is
BSS — zero in the file image, built at startup — so it must be read from a
running client, which is a plain ReadProcessMemory and needs no engine call.

21 entries is exactly facility ids 0..20, which independently matches the id
range implied by the research grants table (research effect kind 0). Two tables
derived separately agreeing on the same range is decent evidence for both.

── Field map ──────────────────────────────────────────────────────────────────
    +0x04  defence class; NON-ZERO means this facility contributes to planetary
           defence. The battle code (0x0044BB10) walks the planet's built-facility
           map and skips every record whose +0x04 is 0.
    +0x0C  defence strength component 1, scaled by (defenceTechBonus + 100) / 100
    +0x10  defence strength component 2, same scaling
    +0x14  the PRODUCTION cost to build one, halved when GetGameOption(1) is set
    +0x1C  the value CanBuildFacility compares against something from 0x004F3100

WHAT +0x14 IS, AND THE INFERENCE THAT WAS WRONG. Static analysis read this field
as per-turn UPKEEP, on decent-looking evidence: it is reached through a different
accessor than the build gate, it is halvable by a difficulty-style option, and
the UI has a distinct "Facilities-Upkeep" line. This module repeated that
reading. It is wrong, and two live observations settle it:

  * a planet building facility type 6 (military camp) showed a production total
    of 1500, and +0x14 for type 6 is 1500;
  * a planet building its first farm showed a production total of 200, and
    +0x14 for type 0 is 200.

Exact matches on two different types. +0x14 is what a facility COSTS TO BUILD in
production — the same quantity the PlanetProperties total-production virtual
returns while it is under construction.

FACILITY UPKEEP IS THEREFORE STILL UNLOCATED, and R-XPL-07 is still blocked on
it. Ranking liquidation candidates by +0x14 would rank them by build cost, which
is a different ordering, and would confidently sell the wrong buildings. The
"Facilities-Upkeep" UI line and the warning that unpaid upkeep makes buildings
"get lost" both exist, so the number is real and computed somewhere; turn stages
42 (0x00543F70) and 49 (0x0052D0E0) remain the candidates.

+0x1C is NOT known to be a cash cost. An earlier round claimed the build gate
checked "cost <= treasury"; that was withdrawn. Builds consume PRODUCTION
accumulated in Planet:120 (measured live: cash never moves across a whole build)
and cash enters only through hurrying. The small values here — farm 7, shipyard
25, military camp 14, defence agency 26 — look nothing like production totals,
which run 200..2900 on the same planets. They are plausibly a required
POPULATION, since they rise with how substantial the building is, but that is a
guess and is marked as one. Do not gate anything on +0x1C until 0x004F3100 is
read.
"""
import struct
import sys

import gamestate as gs

TABLE_565 = 0x00807DB0
STRIDE = 0x8C
COUNT = 21
CONTENT_VER = 0x0080AA00

OFF_DEFENCE_CLASS = 0x04
OFF_STRENGTH_1 = 0x0C
OFF_STRENGTH_2 = 0x10
OFF_BUILD_COST = 0x14      # production cost, NOT upkeep - see the header
OFF_BUILD_GATE = 0x1C

# Confirmed by observation in the running game; the rest of 0..20 is unnamed.
NAMES = {0: "farm", 2: "shipyard", 4: "university", 6: "military camp",
         8: "defence agency", 9: "light turret"}


def read_table(snap, base=TABLE_565):
    raw = snap.read(base, COUNT * STRIDE)
    if not raw or len(raw) < COUNT * STRIDE:
        return {}
    out = {}
    for i in range(COUNT):
        r = raw[i * STRIDE:(i + 1) * STRIDE]
        out[i] = {
            "id": i,
            "name": NAMES.get(i),
            "defence_class": struct.unpack_from("<i", r, OFF_DEFENCE_CLASS)[0],
            "strength_1": struct.unpack_from("<i", r, OFF_STRENGTH_1)[0],
            "strength_2": struct.unpack_from("<i", r, OFF_STRENGTH_2)[0],
            "build_cost": struct.unpack_from("<i", r, OFF_BUILD_COST)[0],
            "build_gate": struct.unpack_from("<i", r, OFF_BUILD_GATE)[0],
        }
    return out


def build_cost(snap, type_id, halved=False):
    """Production needed to build one of these.

    Confirmed against two live builds; see the header for why this is not
    upkeep.
    """
    rec = read_table(snap).get(type_id)
    if rec is None:
        return None
    return rec["build_cost"] // (2 if halved else 1)


def is_defensive(snap, type_id):
    rec = read_table(snap).get(type_id)
    return bool(rec) and rec["defence_class"] != 0


def planet_build_value(snap, planet, halved=False):
    """Total production already sunk into this planet's buildings.

    NOT an upkeep figure, and R-XPL-07 wants upkeep. Kept because "what did this
    planet cost to build" is a fair proxy for how much is at stake on it, and
    because it is what the table actually contains.
    """
    tbl = read_table(snap)
    total, per = 0, {}
    for tid, count in planet.facilities.items():
        rec = tbl.get(tid)
        if rec is None:
            continue
        cost = rec["build_cost"] // (2 if halved else 1) * count
        per[tid] = cost
        total += cost
    return total, per


def main():
    snap = gs.Snapshot()
    ver = struct.unpack("<i", snap.read(CONTENT_VER, 4))[0]
    tbl = read_table(snap)
    print(f"content version {ver}, table 0x{TABLE_565:08X}, {len(tbl)} records\n")
    print(f"{'id':>3} {'name':16s} {'defence':>8} {'str1':>6} {'str2':>6} "
          f"{'build':>7} {'+1C':>5}")
    for i, r in sorted(tbl.items()):
        print(f"{i:3d} {(r['name'] or ''):16s} {r['defence_class']:8d} "
              f"{r['strength_1']:6d} {r['strength_2']:6d} {r['build_cost']:7d} "
              f"{r['build_gate']:5d}")
    civ = gs.resolve_civ(snap, None)
    if civ is None:
        return
    print(f"\nproduction sunk into buildings for {civ.civ_name!r}:")
    grand = 0
    for p in snap.owned_planets(civ):
        tot, per = planet_build_value(snap, p)
        grand += tot
        named = {NAMES.get(k, k): v for k, v in per.items()}
        print(f"  #{p.id:4d} {tot:7d} production  {named}")
    print(f"  empire total: {grand} production")


if __name__ == "__main__":
    main()
