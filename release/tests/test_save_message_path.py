"""
The message save path does no process injection, and is taken when the client
carries the handler.

The message path saves without `OpenProcess` for write, `VirtualAllocEx`,
`WriteProcessMemory` or `CreateRemoteThread`. This checks the module's source
for those calls, and that `game_cycle` takes it when the running client
carries the handler `patch_save_message.py` adds, with `CS_SAVE_VIA_MESSAGE`
forcing either path.

No game and no window: source reads, environment switches, and the handler
check replaced.
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


# The message tool calls none of the injection primitives. Read as text, since
# the claim is about what the file says.
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

# It must also not import trigger_save, which carries them.
imported = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        imported.update(a.name for a in node.names)
    elif isinstance(node, ast.ImportFrom):
        imported.add(node.module or "")
check("message tool does not import trigger_save", "trigger_save" not in imported)

# The one cross-process call it does make is a sent window message.
check("message tool sends a window message", "SendMessageTimeoutW" in source)

# The switch, read through game_cycle
sys.path.insert(0, DEV)
sys.path.insert(0, os.path.join(DEV, "ai_player"))
import game_cycle as gc                                          # noqa: E402


import trigger_save_message as tsm                               # noqa: E402

HAS_HANDLER = [False]
tsm.client_has_handler = lambda: HAS_HANDLER[0]


def with_env(value, handler=False):
    HAS_HANDLER[0] = handler
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


check("unset, a client without the handler is injected", with_env(None), False)
check("unset, a client with the handler is sent the message",
      with_env(None, handler=True), True)
check("empty value follows the client", with_env("", handler=True), True)
check("the switch forces the message path", with_env("1"), True)
check("a word also forces it", with_env("true"), True)
check("0 forces injection", with_env("0", handler=True), False)
check("off forces injection", with_env("off", handler=True), False)
check("an unrelated value follows the client", with_env("maybe"), False)

print()
if fails:
    print("FAILED: " + ", ".join(fails))
    sys.exit(1)
print("save message path checks passed")
