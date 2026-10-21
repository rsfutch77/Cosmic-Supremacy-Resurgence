"""
fb_auth.py: the launcher's Firebase identity

    import fb_auth
    me = fb_auth.identity(data_dir)
    store = turn_store.open_store(spec, token=me.token)

    me.create_account(email, password)    # the guest becomes an account, same uid
    me.log_in(email, password)            # an existing account, its own uid
    me.log_in_google(fb_auth.google_id_token(secret))   # either, by the Google account
    me.log_out()                          # back to this install's guest

An install plays as one of two Firebase users:
  * its guest, an anonymous user minted on first use and kept in
    fb_identity.json. This is "play by name".
  * an account, logged in with an email and password or with Google and kept
    in fb_account.json, with the refresh token sealed to the Windows user.

Creating an account links the email to the guest, so the uid and every seat
it holds carry over, and the guest file is removed. A Google account new to
the project is linked the same way. Logging in to an account that already
exists leaves the guest file alone, and logging out returns to it.

`token` takes no arguments and returns a `str` (sent as `Authorization: Bearer
<token>`) or `None` (the request goes out bare). The uid is never shown to the
player, and is separate from the player's name in `identity.json`.

The launcher holds no Google Cloud credential. It speaks HTTPS to the public
Identity Toolkit endpoints, and the relay decides what the uid may do.
`WEB_API_KEY` is a public Firebase Web API key: it names the project and
authorises nothing.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

# The beta's Firebase project and its public Web API key.
PROJECT = "cs-resurgence"
WEB_API_KEY = "AIzaSyC4gdHRgtkTBee9P45xNzP6M9zE51VPUgI"

TOOLKIT = "https://identitytoolkit.googleapis.com/v1/"
SIGNUP_URL = TOOLKIT + "accounts:signUp"
REFRESH_URL = "https://securetoken.googleapis.com/v1/token"
DELETE_URL = TOOLKIT + "accounts:delete"
UPDATE_URL = TOOLKIT + "accounts:update"            # links an email to the guest
PASSWORD_URL = TOOLKIT + "accounts:signInWithPassword"
OOB_URL = TOOLKIT + "accounts:sendOobCode"          # reset and verification emails
LOOKUP_URL = TOOLKIT + "accounts:lookup"
IDP_URL = TOOLKIT + "accounts:signInWithIdp"

# The launcher's Google OAuth client, of the Desktop app type. Its secret is
# not in the repository: the caller passes it, read from google_client.json.
GOOGLE_CLIENT_ID = ("520820100135-tu8j8tlke98jruudr2n7st0nb2c0028h"
                    ".apps.googleusercontent.com")
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_SCOPES = "openid email profile"
GOOGLE_WAIT = 300.0     # seconds the browser has to come back

# signInWithIdp refusals meaning the Google login belongs to an account
# already, so it is logged in to rather than linked to the guest.
LINK_TAKEN = ("FEDERATED_USER_ID_ALREADY_LINKED", "EMAIL_EXISTS")

STATE_FILE = "fb_identity.json"     # the guest, beside identity.json in the data directory
ACCOUNT_FILE = "fb_account.json"    # the logged-in account, when there is one

REFRESH_MARGIN = 300.0   # seconds before the one-hour ID token expiry to renew it

TIMEOUT = 15.0

# The project's password policy, checked here before anything is sent.
PASSWORD_MIN = 8
PASSWORD_RULES = (f"At least {PASSWORD_MIN} characters, with an upper case letter, "
                  "a lower case letter and a digit.")

# Identity Toolkit errors meaning the refresh token is dead and a new identity
# is needed. Any other error is a failure of this attempt only.
DEAD_REFRESH = ("TOKEN_EXPIRED", "USER_DISABLED", "USER_NOT_FOUND",
                "INVALID_REFRESH_TOKEN")

# What the player reads for each Identity Toolkit refusal.
SAID = {
    "EMAIL_EXISTS": "An account already uses that email. Log in instead.",
    "INVALID_EMAIL": "That is not an email address.",
    "MISSING_EMAIL": "Enter your email address.",
    "MISSING_PASSWORD": "Enter your password.",
    "INVALID_LOGIN_CREDENTIALS": "That email and password do not match an account.",
    "EMAIL_NOT_FOUND": "That email and password do not match an account.",
    "INVALID_PASSWORD": "That email and password do not match an account.",
    "USER_DISABLED": "This account is disabled. Contact the operator.",
    "TOO_MANY_ATTEMPTS_TRY_LATER": "Too many tries. Wait a few minutes and try again.",
    "OPERATION_NOT_ALLOWED": "Accounts are not open yet.",
    "ADMIN_ONLY_OPERATION": "Accounts are not open yet.",
    "OFFLINE": "The login service cannot be reached. Check your connection.",
    "LOGGED_IN": "You are already logged in. Log out first.",
    "CANCELLED": "Logging in was cancelled.",
    "GOOGLE_REFUSED": "Google did not log you in. Try again.",
    "GOOGLE_LATE": "Google did not answer in time. Try again.",
}

# Refusals whose detail after " : " is meant for the player.
DETAILED = ("WEAK_PASSWORD", "PASSWORD_DOES_NOT_MEET_REQUIREMENTS")

# Every failure of a call, refusals included: HTTPError is a URLError.
NETWORK_ERRORS = (urllib.error.URLError, socket.timeout, OSError, ValueError)


class AuthError(Exception):
    """A refused account operation. `code` is the Identity Toolkit's, or one of
    ours in SAID; `str()` is what the player reads."""

    def __init__(self, code: str, text: str = ""):
        self.code = code
        super().__init__(text or SAID.get(code) or f"Login failed ({code}).")


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


def refusal(exc) -> AuthError:
    """The AuthError for a failed call. The message reads `CODE` or
    `CODE : detail`."""
    if not isinstance(exc, urllib.error.HTTPError):
        return AuthError("OFFLINE")
    code, _, detail = error_code(exc).partition(" : ")
    code = code.strip() or f"HTTP_{exc.code}"
    if code in DETAILED and detail.strip():
        return AuthError(code, detail.strip())
    return AuthError(code)


def check_credentials(email: str, password: str, new: bool = False):
    """(email, password) trimmed, or an AuthError before anything is sent."""
    email = (email or "").strip()
    if not email:
        raise AuthError("MISSING_EMAIL")
    if "@" not in email:
        raise AuthError("INVALID_EMAIL")
    if not password:
        raise AuthError("MISSING_PASSWORD")
    if new:
        missing = password_missing(password)
        if missing:
            raise AuthError("WEAK_PASSWORD", "Your password needs " + missing + ".")
    return email, password


def password_missing(password: str) -> str:
    """What `password` lacks under PASSWORD_RULES, as a phrase, or ''."""
    lacks = []
    if len(password) < PASSWORD_MIN:
        lacks.append(f"at least {PASSWORD_MIN} characters")
    if not any(c.isupper() for c in password):
        lacks.append("an upper case letter")
    if not any(c.islower() for c in password):
        lacks.append("a lower case letter")
    if not any(c.isdigit() for c in password):
        lacks.append("a digit")
    if len(lacks) > 1:
        return ", ".join(lacks[:-1]) + " and " + lacks[-1]
    return "".join(lacks)


def post_form(url: str, fields: dict, timeout: float = TIMEOUT) -> dict:
    """One form POST answered in JSON, as Google's token endpoint wants."""
    body = urllib.parse.urlencode(fields).encode("ascii")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# What the browser tab shows once Google sends the player back.
