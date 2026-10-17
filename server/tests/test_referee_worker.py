"""
test_referee_worker.py: the five things unattended has to mean
===============================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_referee_worker.py

Each check is paired with its control:

  * the overdue turn: a turn not yet due, or inside the grace window, is left
    alone.
  * the second copy: a second worker process is refused, and one started after
    the first is killed is allowed.
  * the stub server: a listener on a spare port is refused, then a real
    `cs_server.py` is started on it. Port 8888 is never touched.

A real tick needs the game client, so `referee.resolve_turn` is stubbed and
the worker's decision to call it is what is tested.
"""
import inspect
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'), os.path.join(ROOT, 'server', 'dev_tools')):
    if d not in sys.path:
        sys.path.insert(0, d)

import referee
import referee_worker as rw
from turn_store import TurnStore

WORKER = os.path.join(ROOT, 'server', 'referee_worker.py')

PASS, FAIL = [], []


def check(name, got, want):
    (PASS if got == want else FAIL).append(name)
    print(f'  {"ok  " if got == want else "FAIL"}  {name}')
    if got != want:
        print(f'          wanted {want!r}, got {got!r}')


def tmpdir(prefix):
    return tempfile.mkdtemp(prefix=f'worker_{prefix}_')


def fresh_store(seconds_left=3600.0, turn=12, civs=('DemoPlayer', 'Neighbor')):
    """A store whose deadline is `seconds_left` from now."""
    s = TurnStore(tmpdir('store'))
    s.start(b'BASE' * 64, list(civs), turn_seconds=3600, turn=turn)
    s.update_state({'deadline': time.time() + seconds_left})
    return s


class Calls:
    """A stand-in for a turn being closed, counting what it was asked to do."""

    def __init__(self, raises=None, publishes=True):
        self.args, self.raises, self.publishes = [], raises, publishes

    def __call__(self, store, save_dir=None, log=print):
        self.args.append({'store': store, 'save_dir': save_dir})
        if self.raises is not None:
            raise self.raises
        if self.publishes:
            # Advance the clock as a real resolution would.
            turn, _ = store.current()
            store.publish(turn + 1, b'BASE' * 64)
        return store.current()[0]

    @property
    def n(self):
        return len(self.args)


def stub_referee(resolve):
    """Replace the two referee calls that need a game client. Returns undo."""
    orig = referee.resolve_turn, referee.wait_for_client_free
    referee.resolve_turn = resolve
    referee.wait_for_client_free = lambda *a, **k: True

    def undo():
        referee.resolve_turn, referee.wait_for_client_free = orig
    return undo


def lines():
    """A log that keeps what it was told, for asserting on what was said."""
    said = []
    return said, said.append


def test_overdue():
    print('the overdue turn')

    # Not due at all.
    s = fresh_store(seconds_left=3600)
    check('overdue_by is 0 on a turn with an hour left', rw.overdue_by(s), 0.0)
    calls = stub = Calls()
    undo = stub_referee(stub)
    try:
        said, log = lines()
        check('close_overdue leaves a turn that is not due',
              rw.close_overdue(s, log=log), False)
        check('and closes nothing', calls.n, 0)
        check('and the turn is still 12', s.current()[0], 12)

        # Four seconds past the deadline is inside the grace window.
        s = fresh_store(seconds_left=-4)
        check('overdue_by is 0 inside the grace window', rw.overdue_by(s), 0.0)
        said, log = lines()
        check('close_overdue leaves a turn inside the grace window',
              rw.close_overdue(s, log=log), False)
        check('and still closes nothing', calls.n, 0)

        # The machine was off across a deadline.
        s = fresh_store(seconds_left=-7200)
        check('overdue_by counts a turn two hours late',
              round(rw.overdue_by(s)), 7200 - int(rw.GRACE))
        said, log = lines()
        check('close_overdue closes a turn the machine slept through',
              rw.close_overdue(s, save_dir='D:\\saves', log=log), True)
        check('and closes exactly one', calls.n, 1)
        check('and hands the tick the save directory it was given',
              calls.args[0]['save_dir'], 'D:\\saves')
        check('and says the turn was late rather than closing it silently',
              any('came due' in x and 'closing it now' in x for x in said), True)
        check('and the galaxy has moved on', s.current()[0], 13)

        # One turn is closed however many deadlines were missed, so players
        # get a chance to play the next.
        s = fresh_store(seconds_left=-7200)
        rw.close_overdue(s, log=lambda *a: None)
        check('and only one, however many deadlines were missed',
              s.current()[0], 13)
    finally:
        undo()


