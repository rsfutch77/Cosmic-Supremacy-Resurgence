"""
operator_view.py: is the beta healthy, without opening a log

    python operator_view.py <path-or-url-or-firebase-spec>
    python operator_view.py --directory <folder-or-firebase://project>
    python operator_view.py <spec> --html out.html
    python operator_view.py --directory <spec> --serve 8765

For every galaxy: the turn, when it is due, who has submitted, when the last
tick ran and how late it was, the referee worker's heartbeat, the seat map, and
anything that looks wrong.

The view never writes. Stores and directories are wrapped by `read_only`, which
forwards only the names in an allowlist and raises `ReadOnlyError` for anything
else. `submissions` is left off the allowlist because it downloads every save;
`submitted_civs` is used instead.

The archive record carries `closed_at` and no duration, so tick timing is
derived: how long after the turn fell due (previous `closed_at` plus the turn
length) it closed. A duration recorded under `seconds`, `duration` or `elapsed`
is shown as measured.

On Firebase the worker heartbeat (`worker_seen`, `worker_failures`) and `seats`
are read with one `get` of the galaxy document. A uid is shown by its first
eight characters.

`--html` writes a self-contained page; `--serve` binds 127.0.0.1 only. The
roster is private, so neither belongs under `site\\`. `write_page` is called by
`referee_worker` after each tick and replaces the file atomically; the page
marks itself stale in the browser once older than the turn length plus
`STALE_MARGIN`.
"""
import argparse
import html
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
for _d in (HERE, os.path.join(HERE, 'dev_tools')):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import galaxy_directory
import turn_store

# The reading half of the store interface. A new store method is refused until
# added here. `submissions` is absent on purpose.
STORE_READS = frozenset((
    'exists', 'state', 'current', 'seconds_left', 'civs',
    'turn_blob', 'has_turn', 'has_submitted', 'submission',
    'archive_record', 'note', 'submitted_civs',
    # For callers of this module; the view itself reads these from state.
    'status', 'is_closed', 'closed_reason', 'reclaimed',
    # Identifying attributes the text output prints.
    'root', 'base', 'project', 'galaxy', 'bucket', 'prefix',
))

# Galaxy document fields beyond the store's state, as written by
# `referee_worker`, the relay and `seat_tool.py`.
WORKER_SEEN_KEY = 'worker_seen'
WORKER_FAILURES_KEY = 'worker_failures'
SEATS_KEY = 'seats'
SEAT_CLAIM_KEY = 'seat_claim'
REBOUND_KEY = 'seats_rebound'
DOCUMENT_FIELDS = (WORKER_SEEN_KEY, WORKER_FAILURES_KEY, SEATS_KEY,
                   SEAT_CLAIM_KEY, REBOUND_KEY)

STOPPED_AFTER = 120   # seconds past the deadline with no heartbeat before a worker is called stopped

UID_SHOWN = 8   # characters of a uid the view shows

# The reading half of the directory interface.
DIRECTORY_READS = frozenset(('galaxies', 'galaxy', 'store', 'root', 'project',
                             'prefix', 'index_path'))

LOOK_BACK = 12                    # turns to search back for the newest archive record
HISTORY = 5                       # closed turns to show
REFRESH_SECONDS = 60              # reload interval of a served page
PAGE_REFRESH_SECONDS = 300        # reload interval of the worker's page, from disk
STALE_MARGIN = 1800               # seconds past one turn length before the worker's page is stale
DEFAULT_TURN_SECONDS = 4 * 3600   # turn length for the staleness check when state has none


class ReadOnlyError(RuntimeError):
    """Raised when this view is asked for something that would write."""


