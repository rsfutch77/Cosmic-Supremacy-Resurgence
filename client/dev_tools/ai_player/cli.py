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


def flag(args, name):
    return name in args
