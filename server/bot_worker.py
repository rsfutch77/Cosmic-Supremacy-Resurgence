"""
bot_worker.py: the AI plays every bot civ in every galaxy, unattended
=====================================================================
    python server/bot_worker.py --store "firebase://cs-resurgence/*"
    python server/bot_worker.py --store firebase://cs-resurgence/sandbox --once
    python server/bot_worker.py --store firebase://cs-resurgence/sandbox --add Beta --add "Sir Blorpington"
    python server/bot_worker.py --store firebase://cs-resurgence/sandbox --remove Beta
    python server/bot_worker.py --store "firebase://cs-resurgence/*" --list

A galaxy's bot civs are the names in its `bots` field that are also in its
roster (`turn_store.bots_of`). `--add` and `--remove` edit that field; nothing
else makes a civ a bot.

Each pass reads every galaxy the `--store` specs name, and plays each bot civ
that has not submitted the current turn through `bot_seats.round_once`. A `*`
in place of the galaxy is every galaxy in that Firebase project, listed again
each pass; a folder spec with a `*` is every galaxy folder it matches.

The game client is shared with the referee, so a civ is not started:

- within `TICK_MARGIN` seconds of a refereed galaxy's deadline, or while one
  is overdue by less than `OVERDUE_HOLD` seconds, when its tick is under way
- with under `MIN_LEFT` seconds left on its own galaxy's turn
- before a pass, while a client is running or the client lease is held

Refereed galaxies are those named by a status file in `--referee-dir` (the
referee worker's work directory) updated within `LIVE_STATUS` seconds. Without
`--referee-dir`, every galaxy with bots counts as refereed.

Each galaxy keeps its AI history in its own folder under `--state-dir`.

Needs a `cs_server.py` from this checkout on port 8888, where captures land.
Inside the sandbox that is the referee worker's; `--start-server` starts one
when nothing is listening.
"""
import argparse
import glob
import json
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
for _d in (HERE, os.path.join(HERE, 'dev_tools')):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import referee_worker as rw
import turn_store

WORK_DIR = os.path.join(HERE, 'worker_work')
LOG_NAME = 'bot_worker.log'
STATUS_NAME = 'bot_worker.json'
STATE_DIR_NAME = 'ai_state'

POLL = 60.0               # seconds between passes
TICK_MARGIN = 180.0       # seconds before a refereed deadline that no civ starts
OVERDUE_HOLD = 900.0      # seconds past a refereed deadline that no civ starts
MIN_LEFT = 120.0          # seconds a civ's own turn must have left to start
LIVE_STATUS = 600.0       # age, in seconds, of a referee status file still read
BACKOFF_FIRST, BACKOFF_MAX = 60.0, 900.0   # seconds, after a failed pass

EXIT_OK = 0
EXIT_ANOTHER_WORKER = 2
EXIT_SERVER = 3


class BotGuard(rw.GalaxyGuard):
    """One bot worker per machine. The client is one per machine, so two
    workers would only take turns at it."""

    def __init__(self, work_dir: str = WORK_DIR):
        super().__init__('bots', work_dir=work_dir)
        self.name = 'Local\\cosmic_bot_worker'
        self.path = os.path.join(work_dir, STATUS_NAME)


def expand(specs) -> list:
    """Each spec, with a `*` galaxy replaced by the galaxies it matches."""
    out = []
    for spec in specs:
        if spec.startswith('firebase://') and _galaxy_part(spec) == '*':
            import firebase_store
            out.extend(firebase_store.galaxy_specs(spec))
        elif '*' in spec and '://' not in spec:
            out.extend(sorted(d for d in glob.glob(spec) if os.path.isfile(
                os.path.join(d, 'state.json'))))
        else:
            out.append(spec)
    seen = set()
    return [s for s in out if not (s in seen or seen.add(s))]


def _galaxy_part(spec: str) -> str:
    path = spec.split('://', 1)[1].split('?', 1)[0]
    parts = [p for p in path.split('/') if p]
    return parts[1] if len(parts) > 1 else ''


def galaxy_label(store, spec: str) -> str:
    """A folder name for one galaxy: its Firebase id, or its folder's name."""
    name = getattr(store, 'galaxy', None) or rw.galaxy_name(spec)
    return ''.join(c if c.isalnum() or c in '-_.' else '_' for c in name)


def refereed_keys(referee_dir, now: float):
    """`galaxy_key` of each galaxy a live referee worker names, or None when
    there is no directory to read."""
    if not referee_dir:
        return None
    keys = set()
    for path in glob.glob(os.path.join(referee_dir, 'worker_*.json')):
        try:
            with open(path, encoding='utf-8') as f:
                s = json.load(f)
            if now - float(s['updated']) <= LIVE_STATUS:
                keys.add(rw.galaxy_key(s['store']))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return keys


