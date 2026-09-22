"""
test_inject_donor.py , which civ a join clones, and that a run cannot poison it
==============================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_inject_donor.py

`inject_civ.add_civ` hands a new civ a copy of its donor's homeworld, so the
donor decides what the newcomer starts with. The default used to be the smallest
`OWNR`, and a fresh clone owns one planet and nothing else, so after the first
injection the smallest `OWNR` is the civ that was just added: a run naming
several civs chained each clone off the previous newcomer, and which civ that
was depended on the order the names were given in.

The default is now `inject_civ.seat_one`, the lowest object id. A newcomer takes
`max_object_id + 1` and is therefore always the highest id, so the rule cannot
select a civ the run itself added.

`client/SinglePlayerGalaxy.dat` is the fixture for the same reason
`test_wipe_civ.py` uses it. Its two civs hold equal homeworlds, so the rate pair
that a generated galaxy splits between seat one and the rest is written into
seat one's homeworld here before the donor checks run: the galaxies on disk that
carry a genuine split are all gitignored captures, and a test cannot depend on
them. The values written are the ones measured on generated galaxies and named
by `make_multiplayer_galaxy.RATE_OFFSETS`.

On this fixture the old rule and the new one agree on the FIRST injection,
because its seat one happens to hold the smaller `OWNR` as well as the lower
object id. What the old rule loses here is the second injection onwards, where
the donor becomes whichever newcomer was named first, so that is what the
old-rule checks below look at rather than the transplanted bytes.

What is NOT tested here is that an injected galaxy loads and plays, which is
K1's second half and needs the game client.
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

import save_parser as sp
import inject_civ as icv
import inject_design as idg
import make_multiplayer_galaxy as mmg

GALAXY = os.path.join(ROOT, 'client', 'SinglePlayerGalaxy.dat')
NAMES = ['Ceti', 'Draconis', 'Eros']
BOOSTED = (62, 94)          # a customised homeworld, as generated galaxies read
DEFAULT = (32, 44)          # the engine's default for every other seat
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def quiet(_msg=''):
    pass


def homeworld(blob, civ_name):
    """A civ's homeworld record, picked the way `add_civ` picks a donor's."""
    o = next(r for r in icv.owner_records(blob) if r['name'] == civ_name)
    mine = [p for p in icv.planet_records(blob) if p['owner'] == o['oid']]
    return o, max(mine, key=lambda p: p['plpr_ln'])


def world(blob, civ_name):
    """A civ's homeworld PLPR, with its own object id normalised out.

    Two civs cannot hold byte-identical `PLPR`s while they are owned by
    different civs, so every dword reading the owner's id is zeroed before the
    comparison. Only that id is touched: zeroing anything else, the planet id
    included, would also hit payload values that happen to equal it and would
    manufacture differences that are not there.
    """
    o, h = homeworld(blob, civ_name)
    buf = bytearray(blob[h['plpr_payload']:h['plpr_end']])
    icv.replace_u32(buf, o['oid'], 0, 0, len(buf))
    return bytes(buf)


def rates(blob, civ_name):
    _o, h = homeworld(blob, civ_name)
    return tuple(blob[h['plpr_payload'] + i] for i in mmg.RATE_OFFSETS)


def boost(blob, civ_name, pair):
    """Write a rate pair into a civ's homeworld, as the setup screens do."""
    _o, h = homeworld(blob, civ_name)
    out = bytearray(blob)
    for i, v in zip(mmg.RATE_OFFSETS, pair):
        out[h['plpr_payload'] + i] = v
    return bytes(out)


def run(blob, names, donor_name=None):
    """Inject each name in turn, recording the donor every call chose."""
    donors = []

    def log(msg=''):
        text = str(msg).strip()
        if text.startswith('=== ') and ': cloning ' in text:
            donors.append(text.split(": cloning '", 1)[1].split("'", 1)[0])

    taken = []
    for n in names:
        blob, used = icv.add_civ(blob, n, donor_name=donor_name, taken=taken,
                                 log=log)
        taken.append(used)
    return blob, donors


def homes(blob, names):
    return [homeworld(blob, n)[1]['id'] for n in names]


