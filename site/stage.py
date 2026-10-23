#!/usr/bin/env python3
"""Stage part of the cosmicsupremacy.com mirror for static hosting.

The mirror stores pages under filenames that encode the original query string
(`game_news@sid=abc.php.html`), but the archived markup links to the original
URLs (`/game_news.php?sid=abc`). A static host strips the query and looks for a
file literally named `game_news.php`, which does not exist, so every link 404s.

This copies a chosen set of pages plus the assets they need, then rewrites the
links in the staged copies to point at the staged filenames. The mirror on disk
is never modified.

    python stage.py                  # home page only; everything else 404s
    python stage.py --set core       # + the session-free top-level pages
    python stage.py --set full       # + the wiki, including the manual
    python stage.py --list --set full    # show what a set contains, copy nothing

Re-running wipes and rebuilds public/, so it is always reproducible.
"""

import argparse
import os
import posixpath
import re
import shutil
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))          # <repo>/site
REPO = os.path.dirname(HERE)
# The 1 GB archive and its downloader stay out of git; only this directory is
# tracked, because public/ is entirely derived from the two of them.
WAYBACK = os.path.join(REPO, "tools", "wayback")
sys.path.insert(0, WAYBACK)
import wayback_grab as wg  # noqa: E402  (reuse the mirror's own path logic)
import patches            # noqa: E402  site-wide edits, see patches.py
import inventory          # noqa: E402  writes TODO-content.md

HOST = "cosmicsupremacy.com"
SRC = os.path.join(WAYBACK, "cosmicsupremacy_mirror", HOST)
# Captures of `cosmicsupremacy.com:80` sit in their own folder, and some pages
# exist only there.
SRC_80 = SRC + "_80"
DST = os.path.join(HERE, "public")
# Hand-written replacements for archived pages. Copied over the staged output,
# then rewritten and injected like everything else, so an edited page still
# gets working links and the attribution footer without remembering to add them.
OVERRIDES = os.path.join(HERE, "overrides")

# Attribute refs in the archived markup use single quotes about as often as double.
REF_RE = re.compile(
    r"""(?P<pre>(?:href|src)\s*=\s*)(?P<q>['"])(?P<url>[^'"]*)(?P=q)""", re.I)
CSS_URL_RE = re.compile(r"""url\(\s*['"]?([^'")]+)['"]?\s*\)""", re.I)

ASSET_EXT = {".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".ico",
             ".mp4", ".svg", ".ttf", ".woff", ".woff2", ".swf"}
PAGE_EXT = {".html", ".htm"}

# DokuWiki serves its CSS and JS through PHP endpoints (css.php, js.php) and its
# feeds through feed.php. They are assets in everything but file extension; the
# wiki renders completely unstyled without them.
ASSET_PHP_STEMS = {"css", "js", "feed", "rssfeed"}


def is_asset(rel):
    ext = os.path.splitext(rel)[1].lower()
    if ext in ASSET_EXT:
        return True
    if ext == ".php":
        stem = os.path.basename(rel).split("@")[0].split(".")[0].lower()
        return stem in ASSET_PHP_STEMS
    return False

# Held back deliberately: the forum carries real users' posts, names and
# avatars. Nothing under these prefixes is staged, so they always 404.
EXCLUDE_PREFIXES = ("forum/", "wiki/_export/")

# The crawler turned fragments of the wiki's JavaScript into URLs and then
# archived 404 pages under those names. They are not pages, they are parser
# debris, and publishing them puts nonsense in the site tree.
JUNK_PATH_RE = re.compile(
    r"[+'()\[\]]|;|this\.|\.get\(|indexOf|className|DOKU_BASE|XMLHTTP"
    r"|domLib|ajax_qsearch|=file", re.I)

# Whole pages that are deliberately not republished. Their nav entries are
# stripped by patches.py, and firebase.json must not rewrite anything to them.
EXCLUDE_PAGES = {
    "chat.php.html",             # live chat needs a server nobody is running
    "download_firewall.php.html",  # firewall rules for a server that is gone
    "wiki/tools.html",           # hidden until the tools are worth shipping
    "wiki/wallpapers.html",      # only thumbnails survived; nothing to show
    "wiki/dev.html",             # retired; captured to the CSV at the repo root
    "wiki/guides/todo-guide.html",  # ditto; it only described wiki/dev.html
}

