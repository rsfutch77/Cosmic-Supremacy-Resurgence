"""
patch_tutorial.py: fix the Tutorial's panel, step checks and step texts

    python patch_tutorial.py --source CosmicSupremacy.exe --out <stage>\\game\\CosmicSupremacy.exe
    python patch_tutorial.py --status <exe> [<exe> ...]

`--out` may be `--source` when neither is tracked by git; the release build
patches the staged client in place after patch_launch_messages.py.

The Tutorial runs inside CosmicSupremacy.exe. Its steps are a `char*[27]` at
`0x00819688` (`.data`, writable), indexed by step id. ShowNext (`0x0057E3A0`)
sets the current step at `0x0086F4F4` and shows its text; Event(id)
(`0x0057DF60`) unlocks Next only when `id` is the current step.

Panel (issue #1). Dialog 206 is 219 by 295 dialog units, its text area 211
by 272, and the text area neither scrolls nor wraps, so the longest steps lose
their last lines and the widest lines lose their last word. Both grow by
PANEL_WIDER and PANEL_TALLER, and the Next button moves with them.

Orders given early (issue #2). Step 14 unlocks on a Colonize order (type 3)
and step 17 on a Move order (type 1), and both are raised only when the order
is issued, so an order given before the step appears never counts. The Next
handler's call to Unlock at `0x004C1D04` is redirected to a stub in the
`.text` padding at `0x006D30A0`. The stub calls Unlock, then for step 14 or 17
walks the Ship registry (`0x00854628`) and raises the step's Event when a
local ship already holds that order type. Step 17 accepts any Move order, not
only one to the nearest sun.

Scout Ship (issue #4). Step 21 waits for the Scout Ship, which takes several
turns and a planet full of Workers that then recruits no Military for step
22. The production tick (`0x004FA490`) is hooked where it fetches the item's
cost: on step 21, a local ship design's storage is raised to its cost, so the
ship is finished on the next tick. Where that tick raises Event(21), the hook
also adds one Stationed Military to the planet if it has none, the way
recruitment does (`0x004F4E00`). Facilities are never touched.

Texts (issues #3 and #4). The renderer takes `\\r<rrggbb>` to set a colour
and `\\r<default>` to restore it, so each step's task is drawn in TASK_COLOUR.
Step 20 names a worker count. The texts no longer fit their `.rdata` slots,
so they are written to a new `.tutor` section and the step table points there.
"""
import argparse
import os
import shutil
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_DIR = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(CLIENT_DIR)

IMAGE_BASE = 0x00400000
CREATE_NO_WINDOW = 0x08000000

B, I = '\x08', '\x0c'                 # bold toggle, icon tag prefix
TASK_COLOUR = '\r<ffcc33>'
TASK_END = '\r<default>'

STEP_TABLE = 0x00819688
STEP_COUNT = 27

PANEL_TALLER = 48                     # dialog units, about six lines of text
PANEL_WIDER = 24                      # dialog units, about six characters
DIALOG_206 = 0x007D3820               # file offset of the DLGTEMPLATEEX
# (file offset, original, growth, what) for each geometry word that changes
PANEL_WORDS = [
    (0x007D3836, 219, PANEL_WIDER, 'dialog width'),
    (0x007D3838, 295, PANEL_TALLER, 'dialog height'),
    (0x007D3878, 211, PANEL_WIDER, 'text area width'),
    (0x007D387A, 272, PANEL_TALLER, 'text area height'),
    (0x007D38A0, 165, PANEL_WIDER, 'Next button left'),
    (0x007D38A2, 277, PANEL_TALLER, 'Next button top'),
]

CALL_SITE = 0x004C1D04                # call Unlock in the Next handler
UNLOCK = 0x004C1C60                   # TutorialDlg::Unlock(bool), thiscall, ret 4
EVENT = 0x0057DF60                    # TutorialEvent(int id), cdecl
LOCAL_OWNER = 0x0052A8D0              # returns the local player's Owner in eax
CURRENT_STEP = 0x0086F4F4
SHIPS_BEGIN = 0x00854628
SHIPS_END = 0x0085462C
STUB = 0x006D30A0                     # inside 3947 bytes of int3 padding

