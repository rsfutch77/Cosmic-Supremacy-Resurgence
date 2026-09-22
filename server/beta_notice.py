"""
beta_notice.py , the text every player reads before they join
=============================================================
    import beta_notice
    text = beta_notice.text()               # raises if it is not there
    text, why = beta_notice.text_or_reason()  # None and a reason instead

The words live in `beta_notice.txt` beside this module, because they are for
players rather than for programmers and whoever edits them next should not have
to open a source file to do it. This module only finds that file and reports
what is unfinished in it.

Five things are in the notice, and each is a property the design accepted
rather than a fault waiting to be fixed: a name is a claim and not a
credential, every player's machine is sent the whole galaxy so a modified game
is a maphack, an inactive empire is deleted, the galaxy can be ended at any
time, and what a diagnostic log copy contains. The last of those is written
from the itemised list in `release/launcher.py`'s own redaction section rather
than from a summary of it, since that list is what the code actually produces.

Where it has to be shown
------------------------
Before a player joins a galaxy, and not behind a link. The done condition for
this text is that it is shown before joining rather than buried, so the place
for it is the step where a player names themselves and picks a galaxy, with
joining on the far side of it. A page reached from a help menu does not satisfy
it.

Unfilled numbers
----------------
The notice carries `[SET BEFORE THE BETA OPENS: <key>, ...]` markers where a
value is not this file's to decide. `unfilled()` lists the keys, `markers()`
the whole text of each, and `fill()` replaces them.

Two of the three are a galaxy's own abandonment thresholds, which
`server/abandonment.py` holds per galaxy with defaults behind them, so the
right values are the ones the galaxy being joined actually carries and the
caller passes them in. Showing a notice that still carries a marker tells a
player a rule with a blank where the rule should be, so whatever displays this
should refuse to open the beta rather than show it, and say which key is still
missing.

Finding the file in a frozen build
----------------------------------
A checkout finds it beside this module. A PyInstaller build unpacks data files
into `sys._MEIPASS`, and only if the build was told to carry them: a `.txt` is
not picked up by the analysis the way an imported module is. So the build has
to add this file, and if it did not, `text()` raises and names the file rather
than returning an empty notice. A notice that silently comes out blank is worse
than one that is missing, because nothing downstream can tell the difference.
"""
import os
import re
import sys

NOTICE_NAME = 'beta_notice.txt'

# The marker an operator has to replace before the beta opens. The first word
# after the colon is the marker's key, so a caller holding the value can fill it
# without matching prose that someone may reword. A marker wraps across lines in
# the file, so everything up to the closing bracket is part of it.
UNFILLED = re.compile(r'\[SET BEFORE THE BETA OPENS:\s*([A-Za-z_]+),[^\]]*\]')

# What each key is, and where its value comes from. Named here so a caller can
# see what it has to supply without reading the notice for the list.
KEYS = {
    'warn_after_misses':
        'consecutive missed turns before the player is warned, which is the '
        'first of abandonment.thresholds(store.state()) for the galaxy being '
        'joined',
    'reclaim_after_misses':
        'consecutive missed turns before the empire is deleted, the second of '
        'that pair for the same galaxy',
    'log_upload':
        'one clause saying whether the redacted log copy is sent '
        'automatically or only when the player chooses to send it, which is '
        'M1 to settle',
}

HERE = os.path.dirname(os.path.abspath(__file__))


def candidates() -> list:
    """Every place the notice can legitimately be, in order.

    `sys._MEIPASS` first because a frozen build is the case that has to work
    for players, and `HERE` in a frozen build names the same unpack directory
    anyway. The launcher's own directory is last, so a build that ships the
    notice beside the executable rather than inside it still finds it.
    """
    out = []
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        out.append(os.path.join(meipass, NOTICE_NAME))
    out.append(os.path.join(HERE, NOTICE_NAME))
    if getattr(sys, 'frozen', False):
        out.append(os.path.join(os.path.dirname(os.path.abspath(sys.executable)),
                                NOTICE_NAME))
    seen, unique = set(), []
    for path in out:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def path() -> str:
    """Where the notice is, or raise naming everywhere that was looked."""
    tried = candidates()
    for candidate in tried:
        if os.path.exists(candidate):
            return candidate
    raise FileNotFoundError(
        f'{NOTICE_NAME} is not in this build. A frozen build has to carry it '
        f'as a data file. Looked in: ' + ', '.join(tried))


def text() -> str:
    """The notice, exactly as written."""
    with open(path(), encoding='utf-8') as fh:
        return fh.read()


def text_or_reason():
    """(text, None), or (None, reason) for a caller that cannot raise.

    The reason is worth showing rather than swallowing. A launcher that found
    no notice must not open the beta silently: the rules a player is agreeing
    to are the thing that is missing.
    """
    try:
        return text(), None
    except OSError as exc:
        return None, f'{type(exc).__name__}: {exc}'


def unfilled(body: str = None) -> list:
    """Keys still waiting on the operator, in the order they appear."""
    return UNFILLED.findall(text() if body is None else body)


def markers(body: str = None) -> list:
    """The whole of each unfilled marker, for a message that has to show one."""
    return [m.group(0) for m in
            UNFILLED.finditer(text() if body is None else body)]


def fill(values: dict, body: str = None) -> str:
    """The notice with each named marker replaced by its value.

    Two of the three keys are a galaxy's own thresholds rather than anything
    fixed, so the caller passes what the galaxy being joined actually says and
    this module invents nothing. A key with no value given keeps its marker,
    which `unfilled` then still finds: a notice half filled has to be as
    obviously unfinished as one not filled at all.
    """
    body = text() if body is None else body

    def swap(match):
        key = match.group(1)
        return str(values[key]) if key in values else match.group(0)

    return UNFILLED.sub(swap, body)


def title(body: str = None) -> str:
    """The notice's first line, for whatever heads the panel showing it."""
    return (text() if body is None else body).splitlines()[0].strip()


def main(argv=None) -> int:
    """Print the notice, and say what in it is still the operator's to fill."""
    body, why = text_or_reason()
    if body is None:
        print(why)
        return 2
    print(body)
    pending = markers(body)
    if pending:
        print(f'\n{len(pending)} unfilled marker(s), and the beta should not '
              f'open while any remain:')
        for marker in pending:
            print(f'  {" ".join(marker.split())}')
        for key in unfilled(body):
            print(f'    {key}: {KEYS.get(key, "no description recorded")}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
