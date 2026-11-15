"""
Load a save blob into a sim.state.Galaxy.

Reads through the decoders in server/dev_tools, so a section the referee can
read the simulator can too. Anything that does not read is named in
Galaxy.gaps rather than guessed.

    python -m sim.load path/to/galaxy.dat
"""
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in (os.path.join(ROOT, 'server', 'dev_tools'), os.path.join(ROOT, 'server')):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import save_parser as sp                                        # noqa: E402
import merge_orders as mo                                       # noqa: E402
import inject_civ as icv                                        # noqa: E402
import inject_design as idg                                     # noqa: E402
import set_blob_player as sbp                                   # noqa: E402
import facility_rules as fr                                     # noqa: E402
import activity                                                 # noqa: E402
import exsy                                                     # noqa: E402

from . import state as st                                       # noqa: E402
from . import economy as ec                                     # noqa: E402

HIGH_WATER_AT = 8                   # SAVE payload +0
SHPR_DESIGN_AT = 0
SHPR_CONDITION_FROM_END = 12        # f32, then last fleet and own id
PLNT_NAME_AT = 24
DYNO_ORBIT_AT = 0
DYNO_SHCO_AT = 4                    # SHCO section, payload byte 0 the order type
SHCO_SIZE = 8 + 9
DYNO_ADMIRAL_FROM_SHCO = 5          # u32, has-orders u8, then the admiral
ROUT_FIXED_HEAD = 2 + 24            # kind, flag, origin and target
HISTORY_SLOTS = 8                   # settled block +0: past populations, newest first
PLPR_MILITARY_COST = 28             # u32 food per soldier, then u32 the military store
SOLDIER_XP_AT = 1                   # f16 experience in a nine-byte soldier record
GOLDEN_AGE_IN_CVTR = 12            # u32 turns left, in CVTR's header before the trait pairs
NO_GOLDEN_AGE = 9999                # the idle value the live object held
RESEARCH_AFTER_CREDITS = 4          # u32 research store, right after credits
RADIUS_AT = 20                      # f32 in both SUN and PLNT payloads
ORE_BEFORE_ENLI = 5                 # u8 resource type then u32 rate, just before ENLI
SCORE_AFTER_RESEARCH = 11           # u32 Owner:28 in OWPR, past the second research field
REPUTATION_AFTER_RESEARCH = 27      # f32 Owner:32, four words later


def _people(records):
    """[(job, owner)] from nine-byte citizen records, as bytes or tuples."""
    out = []
    for r in records:
        if isinstance(r, (bytes, bytearray)):
            r = mo._record(r)
        out.append((r[0], r[1]))
    return out


def _order(dyno, gaps, what):
    """(orbit, Order or None, admiral) from a whole DYNO section."""
    p = 8
    orbit = struct.unpack_from('<I', dyno, p + DYNO_ORBIT_AT)[0]
    shco = p + DYNO_SHCO_AT
    if dyno[shco:shco + 4] != b'SHCO':
        gaps.append(f'{what}: DYNO has no SHCO where measured')
        return orbit, None, 0
    kind = dyno[shco + 8]
    at = shco + SHCO_SIZE + 4
    admiral = struct.unpack_from('<I', dyno, at + 1)[0]
    rout = at + DYNO_ADMIRAL_FROM_SHCO
    # A scout's has-orders byte reads 0 and its ROUT is there all the same.
    if dyno[rout:rout + 4] != b'ROUT':
        return orbit, (st.Order(kind) if kind else None), admiral
    version = struct.unpack_from('<I', dyno, rout + 4)[0] >> sp.VERSION_SHIFT
    q = rout + 8 + (2 if version >= 1 else 1)
    target = struct.unpack_from('<3f', dyno, q + 12)
    legs, q = sp._read_legs(dyno, q + 24)
    more, q = sp._read_legs(dyno, q)
    progress = struct.unpack_from('<f', dyno, q)[0]
    ref = struct.unpack_from('<I', dyno, q + 16)[0]
    # The second list is the path already flown; progress runs along the first.
    def tri(gs):
        return [((g['origin_x'], g['origin_y'], g['origin_z']),
                 (g['dest_x'], g['dest_y'], g['dest_z']), g['length']) for g in gs]
    return orbit, st.Order(kind, target, progress, tri(legs), ref, tri(more)), admiral


