"""
test_fleets_merge.py: fleets survive the tick
=============================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_fleets_merge.py

A fleet is a `GLXY > FLET` holding its ships' `SHPR`s; the ships leave `GLXY`
while in it. The merge carries forming, renaming, fleet orders, ships leaving
and joining, and disbanding, for the submitter's own fleets, and refuses with
a note what would leave the galaxy unsafe for the engine.

Fixtures in `fixtures/tier5`, one Single Player session at turn 0 (25 October
2026), DemoPlayer's two Colony Ships 193 and 194 together: `f0c` no fleet,
`f1` forms fleet 202, `f2` renames it `fleet1`, `f3` gives it a move order,
`f4` takes 194 out, `f5` puts 194 back, `f6` disbands it. `fixtures/tier4` is
an earlier session: `t4g` forms fleet 204 from 192 and 193, `t4i` renames it
and orders it to move, `t4j` takes 193 out, `t4k` disbands it.
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools')):
    if d not in sys.path:
        sys.path.insert(0, d)

import save_parser as sp                                        # noqa: E402
import inject_design as idg                                     # noqa: E402
import merge_orders as mo                                       # noqa: E402

FIX = os.path.join(HERE, 'fixtures')
PASS, FAIL = [], []

ME, THEM = 191, 195                          # DemoPlayer, BadGuy in tier5
HIGH_WATER = 8


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def quiet(_msg=''):
    pass


def load(name):
    return sp.load_any(os.path.join(FIX, name + '.dat'))


def merged(served, *subs):
    notes = {}
    out = mo.merge(served, list(subs), log=quiet, notes=notes)
    return out, notes


def high(blob):
    return struct.unpack_from('<I', blob, HIGH_WATER)[0]


# Builders that write SHIPs and FLETs by hand, the way the client does, so the
# submissions below do not depend on the merge's own writers.

def objects(blob):
    """GLXY's SHIP and FLET sections, whole, in order."""
    glxy = next(sp.parse_blob(blob)[0].find('GLXY'))
    return [bytes(blob[c.start:c.end]) for c in glxy.children
            if c.tag in (b'SHIP', b'FLET')]


def with_objects(blob, objs):
    """`blob` with GLXY's run of SHIPs and FLETs replaced by `objs`."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    run = [c for c in glxy.children if c.tag in (b'SHIP', b'FLET')]
    body = (blob[glxy.payload:run[0].start] + b''.join(objs)
            + blob[run[-1].end:glxy.end])
    return sp.replace_payload(blob, tree, glxy, body)


def oid(sec):
    return struct.unpack_from('<I', sec, 8)[0]


def parts(sec):
    """(20-byte header, [whole child sections]) of a SHIP or FLET."""
    kids, at = [], 28
    while at < len(sec):
        size = struct.unpack_from('<I', sec, at + 4)[0] & sp.SIZE_MASK
        kids.append(sec[at:at + 8 + size])
        at += 8 + size
    return sec[8:28], kids


def members(fleet_sec):
    """The whole SHPR sections in a FLET's FLDT."""
    fldt = parts(fleet_sec)[1][1]
    n = struct.unpack_from('<I', fldt, 8)[0]
    at, out = 16 + n, []
    while at < len(fldt):
        size = struct.unpack_from('<I', fldt, at + 4)[0] & sp.SIZE_MASK
        out.append(fldt[at:at + 8 + size])
        at += 8 + size
    return out


def naming(shpr, fleet):
    """A whole SHPR section naming `fleet` as the last one its ship was in."""
    out = bytearray(shpr)
    struct.pack_into('<I', out, len(out) - 8, fleet)
    return bytes(out)


def crewed(shpr, records):
    """A whole SHPR section holding these crew records."""
    body = shpr[8:]
    n = struct.unpack_from('<I', body, 4)[0]
    body = (body[:4] + struct.pack('<I', len(records)) + b''.join(records)
            + body[8 + 9 * n:])
    return sp.build_section(b'SHPR', 4, body)