TUTORIAL_ON = 0x0086F4F8
SHIP_DESIGN_VFTABLE = 0x00771DF8      # ShipDesign has no subclasses
CITIZEN_CTOR = 0x005134D0             # Citizen::Citizen(int job, Owner*), thiscall, ret 8
MILITARY_PUSH = 0x004CB560            # citizen list push_back(const Citizen&), thiscall, ret 4
COST_SITE = 0x004FA6ED                # mov edx, [esi]; mov eax, [edx+0x74] in the tick
COST_ORIGINAL = bytes.fromhex('8b168b4274')
SHIP_DONE_SITE = 0x004FA7E6           # call Event after push 21, ship finished
COST_STUB = 0x006D3110
SHIP_DONE_STUB = 0x006D3170

SECTION_NAME = b'.tutor'


def _rel(frm, to):
    return struct.pack('<i', to - (frm + 5))


class _Asm:
    def __init__(self, base):
        self.base = base
        self.code = bytearray()

    def call(self, target):
        self.code += b'\xe8' + _rel(self.base + len(self.code), target)

    def jump8(self, op):
        self.code += bytes([op, 0])
        return len(self.code) - 1

    def land(self, *slots):
        for slot in slots:
            self.code[slot] = len(self.code) - (slot + 1)


def cost_stub():
    """Replaces the tick's cost fetch, esi = PlanetProperties. Returns eax = cost fn."""
    a = _Asm(COST_STUB)
    c = a.code
    c += COST_ORIGINAL                                      # the two moved instructions
    c += b'\x80\x3d' + struct.pack('<I', TUTORIAL_ON) + b'\x00'
    out = [a.jump8(0x74)]                                   # je out
    c += b'\x83\x3d' + struct.pack('<I', CURRENT_STEP) + b'\x15'  # step 21
    out.append(a.jump8(0x75))
    c += b'\x8b\x8e\xd0\x00\x00\x00'                        # mov ecx, [esi+0xd0] item
    c += b'\x85\xc9'
    out.append(a.jump8(0x74))
    c += b'\x81\x39' + struct.pack('<I', SHIP_DESIGN_VFTABLE)
    out.append(a.jump8(0x75))
    c += b'\x50'                                            # push eax (cost fn)
    c += b'\x8b\xce\xff\x52\x10'                            # planet's owner
    c += b'\x50'
    a.call(LOCAL_OWNER)
    c += b'\x59\x3b\xc1'                                    # pop ecx; cmp eax, ecx
    keep = [a.jump8(0x75)]
    c += b'\x8b\xce\xff\x14\x24'                            # cost
    c += b'\x39\x46\x20'                                    # cmp [esi+0x20], eax
    keep.append(a.jump8(0x7d))                              # jge
    c += b'\x89\x46\x20'                                    # storage = cost
    a.land(*keep)
    c += b'\x58'                                            # pop eax (cost fn)
    a.land(*out)
    c += b'\xc3'
    return bytes(c)


def ship_done_stub():
    """Replaces call Event(21) when a ship is finished, esi = PlanetProperties."""
    a = _Asm(SHIP_DONE_STUB)
    c = a.code
    c += b'\xff\x74\x24\x04'                                # push [esp+4]
    a.call(EVENT)
    c += b'\x83\xc4\x04'
    c += b'\x80\x3d' + struct.pack('<I', TUTORIAL_ON) + b'\x00'
    out = [a.jump8(0x74)]
    c += b'\xa1' + struct.pack('<I', CURRENT_STEP)
    c += b'\x83\xe8\x14\x83\xf8\x02'                        # steps 20 to 22
    out.append(a.jump8(0x77))
    c += b'\x8b\x46\x54\x3b\x46\x50'                        # military end == begin
    out.append(a.jump8(0x75))
    c += b'\x8b\x16\x8b\xce\xff\x52\x10'                    # planet's owner
    c += b'\x50'
    a.call(LOCAL_OWNER)
    c += b'\x59\x3b\xc1'
    out.append(a.jump8(0x75))
    c += b'\x83\xec\x10'                                    # a Citizen on the stack
    c += b'\x51\x6a\x03'                                    # owner, job 3 (Military)
    c += b'\x8d\x4c\x24\x08'
    a.call(CITIZEN_CTOR)
    c += b'\x50\x8d\x4e\x44'                                # into the military list
    a.call(MILITARY_PUSH)
    c += b'\x83\xc4\x10'
    c += b'\x6a\x1a'                                        # the Stationed Military popup
    a.call(EVENT)
    c += b'\x83\xc4\x04'
    a.land(*out)
    c += b'\xc3'
    return bytes(c)


