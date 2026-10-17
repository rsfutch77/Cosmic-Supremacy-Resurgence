"""
player_turn.py: serve one player their turn, and collect it back
=================================================================
    python player_turn.py serve   turn3.b64 --civ DemoPlayer
    python player_turn.py collect --name playerA
    python player_turn.py follow  --store <dir> --civ DemoPlayer

`serve` stamps the state with the civ this player controls, starts a client on
it, and stops the clock. `collect` takes the state back. `follow` is the
player-side loop the launcher drives: serve the current turn, capture and submit
through the turn, then wait for the referee to publish the next one.

The clock must be held: a client that reaches a turn boundary computes its own
turn, and its state then mixes the player's orders with an unauthorised tick.
The turn length lives at `0x0080AA08`, in seconds; the engine re-applies
`GSET.turnlength` at each boundary, so a large value written after load holds
until one occurs.

Requires `cs_server.py` on port 8888, which is where `collect` receives the blob.
"""
import argparse
import ctypes
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEV = os.path.join(REPO, "client", "dev_tools")
sys.path.insert(0, DEV)
sys.path.insert(0, os.path.join(DEV, "ai_player"))
sys.path.insert(0, os.path.join(HERE, "dev_tools"))

import save_parser as sp
import set_blob_player
from turn_store import TurnStore, open_store

HOLD_SECONDS = 86400          # a day; long enough that no boundary arrives
PLAYER_BUILD = "player"       # see game_cycle.resolve_exe for why

# Captures and uploads run on separate intervals.
CAPTURE_EVERY = 5.0      # seconds between client saves to local disk
UPLOADS_PER_TURN = 2     # store uploads per player per turn, spread evenly;
                         # a capture matching the stored one is not sent

# The gap between store reads scales with the distance to the deadline, on
# either side of it.
POLL_FLOOR = 2.0         # shortest gap, near the deadline
POLL_CEILING = 300.0     # longest gap, however far the deadline is
POLL_SHARE = 20.0        # gap is distance / POLL_SHARE


def poll_gap(distance: float, floor: float = POLL_FLOOR,
             ceiling: float = POLL_CEILING) -> float:
    """How long the store can be left alone, given the distance to a deadline.

    `distance` is signed: seconds until the deadline, negative once overdue.
    The gap is shortest near zero.
    """
    return max(floor, min(ceiling, abs(distance) / POLL_SHARE))


def hold_clock(seconds=HOLD_SECONDS, log=print):
    """Push the next turn boundary out of reach of a play session."""
    import advance_turns as at
    pid, name = at.find_pid()
    if not pid:
        raise SystemExit("no client to hold")
    access = (at.PROCESS_VM_READ | at.PROCESS_VM_WRITE |
              at.PROCESS_VM_OPERATION | at.PROCESS_QUERY_INFORMATION)
    h = at.kernel32.OpenProcess(access, False, pid)
    if not h:
        raise SystemExit(f"could not open pid {pid}")
    try:
        was = at.rd32(h, at.TURNLENGTH_ADDR)
        if was == 0xFFFFFFFF:
            raise SystemExit("turn length uninitialised; the galaxy is not up")
        at.wr32(h, at.TURNLENGTH_ADDR, seconds)
        log(f"  clock held: turn length {was}s -> {seconds}s")
        return was
    finally:
        at.kernel32.CloseHandle(h)


def describe_orders(served: bytes, submitted: bytes, civ: str) -> str:
    """A short account of what this submission carries, so an empty turn is
    visible in the log."""
    try:
        import order_diff
        lines = []
        mine, theirs, galaxy = order_diff.compare(
            served, submitted, civ, log=lines.append)
    except Exception as exc:                                # noqa: BLE001
        return f'could not summarise it ({type(exc).__name__})'
    if not (mine or theirs or galaxy):
        return 'NOTHING CHANGED, this turn carries no orders'
    # order_diff prefixes each object with a blank line.
    objs = []
    for line in lines:
        bare = line.lstrip('\n')
        if ' owner ' in bare and bare.startswith('  '):
            parts = bare.split()
            if len(parts) >= 2:
                objs.append(f'{parts[0]} {parts[1]}')
    what = ', '.join(objs[:4]) or 'changes'
    extra = ''
    if theirs:
        extra += f", {theirs} on another civ's objects, which will be dropped"
    if galaxy:
        extra += f', {galaxy} galaxy-level'
    return f'{mine} of yours ({what}){extra}'