class _ReadOnly:
    """A store or directory with only its reading half reachable."""

    def __init__(self, wrapped, allowed, what):
        object.__setattr__(self, '_wrapped', wrapped)
        object.__setattr__(self, '_allowed', allowed)
        object.__setattr__(self, '_what', what)

    def __repr__(self):
        return f'read-only {self._wrapped!r}'

    @property
    def wrapped_class(self) -> str:
        return type(self._wrapped).__name__

    def __getattr__(self, name):
        if name in self._allowed:
            return getattr(self._wrapped, name)
        raise ReadOnlyError(
            f'{self._what} is open read-only here; {name!r} is not one of the '
            f'reads this view is allowed to make')

    def __setattr__(self, name, value):
        raise ReadOnlyError(f'{self._what} is open read-only here')


def read_only(store):
    """A store this view cannot write through."""
    return _ReadOnly(store, STORE_READS, 'the store')


def read_only_directory(directory):
    """A directory this view cannot register or close a galaxy through."""
    return _ReadOnly(directory, DIRECTORY_READS, 'the directory')


def _duration_recorded(rec: dict):
    """A tick duration the referee recorded, or None."""
    for key in ('seconds', 'duration', 'elapsed'):
        value = rec.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _tick_timing(rec: dict, previous: dict, turn_seconds):
    """(measured, late_by) for one tick.

    `measured` is a duration the referee recorded, normally None. `late_by` is
    how long after the previous `closed_at` plus one turn length this turn
    closed; None without a previous record.
    """
    measured = _duration_recorded(rec)
    late_by = None
    closed = rec.get('closed_at')
    prev_closed = (previous or {}).get('closed_at')
    if closed and prev_closed and turn_seconds:
        late_by = closed - (prev_closed + turn_seconds)
    return measured, late_by


def _archive_history(store, turn: int, history: int, look_back: int):
    """The most recent archive records, newest first, as (turn, record).

    Walks back from `turn - 1` past missing records until the first one is
    found, then stops at the next gap.
    """
    found, searched = [], 0
    n = turn - 1
    while n >= 0 and len(found) < history + 1 and searched < look_back + history:
        rec = store.archive_record(n)
        searched += 1
        if rec is not None:
            found.append((n, rec))
        elif found:
            break
        n -= 1
    return found


def _document_fields(store) -> dict:
    """`DOCUMENT_FIELDS` from a Firebase galaxy's document, by one `get`.
    Empty for any other store, whose state already carries them."""
    inner = (object.__getattribute__(store, '_wrapped')
             if isinstance(store, _ReadOnly) else store)
    import firebase_store
    if not isinstance(inner, firebase_store.FirebaseTurnStore):
        return {}
    snap = inner.doc.get()
    data = (snap.to_dict() or {}) if snap.exists else {}
    return {k: data[k] for k in DOCUMENT_FIELDS if k in data}


def short_uid(uid) -> str:
    return str(uid)[:UID_SHOWN] if uid else ''


def _seat_rows(fields: dict, civs: list) -> list:
    """One row per civ on the roster: who holds it and when it last moved."""
    by_civ = {c: u for u, c in (fields.get(SEATS_KEY) or {}).items()}
    moved = fields.get(REBOUND_KEY) or {}
    joined = fields.get(getattr(turn_store, 'JOINED_KEY', 'joined')) or {}
    return [{'civ': c, 'uid': by_civ.get(c),
             'rebound_at': (moved.get(c) or {}).get('at'),
             'joined_uid': (joined.get(c) or {}).get('uid')}
            for c in civs]


