"""
This install's Firebase identity: sign-up, what survives a restart, refresh
ahead of expiry, and what an offline launcher gets instead of a crash.

Headless and offline by default. The Identity Toolkit is replaced at
`fb_auth.post_json`, the module's one network seam.

Set CS_FB_LIVE=1 to also run section 8 against the live project, which creates
one anonymous user and deletes it again before exiting.

Set FIREBASE_AUTH_EMULATOR_HOST to also run section 10 against the Auth
emulator (`python functions/emulators.py --only auth`).
"""
import base64
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pathlib
REPO = str(pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, os.path.join(REPO, "release"))
sys.path.insert(0, os.path.join(REPO, "server"))

import fb_auth                                                  # noqa: E402
import launcher as L                                            # noqa: E402
from fake_toolkit import Clock, FakeToolkit, _body              # noqa: E402

fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        fails.append(label)


dirs = []


def fresh_dir():
    d = tempfile.mkdtemp(prefix="fbauth_")
    dirs.append(d)
    return d


print("1. the first use signs up, and the uid is not the player's name")
data = fresh_dir()
net, clock = FakeToolkit(), Clock()
me = fb_auth.identity(data, transport=net, clock=clock)
check("no identity before anything is asked of it", me.uid, None)
tok = me.token()
check("token() answers a string", isinstance(tok, str), True)
check("and signing up is what produced it", net.calls, ["accounts:signUp"])
check("the install now has a uid", me.uid, "uid1")
check("which is not stored in identity.json",
      os.path.exists(L.identity_path(data)), False)
check("a second ask inside the hour is free",
      (me.token(), net.calls), lambda g: g[0] == tok and len(g[1]) == 1)

print("\n2. the uid and the refresh token survive a restart, the ID token is not kept")
stored = json.load(open(os.path.join(data, fb_auth.STATE_FILE), encoding="utf-8"))
check("the file holds the uid", stored.get("uid"), "uid1")
check("and the refresh token", stored.get("refresh_token"), "refresh1")
check("and names the project", stored.get("project"), "cs-resurgence")
check("and does not hold the ID token", "idToken" in json.dumps(stored), False)

net.calls = []
again = fb_auth.identity(data, transport=net, clock=clock)   # a new process
check("a restart reads the uid back without a call",
      (again.uid, net.calls), ("uid1", []))
check("and its first token is a refresh, not a second sign-up",
      (again.token(), net.calls), ("uid1.fresh", ["token"]))
check("so the install is still the same uid", again.uid, "uid1")

print("\n3. the token is renewed before it expires, not after it fails")
net.calls = []
clock.t += 3600 - fb_auth.REFRESH_MARGIN - 60      # still valid, not yet due
check("a token with time left is reused",
      (again.token(), net.calls), ("uid1.fresh", []))
clock.t += 120                                      # inside the margin now
check("one inside the margin is renewed first",
      (again.token(), net.calls), ("uid1.fresh", ["token"]))

print("\n4. offline answers None rather than raising")
off_dir = fresh_dir()
net.offline = True
cold = fb_auth.identity(off_dir, transport=net, clock=clock)
check("an install that has never signed up gets None", cold.token(), None)
check("and wrote no state file",
      os.path.exists(os.path.join(off_dir, fb_auth.STATE_FILE)), False)
warm = fb_auth.identity(data, transport=net, clock=clock)
clock.t += 3600
check("an install that has a refresh token also gets None", warm.token(), None)
check("and keeps its uid for when the network comes back", warm.uid, "uid1")
net.offline = False
check("and gets a token once it does", isinstance(warm.token(), str), True)

print("\n5. a refresh token the project no longer knows becomes a new identity")
net.calls = []
net.dead_refresh = True
gone = fb_auth.identity(data, transport=net, clock=clock)
gone._expires_at = 0
tok = gone.token()
net.dead_refresh = False
check("the refresh was tried and then a sign-up followed",
      net.calls, ["token", "accounts:signUp"])
check("and the install has a new uid", gone.uid, "uid2")
check("and a usable token", isinstance(tok, str), True)

print("\n6. a state file that cannot be used is no identity at all")
for label, text in (("not JSON", "{"), ("not an object", '"hello"'),
                    ("no refresh token", '{"uid": "u"}'),
                    ("an empty uid", '{"uid": "", "refresh_token": "r"}')):
    d = fresh_dir()
    open(os.path.join(d, fb_auth.STATE_FILE), "w", encoding="utf-8").write(text)
    check(f"{label} reads as no uid",
          fb_auth.identity(d, transport=net, clock=clock).uid, None)