def carries_orders(served: bytes, submitted: bytes, civ: str) -> bool:
    """Would the referee take anything out of this submission?

    True when merging it changes the served blob. A diff is not enough: the
    client changes a blob on its own (the `EXSY` cache, default `Unnamed` star
    names, a nondeterministic `KNPL` field). A submission that cannot be merged
    counts as carrying orders.
    """
    try:
        import merge_orders
        merged = merge_orders.merge(served, [(civ, submitted)],
                                    log=lambda *a: None)
        return merged != served
    except Exception:                                       # noqa: BLE001
        return True


def save_path_ready(host='127.0.0.1', port=8888, timeout=2.0):
    """(ok, why) for the path a captured turn has to travel.

    `SaveGame` posts to the stub server, so nothing listening means every
    capture fails. Checked before serving, while the turn can still be saved.
    """
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, ''
    except OSError as exc:
        return False, (f'nothing is listening on {host}:{port} ({exc.strerror or exc}), '
                       f'so SaveGame would fail and the turn would be lost at '
                       f'the deadline. Start it with: python server/cs_server.py')


def serve(blob: bytes, civ: str, work_dir=None, hold=HOLD_SECONDS,
          exe=PLAYER_BUILD, check_save_path=True, wait_for_lock=0.0,
          log=print):
    """Stamp, launch, hold. Returns the path served.

    `wait_for_lock` is how long to wait for another tool to finish with the
    client. Zero refuses at once.
    """
    import game_cycle as gc
    if check_save_path:
        ok, why = save_path_ready()
        if not ok:
            raise SystemExit(f'refusing to serve {civ}: {why}')
    work_dir = work_dir or os.path.join(HERE, "referee_work")
    os.makedirs(work_dir, exist_ok=True)
    stamped = set_blob_player.set_player(blob, civ, log=log)
    dat = os.path.join(work_dir, f"serve_{civ}.dat")
    with open(dat, "wb") as f:
        f.write(stamped)
    # The client lock is taken before any running client is closed.
    snap = gc.restart(dat, purpose=f"serving {civ}", exe=exe,
                      wait_for_lock=wait_for_lock)
    local = snap.local_civ()
    if local is None or local.civ_name != civ:
        raise SystemExit(f"served {civ} but the client came up as "
                         f"{local.civ_name if local else None}")
    log(f"  serving {civ} at turn {snap.turn}")
    if hold:
        hold_clock(hold, log=log)
    return dat


def collect(name="player", save_dir=None, log=print, announce=True) -> str:
    """Take the player's state back. Returns the capture path.

    `save_dir` is the stub server's data directory, where the capture lands.
    `announce` logs a successful capture; failures are logged regardless.
    """
    import game_cycle as gc
    if save_dir is None:
        return gc.capture_save(name[:15])

    before = time.time() - 1
    # In-process: in a frozen build sys.executable is the launcher, so a
    # subprocess on trigger_save.py would start a second launcher.
    rc, out = gc.trigger_save(name)
    if rc != 0:
        log(out)
        raise SystemExit("SaveGame did not report success")
    fresh = [os.path.join(save_dir, f) for f in os.listdir(save_dir)
             if f.endswith(".b64")
             and os.path.getmtime(os.path.join(save_dir, f)) >= before]
    if not fresh:
        raise SystemExit(f"SaveGame succeeded but no capture appeared in "
                         f"{save_dir}; is the launcher's server running?")
    path = max(fresh, key=os.path.getmtime)
    if announce:
        log(f"  captured {os.path.basename(path)}")
    return path


