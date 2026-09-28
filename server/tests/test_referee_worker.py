"""
test_referee_worker.py , the five things unattended has to mean
===============================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_referee_worker.py

Every check here is paired with the case that would pass a broken
implementation, because each of these is easy to satisfy vacuously:

  * the overdue turn. A worker that closes a turn at startup unconditionally
    passes "an overdue turn is closed" and destroys every turn that was not
    overdue, so a store whose deadline has **not** passed is asserted to be
    left alone, and so is one that is four seconds past it and still inside the
    grace window a player's launcher is submitting in.
  * the second copy. A test that reads a flag proves nothing about a guard, so
    a second worker is actually started, as a process, and its exit code is the
    answer. A third is started after the first is killed, because a guard that
    refuses everything for ever is not a guard either.
  * the stub server. A real listener is bound on a spare port and the refusal
    is asked for against it, then the port is freed and a real `cs_server.py`
    is started on it. Port 8888 is never touched: a launcher may hold it, and
    the answer this code gives about 8888 must not depend on this test.

What is not here is a real tick, which needs the game client. Everything that
would reach one is stubbed at `referee.resolve_turn`, and what is under test is
the worker's decision to call it rather than anything it does.
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
    """A store whose deadline is exactly where the case wants it.

    `start` puts the deadline a turn length away, so the clock is moved
    afterwards rather than by choosing a turn length, which keeps the two
    directions of every overdue check one number apart.
    """
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
            # What a real resolution does to the clock, which is what stops the
            # loop closing the same turn again.
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


# ── the overdue turn, in both directions ─────────────────────────────────────
def test_overdue():
    print('the overdue turn')

    # Not due at all. The case a worker that always catches up would destroy:
    # closing this turn throws away every submission still being played.
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

        # Four seconds past the deadline is inside the grace window, where a
        # player's launcher is still writing the turn it just captured.
        s = fresh_store(seconds_left=-4)
        check('overdue_by is 0 inside the grace window', rw.overdue_by(s), 0.0)
        said, log = lines()
        check('close_overdue leaves a turn inside the grace window',
              rw.close_overdue(s, log=log), False)
        check('and still closes nothing', calls.n, 0)

        # The case the item exists for: the machine was off across a deadline.
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

        # One turn, however late. Three turns passed while the machine was off
        # and none of them had submissions to collect; publishing them in a
        # minute spends orders no player was given the chance to write.
        s = fresh_store(seconds_left=-7200)
        rw.close_overdue(s, log=lambda *a: None)
        check('and only one, however many deadlines were missed',
              s.current()[0], 13)
    finally:
        undo()


# ── the loop ─────────────────────────────────────────────────────────────────
def test_loop():
    print('the loop')

    # A due turn closes.
    s = fresh_store(seconds_left=-60)
    calls = Calls()
    undo = stub_referee(calls)
    try:
        said, log = lines()
        check('run closes a turn whose deadline has passed',
              rw.run(s, save_dir='D:\\saves', once=True, log=log,
                     sleeper=lambda *a: None), rw.EXIT_OK)
        check('and closed it once', calls.n, 1)

        # The control: the same call on a turn with time left must do nothing.
        s = fresh_store(seconds_left=3600)
        calls = Calls()
        undo(); undo = stub_referee(calls)
        said, log = lines()
        check('run leaves a turn that still has time',
              rw.run(s, once=True, log=log, sleeper=lambda *a: None),
              rw.EXIT_OK)
        check('and closed nothing', calls.n, 0)

        # A refusal out of the referee is a SystemExit, which is not an
        # Exception. A worker that catches the wrong one dies overnight on a
        # condition that clears by itself.
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

        # The same for an ordinary exception.
        s = fresh_store(seconds_left=-60)
        calls = Calls(raises=OSError('the share went away'))
        undo(); undo = stub_referee(calls)
        said, log = lines()
        check('an OSError does not kill it either',
              rw.run(s, once=True, log=log, sleeper=lambda *a: None), 1)
        check('and the backoff is said out loud',
              any('waiting' in x for x in said), True)

        # A closed galaxy is not ticked. The worker stays up, because closing
        # is the operator's word and they may take it back.
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


# ── one worker per galaxy ────────────────────────────────────────────────────
def start_worker(store_root, work_dir, *extra):
    """A real worker process over a galaxy whose turn is not due.

    `--no-cs-server` because this must not bind port 8888, which a launcher may
    hold, and because a worker that never reaches a deadline never needs a
    capture. Nothing here can reach the game client: `run` calls the referee
    only when a turn is due, and this one is due in an hour.
    """
    return subprocess.Popen(
        [sys.executable, WORKER, '--store', store_root,
         '--work-dir', work_dir, '--poll', '1', *extra],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def wait_for_holder(path, timeout=30.0):
    """What the worker holding the galaxy wrote about itself, once it has.

    The pid it records is its own and is not the one `Popen` returned: a
    virtualenv's `python.exe` on Windows is a launcher that runs the real
    interpreter as a child, so the process that takes the mutex is a
    grandchild of this test. That is why the refusal is asserted against the
    pid in the record rather than against the one this test started.
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
    """End a worker and the interpreter its launcher started.

    By pid rather than by image name. A kill by name reaches every Python on
    the machine, which is how a test in `release\\tests` nearly ended another
    agent's session, and the pid of a process this test started is the only
    thing it is entitled to end.
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

        # The check that matters: a second worker, as a process, is refused.
        # `--once` so that a guard which failed to refuse shows up as a clean
        # exit 0 rather than as a test that hangs.
        second = start_worker(s.root, work, '--no-cs-server', '--once')
        out, _ = second.communicate(timeout=60)
        check('a second worker is refused', second.returncode,
              rw.EXIT_ANOTHER_WORKER)
        check('and it names the process holding the galaxy',
              held and f"pid {held['pid']}" in out, True)
        check('and it did not close a turn', 'closing turn' in out, False)

        # A worker on a different galaxy is not refused, or one machine could
        # only ever run one galaxy. It is the galaxy that is guarded here; the
        # single game client is `game_cycle`'s lock and a different question.
        other = fresh_store(seconds_left=3600)
        third = start_worker(other.root, work, '--no-cs-server', '--once')
        out, _ = third.communicate(timeout=60)
        check('a worker on another galaxy is allowed', third.returncode,
              rw.EXIT_OK)
    finally:
        kill_tree(first)

    # And the guard is gone with the process that held it, with nothing to
    # clear by hand. A stale lock file that outlived its holder is exactly how
    # the client lock has misled this project before, so the file is not what
    # is being asked here: the mutex is.
    fourth = start_worker(s.root, work, '--no-cs-server', '--once')
    out, _ = fourth.communicate(timeout=60)
    check('the galaxy is free once the worker holding it has died',
          fourth.returncode, rw.EXIT_OK)
    check('and the refusal is not repeated to the next one',
          'another worker already has' in out, False)

    # The status file is how a worker starting later learns which cs_server
    # its predecessor began, so a write from the loop must not erase what
    # startup wrote. It accumulates rather than replacing.
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


# ── the stub server the capture arrives on ───────────────────────────────────
def spare_port():
    """A port nothing is using, which is never 8888: a launcher may be on it
    and this test must not touch it."""
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
    # A backlog deep enough that the connections these checks make are not
    # themselves what closes the port. With a backlog of one, the second
    # `port_listening` call finds a queue that is full and reports the port as
    # free, and the test then measures its own accept queue rather than the
    # code.
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

        # A worker that was killed rather than stopped leaves its server
        # running, and the next one must not describe its own predecessor as a
        # stranger. The pid is the one holding the socket, which on a
        # virtualenv is not the pid Popen returned.
        mine = None
        try:
            rw.ensure_stub_server(tmpdir('data'), port=port,
                                  left_over=os.getpid(), log=lambda *a: None)
        except rw.ServerRefused as exc:
            mine = str(exc)
        check('a server a previous worker left is recognised',
              mine and 'previous worker of this galaxy' in mine, True)
        check('and the refusal says what to run about it',
              mine and f'taskkill /F /PID {os.getpid()}' in mine, True)
        check('while a stranger is still described as one',
              'previous worker of this galaxy' in refused, False)

        # The operator who knows whose server it is says so, and is not
        # refused. Without this the flag would be untested in the only
        # direction it exists for.
        said, log = lines()
        adopted = rw.ensure_stub_server(tmpdir('data'), port=port, adopt=True,
                                        work_dir=tmpdir('work'), log=log)
        check('--adopt-server proceeds', adopted, None)
        check('and warns where captures are expected',
              any('adopting it' in x for x in said), True)
    finally:
        stranger.close()

    check('the port is free again', rw.port_listening(port), False)

    # The other half: a free port is started on, and the worker then knows
    # where the capture will land because it said so.
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


# ── the machine must not sleep ───────────────────────────────────────────────
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

    # The request itself. It is not a setting and cannot become one, but a
    # worker that could not even ask has to say so, so the call is checked
    # rather than assumed.
    check('Windows accepts the request to stay awake', rw.keep_awake(), True)

    # And against the live machine, which is what the operator will read.
    live = rw.sleep_settings()
    check('the live power scheme reads back', bool(live), True)
    check('and it names the idle sleep timeout', 'STANDBYIDLE' in live, True)


# ── the task definition that is shipped rather than described ────────────────
def test_task_definition():
    print('the scheduled task')
    import xml.etree.ElementTree as ET

    path = os.path.join(ROOT, 'referee_worker_task.xml')
    raw = open(path, encoding='utf-8').read()

    # An XML comment cannot hold a double hyphen, and the underline this
    # project puts under a heading everywhere else is exactly that. It cost a
    # round of "incorrect comment syntax" from Task Scheduler, which names a
    # line and a column and not the character.
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

    # The command line the task will carry, read out of the script that writes
    # it. Fails if the task ever gets a window an operator can close: closing a
    # console window sends CTRL_CLOSE_EVENT to everything attached to it, so a
    # minimized one is a taskbar button whose only behaviour is to stop the
    # galaxy, silently, with nothing in the log saying why. That happened once,
    # and the only trace was the task reporting 0xC000013A.
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

    # The three Windows defaults that would stop the galaxy on their own.
    check('it starts on battery',
          text('.//t:DisallowStartIfOnBatteries'), 'false')
    check('and is not stopped by unplugging the machine',
          text('.//t:StopIfGoingOnBatteries'), 'false')
    check('and has no execution time limit',
          text('.//t:ExecutionTimeLimit'), 'PT0S')
    check('and is not stopped when the machine stops being idle',
          text('.//t:IdleSettings/t:StopOnIdleEnd'), 'false')

    # Nothing about this file registers anything. A template that shipped with
    # a registration in it would be the one thing the operator did not ask for.
    check('the placeholders are still placeholders in the template',
          '{{USERID}}' in raw, True)


# ── the log that outlives the window ─────────────────────────────────────────
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

    # A heartbeat that could end a turn would be worse than none at all.
    said = []
    rw.heartbeat(Beat(raises=True), 1, log=said.append)
    check('a store that refuses it does not stop the worker',
          any('heartbeat' in m for m in said), True)

    class Old:
        pass

    rw.heartbeat(Old(), 0, log=lambda *a: None)
    check('and a store too old to carry one simply has none', True, True)

    # The cost rule. An idle pass must write nothing, or one galaxy spends
    # 17,280 writes a day against a 20,000 a day tier, which would undo
    # more than the polling work saved.
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


if __name__ == '__main__':
    test_overdue()
    test_loop()
    test_second_copy()
    test_stub_server()
    test_sleep()
    test_task_definition()
    test_heartbeat()
    test_log()
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    if FAIL:
        for n in FAIL:
            print('  FAILED:', n)
    sys.exit(1 if FAIL else 0)