def stub_bytes():
    """The Next handler's stub, assembled for STUB."""
    code = bytearray()

    def at():
        return STUB + len(code)

    def call(target):
        code.extend(b'\xe8' + _rel(at(), target))

    def jump8(op):
        code.extend(bytes([op, 0]))
        return len(code) - 1

    def land(slot):
        code[slot] = (len(code) - (slot + 1)) & 0xFF

    code += b'\xff\x74\x24\x04'                         # push [esp+4]
    call(UNLOCK)
    code += b'\xa1' + struct.pack('<I', CURRENT_STEP)   # mov eax, [current]
    code += b'\x83\xf8\x0e'                             # cmp eax, 14
    to_scan = jump8(0x74)                               # je scan
    code += b'\x83\xf8\x11'                             # cmp eax, 17
    to_done = jump8(0x75)                               # jne done
    land(to_scan)
    code += b'\x53\x56\x57'                             # push ebx, esi, edi
    code += b'\x8b\xd8'                                 # mov ebx, eax
    call(LOCAL_OWNER)
    code += b'\x8b\xf8'                                 # mov edi, eax
    code += b'\xba\x03\x00\x00\x00'                     # mov edx, 3 (Colonize)
    code += b'\x83\xfb\x0e'                             # cmp ebx, 14
    code += b'\x74\x05'                                 # je +5
    code += b'\xba\x01\x00\x00\x00'                     # mov edx, 1 (Move)
    code += b'\x8b\x35' + struct.pack('<I', SHIPS_BEGIN)  # mov esi, [begin]
    loop = len(code)
    code += b'\x3b\x35' + struct.pack('<I', SHIPS_END)    # cmp esi, [end]
    to_out = jump8(0x73)                                # jae out
    code += b'\x8b\x0e\x83\xc6\x04'                     # mov ecx, [esi]; add esi, 4
    code += b'\x39\x51\x3c'                             # cmp [ecx+0x3c], edx
    back = [jump8(0x75)]                                # jne loop
    code += b'\x8b\x41\x30\x8b\x00\x85\xc0'             # owner of the ship
    back.append(jump8(0x74))                            # je loop
    code += b'\x83\xe8\x2c\x3b\xc7'                     # sub eax, 0x2c; cmp eax, edi
    back.append(jump8(0x75))                            # jne loop
    code += b'\x53'                                     # push ebx
    call(EVENT)
    code += b'\x83\xc4\x04'                             # add esp, 4
    land(to_out)
    code += b'\x5f\x5e\x5b'                             # pop edi, esi, ebx
    land(to_done)
    code += b'\xc2\x04\x00'                             # ret 4
    for slot in back:
        code[slot] = (loop - (slot + 1)) & 0xFF
    return bytes(code)


