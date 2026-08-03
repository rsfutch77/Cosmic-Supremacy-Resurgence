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
import gamestate as gs
import remote
import sensors

TURN_COUNTER    = gs.TURN_COUNTER
TURNLENGTH_ADDR = 0x0080AA08
DEFAULT_LENGTH  = 3600

# Sustain first: a starving colony undoes any amount of expansion, so food is
# settled before anything else spends a citizen or a shipyard.
RULES = exploit.RULES + expand.RULES


class Loop:
    def __init__(self, civ_name="GoodGuy", dry_run=True, vision="all",
                 top=0, log=print):
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
    def one_pass(self):
        snap = gs.Snapshot(self.state)
        civ = gs.resolve_civ(snap, self.civ_name)
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
        ours = {s.design for s in snap.owned_ships(civ) if s.design}
        if ours and not any(d.computed for d in ours):
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
                       log=self.log)
                elif fn is exploit.run_food:
                    fn(snap, civ, act, self.history, log=self.log)
                else:
                    fn(snap, civ, act, log=self.log)
            except Exception as e:
                # One bad rule must not take the loop down mid-game.
                self.log(f"[loop] rule {name} raised {type(e).__name__}: {e}")
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
                vision=opt("--vision", "all"),
                top=opt("--top", 0, int))
    loop.run(follow=cli.flag(args, "--follow"),
             max_turns=opt("--turns", None, int),
             drive=opt("--drive", None, int),
             stall=opt("--stall", 300, int))


if __name__ == "__main__":
    main()
