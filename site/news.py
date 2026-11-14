"""
news.py: the home page's news items, one file each in site/news/

A file is named `YYYY-MM-DD-anything.html`. Its first line is the title, as
plain text; the rest is the body, as HTML. `stage.py` renders every item,
newest first, where the home page override holds the marker.
"""
import html
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
NEWS_DIR = os.path.join(HERE, "news")
MARKER = "<!-- news items -->"
NAME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})-.+\.html$")

ITEM = """<tr><td valign='top' width='100%'>

\t<table class='content_pane'>
\t\t<tr>
\t\t\t<td class='content_heading' width='100%'>
\t\t\t\t{title}<span class='newsdate'>Posted on {date}</span>
\t\t\t</td>
\t\t</tr>
\t</table>

\t<table class='content_pane'>
\t\t<tr>
\t\t\t<td valign='top' colspan='2'>
{body}
\t\t\t</td>
\t\t</tr>
\t</table>

</td></tr>
"""


def items(folder=NEWS_DIR):
    """(date, title, body) for each item, newest first. Same-day items are
    ordered by file name, last first."""
    out = []
    if not os.path.isdir(folder):
        return out
    for fn in os.listdir(folder):
        m = NAME_RE.match(fn)
        if not m:
            continue
        with open(os.path.join(folder, fn), encoding="utf-8") as fh:
            title, _nl, body = fh.read().partition("\n")
        out.append((fn, "%s.%s.%s" % (m.group(3), m.group(2), m.group(1)),
                    title.strip(), body.strip()))
    out.sort(reverse=True)
    return [(date, title, body) for _fn, date, title, body in out]


def render(rows):
    return "\n".join(ITEM.format(title=html.escape(title), date=date, body=body)
                     for date, title, body in rows)


def render_into(path, folder=NEWS_DIR):
    """Replace the marker in the page at `path` with the items, and return how
    many there were. A page without the marker is left alone and gets None."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    if MARKER not in text:
        return None
    rows = items(folder)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text.replace(MARKER, render(rows)))
    return len(rows)