def _ship(oid, owner, pos, shpr, dyno, fleet, gaps):
    design = struct.unpack_from('<I', shpr, SHPR_DESIGN_AT)[0]
    crew = mo.crew_of(shpr)
    if crew is None:
        gaps.append(f'ship {oid}: crew does not read')
    condition = struct.unpack_from('<f', shpr,
                                   len(shpr) - SHPR_CONDITION_FROM_END)[0]
    orbit, order, admiral = (_order(dyno, gaps, f'ship {oid}') if dyno
                             else (0, None, 0))
    ship = st.Ship(oid, owner, pos, design, _people(crew or []), condition,
                   orbit, order, admiral, fleet)
    ship.crew_xp = [struct.unpack_from('<e', r, SOLDIER_XP_AT)[0] for r in crew or []]
    return ship


def _donor_named(blob, o):
    """A SHIP whose SHPR names another ship, read anyway. A cloned ship's tail
    can still name its donor; the header's id is the ship's."""
    if o['tag'] != b'SHIP' or o['why'] != 'its SHPR does not end by naming it':
        return False
    sec = o['sec']
    kids = mo._children(blob, sec.payload + mo.OBJECT_HEADER, sec.end)
    if not kids or [k[0] for k in kids] != [b'DYNO', b'SHPR']:
        return False
    if mo.crew_of(kids[1][3]) is None:
        return False
    o.update(dyno=kids[0][2], shpr=kids[1][3], why='')
    return True


def _space_objects(blob, galaxy):
    gaps = galaxy.gaps
    for o in mo.space_objects(blob)[2]:
        if o['why'] and not _donor_named(blob, o):
            gaps.append(f"{o['tag'].decode()} {o['oid']}: {o['why']}")
            continue
        pos = struct.unpack('<3f', o['pos'])
        if o['tag'] == b'SHIP':
            galaxy.ships[o['oid']] = _ship(o['oid'], o['owner'], pos, o['shpr'],
                                           o['dyno'], 0, gaps)
            continue
        orbit, order, admiral = _order(o['dyno'], gaps, f"fleet {o['oid']}")
        ids = []
        for sid, shpr, _v in o['members']:
            ship = _ship(sid, o['owner'], pos, shpr, None, o['oid'], gaps)
            ship.orbit, ship.order = orbit, order
            galaxy.ships[sid] = ship
            ids.append(sid)
        galaxy.fleets[o['oid']] = st.Fleet(
            o['oid'], o['owner'], pos, o['name'].decode('latin-1'), ids, orbit,
            order, admiral)


def _production(prod):
    kind = mo.prod_kind(prod)
    if kind is None:
        return None
    kind = kind.decode('latin-1')
    # PROD's length varies with its list of queue hops; a ship build's design
    # is always the payload of the SHIP section that ends it.
    item = (mo.prod_facility(prod) if kind == 'FCLT'
            else struct.unpack_from('<I', prod, len(prod) - 4)[0] if kind == 'SHIP'
            else None)
    link = mo.queue_link(prod)
    linked = link is not None and link[0]
    return st.Production(kind, item, 0, mo.is_hurried(prod),
                         link[1] if linked else 0, link[2] if linked else 0)


def _queue_items(blob, galaxy):
    """Fill in the item a queued build names only through its queue entry."""
    entries = {}
    for queues in (mo.queues_by_civ(blob) or {}).values():
        for oid, record in queues or ():
            got, _why = mo.read_queue(record)
            entries[oid] = {eid: item for eid, _t, item, _a in got or ()}
    for p in galaxy.planets.values():
        prod = p.production
        if prod and prod.item is None and prod.queue and prod.kind in ('SHIP', 'FCLT'):
            prod.item = entries.get(prod.queue, {}).get(prod.entry)


