"""
test_no_console_windows.py , the dev tools stop flashing cmd windows
=====================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_no_console_windows.py

"We get a lot of cmd windows opening when we launch sandbox." Every one is a
console child started from a windowed parent without `CREATE_NO_WINDOW`, and
the worst of them is `game_cycle.client_pids`, which shells out to PowerShell
once per poll of the turn loop.

**A test that asserts a flag is set somewhere proves nothing.** The calls that
matter are the ones a turn actually makes, so each of those is invoked here
with `subprocess.run` replaced by a recorder, and the flag is read off the call
that came out. The recorder also hands back the output the real command would,
so a call whose result is parsed is shown to still parse: a flag that quietly
broke `client_pids` would close a player's client at the next poll, having
decided nothing was running.

The sweep at the end is the regression half. It reads the source of every tool
this touches and fails if any subprocess call in them has no `creationflags`
at all, so the next one added is not silently a window.

**Nothing here launches or closes a client.** `subprocess.run` is replaced
before any of it, so the PowerShell that would have killed a process is a
recorded argument list and nothing else, and `CLIENT_LOCK` is repointed at a
temporary path so the lock another session may be holding is neither read nor
removed.

What this cannot do is watch the screen. Whether a window still appears when
the sandbox launches is a thing a person has to look at.
"""
import ast
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'client', 'dev_tools'),
          os.path.join(ROOT, 'server', 'dev_tools'),
          os.path.join(ROOT, 'server')):
    if d not in sys.path:
        sys.path.insert(0, d)

import check_store
import game_cycle
import make_single_player_galaxy as msp

PASS, FAIL = [], []

CREATE_NO_WINDOW = 0x08000000

# The tools swept for a missing flag. `client/dev_tools` is where the operator
# saw the windows; `check_store.py` is the one tool outside it that shells out
# on a path a player can reach.
SWEPT = ([os.path.join(ROOT, 'client', 'dev_tools', n)
          for n in sorted(os.listdir(os.path.join(ROOT, 'client', 'dev_tools')))
          if n.endswith('.py')] +
         [os.path.join(ROOT, 'server', 'dev_tools', 'check_store.py')])

SPAWNERS = {'run', 'Popen', 'call', 'check_call', 'check_output'}


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


class Recorder:
    """Stands in for `subprocess.run` and remembers how it was called.

    `answers` is keyed by a word in the command line, so one recorder serves
    calls whose results are parsed differently. Anything not named gets empty
    output, which is what a call whose result is ignored would see.
    """

    def __init__(self, answers=None):
        self.calls = []
        self.answers = answers or {}

    def __call__(self, args, **kw):
        self.calls.append((list(args), kw))
        text = ' '.join(str(a) for a in args)
        out = ''
        for word, answer in self.answers.items():
            if word in text:
                out = answer
                break
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr='')

    def flagged(self):
        """Every recorded call's creationflags, or None where there were none.
        """
        return [kw.get('creationflags') for _args, kw in self.calls]

    def hidden(self):
        """Whether every recorded call asked for no window."""
        flags = self.flagged()
        return bool(flags) and all(f == CREATE_NO_WINDOW for f in flags)


def with_recorder(module, answers, body):
    """Run `body` with `module.subprocess.run` recorded, and give the record
    back. Restored whatever happens, because these modules are imported once
    and a leaked stub would make the next case pass for the wrong reason."""
    rec = Recorder(answers)
    kept = module.subprocess.run
    module.subprocess.run = rec
    try:
        body()
    finally:
        module.subprocess.run = kept
    return rec


# ── the calls a turn makes ───────────────────────────────────────────────────
def test_client_pids():
    """The one that runs per poll, on both tools that have a copy of it."""
    print('client_pids, the call a turn makes many times')
    for label, module in (('game_cycle', game_cycle),
                          ('make_single_player_galaxy', msp)):
        got = []
        rec = with_recorder(module, {'Get-Process': '4120\n 8864 \n'},
                            lambda: got.append(module.client_pids()))
        check(f'{label}: client_pids asks for one PowerShell',
              len(rec.calls), 1)
        check(f'{label}: and asks for it without a window',
              rec.flagged(), [CREATE_NO_WINDOW])
        check(f'{label}: and the pids still parse out of the output',
              got[0], [4120, 8864])

    # The parse is the thing a wrong flag would break silently, so it is
    # checked against the shapes the real command produces rather than one.
    for out, want in (('', []), ('\r\n', []), ('4120\r\n', [4120]),
                      ('4120\r\n8864\r\n', [4120, 8864])):
        got = []
        with_recorder(game_cycle, {'Get-Process': out},
                      lambda: got.append(game_cycle.client_pids()))
        check(f'client_pids parses {out!r} as {want}', got[0], want)


def test_pid_alive():
    """The lock's liveness check, which runs whenever a tool takes the client.
    """
    print('_pid_alive, the tasklist behind the lock')
    got = []
    rec = with_recorder(
        game_cycle, {'tasklist': 'CosmicSupremacy.exe  4120 Console  1  90 K\n'},
        lambda: got.append(game_cycle._pid_alive(4120)))
    check('_pid_alive runs tasklist without a window',
          rec.flagged(), [CREATE_NO_WINDOW])
    check('and still reads the pid out of the listing', got[0], True)
    with_recorder(game_cycle, {'tasklist': 'INFO: No tasks are running.\n'},
                  lambda: got.append(game_cycle._pid_alive(4120)))
    check('and still says no when the listing does not hold it', got[1], False)
    check('the timeout is kept, so a hung tasklist is still not forever',
          rec.calls[0][1].get('timeout'), 15)


