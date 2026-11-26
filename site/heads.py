"""
heads.py: a title and a description of its own for every staged page
======================================================================
Most archive pages share the title "Cosmic Supremacy" and carry no meta
description, or one shared by every page that has one. Search engines need
each page's own (Website Checklist R7; Opquast 3 and 103).

A page's name is its active tab, else its first `content_heading` cell (a
galaxy page names its galaxy there), else its active menu entry, else NAMES,
else its file name; a wiki page's is its first heading. A list page adds its
page number, and a wiki view other than the page itself (`@do=backlink` and
the like) adds the view.

Its description is the opening of its own text: the main column of an
archive page, or the wiki page's body after its heading and table of
contents, cut at a word boundary near DESCRIPTION_LENGTH. A page with no text
of its own describes itself by its name.

A title is replaced only when it is one the archive shares; a description
only when it is missing or the shared one.
"""
import html as htmlmod
import os
import re

SITE_NAME = 'Cosmic Supremacy'
# The titles and description the archive gave many pages at once.
SHARED_TITLES = {'', 'cosmic supremacy',
                 'cosmic supremacy - a free turn based massively multiplayer '
                 'online space strategy',
                 'cosmic supremacy - a free turn based massively multiplayer '
                 'online space strategy game'}
SHARED_DESCRIPTION_START = 'Challenge your skills in planet management'
DESCRIPTION_LENGTH = 155        # what search results show, about
# Pages with no heading, tab or menu entry of their own: archived error pages.
NAMES = {
    'galaxy.php.html': 'Galaxy',
    'voting.php.html': 'Vote for the game',
}
# What a DokuWiki view other than the page itself shows.
WIKI_VIEWS = {'backlink': 'backlinks', 'index': 'index', 'recent': 'recent changes',
              'revisions': 'old revisions', 'media': 'media'}

_TITLE_RE = re.compile(r'<title[^>]*>(.*?)</title>', re.I | re.S)
_DESC_RE = re.compile(
    r'<meta\b[^>]*\bname=["\']description["\'][^>]*>\s*', re.I)
_DESC_CONTENT_RE = re.compile(r'\bcontent=["\']([^"\']*)["\']', re.I)
_HEADING_RE = re.compile(r"class=['\"]content_heading['\"][^>]*>(.*?)</td>", re.I | re.S)
_TAB_RE = re.compile(r"<ul id=['\"]tab['\"]>.*?id=['\"]active['\"][^>]*>\s*<a[^>]*>(.*?)</a>",
                     re.I | re.S)
_MENU_RE = re.compile(r"<ul id=['\"]menu['\"]>.*?id=['\"]active['\"][^>]*>\s*<a[^>]*>(.*?)</a>",
                      re.I | re.S)
_MAIN_RE = re.compile(r"id=['\"]left_content['\"](.*?)<[^<>]*id=['\"]right_content['\"]",
                      re.I | re.S)
_WIKI_RE = re.compile(r'<div class="page[^"]*"[^>]*>(.*?)(?:<div class="docInfo"|<div id="dokuwiki__footer"|$)',
                      re.I | re.S)
_NOISE_RE = re.compile(r'<(script|noscript|style)\b.*?</\1\s*>|<!--.*?-->', re.I | re.S)
_WIKI_MARK = '<div class="dokuwiki'
_H1_RE = re.compile(r'<h1[^>]*>(.*?)</h1>', re.I | re.S)
_TOC_RE = re.compile(r'<div (?:id="dw__toc"|class="toc")[^>]*>.*?</ul>\s*</div>\s*</div>',
                     re.I | re.S)
# The archive's own boilerplate, at the head of many pages.
_BOILERPLATE = ('Java-Script is disabled on this browser! In order for this page '
                'to display and operate correctly, you need to enable Java-Script '
                'in your browser settings.')


def _text(fragment):
    fragment = _NOISE_RE.sub(' ', fragment)
    fragment = re.sub(r'<[^>]+>', ' ', fragment)
    return ' '.join(htmlmod.unescape(fragment).split())


LINK_SHARE = 0.6     # a heading's section this much link text is a navigation box
_HEAD_RE = re.compile(r'<h[12][^>]*>(.*?)</h[12]>', re.I | re.S)


