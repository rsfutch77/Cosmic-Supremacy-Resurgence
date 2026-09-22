"""
main.py , the Cloud Functions entry point for the relay
========================================================
The adapter and nothing else. Everything the relay decides is in `relay.py`,
which is framework free so that its tests can call it without the functions
runtime, and so that the next place this runs, if it is ever Cloud Run or a
container beside the referee, needs a new adapter rather than a new relay.

The deployed URL is the base a launcher is given, with the galaxy on the end:

    https://us-west1-cs-resurgence.cloudfunctions.net/relay/sandbox

`HttpTurnStore` appends `/state`, `/turn/9` and the rest to that, which is why
the galaxy is a path segment rather than a query parameter. One function serves
every galaxy in the project.

`max_instances` is not tuning. This project also serves the live public website
and is on the Blaze plan, where a loop can spend real money; a ceiling on
instances is the cheapest thing standing between a misbehaving launcher and a
bill, and H0 already asked for the budget alert that is the other one.
"""
from firebase_functions import https_fn, options

import relay as core


@https_fn.on_request(
    region='us-west1',                  # the bucket's region, see H0
    memory=options.MemoryOption.MB_512,
    timeout_sec=60,
    max_instances=10,
)
def relay(req: https_fn.Request) -> https_fn.Response:
    status, headers, body = core.handle(
        req.method, req.path, dict(req.headers), req.get_data())
    return https_fn.Response(body, status=status, headers=headers)
