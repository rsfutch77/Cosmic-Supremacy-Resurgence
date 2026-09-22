"""
This install's Firebase identity: sign-up, what survives a restart, refresh
ahead of expiry, and what an offline launcher gets instead of a crash.

Headless and offline by default. The Identity Toolkit is replaced at
`fb_auth.post_json`, the module's one network seam, so every decision above it
runs in a temporary directory in about a second. No game, no window, and
nothing here signs a real user up.

Set CS_FB_LIVE=1 to also run section 8 against the live project, which creates
one anonymous user and deletes it again before exiting.
"""
import json
import os
import shutil
import sys
import tempfile
import urllib.error

import pathlib
REPO = str(pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, os.path.join(REPO, "release"))
sys.path.insert(0, os.path.join(REPO, "server"))

import fb_auth                                                  # noqa: E402
import launcher as L                                            # noqa: E402

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


class FakeToolkit:
    """The two endpoints, answering the way the real ones were measured to.

    signUp answers in camel case and the secure token service in snake case,
    which is the difference the identity has to absorb. `calls` records what
    was asked for, because "did it refresh" and "did it sign up again" are
    questions about calls rather than about return values.
    """

    def __init__(self):
        self.calls = []
        self.users = {}
        self.n = 0
        self.offline = False
        self.dead_refresh = False

    def __call__(self, url, payload, timeout=None):
        self.calls.append(url.split("/")[-1].split("?")[0])
        if self.offline:
            raise urllib.error.URLError("getaddrinfo failed")
        if url.startswith(fb_auth.SIGNUP_URL):
            self.n += 1
            uid = f"uid{self.n}"
            self.users[uid] = f"refresh{self.n}"
            return {"localId": uid, "idToken": f"id{self.n}.a",
                    "refreshToken": f"refresh{self.n}", "expiresIn": "3600"}
        if url.startswith(fb_auth.REFRESH_URL):
            if self.dead_refresh:
                raise urllib.error.HTTPError(
                    url, 400, "Bad Request", {},
                    _body(json.dumps({"error": {"message": "USER_NOT_FOUND"}})))
            uid = next((u for u, r in self.users.items()
                        if r == payload["refresh_token"]), None)
            if uid is None:
                raise urllib.error.HTTPError(
                    url, 400, "Bad Request", {},
                    _body(json.dumps(
                        {"error": {"message": "INVALID_REFRESH_TOKEN"}})))
            return {"user_id": uid, "id_token": f"{uid}.fresh",
                    "refresh_token": payload["refresh_token"],
                    "expires_in": "3600"}
        if url.startswith(fb_auth.DELETE_URL):
            self.users = {u: r for u, r in self.users.items()
                          if not payload["idToken"].startswith(u)}
            return {}
        raise AssertionError("unexpected endpoint " + url)


def _body(text):
    import io
    return io.BytesIO(text.encode("utf-8"))


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


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
    check("and refreshes it to a different token",
          live.token() not in (None, live_token), True)
    check("and the user is deleted again", live.delete(), True)
    check("leaving no state file behind",
          not os.path.exists(os.path.join(live_dir, fb_auth.STATE_FILE)), True)

for d in dirs:
    shutil.rmtree(d, ignore_errors=True)
print("\n" + ("ALL PASSED" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
