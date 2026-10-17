"""
bundle.py: the store's modules, copied where a deploy can carry them

    python bundle.py                    refresh functions/cs_store/

A Cloud Functions deploy uploads one directory, so the modules the relay needs
from `server/` are copied into `cs_store/`. The copy is a gitignored build
product: `firebase.json` regenerates it before every deploy, and `ensure`
regenerates it at import in a checkout.

`dev_tools/save_parser.py` keeps its subdirectory, because `turn_store` and
`canonical` import it from `dev_tools` beside themselves.
"""
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))

# Where the copies live.
LIB = os.path.join(HERE, 'cs_store')

# The repo's server directory; absent in a deployed function.
SOURCE = os.path.join(os.path.dirname(HERE), 'server')

# What the relay needs, as paths relative to `server/`. `firebase_store` is the
# store, `turn_store` is the interface and `check_save`, `canonical` is the hash
# a publish records, and `save_parser` is the wire format.
FILES = ('turn_store.py', 'firebase_store.py', 'canonical.py',
         os.path.join('dev_tools', 'save_parser.py'))


def copy(source: str = SOURCE, lib: str = LIB) -> list:
    """Copy the store's modules in, and say which ones changed.

    Files are compared by content.
    """
    changed = []
    for rel in FILES:
        src, dst = os.path.join(source, rel), os.path.join(lib, rel)
        with open(src, 'rb') as f:
            data = f.read()
        try:
            with open(dst, 'rb') as f:
                if f.read() == data:
                    continue
        except FileNotFoundError:
            pass
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        changed.append(rel)
    return changed


def ensure(source: str = SOURCE, lib: str = LIB) -> list:
    """Refresh the copies when there is something to refresh them from.

    A deployed function has no `server/` beside it, so this does nothing there.
    """
    if not os.path.isdir(source):
        return []
    return copy(source, lib)


def main():
    changed = ensure()
    print(f'{LIB}: {len(changed)} file(s) refreshed'
          + (': ' + ', '.join(changed) if changed else ''))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
