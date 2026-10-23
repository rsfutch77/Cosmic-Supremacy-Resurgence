"""
metrics.py: per-turn measurements of a run, for comparing rule changes

    python duel.py --apply --follow --drive 15 --metrics run.jsonl
    python ai.py --civ BadGuy --apply --follow --metrics run.jsonl
    python metrics.py run.jsonl                  # summary table
    python metrics.py base.jsonl new.jsonl       # two runs side by side

One JSON line per civ per decided turn. Every civ in the galaxy is recorded;
contact, war and discovery are known only for the civs a controller drives.
The recorder reads the snapshot the rules acted on and feeds nothing back to
them.

A ship that disappears was destroyed, except a colony hull, which is spent on
landing. A ship whose condition fell was damaged. A turn with either is a
battle turn for that civ.
"""
import json
import sys
from types import SimpleNamespace

import exterminate

ROLES = ("COLONY", "SCOUT", "WARSHIP", "TROOP")
SCOUT_ORDER = 2


class Recorder:
    def __init__(self, path, log=print):
        self.path = path
        self.log = log
        self.prev_ships = {}       # ship id -> (civ name, role, condition)
        self.prev_turn = None
        self.life = {}             # warship id -> {"seen", "crewed", "staged"} turns
        # Truncated at start so one file holds one run.
        open(self.path, "w", encoding="utf-8").close()

    def record(self, snap, loops=()):
        """Append one line per civ for this snapshot's turn.

        `loops` are the ai.Loop controllers driving civs in this run.
        """
        driven = {l.civ_name: l for l in loops}
        gap = (snap.turn - self.prev_turn - 1
               if self.prev_turn is not None and snap.turn is not None else 0)
        ships = {}
        for civ in snap.civs:
            for s in snap.owned_ships(civ):
                ships[s.id] = (civ.civ_name, s.role, s.condition)

        damaged, lost, spent = {}, {}, {}
        for sid, (name, role, cond) in ships.items():
            was = self.prev_ships.get(sid)
            if (was and cond is not None and was[2] is not None
                    and cond < was[2] - 1e-6):
                damaged[name] = damaged.get(name, 0) + 1
        for sid, (name, role, _cond) in self.prev_ships.items():
            if sid in ships:
                continue
            bucket = spent if role == "COLONY" else lost
            bucket.setdefault(name, {})
            bucket[name][role] = bucket[name].get(role, 0) + 1

        rows = []
        for civ in snap.civs:
            name = civ.civ_name
            own = snap.owned_ships(civ)
            by_role = {r: 0 for r in ROLES}
            for s in own:
                if s.role in by_role:
                    by_role[s.role] += 1
            warships = [s for s in own if s.role == "WARSHIP"]
            row = {
                "turn": snap.turn, "gap": gap, "civ": name,
                "driven": name in driven,
                "planets": len(snap.owned_planets(civ)),
                "score": civ.score, "cash": civ.cash,
                "techs": len(civ.completed or []),
                "ships": by_role,
                "warships_crewed": sum(1 for s in warships if s.crewed),
                "warships_scouting": sum(1 for s in warships
                                         if (s.order_type or 0) == SCOUT_ORDER),
                "damaged": damaged.get(name, 0),
                "lost": lost.get(name, {}),
                "colony_spent": sum(spent.get(name, {}).values()),
            }
            loop = driven.get(name)
            if loop is not None:
                hist = loop.history
                row["discovered"] = len(hist.discovered)
                rally = exterminate.staging_planet(snap, civ, hist)
                row["staging"] = rally.id if rally is not None else None
                row["warships_staged"] = (
                    sum(1 for s in warships if s.crewed
                        and exterminate.at_planet(s, rally))
                    if rally is not None else 0)
                # Turns from a warship's first appearance to crew and staging.
                crew_lags, stage_lags = [], []
                for s in warships:
                    life = self.life.setdefault(s.id, {"seen": snap.turn})
                    if s.crewed and "crewed" not in life:
                        life["crewed"] = snap.turn
                        crew_lags.append(snap.turn - life["seen"])
                    if (rally is not None and "staged" not in life
                            and s.crewed and exterminate.at_planet(s, rally)):
                        life["staged"] = snap.turn
                        stage_lags.append(snap.turn - life["seen"])
                row["crew_lags"] = crew_lags
                row["stage_lags"] = stage_lags
                row["contacted"] = [c.civ_name for c in
                                    exterminate.contacted(snap, civ, hist)]
                act = SimpleNamespace(remote=loop.remote)
                row["at_war"] = ([c.civ_name for c in
                                  exterminate.enemies(snap, civ, act)]
                                 if loop.remote is not None else None)
            rows.append(row)

        try:
            with open(self.path, "a", encoding="utf-8") as fh:
                for row in rows:
                    fh.write(json.dumps(row) + "\n")
        except OSError as exc:
            self.log(f"[metrics] could not write {self.path}: {exc}")
        self.prev_ships, self.prev_turn = ships, snap.turn