print("\n7. the launcher's side: which galaxies get a token, and how it is passed")
check("an https store wants one",
      L.store_wants_token({"store": "https://x.cloudfunctions.net/g"}), True)
check("a LAN turn_server does not",
      L.store_wants_token({"store": "http://192.168.1.9:8899"}), False)
check("a folder does not",
      L.store_wants_token({"store": "C:\\galaxies\\demo"}), False)
check("an explicit auth:true overrides",
      L.store_wants_token({"store": "http://localhost:5001", "auth": True}), True)
check("an explicit auth:false overrides the other way",
      L.store_wants_token({"store": "https://x/g", "auth": False}), False)


class FakeHttpStore:
    def __init__(self, base, timeout=30.0, token=None):
        self.base, self.token = base, token


class OldHttpStore:
    def __init__(self, base, timeout=30.0):
        self.base, self.token = base, None


class FakeDirStore:
    def __init__(self, path):
        self.path, self.token = path, None


class Stores:
    """A turn_store that knows about tokens, and one that does not."""

    def __init__(self, http):
        self.HttpTurnStore = http

    def open_store(self, spec):
        if spec.startswith(("http://", "https://")):
            return self.HttpTurnStore(spec)
        return FakeDirStore(spec)


new, old = Stores(FakeHttpStore), Stores(OldHttpStore)


def sentinel():
    return "ID-TOKEN"


check("no token means today's call, unchanged",
      L.open_player_store(new, "http://host:8899", None).token, None)
check("a directory is opened the same way with a token in hand",
      isinstance(L.open_player_store(new, "C:\\g\\demo", sentinel), FakeDirStore),
      True)
check("a token-aware HTTP store is given the callable itself",
      L.open_player_store(new, "https://f/g", sentinel).token,
      lambda got: got is sentinel)
check("and a turn_store that predates the token still opens",
      L.open_player_store(old, "https://f/g", sentinel).token, None)


class NewOpenStore(Stores):
    def open_store(self, spec, token=None):
        return self.HttpTurnStore(spec, token=token)


check("an open_store that takes a token is preferred to the class",
      L.open_player_store(NewOpenStore(FakeHttpStore), "https://f/g",
                          sentinel).token, lambda got: got is sentinel)
check("player_token() hands back something callable with no arguments",
      callable(L.player_token(fresh_dir())), True)

print("\n8. against the live project")
if os.environ.get("CS_FB_LIVE") != "1":
    print("  [SKIP] set CS_FB_LIVE=1 to sign a real anonymous user up and "
          "delete it again")
else:
    live_dir = fresh_dir()
    live = fb_auth.identity(live_dir, timeout=20.0)
    live_token = live.token()
    check("the live project signs this install up",
          isinstance(live_token, str) and len(live_token) > 500, True)
    check("and names a uid", bool(live.uid), True)
    live._expires_at = 0
    time.sleep(1.1)         # a token minted in the same second is the same token
    check("and refreshes it to a different token",
          live.token() not in (None, live_token), True)
    live_uid = live.uid
    live.send_verification = lambda: False      # example.com receives nothing
    live_email = f"cs-live-{os.getpid()}@example.com"
    check("and links an email and password to the guest",
          (live.create_account(live_email, "Correct horse 1"), live.uid),
          ({"email": live_email, "provider": "password"}, live_uid))
    check("and the user is deleted again", live.delete(), True)
    check("leaving no state file behind",
          not os.path.exists(os.path.join(live_dir, fb_auth.STATE_FILE)), True)

print("\n9. accounts: creating one keeps the guest's uid, logging in to one keeps the guest")


def refused(fn, *args):
    """The AuthError `fn` raises, as (code, text), or None."""
    try:
        fn(*args)
    except fb_auth.AuthError as exc:
        return exc.code, str(exc)
    return None


net, clock = FakeToolkit(), Clock()
home = fresh_dir()
me = fb_auth.identity(home, transport=net, clock=clock)
me.token()
guest_uid = me.uid
got = me.create_account(" ann@example.com ", "Correct horse 1")
check("creating an account answers who is logged in",
      got, {"email": "ann@example.com", "provider": "password"})
check("and the uid is the guest's, so its seats carry over", me.uid, guest_uid)
check("the guest file is gone, since the guest is the account",
      os.path.exists(os.path.join(home, fb_auth.STATE_FILE)), False)
