"""
test_http_store_hosts.py: the HTTP store talks only to its own host and Storage
================================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_http_store_hosts.py

`HttpTurnStore` follows an upload ticket's `url` and the relay's redirect from a
blob route to a signed Cloud Storage URL. Both come from the relay, so a relay
that has been tampered with could otherwise send a player's orders to any host
or serve a turn from one. Only the store's own host and `TRUSTED_BLOB_HOSTS`
over https are allowed.
"""
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, 'server'))

import turn_store as ts

PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


RELAY = 'https://relay-r5t6py5oxa-uw.a.run.app/h3check'
LAN = 'http://192.168.1.20:8899'
SIGNED = ('https://storage.googleapis.com/cs-resurgence.firebasestorage.app/'
          'beta/h3check/turns/0040.b64?X-Goog-Signature=abc')


def refuses(store, url):
    try:
        store._url(url)
        return False
    except ts.UntrustedURL:
        return True


def main():
    relay = ts.HttpTurnStore(RELAY)
    lan = ts.HttpTurnStore(LAN)

    print('1. tickets the relay really hands out are followed')
    check('a path on the relay', relay._url('/submission/40/Alice'), RELAY + '/submission/40/Alice')
    check('an absolute URL on the relay', relay._url(RELAY + '/x'), RELAY + '/x')
    check('a signed Storage URL', relay._url(SIGNED), SIGNED)
    check('turn_server.py on a LAN, over http', lan._url(LAN + '/turn/3'), LAN + '/turn/3')

    print('\n2. anything else is refused')
    check('another https host', refuses(relay, 'https://evil.example/upload'))
    check('Storage over plain http', refuses(relay, SIGNED.replace('https', 'http', 1)))
    check('a lookalike host', refuses(relay, 'https://storage.googleapis.com.evil.example/x'))
    check('the metadata address', refuses(relay, 'http://169.254.169.254/computeMetadata/v1/'))
    check('another host from a LAN store', refuses(lan, 'http://192.168.1.21:8899/x'))

    print('\n3. a redirect is held to the same rule')
    handler = ts._drop_auth_on_redirect(RELAY)()
    req = urllib.request.Request(RELAY + '/turn/40', headers={'Authorization': 'Bearer t'})
    new = handler.redirect_request(req, None, 302, 'Found', {}, SIGNED)
    check('a redirect to Storage is followed', new is not None and new.full_url == SIGNED)
    check('and carries no token', 'Authorization' not in (new.headers if new else {}))
    try:
        handler.redirect_request(req, None, 302, 'Found', {}, 'https://evil.example/t')
        refused = False
    except ts.UntrustedURL:
        refused = True
    check('a redirect to another host is refused', refused)

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
