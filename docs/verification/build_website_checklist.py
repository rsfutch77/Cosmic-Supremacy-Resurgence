"""
build_website_checklist.py: write Website_Checklist.md from the standards
    python docs/verification/build_website_checklist.py

Every WCAG 2.2 success criterion and every Opquast v5 rule, each with one
disposition:

    apply      a checkbox, with sub-items for cosmicresurgence.com
    done       a ticked checkbox, with what verified it
    na         not applicable, with the reason
    deviation  the site contradicts it on purpose, with the rationale
    out        WCAG Level AAA, beyond the Level AA target

A rule with no decision is written as TO TAILOR. A sub-item beginning "[x] " is
ticked. Decisions are looked up by exact id, then by WCAG guideline or Opquast
theme.

R items come from guidance without numbered rules (Core Web Vitals,
Lighthouse, MDN HTTP Observatory, Google Search Central, Apache release
download pages, Microsoft SmartScreen guidance), paraphrased with a citation.

Sources are read from tools/checklists/website/ (gitignored):
  wcag22.json               W3C, Web Content Accessibility Guidelines 2.2,
                            W3C Recommendation 12 December 2024, W3C Document
                            License; criterion text reproduced unchanged.
  opquast_v5_rules.csv      Opquast Digital Quality Framework, Rules 2025,
                            version 5.0, CC BY-SA 4.0; rule text reproduced.
                            Parsed from the published PDF by parse_opquast.py.
"""
import csv
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(REPO, 'tools', 'checklists', 'website')
OUT = os.path.join(HERE, '3 - Website_Checklist.md')

OUT_OF_TARGET = ('out', 'Level AAA, beyond the Level AA target.', [])
ARCHIVE = ('The archive is 2000s HTML. Fix it in `site/stage.py` as a '
           'transform over every page where the fix is mechanical; otherwise '
           'judge it on the WCAG-EM sample (R6).')

