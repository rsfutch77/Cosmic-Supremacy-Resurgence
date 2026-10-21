"""
intel.py: what a civ has been told about enemy strength (STRATEGY.md §1.1)

A player sees no enemy firepower, hitpoints, planetary defence or troops by
selecting a ship or planet. Only a scan or a battle report reveals them, so
enemy strength is read from the civ's own battle reports and never from the
enemy's objects.

    python intel.py --civ DemoPlayer      # print every battle report parsed

Battle reports are news items in `Owner:344/348/352`, a std::vector of 120-byte
records. Each side of a battle receives its own copy.

    +0    kind, 2 for a battle report
    +8    turn the battle resolved
    +16   title text pointer, size at +36
    +48   report body pointer, size at +68: marked-up text, one block per side
    +108  position XYZ, float

A planet our ship sits at, undamaged, at war, with no report that turn, has no
planetary defence: the turrets fire at any hostile ship in orbit.
"""
import re
import struct
import sys

import cli
import gamestate as gs

NEWS_VECTOR = 344
NEWS_RECORD = 120
KIND_BATTLE = 2
OFF_KIND, OFF_TURN = 0, 8
OFF_TITLE, OFF_TITLE_SIZE = 16, 36
OFF_BODY, OFF_BODY_SIZE = 48, 68
OFF_POS = 108
MAX_TEXT = 64 * 1024       # bytes; a report is a few KB

AT_PLANET = 1.0            # a report this close to a planet is about it
MAX_AGE = 40               # turns; older intel is treated as unknown

# One block per side and topic: "<1>Name\t<-NN>\n<rrggbb|id>Name\n<default>..."
BLOCK = re.compile(r"<1>([^\t\n]+)\t<-?\d+>\n<[0-9a-fA-F]{6}\|\d+>\1\n"
                   r"<default>([^\t\n:]*):")
NUMBER = r"([\d,]+)"


def _num(text, pattern, default=0):
    m = re.search(pattern, text)
    return int(m.group(1).replace(",", "")) if m else default


def parse_body(body):
    """{side name: {topic: values}} from a report body. Lines end in CR."""
    body = body.replace("\r", "\n")
    sides = {}
    marks = list(BLOCK.finditer(body))
    for i, m in enumerate(marks):
        name, topic = m.group(1), m.group(2).strip()
        text = body[m.end():marks[i + 1].start() if i + 1 < len(marks)
                    else len(body)]
        side = sides.setdefault(name, {})
        if topic == "'s fire-power":
            side["firepower"] = (
                _num(text, "\x07" + NUMBER + "\x07<Light-Firepower"),
                _num(text, "\x07" + NUMBER + "\x07<Heavy-Firepower"),
                _num(text, "\x07" + NUMBER + "\x07<Bombing-Firepower"))
        elif topic == "'s ship hitpoints":
            side["ship_hp"] = (
                _num(text, NUMBER + "\n<default>\x07<Light-Hitpoints"),
                _num(text, NUMBER + "\n<default>\x07<Heavy-Hitpoints"))
        elif topic == "planet hitpoints":
            side["planet_hp"] = _num(text, NUMBER + "\n<default>\x07<Planet-Hitpoints")
        elif topic in ("'s ship damage", "planetary-defense damage"):
            side["damage_pct"] = _num(text, r"\t(\d+)%")
            side["destroyed"] = _num(text, r"\((\d+) Ships? destroyed\)")
    return sides


def _text(snap, ptr, size):
    if not gs.is_ptr(ptr) or not 0 < size <= MAX_TEXT:
        return ""
    raw = snap.read(ptr, size) or b""
    return raw.decode("latin-1")


def battle_reports(snap, civ):
    """Every battle report this civ has received, oldest first."""
    raw = snap.read(civ.addr + NEWS_VECTOR, 8)
    if not raw or len(raw) < 8:
        return []
    begin, end = struct.unpack("<II", raw)
    if not gs.is_ptr(begin) or end < begin or (end - begin) % NEWS_RECORD:
        return []
    out = []
    for at in range(begin, end, NEWS_RECORD):
        rec = snap.read(at, NEWS_RECORD)
        if not rec or len(rec) < NEWS_RECORD:
            continue
        kind, = struct.unpack_from("<I", rec, OFF_KIND)
        if kind != KIND_BATTLE:
            continue
        turn, = struct.unpack_from("<I", rec, OFF_TURN)
        title = _text(snap, *struct.unpack_from("<I", rec, OFF_TITLE),
                      struct.unpack_from("<I", rec, OFF_TITLE_SIZE)[0])
        body = _text(snap, struct.unpack_from("<I", rec, OFF_BODY)[0],
                     struct.unpack_from("<I", rec, OFF_BODY_SIZE)[0])
        pos = struct.unpack_from("<fff", rec, OFF_POS)
        m = re.search(r"at Planet '(.*)' \(", title)
        out.append({"turn": turn, "pos": pos,
                    "planet_name": m.group(1) if m else None,
                    "sides": parse_body(body)})
    return out