def test_close_client():
    """The Stop-Process call, once per turn.

    `CLIENT_LOCK` is repointed first. `close_client` reads the lock and, when
    the holder is this process, removes it; another session may be holding the
    real one and a test is not allowed to touch that.
    """
    print('close_client, the Stop-Process call')
    for label, module in (('game_cycle', game_cycle),
                          ('make_single_player_galaxy', msp)):
        kept_lock = getattr(module, 'CLIENT_LOCK', None)
        kept_pids = module.client_pids
        if kept_lock is not None:
            module.CLIENT_LOCK = os.path.join(
                tempfile.mkdtemp(prefix='nowin_'), 'not_the_real_lock')
        answers = iter([[4120], []])
        module.client_pids = lambda: next(answers, [])
        try:
            rec = with_recorder(module, {}, lambda: module.close_client())
        finally:
            module.client_pids = kept_pids
            if kept_lock is not None:
                module.CLIENT_LOCK = kept_lock
        check(f'{label}: close_client stops the pid it found',
              any('Stop-Process -Id 4120' in ' '.join(a)
                  for a, _kw in rec.calls), True)
        check(f'{label}: and does it without a window', rec.hidden(), True)
        check(f'{label}: the real lock file was never touched',
              kept_lock is None or module.CLIENT_LOCK == kept_lock, True)


def test_capture_save():
    """The trigger_save child, once per capture, so several times a turn."""
    print('capture_save, the trigger_save child')
    rec = Recorder({'trigger_save': 'SaveGame saved spseed\n'})
    kept_run, kept_newest = msp.subprocess.run, msp.newest_capture
    msp.subprocess.run = rec
    msp.newest_capture = lambda _since: os.path.join('saves', 'spseed.b64')
    try:
        where = msp.capture_save('spseed')
    finally:
        msp.subprocess.run, msp.newest_capture = kept_run, kept_newest
    check('capture_save runs trigger_save without a window',
          rec.flagged(), [CREATE_NO_WINDOW])
    check('and still reads success out of its stdout',
          where.endswith('spseed.b64'), True)
    check('and still runs it in the tools directory',
          rec.calls[0][1].get('cwd'), msp.HERE)


def test_check_store_sessions():
    """`net use`, on the tool an operator runs when a share will not open."""
    print('check_store.sessions, the net use call')
    listing = ('Status       Local     Remote\n'
               'OK           Z:        \\\\POWERHOUSE1\\galaxies\n')
    rec = Recorder({'net': listing})
    kept = check_store.subprocess.run
    check_store.subprocess.run = rec
    try:
        rows = check_store.sessions()
    finally:
        check_store.subprocess.run = kept
    check('sessions runs net use without a window',
          rec.flagged(), [CREATE_NO_WINDOW])
    check('and still finds the UNC row in the output', len(rows), 1)
    check('and still returns it whole',
          rows[0].endswith('\\\\POWERHOUSE1\\galaxies'), True)


# ── the two that are deliberately left alone ─────────────────────────────────
def test_the_client_launches_are_left_detached():
    """`launch` starts the game, which is a windowed program with no console.

    `DETACHED_PROCESS` and `CREATE_NO_WINDOW` are both console-creation flags
    and cannot be combined, and the client has no console to hide, so this is
    the right flag and the one already there. Pinned so that a later sweep for
    the other one does not change it.
    """
    print('the client launches keep DETACHED_PROCESS')
    for label, path in (('game_cycle',
                         os.path.join(ROOT, 'client', 'dev_tools',
                                      'game_cycle.py')),
                        ('make_single_player_galaxy',
                         os.path.join(ROOT, 'client', 'dev_tools',
                                      'make_single_player_galaxy.py'))):
        src = read(path)
        check(f'{label}: the client Popen still asks for DETACHED_PROCESS',
              'DETACHED_PROCESS' in src, True)
        check(f'{label}: and does not ask for both at once',
              'DETACHED_PROCESS | ' in src or 'CREATE_NO_WINDOW |' in src,
              False)


# ── the sweep ────────────────────────────────────────────────────────────────
def read(path) -> str:
    with open(path, encoding='utf-8') as f:
        return f.read()


def spawns(path):
    """Every `subprocess.<spawner>(...)` in a file, as (line, has_flags)."""
    out = []
    for node in ast.walk(ast.parse(read(path))):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(
            fn, 'id', None)
        if name not in SPAWNERS:
            continue
        if isinstance(fn, ast.Attribute) and getattr(
                fn.value, 'id', None) != 'subprocess':
            continue
        if not isinstance(fn, ast.Attribute):
            continue
        flagged = any(k.arg == 'creationflags' for k in node.keywords)
        out.append((node.lineno, flagged))
    return out


def test_sweep():
    """No subprocess call in these tools may be written without the flag.

    The regression half. Naming the file and the line makes a failure
    actionable, and a file with no subprocess call in it contributes nothing
    rather than passing vacuously.
    """
    print('the sweep: every spawner in these tools names creationflags')
    bare, total = [], 0
    for path in SWEPT:
        for line, flagged in spawns(path):
            total += 1
            if not flagged:
                bare.append(f'{os.path.relpath(path, ROOT)}:{line}')
    check('at least the calls this file exercises were found', total >= 7, True)
    check('and none of them is written without creationflags', bare, [])
    print(f'         {total} subprocess call(s) across {len(SWEPT)} file(s)')


if __name__ == '__main__':
    test_client_pids()
    test_pid_alive()
    test_close_client()
    test_capture_save()
    test_check_store_sessions()
    test_the_client_launches_are_left_detached()
    test_sweep()
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    if FAIL:
        for name in FAIL:
            print(f'  failed: {name}')
    sys.exit(1 if FAIL else 0)