def test_loop():
    print('the loop')

    s = fresh_store(seconds_left=-60)
    calls = Calls()
    undo = stub_referee(calls)
    try:
        said, log = lines()
        check('run closes a turn whose deadline has passed',
              rw.run(s, save_dir='D:\\saves', once=True, log=log,
                     sleeper=lambda *a: None), rw.EXIT_OK)
        check('and closed it once', calls.n, 1)

        # Control: a turn with time left.
        s = fresh_store(seconds_left=3600)
        calls = Calls()
        undo(); undo = stub_referee(calls)
        said, log = lines()
        check('run leaves a turn that still has time',
              rw.run(s, once=True, log=log, sleeper=lambda *a: None),
              rw.EXIT_OK)
        check('and closed nothing', calls.n, 0)

        # A referee refusal is a SystemExit, which is not an Exception.
        s = fresh_store(seconds_left=-60)
        calls = Calls(raises=SystemExit('refusing to tick: nothing is '
                                        'listening on 127.0.0.1:8888'))
        undo(); undo = stub_referee(calls)
        said, log = lines()
        check('a SystemExit out of the referee does not kill the worker',
              rw.run(s, once=True, log=log, sleeper=lambda *a: None), 1)
        check('and it is written down with the reason',
              any('nothing is listening' in x for x in said), True)
        check('and the turn is left where it was', s.current()[0], 12)

        s = fresh_store(seconds_left=-60)
        calls = Calls(raises=OSError('the share went away'))
        undo(); undo = stub_referee(calls)
        said, log = lines()
        check('an OSError does not kill it either',
              rw.run(s, once=True, log=log, sleeper=lambda *a: None), 1)
        check('and the backoff is said out loud',
              any('waiting' in x for x in said), True)

        # A closed galaxy is not ticked, and the worker stays up in case it
        # is reopened.
        s = fresh_store(seconds_left=-60)
        s.close('testing')
        calls = Calls()
        undo(); undo = stub_referee(calls)
        said, log = lines()
        passes = []
        check('run stops when asked',
              rw.run(s, log=log, sleeper=lambda *a: passes.append(1),
                     stop=lambda: len(passes) >= 2), rw.EXIT_OK)
        check('a closed galaxy is never ticked', calls.n, 0)
        check('and the worker says why once rather than every poll',
              sum('this galaxy is closed' in x for x in said), 1)
    finally:
        undo()


def test_poll():
    print('the clock reads')
    check('near the deadline the gap is the floor', rw.poll_gap(30.0), rw.POLL)
    check('far from it, the ceiling', rw.poll_gap(4 * 3600.0), rw.POLL_CEILING)
    check('in between, a share of the distance', rw.poll_gap(1000.0),
          1000.0 / rw.POLL_SHARE)

    # One idle four-hour turn, read by read, as `run` sleeps through it.
    reads, left = 0, 4 * 3600.0
    while left > 0:
        reads += 1
        left -= min(rw.poll_gap(left), left)
    while -left < rw.GRACE:
        reads += 1
        left -= min(rw.POLL, rw.GRACE + left)
    per_day = (reads + 1) * 6
    check('an idle four-hour galaxy reads under 2,000 times a day',
          per_day < 2000, True)

    s = fresh_store(seconds_left=3600)
    s.close('testing')
    slept = []
    rw.run(s, log=lambda *a: None, sleeper=slept.append,
           stop=lambda: len(slept) >= 1)
    check('a closed galaxy is read once per ceiling', slept, [rw.POLL_CEILING])

    s = fresh_store(seconds_left=3600)
    slept = []
    rw.run(s, log=lambda *a: None, sleeper=slept.append,
           stop=lambda: len(slept) >= 1)
    check('an hour out, the worker waits the ceiling, not the floor', slept,
          [rw.POLL_CEILING])


