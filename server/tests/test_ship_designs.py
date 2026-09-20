"""
test_ship_designs.py , a design a player makes has to survive the merge
=======================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_ship_designs.py

The fixture is real. `submissions/0006/Powerhouse.b64` in the 19 September 2026
rehearsal galaxy holds a design a person made by clicking, object 206 named
`test scout`, against `turns/0006.b64` which does not have it. That player made
the same design on turns 5, 6 and 7 and watched it vanish three times, because
`merge_orders` carried production queues and not designs.

The two faults interact, which is why the queue is tested here too. A queue that
builds a design the served state does not have is a dangling reference, and the
engine refuses the entire galaxy rather than the order: the client starts, reads
it and exits in about four seconds. The guard that refuses such a queue stays,
and these tests hold it to refusing only what is really dangling.

The hard case is two players designing a ship on the same turn. The client
allocates a new object id as one past the object count, so both pick the same
number and merging both would put two objects into one galaxy under one id.
Whoever is merged second is renumbered, and every reference to the id they
submitted is rewritten with them.

A copy of the rehearsal galaxy is looked for beside this file first, so the test
runs without the share; `--galaxy` points it at another store.
"""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools')):
    if d not in sys.path:
        sys.path.insert(0, d)

import save_parser as sp
import merge_orders as mo

SHARE = r'\\POWERHOUSE1\Sharing\cosmic\galaxy2'
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def said(log, fragment):
    """Did the merge name this refusal?"""
    return any(fragment in line for line in log)


def remake(record, owner=None, name=None):
    """A DSGN record with a different owner id or name, sizes corrected.

    Built by editing a record a client really wrote rather than by
    synthesising one, so a fixture keeps the client's byte shape whether or not
    the builders are right about it. The shape of a synthesised record is
    `test_build_dsgn.py`'s subject, not this file's.
    """
    dsgn_ln = struct.unpack_from('<I', record, 4)[0] & sp.SIZE_MASK
    sdpr_ln = struct.unpack_from('<I', record, 16)[0] & sp.SIZE_MASK
    at = 20                                     # the SDPR child's payload
    body = bytearray(record[at:at + sdpr_ln])
    if name is not None:
        old_n = struct.unpack_from('<I', body, 0)[0]
        nb = name if isinstance(name, bytes) else name.encode('latin1')
        body[0:4 + old_n] = struct.pack('<I', len(nb)) + nb
    if owner is not None:
        struct.pack_into('<I', body, len(body) - 4, owner)
    delta = len(body) - sdpr_ln
    out = bytearray(record)
    out[at:at + sdpr_ln] = bytes(body)
    struct.pack_into('<I', out, 16, (0 << 26) | (sdpr_ln + delta))
    struct.pack_into('<I', out, 4, (4 << 26) | (dsgn_ln + delta))
    return bytes(out)


def queue_ship(blob, planet_oid, prod):
    """Put a ship-building production queue on a planet."""
    return mo.replace_prod(blob, planet_oid, prod)


