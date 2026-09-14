# Hand-written page overrides

Anything in this directory is copied over the staged output, replacing whatever
came out of the archive at the same path. This is how the site gets edited.

The reason it exists: `stage.py` deletes and rebuilds `public/` on every run, so
editing a file in `public/` directly means losing that edit the next time anyone
re-stages. Files here survive, and they are tracked in git, which the archive
and `public/` are not.

## How to override a page

Copy the staged file out of `public/`, keeping its path, then edit it:

```bat
copy public\game_overview.php.html overrides\game_overview.php.html
python stage.py --set full
```

The path here is the path it is served at. `overrides/game_overview.php.html`
becomes `/game_overview.php.html`.

## What still happens to an overridden file

Overrides are applied *before* the rewrite pass, not after, so an edited page is
treated exactly like an archived one:

- links are repointed at staged filenames, so `/game_news.php?sid=...` in your
  markup still resolves;
- the `noindex` meta tag is injected;
- the attribution footer is injected.

So there is no need to paste the footer into an edited page by hand, and no way
to accidentally publish a page without it.

## Adding new files

Files that do not exist in the archive work too. `overrides/images/logo.png`
is published at `/images/logo.png`. This is the place for anything written for
the revival rather than recovered from the original site.

## What does not belong here

`404.html` lives one level up, in `site/`, because it is not a page of the
archived site. `firebase.json` and the staging script are up there for the
same reason.
