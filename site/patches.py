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


def remove_todo_subtab(html):
    """The list is captured in cosmic_supremacy_original_todo.csv and retired.

    It tracked a live game's backlog against a forum that is not published,
    so every item linked somewhere that cannot be read.
    """
    return _drop_subtab(html, r"wiki/dev")


def remove_tools_subtab(html):
    """Hidden for now: the tools were helper spreadsheets for a live game."""
    return _drop_subtab(html, r"wiki/tools")


def remove_wallpapers_subtab(html):
    """Only thumbnails survived the archive, so the page is not worth showing."""
    return _drop_subtab(html, r"wiki/wallpapers")


_CONTACT_RE = re.compile(
    r"<a\s[^>]*href=['\"][^'\"]*(?:support_)?contact\.php[^'\"]*['\"][^>]*>(.*?)</a>",
    re.S | re.I)


_TABSTRIP_RE = re.compile(r"<ul id='tab'>.*?</ul>", re.S)


def redirect_contact_links(html):
    """Repoint 'contact support' links in page bodies at GitHub issues.

    These are scattered through the wiki as absolute links to contact.php on the
    original domain, which is dead. Doing it here rather than per page catches
    the duplicate copies DokuWiki left behind under _export/ and */index.html.

    The sub-tab strip is left alone. Its Contact tab is part of the site's
    navigation and should keep both its label and its destination; the Contact
    page it leads to is what carries the GitHub link.
    """
    strip = _TABSTRIP_RE.search(html)
    if not strip:
        return _CONTACT_RE.sub(_to_github, html)
    head, tabs, tail = html[:strip.start()], strip.group(0), html[strip.end():]
    return _CONTACT_RE.sub(_to_github, head) + tabs + _CONTACT_RE.sub(_to_github, tail)


def _to_github(m):
    return '<a href="%s" target="_blank">%s</a>' % (GITHUB_ISSUES, m.group(1))


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


_BEACON_RE = re.compile(
    r"<div class=\"no\"><img[^>]*indexer\.php[^>]*/?>\s*</div>", re.I)
_BEACON_IMG_RE = re.compile(r"<img[^>]*indexer\.php[^>]*/?>", re.I)


def strip_indexer_beacons(html):
    """Remove DokuWiki's indexer pixel.

    It is a 1x1 tracking image pointing at a PHP endpoint that was never
    archived. It renders as a broken-image icon and marks 165 references as
    missing, drowning out the images that a reader would actually notice.
    """
    html = _BEACON_RE.sub("", html)
    return _BEACON_IMG_RE.sub("", html)


# Fourteen of the twenty smiley GIFs this wiki used were never archived, and the
# six that survive are 2008-era 15px GIFs. Mixing the two looks broken, most
# obviously on the DokuWiki syntax page that lists them all in one table, so all
# of them become Unicode emoji: nothing to host, nothing to license, and they
# scale with the surrounding text.
#
# Keys are the image filename; values are (emoji, the alt text DokuWiki used).
SMILEYS = {
    "icon_smile.gif":     ("\N{SLIGHTLY SMILING FACE}", ":-)"),
    "icon_smile2.gif":    ("\N{SMILING FACE WITH SMILING EYES}", "=)"),
    "icon_biggrin.gif":   ("\N{GRINNING FACE WITH SMILING EYES}", ":-D"),
    "icon_wink.gif":      ("\N{WINKING FACE}", ";-)"),
    "icon_sad.gif":       ("\N{SLIGHTLY FROWNING FACE}", ":-("),
    "icon_cool.gif":      ("\N{SMILING FACE WITH SUNGLASSES}", "8-)"),
    "icon_confused.gif":  ("\N{CONFUSED FACE}", ":-?"),
    "icon_doubt.gif":     ("\N{FACE WITH ONE EYEBROW RAISED}", ":-/"),
    "icon_doubt2.gif":    ("\N{THINKING FACE}", ":-\\"),
    "icon_eek.gif":       ("\N{FACE SCREAMING IN FEAR}", "8-O"),
    "icon_surprised.gif": ("\N{FACE WITH OPEN MOUTH}", ":-O"),
    "icon_neutral.gif":   ("\N{NEUTRAL FACE}", ":-|"),
    "icon_lol.gif":       ("\N{FACE WITH TEARS OF JOY}", "LOL"),
    "icon_razz.gif":      ("\N{FACE WITH STUCK-OUT TONGUE}", ":-P"),
    "icon_fun.gif":       ("\N{GRINNING CAT FACE WITH SMILING EYES}", "^_^"),
    "icon_silenced.gif":  ("\N{ZIPPER-MOUTH FACE}", ":-X"),
    "icon_exclaim.gif":   ("\N{HEAVY EXCLAMATION MARK SYMBOL}", ":!:"),
    "icon_question.gif":  ("\N{BLACK QUESTION MARK ORNAMENT}", ":?:"),
    "fixme.gif":          ("\N{WARNING SIGN}", "FIXME"),
    "delete.gif":         ("\N{CROSS MARK}", "DELETEME"),
    "wink.gif":           ("\N{WINKING FACE}", ";-)"),
}