def start_worker(store_root, work_dir, *extra):
    """A real worker process over a galaxy whose turn is not due.

    Callers pass `--no-cs-server` so port 8888 is never bound.
    """
    return subprocess.Popen(
        [sys.executable, WORKER, '--store', store_root,
         '--work-dir', work_dir, '--poll', '1', *extra],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def wait_for_holder(path, timeout=30.0):
    """What the worker holding the galaxy wrote about itself, once it has.

    The recorded pid is not the one `Popen` returned: a virtualenv's
    `python.exe` on Windows runs the real interpreter as a child.
    """
    end = time.time() + timeout
    while time.time() < end:
        try:
            with open(path, encoding='utf-8') as f:
                held = json.load(f)
            if held.get('pid'):
                return held
        except (OSError, ValueError):
            pass
        time.sleep(0.2)
    return None


def kill_tree(proc):
    """End a worker and the interpreter its launcher started, by pid.

    Never kill by image name: that reaches every Python on the machine.
    """
    subprocess.run(['taskkill', '/T', '/F', '/PID', str(proc.pid)],
                   capture_output=True,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        proc.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_second_copy():
    print('a second worker on one galaxy')

    check('two spellings of one directory are one galaxy',
          rw.galaxy_key('C:\\Galaxies\\Sandbox') ==
          rw.galaxy_key('c:/galaxies/sandbox/'.rstrip('/')), True)
    check('and two galaxies are not',
          rw.galaxy_key('C:\\galaxies\\a') == rw.galaxy_key('C:\\galaxies\\b'),
          False)

    s = fresh_store(seconds_left=3600)
    work = tmpdir('work')
    guard = rw.GalaxyGuard(s.root, work_dir=work)

    first = start_worker(s.root, work, '--no-cs-server')
    try:
        held = wait_for_holder(guard.path)
        check('the first worker takes the galaxy', held is not None, True)
        check('and says which galaxy it has', held and held.get('store'),
              s.root)

        # `--once`, so a guard that fails to refuse exits 0 instead of hanging.
        second = start_worker(s.root, work, '--no-cs-server', '--once')
        out, _ = second.communicate(timeout=60)
        check('a second worker is refused', second.returncode,
              rw.EXIT_ANOTHER_WORKER)
        check('and it names the process holding the galaxy',
              held and f"pid {held['pid']}" in out, True)
        check('and it did not close a turn', 'closing turn' in out, False)

        # The guard is per galaxy. The single game client is `game_cycle`'s
        # lock.
        other = fresh_store(seconds_left=3600)
        third = start_worker(other.root, work, '--no-cs-server', '--once')
        out, _ = third.communicate(timeout=60)
        check('a worker on another galaxy is allowed', third.returncode,
              rw.EXIT_OK)
    finally:
        kill_tree(first)

    # The mutex goes with the process that held it, whatever the status file
    # says.
    fourth = start_worker(s.root, work, '--no-cs-server', '--once')
    out, _ = fourth.communicate(timeout=60)
    check('the galaxy is free once the worker holding it has died',
          fourth.returncode, rw.EXIT_OK)
    check('and the refusal is not repeated to the next one',
          'another worker already has' in out, False)

    # The status file accumulates, so the cs_server pid written at startup
    # survives later writes.
    solo = rw.GalaxyGuard(tmpdir('store'), work_dir=tmpdir('work'))
    solo.acquire(cs_server_pid=4242)
    try:
        solo.write(turn=12)
        held = solo.holder()
        check('the status file keeps what startup said', held['cs_server_pid'],
              4242)
        check('alongside what the loop says', held['turn'], 12)
    finally:
        solo.release()
    check('and it is gone when the guard is released',
          os.path.exists(solo.path), False)


def spare_port():
    """A port nothing is using, which is never 8888."""
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_stub_server():
    print('the stub server')

    port = spare_port()
    check('the spare port is not the one the launcher uses', port == 8888,
          False)
    check('nothing is listening on it yet', rw.port_listening(port), False)

    stranger = socket.socket()
    stranger.bind(('127.0.0.1', port))
    # A deep backlog, so the probes below never fill the accept queue and read
    # the port as free.
    stranger.listen(64)
    try:
        check('a bound port reads as in use', rw.port_listening(port), True)
        found = rw.port_holder(port)
        check('and the holder is found', found is not None, True)
        check('and it is this process', found and found[0], os.getpid())

        refused = None
        try:
            rw.ensure_stub_server(tmpdir('data'), port=port, log=lambda *a: None)
        except rw.ServerRefused as exc:
            refused = str(exc)
        check('a stranger on the port is refused', refused is not None, True)
        check('and the refusal names the process', refused and
              f'pid {os.getpid()}' in refused, True)
        check('and says why reusing it is not an option',
              refused and 'data directory' in refused, True)

        # The recorded pid now belongs to this test, not a cs_server: a reused
        # pid, which the worker must not end.
        reused = None
        try:
            rw.ensure_stub_server(tmpdir('data'), port=port,
                                  left_over=os.getpid(), log=lambda *a: None)
        except rw.ServerRefused as exc:
            reused = str(exc)
        check('a recorded pid that is now somebody else is refused',
              reused is not None, True)
        check('and the refusal says the number was reused',
              reused and 'reused' in reused, True)
        check('and this process is still running', os.getpid() > 0, True)
        check('while a stranger with no recorded pid is described as one',
              'previous worker of this galaxy' in refused, False)
        # A Python running a file in this project is not enough to count as a
        # cs_server.
        check('the test itself is not mistaken for one',
              rw.is_our_cs_server(os.getpid()), False)

        # `--adopt-server` accepts the stranger.
        said, log = lines()
        adopted = rw.ensure_stub_server(tmpdir('data'), port=port, adopt=True,
                                        work_dir=tmpdir('work'), log=log)
        check('--adopt-server proceeds', adopted, None)
        check('and warns where captures are expected',
              any('adopting it' in x for x in said), True)
    finally:
        stranger.close()

    check('the port is free again', rw.port_listening(port), False)

    # A free port gets a server whose save directory the worker chose.
    data, work = tmpdir('data'), tmpdir('work')
    said, log = lines()
    server = rw.ensure_stub_server(data, port=port, work_dir=work, log=log)
    try:
        check('a free port gets a server of our own', server is not None, True)
        check('it is running', server.alive(), True)
        check('it is listening', rw.port_listening(port), True)
        check('and it writes where the referee will collect from',
              server.save_dir, os.path.join(data, 'saves'))
        check('which exists', os.path.isdir(server.save_dir), True)
    finally:
        server.stop()
    check('and it stops with the worker', server.alive(), False)
    check('leaving the port free', rw.port_listening(port), False)

    # A worker killed outright leaves its cs_server running; the next worker
    # ends it and starts its own.
    print('  recovering a server a killed worker left behind')
    data, work = tmpdir('data'), tmpdir('work')
    orphan = rw.ensure_stub_server(data, port=port, work_dir=work,
                                   log=lambda *a: None)
    check('a server is up to be orphaned', rw.port_listening(port), True)
    stale = orphan.listener_pid
    check('and its listener pid was recorded', isinstance(stale, int), True)
    # Control for the recovery below.
    check('which is recognisable as ours', rw.is_our_cs_server(stale), True)

    # The worker is now gone without running `stop`. Nothing holds `orphan`
    # any more; only the pid in the status file names it.
    said, log = lines()
    replacement = None
    try:
        replacement = rw.ensure_stub_server(data, port=port, work_dir=work,
                                            left_over=stale, log=log)
        check('the next worker starts rather than refusing',
              replacement is not None, True)
        check('it said it was ending the leftover',
              any('did not live to stop' in x for x in said), True)
        check('and that the port came free',
              any('is free again' in x for x in said), True)
        check('the orphan is gone', rw.is_our_cs_server(stale), False)
        check('the port is held by the new server',
              rw.port_listening(port), True)
        # The replacement is a new server, so its save directory is known.
        check('which is a server this worker started',
              replacement.listener_pid != stale, True)
        check('and writes where this worker said',
              replacement.save_dir, os.path.join(data, 'saves'))
    finally:
        if replacement is not None:
            replacement.stop()
        if rw.port_listening(port):
            rw.end_left_over(stale, port, log=lambda *a: None)
    check('and the port is free at the end', rw.port_listening(port), False)


# powercfg output for a scheme that never sleeps.
NEVER = """
    Power Setting GUID: 29f6c1db-86da-48c5-9fdb-f2b67b1f44da  (Sleep after)
      GUID Alias: STANDBYIDLE
      Possible Settings units: Seconds
    Current AC Power Setting Index: 0x00000000
    Current DC Power Setting Index: 0x00000000
"""

AFTER_15 = NEVER.replace('Current AC Power Setting Index: 0x00000000',
                         'Current AC Power Setting Index: 0x00000384')


def test_sleep():
    print('sleep')

    got = rw.parse_sleep_settings(NEVER)
    check('a scheme that never sleeps parses as zero',
          got.get('STANDBYIDLE'), {'ac': 0, 'dc': 0})
    said = rw.sleep_report(got)
    check('and is reported without a warning',
          any('WARNING' in x for x in said), False)

    got = rw.parse_sleep_settings(AFTER_15)
    check('a scheme that sleeps after 15 minutes parses as 900s',
          got.get('STANDBYIDLE', {}).get('ac'), 900)
    said = rw.sleep_report(got)
    check('and is reported loudly', any('WARNING' in x for x in said), True)
    check('with the number in it', any('900s' in x for x in said), True)
    check('and what to run about it',
          any('powercfg /change standby-timeout-ac 0' in x for x in said), True)

    check('a scheme that could not be read is a warning, not silence',
          any('WARNING' in x for x in rw.sleep_report({})), True)

    check('Windows accepts the request to stay awake', rw.keep_awake(), True)

    # The live machine's power scheme.
    live = rw.sleep_settings()
    check('the live power scheme reads back', bool(live), True)
    check('and it names the idle sleep timeout', 'STANDBYIDLE' in live, True)


def test_task_definition():
    print('the scheduled task')
    import xml.etree.ElementTree as ET

    path = os.path.join(ROOT, 'referee_worker_task.xml')
    raw = open(path, encoding='utf-8').read()

    # An XML comment cannot hold a double hyphen; Task Scheduler rejects it.
    comment = raw.split('<!--', 1)[1].split('-->', 1)[0]
    check('the comment carries no double hyphen', '--' in comment, False)

    filled = (raw.replace('{{USERID}}', 'MACHINE\\operator')
                 .replace('{{ARGUMENTS}}', '-File run_worker.ps1')
                 .replace('{{WORKINGDIRECTORY}}', 'C:\\repo'))
    ns = {'t': 'http://schemas.microsoft.com/windows/2004/02/mit/task'}
    root = ET.fromstring(filled)

    def text(path):
        el = root.find(path, ns)
        return el.text if el is not None else None

    # The task's command line, read from the script that writes it. Closing a
    # console window sends CTRL_CLOSE_EVENT to the worker, so the task must
    # have no window.
    script = open(os.path.join(ROOT, 'run_worker.ps1'),
                  encoding='utf-8').read()
    task_args = script.split('$Pass = @(', 1)[1].split(')', 1)[0]
    check('the task runs without a window to close', "'Hidden'" in task_args,
          True)
    check('and not merely minimized into the taskbar',
          "'Minimized'" in task_args, False)

    check('it triggers at logon, because a turn needs a desktop',
          root.find('.//t:LogonTrigger', ns) is not None, True)
    check('and not at boot', root.find('.//t:BootTrigger', ns) is None, True)
    check('it runs in the operator session',
          text('.//t:Principal/t:LogonType'), 'InteractiveToken')
    check('a second instance is ignored rather than started',
          text('.//t:MultipleInstancesPolicy'), 'IgnoreNew')
    check('and the repetition that restarts it after a crash is there',
          text('.//t:LogonTrigger/t:Repetition/t:Interval'), 'PT10M')
    check('a crash that exits nonzero is restarted too',
          text('.//t:RestartOnFailure/t:Interval'), 'PT5M')

    # Windows defaults that would stop the task on their own.
    check('it starts on battery',
          text('.//t:DisallowStartIfOnBatteries'), 'false')
    check('and is not stopped by unplugging the machine',
          text('.//t:StopIfGoingOnBatteries'), 'false')
    check('and has no execution time limit',
          text('.//t:ExecutionTimeLimit'), 'PT0S')
    check('and is not stopped when the machine stops being idle',
          text('.//t:IdleSettings/t:StopOnIdleEnd'), 'false')

    check('the placeholders are still placeholders in the template',
          '{{USERID}}' in raw, True)


def test_heartbeat():
    """What the worker tells the galaxy about itself, and what it costs."""
    print('the heartbeat a player can see')

    class Beat:
        def __init__(self, raises=False):
            self.wrote, self.raises = [], raises

        def update_state(self, d):
            if self.raises:
                raise OSError('store is gone')
            self.wrote.append(d)

    b = Beat()
    rw.heartbeat(b, 0, log=lambda *a: None)
    check('a tick records that the referee was here',
          rw.WORKER_SEEN_KEY in b.wrote[0], True)
    check('and how many times it has failed',
          b.wrote[0][rw.WORKER_FAILURES_KEY], 0)

    b2 = Beat()
    rw.heartbeat(b2, 3, log=lambda *a: None)
    check('a failing worker still reports, with its count',
          b2.wrote[0][rw.WORKER_FAILURES_KEY], 3)

    # A failed heartbeat must not end the turn.
    said = []
    rw.heartbeat(Beat(raises=True), 1, log=said.append)
    check('a store that refuses it does not stop the worker',
          any('heartbeat' in m for m in said), True)

    class Old:
        pass

    rw.heartbeat(Old(), 0, log=lambda *a: None)
    check('and a store too old to carry one simply has none', True, True)

    # An idle pass writes nothing; only a closed turn sends a heartbeat.
    src = inspect.getsource(rw.run)
    idle = src.split('referee.wait_for_client_free')[0]
    check('no heartbeat on an idle pass', 'heartbeat(' in idle, False)
    check('one after a turn closes',
          'heartbeat(' in src.split('referee.resolve_turn')[1], True)


def test_log():
    print('the log')
    work = tmpdir('log')
    log = rw.make_log(work_dir=work, echo=False)
    log('  worker: a line for the morning')
    log('two\nlines')
    check('the log file is where it was said to be', os.path.isfile(log.path),
          True)
    text = open(log.path, encoding='utf-8').read()
    check('and holds what was logged', 'a line for the morning' in text, True)
    check('with a timestamp', text.strip().startswith('20'), True)
    check('and a multi-line message is not one unreadable line',
          text.count('\n'), 3)


def test_operator_page():
    """The page the worker writes so the galaxy is read without a terminal."""
    print('the operator page')
    import operator_view

    def read(path):
        with open(path, encoding='utf-8') as f:
            return f.read()

    quiet = lambda *a: None                                 # noqa: E731

    # After a tick.
    page = os.path.join(tmpdir('page'), 'operator.html')
    s = fresh_store(seconds_left=-60)
    calls = Calls()
    undo = stub_referee(calls)
    try:
        check('a tick with a page path still closes the turn',
              rw.run(s, once=True, log=quiet, sleeper=quiet, page=page,
                     gid='sandbox'), rw.EXIT_OK)
        check('the page is written after a tick', os.path.isfile(page), True)
        text = read(page)
        check('and says which tick wrote it',
              'after closing turn 12' in text, True)
        check('and shows the galaxy as it now is, on turn 13',
              '<td class="">13</td>' in text, True)
        check('and the roster', 'DemoPlayer' in text and 'Neighbor' in text,
              True)

        # Control: an idle pass writes nothing.
        idle = os.path.join(tmpdir('page'), 'operator.html')
        s = fresh_store(seconds_left=3600)
        undo(); undo = stub_referee(Calls())
        rw.run(s, once=True, log=quiet, sleeper=quiet, page=idle)
        check('an idle pass writes no page', os.path.exists(idle), False)

        # After a failure, where the heartbeat has already counted it.
        failed = os.path.join(tmpdir('page'), 'operator.html')
        s = fresh_store(seconds_left=-60)
        undo(); undo = stub_referee(Calls(raises=OSError('the share went '
                                                         'away')))
        check('a failing tick with a page path is still a failure',
              rw.run(s, once=True, log=quiet, sleeper=quiet, page=failed), 1)
        check('the page is written after a failure', os.path.isfile(failed),
              True)
        text = read(failed)
        check('and says what failed',
              'after failure 1 on turn 12' in text
              and 'the share went away' in text, True)
        check('and shows the failure count the heartbeat recorded',
              '1 failure(s) in a row' in text, True)

        # Failures after the first rewrite the page at most every
        # FAILURE_PAGE_EVERY seconds, so a retry loop does not multiply reads.
        s = fresh_store(seconds_left=-60)
        undo(); undo = stub_referee(Calls(raises=OSError('still gone')))
        written, orig = [], rw.write_operator_page
        rw.write_operator_page = lambda *a, **k: written.append(a[2])
        clock, passes = [1000.0], []

        def sleeper(*_):
            passes.append(1)
            clock[0] += rw.BACKOFF_MAX
        try:
            rw.run(s, log=quiet, sleeper=sleeper, page='unused',
                   clock=lambda: clock[0], stop=lambda: len(passes) >= 5)
        finally:
            rw.write_operator_page = orig
        want = 1 + int(4 * rw.BACKOFF_MAX // rw.FAILURE_PAGE_EVERY)
        check('five failures in a row write the page on the first and then '
              'on the interval', len(written), want)

        # A page that cannot be rendered does not cost the tick.
        for exc in (RuntimeError('render broke'), SystemExit('store said no')):
            s = fresh_store(seconds_left=-60)
            calls = Calls()
            undo(); undo = stub_referee(calls)
            orig = operator_view.write_page

            def boom(*a, **k):
                raise exc
            operator_view.write_page = boom
            said, log = lines()
            try:
                got = rw.run(s, once=True, log=log, sleeper=quiet,
                             page=os.path.join(tmpdir('page'), 'x.html'))
            finally:
                operator_view.write_page = orig
            name = type(exc).__name__
            check(f'a {name} while rendering: the tick still succeeds', got,
                  rw.EXIT_OK)
            check(f'a {name} while rendering: the turn still advanced',
                  s.current()[0], 13)
            check(f'a {name} while rendering: it is logged',
                  any('could not write the operator page' in x for x in said),
                  True)
    finally:
        undo()

    # Atomic: written to a temporary file beside the page and moved over it,
    # and a failed move leaves the previous page whole and no temporary file.
    folder = tmpdir('atomic')
    path = os.path.join(folder, 'operator.html')
    s = fresh_store(seconds_left=3600)
    moves, real_replace = [], os.replace

    def spy(src, dst):
        moves.append((src, dst))
        real_replace(src, dst)
    os.replace = spy
    try:
        operator_view.write_page(s, path, event='at startup')
    finally:
        os.replace = real_replace
    check('the page is moved into place with one replace', len(moves), 1)
    check('from a temporary file in the same folder',
          moves and os.path.dirname(moves[0][0]) == folder
          and moves[0][0] != moves[0][1], True)
    before = read(path)

    def refuse(src, dst):
        raise PermissionError('the file is held')
    os.replace = refuse
    try:
        try:
            operator_view.write_page(s, path, event='later')
            raised = False
        except PermissionError:
            raised = True
    finally:
        os.replace = real_replace
    check('a failed move is raised to the worker, which swallows it', raised,
          True)
    check('and the previous page is left whole', read(path), before)
    check('and no temporary file is left behind', os.listdir(folder),
          ['operator.html'])

    # The reads stay inside the allowlist.
    touched = []

    class Watch:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            touched.append(name)
            return getattr(self._inner, name)
    operator_view.write_page(Watch(s), os.path.join(folder, 'w.html'))
    check('every store read the page makes is on STORE_READS',
          sorted(set(touched) - operator_view.STORE_READS), [])
    check('and it never downloads submissions', 'submissions' in touched,
          False)

    # When it was generated, and the script that marks it stale.
    now = 1_900_000_000
    operator_view.write_page(s, path, event='at startup', now=now)
    text = read(path)
    check('the page says when it was generated',
          'generated at ' + operator_view.fmt_when(now) in text, True)
    check('the staleness script is present', 'id="stale"' in text
          and 'Date.now()' in text, True)
    check('and compares against the time the page was generated',
          f'var generated = {now} * 1000' in text, True)
    check('with the turn length plus the margin as the limit',
          f'limit = {3600 + operator_view.STALE_MARGIN} * 1000' in text, True)
    check('the page reloads itself from disk',
          f'content="{operator_view.PAGE_REFRESH_SECONDS}"' in text, True)
    for forbidden in ('src=', 'http://', 'https://', '@import'):
        check(f'and it references nothing external, no {forbidden}',
              forbidden in text, False)

    # The command-line default, and the shortcut the operator opens it with.
    src = inspect.getsource(rw.main)
    check('the worker writes the page at startup', "'at startup'" in src, True)
    check('into the work directory by default',
          'os.path.join(a.work_dir, OPERATOR_PAGE)' in src, True)
    script = open(os.path.join(ROOT, 'run_worker.ps1'),
                  encoding='utf-8').read()
    check('run_worker.ps1 can write a shortcut to the page',
          '$WriteShortcut' in script and 'CreateShortcut' in script, True)
    check('and the shortcut opens the same default path',
          "'worker_work\\operator.html'" in script, True)


if __name__ == '__main__':
    test_overdue()
    test_loop()
    test_poll()
    test_second_copy()
    test_stub_server()
    test_sleep()
    test_task_definition()
    test_heartbeat()
    test_log()
    test_operator_page()
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    if FAIL:
        for n in FAIL:
            print('  FAILED:', n)
    sys.exit(1 if FAIL else 0)