def _planets(blob, tree, galaxy):
    gaps = galaxy.gaps
    bio = mo.bio_penalties(blob)
    glxy = next(tree[0].find('GLXY'))
    for sola in (c for c in glxy.children if c.tag == b'SOLA'):
        sun = next((c for c in sola.children if c.tag == b'SUN '), None)
        sun_id = struct.unpack_from('<I', blob, sun.payload)[0] if sun else 0
        if sun:
            galaxy.suns[sun_id] = struct.unpack_from('<3f', blob, sun.payload + 4)
            galaxy.radius[sun_id] = struct.unpack_from('<f', blob,
                                                       sun.payload + RADIUS_AT)[0]
        for sec in sola.find('PLNT'):
            oid, x, z, y, owner = struct.unpack_from('<IfffI', blob, sec.payload)
            nlen = struct.unpack_from('<I', blob, sec.payload + PLNT_NAME_AT)[0]
            name = bytes(blob[sec.payload + PLNT_NAME_AT + 4:
                              sec.payload + PLNT_NAME_AT + 4 + nlen])
            planet = st.Planet(oid, owner, sun_id, (x, z, y),
                               name.decode('latin-1'),
                               governor=struct.unpack_from('<I', blob,
                                                           sec.end - 4)[0])
            galaxy.planets[oid] = planet
            galaxy.system_planets.setdefault(sun_id, []).append(oid)
            galaxy.radius[oid] = struct.unpack_from('<f', blob,
                                                    sec.payload + RADIUS_AT)[0]
            plpr_sec = next(sec.find('PLPR'), None)
            if plpr_sec is None:
                continue
            plpr = bytes(blob[plpr_sec.payload:plpr_sec.end])
            planet.rates = tuple(plpr[o] for o in mo.HOMEWORLD_RATE_OFFS)
            cz = mo.citizens_of(plpr)
            _at, mil = mo.military_of(plpr)
            fac = mo.facilities_of(plpr)
            if cz is None or mil is None or fac is None:
                gaps.append(f'planet {oid}: people or facilities do not read')
            planet.citizens = [(j, w) for j, w, _e, _r in cz or []]
            planet.military = _people(mil or [])
            planet.military_xp = [struct.unpack_from('<e', r, SOLDIER_XP_AT)[0]
                                  for r in mil or []]
            planet.facilities = dict(fac or [])
            planet.food = struct.unpack_from('<II', plpr, mo.FOOD_STORE_OFF)
            planet.recruit = mo.recruit_of(plpr)
            planet.base_space = struct.unpack_from('<H', plpr,
                                                   fr.PLPR_BASE_SPACE_OFF)[0]
            planet.lost_space = struct.unpack_from('<h', plpr,
                                                   fr.PLPR_LOST_SPACE_OFF)[0]
            planet.founded = struct.unpack_from('<I', plpr, mo.FOUNDED_OFF)[0]
            enli = next(plpr_sec.find('ENLI'), None)
            if enli is not None:
                planet.ore, planet.ore_rate = struct.unpack_from(
                    '<BI', blob, enli.start - ORE_BEFORE_ENLI)
            prod_sec = next(plpr_sec.find('PROD'), None)
            if prod_sec is not None:
                planet.production = _production(
                    bytes(blob[prod_sec.start:prod_sec.end]))
                if planet.production is None:
                    gaps.append(f'planet {oid}: PROD does not read')
                else:
                    planet.production.progress = mo.progress_of(plpr) or 0
            block = mo._block_at(blob, plpr_sec)
            if block is not None:
                at = block[0]
                planet.pop_history = list(struct.unpack_from(
                    f'<{HISTORY_SLOTS}H', blob, at + mo.SETTLED_POP_OFF))
                planet.history_len = struct.unpack_from(
                    '<I', blob, at + mo.SETTLED_COUNTER_OFF)[0]
                planet.rulers, planet.unrest = mo.influence_of(blob, plpr_sec)
                planet.loyalty = ec.planet_loyalty(
                    [(mo.arrived(c), c[1]) for c in cz or []], owner, planet.rulers,
                    {o: n for (r, o), n in bio.items() if r == owner})
                taken_from = [r for r in planet.rulers if r[3] != mo.RULING]
                if taken_from:
                    planet.prior_owner = max(taken_from, key=lambda r: r[3])[0]
            planet.military_cost, planet.military_food = struct.unpack_from(
                '<II', plpr, PLPR_MILITARY_COST)