def galaxy_report(store, gid: str = None, name: str = None, status: str = None,
                  history: int = HISTORY, look_back: int = LOOK_BACK) -> dict:
    """Everything the view shows for one galaxy, read through a read-only store.
    Never raises for a galaxy it cannot read; the failure is in the report."""
    store = store if isinstance(store, _ReadOnly) else read_only(store)
    out = {
        'id': gid, 'name': name or gid, 'status': status,
        'store': getattr(store, 'root', None) or getattr(store, 'base', None)
        or repr(store),
        'kind': store.wrapped_class,
        'error': None, 'problems': [],
        'closed_reason': None, 'reclaimed': {},
        'turn': None, 'deadline': None, 'seconds_left': None,
        'turn_seconds': None, 'hash': None,
        'civs': [], 'submitted': [], 'waiting': [],
        'last_tick': None, 'history': [],
        'worker': {'seen': None, 'failures': None},
        'seats': None, 'seat_claim': None,
        'read_at': time.time(),
    }
    try:
        if not store.exists():
            out['error'] = 'no galaxy here yet'
            out['problems'].append(
                'the store is reachable and holds no galaxy, so either no '
                'first turn has been published or this is the wrong spec')
            return out
        state = store.state()
    except Exception as exc:                                # noqa: BLE001
        out['error'] = f'{type(exc).__name__}: {exc}'
        out['problems'].append(f'the store could not be read, {out["error"]}')
        return out

    # The galaxy's own status wins over the listing's.
    status_of = getattr(turn_store, 'status_of', None)
    if status_of is not None:
        out['status'] = status_of(state)
    if out['status'] == getattr(turn_store, 'CLOSED', 'closed'):
        out['closed_reason'] = state.get(
            getattr(turn_store, 'CLOSED_REASON_KEY', 'closed_reason'))
    reclaimed_of = getattr(turn_store, 'reclaimed_of', None)
    if reclaimed_of is not None:
        out['reclaimed'] = reclaimed_of(state) or {}

    turn = state.get('turn')
    out['turn'] = turn
    out['deadline'] = state.get('deadline')
    out['turn_seconds'] = state.get('turn_seconds')
    out['hash'] = state.get('hash')
    if out['deadline'] is not None:
        out['seconds_left'] = out['deadline'] - out['read_at']

    civs = list(state.get('civs', []))

    fields = dict(state)
    try:
        fields.update(_document_fields(store))
    except Exception as exc:                                # noqa: BLE001
        out['problems'].append(f'the galaxy document could not be read for '
                               f'its seats and worker, {type(exc).__name__}: '
                               f'{exc}')
    out['worker'] = {'seen': fields.get(WORKER_SEEN_KEY),
                     'failures': fields.get(WORKER_FAILURES_KEY)}
    # Seats are shown wherever there is a seat map, and always for Firebase.
    if SEATS_KEY in fields or out['kind'] == 'FirebaseTurnStore':
        out['seat_claim'] = fields.get(SEAT_CLAIM_KEY) or None
        out['seats'] = _seat_rows(fields, civs)

    try:
        played, err = set(store.submitted_civs(turn)), None
    except Exception as exc:                                # noqa: BLE001
        played, err = None, f'{type(exc).__name__}: {exc}'
        out['problems'].append(f'could not tell who has submitted, {err}')
    for civ in civs:
        done = None if played is None else civ in played
        out['civs'].append({'name': civ, 'submitted': done, 'error': err})
    out['submitted'] = [c['name'] for c in out['civs'] if c['submitted']]
    out['waiting'] = [c['name'] for c in out['civs'] if c['submitted'] is False]

    try:
        found = _archive_history(store, turn, history, look_back)
    except Exception as exc:                                # noqa: BLE001
        found = []
        out['problems'].append(f'the archive could not be read, '
                               f'{type(exc).__name__}: {exc}')

    for i, (n, rec) in enumerate(found[:history]):
        previous = found[i + 1][1] if i + 1 < len(found) else None
        measured, late_by = _tick_timing(rec, previous, out['turn_seconds'])
        row = {
            'turn': n,
            'published': rec.get('published'),
            'closed_at': rec.get('closed_at'),
            'submitted': list(rec.get('submitted') or []),
            'missing': list(rec.get('missing') or []),
            'ignored': list(rec.get('ignored') or []),
            'refused': dict(rec.get('refused') or {}),
            'bytes_in': rec.get('bytes_in'),
            'bytes_out': rec.get('bytes_out'),
            'measured_seconds': measured,
            'late_by': late_by,
        }
        out['history'].append(row)
    if out['history']:
        out['last_tick'] = out['history'][0]

    out['problems'].extend(_problems(out))
    return out