BACK_PAGE = ("<!doctype html><meta charset='utf-8'><title>Cosmic Supremacy: "
             "Resurgence</title><body style='font-family:sans-serif;"
             "background:#0a1018;color:#e8eef2;text-align:center;padding-top:20vh'>"
             "<h2>{}</h2><p>You can close this tab and return to the launcher.</p>")


def google_id_token(client_secret: str, cancel=None, open_browser=None,
                    transport=post_form, wait: float = GOOGLE_WAIT) -> str:
    """Log in with Google in the player's browser and return Google's ID token.

    Google sends the browser back to a listener on 127.0.0.1 at a port of the
    system's choosing. The request carries a PKCE challenge and a state value,
    so a code taken by another program is useless and a stray request is
    ignored. Raises AuthError: CANCELLED once `cancel` is set, GOOGLE_LATE when
    the browser does not come back within `wait`, GOOGLE_REFUSED when the
    player declines.
    """
    if open_browser is None:
        import webbrowser
        open_browser = webbrowser.open
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    state = secrets.token_urlsafe(24)
    back = {}

    class Back(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if query.get("state", [""])[0] != state:
                self.send_error(404)
                return
            back.update({k: v[0] for k, v in query.items()})
            said = "You are logged in." if "code" in back else "You were not logged in."
            page = BACK_PAGE.format(said).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Back)
    server.timeout = 0.25
    redirect = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        open_browser(GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode({
            "client_id": GOOGLE_CLIENT_ID, "redirect_uri": redirect,
            "response_type": "code", "scope": GOOGLE_SCOPES,
            "code_challenge": challenge, "code_challenge_method": "S256",
            "state": state, "prompt": "select_account"}))
        deadline = time.monotonic() + wait
        while not back:
            if cancel is not None and cancel.is_set():
                raise AuthError("CANCELLED")
            if time.monotonic() > deadline:
                raise AuthError("GOOGLE_LATE")
            server.handle_request()
    finally:
        server.server_close()
    if "code" not in back:
        raise AuthError("GOOGLE_REFUSED")
    try:
        tokens = transport(GOOGLE_TOKEN_URL, {
            "code": back["code"], "client_id": GOOGLE_CLIENT_ID,
            "client_secret": client_secret, "redirect_uri": redirect,
            "grant_type": "authorization_code", "code_verifier": verifier})
    except urllib.error.HTTPError:
        raise AuthError("GOOGLE_REFUSED")
    except NETWORK_ERRORS:
        raise AuthError("OFFLINE")
    if not tokens.get("id_token"):
        raise AuthError("GOOGLE_REFUSED")
    return tokens["id_token"]


