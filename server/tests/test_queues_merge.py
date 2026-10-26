"""
test_queues_merge.py: production queues survive the tick
========================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_queues_merge.py

A queue is an SPRQ in the civ's OWNR > DATA > SPQS and a planet joins it
through its PROD: a flag at +2, the queue's id at +3 and the entry it is
building at +7. Without the merge carrying both, a planet joining a queue kept
what it was building and the queue was gone after the tick.

The fixtures are one Single Player session on clean.dat at turn 111 (25
October 2026), civ GoodGuy (656), HQ 265 and a second planet 261, saved after
each step: `q1base2` queue "newqueue" (674) with one farm, unassigned;
`q1assign` assigned to the HQ; `q2b` a Colony Ship entry added; `q3` farm count
3; `q4` farm "build up to"; `q5` ship infinite; `q6` reordered; `q7` shared
with 261; `q8` farm deleted; `q9` 261 set to Wealth; `q10` HQ overridden by
hand; `q11s2` some turns later, the HQ back on the queue; `q12` HQ set to
Wealth; `q13` the queue deleted.

Each merge parses the galaxy dozens of times, so this runs for minutes.
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

FIX = os.path.join(HERE, 'fixtures', 'tier5')
PASS, FAIL = [], []

ME, THEM = 656, 660                          # GoodGuy, BadGuy
HQ, SECOND, THEIR_HOME = 265, 261, 384
QUEUE = 674
COLONY_SHIP = 657


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


def about_queues(notes):
    """The notes that concern production queues or production."""
    return [n for n in sum(notes.values(), [])
            if 'queue' in n or 'production' in n]


def sprq(oid, name, entries, using=0, counter=2):
    """A whole SPRQ section as the client writes one. Each entry is (entry id,
    count, infinite, count mode, item type, item); item is None for an item
    type with nothing after it."""
    body = struct.pack('<HI', counter, len(name)) + name
    body += struct.pack('<I', len(entries))
    for eid, count, inf, mode, itype, item in entries:
        body += struct.pack('<HHBBI', eid, count, inf, mode, itype)
        if item is not None:
            body += struct.pack('<I', item)
    return sp.build_section(b'SPRQ', 0, struct.pack('<IIB', oid, using, 0)
                            + sp.build_section(b'PRQU', 1, body))


FARM = (1, 1, 0, 0, mo.ITEM_FACILITY, 0)


def linked(blob, planet, qid, entry, like=None):
    """`blob` with a planet's PROD linked to a queue entry. `like` is a PROD
    to start from instead of the planet's own."""
    prod = bytearray(like or mo.planet_index(blob)[planet][1])
    struct.pack_into('<BIH', prod, 8 + mo.PROD_QUEUE_OFF, 1 if qid else 0,
                     qid, entry)
    return mo.replace_prod(blob, planet, bytes(prod))


def high_water(blob, oid):
    out = bytearray(blob)
    struct.pack_into('<I', out, idg.HIGH_WATER_ID, oid)
    return bytes(out)


def state(blob, civ=ME):
    """What the merge must reproduce: the civ's queues and its planets'
    links."""
    links = mo.queue_links(blob)
    planets = sorted(p for p, v in mo.planet_index(blob).items()
                     if v[0] == civ)
    return mo.queues_of(blob, civ), {p: links.get(p) for p in planets}


def consistent(blob):
    """'' when every link names a queue the galaxy holds, on an entry it
    holds, and every queue counts the planets linked to it."""
    queues = {oid: r for civ in mo.queues_by_civ(blob).values()
              for oid, r in civ or ()}
    using = {}
    for planet, (flag, qid, entry) in mo.queue_links(blob).items():
        if flag != 1:
            continue
        if qid not in queues:
            return f'planet {planet} names queue {qid}, which is not there'
        entries = {e[0] for e in mo.read_queue(queues[qid])[0]}
        if entry and entry not in entries:
            return f'planet {planet} is on entry {entry} of queue {qid}'
        using[qid] = using.get(qid, 0) + 1
    for oid, record in queues.items():
        count = struct.unpack_from('<I', record, 8 + mo.SPRQ_USING_AT)[0]
        if count != using.get(oid, 0):
            return f'queue {oid} counts {count} planet(s)'
    return ''


def entries_of(blob, civ, qid):
    return [e[:3] for e in mo.read_queue(dict(mo.queues_of(blob, civ))[qid])[0]]


