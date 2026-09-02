"""
inject_civ.py — Add a whole PLAYER to a save blob
=================================================
Clones an existing civ's `OWNR` block under a new name and object id, and hands
the new civ a homeworld by giving an uncolonised planet the donor homeworld's
`PLPR`. The engine's own deserialiser then builds and registers the civ on load,
the same way `inject_design.py` gets a ship design built.

    python inject_civ.py ../saves/<capture>.b64 --list
    python inject_civ.py ../saves/<capture>.b64 --name Ceti --name Draco \
        -o ../loadgame_blob.b64

`--name` is repeatable, so a two-civ galaxy becomes a twenty-civ galaxy in one
pass: each new civ gets its own auto-picked homeworld, chosen to sit as far as
possible from every planet already spoken for.

`[ ]` NOT DONE: the new civ gets no ships. It gets a homeworld with whatever the
donor's homeworld had — shipyard included — so it can build its own, but the
`SHIP`/`DYNO` records are galaxy-level rather than per-civ and adding one is a
separate job.

`[ ]` **A CLONED CIV INHERITS THE DONOR'S EXPLORED MAP, AND THAT IS A CHEAT.**
`EXSY` is copied verbatim, so injecting a civ off a developed donor hands the
newcomer everything that donor had found — including, once anyone has met
anyone, where the home planets are. Nobody should start a game knowing that.

`[ ]` **Homeworld placement is one hardcoded policy, and it should be a choice.**
`pick_homeworld` spreads civs as far apart as it can, which is the right default
for testing the strategy — contact is then a thing the AI has to earn rather than
a thing the setup hands it. It is not the only placement anyone will want:
RANDOM placement is the obvious second (it is what a real galaxy generator does,
and it is the only way to test how the rules cope with a hostile neighbour two
systems away), and clustered or per-team placement follow from that. Make it a
`--placement` option rather than adding a second function.

"""
import argparse
import math
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import save_parser as sp
import inject_design as idg

OWNR, DATA, PLNT, PLPR, DSGN, SDPR, KNPL, GLXY = (
    b"OWNR", b"DATA", b"PLNT", b"PLPR", b"DSGN", b"SDPR", b"KNPL", b"GLXY")


# ── section helpers ───────────────────────────────────────────────────────────
def set_len(buf, off, new_len):
    ver = struct.unpack_from("<I", buf, off + 4)[0] >> 26
    struct.pack_into("<I", buf, off + 4, (ver << 26) | new_len)


def add_len(buf, off, delta):
    lw = struct.unpack_from("<I", buf, off + 4)[0]
    struct.pack_into("<I", buf, off + 4, ((lw >> 26) << 26) |
                     ((lw & 0x3FFFFFF) + delta))


def splice(blob, start, end, new_bytes, log=print, what=""):
    """Replace blob[start:end], widening every section that encloses it.

    Sections nest and carry spanning lengths, so a payload cannot grow on its
    own: `inject_design.py` produced a blob the client rejected outright by
    missing one ancestor. Ancestors are found from a point strictly INSIDE the
    replaced range, so the enclosing sections are widened and the neighbours are
    not.
    """
    delta = len(new_bytes) - (end - start)
    out = bytearray(blob)
    if delta:
        for off, tag, ver, ln in idg.containing_sections(blob, start + 1
                                                         if end > start else start):
            add_len(out, off, delta)
            if what:
                log(f"      {tag.decode():4} @{off:<7} len {ln} -> {ln + delta}")
    out[start:end] = new_bytes
    return bytes(out)


def replace_u32(buf, old, new, start, end):
    """Every 4-byte little-endian `old` in buf[start:end] becomes `new`."""
    needle, repl = struct.pack("<I", old), struct.pack("<I", new)
    n, pos = 0, start
    while True:
        i = buf.find(needle, pos, end)
        if i < 0:
            return n
        buf[i:i + 4] = repl
        n += 1
        pos = i + 4