# The refresh token of an account is sealed with DPAPI, so a copied data
# directory does not log anyone in. A guest's is kept plain, so copying the
# data directory into a new install carries the guest and its seats.
DPAPI_ENTROPY = b"cs-resurgence fb_account"


def _dpapi(data: bytes, seal: bool) -> bytes:
    """CryptProtectData or CryptUnprotectData, for the current Windows user."""
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(raw: bytes) -> Blob:
        buf = ctypes.create_string_buffer(raw, len(raw))
        return Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    src, extra, out = blob(data), blob(DPAPI_ENTROPY), Blob()
    call = crypt32.CryptProtectData if seal else crypt32.CryptUnprotectData
    if not call(ctypes.byref(src), None, ctypes.byref(extra), None, None,
                0x1, ctypes.byref(out)):              # CRYPTPROTECT_UI_FORBIDDEN
        raise OSError("DPAPI refused")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def seal(secret: str) -> dict:
    """The fields that hold `secret` in an account file."""
    if sys.platform != "win32":
        return {"refresh_token": secret}
    sealed = _dpapi(secret.encode("utf-8"), True)
    return {"refresh_token_dpapi": base64.b64encode(sealed).decode("ascii")}


def unseal(stored: dict):
    """The refresh token out of an account file's fields, or None."""
    if isinstance(stored.get("refresh_token"), str):
        return stored["refresh_token"]
    sealed = stored.get("refresh_token_dpapi")
    if not isinstance(sealed, str) or sys.platform != "win32":
        return None
    try:
        return _dpapi(base64.b64decode(sealed), False).decode("utf-8")
    except (OSError, ValueError):
        return None             # another Windows user, or another machine


