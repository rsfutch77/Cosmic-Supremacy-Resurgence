"""
fb_auth.py , the launcher's Firebase identity
==============================================
    import fb_auth
    me = fb_auth.identity(data_dir)
    store = turn_store.open_store(spec, token=me.token)

An install's stable Firebase uid, and a short-lived ID token to put on the
requests that carry it. `token` takes no arguments and returns a `str` or
`None`, which is exactly what `HttpTurnStore(base, token=...)` expects: a
string becomes `Authorization: Bearer <token>`, and `None` means the request
goes out bare and the store behaves as it did before any of this existed.

This is H4's identity. It is not the player's name and does not replace it.
The name in `identity.json` stays what it has always been, a claim the player
typed, shown to other players and matched against a galaxy's roster. The uid
here is never shown to the player, is per install rather than per person, and
exists so the function in front of the store can bind a seat to the install
that claimed it (J4).

Why an ID token rather than a Firebase client
---------------------------------------------
There is no official Firebase client SDK for Python, and a player must never
hold a Google Cloud credential: a service account key inside a distributed
executable is project-wide, unrevokable per player, and would let any holder do
anything to any galaxy. So the launcher speaks plain HTTPS to two public Google
endpoints, gets back a token that says only "this install", and hands that to
the Cloud Function, which holds the admin credential and decides what the uid
is allowed to do.

Anonymous sign-in is what H4 chose because it adds no login screen: no account,
no password, nothing for a beta player to fill in. Google login replaces where
this token comes from and nothing else. That swap is `_sign_in` below and the
URL beside it; everything else here, the persistence, the refresh, the offline
answer and the shape `token` presents, is unchanged by it. There is
deliberately no custom claim scheme, which H4 says not to build because Google
login would immediately replace it.

The API key
-----------
`WEB_API_KEY` is a Firebase Web API key. **It is not a secret and it is not a
credential.** It identifies the project to the Identity Toolkit, it authorises
nothing, and every web app built on Firebase ships one in its page source. It
is here for the same reason: a desktop app has to name the project it is
signing in to. What protects the project is the security rules and the
function, not this string. A service account key, an ADC file or any private
key is a different thing entirely and none of them belongs anywhere near the
launcher.
"""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request

# The Firebase project the beta runs in, and its public Web API key. See the
# module docstring: this key is public by design and authorises nothing.
PROJECT = "cs-resurgence"
WEB_API_KEY = "AIzaSyC4gdHRgtkTBee9P45xNzP6M9zE51VPUgI"

SIGNUP_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signUp"
REFRESH_URL = "https://securetoken.googleapis.com/v1/token"
DELETE_URL = "https://identitytoolkit.googleapis.com/v1/accounts:delete"

# Beside identity.json in the launcher's data directory, and separate from it
# because the two answer different questions and only one of them is the
# player's. See `identity_path` and `find_data_dir` in launcher.py.
STATE_FILE = "fb_identity.json"

# An ID token lasts an hour. Renewing it this many seconds early means a turn
# that starts holding a valid token still holds one when it submits, rather
# than meeting the expiry as a 401 part way through.
REFRESH_MARGIN = 300.0

TIMEOUT = 15.0

# What the Identity Toolkit says when a refresh token is no longer worth
# holding: the user was deleted, disabled, or the token was revoked. Anything
# else is a failure of this attempt rather than of the identity.
DEAD_REFRESH = ("TOKEN_EXPIRED", "USER_DISABLED", "USER_NOT_FOUND",
                "INVALID_REFRESH_TOKEN")


def post_json(url: str, payload: dict, timeout: float = TIMEOUT) -> dict:
    """One JSON POST, raising on anything that is not a 2xx.

    The transport the identity uses by default, and the seam a test replaces:
    everything above it is decision rather than network.
    """
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def error_code(exc) -> str:
    """The Identity Toolkit's own name for a failure, or ''.

    A 400 from these endpoints carries `error.message`, which is the only part
    of the response worth branching on. A body that does not parse is not worth
    a second error, so it reads as no code at all.
    """
    read = getattr(exc, "read", None)
    if read is None:
        return ""
    try:
        return str(json.loads(read()).get("error", {}).get("message", ""))
    except (ValueError, OSError):
        return ""