# Edits per step id. ('task', start, end) colours from `start` through `end`
# inclusive, or to the end of the text when `end` is None. ('swap', old, new)
# replaces text. Each anchor must occur once.
EDITS = {
    1: [('task', B + 'Press the right-most button', None)],
    2: [('task', 'To do so\neither', None)],
    3: [('task', B + "Let's assign a " + I + '<BankerSmall>Banker', None)],
    4: [('task', "So let's " + B + 'switch to the Overview Screen', None)],
    5: [('task', "Let's switch back to the " + B + 'Planet View', None)],
    6: [('task', 'To do so ' + B + 'click on the ' + I + '<WealthSmall>Wealth', None)],
    7: [('task', "let's " + B + 'set the\nMilitary Recruitment Rate', None)],
    8: [('task', B + "Let's switch to the Research Screen", None)],
    9: [('task', B + 'Now go ahead and pick', None)],
    10: [('task', B + "Let's switch to the Ships-Screen", None)],
    # Double-clicking one ship selects only that ship, and Create Fleet needs two.
    11: [('swap',
          'orbit of your home planet. ' + B + 'Double-click\none of the two '
          + I + '<ShipShuttleSmall>Colony-Ships' + B + ' in the ' + B + 'Ships Screen'
          + B + '.\nNote how ' + B + 'both ships are selected' + B + '. This is a '
          'convenient\nfunction that helps you to select all ships at the same '
          'location\nin space as the ship that you double-clicked.\n\nNext '
          + B + 'right-click one of the two ships' + B + ', and select ' + B
          + 'Create\nFleet' + B + ' from the context menu. Congratulations, you have\n'
          'created your first fleet!',
          'orbit of your home planet.\n\n' + TASK_COLOUR + B + 'Select both '
          + I + '<ShipShuttleSmall>Colony Ships' + B + ' in the ' + B
          + 'Ships Screen' + B + ',\neither by ' + B + 'dragging a box around them'
          + B + ' or by holding ' + B + 'CTRL' + B + '\nand clicking each one. Then '
          + B + 'right-click one of them' + B + ' and\nselect ' + B + 'Create Fleet'
          + B + ' from the context menu.' + TASK_END + '\n\nCongratulations, you '
          'have created your first fleet!')],
    12: [('task', B + 'Right click on one of the two', B + 'Remove From Fleet' + B + '.')],
    13: [('task', 'simply ' + B + 'drag the ship', 'over the fleet icon.')],
    14: [('task', 'To do so\n' + B + 'right click a ', B + 'Colonize Planet' + B + '.'),
         ('task', B + 'Click on the planet that you want to colonize' + B + '.',
          B + 'Click on the planet that you want to colonize' + B + '.')],
    15: [('task', B + 'Select that ', None)],
    16: [('task', 'So, in order to list unexplored suns only', None)],
    17: [('task', 'Now go ahead and actually send', None)],
    18: [('task', "We are done with the Radar, so let's", None)],
    19: [('task', "Let's " + B + 'design a ship with a minimum speed', None)],
    20: [('task', 'Next, ' + B + 'click on the\nPlanet Production box', 'from the popup menu.'),
         ('swap',
          'The more ' + I + '<WorkerSmall>' + B + 'Workers' + B + ' assigned on this '
          'planet, the quicker\nthe production is finished. Make sure that you have '
          + B + 'at least\none ' + I + '<WorkerSmall>Worker assigned' + B
          + ' on this planet.',
          'In this Tutorial the Scout Ship is finished one turn after you\n'
          'press Next. In a real galaxy, the more ' + I + '<WorkerSmall>' + B
          + 'Workers' + B + ' assigned\non a planet, the quicker its production '
          'is finished.')],
    21: [('swap',
          'We are now waiting for the Scout Ship to finish. You can speed\n'
          'up this process by assigning even more ' + I + '<WorkerSmall>' + B
          + 'Workers' + B + ' on this\nplanet.',
          'We are now waiting for the Scout Ship to finish, which takes one\n'
          'turn in this Tutorial. In a real galaxy you can speed up\n'
          'production by assigning more ' + I + '<WorkerSmall>' + B + 'Workers'
          + B + ' on the planet.')],
    22: [('swap',
          'Military Recruitment Rate is high enough to create Military.',
          'Military Recruitment Rate is high enough to create Military.\n'
          'If your home planet had none, one was recruited when the Scout\n'
          'Ship was finished. Later on you can also ' + B + 'right-click a citizen'
          + B + '\nand draft it into Military right away for '
          + I + '<GoldMed>Credits.'),
         ('task', 'Next ' + B + 'select the Scout Ship', 'Ships-In-Orbit box' + B + '.'),
         ('task', 'The simplest method to assign crew', 'or the gray icon' + B + '.')],
}


def _once(text, anchor, step, after=0):
    at = text.find(anchor, after)
    if at < 0 or text.find(anchor, at + 1) >= 0:
        raise SystemExit(f'step {step}: anchor {anchor!r} does not occur exactly once')
    return at


def edited(step, text):
    """The step's text with its EDITS applied."""
    for kind, a, b in EDITS.get(step, []):
        if kind == 'swap':
            at = _once(text, a, step)
            text = text[:at] + b + text[at + len(a):]
            continue
        start = _once(text, a, step)
        if b is None:
            stop = len(text.rstrip('\n '))
        else:
            stop = _once(text, b, step, start) + len(b)
        text = (text[:start] + TASK_COLOUR + text[start:stop] + TASK_END
                + text[stop:])
    return text


