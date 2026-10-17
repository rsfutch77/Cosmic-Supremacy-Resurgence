"""
deploy.py: the only deploy this directory is allowed to do

    python functions/deploy.py --check          say what would run
    python functions/deploy.py                  run it

    firebase deploy --only functions:relay --project cs-resurgence

`cs-resurgence` also serves the live website from `site/`. A `firebase deploy`
without `--only`, from a directory whose `firebase.json` has a `hosting` block,
replaces that website. This directory's `firebase.json` must have no `hosting`
key, and this script refuses any deploy not scoped to `--only functions`.

The predeploy step runs `bundle.py`, which rebuilds `cs_store/` from `server/`.

A first deploy needs the IAM Credentials API to sign Cloud Storage URLs, since
the runtime service account has no private key. Once, by the operator:

    gcloud services enable iamcredentials.googleapis.com --project cs-resurgence
    SA=<runtime-sa>@cs-resurgence.iam.gserviceaccount.com
    gcloud iam service-accounts add-iam-policy-binding $SA \\
        --member=serviceAccount:$SA \\
        --role=roles/iam.serviceAccountTokenCreator \\
        --project cs-resurgence

The runtime account is the one the deploy reports, granted the role on itself.
Without it every signed URL raises about a missing private key.
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
    # On Windows the CLI is `firebase.cmd`, which `CreateProcess` does not find
    # from the bare name, so the executable is resolved first.
    import shutil
    exe = shutil.which(COMMAND[0])
    if exe is None:
        raise SystemExit(f'{COMMAND[0]} is not on PATH')
    return subprocess.call([exe] + COMMAND[1:], cwd=HERE)


if __name__ == '__main__':
    sys.exit(main())
