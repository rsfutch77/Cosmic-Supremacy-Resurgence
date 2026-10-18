"""
ai.py: the turn loop (STRATEGY.md §1).

Wakes when the turn counter moves, snapshots memory, runs every rule once in
priority order, writes, and sleeps.

    python ai.py                          # dry run, one pass, right now
    python ai.py --apply                  # act on this turn
    python ai.py --apply --follow         # keep playing, every turn, forever
    python ai.py --apply --follow --turns 20
    python ai.py --apply --follow --drive 10   # also shorten turns to 10s
    python ai.py --apply --follow --drive 10 --halt-on-contact
    python ai.py --apply --follow --metrics run.jsonl

`--halt-on-contact` stops the loop on the first turn an enemy planet is visible,
after the rules have run (so R-XTM-03 has already committed the fleet), and
restores the turn length on the way out.

"""
import json
import os
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
import metrics
import remote
import sensors

TURN_COUNTER    = gs.TURN_COUNTER
TURNLENGTH_ADDR = 0x0080AA08
DEFAULT_LENGTH  = 3600

# A snapshot taken during turn resolution can show part of an empire. A civ
# read below this fraction of last turn's planet count is re-read.
TORN_FRACTION = 0.5


def looks_torn(now, before):
    """True when `now` planets reads like a partial copy of `before`.

    Zero planets is always suspect: a civ that truly holds nothing has lost.
    """
    return now == 0 or (bool(before) and now < before * TORN_FRACTION)


def planet_count(hist):
    """Planets the civ held at the last pass, or None before the first."""
    return len(hist.prev["planets"]) if hist.prev else None

# Rule order is a strategy decision. Only shipyard planets can make ships, while
# facilities can be built anywhere, so ship builds get first claim on production.
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
    ("R-XTM-07", exterminate.run_xtm07),      # a troop hull while anything is takeable
    ("R-XPN-01", expand.run_xpn01),           # colony ships
    ("R-EXP-01", explore.run_exp01),          # dedicated scouts
    ("R-XPL-02", exploit.run_facilities),     # facilities take what is left
    ("R-XPL-08", exploit.run_hurry),          # spend surplus cash on all of it

    # 3. Crew, then research. Crew gates every order rule below.
    #    Conscription runs before the loader; a unit drafted this turn is
    #    loaded next turn, since this pass's snapshot predates the draft.
    ("R-XPL-05", exploit.run_crew_supply),
    ("R-XPL-09", exploit.run_conscript),
    ("R-XPL-06", exploit.run_crew_load),
    ("R-XPL-04", exploit.run_research),

    # 4. Orders: garrison, conquer, raid, rally, probe, colonise, scout.
    #    A hull that just took a planet lands its troops before R-XTM-04 sends
    #    it on, or the planet rebels. Invasions claim undefended targets before
    #    raids pick defended ones. Warships no raid claimed go to the staging
    #    planet. A probe claims its scout before Explore can.
    ("R-XTM-06", exterminate.run_xtm06),
    ("R-XTM-04", exterminate.run_xtm04),
    ("R-XTM-03", exterminate.run_xtm03),
    ("R-XTM-08", exterminate.run_xtm08),
    ("R-XTM-09", exterminate.run_xtm09),
    ("R-XPN-02", expand.run_xpn02),
    ("R-EXP-02", explore.run_exp02),
]


def pass_marker_path(civ_name):
    """Path of the file recording the last turn the loop finished deciding.

    Lives in sensors.STATE_DIR, beside the discovery set.
    """
    safe = "".join(c if c.isalnum() else "_" for c in (civ_name or "unknown"))
    return os.path.join(sensors.STATE_DIR, f"pass_{safe}.json")


