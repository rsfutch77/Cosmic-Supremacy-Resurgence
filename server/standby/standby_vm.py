"""
standby_vm.py: the referee's standby, a cloud Windows VM started only when needed

    python server/standby/standby_vm.py prepare
    python server/standby/standby_vm.py up [--home-is-down]
    python server/standby/standby_vm.py status
    python server/standby/standby_vm.py down

The live referee runs in Windows Sandbox on the home PC (`server/sandbox`). The
standby runs the same stage on a Compute Engine VM, with the `cs-referee`
service account attached instead of a key file. Only one of them may run: the
galaxy guard is a mutex on one machine, so two referees on two machines would
both tick every galaxy.

`prepare` (on the home PC, after each restage): zips `C:\\CSReferee\\stage`, the
interpreter its venv names, and this checkout's bootstrap.ps1, standby_check.py
and this folder, and uploads it to `gs://<bucket>/standby/bundle.zip`. The
bundle then outlives the home PC.

`up`: writes `standby/active.json` naming the VM, then creates the VM. On the
home PC it first refuses while Windows Sandbox runs, and disables the sandbox's
logon task. Anywhere else (another computer, Cloud Shell) it needs
`--home-is-down`, since it cannot see the home PC. The marker keeps a home PC
that comes back from starting a second referee: bootstrap.ps1 stays idle while
another host holds it. A stage made before the marker check (before this
commit) does not read it, so restage before relying on that.

`status`: the VM's state, and the logs it pushes to `standby/out/` every five
minutes.

`down`: deletes the VM, waits for it to go, removes the marker, and on the home
PC enables the sandbox's logon task again. Then start
`C:\\CSReferee\\referee.wsb`.

Switching home to VM: close the Windows Sandbox window, `up`, then `status`
until bootstrap.log shows the worker and a turn closes. Switching back: `down`,
then start referee.wsb and check C:\\CSReferee\\out\\bootstrap.log. Do either
between turns.

`--prefix standby-test` with `prepare --seed <folder galaxy> --once --no-bots`
and `up --no-switch` runs a throwaway VM on a copy of a galaxy, without the
marker or the home checks.

The VM costs about $95 a month while it exists (e2-medium, Windows licence, a
50 GB disk); nothing while deleted.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
import tempfile
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.dirname(HERE)
REPO = os.path.dirname(SERVER)
sys.path.insert(0, os.path.join(SERVER, 'sandbox'))

import standby_check                                            # noqa: E402

PROJECT = 'cs-resurgence'
BUCKET = 'cs-resurgence.firebasestorage.app'
ZONE = 'us-central1-a'
MACHINE = 'e2-medium'             # 2 shared vCPUs, 4 GB, as the sandbox
IMAGE_FAMILY = 'windows-2022'
INSTANCE = 'referee-standby'
SERVICE_ACCOUNT = f'cs-referee@{PROJECT}.iam.gserviceaccount.com'
STAGE = r'C:\CSReferee\stage'
HOME_TASK = 'CosmicSupremacy Referee Sandbox'
SANDBOX_PROCESSES = ('WindowsSandbox.exe', 'WindowsSandboxClient.exe',
                     'WindowsSandboxRemoteSession.exe')
PUSH_SECONDS = 300
PUSHED = ('bootstrap.log', 'preflight.log', 'operator.html', 'bot_worker.json')


def gcloud(*args, check=True, capture=False):
    exe = 'gcloud.cmd' if os.name == 'nt' else 'gcloud'
    r = subprocess.run([exe, *args, f'--project={PROJECT}'], text=True,
                       capture_output=capture)
    if check and r.returncode:
        raise SystemExit(f'gcloud {" ".join(args[:3])} failed ({r.returncode})'
                         + (f': {r.stderr.strip()}' if capture else ''))
    return r


def bucket(name=BUCKET):
    from google.cloud import storage
    return storage.Client(project=PROJECT).bucket(name)


# prepare ---------------------------------------------------------------------

def python_home(stage: str) -> str:
    cfg = os.path.join(stage, 'resurgence', 'server', '.venv', 'pyvenv.cfg')
    for line in open(cfg, encoding='utf-8'):
        if line.split('=')[0].strip() == 'home':
            return line.split('=', 1)[1].strip()
    raise SystemExit(f'{cfg} names no interpreter home')


def add_tree(z, src, arc, skip=()):
    for dp, dn, fn in os.walk(src):
        dn[:] = [d for d in dn if d != '__pycache__']
        for f in fn:
            p = os.path.join(dp, f)
            name = os.path.join(arc, os.path.relpath(p, src))
            if name not in skip:
                z.write(p, name)


def cmd_prepare(a) -> int:
    stage = a.stage
    home = python_home(stage)
    config = json.load(open(os.path.join(stage, 'config.json'), encoding='utf-8-sig'))
    if a.seed:
        config.update(seed='seed_galaxy', store='')
    if a.once:
        config['once'] = True
    if a.no_bots:
        config['bots'] = False
    out = os.path.join(tempfile.mkdtemp(), 'bundle.zip')
    code = os.path.join('stage', 'resurgence', 'server')
    # The standby's own scripts, whatever the stage's commit carries.
    overlay = {os.path.join(code, 'sandbox', rel): os.path.join(SERVER, 'sandbox', rel)
               for rel in ('bootstrap.ps1', 'standby_check.py')}
    overlay.update({os.path.join(code, 'standby', rel): os.path.join(HERE, rel)
                    for rel in os.listdir(HERE) if rel.endswith(('.py', '.ps1'))})
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for name in os.listdir(stage):
            p = os.path.join(stage, name)
            if name == 'config.json' or name == 'seed_galaxy':
                continue
            if os.path.isdir(p):
                add_tree(z, p, os.path.join('stage', name), skip=overlay)
            else:
                z.write(p, os.path.join('stage', name))
        z.writestr(os.path.join('stage', 'config.json'), json.dumps(config, indent=1))
        if a.seed:
            add_tree(z, a.seed, os.path.join('stage', 'seed_galaxy'))
        for name, src in overlay.items():
            z.write(src, name)
        add_tree(z, os.path.realpath(home), 'python')
        z.writestr('python_home.txt', home)
    size = os.path.getsize(out)
    blob = bucket().blob(f'{a.prefix}/bundle.zip')
    blob.upload_from_filename(out, timeout=600)
    commit = open(os.path.join(stage, 'commit.txt'), encoding='utf-8-sig').read().strip()
    print(f'uploaded gs://{BUCKET}/{a.prefix}/bundle.zip, {size / 1048576:.0f} MB, '
          f'stage {commit}, store {config.get("store") or "seed galaxy"}')
    return 0


# up / down -------------------------------------------------------------------

def on_home_pc() -> bool:
    r = subprocess.run(['schtasks', '/Query', '/TN', HOME_TASK], capture_output=True,
                       text=True) if os.name == 'nt' else None
    return bool(r) and r.returncode == 0


def sandbox_running() -> list:
    r = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True, text=True)
    names = {line.split('","')[0].strip('"') for line in r.stdout.splitlines() if line}
    return [p for p in SANDBOX_PROCESSES if p in names]


def home_task(enable: bool):
    flag = '/ENABLE' if enable else '/DISABLE'
    subprocess.run(['schtasks', '/Change', '/TN', HOME_TASK, flag], check=True,
                   capture_output=True)
    print(f'home: {HOME_TASK} {"enabled" if enable else "disabled"}')


def instance_exists(name) -> bool:
    r = gcloud('compute', 'instances', 'describe', name, f'--zone={ZONE}',
               '--format=value(status)', check=False, capture=True)
    return r.returncode == 0


def cmd_up(a) -> int:
    if instance_exists(a.name):
        raise SystemExit(f'{a.name} exists already; `status` or `down` it')
    if not a.no_switch:
        if on_home_pc():
            running = sandbox_running()
            if running:
                raise SystemExit('Windows Sandbox is running (' + ', '.join(running)
                                 + '); close its window between turns, then run up again')
            home_task(enable=False)
        elif not a.home_is_down:
            raise SystemExit('this is not the home PC, so it cannot check that the '
                             'home referee is stopped; run again with --home-is-down '
                             'once you know it is')
        mark = {'holder': 'standby-vm', 'instance': a.name,
                'since': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')}
        bucket().blob(standby_check.MARKER).upload_from_string(
            json.dumps(mark), content_type='application/json')
        print(f'marker: gs://{BUCKET}/{standby_check.MARKER} names {a.name}')
    gcloud('compute', 'instances', 'create', a.name, f'--zone={ZONE}',
           f'--machine-type={a.machine}', '--image-project=windows-cloud',
           f'--image-family={IMAGE_FAMILY}', '--boot-disk-size=50GB',
           '--boot-disk-type=pd-balanced', f'--service-account={SERVICE_ACCOUNT}',
           '--scopes=cloud-platform', '--labels=purpose=referee-standby',
           f'--metadata=standby-bucket={BUCKET},standby-prefix={a.prefix}',
           '--metadata-from-file=windows-startup-script-ps1='
           + os.path.join(HERE, 'vm_startup.ps1'))
    print(f'{a.name} is starting. Windows boots, downloads the bundle and reboots '
          f'into auto-logon, about 10 minutes; then `status`.')
    return 0


def cmd_down(a) -> int:
    if instance_exists(a.name):
        r = gcloud('compute', 'instances', 'delete', a.name, f'--zone={ZONE}',
                   '--quiet', check=False)
        # gcloud can fail after the delete went through, so the instance decides.
        if instance_exists(a.name):
            raise SystemExit(f'{a.name} is still there (gcloud exit {r.returncode}); '
                             f'the marker stays until it is gone')
        print(f'{a.name} deleted')
    else:
        print(f'{a.name} does not exist')
    if not a.no_switch:
        blob = bucket().blob(standby_check.MARKER)
        if blob.exists():
            blob.delete()
            print('marker removed')
        if on_home_pc():
            home_task(enable=True)
        print(r'Now start C:\CSReferee\referee.wsb on the home PC and check '
              r'C:\CSReferee\out\bootstrap.log.')
    return 0


# status / push ---------------------------------------------------------------

def cmd_status(a) -> int:
    r = gcloud('compute', 'instances', 'describe', a.name, f'--zone={ZONE}',
               '--format=value(status)', check=False, capture=True)
    print(f'{a.name}: {r.stdout.strip() if r.returncode == 0 else "does not exist"}')
    if not a.no_switch:
        try:
            mark = standby_check.holder(BUCKET)
        except Exception as exc:                            # noqa: BLE001
            mark = f'unreadable ({exc})'
        print(f'marker: {mark or "none; the home PC holds the galaxies"}')
    b = bucket()
    blobs = sorted(b.list_blobs(prefix=f'{a.prefix}/out/'), key=lambda x: x.name)
    if not blobs:
        print('no pushed logs yet')
    for blob in blobs:
        name = blob.name.rsplit('/', 1)[1]
        print(f'\n== {name}, pushed {blob.updated:%Y-%m-%d %H:%M} UTC')
        if name.endswith('.html'):
            continue
        text = blob.download_as_bytes().decode('utf-8-sig', 'replace')
        print('\n'.join(text.splitlines()[-a.lines:]))
    return 0


def push_once(b, prefix, out):
    names = list(PUSHED) + [n for n in os.listdir(out)
                            if n.startswith('worker_') and n.endswith('.json')]
    for name in names:
        p = os.path.join(out, name)
        if os.path.isfile(p):
            b.blob(f'{prefix}/out/{name}').upload_from_filename(p)
    log = os.path.join(out, 'referee_worker.log')
    if os.path.isfile(log):
        with open(log, 'rb') as f:
            f.seek(max(0, os.path.getsize(log) - 64 * 1024))
            b.blob(f'{prefix}/out/referee_worker.log').upload_from_string(f.read())


def cmd_push(a) -> int:
    """On the VM: copy the logs to the bucket every PUSH_SECONDS, forever."""
    b = bucket(a.bucket)
    while True:
        try:
            push_once(b, a.prefix, a.out)
        except Exception as exc:                            # noqa: BLE001
            print(f'push failed ({type(exc).__name__}: {exc})', flush=True)
        time.sleep(PUSH_SECONDS)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('prepare', 'up', 'down', 'status', 'push'):
        p = sub.add_parser(name)
        p.add_argument('--prefix', default='standby',
                       help='bucket folder for the bundle and logs')
        if name in ('up', 'down', 'status'):
            p.add_argument('--name', default=INSTANCE)
            p.add_argument('--no-switch', action='store_true',
                           help='leave the marker and the home PC alone (a test VM)')
    pr = sub.choices['prepare']
    pr.add_argument('--stage', default=STAGE)
    pr.add_argument('--seed', help='a folder galaxy to run instead of the stage\'s store')
    pr.add_argument('--once', action='store_true')
    pr.add_argument('--no-bots', action='store_true')
    up = sub.choices['up']
    up.add_argument('--machine', default=MACHINE)
    up.add_argument('--home-is-down', action='store_true')
    sub.choices['status'].add_argument('--lines', type=int, default=15)
    pu = sub.choices['push']
    pu.add_argument('--bucket', default=BUCKET)
    pu.add_argument('--out', default=r'C:\out')
    a = ap.parse_args(argv)
    return {'prepare': cmd_prepare, 'up': cmd_up, 'down': cmd_down,
            'status': cmd_status, 'push': cmd_push}[a.cmd](a)


if __name__ == '__main__':
    sys.exit(main())
