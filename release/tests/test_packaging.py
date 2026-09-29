"""
The multiplayer path has to survive being frozen.

Every check here is something that fails silently rather than loudly. A
misspelled `--hidden-import` still builds; PyInstaller notes it and carries on,
and the module is simply absent at runtime. A module that works out where it is
from `__file__` still imports; it just computes paths inside the temporary
directory the build unpacks into. A `sys.executable` subprocess still runs; in a
frozen build it starts a second launcher.

No game and no window. Nothing here starts a client, because the machine has one
and something else is usually using it.
"""
import ast
import os
import pathlib
import re
import sys

REPO = str(pathlib.Path(__file__).resolve().parents[2])
RELEASE = os.path.join(REPO, "release")
BUILD_PS1 = os.path.join(RELEASE, "build.ps1")

sys.path.insert(0, RELEASE)
import launcher as L                                            # noqa: E402

fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        fails.append(label)


def build_flags(flag):
    """Every value build.ps1 passes to a PyInstaller flag.

    Read out of the script rather than duplicated here. A list kept in both
    places would drift, and the copy that matters is the one the build runs.
    """
    text = open(BUILD_PS1, encoding="utf-8-sig").read()
    return re.findall(rf"'{flag}',\s*(?:'([^']+)'|\$(\w+))", text)


def ps_variable(name):
    """A path variable's definition in build.ps1, resolved against the repo."""
    text = open(BUILD_PS1, encoding="utf-8-sig").read()
    m = re.search(rf"^\s*\${name}\s*=\s*Join-Path \$(\w+) '([^']+)'", text, re.M)
    if not m:
        return None
    parent, leaf = m.group(1), m.group(2)
    base = REPO if parent == "RepoRoot" else ps_variable(parent)
    return None if base is None else os.path.join(base, leaf.replace("\\", os.sep))


# ── The analysis path ─────────────────────────────────────────────────────────
# Resolved the way build.ps1 resolves it, so a renamed directory fails here
# rather than at the first multiplayer turn of the next release.
paths = []
for literal, var in build_flags("--paths"):
    p = literal or ps_variable(var)
    check(f"--paths {literal or '$' + var} resolves", p is not None, True)
    if p:
        check(f"--paths {os.path.relpath(p, REPO)} exists", os.path.isdir(p), True)
        paths.append(p)

# ── Every hidden import is a module that actually exists ──────────────────────
# The whole point of naming them: they are imported inside functions, so nothing
# else in this repo would notice one going missing.
import importlib.util                                           # noqa: E402

probe = list(paths) + sys.path
for literal, var in build_flags("--hidden-import"):
    name = literal or var
    try:
        spec = importlib.util.find_spec(name) if name in sys.modules else None
    except (ImportError, ValueError):
        spec = None
    if spec is None:
        found = any(os.path.exists(os.path.join(d, name + ".py")) for d in probe)
    else:
        found = True
    check(f"--hidden-import {name} exists", found, True)

# ── Nothing in the player's path re-runs sys.executable ───────────────────────
# This is the bug the packaging work existed to fix, and it is invisible from a
# checkout: sys.executable is a real interpreter there and the subprocess works.
# referee.py is deliberately not in this list. It runs from a checkout and the
# release does not contain it.
PLAYER_PATH = [
    os.path.join(REPO, "server", "player_turn.py"),
    os.path.join(REPO, "client", "dev_tools", "game_cycle.py"),
]
for src in PLAYER_PATH:
    tree = ast.parse(open(src, encoding="utf-8").read())
    hits = [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and n.attr == "executable"
            and isinstance(n.value, ast.Name) and n.value.id == "sys"]
    check(f"{os.path.basename(src)} does not re-exec sys.executable", hits, [])

# ── The turn machinery can be pointed somewhere else ──────────────────────────
mods = L.multiplayer_modules()
check("multiplayer_modules() finds the machinery", mods is not None, True)

if mods:
    player_turn, _turn_store = mods
    import game_cycle as gc

    game_root, data_dir = os.path.join(REPO, "nowhere", "game"), os.path.join(
        REPO, "nowhere", "data")
    L.bind_multiplayer_paths(game_root, data_dir)

    check("client dir follows the game folder", gc.CLIENT_DIR, game_root)
    check("player build follows the game folder",
          os.path.dirname(gc.PLAYER_EXE), game_root)
    check("resolve_exe('player') stays inside the game folder",
          os.path.dirname(gc.resolve_exe("player")), game_root)
    check("saves follow the data directory", gc.SAVES,
          os.path.join(data_dir, "saves"))
    check("player_turn works below the data directory", player_turn.HERE, data_dir)
    check("game_cycle.trigger_save is callable",
          callable(getattr(gc, "trigger_save", None)), True)

    # The launcher refuses to start a turn without one of these, and build.ps1
    # is what puts one there. Two lists, one contract.
    shipped = set(re.findall(r"'(CosmicSupremacy_\w+\.exe)'",
                             open(BUILD_PS1, encoding="utf-8-sig").read()))
    check("build.ps1 ships a client the launcher will accept",
          shipped & set(L.MP_CLIENTS), lambda got: bool(got))