def main(galaxy):
    turn6 = os.path.join(galaxy, 'turns', '0006.b64')
    subs = os.path.join(galaxy, 'submissions', '0006')
    if not os.path.exists(turn6):
        print(f'no rehearsal galaxy at {galaxy}; pass --galaxy <store>')
        return 0

    served = sp.load_any(turn6)
    ph = sp.load_any(os.path.join(subs, 'Powerhouse.b64'))
    lt = sp.load_any(os.path.join(subs, 'Laptop.b64'))

    before = mo.design_index(served)
    submitted = mo.design_index(ph)
    new_id = 206
    check('the fixture is the one this test is about: 206 is new in the '
          'submission and not in the turn',
          new_id in submitted and new_id not in before)
    record = submitted[new_id]['record']
    ph_oid = mo.civ_by_name(served, 'Powerhouse')['oid']
    lt_oid = mo.civ_by_name(served, 'Laptop')['oid']

    # ── 1. the design survives, and so does the queue that builds it ─────────
    log, notes = [], {}
    merged = mo.merge(served, [('Powerhouse', ph), ('Laptop', lt)],
                      log=log.append, notes=notes)
    after = mo.design_index(merged)
    check('a design in a submission survives the merge', new_id in after)
    check('it belongs to the civ that submitted it',
          after.get(new_id, {}).get('civ'), ph_oid)
    check('its name comes through', after.get(new_id, {}).get('name'),
          'test scout')
    check("the other civ's designs are untouched",
          {o: d['record'] for o, d in after.items() if d['civ'] == lt_oid},
          {o: d['record'] for o, d in before.items() if d['civ'] == lt_oid})
    check('the queue that builds it is carried rather than refused',
          mo.prod_builds(mo.planet_index(merged)[139][1]), new_id)
    check('nothing was dropped', notes, {})

    # The blob has to be a blob, not just agree field by field.
    try:
        sp.parse_blob(merged)
        parses = True
    except Exception as exc:                                # noqa: BLE001
        parses = f'{type(exc).__name__}: {exc}'
    check('the merged blob reparses', parses)
    check("SAVE's object counter covers the new id",
          struct.unpack_from('<I', merged, 8)[0] >= new_id)
    check('the merge grew the blob by exactly the record it added',
          len(merged) - len(served), len(record))

    # ── 2. two players allocating the same id ────────────────────────────────
    # Laptop's client, handed the same turn, allocates the same 206.
    rival = mo.add_design(served, lt_oid,
                          remake(record, owner=lt_oid, name='lt scout'))
    rival = mo.set_high_water(rival, new_id)
    rival = queue_ship(rival, 196, mo.planet_index(ph)[139][1])
    check('the rival submission really does claim the same id',
          new_id in mo.design_index(rival)
          and mo.design_index(rival)[new_id]['civ'], lt_oid)

    log, notes = [], {}
    both = mo.merge(served, [('Powerhouse', ph), ('Laptop', rival)],
                    log=log.append, notes=notes)
    idx = mo.design_index(both)
    mine_ph = {o for o, d in idx.items() if d['civ'] == ph_oid}
    mine_lt = {o for o, d in idx.items() if d['civ'] == lt_oid}
    check('both new designs are in the merged galaxy', len(idx), 4)
    check('under two different object ids',
          len(mine_ph & mine_lt), 0)
    check('the first submitted keeps the id it asked for', new_id in mine_ph)
    check('the second is renumbered', len(mine_lt), 2)
    check('and the merge says so', said(log, 'renumbered'))
    check('no two designs share an id',
          len(idx), len({o for o in idx}))

    names = {o: d['name'] for o, d in idx.items()}
    check('both designs are still there by name',
          {'test scout', 'lt scout'} <= set(names.values()))

    # The reference that made this dangerous: every queue must resolve, and to
    # a design its own owner holds.
    dangling = []
    for poid, (owner, prod, _plpr, _nm) in mo.planet_index(both).items():
        builds = mo.prod_builds(prod) if prod else None
        if builds is None:
            continue
        if builds not in idx or idx[builds]['civ'] != owner:
            dangling.append((poid, builds))
    check('no production queue is left pointing at a missing design',
          dangling, [])
    check("the renumbered player's queue was repointed with it",
          mo.prod_builds(mo.planet_index(both)[196][1]) in mine_lt)
    try:
        sp.parse_blob(both)
        parses = True
    except Exception as exc:                                # noqa: BLE001
        parses = f'{type(exc).__name__}: {exc}'
    check('the two-design galaxy reparses', parses)
    check('nothing was dropped from either player', notes, {})

    # ── 3. what is refused, each named ───────────────────────────────────────
    def refuse(what, sub, civ='Powerhouse', fragment=''):
        log, notes = [], {}
        out = mo.merge(served, [(civ, sub)], log=log.append, notes=notes)
        check(what, said(log, fragment) and mo.design_index(out) == before)
        check(f'    ... and the player is told: {fragment!r}',
              any(fragment in line for line in notes.get(civ, [])))

    refuse("a design planted under another civ's OWNR is refused",
           mo.add_design(served, lt_oid, record),
           fragment="under another civ's OWNR")

    refuse('a design whose payload claims another civ is refused',
           mo.add_design(served, ph_oid, remake(record, owner=lt_oid)),
           fragment='names civ')

    refuse('a design name holding control bytes is refused',
           mo.add_design(served, ph_oid,
                         remake(record, name=b'scout\x01\x02')),
           fragment='outside printable ASCII')

    refuse("a design name over the client's buffer is refused",
           mo.add_design(served, ph_oid, remake(record, name='x' * 200)),
           fragment='buffer the client offers')

    refuse('a design reusing a name the civ already owns is refused',
           mo.add_design(served, ph_oid, remake(record, name='Colony Ship')),
           fragment='unique within a civ')

    # Editing and deleting are separate orders nobody has measured.
    edited = mo.add_design(served, ph_oid, record)          # a legal new design
    edited_idx = mo.design_index(edited)
    log, notes = [], {}
    out = mo.merge(edited, [('Powerhouse', served)], log=log.append,
                   notes=notes)
    check('a design missing from a submission is refused, not deleted',
          said(log, 'deleting a design is not carried yet')
          and mo.design_index(out) == edited_idx)
    check('    ... and the player is told',
          any('deleting a design' in line
              for line in notes.get('Powerhouse', [])))

    # Renamed to something the same length, so the edit is an edit and not a
    # blob with every enclosing size left wrong. 'scout test' is what the
    # player called their third attempt.
    tweaked = bytearray(ph)
    where = ph.find(record)
    tweaked[where:where + len(record)] = remake(record, name='scout test')
    log, notes = [], {}
    out = mo.merge(ph, [('Powerhouse', bytes(tweaked))], log=log.append,
                   notes=notes)
    check('an existing design that came back changed is refused',
          said(log, 'editing a design is not carried yet'))

    # ── 4. the dangling-reference guard still fires ──────────────────────────
    # A queue naming a design that exists but belongs to the other player.
    poaching = queue_ship(served, 139,
                          mo.with_prod_design(mo.planet_index(ph)[139][1],
                                              sorted(mine_lt)[0]))
    log, notes = [], {}
    out = mo.merge(served, [('Powerhouse', poaching)], log=log.append,
                   notes=notes)
    check("a queue building a design the submitter does not own is refused",
          said(log, 'does not own in the merged galaxy'))
    check('    ... and the player is told',
          any('does not own' in line for line in notes.get('Powerhouse', [])))

    absent = queue_ship(served, 139,
                        mo.with_prod_design(mo.planet_index(ph)[139][1], 9999))
    log, notes = [], {}
    mo.merge(served, [('Powerhouse', absent)], log=log.append, notes=notes)
    check('a queue building a design nothing holds is refused',
          said(log, 'design 9999'))

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--galaxy', default=None,
                    help='a turn store holding turn 6 and its submissions')
    a = ap.parse_args()
    local = os.path.join(HERE, 'galaxy2')
    sys.exit(main(a.galaxy or (local if os.path.exists(local) else SHARE)))