_SMILEY_IMG_RE = re.compile(
    r'<img[^>]*?src\s*=\s*(?:"(?P<dq>[^"]*)"|\'(?P<sq>[^\']*)\')[^>]*>',
    re.I)


def modernise_smileys(html):
    """Swap smiley images for the equivalent emoji."""

    def sub(m):
        src = m.group("dq")
        if src is None:
            src = m.group("sq")
        low = src.lower()
        if "/smileys/" not in low and "/smilies/" not in low:
            return m.group(0)
        name = low.rsplit("/", 1)[-1].split("?")[0]
        entry = SMILEYS.get(name)
        if not entry:
            return m.group(0)
        emoji, alt = entry
        return ('<span class="smiley" title="%s" role="img" '
                'aria-label="%s">%s</span>' % (alt, alt, emoji))

    return _SMILEY_IMG_RE.sub(sub, html)


# Anchors pointing at the two retired ToDo pages. Most are written relative
# ("../dev.html"), so matching on a full path would miss them.
_ANCHOR_RE = re.compile(
    r"<a[^>]*href=(?:\"([^\"]*)\"|'([^']*)')[^>]*>.*?</a>", re.S | re.I)
_LI_RE = re.compile(r"<li[^>]*>.*?</li>", re.S | re.I)
_BCSEP_RE = re.compile(r"\s*<span class=\"bcsep\">[^<]*</span>", re.I)


def _is_retired(href):
    path = href.split("?")[0].split("#")[0].lower()
    return path.endswith("/dev.html") or path == "dev.html" or "todo-guide" in path


def remove_retired_links(html):
    """Drop links to the retired ToDo pages.

    Both were captured to cosmic_supremacy_original_todo.csv before removal.
    A list entry goes whole, because the text beside it describes a page that
    no longer exists. Anywhere else the anchor goes on its own, taking a
    following breadcrumb separator with it so no trail ends in a chevron.
    """

    def li_holds_retired(m):
        for am in _ANCHOR_RE.finditer(m.group(0)):
            if _is_retired(am.group(1) or am.group(2) or ""):
                return ""
        return m.group(0)

    html = _LI_RE.sub(li_holds_retired, html)

    def drop_anchor(m):
        if _is_retired(m.group(1) or m.group(2) or ""):
            return ""
        return m.group(0)

    html = _ANCHOR_RE.sub(drop_anchor, html)
    # A separator left stranded at the start of a trail or doubled up.
    html = re.sub(r'(<span class="bcsep">[^<]*</span>\s*){2,}',
                  lambda m: m.group(1), html, flags=re.I)
    return html


# The home page carries the same note inline, written when its override was made
# by hand. Every other page gets it from here.
ACCOUNT_NOTE = """<!-- [ ] TODO: restore the account login here when multiplayer is implemented.
     Hidden for now because there is no server to authenticate against: the
     original form posted to this page with action=login, and Register and
     Forgot Password both pointed at pages that cannot work yet. -->"""

_ACCOUNT_RE = re.compile(r"<div\s+class=['\"]section account['\"]\s*>", re.I)
_DIV_TAG_RE = re.compile(r"<div\b[^>]*>|</div\s*>", re.I)


def remove_account_box(html):
    """Drop the Account panel from the right-hand column.

    The form posts a login to a static host, and Register and Forgot Password
    lead to archived pages that cannot do anything. Left in place it is the most
    inviting thing on the page, so a new arrival's first action fails.

    The panel wraps four nested divs, so the end is found by counting depth
    rather than matching to the first `</div>`, which would close the header and
    orphan the rest.
    """
    m = _ACCOUNT_RE.search(html)
    if not m:
        return html
    depth = 0
    for tag in _DIV_TAG_RE.finditer(html, m.start()):
        depth += -1 if tag.group(0).startswith("</") else 1
        if depth == 0:
            return html[:m.start()] + ACCOUNT_NOTE + html[tag.end():]
    # Unbalanced markup: leave the page alone rather than truncate it.
    return html


PATCHES = [
    ("remove the Live Chat tab", remove_live_chat),
    ("hide the dead account login", remove_account_box),
    ("remove the Firewall sub-tab", remove_firewall_subtab),
    ("hide the Tools sub-tab", remove_tools_subtab),
    ("remove the ToDo List sub-tab", remove_todo_subtab),
    ("remove links to retired ToDo pages", remove_retired_links),
    ("remove the Wallpapers sub-tab", remove_wallpapers_subtab),
    ("point contact links at GitHub", redirect_contact_links),
    ("localise absolute self-links", localise_self_links),
    ("strip DokuWiki indexer beacons", strip_indexer_beacons),
    ("modernise smileys", modernise_smileys),
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
