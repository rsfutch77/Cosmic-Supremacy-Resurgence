"""
referee_worker.py , the referee as something that survives a reboot
====================================================================
    python referee_worker.py --store ..\\galaxies\\sandbox
    python referee_worker.py --store ..\\galaxies\\sandbox --once
    python referee_worker.py --store <spec> --status

`referee.loop` is the turn loop and it is correct; what it is not is a thing
that starts by itself. Today a galaxy runs because somebody typed a command in
a terminal, and it runs for exactly as long as that window is open. At four
hours a turn over a permanent galaxy the machine's uptime is the galaxy's
uptime, so a window closed by a reboot for Windows Update costs every player
every turn until somebody notices.

This wraps the loop in the five things that unattended means, and it calls
`referee` rather than changing it: a turn is still closed by `resolve_turn`.

1.  **An overdue turn closes at startup.** A machine that was off across a
    deadline must not then wait a further four hours. The loop already resolves
    a turn whose deadline has passed, so this is not new behaviour, but it is
    said out loud and separately at startup because it is the behaviour a
    reboot depends on and a silent one cannot be checked.

2.  **One worker per galaxy.** Two referees ticking one galaxy is two clients
    and a corrupted turn. `game_cycle`'s advisory lock stops the second one
    inside the tick, several minutes of merging later, and only because both
    happen to want the client; it says nothing about two workers on two
    machines or about the turn they are both about to publish. The guard here
    is a named mutex on the galaxy, taken before anything is read.

3.  **The stub server, or a refusal naming what is on the port.** `cs_server.py`
    on 8888 is where a capture arrives, and without it a tick does the whole
    merge, launches the client, advances the galaxy and only then loses it.
    `referee.tick` and `resolve_turn` check the port, which is enough to not
    lose the turn and not enough to close it. So the worker starts a server of
    its own when the port is free and it knows where that one writes. When a
    stranger holds the port it refuses, because a server whose data directory
    it cannot ask about is the bug that killed every turn two seconds in: the
    capture succeeds, into somebody else's directory, and the referee waits for
    a file that was written somewhere else.

4.  **The machine is asked not to sleep**, and told on if it is going to
    anyway. `SetThreadExecutionState` is a request from this process for the
    idle timer to leave the system alone. It is not a power setting and it
    cannot become one: a lid close, a deliberate sleep and a Windows Update
    restart all still happen. So the idle timeouts are read back and reported,
    and an operator who sees a number there has something to act on.

5.  **A log that outlives the window.** Everything the referee prints goes to a
    rotating file as well as the console, so a failure at 3am is readable at
    9am from a terminal that is gone, and a status file beside it says what the
    worker was doing when it stopped.

What this cannot do, and why
----------------------------
It cannot start without a desktop session. `referee.tick` launches the game
client, which is a DirectX application, and a task that runs whether or not a
user is logged on runs in session 0, where there is no desktop to launch one
onto. So the shipped task triggers at logon rather than at boot, and reaching a
logon after a reboot with nobody there is the operator's automatic logon, not
something a program can arrange for itself. `referee_worker_task.xml` says the
same thing where the operator will be reading it.
"""
import argparse
import ctypes
import json
import hashlib
import logging
import logging.handlers
import os
import re
import socket
import subprocess
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for _d in (HERE, os.path.join(HERE, 'dev_tools')):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import referee
import turn_store

WINDOWS = os.name == 'nt'

# Every subprocess here is started by a process that may be running minimised
# behind a scheduled task, and a console window per netstat is the thing an
# operator asked not to have.
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

# Where the log, the status file and the stub server's output live. Named for
# the worker the way `referee_work` and `join_work` are named for what writes
# them, and it is not the store: a galaxy is shared and this is one machine's
# account of itself.
WORK_DIR = os.path.join(HERE, 'worker_work')
LOG_NAME = 'referee_worker.log'

# 4 MB a file and five files back. A quiet galaxy writes a few lines a turn and
# a failing one writes a traceback a turn, so the cap is there for the second
# case and five files is about a week of it.
LOG_BYTES, LOG_KEEP = 4 * 1024 * 1024, 5

# Seconds past the deadline that submissions are still taken. `referee.loop`'s
# default and the same reason: a launcher needs a few seconds to trigger a
# save, wait for it over HTTP and write it to the store, and a turn closed
# inside that window drops orders the player did give.
GRACE = 20.0

