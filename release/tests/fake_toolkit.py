"""
A stand-in for the Identity Toolkit and the secure token service, for tests of
fb_auth and of the launcher's account dialogs that run offline.

`FakeToolkit` is passed as fb_auth's `transport`; `Clock` as its `clock`.
"""
import io
import json
import urllib.error
import urllib.parse

import fb_auth


class FakeToolkit:
    """The endpoints, answering the way the real ones do.

    signUp answers in camel case and the secure token service in snake case.
    `calls` records what was asked for, `emails` what was sent.

    A Google ID token here is "<subject>|<email>", and the Google profile's
    name is "Google <subject>". An email in `password_emails` answers a
    Google sign-in with needConfirmation, as for an unverified email.
    """

    def __init__(self):
        self.calls = []
        self.users = {}
        self.n = 0
        self.offline = False
        self.dead_refresh = False
        self.tokens = {}        # ID token -> uid
        self.accounts = {}      # email -> (uid, password)
        self.names = {}         # uid -> display name
        self.emails = []        # (request type, email or uid)
        self.google = {}        # Google subject -> uid
        self.password_emails = set()
        self.verified = set()   # uids whose email is verified

    def _issue(self, uid):
        """A fresh login for `uid`, in the Identity Toolkit's camel case."""
        self.n += 1
        token = f"id{self.n}.{uid}"
        self.tokens[token] = uid
        self.users[uid] = f"refresh{self.n}"
        return {"localId": uid, "idToken": token,
                "refreshToken": f"refresh{self.n}", "expiresIn": "3600"}

    def _refuse(self, url, code):
        raise urllib.error.HTTPError(
            url, 400, "Bad Request", {},
            _body(json.dumps({"error": {"message": code}})))

    def __call__(self, url, payload, timeout=None):
        self.calls.append(url.split("/")[-1].split("?")[0])
        if self.offline:
            raise urllib.error.URLError("getaddrinfo failed")
        if url.startswith(fb_auth.SIGNUP_URL):
            self.n += 1
            uid = f"uid{self.n}"
            self.users[uid] = f"refresh{self.n}"
            self.tokens[f"id{self.n}.a"] = uid
            return {"localId": uid, "idToken": f"id{self.n}.a",
                    "refreshToken": f"refresh{self.n}", "expiresIn": "3600"}
        if url.startswith(fb_auth.REFRESH_URL):
            uid = next((u for u, r in self.users.items()
                        if r == payload["refresh_token"]), None)
            if self.dead_refresh in (True, uid):    # every user, or just this one
                raise urllib.error.HTTPError(
                    url, 400, "Bad Request", {},
                    _body(json.dumps({"error": {"message": "USER_NOT_FOUND"}})))
            if uid is None:
                raise urllib.error.HTTPError(
                    url, 400, "Bad Request", {},
                    _body(json.dumps(
                        {"error": {"message": "INVALID_REFRESH_TOKEN"}})))
            self.tokens[f"{uid}.fresh"] = uid
            return {"user_id": uid, "id_token": f"{uid}.fresh",
                    "refresh_token": payload["refresh_token"],
                    "expires_in": "3600"}
        if url.startswith(fb_auth.UPDATE_URL):
            uid = self.tokens[payload["idToken"]]
            if "displayName" in payload:
                self.names[uid] = payload["displayName"]
                return {"localId": uid}
            if payload["email"] in self.accounts:
                self._refuse(url, "EMAIL_EXISTS")
            self.accounts[payload["email"]] = (uid, payload["password"])
            return dict(self._issue(uid), email=payload["email"])
        if url.startswith(fb_auth.PASSWORD_URL):
            uid, password = self.accounts.get(payload["email"], (None, None))
            if uid is None or password != payload["password"]:
                self._refuse(url, "INVALID_LOGIN_CREDENTIALS")
            return dict(self._issue(uid), email=payload["email"])
        if url.startswith(fb_auth.OOB_URL):
            who = payload.get("email") or self.tokens[payload["idToken"]]
            self.emails.append((payload["requestType"], who))
            return {}
        if url.startswith(fb_auth.LOOKUP_URL):
            uid = self.tokens[payload["idToken"]]
            email = next((e for e, (u, _p) in self.accounts.items() if u == uid), "")
            google = [{"providerId": "google.com", "displayName": "Google " + s}
                      for s, u in self.google.items() if u == uid]
            return {"users": [{"localId": uid, "email": email,
                               "emailVerified": uid in self.verified,
                               "displayName": self.names.get(uid),
                               "providerUserInfo": google}]}
        if url.startswith(fb_auth.IDP_URL):
            post = urllib.parse.parse_qs(payload["postBody"])
            sub, _, email = post["id_token"][0].partition("|")
            if email in self.password_emails:
                return {"needConfirmation": True, "email": email}
            if "idToken" in payload:
                if sub in self.google:
                    self._refuse(url, "FEDERATED_USER_ID_ALREADY_LINKED")
                uid = self.tokens[payload["idToken"]]
            else:
                uid = self.google.get(sub)
                if uid is None:
                    self.n += 1
                    uid = f"uid{self.n}"
            self.google.setdefault(sub, uid)
            self.names.setdefault(uid, "Google " + sub)
            return dict(self._issue(uid), email=email)
        if url.startswith(fb_auth.DELETE_URL):
            self.users = {u: r for u, r in self.users.items()
                          if not payload["idToken"].startswith(u)}
            return {}
        raise AssertionError("unexpected endpoint " + url)


def _body(text):
    return io.BytesIO(text.encode("utf-8"))


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t
