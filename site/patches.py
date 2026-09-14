"""Site-wide edits applied to every staged page.

Some changes cannot be expressed as a file in `overrides/`. The navigation bar
and the support email addresses appear in all 425 archived pages, so replacing
them page by page would mean 425 near-identical override files that all have to
be regenerated whenever the archive is re-staged.

Anything here runs against every page during staging, after the archived markup
has been link-rewritten and before the attribution footer is injected. Add
per-page changes to `overrides/` instead; only put a patch here when it genuinely
applies site-wide.

Each patch is `(description, function)` where the function takes the page HTML
and returns it changed or unchanged. `stage.py` reports how many pages each one
touched, which is the quickest way to notice a patch that has silently stopped
matching after the archive is re-staged.
"""

import re

GITHUB = "https://github.com/rsfutch77/Cosmic-Supremacy-Resurgence"
GITHUB_ISSUES = GITHUB + "/issues"

# The menu is a fixed-width strip. The eight original tabs were sized to total
# exactly this; drop one and the remainder have to be resized or the bar ends
# short of its own background image.
MENU_TOTAL_PX = 990

_MENU_RE = re.compile(r"(<ul id='menu'>)(.*?)(</ul>)", re.S)
_ITEM_RE = re.compile(r"<li\b[^>]*>.*?</li>", re.S)


def _set_width(fragment, px):
    return re.sub(r"width:\s*\d+px;", "width:%dpx;" % px, fragment)


def remove_live_chat(html):
    """Drop the Live Chat tab and re-space the survivors across the bar.

    Live chat needs a server nobody is running, so the tab only ever leads to an
    archived snapshot of an empty room.
    """
    m = _MENU_RE.search(html)
    if not m:
        return html
    items = _ITEM_RE.findall(m.group(2))
    kept = [it for it in items
            if not re.search(r">\s*LIVE CHAT\s*<", it, re.I)]
    if len(kept) == len(items):
        return html

    # Distribute the freed pixels so the widths still sum to the bar width.
    base, extra = divmod(MENU_TOTAL_PX, len(kept))
    resized = []
    for i, it in enumerate(kept):
        px = base + (1 if i >= len(kept) - extra else 0)
        it = _set_width(it, px)
        # first/last carry rounded corners, so the classes must follow the move
        it = re.sub(r"class='(?:first|last)'", "class=''", it)
        if i == 0:
            it = it.replace("<li class=''", "<li class='first'", 1)
        elif i == len(kept) - 1:
            it = it.replace("<li class=''", "<li class='last'", 1)
        resized.append(it)

    return html[:m.start()] + m.group(1) + "".join(resized) + m.group(3) + html[m.end():]


_MAILTO_RE = re.compile(
    r"<a\s+href=['\"]mailto:[A-Za-z0-9._%+-]+@cosmicsupremacy\.com[^'\"]*['\"][^>]*>"
    r".*?</a>", re.S | re.I)


def redirect_support_email(html):
    """Point the old support addresses at GitHub issues.

    Nobody receives mail at cosmicsupremacy.com any more, so a mailto: is a dead
    end that looks like a working contact route.
    """
    html = _MAILTO_RE.sub(
        '<a href="%s" target="_blank">report it on GitHub</a>' % GITHUB_ISSUES,
        html)
    # One address appears as prose rather than as a link.
    html = re.sub(
        r"The e-mail will be sent from admin@cosmicsupremacy\.com\.",
        "Account e-mail is not running yet.", html)
    # Anything left over, so no dead address survives a future re-stage.
    html = re.sub(r"[A-Za-z0-9._%+-]+@cosmicsupremacy\.com",
                  "the project's GitHub issues", html)
    return html


def _drop_subtab(html, href_fragment):
    """Remove one tab from a sub-tab strip and re-mark first/last."""
    pat = re.compile(
        r"<li[^>]*>\s*<a\s+href=['\"][^'\"]*" + href_fragment
        + r"[^'\"]*['\"][^>]*>.*?</a>\s*</li>", re.S | re.I)
    m = pat.search(html)
    if not m:
        return html
    html = html[:m.start()] + html[m.end():]
    # Whichever tab is now last has to carry the rounded corner.
    tabs = re.search(r"(<ul id='tab'>)(.*?)(</ul>)", html, re.S)
    if tabs:
        items = re.findall(r"<li.*?</li>", tabs.group(2), re.S)
        if items:
            fixed = []
            for i, it in enumerate(items):
                it = re.sub(r"class='(?:first|last)'", "class=''", it)
                if i == 0:
                    it = it.replace("<li class=''", "<li class='first'", 1)
                if i == len(items) - 1:
                    it = it.replace("<li class=''", "<li class='last'", 1)
                fixed.append(it)
            html = (html[:tabs.start()] + tabs.group(1) + "".join(fixed)
                    + tabs.group(3) + html[tabs.end():])
    return html


def remove_firewall_subtab(html):
    """The advice was about opening ports to a server that no longer exists."""
    return _drop_subtab(html, r"download_firewall\.php")


def remove_tools_subtab(html):
    """Hidden for now: the tools were helper spreadsheets for a live game."""
    return _drop_subtab(html, r"wiki/tools")


def remove_wallpapers_subtab(html):
    """Only thumbnails survived the archive, so the page is not worth showing."""
    return _drop_subtab(html, r"wiki/wallpapers")


_CONTACT_RE = re.compile(
    r"<a\s[^>]*href=['\"][^'\"]*(?:support_)?contact\.php[^'\"]*['\"][^>]*>(.*?)</a>",
    re.S | re.I)


def redirect_contact_links(html):
    """Repoint every 'contact support' link at GitHub issues.

    These are scattered through the wiki as absolute links to contact.php on the
    original domain, which is dead. Doing it here rather than per page catches
    the duplicate copies DokuWiki left behind under _export/ and */index.html.
    """
    return _CONTACT_RE.sub(
        lambda m: '<a href="%s" target="_blank">%s</a>' % (GITHUB_ISSUES, m.group(1)),
        html)


# Only href/src/action are rewritten. title= and og:image content= keep the
# original absolute URL: they are not clickable, and leaving them records what
# the address used to be.
_ABS_SELF_RE = re.compile(
    r"(?P<attr>(?:href|src|action)\s*=\s*(?P<q>[\"']))"
    r"https?://(?:www\.)?cosmicsupremacy\.com"
    r"(?P<path>/[^\"']*)(?P=q)", re.I)


def localise_self_links(html):
    """Rewrite absolute links back to this site as site-relative paths.

    The archive is full of links that spell out the original domain. Left alone
    they point at a host that no longer answers, which is indistinguishable from
    a broken site. Made relative, they either hit the restored page or land on
    the "not restored yet" 404, which is the honest answer either way.
    """
    return _ABS_SELF_RE.sub(
        lambda m: m.group("attr") + m.group("path") + m.group("q"), html)


PATCHES = [
    ("remove the Live Chat tab", remove_live_chat),
    ("remove the Firewall sub-tab", remove_firewall_subtab),
    ("hide the Tools sub-tab", remove_tools_subtab),
    ("remove the Wallpapers sub-tab", remove_wallpapers_subtab),
    ("point contact links at GitHub", redirect_contact_links),
    ("localise absolute self-links", localise_self_links),
    ("point support e-mail at GitHub", redirect_support_email),
]


def apply_all(html):
    """Run every patch. Returns (html, set of descriptions that changed it)."""
    hit = set()
    for name, fn in PATCHES:
        new = fn(html)
        if new != html:
            hit.add(name)
            html = new
    return html, hit