def _problems(r: dict) -> list:
    """What is wrong, in the words an operator would use. A civ that has not
    submitted yet is not a problem."""
    out = []
    turn, last = r['turn'], r['last_tick']
    worker = r.get('worker') or {}
    seen, fails = worker.get('seen'), worker.get('failures') or 0

    if r['seconds_left'] is not None and r['seconds_left'] < 0:
        out.append(f'the turn is overdue by {fmt_span(-r["seconds_left"])}, so '
                   f'the worker has not closed it')
    if fails > 0:
        out.append(f'the worker has failed {fails} time(s) in a row on turn '
                   f'{turn} and is retrying, last heard from '
                   f'{fmt_when(seen)}')
    elif (seen is not None and r['deadline'] is not None
          and r['seconds_left'] is not None
          and r['seconds_left'] < -STOPPED_AFTER and seen < r['deadline']):
        out.append(f'the worker has not been heard from since '
                   f'{fmt_when(seen)}, before this turn fell due, so it is '
                   f'stopped rather than retrying')

    if r.get('seats') is not None and r.get('seat_claim') != 'first-use':
        for row in r['seats']:
            if row['uid']:
                continue
            asked = (f' It joined as {short_uid(row["joined_uid"])}.'
                     if row.get('joined_uid') else '')
            out.append(f'{row["civ"]} is on the roster and no sign-in holds '
                       f'its seat, and first-use claiming is off, so nobody '
                       f'can play it through the relay; `seat_tool.py bind` '
                       f'seats it.{asked}')

    if last is None:
        out.append(f'no archive record for any of the {LOOK_BACK} turns before '
                   f'turn {turn}, so nothing here has been through the referee')
    else:
        behind = turn - 1 - last['turn']
        if behind > 0:
            out.append(f'the newest archive record is turn {last["turn"]} and '
                       f'the galaxy is on turn {turn}, so {behind} tick(s) '
                       f'published a turn without recording one')
        published = last.get('published')
        if published is not None and published <= last['turn']:
            out.append(f'turn {last["turn"]} published turn {published}, which '
                       f'is not past it, so the galaxy did not advance')
        if last['ignored']:
            out.append('submissions from ' + ', '.join(last['ignored']) +
                       ' were ignored at turn ' + str(last['turn']) +
                       ', they are not on the roster')
        for civ, lines in sorted(last['refused'].items()):
            if lines:
                out.append(f'{len(lines)} order(s) refused from {civ} at turn '
                           f'{last["turn"]}: {lines[0]}'
                           + (' ...' if len(lines) > 1 else ''))
        if last['late_by'] is not None and last['late_by'] > 60:
            out.append(f'turn {last["turn"]} closed {fmt_span(last["late_by"])}'
                       f' after it fell due')
    return out


def _error_row(gid, name, store_spec, exc, note, **extra) -> dict:
    """A galaxy that could not be opened, as a row carrying every key a real
    report does."""
    why = f'{type(exc).__name__}: {exc}'
    row = {'id': gid, 'name': name or gid, 'status': None,
           'store': store_spec, 'kind': None, 'error': why,
           'problems': [f'{note}, {why}'],
           'closed_reason': None, 'reclaimed': {},
           'turn': None, 'deadline': None, 'seconds_left': None,
           'turn_seconds': None, 'hash': None, 'civs': [], 'submitted': [],
           'waiting': [], 'last_tick': None, 'history': [],
           'worker': {'seen': None, 'failures': None},
           'seats': None, 'seat_claim': None,
           'read_at': time.time()}
    row.update(extra)
    return row


