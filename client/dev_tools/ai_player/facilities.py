"""
facilities.py , the facility definition table
=============================================
    python facilities.py          # dump the live table

21 records of 0x8C bytes. The tables are zero in the file image and built at
startup, so they must be read from a running client, which is a plain
ReadProcessMemory and needs no engine call.

EIGHT such tables ship in the binary and their stats genuinely differ. The engine
picks one by content version at [0x0080AA00]; see pick_table below. Reading a
fixed base is wrong: this module used to hardcode 0x00807DB0, which serves
content versions 57..645, while a client reporting 99999 is served 0x008066B0.

21 entries is exactly facility ids 0..20, which independently matches the id
range implied by the research grants table (research effect kind 0). Two tables
derived separately agreeing on the same range is decent evidence for both.

── Field map ──────────────────────────────────────────────────────────────────
    +0x04  planetary-defence hitpoints this facility contributes, the manual's
           "Units". NON-ZERO therefore also means the facility is defensive, which
           is how the battle code (0x0044BB10) uses it: it walks the planet's
           built-facility map and skips every record whose +0x04 is 0.
    +0x0C  defence strength component 1, light firepower, scaled by
           (defenceTechBonus + 100) / 100
    +0x10  defence strength component 2, heavy firepower, same scaling
    +0x14  the PRODUCTION cost to build one, halved when GetGameOption(1) is set
    +0x18  upkeep per turn
    +0x1C  planet space this facility occupies. CanBuildFacility compares it
           against the planet's free space from 0x004F3100.
    +0x20  the facility's primary bonus magnitude, in whatever unit its effect
           uses: percent for the output and defence boosters, TW for the
           hyperspace transmitter.
    +0x24  1 when only one may be built per planet, 0 otherwise. These are the
           facilities exempt from the build-cost escalation formula.
    +0x08  shield strength percent; non-zero only on the shield generator.
    +0x30  name, MSVC std::string. +0x50 holds the same name without spaces.

Every one of these was confirmed against the manual's stat tables across all 21
records; see docs/CosmicSupremacy_Stat_Tables.md. The names come out of the table
itself, so NAMES below is only a fallback for a read that fails.

"""
import struct
import sys

import gamestate as gs

STRIDE = 0x8C
COUNT = 21
CONTENT_VER = 0x0080AA00

OFF_DEFENCE_UNITS = 0x04
OFF_SHIELD_PCT = 0x08
OFF_STRENGTH_1 = 0x0C
OFF_STRENGTH_2 = 0x10
OFF_BUILD_COST = 0x14
OFF_UPKEEP = 0x18
OFF_SPACE = 0x1C
OFF_BONUS = 0x20
OFF_ONCE_PER_PLANET = 0x24
OFF_NAME = 0x30

# The engine's picker lives at 0x00529CB0 and returns base + id * 0x8C after
# walking this ladder on [0x0080AA00]. Ported as (exclusive_upper_bound, base),
# last entry open-ended.
TABLE_LADDER = ((8, 0x0080A370), (10, 0x00809CE0), (13, 0x00809650),
                (57, 0x00808FC0), (646, 0x00807DB0), (None, 0x008066B0))

# Two branches of the original ladder are deliberately not ported. A helper at
# 0x0052A790 returning false selects 0x00808930, and content version >= 639 with
# GetGameOption(3) set selects 0x00807230. The latter is byte-identical to
# 0x008066B0 over every field read here, so it cannot change an answer. The
# former is an older stat set that a modern galaxy does not reach.
TABLE_HELPER_FALSE = 0x00808930
TABLE_OPTION3 = 0x00807230


def content_version(snap):
    """The galaxy's content version. Selects which stat table the engine uses."""
    b = snap.read(CONTENT_VER, 4)
    if not b or len(b) != 4:
        return None
    return struct.unpack("<i", b)[0]