class Intel:
    """Enemy planet strength as this civ knows it, for one pass.

    Reports still readable this pass are copied into `hist.intel`, which keeps
    them after the game empties their text.
    """

    def __init__(self, snap, civ, hist):
        self.snap, self.civ, self.hist = snap, civ, hist
        self.fought = {}            # planet id -> newest battle turn there
        for rep in battle_reports(snap, civ):
            p = self._planet_at(rep["pos"])
            if p is None:
                continue
            self.fought[p.id] = max(self.fought.get(p.id, 0), rep["turn"])
            old = hist.intel.get(p.id)
            if rep["sides"] and (old is None or rep["turn"] > old["turn"]):
                hist.intel[p.id] = {"turn": rep["turn"],
                                    "planet_name": rep["planet_name"],
                                    "sides": rep["sides"]}

    def _planet_at(self, pos):
        best = None
        for p in self.snap.planets:
            if None in p.pos:
                continue
            d = gs.dist(pos, p.pos)
            if d <= AT_PLANET and (best is None or d < best[0]):
                best = (d, p)
        return best[1] if best else None

    def defence(self, planet):
        """What we know of `planet`'s defenders, or None when nothing fresh is.

        {"turn", "firepower": (light, heavy, bomb), "planet_hp",
         "ship_hp": (light, heavy)}. A quiet observation reads all zero.
        """
        turn = self.snap.turn or 0
        rep = self.hist.intel.get(planet.id)
        quiet = self.hist.quiet.get(planet.id)
        if rep is not None and turn - rep["turn"] <= MAX_AGE                 and (quiet is None or rep["turn"] >= quiet):
            owner = planet.owner.civ_name if planet.owner else None
            side = rep["sides"].get(owner, {})
            # The planet's own block is the one carrying planet hitpoints.
            place = next((v for v in rep["sides"].values() if "planet_hp" in v),
                         {})
            return {"turn": rep["turn"],
                    "firepower": tuple(side.get("firepower", (0, 0, 0))),
                    "planet_hp": place.get("planet_hp", 0),
                    "ship_hp": tuple(side.get("ship_hp", (0, 0)))}
        if quiet is not None and turn - quiet <= MAX_AGE:
            return {"turn": quiet, "firepower": (0, 0, 0), "planet_hp": 0,
                    "ship_hp": (0, 0)}
        return None

    def fleet(self, foe_names):
        """The newest fresh report of these civs' ships in a battle.

        Shaped like defence(), planet_hp 0, or None when no such report is
        fresh. A battle at one of the civ's own planets is passed over: the
        planet's block carries only its hitpoints, and the owner's side counts
        the turrets' firepower (BadGuy's HQ read 400/100, a light turret's).
        So is an unarmed side, a probe or a colony ship, which says nothing
        about a fleet. A report is about one battle, not the fleet now in view,
        so this is the last thing the civ was shown, not a count.
        """
        turn = self.snap.turn or 0
        owner = {p.id: p.owner.civ_name for p in self.snap.planets
                 if p.owner is not None}
        best = None
        for pid, rep in self.hist.intel.items():
            if turn - rep["turn"] > MAX_AGE:
                continue
            for name in foe_names:
                side = rep["sides"].get(name)
                if (not side or owner.get(pid) == name
                        or not any(side.get("ship_hp", (0, 0)))
                        or not any(side.get("firepower", (0, 0, 0)))):
                    continue
                if best is None or rep["turn"] > best["turn"]:
                    best = {"turn": rep["turn"], "civ": name,
                            "firepower": tuple(side.get("firepower", (0, 0, 0))),
                            "planet_hp": 0,
                            "ship_hp": tuple(side["ship_hp"])}
        return best

    def fought_at(self, planet, since):
        """True when a battle at `planet` resolved on turn `since` or later."""
        return self.fought.get(planet.id, -1) >= since


# Scan reports, Owner:320/324: pointers to report objects. Every report has
# +4 type, +0xC turn, +0x10..0x18 target position. A planetary scan (type 0)
# holds a copy of the target's PlanetProperties at +0x28, so the target's
# Planet:N reads at report + 0x28 + (N - 88).
SCAN_REPORTS = 320
PLANETARY = 0
PLANETARY_COPY = 0x28 - 88


def planetary_scans(snap, civ):
    """{planet id: newest planetary scan of it}: turn, stationed military,
    citizens, facilities. What a player reads in the scan reports tab."""
    b, e = snap.rd32(civ.addr + SCAN_REPORTS), snap.rd32(civ.addr + SCAN_REPORTS + 4)
    out = {}
    if not b or not e or e < b or e - b > 4 * 4096:
        return out
    for slot in range(b, e, 4):
        rep = snap.rd32(slot)
        raw = snap.read(rep, 0x200) if gs.is_ptr(rep) else None
        if not raw or len(raw) < 0x200:
            continue
        kind, turn = struct.unpack_from("<I", raw, 4)[0], struct.unpack_from("<I", raw, 0xC)[0]
        if kind != PLANETARY:
            continue
        pos = struct.unpack_from("<3f", raw, 0x10)
        planet = min(snap.planets, key=lambda p: gs.dist(p.pos, pos), default=None)
        if planet is None or gs.dist(planet.pos, pos) > AT_PLANET:
            continue

        def count(off, stride=16):
            first, last = struct.unpack_from("<II", raw, PLANETARY_COPY + off)
            return (last - first) // stride if first and last >= first else 0
        if planet.id in out and out[planet.id]["turn"] > turn:
            continue
        out[planet.id] = {
            "turn": turn,
            "military": count(168),
            "citizens": count(144),
            "facilities": gs.facility_map(
                snap, struct.unpack_from("<I", raw, PLANETARY_COPY + 204)[0]),
        }
    return out


def defended(info):
    return bool(info) and (info["planet_hp"] > 0 or any(info["firepower"])
                           or any(info["ship_hp"]))


def main():
    args = sys.argv[1:]
    snap = gs.Snapshot()
    civ = gs.resolve_civ(snap, cli.opt(args, "--civ"))
    if civ is None:
        return
    for rep in battle_reports(snap, civ):
        print(f"turn {rep['turn']} at {rep['planet_name']!r} "
              f"({', '.join(f'{v:.1f}' for v in rep['pos'])})")
        for name, side in rep["sides"].items():
            print(f"    {name:20} {side}")


if __name__ == "__main__":
    main()
