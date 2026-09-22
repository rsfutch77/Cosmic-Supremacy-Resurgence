"""
test_beta_notice.py , the player notice says what the code actually does
========================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_beta_notice.py

The notice is the one file in the tree whose readers are strangers, and the
only way it can be wrong is by drifting away from the code it describes. Prose
does not fail a test on its own, so each check here is tied to something that
does change: a constant, a function, or a list written in another file.

The section that matters most is the one about the diagnostic log, because it
is the only part a player is asked to act on. It is written from the itemised
list in `release/launcher.py`'s redaction section header, and that list is
counted here. A bullet added there and not added to the notice fails this test,
which is the only thing that keeps the two from drifting.

Nothing here edits `release/launcher.py`; it is read as text.
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
PLAN = os.path.join(ROOT, 'docs', 'Public_Beta_Plan.md')

PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def flat(body: str) -> str:
    """The notice as one lowercase line.

    The file is hard-wrapped for reading, so a phrase a player sees as one
    sentence is two lines in the bytes. Searching the raw text would fail on
    where the wrap happened rather than on what it says, which is a check
    failing for the wrong reason.
    """
    return ' '.join(body.split()).lower()


def read(path):
    with open(path, encoding='utf-8') as fh:
        return fh.read()


def launcher_bullets(src: str) -> list:
    """The itemised list under WHAT THE REDACTED COPY STILL CONTAINS.

    Each item begins a comment line with a lone comma, which is that file's
    bullet. Counting them is how the notice is held to the list rather than to
    somebody's memory of it.
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


# ── checks ───────────────────────────────────────────────────────────────────
def check_found():
    print('file: the notice is where the launcher will look for it')
    body, why = beta_notice.text_or_reason()
    check('file: it is found in a checkout', why, None)
    check('file: it is not empty', bool(body and body.strip()))
    check('file: it sits beside the module that loads it',
          os.path.dirname(beta_notice.path()), os.path.join(ROOT, 'server'))
    check('file: its first line is a title a panel can use',
          beta_notice.title(body), 'Before you join the beta')
    # The control: a build that does not carry the file must say so rather
    # than hand back an empty notice that reads as "there are no rules".
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
    """Each of the five things N4 names has to be in there, in plain words."""
    print('content: all five of the things a player has to be told')
    low = flat(body)
    check('content: a name is a claim, not a credential',
          'not a password' in low and 'no account' in low)
    check('content: cheating is possible and easy',
          'cheating is possible' in low and 'easy' in low)
    check('content: the whole galaxy is sent to every player',
          'entire state of the galaxy' in low)
    check('content: and a modified game sees all of it',
          'modified copy' in low and 'whole map' in low)
    check('content: an inactive empire is deleted',
          'removed from the galaxy' in low and 'missed turns before deletion'
          in low)
    check('content: nobody inherits it, which is the part players assume',
          'nobody inherits' in low)
    check('content: the galaxy can be ended at any time',
          'can end it' in low and 'no end date' in low)
    check('content: and that nothing survives it',
          'nothing you build here is permanent' in low)
    check('content: the diagnostic copy is described',
          'launcher' in low and 'log' in low)


def check_placeholders(body: str):
    print('placeholders: undecided values read as blanks, not as numbers')
    pending = beta_notice.unfilled(body)
    check('placeholders: the warning threshold is a marker',
          'warn_after_misses' in pending)
    check('placeholders: the deletion threshold is a marker',
          'reclaim_after_misses' in pending)
    check('placeholders: whether the log is sent automatically is a marker',
          'log_upload' in pending)
    check('placeholders: no invented number stands next to a threshold',
          re.search(r'miss \d+', flat(body)), None)
    check('placeholders: every key is described for whoever fills it',
          sorted(set(pending)), sorted(beta_notice.KEYS))

    print('placeholders: filling them, which is what the launcher does')
    filled = beta_notice.fill({'warn_after_misses': 6,
                               'reclaim_after_misses': 12,
                               'log_upload': 'It is sent only when you ask'},
                              body)
    check('placeholders: a filled notice has none left',
          beta_notice.unfilled(filled), [])
    check('placeholders: and reads as a sentence',
          'miss 6 turns in a row and the launcher will warn you'
          in flat(filled))
    # The control. A half-filled notice is as unfinished as an empty one, and
    # a fill() that quietly dropped the markers it had no value for would hide
    # exactly that.
    half = beta_notice.fill({'warn_after_misses': 6}, body)
    check('placeholders CONTROL: a half-filled notice still reports the rest',
          beta_notice.unfilled(half),
          ['reclaim_after_misses', 'log_upload'])
    check('placeholders CONTROL: and the unfilled one still shows its marker',
          'SET BEFORE THE BETA OPENS' in half)

    print('placeholders: the thresholds belong to the code, not to this file')
    try:
        import abandonment
    except ImportError:
        check('placeholders: server/abandonment.py holds them', False)
        return
    warn, reclaim = abandonment.thresholds({})
    check('placeholders: the store side really has a pair to fill them with',
          isinstance(warn, int) and isinstance(reclaim, int))
    check('placeholders: neither number is written into the notice',
          str(warn) in body or str(reclaim) in body, False)


