"""
news.py: each civ's news list and the galaxy's news string table

    import news
    table = news.read_table(blob)            # next id and {id: text}
    items = news.items_of(blob, civ_oid)     # [item dict], in list order
    blob = news.append_item(blob, civ_oid, news.build_item(...))

The strings are in `NWDB` v1 after `GLOB`: a u32 next id, a u32 count, then per
string a u32 id, a u32 hash of the text (`string_hash`), a u32 length and the
text, Latin-1 with no terminator. The client's writer (0x5371C0) and reader
(0x536CF0) are read off the binary.

A civ's list is in its `OWNR > DATA`, after the designs and before `GOVS`: a
u32 count, then one `NEWS` v3 per item (writer 0x5355B0, reader 0x535E70). An
item names its title and body by string id; its own title and body fields are
written empty when it does.
"""
import struct
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import save_parser as sp
import inject_civ as icv

TABLE_HEAD = 8              # next id, count
NEWS_VERSION = 3
RECEIVED, SENT = 1, 6       # item types of a message, as the recipient and the sender hold it
IMPORTANT = 1               # flags bit, "Mark As Important"
# The civ's new-news flag in OWPR, this many bytes before the civ name's length
# field; the client sets it when an item arrives with notify and clears it when
# the news is opened.
NEW_NEWS_BACK = 81
U32 = struct.Struct('<I')


def string_hash(text: bytes) -> int:
    """The client's hash of a table string (0x5E5230): one-at-a-time over the
    bytes as signed chars, A to Z lowered and a backslash read as a slash. A
    leading double quote is skipped and the next one ends the string."""
    mask = 0xFFFFFFFF
    h = 0
    quoted = text[:1] == b'"'
    if quoted:
        text = text[1:]
    for c in text:
        if c == 0 or (quoted and c == 0x22):
            break
        if 0x41 <= c <= 0x5A:
            c += 0x20
        elif c == 0x5C:
            c = 0x2F
        if c >= 0x80:
            c -= 0x100
        h = (h + c) & mask
        h = (h + (h << 10)) & mask
        h ^= h >> 6
    h = (h + (h << 3)) & mask
    h ^= h >> 11
    return (h + (h << 15)) & mask


def _table_section(blob):
    tree = sp.parse_blob(blob)
    return tree, next(tree[0].find('NWDB'), None)


def read_table(blob):
    """(next id, {id: text}) from `NWDB`, or None when it does not read as
    whole entries matching its count."""
    _tree, sec = _table_section(blob)
    if sec is None or sec.size < TABLE_HEAD:
        return None
    next_id, count = struct.unpack_from('<II', blob, sec.payload)
    at, strings = sec.payload + TABLE_HEAD, {}
    while at + 12 <= sec.end:
        sid, _h, n = struct.unpack_from('<III', blob, at)
        if at + 12 + n > sec.end:
            return None
        strings[sid] = bytes(blob[at + 12:at + 12 + n])
        at += 12 + n
    if at != sec.end or len(strings) != count:
        return None
    return next_id, strings


def write_table(blob, next_id, strings):
    """`blob` with `NWDB` holding `strings` ({id: text}) in id order and the
    next id given. Each hash is computed from its text."""
    tree, sec = _table_section(blob)
    body = struct.pack('<II', next_id, len(strings))
    for sid in sorted(strings):
        text = strings[sid]
        body += struct.pack('<III', sid, string_hash(text), len(text)) + text
    return sp.replace_payload(blob, tree, sec, body)


def intern(next_id, strings, text):
    """(id, next id) for `text`: an existing id holding the same text, or a
    new one added to `strings`, as the client does (0x5370A0)."""
    for sid, have in strings.items():
        if have == text:
            return sid, next_id
    strings[next_id] = text
    return next_id, next_id + 1


def parse_item(blob, sec):
    """A `NEWS` v3 payload as a dict, or None when it does not read whole."""
    if sec.version != NEWS_VERSION:
        return None
    p, end = sec.payload, sec.end
    try:
        kind, flags, turn = struct.unpack_from('<III', blob, p)
        p += 12
        out = {'type': kind, 'flags': flags, 'turn': turn}
        for field in ('title', 'body'):
            n = U32.unpack_from(blob, p)[0]
            if p + 4 + n > end:
                return None
            out[field] = bytes(blob[p + 4:p + 4 + n])
            p += 4 + n
        out['title_id'], out['body_id'], n = struct.unpack_from('<III', blob, p)
        p += 12
        if p + 4 * n + 12 != end:
            return None
        out['ids'] = list(struct.unpack_from(f'<{n}I', blob, p))
        out['position'] = bytes(blob[p + 4 * n:end])
    except struct.error:
        return None
    return out


def build_item(item):
    """The `NEWS` v3 section for an item dict as `parse_item` returns it."""
    body = struct.pack('<III', item['type'], item['flags'], item['turn'])
    for field in ('title', 'body'):
        body += U32.pack(len(item[field])) + item[field]
    body += struct.pack('<III', item['title_id'], item['body_id'],
                        len(item['ids']))
    body += struct.pack(f'<{len(item["ids"])}I', *item['ids'])
    body += item['position']
    return sp.build_section(b'NEWS', NEWS_VERSION, body)


