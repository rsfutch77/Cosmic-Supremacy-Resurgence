"""
test_beta_notice.py: the player notice says what the code actually does
========================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_beta_notice.py

Each check ties the notice's prose to something in the code: a constant, a
function, or a list written in another file. `release/launcher.py` is read as
text.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools')):
    if d not in sys.path:
        sys.path.insert(0, d)

import beta_notice

LAUNCHER = os.path.join(ROOT, 'release', 'launcher.py')

PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def flat(body: str) -> str:
    """The notice as one lowercase line, so phrase checks ignore line wraps."""
    return ' '.join(body.split()).lower()


def read(path):
    with open(path, encoding='utf-8') as fh:
        return fh.read()


def launcher_bullets(src: str) -> list:
    """The itemised list under WHAT THE REDACTED COPY STILL CONTAINS.

    Each item begins a comment line with a lone comma, which is that file's
    bullet.
    """
    lines = src.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines)
                     if 'WHAT THE REDACTED COPY STILL CONTAINS' in ln)
    except StopIteration:
        return []
    out = []
    for ln in lines[start:]:
        if not ln.startswith('#'):
            break
        if re.match(r'#\s{2,},\s', ln):
            out.append(ln)
    return out


def notice_bullets(body: str) -> list:
    return [ln for ln in body.splitlines() if ln.startswith('  - ')]


def check_found():
    print('file: the notice is where the launcher will look for it')
    body, why = beta_notice.text_or_reason()
    check('file: it is found in a checkout', why, None)
    check('file: it is not empty', bool(body and body.strip()))
    check('file: it sits beside the module that loads it',
          os.path.dirname(beta_notice.path()), os.path.join(ROOT, 'server'))
    check('file: its first line is a title a panel can use',
          beta_notice.title(body), 'Before you join the beta')
    # Control: a build without the file returns a reason, not an empty notice.
    saved = beta_notice.NOTICE_NAME
    try:
        beta_notice.NOTICE_NAME = 'no_such_notice.txt'
        missing, reason = beta_notice.text_or_reason()
        check('file CONTROL: a build without it returns None and a reason',
              (missing, reason is not None), (None, True))
        check('file CONTROL: and the reason names every path it tried',
              'no_such_notice.txt' in (reason or ''))
    finally:
        beta_notice.NOTICE_NAME = saved
    return body


def check_the_five(body: str):
    """The topics the notice covers, so an edit that drops one is caught."""
    print('content: what this notice undertakes to say')
    low = flat(body).lower()
    check('content: it asks players not to cheat', 'cheat' in low)
    check('content: it says an absent player may be removed',
          'miss' in low and ('kick' in low or 'remov' in low))
    check('content: it warns the galaxy may be restarted',
          'restart' in low and ('any time' in low or 'anytime' in low))
    check('content: it says there may be serious bugs', 'bug' in low)
    check('content: and where to report them', 'github.com' in low)

def check_placeholders(body: str):
    """No invented value, and no marker left showing.

    `log_upload` is the only placeholder key.
    """
    print('placeholders: nothing invented, nothing left showing')
    check('placeholders: no marker survives in the shipped notice',
          beta_notice.markers(body), [])
    check('placeholders: log_upload is the only key still defined',
          list(beta_notice.KEYS), ['log_upload'])
    check('placeholders: and it is described for whoever fills it',
          len(beta_notice.KEYS['log_upload']) > 20)
    check('placeholders: nothing reports as unfilled',
          beta_notice.unfilled(body), [])
    check('placeholders CONTROL: a marker added back is seen',
          beta_notice.unfilled(body + chr(10) +
                               '[SET BEFORE THE BETA OPENS: x, a thing]'),
          ['x'])

def check_against_launcher(body: str):
    """The notice makes no claim about the diagnostic log.

    If the log is ever sent anywhere, the notice must describe it, and this
    check should hold it to the launcher's redaction list.
    """
    print('log: the notice no longer describes the diagnostic copy')
    low = flat(body).lower()
    check('log: it makes no claim about what leaves the machine',
          all(w not in low for w in ('log', 'upload', 'diagnostic')))

def check_against_code(body: str):
    """Every claim the notice makes is true of the code."""
    print('claims: the rest of the notice against the code it describes')
    low = flat(body).lower()
    ab = os.path.join(ROOT, 'server', 'abandonment.py')
    check('claims: an absent player really is removed',
          os.path.exists(ab) and 'def enforce' in read(ab))
    check('claims: and the notice says they may be', 'kick' in low or
          'remov' in low)
    ts = os.path.join(ROOT, 'server', 'turn_store.py')
    check('claims: a galaxy really can be closed',
          os.path.exists(ts) and 'def close' in read(ts))
    check('claims: and the notice says it may be restarted',
          'restart' in low)
    check('claims: the notice invents no number', not any(
        w in low for w in ('six turns', '6 turns', '12 turns', 'four hours')))

def check_tone(body: str):
    print('tone: written for a player, not for this repo')
    low = flat(body)
    for word in ('blob', 'store spec', 'referee', 'civ ', 'canonical hash',
                 'firestore', 'turn_store', 'roster'):
        check(f'tone: no codebase word "{word.strip()}"', word in low, False)
    check('tone: no em-dashes', '\u2014' in body, False)
    longest = max((len(ln) for ln in body.splitlines()), default=0)
    check('tone: no line is wider than a small window', longest <= 80)
    check('tone: short enough to be read rather than skipped',
          len(body.split()) < 1200)


def main():
    body = check_found()
    if not body:
        print('\nthe notice could not be read; nothing else here means '
              'anything')
        return 1
    check_the_five(body)
    check_placeholders(body)
    check_against_launcher(body)
    check_against_code(body)
    check_tone(body)

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    for name in FAIL:
        print(f'  failed: {name}')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
