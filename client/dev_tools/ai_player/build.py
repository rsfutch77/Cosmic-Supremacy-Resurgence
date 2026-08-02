"""
build.py — Ship designs and ship production (actuators D, D′ and I)
===================================================================
The production half of the AI: what a civ can build, and telling a planet to
build it. Rules R-EXP-01, R-XPN-01 and R-XTM-01 all end up here.

    python build.py                                    # what we have
    python build.py --new-design scout2 --apply       # UNTESTED, has no parts
    python build.py --queue scout1 --planet 116 --apply
    python build.py --wealth --planet 116 --apply

Dry run is the default, as everywhere else.

Queueing is two plain pointer writes: a ship build has no production object at
all — `Planet:296` points straight at the shared `ShipDesign` in its
allocation-start form, and is CONFIRMED working.

Creating a design is NOT solved. `--new-design` default-constructs one through
the engine, but the result has no chassis, engine or weapons and is very likely
unbuildable. There is no clone path: the constructor that looked like a copy
constructor deserialises from a save stream and crashed the client when handed
a design. See STRATEGY.md 6a and the report.
"""
import sys

import actions
import cli
import gamestate as gs
import remote


def show(snap, civ):
    print(f"\n--- designs ---")
    for d in snap.designs:
        print(f"  #{d.id:<5} {d.design_name!r:16} role={d.role:<8} "
              f"cost={d.cost} speed={d.speed} "
              f"parts: chassis={d.chassis} engines={d.engines} "
              f"weapons={d.weapons} modules={d.modules}")
    print(f"\n--- our planets ---")
    for p in snap.owned_planets(civ):
        yard = "shipyard" if 2 in p.facilities else "no shipyard"
        print(f"  #{p.id:<5} {p.planet_name!r:16} {yard:12} "
              f"production={p.production!r} building={p.building_now} "
              f"progress={p.build_progress}")


def main():
    args = sys.argv[1:]
    def opt(n, d=None, cast=None):
        return cli.opt(args, n, d, cast)
    civ_name = opt("--civ", "GoodGuy")
    apply_it = "--apply" in args
    dry_run  = not apply_it

    snap = gs.Snapshot()
    civ = snap.civ(civ_name)
    if civ is None:
        sys.exit(f"no civ {civ_name!r}")
    print(f"=== build — turn {snap.turn} — civ {civ.civ_name!r} — "
          f"{'DRY RUN' if dry_run else 'APPLYING'} ===")

    new_name = opt("--new-design")
    queue, planet_id = opt("--queue"), opt("--planet")
    wealth = "--wealth" in args

    if not (new_name or queue or wealth):
        show(snap, civ)
        print("\nusage: --new-design <name>  |  --queue <design> --planet <id>"
              "  |  --wealth --planet <id>")
        return

    def design_by_name(n):
        d = next((x for x in snap.designs if x.design_name == n), None)
        if d is None:
            sys.exit(f"no design named {n!r}; saw "
                     f"{[x.design_name for x in snap.designs]}")
        return d

    def planet_by_id(i):
        p = next((x for x in snap.owned_planets(civ) if x.id == int(i)), None)
        if p is None:
            sys.exit(f"we do not own planet {i}")
        return p

    # The elevated handle is only needed to CREATE a design; queueing is a
    # plain write and must not require it.
    rem = None
    if new_name and not dry_run:
        rem = remote.Remote(snap.state.pid)
    act = actions.Actuator(snap, dry_run=dry_run, remote_=rem)

    try:
        if new_name:
            print(f"\ndefault-constructing a ShipDesign named {new_name!r}")
            print("  NOTE: the result will have NO parts and is very likely "
                  "unbuildable — see the module docstring")
            block = act.create_design(new_name)
            if block is not None:
                snap.refresh()
                found = next((d for d in snap.designs
                              if d.addr == block + 12), None)
                print(f"\n  the new design is "
                      f"{'VISIBLE' if found else 'NOT VISIBLE'} to an EJBO scan"
                      + (f": {found}" if found else
                         " — it has no EJBO tag, so it exists but the object "
                         "scanner cannot enumerate it"))
                if found:
                    print(f"    name={found.design_name!r} role={found.role} "
                          f"speed={found.speed} cost={found.cost}")
                    print(f"    chassis={found.chassis} engines={found.engines} "
                          f"weapons={found.weapons} modules={found.modules}")
                print(f"    queue it with:  --queue {new_name} --planet <id> --apply")

        if queue:
            if not planet_id:
                sys.exit("--queue needs --planet")
            act.queue_ship(planet_by_id(planet_id), design_by_name(queue))

        if wealth:
            if not planet_id:
                sys.exit("--wealth needs --planet")
            act.set_wealth_mode(planet_by_id(planet_id))
    finally:
        if rem is not None:
            rem.close()

    print(f"\n{act.summary()}")
    if dry_run:
        print("re-run with --apply to perform these writes")


if __name__ == "__main__":
    main()
