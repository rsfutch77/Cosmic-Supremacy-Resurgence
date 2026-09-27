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
    """What the notice actually undertakes to say.

    It used to assert all five of N4's topics plus the diagnostic copy. The
    notice was rewritten to four short bullets by an editorial decision, and
    asserting the old list against it would only record that the file no longer
    matches a plan item. What is asserted instead is what this notice claims, so
    that a later edit which drops one of these is still caught.

    **What it no longer says**, recorded here rather than silently dropped: that
    a name is a claim and not a credential, that every player holds the whole
    galaxy and a modified client is a maphack, that nobody inherits a deleted
    empire, and what leaves the player's computer. If any of those is meant to
    reach a player it now has to reach them somewhere else. The maphack and the
    data-collection points are the two worth being deliberate about.
    """
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

    The two miss thresholds were markers here and are gone with the sentences
    that carried them: the notice states no turn limits at all now, so there is
    nothing per galaxy for it to be given. `log_upload` is the only key left and
    M1 has still not settled it.
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
    """The notice no longer describes the diagnostic copy, so there is nothing
    to hold item for item against the launcher's own list.

    This used to count the bullets in `launcher.py`'s redaction section and
    require the notice to carry one phrase for each, so that a line added there
    and not here failed. The rewritten notice does not mention the log at all.
    That coupling is therefore gone, and with it the guarantee that what the
    launcher collects is what a player was told it collects.

    Recorded rather than deleted outright: if the diagnostic copy is ever sent
    anywhere, M1, then somewhere has to say so and this check is the shape of
    what should guard it.
    """
    print('log: the notice no longer describes the diagnostic copy')
    low = flat(body).lower()
    check('log: it makes no claim about what leaves the machine',
          all(w not in low for w in ('log', 'upload', 'diagnostic')))

def check_against_code(body: str):
    """Every claim the notice makes has to be true of the code.

    The list shrank with the notice. What it used to check, and cannot now,
    is recorded in `check_the_five`: the anonymous id, the four-hour turn, that
    nobody inherits a deleted empire, and that there is one galaxy with no
    season timer are all things the notice no longer says. A notice that says
    less needs fewer of these, and the rule that survives is the one that
    matters: it must not say anything the code does not do.
    """
    print('claims: the rest of the notice against the code it describes')
    low = flat(body).lower()
    # An absent player really is removed, and by this code.
    ab = os.path.join(ROOT, 'server', 'abandonment.py')
    check('claims: an absent player really is removed',
          os.path.exists(ab) and 'def enforce' in read(ab))
    check('claims: and the notice says they may be', 'kick' in low or
          'remov' in low)
    # The galaxy really can be ended by the operator, and really is restartable.
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
