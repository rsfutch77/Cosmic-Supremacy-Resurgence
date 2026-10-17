"""
referee_worker.py: the referee as something that survives a reboot
====================================================================
    python referee_worker.py --store ..\\galaxies\\sandbox
    python referee_worker.py --store ..\\galaxies\\sandbox --once
    python referee_worker.py --store <spec> --status

Wraps `referee`'s turn loop for unattended running; turns are still closed by
`referee.resolve_turn`.

1.  An overdue turn is closed at startup.
2.  One worker per galaxy, enforced by a named mutex taken before anything is
    read.
3.  The worker starts its own `cs_server.py` on 8888 so it knows where captures
    land, and refuses to start if a server it cannot account for holds the port.
4.  The machine is asked not to sleep (`SetThreadExecutionState`), and the
    power scheme's idle timeouts are reported.
5.  Output goes to a rotating log file as well as the console, with a status
    file beside it.
6.  `operator_view`'s page is written to `<work-dir>\\operator.html` at startup,
    after every tick and after a failure. `run_worker.ps1 -WriteShortcut` makes
    a shortcut to it.

The game client needs a desktop session, so the scheduled task
(`referee_worker_task.xml`) triggers at logon, not at boot; an unattended reboot
needs automatic logon.
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

# Subprocesses run without a console window.
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

# Where the log, the status file and the stub server's output live. One
# machine's records, separate from the shared store.
WORK_DIR = os.path.join(HERE, 'worker_work')
LOG_NAME = 'referee_worker.log'

LOG_BYTES, LOG_KEEP = 4 * 1024 * 1024, 5   # bytes per log file, files kept

# Heartbeat fields written into the galaxy state, so a launcher can tell a
# referee that is down from one that is up and failing.
WORKER_SEEN_KEY = 'worker_seen'
WORKER_FAILURES_KEY = 'worker_failures'

# Seconds past the deadline that submissions are still taken.
GRACE = 20.0

# Seconds between clock reads while waiting.
POLL = 5.0

# Retry wait after a failed resolution, in seconds: doubles from the first
# value up to the ceiling.
BACKOFF_FIRST, BACKOFF_MAX = 30.0, 300.0

# Exit codes, visible in the Task Scheduler history.
EXIT_OK = 0
EXIT_ANOTHER_WORKER = 2
EXIT_SERVER = 3
EXIT_NO_GALAXY = 4

STUB_PORT = 8888

OPERATOR_PAGE = 'operator.html'   # file name in the work directory

FAILURE_PAGE_EVERY = 900.0   # shortest gap, in seconds, between failure pages


class AnotherWorker(RuntimeError):
    """This galaxy already has a worker. A separate class so a caller can exit
    quietly on it."""


def galaxy_key(spec: str) -> str:
    """A stable name for one galaxy, whatever kind of store it is.

    A directory is normalised to its case-folded absolute path; a URL or
    `firebase://` spec is lowercased with any trailing slash removed. The
    result is hashed because it becomes a kernel object name.
    """
    spec = (spec or '').strip()
    if spec.startswith(('http://', 'https://', 'firebase://')):
        norm = spec.rstrip('/').lower()
    else:
        norm = os.path.normcase(os.path.abspath(spec))
    return hashlib.sha1(norm.encode('utf-8')).hexdigest()[:16]


class GalaxyGuard:
    """One worker for one galaxy, enforced by the kernel and explained by a file.

    The `Local\\` named mutex is the guarantee; it is released when the holding
    process dies. The status file beside it names the pid for refusal messages
    and must not be treated as evidence that a worker is running. Raises on a
    machine without `CreateMutexW`.
    """

    def __init__(self, spec: str, work_dir: str = WORK_DIR):
        self.spec = spec
        self.key = galaxy_key(spec)
        self.name = f'Local\\cosmic_referee_{self.key}'
        self.path = os.path.join(work_dir, f'worker_{self.key}.json')
        self._handle = None
        # The previous worker's status file, read before acquiring overwrites
        # it.
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

        Overwritten each time; facts accumulate across calls.
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
            # The status file is optional; the mutex is the guard.
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


def port_listening(port: int = STUB_PORT, host: str = '127.0.0.1',
                   timeout: float = 2.0) -> bool:
    """Whether anything accepts a connection on `port`."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def port_holder(port: int = STUB_PORT):
    """(pid, image) for whatever is listening on `port`, or None.

    For naming the holder in a message, not for deciding whether the port is
    in use (`port_listening` does that). The image is None when unreadable.
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


def process_command_line(pid: int):
    """The full command line of `pid`, or None when it cannot be read.

    None on any failure. The image name alone cannot tell one `python.exe`
    from another.
    """
    if not WINDOWS:
        return None
    try:
        out = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command',
             f'(Get-CimInstance Win32_Process -Filter '
             f'"ProcessId={int(pid)}").CommandLine'],
            capture_output=True, text=True, timeout=30,
            creationflags=_NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return (out or '').strip() or None


def is_our_cs_server(pid: int) -> bool:
    """Whether `pid` is a Python running this checkout's `cs_server.py`.

    False whenever it cannot be established, since a recorded pid may have
    been reused. The command line must name this checkout's `cs_server.py`; a
    server from another checkout counts as a stranger.
    """
    line = process_command_line(pid)
    if not line:
        return False
    want = os.path.normcase(os.path.join(HERE, 'cs_server.py'))
    return want in os.path.normcase(line)


def end_left_over(pid: int, port: int = STUB_PORT, log=print) -> bool:
    """End a `cs_server` a previous worker of this galaxy left behind, and say
    whether the port came free.

    A killed worker leaves its server holding the port. The caller has already
    verified the pid is this checkout's `cs_server.py`. A launcher sharing the
    server loses a capture in progress. The tree is killed by pid, never by
    image name, and this waits up to 10 seconds for the port to free.
    """
    log(f'  worker: port {port} is held by pid {pid}, which is the cs_server '
        f'a previous worker of this galaxy started and did not live to stop. '
        f'Its command line names this checkout, so it is ended here rather '
        f'than left for an operator to find.')
    if WINDOWS:
        subprocess.run(['taskkill', '/T', '/F', '/PID', str(pid)],
                       capture_output=True, creationflags=_NO_WINDOW)
    end = time.time() + 10
    while time.time() < end and port_listening(port, timeout=0.5):
        time.sleep(0.1)
    if port_listening(port, timeout=0.5):
        log(f'  worker: WARNING pid {pid} was ended and port {port} is still '
            f'held, so something else is on it as well')
        return False
    log(f'  worker: port {port} is free again')
    return True


class StubServer:
    """`cs_server.py` started by the worker, against a data directory it named.

    `cs_server` writes captures to `CS_DATA_DIR\\saves`, which the protocol
    cannot report, so only a server started here has a known save directory.
    Its output goes to `cs_server_stdout.log` in the work directory, so a
    crash leaves a record.
    """

    def __init__(self, data_dir: str, port: int = STUB_PORT,
                 work_dir: str = WORK_DIR, log=print):
        self.data_dir = os.path.abspath(data_dir)
        self.port = port
        self.work_dir = work_dir
        self.log = log
        self.proc = None
        # The pid holding the socket, which under a virtualenv is not
        # `proc.pid`. Recorded so a later worker can recognise a leftover.
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

        A virtualenv's `python.exe` runs the real interpreter as a child, so the
        process tree is killed by pid (never by image name) as well as
        terminated. Returns only once the port stops answering, or after 10
        seconds.
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
    """The capture port is held by something the worker cannot account for.
    Fatal, since every turn would be computed and then lost."""


def ensure_stub_server(data_dir: str, port: int = STUB_PORT, adopt: bool = False,
                       work_dir: str = WORK_DIR, log=print, left_over=None):
    """A server on `port` whose save directory is known, or a refusal saying
    what is on it instead. Returns the `StubServer` it started, or None when
    one was already there and adopting it was allowed.

    A free port is started on. An occupied port is refused, unless `adopt` is
    set (the operator has pointed `--save-dir` at that server's directory), or
    the holder is `left_over`, the pid a previous worker of this galaxy
    recorded, verified by `is_our_cs_server`; that one is ended and replaced.
    A recorded pid now held by another process is refused.
    """
    if port_listening(port):
        held = port_holder(port)
        who = 'something this worker did not start'
        if held:
            who = f'pid {held[0]}' + (f' ({held[1]})' if held[1] else '')
        mine = bool(held) and bool(left_over) and held[0] == left_over \
            and not adopt
        # Pids are reused, so the process must be verified before it is ended.
        if mine and is_our_cs_server(held[0]):
            if not end_left_over(held[0], port, log=log):
                raise ServerRefused(
                    f'port {port} was held by {who}, the cs_server a previous '
                    f'worker of this galaxy left behind. It was ended and the '
                    f'port is still held, so something else is on it too. '
                    f'Look with: netstat -ano | findstr :{port}')
            # Falls through to start a fresh server.
        elif mine:
            raise ServerRefused(
                f'port {port} is held by {who}. A previous worker of this '
                f'galaxy recorded that pid for the cs_server it started, but '
                f'what holds it now is not this checkout\'s cs_server, so the '
                f'number has been reused and this is a stranger. Stop it '
                f'yourself, or point --data-dir at its data directory and '
                f'pass --adopt-server.')
        elif adopt:
            log(f'  worker: port {port} is held by {who}; adopting it because '
                f'--adopt-server was given. Captures are collected from '
                f'{os.path.join(os.path.abspath(data_dir), "saves")}, and if '
                f'that is not where this server writes then every turn will '
                f'compute and then be lost.')
            return None
        else:
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


ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def keep_awake() -> bool:
    """Ask Windows to leave the system alone while this process runs.

    `ES_CONTINUOUS | ES_SYSTEM_REQUIRED` holds off the idle timer until the
    process exits. It does not prevent a lid close, a chosen sleep or an update
    restart, and does not keep the display on.
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

    Each index line belongs to the most recent alias. Subgroup aliases have no
    index lines and end up as empty entries.
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

    Read only. `powercfg` can exit nonzero while printing the answer, so only
    an empty parse counts as failure.
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

    A nonzero idle timeout is a warning.
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


def make_log(path: str = None, work_dir: str = WORK_DIR, echo: bool = True):
    """A `log(line)` the referee can be handed, writing to a rotating file and,
    with `echo`, the console."""
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


def overdue_by(store, grace: float = GRACE) -> float:
    """Seconds past the deadline beyond `grace`, or 0."""
    over = -store.seconds_left() - grace
    return over if over > 0 else 0.0


def close_overdue(store, save_dir=None, grace: float = GRACE, log=print) -> bool:
    """Close a turn whose deadline passed while nothing was running.

    Called once at startup, before the loop. Returns whether a turn was
    closed. Only one turn is closed however late the machine is.
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


def heartbeat(store, fails: int, log=print) -> None:
    """Record in the galaxy state that its referee is alive, and its failures.

    Called when a turn closes or fails, never on an idle pass. Never raises;
    a store without `update_state` gets no heartbeat.
    """
    write = getattr(store, 'update_state', None)
    if write is None:
        return
    try:
        write({WORKER_SEEN_KEY: time.time(), WORKER_FAILURES_KEY: int(fails)})
    except Exception as exc:                                # noqa: BLE001
        log(f'  worker: could not record a heartbeat ({type(exc).__name__}: '
            f'{exc}); the turn is unaffected')


def write_operator_page(store, path, event: str, gid: str = None,
                        log=print) -> bool:
    """Write the operator page for this galaxy, and say whether it was written.

    Never raises except `KeyboardInterrupt`: any other failure, `SystemExit`
    included, is logged and dropped.
    """
    if not path:
        return False
    try:
        import operator_view
        operator_view.write_page(store, path, event=event, gid=gid)
        return True
    except KeyboardInterrupt:
        raise
    except BaseException as exc:                            # noqa: BLE001
        log(f'  worker: could not write the operator page {path} '
            f'({type(exc).__name__}: {exc}); the turn is unaffected')
        return False


def galaxy_name(spec: str) -> str:
    """The last segment of a store spec, which is what the page is titled."""
    return os.path.basename((spec or '').rstrip('\\/')) or spec


def run(store, save_dir=None, grace: float = GRACE, poll: float = POLL,
        once: bool = False, stop=None, guard=None, log=print,
        server=None, sleeper=time.sleep, page=None, gid=None,
        clock=time.time) -> int:
    """Close each turn as its deadline passes, for as long as the machine is up.

    Like `referee.loop`, but a failed resolution (`SystemExit` included, which
    the referee raises for conditions that clear on their own) is logged,
    backed off from and retried.

    `page` is the operator page's path, rewritten after each turn closes and
    after a failure (at most every `FAILURE_PAGE_EVERY` seconds after the
    first), never on an idle pass.
    """
    fails = 0
    said_closed = False
    turn = None
    failure_page_at = None
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

            # One state read per pass serves status, clock and turn.
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
            published = referee.resolve_turn(store, save_dir=save_dir, log=log)
            fails = 0
            failure_page_at = None
            heartbeat(store, fails, log=log)
            event = (f'after closing turn {turn}' if published != turn else
                     f'after holding turn {turn}: no civ holds a planet and '
                     f'nobody is waiting to join')
            write_operator_page(store, page, event, gid=gid, log=log)
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
            heartbeat(store, fails, log=log)
            now = clock()
            if (failure_page_at is None
                    or now - failure_page_at >= FAILURE_PAGE_EVERY):
                failure_page_at = now
                write_operator_page(
                    store, page, f'after failure {fails} on turn {turn} '
                    f'({type(exc).__name__}: {exc})', gid=gid, log=log)
            if once:
                return 1
            sleeper(wait)


def describe(store, spec: str, log=print) -> None:
    """Log the galaxy, turn, deadline and roster at startup."""
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
    ap.add_argument('--operator-page',
                    help='where the operator page is written, if not '
                         '<work-dir>\\' + OPERATOR_PAGE + '. An empty value '
                         'writes none. A worker on a second galaxy wants one '
                         'of its own')
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
        page = (os.path.join(a.work_dir, OPERATOR_PAGE)
                if a.operator_page is None else a.operator_page)
        gid = galaxy_name(a.store)
        if page:
            log(f'  worker: the operator page is {os.path.abspath(page)}')
        write_operator_page(store, page, 'at startup', gid=gid, log=log)

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
                if close_overdue(store, save_dir=save_dir, grace=a.grace,
                                 log=log):
                    write_operator_page(store, page,
                                        'after closing the overdue turn',
                                        gid=gid, log=log)
            except (Exception, SystemExit) as exc:          # noqa: BLE001
                write_operator_page(
                    store, page, f'after the overdue turn failed to close '
                    f'({type(exc).__name__}: {exc})', gid=gid, log=log)
                # The loop retries it.
                log(f'  worker: the overdue turn did not close '
                    f'({type(exc).__name__}: {exc}); the loop will try again')
                log(traceback.format_exc())

        return run(store, save_dir=save_dir, grace=a.grace, poll=a.poll,
                   once=a.once, guard=guard, log=log, server=server,
                   page=page, gid=gid)
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
