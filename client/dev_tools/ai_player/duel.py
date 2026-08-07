"""
duel.py — N Python controllers, one galaxy, one client
======================================================
Runs `ai.py`'s decision pass once per turn for EACH civ in the galaxy, so the
strategy plays against itself instead of against the engine's own AI. Two civs
is the smallest case, not the design: with no `--civ` it drives every civ it
finds, and `server/dev_tools/inject_civ.py` puts as many in the galaxy as you
want.

    python duel.py                                  # dry run, one turn, every civ
    python duel.py --apply --follow --drive 15      # play it out
    python duel.py --civ GoodGuy --civ BadGuy --apply --follow --turns 40

Why this exists: the engine's AI civ does almost nothing. Over a 154-turn game
it held one planet, built no warship, declared no war and never contested
anything, so every rule in STRATEGY.md was measured against an opponent that
never pushed back — R-XTM-02 (defend) has no way to fire at all. A second
controller is the only opponent that plays the same game we do.

── Nothing here needed a new actuator ────────────────────────────────────────
A civ is drivable because every module already resolves it BY NAME
(`gamestate.resolve_civ`) rather than assuming the local player, and every write
is addressed through that civ's own objects. The `.data` local-player node at
0x00857904 only decides the DEFAULT civ, not what may be written. Confirmed
live: a controller pointed at 'BadGuy' colonised a planet, set research and
built a farm for it, and the engine kept all three across turn boundaries.

── Fairness ──────────────────────────────────────────────────────────────────
Two constraints, both cheap to state and easy to lose by accident:

* **Separate fog of war.** Each loop carries its own `sensors.History`, and the
  discovery set is persisted per civ (`state/discovered_<galaxy>_<civ>.json`),
  so neither controller sees inside a system its own ships have not reached.
  `--vision all` is honoured but it is a different game; the default is `known`.
* **No fixed move order.** Whoever acts first sees the others' pre-move state
  and claims contested things — a shipyard, a colony target — first. The order
  is therefore ROTATED by the turn number rather than fixed, so each civ leads
  an equal share of turns however many of them there are.

Everything else about the AI is unchanged: same rules, same constants, same
§1.1 no-cheating rule applied per civ.
"""
import sys
import time

import ai
import cli
import gamestate as gs


class Duel:
    """One turn clock, several controllers."""

    def __init__(self, civ_names=None, dry_run=True, vision="known",
                 log=print, settle=1.5):
        self.log = log
        self.state = gs.ev.ViewerState()
        if not self.state.connect():
            raise RuntimeError("CosmicSupremacy is not running")

        if not civ_names:
            civ_names = [c.civ_name for c in gs.Snapshot(self.state).civs
                         if c.civ_name]
            self.log(f"[duel] no --civ given; driving every civ in the galaxy: "
                     f"{civ_names}")
        if len(civ_names) < 2:
            raise RuntimeError(f"nothing to play against: {civ_names}")

        # One shared ViewerState: one process handle, and each pass re-scans it
        # anyway, so a second connection would buy nothing.
        self.loops = [ai.Loop(civ_name=n, dry_run=dry_run, vision=vision,
                              log=log, settle=settle, state=self.state)
                      for n in civ_names]

    # -- one turn for everybody -----------------------------------------
    def round(self, turn):
        """Every civ takes its pass. Returns the turn they were taken on."""
        # ROTATE rather than reverse. With two civs the two are the same thing;
        # with twenty, reversing still gives the two ends of the list every
        # first and last move, and first move is worth a contested colony
        # target. A rotation by the turn number gives each civ the lead in an
        # equal share of turns.
        n = len(self.loops)
        k = turn % n
        order = self.loops[k:] + self.loops[:k]
        self.log(f"\n########## move order: "
                 f"{' then '.join(l.civ_name for l in order)} ##########")
        seen = None
        for loop in order:
            t = loop.one_pass()
            if t is None:
                self.log(f"[duel] {loop.civ_name!r} could not take its pass")
            else:
                seen = t
        return seen

    def run(self, follow=False, max_turns=None, drive=None, stall=300):
        clock = self.loops[0]          # any loop can read the turn counter
        original = None
        try:
            if drive:
                original = clock.read_turn_length()
                if original in (None, 0xFFFFFFFF):
                    self.log("[duel] turn length is uninitialised — load a "
                             "galaxy first; not driving turns")
                    original = None
                else:
                    self.log(f"[duel] driving turns at {drive}s "
                             f"(was {original}s)")
                    clock.set_turn_length(drive)

            t = clock.turn()
            if t is None:
                self.log("[duel] cannot read the turn counter")
                return
            self.round(t)
            if not follow:
                return
            done = 1
            while max_turns is None or done < max_turns:
                nxt = clock.wait_for_turn(t, timeout=stall, drive=drive)
                if nxt is None:
                    return
                t = nxt
                self.round(t)
                done += 1
            self.log(f"[duel] finished {done} turn(s)")
        except KeyboardInterrupt:
            self.log("\n[duel] interrupted")
        finally:
            if original is not None:
                clock.set_turn_length(original)
                now = clock.read_turn_length()
                self.log(f"[duel] turn length restored to {now}s" if now
                         else "[duel] could not restore turn length — the "
                              "client is gone (it exited or crashed)")
            for loop in self.loops:
                if loop.remote is not None:
                    loop.remote.close()

    # -- scoreboard ------------------------------------------------------
    def standings(self):
        snap = gs.Snapshot(self.state)
        rows = []
        for loop in self.loops:
            civ = snap.civ(loop.civ_name)
            if civ is None:
                continue
            rows.append((civ.civ_name, civ.score or 0,
                         len(snap.owned_planets(civ)),
                         len(snap.owned_ships(civ)),
                         civ.cash or 0, len(civ.completed or [])))
        return snap.turn, rows


def main():
    args = sys.argv[1:]
    duel = Duel(civ_names=cli.opts(args, "--civ"),
                dry_run=not cli.flag(args, "--apply"),
                vision=cli.opt(args, "--vision", "known"),
                settle=cli.opt(args, "--settle", 1.5, float))
    duel.run(follow=cli.flag(args, "--follow"),
             max_turns=cli.opt(args, "--turns", None, int),
             drive=cli.opt(args, "--drive", None, int),
             stall=cli.opt(args, "--stall", 300, int))

    turn, rows = duel.standings()
    print(f"\n=== standings at turn {turn} ===")
    print(f"{'civ':>12} {'score':>8} {'planets':>8} {'ships':>6} "
          f"{'cash':>8} {'techs':>6}")
    for name, score, planets, ships, cash, techs in rows:
        print(f"{name:>12} {score:>8} {planets:>8} {ships:>6} "
              f"{cash:>8} {techs:>6}")


if __name__ == "__main__":
    main()
