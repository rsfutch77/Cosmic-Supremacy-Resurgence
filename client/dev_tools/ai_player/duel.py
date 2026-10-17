"""
duel.py: N Python controllers, one galaxy, one client

Runs `ai.py`'s decision pass once per turn for each civ in the galaxy. With no
`--civ` it drives every civ it finds; `server/dev_tools/inject_civ.py` adds
civs.

    python duel.py                                  # dry run, one turn, every civ
    python duel.py --apply --follow --drive 15      # play it out
    python duel.py --civ GoodGuy --civ BadGuy --apply --follow --turns 40
    python duel.py --apply --follow --drive 15 --metrics run.jsonl

"""
import sys
import time

import ai
import cli
import gamestate as gs
import metrics


class Duel:
    """One turn clock, several controllers."""

    def __init__(self, civ_names=None, dry_run=True, vision="known",
                 log=print, settle=1.5, skip=()):
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
        self.last_decided = None       # the turn the last round acted on
        self.metrics = None            # a metrics.Recorder, set from --metrics

        # One shared ViewerState; each pass re-scans it.
        if skip:
            self.log(f"[duel] NOT running: {sorted(skip)}")
        self.loops = [ai.Loop(civ_name=n, dry_run=dry_run, vision=vision,
                              log=log, settle=settle, state=self.state,
                              skip=skip)
                      for n in civ_names]

    def settled_snapshot(self, tries=3, pause=1.5):
        """One snapshot for the whole turn, re-read if any civ looks torn."""
        for attempt in range(tries):
            snap = gs.Snapshot(self.state)
            torn = []
            for loop in self.loops:
                civ = snap.civ(loop.civ_name)
                now = len(snap.owned_planets(civ)) if civ is not None else 0
                had = ai.planet_count(loop.history)
                if ai.looks_torn(now, had):
                    torn.append(f"{loop.civ_name} {now}"
                                + (f"/{had}" if had else ""))
            if not torn:
                return snap
            self.log(f"[duel] planets read {torn} against last turn; turn "
                     f"resolution may still be in flight, re-reading "
                     f"({attempt + 1}/{tries})")
            time.sleep(pause)
        return snap

    def round(self, turn):
        """Every civ takes its pass. Returns the turn decided, or None if none.

        One round per turn, taken from the snapshot: the counter can move while
        `settled_snapshot` retries, and some rules are not idempotent.
        """
        snap = self.settled_snapshot()
        t = snap.turn if snap.turn is not None else turn
        if t == self.last_decided:
            self.log(f"[duel] turn {t} was already decided; the counter moved "
                     f"while the snapshot was being read; not acting twice")
            return None
        # The snapshot can be newer than the turn waited for; the turns skipped
        # went undecided.
        if t > turn:
            self.log(f"[duel] MISSED {t - turn} turn(s): waited for {turn}, "
                     f"the snapshot is already at {t}. Those turns resolved "
                     f"without a decision")
        self.last_decided = t

        # Rotating by the turn number gives each civ the first move in an equal
        # share of turns.
        n = len(self.loops)
        k = t % n
        order = self.loops[k:] + self.loops[:k]
        self.log(f"\n########## turn {t}, move order: "
                 f"{' then '.join(l.civ_name for l in order)} ##########")
        for loop in order:
            if loop.one_pass(snap) is None:
                self.log(f"[duel] {loop.civ_name!r} could not take its pass")
        if self.metrics is not None:
            self.metrics.record(snap, self.loops)
        return t

    def run(self, follow=False, max_turns=None, drive=None, stall=300):
        clock = self.loops[0]          # any loop can read the turn counter
        original = None
        try:
            if drive:
                original = clock.read_turn_length()
                if original in (None, 0xFFFFFFFF):
                    self.log("[duel] turn length is uninitialised; load a "
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
            # `done` counts turns decided, so a skipped duplicate does not
            # consume one of --turns.
            first = self.round(t)
            done = 1 if first is not None else 0
            if first is not None:
                t = first
            if not follow:
                return
            missed = 0
            while max_turns is None or done < max_turns:
                nxt = clock.wait_for_turn(t, timeout=stall, drive=drive)
                if nxt is None:
                    return
                # Logs turns the engine resolved without a pass.
                if nxt > t + 1:
                    missed += nxt - t - 1
                    self.log(f"[duel] MISSED {nxt - t - 1} turn(s): {t} -> "
                             f"{nxt}. The engine resolved turns this "
                             f"controller never acted on, so per-turn "
                             f"measurements across this run are not contiguous")
                # Waits from the turn round() decided, which may be later than
                # the one waited for.
                decided = self.round(nxt)
                t = decided if decided is not None else nxt
                if decided is not None:
                    done += 1
            if missed:
                self.log(f"[duel] {missed} turn(s) went undecided across "
                         f"{done} pass(es)")
            self.log(f"[duel] finished {done} turn(s)")
        except KeyboardInterrupt:
            self.log("\n[duel] interrupted")
        finally:
            if original is not None:
                clock.set_turn_length(original)
                now = clock.read_turn_length()
                self.log(f"[duel] turn length restored to {now}s" if now
                         else "[duel] could not restore turn length; the "
                              "client is gone (it exited or crashed)")
            for loop in self.loops:
                if loop.remote is not None:
                    loop.remote.close()

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
                settle=cli.opt(args, "--settle", 1.5, float),
                skip=cli.opts(args, "--without"))
    if cli.opt(args, "--metrics"):
        duel.metrics = metrics.Recorder(cli.opt(args, "--metrics"),
                                        log=duel.log)
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
