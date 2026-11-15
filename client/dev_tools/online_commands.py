"""
online_commands.py: the original client's server commands, read from the exe

    python online_commands.py [<exe>]            # the table, TestBed by default
    python online_commands.py --json [<exe>]

The original client is a socket client. Each player action is a command: the
action's routine opens a message on the channel at `[0x0086F198]`, writes one
section named for the command, sends it and blocks for the server's answer.
With no live connection and `[0x0086F1A0]` set, the same routine applies the
action locally instead, which is the path every client in this project takes.

A command is found as the call that opens a message (`0x00562030`) followed by
`push <tag>` and the call that begins a section (`0x005E6260`). The routine is
the nearest call target or table entry at or before that call. The server's own messages are the tags the
dispatcher at `0x00573EA0` compares.

The four clients share these addresses.
"""
import argparse
import json
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_DIR = os.path.dirname(HERE)
DEFAULT_EXE = os.path.join(CLIENT_DIR, "CosmicSupremacy_TestBed.exe")

OPEN_MESSAGE = 0x00562030    # channel method: a new outgoing message
BEGIN_SECTION = 0x005E6260   # Archive::BeginSection(tag, version)
SEND_EMPTY = 0x00561C90      # message method: an empty section, sent
DISPATCH = (0x00573EA0, 0x00574172)   # the server-message dispatcher
WINDOW = 24                  # bytes after the open call to look for the tag


class Image:
    """A PE file mapped to virtual addresses, with no dependency."""

    def __init__(self, data):
        self.data = data
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        nsec = struct.unpack_from("<H", data, pe + 6)[0]
        opt = struct.unpack_from("<H", data, pe + 20)[0]
        self.base = struct.unpack_from("<I", data, pe + 24 + 28)[0]
        self.sections = []
        at = pe + 24 + opt
        for _ in range(nsec):
            name = data[at:at + 8].rstrip(b"\0")
            vsize, va, rsize, raw = struct.unpack_from("<IIII", data, at + 8)
            self.sections.append((name, self.base + va, vsize, raw, rsize))
            at += 40

    def text(self):
        for name, va, vsize, raw, rsize in self.sections:
            if name == b".text":
                return va, self.data[raw:raw + min(vsize, rsize)]
        raise ValueError("no .text section")


def _is_tag(value):
    t = struct.pack(">I", value)
    return all(65 <= c <= 90 or 48 <= c <= 57 for c in t)


def _entry_points(img, base, code):
    """Every `call rel32` target in `.text`, and every dword elsewhere that
    points into it (vtables, message maps), sorted."""
    top = base + len(code)
    found = set()
    for m in re.finditer(b"\xE8", code):
        o = m.start()
        if o + 5 > len(code):
            break
        t = (base + o + 5 + struct.unpack_from("<i", code, o + 1)[0]) & 0xFFFFFFFF
        if base <= t < top:
            found.add(t)
    for name, _va, _vsize, raw, rsize in img.sections:
        if name in (b".rdata", b".data"):
            blob = img.data[raw:raw + rsize]
            for o in range(0, len(blob) - 3, 4):
                t = struct.unpack_from("<I", blob, o)[0]
                if base <= t < top:
                    found.add(t)

    def after_end(t):
        # A function begins where the one before it ended: after int3
        # padding, a `ret` or a `ret imm16`.
        o = t - base
        return o >= 3 and (code[o - 1] in (0xCC, 0xC3) or code[o - 3] == 0xC2)
    return sorted(t for t in found if after_end(t))


def _function_start(entries, va):
    """The nearest entry point at or before `va`."""
    lo, hi = 0, len(entries)
    while lo < hi:
        mid = (lo + hi) // 2
        if entries[mid] <= va:
            lo = mid + 1
        else:
            hi = mid
    return entries[lo - 1] if lo else None


def _calls_to(code, base, target):
    out = []
    for m in re.finditer(b"\xE8", code):
        o = m.start()
        if o + 5 > len(code):
            break
        rel = struct.unpack_from("<i", code, o + 1)[0]
        if (base + o + 5 + rel) & 0xFFFFFFFF == target:
            out.append(o)
    return out


def commands(data):
    """[{tag, version, routine, site}] for every command the client sends."""
    img = Image(data)
    base, code = img.text()
    entries = _entry_points(img, base, code)
    found = []
    for o in _calls_to(code, base, OPEN_MESSAGE):
        tail = code[o + 5:o + 5 + WINDOW]
        for m in re.finditer(b"\x68", tail):
            at = m.start()
            if at + 5 > len(tail):
                break
            value = struct.unpack_from("<I", tail, at + 1)[0]
            if not _is_tag(value):
                continue
            # The section's version is the push just before the tag.
            version = None
            if at >= 2 and tail[at - 2] == 0x6A:
                version = tail[at - 1]
            elif at >= 1 and tail[at - 1] in (0x53, 0x55, 0x56, 0x57):
                version = 0     # push of a register this compiler zeroes
            begins = [b for b in _calls_to(tail[at + 5:], base + o + 5 + at + 5,
                                           BEGIN_SECTION)]
            if not begins:
                continue
            found.append({
                "tag": struct.pack(">I", value).decode(),
                "version": version,
                "routine": _function_start(entries, base + o),
                "site": base + o,
            })
            break
    # A command with no body: `push <tag>`, open a message, then
    # `0x00561C90(tag)` writes the empty section and sends it.
    for o in _calls_to(code, base, SEND_EMPTY):
        head = code[max(0, o - 16):o]
        at = head.rfind(b"\x68")
        if at < 0 or at + 5 > len(head):
            continue
        value = struct.unpack_from("<I", head, at + 1)[0]
        if _is_tag(value):
            found.append({
                "tag": struct.pack(">I", value).decode(),
                "version": 0,
                "routine": _function_start(entries, base + o),
                "site": base + o,
            })
    found.sort(key=lambda c: c["site"])
    return found


def server_messages(data):
    """Tags the dispatcher compares, in the order it tests them."""
    img = Image(data)
    base, code = img.text()
    lo, hi = DISPATCH[0] - base, DISPATCH[1] - base
    tags = []
    for m in re.finditer(b"\x3D", code[lo:hi]):
        at = lo + m.start()
        value = struct.unpack_from("<I", code, at + 1)[0]
        if _is_tag(value):
            tag = struct.pack(">I", value).decode()
            if tag not in tags:
                tags.append(tag)
    return tags


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("exe", nargs="?", default=DEFAULT_EXE)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    with open(a.exe, "rb") as f:
        data = f.read()
    cmds, pushed = commands(data), server_messages(data)
    if a.json:
        print(json.dumps({"commands": cmds, "server": pushed}, indent=1))
        return 0
    print(f"{len(cmds)} commands the client sends")
    for c in cmds:
        v = "?" if c["version"] is None else c["version"]
        print(f"  {c['tag']}  v{v:<2}  routine 0x{c['routine']:08X}  "
              f"send 0x{c['site']:08X}")
    print(f"{len(pushed)} messages the server sends: {' '.join(pushed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