# ── records ───────────────────────────────────────────────────────────────────
def owner_records(blob):
    """Every OWNR, with its name and object id located.

    Validated by shape rather than by trusting the tag: a four-byte run can
    occur inside binary payloads, so a candidate has to carry a plausible
    name length, a printable name and a DATA child right behind it.
    """
    out = []
    for off in idg.find_all(blob, OWNR):
        try:
            ver, ln = idg.sec_len(blob, off)
        except Exception:
            continue
        p = off + 8
        if ln <= 0 or p + ln > len(blob):
            continue
        nlen = struct.unpack_from("<I", blob, p)[0]
        if not (0 < nlen <= 64) or p + 8 + nlen > len(blob):
            continue
        name = blob[p + 4:p + 4 + nlen]
        if not all(32 <= c < 127 for c in name):
            continue
        if blob[p + 8 + nlen:p + 12 + nlen] != DATA:
            continue
        out.append({
            "off": off, "ver": ver, "ln": ln, "end": off + 8 + ln,
            "payload": p, "nlen": nlen, "name": name.decode("ascii"),
            "id_at": p + 4 + nlen,
            "oid": struct.unpack_from("<I", blob, p + 4 + nlen)[0],
        })
    return out


def planet_records(blob):
    """Every PLNT, with the fields a homeworld transplant needs."""
    out = []
    for off in idg.find_all(blob, PLNT):
        try:
            ver, ln = idg.sec_len(blob, off)
        except Exception:
            continue
        p = off + 8
        if ln <= 0 or p + ln > len(blob):
            continue
        try:
            pid, x, z, y, owner, f5 = struct.unpack_from("<IfffIf", blob, p)
            nlen = struct.unpack_from("<I", blob, p + 24)[0]
        except Exception:
            continue
        if nlen > 64 or p + 28 + nlen > off + 8 + ln:
            continue
        name = blob[p + 28:p + 28 + nlen]
        if not all(32 <= c < 127 for c in name):
            continue
        plpr = blob.find(PLPR, p, off + 8 + ln)
        if plpr < 0:
            continue
        pver, pln = idg.sec_len(blob, plpr)
        out.append({
            "off": off, "ln": ln, "end": off + 8 + ln, "payload": p,
            "id": pid, "pos": (x, z, y), "owner": owner, "f5": f5,
            "nlen": nlen, "name": name.decode("ascii"), "name_at": p + 24,
            "plpr": plpr, "plpr_ln": pln,
            "plpr_payload": plpr + 8, "plpr_end": plpr + 8 + pln,
        })
    return out


def civ_count_at(blob):
    """Offset of GLXY's leading civ count."""
    g = blob.find(GLXY)
    if g < 0:
        raise SystemExit("no GLXY section; is this a real save blob?")
    return g + 8


def dist(a, b):
    return math.sqrt(sum((u - v) ** 2 for u, v in zip(a, b)))


def pick_homeworld(planets, taken):
    """The uncolonised planet furthest from everything already claimed.

    Maximin rather than "furthest from the mean": with twenty civs to place, the
    mean sits in the middle of the galaxy and every pick lands on the same rim.
    """
    claimed = [p["pos"] for p in planets if p["owner"]] + \
              [p["pos"] for p in planets if p["id"] in taken]
    free = [p for p in planets
            if not p["owner"] and p["id"] not in taken and p["nlen"] == 0]
    if not free:
        raise SystemExit("no uncolonised planet left to use as a homeworld")
    if not claimed:
        return free[0]
    return max(free, key=lambda p: min(dist(p["pos"], c) for c in claimed))


