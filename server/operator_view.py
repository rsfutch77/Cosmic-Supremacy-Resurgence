"""
operator_view.py , is the beta healthy, without opening a log
=============================================================
    python operator_view.py <path-or-url-or-firebase-spec>
    python operator_view.py --directory <folder-or-firebase://project>
    python operator_view.py <spec> --html out.html
    python operator_view.py --directory <spec> --serve 8765

One page, or one screen of text, saying for every galaxy: which turn it is on,
when that turn is due, who has handed a turn back and who has not, when the
last tick ran and what is known about how long it took, and anything that looks
wrong. Until this existed the only account of a running beta was `launcher.log`
and the referee's console, both on one machine, and neither readable by anyone
who was not sitting at it.

This is a **diagnostic and it never writes**. A tool that can mutate a live
galaxy while the referee is mid-tick is worse than no tool, so the store and
the directory are wrapped before they are used: `read_only` forwards the
reading half of the interface by name and raises `ReadOnlyError` for anything
else. The allowlist is the whole of the guarantee, so a store that grows a new
method is refused by default rather than forwarded by default.

`submissions(turn)` is deliberately **not** on that allowlist, which is the
other half of the H6 finding. It returns `{civ: blob}`, so asking it who has
handed a turn back downloads every player's save to read the keys and throw the
bytes away. On a folder that is free and on Firebase it is a download per
player per refresh. This asks `has_submitted(civ, turn)` once per civ instead,
which is an existence check on all three stores. Leaving the method off the
proxy means a later edit that reaches for it fails loudly rather than costing
money quietly.

Everything comes through `open_store` and `open_directory`, so a folder, a
`turn_server.py` over HTTP and Firebase are all read the same way and this
module never learns which it got.

How long a tick took
--------------------
The archive record the referee writes carries `closed_at` and no duration, so
the honest answer to "what did it take" has two parts and they are labelled
separately. `closed_at` is reported as it stands. Anything further is derived:
the previous record's `closed_at` plus the galaxy's turn length is when this
turn fell due, and the gap from there to this turn's `closed_at` is how late
the tick was. That is the number an operator watching for a stalled worker
wants, and it is not the tick's own duration. If a future referee records a
duration under `seconds`, `duration` or `elapsed`, it is shown as measured.

Serving it
----------
`--html` writes a self-contained file with no external references. `--serve`
binds `127.0.0.1` only and refreshes itself, which is a page on the operator's
own screen rather than anything deployed. Neither belongs under `site\\`: that
project is a live public website and a galaxy's roster is not public.
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

# The reading half of the store interface. An allowlist rather than a list of
# things to block, so a store that grows a method is refused until someone has
# looked at it. `submissions` is absent on purpose; see the module header.
STORE_READS = frozenset((
    'exists', 'state', 'current', 'seconds_left', 'civs',
    'turn_blob', 'has_turn', 'has_submitted', 'submission',
    'archive_record', 'note',
    # Added when a galaxy learned to be closed and a seat learned to be taken
    # back. The view reads both out of the state it already holds rather than
    # calling these, so they are here for a caller of this module rather than
    # for this module, and the writes beside them, `close`, `reopen` and
    # `update_state`, stay off the list by saying nothing about them.
    'status', 'is_closed', 'closed_reason', 'reclaimed',
    # Identifying attributes the text output prints. They are values rather
    # than calls, and they say which machine or project is being read.
    'root', 'base', 'project', 'galaxy', 'bucket', 'prefix',
))

# The same for the directory above it. `register` and `set_status` are writes.
DIRECTORY_READS = frozenset(('galaxies', 'galaxy', 'store', 'root', 'project',
                             'prefix', 'index_path'))

# How many turns back to look for the most recent archive record. A galaxy that
# has just started has none, and one whose referee died has one a few turns
# back. Each step is one read, so the ceiling is the cost of a refresh on a
# galaxy that has never ticked.
LOOK_BACK = 12

# How many closed turns to show. Enough to see whether ticks are keeping their
# cadence, and small enough that a refresh is a handful of reads.
HISTORY = 5

# How often a served page reloads itself. A 4-hour turn does not need faster,
# and this is a page the operator leaves open.
REFRESH_SECONDS = 60


class ReadOnlyError(RuntimeError):
    """Raised when this view is asked for something that would write."""


class _ReadOnly:
    """A store or directory with only its reading half reachable.

    Wrapping rather than trusting the caller, because the thing being read is a
    live galaxy that players are mid-turn in. `publish`, `submit`, `archive`
    and `put_note` are not slips that a careful reader would catch: they are
    ordinary method names on an object this module holds a reference to.
    """

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


# ── reading one galaxy ───────────────────────────────────────────────────────
def _duration_recorded(rec: dict):
    """A tick duration the referee measured, if a referee ever records one.

    The archive record does not carry one today. Reading three plausible names
    rather than inventing a number is the point: an operator must be able to
    tell a measured duration from this module's arithmetic.
    """
    for key in ('seconds', 'duration', 'elapsed'):
        value = rec.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _tick_timing(rec: dict, previous: dict, turn_seconds):
    """What can be said about the last tick's timing, and how it is known.

    Returns (measured, late_by). `measured` is a duration the referee recorded
    and is normally None. `late_by` is derived: the previous turn closed at
    `closed_at`, which is when this turn was published and its clock started,
    so this turn fell due one turn length later and `late_by` is how long after
    that the referee closed it. It needs the previous record, so the oldest
    turn shown has none.
    """
    measured = _duration_recorded(rec)
    late_by = None
    closed = rec.get('closed_at')
    prev_closed = (previous or {}).get('closed_at')
    if closed and prev_closed and turn_seconds:
        late_by = closed - (prev_closed + turn_seconds)
    return measured, late_by


def _archive_history(store, turn: int, history: int, look_back: int):
    """The most recent archive records, newest first, and how far back we went.

    The referee archives the turn it closed, so the newest record is normally
    `turn - 1`. A galaxy whose worker has been down for a while has its newest
    record further back, and that gap is itself the diagnosis, so the search
    walks back rather than giving up at the first miss.
    """
    found, searched = [], 0
    n = turn - 1
    while n >= 0 and len(found) < history + 1 and searched < look_back + history:
        rec = store.archive_record(n)
        searched += 1
        if rec is not None:
            found.append((n, rec))
        elif found:
            # A hole after the newest record means the run is not contiguous.
            # Stop rather than trawling the whole archive for older ones.
            break
        n -= 1
    return found


def galaxy_report(store, gid: str = None, name: str = None, status: str = None,
                  history: int = HISTORY, look_back: int = LOOK_BACK) -> dict:
    """Everything the view shows for one galaxy, read through a read-only store.

    Never raises for a galaxy it cannot read. An unreachable share, a service
    that is down and a store that holds no galaxy are three of the states this
    exists to report, and a view that died on the first of them would report
    none of the others.
    """
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

    # The galaxy's own word about itself wins over whatever the listing said,
    # for the reason the directory gives: a galaxy the operator closed through
    # its store is closed whether or not an index was updated with it. Read
    # from the state already in hand, and through the store module's own
    # helpers when it has them, so this costs no extra read and does not
    # break against a store written before either existed.
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
    for civ in civs:
        try:
            done = bool(store.has_submitted(civ, turn))
            err = None
        except Exception as exc:                            # noqa: BLE001
            done, err = None, f'{type(exc).__name__}: {exc}'
            out['problems'].append(f'could not tell whether {civ} has '
                                   f'submitted, {err}')
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
    """What is wrong, in the words an operator would use.

    Only things that are actually wrong. A civ that has not submitted yet is
    the ordinary state of a turn in progress and is reported as the roster
    rather than as a fault.
    """
    out = []
    turn, last = r['turn'], r['last_tick']

    if r['seconds_left'] is not None and r['seconds_left'] < 0:
        out.append(f'the turn is overdue by {fmt_span(-r["seconds_left"])}, so '
                   f'the worker has not closed it')

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


# ── reading a whole directory ────────────────────────────────────────────────
def _error_row(gid, name, store_spec, exc, note, **extra) -> dict:
    """A galaxy that could not be opened, as a row rather than a traceback.

    A view that raised on the first galaxy it could not open would report none
    of the others, and an unopenable galaxy is one of the things it exists to
    notice. The row carries every key a real report does, so the renderers do
    not have to know which kind they were handed.
    """
    why = f'{type(exc).__name__}: {exc}'
    row = {'id': gid, 'name': name or gid, 'status': None,
           'store': store_spec, 'kind': None, 'error': why,
           'problems': [f'{note}, {why}'],
           'closed_reason': None, 'reclaimed': {},
           'turn': None, 'deadline': None, 'seconds_left': None,
           'turn_seconds': None, 'hash': None, 'civs': [], 'submitted': [],
           'waiting': [], 'last_tick': None, 'history': [],
           'read_at': time.time()}
    row.update(extra)
    return row


def directory_reports(spec: str, history: int = HISTORY,
                      look_back: int = LOOK_BACK) -> list:
    """One report per galaxy a directory lists.

    A galaxy whose store cannot be opened still gets a row. The directory's own
    listing already survives an unreachable store, and a view that dropped the
    galaxy would say a beta is healthy because the broken half is invisible.
    """
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


# ── formatting ───────────────────────────────────────────────────────────────
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
    """How long a turn has, or how far past its deadline it is.

    A negative countdown is the state this view exists to make obvious, so it
    is said in words rather than shown as a minus sign in front of a duration.
    """
    if seconds is None:
        return 'no deadline'
    return (f'{fmt_span(seconds)} left' if seconds >= 0
            else f'{fmt_span(-seconds)} OVERDUE')


def fmt_when(epoch) -> str:
    """An absolute local time, because a relative one alone hides a stopped
    clock."""
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
    if r['problems']:
        items = ''.join(f'<li>{html.escape(p)}</li>' for p in r['problems'])
        body.append(cell('problems', f'<ul>{items}</ul>', 'err'))
    else:
        body.append(cell('problems', '<span class="muted">none</span>'))
    body.append('</table></div>')
    return ''.join(body)


def render_html(rows: list, refresh: int = 0) -> str:
    """One self-contained page. No external references of any kind.

    Nothing is fetched, so this opens from a file over `file:` and shows the
    same thing it shows when served. The reason is not tidiness: a page about a
    private galaxy that fetched a font would say when the operator looked at it
    and from where.
    """
    meta = (f'<meta http-equiv="refresh" content="{int(refresh)}">'
            if refresh else '')
    cards = ''.join(_row_html(r) for r in rows) or \
        '<div class="g"><h2>no galaxies listed</h2></div>'
    return ('<!doctype html><html><head><meta charset="utf-8">'
            '<title>Resurgence, galaxies</title>' + meta +
            f'<style>{CSS}</style></head><body>'
            f'<h1>galaxies at {html.escape(fmt_when(time.time()))}</h1>'
            f'{cards}</body></html>')


# ── serving ──────────────────────────────────────────────────────────────────
def serve(spec: str, directory: bool = False, host: str = '127.0.0.1',
          port: int = 8765, history: int = HISTORY, look_back: int = LOOK_BACK):
    """A page on the operator's own machine, rebuilt on each request.

    Bound to the loopback address and not configurable from the command line.
    This shows a galaxy's roster and its refusals, which J4 keeps out of a
    launcher's reach on purpose, so the one place it is allowed to exist is a
    screen the operator is sitting at.

    Returns the server without starting it, so a caller can run it on its own
    thread and shut it down; `main` calls `serve_forever`.
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
                # A view that 500s with a stack trace is still a better answer
                # than a blank page, and the operator is the only reader.
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
    # Three outcomes rather than two, because a scheduled task that only knows
    # "did it run" would treat an overdue turn and an unreachable share the
    # same. 0 nothing to look at, 1 something is wrong in a galaxy that reads,
    # 2 a galaxy that cannot be read at all.
    if any(r['error'] for r in rows):
        return 2
    return 1 if any(r['problems'] for r in rows) else 0


if __name__ == '__main__':
    sys.exit(main())