def close():
    import game_cycle as gc
    gc.close_client()


def report_refusals(store: TurnStore, civ: str, turn: int, log=print,
                    emit=None) -> int:
    """Print what the referee refused from this civ's turn, and any other note
    it left. Returns how many changes were refused.

    Refusals and other notes (welcome, missed-turns warning) are printed under
    separate headings, and emitted as `refused_orders` and `warned`. A failure
    to read the note is logged and the loop goes on.

    The missed-turns warning is read from `turn + 1`'s note, where
    `abandonment.enforce` writes it; one on `turn`'s own note was already shown.
    """
    try:
        lines = store.note(civ, turn)
    except Exception as exc:                                # noqa: BLE001
        log(f"[{civ}] turn {turn}: could not read the referee's notes ({exc})")
        return 0
    try:
        ahead = warning_lines(store.note(civ, turn + 1) or [])
    except Exception:                                       # noqa: BLE001
        ahead = []
    if not lines and not ahead:
        return 0
    welcome, refused, told = note_parts(lines or [])
    told = [line for line in told if line not in warning_lines(lines or [])]
    if refused:
        log(f"[{civ}] turn {turn}: the referee refused {len(refused)} of your "
            f"change(s):")
        for line in refused:
            log(f"[{civ}]     {line}")
    if welcome or told:
        log(f"[{civ}] turn {turn}: the referee left you a note:")
        for line in welcome + told:
            log(f"[{civ}]     {line}")
    if ahead:
        log(f"[{civ}] turn {turn + 1}: the referee left you a note:")
        for line in ahead:
            log(f"[{civ}]     {line}")
    if emit:
        if refused:
            emit("refused_orders", turn=turn, civ=civ, count=len(refused),
                 lines=refused)
        if ahead:
            emit("warned", turn=turn + 1, civ=civ, lines=ahead)
    return len(refused)


# A referee's note is up to three parts, in order: a welcome (`joins`), the
# merge's refusals, and a missed-turns warning or reclaim notice
# (`abandonment`). These patterns must match the first lines of
# `abandonment.warning_note` and `abandonment.reclaim_note` and every line of
# `joins.welcome_note`; `test_refusal_notes.py` checks them.
WARNING_OPENS = re.compile(r"You have missed \d+ turns? in a row\.")
RECLAIM_OPENS = re.compile(r"This civ was wiped at turn \d+ after \d+ missed "
                           r"turns? in a row\.")
WELCOME_LINES = (
    re.compile(r"You joined this galaxy as .+ at turn \d+\."),
    re.compile(r"Your homeworld is .+, planet #\S+, in .+\."),
    re.compile(r"It is the only thing you own\. Everything else is yours to "
               r"build\."),
)


def warning_lines(lines) -> list:
    """The part of a referee's note that warns a silent player, or []."""
    for i, line in enumerate(lines):
        if WARNING_OPENS.fullmatch(line.strip()):
            return list(lines[i:])
    return []


def note_parts(lines) -> tuple:
    """(welcome, refused, told): a referee's note split into its three parts.

    `welcome` is the leading welcome block, `told` the warning or reclaim
    notice from its first line to the end, and `refused` what lies between.
    An unrecognised line counts as a refusal.
    """
    lines = [str(x) for x in lines]
    head = 0
    while (head < len(WELCOME_LINES) and head < len(lines)
           and WELCOME_LINES[head].fullmatch(lines[head].strip())):
        head += 1
    if head < len(WELCOME_LINES):
        head = 0
    tail = len(lines)
    for i in range(head, len(lines)):
        text = lines[i].strip()
        if WARNING_OPENS.fullmatch(text) or RECLAIM_OPENS.fullmatch(text):
            tail = i
            break
    return lines[:head], lines[head:tail], lines[tail:]


