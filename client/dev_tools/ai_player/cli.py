"""
cli.py — the tiny argument helpers the tools share
==================================================
These lived as a copy-pasted closure in every script, which meant the same bug
lived in every script: `args[args.index(name) + 1]` raises IndexError when a
flag is the last argument, so `build.py --new-design` died with a traceback
instead of saying what was missing.

Nothing here is clever. It just fails with a sentence instead of a stack trace.
"""
import sys


def opt(args, name, default=None, cast=None):
    """Value following `name`, else `default`.

    A flag present with no value is a usage error, not an IndexError, and a
    flag followed by another flag is treated the same way — `--civ --apply`
    means someone forgot the civ name.
    """
    if name not in args:
        return default
    i = args.index(name) + 1
    if i >= len(args) or args[i].startswith("--"):
        sys.exit(f"error: {name} needs a value")
    value = args[i]
    if cast is None:
        return value
    try:
        return cast(value)
    except ValueError:
        sys.exit(f"error: {name} expected {cast.__name__}, got {value!r}")


def opts(args, name, cast=None):
    """Every value following a repeated `name`, in the order given.

    `--civ A --civ B` is how duel.py names both sides; opt() would silently
    return only the first, which is exactly the kind of quiet half-answer this
    module exists to avoid.
    """
    out = []
    for i, a in enumerate(args):
        if a != name:
            continue
        if i + 1 >= len(args) or args[i + 1].startswith("--"):
            sys.exit(f"error: {name} needs a value")
        value = args[i + 1]
        if cast is not None:
            try:
                value = cast(value)
            except ValueError:
                sys.exit(f"error: {name} expected {cast.__name__}, "
                         f"got {value!r}")
        out.append(value)
    return out


def flag(args, name):
    return name in args
