"""
build.py — Ship designs and ship production (actuators D, D′ and I)
===================================================================
The production half of the AI: what a civ can build, and telling a planet to
build it. Rules R-EXP-01, R-XPN-01 and R-XTM-01 all end up here.

    python build.py                                    # what we have
    python build.py --new-design aiscout --fit engines=0,0,0 --apply
    python build.py --design scout2 --fit engines=0,0,0 --apply
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
    civ_name = opt("--civ")
    apply_it = "--apply" in args
    dry_run  = not apply_it

    snap = gs.Snapshot()
    civ = gs.resolve_civ(snap, civ_name)
    if civ is None:
        sys.exit(f"no civ {civ_name!r}")
    print(f"=== build — turn {snap.turn} — civ {civ.civ_name!r} — "
          f"{'DRY RUN' if dry_run else 'APPLYING'} ===")

    new_name = opt("--new-design")
    design_name, fit = opt("--design"), opt("--fit")
    queue, planet_id = opt("--queue"), opt("--planet")
    wealth = "--wealth" in args

    if not (new_name or queue or wealth or fit):
        show(snap, civ)
        print("\nusage: --new-design <name>  |  --design <name> --fit "
              "engines=0,0,0  |  --queue <design> --planet <id>  |  "
              "--wealth --planet <id>")
        return

    def design_by_name(n):
        hits = [x for x in snap.designs if x.design_name == n]
        if not hits:
            sys.exit(f"no design named {n!r}; saw "
                     f"{[x.design_name for x in snap.designs]}")
        if len(hits) > 1:
            sys.exit(f"{len(hits)} designs are named {n!r} "
                     f"(ids {[x.id for x in hits]}); refusing to guess. Use a "
                     f"unique name.")
        return hits[0]

    def planet_by_id(i):
        p = next((x for x in snap.owned_planets(civ) if x.id == int(i)), None)
        if p is None:
            sys.exit(f"we do not own planet {i}")
        return p

    # The elevated handle is only needed to CREATE a design; queueing is a
    # plain write and must not require it.
    rem = None
    if (new_name or fit) and not dry_run:
        rem = remote.Remote(snap.state.pid)
    act = actions.Actuator(snap, dry_run=dry_run, remote_=rem)

    try:
        if new_name:
            print(f"\ndefault-constructing a ShipDesign named {new_name!r}")
            print("  NOTE: the result will have NO parts and is very likely "
                  "unbuildable — see the module docstring")
            block = act.create_design(new_name, civ)
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

        if fit:
            # --fit alongside --new-design targets what was just created, so a
            # whole design is one command: create, own, fit.
            design_name = design_name or new_name
            if not design_name:
                sys.exit("--fit needs --design <name>")
            if "=" not in fit:
                sys.exit("--fit expects category=id[,id...], e.g. engines=0,0,0")
            cat, raw = fit.split("=", 1)
            ids = [int(x) for x in raw.split(",") if x != ""]
            snap.refresh()
            if dry_run and design_name == new_name:
                # The design does not exist yet in a dry run, so there is
                # nothing to look up — say what would happen and move on.
                print(f"\n  [WOULD FIT] {cat}={ids} to the design that "
                      f"--new-design would have created")
                d = None
            else:
                d = design_by_name(design_name)
            if d is not None:
                print(f"\nfitting {cat}={ids} to {d}")
                act.fit_parts(d, cat, ids)
            if d is not None and not dry_run:
                snap.refresh()
                d2 = next((x for x in snap.designs if x.id == d.id), None)
                if d2:
                    print(f"\n  after fitting: computed={d2.computed} "
                          f"speed={d2.speed} cost={d2.cost} hp={d2.hp}")
                    print(f"    chassis={d2.chassis} scanners={d2.scanners} "
                          f"engines={d2.engines} weapons={d2.weapons} "
                          f"modules={d2.modules}")
                    print("    if speed/cost are still -1, the engine has not "
                          "recomputed them yet — advance a turn and re-check")

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
