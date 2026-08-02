"""
survey.py — Print what the AI player can see this turn
======================================================
Read-only. This is the AI's own view of the game, rendered for a human, and it
is the first thing to run when a rule misbehaves: if the sensor table here is
wrong, every rule built on it is wrong too.

    python survey.py                 # everything
    python survey.py --civ GoodGuy   # only that civ's assets in detail
"""
import sys

import cli
import gamestate as gs


def fmt_pos(p):
    return "(" + ", ".join("--" if v is None else f"{v:8.2f}" for v in p) + ")"


def num(v, spec="", none="uncomputed"):
    """An uncomputed ShipDesign field is None, not 0 — print it as such rather
    than letting a format spec raise or a 0 masquerade as a real value."""
    return none if v is None else format(v, spec)


def main():
    args = sys.argv[1:]
    want_civ = cli.opt(args, "--civ")

    snap = gs.Snapshot()
    print(f"\n=== TURN {snap.turn} ===")
    print(f"{len(snap.civs)} civs, {len(snap.planets)} planets, "
          f"{len(snap.suns)} suns, {len(snap.ships)} ships, "
          f"{len(snap.designs)} designs")

    print("\n--- systems (nearest-sun heuristic) ---")
    for k, v in snap.system_report().items():
        print(f"  {k:32} {v}")

    print("\n--- civs ---")
    for c in snap.civs:
        st = ", ".join(f"{gs.RESOURCES.get(k, k)}={v}" for k, v in sorted(c.stocks.items()))
        print(f"  {c} name={c.civ_name!r} key=0x{(c.key or 0):08X}")
        print(f"      cash={c.cash} research={c.research} score={c.score} "
              f"topic={c.topic}")
        print(f"      stocks: {st or 'none'}")
        print(f"      unlocked facilities: "
              f"{[gs.FACILITIES.get(i, i) for i in c.unlocked_facilities] or 'none'}")
        print(f"      completed research: {c.completed or 'none'}")

    print("\n--- ship designs ---")
    for d in snap.designs:
        print(f"  {d} name={d.design_name!r} role={d.role}"
              f"{'' if d.computed else '   [DESIGN NOT COMPUTED]'}")
        print(f"      speed={num(d.speed, '.2f')} cost={num(d.cost)} "
              f"upkeep={num(d.upkeep)} hp={num(d.hp)} shield={num(d.shield)} "
              f"space={num(d.space)} metal={num(d.cost_metal)} "
              f"radio={num(d.cost_radio)}")
        print(f"      firepower={d.firepower} "
              f"chassis={[gs.CHASSIS.get(i, i) for i in d.chassis]} "
              f"engines={d.engines} weapons={d.weapons} "
              f"modules={[gs.MODULES.get(i, i) for i in d.modules]}")

    print("\n--- ships ---")
    for s in snap.ships:
        o = s.owner
        d = s.design
        orb = s.orbiting
        print(f"  {s} owner={o.civ_name if o else None!r} "
              f"design={d.design_name if d else None!r} role={s.role}")
        print(f"      pos={fmt_pos(s.pos)} orbiting="
              f"{orb.planet_name if orb else None!r} "
              f"condition={s.condition:.4f}")
        print(f"      order={gs.ORDERS.get(s.order_type, s.order_type)} "
              f"order_obj=0x{(s.order_ptr or 0):08X} "
              f"has_orders={s.i32(76)}")

    print("\n--- colonised planets ---")
    for p in snap.planets:
        if not p.colonised:
            continue
        if want_civ and (p.owner is None or p.owner.civ_name != want_civ):
            continue
        jobs = ", ".join(f"{gs.JOBS.get(k, k)}={v}" for k, v in sorted(p.jobs.items()))
        facs = ", ".join(f"{gs.FACILITIES.get(k, k)}x{v}"
                         for k, v in sorted(p.facilities.items()))
        sun, r = snap.nearest_sun(p)
        print(f"  {p} name={p.planet_name!r} owner={p.owner.civ_name!r}")
        print(f"      pos={fmt_pos(p.pos)} sun={sun.id if sun else None} "
              f"r={r:.2f} space={p.space} maxpop={p.max_pop}")
        print(f"      pop={len(p.population)} [{jobs}] "
              f"food={p.food}/{p.food_cap} military={p.military}")
        print(f"      facilities: {facs or 'none'}   "
              f"production={p.production} building={p.building_now} "
              f"progress={p.build_progress} selected="
              f"{gs.FACILITIES.get(p.selected_building, p.selected_building)}")

    unowned = snap.unowned_planets()
    print(f"\n--- {len(unowned)} unowned planets (top 10 by space) ---")
    for p in sorted(unowned, key=lambda x: -x.space)[:10]:
        sun, r = snap.nearest_sun(p)
        print(f"  {p} pos={fmt_pos(p.pos)} space={p.space} maxpop={p.max_pop} "
              f"sun={sun.id if sun else None} r={r:.2f}")


if __name__ == "__main__":
    main()
