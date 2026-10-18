"""
constants.py: the tuning constants table in STRATEGY.md §5, generated from code

    python constants.py            # print the table
    python constants.py --write    # replace the table in STRATEGY.md

Every module-level numeric constant in the rule modules is listed, with its
inline comment, or the comment block directly above it, as the meaning.
Engine facts (ids, offsets, codes) are not tuning and are skipped by name.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODULES = ("ai.py", "exploit.py", "expand.py", "explore.py", "exterminate.py",
           "intel.py")
SPEC = os.path.join(HERE, "STRATEGY.md")
BEGIN, END = "<!-- constants:begin -->", "<!-- constants:end -->"

# Fixed by the game or the memory layout, not chosen.
ENGINE_FACTS = {"DEFAULT_LENGTH", "TROOP_BAY", "WAR", "NEWS_VECTOR",
                "NEWS_RECORD", "KIND_BATTLE", "OFF_POS", "MAX_TEXT"}

ASSIGN = re.compile(r"^([A-Z][A-Z0-9_]*)\s*=\s*(-?[0-9][0-9._]*(?:\s*\*\s*[0-9]+)?)"
                    r"\s*(?:#\s*(.*))?$")


def constants():
    """[(module, name, value, meaning)] in file order."""
    out = []
    for mod in MODULES:
        lines = open(os.path.join(HERE, mod), encoding="utf-8").read().splitlines()
        for i, line in enumerate(lines):
            m = ASSIGN.match(line)
            if not m or m.group(1) in ENGINE_FACTS:
                continue
            name, value, note = m.group(1), m.group(2), m.group(3) or ""
            # An inline comment can continue on indented comment lines below.
            j = i + 1
            while note and j < len(lines) and re.match(r"^\s+#", lines[j]):
                note += " " + lines[j].strip().lstrip("#").strip()
                j += 1
            if not note:
                above = []
                k = i - 1
                while k >= 0 and lines[k].startswith("#"):
                    above.insert(0, lines[k].lstrip("#").strip())
                    k -= 1
                note = " ".join(above)
            out.append((mod, name, value, note))
    return out


def table():
    rows = ["| Name | Value | Module | Meaning |", "|---|---|---|---|"]
    for mod, name, value, note in constants():
        rows.append(f"| `{name}` | {value} | `{mod}` | {note.replace('|', '/')} |")
    return "\n".join(rows)


def write():
    text = open(SPEC, encoding="utf-8", newline="").read()
    if BEGIN not in text or END not in text:
        sys.exit(f"STRATEGY.md has no {BEGIN} ... {END} block")
    head, rest = text.split(BEGIN, 1)
    _old, tail = rest.split(END, 1)
    new = head + BEGIN + "\n" + table() + "\n" + END + tail
    open(SPEC, "w", encoding="utf-8", newline="").write(new)
    print(f"wrote {len(constants())} constants into {SPEC}")


if __name__ == "__main__":
    if "--write" in sys.argv[1:]:
        write()
    else:
        print(table())
