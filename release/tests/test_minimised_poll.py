"""
The Galaxies page re-lists on a timer only while it can be seen.

    python release/tests/test_minimised_poll.py

`Launcher._games_tick` runs every GAMES_POLL_MS and skips the listing while the
window is minimised; `Launcher.on_restored` lists at once when it comes back.

Checks:
  1. the tick lists on the Galaxies page with the window up, and not off it
  2. the tick does not list while the window is minimised, and keeps its timer
  3. restoring the window lists at once on the Galaxies page, and not off it
  4. WebView asks Windows by handle, reads False before the window is shown,
     and forwards pywebview's restored event to the Launcher

No game, no network.
"""
import inspect
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import launcher as L                                            # noqa: E402
import web_view                                                 # noqa: E402
from fake_view import FakeView                                  # noqa: E402

fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        fails.append(label)


def make_app(page="galaxies", minimized=False):
    app = types.SimpleNamespace()
    app.view = FakeView()
    app.view.minimized = minimized
    app.page = page
    app.listed = 0

    def refresh_games():
        app.listed += 1
    app.refresh_games = refresh_games
    for name in ("_games_tick", "on_restored"):
        setattr(app, name, getattr(L.Launcher, name).__get__(app))
    return app


print("\n1. the tick lists while the page can be seen")
app = make_app()
app._games_tick()
check("on the Galaxies page with the window up, one listing", app.listed, 1)
check("and the next tick is set", [ms for ms, _fn in app.view.timers],
      [L.GAMES_POLL_MS])
app = make_app(page="home")
app._games_tick()
check("off the Galaxies page, no listing", app.listed, 0)

print("\n2. the tick does not list while the window is minimised")
app = make_app(minimized=True)
app._games_tick()
app._games_tick()
check("minimised on the Galaxies page, no listing over two ticks",
      app.listed, 0)
check("and the timer is kept, so listing resumes once it is restored",
      [ms for ms, _fn in app.view.timers], [L.GAMES_POLL_MS] * 2)

print("\n3. restoring the window lists at once")
app = make_app(minimized=True)
app._games_tick()
app.view.minimized = False
app.on_restored()
check("restored on the Galaxies page, one listing straight away",
      app.listed, 1)
app = make_app(page="home")
app.on_restored()
check("restored on another page, no listing", app.listed, 0)

print("\n4. the window's own answer")
view = web_view.WebView({"product": "test"})
check("before the window is shown it reads as not minimised",
      view.is_minimized(), False)
src = inspect.getsource(web_view.WebView.is_minimized)
check("it asks Windows by the handle taken when the window was shown",
      "IsIconic(self._hwnd)" in src, True)
check("and the handle is taken when the window is shown",
      "self._hwnd = " in inspect.getsource(web_view.WebView._on_shown), True)
check("pywebview's restored event is subscribed",
      "events.restored += self._on_restored"
      in inspect.getsource(web_view.WebView.run), True)
posted = []
view.post = posted.append
view._on_restored()
check("with no Launcher yet, a restore posts nothing", posted, [])
view.app = types.SimpleNamespace(on_restored=lambda: "relisted")
view._on_restored()
check("with a Launcher, a restore posts its on_restored",
      [fn() for fn in posted], ["relisted"])

print()
if fails:
    print(f"{len(fails)} FAILED:")
    for f in fails:
        print(f"  {f}")
    sys.exit(1)
print("ALL PASSED")
