"""
scan.py: the referee runs a player's scan in the client and answers it

    python scan.py galaxy.b64 --civ GoodGuy --scan 0:384 -o answer.bin
    python scan.py galaxy.b64 --civ GoodGuy --earlier 0:384 --scan 2:677 -o answer.bin

`--scan` and `--earlier` are `type:id[,id...]`. `--earlier` repeats, in the
order the civ's scans were delivered this turn.

A planetary, technology or energy-grid scan reads state the player's view
does not hold, so the client sends `DOSC` online and the referee answers.
`answer` loads the authoritative galaxy in a client, replays the civ's scans
delivered earlier this turn, runs the engine's own `UseScans` (`0x0054E840`)
on the request, saves, and builds the answer from the capture. The engine
checks the stock and each target's class, takes one scan per report and
posts the detection items. Nothing here is recorded: the capture is thrown
away, and the tick replays the turn's delivered scans.

The answer is the body of a `SUCC` v0 section: a `u32` count, each new report
section, the scanner's `OWPR` and its `KNPL`. The client reads the reports by
tag (`0x0054E2D0`), then the `OWPR` (`0x00540100`) and the `KNPL`
(`0x00535160`).

The client's wait for an answer (`0x0056E930`) holds no request id, so one
answer answers one request.
"""
import argparse
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEV = os.path.join(REPO, "client", "dev_tools")
for _d in (os.path.join(HERE, "dev_tools"), HERE, DEV,
           os.path.join(DEV, "ai_player")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import save_parser as sp
import merge_orders as mo
import fog
import turn_store

SCAN_TYPES = {0: "planetary", 1: "system", 2: "route", 3: "fleet",
              4: "military", 5: "technology", 6: "energy grid",
              7: "leader name"}


class ScanError(Exception):
    """The scan could not be run; the player keeps it."""


def parse_request(payload: bytes):
    """(scan type, [target object id]) from a `DOSC` v0 payload."""
    try:
        return turn_store.parse_scan_request(payload)
    except ValueError as exc:
        raise ScanError(str(exc)) from None


def build_request(kind: int, targets) -> bytes:
    """The `DOSC` v0 payload the client sends for this scan."""
    return turn_store.scan_request(kind, targets)


def answer_body(capture: bytes, civ: str, made: int) -> bytes:
    """The `SUCC` body for the last `made` reports of `civ` in `capture`."""
    tree = sp.parse_blob(capture)
    owners = fog._owners(capture, tree)
    if civ not in owners:
        raise ScanError(f"no civ {civ!r} in the capture")
    oid, owner = owners[civ]
    reports = mo.reports_of(capture, oid)
    if reports is None:
        raise ScanError(f"{civ}'s reports do not read in the capture")
    if made > len(reports):
        raise ScanError(f"{made} new report(s) but {civ} holds {len(reports)}")
    data = next(owner.find("DATA"))
    owpr = fog._sections(owner)[b"OWPR"]
    knpl = next(c for c in data.children if c.tag == b"KNPL")
    new = reports[len(reports) - made:] if made else []
    return (struct.pack("<I", made) + b"".join(new)
            + capture[owpr.start:owpr.end] + capture[knpl.start:knpl.end])


def run_scans(blob: bytes, civ: str, scans, save_dir=None, work_dir=None,
              log=print):
    """Run `scans`, [(type, [target id])], as `civ` in a client on `blob`.

    Returns (capture, [reports made by each scan]). The client is closed and
    its lock released before returning.
    """
    import game_cycle as gc
    import player_turn
    import remote

    work_dir = work_dir or os.path.join(HERE, "referee_work")
    os.makedirs(work_dir, exist_ok=True)
    dat = os.path.join(work_dir, f"scan_{int(time.time() * 1000)}.dat")
    with open(dat, "wb") as f:
        f.write(blob)
    try:
        snap = gc.restart(dat, purpose="referee scan", wait_for_lock=240.0)
        try:
            owner = snap.civ(civ)
            if owner is None:
                raise ScanError(f"no civ {civ!r} in the galaxy")
            alloc = {o["id"]: o["addr"] - 8 for o in snap.state.objects
                     if not o["static"]}
            rem = remote.Remote(snap.state.pid, log=log)
            try:
                made = []
                for kind, targets in scans:
                    missing = [t for t in targets if t not in alloc]
                    if missing:
                        raise ScanError(f"no object {missing} in the galaxy")
                    out = rem.use_scans(owner.addr, kind,
                                        [alloc[t] for t in targets])
                    log(f"  scan: {SCAN_TYPES[kind]} on {targets}: "
                        f"{len(out)} report(s)")
                    made.append(len(out))
            finally:
                rem.close()
            capture = sp.load_any(player_turn.collect("scan", save_dir=save_dir,
                                                      log=log))
        finally:
            gc.close_client()
    finally:
        try:
            os.remove(dat)
        except OSError:
            pass
    return capture, made


def answer(blob: bytes, civ: str, kind: int, targets, earlier=(),
           save_dir=None, log=print) -> bytes:
    """The `SUCC` body answering `civ`'s scan on the authoritative `blob`.

    `earlier` is the civ's scans delivered this turn, [(type, [target id])],
    replayed first so the stock is what the player's client holds.
    """
    capture, made = run_scans(blob, civ, list(earlier) + [(kind, list(targets))],
                              save_dir=save_dir, log=log)
    return answer_body(capture, civ, made[-1])


def _scan_arg(text):
    kind, _, ids = text.partition(":")
    return int(kind), [int(t) for t in ids.split(",") if t]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("galaxy", help="the authoritative blob (.b64 or .dat)")
    ap.add_argument("--civ", required=True)
    ap.add_argument("--scan", required=True, type=_scan_arg)
    ap.add_argument("--earlier", action="append", default=[], type=_scan_arg)
    ap.add_argument("--save-dir", help="where the capture server writes saves")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    blob = sp.load_any(a.galaxy)
    kind, targets = a.scan
    body = answer(blob, a.civ, kind, targets, a.earlier, save_dir=a.save_dir)
    with open(a.out, "wb") as f:
        f.write(body)
    print(f"wrote {len(body):,} bytes, "
          f"{struct.unpack_from('<I', body)[0]} report(s)")


if __name__ == "__main__":
    main()