class Image:
    def __init__(self, data):
        self.data = data
        self.pe = struct.unpack_from('<I', data, 0x3C)[0]
        if data[self.pe:self.pe + 4] != b'PE\0\0':
            raise SystemExit('not a PE image')
        self.coff = self.pe + 4
        self.opt = self.coff + 20
        opt_size = struct.unpack_from('<H', data, self.coff + 16)[0]
        self.sect = self.opt + opt_size

    @property
    def n_sections(self):
        return struct.unpack_from('<H', self.data, self.coff + 2)[0]

    def sections(self):
        for i in range(self.n_sections):
            off = self.sect + 40 * i
            name = bytes(self.data[off:off + 8]).rstrip(b'\0')
            vsize, va, rawsize, rawptr = struct.unpack_from('<IIII', self.data, off + 8)
            yield name, vsize, va, rawsize, rawptr

    def offset(self, va):
        rva = va - IMAGE_BASE
        for _name, vsize, sva, rawsize, rawptr in self.sections():
            if sva <= rva < sva + max(vsize, rawsize):
                return rawptr + (rva - sva)
        raise SystemExit(f'{va:#010x} is in no section')

    def cstring(self, va):
        off = self.offset(va)
        end = self.data.index(b'\0', off)
        return bytes(self.data[off:end]).decode('latin-1')

    def section(self, name):
        for s in self.sections():
            if s[0] == name:
                return s
        return None

    def add_section(self, name, payload):
        """Append a read-only initialised data section; returns its VA."""
        falign, = struct.unpack_from('<I', self.data, self.opt + 36)
        salign, = struct.unpack_from('<I', self.data, self.opt + 32)
        header_room = min(s[4] for s in self.sections())
        hdr = self.sect + 40 * self.n_sections
        if hdr + 40 > header_room or any(self.data[hdr:hdr + 40]):
            raise SystemExit('no room for another section header')
        end_va = max(va + vsize for _n, vsize, va, _r, _p in self.sections())
        va = -(-end_va // salign) * salign
        rawptr = -(-len(self.data) // falign) * falign
        rawsize = -(-len(payload) // falign) * falign
        struct.pack_into('<8sIIIIIIHHI', self.data, hdr, name, len(payload), va,
                         rawsize, rawptr, 0, 0, 0, 0, 0x40000040)
        struct.pack_into('<H', self.data, self.coff + 2, self.n_sections + 1)
        struct.pack_into('<I', self.data, self.opt + 56,
                         -(-(va + len(payload)) // salign) * salign)
        self.data.extend(b'\0' * (rawptr - len(self.data)))
        self.data.extend(payload.ljust(rawsize, b'\0'))
        return IMAGE_BASE + va


def table_entry(img, step):
    return img.offset(STEP_TABLE + 4 * step)


def state(data):
    """{site: 'original' | 'patched' | 'unknown'} for each part of the patch."""
    img = Image(bytearray(data))
    out = {}
    words = [struct.unpack_from('<H', data, off)[0] for off, _o, _g, _w in PANEL_WORDS]
    out['panel'] = ('original' if words == [o for _f, o, _g, _w in PANEL_WORDS] else
                    'patched' if words == [o + g for _f, o, g, _w in PANEL_WORDS]
                    else 'unknown')
    site = img.offset(CALL_SITE)
    stub = stub_bytes()
    here = bytes(data[site:site + 5])
    body = bytes(data[img.offset(STUB):img.offset(STUB) + len(stub)])
    out['orders'] = ('original' if here == b'\xe8' + _rel(CALL_SITE, UNLOCK)
                     and body == b'\xcc' * len(stub) else
                     'patched' if here == b'\xe8' + _rel(CALL_SITE, STUB)
                     and body == stub else 'unknown')
    hooks = [(COST_SITE, COST_ORIGINAL, COST_STUB, cost_stub()),
             (SHIP_DONE_SITE, b'\xe8' + _rel(SHIP_DONE_SITE, EVENT),
              SHIP_DONE_STUB, ship_done_stub())]
    sites = [bytes(data[img.offset(s):img.offset(s) + 5]) for s, _o, _t, _b in hooks]
    bodies = [bytes(data[img.offset(t):img.offset(t) + len(b)]) for _s, _o, t, b in hooks]
    if (sites == [o for _s, o, _t, _b in hooks]
            and bodies == [b'\xcc' * len(b) for _s, _o, _t, b in hooks]):
        out['scout'] = 'original'
    elif (sites == [b'\xe8' + _rel(s, t) for s, _o, t, _b in hooks]
            and bodies == [b for _s, _o, _t, b in hooks]):
        out['scout'] = 'patched'
    else:
        out['scout'] = 'unknown'
    out['texts'] = 'patched' if img.section(SECTION_NAME) else 'original'
    return out


def patch(data):
    """Patch `data` (a bytearray) in place. Returns how many parts changed."""
    before = state(data)
    if 'unknown' in before.values():
        raise SystemExit(f'not a client this patch knows: {before}')
    img = Image(data)
    done = 0

    if before['panel'] == 'original':
        if struct.unpack_from('<HH', data, DIALOG_206) != (1, 0xFFFF):
            raise SystemExit('dialog 206 is not where it was measured')
        for off, original, growth, _what in PANEL_WORDS:
            struct.pack_into('<H', data, off, original + growth)
        done += 1

    if before['orders'] == 'original':
        stub = stub_bytes()
        at = img.offset(STUB)
        data[at:at + len(stub)] = stub
        site = img.offset(CALL_SITE)
        data[site:site + 5] = b'\xe8' + _rel(CALL_SITE, STUB)
        done += 1

    if before['scout'] == 'original':
        for site, stub_at, stub in ((COST_SITE, COST_STUB, cost_stub()),
                                    (SHIP_DONE_SITE, SHIP_DONE_STUB, ship_done_stub())):
            at = img.offset(stub_at)
            data[at:at + len(stub)] = stub
            off = img.offset(site)
            data[off:off + 5] = b'\xe8' + _rel(site, stub_at)
        done += 1

    if before['texts'] == 'original':
        steps = sorted(EDITS)
        texts = {}
        for step in steps:
            va = struct.unpack_from('<I', data, table_entry(img, step))[0]
            texts[step] = edited(step, img.cstring(va)).encode('latin-1') + b'\0'
        payload = bytearray()
        where = {}
        for step in steps:
            where[step] = len(payload)
            payload += texts[step]
        base = img.add_section(SECTION_NAME, bytes(payload))
        for step in steps:
            struct.pack_into('<I', data, table_entry(img, step), base + where[step])
        done += 1
    return done


def is_tracked(path):
    try:
        r = subprocess.run(['git', 'ls-files', '--error-unmatch', path],
                           cwd=REPO_ROOT, capture_output=True,
                           creationflags=CREATE_NO_WINDOW if os.name == 'nt' else 0)
    except OSError:
        return False
    return r.returncode == 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--status', nargs='+', metavar='EXE',
                    help='report each part of the patch in these clients')
    ap.add_argument('--source', help='client to read')
    ap.add_argument('--out', help='patched client to write')
    a = ap.parse_args()

    if a.status:
        for path in a.status:
            if not os.path.exists(path):
                print(f'  {os.path.basename(path):<34} missing')
                continue
            with open(path, 'rb') as f:
                got = state(f.read())
            print(f'  {os.path.basename(path):<34} '
                  + '  '.join(f'{k} {v}' for k, v in got.items()))
        return 0

    if not (a.source and a.out):
        ap.error('give --status, or both --source and --out')
    src = a.source if os.path.isabs(a.source) else os.path.join(CLIENT_DIR, a.source)
    out = os.path.abspath(a.out)
    if not os.path.exists(src):
        sys.exit(f'no such client: {src}')
    if is_tracked(out):
        sys.exit(f'{out} is tracked by git; refusing to patch it')

    with open(src, 'rb') as f:
        data = bytearray(f.read())
    done = patch(data)
    tmp = out + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(data)
    shutil.copystat(src, tmp)
    os.replace(tmp, out)
    print(f'wrote {os.path.basename(out)} from {os.path.basename(src)}, '
          f'{done} of {len(state(data))} part(s) patched')
    return 0


if __name__ == '__main__':
    sys.exit(main())
