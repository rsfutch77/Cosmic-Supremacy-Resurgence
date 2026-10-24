"""
test_governors_merge.py: governors survive the tick
===================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_governors_merge.py

A governor lives in the civ's GOVS and a planet names it in the last u32 of
its PLNT. Without the merge carrying both, a governor made in the client was
gone after the tick and its planet unassigned.

The fixtures are one Single Player session at turn 0 (23 October 2026), saved
after each step: `g_base` none; `g_made` g1 created (id 218), unassigned;
`g_assigned` g1 governs the homeworld; `g_switched` g1 edited in place into
g2, its rule replaced; `g_reassigned` a new g3 (219) governs the homeworld
instead; `g_deleted` g2 deleted from the dropdown.
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

FIX = os.path.join(HERE, 'fixtures', 'governors')
PASS, FAIL = [], []

ME, THEM = 199, 203                          # DemoPlayer, BadGuy
HOME = 141


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


def gvnr(oid, name, rules=((b'RLSP', 0, b''),), governs=0):
    """A whole GVNR section as the client writes one. Each rule is (tag,
    version, payload) and is preceded by its checked RULE."""
    body = struct.pack('<I', len(name)) + name + b'\x00\x00'
    body += struct.pack('<I', len(rules))
    for tag, version, payload in rules:
        body += sp.build_section(b'RULE', 1, b'\x01')
        body += sp.build_section(tag, version, payload)
    return sp.build_section(b'GVNR', 0, struct.pack('<II', oid, governs)
                            + b'\x00' + sp.build_section(b'GVDT', 1, body))


def created(served, civ, records, links=()):
    """A submission from `served` in which `civ` holds these governors and
    these planets name them, with SAVE+0 raised past every id."""
    blob = mo.set_governors(served, civ, list(records))
    for oid, _r in ((struct.unpack_from('<I', r, 8)[0], r) for r in records):
        blob = mo.set_high_water(blob, oid)
    for planet, gov in links:
        blob = mo.set_governor_link(blob, planet, gov)
    return blob


def state(blob, civ=ME):
    """What the merge must reproduce: the civ's governors and every link."""
    return mo.governors_of(blob, civ), mo.governor_links(blob)