stored = json.load(open(os.path.join(home, fb_auth.ACCOUNT_FILE), encoding="utf-8"))
check("the account file holds the uid and the email",
      (stored.get("uid"), stored.get("email")), (guest_uid, "ann@example.com"))
check("and does not hold the refresh token in the clear",
      net.users[guest_uid] in json.dumps(stored), sys.platform != "win32")
check("a verification email went out", net.emails,
      [("VERIFY_EMAIL", guest_uid)])

net.calls = []
again = fb_auth.identity(home, transport=net, clock=clock)    # a new process
check("a restart is still logged in",
      (again.account, again.uid), ({"email": "ann@example.com",
                                    "provider": "password"}, guest_uid))
check("and refreshes the account's token", (again.token(), net.calls),
      (f"{guest_uid}.fresh", ["token"]))
check("the account takes a display name", again.set_display_name("Ann"), True)
check("and looks itself up",
      again.lookup(), {"email": "ann@example.com", "verified": False, "name": "Ann"})
check("logging in while logged in is refused",
      refused(again.log_in, "ann@example.com", "Correct horse 1"),
      lambda g: g and g[0] == "LOGGED_IN")

again.log_out()
check("logging out forgets the account",
      (again.account, os.path.exists(os.path.join(home, fb_auth.ACCOUNT_FILE))),
      (None, False))
again.token()
check("and the next token is a new guest, without the account's seats",
      again.uid not in (None, guest_uid), True)

other = fresh_dir()
two = fb_auth.identity(other, transport=net, clock=clock)
two.token()
second_guest = two.uid
check("a second install logs in to the account",
      two.log_in("ann@example.com", "Correct horse 1")["email"], "ann@example.com")
check("as the account's uid", two.uid, guest_uid)
check("keeping its own guest on disk",
      json.load(open(os.path.join(other, fb_auth.STATE_FILE),
                     encoding="utf-8"))["uid"], second_guest)
two.log_out()
check("and logging out returns to that guest", two.uid, second_guest)

print("\n9b. what the player is told")
check("a wrong password",
      refused(two.log_in, "ann@example.com", "wrong"),
      ("INVALID_LOGIN_CREDENTIALS",
       "That email and password do not match an account."))
check("an email already in use",
      refused(two.create_account, "ann@example.com", "Another long 1"),
      lambda g: g and g[0] == "EMAIL_EXISTS" and "Log in instead" in g[1])
net.calls = []
check("a short password is refused before anything is sent",
      (refused(two.create_account, "bo@example.com", "short"), net.calls),
      lambda g: g[0][0] == "WEAK_PASSWORD" and g[1] == [])
check("and it says what is missing",
      refused(two.create_account, "bo@example.com", "lowercase only")[1],
      "Your password needs an upper case letter and a digit.")
check("a password that meets every rule needs nothing",
      fb_auth.password_missing("Correct horse 1"), "")
check("logging in does not apply the rules to an old password",
      refused(two.log_in, "ann@example.com", "short")[0],
      "INVALID_LOGIN_CREDENTIALS")
check("so is a missing email", refused(two.log_in, "  ", "x")[0], "MISSING_EMAIL")
check("and one with no @", refused(two.log_in, "bo", "x")[0], "INVALID_EMAIL")
check("a detailed refusal passes its detail on",
      str(fb_auth.refusal(urllib.error.HTTPError(
          "u", 400, "Bad Request", {}, _body(json.dumps({"error": {
              "message": "WEAK_PASSWORD : Password should be at least 6 "
                         "characters"}}))))),
      "Password should be at least 6 characters")
check("an unknown one names its code",
      str(fb_auth.refusal(urllib.error.HTTPError(
          "u", 400, "Bad Request", {},
          _body(json.dumps({"error": {"message": "SOMETHING_NEW"}}))))),
      "Login failed (SOMETHING_NEW).")
net.offline = True
check("offline", refused(two.log_in, "ann@example.com", "Correct horse 1")[0],
      "OFFLINE")
net.offline = False
two.send_password_reset("ann@example.com")
check("a reset email is asked for by address",
      net.emails[-1], ("PASSWORD_RESET", "ann@example.com"))

print("\n9c. an account whose login dies, or cannot be read, falls back to the guest")
two.log_in("ann@example.com", "Correct horse 1")
two._expires_at = 0
net.dead_refresh = guest_uid
two.token()
net.dead_refresh = False
check("a dead account login is dropped", (two.account, two.dropped), (None, True))
check("and the install plays as its guest", two.uid, second_guest)
check("the account file is gone",
      os.path.exists(os.path.join(other, fb_auth.ACCOUNT_FILE)), False)