# ── the injection ─────────────────────────────────────────────────────────────
def add_civ(blob, new_name, donor_name=None, home_id=None, taken=(),
            user_id=None, log=print):
    owners = owner_records(blob)
    if not owners:
        raise SystemExit("no OWNR records found")
    names = {o["name"] for o in owners}
    if new_name in names:
        raise SystemExit(f"a civ named {new_name!r} already exists; the name is "
                         f"what the engine derives the registry key from, so it "
                         f"must be unique")
    if len(new_name) > 15:
        raise SystemExit(f"{new_name!r} is longer than the 15-char name buffer")

    donor = (next((o for o in owners if o["name"] == donor_name), None)
             if donor_name else min(owners, key=lambda o: o["ln"]))
    if donor is None:
        raise SystemExit(f"no civ named {donor_name!r}; saw {sorted(names)}")

    planets = planet_records(blob)
    donor_home = max((p for p in planets if p["owner"] == donor["oid"]),
                     key=lambda p: p["plpr_ln"], default=None)
    if donor_home is None:
        raise SystemExit(f"{donor['name']!r} owns no planet to copy")

    if home_id is not None:
        target = next((p for p in planets if p["id"] == home_id), None)
        if target is None:
            raise SystemExit(f"no planet with object id {home_id}")
        if target["owner"]:
            raise SystemExit(f"planet {home_id} already belongs to civ id "
                             f"{target['owner']}")
    else:
        target = pick_homeworld(planets, set(taken))

    new_id = idg.max_object_id(blob) + 1
    log(f"\n=== {new_name!r}: cloning {donor['name']!r} (id {donor['oid']}) "
        f"as object id {new_id}")
    log(f"  homeworld: planet #{target['id']} at "
        f"({target['pos'][0]:.0f}, {target['pos'][1]:.0f}, {target['pos'][2]:.0f})"
        f", {min(dist(target['pos'], p['pos']) for p in planets if p['owner']):.0f} "
        f"from the nearest owned planet")
    log(f"  donor homeworld: planet #{donor_home['id']} {donor_home['name']!r} "
        f"(PLPR {donor_home['plpr_ln']} bytes)")

    # -- 1. the homeworld, before anything shifts the planet offsets ----------
    donor_plpr = bytearray(blob[donor_home["plpr_payload"]:donor_home["plpr_end"]])
    n = replace_u32(donor_plpr, donor["oid"], new_id, 0, len(donor_plpr))
    log(f"  PLPR transplant: {len(donor_plpr)} bytes, {n} owner reference(s) "
        f"repointed to {new_id}")
    out = splice(blob, target["plpr_payload"], target["plpr_end"],
                 bytes(donor_plpr), log=log, what="plpr")

    # The name is in front of the PLPR, so re-read the record after the splice
    # rather than reusing offsets the splice may have moved.
    target = next(p for p in planet_records(out) if p["id"] == target["id"])
    hq = f"{new_name}'s HQ".encode("ascii")
    out = splice(out, target["name_at"], target["name_at"] + 4 + target["nlen"],
                 struct.pack("<I", len(hq)) + hq, log=log, what="name")

    target = next(p for p in planet_records(out) if p["id"] == target["id"])
    buf = bytearray(out)
    struct.pack_into("<I", buf, target["payload"] + 16, new_id)
    log(f"  planet #{target['id']} owner -> {new_id}, named {hq.decode()!r}")
    blob = bytes(buf)

    # -- 2. the OWNR clone ---------------------------------------------------
    owners = owner_records(blob)
    donor = next(o for o in owners if o["name"] == donor["name"])
    rec = bytearray(blob[donor["off"]:donor["end"]])

    # Ownership by id, everywhere inside the clone: design owners, citizens,
    # and the prologue's own object id.
    n = replace_u32(rec, donor["oid"], new_id, 0, len(rec))
    log(f"  OWNR clone: {len(rec)} bytes, {n} embedded owner reference(s) "
        f"repointed")

    nb = new_name.encode("ascii")
    rec = bytearray(splice(bytes(rec), 8, 12 + donor["nlen"],
                           struct.pack("<I", len(nb)) + nb))
    log(f"  name {donor['name']!r} -> {new_name!r}")

    # Fresh ids for the cloned designs. Two civs may share a design NAME — both
    # start with a 'Colony Ship' — but an object id is galaxy-wide.
    next_id = new_id
    for off in idg.find_all(bytes(rec), DSGN):
        if rec[off + 12:off + 16] != SDPR:
            continue
        next_id += 1
        old = struct.unpack_from("<I", rec, off + 8)[0]
        struct.pack_into("<I", rec, off + 8, next_id)
        log(f"  design object id {old} -> {next_id}")

    # KNPL is the known-PLAYERS vector: a leading count, then one record per civ
    # met. A civ that has just appeared has met nobody.
    k = bytes(rec).find(KNPL)
    if k >= 0:
        kver, kln = idg.sec_len(bytes(rec), k)
        met = struct.unpack_from("<I", rec, k + 8)[0]
        if kln >= 4 and met < 64:
            rec = bytearray(splice(bytes(rec), k + 8, k + 8 + kln,
                                   struct.pack("<I", 0)))
            log(f"  KNPL: {met} known player(s) -> 0")
        else:
            log(f"  KNPL left alone (count field reads {met}, not a count)")

    # The last u32 of the payload is Owner:4, and it is the ONE per-civ field a
    # clone would otherwise duplicate: the blanket replace_u32 above rewrites the
    # donor's OBJECT id, and this is a different number entirely. Two generated
    # civs read 20 and 21, so max+1 continues the sequence.
    donor_uid = struct.unpack_from("<I", blob, donor["end"] - 4)[0]
    uid = (max(struct.unpack_from("<I", blob, o["end"] - 4)[0]
               for o in owners) + 1) if user_id is None else user_id
    struct.pack_into("<I", rec, len(rec) - 4, uid)
    forced = "" if user_id is None else ("  (FORCED — a value already in use "
                                         "hides the civ from the highscore list)")
    log(f"  Owner:4 {donor_uid} -> {uid}{forced}")

    # -- 3. splice the clone in after the last OWNR --------------------------
    at = max(o["end"] for o in owner_records(blob))
    log(f"  inserting {len(rec)} bytes at {at}:")
    blob = splice(blob, at, at, bytes(rec), log=log, what="ownr")

    # -- 4. the two counters -------------------------------------------------
    buf = bytearray(blob)
    ca = civ_count_at(buf)
    civs = struct.unpack_from("<I", buf, ca)[0]
    struct.pack_into("<I", buf, ca, civs + 1)
    log(f"  GLXY civ count {civs} -> {civs + 1}")

    hi = struct.unpack_from("<I", buf, idg.HIGH_WATER_ID)[0]
    if next_id > hi:
        struct.pack_into("<I", buf, idg.HIGH_WATER_ID, next_id)
        log(f"  high-water object id {hi} -> {next_id}")
    return bytes(buf), target["id"]


