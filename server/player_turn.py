"""
player_turn.py , serve one player their turn, and collect it back
=================================================================
    python player_turn.py serve   turn3.b64 --civ DemoPlayer
    python player_turn.py collect --name playerA
    python player_turn.py follow  --store <dir> --civ DemoPlayer

`serve` stamps the state with the civ this player controls, starts a client on
it, and stops the clock. `collect` takes the state back.

`follow` is the whole player-side loop, and it is what the launcher drives: wait
for the turn a `TurnStore` says is current, serve it, let the player play, and at
the deadline take their state back, submit it, and wait for the next turn. A
player does nothing manual between turns, which is the point: the `.dat` push
path is startup-only, so their client has to restart every turn, and the only way
that is acceptable is if nobody has to think about it.

Stopping the clock is not a nicety. A player's client that reaches a turn
boundary computes its own turn, and the state it hands back then mixes the
player's orders with a simulation the server never authorised. Extracting
orders from that is unsound: a diff of a ticked state against the state served
shows every consequence of the tick as well, and copying an order out of it
copies post-tick positions with it. Measured, a client left at turn 3 for a few
minutes returned turn 4 with 174 sections changed instead of one.

The clock lives at `0x0080AA08`, in seconds, and the engine re-applies
`GSET.turnlength` at every boundary, so a large value written after load holds
only while no boundary occurs, which is the point. This is the blunt version of
what the launcher is meant to do: own the turn clock, show the countdown, and
close the client when time is up.

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

# ── The two cadences a turn runs on ──────────────────────────────────────────
# Taking a capture and sending one are not the same kind of act and must not
# share an interval.
#
# A capture is a `SaveGame` in the client and a write to this disk. Nothing
# meters it, so the only thing setting its interval is how much thought a
# failure may cost and how often the client can be asked without the saves
# overlapping. `collect` triggers the save and then looks for the file the stub
# server wrote; the launcher's own Save allows 1.5 seconds for one to land, so
# five seconds leaves the client alone between captures and still bounds the
# loss at five seconds of play rather than twenty.
#
# Sending a capture to the store is a Cloud Storage Class A operation, and
# docs\\Public_Beta_Plan.md H5 measures those as the binding quota for the whole
# beta: 5,000 a month. Its own arithmetic is 182 four-hour turns a month and
# 1,456 operations at six players saving once, so 1,092 of those are the
# players (6 x 182) and the remaining 364 are the referee's two per tick. That
# leaves 5,000 - 364 = 4,636 for players, and
#
#     4,636 / (182 turns x 10 players) = 2.5 uploads per player per turn
#
# at the ten players H5 names as the size that goes over at three saves each.
# Two is therefore the budget, not a round number: ten players at two uploads a
# turn is 364 + 3,640 = 4,004 operations, 80% of the allowance, and six players
# is 2,548, half of it.
#
# The budget is per turn rather than per second, because that is how the quota
# is actually spent, so the upload interval is derived from the turn a galaxy
# publishes: a budget of two on a four-hour turn is one upload at the halfway
# point and one at the deadline, and the same budget on a ten-minute test
# galaxy is one at five minutes and one at the deadline. A capture identical to
# what the store already holds is skipped either way, which is what keeps an
# idle turn costing nothing at all.
CAPTURE_EVERY = 5.0
UPLOADS_PER_TURN = 2

# ── How often the store is read ──────────────────────────────────────────────
# Reading the store is a Firestore document read, and docs\Public_Beta_Plan.md
# H5 measures 50,000 of those a day as the whole project's free allowance. A
# fixed two-second poll held through a turn and through the wait after it is
# 43,200 reads a day for one player, 86% of that allowance, which is why two
# players could not both leave a launcher open.
#
# What a poll is for is noticing that the deadline arrived, and every read
# hands the deadline over. So the gap to the next read is taken from the
# distance to that deadline rather than from a constant.
#
# POLL_SHARE is the fraction of that distance this loop is willing to be late
# by. Sleeping a twentieth of it wakes with 95% of the wait still ahead, so a
# turn published before its deadline, or a deadline that moved, costs at most a
# twentieth of what was left rather than a whole fixed interval. The same rule
# runs on the far side of a deadline, where the distance is how overdue the
# turn is: N5 measures the referee's tick at about ten seconds, so a turn
# published ten seconds late is picked up on the next read rather than waited
# for, and a referee that has been down for an hour is asked every three
# minutes instead of every two seconds.
#
# POLL_FLOOR is the last stretch before a deadline, and is the interval the
# launcher used everywhere. Nothing near a deadline got slower than it was.
#
# POLL_CEILING puts a read every five minutes however far away the deadline
# is, so a clock that changed under this loop is still noticed.
#
# The arithmetic, one player with a launcher left open all day. Backing off
# from a distance L to POLL_FLOOR x POLL_SHARE = 40 seconds, where the floor
# takes over, is ln(L/40)/ln(20/19) reads, about 19.5 ln(L/40), and the floor
# itself is another 20.
#
#   4-hour turns, 6 a day   28 + 19.5 ln(6000/40) + 20 + 5 = 151 a turn
#                           151 x 6                        = 906 a day, 1.8%
#   15-minute turns, 96     19.5 ln(900/40) + 20 + 5       =  86 a turn
#                            86 x 96                       = 8,256 a day, 17%
#
# The 28 is the ceiling doing the first 8,400 seconds of a four-hour turn and
# the 5 is the referee's tick. So the allowance holds 55 players at four-hour
# turns and 6 at fifteen-minute ones, against one either way before this.
POLL_FLOOR = 2.0
POLL_CEILING = 300.0
POLL_SHARE = 20.0


def poll_gap(distance: float, floor: float = POLL_FLOOR,
             ceiling: float = POLL_CEILING) -> float:
    """How long the store can be left alone, given the distance to a deadline.

    `distance` is signed: seconds until the deadline while a turn is open, and
    minus how overdue it is once the deadline has passed. Both matter and both
    are tightest at zero, which is the one moment worth watching closely.
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
    """A short account of what this submission actually carries.

    A submission that writes cleanly and reads back byte for byte can still be
    a turn where the player's click never reached the blob, and a log line
    saying only that some bytes were stored cannot tell the two apart. This
    says what changed and whose it is, so an empty turn is visible as an empty
    turn while it can still be acted on.
    """
    try:
        import order_diff
        lines = []
        mine, theirs, galaxy = order_diff.compare(
            served, submitted, civ, log=lines.append)
    except Exception as exc:                                # noqa: BLE001
        return f'could not summarise it ({type(exc).__name__})'
    if not (mine or theirs or galaxy):
        return 'NOTHING CHANGED, this turn carries no orders'
    # order_diff prefixes each object with a blank line, so strip before
    # matching: testing startswith on the raw line quietly matched nothing and
    # the summary said "changes" for everything.
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

    Asks the **merge**, not the diff. A client changes a blob for reasons that
    have nothing to do with the player: it populates the civ's `EXSY` cache the
    first time it opens a galaxy, it materialises the default star name
    `Unnamed` over every empty one, and `KNPL` carries a field that is not
    deterministic. `order_diff` counts all of those as changes, so a turn
    nobody played reports as a turn with orders in it.

    That matters because of what this answer is used for. The guard in `follow`
    refuses to replace someone else's submission with one that carries nothing;
    if every capture "carries orders", the guard never fires and the turn-12
    failure it exists to prevent is live again. Measured in the 19 September
    rehearsal: an unplayed turn 0 reported `1 of yours (OWNR 202)`, and it was
    the `EXSY` cache filling in.

    The merge already knows the difference, because deciding what is an order
    is its whole job. If merging changes the authoritative blob, there was an
    order in here; if it does not, there was not.

    Still conservative on failure: a submission that cannot be merged counts as
    carrying orders, because this answer only ever licenses throwing one away.
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

    A turn is lost anywhere along serve, play, save, capture, submit, and the
    save link is the one nothing was watching. `SaveGame` posts to the stub
    server, so if nothing is listening the capture fails, and it fails at the
    deadline, when the turn has already been played and cannot be replayed.
    That is exactly how a player lost a turn the first time two machines ran
    together: the run looked healthy for its whole length because serving never
    touches the save path.

    Checked before serving rather than at collect, because the only useful time
    to find out is before the turn is spent.
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
    client. Zero refuses at once, which is what somebody at a command line
    wants; `follow` passes a real number, because a player's loop that gives up
    because the referee was mid-tick has failed their turn over a few seconds.
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
    # Lock first, close second. Serving a turn used to close whatever was
    # running before `launch` reached the lock, so two tools racing ended with
    # one client killed rather than one caller refused.
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

    `save_dir` is where the capture will land, which is wherever the stub server
    was told to keep its data. The launcher runs the server against its own data
    directory rather than the checkout's, so the caller has to say.

    `announce` is off for the captures a turn takes on its own cadence. One
    line per capture every few seconds buries the lines that matter in a log a
    player is asked to read when something goes wrong. What failed is still
    said: the failure path logs and raises whatever `announce` is.
    """
    import game_cycle as gc
    if save_dir is None:
        return gc.capture_save(name[:15])

    before = time.time() - 1
    # game_cycle.trigger_save rather than a subprocess on trigger_save.py:
    # sys.executable is the launcher executable in a frozen build, so that
    # command line starts a second launcher and no save is taken.
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

    Refusals go under a heading that counts them, and a welcome or a
    missed-turns warning under one that calls it a note, so neither is
    printed as a refused change. `refused_orders` carries the refusals and
    `warned` the warning.

    The player's half of the refusal note. Everything the merge drops is
    already written down; until this it was written down on the referee's
    machine, and the player's launcher never saw it. Two of the first
    two-machine rehearsal's six findings were silent for exactly that reason: a
    system rename and a conscription, both accepted by the client, both dropped
    by the merge, both simply gone the next turn.

    No new UI, which is what was asked for. `log` is the launcher's log pane.

    Reading the note can fail , it lives on a share another machine writes ,
    and a turn that has already been played is not worth unwinding the loop
    for, so a failure here says so and the loop goes on.
    """
    try:
        lines = store.note(civ, turn)
    except Exception as exc:                                # noqa: BLE001
        log(f"[{civ}] turn {turn}: could not read the referee's notes ({exc})")
        return 0
    if not lines:
        return 0
    welcome, refused, told = note_parts(lines)
    if refused:
        log(f"[{civ}] turn {turn}: the referee refused {len(refused)} of your "
            f"change(s):")
        for line in refused:
            log(f"[{civ}]     {line}")
    if welcome or told:
        log(f"[{civ}] turn {turn}: the referee left you a note:")
        for line in welcome + told:
            log(f"[{civ}]     {line}")
    if emit:
        if refused:
            emit("refused_orders", turn=turn, civ=civ, count=len(refused),
                 lines=refused)
        warning = warning_lines(lines)
        if warning:
            emit("warned", turn=turn, civ=civ, lines=warning)
    return len(refused)


