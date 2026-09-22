"""
deploy.py , the only deploy this directory is allowed to do
============================================================
    python functions/deploy.py --check          say what would run
    python functions/deploy.py                  run it

    firebase deploy --only functions:relay --project cs-resurgence

That command, from this directory, and nothing else.

Why this exists rather than a note
-----------------------------------
`cs-resurgence` serves the live public website cosmicresurgence.com. Its Hosting
config is `site/firebase.json` and its content is `site/`, and a `firebase
deploy` with no `--only`, run from a directory whose `firebase.json` has a
`hosting` block, replaces a live website with whatever that block points at.
H0 recorded the same hazard for the rules deploy and solved it the same way: a
config that names one thing.

Three things keep that from being possible here, and each is worth stating
because any one of them alone is a rule someone can forget.

1. **This `firebase.json` has no `hosting` key.** A deploy driven by it has
   nothing to deploy to Hosting even if it is asked. `firebase deploy` from
   here deploys the codebase in the `functions` array and reports "no hosting
   target" for the rest, because there is no rest.
2. **`--only functions:relay` names one function of one codebase.** Even in a
   config that grew a second thing, this cannot reach it.
3. **The Hosting config is in another directory and the CLI only reads one.**
   `site/firebase.json` is never loaded by a command run from here, and no
   firebase command is ever run from `site/`.

This script refuses to run anything that does not satisfy 2, so a hurried edit
to the command is a refusal rather than an outage.

What the predeploy step does
-----------------------------
`firebase.json` runs `bundle.py` first, which copies the store's modules into
`cs_store/` so the upload carries them. It is a build product, ignored by git,
and rebuilt from `server/` every time, so a deploy cannot ship a stale copy of
a module that was edited in `server/`.

What a first deploy needs that this cannot do
----------------------------------------------
Signing a Cloud Storage URL from a Cloud Functions runtime needs the IAM
Credentials API, because the runtime service account has no private key. Once,
by the operator, in the project:

    gcloud services enable iamcredentials.googleapis.com --project cs-resurgence
    SA=<runtime-sa>@cs-resurgence.iam.gserviceaccount.com
    gcloud iam service-accounts add-iam-policy-binding $SA \\
        --member=serviceAccount:$SA \\
        --role=roles/iam.serviceAccountTokenCreator \\
        --project cs-resurgence

The runtime account is the one the deploy reports, and the binding is the
account granted the role **on itself**, which is what lets it ask the IAM API to
sign for it. Without it every signed URL raises about a missing private key, and
nothing in that message says any of this.
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

COMMAND = ['firebase', 'deploy', '--only', 'functions:relay',
           '--project', 'cs-resurgence']


def refuse_unless_scoped(command) -> None:
    """A deploy that does not name one function does not run from here."""
    if 'deploy' in command and '--only' not in command:
        raise SystemExit('refused: a deploy from this directory must be '
                         '--only functions:relay, because this project serves '
                         'the live website')
    for i, word in enumerate(command):
        if word == '--only' and not command[i + 1].startswith('functions'):
            raise SystemExit(f'refused: --only {command[i + 1]} is not '
                             f'functions')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--check', action='store_true',
                    help='print the command and the config, and run nothing')
    a = ap.parse_args()
    refuse_unless_scoped(COMMAND)
    print(' '.join(COMMAND))
    print(f'  from {HERE}')
    if a.check:
        import json
        with open(os.path.join(HERE, 'firebase.json'), encoding='utf-8') as f:
            config = json.load(f)
        print(f'  firebase.json keys: {sorted(config)}')
        if 'hosting' in config:
            raise SystemExit('refused: this firebase.json has a hosting key')
        print('  no hosting key, so this config cannot deploy the website')
        return 0
    return subprocess.call(COMMAND, cwd=HERE)


if __name__ == '__main__':
    sys.exit(main())
