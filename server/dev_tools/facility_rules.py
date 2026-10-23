"""
facility_rules.py: whether a civ may start building a facility on a planet

The client's CanBuildFacility (`0x004F5030`), read off a save: the facility
must be unlocked by the civ's completed technologies, fit the planet's free
space, not clash with the hyperspace pair, and not exceed its per-planet
limit. It answers for a new build, not for facilities already standing:
generation places some on homeworlds without the research.

Space per facility and the one-per-planet set come from the client's facility
tables, picked by the galaxy's content version (`0x00529CB0`). A planet's free
space is its base space plus the civ's space bonus, less damage, less what its
facilities use, in 16-bit arithmetic (`0x004F1140`, `0x004F3100`).
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)),
                                'client', 'dev_tools', 'ai_player'))

import save_parser as sp
import set_blob_player as sbp
import research

# Space each facility type takes, indexed by type id.
SPACE_RELEASED = (7, 7, 25, 11, 9, 15, 14, 34, 26, 3, 6, 14, 9, 6, 12, 5, 12,
                  8, 9, 16, 10)                       # content 150 to 645, 0x807DB0
SPACE_CURRENT = (7, 7, 25, 11, 9, 15, 14, 34, 26, 2, 4, 14, 9, 6, 12, 5, 12,
                 8, 9, 16, 10)                        # content 646 and up, 0x8066B0
SPACE_LEGACY = (8, 8, 30, 12, 10, 16, 15, 40, 30, 5, 10, 25)   # below 150, 0x808930
ONCE = {2, 7, 8, 19}                                  # one per planet
ONCE_LEGACY = {2, 6, 7, 8}
CURRENT_FROM = 646
RELEASED_FROM = 150

TRANSMITTER, RECEIVER = 18, 19                        # never on one planet together
CAP = 99                                              # most of one type on a planet
SPACE_TRAIT = (11, 38)                                # grant (kind, index): space percent

PLPR_BASE_SPACE_OFF = 11                              # u16
PLPR_LOST_SPACE_OFF = 13                              # i16, battle damage


def tables(version):
    """(space per type, one-per-planet types) for a content version."""
    if version is None or version >= CURRENT_FROM:
        return SPACE_CURRENT, ONCE
    if version >= RELEASED_FROM:
        return SPACE_RELEASED, ONCE
    return SPACE_LEGACY, ONCE_LEGACY


def unlocked(completed):
    """(facility types, space bonus percent) the completed technologies give.
    The farm comes from technology 1, which every civ starts with."""
    grants = [g for t in completed for g in research.grants(t)]
    types = {oid for kind, oid, _v in grants if kind == research.FACILITY}
    pct = sum(v for kind, oid, v in grants if (kind, oid) == SPACE_TRAIT)
    return types, pct


def _round_away(x):
    return int(x + 0.5) if x >= 0 else int(x - 0.5)


def free_space(base, lost, pct, used):
    total = base + _round_away(base * pct / 100.0) if pct else base
    total = ((total - lost + 0x8000) & 0xFFFF) - 0x8000
    return total - used


def can_build(blob, completed, plpr, facilities, facility_type):
    """(ok, reason) for starting `facility_type` on a planet.

    `completed` is the owner's completed technology ids, `plpr` the planet's
    PLPR payload and `facilities` its [(type, count)] list. Ownership is the
    caller's to check: the client's rule does not look at it.
    """
    try:
        version = sbp.get_content_version(blob)
    except (SystemExit, Exception):           # noqa: BLE001
        version = None
    space, once = tables(version)
    if not 0 <= facility_type < len(space):
        return False, f'there is no facility type {facility_type}'
    types, pct = unlocked(completed)
    if facility_type not in types:
        return False, 'it is not yet researched'
    base = struct.unpack_from('<H', plpr, PLPR_BASE_SPACE_OFF)[0]
    lost = struct.unpack_from('<h', plpr, PLPR_LOST_SPACE_OFF)[0]
    used = sum(space[t] * c for t, c in facilities if t < len(space))
    free = free_space(base, lost, pct, used)
    if free < space[facility_type]:
        return False, (f'it needs {space[facility_type]} space and the planet '
                       f'has {free} free')
    have = dict(facilities)
    if facility_type == RECEIVER and TRANSMITTER in have:
        return False, 'the planet has a hyperspace transmitter'
    if facility_type == TRANSMITTER and RECEIVER in have:
        return False, 'the planet has a hyperspace receiver'
    if facility_type not in have:
        return True, ''
    if facility_type in once:
        return False, 'only one may stand on a planet and it is built'
    if have[facility_type] >= CAP:
        return False, f'{CAP} already stand on the planet'
    return True, ''
