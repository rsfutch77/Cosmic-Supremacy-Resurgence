"""
The launcher's account dialogs: create an account, log in, log out, and what
the player is told when a guest's galaxies stay behind.

Headless and offline. Launcher methods run on a stand-in with a `FakeView`, and
fb_auth talks to the `FakeToolkit`. fb_auth itself is covered in
test_fb_auth.py.
"""
import os
import shutil
import sys
import tempfile
import threading
import types

import pathlib
REPO = str(pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, os.path.join(REPO, "release"))
sys.path.insert(0, os.path.join(REPO, "server"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fb_auth                                                  # noqa: E402
import launcher as L                                            # noqa: E402
from fake_toolkit import Clock, FakeToolkit                     # noqa: E402
from fake_view import FakeView                                  # noqa: E402

fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        fails.append(label)


dirs = []


def fresh_dir():
    d = tempfile.mkdtemp(prefix="account_")
    dirs.append(d)
    return d


net, clock = FakeToolkit(), Clock()
PASSWORD = "Correct horse 1"
L.PLAYER_CODE_LENGTH = 4        # the stand-in's uids are short


def make_app(player, **answers):
    """A Launcher's account methods on a fresh data directory whose guest has
    signed up."""
    app = types.SimpleNamespace()
    app.data_dir = fresh_dir()
    app.player = player
    app.mp_thread = None
    app.cfg = {"product": "Cosmic Supremacy: Resurgence"}
    app.view = FakeView(**answers)
    app.said = []
    app.say = app.said.append
    for name in ("on_account", "_account_locked", "_log_in", "_google",
                 "_create_account", "_email_ready",
                 "_account_panel", "_guest_games", "_adopt_account_name",
                 "_push_name", "_show_account", "_show_identity",
                 "_show_player_code", "warn"):
        setattr(app, name, getattr(L.Launcher, name).__get__(app))
    ident = fb_auth.identity(app.data_dir, transport=net, clock=clock)
    ident.token()
    return app, ident


def join(app, ident, galaxy):
    """Write down a galaxy this install joined as whoever is playing now."""
    L.save_joined(app.data_dir, {"galaxy": galaxy, "name": app.player,
                                 "store": "https://relay/" + galaxy,
                                 "uid": ident.uid})


def settle():
    """Wait for the name to reach the account."""
    for t in threading.enumerate():
        if t.name == "account_name":
            t.join(5)


def fields_errors(app):
    return [args[4] for kind, args in app.view.prompts if kind == "fields"]


print("1. a guest creates an account and keeps its galaxies")
ann, ann_id = make_app("Ann", fields=[
    ("create", {"email": "ann@example.com", "password": ""}),
    ("create", {"email": "ann@example.com", "password": PASSWORD,
                "confirm": "Correct horse 2"}),
    ("create", {"email": "ann@example.com", "password": PASSWORD,
                "confirm": PASSWORD})])
join(ann, ann_id, "h3check")
guest = ann_id.uid
ann.on_account()
settle()
check("the top bar shows the account", ann.view.account, "ann@example.com")
check("the uid is the guest's", ann_id.uid, guest)
check("so the joined galaxy is the account's",
      L.load_joined(ann.data_dir)["h3check"]["uid"], ann_id.uid)
check("mismatched passwords are caught before anything is sent",
      fields_errors(ann), ["", "", "The two passwords are not the same."])
check("the call ran with the window answering",
      ("wait", "Creating your account…") in ann.view.prompts, True)
check("the player's name is stored on the account", net.names.get(guest), "Ann")
check("and the player is told about the verification email",
      ann.view.warnings(), lambda w: len(w) == 1 and "ann@example.com" in w[0]
      and "verification" in w[0])

print("\n2. a second install logs in, takes the account's name, and is told "
      "its guest's galaxies stay behind")
bo, bo_id = make_app("Bo", fields=[
    ("log_in", {"email": "ann@example.com", "password": "wrong"}),
    ("log_in", {"email": "ann@example.com", "password": PASSWORD})])
join(bo, bo_id, "sandbox")
bo_guest = bo_id.uid
bo.on_account()
check("a wrong password comes back in the dialog",
      fields_errors(bo), ["", "That email and password do not match an account."])
check("then the install is the account", (bo_id.uid, bo.view.account),
      (guest, "ann@example.com"))
check("and takes the account's name", (bo.player, bo.view.identity), ("Ann", "Ann"))
check("which is kept for the next run", L.load_identity(bo.data_dir), "Ann")
told = bo.view.warnings()
check("the player is given the guest's code for the operator",
      told, lambda w: len(w) == 1 and L.player_code(bo_guest) in w[0])

print("\n3. the account panel logs out to the guest")
bo.view = FakeView(choose="log_out", yes_no=True)
bo.on_account()
check("it says who is logged in and that the email is unverified",
      [args[1] for kind, args in bo.view.prompts if kind == "choose"],
      lambda b: len(b) == 1 and "ann@example.com" in b[0]
      and "not verified" in b[0])
check("logging out asks first", [k for k, _a in bo.view.prompts].count("yes_no"), 1)
check("and returns to the guest", (bo_id.account, bo_id.uid), (None, bo_guest))
check("and the top bar says log in", bo.view.account, None)

bo.view = FakeView(choose="log_out", yes_no=False)
bo_id.log_in("ann@example.com", PASSWORD)
bo.on_account()
check("saying no keeps the account", bo_id.account is not None, True)
bo.view = FakeView(choose="verify")
bo.on_account()
check("the panel sends the verification email again",
      net.emails[-1], ("VERIFY_EMAIL", guest))
bo_id.log_out()

print("\n4. a forgotten password, and a cancel")
cy, cy_id = make_app("Cy", fields=[
    ("reset", {"email": "ann@example.com", "password": ""}),
    (None, {"email": "ann@example.com", "password": ""})])
cy.on_account()
check("a reset email is asked for",
      net.emails[-1], ("PASSWORD_RESET", "ann@example.com"))
check("the player is told to look for it", cy.view.warnings(),
      lambda w: len(w) == 1 and "reset" in w[0])
check("cancelling leaves the guest", (cy_id.account, cy.view.account), (None, None))

print("\n5. not under a live galaxy")
dee, dee_id = make_app("Dee")
dee.mp_thread = types.SimpleNamespace(is_alive=lambda: True)
dee.on_account()
check("the player is told why", dee.view.warnings(),
      lambda w: len(w) == 1 and "playing a galaxy" in w[0])
check("and no dialog opens",
      [k for k, _a in dee.view.prompts if k in ("fields", "choose")], [])

print("\n5b. Google, when the build carries the client secret")
L.google_secret = lambda: "s3cret"


def google_in_browser(secret, cancel=None):
    """The browser half of a Google login, which Cancel interrupts."""
    if cancel.is_set():
        raise fb_auth.AuthError("CANCELLED")
    return "g1|eve@gmail.com"


fb_auth.google_id_token = google_in_browser
eve, eve_id = make_app("Eve", fields=[("google", {"email": "", "password": ""})])
join(eve, eve_id, "h3check")
eve_guest = eve_id.uid
eve.on_account()
settle()
check("the log in dialog offers Google",
      [b for kind, args in eve.view.prompts if kind == "fields" for b in args[3]],
      lambda b: ("Log in with Google", "google") in b)
check("a new Google account is the guest's, with its galaxies",
      (eve_id.uid, eve.view.account, L.load_joined(eve.data_dir)["h3check"]["uid"]),
      (eve_guest, "eve@gmail.com", eve_guest))
check("the player keeps their own name, not Google's",
      (eve.player, net.names.get(eve_guest)), ("Eve", "Eve"))
check("and is told the galaxies are the account's", eve.view.warnings(),
      lambda w: len(w) == 1 and "Google" in w[0])

fay, fay_id = make_app("Fay", cancel=True, fields=[
    ("google", {"email": "", "password": ""}), (None, {})])
fay.on_account()
check("Cancel while the browser is open goes back to the dialog quietly",
      (fields_errors(fay), fay_id.account, fay.view.warnings()),
      (["", ""], None, []))
L.google_secret = lambda: None
gil, _gil_id = make_app("Gil", fields=[(None, {})])
gil.on_account()
check("a build without the secret offers no Google",
      [b for kind, args in gil.view.prompts if kind == "fields" for b in args[3]],
      lambda b: all(v != "google" for _l, v in b))

print("\n5c. an email account joins and plays once its address is verified")
hal, hal_id = make_app("Hal")
check("a guest goes straight through", hal._email_ready(), True)
check("without a dialog", hal.view.prompts, [])
hal_id.create_account("hal@example.com", PASSWORD)
hal.view = FakeView(choose=["verify", "check", None])
check("an unverified account is stopped", hal._email_ready(), False)
told = [args[1] for kind, args in hal.view.prompts if kind == "choose"]
check("and told why, three times round", told,
      lambda b: len(b) == 3 and all("hal@example.com" in t for t in b))
check("Send email again sends it", net.emails[-1], ("VERIFY_EMAIL", hal_id.uid))
check("and says so", told[1], lambda t: t.startswith("We sent another"))
check("I have verified it, when it is not, says that", told[2],
      lambda t: t.startswith("Your email address is not verified yet"))
check("closing it is logged", hal.said[-1],
      lambda s: "not verified" in s)
held = hal_id.token()
net.verified.add(hal_id.uid)
hal.view = FakeView(choose="check")
check("a verified account goes through", hal._email_ready(), True)
check("with a renewed token, so the relay sees the address verified",
      hal_id.token() != held, True)
check("the relay's sentence is recognised",
      L.email_unverified("verify the email address of this account before it "
                         "joins or plays"), True)
check("and its box names the address",
      L.refusal_box("verify the email address of this account", "Hal", hal.data_dir),
      lambda t: "hal@example.com" in t and "not verified" in t)
check("a seat refusal keeps its own box",
      L.refusal_box("Hal is already held by another sign-in", "Hal", hal.data_dir),
      lambda t: "held by another sign-in" in t)
check("and other refusals have none",
      L.refusal_box("this galaxy is closed", "Hal", hal.data_dir), None)

print("\n6. an account whose login dies is reported once")
dee.mp_thread = None
dee_id.log_in("ann@example.com", PASSWORD)
dee_id.dropped = True
dee._show_player_code()
dee._show_player_code()
check("the player is told once", dee.view.warnings(),
      lambda w: len(w) == 2 and "logged out" in w[1])

for d in dirs:
    shutil.rmtree(d, ignore_errors=True)
print("\n" + ("ALL PASSED" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