def main():
    if not os.path.exists(GALAXY):
        print(f'no fixture at {GALAXY}')
        return 1
    base = sp.load_any(GALAXY)
    owners = icv.owner_records(base)
    if len(owners) < 2:
        print(f'{GALAXY} holds {len(owners)} civ(s); this test needs two')
        return 1

    one = icv.seat_one(owners)
    two = next(o for o in owners if o['name'] != one['name'])
    base = boost(base, one['name'], BOOSTED)
    print(f'{os.path.basename(GALAXY)}: seat one {one["name"]!r} id {one["oid"]}'
          f', seat two {two["name"]!r} id {two["oid"]}')

    print('\nthe default donor')
    check('seat_one takes the lowest object id',
          one['oid'], min(o['oid'] for o in owners))
    check('the fixture splits the rate pair between the seats',
          (rates(base, one['name']), rates(base, two['name'])),
          (BOOSTED, DEFAULT))

    print('\nthe rule cannot select a civ the run added')
    once, _d = run(base, ['Ceti'])
    after = icv.owner_records(once)
    check('the newcomer is the smallest OWNR, which is what poisoned the '
          'old default', min(after, key=lambda o: o['ln'])['name'], 'Ceti')
    check('the newcomer holds the highest object id',
          max(after, key=lambda o: o['oid'])['name'], 'Ceti')
    check('seat_one still names seat one',
          icv.seat_one(after)['name'], one['name'])

    print(f'\na {len(NAMES)}-name run, forwards and backwards')
    fwd, fwd_donors = run(base, list(NAMES))
    rev, rev_donors = run(base, list(reversed(NAMES)))
    check('every call in the forward run clones seat one',
          fwd_donors, [one['name']] * len(NAMES))
    check('every call in the reversed run clones seat one',
          rev_donors, [one['name']] * len(NAMES))
    check('each name gets the same world whichever order it was named in',
          all(world(fwd, n) == world(rev, n) for n in NAMES))
    check('the same homeworlds are used either way',
          sorted(homes(fwd, NAMES)), sorted(homes(rev, NAMES)))
    check('no two newcomers share a homeworld',
          len(set(homes(fwd, NAMES))), len(NAMES))
    check('every newcomer carries seat one\'s rates',
          {rates(fwd, n) for n in NAMES}, {BOOSTED})

    print('\nagainst injecting one at a time into the untouched galaxy')
    for n in NAMES:
        solo, _d = run(base, [n])
        check(f'{n} gets the same world in the run as on its own',
              world(solo, n) == world(fwd, n))

    print('\nthe donor decides the world, so naming one still changes it')
    named, named_donors = run(base, list(NAMES), donor_name=two['name'])
    check('a named donor is used for every call in the run',
          named_donors, [two['name']] * len(NAMES))
    check('newcomers cloned off seat two carry seat two\'s rates',
          {rates(named, n) for n in NAMES}, {DEFAULT})

    print('\nwhat the old default did, with the rule swapped back')
    keep = icv.seat_one
    try:
        icv.seat_one = lambda os_: min(os_, key=lambda o: o['ln'])
        old_fwd, old_f = run(base, list(NAMES))
        _old_rev, old_r = run(base, list(reversed(NAMES)))
    finally:
        icv.seat_one = keep
    check('the old rule clones the first newcomer from the second call on',
          old_f, [one['name'], NAMES[0], NAMES[0]])
    check('and which civ that is depends on the order the names came in',
          old_f != old_r)
    check('the fixed rule does not depend on the order', fwd_donors, rev_donors)

    print('\nwhat every join rewrites')
    counted = struct.unpack_from('<I', fwd, icv.civ_count_at(fwd))[0]
    before = struct.unpack_from('<I', base, icv.civ_count_at(base))[0]
    check('GLXY civ count rises once per join', counted, before + len(NAMES))
    check('one OWNR per join', len(icv.owner_records(fwd)),
          len(owners) + len(NAMES))
    oids = [o['oid'] for o in icv.owner_records(fwd)]
    check('object ids stay unique', len(set(oids)), len(oids))
    uids = [struct.unpack_from('<I', fwd, o['end'] - 4)[0]
            for o in icv.owner_records(fwd)]
    check('Owner:4 stays unique', len(set(uids)), len(uids))
    pids = [p['id'] for p in icv.planet_records(fwd)]
    check('planet ids stay unique', len(set(pids)), len(pids))
    check('no civ took an id a planet holds', set(oids) & set(pids), set())
    check('the high-water id covers every id in the blob',
          struct.unpack_from('<I', fwd, idg.HIGH_WATER_ID)[0],
          idg.max_object_id(fwd))
    check('the id allocation does not depend on the order',
          sorted(oids), sorted(o['oid'] for o in icv.owner_records(rev)))

    print('\nthe result is still a blob')
    for label, out in (('forward', fwd), ('reversed', rev)):
        try:
            sp.parse_blob(out)
            parsed = True
        except Exception as exc:
            parsed = f'{type(exc).__name__}: {exc}'
        check(f'the {label} run reparses section by section', parsed)


    # ── make_multiplayer_galaxy.build levels to seat one ─────────────────────
    # Same root cause as the donor rule: blob order is not seat order. `build`
    # maps player names onto existing civs positionally and `equalise_homeworlds`
    # then levels everyone to players[0], so taking them in blob order makes the
    # reference whichever civ happens to serialise first. On a galaxy grown by
    # injection that is the newcomer, and the whole galaxy levels DOWN to the
    # engine default instead of up to the customised seat.
    print('\nmake_multiplayer_galaxy.build levels to seat one')
    demo = os.path.join(ROOT, 'server', 'galaxy_demo', 'turns', '0007.b64')
    if not os.path.exists(demo):
        print('  [SKIP] no galaxy_demo fixture in this checkout')
    else:
        grown = sp.load_any(demo)
        blob_order = [o['name'] for o in icv.owner_records(grown)]
        seat_order = [o['name'] for o in mmg.seated(grown)]
        # The test is only meaningful on a galaxy where the two disagree.
        check('blob order and seat order disagree on this fixture',
              blob_order != seat_order, True)
        check('seat one is the lowest object id',
              seat_order[0],
              min(icv.owner_records(grown), key=lambda o: o['oid'])['name'])

        names = ['Alpha', 'Beta', 'Gamma'][:len(seat_order)]
        out = mmg.build(grown, names, log=quiet)
        got = {n: rates(out, n) for n in names}
        check('every player is levelled to the boosted world',
              set(got.values()), {BOOSTED})

        # And the check can fail: with blob order restored, the same call
        # levels everyone to the engine default instead.
        real = mmg.seated
        try:
            mmg.seated = lambda b: icv.owner_records(b)
            bad = mmg.build(grown, names, log=quiet)
            check('blob order would have levelled everyone down',
                  {rates(bad, n) for n in names}, {DEFAULT})
        finally:
            mmg.seated = real

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    for f in FAIL:
        print(f'  FAILED: {f}')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
