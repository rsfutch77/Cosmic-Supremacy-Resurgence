"""
block_tool.py: the operator blocks one player's messages to another
    python block_tool.py firebase://cs-resurgence/galaxy-one list
    python block_tool.py firebase://cs-resurgence/galaxy-one add Duke Baron
    python block_tool.py firebase://cs-resurgence/galaxy-one remove Duke Baron

`add Duke Baron` stops Duke receiving Baron's messages (Multiplayer_Checklist
M17). A player asks through the Facebook group, since the client has no block
control. The referee reads the list at each turn boundary and drops Baron's
message from Duke's news; Baron keeps its sent copy and is not told. A message
already delivered stays.

The list is the galaxy state's `blocks`, `{civ: [sender, ...]}`, never served
to a player. Both civs must be on the roster. A reclaim takes the reclaimed
civ out of it on both sides, so a newcomer given that name starts clean.

Runs with the operator's administrator credentials against a `firebase://`
spec, or a galaxy directory for development. The relay has no route for it.
The list is read back after every write.
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.dirname(HERE)
for _d in (SERVER, HERE):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import turn_store                                               # noqa: E402

KEY = turn_store.BLOCKS_KEY


class BlockRefused(Exception):
    """An operator request this tool declined, with the reason as its text."""


def current(store) -> dict:
    """`{civ: [sender]}` as stored, empty lists kept."""
    raw = store.state().get(KEY) or {}
    return {c: list(s) for c, s in raw.items() if isinstance(s, list)}


def check_pair(store, civ: str, sender: str) -> None:
    roster = store.civs()
    for who in (civ, sender):
        if who not in roster:
            raise BlockRefused(f'{who} is not on the roster: '
                               f'{", ".join(roster) or "(empty)"}')
    if civ == sender:
        raise BlockRefused('a civ cannot block itself')


def add(store, civ: str, sender: str) -> dict:
    check_pair(store, civ, sender)
    blocks = current(store)
    if sender in blocks.get(civ, []):
        raise BlockRefused(f'{civ} already blocks {sender}')
    blocks[civ] = sorted(set(blocks.get(civ, [])) | {sender})
    # The whole map: a directory store replaces the field, Firestore merges it.
    store.update_state({KEY: blocks})
    return turn_store.blocks_of(store.state())


def remove(store, civ: str, sender: str) -> dict:
    blocks = current(store)
    if sender not in blocks.get(civ, []):
        raise BlockRefused(f'{civ} does not block {sender}')
    blocks[civ] = [s for s in blocks[civ] if s != sender]
    store.update_state({KEY: blocks})
    return turn_store.blocks_of(store.state())


def show(blocks: dict, out) -> None:
    if not blocks:
        print('  nobody blocks anyone', file=out)
    for civ in sorted(blocks):
        print(f'  {civ} blocks {", ".join(sorted(blocks[civ]))}', file=out)


def main(argv=None, out=None) -> int:
    out = out or sys.stdout
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('galaxy', help='firebase://project/galaxy, or a directory')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('list', help='who blocks whom')
    for name, what in (('add', 'stop CIV receiving SENDER\'s messages'),
                       ('remove', 'let them through again')):
        p = sub.add_parser(name, help=what)
        p.add_argument('civ', help='the player who blocks')
        p.add_argument('sender', help='the player blocked')
    args = ap.parse_args(argv)
    store = turn_store.open_store(args.galaxy)
    try:
        if args.cmd == 'add':
            got = add(store, args.civ, args.sender)
        elif args.cmd == 'remove':
            got = remove(store, args.civ, args.sender)
        else:
            got = turn_store.blocks_of(store.state())
    except BlockRefused as exc:
        print(f'refused: {exc}', file=sys.stderr)
        return 2
    show(got, out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