def directory_reports(spec: str, history: int = HISTORY,
                      look_back: int = LOOK_BACK) -> list:
    """One report per galaxy a directory lists, including one whose store
    cannot be opened."""
    try:
        listing = read_only_directory(
            galaxy_directory.open_directory(spec)).galaxies()
    except Exception as exc:                                # noqa: BLE001
        return [_error_row(None, spec, spec, exc,
                           'the directory would not open')]
    out = []
    for row in listing:
        try:
            store = read_only(turn_store.open_store(row.store))
        except Exception as exc:                            # noqa: BLE001
            out.append(_error_row(row.id, row.name, row.store, exc,
                                  'the store spec would not open',
                                  status=row.status, turn=row.turn,
                                  deadline=row.deadline))
            continue
        out.append(galaxy_report(store, gid=row.id, name=row.name,
                                 status=row.status, history=history,
                                 look_back=look_back))
    return out


def reports(spec: str, directory: bool = False, history: int = HISTORY,
            look_back: int = LOOK_BACK) -> list:
    """Every galaxy at a spec, whether it names one galaxy or a list of them."""
    if directory:
        return directory_reports(spec, history=history, look_back=look_back)
    try:
        store = read_only(turn_store.open_store(spec))
    except Exception as exc:                                # noqa: BLE001
        return [_error_row(None, spec, spec, exc,
                           'the store spec would not open')]
    gid = os.path.basename(str(getattr(store, 'root', '')).rstrip('\\/')) or None
    return [galaxy_report(store, gid=gid, history=history,
                          look_back=look_back)]


def fmt_span(seconds) -> str:
    """A duration a person reads at a glance."""
    if seconds is None:
        return '-'
    seconds = float(seconds)
    sign = '-' if seconds < 0 else ''
    seconds = abs(seconds)
    if seconds < 90:
        return f'{sign}{seconds:.0f}s'
    if seconds < 5400:
        return f'{sign}{seconds / 60:.0f}m'
    if seconds < 172800:
        return f'{sign}{int(seconds // 3600)}h {int(seconds % 3600 // 60):02d}m'
    return f'{sign}{seconds / 86400:.1f}d'


def fmt_left(seconds) -> str:
    """How long a turn has, or how far past its deadline it is."""
    if seconds is None:
        return 'no deadline'
    return (f'{fmt_span(seconds)} left' if seconds >= 0
            else f'{fmt_span(-seconds)} OVERDUE')


def fmt_when(epoch) -> str:
    """An absolute local time."""
    if not epoch:
        return '-'
    return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(epoch))


def _tick_line(row: dict) -> str:
    """What the last tick took, saying which part is measured and which
    derived."""
    if row is None:
        return 'never'
    parts = [f'turn {row["turn"]} to {row.get("published")} at '
             f'{fmt_when(row["closed_at"])}']
    if row['closed_at']:
        parts.append(f'{fmt_span(time.time() - row["closed_at"])} ago')
    if row['measured_seconds'] is not None:
        parts.append(f'took {fmt_span(row["measured_seconds"])}, measured')
    elif row['late_by'] is not None:
        parts.append(f'closed {fmt_span(row["late_by"])} after it fell due, '
                     f'derived')
    else:
        parts.append('duration not recorded')
    return ', '.join(parts)


def _worker_line(r: dict) -> str:
    w = r.get('worker') or {}
    if w.get('seen') is None:
        return 'no heartbeat recorded'
    return (f'last heard from {fmt_when(w["seen"])}, '
            f'{fmt_span(time.time() - w["seen"])} ago, '
            f'{int(w.get("failures") or 0)} failure(s) in a row')


def _seat_lines(r: dict) -> list:
    """The seat map, one civ a line, or [] when there is none to show."""
    if r.get('seats') is None:
        return []
    claim = 'on' if r.get('seat_claim') == 'first-use' else 'off'
    lines = [f'first-use claiming {claim}']
    for row in r['seats']:
        if row['uid']:
            line = f'{row["civ"]:<16} {short_uid(row["uid"])}'
            if row.get('rebound_at'):
                line += f'  moved {fmt_when(row["rebound_at"])}'
        else:
            line = f'{row["civ"]:<16} no seat'
            if row.get('joined_uid'):
                line += f', joined as {short_uid(row["joined_uid"])}'
        lines.append(line)
    return lines