def failure_text(exc) -> str:
    """What went wrong with a store call, in the relay's own sentence when it
    gave one.

    Appends the `error` field of an HTTPError's JSON body. Reads the body, so
    use it only for an exception that is not re-raised.
    """
    text = str(exc)
    code = getattr(exc, "code", None)
    read = getattr(exc, "read", None)
    if isinstance(code, int) and callable(read):
        try:
            import json
            body = json.loads(read() or b"{}")
            said = body.get("error") if isinstance(body, dict) else None
        except Exception:                                   # noqa: BLE001
            said = None
        if isinstance(said, str) and said.strip():
            text = f"{text}: {said.strip()}"
    return text


def follow(store: TurnStore, civ: str, poll: float = 5.0, rounds: int = 0,
           on_state=None, exe=PLAYER_BUILD, save_dir=None, stop=None,
           poll_max: float = POLL_CEILING,
           capture_every: float = CAPTURE_EVERY,
           uploads_per_turn: int = UPLOADS_PER_TURN,
           submit_every: float = None, send_now=None, reopen=None,
           log=print, clock=time.time, sleep=time.sleep):
    """Play one civ's turns as the store publishes them.

    One pass is: serve the current turn, wait until its deadline, take the
    player's state back, submit it, then wait for the referee to publish the
    next one. `rounds` of 0 means keep going.

    `on_state(kind, **facts)` is called at each step so a UI can narrate.
    `stop()` is checked wherever this would sleep, so a UI can end the loop
    without interrupting a capture.

    The store is read on a schedule set by `poll_gap` from the known deadline,
    between `poll` and `poll_max`; the countdown and capture cadence run off
    the deadline between reads.

    `send_now` and `reopen` are events (`is_set()` and `clear()`). `send_now`
    captures and sends at once (the launcher's Save button). `reopen` ends the
    wait for the referee and serves the turn again, from the player's own
    submission. Both go through this loop because it owns the client.

    The state is captured every `capture_every` seconds and uploaded
    `uploads_per_turn` times across the turn, the last at the deadline.
    `submit_every` sets a fixed upload interval instead. A capture identical to
    what was last stored is not sent.

    `clock` and `sleep` are parameters so a test can drive the loop.
    """
    def emit(kind, **facts):
        if on_state:
            on_state(kind, **facts)

    def halted():
        return bool(stop and stop())

    # Whether a turn is being played; `send_now` is acted on only then, so a
    # request between turns does not wake every nap.
    playing = [False]

    def asked():
        """Has a player asked for their turn to go now, and can it go?"""
        return bool(playing[0] and send_now is not None and send_now.is_set())

    def wants_turn():
        """Has a player asked for the game window back while waiting for the
        referee?"""
        return bool(not playing[0] and reopen is not None
                    and reopen.is_set())

    def nap(seconds, until=None):
        """Sleep, but wake early if asked to stop, to send now, or for `until`.

        `until` is an extra wake condition. The naps that retry an unreadable
        store must not pass one, or the retry would spin. Sleeps in half-second
        steps so stop and send requests are answered promptly.
        """
        end = clock() + seconds
        while clock() < end:
            if halted() or asked() or (until is not None and until()):
                return
            sleep(min(0.5, max(0.0, end - clock())))

    # Whether the last store read failed. Losing and regaining the store are
    # each emitted once, as `unreachable` and `reachable`, so a UI can tell an
    # unreachable store from a stopped referee.
    cut_off = [False]

    def read_current():
        """`store.current()`, saying when the store is lost and found again."""
        try:
            got = store.current()
        except OSError as exc:
            if not cut_off[0]:
                cut_off[0] = True
                emit("unreachable", civ=civ, error=failure_text(exc))
            raise
        if cut_off[0]:
            cut_off[0] = False
            emit("reachable", civ=civ)
        return got

    played = 0
    while not halted():
        def current_or_wait():
            """(turn, deadline), retrying while the store cannot be read.
            None once stopped."""
            while not halted():
                try:
                    return read_current()
                except OSError as exc:
                    log(f"[{civ}] store unreadable ({exc.__class__.__name__}), "
                        f"retrying in {poll}s")
                    nap(poll)
            return None

        got = current_or_wait()
        if got is None:
            break
        turn, deadline = got

        blob = store.turn_blob(turn)

        # `blob` is the authoritative turn and the baseline for every diff.
        # `to_load` is what goes into the client, which on a restart is the
        # player's own submission.
        to_load = blob
        prior = store.submission(civ, turn)
        if prior is not None:
            if carries_orders(blob, prior, civ):
                # Resume from the submission. Serving the pristine turn would
                # discard the player's orders and then overwrite them.
                to_load = prior
                log(f"[{civ}] turn {turn}: resuming from your submission "
                    f"({len(prior):,} bytes, it carries orders)")
                emit("resuming", turn=turn, civ=civ)
            else:
                # An orderless submission has nothing to preserve.
                log(f"[{civ}] turn {turn}: a submission exists but carries no "
                    f"orders, serving the turn fresh")
        emit("serving", turn=turn, civ=civ)
        log(f"[{civ}] turn {turn}: serving")
        try:
            serve(to_load, civ, hold=HOLD_SECONDS, exe=exe,
                  wait_for_lock=240.0, log=log)
        except BaseException as exc:                        # noqa: BLE001
            # A failed serve costs this turn only; the loop must keep going.
            log(f"[{civ}] turn {turn}: COULD NOT SERVE, {exc}")
            emit("serve_failed", turn=turn, civ=civ, error=str(exc))
            try:
                close()
            except BaseException:                           # noqa: BLE001
                pass
            # Wait this turn out, then try again on the next one.
            while not halted():
                got = current_or_wait()
                if got is None or got[0] != turn:
                    break
                nap(poll_gap(got[1] - clock(), poll, poll_max))
            continue

        if reopen is not None:
            # The game is open, so any pending reopen request is answered.
            reopen.clear()

        sent = None                     # the last blob this turn actually stored
        held = None                     # the last blob taken out of the client

        def take(final=False):
            """Capture the player's state to local disk."""
            nonlocal held
            emit("capturing", turn=turn, civ=civ)
            capture = collect(f"{civ[:8]}t{turn}", save_dir=save_dir, log=log,
                              announce=final)
            held = sp.load_any(capture)
            emit("captured", turn=turn, civ=civ, pending=held != sent)
            return held

        def send(final):
            """Store the last capture, if the store has not got it already."""
            nonlocal sent
            mine = held
            if mine is None or mine == sent:
                return True

            # Never replace a submission this pass did not write (`sent`) with
            # an orderless capture. Replacing this pass's own submission is
            # allowed, so a player can cancel an order.
            existing = store.submission(civ, turn)
            if (existing is not None and existing != sent
                    and not carries_orders(blob, mine, civ)):
                # Only warn when the existing submission carries orders.
                if carries_orders(blob, existing, civ):
                    log(f"[{civ}] turn {turn}: REFUSING to submit, this "
                        f"capture carries no orders and would overwrite a "
                        f"{len(existing):,}-byte submission carrying orders "
                        f"that this loop did not write. Leaving it standing.")
                    emit("refused", turn=turn, civ=civ)
                else:
                    log(f"[{civ}] turn {turn}: nothing played yet, leaving the "
                        f"existing empty submission alone")
                return True

            store.submit(civ, turn, mine)
            landed = store.submission(civ, turn)
            if landed != mine:
                raise RuntimeError(
                    f"submission for turn {turn} did not land in the store: "
                    f"wrote {len(mine):,} bytes, read back "
                    + (f"{len(landed):,}" if landed is not None else "nothing")
                    + ". Can this machine WRITE to the store?")
            sent = mine
            log(f"[{civ}] turn {turn}: submitted {len(mine):,} bytes"
                + (" (final)" if final else "")
                + f", {describe_orders(blob, mine, civ)}")
            emit("submitted", turn=turn, civ=civ, final=final)
            return True

        def push(final):
            """Capture and store, which is what a deadline and a Save both mean."""
            try:
                take(final=final)
            except BaseException as exc:                    # noqa: BLE001
                # At the deadline the client may already be closed; send the
                # last capture taken.
                if final and held is not None:
                    log(f"[{civ}] turn {turn}: could not take a last capture "
                        f"({exc}); sending the {len(held):,} bytes already "
                        f"captured")
                else:
                    raise
            return send(final)

        # The upload interval divides the time left in the turn by the budget.
        left0 = deadline - clock() if deadline else None
        if submit_every is not None:
            gap = float(submit_every)
        elif left0 and left0 > 0:
            gap = max(capture_every, left0 / max(1, uploads_per_turn))
        else:
            # No readable clock: upload at the deadline only.
            gap = float("inf")
        log(f"[{civ}] turn {turn}: capturing every {capture_every:g}s, "
            + ("sending at the deadline only" if gap == float("inf")
               else f"sending every {gap:g}s and again at the deadline"))

        started = clock()
        last_capture = started
        last_upload = started
        # Read at once: the deadline read before the serve may be stale.
        next_read = started
        playing[0] = True

        while not halted():
            left = deadline - clock()
            if clock() >= next_read:
                # A failed read is retried; the loop must not exit on it.
                try:
                    cur, deadline = read_current()
                except OSError as exc:
                    log(f"[{civ}] turn {turn}: store unreadable ({exc.__class__.__name__}), "
                        f"retrying in {poll}s")
                    nap(poll)
                    continue
                left = deadline - clock()
                next_read = clock() + poll_gap(left, poll, poll_max)
                if cur != turn:
                    # The turn closed while playing (late join, sleep).
                    log(f"[{civ}] turn {turn} closed while playing; skipping submit")
                    emit("overtaken", turn=turn, current=cur)
                    break
            if left <= 0:
                emit("collecting", turn=turn, civ=civ)
                log(f"[{civ}] turn {turn}: time is up, collecting")
                try:
                    push(final=True)
                except Exception as exc:                    # noqa: BLE001
                    # This turn is lost; log it and carry on to the next.
                    why = failure_text(exc)
                    log(f"[{civ}] turn {turn}: LOST, {why}")
                    emit("lost", turn=turn, civ=civ, error=why)
                break

            now = clock()
            # A send request captures and uploads at once.
            wanted = asked()
            if wanted:
                send_now.clear()
                log(f"[{civ}] turn {turn}: sending now, you asked")

            if wanted or now - last_capture >= capture_every:
                last_capture = now
                try:
                    take()
                except (Exception, SystemExit) as exc:      # noqa: BLE001
                    # A failed capture is logged and retried next cadence
                    # (`collect` raises SystemExit). If the client has closed,
                    # send the last capture and end the turn. `client_pids`
                    # spawns PowerShell, so it is checked only after a failure.
                    import game_cycle as gc
                    if not gc.client_pids():
                        log(f"[{civ}] turn {turn}: the game has closed, "
                            f"sending what was captured before it did")
                        try:
                            # `send` emits `submitted` itself.
                            send(final=True)
                        except Exception as bad:            # noqa: BLE001
                            why = failure_text(bad)
                            log(f"[{civ}] turn {turn}: LOST, {why}")
                            emit("lost", turn=turn, civ=civ, error=why)
                        break
                    log(f"[{civ}] turn {turn}: could not capture, {exc}")
                    emit("capture_failed", turn=turn, civ=civ, error=str(exc))

            if wanted or now - last_upload >= gap:
                last_upload = now
                try:
                    send(final=False)
                except Exception as exc:                    # noqa: BLE001
                    # A failed interim write is logged; the next one retries.
                    why = failure_text(exc)
                    log(f"[{civ}] turn {turn}: interim submit failed, {why}")
                    emit("send_failed", turn=turn, civ=civ, error=why)

            emit("playing", turn=turn, civ=civ, seconds_left=left)
            # Wake for the next capture, the next store read, or the deadline,
            # whichever comes first.
            due = last_capture + capture_every - clock()
            nap(max(0.1, min(due if due > 0 else capture_every, left,
                             next_read - clock())))

        playing[0] = False
        if send_now is not None:
            # A send request must not carry into the next turn.
            send_now.clear()
        close()
        played += 1
        if rounds and played >= rounds:
            emit("done", turns=played)
            return played
        if halted():
            break

        emit("waiting", turn=turn, civ=civ)
        log(f"[{civ}] waiting for the referee to publish past turn {turn}")
        reopening = False
        while not halted():
            got = current_or_wait()
            if got is None or got[0] != turn:
                break
            if reopen is not None and reopen.is_set():
                reopen.clear()
                reopening = True
                break
            # The next turn cannot appear before this deadline, so the gap
            # backs off far from it.
            nap(poll_gap(got[1] - clock(), poll, poll_max), until=wants_turn)
        if reopening:
            # Go round the outer loop, which serves the player's own
            # submission and restarts the capture and upload cadences.
            log(f"[{civ}] turn {turn}: opening the game again, you asked")
            emit("reopening", turn=turn, civ=civ)
            continue

        # The turn has closed, so the referee's note now exists.
        report_refusals(store, civ, turn, log=log, emit=emit)

    emit("stopped", turns=played)
    return played


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve")
    s.add_argument("blob")
    s.add_argument("--civ", required=True)
    s.add_argument("--hold", type=int, default=HOLD_SECONDS,
                   help="turn length to write after load; 0 leaves it alone")
    s.add_argument("--exe", default=PLAYER_BUILD,
                   help="client build: testbed, resurgence, or a path")

    c = sub.add_parser("collect")
    c.add_argument("--name", default="player")
    c.add_argument("--save-dir", help="where the capture will land; defaults "
                                      "to the checkout's server/saves")

    f = sub.add_parser("follow")
    f.add_argument("--store", required=True)
    f.add_argument("--civ", required=True)
    f.add_argument("--rounds", type=int, default=0,
                   help="stop after this many turns; 0 keeps going")
    f.add_argument("--poll", type=float, default=5.0,
                   help="the tightest the store is read, which is what "
                        "the last stretch before a deadline uses")
    f.add_argument("--poll-max", type=float, default=POLL_CEILING,
                   help="the slackest it is read, far from a deadline")
    f.add_argument("--exe", default=PLAYER_BUILD)
    f.add_argument("--capture-every", type=float, default=CAPTURE_EVERY,
                   help="seconds between captures, which cost nothing but a "
                        "SaveGame and a local write")
    f.add_argument("--uploads-per-turn", type=int, default=UPLOADS_PER_TURN,
                   help="how many times a turn may be sent to the store, the "
                        "one at the deadline included; the interval comes from "
                        "the turn's own length")
    f.add_argument("--submit-every", type=float,
                   help="a fixed interval between uploads instead, in seconds, "
                        "which spends the month's quota by the clock rather "
                        "than by the turn")

    a = ap.parse_args()
    if a.cmd == "serve":
        print(serve(sp.load_any(a.blob), a.civ, hold=a.hold, exe=a.exe))
    elif a.cmd == "collect":
        print(collect(a.name, save_dir=a.save_dir))
    else:
        store = open_store(a.store)
        if not store.exists():
            raise SystemExit(f"no galaxy at {a.store}")
        follow(store, a.civ, poll=a.poll, poll_max=a.poll_max,
               rounds=a.rounds, exe=a.exe,
               capture_every=a.capture_every,
               uploads_per_turn=a.uploads_per_turn,
               submit_every=a.submit_every)


if __name__ == "__main__":
    sys.exit(main())
