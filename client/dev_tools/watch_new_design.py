"""
watch_new_design.py — catch a design the moment the engine creates one, and
report exactly what registering it consisted of
=========================================================================
    python watch_new_design.py

Leave this running, then create and SAVE a design in the game's design editor.

WHY A WATCHER RATHER THAN TWO SNAPSHOTS BY HAND. The question is which memory
gained a pointer to the new object, and that only means something if the "before"
picture is of the same galaxy moments earlier. Taking the two snapshots manually
puts an unknown amount of play between them — a turn boundary, a scan report, a
ship arriving — and any of that shows up as a difference that has nothing to do
with designs. This records the baseline itself, then waits.

WHAT IT ANSWERS. STRATEGY.md §3 says a design built by actuator I has zero
`tag-12` references where a UI design has three, and that is the whole blocker on
the AI ever designing a ship. The referrers printed here ARE the registration:
whatever the editor wrote that we do not, in the order it appears.

It writes nothing and calls nothing in the game. It only reads.
"""
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "ai_player"))

import ejbo_viewer as ev
import gamestate as gs
import find_refs as fr


def design_key(d):
    return (d.id, d.addr)


def describe(snap, d):
    own = d.owner.civ_name if d.owner is not None else None
    return (f"ShipDesign#{d.id} {d.design_name!r} owner={own!r} "
            f"tag=0x{d.addr:08X} computed={d.computed}")


def main():
    snap = gs.Snapshot()
    handle = snap.state.handle
    before = {design_key(d) for d in snap.designs}
    print(f"attached to pid {snap.state.pid}; {len(before)} design(s) at baseline:")
    for d in snap.designs:
        print("   ", describe(snap, d))
    print("\nNow create and SAVE a design in the game's design editor.")
    print("Watching (Ctrl-C to stop)...\n", flush=True)

    while True:
        time.sleep(2.0)
        try:
            snap = gs.Snapshot(snap.state)
        except Exception as exc:
            print(f"  (snapshot failed: {exc})", flush=True)
            continue
        fresh = [d for d in snap.designs if design_key(d) not in before]
        if not fresh:
            continue

        for d in fresh:
            print("=" * 70)
            print("NEW DESIGN:", describe(snap, d))
            print("=" * 70, flush=True)

            # Give the engine a moment to finish whatever else the save does —
            # a design seen mid-registration would report half the answer and
            # look like a complete one.
            time.sleep(2.0)
            snap = gs.Snapshot(snap.state)
            spans = fr.build_attribution(snap)
            known = {}
            for lo, hi, label, base in spans:
                for form, delta in fr.REF_FORMS.items():
                    known[base + delta] = f"-> {label} ({form})"

            regions = fr.scan_regions(handle)
            values = {d.addr + delta: form
                      for form, delta in fr.REF_FORMS.items()}
            hits = fr.find_dwords(handle, regions, list(values))
            for form, delta in fr.REF_FORMS.items():
                got = hits.get(d.addr + delta, [])
                external = [h for h in got
                            if not (d.addr - 64 <= h < d.addr + 280)]
                print(f"\n  {form:7} (0x{d.addr + delta:08X}): "
                      f"{len(external)} external reference(s)")
                for h in external:
                    who = fr.attribute(spans, h)
                    print(f"    0x{h:08X}  {who or 'unattributed'}")
                    for at, val, name, is_hit in fr.neighbourhood(handle, h, known):
                        mark = " <<<" if is_hit else ""
                        tail = f"  {name}" if name else ""
                        print(f"         {at:08X}: {val:08X}{tail}{mark}")
            print(flush=True)
            before.add(design_key(d))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nstopped")
