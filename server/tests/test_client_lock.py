"""
test_client_lock.py: a client left open is not a free machine
===============================================================
    python server\\tests\\test_client_lock.py

`launch` notes the pid of the client it starts on the lock. A lock whose
holder has exited is stale only when that client is gone too; while it runs,
the client was left open and somebody may be using it. Only the purpose that
left it may take it over, and `close_client` refuses it without `force`.

Nothing here starts or stops a client: `CLIENT_LOCK` is repointed at a
temporary path, and the process checks and `subprocess.run` are replaced.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'client', 'dev_tools'),):
    if d not in sys.path:
        sys.path.insert(0, d)

import game_cycle as gc                                          # noqa: E402

PASS, FAIL = [], []
DEAD, LIVE, CLIENT = 999001, 999002, 999003


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


class World:
    """Which pids are alive and which clients run, and what was stopped."""

    def __init__(self, alive=(), clients=()):
        self.alive, self.clients, self.stopped = set(alive), list(clients), []

    def install(self):
        gc._pid_alive = lambda pid: pid in self.alive or pid == os.getpid()
        gc.client_pids = lambda: list(self.clients)

        def run(cmd, **kw):
            text = ' '.join(cmd) if isinstance(cmd, list) else str(cmd)
            if 'Stop-Process' in text:
                pid = int(text.split('-Id')[1].split()[0])
                self.stopped.append(pid)
                self.clients = [c for c in self.clients if c != pid]

            class R:
                stdout, returncode = '', 0
            return R()
        gc.subprocess.run = run


def write_lock(text):
    with open(gc.CLIENT_LOCK, 'w', encoding='utf-8') as f:
        f.write(text)


def refused(fn):
    try:
        fn()
    except SystemExit as e:
        return str(e)
    return None


def main():
    kept = (gc.CLIENT_LOCK, gc._pid_alive, gc.client_pids, gc.subprocess.run)
    tmp = tempfile.mkdtemp(prefix='client_lock_')
    gc.CLIENT_LOCK = os.path.join(tmp, 'cosmic_client.lock')
    gc.log = lambda msg: None
    try:
        print('the lock notes the client it started')
        World().install()
        gc.take_client_lock('a test')
        gc.record_client(CLIENT)
        check('the state names the client', gc.lock_state(),
              {'pid': os.getpid(), 'purpose': 'a test', 'client': CLIENT})
        check('lock_holder still reads pid and purpose', gc.lock_holder(),
              (os.getpid(), 'a test'))
        gc.release_client_lock()
        check('releasing removes it', os.path.exists(gc.CLIENT_LOCK), False)

        print('\na holder that exited with no client is stale')
        World().install()
        write_lock(f'{DEAD} inspection')
        check('taken', gc.take_client_lock('a test'), True)
        check('and now ours', gc.lock_holder(), (os.getpid(), 'a test'))
        gc.release_client_lock()

        print('\na holder that exited and whose client is gone is stale')
        World(clients=[]).install()
        write_lock(f'{DEAD} inspection\nclient {CLIENT}')
        check('taken', gc.take_client_lock('a test'), True)
        gc.release_client_lock()

        print('\na holder that exited and left its client up has left it open')
        world = World(clients=[CLIENT])
        world.install()
        write_lock(f'{DEAD} inspection\nclient {CLIENT}')
        msg = refused(lambda: gc.take_client_lock('a test'))
        check('another purpose is refused', msg is not None, True)
        check('naming the holder, its purpose and the client',
              all(s in (msg or '') for s in (str(DEAD), 'inspection',
                                             str(CLIENT), '--close')), True)
        check('the lock is untouched', gc.lock_state()['pid'], DEAD)
        msg = refused(lambda: gc.close_client())
        check('close_client refuses it', msg is not None, True)
        check('and stops nothing', world.stopped, [])
        check('the purpose that left it takes it over',
              gc.take_client_lock('inspection'), True)
        check('and holds it', gc.lock_holder(), (os.getpid(), 'inspection'))
        gc.release_client_lock()

        print('\nan unrecorded client keeps the old behaviour')
        World(clients=[CLIENT]).install()
        write_lock(f'{DEAD} inspection')
        check('a lock with no client line is stale',
              gc.take_client_lock('a test'), True)
        gc.release_client_lock()

        print('\n--close frees a client left open')
        world = World(clients=[CLIENT])
        world.install()
        write_lock(f'{DEAD} inspection\nclient {CLIENT}')
        check('close_client(force=True) stops it', gc.close_client(force=True),
              True)
        check('the client is stopped', world.stopped, [CLIENT])
        check('and the lock is gone', os.path.exists(gc.CLIENT_LOCK), False)

        print('\na live holder is still refused')
        world = World(alive=[LIVE], clients=[CLIENT])
        world.install()
        write_lock(f'{LIVE} serving GoodGuy\nclient {CLIENT}')
        check('take refuses', refused(lambda: gc.take_client_lock('a test'))
              is not None, True)
        check('close refuses', refused(lambda: gc.close_client()) is not None,
              True)
        check('and stops nothing', world.stopped, [])
    finally:
        (gc.CLIENT_LOCK, gc._pid_alive, gc.client_pids,
         gc.subprocess.run) = kept

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