# WCAG 2.2
W = {
    '1.1.1': ('apply', None, [
        '198 of 229 pages carry `alt` on images. Find the images with no `alt`, and give decorative ones `alt=""` in a `stage.py` transform.',
        'Overrides and new pages: every image has a text alternative.']),
    '1.2.1': ('apply', None, ['17 pages use audio or video tags: give each prerecorded audio-only or video-only item a text alternative.']),
    '1.2.2': ('apply', None, ['Captions for any prerecorded video with sound (same 17 pages).']),
    '1.2.3': ('apply', None, ['Audio description or a text alternative for prerecorded video.']),
    '1.2.4': ('na', 'No live audio or video.', []),
    '1.2.5': ('apply', 'As 1.2.3.', []),
    '1.3.1': ('apply', None, ['Headings, lists and data tables are marked up as such on override pages.', 'Archive pages use layout tables: they must linearise in reading order (Opquast 244).', ARCHIVE]),
    '1.3.2': ('apply', 'As 1.3.1, layout-table reading order.', []),
    '1.3.3': ('apply', None, ['No instruction relies on shape, position or colour alone ("click the green button").']),
    '1.3.4': ('apply', None, ['No page locks orientation.']),
    '1.3.5': ('na', 'No live form collects personal data (the archived forms are made inert, R4).', []),
    '1.4.1': ('apply', 'As Opquast 181.', []),
    '1.4.2': ('apply', None, ['No audio plays automatically; check the 17 media pages and the 4 Flash embeds.']),
    '1.4.3': ('apply', None, ['Text contrast at least 4.5:1 (3:1 for large text). The original site is light text on dark blue; check the body, link and menu colours once in `site/public/css` and fix them there.']),
    '1.4.4': ('apply', None, ['Text resizes to 200% without loss; fixed pixel widths in the archive layout are the likely failure.']),
    '1.4.5': ('apply', None, ['Menus and headings drawn as images (`THE GAME`, `DOWNLOAD`, and the rest) carry the same text in `alt`. Replacing them with styled text is the full fix.']),
    '1.4.10': ('apply', None, ['Content reflows at 320 CSS pixels wide. Only one page has a viewport tag; add one site-wide in `stage.py`, then check the layout table.']),
    '1.4.11': ('apply', None, ['Focus indicators and control borders reach 3:1 against the background.']),
    '1.4.12': ('apply', None, ['Text spacing can be increased without clipping (fixed-height boxes in the archive layout).']),
    '1.4.13': ('apply', None, ['Menus or tooltips shown on hover can be dismissed and hovered.']),
    '2.1.1': ('apply', None, ['Every link and control works from the keyboard, including the menu.']),
    '2.1.2': ('apply', None, ['No keyboard trap, including inside the four Flash embeds (or remove them, R5).']),
    '2.1.4': ('na', 'No single-key shortcuts.', []),
    '2.2.1': ('na', 'No time limits.', []),
    '2.2.2': ('apply', None, ['Anything that moves, blinks or scrolls for more than five seconds can be paused (animated GIFs in the archive).']),
    '2.3.1': ('apply', None, ['No content flashes more than three times a second (animated GIFs, concept-art and video pages).']),
    '2.4.1': ('apply', None, ['A skip link to the main content on every page, added in `stage.py` (Opquast 164).']),
    '2.4.2': ('apply', None, ['Every page title names the page and the site (Opquast 102, 103). Check archive titles, which may all read "Cosmic Supremacy".']),
    '2.4.3': ('apply', 'As 1.3.2.', []),
    '2.4.4': ('apply', None, ['Link text says where it goes; "click here" and image links without `alt` fail (Opquast 136, 137).']),
    '2.4.5': ('apply', None, ['Two ways to reach a page: the menu plus a site map (Opquast 171) or a search.']),
    '2.4.6': ('apply', 'As 1.3.1, headings.', []),
    '2.4.7': ('apply', None, ['Focus is visible: no `outline: none` without a replacement (Opquast 165).']),
    '2.4.11': ('apply', None, ['A focused item is not hidden behind a fixed header.']),
    '2.5.1': ('na', 'No path-based or multipoint gestures.', []),
    '2.5.2': ('apply', 'Standard links and buttons only.', []),
    '2.5.3': ('apply', None, ['Image-text menu items: the accessible name contains the visible text.']),
    '2.5.4': ('na', 'No motion-actuated functions.', []),
    '2.5.7': ('na', 'No dragging.', []),
    '2.5.8': ('apply', None, ['Targets at least 24 by 24 CSS pixels, or spaced: the small text links in the archive footer and the wiki.']),
    '3.1.1': ('apply', None, ['21 of 229 pages have no `lang` on `<html>`: add `lang="en"` in `stage.py` (Opquast 130).']),
    '3.1.2': ('apply', None, ['Mark passages in another language, if any (the German original pages, if mirrored).']),
    '3.2.1': ('apply', 'No change of context on focus.', []),
    '3.2.2': ('apply', 'No change of context on input.', []),
    '3.2.3': ('apply', None, ['The menu is in the same place, in the same order, on every page, overrides included (Opquast 158).']),
    '3.2.4': ('apply', 'As 3.2.3.', []),
    '3.2.6': ('apply', None, ['The way to get help (contact, support page) is in the same place on every page.']),
    '3.3.1': ('na', 'No live forms (R4).', []),
    '3.3.2': ('na', 'No live forms (R4).', []),
    '3.3.3': ('na', 'No live forms (R4).', []),
    '3.3.4': ('na', 'No live forms (R4).', []),
    '3.3.7': ('na', 'No multi-step forms.', []),
    '3.3.8': ('na', 'No sign-in on the site.', []),
    '4.1.1': ('na', 'Obsolete and removed from WCAG 2.2.', []),
    '4.1.2': ('apply', None, ['Custom controls (scripted menus) expose a name and role.']),
    '4.1.3': ('na', 'No status messages: the site is static.', []),
}