def load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def summarise(rows):
    """{civ: {measure: value}} over a whole run."""
    out = {}
    for r in rows:
        s = out.setdefault(r["civ"], {
            "turns": 0, "first_turn": r["turn"], "last_turn": r["turn"],
            "missed": 0, "planets": 0, "score": 0, "max_warships": 0,
            "battle_turns": 0, "damaged": 0, "lost": 0, "lost_warships": 0,
            "colony_spent": 0, "first_contact": None, "first_war": None,
            "first_battle": None, "scouting_warship_turns": 0,
            "discovered": None, "staged_at_contact": None,
            "staged_at_war": None, "_crew": [], "_stage": []})
        s["turns"] += 1
        s["last_turn"] = r["turn"]
        s["missed"] += r.get("gap", 0)
        s["planets"] = r["planets"]
        s["score"] = r["score"] or 0
        s["max_warships"] = max(s["max_warships"], r["ships"]["WARSHIP"])
        lost = sum(r["lost"].values())
        s["damaged"] += r["damaged"]
        s["lost"] += lost
        s["lost_warships"] += r["lost"].get("WARSHIP", 0)
        s["colony_spent"] += r["colony_spent"]
        s["scouting_warship_turns"] += 1 if r["warships_scouting"] else 0
        if r["damaged"] or lost:
            s["battle_turns"] += 1
            if s["first_battle"] is None:
                s["first_battle"] = r["turn"]
        if r.get("contacted") and s["first_contact"] is None:
            s["first_contact"] = r["turn"]
            if "warships_staged" in r:
                s["staged_at_contact"] = (f"{r['warships_staged']}/"
                                          f"{r['warships_crewed']}")
        if r.get("at_war") and s["first_war"] is None:
            s["first_war"] = r["turn"]
            if "warships_staged" in r:
                s["staged_at_war"] = (f"{r['warships_staged']}/"
                                      f"{r['warships_crewed']}")
        if r.get("discovered") is not None:
            s["discovered"] = r["discovered"]
        s["_crew"] += r.get("crew_lags", [])
        s["_stage"] += r.get("stage_lags", [])
    for s in out.values():
        crew, stage = s.pop("_crew"), s.pop("_stage")
        s["warships_crewed_n"] = len(crew)
        s["turns_to_crew"] = round(sum(crew) / len(crew), 1) if crew else None
        s["turns_to_stage"] = round(sum(stage) / len(stage), 1) if stage else None
    return out


MEASURES = ["turns", "first_turn", "last_turn", "missed", "planets", "score",
            "discovered", "max_warships", "first_contact", "staged_at_contact",
            "first_war", "staged_at_war",
            "first_battle", "battle_turns", "damaged", "lost",
            "lost_warships", "colony_spent", "scouting_warship_turns",
            "warships_crewed_n", "turns_to_crew", "turns_to_stage"]


def print_table(columns):
    """`columns` is [(heading, {measure: value})]."""
    width = max(12, *(len(h) for h, _ in columns))
    print(f"{'':24}" + "".join(f"{h:>{width + 2}}" for h, _ in columns))
    for m in MEASURES:
        cells = "".join(f"{str(c.get(m, '')):>{width + 2}}"
                        for _h, c in columns)
        print(f"{m:24}{cells}")


def main():
    paths = sys.argv[1:]
    if not paths:
        sys.exit(__doc__)
    columns = []
    for path in paths:
        for civ, s in summarise(load(path)).items():
            heading = civ if len(paths) == 1 else f"{civ}@{path}"
            columns.append((heading, s))
    print_table(columns)


if __name__ == "__main__":
    main()
