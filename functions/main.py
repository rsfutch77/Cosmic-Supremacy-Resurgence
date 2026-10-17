"""
main.py: the Cloud Functions entry point for the relay

The adapter only; the relay's logic is in the framework-free `relay.py`.

The deployed URL is the base a launcher is given, with the galaxy on the end:

    https://us-west1-cs-resurgence.cloudfunctions.net/relay/sandbox

`HttpTurnStore` appends `/state`, `/turn/9` and the rest to that. One function
serves every galaxy in the project.

`max_instances` caps how far a misbehaving launcher can scale the function.

`service_account` is the relay's own account, `cs-relay`. It holds:

    roles/datastore.user                    the galaxy documents
    roles/serviceusage.serviceUsageConsumer the store bills this project
    roles/storage.objectAdmin               on the galaxy bucket only
    roles/storage.legacyBucketReader        on the galaxy bucket only, for
                                            `firebase_store.default_bucket`
    roles/iam.serviceAccountTokenCreator    on itself only, to sign URLs
"""
from firebase_functions import https_fn, options

import relay as core


@https_fn.on_request(
    region='us-west1',                  # the bucket's region
    memory=options.MemoryOption.MB_512,
    timeout_sec=60,
    max_instances=10,
    service_account='cs-relay@cs-resurgence.iam.gserviceaccount.com',
)
def relay(req: https_fn.Request) -> https_fn.Response:
    status, headers, body = core.handle(
        req.method, req.path, dict(req.headers), req.get_data())
    return https_fn.Response(body, status=status, headers=headers)