def render_text(rows: list) -> str:
    """The same page as one screen of terminal text."""
    out = [f'galaxies at {fmt_when(time.time())}', '']
    for r in rows:
        title = r['name'] or r['id'] or r['store']
        head = f'{title}'
        if r['status']:
            head += f'  [{r["status"]}]'
        if r.get('closed_reason'):
            head += f'  {r["closed_reason"]}'
        out.append(head)
        out.append(f'  store    {r["store"]}'
                   + (f'  ({r["kind"]})' if r.get('kind') else ''))
        if r['error']:
            out.append(f'  ERROR    {r["error"]}')
        else:
            out.append(f'  turn     {r["turn"]}, due {fmt_when(r["deadline"])}'
                       f', {fmt_left(r["seconds_left"])}')
            done = ', '.join(r['submitted']) or '(nobody)'
            wait = ', '.join(r['waiting']) or '(nobody)'
            out.append(f'  in       {len(r["submitted"])}/{len(r["civs"])}  '
                       f'{done}')
            out.append(f'  waiting  {wait}')
            for civ in r['civs']:
                if civ['error']:
                    out.append(f'           {civ["name"]}: {civ["error"]}')
            for civ, rec in sorted((r.get('reclaimed') or {}).items()):
                out.append(f'  gone     {civ}, seat taken back at turn '
                           f'{(rec or {}).get("turn", "?")} after '
                           f'{(rec or {}).get("missed", "?")} missed turn(s)')
            out.append(f'  last     {_tick_line(r["last_tick"])}')
            for row in r['history'][1:]:
                out.append(f'           {_tick_line(row)}')
            out.append(f'  referee  {_worker_line(r)}')
            for i, line in enumerate(_seat_lines(r)):
                out.append(f'  {"seats" if i == 0 else "":<7}  {line}')
        if r['problems']:
            for p in r['problems']:
                out.append(f'  PROBLEM  {p}')
        else:
            out.append('  problems none')
        out.append('')
    if not rows:
        out.append('no galaxies listed')
    return '\n'.join(out)


CSS = """
body { background:#050a1a; color:#c6d4f0; font:14px/1.5 Consolas,monospace;
       margin:0; padding:16px; }
h1 { font-size:16px; font-weight:normal; color:#8fa4cc; margin:0 0 16px; }
.g { background:#080f22; border:1px solid #1b2a4a; border-radius:6px;
     padding:12px 14px; margin:0 0 14px; max-width:100%; }
.g h2 { font-size:15px; margin:0 0 8px; color:#e8eefc; }
.tag { font-size:12px; color:#8fa4cc; border:1px solid #2b3d63; border-radius:3px;
       padding:1px 6px; margin-left:8px; }
table { border-collapse:collapse; width:100%; }
td { padding:2px 10px 2px 0; vertical-align:top; }
td.k { color:#6f84ad; width:110px; white-space:nowrap; }
.in { color:#7fd6a2; }
.out { color:#d8b25f; }
.err { color:#e8788a; }
.muted { color:#6f84ad; }
ul { margin:4px 0 0; padding-left:18px; }
"""