def _wiki_heading(fragment):
    """The first heading that is the page's own, not a box of links (the
    manual's "Main Window Links", a guide's "Volume I: The Basics"), whose
    page names itself in the next heading down."""
    heads = list(_HEAD_RE.finditer(fragment))
    for i, m in enumerate(heads):
        text = _text(m.group(1))
        if not text or text.endswith(' Links'):
            continue
        end = heads[i + 1].start() if i + 1 < len(heads) else len(fragment)
        section = fragment[m.end():end]
        words = _text(section)
        links = ' '.join(_text(a) for a in re.findall(r'<a\b[^>]*>(.*?)</a>', section,
                                                       re.I | re.S))
        if words and len(links) >= LINK_SHARE * len(words):
            continue
        return m
    return None


def page_name(html, rel):
    if _WIKI_MARK in html:
        m = _wiki_heading(html)
        if m:
            return _text(m.group(1))
    for rx in (_TAB_RE, _HEADING_RE):
        m = rx.search(html)
        if m and _text(m.group(1)):
            return _text(m.group(1))
    m = _MENU_RE.search(html)
    if m and _text(m.group(1)):
        return _text(m.group(1)).title()
    base = os.path.basename(rel)
    if base in NAMES:
        return NAMES[base]
    stem = re.split(r'[@.]', base)[0]
    return stem.replace('_', ' ').capitalize()


def _suffix(rel):
    m = re.search(r'[@&]page=(\d+)', rel)
    if m:
        return f' (page {m.group(1)})'
    m = re.search(r'@do=([a-z]+)', rel)
    if m:
        return f' ({WIKI_VIEWS.get(m.group(1), m.group(1))})'
    return ''


def page_text(html):
    """The page's own text: the archive's main column, or the wiki body."""
    m = None if _WIKI_MARK in html else _MAIN_RE.search(html)
    if m:
        body = m.group(1)
        # The page's tab bar names its siblings, not the page.
        body = re.sub(r"<ul id=['\"]tab['\"]>.*?</ul>", ' ', body, flags=re.I | re.S)
    else:
        m = _WIKI_RE.search(html)
        body = _TOC_RE.sub(' ', m.group(1)) if m else ''
        h1 = _wiki_heading(body)
        body = body[h1.end():] if h1 else body
    text = _text(body).replace(_BOILERPLATE, ' ')
    return ' '.join(text.lstrip('> ').split())


def describe(html, rel, name):
    text = page_text(html)
    if text.startswith(name):
        text = text[len(name):].lstrip(' -:>')
    if len(text) < 20:
        return f'{name}, from the {SITE_NAME} archive.'
    if len(text) > DESCRIPTION_LENGTH:
        text = text[:DESCRIPTION_LENGTH].rsplit(' ', 1)[0].rstrip(',;:-') + '...'
    return f'{name}: {text}' if not text.startswith(name) else text


def _attr(value):
    return htmlmod.escape(value, quote=True)


def retitle(html, rel):
    """`html` with its own title and description where the archive gave it a
    shared one or none. Idempotent."""
    m = _TITLE_RE.search(html)
    old = ' '.join(htmlmod.unescape(m.group(1)).split()) if m else ''
    name = page_name(html, rel)
    if old.lower() in SHARED_TITLES:
        title = f'{name}{_suffix(rel)} - {SITE_NAME}'
    elif '@do=' in rel and _suffix(rel) not in old:
        title = old + _suffix(rel)
    else:
        title = None
    if title:
        tag = f'<title>{htmlmod.escape(title, quote=False)}</title>'
        html = _TITLE_RE.sub(lambda _m: tag, html, count=1) if m else \
            re.sub(r'(<head[^>]*>)', lambda h: h.group(1) + tag, html, count=1, flags=re.I)
    d = _DESC_RE.search(html)
    current = _DESC_CONTENT_RE.search(d.group(0)).group(1) if d and \
        _DESC_CONTENT_RE.search(d.group(0)) else ''
    if not d or not current.strip() or current.startswith(SHARED_DESCRIPTION_START):
        text = describe(html, rel, name + _suffix(rel))
        meta = f'<meta name="description" content="{_attr(text)}" />\n'
        if d:
            html = html[:d.start()] + meta + html[d.end():]
        else:
            close = re.search(r'</head>', html, re.I)
            if close:
                html = html[:close.start()] + meta + html[close.start():]
    return html
