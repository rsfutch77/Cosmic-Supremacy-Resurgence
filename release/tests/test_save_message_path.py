"""
The message save path does no process injection, and the switch stays off.

M3 weighs a save that posts a window message against the remote thread
`trigger_save.py` uses. The message path exists so the launcher can stop calling
`OpenProcess` for write, `VirtualAllocEx`, `WriteProcessMemory` and
`CreateRemoteThread`, which is what an antivirus product objects to. A path that
quietly kept one of those calls would defeat its own reason to exist, so this
checks the module's source for them, checks that `game_cycle` chooses it only
when the operator asks, and checks that the default is still the injection path
a packaged build has taken a real turn with.

No game and no window. Nothing here starts a client; the checks read source and
drive the switch with the environment.
"""
import ast
import os
import pathlib
import sys

REPO = str(pathlib.Path(__file__).resolve().parents[2])
DEV = os.path.join(REPO, "client", "dev_tools")
MESSAGE_TOOL = os.path.join(DEV, "trigger_save_message.py")

fails = []


def check(label, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        fails.append(label)


# ── The message tool calls none of the injection primitives ───────────────────
# Read as text rather than imported, because importing it loads ctypes and the
# Win32 libraries, and the claim under test is about what the file says, not
# about a live process.
source = open(MESSAGE_TOOL, encoding="utf-8").read()
INJECTION_CALLS = [
    "VirtualAllocEx", "WriteProcessMemory", "CreateRemoteThread",
    "VirtualFreeEx", "WaitForSingleObject",
]
tree = ast.parse(source)
called = {
    node.func.attr
    for node in ast.walk(tree)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
}
for name in INJECTION_CALLS:
    check(f"message tool never calls {name}", name not in called)

# It must also not import trigger_save, which is the module that carries them
# and the one whose bytecode Defender quarantines.
imported = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        imported.update(a.name for a in node.names)
    elif isinstance(node, ast.ImportFrom):
        imported.add(node.module or "")
check("message tool does not import trigger_save", "trigger_save" not in imported)

# The one cross-process call it does make is a posted window message.
check("message tool posts a window message", "PostMessageW" in source)

# ── The switch, read through game_cycle ───────────────────────────────────────
sys.path.insert(0, DEV)
sys.path.insert(0, os.path.join(DEV, "ai_player"))
import game_cycle as gc                                          # noqa: E402


def with_env(value):
    saved = os.environ.get("CS_SAVE_VIA_MESSAGE")
    if value is None:
        os.environ.pop("CS_SAVE_VIA_MESSAGE", None)
    else:
        os.environ["CS_SAVE_VIA_MESSAGE"] = value
    try:
        return gc.save_via_message()
    finally:
        if saved is None:
            os.environ.pop("CS_SAVE_VIA_MESSAGE", None)
        else:
            os.environ["CS_SAVE_VIA_MESSAGE"] = saved


check("default is the injection path (switch unset)", with_env(None), False)
check("empty value is the injection path", with_env(""), False)
check("the switch turns the message path on", with_env("1"), True)
check("a word also turns it on", with_env("true"), True)
check("an unrelated value stays off", with_env("maybe"), False)

print()
if fails:
    print("FAILED: " + ", ".join(fails))
    sys.exit(1)
print("save message path checks passed")
