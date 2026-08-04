"""
ai.py — the turn loop (STRATEGY.md §1)
======================================
Turns the toolbox into an actual player. Wakes when the turn counter moves,
snapshots memory, runs every rule once in priority order, writes, and sleeps.

    python ai.py                          # dry run, one pass, right now
    python ai.py --apply                  # act on this turn
    python ai.py --apply --follow         # keep playing, every turn, forever
    python ai.py --apply --follow --turns 20
    python ai.py --apply --follow --drive 10   # also shorten turns to 10s

Dry run is the default here as everywhere: a pass with no `--apply` prints every
intended write and performs none of them.

── Why wake on the turn counter ───────────────────────────────────────────────
The engine resolves everything at a turn boundary — movement, production, growth
— so a decision taken mid-turn is a decision taken on stale numbers, and the
derived-by-observation sensors (STRATEGY.md §2.5) are only meaningful when
sampled one turn apart. One pass per turn, immediately after the counter moves,
is both the cheapest and the most accurate cadence.

`--drive` shortens the turn length so a game runs in minutes instead of hours;
it restores the original on exit, including on Ctrl-C. Without it the loop is
happy to sit on a real 3600-second turn.
"""
import struct
import sys
import time

import actions
import cli
import expand
import exploit
import exterminate
import explore
import gamestate as gs
import remote
import sensors

TURN_COUNTER    = gs.TURN_COUNTER
TURNLENGTH_ADDR = 0x0080AA08
DEFAULT_LENGTH  = 3600

# ── Rule order ────────────────────────────────────────────────────────────────
# Spelled out rather than concatenating each module's list, because the order is
# a STRATEGY decision and concatenation quietly encoded the wrong one: the
# facility rule sat ahead of both ship-build rules, so on a civ whose only
# shipyard was the homeworld it took that planet's production every single turn.
# The empire built farms for 40 turns, never produced a ship, never explored, and
# never found the enemy. Nothing looked broken — every rule was doing its job.
#
# The principle: PRODUCTION IS THE SCARCE RESOURCE, and only shipyard planets can
# make ships, while facilities can be built anywhere. So ships get first refusal
# on a shipyard and facilities take what is left.
RULES = [
    # 1. Preserve what exists. A wreck must not be claimed by anything else,
    #    and a starving colony undoes any amount of expansion.
    ("R-XTM-05", exterminate.run_xtm05),      # withdraw damaged ships
    ("R-XTM-00", exterminate.run_xtm00),      # declare war (off by default)
    ("S-food",   exploit.run_food),           # famine / surplus farmers
    ("R-XPL-01", exploit.run_jobs),           # the rest of the job mix
    ("R-XPN-03", expand.run_xpn03),           # set up anything colonised this turn

    # 2. Production, ships before buildings.
    ("R-XTM-01", exterminate.run_xtm01),      # warships, only when at war
    ("R-XPN-01", expand.run_xpn01),           # colony ships
    ("R-XPL-02", exploit.run_facilities),     # facilities take what is left
    ("R-XPL-08", exploit.run_hurry),          # spend surplus cash on all of it

    # 3. Crew, then research. Crew gates every order rule below.
    ("R-XPL-05", exploit.run_crew_supply),
    ("R-XPL-06", exploit.run_crew_load),
    ("R-XPL-04", exploit.run_research),

    # 4. Orders. Attack before colonise before scout: a warship should fight
    #    rather than explore, and a colony ship should take a known planet in
    #    preference to going looking for a new one.
    ("R-XTM-03", exterminate.run_xtm03),
    ("R-XPN-02", expand.run_xpn02),
    ("R-EXP-02", explore.run_exp02),
]