two.log_in("ann@example.com", "Correct horse 1")
path = os.path.join(other, fb_auth.ACCOUNT_FILE)
stored = json.load(open(path, encoding="utf-8"))
stored.pop("refresh_token", None)
stored["refresh_token_dpapi"] = "c2VhbGVkIGVsc2V3aGVyZQ=="
json.dump(stored, open(path, "w", encoding="utf-8"))
moved = fb_auth.identity(other, transport=net, clock=clock)
check("an account file sealed for another Windows user reads as the guest",
      (moved.account, moved.uid), (None, second_guest))

print("\n9d. Google: the browser comes back to the launcher with a code")


class Browser:
    """Opens the URL it is given by following Google's redirect at once: first
    a request with the wrong state, then the real answer `query` adds to."""

    def __init__(self, query=None, wrong_first=True):
        self.query, self.wrong_first, self.opened = query or {}, wrong_first, []

    def __call__(self, url):
        self.opened.append(url)
        sent = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        back = sent["redirect_uri"][0]

        def go():
            if self.wrong_first:
                try:
                    urllib.request.urlopen(back + "/?code=stolen&state=guess", timeout=5)
                except urllib.error.HTTPError as exc:
                    self.wrong_status = exc.code
            answer = dict({"state": sent["state"][0]}, **self.query)
            with urllib.request.urlopen(back + "/?" + urllib.parse.urlencode(answer),
                                        timeout=5) as r:
                self.page = r.read().decode("utf-8")

        self.thread = threading.Thread(target=go, daemon=True)
        self.thread.start()


class TokenEndpoint:
    def __init__(self, answer):
        self.answer, self.asked = answer, []

    def __call__(self, url, fields, timeout=None):
        self.asked.append((url, dict(fields)))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


browser = Browser({"code": "the-code"})
endpoint = TokenEndpoint({"id_token": "g1|ann@gmail.com"})
check("the browser's code becomes Google's ID token",
      fb_auth.google_id_token("s3cret", open_browser=browser, transport=endpoint),
      "g1|ann@gmail.com")
sent = urllib.parse.parse_qs(urllib.parse.urlparse(browser.opened[0]).query)
check("Google is asked for the profile and email only",
      sent["scope"], ["openid email profile"])
check("with a PKCE challenge", sent["code_challenge_method"], ["S256"])
check("and a listener on this machine only",
      sent["redirect_uri"][0].startswith("http://127.0.0.1:"), True)
check("a request with the wrong state is turned away", browser.wrong_status, 404)
asked = endpoint.asked[0][1]
check("the code is exchanged with the verifier and the secret",
      (asked["code"], asked["client_secret"], len(asked["code_verifier"]) >= 43),
      ("the-code", "s3cret", True))
browser.thread.join(5)
check("and the tab says to go back to the launcher",
      "return to the launcher" in browser.page, True)


def google_refused(**kw):
    try:
        fb_auth.google_id_token("s3cret", **kw)
    except fb_auth.AuthError as exc:
        return exc.code
    return None


check("declining in the browser",
      google_refused(open_browser=Browser({"error": "access_denied"}, False),
                     transport=endpoint), "GOOGLE_REFUSED")
check("a browser that never comes back",
      google_refused(open_browser=lambda url: None, transport=endpoint, wait=0.5),
      "GOOGLE_LATE")
stop = threading.Event()
stop.set()
check("Cancel in the launcher",
      google_refused(open_browser=lambda url: None, transport=endpoint, cancel=stop),
      "CANCELLED")
check("a code Google will not exchange",
      google_refused(open_browser=Browser({"code": "x"}, False),
                     transport=TokenEndpoint(urllib.error.HTTPError(
                         "u", 400, "Bad Request", {}, _body("{}")))),
      "GOOGLE_REFUSED")

print("\n9e. Google: a new Google account becomes the guest, a known one is logged in to")
gdir = fresh_dir()
g = fb_auth.identity(gdir, transport=net, clock=clock)
g.token()
g_guest = g.uid
got = g.log_in_google("g7|gus@gmail.com")
check("a Google account new to the project is linked to the guest",
      (got, g.uid), ({"email": "gus@gmail.com", "provider": "google.com",
                      "linked": True}, g_guest))
check("and the guest file is gone",
      os.path.exists(os.path.join(gdir, fb_auth.STATE_FILE)), False)
