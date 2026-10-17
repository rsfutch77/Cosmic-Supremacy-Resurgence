"""
beta_notice.py: finds the notice every player reads before joining.

    import beta_notice
    text = beta_notice.text()               # raises if it is not there
    text, why = beta_notice.text_or_reason()  # None and a reason instead

The text lives in `beta_notice.txt` beside this module and is shown at the step
where a player names themselves and picks a galaxy, before joining.

The notice carries `[SET BEFORE THE BETA OPENS: <key>, ...]` markers.
`unfilled()` lists the keys, `markers()` the whole text of each, and `fill()`
replaces them. A notice that still carries a marker must not be shown.

A PyInstaller build must add the .txt as a data file; without it `text()` raises.
"""
import os
import re
import sys

NOTICE_NAME = 'beta_notice.txt'

# A marker the operator replaces before the beta opens. The first word after the
# colon is its key. A marker can wrap across lines up to the closing bracket.
UNFILLED = re.compile(r'\[SET BEFORE THE BETA OPENS:\s*([A-Za-z_]+),[^\]]*\]')

# What each key is, and where its value comes from. Every key has a marker in
# the notice text.
KEYS = {
    'log_upload':
        'one clause saying whether the redacted log copy is sent '
        'automatically or only when the player chooses to send it; M1 sends '
        'it only when the player presses Send',
}

HERE = os.path.dirname(os.path.abspath(__file__))


def candidates() -> list:
    """Every place the notice can be, in search order: `sys._MEIPASS`, this
    module's directory, then the frozen executable's directory."""
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
    """(text, None), or (None, reason) for a caller that cannot raise."""
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

    A key with no value given keeps its marker, so `unfilled` still finds it.
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