# Opquast v5
NO_SALES = ('na', 'Nothing is sold. The archived premium pages are history; their purchase forms are inert (R4).', [])
NO_ACCOUNTS = ('na', 'The site has no accounts. The archived register and password forms are inert (R4).', [])
NO_FORMS = ('na', 'No live forms (R4).', [])
NO_PUBLIC_SPACE = ('na', 'The site has no public spaces; the forum is withheld.', [])

O = {
    'Contents': ('apply', None, []),
    'E-Commerce': NO_SALES,
    'Personal data': NO_ACCOUNTS,
    'Forms': NO_FORMS,
    'Newsletter': ('na', 'No newsletter. The news page and its RSS feed are covered by rule 1.', []),
    1: ('apply', None, ['The news page (override) and `rssfeed.php` carry revival news, newest first, dated.']),
    2: ('apply', None, ['Every page links to copyright and reuse information: the archive\'s text and art belong to the original authors, and the page says so and says what the revival claims (nothing).']),
    3: ('apply', None, ['Each page has a `<meta name="description">`, written for the overrides and generated for the wiki.']),
    4: ('apply', None, ['[x] The download page date reads `18 September 2026`.', 'Other override pages and the news use the same unambiguous form.']),
    5: ('apply', None, ['Game abbreviations (PP, BC, the ship class codes) are expanded on first use on override pages; the manual\'s glossary covers the wiki.']),
    6: ('apply', None, ['Override pages and the news say when they were written; archive pages say they are archived and from when.']),
    7: ('apply', None, ['The manual\'s glossary is linked from the wiki index.']),
    8: ('apply', None, ['Any archived advert or banner is marked as historical or removed.']),
    9: NO_PUBLIC_SPACE, 10: NO_PUBLIC_SPACE, 11: NO_PUBLIC_SPACE,
    12: ('na', 'No graphs.', []),
    13: ('na', 'No search (rule 168).', []),
    14: ('apply', None, ['No ASCII art or spaced-out letters standing in for formatting.']),
    15: ('apply', None, ['A privacy page, linked from every page. It covers what the launcher sends (uid, player name, logs on request) as well as the site, which sets no cookies. Shared with the privacy checklist.']),
    16: ('apply', None, ['The privacy page says how a player asks for their data to be shown or deleted.']),
    25: ('done', '`Referrer-Policy: strict-origin-when-cross-origin`, live on 8 October 2026.', []),
    27: ('apply', 'As R4: the inert forms must not collect passwords.', []),
    28: ('apply', 'As R4.', []),
    29: ('apply', None, ['Confirm the site sets no cookies (Firebase Hosting sets none by default), and say so on the privacy page.']),
    99: ('apply', None, ['The home page says, near the top, that this is an archive of the original Cosmic Supremacy site plus a fan revival, and links to the download and the beta.']),
    100: ('apply', None, ['Say who the beta is for (Windows 10 and 11, adults or an age floor if the privacy work sets one).']),
    101: ('apply', None, ['Name who runs the revival.']),
    102: ('apply', 'As WCAG 2.4.2.', []),
    103: ('apply', 'As WCAG 2.4.2.', []),
    104: ('apply', None, ['Every page links a favicon.']),
    105: ('na', 'A non-commercial fan project with no office. Contact is by the means in rule 107.', []),
    106: ('na', 'No registered organisation runs the site.', []),
    107: ('apply', None, ['Two ways to make contact (for example an address on the support page and the GitHub issues page).']),
    108: ('apply', None, ['The support page gives an honest response time ("usually within a week").']),
    109: ('na', 'No paid service.', []),
    110: ('na', 'No form submits requests; contact is by email or GitHub.', []),
    111: ('na', 'The site sends no email.', []),
    112: ('apply', 'As 107.', []),
    113: NO_PUBLIC_SPACE,
    114: ('apply', 'As 101.', []),
    115: ('apply', None, ['If the site states conformance to WCAG or anything else, link the standard.']),
    116: ('apply', 'As WCAG 1.1.1.', []), 117: ('apply', 'As WCAG 1.1.1 and 2.4.4.', []),
    118: ('apply', 'As WCAG 1.1.1.', []),
    119: ('apply', None, ['Screenshot thumbnails are small files, not the full screenshot scaled down by the browser.']),
    120: ('apply', 'As WCAG 1.1.1, for the four Flash embeds (R5).', []),
    121: ('apply', 'As WCAG 1.2.1.', []), 122: ('apply', 'As WCAG 1.2.2.', []),
    123: ('apply', None, ['Media pages show each clip\'s length.']),
    124: ('apply', 'As WCAG 1.4.2.', []), 125: ('apply', 'As WCAG 1.4.2.', []),
    126: ('apply', 'As WCAG 2.2.2.', []), 127: ('apply', 'As WCAG 2.2.2.', []),
    128: ('na', 'No phone numbers.', []), 129: ('na', 'No postal addresses.', []),
    130: ('apply', 'As WCAG 3.1.1.', []),
    131: ('apply', None, ['Links to German-language pages, if any, say so.']),
    132: ('apply', 'As WCAG 3.1.2.', []),
    133: ('na', 'One language.', []), 134: ('na', 'One language.', []), 135: ('na', 'One language.', []),
    136: ('apply', 'As WCAG 2.4.4.', []), 137: ('apply', 'As WCAG 2.4.4.', []),
    138: ('apply', None, ['Links look and behave the same on overrides and archive pages.']),
    139: ('apply', None, ['Underline only links.']), 140: ('apply', 'As WCAG 1.4.1.', []),
    141: ('apply', None, ['Visited links look different.']),
    142: ('apply', None, ['External links (GitHub releases, the Wayback Machine) are marked as leaving the site.']),
    143: ('apply', None, ['Links into pages the archive did not keep (`404.html`, "not restored yet") are marked, or removed.']),
    144: ('apply', None, ['A link that starts a download of the launcher says it is a Windows program.']),
    145: ('na', 'No phone numbers.', []),
    146: ('apply', None, ['Links that open a new window say so; prefer not opening one.']),
    147: ('apply', None, ['The download link shows the format (`.zip`).']),
    148: ('apply', None, ['The download link shows the size, and it matches the current file.']),
    149: ('na', 'All downloads are in English.', []),
    150: ('apply', None, ['The zip name identifies the product and version (`CosmicSupremacy-Resurgence-vX.Y.Z.zip`).']),
    151: ('apply', None, ['Nothing forbids or restricts linking to the site.']),
    152: ('apply', None, ['Every internal link resolves (a link checker over `site/public` before each deploy); the archive\'s dead links go to the "not restored yet" page on purpose.']),
    153: ('apply', None, ['No splash or age gate in front of the content.']),
    154: ('apply', None, ['No pop-ups.']),
    155: ('apply', None, ['Every page links home, including the wiki pages and the 404 page.']),
    156: ('apply', None, ['Wiki pages show where they sit (breadcrumb or section heading).']),
    157: ('apply', None, ['The current menu section is marked.']),
    158: ('apply', 'As WCAG 3.2.3.', []), 159: ('apply', 'As WCAG 1.4.5 and 2.5.3.', []),
    160: ('na', 'No windows or dialogs to close.', []), 161: ('na', 'As 160.', []),
    162: ('na', 'As 160.', []), 163: ('na', 'As 160.', []),
    164: ('apply', 'As WCAG 2.4.1.', []), 165: ('apply', 'As WCAG 2.4.7.', []),
    166: ('apply', 'As WCAG 2.1.1.', []), 167: ('apply', 'As WCAG 2.4.3.', []),
    168: ('apply', None, ['Decide: a site search (a static index, or a Google site-search link) for the 425-page manual, or a site map alone (171).']),
    169: ('apply', 'If a search exists, its results have a URL.', []),
    170: ('apply', 'If a search exists, it can be rerun from its results.', []),
    171: ('apply', None, ['A site map page, linked from every page, generated by `stage.py`.']),
    172: ('na', 'No time limits.', []),
    180: ('apply', None, ['Override pages use the archive\'s styles, so new pages look like the site.']),
    181: ('apply', None, ['Nothing is conveyed by colour alone.']),
    182: ('apply', 'As WCAG 1.4.3.', []),
    183: ('apply', None, ['Pages make sense with styles off (reading order of the layout tables).']),
    184: ('apply', 'As WCAG 1.3.3.', []), 185: ('apply', None, ['Nothing meant for screen readers is hidden with `display:none`.']),
    186: ('apply', 'As WCAG 2.5.8.', []), 187: ('apply', 'As WCAG 1.4.5.', []),
    188: ('apply', None, ['Content inserted by CSS (`::before` text, background images that carry meaning) has an alternative.']),
    189: ('apply', None, ['Arrows and bullets drawn with symbols have text equivalents where they carry meaning.']),
    190: ('apply', None, ['Every `font-family` ends in a generic family.']),
    191: ('apply', None, ['No justified text.']),
    192: ('apply', None, ['Capitals for decoration come from `text-transform`, not typed capitals.']),
    193: ('apply', None, ['No `user-scalable=no` or `maximum-scale` on the viewport.']),
    194: ('apply', 'As WCAG 1.4.10.', []),
    195: ('apply', None, ['A print stylesheet hides the menu (players print manual pages).']),
    196: ('apply', 'As 195.', []),
    197: ('apply', None, ['Every page is served over HTTPS; http redirects to https. Check `cosmicresurgence.com`, `www.`, and `cs-resurgence.web.app`.']),
    198: ('apply', 'Google-managed certificates; check expiry monitoring is Google\'s.', []),
    199: ('done', 'Firebase Hosting sends `Strict-Transport-Security: max-age=31556926` (checked 8 October 2026).', []),
    200: ('apply', None, ['Three pages load resources over `http://`: find them (`grep` for `src=http://`) and fix the URLs or remove the resources.']),
    201: NO_ACCOUNTS, 202: NO_ACCOUNTS, 203: NO_ACCOUNTS, 204: NO_ACCOUNTS, 205: ('apply', 'As R4.', []),
    206: ('done', '`X-Content-Type-Options: nosniff`, live on 8 October 2026.', []),
    207: ('apply', None, ['Every resource has the right Content-Type: the `.php.html` rewrites, `css*.php`, `js*.php` and `rssfeed.php` (headers already set for some in `site/firebase.json`).']),
    208: ('na', 'No transactions.', []),
    209: ('apply', None, ['Firebase Hosting lists no directories: check `/images/` and `/downloads/` on the live site.']),
    210: ('na', 'The X-XSS-Protection header is obsolete and now discouraged; Content-Security-Policy (212) replaces it.', []),
    211: ('done', '`X-Frame-Options: SAMEORIGIN`, live on 8 October 2026.', []),
    212: ('apply', None, ['A Content-Security-Policy, report-only first, because the archive carries inline scripts.']),
    213: ('apply', None, ['No `Server` or `X-Powered-By` detail beyond what Google\'s front end sends.']),
    214: ('apply', None, ['Third-party scripts or styles, if any, carry Subresource Integrity hashes.']),
    215: ('na', 'No secure operations.', []),
    216: ('apply', None, ['No page hides the address bar or opens chromeless windows.']),
    217: ('apply', None, ['If the support address uses the `cosmicresurgence.com` domain, publish SPF, DKIM and DMARC for it; if it uses another provider, nothing to do.']),
    218: ('apply', None, ['`www.cosmicresurgence.com` and `cosmicresurgence.com` both work and one redirects to the other.']),
    219: ('done', '`robots.txt` exists and allows all.', []),
    220: ('apply', None, ['A `sitemap.xml` generated by `stage.py`, named in `robots.txt`.']),
    221: ('na', 'No separate mobile site.', []),
    222: ('apply', None, ['A missing page answers 404, not 200 (Firebase Hosting with `404.html`).']),
    223: ('done', '`site/404.html` is the "not restored yet" page.', []),
    224: ('na', 'Static hosting serves no 403 pages.', []),
    225: ('apply', None, ['The 404 page carries the site menu.']),
    226: ('apply', 'Firebase Hosting compresses; confirm `content-encoding` on HTML and CSS.', []),
    227: ('apply', None, ['Caching headers: long for images, short for HTML (Firebase Hosting defaults, or set in `site/firebase.json`).']),
    228: ('apply', None, ['`Content-Type` carries `charset=utf-8` for HTML.']),
    229: ('na', 'Archived styles are kept as they were; minifying them gains little at this size.', []),
    230: ('na', 'As 229.', []),
    231: ('apply', None, ['Override and news pages carry a machine-readable date (`<time datetime>`).']),
    232: ('done', 'All 229 HTML pages declare a charset.', []),
    233: ('apply', None, ['The declared charset is UTF-8; archive pages may declare ISO-8859-1 (check, and convert in `stage.py`).']),
    234: ('apply', 'As WCAG 1.3.1.', []), 235: ('apply', 'As WCAG 1.3.1.', []),
    236: ('apply', None, ['Duplicate IDs: validate a sample of archive pages and every override.']),
    237: ('apply', None, ['No script blocks copying.']), 238: ('apply', None, ['No script blocks the context menu.']),
    239: ('apply', None, ['No meta refresh or script redirect on the client.']),
    240: ('na', 'No PDFs are hosted.', []), 241: ('na', 'No PDFs are hosted.', []),
    242: ('apply', None, ['Data tables in the manual (stat tables, ship components) have header cells.']),
    243: ('apply', None, ['Data tables in the manual have captions.']),
    244: ('apply', 'As WCAG 1.3.2.', []),
    245: ('apply', None, ['Data is not laid out with spaces or images instead of tables.']),
}