# ── The checkout writes no bytecode for trigger_save ──────────────────────────
# Defender quarantines trigger_save.cpython-312.pyc as Exploit:Python/Leivion.C,
# measured 13 times between 20 and 27 September 2026, and never the .py. The
# probe modules below are harmless, so this proves the mechanism without
# writing the file that gets quarantined.
import tempfile                                                 # noqa: E402

sys.path.insert(0, os.path.join(REPO, "client", "dev_tools"))
import game_cycle as gc_mod                                     # noqa: E402

if sys.dont_write_bytecode:
    print("  [SKIP] bytecode writing is already off in this interpreter")
else:
    probe_dir = tempfile.mkdtemp(prefix="nobytecode_")
    stamp = str(os.getpid())
    quiet, loud = f"probe_quiet_{stamp}", f"probe_loud_{stamp}"
    for mod in (quiet, loud):
        with open(os.path.join(probe_dir, mod + ".py"), "w") as fh:
            fh.write("VALUE = 1\n")
    sys.path.insert(0, probe_dir)
    cache = os.path.join(probe_dir, "__pycache__")

    def cached(mod):
        return os.path.isdir(cache) and any(
            f.startswith(mod + ".") for f in os.listdir(cache))

    got = gc_mod.import_without_bytecode(quiet)
    check("import_without_bytecode returns the module", got.VALUE, 1)
    check("and writes no .pyc for it", cached(quiet), False)
    check("and leaves bytecode writing as it found it",
          sys.dont_write_bytecode, False)
    __import__(loud)
    check("while a plain import here does write one", cached(loud), True)
    sys.path.remove(probe_dir)
    import shutil                                               # noqa: E402
    shutil.rmtree(probe_dir, ignore_errors=True)


def trigger_save_imports(tree):
    """Line numbers of every import statement that loads trigger_save."""
    lines = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import) and any(
                a.name == "trigger_save" for a in n.names):
            lines.append(n.lineno)
        elif isinstance(n, ast.ImportFrom) and n.module == "trigger_save":
            lines.append(n.lineno)
    return lines


def bytecode_off_line(tree):
    """First module-level `sys.dont_write_bytecode = True`, or None."""
    for n in tree.body:
        if (isinstance(n, ast.Assign) and len(n.targets) == 1
                and isinstance(n.targets[0], ast.Attribute)
                and n.targets[0].attr == "dont_write_bytecode"
                and isinstance(n.value, ast.Constant) and n.value.value is True):
            return n.lineno
    return None


# Every file in the checkout that imports trigger_save turns bytecode off
# first. game_cycle is the one the launcher reaches, and it goes through the
# helper instead of an import statement.
SKIP_DIRS = {".git", ".claude", "dist", "build", "__pycache__", "wayback",
             "node_modules"}
importers = []
for root, dirs, files in os.walk(REPO):
    dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".venv")]
    for f in files:
        if not f.endswith(".py") or f == "trigger_save.py":
            continue
        src = os.path.join(root, f)
        try:
            tree = ast.parse(open(src, encoding="utf-8").read())
        except (SyntaxError, UnicodeDecodeError):
            continue
        hits = trigger_save_imports(tree)
        if hits:
            importers.append(f)
            off = bytecode_off_line(tree)
            check(f"{f} turns bytecode off before importing trigger_save",
                  off is not None and off < min(hits), True)
check("the dev tools that import trigger_save were found",
      sorted(importers), lambda got: "trigger_load.py" in got)

gc_tree = ast.parse(open(gc_mod.__file__, encoding="utf-8").read())
check("game_cycle has no import statement for trigger_save",
      trigger_save_imports(gc_tree), [])
ts_fn = next(n for n in gc_tree.body
             if isinstance(n, ast.FunctionDef) and n.name == "trigger_save")
helper_calls = [c for c in ast.walk(ts_fn)
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                and c.func.id == "import_without_bytecode"
                and c.args and isinstance(c.args[0], ast.Constant)
                and c.args[0].value == "trigger_save"]
check("game_cycle.trigger_save imports it through import_without_bytecode",
      len(helper_calls), 1)

print()
if fails:
    print("FAILED: " + ", ".join(fails))
    sys.exit(1)
print("packaging checks passed")
