"""cli.py: argument helpers shared by the tools."""
import sys


def opt(args, name, default=None, cast=None):
    """Value following `name`, else `default`.

    A flag with no value, or followed by another flag, is a usage error.
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
    """Every value following a repeated `name`, in the order given."""
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