# R items: guidance sources, paraphrased
R = [
    ('R1', 'Core Web Vitals, web.dev "Web Vitals" (Google, updated 31 Oct 2024)',
     'At the 75th percentile of page loads, Largest Contentful Paint is 2.5 s or less, Interaction to Next Paint 200 ms or less, and Cumulative Layout Shift 0.1 or less.',
     ['Measure the home page, the download page and a long manual page with Lighthouse (mobile and desktop).',
      'Images without width and height in the archive are the likely layout-shift cause.']),
    ('R2', 'Lighthouse v13 (Google), default configuration',
     'The automated audits for performance, accessibility, best practices and SEO run against the site and their failures are triaged.',
     ['Run Lighthouse on the same three pages before each site deploy; record the scores here.',
      'Lighthouse catches only part of WCAG; it does not replace the manual items above.']),
    ('R3', 'MDN HTTP Observatory, "Tests & Scoring" (Mozilla), with the OWASP Secure Headers Project list',
     'The site\'s response headers pass the Observatory tests: Content-Security-Policy, Strict-Transport-Security, X-Content-Type-Options, frame-ancestors, Referrer-Policy, and the rest.',
     ['[x] `site/firebase.json` sends `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin` and `X-Frame-Options: SAMEORIGIN` on every path; Firebase Hosting already sends HSTS (live on 8 October 2026).',
      'Content-Security-Policy, report-only first (Opquast 212).',
      'Scan `https://cosmicresurgence.com` and record the grade here.',
      'Add the headers in `site/firebase.json`. Do not take `Cache-Control: no-store` or `Clear-Site-Data` from the OWASP list: both are wrong for a cached public site.']),
    ('R4', 'Opquast 27, 28, 205; OWASP ASVS V14.2.1',
     'No page on the site collects a password or personal data it cannot use.',
     ['[x] Every archived form is wrapped in a disabled fieldset with a note saying it no longer works (`stage.py`, `disable_forms`); live on 8 October 2026 on `register.php` and `forgotpw.php`.',
      'The archived premium and upgrade pages carry no working purchase controls.']),
    ('R5', 'WCAG 2.2 SC 2.1.2 and 1.1.1; Opquast 120',
     'No page depends on a browser plug-in.',
     ['Four pages embed a Flash player (`/images/preloadFLV.swf`): `galaxysettings`, `premiumoverview`, `premiumupgrade`, `voting`. Replace each embed with a still image and a caption, or remove it.']),
    ('R6', 'W3C WCAG-EM 2.0 (Group Note, 23 July 2026)',
     'Conformance of the archive is judged on a structured sample, not on every page.',
     ['Sample: the home page, every override page, the download page, the 404 page, five wiki/manual pages chosen by type (index, long text, data tables, media, concept art), and five randomly chosen archive pages.',
      'Record the sample and its results in this file.',
      'Scope: every page the live site serves is held to this checklist, archived or not (on 8 October 2026 `register.php`, `forgotpw.php`, `voting.php` and `premiumupgrade.php` all answered 200). Archive content that is not published, such as the forum, is out of scope until it is migrated and published.']),
    ('R7', 'Google Search Central, "SEO Starter Guide" (updated 10 Dec 2025) and Search Essentials',
     'Search engines can find, understand and correctly attribute the site.',
     ['A canonical URL on every page (none today), so `.php` and `.php.html` addresses do not compete.',
      'Unique titles and descriptions (Opquast 3, 103).',
      'Register the site in Google Search Console and submit the sitemap (Opquast 220).']),
    ('R8', 'Apache Software Foundation, "Release Download Pages for Projects"',
     'The download page links the file, its checksum, and how to verify it, all over HTTPS.',
     ['[x] The download page offered v0.1.0. It now offers v0.1.1, the latest published release, with its size, date and SHA-256 (matching GitHub\'s digest); live on 8 October 2026.',
      'Make updating the download page part of the release steps (`5 - Release_Checklist.md` REL-3), including when v0.1.8 or later is published.',
      '[x] The page explains how to check the SHA-256 (`certutil -hashfile <zip> SHA256`).',
      'When the launcher is code-signed (M3), say so and name the publisher.']),
    ('R9', 'Microsoft Learn, "SmartScreen reputation for Windows app developers" (2026)',
     'Players are told in advance what Windows will show on first run of an unsigned or newly signed build, and what to do.',
     ['[x] The download page warns about the unrecognised publisher and says Smart App Control can block an unsigned build outright on Windows 11; live on 8 October 2026.']),
]