def check_against_launcher(body: str):
    print('log: the list matches the launcher\'s own, item for item')
    if not os.path.exists(LAUNCHER):
        check('log: release/launcher.py is readable', False)
        return
    src = read(LAUNCHER)
    bullets = launcher_bullets(src)
    check('log: the launcher still itemises what the copy contains',
          len(bullets) > 0)
    check('log: the notice lists the same number of things',
          len(notice_bullets(body)), len(bullets))

    low = flat(body)
    # One phrase per launcher bullet. A phrase rather than the whole sentence,
    # because the notice is written for a player and the launcher's line is
    # written for a programmer, and they are allowed to read differently.
    for what, phrase in (
            ('the player name', 'your player name'),
            ('the galaxy and where it is', 'which galaxy you are playing'),
            ('the referee machine in a UNC path', 'name of the machine'),
            ('the save request head', 'the turn number'),
            ('which modes ran', 'which parts of the game were started'),
            ('the install layout', 'folder layout'),
            ('the AI reasoning', "computer opponent's reasoning"),
            ('timestamps', 'the time, to the second')):
        check(f'log: the notice covers {what}', phrase in low)

    print('log: the two things M2 leaves unscrubbed are named')
    check('log: a referee machine name in a path is named',
          'stays in the path' in low)
    check('log: an account name outside a path is named',
          'is not a file path' in low)

    print('log: the claims about the copy match the code')
    check('log: the account name really is taken out of paths',
          'USER_MARK' in src and 'scrub_paths' in src)
    check('log: the notice says so', 'account name is taken out' in low)
    check('log: there really is a cap', 'LOG_UPLOAD_CAP' in src)
    check('log: and the tail really is what is kept',
          '_keep_tail' in src and 'keeping the' in src)
    check('log: the notice says the oldest part goes',
          'oldest part is dropped' in low)
    check('log: the notice does not quote a cap that could change',
          '256' in body, False)


def check_against_code(body: str):
    print('claims: the rest of the notice against the code it describes')
    low = flat(body)
    fb_auth = os.path.join(ROOT, 'server', 'fb_auth.py')
    check('claims: an anonymous id really is registered',
          os.path.exists(fb_auth) and 'accounts:signUp' in read(fb_auth))
    check('claims: the notice says so', 'anonymous id' in low)
    check('claims: and that the player never sees it', 'never see it' in low)

    plan = read(PLAN) if os.path.exists(PLAN) else ''
    check('claims: four hours is the decided turn length',
          'turn length | 4 hours' in plan)
    check('claims: the notice says four hours',
          'a turn lasts four hours' in low)
    check('claims: the seat is not passed on, which the plan decided',
          'the seat is not passed to anyone' in plan)
    check('claims: the notice says nobody inherits it', 'nobody inherits' in low)
    check('claims: one galaxy, no season timer',
          'no season timer' in plan and 'no season' in low)


def check_tone(body: str):
    print('tone: written for a player, not for this repo')
    low = flat(body)
    for word in ('blob', 'store spec', 'referee', 'civ ', 'canonical hash',
                 'firestore', 'turn_store', 'roster'):
        check(f'tone: no codebase word "{word.strip()}"', word in low, False)
    check('tone: no em-dashes', '—' in body, False)
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