# No robots meta is injected: the site is indexable. The archived pages carry
# DokuWiki's own `index,follow` tags, which are stripped anyway so that what a
# crawler sees comes from firebase.json and robots.txt alone.

# Injected into staged pages only; the mirror on disk is never modified. The
# archived markup is someone else's work, so every served page says so and says
# how to reach whoever is hosting it, and links the terms page's privacy section.
NOTICE = """
<div style="max-width:990px;margin:0 auto;padding:14px 18px;
     font:11px Verdana,Arial,sans-serif;color:#7a94ad;line-height:1.6;
     background:#001019;border-top:1px solid rgba(255,255,255,0.12);">
  Cosmic Supremacy and the original contents of this site are the work and
  property of their original creator. This is an unofficial preservation of a
  site that went offline, rebuilt from public web archives so the work is not
  lost. No ownership is claimed and nothing here is sold.
  If you are the original owner and would like this taken down or handed over,
  contact us in the <a href="https://www.facebook.com/groups/145339627564"
  style="color:#a9c6e0;text-decoration:underline;">Facebook group</a>.
  <br><a href="/support_terms.php.html#privacy"
  style="color:#a9c6e0;text-decoration:underline;">Privacy</a>
</div>
"""


# --------------------------------------------------------------------------
# Choosing what to publish
# --------------------------------------------------------------------------

def canonical_pages():
    """Top-level pages with no query string encoded in their filename.

    An `@` in the name means the capture carried a query string, which on this
    site is almost always a session id, making it a duplicate of the clean page.
    """
    return [fn for fn in sorted(os.listdir(SRC))
            if fn.endswith(".php.html") and "@" not in fn
            and fn not in EXCLUDE_PAGES]


def mirror_file(rel):
    """The mirror's copy of `rel`, or None."""
    for root in (SRC, SRC_80):
        path = os.path.join(root, rel.replace("/", os.sep))
        if os.path.isfile(path):
            return path
    return None


_MEDIA_INDEX = None


def media_variants(rel):
    """Every capture of a wiki media file, whatever size it was fetched at.

    DokuWiki resizes on a query (`?w=400&h=236`) and the archive often holds a
    file at one size only. The full-size capture comes first.
    """
    global _MEDIA_INDEX
    if _MEDIA_INDEX is None:
        _MEDIA_INDEX = {}
        for root in (SRC, SRC_80):
            for dirpath, _dirs, files in os.walk(os.path.join(root, "wiki", "_media")):
                folder = os.path.relpath(dirpath, root).replace(os.sep, "/")
                for fn in files:
                    # `pirate@w=&h=&cache=cache.svg.png` is `pirate.svg.png`
                    name, _at, query = fn.partition("@")
                    key = folder + "/" + name + (query[query.find("."):] if "." in query else "")
                    variant = folder + "/" + fn
                    if variant not in _MEDIA_INDEX.setdefault(key, []):
                        _MEDIA_INDEX[key].append(variant)
        for found in _MEDIA_INDEX.values():
            found.sort(key=lambda f: (0 if "@" not in f else 1 if "@w=&h=" in f else 2, f))
    return _MEDIA_INDEX.get(rel, [])


def wiki_pages():
    out = set()
    for root in (SRC, SRC_80):
        wroot = os.path.join(root, "wiki")
        if not os.path.isdir(wroot):
            continue
        for dirpath, _dirs, files in os.walk(wroot):
            for fn in files:
                if not fn.endswith(".html") or "@" in fn:
                    continue
                rel = os.path.relpath(os.path.join(dirpath, fn), root)
                out.add(rel.replace(os.sep, "/"))
    return sorted(out)


def build_set(name):
    pages = ["index.html"]
    if name in ("core", "full"):
        pages += canonical_pages()
    if name == "full":
        pages += wiki_pages()
    seen, uniq = set(), []
    for p in pages:
        if (p not in seen and not p.startswith(EXCLUDE_PREFIXES)
                and p not in EXCLUDE_PAGES
                and not JUNK_PATH_RE.search(p)):
            seen.add(p)
            uniq.append(p)
    return uniq


# --------------------------------------------------------------------------
# Resolving a markup reference to a staged file
# --------------------------------------------------------------------------