def pick_table(snap):
    """The base address the engine would use, for this client's content version.

    Returns (base, version). Falls back to the newest table when the version
    cannot be read, which is what a modern galaxy uses.
    """
    ver = content_version(snap)
    if ver is None:
        return TABLE_LADDER[-1][1], None
    for bound, base in TABLE_LADDER:
        if bound is None or ver < bound:
            return base, ver
    return TABLE_LADDER[-1][1], ver


def read_name(snap, addr):
    """MSVC std::string: 16-byte buffer or pointer, _Mysize +0x10, _Myres +0x14."""
    raw = snap.read(addr, 0x18)
    if not raw or len(raw) < 0x18:
        return ""
    size, res = struct.unpack_from("<II", raw, 0x10)
    if size > 4096:
        return ""
    if res > 15:
        ptr = struct.unpack_from("<I", raw, 0)[0]
        return (snap.read(ptr, size) or b"").decode("latin-1")
    return raw[:size].decode("latin-1")

# The full id space, complete. Six ids were confirmed by observation in the
# running game; the rest were resolved from the research grants table (which
# names the tech that unlocks each id) and then checked against this table's own
# build_cost and space, both of which match the manual's stat tables exactly for
# all 21 records. See docs/CosmicSupremacy_Stat_Tables.md.
NAMES = {0: "farm", 1: "factory", 2: "shipyard", 3: "automated factory",
         4: "university", 5: "science lab", 6: "military camp",
         7: "military academy", 8: "defence agency", 9: "light turret",
         10: "heavy turret", 11: "shield generator", 12: "propaganda office",
         13: "mine", 14: "robo mine", 15: "bunker", 16: "banking center",
         17: "planetary fortress", 18: "hyperspace transmitter",
         19: "hyperspace receiver", 20: "command center"}


def read_table(snap, base=None):
    """The facility definition table the ENGINE is using, not a fixed address.

    Pass an explicit base only to inspect a table the current content version
    does not select.
    """
    if base is None:
        base, _ver = pick_table(snap)
    raw = snap.read(base, COUNT * STRIDE)
    if not raw or len(raw) < COUNT * STRIDE:
        return {}
    out = {}
    for i in range(COUNT):
        r = raw[i * STRIDE:(i + 1) * STRIDE]
        out[i] = {
            "id": i,
            "name": read_name(snap, base + i * STRIDE + OFF_NAME).lower()
                    or NAMES.get(i),
            "defence_units": struct.unpack_from("<i", r, OFF_DEFENCE_UNITS)[0],
            "shield_pct": struct.unpack_from("<i", r, OFF_SHIELD_PCT)[0],
            "strength_1": struct.unpack_from("<i", r, OFF_STRENGTH_1)[0],
            "strength_2": struct.unpack_from("<i", r, OFF_STRENGTH_2)[0],
            "build_cost": struct.unpack_from("<i", r, OFF_BUILD_COST)[0],
            "upkeep": struct.unpack_from("<i", r, OFF_UPKEEP)[0],
            "space": struct.unpack_from("<i", r, OFF_SPACE)[0],
            "bonus": struct.unpack_from("<i", r, OFF_BONUS)[0],
            "once_per_planet": bool(
                struct.unpack_from("<i", r, OFF_ONCE_PER_PLANET)[0]),
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
    return bool(rec) and rec["defence_units"] != 0


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
    base, ver = pick_table(snap)
    tbl = read_table(snap, base)
    print(f"content version {ver}, table 0x{base:08X}, "
          f"{len(tbl)} records")
    print()
    print(f"{'id':>3} {'name':24s} {'units':>6} {'lightFP':>7} {'heavyFP':>7} "
          f"{'build':>6} {'upkeep':>6} {'space':>5} {'bonus':>6} {'1/planet':>8}")
    for i, r in sorted(tbl.items()):
        print(f"{i:3d} {(r['name'] or ''):24s} {r['defence_units']:6d} "
              f"{r['strength_1']:7d} {r['strength_2']:7d} {r['build_cost']:6d} "
              f"{r['upkeep']:6d} {r['space']:5d} {r['bonus']:6d} "
              f"{('yes' if r['once_per_planet'] else ''):>8}")
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
