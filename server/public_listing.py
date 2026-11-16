"""
public_listing.py: the open galaxies, as a public file the website reads

    import public_listing
    public_listing.publish(store)          # after each tick, from the worker

The referee writes `public/galaxies.json` to the galaxy store's bucket after
every tick. It is the one object `beta_storage.rules` lets anyone read, and the
website's Galaxies panel fetches it (`site/overrides/scripts/galaxies.js`).

A row carries the galaxy's title and its current turn. The rows are objects so
links, player counts and descriptions can be added later without breaking a
page that reads only some of them.

Only a Firebase store has a bucket to publish to; any other store is skipped.
"""
import json

import galaxy_directory

OBJECT = 'public/galaxies.json'
# Long enough to spare the bucket a read per page view, short next to a turn.
CACHE_CONTROL = 'public, max-age=300'


def rows(directory) -> list:
    """One row per open galaxy, in the directory's order."""
    return [{'title': g.name, 'turn': g.turn} for g in directory.galaxies()
            if g.status == galaxy_directory.OPEN]


def document(directory) -> bytes:
    return json.dumps({'galaxies': rows(directory)}, indent=1,
                      ensure_ascii=False).encode('utf-8')


def directory_for(store):
    """The directory a Firebase store's galaxy is listed in, sharing its
    credentials, or None for any other store."""
    if not all(hasattr(store, a) for a in ('project', 'prefix', 'fs', 'bucket')):
        return None
    d = galaxy_directory.FirebaseGalaxyDirectory(store.project, store.prefix)
    d._fs = store.fs
    return d


def publish(store, log=print) -> bool:
    """Write the listing beside `store`'s galaxy, and say whether it was written.

    Never raises except `KeyboardInterrupt`: the listing is not worth a turn.
    """
    try:
        directory = directory_for(store)
        if directory is None:
            return False
        blob = store.bucket.blob(OBJECT)
        blob.cache_control = CACHE_CONTROL
        blob.upload_from_string(document(directory),
                                content_type='application/json')
        return True
    except KeyboardInterrupt:
        raise
    except BaseException as exc:                            # noqa: BLE001
        log(f'  worker: could not publish the galaxy listing '
            f'({type(exc).__name__}: {exc}); the turn is unaffected')
        return False