class FirebaseIdentity:
    """This install's uid, and an ID token for it on demand.

    `token()` is the whole interface a caller needs. It signs up on first use,
    refreshes ahead of expiry on later ones, and answers None rather than
    raising when the network is not there, so an offline launcher degrades to
    the behaviour it had before tokens existed instead of failing to open a
    galaxy at all.
    """

    def __init__(self, data_dir: str, api_key: str = WEB_API_KEY,
                 timeout: float = TIMEOUT, transport=post_json,
                 clock=time.time):
        self.path = os.path.join(data_dir, STATE_FILE)
        self.api_key = api_key
        self.timeout = timeout
        self._post = transport
        self._clock = clock
        self._id_token = None
        self._expires_at = 0.0
        self.uid, self._refresh_token = self._load()

    # -- persistence --
    def _load(self):
        """(uid, refresh token) from disk, or (None, None).

        A file that is missing, unreadable or incomplete counts as no identity,
        the way `load_identity` treats a name it cannot use. The cost of being
        wrong here is one more anonymous user, not a player locked out.
        """
        try:
            with open(self.path, encoding="utf-8") as fh:
                stored = json.load(fh)
        except (OSError, ValueError):
            return None, None
        if not isinstance(stored, dict):
            return None, None
        uid = stored.get("uid")
        refresh = stored.get("refresh_token")
        if not isinstance(uid, str) or not isinstance(refresh, str):
            return None, None
        if not uid or not refresh:
            return None, None
        return uid, refresh

    def _save(self) -> None:
        """Written the way identity.json is written, beside it.

        The ID token itself is not kept. It is good for an hour, a refresh
        mints another in one call, and a bearer token left on disk is one more
        thing to reason about for no gain.
        """
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"uid": self.uid, "refresh_token": self._refresh_token,
                       "project": PROJECT}, fh, indent=2)

    def _keep(self, got: dict):
        """Take what a sign-in or a refresh returned, and remember it.

        The two endpoints answer in different cases, `idToken` from the
        Identity Toolkit and `id_token` from the secure token service, so both
        spellings are read here rather than at each call site.
        """
        self._id_token = got.get("idToken") or got.get("id_token")
        uid = got.get("localId") or got.get("user_id")
        if uid:
            self.uid = uid
        refresh = got.get("refreshToken") or got.get("refresh_token")
        if refresh:
            self._refresh_token = refresh
        try:
            lifetime = float(got.get("expiresIn") or got.get("expires_in") or 0)
        except (TypeError, ValueError):
            lifetime = 0.0
        self._expires_at = self._clock() + lifetime
        try:
            self._save()
        except OSError:
            # Usable for this run, and a next run signs up again and the galaxy
            # sees a new install. Not worth failing a turn over.
            pass
        return self._id_token

    # -- the two calls that talk to Google --
    def _sign_in(self) -> dict:
        """A new anonymous user in the project.

        **This is the one method Google login replaces.** It returns whatever
        the sign-in endpoint answered, and `_keep` reads the uid, the ID token
        and the refresh token out of it under the names both of this module's
        endpoints already use, which a Google sign-in response also carries.
        Nothing else in this file knows which sign-in produced the token it is
        holding.
        """
        return self._post(SIGNUP_URL + "?key=" + self.api_key,
                          {"returnSecureToken": True}, self.timeout)

    def _refresh(self) -> dict:
        return self._post(REFRESH_URL + "?key=" + self.api_key,
                          {"grant_type": "refresh_token",
                           "refresh_token": self._refresh_token},
                          self.timeout)

    # -- the interface --
    def token(self):
        """An ID token, or None. Takes no arguments, so it can be passed as one.

        Handed to `HttpTurnStore(base, token=...)` as the callable itself
        rather than as a value, because a galaxy is followed for longer than a
        token lasts and the store has to be able to ask again.
        """
        if self._id_token and self._clock() < self._expires_at - REFRESH_MARGIN:
            return self._id_token
        if self._refresh_token:
            try:
                return self._keep(self._refresh())
            except urllib.error.HTTPError as exc:
                if error_code(exc) not in DEAD_REFRESH:
                    return None
                # The user behind this refresh token is gone, so the install
                # needs a new identity rather than another attempt at this one.
                self.uid = self._refresh_token = None
                self._id_token = None
            except (urllib.error.URLError, socket.timeout, OSError, ValueError):
                return None
        try:
            return self._keep(self._sign_in())
        except (urllib.error.HTTPError, urllib.error.URLError, socket.timeout,
                OSError, ValueError):
            # Offline, or the project is not answering. The caller carries on
            # with no header, and whatever it is talking to refuses or allows
            # the request on its own terms.
            return None

    def forget(self) -> None:
        """Drop this install's identity, in memory and on disk.

        Not on the player's path. It is here for a caller that has just deleted
        the user this identity names, so the next `token()` does not spend a
        call discovering that.
        """
        self.uid = self._refresh_token = self._id_token = None
        self._expires_at = 0.0
        try:
            os.remove(self.path)
        except OSError:
            pass

    def delete(self) -> bool:
        """Remove this anonymous user from the project. True if it is gone.

        An anonymous user costs nothing and is never reused, so the launcher
        has no reason to call this. Something that created one for a test does.
        """
        token = self.token()
        if not token:
            return False
        try:
            self._post(DELETE_URL + "?key=" + self.api_key,
                       {"idToken": token}, self.timeout)
        except (urllib.error.HTTPError, urllib.error.URLError, socket.timeout,
                OSError, ValueError):
            return False
        self.forget()
        return True


_identities = {}


def identity(data_dir: str, **kw) -> FirebaseIdentity:
    """This install's identity, one per data directory per process.

    Cached so a launcher that opens a galaxy, stops, and opens it again keeps
    the token it already holds instead of refreshing on every open. Passing
    any keyword builds a fresh one, which is what a test wants and what the
    launcher never does.
    """
    key = os.path.abspath(data_dir)
    if kw or key not in _identities:
        _identities[key] = FirebaseIdentity(data_dir, **kw)
    return _identities[key]
