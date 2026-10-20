"""
cash.py: the cash policy (STRATEGY.md §4.3, Cash)

How much cash is kept back, and what the rest is for. The rules that spend
(R-XPL-08 hurry, R-XPL-09 conscription) and the one that earns in a pinch
(R-XPL-03 wealth mode) all take their numbers from here.

A civ whose cash goes negative has its ships and facilities sold by the game
until it is positive again, so the reserve is sized in turns of upkeep.

    python cash.py               # report cash, upkeep and the reserve
"""
import sys

import cli
import facilities
import gamestate as gs

RESERVE_TURNS = 10        # turns of upkeep never spent on anything discretionary
MIN_RESERVE = 1000        # the reserve is never smaller than this

# Upkeep of one stationed military unit, the Planet:168 record's +0, which read
# 3 on every unit the memory report observed. Crew aboard a ship are left to
# the ship's own ShipDesign:48.
UNIT_UPKEEP = 3

# The treasury is short when income is negative and this many turns of it
# would take cash inside the reserve. R-XPL-03 and R-XPL-01 both act on it.
SHORT_HORIZON = 5

SHIPYARD = 2

# Hurry tiers, lowest first.
MILITARY, EXPANSION, OTHER = 0, 1, 2
TIER_NAME = {MILITARY: "military", EXPANSION: "expansion", OTHER: "other"}


def upkeep(snap, civ):
    """Estimated cash this civ pays per turn: facilities, garrisons and ships.

    Facility upkeep per type is the live definition table's `+0x18`; ship
    upkeep is ShipDesign:48; a stationed unit is UNIT_UPKEEP. None is checked
    against the engine's own total, so this is an estimate.
    """
    table = facilities.read_table(snap)
    total = 0
    for p in snap.owned_planets(civ):
        for tid, n in p.facilities.items():
            rec = table.get(tid)
            if rec:
                total += rec["upkeep"] * n
        total += UNIT_UPKEEP * p.military
    for s in snap.owned_ships(civ):
        d = s.design
        if d is not None and d.upkeep:
            total += d.upkeep
    return total


def reserve(snap, civ):
    return max(MIN_RESERVE, RESERVE_TURNS * upkeep(snap, civ))


def cash_now(civ, act):
    """Cash after what this pass has already spent; civ.cash is snapshot-time."""
    return (civ.cash or 0) + act.cash_effect


def spendable(snap, civ, act):
    """Cash above the reserve, after this pass's spending so far."""
    return cash_now(civ, act) - reserve(snap, civ)


def short(snap, civ, act, rate):
    """Is income negative and the reserve within SHORT_HORIZON turns of it?"""
    return (rate is not None and rate < 0
            and cash_now(civ, act) + rate * SHORT_HORIZON < reserve(snap, civ))


def at_war(snap, civ, act):
    import exterminate
    return bool(exterminate.enemies(snap, civ, act))


def hurry_tier(snap, planet, war):
    """MILITARY, EXPANSION or OTHER for what this planet is building.

    Military builds outrank the rest only while at war; otherwise a warship is
    no more urgent than a farm.
    """
    import exterminate
    design = exterminate.production_design(snap, planet)
    if design is not None:
        if design.role in ("WARSHIP", "TROOP"):
            return MILITARY if war else OTHER
        if design.role == "COLONY":
            return EXPANSION
        return OTHER
    fac = planet.selected_building
    if fac is None:
        return OTHER
    if war and facilities.is_defensive(snap, fac):
        return MILITARY
    if fac == SHIPYARD:
        return EXPANSION
    return OTHER


def main():
    args = sys.argv[1:]
    snap = gs.Snapshot()
    civ = gs.resolve_civ(snap, cli.opt(args, "--civ"))
    if civ is None:
        return
    up = upkeep(snap, civ)
    print(f"{civ.civ_name!r}, turn {snap.turn}: cash {civ.cash}, upkeep about "
          f"{up}/turn, reserve {reserve(snap, civ)} "
          f"({RESERVE_TURNS} turns, floor {MIN_RESERVE})")


if __name__ == "__main__":
    main()