def main():
    base, assign = load('q1base2'), load('q1assign')
    # The civ before it made a queue: SPQS as a civ that never had one, and
    # SAVE+0 one below the queue's id.
    noq = high_water(mo.set_queues(base, ME, []), QUEUE - 1)

    print('what the fixtures hold')
    check("the parser reaches both civs' SPQS, GoodGuy's past its news",
          [s.tag for s in sp.flatten(sp.parse_blob(base)) if s.tag == b'SPQS'],
          [b'SPQS', b'SPQS'])
    check("GoodGuy's governors are read too, none of them",
          mo.governors_of(base, ME), [])
    check('and it is found by its tag, with the one queue',
          [oid for oid, _r in mo.queues_of(base, ME)], [QUEUE])
    check('the layout written here is the one the client writes',
          sprq(QUEUE, b'newqueue', [FARM, (3, 1, 0, 0, mo.ITEM_SHIP,
                                           COLONY_SHIP)], using=1, counter=4),
          mo.queues_of(load('q2b'), ME)[0][1])
    check('the entries carry ids, which move with them when reordered',
          [entries_of(load(s), ME, QUEUE) for s in ('q5', 'q6')],
          [[(1, 0, 0), (3, 8, COLONY_SHIP)], [(3, 8, COLONY_SHIP), (1, 0, 0)]])
    check('a planet on the queue names the entry it builds, 0 when overridden',
          [mo.queue_link(mo.planet_index(load(s))[HQ][1])
           for s in ('q1assign', 'q4', 'q10')],
          [(1, QUEUE, 1), (1, QUEUE, 3), (1, QUEUE, 0)])
    check('deleting the queue leaves SPQS as a civ that never had one',
          mo.queues_of(load('q13'), ME), [])
    check('the made-up starting state holds no queue, and 674 is free',
          (mo.queues_of(noq, ME), idg.max_object_id(noq)), ([], QUEUE - 1))

    print('each step the client took is carried as it took it')
    steps = (('creating a queue', noq, base),
             ('creating and assigning it in one turn', noq, assign),
             ('assigning it to a planet', base, assign),
             ('adding a ship entry', assign, load('q2b')),
             ('editing a count', load('q2b'), load('q3')),
             ('editing the count mode', load('q3'), load('q4')),
             ('making an entry infinite', load('q4'), load('q5')),
             ('reordering', load('q5'), load('q6')),
             ('sharing it with a second planet', load('q6'), load('q7')),
             ('deleting an entry', load('q7'), load('q8')),
             ('a planet leaving it through Wealth', load('q8'), load('q9')),
             ('overriding a queued planet by hand', load('q9'), load('q10')),
             ('the last planet leaving it', load('q11s2'), load('q12')),
             ('deleting it', load('q12'), load('q13')))
    for label, served, sub in steps:
        out, notes = merged(served, ('GoodGuy', sub))
        check(f'{label}: nothing about queues refused', about_queues(notes), [])
        check('  and the merged turn holds what the client wrote',
              state(out), state(sub))
        check('  every link resolves and every count is right',
              consistent(out), '')
        check('  the other civ is untouched',
              state(out, THEM), state(served, THEM))
    out, _ = merged(noq, ('GoodGuy', base))
    check('SAVE+0 covers the new queue',
          struct.unpack_from('<I', out, idg.HIGH_WATER_ID)[0], QUEUE)

    print('the planet counts follow the links as merged')
    wrong = mo.set_queues(assign, ME, [mo.with_using(
        mo.queues_of(assign, ME)[0][1], 5)])
    out, notes = merged(base, ('GoodGuy', wrong))
    check('a queue claiming five planets counts the one linked to it',
          struct.unpack_from('<I', mo.queues_of(out, ME)[0][1],
                             8 + mo.SPRQ_USING_AT)[0], 1)
    check('  without a note', about_queues(notes), [])

    print('two civs making a queue on the same turn claim the same id')
    farm_prod = mo.planet_index(assign)[HQ][1]
    theirs = mo.set_queues(noq, THEM, [sprq(QUEUE, b'bq', [FARM], using=1)])
    theirs = high_water(linked(theirs, THEIR_HOME, QUEUE, 1, like=farm_prod),
                        QUEUE)
    home_of = {ME: HQ, THEM: THEIR_HOME}
    for order in ((('GoodGuy', assign), ('BadGuy', theirs)),
                  (('BadGuy', theirs), ('GoodGuy', assign))):
        out, notes = merged(noq, *order)
        first, second = (ME, THEM) if order[0][0] == 'GoodGuy' else (THEM, ME)
        check(f'{order[0][0]} first: nothing about queues refused',
              about_queues(notes), [])
        check('  the first keeps 674 and the second is renumbered to 675',
              ([oid for oid, _r in mo.queues_of(out, first)],
               [oid for oid, _r in mo.queues_of(out, second)]),
              ([QUEUE], [QUEUE + 1]))
        links = mo.queue_links(out)
        check('  each planet names its own civ\'s queue',
              (links[home_of[first]], links[home_of[second]]),
              ((1, QUEUE, 1), (1, QUEUE + 1, 1)))
        check('  every link resolves and every count is right',
              consistent(out), '')
        check('  and SAVE+0 covers both',
              struct.unpack_from('<I', out, idg.HIGH_WATER_ID)[0], QUEUE + 1)

    print("a queue and another civ's new design claim the same id")
    like = next(d for d in mo.design_index(base).values() if d['civ'] == THEM)
    probe = high_water(mo.add_design(
        noq, THEM, idg.build_dsgn(QUEUE, 'Probe', like['parts'], THEM)), QUEUE)
    for order in ((('BadGuy', probe), ('GoodGuy', assign)),
                  (('GoodGuy', assign), ('BadGuy', probe))):
        out, notes = merged(noq, *order)
        design_id = next(oid for oid, d in mo.design_index(out).items()
                         if d['name'] == 'Probe')
        queue_id = mo.queues_of(out, ME)[0][0]
        check(f'{order[0][0]} first: nothing about queues refused',
              about_queues(notes), [])
        check('  the second is renumbered, so the ids differ',
              (queue_id, design_id) if order[0][0] == 'GoodGuy'
              else (design_id, queue_id), (QUEUE, QUEUE + 1))
        check('  and the HQ names its queue',
              mo.queue_links(out)[HQ], (1, queue_id, 1))

    print('a queue naming a design that is renumbered in the merge')
    mine_like = mo.design_index(base)[COLONY_SHIP]
    ours = mo.add_design(noq, ME, idg.build_dsgn(
        QUEUE, 'Hauler', mine_like['parts'], ME))
    ours = mo.set_queues(ours, ME, [sprq(
        QUEUE + 1, b'hq', [FARM, (3, 1, 0, 0, mo.ITEM_SHIP, QUEUE)],
        using=1, counter=4)])
    ours = high_water(linked(ours, HQ, QUEUE + 1, 1, like=farm_prod),
                      QUEUE + 1)
    out, notes = merged(noq, ('BadGuy', probe), ('GoodGuy', ours))
    hauler = next(oid for oid, d in mo.design_index(out).items()
                  if d['name'] == 'Hauler')
    (qid, _r), = mo.queues_of(out, ME)
    check('nothing about queues refused', about_queues(notes), [])
    check('the design is renumbered past BadGuy\'s', hauler, QUEUE + 1)
    check('the queue is renumbered past the design', qid, QUEUE + 2)
    check('its ship entry names the design under its new id',
          entries_of(out, ME, qid), [(1, 0, 0), (3, 8, hauler)])
    check('and the HQ names the queue under its new id',
          mo.queue_links(out)[HQ], (1, qid, 1))
    check('every link resolves and every count is right', consistent(out), '')

    print('what the merge cannot carry safely is refused, saying why')
    designs = mo.designs_of(base, ME)
    types = mo.facility_rules.type_count(base)

    def judged(sub, served=noq, remap=None):
        new, edited, deleted, why = mo.queue_orders(
            served, sub, ME, designs, remap or {}, types)
        return new, edited, deleted, why

    def ours_with(*records):
        return high_water(mo.set_queues(noq, ME, list(records)), QUEUE + 1)

    tree_civs = mo._spqs(noq)[1]
    _data, at, end = tree_civs[ME]
    miscounted = bytearray(noq)
    struct.pack_into('<I', miscounted, at + 8, 1)
    whole = sprq(QUEUE, b'q', [FARM])
    head, entries = whole[8:8 + mo.SPRQ_FIXED], whole[8 + mo.SPRQ_FIXED + 8:]
    cut = sp.build_section(b'SPRQ', 0, head + sp.build_section(
        b'PRQU', 1, entries[:-4 - mo.QUEUE_ENTRY]))
    old = sp.build_section(b'SPRQ', 0, head + sp.build_section(
        b'PRQU', 0, entries))
    cases = {
        'a new queue under an id already in use': (
            mo.set_queues(noq, ME, [sprq(600, b'q', [FARM])]),
            'production queue 600 DROPPED, it is new and 600 is not a new '
            'object id'),
        'a ship entry naming a design nobody holds': (
            ours_with(sprq(QUEUE, b'q', [(1, 1, 0, 0, 8, 9999)])),
            'entry 1 builds design 9999, which the civ does not own'),
        "a ship entry naming another civ's design": (
            ours_with(sprq(QUEUE, b'q', [(1, 1, 0, 0, 8, 661)])),
            'entry 1 builds design 661, which the civ does not own'),
        'an item type nobody has measured': (
            ours_with(sprq(QUEUE, b'q', [(1, 1, 0, 0, 3, None)])),
            'entry 1 has item type 3; only facilities and ships'),
        'an item type the engine does not read': (
            ours_with(sprq(QUEUE, b'q', [(1, 1, 0, 0, 12, None)])),
            'entry 1 has item type 12, which the engine does not read'),
        'a facility type the galaxy lacks': (
            ours_with(sprq(QUEUE, b'q', [(1, 1, 0, 0, 0, 99)])),
            f'entry 1 names facility type 99, and the galaxy has {types}'),
        'two entries under one id': (
            ours_with(sprq(QUEUE, b'q', [FARM, FARM])),
            'its entry ids are not distinct and non-zero'),
        'an entry list shorter than its count': (
            ours_with(cut), 'its PRQU does not hold the 1 entries it counts'),
        'a PRQU of the old version': (
            ours_with(old), 'its PRQU is version 0, not 1'),
        'a name outside printable ASCII': (
            ours_with(sprq(QUEUE, b'q\x01', [FARM])),
            'name holds bytes outside printable ASCII'),
        'two queues under one id': (
            ours_with(sprq(QUEUE, b'a', [FARM]), sprq(QUEUE, b'b', [FARM])),
            'two of them share an object id'),
        'an SPQS whose count is wrong': (
            bytes(miscounted), 'SPQS does not hold its count of queues'),
        "changing another civ's queues": (
            mo.set_queues(noq, THEM, [sprq(QUEUE, b'bq', [FARM])]),
            'production queues DROPPED, they belong to BadGuy'),
    }
    for label, (sub, fragment) in cases.items():
        new, edited, deleted, said = judged(sub)
        check(f'{label}: refused', any(fragment in n for n in said), True)
        if not any(fragment in n for n in said):
            print(f'          notes: {said}')
        check('  and nothing is carried', (new, edited, deleted), ([], [], []))

    print('a refused queue takes the planets that join it with it')
    bad = mo.set_queues(assign, ME, [sprq(QUEUE, b'new\x01', [FARM], using=1)])
    out, notes = merged(noq, ('GoodGuy', bad))
    said = notes.get('GoodGuy', [])
    check('the queue is refused',
          any('production queue 674 DROPPED, name holds bytes' in n
              for n in said))
    check('and so is the HQ joining it',
          any(n.startswith(f'planet {HQ}: production DROPPED, it names '
                           f'production queue 674') for n in said))
    check('the merged turn holds no queue and the HQ builds what it was',
          (mo.queues_of(out, ME), mo.planet_index(out)[HQ][1]),
          ([], mo.planet_index(noq)[HQ][1]))

    print('links the merged galaxy could not resolve are refused')
    link_cases = {
        'joining a queue the civ does not hold': (
            base, linked(base, HQ, 999, 1),
            f'planet {HQ}: production DROPPED, it names production queue 999'),
        'standing on an entry the queue does not hold': (
            base, linked(base, HQ, QUEUE, 7),
            f'planet {HQ}: production DROPPED, it is on entry 7 of production '
            f'queue 674'),
        'deleting a queue a planet still uses': (
            load('q11s2'), mo.set_queues(load('q11s2'), ME, []),
            f'production queue 674 deletion DROPPED, planet(s) [{HQ}] still '
            f'use it'),
    }
    for label, (served, sub, fragment) in link_cases.items():
        out, notes = merged(served, ('GoodGuy', sub))
        said = notes.get('GoodGuy', [])
        check(f'{label}: refused', any(fragment in n for n in said), True)
        if not any(fragment in n for n in said):
            print(f'          notes: {said}')
        check('  and the queues and links are as served',
              state(out), state(served))
        check('  every link resolves and every count is right',
              consistent(out), '')

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