def _data_of(blob, civ_oid, tree=None):
    tree = tree or sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    for o in glxy.children:
        if o.tag != b'OWNR':
            continue
        n = U32.unpack_from(blob, o.payload)[0]
        if U32.unpack_from(blob, o.payload + 4 + n)[0] == civ_oid:
            return next(o.find('DATA'), None)
    return None


def list_of(blob, civ_oid):
    """(offset of the count, [NEWS section]) for a civ's list, or None when
    the count does not match the items before its `GOVS`."""
    data = _data_of(blob, civ_oid)
    if data is None:
        return None
    kids = data.children
    govs = next((i for i, c in enumerate(kids) if c.tag == b'GOVS'), None)
    if govs is None:
        return None
    first = govs
    while first > 0 and kids[first - 1].tag == b'NEWS':
        first -= 1
    secs = kids[first:govs]
    count_at = kids[first].start - 4
    if count_at < data.payload or U32.unpack_from(blob, count_at)[0] != len(secs):
        return None
    return count_at, secs


def items_of(blob, civ_oid):
    """[item dict] for a civ's list, each with its section as `sec`, or None
    when the list or any item does not read."""
    found = list_of(blob, civ_oid)
    if found is None:
        return None
    out = []
    for sec in found[1]:
        item = parse_item(blob, sec)
        if item is None:
            return None
        item['sec'] = sec
        out.append(item)
    return out


def append_item(blob, civ_oid, section):
    """`blob` with one `NEWS` section added to the end of a civ's list."""
    count_at, secs = list_of(blob, civ_oid)
    at = secs[-1].end if secs else count_at + 4
    out = bytearray(icv.splice(blob, at, at, section, log=lambda *_: None))
    U32.pack_into(out, count_at, len(secs) + 1)
    return bytes(out)


def set_flags(blob, civ_oid, index, flags):
    """`blob` with the flags of the civ's item at `index` set."""
    sec = list_of(blob, civ_oid)[1][index]
    out = bytearray(blob)
    U32.pack_into(out, sec.payload + 4, flags)
    return bytes(out)


def new_news_at(blob, civ_oid, identity_at):
    """Offset of the civ's new-news flag, from its name's length field."""
    return identity_at - NEW_NEWS_BACK


def clear_list(blob, civ_oid):
    """`blob` with a civ's list emptied. A list that does not read is left."""
    found = list_of(blob, civ_oid)
    if found is None or not found[1]:
        return blob
    count_at, secs = found
    tree = sp.parse_blob(blob)
    data = _data_of(blob, civ_oid, tree)
    payload = (blob[data.payload:count_at] + U32.pack(0)
               + blob[secs[-1].end:data.end])
    return sp.replace_payload(blob, tree, data, payload)


def own_view(blob, civ_oid):
    """`blob` with every other civ's list empty and the string table holding
    only the strings `civ_oid`'s own list names. The next id is kept, so a
    string the civ adds is numbered as in the full table."""
    for o in icv.owner_records(blob):
        if o['oid'] != civ_oid:
            blob = clear_list(blob, o['oid'])
    own = items_of(blob, civ_oid) or []
    named = {i['title_id'] for i in own} | {i['body_id'] for i in own}
    table = read_table(blob)
    if table is not None:
        next_id, strings = table
        blob = write_table(blob, next_id,
                           {k: v for k, v in strings.items() if k in named})
    return blob


def restore_view(blob, civ_oid, sub):
    """`sub`, made from `civ_oid`'s `own_view` of `blob`, with every other
    civ's served items put back ahead of the items it added, and the table
    holding `blob`'s strings and the ones it added. A list `sub` already holds
    whole is left alone, and a string id it gives another text keeps that
    text, for the merge to refuse."""
    for o in icv.owner_records(blob):
        if o['oid'] == civ_oid:
            continue
        auth, now = list_of(blob, o['oid']), list_of(sub, o['oid'])
        if auth is None or now is None or not auth[1]:
            continue
        held = [bytes(blob[s.start:s.end]) for s in auth[1]]
        have = [bytes(sub[s.start:s.end]) for s in now[1]]
        if have[:len(held)] == held:
            continue
        count_at, secs = now
        tree = sp.parse_blob(sub)
        data = _data_of(sub, o['oid'], tree)
        tail = secs[-1].end if secs else count_at + 4
        payload = (sub[data.payload:count_at] + U32.pack(len(held) + len(have))
                   + b''.join(held + have) + sub[tail:data.end])
        sub = sp.replace_payload(sub, tree, data, payload)
    auth, now = read_table(blob), read_table(sub)
    if auth is not None and now is not None:
        strings = dict(auth[1])
        strings.update(now[1])
        if strings != now[1]:
            sub = write_table(sub, max(auth[0], now[0]), strings)
    return sub