def _civs(blob, galaxy):
    gaps = galaxy.gaps
    stances = mo.stances(blob)
    active = activity.state(blob)
    for rec in icv.owner_records(blob):
        oid = rec['oid']
        civ = st.Civ(oid, rec['name'])
        civ.credits = mo.credits_of(blob, oid)
        at = rec['id_at'] + mo.CREDITS_AFTER_ID + RESEARCH_AFTER_CREDITS
        civ.research = struct.unpack_from('<I', blob, at)[0]
        civ.uid = struct.unpack_from('<I', blob, rec['end'] - 4)[0]
        civ.resources = mo.resources_of(blob, oid)
        civ.completed = mo.completed_research(blob, oid)
        research = mo.research_of(blob, oid)
        civ.topic = struct.unpack('<i', research[0])[0] if research else None
        civ.goal = struct.unpack('<i', research[1])[0] if research else None
        civ.traits = mo.traits_of(blob, oid)
        owpr = mo._owpr(blob, oid)
        offs = mo.research_offsets(blob, owpr) if owpr is not None else None
        if offs and offs[1] + REPUTATION_AFTER_RESEARCH + 4 <= owpr.size:
            at = owpr.payload + offs[1]
            civ.score = struct.unpack_from('<I', blob, at + SCORE_AFTER_RESEARCH)[0]
            civ.reputation = struct.unpack_from('<f', blob, at + REPUTATION_AFTER_RESEARCH)[0]
        cvtr = mo._cvtr(blob, oid)
        if cvtr is not None and cvtr.size >= GOLDEN_AGE_IN_CVTR + 4:
            left = struct.unpack_from('<I', blob, cvtr.payload + GOLDEN_AGE_IN_CVTR)[0]
            civ.golden_age = 0 if left == NO_GOLDEN_AGE else left
        if rec['name'] in active:
            civ.active_turn, flag = active[rec['name']]
            civ.active = bool(flag)
        civ.stances = {other: code for (me, other), code in stances.items()
                       if me == oid}
        for what in ('credits', 'resources', 'completed', 'topic', 'traits'):
            if getattr(civ, what) is None:
                gaps.append(f'civ {oid} {rec["name"]}: {what} does not read')
        galaxy.civs[oid] = civ


def _explored(blob, tree, galaxy):
    by_offset = {rec['off']: rec['oid'] for rec in icv.owner_records(blob)}
    for ownr in tree[0].find('OWNR'):
        civ = galaxy.civs.get(by_offset.get(ownr.start))
        if civ is None:
            continue
        for sec in ownr.find('EXSY'):
            try:
                rows = exsy.parse(bytes(blob[sec.payload:sec.end]))
            except ValueError:
                galaxy.gaps.append(f'civ {civ.oid}: EXSY does not read')
                continue
            civ.explored = {r['system'] for r in rows}
            for r in rows:
                # A row's planets are the system's, in the order SOLA holds them.
                ids = galaxy.system_planets.get(r['system'], [])
                civ.seen[r['system']] = (r['turn'], {
                    oid: (p['owner'], p['population'])
                    for oid, p in zip(ids, r['planets'])})