def main():
    base, made, assigned = load('g_base'), load('g_made'), load('g_assigned')
    switched, reassigned = load('g_switched'), load('g_reassigned')
    deleted = load('g_deleted')
    their_home = next(iter(mo.homeworlds(mo.planet_index(base), THEM)))

    print('what the fixtures hold')
    check('no civ starts with a governor',
          (mo.governors_of(base, ME), mo.governors_of(base, THEM)), ([], []))
    check('and no planet names one',
          set(mo.governor_links(base).values()), {0})
    check('g1 is id 218 and governs nothing until assigned',
          [(oid, mo.read_governor(r)[0],
            struct.unpack_from('<I', r, 8 + mo.GVNR_GOVERNS_AT)[0])
           for oid, r in mo.governors_of(made, ME)], [(218, b'g1', 0)])
    check('the layout written here is the one the client writes',
          gvnr(218, b'g1'), mo.governors_of(made, ME)[0][1])
    check('assigning names it on the homeworld and counts it',
          (mo.governor_links(assigned)[HOME],
           struct.unpack_from('<I', mo.governors_of(assigned, ME)[0][1],
                              8 + mo.GVNR_GOVERNS_AT)[0]), (218, 1))
    check('every measured governor reads', [
        mo.read_governor(r) for b in (made, switched, reassigned, deleted)
        for _oid, r in mo.governors_of(b, ME)],
        [(b'g1', ''), (b'g2', ''), (b'g2', ''), (b'g3', ''), (b'g3', '')])

    print('each step the client took is carried as it took it')
    steps = (('creating', base, made), ('assigning', base, assigned),
             ('creating and assigning from nothing', made, assigned),
             ('editing in place', assigned, switched),
             ('a new governor taking the planet over', switched, reassigned),
             ('deleting one that governs nothing', reassigned, deleted))
    for label, served, sub in steps:
        out, notes = merged(served, ('DemoPlayer', sub))
        check(f'{label}: nothing refused', notes.get('DemoPlayer', []), [])
        check(f'  and the merged turn holds what the client wrote',
              state(out), state(sub))
        check('  the blob still parses', bool(sp.parse_blob(out)), True)
    out, _ = merged(switched, ('DemoPlayer', reassigned))
    check('SAVE+0 covers the new governor',
          struct.unpack_from('<I', out, 8)[0], 219)
    check('the other civ is untouched',
          state(out, THEM)[0], mo.governors_of(switched, THEM))

    print('two civs making a governor on the same turn claim the same id')
    theirs = created(base, THEM, [gvnr(218, b'bg', governs=1)],
                     [(their_home, 218)])
    for order in ((('DemoPlayer', assigned), ('BadGuy', theirs)),
                  (('BadGuy', theirs), ('DemoPlayer', assigned))):
        out, notes = merged(base, *order)
        first, second = (ME, THEM) if order[0][0] == 'DemoPlayer' else (THEM, ME)
        home_of = {ME: HOME, THEM: their_home}
        check(f'{order[0][0]} first: nothing refused',
              sum(notes.values(), []), [])
        check('  the first keeps 218 and the second is renumbered to 219',
              ([oid for oid, _r in mo.governors_of(out, first)],
               [oid for oid, _r in mo.governors_of(out, second)]), ([218], [219]))
        check('  each homeworld names its own governor',
              (mo.governor_links(out)[home_of[first]],
               mo.governor_links(out)[home_of[second]]), (218, 219))
        check('  each governs one planet',
              [struct.unpack_from('<I', mo.governors_of(out, c)[0][1],
                                  8 + mo.GVNR_GOVERNS_AT)[0] for c in (ME, THEM)],
              [1, 1])
        check('  and SAVE+0 covers both', struct.unpack_from('<I', out, 8)[0], 219)

    print('a governor and another civ\'s new design claim the same id')
    like = next(d for d in mo.design_index(base).values() if d['civ'] == ME)
    designed = mo.set_high_water(mo.add_design(
        base, ME, idg.build_dsgn(218, 'Probe', like['parts'], ME)), 218)
    for order in ((('BadGuy', theirs), ('DemoPlayer', designed)),
                  (('DemoPlayer', designed), ('BadGuy', theirs))):
        out, notes = merged(base, *order)
        design_id = next(oid for oid, d in mo.design_index(out).items()
                         if d['name'] == 'Probe')
        gov_id = mo.governors_of(out, THEM)[0][0]
        check(f'{order[0][0]} first: nothing refused',
              sum(notes.values(), []), [])
        check('  the second is renumbered, so the ids differ',
              (design_id, gov_id) if order[0][0] == 'DemoPlayer'
              else (gov_id, design_id), (218, 219))
        check("  and BadGuy's homeworld names its governor",
              mo.governor_links(out)[their_home], gov_id)

    print('what the client cannot do is refused, saying why')
    overrun = bytearray(gvnr(218, b'g1'))
    struct.pack_into('<I', overrun, len(overrun) - 4, 4)   # RLSP claims 4 bytes
    tree, govs = mo._govs(base, ME)
    miscounted = sp.replace_payload(base, tree, govs, struct.pack('<I', 2))
    cases = {
        "naming a governor on another civ's planet": (
            assigned, mo.set_governor_link(assigned, their_home, 218),
            f'planet {their_home}: governor DROPPED, owned by BadGuy'),
        'unassigning, which the client offers no way to do': (
            assigned, mo.set_governor_link(assigned, HOME, 0),
            'unassigning one is not something the client offers'),
        'deleting a governor that still governs': (
            assigned, mo.set_governors(assigned, ME, []),
            'governor 218 deletion DROPPED, it still governs planet(s) [141]'),
        'naming a governor the civ does not hold': (
            base, mo.set_governor_link(base, HOME, 218),
            'planet 141: governor DROPPED, 218 is not a governor this civ holds'),
        'a governor with no rules': (
            base, created(base, ME, [gvnr(218, b'g1', rules=())]),
            'it has no rules, which the client does not allow'),
        'a governor with no name': (
            base, created(base, ME, [gvnr(218, b'')]), 'the name is empty'),
        'a name outside printable ASCII': (
            base, created(base, ME, [gvnr(218, b'g\x01')]),
            'name holds bytes outside printable ASCII'),
        'a rule section that runs past its governor': (
            base, created(base, ME, [bytes(overrun)]),
            'its GVDT does not hold the 1 rule(s) it counts'),
        'a new governor under an id already in use': (
            base, mo.set_governors(base, ME, [gvnr(150, b'g1')]),
            'governor 150 DROPPED, it is new and 150 is not a new object id'),
        "changing another civ's governors": (
            base, created(base, THEM, [gvnr(218, b'bg')]),
            'governors DROPPED, they belong to BadGuy'),
        'a GOVS whose count is wrong': (
            base, miscounted, 'GOVS does not hold its count of governors'),
    }
    for label, (served, bad, fragment) in cases.items():
        out, notes = merged(served, ('DemoPlayer', bad))
        said = notes.get('DemoPlayer', [])
        check(f'{label}: refused', any(fragment in n for n in said), True)
        if not any(fragment in n for n in said):
            print(f'          notes: {said}')
        check('  and the governors and links are as served',
              (state(out), state(out, THEM)), (state(served), state(served, THEM)))

    print('a refused link does not cost the governor it names')
    sub = mo.set_governor_link(made, their_home, 218)
    out, notes = merged(base, ('DemoPlayer', sub))
    check('the governor is carried', [oid for oid, _r in mo.governors_of(out, ME)],
          [218])
    check('the link to the other civ is refused',
          (mo.governor_links(out)[their_home], len(notes.get('DemoPlayer', []))),
          (0, 1))

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
