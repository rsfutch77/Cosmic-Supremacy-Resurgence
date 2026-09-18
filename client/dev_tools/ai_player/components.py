"""
components.py , the ship-component definition tables
====================================================
    python components.py          # dump every category from the live client

Six categories, one table each, read out of a running client the same way
facilities.py reads the facility table. The tables are zero in the file image and
built at startup, so this needs a live process; it is a plain ReadProcessMemory
and needs no engine call.

Like the facility table, each category ships SEVERAL tables and the engine picks
one by content version at [0x0080AA00]. Every released mode runs a version below
639 (Tutorial and Demo at 171, Sandbox and Single Player at 565), and every one of
the six pickers returns the same branch for those: the table named RELEASED below.
See docs/CosmicSupremacy_Stat_Tables.md section 4a for the mechanism, and OLDER
for where the superseded stat sets live if anyone ever needs one.

Names live in the record, so no hand-maintained name table is needed. The ids are
the record's position, which is what a ShipDesign's part vectors store.

── Field map ──────────────────────────────────────────────────────────────────
Shared by every category:
    +0x00  id, the record's own index
    +0x04  units, the hitpoint contribution
    +0x0C  production cost
    +0x10  upkeep per turn, a FLOAT where every other field is an int
Then the category-specific block, and five int resource costs in the canonical
order metal, deuterium, radioactives, crystal, exotics.

    engines / scanners / shields (stride 0x8C)
        +0x08  space used          +0x14  thrust / range / shield strength
        +0x18  resources           +0x30  name
    weapons (stride 0x94)
        +0x08  space used          +0x14  firepower vs light ships
        +0x18  firepower vs heavy  +0x1C  firepower vs planets
        +0x20  resources           +0x38  name
    chassis (stride 0x94)
        +0x14  space PROVIDED, not used , a chassis is the hull
        +0x18  crew minimum        +0x1C  crew maximum
        +0x20  resources           +0x38  name
    modules (stride 0xA8)
        +0x08  space used          +0x14  resources          +0x4C  name

Checked against the wiki manual across all 43 records. Five numbers disagree and
the client wins on all five; see the module docstring note in main().
"""
import struct
import sys

import gamestate as gs

CONTENT_VER = 0x0080AA00
OPTION_BRANCH_MIN = 639

RESOURCES = ("metal", "deuterium", "radioactives", "crystal", "exotics")

# category -> (released base, stride, count, name offset, resource offset, kind)
CATEGORIES = {
    "chassis":  (0x0080B5A8, 0x94, 6,  0x38, 0x20, "chassis"),
    "engines":  (0x0080D168, 0x8C, 7,  0x30, 0x18, "stat"),
    "modules":  (0x0080E878, 0xA8, 6,  0x4C, 0x14, "plain"),
    "scanners": (0x0080F838, 0x8C, 4,  0x30, 0x18, "stat"),
    "shields":  (0x00810328, 0x8C, 5,  0x30, 0x18, "stat"),
    "weapons":  (0x00811BE8, 0x94, 15, 0x38, 0x20, "weapon"),
}

# The superseded tables, newest first, for each category. Not read by anything
# here: they belong to content versions whose galaxies are long gone. Recorded so
# that a stat set can be recovered without repeating the search. The picker
# addresses are the authority on which version selects which.
OLDER = {
    "chassis":  (0x0080B920, 0x0080BC98, 0x0080C010, 0x0080C388,
                 0x0080C700, 0x0080CA78, 0x0080CDF0),
    "engines":  (0x0080D540, 0x0080D918, 0x0080DCF0, 0x0080E0C8, 0x0080E4A0),
    "modules":  (0x0080EC68, 0x0080F058, 0x0080F448),
    "scanners": (0x0080FA68, 0x0080FC98, 0x0080FEC8, 0x008100F8),
    "shields":  (0x008105E8, 0x008108A8, 0x00810E28, 0x008110E8,
                 0x008113A8, 0x00811668, 0x00811928),
    "weapons":  (0x00812498, 0x00812D48, 0x00813EA8),
}

PICKERS = {
    "chassis": 0x0055E630, "engines": 0x0055F080, "modules": 0x0055F520,
    "scanners": 0x0055FE20, "shields": 0x00560336, "weapons": 0x00560A01,
}

STAT_NAME = {"engines": "thrust", "scanners": "range", "shields": "strength"}


def content_version(snap):
    b = snap.read(CONTENT_VER, 4)
    if not b or len(b) != 4:
        return None
    return struct.unpack("<i", b)[0]


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


def read_table(snap, category, base=None):
    """Every record of one component category, keyed by the id a design stores.

    Pass an explicit base only to inspect one of the OLDER tables.
    """
    released, stride, count, noff, roff, kind = CATEGORIES[category]
    if base is None:
        base = released
    blob = snap.read(base, count * stride)
    if not blob or len(blob) < count * stride:
        return {}
    out = {}
    for i in range(count):
        r = blob[i * stride:(i + 1) * stride]
        g = lambda o: struct.unpack_from("<i", r, o)[0]
        rec = {
            "id": i,
            "name": read_name(snap, base + i * stride + noff),
            "units": g(0x04),
            "cost": g(0x0C),
            "upkeep": struct.unpack_from("<f", r, 0x10)[0],
            "resources": {RESOURCES[k]: g(roff + 4 * k) for k in range(5)},
        }
        if kind == "chassis":
            rec["space_provided"] = g(0x14)
            rec["crew"] = (g(0x18), g(0x1C))
        else:
            rec["space"] = g(0x08)
        if kind == "weapon":
            rec["firepower"] = (g(0x14), g(0x18), g(0x1C))
        elif kind == "stat":
            rec[STAT_NAME[category]] = g(0x14)
        out[i] = rec
    return out


def read_all(snap):
    return {cat: read_table(snap, cat) for cat in CATEGORIES}


def main():
    snap = gs.Snapshot()
    ver = content_version(snap)
    print(f"content version {ver}")
    if ver is not None and ver >= OPTION_BRANCH_MIN:
        print("  [!] version >= 639: the engine may select the GetGameOption(3)")
        print("      branch instead, which this module does not read. Every")
        print("      released mode runs below 639.")
    print()
    for cat in sorted(CATEGORIES):
        base = CATEGORIES[cat][0]
        tbl = read_table(snap, cat)
        print(f"== {cat}  table 0x{base:08X}, {len(tbl)} records ==")
        for i, r in sorted(tbl.items()):
            res = " ".join(f"{k[:5]}={v}" for k, v in r["resources"].items() if v)
            if cat == "chassis":
                extra = (f"provides={r['space_provided']:5d} "
                         f"crew={r['crew'][0]}-{r['crew'][1]}")
            elif cat == "weapons":
                extra = "fp=" + "/".join(str(x) for x in r["firepower"])
            else:
                key = STAT_NAME.get(cat)
                extra = f"space={r['space']:4d}"
                if key:
                    extra += f" {key}={r[key]}"
            print(f"  {i:2d} {r['name']:22s} units={r['units']:5d} "
                  f"cost={r['cost']:6d} upkeep={r['upkeep']:5.0f} {extra}  {res}")
        print()


if __name__ == "__main__":
    main()