def local_for(url_path, query, as_html):
    """Ask the mirror's own naming function what it called this URL.

    Reversing the mangled filenames is ambiguous because the query string can
    contain dots and long ones were hashed, so map forward instead.
    """
    url = urllib.parse.urlunsplit(("http", HOST, url_path, query, ""))
    rel = wg.local_path_for(url, "text/html" if as_html else "", True)
    prefix = HOST + "/"
    return rel[len(prefix):] if rel.startswith(prefix) else rel


def candidates(ref, base_dir):
    """Mirror-relative paths this reference could mean, best guess first.

    Session ids make one page look like hundreds of URLs, so a link carrying a
    sid that was never captured should still find the clean copy of the page.
    """
    if re.match(r"^(mailto:|javascript:|data:|#)", ref, re.I):
        return []
    ref = ref.replace("&amp;", "&")  # the mirror's rewriter never decoded these
    if ref.startswith("//"):
        ref = "http:" + ref
    split = urllib.parse.urlsplit(ref)
    if split.scheme or split.netloc:
        # An absolute link back to the site's own host is still a local page.
        # The archive is full of these and left alone they point at the dead
        # original domain, which looks like a working link and is not.
        host = split.netloc.lower().split("@")[-1].split(":")[0]
        if host not in (HOST, "www." + HOST):
            return []
    path, query = split.path, split.query
    if not path and not query:
        return []

    if path.startswith("/"):
        rel = path.lstrip("/")
    else:
        rel = posixpath.normpath(posixpath.join(base_dir, path)) if base_dir else path
        if rel.startswith(".."):
            return []
    rel = rel.lstrip("/")

    if rel.endswith("lib/exe/fetch.php") and query:
        media = urllib.parse.parse_qs(query).get("media", [""])[0]
        if media.startswith(("http://", "https://")):
            mp = urllib.parse.urlsplit(media)
            mhost = mp.netloc.lower().split(":")[0]
            if mhost in (HOST, "www." + HOST):
                return candidates(mp.path, "")
        return []

    queries = [query]
    if query:
        kept = [kv for kv in urllib.parse.parse_qsl(query, keep_blank_values=True)
                if kv[0].lower() != "sid"]
        without_sid = urllib.parse.urlencode(kept)
        if without_sid != query:
            queries.append(without_sid)
        if "" not in queries:
            queries.append("")

    out = []
    for q in queries:
        for as_html in (True, False):
            cand = local_for("/" + rel, q, as_html)
            if cand not in out:
                out.append(cand)

    if rel.startswith("wiki/_media/"):
        for cand in media_variants(rel):
            if cand not in out:
                out.append(cand)

    # A DokuWiki image viewer only frames the image, so the image stands in for
    # a viewer that was never captured.
    if rel.startswith("wiki/_detail/"):
        for cand in candidates("/wiki/_media/" + rel[len("wiki/_detail/"):], ""):
            if cand not in out:
                out.append(cand)
    return out


NEVER_ARCHIVED = inventory.never_archived()

_IMG_RE = re.compile(
    '<img[^>]*?src\\s*=\\s*(?:"(?P<dq>[^"]*)"|\'(?P<sq>[^\']*)\')[^>]*>',
    re.I)


def mark_missing_images(html, page_rel, staged):
    """Replace images the archive never captured with a visible TODO marker.

    A broken-image icon says nothing about what is missing. This names the file
    so a replacement can be found, and makes the gaps countable while browsing.
    """
    base = posixpath.dirname(page_rel)
    found = []

    def sub(m):
        src = m.group("dq")
        if src is None:
            src = m.group("sq")
        for cand in candidates(src, base):
            if cand in staged:
                return m.group(0)
        if not candidates(src, base):      # external image, not ours to judge
            return m.group(0)
        found.append(src)
        if inventory.image_key(src) in NEVER_ARCHIVED:
            return ('<span style="display:inline-block;padding:4px 8px;'
                    'border:1px dashed #888;color:#666;font:11px Verdana,sans-serif;'
                    'background:#f4f4f4;">image not archived</span>')
        # The comment opener is split from the "[ ]" so a TODO scanner finds the
        # marker in the staged pages, where it is a task, and not here.
        return (
            '<!--' ' [ ] TODO(image): not in the web archive, needs a replacement: '
            + src.replace("--", "- -") + ' -->'
            '<span style="display:inline-block;padding:4px 8px;border:1px dashed #b00;'
            'color:#b00;font:11px Verdana,sans-serif;background:#fff5f5;">'
            '[ ] missing image</span>'
        )

    return _IMG_RE.sub(sub, html), found