def _row_html(r: dict) -> str:
    def cell(k, v, cls=''):
        return (f'<tr><td class="k">{html.escape(k)}</td>'
                f'<td class="{cls}">{v}</td></tr>')

    title = html.escape(str(r['name'] or r['id'] or r['store']))
    tag = (f'<span class="tag">{html.escape(r["status"])}</span>'
           if r['status'] else '')
    if r.get('closed_reason'):
        tag += f'<span class="tag">{html.escape(str(r["closed_reason"]))}</span>'
    body = [f'<div class="g"><h2>{title}{tag}</h2><table>']
    body.append(cell('store', f'<span class="muted">'
                              f'{html.escape(str(r["store"]))}</span>'))
    if r['error']:
        body.append(cell('error', html.escape(r['error']), 'err'))
    else:
        body.append(cell('turn', f'{r["turn"]}'))
        body.append(cell('due', f'{html.escape(fmt_when(r["deadline"]))} '
                                f'({html.escape(fmt_left(r["seconds_left"]))})',
                         'err' if (r['seconds_left'] or 0) < 0 else ''))
        civs = ' '.join(
            f'<span class="{"in" if c["submitted"] else "out"}">'
            f'{html.escape(c["name"])}'
            f'{" &#10003;" if c["submitted"] else " &#8230;"}</span>'
            for c in r['civs']) or '<span class="muted">(empty roster)</span>'
        body.append(cell(f'in {len(r["submitted"])}/{len(r["civs"])}', civs))
        taken = r.get('reclaimed') or {}
        if taken:
            body.append(cell('seats taken back', '<br>'.join(
                html.escape(f'{civ}, at turn {(rec or {}).get("turn", "?")} '
                            f'after {(rec or {}).get("missed", "?")} missed '
                            f'turn(s)')
                for civ, rec in sorted(taken.items()))))
        body.append(cell('last tick', html.escape(_tick_line(r['last_tick']))))
        if r['history'][1:]:
            older = '<br>'.join(html.escape(_tick_line(row))
                                for row in r['history'][1:])
            body.append(cell('before that', f'<span class="muted">{older}'
                                            f'</span>'))
        failing = (r.get('worker') or {}).get('failures') or 0
        body.append(cell('referee', html.escape(_worker_line(r)),
                         'err' if failing else ''))
        seat_lines = _seat_lines(r)
        if seat_lines:
            body.append(cell('seats', '<br>'.join(
                html.escape(line) for line in seat_lines)))
    if r['problems']:
        items = ''.join(f'<li>{html.escape(p)}</li>' for p in r['problems'])
        body.append(cell('problems', f'<ul>{items}</ul>', 'err'))
    else:
        body.append(cell('problems', '<span class="muted">none</span>'))
    body.append('</table></div>')
    return ''.join(body)


# Marks the page stale by the browser's clock.
STALE_SCRIPT = """<script>
(function () {
  var generated = %(generated)d * 1000, limit = %(limit)d * 1000;
  function check() {
    var age = Date.now() - generated;
    var box = document.getElementById('stale');
    if (age > limit) {
      box.style.display = 'block';
      box.textContent = 'STALE: this page was generated ' +
        Math.round(age / 60000) + ' minutes ago, more than the turn length ' +
        'plus the margin, so the referee worker is not running or cannot ' +
        'write it. Nothing below can be trusted as current.';
      document.title = 'STALE, ' + document.title.replace(/^STALE, /, '');
    }
  }
  check();
  setInterval(check, 60000);
})();
</script>"""


def _generated_html(generated_at: float, event, stale_after) -> str:
    """The line saying when the page was written, and the staleness check."""
    line = f'generated at {fmt_when(generated_at)}'
    if event:
        line += f' by the referee worker, {event}'
    line += (f'. If this is older than {fmt_span(stale_after)}, the turn '
             f'length plus a margin, the worker is not running.')
    return (f'<p class="muted" id="generated">{html.escape(line)}</p>'
            '<div class="g err" id="stale" style="display:none"></div>'
            + STALE_SCRIPT % {'generated': int(generated_at),
                              'limit': int(stale_after)})