class Loop:
    def __init__(self, civ_name="GoodGuy", dry_run=True, vision="all",
                 top=0, log=print, settle=1.5):
        self.civ_name = civ_name
        self.dry_run = dry_run
        self.vision = vision
        self.top = top
        self.log = log
        self.state = gs.ev.ViewerState()
        if not self.state.connect():
            raise RuntimeError("CosmicSupremacy is not running")
        self.remote = None
        self.passes = 0
        self.history = sensors.History()
        # Seconds to let turn resolution finish before snapshotting.
        self.settle = settle

    # -- turn clock -----------------------------------------------------
    def turn(self):
        b = gs.ev.read_bytes(self.state.handle, TURN_COUNTER, 4)
        return struct.unpack("<I", b)[0] if b and len(b) == 4 else None

    def wait_for_turn(self, after, poll=1.0, timeout=None, drive=None,
                      heartbeat=15.0):
        """Block until the turn counter moves past `after`.

        Says so, and keeps saying so. A silent poll loop is indistinguishable
        from a hung one — the first version printed nothing here and got killed
        twice by hand while it was working perfectly.

        Also re-asserts the driven turn length DURING the wait, not only after a
        boundary: if the engine resets it to 3600 mid-wait, a loop that only
        re-checks after a turn would sit there for an hour.
        """
        start = last_beat = time.time()
        self.log(f"[loop] waiting for turn {after + 1} "
                 f"(turn length {self.read_turn_length()}s, Ctrl-C to stop)")
        while True:
            t = self.turn()
            if t is None:
                self.log("[loop] lost the process")
                return None
            if t > after:
                # The counter moves at the START of turn resolution, not the
                # end. A snapshot taken immediately can catch the engine
                # mid-rewrite: one pass read "0 planet(s)" for a civ that had
                # three, and the next read four. Deciding on that would have
                # had R-XPN-01 see no shipyard and R-XPN-02 no targets. Let it
                # settle before looking.
                time.sleep(self.settle)
                return t
            if drive and self.read_turn_length() != drive:
                self.log(f"[loop] turn length drifted; re-asserting {drive}s")
                self.set_turn_length(drive)
            now = time.time()
            if now - last_beat >= heartbeat:
                self.log(f"[loop] still on turn {t}, {int(now - start)}s "
                         f"elapsed (turn length {self.read_turn_length()}s)")
                last_beat = now
            if timeout and now - start > timeout:
                self.log(f"[loop] no turn in {timeout}s. The turn pipeline is "
                         f"not firing. The usual cause is a MODAL DIALOG in the "
                         f"client — the home-world customisation popup blocks "
                         f"turn resolution entirely. Dismiss it and re-run.")
                return None
            time.sleep(poll)

    # -- turn driving (optional) ----------------------------------------
    def set_turn_length(self, secs):
        gs.ev.write_bytes(self.state.handle, TURNLENGTH_ADDR,
                          struct.pack("<I", secs))

    def read_turn_length(self):
        b = gs.ev.read_bytes(self.state.handle, TURNLENGTH_ADDR, 4)
        return struct.unpack("<I", b)[0] if b and len(b) == 4 else None

    def _get_remote(self):
        """Open the elevated handle on first actual need, then reuse it."""
        if self.remote is None:
            self.remote = remote.Remote(self.state.pid, log=self.log)
        return self.remote

    # -- one decision pass ----------------------------------------------
    def _snapshot_settled(self, tries=3, pause=1.5):
        """Snapshot, and retry if the galaxy looks half-rewritten.

        The turn counter moves at the START of resolution, so a snapshot can
        catch the engine mid-rewrite and report a civ owning nothing. A fixed
        settle delay only moves the race: 1.5s was enough most turns and still
        produced a "0 planet(s)" read. Losing every planet between two turns is
        not something that happens in this game, so treat it as evidence the
        read was torn and look again rather than guessing at a longer sleep.
        """
        for attempt in range(tries):
            snap = gs.Snapshot(self.state)
            civ = gs.resolve_civ(snap, self.civ_name)
            if civ is None:
                return snap, None
            if snap.owned_planets(civ):
                return snap, civ
            # Retry even on the FIRST pass, when there is no history to compare
            # against. That blind spot let a torn read through on loop start:
            # the pass reported "0 planet(s)" for a five-planet civ and computed
            # its buildable-facility set from an empty empire. A civ genuinely
            # holding nothing has already lost, so a wasted re-read costs
            # nothing and the check is worth having unconditionally.
            had = self.history.prev and self.history.prev["planets"]
            self.log(f"[loop] read 0 planets"
                     + (f" for a civ that had {len(had)}" if had else "")
                     + f" — turn resolution may still be in flight, "
                       f"re-reading ({attempt + 1}/{tries})")
            time.sleep(pause)
        return snap, civ

    def one_pass(self):
        snap, civ = self._snapshot_settled()
        if civ is None:
            self.log(f"[loop] no civ named {self.civ_name!r}; saw "
                     f"{[c.civ_name for c in snap.civs]}")
            return None

        self.log(f"\n=== turn {snap.turn} — {civ.civ_name!r} — "
                 f"{'DRY RUN' if self.dry_run else 'APPLYING'} ===")
        self.log(f"    {len(snap.owned_planets(civ))} planet(s), "
                 f"{len(snap.owned_ships(civ))} ship(s), cash {civ.cash}, "
                 f"research {civ.research}, score {civ.score}")

        # A game still sitting on the home-world customisation popup has
        # uncomputed designs AND a frozen turn pipeline, so every rule skips and
        # then the loop waits for a turn that can never arrive. Name it.
        # Only meaningful if the turn counter is ALSO stuck, which is the
        # symptom this warning actually claims. Judging it on the stat cache
        # alone made it fire on a healthy game at turn 99 with every design
        # reading computed=True a moment later — a cold or torn read looks
        # identical to an unstarted game.
        ours = {s.design for s in snap.owned_ships(civ) if s.design}
        stuck = (self.history.prev
                 and self.history.prev.get("turn") == snap.turn)
        if stuck and ours and not any(d.computed for d in ours):
            self.log("    [!] none of this civ's ship designs are computed. "
                     "The game has almost certainly not started — finish the "
                     "home-world customisation popup. While it is up the turn "
                     "counter will not advance either.")

        self.history.observe(snap, civ)
        self.log(f"    {self.history.summary()}")

        act = actions.Actuator(snap, dry_run=self.dry_run, log=self.log,
                               remote_factory=self._get_remote)
        for name, fn in RULES:
            try:
                if fn is expand.run_xpn02:
                    fn(snap, civ, act, vision=self.vision, top=self.top,
                       hist=self.history, log=self.log)
                elif fn in (exploit.run_food, explore.run_exp02,
                            exploit.run_facilities, exploit.run_jobs,
                            exploit.run_hurry, exploit.run_crew_supply,
                            exterminate.run_xtm00,
                            exterminate.run_xtm01,
                            exterminate.run_xtm03,
                            exterminate.run_xtm05):
                    fn(snap, civ, act, self.history, log=self.log)
                else:
                    fn(snap, civ, act, log=self.log)
            except Exception as e:
                # One bad rule must not take the loop down mid-game.
                self.log(f"[loop] rule {name} raised {type(e).__name__}: {e}")
        # Tell the sensors what WE did to the treasury, so next turn's income
        # reading reflects the economy and not this pass's purchases.
        self.history.note_cash_effect(act.cash_effect)
        self.passes += 1
        self.log(f"    {act.summary()}")
        return snap.turn

    # -- drive ----------------------------------------------------------
    def run(self, follow=False, max_turns=None, drive=None, stall=None):
        original = None
        try:
            if drive:
                original = self.read_turn_length()
                if original in (None, 0xFFFFFFFF):
                    self.log("[loop] turn length is uninitialised — load a "
                             "galaxy first; not driving turns")
                    original = None
                else:
                    self.log(f"[loop] driving turns at {drive}s "
                             f"(was {original}s)")
                    self.set_turn_length(drive)

            t = self.one_pass()
            if t is None or not follow:
                return
            done = 1
            while max_turns is None or done < max_turns:
                nxt = self.wait_for_turn(t, timeout=stall, drive=drive)
                if nxt is None:
                    return
                t = self.one_pass()
                if t is None:
                    return
                done += 1
            self.log(f"[loop] finished {done} turn(s)")
        except KeyboardInterrupt:
            self.log(f"\n[loop] interrupted after {self.passes} pass(es)")
        finally:
            if original is not None:
                self.set_turn_length(original)
                now = self.read_turn_length()
                # A dead process reads None; saying "restored to Nones" hides
                # the far more important fact that the client is gone.
                self.log(f"[loop] turn length restored to {now}s" if now
                         else "[loop] could not restore turn length — the "
                              "client is gone (it exited or crashed)")
            if self.remote is not None:
                self.remote.close()


def main():
    args = sys.argv[1:]
    def opt(n, d=None, cast=None):
        return cli.opt(args, n, d, cast)
    loop = Loop(civ_name=opt("--civ"),
                dry_run=not cli.flag(args, "--apply"),
                vision=opt("--vision", "known"),
                top=opt("--top", 0, int),
                settle=opt("--settle", 1.5, float))
    loop.run(follow=cli.flag(args, "--follow"),
             max_turns=opt("--turns", None, int),
             drive=opt("--drive", None, int),
             stall=opt("--stall", 300, int))


if __name__ == "__main__":
    main()
