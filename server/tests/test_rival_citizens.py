"""
test_rival_citizens.py: people on another civ's planet are refused with a note
==============================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_rival_citizens.py

A colony keeps the owner of the colonists that founded it, so a civ can hold
citizens on another civ's planet. The merge carries people only on the
submitter's own planets and ships; a change anywhere else is refused, and the
note names the planet, its owner and what the submitter did to its own
citizens there.

The fixture is `client/SinglePlayerGalaxy.dat`, with two of BadGuy's citizens
handed to DemoPlayer here. `server/galaxy_demo` turn 11, when present, has the
real case: planet 138 is Neighbor's and holds two of DemoPlayer's farmers.
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

import merge_orders as mo
import save_parser as sp
import inject_civ as icv

GALAXY = os.path.join(ROOT, 'client', 'SinglePlayerGalaxy.dat')
DEMO = os.path.join(ROOT, 'server', 'galaxy_demo', 'turns', '0011.b64')
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def records(blob, planet):
    arr = mo.citizen_bytes(mo.planet_index(blob)[planet][2])
    return [bytearray(arr[i:i + mo.POP_RECORD])
            for i in range(0, len(arr), mo.POP_RECORD)]


def write(blob, planet, recs):
    """Write the records back sorted by job, as the engine keeps them."""
    recs = sorted(recs, key=lambda r: r[0])
    return mo.set_citizens(blob, planet, b''.join(bytes(r) for r in recs))


def rejob(blob, planet, owner, job, count=1):
    """Give `count` citizens of `owner` on `planet` a new job."""
    recs = records(blob, planet)
    for r in recs:
        if count and struct.unpack_from('<I', r, 3)[0] == owner and r[0] != job:
            r[0] = job
            count -= 1
    return write(blob, planet, recs)


def merged(served, subs):
    notes = {}
    out = mo.merge(served, subs, log=lambda _s: None, notes=notes)
    return out, notes


def main():
    base = open(GALAXY, 'rb').read()
    civs = {o['name']: o['oid'] for o in icv.owner_records(base)}
    demo, bad = civs['DemoPlayer'], civs['BadGuy']
    index = mo.planet_index(base)
    theirs = next(p for p, v in sorted(index.items()) if v[0] == bad)
    ours = next(p for p, v in sorted(index.items()) if v[0] == demo)

    # Two of BadGuy's farmers become DemoPlayer's, as a colony founded by
    # DemoPlayer's colonists under BadGuy would hold them.
    recs = records(base, theirs)
    for r in recs[:2]:
        struct.pack_into('<I', r, 3, demo)
    served = write(base, theirs, recs)

    print('the served state')
    check('the fixture holds DemoPlayer citizens on a BadGuy planet',
          sorted(c[1] for c in mo.citizens_of(
              mo.planet_index(served)[theirs][2])).count(demo), 2)
    out, notes = merged(served, [('DemoPlayer', served)])
    check('a submission that changes nothing leaves no note', notes, {})

    print("a civ's own citizens on another civ's planet")
    sub = rejob(served, theirs, demo, 1)
    check('people_acceptable does not see that planet',
          mo.people_acceptable(served, sub, demo), (True, ''))
    out, notes = merged(served, [('DemoPlayer', sub)])
    check('the reassignment is not carried',
          mo.planet_index(out)[theirs][2], mo.planet_index(served)[theirs][2])
    check('and the note names the planet, its owner and the jobs',
          notes.get('DemoPlayer'),
          [f"planet {theirs}: people DROPPED, owned by BadGuy; DemoPlayer's "
           f"citizens there went 2 farmer -> 1 farmer, 1 worker, and people "
           f"on another civ's planet are not carried"])

    both = rejob(rejob(served, ours, demo, 2), theirs, demo, 5)
    out, notes = merged(served, [('DemoPlayer', both)])
    check('a reassignment on its own planet in the same turn is still taken',
          mo.planet_index(out)[ours][2], mo.planet_index(both)[ours][2])
    check('while the one on the rival planet is refused',
          (mo.planet_index(out)[theirs][2] == mo.planet_index(served)[theirs][2],
           len(notes.get('DemoPlayer', []))), (True, 1))

    print("the rival's own people")
    sub = rejob(served, theirs, bad, 2)
    out, notes = merged(served, [('DemoPlayer', sub)])
    check("changing the owner's citizens is refused without the own-citizens "
          "clause", notes.get('DemoPlayer'),
          [f'planet {theirs}: people DROPPED, owned by BadGuy'])
    ship = next(s for s, (o, _sh) in sorted(mo._ship_crew_index(served).items())
                if o == bad)
    crew = mo.crew_of(mo._ship_crew_index(served)[ship][1])
    sub = mo.set_crew(served, ship, crew[:-1])
    out, notes = merged(served, [('DemoPlayer', sub)])
    check("changing another civ's ship's crew is refused",
          notes.get('DemoPlayer'), [f'ship {ship}: crew DROPPED, owned by BadGuy'])

    print('the planet owner')
    sub = rejob(served, theirs, bad, 2)
    out, notes = merged(served, [('BadGuy', sub)])
    check('reassigning its own citizens there is taken without a note',
          (mo.planet_index(out)[theirs][2] == mo.planet_index(sub)[theirs][2],
           notes), (True, {}))
    # The owner's client moves another civ's citizens on its planet (rivalB).
    sub = rejob(served, theirs, demo, 2, count=2)
    out, notes = merged(served, [('BadGuy', sub)])
    check("reassigning DemoPlayer's citizens there is taken, as it was",
          (mo.planet_index(out)[theirs][2] == mo.planet_index(sub)[theirs][2],
           notes), (True, {}))

    if os.path.exists(DEMO):
        print('galaxy_demo turn 11, planet 138')
        real = sp.load_any(DEMO)
        dp = mo.civ_by_name(real, 'DemoPlayer')['oid']
        sub = rejob(real, 138, dp, 1)
        out, notes = merged(real, [('DemoPlayer', sub)])
        check("DemoPlayer's farmer on Neighbor's planet is refused with a note",
              (out == real, notes.get('DemoPlayer')),
              (True, ["planet 138: people DROPPED, owned by Neighbor; "
                      "DemoPlayer's citizens there went 2 farmer -> 1 farmer, "
                      "1 worker, and people on another civ's planet are not "
                      "carried"]))
    else:
        print('galaxy_demo turn 11 is absent; the real case is skipped')

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
