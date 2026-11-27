"""
battle_reports.py: an ally reads a shared battle report as an outsider would
    import battle_reports
    blob = battle_reports.for_outsiders(blob, log=log)   # after every tick

A battle report is a `NEWS` item of type 2 whose title and body are strings in
`NWDB`. Each civ that fought gets its own text, naming its own ship designs and
every other civ's ships by class (`Shuttle-Class`). An alliance that shares
battle reports makes the engine give the ally an item naming the same strings
as a participant's, so the ally reads that participant's design names
(GitHub #16, on the original developers' list).

`for_outsiders` gives each civ holding a report it did not fight in a body of
its own, with every design name shown by its class, as an enemy's copy shows
it. The title names no design and is kept. A civ's own report and an enemy's
are not touched.

What is not matched: the enemy's copy counts destroyed ships per class
("2 x Shuttle-Class Ships"); an outsider's keeps one line per design, each now
naming its class.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inject_civ as icv
import inject_design as idg
import news

BATTLE = 2                  # NEWS item type of a battle report
# ShipDesign:128, the chassis, as the stat tables name them.
CHASSIS = ('Shuttle', 'Corvette', 'Frigate', 'Destroyer', 'Cruiser',
           'Battleship')
# A ship icon and the label after it, up to the colon or line end.
LABEL = re.compile(rb'(\x0c<Ship\w*>)([^:\n\r\t\x0b\x0c<]+)')
PARTIES = re.compile(rb':\s*([^:]+?)\s*$')


def design_classes(blob):
    """{civ object id: {design name: class label}} for every design that
    parses and names a known chassis."""
    out = {}
    for civ, _data, designs in _design_sections(blob):
        for d in designs:
            try:
                _oid, name, parts, _owner = idg.read_design(blob, d.start)
            except Exception:                                   # noqa: BLE001
                continue
            chassis = (parts or {}).get('chassis') or []
            if chassis and 0 <= chassis[0] < len(CHASSIS):
                out.setdefault(civ, {})[name.encode('latin-1')] = (
                    CHASSIS[chassis[0]].encode('latin-1') + b'-Class')
    return out


def _design_sections(blob):
    import merge_orders
    return merge_orders.design_sections(blob)[0]


def names_of(blob, civ_oid, player_name):
    """The names a battle title may give a civ: its player name, and the
    empire name it chose, when it chose one."""
    import merge_orders
    out = {player_name}
    identity = merge_orders.civ_identity(blob, civ_oid)
    if identity and identity[0]:
        out.add(identity[0])
    return out


def parties(title):
    """The civ names after the title's last colon, split on ' vs '."""
    m = PARTIES.search(title or b'')
    return [p.strip() for p in m.group(1).split(b' vs ')] if m else []


def by_class(body, classes):
    """`body` with each ship label found in `classes` replaced by its class."""
    def swap(m):
        label = m.group(2)
        name = label.rstrip()
        if name not in classes:
            return m.group(0)
        return m.group(1) + classes[name] + label[len(name):]
    return LABEL.sub(swap, body)


def for_outsiders(blob, log=print):
    """`blob` with every battle report held by a civ that did not fight given
    a body naming designs only by class."""
    table = news.read_table(blob)
    if table is None:
        return blob
    next_id, strings = table
    owners = {o['oid']: o['name'].encode('latin-1')
              for o in icv.owner_records(blob)}
    known = {oid: names_of(blob, oid, name) for oid, name in owners.items()}
    by_name = {n: oid for oid, ns in known.items() for n in ns}
    classes = design_classes(blob)
    changes = []
    for oid, name in owners.items():
        for i, item in enumerate(news.items_of(blob, oid) or []):
            if item['type'] != BATTLE:
                continue
            title = strings.get(item['title_id'], item['title'])
            fought = parties(title)
            if not fought or known[oid] & set(fought):
                continue
            # The fighters' designs first, so a name two civs share reads as
            # the class of the one that fought.
            seen = {}
            for civ in [by_name.get(f) for f in fought] + list(classes):
                if civ is not None and civ != oid:
                    for k, v in classes.get(civ, {}).items():
                        seen.setdefault(k, v)
            body = strings.get(item['body_id'], item['body'])
            hidden = by_class(body, seen)
            if hidden != body:
                changes.append((oid, i, item, hidden, title))
    if not changes:
        return blob
    items = []
    for oid, i, item, hidden, title in changes:
        body_id, next_id = news.intern(next_id, strings, hidden)
        items.append((oid, i, dict(item, body=b'', body_id=body_id), title))
    blob = news.write_table(blob, next_id, strings)
    for oid, i, item, title in items:
        blob = news.replace_item(blob, oid, i, news.build_item(item))
        log(f"  battle report: {owners[oid].decode('latin-1')}'s copy of "
            f"{b' vs '.join(parties(title)).decode('latin-1')} shows the "
            f"designs by class")
    return blob