def load_wcag():
    d = json.load(open(os.path.join(SRC, 'wcag22.json'), encoding='utf-8'))
    for p in d['principles']:
        for g in p['guidelines']:
            for sc in g['successcriteria']:
                yield p['handle'], f"{g['num']} {g['handle']}", sc


JUNK = ('Interested in Digital Quality', 'Mobilize a digital quality',
        'Who drives the Opquast', 'Credits and thanks')


def clean(text):
    for j in JUNK:
        if j in text:
            text = text[:text.index(j)]
    return ' '.join(text.split()).rstrip(' �')


MARK = {'apply': '- [ ]', 'done': '- [x]', 'na': '- N/A', 'deviation': '- DEVIATION', 'out': '- AAA'}


def render(head, d):
    if d is None:
        return [f'- [ ] {head}', '  - TO TAILOR']
    status, note, subs = d
    lines = [f'{MARK[status]} {head}']
    if note:
        lines.append(f'  - N/A: {note}' if status == 'na' else f'  - {note}')
    for s in subs:
        lines.append(f'  - [x] {s[4:]}' if s.startswith('[x] ') else f'  - [ ] {s}')
    return lines


def main():
    counts = {'WCAG 2.2': {}, 'Opquast v5': {}, 'R items': {}}
    out = []
    principle = guideline = None
    for prin, guide, sc in load_wcag():
        if prin != principle:
            principle = prin
            out += ['', f'### {prin}']
        if guide != guideline:
            guideline = guide
            out += ['', f'#### {guide}', '']
        num, level = sc['num'], sc['level']
        d = OUT_OF_TARGET if level == 'AAA' else W.get(num)
        if num == '4.1.1':
            d = W['4.1.1']
        key = d[0] if d else 'untailored'
        counts['WCAG 2.2'][key] = counts['WCAG 2.2'].get(key, 0) + 1
        title = ' '.join((sc.get('title') or '').split())
        out += render(f'**{num} {sc["handle"]}** ({level or "obsolete"}) {title}', d)
    wcag = out

    out, theme = [], None
    for r in csv.DictReader(open(os.path.join(SRC, 'opquast_v5_rules.csv'), encoding='utf-8')):
        rid = int(r['id'])
        if r['theme'] != theme:
            theme = r['theme']
            out += ['', f'### {theme}', '']
        d = O.get(rid, O.get(theme))
        key = d[0] if d else 'untailored'
        counts['Opquast v5'][key] = counts['Opquast v5'].get(key, 0) + 1
        out += render(f'**Opquast {rid}** {clean(r["rule"])}', d)
    opq = out

    out = []
    for rid, source, req, subs in R:
        counts['R items']['apply'] = counts['R items'].get('apply', 0) + 1
        out += [f'- [ ] **{rid}** {req}', f'  - Source: {source}.']
        out += [f'  - [ ] {s}' for s in subs]
    ritems = out

    order = ['apply', 'done', 'deviation', 'na', 'out', 'untailored']
    totals = ['| Source | apply | done | deviation | N/A | AAA | to tailor | total |',
              '|---|---|---|---|---|---|---|---|']
    for name, c in counts.items():
        totals.append(f'| {name} | ' + ' | '.join(str(c.get(k, 0)) for k in order)
                      + f' | {sum(c.values())} |')

    head = [
        '# Website verification checklist',
        '',
        'For cosmicresurgence.com: the static archive of the original Cosmic '
        'Supremacy site, the override pages written for the revival, and the '
        'launcher download. Rerun the items a change touches before each '
        '`firebase deploy --only hosting`.',
        '',
        'Written for the published site. Unchecked items are the gap to it. '
        'Scope is what the live site serves; archive content not yet migrated '
        'and published (the forum, unstaged pages) is outside it until it is '
        '(R6).',
        '',
        '**Sources.** W3C Web Content Accessibility Guidelines 2.2 (W3C '
        'Recommendation, 12 December 2024; criterion text reproduced under the W3C '
        'Document License, https://www.w3.org/TR/WCAG22/) and the Opquast Digital '
        'Quality Framework, Rules 2025, version 5.0 (CC BY-SA 4.0, '
        'https://checklists.opquast.com/en/digital-quality/; rule text reproduced, '
        'and this checklist is shared under the same licence). The R items '
        'paraphrase guidance documents and cite each. Target: WCAG 2.2 Level AA. '
        'See `Checklist_Sources.md` for why these sources were chosen.',
        '',
        'Generated by `build_website_checklist.py` beside this file.',
        '',
        '## How to read an item',
        '',
        '- `- [ ]` applies and is not yet verified; the sub-items are this site\'s '
        'checks.',
        '- `- [x]` verified, with what verified it.',
        '- `- N/A` does not apply, with the reason.',
        '- `- AAA` WCAG Level AAA, beyond the target.',
        '',
        '## The site, as measured on 7 October 2026',
        '',
        '| Fact | Value |',
        '|---|---|',
        '| HTML pages in `site/public` | 229 (set `full`) |',
        '| Pages with no `lang` on `<html>` | 21 |',
        '| Pages with a viewport meta tag | 1 |',
        '| Pages with a canonical link | 0 |',
        '| Pages embedding Flash | 4 |',
        '| Pages with audio or video elements | 17 |',
        '| Pages loading resources over `http://` | 3 |',
        '| Live forms | 2, both archived and both asking for a password (R4) |',
        '| Security headers in `site/firebase.json` | none |',
        '| `sitemap.xml` | absent |',
        '| Privacy page | absent |',
        '| Download page | offers v0.1.0; current build v0.1.8 (R8) |',
        '',
        '## Totals',
        '',
    ] + totals + ['', '## Resurgence items (R)', ''] + ritems + \
        ['', '## WCAG 2.2'] + wcag + ['', '## Opquast Digital Quality Framework v5'] + opq
    text = '\n'.join(head) + '\n'
    with open(OUT, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    print(f'wrote {OUT}')
    for line in totals[2:]:
        print(line)


if __name__ == '__main__':
    main()