# A referee's note for one civ and turn is one list of lines with up to three
# parts, in this order. `joins` writes a welcome at the head of the first turn
# a newcomer plays. The referee appends what the merge refused. `abandonment`
# appends a missed-turns warning, or the notice that the seat was reclaimed,
# after anything already there. These patterns are the first lines of
# `abandonment.warning_note` and `abandonment.reclaim_note` and every line of
# `joins.welcome_note`; `test_refusal_notes.py` checks them against the
# functions' own output, since this module is bundled into the launcher and
# does not import either.
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
    """The part of a referee's note that warns a silent player, or [].

    Named here so a launcher reads the `warned` event rather than matching the
    note's wording itself.
    """
    for i, line in enumerate(lines):
        if WARNING_OPENS.fullmatch(line.strip()):
            return list(lines[i:])
    return []


def note_parts(lines) -> tuple:
    """(welcome, refused, told): a referee's note split into its three parts.

    `welcome` is the leading welcome block, `told` the warning or reclaim
    notice from its first line to the end, and `refused` what lies between,
    which is the merge's refusals. A line nothing recognises counts as a
    refusal, since that is what the note held before it held anything else.
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

    A relay refusal is an HTTPError whose text is only the status line, "HTTP
    Error 400: Bad Request", and whose body holds the sentence that says why.
    That line is all a turn loop's log kept of a lost turn, and the log is what
    the operator reads when nobody watched the turn fail. The body is read
    here, so this is only for an exception the loop does not raise on.
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

    `on_state(kind, **facts)` is called at each step so a UI can narrate without
    this function knowing what a UI is. The launcher passes one; the command line
    does not.

    `stop()` is checked wherever this would otherwise sleep, so a UI can end the
    loop between turns without killing the thread mid-capture and losing the
    player's orders.

    **The store is read against the deadline it has already given, not on a
    fixed interval.** `poll` is the floor of that schedule rather than all of
    it, and `poll_max` the ceiling: see POLL_SHARE above for what sets the
    gap between them and what a day of it costs. The deadline is an absolute
    time, so between reads this loop knows how long is left by subtracting, and
    the capture cadence and the countdown run off that rather than off the
    store.

    `send_now` is anything with `is_set()` and `clear()`, a `threading.Event` in
    the launcher. Setting it captures and sends at once instead of waiting for
    the next cadence, which is what the launcher's Save button means during a
    multiplayer turn. It goes through here rather than through a second call
    into the client, because this loop is already driving that client and two
    things running `SaveGame` on one client at once is a race nobody can see.

    `reopen` is an event of the same shape, and it is the other half of the
    game window closing. This loop outlives that window: once the turn is sent
    it waits here for the referee, which at four hours a turn is most of the
    day, and a player who wants to look at the galaxy again, or read a message
    in it, has nothing to press. Setting this ends that wait and takes the turn
    round again, so the turn is opened through the same path as any other and
    the player is served their own submission rather than the store's pristine
    blob. It is an event rather than a second call into `serve` for the reason
    `send_now` is: this loop owns the client, and a second thing starting one
    is what the lock in `game_cycle` exists to prevent.

    **A player's state is captured and submitted throughout the turn, not once
    at the deadline.** Submitting once put the whole turn on a single write
    landing in a narrow window, and the first time two machines played, one
    player's orders were lost to exactly that: their submission never reached
    the store, and the referee closed the turn reporting a missing player, which
    reads as somebody who did not turn up rather than a write that failed.

    **The capture and the upload run on separate cadences**, because they cost
    different things: see CAPTURE_EVERY and UPLOADS_PER_TURN above for the
    quota arithmetic that sets them. `capture_every` seconds between captures,
    `uploads_per_turn` uploads across the turn including the one at the
    deadline, and the last capture of the turn is always sent before it closes.
    `submit_every` overrides the derived upload interval with a fixed number of
    seconds, which is what the command line's switch sets.

    A capture identical to what the store already holds is not sent, so an idle
    turn costs one upload rather than one per interval.

    `clock` and `sleep` are the loop's own time. They are parameters so a test
    can drive several intervals without waiting for them.
    """
    def emit(kind, **facts):
        if on_state:
            on_state(kind, **facts)

    def halted():
        return bool(stop and stop())

    # Whether a turn is being played right now, which is the only time a
    # request to send one can be acted on. Without it, a request made while the
    # loop is waiting for the referee wakes every nap it ever takes and the
    # wait becomes a spin on the store.
    playing = [False]

    def asked():
        """Has a player asked for their turn to go now, and can it go?"""
        return bool(playing[0] and send_now is not None and send_now.is_set())

    def wants_turn():
        """Has a player asked for the game window back, with a wait to end?

        The mirror of `asked()` on the other side of a turn, and gated the same
        way: a request can only be acted on while this loop is waiting for the
        referee, and it is cleared where it is acted on.
        """
        return bool(not playing[0] and reopen is not None
                    and reopen.is_set())

    def nap(seconds, until=None):
        """Sleep, but wake early if asked to stop, to send now, or for `until`.

        `until` is this particular wait ending for a reason of its own, which
        between turns is a player pressing Play. It is a parameter rather than
        another clause in here because the naps that retry an unreadable store
        must not take it: nothing clears that request while the store cannot be
        read, so a nap waking for it there would return at once and turn the
        retry into a spin on a store that is already failing.

        Sleeping in half seconds rather than in one go is what keeps stop and
        Send Turn answered inside half a second of being asked, whatever the
        schedule above decided the gap should be.
        """
        end = clock() + seconds
        while clock() < end:
            if halted() or asked() or (until is not None and until()):
                return
            sleep(min(0.5, max(0.0, end - clock())))

    played = 0
    while not halted():
        def current_or_wait():
            """(turn, deadline), waiting out a store that cannot be read.

            Every call into the store crosses a network share that another
            machine rewrites, so any of them can fail for a moment. Returning
            None lets the caller keep waiting instead of unwinding the loop.
            """
            while not halted():
                try:
                    return store.current()
                except OSError as exc:
                    log(f"[{civ}] store unreadable ({exc.__class__.__name__}), "
                        f"retrying in {poll}s")
                    nap(poll)
            return None

        got = current_or_wait()
        if got is None:
            break
        turn, deadline = got

        # Never re-serve a turn this civ has already submitted for.
        #
        # Serving is destructive: it closes whatever client is running and
        # relaunches on the store's pristine turn blob. If the player has
        # already played this turn, that throws away what they played AND the
        # orderless state that replaces it then overwrites their submission,
        # because `submit` is last-write-wins. Measured: a loop restarted with
        # 22 seconds left on turn 12 took the player's client away, submitted
        # 39,128 bytes reading NOTHING CHANGED over a 39,222-byte submission
        # carrying their colonise order, and only an unclosed turn and a copy
        # still on disk got it back.
        #
        # Waiting is always safe and redoing never is, so wait. The player has
        # submitted; that submission is their turn.
        blob = store.turn_blob(turn)

        # `blob` stays the authoritative turn throughout: it is the baseline
        # every diff and summary is measured against. `to_load` is only what
        # goes into the client, and on a restart those are not the same thing.
        to_load = blob
        prior = store.submission(civ, turn)
        if prior is not None:
            if carries_orders(blob, prior, civ):
                # Resume, do not restart. The submission is a complete blob of
                # this turn carrying what the player played, so loading it puts
                # them back where they were. Loading `blob` instead would take
                # their orders away and then overwrite the submission holding
                # them, which is how turn 12 was nearly lost: a loop restarted
                # 22 seconds before the deadline served the pristine turn and
                # submitted 39,128 orderless bytes over a 39,222-byte colonise
                # order.
                to_load = prior
                log(f"[{civ}] turn {turn}: resuming from your submission "
                    f"({len(prior):,} bytes, it carries orders)")
                emit("resuming", turn=turn, civ=civ)
            else:
                # An orderless submission is the interim write of a turn nobody
                # has played yet. There is nothing in it to preserve, so a
                # plain serve is correct and refusing here would strand a
                # player whose client died with their turn still open.
                log(f"[{civ}] turn {turn}: a submission exists but carries no "
                    f"orders, serving the turn fresh")
        emit("serving", turn=turn, civ=civ)
        log(f"[{civ}] turn {turn}: serving")
        try:
            serve(to_load, civ, hold=HOLD_SECONDS, exe=exe,
                  wait_for_lock=240.0, log=log)
        except BaseException as exc:                        # noqa: BLE001
            # A serve that fails costs THIS turn. Letting it end the loop costs
            # every turn after it, and nobody is watching a player's loop at
            # 03:00. Measured: a client that came up but never became readable
            # killed this loop at turn 28, and the galaxy reached turn 47 with
            # the player absent from all nineteen.
            log(f"[{civ}] turn {turn}: COULD NOT SERVE, {exc}")
            emit("serve_failed", turn=turn, civ=civ, error=str(exc))
            try:
                close()
            except BaseException:                           # noqa: BLE001
                pass
            # Wait this turn out rather than spinning on a client that may be
            # wedged, then try again on the next one.
            while not halted():
                got = current_or_wait()
                if got is None or got[0] != turn:
                    break
                nap(poll_gap(got[1] - clock(), poll, poll_max))
            continue

        if reopen is not None:
            # The game is open, so any request to open it has been answered,
            # including one made in the seconds between the referee publishing
            # and this serve. Left standing it would spend itself at the far
            # end of a turn the player is in the middle of.
            reopen.clear()

        sent = None                     # the last blob this turn actually stored
        held = None                     # the last blob taken out of the client

        def take(final=False):
            """Capture the player's state to this disk. Metered nowhere.

            Kept separate from `send` because that is the whole point of the
            two cadences: this half can run often, and does.
            """
            nonlocal held
            emit("capturing", turn=turn, civ=civ)
            capture = collect(f"{civ[:8]}t{turn}", save_dir=save_dir, log=log,
                              announce=final)
            held = sp.load_any(capture)
            emit("captured", turn=turn, civ=civ, pending=held != sent)
            return held

        def send(final):
            """Store the last capture, if the store has not got it already.

            This is the half that is metered, so it does nothing at all
            for a capture the store already holds, which is what an idle
            turn is made of.
            """
            nonlocal sent
            mine = held
            if mine is None or mine == sent:
                return True

            # Refuse to replace a submission we did not write with one that
            # carries nothing. `sent` is what THIS pass stored, so `existing`
            # differing from it means the submission came from somewhere else:
            # an earlier run, another process, a restart mid-turn. Overwriting
            # that with an empty capture is the one failure today that
            # destroyed rather than dropped, and it is silent because
            # last-write-wins has no opinion about what it is replacing.
            #
            # A player who genuinely cancels their order is still served: the
            # submission we wrote ourselves is `sent`, so replacing our own
            # with an empty one is allowed. Only a stranger's is protected.
            existing = store.submission(civ, turn)
            if (existing is not None and existing != sent
                    and not carries_orders(blob, mine, civ)):
                # Both are orderless: nothing is at stake, so say so quietly.
                # Shouting about a no-op is how a warning that matters gets
                # read past, and this fired on the first live turn after the
                # guard went in, replacing one empty submission with another.
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
                # The client being gone is the ordinary way this fails at the
                # deadline: the player closed the game window, so there is
                # nothing left to ask for a save. The capture taken seconds
                # before it closed is still theirs and still the turn, so send
                # that rather than lose the turn to the window closing.
                if final and held is not None:
                    log(f"[{civ}] turn {turn}: could not take a last capture "
                        f"({exc}); sending the {len(held):,} bytes already "
                        f"captured")
                else:
                    raise
            return send(final)

        # How often this turn's uploads may go, from the turn's own length. The
        # budget is per turn because that is how the quota is spent, so a
        # galaxy with short turns does not spend more per turn than a galaxy
        # with long ones.
        # `deadline` came back with the turn, so how long is left is a
        # subtraction rather than another read of the same document.
        left0 = deadline - clock() if deadline else None
        if submit_every is not None:
            gap = float(submit_every)
        elif left0 and left0 > 0:
            gap = max(capture_every, left0 / max(1, uploads_per_turn))
        else:
            # No readable clock to divide up. The upload at the deadline is the
            # one this can still promise, and guessing an interval here is
            # guessing with the month's quota.
            gap = float("inf")
        log(f"[{civ}] turn {turn}: capturing every {capture_every:g}s, "
            + ("sending at the deadline only" if gap == float("inf")
               else f"sending every {gap:g}s and again at the deadline"))

        started = clock()
        last_capture = started
        last_upload = started
        # The first read confirms the deadline after a serve, which takes long
        # enough to start a client that the read before it is stale.
        next_read = started
        playing[0] = True

        while not halted():
            # The deadline is an absolute time, so how long is left is known
            # between reads. This loop wakes for the capture cadence far more
            # often than it needs the store, and subtracting here is what lets
            # the two run at different rates.
            left = deadline - clock()
            if clock() >= next_read:
                # A store read can fail transiently , the store lives on an SMB
                # share and the referee rewrites it from another machine. `state()`
                # already retries for a couple of seconds, and when an outage
                # outlasts that the right answer is still not to exit: this loop
                # dying is how a player silently stops taking turns. Measured, a
                # `FileNotFoundError` on `state.json` here killed the loop at turn
                # 18 and the galaxy was at turn 20 before anyone noticed.
                try:
                    cur, deadline = store.current()
                except OSError as exc:
                    log(f"[{civ}] turn {turn}: store unreadable ({exc.__class__.__name__}), "
                        f"retrying in {poll}s")
                    nap(poll)
                    continue
                left = deadline - clock()
                next_read = clock() + poll_gap(left, poll, poll_max)
                if cur != turn:
                    # The referee moved on without us, which happens if this
                    # player joined late or the machine slept. Nothing to submit
                    # for a turn that is already closed.
                    log(f"[{civ}] turn {turn} closed while playing; skipping submit")
                    emit("overtaken", turn=turn, current=cur)
                    break
            if left <= 0:
                emit("collecting", turn=turn, civ=civ)
                log(f"[{civ}] turn {turn}: time is up, collecting")
                try:
                    push(final=True)
                except Exception as exc:                    # noqa: BLE001
                    # This turn is forfeit either way, but stopping here also
                    # abandons every turn after it, which is how one bad
                    # collect became a player who had simply stopped. Say it
                    # loudly, then carry on to the next turn.
                    why = failure_text(exc)
                    log(f"[{civ}] turn {turn}: LOST, {why}")
                    emit("lost", turn=turn, civ=civ, error=why)
                break

            now = clock()
            # A player who pressed Save is not waiting for either cadence. Both
            # halves run at once for them, which is the whole of what the
            # button means.
            wanted = asked()
            if wanted:
                send_now.clear()
                log(f"[{civ}] turn {turn}: sending now, you asked")

            if wanted or now - last_capture >= capture_every:
                last_capture = now
                try:
                    take()
                except (Exception, SystemExit) as exc:      # noqa: BLE001
                    # One failed capture is not a turn. The next one is seconds
                    # away and the one at the deadline still has to succeed or
                    # say why. SystemExit by name because that is what a failed
                    # `collect` raises, and it is not an Exception.
                    #
                    # Unless the game has gone, which is not transient and never
                    # recovers. The first version of this cadence kept asking
                    # every five seconds for the rest of the turn, filling the
                    # log with `SaveGame did not report success` until somebody
                    # stopped it by hand. The client is only looked for once a
                    # capture has already failed, because a capture that worked
                    # is the cheapest possible proof that it is still there and
                    # `client_pids` costs a PowerShell process.
                    import game_cycle as gc
                    if not gc.client_pids():
                        log(f"[{civ}] turn {turn}: the game has closed, "
                            f"sending what was captured before it did")
                        try:
                            # `send` emits `submitted` itself. Emitting it here
                            # as well told the launcher twice and printed
                            # "orders sent" twice in the log.
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
                    # A failed interim write is worth saying and not worth
                    # stopping for: the next one is a cadence away, and the one
                    # at the deadline still has to succeed or raise.
                    log(f"[{civ}] turn {turn}: interim submit failed, "
                        f"{failure_text(exc)}")

            emit("playing", turn=turn, civ=civ, seconds_left=left)
            # Wake for whichever comes first: the next capture, the next store
            # read, or the deadline. Sleeping a whole interval regardless would
            # stretch the capture cadence to the interval above it, so a
            # five-second capture on a two-second poll would really be every
            # six, and on a poll that has backed off to five minutes it would
            # be every five minutes. Waking for a capture costs nothing at the
            # store, which is what the separate `next_read` is for.
            due = last_capture + capture_every - clock()
            nap(max(0.1, min(due if due > 0 else capture_every, left,
                             next_read - clock())))

        playing[0] = False
        if send_now is not None:
            # A request made in the last seconds of a turn has been served by
            # the upload at the deadline, and one made after it names a turn
            # that is closed. Either way it must not carry into the next turn
            # and spend an upload on a turn nobody has played yet.
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
            # This is the wait that costs the day: at four hours a turn, a
            # player who has closed their game window spends nearly all of it
            # here. The deadline the store just gave is the earliest the next
            # turn can exist, so the gap grows while that is far off and comes
            # back to the floor around it, where a turn actually appears.
            nap(poll_gap(got[1] - clock(), poll, poll_max), until=wants_turn)
        if reopening:
            # Round the outer loop rather than serving from here. Everything
            # that decides what a turn is opened on lives at the top of it:
            # `store.submission` is read there, and a submission carrying
            # orders is what gets loaded. Serving from this point would be the
            # 18 September failure with a button on it, the pristine turn over
            # a played one. Going round also re-enters the capture and upload
            # cadences, so anything played after the window opens again is
            # captured and sent like the rest of the turn.
            log(f"[{civ}] turn {turn}: opening the game again, you asked")
            emit("reopening", turn=turn, civ=civ)
            continue

        # The turn has closed, so the referee has said what it refused. This is
        # the first moment the note can exist and the last moment the player
        # can still act on it, since the state they are about to be served no
        # longer carries the order they lost.
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