check("the profile's name is not taken for the player's",
      g.lookup()["name"], None)
check("but a name the launcher stored is", (g.set_display_name("Gus"),
                                           g.lookup()["name"]), (True, "Gus"))
hdir = fresh_dir()
h = fb_auth.identity(hdir, transport=net, clock=clock)
h.token()
h_guest = h.uid
got = h.log_in_google("g7|gus@gmail.com")
check("on another install the same Google account logs in to it",
      (got["linked"], h.uid), (False, g_guest))
check("keeping that install's guest", h._load()[0], h_guest)
h.log_out()
net.password_emails.add("pat@example.com")
check("an email that has a password account is sent to the password",
      refused(h.log_in_google, "g8|pat@example.com"),
      lambda r: r and r[0] == "NEEDS_PASSWORD" and "pat@example.com" in r[1])
check("and the install is still its guest", (h.account, h.uid), (None, h_guest))

print("\n10. accounts against the Auth emulator")
EMU = os.environ.get("FIREBASE_AUTH_EMULATOR_HOST")
if not EMU:
    print("  [SKIP] set FIREBASE_AUTH_EMULATOR_HOST to run against the emulator")
else:
    stamp = str(int(clock.t * 1000) ^ os.getpid())
    email = f"login{stamp}@example.com"
    a_dir, b_dir = fresh_dir(), fresh_dir()
    a = fb_auth.identity(a_dir, emulator=EMU, api_key="fake-api-key")
    check("the emulator signs a guest up", isinstance(a.token(), str), True)
    guest = a.uid
    a.create_account(email, "Correct horse 1")
    check("linking an email keeps the guest's uid", a.uid, guest)
    a._expires_at = 0
    check("and the account's refresh token works", isinstance(a.token(), str), True)
    claims = json.loads(base64.urlsafe_b64decode(
        a.token().split(".")[1] + "=="))
    check("its token now says it signed in with a password",
          claims.get("firebase", {}).get("sign_in_provider"), "password")
    check("the display name is stored", a.set_display_name("Ann"), True)
    check("and read back, unverified",
          a.lookup(), {"email": email, "verified": False, "name": "Ann"})
    b = fb_auth.identity(b_dir, emulator=EMU, api_key="fake-api-key")
    b.token()
    b_guest = b.uid
    b.log_in(email, "Correct horse 1")
    check("another install logs in as the same uid", b.uid, guest)
    b.log_out()
    check("and logs out to its own guest", b.uid, b_guest)
    check("a wrong password is refused",
          refused(b.log_in, email, "nope")[0],
          lambda c: c in ("INVALID_LOGIN_CREDENTIALS", "INVALID_PASSWORD"))
    check("an email in use is refused",
          refused(b.create_account, email, "Correct horse 1")[0], "EMAIL_EXISTS")
    b.send_password_reset(email)
    check("a reset email is accepted", True, True)
    check("the account is deleted again", a.delete(), True)
    check("and so is the other guest", b.delete(), True)

    c_dir, d_dir = fresh_dir(), fresh_dir()
    c = fb_auth.identity(c_dir, emulator=EMU, api_key="fake-api-key")
    c.token()
    c_guest = c.uid
    fake_google = json.dumps({"sub": "emu" + stamp, "email": f"g{stamp}@example.com",
                              "email_verified": True, "name": "Google Person"})
    got = c.log_in_google(fake_google)
    check("the emulator links a Google login to the guest",
          (got["linked"], c.uid), (True, c_guest))
    c._expires_at = 0
    claims = json.loads(base64.urlsafe_b64decode(c.token().split(".")[1] + "=="))
    check("and its token says it signed in with Google",
          claims.get("firebase", {}).get("sign_in_provider"), "google.com")
    check("the Google profile's name is not the player's", c.lookup()["name"], None)
    c.set_display_name("Gus")
    check("but a name the launcher stores is", c.lookup()["name"], "Gus")
    d = fb_auth.identity(d_dir, emulator=EMU, api_key="fake-api-key")
    d.token()
    d_guest = d.uid
    got = d.log_in_google(fake_google)
    check("another install's Google login finds the same account",
          (got["linked"], d.uid), (False, c_guest))
    d.log_out()
    check("and logs out to its own guest", d.uid, d_guest)
    check("the Google account is deleted again", c.delete(), True)
    check("and so is the other guest", d.delete(), True)

for d in dirs:
    shutil.rmtree(d, ignore_errors=True)
print("\n" + ("ALL PASSED" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
