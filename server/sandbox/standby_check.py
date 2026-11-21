"""
standby_check.py: may this host run the referee, or does the standby VM hold it

    python standby_check.py cs-resurgence.firebasestorage.app

Run by bootstrap.ps1 before the workers start. `standby_vm.py up` writes the
marker object and `down` deletes it. The marker names the host that holds the
galaxies; a host whose `CS_REFEREE_HOST` differs stays idle, so a home PC that
reboots while the VM runs does not start a second referee.

Exit 0: run. Exit 3: another host holds the galaxies. An unreadable marker is
reported and treated as absent, since a referee that never starts stalls every
galaxy.
"""
import json
import os
import sys

MARKER = 'standby/active.json'
HOST_ENV = 'CS_REFEREE_HOST'
HOME = 'home'                  # the host name when CS_REFEREE_HOST is unset
BLOCKED = 3


def holder(bucket_name: str):
    """The marker's contents, or None when there is no marker."""
    from google.cloud import storage
    blob = storage.Client().bucket(bucket_name).blob(MARKER)
    if not blob.exists():
        return None
    return json.loads(blob.download_as_bytes() or b'{}')


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or not argv[0]:
        print('no bucket; nothing to check')
        return 0
    me = os.environ.get(HOST_ENV) or HOME
    try:
        mark = holder(argv[0])
    except Exception as exc:                                # noqa: BLE001
        print(f'could not read the standby marker ({type(exc).__name__}: '
              f'{exc}); running')
        return 0
    if not mark:
        print(f'no standby marker; {me} runs the referee')
        return 0
    if mark.get('holder') == me:
        print(f'the marker names this host ({me}); running')
        return 0
    print(f'{mark.get("holder")} holds the galaxies since {mark.get("since")}; '
          f'{me} stays idle until `standby_vm.py down` removes the marker')
    return BLOCKED


if __name__ == '__main__':
    sys.exit(main())
