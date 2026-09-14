# cosmicresurgence.com

The archived cosmicsupremacy.com, rebuilt as a static site and served from
Firebase Hosting.

Live at **https://cosmicresurgence.com** (also `cs-resurgence.web.app`).

## What is tracked and what is not

Tracked, because it is source:

    firebase.json     hosting config: rewrites, headers, 404 behaviour
    404.html          the "not restored yet" page
    stage.py          builds public/ out of the archive
    serve.py          local preview that mimics Firebase's 404 handling
    overrides/        hand-written pages that replace archived ones

Not tracked:

    public/                          rebuilt from scratch by stage.py
    ../tools/wayback/                the 1 GB Wayback mirror and its downloader

The archive is deliberately outside git. Everything in `public/` is derived from
it, so the repo stays small and the site stays reproducible from two inputs: the
mirror, and this directory.

## Building and deploying

```bat
python stage.py --set full
firebase deploy --only hosting
```

Preview locally first, without touching the live site:

```bat
python serve.py                        REM plain, no account needed
firebase emulators:start --only hosting    REM real Firebase behaviour
```

## Page sets

| Set | Pages | Contents |
|---|---|---|
| `home` | 1 | the front page only; everything else 404s |
| `core` | 23 | + the session-free top-level pages |
| `full` | 425 | + the wiki, including the whole manual |

`python stage.py --list --set full` prints a set without copying anything.

## Editing pages

Put edits in `overrides/`, never in `public/` — see `overrides/README.md`.
`stage.py` wipes `public/` on every run.

## Things that will break if changed carelessly

**Never add a catch-all rewrite.** `firebase init` offers to configure the site
as a single-page app, which adds `**` to `/index.html`. That swallows every
broken link and serves the home page instead of the 404. The `rewrites` list
must stay as specific entries only. `cleanUrls` stays `false` too, or Firebase
strips `.html` and mangles the `game_news.php.html` filenames.

**The `.php` content-type headers are load-bearing.** DokuWiki serves its
stylesheet and JavaScript from `css.php` and `js.php`. Without the explicit
`Content-Type` headers in `firebase.json`, browsers reject them and all 261 wiki
pages render unstyled.

**The forum is withheld deliberately.** `EXCLUDE_PREFIXES` in `stage.py` keeps
`forum/` out of the build because those pages carry real users' posts, names and
avatars. It is enforced in code so that adding pages later cannot leak it.

**The site is noindexed on purpose** while it is still mostly 404s: an
`X-Robots-Tag` header on `**`, plus a meta tag injected into every page.
`robots.txt` allows crawling on purpose — a `Disallow` would stop crawlers ever
reading the noindex and could leave bare URLs listed anyway. Remove all three
together, or none.

## How links get fixed

The mirror stores pages under filenames encoding the original query string
(`game_news@sid=abc.php.html`) while the markup links to `/game_news.php?sid=abc`.
A static host strips the query and looks for a file named `game_news.php`.

Rather than reverse those filenames — ambiguous, since query strings can contain
dots and long ones were hashed away — `stage.py` maps forward: it asks the
mirror's own `local_path_for()` what filename a given URL would have produced,
and repoints the link if that file is staged. It also retries with `sid`
stripped, so a link carrying a session id that was never captured still finds
the clean copy of the page.

`firebase.json` additionally rewrites the 22 original-style URLs
(`/game_news.php` to `/game_news.php.html`) so inbound links from old forum
posts and search results still land.

## Known gaps

- `indexer.php` (a DokuWiki tracking beacon) and `fetch.php` are referenced but
  absent from the mirror. Both are invisible to a reader.
- A handful of manual images (`resource1.png` and friends) were never captured.
- Roughly 14% of internal links still 404; about a quarter of those are the
  withheld forum.
