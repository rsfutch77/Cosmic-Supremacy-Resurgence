"""
The web window's controller loop and what it sends the page, without a window.

web_view.WebView runs every Launcher call on one thread, as Tk does, and a
prompt waits by running that loop, so timers keep firing under a dialog as they
do under a Tk modal. A stand-in for the pywebview window records the script the
page would be sent, and answers come in from another thread as the page's
js_api calls do. pywebview itself is not imported.
"""
import json
import os
import re
import sys
import threading
import time

import pathlib
REPO = str(pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, os.path.join(REPO, "release"))

import launcher as L
sys.modules.setdefault("launcher", L)
import web_view as W

fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        fails.append(label)


class Window:
    """Records what the page is sent; destroy() is the window closing."""

    def __init__(self, view):
        self.view = view
        self.sent = []
        self.destroyed = False

    def run_js(self, script):
        m = re.fullmatch(r"window\.ui\.(\w+)\((.*)\)", script, re.S)
        self.sent.append((m.group(1), json.loads(m.group(2))))

    def destroy(self):
        self.destroyed = True
        self.view._on_closed()

    def prompts(self):
        return [arg for fn, arg in self.sent if fn == "prompt"]


def fresh():
    view = W.WebView({"product": "CS"})
    view.window = Window(view)
    return view


def run_loop(view, seconds=5.0):
    """Run the controller loop on its own thread, as webview.start does."""
    t = threading.Thread(target=view._pump, daemon=True)
    t.start()
    return t


print("1. timers and posted calls run on the controller thread, in order")
view = fresh()
seen = []
view.after(200, lambda: seen.append(("timer", threading.current_thread().name)))
view.after(50, lambda: seen.append(("early", threading.current_thread().name)))
t = run_loop(view)
threading.Thread(target=lambda: view.post(lambda: seen.append(
    ("posted", threading.current_thread().name))), name="js_api").start()
time.sleep(0.5)
check("the early timer, the post and the late timer all ran",
      sorted(k for k, _ in seen), ["early", "posted", "timer"])
check("the early timer before the late one",
      [k for k, _ in seen].index("early") < [k for k, _ in seen].index("timer"), True)
check("every one on the loop's thread, none on the poster's",
      {n for _, n in seen}, lambda names: "js_api" not in names)
view.close()
t.join(2)
check("closing ends the loop", t.is_alive(), False)

print("\n2. a prompt waits for its answer and the loop keeps running under it")
view = fresh()
ticks = []


def tick():
    ticks.append(time.monotonic())
    view.after(50, tick)


answers = []


def ask():
    answers.append(view.ask_yes_no("Change your name now?"))


view.after(0, tick)
view.after(10, ask)
t = run_loop(view)
time.sleep(0.6)
check("the question reached the page", [p["text"] for p in view.window.prompts()],
      ["Change your name now?"])
check("no answer yet", answers, [])
during = len(ticks)
check("the timer kept firing while the prompt was open", during, lambda n: n >= 5)
pid = view.window.prompts()[0]["id"]
threading.Thread(target=lambda: view.resolve(pid, True, None), name="js_api").start()
time.sleep(0.3)
check("Yes comes back as True", answers, [True])
check("and the timer is still going after it", len(ticks), lambda n: n > during)
view.close()
t.join(2)

print("\n3. a window closed under an open prompt answers its cancel value")
view = fresh()
answers = []
view.after(0, lambda: answers.append(view.ask_ok_cancel("Quit launcher?", "Close anyway?")))
t = run_loop(view)
time.sleep(0.3)
view.window.destroy()
t.join(2)
check("the prompt returned", answers, [False])
check("and the loop ended", t.is_alive(), False)

print("\n4. the name prompt asks again with the reason until a name is taken")
view = fresh()
got = []
view.after(0, lambda: got.append(view.ask_name("Who are you?", "", L.NAME_RULES,
                                               L.validate_player_name)))
t = run_loop(view)
time.sleep(0.3)
first = view.window.prompts()[0]
check("no reason shown the first time", first["error"], "")
view.resolve(first["id"], "save", "Ali/ce")
time.sleep(0.3)
second = view.window.prompts()[1]
check("the refusal comes back in the same dialog",
      second["error"], "We can't accept / in a name.")
check("with what was typed still in the field", second["input"]["value"], "Ali/ce")
view.resolve(second["id"], "save", "Alice")
time.sleep(0.3)
check("a good name is returned", got, ["Alice"])
view.close()
t.join(2)

print("\n5. a once-a-second repaint that changes nothing sends nothing")
view = fresh()
for _ in range(3):
    view.set_turn_label("turn 4 · 3:10:00 left")
    view.show_capture("saved 1m ago", L.OK)
    view.set_hint("")
check("one turn label sent", [a for f, a in view.window.sent if "turnLabel" in a],
      [{"turnLabel": "turn 4 · 3:10:00 left"}])
check("one capture sent", len([a for f, a in view.window.sent if "capture" in a]), 1)
view.set_control("save", text="Send Turn")
view.set_control("save", text="Send Turn")
view.set_control("save", enabled=False)
check("a control is sent on each change and only then",
      [a["control"] for f, a in view.window.sent if "control" in a],
      [{"text": "Send Turn", "key": "save"},
       {"text": "Send Turn", "enabled": False, "key": "save"}])

print("\n6. a row's button runs that row's action, and a clock cell is rewritten in place")
view = fresh()
pressed = []
view.draw_galaxies([L.GalaxyRow([("Andromeda", L.TEXT), ("open", L.TEXT)], "Play",
                                 lambda: pressed.append("andromeda")),
                    L.GalaxyRow([("Draco", L.DIM), ("forming", L.DIM)])])
view.row_pressed(0)
view.row_pressed(1)
view.row_pressed(7)
check("only the row with a button acts", pressed, ["andromeda"])
view.set_cell(0, 1, "open", L.TEXT)
check("an unchanged cell sends nothing",
      [a for f, a in view.window.sent if "cell" in a], [])
view.set_cell(0, 1, "stopped", L.WARN)
check("a changed cell is sent with the colour for the slate",
      [a["cell"] for f, a in view.window.sent if "cell" in a],
      [{"row": 0, "column": 1, "text": "stopped", "colour": W.ON_DARK[L.WARN]}])

print("\n" + ("ALL PASSED" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