# How often the clock is read while waiting. The turn is hours and this costs a
# state read, so it is set by how promptly a deadline should be noticed rather
# than by load.
POLL = 5.0

# After a failed resolution the worker waits before trying again, doubling to a
# ceiling. A turn that cannot be closed usually cannot be closed a second later
# either, and retrying flat out turns one broken turn into a log nobody can
# read. The ceiling is well under a turn, so a fault that clears is picked up
# without an operator.
BACKOFF_FIRST, BACKOFF_MAX = 30.0, 300.0

# Exit codes, so the Task Scheduler history says which of these happened
# without anybody opening the log.
EXIT_OK = 0
EXIT_ANOTHER_WORKER = 2
EXIT_SERVER = 3
EXIT_NO_GALAXY = 4

STUB_PORT = 8888


# ── one worker per galaxy ────────────────────────────────────────────────────
class AnotherWorker(RuntimeError):
    """This galaxy already has a worker. Its own class because it is the one
    refusal a caller may want to treat as ordinary: a second copy started by a
    scheduled task that fired while the first was still running has done
    nothing wrong and should exit quietly."""


def galaxy_key(spec: str) -> str:
    """A stable name for one galaxy, whatever kind of store it is.

    A directory is compared by absolute path with case folded, because
    `..\\galaxies\\sandbox` and `C:\\Galaxies\\Sandbox` are one galaxy and
    Windows agrees. A URL and a `firebase://` spec are compared as text with a
    trailing slash removed, since neither has a filesystem to resolve against
    and two spellings of one relay are not worth guessing at.

    The answer is hashed rather than used as-is because it becomes a kernel
    object name, where a backslash is a namespace separator.
    """
    spec = (spec or '').strip()
    if spec.startswith(('http://', 'https://', 'firebase://')):
        norm = spec.rstrip('/').lower()
    else:
        norm = os.path.normcase(os.path.abspath(spec))
    return hashlib.sha1(norm.encode('utf-8')).hexdigest()[:16]