def read_pass_marker(civ_name):
    """(turn, stamp) of the opponent's last completed pass, or (None, None)."""
    try:
        with open(pass_marker_path(civ_name), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("turn"), data.get("stamp")
    except (OSError, ValueError):
        return None, None


class Loop:
    def __init__(self, civ_name="GoodGuy", dry_run=True, vision="all",
                 top=0, log=print, settle=1.5, state=None, skip=(),
                 halt_on_contact=False):
        # `skip` names rules not to run, by R-XXX-NN id, for bisecting a
        # reproducible crash.
        self.skip = set(skip)
        self.civ_name = civ_name
        self.dry_run = dry_run
        self.vision = vision
        self.top = top
        self.log = log
        # `state` lets several loops share one connection and scan cache.
        # Left None, the loop opens its own.
        self.state = state or gs.ev.ViewerState()
        if self.state.handle is None and not self.state.connect():
            raise RuntimeError("CosmicSupremacy is not running")
        self.remote = None
        self.passes = 0
        # Set by _snapshot_settled so one_pass can report where its time went.
        self.last_scan_secs = 0.0
        self.last_scan_tries = 1
        self.history = sensors.History()
        # Seconds to let turn resolution finish before snapshotting.
        self.settle = settle
        # Stop the loop the turn an enemy planet first becomes visible, so a
        # human can drive the approach and read the battle report.
        self.halt_on_contact = halt_on_contact
        self.halt_reason = None
        # Set by run(); one_pass uses it to notice when it is outrunning the
        # turn it is deciding for.
        self.drive = None
        # A metrics.Recorder, or None. Set by main() from --metrics.
        self.metrics = None

    def turn(self):
        b = gs.ev.read_bytes(self.state.handle, TURN_COUNTER, 4)
        return struct.unpack("<I", b)[0] if b and len(b) == 4 else None

    def wait_for_turn(self, after, poll=1.0, timeout=None, drive=None,
                      heartbeat=15.0):
        """Block until the turn counter moves past `after`.

        Logs a heartbeat while waiting, and re-asserts the driven turn length
        during the wait because the engine can reset it to 3600.
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
                # The counter moves at the start of turn resolution, so an
                # immediate snapshot can catch the engine mid-rewrite.
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
                         f"not firing. Check the client is alive and that the "
                         f"turn length at 0x{TURNLENGTH_ADDR:08X} is what you "
                         f"expect; the engine resets it, often.")
                return None
            time.sleep(poll)

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

    def _snapshot_settled(self, tries=3, pause=1.5):
        """Snapshot, and retry if the galaxy looks half-rewritten.

        A read that `looks_torn` against last turn's planet count is repeated.
        After `tries` reads the last one is used.
        """
        had = planet_count(self.history)
        for attempt in range(tries):
            t0 = time.time()
            snap = gs.Snapshot(self.state)
            scan_secs = time.time() - t0
            civ = gs.resolve_civ(snap, self.civ_name)
            self.last_scan_secs = scan_secs
            self.last_scan_tries = attempt + 1
            if civ is None:
                return snap, None
            now = len(snap.owned_planets(civ))
            if not looks_torn(now, had):
                return snap, civ
            self.log(f"[loop] read {now} planet(s)"
                     + (f" for a civ that had {had}" if had else "")
                     + f"; turn resolution may still be in flight, "
                       f"re-reading ({attempt + 1}/{tries})")
            time.sleep(pause)
        return snap, civ

    def one_pass(self, snap=None):
        """One decision pass. `snap` lets a caller supply a shared snapshot.

        With a shared snapshot every civ decides from the same start-of-turn
        state. Civs share no objects, so nothing a rule reads about its own
        empire goes stale mid-turn.
        """
        if snap is not None:
            civ = gs.resolve_civ(snap, self.civ_name)
        else:
            snap, civ = self._snapshot_settled()
        if civ is None:
            self.log(f"[loop] no civ named {self.civ_name!r}; saw "
                     f"{[c.civ_name for c in snap.civs]}")
            return None

        self.log(f"\n=== turn {snap.turn}, {civ.civ_name!r}, "
                 f"{'DRY RUN' if self.dry_run else 'APPLYING'} ===")
        self.log(f"    {len(snap.owned_planets(civ))} planet(s), "
                 f"{len(snap.owned_ships(civ))} ship(s), cash {civ.cash}, "
                 f"research {civ.research}, score {civ.score}")

        # Warns when no owned design is computed and the turn counter has not
        # moved since the last pass, which means the game is not running.
        ours = {s.design for s in snap.owned_ships(civ) if s.design}
        stuck = (self.history.prev
                 and self.history.prev.get("turn") == snap.turn)
        if stuck and ours and not any(d.computed for d in ours):
            self.log("    [!] none of this civ's ship designs are computed and "
                     "the turn counter has not moved. The game is not running: "
                     "check the client is alive and that something is driving "
                     "the turn length.")

        # Per-turn engine results go stale at a turn boundary. Design-level
        # facts survive, since nothing refits a design.
        if self.remote is not None:
            self.remote.begin_pass()

        self.history.observe(snap, civ)
        self.log(f"    {self.history.summary()}")

        act = actions.Actuator(snap, dry_run=self.dry_run, log=self.log,
                               remote_factory=self._get_remote)
        # Per-rule wall clock. A pass that outruns the turn length decides
        # against a game that has already moved on (§1).
        rule_times = {}
        pass_started = time.time()
        for name, fn in RULES:
            if name in self.skip:
                continue
            rule_started = time.time()
            try:
                if fn is expand.run_xpn02:
                    fn(snap, civ, act, vision=self.vision, top=self.top,
                       hist=self.history, log=self.log)
                elif fn in (exploit.run_food, explore.run_exp02,
                            exploit.run_facilities, exploit.run_jobs,
                            exploit.run_hurry, exploit.run_crew_supply,
                            exploit.run_conscript,
                            explore.run_exp01,
                            expand.run_xpn01,
                            exterminate.run_xtm00,
                            exterminate.run_xtm01,
                            exterminate.run_xtm03,
                            exterminate.run_xtm04,
                            exterminate.run_xtm05,
                            exterminate.run_xtm06,
                            exterminate.run_xtm07,
                            exterminate.run_xtm08,
                            exterminate.run_xtm09):
                    fn(snap, civ, act, self.history, log=self.log)
                else:
                    fn(snap, civ, act, log=self.log)
            except Exception as e:
                # One bad rule must not take the loop down mid-game.
                self.log(f"[loop] rule {name} raised {type(e).__name__}: {e}")
            finally:
                rule_times[name] = time.time() - rule_started
        # Record this pass's spending so next turn's income reading excludes it.
        self.history.note_cash_effect(act.cash_effect)
        # Intel read by the rules this pass.
        self.history.save()
        self.passes += 1
        self.log(f"    {act.summary()}")
        if self.remote is not None:
            self.log(f"    {self.remote.cache_summary()}")
        # In Single Player the launcher gates its Next Turn button on this
        # marker. It is written after every rule has run, atomically, so a
        # killed loop cannot leave a file that reads as a completed turn.
        self._mark_pass_done(snap.turn)
        if self.metrics is not None:
            self.metrics.record(snap, [self])

        elapsed = time.time() - pass_started
        slow = sorted(rule_times.items(), key=lambda kv: -kv[1])[:3]
        self.log(f"    scan {self.last_scan_secs:.1f}s"
                 + (f" x{self.last_scan_tries} tries"
                    if self.last_scan_tries > 1 else "")
                 + f", rules {elapsed:.1f}s"
                 + ("; slowest: "
                    + ", ".join(f"{n} {t:.1f}s" for n, t in slow if t >= 0.05)
                    if any(t >= 0.05 for _n, t in slow) else ""))

        # A pass that outlasts the driven turn writes orders while the engine
        # resolves them. Measured as scan plus rules.
        total = self.last_scan_secs + elapsed
        if self.drive and total > self.drive * 0.6:
            self.log(f"    [!] this pass took {total:.1f}s against a "
                     f"{self.drive}s turn. Orders are being written close to "
                     f"the boundary; raise --drive to at least "
                     f"{max(30, int(total * 3))}s.")

        # Checked after the rules, so R-XTM-03 has already committed the fleet.
        if self.halt_on_contact:
            self._check_contact(snap, civ, act)
        return snap.turn

    def _mark_pass_done(self, turn):
        try:
            os.makedirs(sensors.STATE_DIR, exist_ok=True)
            path = pass_marker_path(self.civ_name)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"civ": self.civ_name, "turn": turn,
                           "stamp": time.time(), "passes": self.passes}, fh)
            os.replace(tmp, path)
        except OSError as exc:
            # Without a marker the launcher falls back to a timeout.
            self.log(f"[loop] could not record the completed pass: {exc}")

    def _check_contact(self, snap, civ, act):
        """Set halt_reason once an enemy planet is legitimately visible."""
        try:
            foes = exterminate.enemies(snap, civ, act)
            if not foes:
                return
            seen = exterminate.enemy_planets(snap, civ, self.history, foes)
        except Exception as e:
            self.log(f"[loop] contact check raised {type(e).__name__}: {e}")
            return
        if not seen:
            return
        self.halt_reason = (f"CONTACT on turn {snap.turn}: {len(seen)} enemy "
                            f"planet(s) visible")

    def run(self, follow=False, max_turns=None, drive=None, stall=None):
        original = None
        self.drive = drive
        try:
            if drive:
                original = self.read_turn_length()
                if original in (None, 0xFFFFFFFF):
                    self.log("[loop] turn length is uninitialised; load a "
                             "galaxy first; not driving turns")
                    original = None
                else:
                    self.log(f"[loop] driving turns at {drive}s "
                             f"(was {original}s)")
                    self.set_turn_length(drive)

            t = self.one_pass()
            if t is None or not follow or self.halt_reason:
                return
            done = 1
            while max_turns is None or done < max_turns:
                nxt = self.wait_for_turn(t, timeout=stall, drive=drive)
                if nxt is None:
                    return
                t = self.one_pass()
                if t is None:
                    return
                if self.halt_reason:
                    return
                done += 1
            self.log(f"[loop] finished {done} turn(s)")
        except KeyboardInterrupt:
            self.log(f"\n[loop] interrupted after {self.passes} pass(es)")
        finally:
            if original is not None:
                self.set_turn_length(original)
                now = self.read_turn_length()
                # A dead process reads None.
                self.log(f"[loop] turn length restored to {now}s" if now
                         else "[loop] could not restore turn length; the "
                              "client is gone (it exited or crashed)")
            if self.remote is not None:
                self.remote.close()
            # Last, so it follows the turn length restore.
            if self.halt_reason:
                self.log(f"\n[loop] {self.halt_reason}\n"
                         f"[loop] stopping here. The fleet has its orders and "
                         f"turns are back to normal length; drive it yourself "
                         f"from the client and read the battle report.")


def wait_for_client(seconds, log=print):
    """Block until a client with a loaded galaxy is attachable, or give up
    after `seconds`. Returns True on success."""
    deadline = time.time() + seconds
    said = False
    while time.time() < deadline:
        state = gs.ev.ViewerState()
        if state.connect():
            try:
                snap = gs.Snapshot(state)
                if snap.civs and snap.suns:
                    return True
            except Exception:
                pass        # attached, galaxy not deserialised yet
        if not said:
            log(f"[loop] waiting up to {seconds}s for the game to finish "
                f"loading")
            said = True
        time.sleep(1.0)
    log(f"[loop] no readable game within {seconds}s; giving up")
    return False


def main():
    args = sys.argv[1:]
    def opt(n, d=None, cast=None):
        return cli.opt(args, n, d, cast)
    wait = opt("--wait-for-client", 0, int)
    if wait and not wait_for_client(wait):
        return 1
    loop = Loop(civ_name=opt("--civ"),
                dry_run=not cli.flag(args, "--apply"),
                vision=opt("--vision", "known"),
                top=opt("--top", 0, int),
                settle=opt("--settle", 1.5, float),
                halt_on_contact=cli.flag(args, "--halt-on-contact"))
    if opt("--metrics"):
        loop.metrics = metrics.Recorder(opt("--metrics"), log=loop.log)
    loop.run(follow=cli.flag(args, "--follow"),
             max_turns=opt("--turns", None, int),
             drive=opt("--drive", None, int),
             stall=opt("--stall", 300, int))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