# ── entry point ───────────────────────────────────────────────────────────────
def describe(blob, log=print):
    owners = owner_records(blob)
    planets = planet_records(blob)
    ca = civ_count_at(blob)
    log(f"civ count field @{ca} reads "
        f"{struct.unpack_from('<I', blob, ca)[0]}; {len(owners)} OWNR record(s)")
    seen = {}
    for o in owners:
        mine = [p for p in planets if p["owner"] == o["oid"]]
        uid = struct.unpack_from("<I", blob, o["end"] - 4)[0]
        clash = f"  <-- SAME Owner:4 AS {seen[uid]!r}" if uid in seen else ""
        seen.setdefault(uid, o["name"])
        log(f"  {o['name']!r:16} id={o['oid']:<6} Owner:4={uid:<4} "
            f"OWNR {o['ln']:>6} bytes  {len(mine)} planet(s): "
            f"{', '.join(str(p['id']) for p in mine)}{clash}")
    free = [p for p in planets if not p["owner"]]
    log(f"  {len(planets)} planet(s), {len(free)} uncolonised")
    log(f"  high-water object id "
        f"{struct.unpack_from('<I', blob, idg.HIGH_WATER_ID)[0]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("capture", help=".b64 save capture to edit")
    ap.add_argument("--name", action="append", default=[],
                    help="name of a civ to add; repeatable")
    ap.add_argument("--home", action="append", default=[], type=int,
                    help="planet object id for the matching --name; "
                         "auto-picked when omitted")
    ap.add_argument("--userid", action="append", default=[], type=int,
                    help="Owner:4 for the matching --name; -1 or omitted takes "
                         "max(existing) + 1. Pass a value another civ already "
                         "holds to reproduce the missing-from-highscores bug")
    ap.add_argument("--donor", default=None,
                    help="civ to clone (default: the smallest OWNR, which is "
                         "normally the engine's own civ)")
    ap.add_argument("-o", "--out", default=None, help="output .b64")
    ap.add_argument("--dat", default=None,
                    help="also write the decompressed blob here, ready to be "
                         "passed to the client on its command line")
    ap.add_argument("--list", action="store_true",
                    help="describe the capture and exit")
    a = ap.parse_args()

    blob = sp.decode_save(open(a.capture).read())
    print(f"{os.path.basename(a.capture)}: {len(blob):,} bytes")
    describe(blob)
    if a.list or not a.name:
        return

    taken = []
    for i, name in enumerate(a.name):
        home = a.home[i] if i < len(a.home) else None
        uid = a.userid[i] if i < len(a.userid) else -1
        blob, used = add_civ(blob, name, donor_name=a.donor, home_id=home,
                             taken=taken, user_id=None if uid < 0 else uid)
        taken.append(used)

    print()
    describe(blob)

    out = a.out or os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "loadgame_blob.b64")
    enc = sp.encode_save(blob)
    with open(out, "w") as f:
        f.write(enc if isinstance(enc, str) else enc.decode("ascii"))
    print(f"\nwrote {out} ({len(blob):,} bytes of blob)")
    if a.dat:
        with open(a.dat, "wb") as f:
            f.write(blob)
        print(f"wrote {a.dat}")


if __name__ == "__main__":
    main()
