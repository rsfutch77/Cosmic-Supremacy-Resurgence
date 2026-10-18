"""
Collects server/tests and release/tests as pytest items for the VS Code Test
Explorer.

Each test_*.py is one item that runs the script with the current interpreter
and passes on exit code 0, so the scripts and run_all.ps1 work unchanged. A
failure shows the script's FAIL lines, then its whole output.

    server\\.venv\\Scripts\\python.exe -m pytest
    server\\.venv\\Scripts\\python.exe -m pytest --run-game

The scripts that start the real game are skipped without --run-game, since
the client is shared with whoever else is using this machine. Release scripts
are skipped while the packaged launcher is running, because it owns port 8888
and the scripts would talk to its server instead of their own.
"""
import os
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent
SCRIPT_DIRS = {ROOT / 'server' / 'tests', ROOT / 'release' / 'tests'}
TIMEOUT = 900

# Scripts that start and kill the game, and the arguments each run takes.
GAME_RUNS = {
    'test_status_cycle.py': ['tutorial', 'demo', 'testbed'],
    'test_external_status.py': [None],
}

FAIL_LINE = re.compile(r'\bFAIL', re.IGNORECASE)


def pytest_addoption(parser):
    parser.addoption('--run-game', action='store_true',
                     help='also run the scripts that start the real game')


def pytest_collect_file(file_path, parent):
    if (file_path.suffix == '.py' and file_path.name.startswith('test_')
            and file_path.parent in SCRIPT_DIRS):
        return ScriptFile.from_parent(parent, path=file_path)


def launcher_running():
    out = subprocess.run(
        ['tasklist', '/FI', 'IMAGENAME eq CosmicSupremacyLauncher.exe', '/NH'],
        capture_output=True, text=True).stdout
    return 'CosmicSupremacyLauncher' in out


class ScriptFile(pytest.File):
    def collect(self):
        for arg in GAME_RUNS.get(self.path.name, [None]):
            yield ScriptItem.from_parent(self, name=arg or self.path.stem, arg=arg)


class ScriptItem(pytest.Item):
    def __init__(self, *, arg, **kw):
        super().__init__(**kw)
        self.arg = arg
        self.game = self.path.name in GAME_RUNS
        if self.game:
            self.add_marker(pytest.mark.game)

    def setup(self):
        if self.game and not self.config.getoption('run_game'):
            pytest.skip('starts the real game; pass --run-game')
        if self.path.parent.parent.name == 'release' and launcher_running():
            pytest.skip('close the launcher first; it owns port 8888')

    def runtest(self):
        cmd = [sys.executable, str(self.path)] + ([self.arg] if self.arg else [])
        env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
        try:
            proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True,
                                  timeout=TIMEOUT)
        except subprocess.TimeoutExpired as e:
            self.output = (e.stdout or b'').decode('utf-8', 'replace')
            raise ScriptFailed(f'timed out after {TIMEOUT} s')
        finally:
            if self.game:
                subprocess.run(['taskkill', '/F', '/IM', 'CosmicSupremacy*'],
                               capture_output=True)
        out = proc.stdout.decode('utf-8', 'replace')
        err = proc.stderr.decode('utf-8', 'replace')
        self.output = out + ('\n' + err if err.strip() else '')
        self.add_report_section('call', 'output', self.output)
        if proc.returncode != 0:
            raise ScriptFailed(f'exit code {proc.returncode}')

    def repr_failure(self, excinfo):
        if not isinstance(excinfo.value, ScriptFailed):
            return super().repr_failure(excinfo)
        output = getattr(self, 'output', '')
        fails = [l for l in output.splitlines() if FAIL_LINE.search(l)]
        parts = [str(excinfo.value)]
        if fails:
            parts += ['', *fails]
        parts += ['', '--- output ---', output.rstrip()]
        return '\n'.join(parts)

    def reportinfo(self):
        return self.path, 0, self.name


class ScriptFailed(Exception):
    pass


def pytest_configure(config):
    config.addinivalue_line('markers', 'game: starts the real game client')