class GalaxyGuard:
    """One worker for one galaxy, enforced by the kernel and explained by a file.

    The mutex is the guarantee. It exists while a handle is open and it is gone
    the moment the process holding it dies, however it dies, so there is no
    stale state to clear and no liveness check to get wrong. `Local\\` rather
    than `Global\\`: the worker runs in the operator's own session because the
    game client has to, and a global name would need a privilege an ordinary
    account does not have.

    The file beside it is the explanation and nothing else. It names the pid so
    a refusal can point at a process, and it is written after the mutex is held
    and removed when it is released. A reader must not treat it as evidence:
    it can be left behind by a machine that lost power, and the client lock has
    already taught this project what happens when a file is believed about a
    process that is gone.

    On a machine with no `CreateMutexW` this raises rather than guessing. The
    referee runs the Windows game client, so there is no case to fall back for,
    and a guard that silently does nothing is worse than one that is absent.
    """

    def __init__(self, spec: str, work_dir: str = WORK_DIR):
        self.spec = spec
        self.key = galaxy_key(spec)
        self.name = f'Local\\cosmic_referee_{self.key}'
        self.path = os.path.join(work_dir, f'worker_{self.key}.json')
        self._handle = None
        # What the last worker of this galaxy wrote, read once as the guard is
        # taken and kept, because acquiring overwrites it. It is the only
        # account of a worker that was killed rather than stopped.
        self.previous = None
        self._facts = {}
        self._started = time.time()

    def holder(self):
        """What the last holder wrote about itself, or None. Not evidence that
        anyone is holding it; `acquire` is the only thing that answers that."""
        try:
            with open(self.path, encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def acquire(self, **facts):
        if not WINDOWS:
            raise RuntimeError('the worker guard needs Windows; the referee '
                               'runs the Windows game client')
        ERROR_ALREADY_EXISTS = 183
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                     ctypes.c_wchar_p]
        k32.CreateMutexW.restype = ctypes.c_void_p
        handle = k32.CreateMutexW(None, 1, self.name)
        err = ctypes.get_last_error()
        if not handle:
            raise OSError(err, f'could not create {self.name}')
        if err == ERROR_ALREADY_EXISTS:
            k32.CloseHandle(ctypes.c_void_p(handle))
            held = self.holder() or {}
            who = (f"pid {held['pid']}" if held.get('pid') else 'another process')
            since = (f", started {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(held['started']))}"
                     if held.get('started') else '')
            raise AnotherWorker(
                f'another worker already has {self.spec}: {who}{since}. Two '
                f'referees on one galaxy is two clients and a turn neither of '
                f'them published. Stop that one first, or leave it to it.')
        self._handle = handle
        self.previous = self.holder()
        self.write(**facts)
        return self

    def write(self, **facts):
        """Say what this worker is doing, for whoever reads the file next.

        Written over each time rather than appended, because this answers what
        is happening now and the log answers what happened. Facts accumulate
        across calls, so the loop saying which turn it is on does not erase
        what startup said about the server it began.
        """
        if self._handle is None:
            return
        self._facts.update(facts)
        record = {'pid': os.getpid(), 'store': self.spec,
                  'started': self._started, 'updated': time.time()}
        record.update(self._facts)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = f'{self.path}.{os.getpid()}.tmp'
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(record, f, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            # The status file is a convenience and the mutex is the guard, so
            # a machine that cannot write it still runs the galaxy.
            pass

    def release(self):
        if self._handle is None:
            return
        try:
            ctypes.WinDLL('kernel32').CloseHandle(ctypes.c_void_p(self._handle))
        finally:
            self._handle = None
        try:
            os.remove(self.path)
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()
        return False


# ── the stub server the capture arrives on ───────────────────────────────────
def port_listening(port: int = STUB_PORT, host: str = '127.0.0.1',
                   timeout: float = 2.0) -> bool:
    """Whether anything answers a connection, which is what the client will do.

    The same question `player_turn.save_path_ready` asks, asked here as well
    because the worker has to decide what to do about the answer before a turn
    is spent rather than refuse one at the boundary.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def port_holder(port: int = STUB_PORT):
    """(pid, image) for whatever is listening on `port`, or None.

    For naming a stranger in a refusal, never for deciding whether the port is
    in use: `netstat` is a snapshot of a table and a connection is the thing
    that actually has to work. A pid with no readable image name is still
    worth reporting, so the image falls back to None rather than the whole
    answer being dropped.
    """
    if not WINDOWS:
        return None
    try:
        out = subprocess.run(['netstat', '-ano', '-p', 'tcp'],
                             capture_output=True, text=True, timeout=20,
                             creationflags=_NO_WINDOW).stdout or ''
    except (OSError, subprocess.SubprocessError):
        return None
    pid = None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[3].upper() != 'LISTENING':
            continue
        local = parts[1]
        if local.rsplit(':', 1)[-1] != str(port):
            continue
        try:
            pid = int(parts[4])
        except ValueError:
            continue
        break
    if pid is None:
        return None
    image = None
    try:
        row = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/NH'],
                             capture_output=True, text=True, timeout=20,
                             creationflags=_NO_WINDOW).stdout or ''
        first = row.strip().split()
        if first and first[0].lower().endswith('.exe'):
            image = first[0]
    except (OSError, subprocess.SubprocessError):
        pass
    return pid, image


class StubServer:
    """`cs_server.py` started by the worker, against a data directory it named.

    Starting it rather than requiring it is the only way to know where a
    capture will land. `cs_server` writes to `CS_DATA_DIR\\saves` and there is
    no message in the protocol that asks a running one where that is, so a
    server this process did not start is a server whose save directory is a
    guess. The launcher reached the same conclusion from the other side and
    now refuses to reuse a foreign server for exactly this.

    Its output is kept in a file rather than sent to DEVNULL. A referee whose
    output went to DEVNULL died invisibly once already in this project, and the
    same reasoning applies to anything it starts.
    """

    def __init__(self, data_dir: str, port: int = STUB_PORT,
                 work_dir: str = WORK_DIR, log=print):
        self.data_dir = os.path.abspath(data_dir)
        self.port = port
        self.work_dir = work_dir
        self.log = log
        self.proc = None
        # The pid actually holding the socket, which on a virtualenv is not
        # `proc.pid`: see `stop`. Recorded so that a worker starting later can
        # recognise a server its predecessor left behind rather than reporting
        # it as a stranger.
        self.listener_pid = None
        self.out_path = os.path.join(work_dir, 'cs_server_stdout.log')

    @property
    def save_dir(self) -> str:
        return os.path.join(self.data_dir, 'saves')

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> None:
        os.makedirs(self.work_dir, exist_ok=True)
        os.makedirs(self.save_dir, exist_ok=True)
        env = dict(os.environ, CS_DATA_DIR=self.data_dir,
                   CSPORT=str(self.port))
        out = open(self.out_path, 'ab', buffering=0)
        self.proc = subprocess.Popen(
            [sys.executable, os.path.join(HERE, 'cs_server.py')],
            cwd=HERE, env=env, stdout=out, stderr=subprocess.STDOUT,
            creationflags=_NO_WINDOW)
        self.log(f'  worker: started cs_server.py on port {self.port}, '
                 f'pid {self.proc.pid}, writing to {self.save_dir}')

    def wait_until_listening(self, timeout: float = 30.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if self.proc is not None and self.proc.poll() is not None:
                return False
            if port_listening(self.port):
                held = port_holder(self.port)
                self.listener_pid = held[0] if held else None
                return True
            time.sleep(0.5)
        return False

    def stop(self) -> None:
        """End the server, and do not return until the port is actually free.

        Two processes, not one. A virtualenv's `python.exe` on Windows is a
        launcher that runs the real interpreter as a child, so `sys.executable`
        produces a pid that is not the pid holding the socket. Measured on this
        machine: `proc.pid` 43204 and the listener 44300.

        Waiting is the part that matters. Ending either of them frees the port
        in about 0.4 seconds, and `stop` used to return inside that, so the
        next thing to look would find the port still answering and conclude a
        stranger had it. An orphan on 8888 is the condition that killed every
        turn two seconds in once already, and the worst version of it is the
        one this worker creates for itself.

        The tree is killed as well as terminated, by pid, which costs nothing
        and covers a launcher that does not pass the signal on. Never by image
        name: a kill by name reaches every Python on the machine, including
        another agent's.
        """
        if not self.alive():
            return
        pid = self.proc.pid
        self.log(f'  worker: stopping the cs_server it started, pid {pid}')
        if WINDOWS:
            subprocess.run(['taskkill', '/T', '/F', '/PID', str(pid)],
                           capture_output=True, creationflags=_NO_WINDOW)
        try:
            self.proc.terminate()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.SubprocessError:
            pass
        end = time.time() + 10
        while time.time() < end and port_listening(self.port, timeout=0.5):
            time.sleep(0.1)
        if port_listening(self.port, timeout=0.5):
            self.log(f'  worker: WARNING something is still listening on port '
                     f'{self.port} after the stub server was stopped')


class ServerRefused(RuntimeError):
    """The port a capture arrives on is held by something the worker cannot
    account for. Fatal on purpose: every turn would be computed and then lost,
    which is worse than not starting."""


def ensure_stub_server(data_dir: str, port: int = STUB_PORT, adopt: bool = False,
                       work_dir: str = WORK_DIR, log=print, left_over=None):
    """A server on `port` whose save directory is known, or a refusal saying
    what is on it instead. Returns the `StubServer` it started, or None when
    one was already there and adopting it was allowed.

    Three cases and they are not symmetric. A free port is started on, and the
    worker then knows where captures land because it said so. An occupied port
    is refused, because the one thing that matters about the holder, its data
    directory, is the one thing that cannot be asked, and the failure it
    produces is a turn that computes for minutes and then reports that no
    capture appeared. `adopt` is for the operator who knows whose server it is
    and has pointed `--save-dir` at its directory; it is not a default, because
    the default would be to guess.

    `left_over` is the pid a previous worker of this galaxy recorded for the
    server it started. When the holder is that pid the refusal says so and
    names the one command that clears it, because a worker killed outright runs
    no cleanup and the server it started outlives it. Without that line the
    operator is told a stranger holds the port about a process this code
    started itself.
    """
    if port_listening(port):
        held = port_holder(port)
        who = 'something this worker did not start'
        if held:
            who = f'pid {held[0]}' + (f' ({held[1]})' if held[1] else '')
        if held and left_over and held[0] == left_over and not adopt:
            raise ServerRefused(
                f'port {port} is held by {who}, which is the cs_server a '
                f'previous worker of this galaxy started and did not live to '
                f'stop. Nothing is wrong with it except that this worker '
                f'cannot ask it what data directory it was given. End it '
                f'with: taskkill /F /PID {held[0]}')
        if adopt:
            log(f'  worker: port {port} is held by {who}; adopting it because '
                f'--adopt-server was given. Captures are collected from '
                f'{os.path.join(os.path.abspath(data_dir), "saves")}, and if '
                f'that is not where this server writes then every turn will '
                f'compute and then be lost.')
            return None
        raise ServerRefused(
            f'port {port} is already held by {who}. A capture arrives over '
            f'that port and lands in whatever data directory its server was '
            f'given, which nothing in the protocol can ask it. Reusing it is '
            f'how a turn is computed in full and then reported missing. Stop '
            f'that server, or point --data-dir at its data directory and pass '
            f'--adopt-server.')
    server = StubServer(data_dir, port=port, work_dir=work_dir, log=log)
    server.start()
    if not server.wait_until_listening():
        server.stop()
        raise ServerRefused(
            f'started cs_server.py and nothing was listening on port {port} '
            f'30 seconds later; its output is in {server.out_path}')
    return server


# ── the machine must not sleep ───────────────────────────────────────────────
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def keep_awake() -> bool:
    """Ask Windows to leave the system alone while this process runs.

    `ES_CONTINUOUS | ES_SYSTEM_REQUIRED` is a standing request against the idle
    timer, held by this thread for as long as the process lives and dropped
    with it, which is the behaviour wanted: a worker that has stopped has no
    business keeping a machine up.

    What it does not do is the part worth being clear about. It is not a power
    setting, and it does not stop a lid close, a chosen sleep, a Windows Update
    restart or a power cut. The display is deliberately not requested, since a
    dark monitor costs nothing and asking for one to stay lit for four months
    is a different thing to ask for.
    """
    if not WINDOWS:
        return False
    try:
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k32.SetThreadExecutionState.argtypes = [ctypes.c_uint]
        k32.SetThreadExecutionState.restype = ctypes.c_uint
        return bool(k32.SetThreadExecutionState(ES_CONTINUOUS |
                                                ES_SYSTEM_REQUIRED))
    except (OSError, AttributeError):
        return False


_SETTING = re.compile(r'GUID Alias:\s*(\S+)')
_INDEX = re.compile(r'Current (AC|DC) Power Setting Index:\s*(0x[0-9a-fA-F]+)')


def parse_sleep_settings(text: str) -> dict:
    """{alias: {'ac': seconds, 'dc': seconds}} out of `powercfg /q` output.

    The output is a tree printed flat, so a setting's two indexes follow its
    alias and belong to the alias most recently seen. Subgroup aliases appear
    the same way and are harmless: they carry no index lines of their own, so
    they end up as empty entries rather than as wrong ones.
    """
    out, current = {}, None
    for line in (text or '').splitlines():
        m = _SETTING.search(line)
        if m:
            current = m.group(1)
            out.setdefault(current, {})
            continue
        m = _INDEX.search(line)
        if m and current:
            out[current][m.group(1).lower()] = int(m.group(2), 16)
    return out


def sleep_settings() -> dict:
    """What the active power scheme does when the machine is left alone.

    Read only. Changing it is the operator's, both because it is their machine
    and because a program that quietly reconfigured power management would be
    the kind of thing this beta must not do.

    `powercfg` exits nonzero on this machine while printing the answer, so the
    output is parsed whatever it returns and an empty parse is what counts as
    a failure.
    """
    if not WINDOWS:
        return {}
    try:
        r = subprocess.run(['powercfg', '/q', 'SCHEME_CURRENT', 'SUB_SLEEP'],
                           capture_output=True, text=True, timeout=30,
                           creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return {}
    return parse_sleep_settings(r.stdout or '')


def sleep_report(settings: dict) -> list:
    """Lines about whether this machine will sleep out from under the galaxy.

    Every line is either a fact read off the scheme or a named unknown. The
    loud case is a nonzero idle timeout, because that is a machine configured
    to stop, and the request this worker makes is a request rather than a
    setting.
    """
    if not settings:
        return ['  worker: WARNING could not read the power scheme, so '
                'whether this machine sleeps is unknown. Check it with: '
                'powercfg /q SCHEME_CURRENT SUB_SLEEP']
    lines = []
    for alias, what in (('STANDBYIDLE', 'sleep'), ('HIBERNATEIDLE', 'hibernate')):
        got = settings.get(alias) or {}
        for side, plugged in (('ac', 'on mains'), ('dc', 'on battery')):
            secs = got.get(side)
            if secs is None:
                continue
            if secs == 0:
                lines.append(f'  worker: the machine is set never to {what} '
                             f'when idle {plugged}')
            else:
                lines.append(
                    f'  worker: WARNING the machine is set to {what} after '
                    f'{secs}s idle {plugged}. This worker asks the idle timer '
                    f'to leave it alone, which is a request and not a setting. '
                    f'To make it one: powercfg /change '
                    f'{"standby" if what == "sleep" else "hibernate"}-timeout-'
                    f'{side} 0')
    if not lines:
        lines.append('  worker: WARNING the power scheme named no idle sleep '
                     'timeout, so whether this machine sleeps is unknown')
    return lines


# ── logging that outlives the window ─────────────────────────────────────────
def make_log(path: str = None, work_dir: str = WORK_DIR, echo: bool = True):
    """A `log(line)` the referee can be handed, writing to a rotating file.

    The referee's functions all take `log` and call it with one finished
    string, so the worker's logging is a matter of where that string goes
    rather than of changing anything that produces it. Both places: the file is
    for the morning and the console is for an operator who is watching.
    """
    path = path or os.path.join(work_dir, LOG_NAME)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    logger = logging.getLogger(f'referee_worker.{os.path.abspath(path)}')
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        fmt = logging.Formatter('%(asctime)s %(message)s',
                                datefmt='%Y-%m-%d %H:%M:%S')
        fh = logging.handlers.RotatingFileHandler(
            path, maxBytes=LOG_BYTES, backupCount=LOG_KEEP, encoding='utf-8')
        fh.setFormatter(fmt)
        logger.addHandler(fh)
        if echo:
            sh = logging.StreamHandler(sys.stdout)
            sh.setFormatter(fmt)
            logger.addHandler(sh)

    def log(line=''):
        for part in str(line).rstrip().split('\n'):
            logger.info(part)
    log.path = os.path.abspath(path)
    return log


# ── the overdue turn ─────────────────────────────────────────────────────────
def overdue_by(store, grace: float = GRACE) -> float:
    """How long ago this turn should already have been closed, or 0.

    `grace` is subtracted rather than ignored, so a turn that came due four
    seconds ago is not overdue: a player's launcher is in the middle of
    capturing and submitting it, and closing the turn under them drops orders
    they did give. A turn that came due while the machine was off is overdue by
    hours and is not a close-run thing.
    """
    over = -store.seconds_left() - grace
    return over if over > 0 else 0.0


def close_overdue(store, save_dir=None, grace: float = GRACE, log=print) -> bool:
    """Close a turn whose deadline passed while nothing was running.

    Called once at startup, before the loop. The loop would reach the same
    conclusion on its first pass, and this exists beside it because a machine
    that reboots across a deadline is the case the whole item is about: a turn
    that waits a further four hours for want of anybody watching is the failure
    being prevented, and behaviour that is only implied by a loop is behaviour
    nobody checks.

    Returns whether a turn was closed, so a caller can tell "nothing was due"
    from "the galaxy caught up". Only one turn is closed however late the
    machine is: the turns that passed while it was off had no submissions to
    collect, and publishing three of them in a minute would spend orders
    players never got to give.
    """
    over = overdue_by(store, grace)
    if not over:
        turn, _ = store.current()
        log(f'  worker: turn {turn} is not due for '
            f'{store.seconds_left():.0f}s; nothing was missed')
        return False
    turn, _ = store.current()
    log(f'  worker: turn {turn} came due {over / 60:.0f} minute(s) ago and '
        f'nothing closed it; closing it now rather than at the next deadline')
    referee.wait_for_client_free(log=log)
    referee.resolve_turn(store, save_dir=save_dir, log=log)
    return True


# ── the loop ─────────────────────────────────────────────────────────────────
def run(store, save_dir=None, grace: float = GRACE, poll: float = POLL,
        once: bool = False, stop=None, guard=None, log=print,
        server=None, sleeper=time.sleep) -> int:
    """Close each turn as its deadline passes, for as long as the machine is up.

    The shape is `referee.loop`'s and the difference is what happens when a
    turn cannot be closed. `referee.loop` lets the exception out, which on a
    console is right: somebody is reading it. Unattended it is the galaxy
    stopping over something that is often momentary, a client that had not
    finished exiting or a share that was not there for a second, so here a
    failed resolution is logged in full, backed off from and tried again.

    `SystemExit` is caught with the rest deliberately. The referee raises it
    for a refusal rather than a fault, the save path not being ready and the
    client being held by somebody else, and both of those are conditions that
    clear on their own.
    """
    fails = 0
    said_closed = False
    while True:
        if stop is not None and stop():
            log('  worker: asked to stop')
            return EXIT_OK
        try:
            if server is not None and not server.alive():
                log('  worker: the cs_server it started has exited; '
                    'restarting it')
                server.start()
                server.wait_until_listening()

            # One read of the state per pass, rather than a call each for the
            # status, the clock and the turn number. All three come out of the
            # same document and every store pays for it as a read: on a
            # directory that is three opens a poll and on Firebase it is three
            # billed reads a poll, of a document that cannot have changed
            # between them.
            state = store.state()
            if turn_store.status_of(state) == turn_store.CLOSED:
                if not said_closed:
                    log('  worker: this galaxy is closed, so no turn will be '
                        'ticked. The worker stays up, and will resume if it '
                        'is reopened.')
                    said_closed = True
                sleeper(poll)
                continue
            said_closed = False

            turn, left = state['turn'], state['deadline'] - time.time()
            if guard is not None:
                guard.write(turn=turn, seconds_left=round(left, 1),
                            failures=fails)
            if left > 0:
                if once:
                    log(f'  worker: turn {turn} has {left:.0f}s left, '
                        f'nothing to do')
                    return EXIT_OK
                sleeper(min(poll, left))
                continue
            if -left < grace:
                sleeper(min(poll, grace + left))
                continue

            referee.wait_for_client_free(log=log)
            referee.resolve_turn(store, save_dir=save_dir, log=log)
            fails = 0
            if once:
                return EXIT_OK
        except (Exception, SystemExit) as exc:              # noqa: BLE001
            fails += 1
            wait = min(BACKOFF_FIRST * (2 ** (fails - 1)), BACKOFF_MAX)
            log(f'  worker: turn not closed ({type(exc).__name__}: {exc}); '
                f'attempt {fails}, waiting {wait:.0f}s')
            log(traceback.format_exc())
            if guard is not None:
                guard.write(failures=fails, last_error=f'{type(exc).__name__}: {exc}',
                            last_error_at=time.time())
            if once:
                return 1
            sleeper(wait)


def describe(store, spec: str, log=print) -> None:
    """One block at startup saying what this worker is about to run.

    An unattended process is read backwards from a log, so the log has to open
    with the facts a reader would otherwise have to ask a terminal that is
    gone: which galaxy, which turn, when it is due, who is in it.
    """
    log(f'  worker: galaxy {spec}')
    try:
        turn, deadline = store.current()
        log(f'  worker: turn {turn}, due '
            f'{time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(deadline))} '
            f'({store.seconds_left():.0f}s), roster {store.civs() or "(none)"}')
        if store.is_closed():
            log(f'  worker: this galaxy is CLOSED '
                f'({store.closed_reason() or "no reason recorded"})')
    except Exception as exc:                                # noqa: BLE001
        log(f'  worker: could not read the galaxy state ({type(exc).__name__}: '
            f'{exc})')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description='Run the referee unattended over one galaxy.')
    ap.add_argument('--store', required=True,
                    help='the galaxy: a directory, a turn_server.py base URL '
                         'or a firebase:// spec')
    ap.add_argument('--data-dir', default=HERE,
                    help='what cs_server.py is given as CS_DATA_DIR; captures '
                         'arrive in its saves subdirectory. Defaults to the '
                         "checkout's server directory")
    ap.add_argument('--save-dir',
                    help='where captures are collected from, if that is not '
                         '<data-dir>\\saves')
    ap.add_argument('--work-dir', default=WORK_DIR,
                    help='where the log and the status file are kept')
    ap.add_argument('--log',
                    help='the log file, if not <work-dir>\\' + LOG_NAME +
                         '. A worker on a second galaxy wants one of its own, '
                         'since two processes rotating one file is a race')
    ap.add_argument('--grace', type=float, default=GRACE,
                    help='seconds past the deadline to keep taking submissions')
    ap.add_argument('--poll', type=float, default=POLL,
                    help='how often the clock is read')
    ap.add_argument('--port', type=int, default=STUB_PORT,
                    help='the port cs_server.py listens on')
    ap.add_argument('--no-cs-server', action='store_true',
                    help='do not start a stub server and do not refuse one '
                         'that is already there. The referee still checks the '
                         'port before it spends a turn, so this loses the '
                         'early refusal and not the turn')
    ap.add_argument('--adopt-server', action='store_true',
                    help='use a stub server already on the port. Only with a '
                         '--data-dir or --save-dir naming where that server '
                         'writes, since nothing can ask it')
    ap.add_argument('--once', action='store_true',
                    help='close at most one turn and exit')
    ap.add_argument('--no-catch-up', action='store_true',
                    help='do not close an overdue turn at startup')
    ap.add_argument('--status', action='store_true',
                    help='say what the worker for this galaxy last wrote '
                         'about itself, and exit')
    a = ap.parse_args(argv)

    guard = GalaxyGuard(a.store, work_dir=a.work_dir)
    if a.status:
        held = guard.holder()
        print(json.dumps(held, indent=2) if held else
              f'no worker has written a status file for {a.store} '
              f'({guard.path})')
        return EXIT_OK

    log = make_log(a.log, work_dir=a.work_dir)
    log('')
    log(f'  worker: starting, pid {os.getpid()}, logging to {log.path}')

    try:
        guard.acquire(log=log.path)
    except AnotherWorker as exc:
        log(f'  worker: {exc}')
        return EXIT_ANOTHER_WORKER

    server = None
    try:
        for line in sleep_report(sleep_settings()):
            log(line)
        if keep_awake():
            log('  worker: asked Windows not to sleep while this runs')
        else:
            log('  worker: WARNING could not ask Windows to stay awake; the '
                'idle timer is whatever the power scheme says')

        store = turn_store.open_store(a.store)
        if not store.exists():
            log(f'  worker: there is no galaxy at {a.store}. Start one with '
                f'referee.py --store <spec> --start <blob>')
            return EXIT_NO_GALAXY
        describe(store, a.store, log=log)

        save_dir = a.save_dir or os.path.join(os.path.abspath(a.data_dir),
                                              'saves')
        if a.no_cs_server:
            log(f'  worker: not managing a stub server; captures will be '
                f'collected from {save_dir}')
        else:
            try:
                server = ensure_stub_server(
                    a.data_dir, port=a.port, adopt=a.adopt_server,
                    work_dir=a.work_dir, log=log,
                    left_over=(guard.previous or {}).get('cs_server_pid'))
            except ServerRefused as exc:
                log(f'  worker: {exc}')
                return EXIT_SERVER
            if server is not None:
                save_dir = a.save_dir or server.save_dir
                guard.write(cs_server_pid=server.listener_pid)
        log(f'  worker: captures are collected from {save_dir}')

        if not a.no_catch_up:
            try:
                close_overdue(store, save_dir=save_dir, grace=a.grace, log=log)
            except (Exception, SystemExit) as exc:          # noqa: BLE001
                # An overdue turn that will not close is not a reason to refuse
                # to run: the loop retries it in a few seconds and reports it
                # the same way as any other turn that failed.
                log(f'  worker: the overdue turn did not close '
                    f'({type(exc).__name__}: {exc}); the loop will try again')
                log(traceback.format_exc())

        return run(store, save_dir=save_dir, grace=a.grace, poll=a.poll,
                   once=a.once, guard=guard, log=log, server=server)
    except KeyboardInterrupt:
        log('  worker: stopped from the console')
        return EXIT_OK
    finally:
        if server is not None:
            server.stop()
        guard.release()
        log('  worker: stopped')


if __name__ == '__main__':
    sys.exit(main())