def fleet(fid, head, dyno, name, shprs, count=None):
    fldt = (struct.pack('<I', len(name)) + name
            + struct.pack('<I', len(shprs) if count is None else count)
            + b''.join(shprs))
    return sp.build_section(b'FLET', 0, struct.pack('<I', fid) + head[4:] + dyno
                            + sp.build_section(b'FLDT', 1, fldt))


def ship(head, dyno, shpr):
    return sp.build_section(b'SHIP', 0, head + dyno + shpr)


def formed(blob, fid, ships, name=b'Battle Fleet', owner=None, like=None,
           keep=(), count=None):
    """`blob` with `ships` formed into fleet `fid` after the last SHIP, as the
    client forms one, SAVE+0 raised. `keep` ships also stay single."""
    by = {oid(o): o for o in objects(blob)}
    head, (dyno, _shpr) = parts(by[like or ships[0]])
    if owner is not None:
        head = head[:16] + struct.pack('<I', owner)
    shprs = [naming(parts(by[s])[1][1], fid) for s in ships]
    rest = [o for o in objects(blob) if oid(o) not in ships or oid(o) in keep]
    out = with_objects(blob, rest + [fleet(fid, head, dyno, name, shprs, count)])
    return mo.set_high_water(out, fid)


def edited_fleet(blob, fid, name=None, head=None, shprs=None, count=None):
    """`blob` with one FLET rewritten in place."""
    objs = objects(blob)
    at = next(i for i, o in enumerate(objs) if oid(o) == fid)
    old_head, (dyno, fldt) = parts(objs[at])
    n = struct.unpack_from('<I', fldt, 8)[0]
    objs[at] = fleet(fid, head or old_head, dyno,
                     fldt[12:12 + n] if name is None else name,
                     members(objs[at]) if shprs is None else shprs, count)
    return with_objects(blob, objs)


def shifted(head, dx=10.0):
    """An object header whose x is moved."""
    out = bytearray(head)
    struct.pack_into('<f', out, 4, struct.unpack_from('<f', out, 4)[0] + dx)
    return bytes(out)


def moved(blob, ship_oid):
    """`blob` with one SHIP somewhere else, as if it had flown there."""
    objs = objects(blob)
    at = next(i for i, o in enumerate(objs) if oid(o) == ship_oid)
    head, (dyno, shpr) = parts(objs[at])
    objs[at] = ship(shifted(head), dyno, shpr)
    return with_objects(blob, objs)


def layout(blob, civ=ME):
    """(single ship ids, {fleet: (name, member ids)}) for one civ."""
    singles = sorted(s for s, (owner, _d) in mo.ship_index(blob).items()
                     if owner == civ)
    fleets = {fid: (f['name'], [m for m, _s, _v in f['members']])
              for fid, f in mo.fleet_index(blob).items() if f['owner'] == civ}
    return singles, fleets


def last_fleet(blob, ship_oid):
    """The last fleet a ship's SHPR names, wherever the ship is."""
    return mo.shpr_ids(mo._ship_crew_index(blob)[ship_oid][1])[0]


