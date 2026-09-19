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

print()
if fails:
    print("FAILED: " + ", ".join(fails))
    sys.exit(1)
print("packaging checks passed")