class FirebaseIdentity:
    """This install's uid, and an ID token for it on demand.

    `token()` signs up a guest on first use, refreshes ahead of expiry, and
    returns None when offline. `account` is None for the guest, otherwise
    {"email", "provider"}. `dropped` is set when an account's login died and
    the install went back to its guest; the launcher clears it once told.
    """

    def __init__(self, data_dir: str, api_key: str = WEB_API_KEY,
                 timeout: float = TIMEOUT, transport=post_json,
                 clock=time.time, emulator: "str | None" = None):
        self.path = os.path.join(data_dir, STATE_FILE)
        self.account_path = os.path.join(data_dir, ACCOUNT_FILE)
        self.api_key = api_key
        self.timeout = timeout
        self.emulator = emulator    # host:port of an Auth emulator, for tests
        self._post = transport
        self._clock = clock
        self._lock = threading.RLock()
        self._id_token = None
        self._expires_at = 0.0
        self.dropped = False
        self.account = self._load_account()
        if self.account is None:
            self.uid, self._refresh_token = self._load()

    def _url(self, url: str) -> str:
        if self.emulator:
            url = url.replace("https://", f"http://{self.emulator}/", 1)
        return url + "?key=" + self.api_key

    def _call(self, url: str, payload: dict) -> dict:
        return self._post(self._url(url), payload, self.timeout)

    def _load(self):
        """The guest's (uid, refresh token) from disk, or (None, None) when the
        file is missing, unreadable or incomplete."""
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

    def _load_account(self):
        """The account from disk, setting uid and refresh token, or None when
        there is none this Windows user can read."""
        try:
            with open(self.account_path, encoding="utf-8") as fh:
                stored = json.load(fh)
        except (OSError, ValueError):
            return None
        if not isinstance(stored, dict):
            return None
        uid, refresh = stored.get("uid"), unseal(stored)
        if not isinstance(uid, str) or not uid or not refresh:
            return None
        self.uid, self._refresh_token = uid, refresh
        return {"email": str(stored.get("email") or ""),
                "provider": str(stored.get("provider") or "password")}

    def _save(self) -> None:
        """Write the uid and refresh token to the guest or the account file.
        The ID token is not kept on disk."""
        if self.account is None:
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump({"uid": self.uid, "refresh_token": self._refresh_token,
                           "project": PROJECT}, fh, indent=2)
            return
        record = {"uid": self.uid, "email": self.account["email"],
                  "provider": self.account["provider"], "project": PROJECT}
        record.update(seal(self._refresh_token))
        with open(self.account_path, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2)

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
            # Usable for this run; the next run signs up again or asks for a login.
            pass
        return self._id_token

    def _sign_in(self) -> dict:
        """A new anonymous user in the project: this install's guest."""
        return self._call(SIGNUP_URL, {"returnSecureToken": True})

    def _refresh(self) -> dict:
        return self._call(REFRESH_URL, {"grant_type": "refresh_token",
                                        "refresh_token": self._refresh_token})

    def token(self):
        """An ID token, or None. Takes no arguments, so the method itself can be
        passed as `HttpTurnStore(base, token=...)`."""
        with self._lock:
            if self._id_token and self._clock() < self._expires_at - REFRESH_MARGIN:
                return self._id_token
            if self._refresh_token:
                try:
                    return self._keep(self._refresh())
                except urllib.error.HTTPError as exc:
                    if error_code(exc) not in DEAD_REFRESH:
                        return None
                    if self.account is not None:
                        # The account's login is gone: back to the guest, and say so.
                        self._drop_account()
                        self.dropped = True
                        if self._refresh_token:
                            return self.token()
                    # The user behind this refresh token is gone: sign up again.
                    self.uid = self._refresh_token = None
                    self._id_token = None
                except NETWORK_ERRORS:
                    return None
            try:
                return self._keep(self._sign_in())
            except NETWORK_ERRORS:
                # Offline, or the project is not answering: the caller sends no header.
                return None

    def _drop_account(self) -> None:
        """Forget the account on disk and in memory, and load the guest."""
        try:
            os.remove(self.account_path)
        except OSError:
            pass
        self.account = None
        self._id_token = None
        self._expires_at = 0.0
        self.uid, self._refresh_token = self._load()

    def _become(self, got: dict, email: str, provider: str) -> dict:
        self.account = {"email": email, "provider": provider}
        self.dropped = False
        self._keep(got)
        return dict(self.account)

    def create_account(self, email: str, password: str) -> dict:
        """Link an email and password to this install's guest, which becomes
        the account with the same uid. Sends the verification email. Returns
        `account`, or raises AuthError."""
        email, password = check_credentials(email, password, new=True)
        with self._lock:
            if self.account is not None:
                raise AuthError("LOGGED_IN")
            guest = self.token()
            if not guest:
                raise AuthError("OFFLINE")
            try:
                got = self._call(UPDATE_URL, {"idToken": guest, "email": email,
                                              "password": password,
                                              "returnSecureToken": True})
            except NETWORK_ERRORS as exc:
                raise refusal(exc)
            account = self._become(got, got.get("email") or email, "password")
            try:
                os.remove(self.path)        # the guest is the account now
            except OSError:
                pass
        self.send_verification()
        return account

    def log_in(self, email: str, password: str) -> dict:
        """Log in to an account that already exists. The guest is kept for
        log_out. Returns `account`, or raises AuthError."""
        email, password = check_credentials(email, password)
        with self._lock:
            if self.account is not None:
                raise AuthError("LOGGED_IN")
            try:
                got = self._call(PASSWORD_URL, {"email": email, "password": password,
                                                "returnSecureToken": True})
            except NETWORK_ERRORS as exc:
                raise refusal(exc)
            return self._become(got, got.get("email") or email, "password")

    def log_in_google(self, google_token: str) -> dict:
        """Log in with a Google ID token. A Google account new to the project
        is linked to the guest, which becomes the account with the same uid;
        one that already has an account logs in to it, and the guest is kept.
        Returns `account` with "linked" saying which. Raises AuthError."""
        payload = {"postBody": urllib.parse.urlencode(
                       {"id_token": google_token, "providerId": "google.com"}),
                   "requestUri": "http://localhost", "returnSecureToken": True}
        with self._lock:
            if self.account is not None:
                raise AuthError("LOGGED_IN")
            guest, got = self.token(), None
            if guest:
                try:
                    got = self._call(IDP_URL, dict(payload, idToken=guest))
                except NETWORK_ERRORS as exc:
                    refused = refusal(exc)
                    if refused.code not in LINK_TAKEN:
                        raise refused
            linked = got is not None
            if not linked:
                try:
                    got = self._call(IDP_URL, payload)
                except NETWORK_ERRORS as exc:
                    raise refusal(exc)
            if got.get("needConfirmation") or not got.get("idToken"):
                email = got.get("email") or "that email"
                raise AuthError("NEEDS_PASSWORD",
                                f"An account already uses {email} with a "
                                "password. Log in with your password.")
            account = self._become(got, got.get("email") or "", "google.com")
            if linked:
                try:
                    os.remove(self.path)        # the guest is the account now
                except OSError:
                    pass
        return dict(account, linked=linked)

    def log_out(self) -> None:
        """Forget the account on this install and play as the guest again.
        After a create_account there is no guest, so the next token() mints one."""
        with self._lock:
            if self.account is not None:
                self._drop_account()

    def send_password_reset(self, email: str) -> None:
        """Ask Firebase to email a password reset link. Raises AuthError."""
        email = (email or "").strip()
        if not email:
            raise AuthError("MISSING_EMAIL")
        try:
            self._call(OOB_URL, {"requestType": "PASSWORD_RESET", "email": email})
        except NETWORK_ERRORS as exc:
            raise refusal(exc)

    def send_verification(self) -> bool:
        """Ask Firebase to email the account a verification link. True if sent."""
        token = self.token() if self.account is not None else None
        if not token:
            return False
        try:
            self._call(OOB_URL, {"requestType": "VERIFY_EMAIL", "idToken": token})
        except NETWORK_ERRORS:
            return False
        return True

    def lookup(self):
        """The account as Firebase holds it: {"email", "verified", "name"}, or
        None for the guest or when offline. `name` is the player's name as
        set_display_name stored it, never a name a provider such as Google
        filled in."""
        token = self.token() if self.account is not None else None
        if not token:
            return None
        try:
            users = self._call(LOOKUP_URL, {"idToken": token}).get("users") or []
        except NETWORK_ERRORS:
            return None
        if not users:
            return None
        user = users[0]
        # Firebase copies the account's name into its password entry, and fills
        # it in from another provider's profile, such as Google's.
        filled = [p.get("displayName") for p in user.get("providerUserInfo") or []
                  if p.get("providerId") != "password"]
        name = user.get("displayName") or None
        if name in filled:
            name = None
        return {"email": user.get("email") or "",
                "verified": bool(user.get("emailVerified")),
                "name": name}

    def set_display_name(self, name: str) -> bool:
        """Store the player's name on the account, so another install that
        logs in finds it. True if stored."""
        token = self.token() if self.account is not None else None
        if not token:
            return False
        try:
            self._call(UPDATE_URL, {"idToken": token, "displayName": name,
                                    "returnSecureToken": False})
        except NETWORK_ERRORS:
            return False
        return True

    def forget(self) -> None:
        """Drop this install's identity, guest and account, in memory and on disk."""
        with self._lock:
            self.uid = self._refresh_token = self._id_token = None
            self.account = None
            self._expires_at = 0.0
            for path in (self.path, self.account_path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def delete(self) -> bool:
        """Remove the current user from the project (for tests). True if it is gone."""
        token = self.token()
        if not token:
            return False
        try:
            self._call(DELETE_URL, {"idToken": token})
        except NETWORK_ERRORS:
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