def rewrite_links(text, page_rel, staged):
    """Point every reference that has a staged target at that target."""
    base_dir = posixpath.dirname(page_rel)
    count = 0

    def sub(match):
        nonlocal count
        for cand in candidates(match.group("url"), base_dir):
            if cand in staged:
                count += 1
                q = match.group("q")
                return match.group("pre") + q + "/" + cand + q
        return match.group(0)

    return REF_RE.sub(sub, text), count


# DokuWiki marks a link to a page nobody wrote with class wikilink2 and shows
# it in red. The page never existed, so the link loses its href and keeps the
# red text, which the wiki's own CSS still styles.
_RED_LINK_RE = re.compile(r"<a\b[^>]*\bwikilink2\b[^>]*>", re.I)
_HREF_ATTR_RE = re.compile(r"""\s+href\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I)
RED_LINK_ATTR = ' data-red-link="never written on the original wiki"'


def unlink_red_links(html, page_rel, staged):
    base = posixpath.dirname(page_rel)
    count = 0

    def sub(m):
        nonlocal count
        tag = m.group(0)
        href = _HREF_ATTR_RE.search(tag)
        if not href:
            return tag
        cands = candidates(href.group(1) or href.group(2) or "", base)
        if not cands or any(c in staged for c in cands):
            return tag
        count += 1
        return _HREF_ATTR_RE.sub("", tag, count=1)[:-1] + RED_LINK_ATTR + ">"

    return _RED_LINK_RE.sub(sub, html), count


NOTICE_MARK = "property of their original creator"
_ANY_ROBOTS_RE = re.compile(r"\s*<meta[^>]+name=[\"']robots[\"'][^>]*>", re.I)

# An archived form (registration, password reset) posts to a server that no
# longer exists, and some ask for a password. Every field is disabled through a
# disabled fieldset, so nothing can be typed or sent, and a line says why.
FORM_MARK = "data-archived-form"
FORM_OPEN_RE = re.compile(r"(<form\b[^>]*>)", re.I)
FORM_CLOSE_RE = re.compile(r"(</form\s*>)", re.I)
FORM_NOTE = ("<p class='archived-form-note'><b>This form is from the original "
             "site and no longer works.</b> Its fields are disabled and nothing "
             "is sent anywhere.</p>")


def disable_forms(html):
    """Make every form inert. Idempotent through FORM_MARK."""
    if FORM_MARK in html or not FORM_OPEN_RE.search(html):
        return html
    html = FORM_OPEN_RE.sub(
        lambda m: (FORM_NOTE + m.group(1)[:-1] + " " + FORM_MARK
                   + " onsubmit='return false'><fieldset disabled "
                   "style='border:0;margin:0;padding:0'>"), html)
    return FORM_CLOSE_RE.sub(lambda m: "</fieldset>" + m.group(1), html)


def inject(html):
    """Strip stale robots tags, make archived forms inert, and add the attribution
    before </body>.

    Must be idempotent. Overrides are made by copying a file out of public/,
    which already carries the notice, and re-running the build would otherwise
    stack a second copy onto every page each time.

    Every robots tag is dropped, both the archived DokuWiki `index,follow` and
    the `noindex` this site used to inject. Nothing replaces them, so indexing
    is governed by firebase.json and robots.txt in one place.
    """
    html = _ANY_ROBOTS_RE.sub("", html)
    html = disable_forms(html)

    if NOTICE_MARK not in html:
        m = re.search(r"</body>", html, re.I)
        html = html[:m.start()] + NOTICE + html[m.start():] if m else html + NOTICE
    return html


# --------------------------------------------------------------------------
# Copying
# --------------------------------------------------------------------------

def copy(rel, copied, missing):
    if rel.startswith(EXCLUDE_PREFIXES) or JUNK_PATH_RE.search(rel):
        return False
    src = mirror_file(rel)
    if src is None:
        missing.add(rel)
        return False
    if rel in copied:
        return True
    dst = os.path.join(DST, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(dst) or DST, exist_ok=True)
    shutil.copy2(src, dst)
    copied[rel] = os.path.getsize(src)
    return True


def is_page(rel):
    return os.path.splitext(rel)[1].lower() in PAGE_EXT


def follow_links(rel, copied, missing):
    """Stage the archived pages a staged page links to that its set left out.

    These are captures named after their query string, mostly DokuWiki image
    viewers and backlink lists. A session-id capture is a duplicate of the clean
    page, so a link is only followed to a capture without one. Only wiki pages
    are followed; the game's per-galaxy and score pages are not published.
    """
    with open(os.path.join(DST, rel.replace("/", os.sep)),
              "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    found = []
    for match in REF_RE.finditer(text):
        if not match.group("pre").lower().startswith("href"):
            continue
        for cand in candidates(match.group("url"), posixpath.dirname(rel)):
            if cand in copied:
                break
            if (is_page(cand) and cand.startswith("wiki/") and "sid=" not in cand
                    and not cand.startswith(inventory.INTENTIONAL)
                    and cand not in EXCLUDE_PAGES and mirror_file(cand)
                    and copy(cand, copied, missing)):
                found.append(cand)
                break
    return found


def collect_assets(text, base_dir, copied, missing, css_queue):
    for match in REF_RE.finditer(text):
        for cand in candidates(match.group("url"), base_dir):
            if cand in copied:
                break
            if is_asset(cand):
                if copy(cand, copied, missing) and cand.endswith(".css"):
                    css_queue.append(cand)
                break


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", dest="setname", default="home",
                    choices=["home", "core", "full"])
    ap.add_argument("--seed", default="game_news.php.html",
                    help="page served at / (default: the archived home page)")
    ap.add_argument("--list", action="store_true", help="show the set and exit")
    args = ap.parse_args()

    if not os.path.isdir(SRC):
        sys.exit("mirror not found at " + SRC)

    pages = build_set(args.setname)
    if args.seed not in pages:
        pages.insert(0, args.seed)

    if args.list:
        print("set '%s' -> %d pages" % (args.setname, len(pages)))
        for p in pages:
            print("   ", p)
        return

    seed_src = os.path.join(SRC, args.seed.replace("/", os.sep))
    if not os.path.isfile(seed_src):
        sys.exit("seed page not found: " + seed_src)

    # Empty public/ rather than deleting it. On Windows an editor, indexer or
    # dev server frequently holds a handle on the directory itself, which makes
    # rmtree fail even though every file inside is removable.
    if os.path.isdir(DST):
        for name in os.listdir(DST):
            victim = os.path.join(DST, name)
            if os.path.isdir(victim) and not os.path.islink(victim):
                shutil.rmtree(victim, ignore_errors=True)
            else:
                try:
                    os.remove(victim)
                except OSError:
                    pass
        leftover = sum(len(f) for _r, _d, f in os.walk(DST))
        if leftover:
            print("  warning: %d file(s) in public/ could not be removed" % leftover)
    else:
        os.makedirs(DST)

    copied, missing, css_queue = {}, set(), []

    for rel in pages:
        copy(rel, copied, missing)

    followed = 0
    queue = [rel for rel in copied if is_page(rel)]
    while queue:
        found = follow_links(queue.pop(), copied, missing)
        followed += len(found)
        queue += found

    # Assets referenced by any staged page.
    for rel in list(copied):
        if os.path.splitext(rel)[1].lower() not in PAGE_EXT:
            continue
        with open(os.path.join(DST, rel.replace("/", os.sep)),
                  "r", encoding="utf-8", errors="replace") as fh:
            collect_assets(fh.read(), posixpath.dirname(rel),
                           copied, missing, css_queue)

    # CSS pulls in its own backgrounds; without these the pages render bare.
    seen_css = set()
    while css_queue:
        css_rel = css_queue.pop()
        if css_rel in seen_css:
            continue
        seen_css.add(css_rel)
        with open(mirror_file(css_rel), "r", encoding="utf-8", errors="replace") as fh:
            css = fh.read()
        for ref in CSS_URL_RE.findall(css):
            for cand in candidates(ref, posixpath.dirname(css_rel)):
                if is_asset(cand):
                    if copy(cand, copied, missing) and cand.endswith(".css"):
                        css_queue.append(cand)
                    break

    # Hand-written pages win over whatever came out of the archive.
    override_count = 0
    if os.path.isdir(OVERRIDES):
        for dirpath, _dirs, files in os.walk(OVERRIDES):
            for fn in files:
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, OVERRIDES).replace(os.sep, "/")
                if rel == "README.md":      # documents the directory, not content
                    continue
                dst = os.path.join(DST, rel.replace("/", os.sep))
                os.makedirs(os.path.dirname(dst) or DST, exist_ok=True)
                shutil.copy2(full, dst)
                copied[rel] = os.path.getsize(full)
                override_count += 1

    # The seed is also served at /. Copied after overrides so that editing the
    # seed page updates the front page too, rather than only its own URL.
    seed_staged = os.path.join(DST, args.seed.replace("/", os.sep))
    if os.path.isfile(seed_staged):
        shutil.copy2(seed_staged, os.path.join(DST, "index.html"))
        copied["index.html"] = os.path.getsize(seed_staged)

    # Only now is the staged set final, so links can be pointed at it.
    staged = set(copied)
    rewritten = 0
    red_links = 0
    patch_hits = {}
    image_todos = {}
    for rel in sorted(copied):
        if os.path.splitext(rel)[1].lower() not in PAGE_EXT:
            continue
        full = os.path.join(DST, rel.replace("/", os.sep))
        with open(full, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        text, n = rewrite_links(text, rel, staged)
        rewritten += n
        text, n = unlink_red_links(text, rel, staged)
        red_links += n
        # Patches run first: they strip the indexer beacon, which would
        # otherwise be counted and marked as a missing image on every page.
        text, hit = patches.apply_all(text)
        text, missing_imgs = mark_missing_images(text, rel, staged)
        for src in missing_imgs:
            image_todos.setdefault(src, []).append(rel)
        for name in hit:
            patch_hits[name] = patch_hits.get(name, 0) + 1
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(inject(text))

    # Release binaries are not staged here. The client zip is attached to the
    # GitHub release for its tag, which serves the download and counts it, and
    # download_client.php.html links there. Keeping the 30 MB build out of
    # public/ also keeps it off this host's bandwidth and out of every re-stage.

    # Crawling and indexing are both allowed. The forum and everything else
    # withheld is absent from public/ entirely, so there is nothing here that a
    # Disallow would need to protect.
    with open(os.path.join(DST, "robots.txt"), "w", encoding="utf-8") as fh:
        fh.write("User-agent: *\nAllow: /\n")
    shutil.copy2(os.path.join(HERE, "404.html"), os.path.join(DST, "404.html"))

    page_count = sum(1 for r in copied
                     if os.path.splitext(r)[1].lower() in PAGE_EXT)
    total = sum(os.path.getsize(os.path.join(DST, r.replace("/", os.sep)))
                for r in copied
                if os.path.isfile(os.path.join(DST, r.replace("/", os.sep))))
    print("set '%s': %d files (%d pages), %.1f MiB"
          % (args.setname, len(copied), page_count, total / 1048576))
    print("  links repointed at staged pages: %d" % rewritten)
    print("  linked pages staged from outside the set: %d" % followed)
    print("  red links to unwritten wiki pages unlinked: %d" % red_links)
    if override_count:
        print("  hand-written overrides applied:  %d" % override_count)
    for name, n in sorted(patch_hits.items()):
        print("  site-wide patch: %-32s %d pages" % (name, n))
    n_img, n_tgt, n_ref = inventory.write(
        os.path.join(HERE, "TODO-content.md"), image_todos, DST, staged, candidates)
    print("  TODO-content.md: %d missing images, %d broken link targets (%d refs)"
          % (n_img, n_tgt, n_ref))
    if image_todos:
        print("  images not in the mirror: %d distinct (%d places), %d marked TODO"
              % (len(image_todos), sum(len(v) for v in image_todos.values()), n_img))
    for name, _fn in patches.PATCHES:
        if name not in patch_hits:
            print("  site-wide patch MATCHED NOTHING: %s" % name)
    if missing:
        print("  referenced but absent from mirror: %d" % len(missing))


if __name__ == "__main__":
    main()
