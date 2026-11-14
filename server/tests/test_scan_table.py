"""
test_scan_table.py: the fast sim's scans are the client's
==========================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_scan_table.py

The client's scan descriptor table at 0x80AB60 holds 0x68-byte entries, `+0`
the scan id and `+4` its production cost. Entry 0 is initialised data; the
initialiser at 0x73EE50 writes entries 1 to 7 with `mov dword ptr [abs], imm32`.
The factory at 0x54E030 builds, by id, PlanetaryScan, SystemScan, RouteScan,
FleetScan, MilitaryScan, TechnologyScan, EnergyGridScan and LeaderNameScan.

`sim.scans.SCANS` and `sim.economy.SCAN_COSTS` are checked against the table
read out of `client/CosmicSupremacy.exe`.
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sim import scans, economy

EXE = os.path.join(ROOT, 'client', 'CosmicSupremacy.exe')
TABLE, STRIDE, COUNT = 0x80AB60, 0x68, 8
INIT, INIT_END = 0x73EE50, 0x73F100
NAMES = {0: 'planetary', 1: 'system', 2: 'route', 3: 'fleet', 4: 'military',
         5: 'technology', 6: 'energy grid', 7: 'leader name'}
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


class Image:
    def __init__(self, path):
        d = self.d = open(path, 'rb').read()
        pe = struct.unpack_from('<I', d, 0x3C)[0]
        n = struct.unpack_from('<H', d, pe + 6)[0]
        opt = struct.unpack_from('<H', d, pe + 20)[0]
        self.base = struct.unpack_from('<I', d, pe + 52)[0]
        self.secs = [struct.unpack_from('<IIII', d, pe + 24 + opt + i * 40 + 8)
                     for i in range(n)]

    def off(self, va):
        for vsz, sva, rsz, ra in self.secs:
            if self.base + sva <= va < self.base + sva + rsz:
                return ra + va - self.base - sva
        raise ValueError(hex(va))


def table(img):
    """{scan id: cost} from the entry in .data and the initialiser's stores."""
    out = {}
    entry0 = struct.unpack_from('<II', img.d, img.off(TABLE))
    out[entry0[0]] = entry0[1]
    stores = {}
    code = img.d[img.off(INIT):img.off(INIT_END)]
    i = code.find(b'\xc7\x05')
    while i >= 0:
        addr, imm = struct.unpack_from('<II', code, i + 2)
        if TABLE <= addr < TABLE + STRIDE * COUNT and (addr - TABLE) % STRIDE in (0, 4):
            stores[addr] = imm
        i = code.find(b'\xc7\x05', i + 1)
    for k in range(1, COUNT):
        at = TABLE + k * STRIDE
        if at in stores and at + 4 in stores:
            out[stores[at]] = stores[at + 4]
    return out


def main():
    if not os.path.exists(EXE):
        print(f'no client at {EXE}')
        return 1
    costs = table(Image(EXE))
    print(f'client table: {dict(sorted(costs.items()))}')
    check('the table names all eight ids', sorted(costs), list(range(COUNT)))
    for sid in range(COUNT):
        name, cost = scans.SCANS[sid]
        check(f'scan {sid} is the {NAMES[sid]} scan', name, NAMES[sid])
        check(f'scan {sid} costs {costs.get(sid)}', cost, costs.get(sid))
        check(f'economy agrees on scan {sid}', economy.SCAN_COSTS[sid], cost)
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