def render_html(rows: list, refresh: int = 0, generated_at: float = None,
                event: str = None, stale_after: float = None) -> str:
    """One self-contained page with no external references, so viewing it
    fetches nothing.

    With `stale_after`, the page says when it was generated and marks itself
    stale in the browser once it is older than that many seconds.
    """
    meta = (f'<meta http-equiv="refresh" content="{int(refresh)}">'
            if refresh else '')
    now = time.time() if generated_at is None else generated_at
    cards = ''.join(_row_html(r) for r in rows) or \
        '<div class="g"><h2>no galaxies listed</h2></div>'
    generated = (_generated_html(now, event, stale_after)
                 if stale_after else '')
    return ('<!doctype html><html><head><meta charset="utf-8">'
            '<title>Resurgence, galaxies</title>' + meta +
            f'<style>{CSS}</style></head><body>'
            f'<h1>galaxies at {html.escape(fmt_when(now))}</h1>'
            f'{generated}{cards}</body></html>')


def write_page(store, path: str, event: str = None, gid: str = None,
               name: str = None, now: float = None) -> str:
    """Render one galaxy to `path`, replacing it atomically. Returns the path.
    Raises on failure."""
    now = time.time() if now is None else now
    rows = [galaxy_report(store, gid=gid, name=name)]
    turn_seconds = rows[0].get('turn_seconds') or DEFAULT_TURN_SECONDS
    body = render_html(rows, refresh=PAGE_REFRESH_SECONDS, generated_at=now,
                       event=event, stale_after=turn_seconds + STALE_MARGIN)
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f'{path}.{os.getpid()}.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(body)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return path


def serve(spec: str, directory: bool = False, host: str = '127.0.0.1',
          port: int = 8765, history: int = HISTORY, look_back: int = LOOK_BACK):
    """A page on the operator's own machine, rebuilt on each request.

    The command line always binds loopback, since the page shows the roster.
    Returns the server without starting it.
    """
    import http.server

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):                                   # noqa: N802
            try:
                rows = reports(spec, directory=directory, history=history,
                               look_back=look_back)
                body = render_html(rows, refresh=REFRESH_SECONDS)
                code = 200
            except Exception as exc:                        # noqa: BLE001
                body = ('<!doctype html><html><body><pre>'
                        + html.escape(f'{type(exc).__name__}: {exc}')
                        + '</pre></body></html>')
                code = 500
            data = body.encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt, *args):
            pass

    return http.server.HTTPServer((host, port), Handler)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description='What every galaxy is doing, read-only.')
    ap.add_argument('spec', nargs='?',
                    help='a store spec, or a directory spec with --directory')
    ap.add_argument('--directory', action='store_true',
                    help='the spec lists galaxies rather than being one')
    ap.add_argument('--html', metavar='PATH',
                    help='write a self-contained page instead of printing')
    ap.add_argument('--serve', nargs='?', type=int, const=8765, metavar='PORT',
                    help='serve the page on 127.0.0.1 at this port')
    ap.add_argument('--json', action='store_true',
                    help='the same reading as JSON, for another tool')
    ap.add_argument('--history', type=int, default=HISTORY,
                    help=f'closed turns to show, default {HISTORY}')
    ap.add_argument('--look-back', type=int, default=LOOK_BACK,
                    help=f'turns to search for an archive record, default '
                         f'{LOOK_BACK}')
    a = ap.parse_args(argv)
    if not a.spec:
        ap.error('a store or directory spec is required')

    if a.serve is not None:
        httpd = serve(a.spec, directory=a.directory, port=a.serve,
                      history=a.history, look_back=a.look_back)
        print(f'http://127.0.0.1:{httpd.server_address[1]}/  '
              f'(loopback only, ctrl-c to stop)')
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()
        return 0

    rows = reports(a.spec, directory=a.directory, history=a.history,
                   look_back=a.look_back)
    if a.json:
        print(json.dumps(rows, indent=2))
    elif a.html:
        with open(a.html, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(render_html(rows))
        print(f'wrote {os.path.abspath(a.html)}')
    else:
        print(render_text(rows))
    # Exit 0: nothing to look at. 1: a problem in a galaxy that reads.
    # 2: a galaxy that cannot be read.
    if any(r['error'] for r in rows):
        return 2
    return 1 if any(r['problems'] for r in rows) else 0


if __name__ == '__main__':
    sys.exit(main())
