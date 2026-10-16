"""
bypass_check.py: does a client that goes around the relay reach anything?
==========================================================================
    python bypass_check.py                         # cs-resurgence, h3check
    python bypass_check.py --galaxy verify
    python bypass_check.py --project P --bucket B --galaxy G

The second half of plan item H4. The relay's own refusals are measured by
`test_relay_function.py` and J4; this is the other door. Every launcher reaches
the store through the relay, and the deployed Security Rules exist so that a
launcher, or anything that has read the launcher's source, cannot reach
Firestore or the bucket by speaking to Google directly with the same identity.

So this signs in anonymously the way a launcher does, in a scratch directory
and never with a real install's `fb_identity.json`, and then skips the relay:

    Firestore REST     list the galaxy collection, get the galaxy document and
                       an archived turn, create, patch and delete a document
    Firebase Storage   list, download a real turn object, upload, delete
    GCS JSON API       the same four, with the Firebase ID token as bearer

each with that token, and each again with no token at all. Reads aim at real
paths, because a read of something that does not exist proves nothing. Writes
and deletes aim only at a throwaway path under `bypass_probe_<date>_<run>`
that nothing reads and nothing writes, so a probe that unexpectedly succeeds
costs the galaxy nothing and the harness removes what it made with the
operator's own credentials.

A delete aimed at nothing proves nothing either: the GCS JSON API answers an
anonymous delete of a missing object with 404 before it answers who is asking.
So the harness first plants one document and one object under the throwaway
prefix with the operator's credentials, aims the update and delete probes at
those as well, reads back that both survived, and removes them at the end
along with anything else the run left under its prefix.

Before any probe, the live rules are read back through the Rules API and
printed, so the run records what was actually deployed rather than what the
repository's draft files say. A probe is ALLOWED on any 2xx. Everything else
is a refusal, and the status is printed so a 404 where a 403 was expected is
visible rather than scored as a pass.

The anonymous user this makes is deleted at the end, through the Identity
Toolkit's admin endpoint with the operator's default credentials, even when a
probe failed, and the delete is read back. Exit status is the number of probes
allowed, so zero means the door is shut. This touches the live project and is
run under the `firebase-live` lease.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import secrets
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.dirname(HERE)
for _d in (SERVER, HERE):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import fb_auth                                                  # noqa: E402

RULES_API = 'https://firebaserules.googleapis.com/v1'
FIRESTORE = 'https://firestore.googleapis.com/v1'
FB_STORAGE = 'https://firebasestorage.googleapis.com/v0'
GCS = 'https://storage.googleapis.com'
IDENTITY_TOOLKIT = 'https://identitytoolkit.googleapis.com/v1'
ADMIN_SCOPE = 'https://www.googleapis.com/auth/cloud-platform'

# The collection firebase_store.py keeps galaxies in, and the subcollection
# and object folder a published turn lands in. Read probes name these.
COLLECTION = 'beta'
ARCHIVE = 'archive'
TURNS = 'turns'

TIMEOUT = 30.0


# ── one request, scored ─────────────────────────────────────────────────────
def call(method, url, headers=None, body=None, content_type=None):
    """(status, text) for one request. Never raises on an HTTP status."""
    data = None
    headers = dict(headers or {})
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        else:
            data = body
            headers['Content-Type'] = content_type or 'text/plain'
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode('utf-8', 'replace')


def reason(text):
    """The short message inside an error body, for the one line per probe."""
    try:
        parsed = json.loads(text)
        # runQuery answers with a list of one error, the rest with a dict.
        if isinstance(parsed, list) and parsed:
            parsed = parsed[0]
        err = parsed.get('error')
    except (ValueError, AttributeError):
        return text.strip().replace('\n', ' ')[:80]
    if isinstance(err, dict):
        return str(err.get('message') or err.get('status') or '')[:80]
    return str(err)[:80]


class Probes:
    def __init__(self, out):
        self.out = out
        self.allowed = []
        self.refused = 0

    def check(self, who, name, still_there):
        """Score a planted path by whether it is still there afterwards."""
        verdict = 'survived' if still_there else 'ALLOWED, gone'
        print(f'  {who:9} {name:38} ---  {verdict}', file=self.out)
        if still_there:
            self.refused += 1
        else:
            self.allowed.append((who, name, 'read back', ''))

    def run(self, who, name, method, url, headers=None, body=None,
            content_type=None):
        status, text = call(method, url, headers, body, content_type)
        ok = 200 <= status < 300
        verdict = 'ALLOWED' if ok else 'refused'
        note = '' if ok else '  ' + reason(text)
        print(f'  {who:9} {name:38} {status}  {verdict}{note}', file=self.out)
        if ok:
            self.allowed.append((who, name, method, url))
        else:
            self.refused += 1
        return ok


# ── the operator's credentials, for reading rules and for cleaning up ───────
def admin_headers(project):
    import google.auth
    import google.auth.transport.requests
    creds, _ = google.auth.default(scopes=[ADMIN_SCOPE])
    if hasattr(creds, 'with_quota_project'):
        creds = creds.with_quota_project(project)
    creds.refresh(google.auth.transport.requests.Request())
    return {'Authorization': f'Bearer {creds.token}',
            'x-goog-user-project': project}


def show_rules(project, bucket, admin, out):
    """Print every live rules release and its source, as deployed."""
    status, text = call('GET', f'{RULES_API}/projects/{project}/releases',
                        admin)
    if status != 200:
        print(f'rules: could not list releases: {status} {reason(text)}',
              file=out)
        return False
    releases = json.loads(text).get('releases') or []
    wanted = {'cloud.firestore', f'firebase.storage/{bucket}'}
    seen = set()
    for rel in releases:
        short = rel['name'].split('/releases/', 1)[1]
        seen.add(short)
        print(f'rules: {short}  updated {rel.get("updateTime")}', file=out)
        status, text = call('GET', f'{RULES_API}/{rel["rulesetName"]}', admin)
        if status != 200:
            print(f'  ruleset unreadable: {status} {reason(text)}', file=out)
            continue
        for f in json.loads(text).get('source', {}).get('files', []):
            lines = [ln for ln in f.get('content', '').splitlines()
                     if ln.strip() and not ln.strip().startswith('//')]
            print(f'  {f.get("name")}:', file=out)
            for ln in lines:
                print(f'    {ln}', file=out)
    for missing in sorted(wanted - seen):
        print(f'rules: NO RELEASE for {missing}, so its service runs on '
              f'whatever default Firebase applies', file=out)
    return wanted <= seen


# ── the anonymous identity, made and unmade ─────────────────────────────────
def sign_in(scratch):
    ident = fb_auth.FirebaseIdentity(scratch)
    token = ident.token()
    if not token:
        raise SystemExit('anonymous sign-in failed; is the network up?')
    return ident, token


def delete_user(project, uid, admin, out):
    """Admin delete of the anonymous user, then a lookup to confirm."""
    base = f'{IDENTITY_TOOLKIT}/projects/{project}/accounts'
    status, text = call('POST', f'{base}:delete', admin, {'localId': uid})
    if status != 200:
        print(f'cleanup: admin delete of {uid} failed: {status} '
              f'{reason(text)}', file=out)
        return False
    status, text = call('POST', f'{base}:lookup', admin, {'localId': [uid]})
    users = json.loads(text).get('users') if status == 200 else None
    gone = status == 200 and not users
    print(f'cleanup: anonymous user {uid} '
          f'{"deleted and gone" if gone else "STILL PRESENT after delete"}',
          file=out)
    return gone


# ── the probes ──────────────────────────────────────────────────────────────
def firestore_probes(p, who, headers, project, galaxy, turn_doc, scratch_doc,
                     planted_doc):
    docs = docs_url(project)
    p.run(who, f'firestore list {COLLECTION}', 'GET',
          f'{docs}/{COLLECTION}?pageSize=5', headers)
    p.run(who, f'firestore runQuery {COLLECTION}', 'POST', f'{docs}:runQuery',
          headers, {'structuredQuery': {'from': [{'collectionId': COLLECTION}],
                                        'limit': 1}})
    p.run(who, f'firestore get {COLLECTION}/{galaxy}', 'GET',
          f'{docs}/{COLLECTION}/{galaxy}', headers)
    p.run(who, f'firestore get {ARCHIVE}/{turn_doc}', 'GET',
          f'{docs}/{COLLECTION}/{galaxy}/{ARCHIVE}/{turn_doc}', headers)
    coll, doc = scratch_doc.split('/', 1)
    fields = {'fields': {'probe': {'stringValue': who}}}
    p.run(who, 'firestore create probe doc', 'POST',
          f'{docs}/{coll}?documentId={doc}', headers, fields)
    p.run(who, 'firestore patch probe doc', 'PATCH',
          f'{docs}/{scratch_doc}', headers, fields)
    p.run(who, 'firestore commit probe doc', 'POST', f'{docs}:commit', headers,
          {'writes': [{'update': {
              'name': f'projects/{project}/databases/(default)/documents/'
                      f'{scratch_doc}',
              'fields': fields['fields']}}]})
    p.run(who, 'firestore delete probe doc', 'DELETE',
          f'{docs}/{scratch_doc}', headers)
    p.run(who, 'firestore patch planted doc', 'PATCH',
          f'{docs}/{planted_doc}', headers, fields)
    p.run(who, 'firestore delete planted doc', 'DELETE',
          f'{docs}/{planted_doc}', headers)


def storage_probes(p, who, fb_headers, gcs_headers, bucket, galaxy, turn_obj,
                   scratch_obj, planted_obj):
    real = urllib.parse.quote(f'{COLLECTION}/{galaxy}/{TURNS}/{turn_obj}',
                              safe='')
    probe = urllib.parse.quote(scratch_obj, safe='')
    planted = urllib.parse.quote(planted_obj, safe='')
    b = f'{FB_STORAGE}/b/{bucket}/o'
    p.run(who, 'fb storage list turns', 'GET',
          f'{b}?prefix={urllib.parse.quote(f"{COLLECTION}/{galaxy}/{TURNS}/")}'
          f'&maxResults=5', fb_headers)
    p.run(who, f'fb storage download {turn_obj}', 'GET',
          f'{b}/{real}?alt=media', fb_headers)
    p.run(who, f'fb storage metadata {turn_obj}', 'GET', f'{b}/{real}',
          fb_headers)
    p.run(who, 'fb storage upload probe object', 'POST',
          f'{b}?name={probe}', fb_headers, b'bypass probe', 'text/plain')
    p.run(who, 'fb storage delete probe object', 'DELETE', f'{b}/{probe}',
          fb_headers)
    p.run(who, 'fb storage overwrite planted object', 'POST',
          f'{b}?name={planted}', fb_headers, b'bypass probe', 'text/plain')
    p.run(who, 'fb storage delete planted object', 'DELETE',
          f'{b}/{planted}', fb_headers)

    g = f'{GCS}/storage/v1/b/{bucket}/o'
    p.run(who, 'gcs json list turns', 'GET',
          f'{g}?prefix={urllib.parse.quote(f"{COLLECTION}/{galaxy}/{TURNS}/")}'
          f'&maxResults=5', gcs_headers)
    p.run(who, f'gcs json download {turn_obj}', 'GET',
          f'{g}/{real}?alt=media', gcs_headers)
    p.run(who, 'gcs json upload probe object', 'POST',
          f'{GCS}/upload/storage/v1/b/{bucket}/o?uploadType=media&name={probe}',
          gcs_headers, b'bypass probe', 'text/plain')
    p.run(who, 'gcs json delete probe object', 'DELETE', f'{g}/{probe}',
          gcs_headers)
    p.run(who, 'gcs json delete planted object', 'DELETE', f'{g}/{planted}',
          gcs_headers)


# ── the planted pair, and the sweep ─────────────────────────────────────────
PLANTED = b'planted by bypass_check; nothing reads this'


def docs_url(project):
    return f'{FIRESTORE}/projects/{project}/databases/(default)/documents'


def plant(project, bucket, planted_doc, planted_obj, admin):
    """Make the document and object the delete probes aim at, as admin."""
    coll, doc = planted_doc.split('/', 1)
    status, text = call('POST', f'{docs_url(project)}/{coll}?documentId={doc}',
                        admin, {'fields': {'planted': {'stringValue': 'yes'}}})
    if status != 200:
        raise SystemExit(f'could not plant {planted_doc}: {status} '
                         f'{reason(text)}')
    name = urllib.parse.quote(planted_obj, safe='')
    status, text = call('POST', f'{GCS}/upload/storage/v1/b/{bucket}/o?'
                        f'uploadType=media&name={name}', admin, PLANTED,
                        'text/plain')
    if status != 200:
        raise SystemExit(f'could not plant {planted_obj}: {status} '
                         f'{reason(text)}')


def still_there(project, bucket, planted_doc, planted_obj, admin):
    """(document survives, object survives unchanged), read as admin."""
    status, _ = call('GET', f'{docs_url(project)}/{planted_doc}', admin)
    doc_ok = status == 200
    name = urllib.parse.quote(planted_obj, safe='')
    status, text = call('GET', f'{GCS}/storage/v1/b/{bucket}/o/{name}'
                        f'?alt=media', admin)
    obj_ok = status == 200 and text.encode('utf-8') == PLANTED
    return doc_ok, obj_ok


def sweep(project, bucket, run, admin, out):
    """Remove everything under the run's prefix, planted or not, as admin.

    The planted pair is expected here. Anything else is what an allowed write
    made, and it has already been reported as ALLOWED above. True when the
    prefix reads back empty.
    """
    docs = docs_url(project)
    status, text = call('GET', f'{docs}/{run}?pageSize=100', admin)
    found = json.loads(text).get('documents', []) if status == 200 else []
    for d in found:
        path = d['name'].split('/documents/', 1)[1]
        s, t = call('DELETE', f'{docs}/{path}', admin)
        print(f'cleanup: document {path} removed'
              f'{"" if s == 200 else f": {s} {reason(t)}"}', file=out)
    prefix = urllib.parse.quote(run + '/', safe='')
    listing = (f'{GCS}/storage/v1/b/{bucket}/o?prefix={prefix}'
               f'&fields=items(name)')
    status, text = call('GET', listing, admin)
    found = json.loads(text).get('items', []) if status == 200 else []
    for o in found:
        name = urllib.parse.quote(o['name'], safe='')
        s, t = call('DELETE', f'{GCS}/storage/v1/b/{bucket}/o/{name}', admin)
        print(f'cleanup: object {o["name"]} removed'
              f'{"" if s == 204 else f": {s} {reason(t)}"}', file=out)
    status, text = call('GET', listing, admin)
    left = json.loads(text).get('items', []) if status == 200 else ['?']
    status, text = call('GET', f'{docs}/{run}?pageSize=1', admin)
    left += json.loads(text).get('documents', []) if status == 200 else ['?']
    print(f'cleanup: {run}/ reads back '
          f'{"empty" if not left else "NOT EMPTY, " + str(len(left)) + " left"}',
          file=out)
    return not left


def real_turn(bucket, galaxy, admin):
    """The lowest published turn object of the galaxy, by admin listing.

    A read probe has to name an object that exists, or a 404 would look like
    a refusal. The admin listing also shows the bucket is reachable at all,
    which is what the client probes are measured against.
    """
    prefix = urllib.parse.quote(f'{COLLECTION}/{galaxy}/{TURNS}/', safe='')
    status, text = call('GET', f'{GCS}/storage/v1/b/{bucket}/o?prefix='
                        f'{prefix}&maxResults=1&fields=items(name)', admin)
    if status != 200:
        raise SystemExit(f'admin cannot list {bucket}: {status} '
                         f'{reason(text)}')
    items = json.loads(text).get('items') or []
    if not items:
        raise SystemExit(f'no published turn under {COLLECTION}/{galaxy}/'
                         f'{TURNS}/ in {bucket}')
    return items[0]['name'].rsplit('/', 1)[1]


def main(argv=None, out=None):
    out = out or sys.stdout
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--project', default=fb_auth.PROJECT)
    ap.add_argument('--bucket', help='default <project>.firebasestorage.app')
    ap.add_argument('--galaxy', default='h3check',
                    help='a real galaxy; read probes only')
    args = ap.parse_args(argv)
    project = args.project
    bucket = args.bucket or f'{project}.firebasestorage.app'

    run = (datetime.date.today().strftime('bypass_probe_%Y%m%d') + '_' +
           secrets.token_hex(3))
    scratch_doc = f'{run}/probe'
    scratch_obj = f'{run}/probe.txt'
    planted_doc = f'{run}/planted'
    planted_obj = f'{run}/planted.txt'
    print(f'project {project}  bucket {bucket}  galaxy {COLLECTION}/{args.galaxy}'
          f'  throwaway {run}/', file=out)

    admin = admin_headers(project)
    both = show_rules(project, bucket, admin, out)
    turn_obj = real_turn(bucket, args.galaxy, admin)
    turn_doc = turn_obj.split('.', 1)[0]

    p = Probes(out)
    scratch = tempfile.mkdtemp(prefix='bypass_check_')
    ident, token = sign_in(scratch)
    print(f'signed in anonymously as {ident.uid} (scratch {scratch})',
          file=out)
    swept = False
    try:
        plant(project, bucket, planted_doc, planted_obj, admin)
        print(f'planted {planted_doc} and {planted_obj} as admin', file=out)
        for who, fs, fb, gcs in (
                ('anonymous', {'Authorization': f'Bearer {token}'},
                 {'Authorization': f'Firebase {token}'},
                 {'Authorization': f'Bearer {token}'}),
                ('no token', {}, {}, {})):
            firestore_probes(p, who, fs, project, args.galaxy, turn_doc,
                             scratch_doc, planted_doc)
            storage_probes(p, who, fb, gcs, bucket, args.galaxy, turn_obj,
                           scratch_obj, planted_obj)
            doc_ok, obj_ok = still_there(project, bucket, planted_doc,
                                         planted_obj, admin)
            p.check(who, 'planted doc read back', doc_ok)
            p.check(who, 'planted object read back unchanged', obj_ok)
    finally:
        swept = sweep(project, bucket, run, admin, out)
        gone = delete_user(project, ident.uid, admin, out)
        if not gone:
            # The admin route failed; the user can still delete itself.
            print(f'cleanup: self delete '
                  f'{"succeeded" if ident.delete() else "failed too"}',
                  file=out)
        else:
            ident.forget()
        shutil.rmtree(scratch, ignore_errors=True)

    print(f'{p.refused} refused, {len(p.allowed)} allowed'
          f'{"" if both else ", and a rules release is missing"}'
          f'{"" if swept else ", and the sweep left something"}', file=out)
    for who, name, method, url in p.allowed:
        print(f'  ALLOWED  {who}  {name}  {method} {url}', file=out)
    return len(p.allowed) + (0 if both else 1) + (0 if swept else 1)


if __name__ == '__main__':
    sys.exit(main())
