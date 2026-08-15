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

        # One shared ViewerState: one process handle, and each pass re-scans it
        # anyway, so a second connection would buy nothing.
        if skip:
            self.log(f"[duel] NOT running: {sorted(skip)}")
        self.loops = [ai.Loop(civ_name=n, dry_run=dry_run, vision=vision,
                              log=log, settle=settle, state=self.state,
                              skip=skip)
                      for n in civ_names]

    # -- one shared scan per turn ---------------------------------------
    def settled_snapshot(self, tries=3, pause=1.5):
        """One snapshot for the whole turn, re-read if it looks torn.

        The scan is 94% of a decision pass, so scanning per civ multiplies the
        expensive part by the number of players for no extra information —
        measured at 5.72s for three civs against 2.12s sharing one.

        The torn-read guard that each Loop used to do for itself moves here,
        and gets stricter in the process: it now demands that EVERY civ reads a
        non-empty empire, because the counter-example is a snapshot caught
        mid-resolution where one civ's planets have been rewritten and another's
        have not. A per-civ check could pass for the civ that happened to look
        fine and hand the torn read to the next one.
        """
        for attempt in range(tries):
            snap = gs.Snapshot(self.state)
            civs = [snap.civ(l.civ_name) for l in self.loops]
            if all(c is not None and snap.owned_planets(c) for c in civs):
                return snap
            empty = [l.civ_name for l, c in zip(self.loops, civs)
                     if c is None or not snap.owned_planets(c)]
            self.log(f"[duel] {empty} read as holding nothing — turn "
                     f"resolution may still be in flight, re-reading "
                     f"({attempt + 1}/{tries})")
            time.sleep(pause)
        return snap

    # -- one turn for everybody -----------------------------------------
    def round(self, turn):
        """Every civ takes its pass. Returns the turn decided, or None if none.

        ONE ROUND PER TURN, and the SNAPSHOT decides which turn that is. A run
        of 25 rounds covered only 24 distinct turns: the counter can move while
        `settled_snapshot` is retrying a torn read, so the turn the caller waited
        for is not always the turn the snapshot describes. Acting twice on one
        turn is not as harmless as it sounds — most rules re-read state and are
        idempotent, but a second conscript or a second queued hull is real.

        Taking the turn from the snapshot rather than from the caller also fixes
        the move order, which is derived from it: a rotation computed on a stale
        number gives one civ the lead twice running.
        """
        snap = self.settled_snapshot()
        t = snap.turn if snap.turn is not None else turn
        if t == self.last_decided:
            self.log(f"[duel] turn {t} was already decided — the counter moved "
                     f"while the snapshot was being read; not acting twice")
            return None
        # THE SNAPSHOT CAN BE NEWER THAN THE TURN WE WAITED FOR. wait_for_turn
        # polls at 1s and settled_snapshot then spends its settle delay and any
        # torn-read retries, so the counter can move on in between. Whatever it
        # skipped past is a turn nobody decided, and it used to vanish silently:
        # the round simply decided the newer turn instead, and the caller then
        # waited for a turn already gone.
        if t > turn:
            self.log(f"[duel] MISSED {t - turn} turn(s): waited for {turn}, "
                     f"the snapshot is already at {t}. Those turns resolved "
                     f"without a decision")
        self.last_decided = t

        # ROTATE rather than reverse. With two civs the two are the same thing;
        # with twenty, reversing still gives the two ends of the list every
        # first and last move, and first move is worth a contested colony
        # target. A rotation by the turn number gives each civ the lead in an
        # equal share of turns.
        n = len(self.loops)
        k = t % n
        order = self.loops[k:] + self.loops[:k]
        self.log(f"\n########## turn {t} — move order: "
                 f"{' then '.join(l.civ_name for l in order)} ##########")
        for loop in order:
            if loop.one_pass(snap) is None:
                self.log(f"[duel] {loop.civ_name!r} could not take its pass")
        return t

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
            # `done` counts turns actually DECIDED, not loop iterations, so a
            # skipped duplicate does not consume one of --turns.
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
                # SAY SO WHEN TURNS OUTRUN PASSES. A run once took 16 passes
                # across 60 turns and left no way to tell why: the engine
                # resolved turns nobody decided on, every rule looked healthy,
                # and the standings at the end were perfectly plausible. The
                # candidates -- a suspended host, a pass slower than its turn,
                # a stalled re-assert -- are indistinguishable after the fact,
                # so the loop records it as it happens instead.
                if nxt > t + 1:
                    missed += nxt - t - 1
                    self.log(f"[duel] MISSED {nxt - t - 1} turn(s): {t} -> "
                             f"{nxt}. The engine resolved turns this "
                             f"controller never acted on, so per-turn "
                             f"measurements across this run are not contiguous")
                # Follow the SNAPSHOT, not the clock. round() returns the turn
                # it actually decided, which may be later than the one waited
                # for; waiting again from the older number asks for a turn that
                # has already passed, and the next round then skips as a
                # duplicate — which is how a decided turn and a skipped turn
                # ended up alternating.
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
                settle=cli.opt(args, "--settle", 1.5, float),
                skip=cli.opts(args, "--without"))
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