def _designs(blob, galaxy):
    for oid, d in mo.design_index(blob).items():
        if d['parts'] is None:
            galaxy.gaps.append(f'design {oid}: does not read')
            continue
        parts = {('shields' if k == 'list6' else k): v
                 for k, v in d['parts'].items()}
        galaxy.designs[oid] = st.Design(oid, d['civ'], d['name'], parts,
                                        d['record'][-1] == 1)


def _treaties(blob, galaxy):
    records = mo.treaties(blob)
    if records is None:
        galaxy.gaps.append('treaty list does not read')
        return
    for oid, rec in records.items():
        t = mo.read_trdt(rec)
        if t is None:
            galaxy.gaps.append(f'treaty {oid}: does not read')
            continue
        galaxy.treaties[oid] = st.Treaty(oid, t['proposer'], t['recipient'],
                                         t['kind'], t['status'], t['length'],
                                         t['start'])


class _OneParse:
    """Within the block, parsing this blob returns one shared tree. The
    merge_orders readers each parse the whole blob, and a load calls them per
    civ. The readers only read, so sharing the tree is safe."""

    def __init__(self, blob):
        self.blob, self.tree, self.real = blob, None, sp.parse_blob

    def parse(self, blob):
        if blob is not self.blob:
            return self.real(blob)
        if self.tree is None:
            self.tree = self.real(blob)
        return self.tree

    def __enter__(self):
        sp.parse_blob = self.parse
        return self

    def __exit__(self, *_exc):
        sp.parse_blob = self.real


def load(blob):
    """A Galaxy from a decoded save blob."""
    blob = bytes(blob)
    with _OneParse(blob):
        return _load(blob)


def _load(blob):
    tree = sp.parse_blob(blob)
    galaxy = st.Galaxy(
        turn=icv.turn_of(blob),
        content_version=sbp.get_content_version(blob),
        market=mo.market_of(blob),
        high_water=struct.unpack_from('<I', blob, HIGH_WATER_AT)[0],
        suns={}, radius={}, civs={}, planets={}, ships={}, fleets={},
        designs={}, treaties={})
    if galaxy.market is None:
        galaxy.gaps.append('market does not read')
    try:
        galaxy.player = sbp.get_player(blob)
    except SystemExit:
        galaxy.gaps.append('the local player does not read')
    _civs(blob, galaxy)
    _designs(blob, galaxy)
    _planets(blob, tree, galaxy)
    _space_objects(blob, galaxy)
    _treaties(blob, galaxy)
    _explored(blob, tree, galaxy)
    _queue_items(blob, galaxy)
    for ship in galaxy.ships.values():
        if ship.design not in galaxy.designs:
            galaxy.gaps.append(f'ship {ship.oid}: design {ship.design} not held')
    return galaxy


def load_path(path):
    return load(sp.load_any(path))


def describe(g):
    lines = [f'turn {g.turn}, content version {g.content_version}, '
             f'{len(g.suns)} systems, {len(g.planets)} planets, '
             f'{len(g.ships)} ships in {len(g.fleets)} fleets, '
             f'{len(g.designs)} designs, {len(g.treaties)} treaties']
    lines.append(f'market {g.market}')
    for civ in g.civs.values():
        planets = g.civ_planets(civ.oid)
        pop = sum(len(p.citizens) for p in planets)
        lines.append(f'  {civ.name} ({civ.oid}): {len(planets)} planets, '
                     f'{pop} citizens, {len(g.civ_ships(civ.oid))} ships, '
                     f'{civ.credits} credits, resources {civ.resources}, '
                     f'{len(civ.completed or [])} techs, topic {civ.topic}')
    for gap in g.gaps:
        lines.append(f'  gap: {gap}')
    return '\n'.join(lines)


if __name__ == '__main__':
    for path in sys.argv[1:]:
        print(path)
        print(describe(load_path(path)))