def tick_hold(galaxies, keys, now: float):
    """Why no civ may start now because a referee is about to tick, or None.

    `galaxies` is `[(spec, state)]`; `keys` is `refereed_keys`, where None
    counts every galaxy that has bots.
    """
    for spec, state in galaxies:
        if turn_store.status_of(state) == turn_store.CLOSED:
            continue
        if keys is None:
            if not turn_store.bots_of(state):
                continue
        elif rw.galaxy_key(spec) not in keys:
            continue
        left = state['deadline'] - now
        if -OVERDUE_HOLD < left < TICK_MARGIN:
            when = (f'in {left:.0f}s' if left >= 0 else
                    f'{-left:.0f}s ago')
            return f'{rw.galaxy_name(spec)} turn {state["turn"]} is due {when}'
    return None


def pending_bots(store, state) -> list:
    """The bot civs that have not submitted the current turn."""
    done = set(store.submitted_civs(state['turn']))
    return [c for c in turn_store.bots_of(state) if c not in done]


def client_busy(holder=None):
    """Why the client is not free for a pass, or None. A client lease held
    by `holder` is this worker's own."""
    try:
        import game_cycle as gc
        pids = gc.client_pids()
    except Exception as exc:                                # noqa: BLE001
        return f'could not list clients ({type(exc).__name__}: {exc})'
    if pids:
        return f'a client is running (pid {", ".join(map(str, pids))})'
    try:
        import lease
        held = lease.read('client')
    except Exception:                                       # noqa: BLE001
        held = None
    if (held and float(held.get('until', 0)) > time.time()
            and held.get('holder') != holder):
        return f'the client lease is held by {held.get("holder")}'
    return None


def read_galaxies(specs, open_store=turn_store.open_store, log=print):
    """`[(spec, store, state)]` for each galaxy that can be read."""
    out = []
    for spec in specs:
        try:
            store = open_store(spec)
            out.append((spec, store, store.state()))
        except Exception as exc:                            # noqa: BLE001
            log(f'  bots: {spec}: not read ({type(exc).__name__}: {exc})')
    return out


def one_pass(specs, state_root, referee_dir=None, open_store=turn_store.open_store,
             play=None, busy=client_busy, clock=time.time, log=print) -> dict:
    """Play every pending bot civ that may start. Returns what happened.

    `play(spec, store, civs, may_start, log)` plays one galaxy; it defaults to
    `bot_seats.round_once` with the galaxy's own AI history folder.
    """
    report = {'galaxies': 0, 'pending': 0, 'played': 0, 'held': None}
    galaxies = read_galaxies(expand(specs), open_store=open_store, log=log)
    report['galaxies'] = len(galaxies)
    work = []
    for spec, store, state in galaxies:
        if turn_store.status_of(state) == turn_store.CLOSED:
            continue
        if not turn_store.bots_of(state):
            continue
        civs = pending_bots(store, state)
        if civs:
            work.append((state['deadline'], spec, store, state, civs))
    report['pending'] = sum(len(w[4]) for w in work)
    if not work:
        return report

    why = busy()
    if why:
        report['held'] = why
        log(f'  bots: {report["pending"]} civ(s) waiting; {why}')
        return report

    # Status files carry wall-clock times, whatever `clock` is.
    keys = refereed_keys(referee_dir, time.time())
    listed = [(spec, state) for spec, _store, state in galaxies]
    play = play or _play_galaxy(state_root)
    # The soonest deadline first.
    for deadline, spec, store, state, civs in sorted(work, key=lambda w: w[0]):
        def may_start(deadline=deadline, spec=spec):
            hold = tick_hold(listed, keys, clock())
            if hold:
                report['held'] = hold
                log(f'  bots: holding, {hold}')
                return False
            left = deadline - clock()
            if left < MIN_LEFT:
                log(f'  bots: {rw.galaxy_name(spec)} has {left:.0f}s left, '
                    f'too little for a civ')
                return False
            return True
        if not may_start():
            if report['held']:
                break               # a tick is close, so no galaxy starts
            continue
        log(f'  bots: {rw.galaxy_name(spec)} turn {state["turn"]}: '
            f'{", ".join(civs)}')
        play(spec, store, civs, may_start, log)
        report['played'] += len(civs) - len(pending_bots(store, store.state()))
    return report


def _play_galaxy(state_root):
    def play(spec, store, civs, may_start, log):
        import bot_seats
        import sensors
        sensors.STATE_DIR = os.path.join(state_root, galaxy_label(store, spec))
        os.makedirs(sensors.STATE_DIR, exist_ok=True)
        bot_seats.round_once(store, civs, may_start=may_start, log=log)
    return play


