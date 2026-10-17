"""
facilities.py: the facility definition table.

    python facilities.py          # dump the live table

21 records (ids 0..20) of 0x8C bytes, built at startup, so read from a running
client. Eight tables ship; the engine picks one by content version at
[0x0080AA00] (see pick_table).

Field map:
    +0x04  planetary-defence hitpoints ("Units"); non-zero means defensive
    +0x08  shield strength percent (shield generator only)
    +0x0C  light firepower, scaled by (defenceTechBonus + 100) / 100
    +0x10  heavy firepower, same scaling
    +0x14  production cost to build one, halved when GetGameOption(1) is set
    +0x18  upkeep per turn
    +0x1C  planet space occupied
    +0x20  primary bonus magnitude (percent, or TW for the transmitter)
    +0x24  1 when only one may be built per planet
    +0x30  name, MSVC std::string; +0x50 holds it without spaces
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

# The engine's picker (0x00529CB0) on [0x0080AA00], as
# (exclusive_upper_bound, base); the last entry is open-ended.
TABLE_LADDER = ((8, 0x0080A370), (10, 0x00809CE0), (13, 0x00809650),
                (57, 0x00808FC0), (646, 0x00807DB0), (None, 0x008066B0))

# Picker branches not ported: helper 0x0052A790 false selects 0x00808930 (an
# older stat set); version >= 639 with GetGameOption(3) selects 0x00807230,
# identical to 0x008066B0 over the fields read here.
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

    Returns (base, version); the newest table when the version is unreadable.
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

# Fallback names, used when the table's own name read fails.
NAMES = {0: "farm", 1: "factory", 2: "shipyard", 3: "automated factory",
         4: "university", 5: "science lab", 6: "military camp",
         7: "military academy", 8: "defence agency", 9: "light turret",
         10: "heavy turret", 11: "shield generator", 12: "propaganda office",
         13: "mine", 14: "robo mine", 15: "bunker", 16: "banking center",
         17: "planetary fortress", 18: "hyperspace transmitter",
         19: "hyperspace receiver", 20: "command center"}


def read_table(snap, base=None):
    """The facility definition table the engine is using, or the one at `base`."""
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
    """Production needed to build one of these."""
    rec = read_table(snap).get(type_id)
    if rec is None:
        return None
    return rec["build_cost"] // (2 if halved else 1)


def is_defensive(snap, type_id):
    rec = read_table(snap).get(type_id)
    return bool(rec) and rec["defence_units"] != 0


def planet_build_value(snap, planet, halved=False):
    """Total production already sunk into this planet's buildings.

    Not an upkeep figure (R-XPL-07 wants upkeep). Returns (total, {typeId: cost}).
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
