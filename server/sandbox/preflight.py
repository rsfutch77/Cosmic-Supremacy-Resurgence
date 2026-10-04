"""
preflight.py , can this machine reach the galaxy, step by step
==============================================================
    python preflight.py firebase://cs-resurgence/h3check

Run by bootstrap.ps1 before the worker. Each step is printed before it starts,
so a step that never returns is named by the last line. A step that has not
returned after STALL seconds dumps every thread's stack and the process exits,
since a hung network call otherwise looks the same as a slow one. A handshake
that times out after the TCP connection succeeds is the MTU symptom bootstrap.ps1
describes.
"""
import faulthandler
import os
import socket
import ssl
import sys
import time

# The Google client libraries verify against certifi's bundle, and gRPC against
# its own. A new sandbox's Windows store holds few roots and fetches the rest on
# demand, which Python's ssl module never asks it to do.
import certifi

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.dirname(HERE)
sys.path.insert(0, SERVER)

STALL = 90


def step(name):
    print(f'{time.strftime("%H:%M:%S")}  {name}', flush=True)


def main():
    store = sys.argv[1]
    faulthandler.dump_traceback_later(STALL, exit=True)
    if not store.startswith('firebase://'):
        step(f'{store} is not a Firebase store; nothing to reach')
        return 0
    for host in ('oauth2.googleapis.com', 'firestore.googleapis.com',
                 'storage.googleapis.com'):
        step(f'resolve {host}')
        addr = socket.getaddrinfo(host, 443)[0][4][0]
        step(f'  {addr}; TLS handshake')
        with socket.create_connection((host, 443), timeout=20) as raw:
            with ssl.create_default_context(cafile=certifi.where()).wrap_socket(
                    raw, server_hostname=host) as tls:
                step(f'  {tls.version()}')
    step(f'credential {os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")}')
    import google.auth
    import google.auth.transport.requests
    creds, _ = google.auth.default(
        scopes=['https://www.googleapis.com/auth/cloud-platform'])
    creds.refresh(google.auth.transport.requests.Request())
    step(f'  token for {getattr(creds, "service_account_email", "a user")}')
    step(f'open {store}')
    from turn_store import open_store
    s = open_store(store)
    step('read state')
    st = s.state()
    step(f'  turn {st.get("turn")}')
    step(f'download turn {st.get("turn")}')
    blob = s.turn_blob(st['turn'])
    step(f'  {len(blob):,} bytes')
    faulthandler.cancel_dump_traceback_later()
    step('preflight passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
