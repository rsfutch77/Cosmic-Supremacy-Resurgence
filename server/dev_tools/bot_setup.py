"""
bot_setup.py: a bot civ confirms its one-time setup prompts
===========================================================
The client offers the civ-trait prompt (dialog 218) and the homeworld prompt
(dialog 210) on every turn until each is confirmed once. A bot never clicks
them, so `bot_seats` writes the confirm into the captured save instead, as the
dialog's OK would have: trait values, homeworld rates and space, and the flag.
The referee carries it only as the dialog could have made it
(`merge_orders.setup_orders`).

The bots keep to the easy economy: production first, then food.
"""
import struct

import merge_orders as mo

# Traits raised, by index in `merge_orders.TRAIT_RULES`, in tiers: a tier
# takes changes only once every trait before it is full.
TRAIT_TIERS = ((3, 2),              # production output, food output
               (4,))                # science output, for a larger budget

# Homeworld rates raised, in priority order, by index in HOMEWORLD_RATE_OFFS.
HOMEWORLD_PRIORITY = (1, 0)         # production, food


def pick_traits(budget):
    """Trait values that spend `budget` changes exactly, or None.

    Each change goes to the trait in the first tier with room whose next click
    adds the most, production on a tie, so the spend alternates down the
    tables.
    """
    if budget is None or budget < 0:
        return None
    clicks = [0] * len(mo.TRAIT_RULES)
    for _ in range(budget):
        best = None
        for tier in TRAIT_TIERS:
            for i in tier:
                _name, fewest, table, cost = mo.TRAIT_RULES[i]
                at = clicks[i] - fewest
                if cost != 1 or at + 1 >= len(table):
                    continue
                gain = table[at + 1] - table[at]
                if best is None or gain > best[0]:
                    best = (gain, i)
            if best is not None:
                break
        if best is None:
            return None
        clicks[best[1]] += 1
    return tuple(table[c - fewest] for c, (_n, fewest, table, _c)
                 in zip(clicks, mo.TRAIT_RULES))


def pick_homeworld(plpr, budget):
    """(rates, space) for a homeworld that spends `budget` changes, or None.

    The changes alternate between production and food, production first,
    within the dialog's 25..80. Space takes no clicks but still rises to the
    floor the dialog sets on any confirm.
    """
    if budget is None or budget < 0:
        return None
    rates, space = mo.homeworld_fields(plpr)
    rates = list(rates)
    lo, hi = mo.HOMEWORLD_RATES
    for n in range(budget):
        turn = HOMEWORLD_PRIORITY[n % len(HOMEWORLD_PRIORITY):] \
            + HOMEWORLD_PRIORITY[:n % len(HOMEWORLD_PRIORITY)]
        room = [i for i in turn if lo <= rates[i] + 1 <= hi]
        if not room:
            return None
        rates[room[0]] += 1
    return tuple(rates), max(space, mo.HOMEWORLD_SPACE_FLOOR)


def homeworld_plpr(plpr, rates, space):
    """A PLPR payload with the homeworld dialog's fields replaced."""
    out = bytearray(plpr)
    for off, v in zip(mo.HOMEWORLD_RATE_OFFS, rates):
        struct.pack_into('<b', out, off, v)
    struct.pack_into('<H', out, mo.HOMEWORLD_SPACE_OFF, space)
    return bytes(out)


def confirm(served, mine, civ, log=print):
    """`mine` with every setup prompt the served turn still offers confirmed.

    `civ` is the civ's name. A prompt already confirmed as served, or by the
    client in `mine`, is left alone, and so is one the bot cannot spend
    exactly.
    """
    oid = mo.civ_by_name(served, civ)['oid']
    flags_s, flags_m = mo.setup_flags(served, oid), mo.setup_flags(mine, oid)
    if flags_s is None or flags_m is None:
        log(f"[{civ}] setup prompts: flags unreadable, left alone")
        return mine
    (home_s, civ_s), (home_m, civ_m) = flags_s, flags_m

    if not civ_s and not civ_m:
        budget = mo.gset_value(served, 'civilization_changes')
        values = pick_traits(budget)
        if values is None or mo.traits_of(mine, oid) is None:
            log(f"[{civ}] civilization traits: cannot spend {budget}, "
                f"left unconfirmed")
        else:
            mine = mo.set_traits(mine, oid, values)
            mine = mo.set_setup_flag(mine, oid, 'civ')
            log(f"[{civ}] civilization traits confirmed: "
                + ", ".join(f"{name} {v:+d}" for (name, *_), v
                            in zip(mo.TRAIT_RULES, values) if v))

    if not home_s and not home_m:
        budget = mo.gset_value(served, 'homeworld_changes')
        planets = mo.planet_index(mine)
        homes = sorted(mo.homeworlds(mo.planet_index(served), oid))
        home = homes[0] if homes else None
        picked = (pick_homeworld(planets[home][2], budget)
                  if home in planets else None)
        if picked is None:
            log(f"[{civ}] homeworld: cannot spend {budget}, left unconfirmed")
        else:
            rates, space = picked
            mine = mo.set_homeworld(mine, home, homeworld_plpr(
                planets[home][2], rates, space))
            mine = mo.set_setup_flag(mine, oid, 'homeworld')
            log(f"[{civ}] homeworld #{home} confirmed: food, production, "
                f"science rates {rates}, space {space}")
    return mine
