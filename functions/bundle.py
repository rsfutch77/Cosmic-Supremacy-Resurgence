"""
bundle.py , the store's modules, copied where a deploy can carry them
=====================================================================
    python bundle.py                    refresh functions/cs_store/

A Cloud Functions deploy uploads one directory. It cannot reach `server/`
sitting beside this one, and a relay that reimplemented the store to get around
that would be a fourth implementation of an interface that already costs work to
keep three of in step. So the four modules the relay needs are copied in, and
the copy is a build product rather than source: `.gitignore` here keeps it out
of the tree, `firebase.json` regenerates it before every deploy, and `ensure`
regenerates it at import in a checkout.

That last part is what stops the copy drifting. A vendored copy that is only
refreshed by hand is a second version of the file that nobody remembers to
update, and the failure is silent: the relay's tests pass against last week's
store. Here, a checkout always tests what `server/` currently says, and a deploy
always ships what was tested.

`dev_tools/save_parser.py` keeps its subdirectory, because `turn_store` and
`canonical` both add `dev_tools` beside themselves to `sys.path` and import it
from there. Copying it flat would need an edit to two files that have no reason
to change.
"""
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))

# Where the copies live. Not `server`, which would shadow nothing here but would
# read as the real thing in a traceback.
LIB = os.path.join(HERE, 'cs_store')

# The repo this was copied from, when there is one. A deployed function has no
# such directory and never looks for it twice.
SOURCE = os.path.join(os.path.dirname(HERE), 'server')

# What the relay needs, as paths relative to `server/`. `firebase_store` is the
# store, `turn_store` is the interface and `check_save`, `canonical` is the hash
# a publish records, and `save_parser` is the wire format.
FILES = ('turn_store.py', 'firebase_store.py', 'canonical.py',
         os.path.join('dev_tools', 'save_parser.py'))


def copy(source: str = SOURCE, lib: str = LIB) -> list:
    """Copy the store's modules in, and say which ones changed.

    Compared by content rather than by timestamp, so that a checkout copied
    about, or a file touched by a tool, does not read as a change.
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

    A deployed function has no `server/` beside it and this does nothing, which
    is the whole of its error handling: the copies are there because the deploy
    carried them.
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