def main():
    f0c, f1, f2, f3, f4, f5, f6 = (load(f'tier5/f{s}') for s in
                                   ('0c', '1', '2', '3', '4', '5', '6'))

    print('what the fixtures hold')
    check('f1 holds fleet 202, its two ships and the default name',
          layout(f1), ([], {202: (b'Battle Fleet', [193, 194])}))
    check('forming raised SAVE+0 from 201 to 202', (high(f0c), high(f1)),
          (201, 202))
    check('the builder here writes the fleet the client writes',
          formed(f0c, 202, [193, 194]) == f1)
    check('every fleet measured reads', [
        mo.fleet_index(b)[fid]['why'] for b, fid in
        ((f1, 202), (f3, 202), (f4, 202), (load('tier4/t4i'), 204))],
        ['', '', '', ''])
    check('a ship out of a fleet names it as its last fleet',
          (last_fleet(f0c, 194), last_fleet(f1, 194), last_fleet(f4, 194),
           last_fleet(f6, 193)), (0, 202, 202, 202))

    print('each step the client took is carried byte for byte')
    steps = (('forming', 'tier5/f0c', 'tier5/f1'),
             ('renaming', 'tier5/f1', 'tier5/f2'),
             ('a move order', 'tier5/f2', 'tier5/f3'),
             ('a ship leaving', 'tier5/f3', 'tier5/f4'),
             ('a ship joining', 'tier5/f4', 'tier5/f5'),
             ('disbanding', 'tier5/f5', 'tier5/f6'),
             ('forming (tier4)', 'tier4/t4f', 'tier4/t4g'),
             ('a 32-character name and a move order', 'tier4/t4g', 'tier4/t4i'),
             ('a ship leaving (tier4)', 'tier4/t4i', 'tier4/t4j'),
             ('disbanding a fleet of one', 'tier4/t4j', 'tier4/t4k'))
    for label, served, sub in steps:
        served, sub = load(served), load(sub)
        out, notes = merged(served, ('DemoPlayer', sub))
        check(f'{label}: nothing refused', notes, {})
        check('  and the merged turn is what the client saved', out == sub)

    print('several steps in one turn')
    out, notes = merged(f0c, ('DemoPlayer', f3))
    check('forming, renaming and ordering: as the client saved', out == f3)
    out, notes = merged(f0c, ('DemoPlayer', f4))
    check('forming and taking a ship out: nothing refused', notes, {})
    check('  one ship in the fleet, the other single',
          layout(out), ([194], {202: (b'fleet1', [193])}))
    check('  the single ship names the fleet, and SAVE+0 covers it',
          (last_fleet(out, 194), high(out)), (202, 202))
    check('  and the fleet has its order',
          mo.fleet_index(out)[202]['dyno'], mo.fleet_index(f4)[202]['dyno'])
    out, notes = merged(f0c, ('DemoPlayer', f6))
    check('forming and disbanding: nothing refused, nothing left of it',
          (notes, layout(out)), ({}, ([193, 194], {})))
    check('  no ship names a fleet that never reached the galaxy, and SAVE+0 '
          'stays', (last_fleet(out, 193), last_fleet(out, 194), high(out)),
          (0, 0, 201))

    print('one fleet merging into another')
    two = formed(f4, 203, [194])
    check('the served state holds two fleets of one ship each',
          layout(two), ([], {202: (b'fleet1', [193]), 203: (b'Battle Fleet', [194])}))
    out, notes = merged(two, ('DemoPlayer', f5))
    check('nothing refused', notes, {})
    check('  194 joins 202, and 203, left empty, is gone',
          layout(out), ([], {202: (b'fleet1', [193, 194])}))
    check('  and 194 names 202', last_fleet(out, 194), 202)

    print('crew moved inside a fleet is judged and carried')
    shprs = members(objects(f1)[-1])
    crew = mo.crew_of(shprs[0][8:])
    sub = edited_fleet(f1, 202, shprs=[crewed(shprs[0], crew[:1]),
                                       crewed(shprs[1], crew + crew[:1])])
    for label, served in (('forming', f0c), ('already formed', f1)):
        out, notes = merged(served, ('DemoPlayer', sub))
        check(f'{label}: nothing refused', notes, {})
        check('  one soldier moved from 193 to 194',
              {s: len(c) for s, c in mo.population(out, ME)[1].items()},
              {193: 1, 194: 3})

    print('two civs forming a fleet on the same turn claim the same id')
    theirs = formed(f0c, 202, [197, 198])
    check("BadGuy's fleet reads as theirs",
          layout(theirs, THEM), ([], {202: (b'Battle Fleet', [197, 198])}))
    for order in ((('DemoPlayer', f1), ('BadGuy', theirs)),
                  (('BadGuy', theirs), ('DemoPlayer', f1))):
        out, notes = merged(f0c, *order)
        first, second = (ME, THEM) if order[0][0] == 'DemoPlayer' else (THEM, ME)
        ships = {ME: [193, 194], THEM: [197, 198]}
        check(f'{order[0][0]} first: nothing refused', notes, {})
        check('  the first keeps 202 and the second is renumbered to 203',
              (layout(out, first)[1], layout(out, second)[1]),
              ({202: (b'Battle Fleet', ships[first])},
               {203: (b'Battle Fleet', ships[second])}))
        check('  each ship names its own fleet',
              [last_fleet(out, s) for s in ships[first] + ships[second]],
              [202, 202, 203, 203])
        check('  each fleet sits where its ships were',
              (mo.fleet_index(out)[202]['pos'], mo.fleet_index(out)[203]['pos']),
              (mo.fleet_index({ME: f1, THEM: theirs}[first])[202]['pos'],
               mo.fleet_index({ME: f1, THEM: theirs}[second])[202]['pos']))
        check('  SAVE+0 covers both, and the blob parses',
              (high(out), bool(sp.parse_blob(out))), (203, True))

    print("a fleet and another civ's new design claim the same id")
    like = next(d for d in mo.design_index(f0c).values() if d['civ'] == THEM)
    designed = mo.set_high_water(mo.add_design(
        f0c, THEM, idg.build_dsgn(202, 'Probe', like['parts'], THEM)), 202)
    for order in ((('BadGuy', designed), ('DemoPlayer', f1)),
                  (('DemoPlayer', f1), ('BadGuy', designed))):
        out, notes = merged(f0c, *order)
        design_id = next(k for k, d in mo.design_index(out).items()
                         if d['name'] == 'Probe')
        fleet_id = next(iter(layout(out)[1]))
        check(f'{order[0][0]} first: nothing refused', notes, {})
        check('  the second is renumbered, so the ids differ',
              (design_id, fleet_id) if order[0][0] == 'BadGuy'
              else (fleet_id, design_id), (202, 203))

    print('a renumbered fleet is still named by the ship that left it')
    objs = objects(formed(f0c, 202, [197]))
    at = next(i for i, o in enumerate(objs) if oid(o) == 198)
    head, (dyno, shpr) = parts(objs[at])
    objs[at] = ship(head, dyno, naming(shpr, 202))
    left = with_objects(formed(f0c, 202, [197]), objs)
    out, notes = merged(f0c, ('DemoPlayer', f1), ('BadGuy', left))
    check('nothing refused', notes, {})
    check("BadGuy's fleet is 203 and 198 names it",
          (layout(out, THEM), last_fleet(out, 198)),
          (([198], {203: (b'Battle Fleet', [197])}), 203))

    print('what cannot be carried safely is refused, saying why')
    elsewhere = moved(f0c, 194)
    four_apart = moved(f4, 194)
    long_name = b'x' * (mo.MAX_FLEET_NAME + 1)
    cases = {
        'a new fleet under an id already in use': (
            f0c, formed(f0c, 150, [193, 194]),
            'fleet 150 (\'Battle Fleet\') DROPPED, it is new and 150 is not a '
            'new object id'),
        'a new fleet naming another civ as its owner': (
            f0c, formed(f0c, 202, [193, 194], owner=THEM),
            'names BadGuy as its owner'),
        "another civ's ship in a new fleet": (
            f0c, formed(f0c, 202, [193, 197]),
            'ship 197 is not one this civ was served'),
        'a ship that was never served in a new fleet': (
            f0c, edited_fleet(f1, 202, shprs=[
                members(objects(f1)[-1])[0],
                naming(members(objects(f1)[-1])[1][:-4]
                       + struct.pack('<I', 999), 202)]),
            'ship 999 is not one this civ was served'),
        'a ship in two fleets': (
            f0c, with_objects(f1, objects(f1) + [
                fleet(203, parts(objects(f1)[-1])[0],
                      parts(objects(f1)[-1])[1][0], b'two',
                      [members(objects(f1)[-1])[1]])]),
            'ship 194 is placed more than once'),
        'a ship in a fleet and on its own': (
            f0c, formed(f0c, 202, [193, 194], keep=(194,)),
            'ship 194: fleet change DROPPED, the submission places it 2 times'),
        'ships at two positions': (
            elsewhere, formed(elsewhere, 202, [193, 194]),
            'its ships are not all at one position'),
        'a fleet with no ship': (
            f0c, formed(f0c, 202, [], like=193), 'it holds no ship'),
        'a new fleet with a name over 32 bytes': (
            f0c, formed(f0c, 202, [193, 194], name=long_name),
            'the name is 33 bytes and the client cuts one to 32'),
        'a rename over 32 bytes': (
            f1, edited_fleet(f1, 202, name=long_name),
            'fleet 202: rename DROPPED, the name is 33 bytes'),
        'a rename holding control bytes': (
            f1, edited_fleet(f1, 202, name=b'fleet\x01'),
            'fleet 202: rename DROPPED, the name holds bytes outside printable'),
        'an FLDT whose count is wrong': (
            f0c, formed(f0c, 202, [193, 194], count=3),
            "fleet 202 DROPPED, its FLDT does not hold the 3 SHPR(s) it counts"),
        'two fleets under one id': (
            f0c, with_objects(f1, objects(f1) + [objects(f1)[-1]]),
            'the submission holds 2 fleets under its id'),
        'a served fleet that comes back twice': (
            f1, with_objects(f1, objects(f1) + [objects(f1)[-1]]),
            'fleet 202: changes DROPPED, the submission holds 2 fleets'),
        'a served fleet that comes back unreadable': (
            f1, edited_fleet(f1, 202, count=5),
            'fleet 202: changes DROPPED, its FLDT does not hold the 5 SHPR(s)'),
        'moving a fleet': (
            f1, edited_fleet(f1, 202, head=shifted(parts(objects(f1)[-1])[0])),
            'fleet 202: position or owner DROPPED'),
        "joining at another fleet's position": (
            four_apart, f5,
            "ship 194: joining fleet 202 DROPPED, it is not at the fleet's "
            "position"),
        "joining another civ's fleet": (
            theirs, with_objects(theirs, [
                o for o in objects(theirs) if oid(o) != 193][:-1] + [fleet(
                    202, parts(objects(theirs)[-1])[0],
                    parts(objects(theirs)[-1])[1][0], b'Battle Fleet',
                    members(objects(theirs)[-1])
                    + [naming(parts(objects(theirs)[0])[1][1], 202)])]),
            'ship 193: joining fleet 202 DROPPED, the fleet is owned by '
            'BadGuy'),
        "renaming another civ's fleet": (
            theirs, edited_fleet(theirs, 202, name=b'mine now'),
            'fleet 202: DROPPED, owned by BadGuy'),
    }
    for label, (served, bad, fragment) in cases.items():
        out, notes = merged(served, ('DemoPlayer', bad))
        said = notes.get('DemoPlayer', [])
        check(f'{label}: refused', any(fragment in n for n in said), True)
        if not any(fragment in n for n in said):
            print(f'          notes: {said}')
        check('  and both civs\' ships and fleets are as served',
              (layout(out), layout(out, THEM)),
              (layout(served), layout(served, THEM)))
        check('  and the blob parses', bool(sp.parse_blob(out)), True)

    print('a refused fleet does not cost the rest of the submission')
    sub = formed(f0c, 150, [193, 194])
    sub = mo.set_planet_name(sub, 138, b'Kept')
    out, notes = merged(f0c, ('DemoPlayer', sub))
    check('the fleet is refused and the planet renamed',
          (len(notes['DemoPlayer']), mo.planet_index(out)[138][3][1]),
          (1, b'Kept'))

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
