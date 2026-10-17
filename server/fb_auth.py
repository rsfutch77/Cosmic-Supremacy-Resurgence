"""
fb_auth.py: the launcher's Firebase identity

    import fb_auth
    me = fb_auth.identity(data_dir)
    store = turn_store.open_store(spec, token=me.token)

An install's stable anonymous Firebase uid, and a short-lived ID token for it.
`token` takes no arguments and returns a `str` (sent as `Authorization: Bearer
<token>`) or `None` (the request goes out bare). The uid is per install, never
shown to the player, and separate from the player's name in `identity.json`.

The launcher holds no Google Cloud credential. It speaks HTTPS to the public
Identity Toolkit endpoints, and the relay decides what the uid may do.
`WEB_API_KEY` is a public Firebase Web API key: it names the project and
authorises nothing.
"""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request

# The beta's Firebase project and its public Web API key.
PROJECT = "cs-resurgence"
WEB_API_KEY = "AIzaSyC4gdHRgtkTBee9P45xNzP6M9zE51VPUgI"

SIGNUP_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signUp"
REFRESH_URL = "https://securetoken.googleapis.com/v1/token"
DELETE_URL = "https://identitytoolkit.googleapis.com/v1/accounts:delete"

STATE_FILE = "fb_identity.json"   # beside identity.json in the launcher's data directory

REFRESH_MARGIN = 300.0   # seconds before the one-hour ID token expiry to renew it

TIMEOUT = 15.0

# Identity Toolkit errors meaning the refresh token is dead and a new identity
# is needed. Any other error is a failure of this attempt only.
DEAD_REFRESH = ("TOKEN_EXPIRED", "USER_DISABLED", "USER_NOT_FOUND",
                "INVALID_REFRESH_TOKEN")


def post_json(url: str, payload: dict, timeout: float = TIMEOUT) -> dict:
    """One JSON POST, raising on anything that is not a 2xx. The default
    transport, which a test replaces."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def error_code(exc) -> str:
    """The Identity Toolkit's `error.message` for a failure, or '' when the
    body does not parse."""
    read = getattr(exc, "read", None)
    if read is None:
        return ""
    try:
        return str(json.loads(read()).get("error", {}).get("message", ""))
    except (ValueError, OSError):
        return ""


class FirebaseIdentity:
    """This install's uid, and an ID token for it on demand.

    `token()` signs up on first use, refreshes ahead of expiry, and returns None
    when offline.
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

    def _load(self):
        """(uid, refresh token) from disk, or (None, None) when the file is
        missing, unreadable or incomplete."""
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
        """Write the uid and refresh token. The ID token is not kept on disk."""
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"uid": self.uid, "refresh_token": self._refresh_token,
                       "project": PROJECT}, fh, indent=2)

    def _keep(self, got: dict):
        """Take what a sign-in or a refresh returned, and remember it.

        The Identity Toolkit answers in camelCase (`idToken`), the secure token
        service in snake_case (`id_token`); both are read.
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
            # Usable for this run; the next run signs up again as a new install.
            pass
        return self._id_token

    def _sign_in(self) -> dict:
        """A new anonymous user in the project. The only method a different
        sign-in method would replace."""
        return self._post(SIGNUP_URL + "?key=" + self.api_key,
                          {"returnSecureToken": True}, self.timeout)

    def _refresh(self) -> dict:
        return self._post(REFRESH_URL + "?key=" + self.api_key,
                          {"grant_type": "refresh_token",
                           "refresh_token": self._refresh_token},
                          self.timeout)

    def token(self):
        """An ID token, or None. Takes no arguments, so the method itself can be
        passed as `HttpTurnStore(base, token=...)`."""
        if self._id_token and self._clock() < self._expires_at - REFRESH_MARGIN:
            return self._id_token
        if self._refresh_token:
            try:
                return self._keep(self._refresh())
            except urllib.error.HTTPError as exc:
                if error_code(exc) not in DEAD_REFRESH:
                    return None
                # The user behind this refresh token is gone: sign up again.
                self.uid = self._refresh_token = None
                self._id_token = None
            except (urllib.error.URLError, socket.timeout, OSError, ValueError):
                return None
        try:
            return self._keep(self._sign_in())
        except (urllib.error.HTTPError, urllib.error.URLError, socket.timeout,
                OSError, ValueError):
            # Offline, or the project is not answering: the caller sends no header.
            return None

    def forget(self) -> None:
        """Drop this install's identity, in memory and on disk."""
        self.uid = self._refresh_token = self._id_token = None
        self._expires_at = 0.0
        try:
            os.remove(self.path)
        except OSError:
            pass

    def delete(self) -> bool:
        """Remove this anonymous user from the project (for tests). True if it is gone."""
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
    """This install's identity, cached per data directory per process.
    Passing any keyword builds a fresh one."""
    key = os.path.abspath(data_dir)
    if kw or key not in _identities:
        _identities[key] = FirebaseIdentity(data_dir, **kw)
    return _identities[key]