def run(specs, state_root, referee_dir=None, poll: float = POLL, once=False,
        guard=None, server=None, holder=None, log=print,
        sleeper=time.sleep) -> int:
    """Pass after pass, for as long as the machine is up."""
    fails = 0
    while True:
        try:
            if server is not None and not server.alive():
                log('  bots: the cs_server this worker started has exited; '
                    'restarting it')
                server.start()
                server.wait_until_listening()
            report = one_pass(specs, state_root, referee_dir=referee_dir,
                              busy=lambda: client_busy(holder), log=log)
            fails = 0
            if guard is not None:
                guard.write(last_pass=time.time(), failures=0, **report)
            if once:
                return EXIT_OK
            sleeper(poll)
        except KeyboardInterrupt:
            raise
        except BaseException as exc:                        # noqa: BLE001
            fails += 1
            wait = min(BACKOFF_FIRST * (2 ** (fails - 1)), BACKOFF_MAX)
            log(f'  bots: pass failed ({type(exc).__name__}: {exc}); attempt '
                f'{fails}, waiting {wait:.0f}s')
            log(traceback.format_exc())
            if guard is not None:
                guard.write(failures=fails, last_error=f'{type(exc).__name__}: '
                            f'{exc}', last_error_at=time.time())
            if once:
                return 1
            sleeper(wait)


def edit_bots(spec, add=(), remove=(), open_store=turn_store.open_store,
              log=print) -> list:
    """Add and remove bot civs on one galaxy, and return its list after.

    A name not in the roster is refused, so a typo never waits for a civ that
    will not come.
    """
    store = open_store(spec)
    state = store.state()
    roster = list(state.get('civs') or [])
    missing = [c for c in add if c not in roster]
    if missing:
        raise SystemExit(f'not in {rw.galaxy_name(spec)}: {missing}; the roster '
                         f'is {roster}')
    bots = [c for c in (state.get(turn_store.BOTS_KEY) or []) if c not in remove]
    bots += [c for c in add if c not in bots]
    store.update_state({turn_store.BOTS_KEY: bots})
    log(f'{rw.galaxy_name(spec)}: bots {bots}')
    return bots


def list_bots(specs, open_store=turn_store.open_store, log=print) -> None:
    for spec, store, state in read_galaxies(expand(specs),
                                            open_store=open_store, log=log):
        bots = state.get(turn_store.BOTS_KEY) or []
        if not bots:
            continue
        waiting = pending_bots(store, state)
        gone = [c for c in bots if c not in (state.get('civs') or [])]
        log(f'{rw.galaxy_name(spec)} turn {state["turn"]}: bots {bots}'
            + (f', not submitted {waiting}' if waiting else ', all submitted')
            + (f', not in the roster {gone}' if gone else ''))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1].strip())
    ap.add_argument('--store', action='append', required=True,
                    help='a galaxy, or a project with * for its galaxies; '
                         'repeat for more')
    ap.add_argument('--add', action='append', default=[],
                    help='make this civ a bot (one galaxy only)')
    ap.add_argument('--remove', action='append', default=[],
                    help='stop playing this civ (one galaxy only)')
    ap.add_argument('--list', action='store_true',
                    help='show each galaxy\'s bots and who has not submitted')
    ap.add_argument('--work-dir', default=WORK_DIR,
                    help='log, status file and AI history')
    ap.add_argument('--state-dir',
                    help=f'AI history root (default <work-dir>\\{STATE_DIR_NAME})')
    ap.add_argument('--referee-dir',
                    help='the referee worker\'s work directory, for its ticks')
    ap.add_argument('--lease-holder',
                    help='a client lease held under this name is this '
                         'worker\'s own')
    ap.add_argument('--poll', type=float, default=POLL)
    ap.add_argument('--once', action='store_true', help='one pass, then exit')
    ap.add_argument('--start-server', action='store_true',
                    help='start cs_server.py on 8888 when nothing listens there')
    a = ap.parse_args(argv)

    if a.add or a.remove:
        if len(a.store) != 1 or '*' in a.store[0]:
            raise SystemExit('--add and --remove take exactly one galaxy')
        edit_bots(a.store[0], add=a.add, remove=a.remove)
        return EXIT_OK
    if a.list:
        list_bots(a.store)
        return EXIT_OK

    log = rw.make_log(os.path.join(a.work_dir, LOG_NAME), work_dir=a.work_dir)
    state_root = a.state_dir or os.path.join(a.work_dir, STATE_DIR_NAME)
    guard = BotGuard(a.work_dir)
    try:
        guard.acquire(stores=a.store, state_dir=state_root)
    except rw.AnotherWorker as exc:
        log(f'  bots: {exc}')
        return EXIT_ANOTHER_WORKER

    import player_turn
    server = None
    try:
        ok, why = player_turn.save_path_ready()
        if not ok:
            if not a.start_server:
                log(f'  bots: {why}. The referee worker starts one; pass '
                    f'--start-server to start one here.')
                return EXIT_SERVER
            server = rw.ensure_stub_server(HERE, work_dir=a.work_dir, log=log)
        log(f'  bots: worker up, stores {a.store}, history in {state_root}, '
            f'referee {a.referee_dir or "(every galaxy with bots)"}')
        return run(a.store, state_root, referee_dir=a.referee_dir,
                   poll=a.poll, once=a.once, guard=guard, server=server,
                   holder=a.lease_holder, log=log)
    finally:
        if server is not None:
            server.stop()
        guard.release()


if __name__ == '__main__':
    sys.exit(main())
