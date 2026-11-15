"""
test_online_commands.py: the original client's server commands
================================================================
    python server\\tests\\test_online_commands.py

`online_commands.py` reads the commands out of the tracked clients. Each is a
routine that sends one section and waits for the server's answer, or applies
the action locally when there is no server. The table is pinned here so a
reading of a single routine elsewhere in the reports can cite it.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CLIENT = os.path.join(ROOT, 'client')
sys.path.insert(0, os.path.join(CLIENT, 'dev_tools'))
sys.path.insert(0, os.path.join(ROOT, 'server', 'dev_tools'))

import online_commands as oc                                    # noqa: E402
import merge_orders                                             # noqa: E402

PASS, FAIL = [], []

# Routines read by hand from the disassembly (Report §12, "The original
# client's online path").
KNOWN = {
    'STCO': 0x0056EBC0,    # a ship's course
    'CHCZ': 0x00574180,    # ChangeCitizenJobs (Report §13)
    'CUCV': 0x00571730,    # Customize Your Civilization's OK
    'SDNW': 0x00575970,    # a message or note
    'CNCA': 0x00579230,    # civ name, checkbox and arms
    'ALIV': 0x00573EA0,    # the dispatcher's keep-alive when idle
    'SLFC': 0x0056EDB0,    # PlanetProperties::SellFacility offline
    'TGSD': 0x0056F000,    # toggles ShipDesign+0x114 offline
    'STEL': 0x00571B70,    # a planet's ENLI, online only
    'SVUS': 0x005718C0,    # USSE, Owner+0x2cc
}
SERVER = ['TICK', 'RSMA', 'ALIV', 'NEWS', 'SAVE', 'TIME', 'TRKP', 'UPDT']


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def main():
    tables = {}
    for exe in ('CosmicSupremacy.exe', 'CosmicSupremacy_TestBed.exe',
                'CosmicSupremacy_Resurgence.exe'):
        with open(os.path.join(CLIENT, exe), 'rb') as f:
            data = f.read()
        tables[exe] = (oc.commands(data), oc.server_messages(data))

    cmds, pushed = tables['CosmicSupremacy_TestBed.exe']
    by_tag = {c['tag']: c for c in cmds}
    print('the commands')
    check('56 of them', len(cmds), 56)
    check('each tag once', len(by_tag), len(cmds))
    for tag, routine in KNOWN.items():
        check(f'{tag} is sent from 0x{routine:08X}',
              by_tag.get(tag, {}).get('routine'), routine)
    check('every section is version 0 but NWTR',
          sorted(c['tag'] for c in cmds if c['version'] != 0), ['NWTR'])
    check('STCO is sent at 0x0056EC67', by_tag['STCO']['site'], 0x0056EC67)

    print('\nthe merge names every command')
    check('merge_orders.COMMANDS holds the 56 tags',
          sorted(merge_orders.COMMANDS), sorted(by_tag))
    doc = merge_orders.__doc__
    for tag, (rule, _what) in sorted(merge_orders.COMMANDS.items()):
        if rule:
            head = doc.find(f'**{rule}** (')
            check(f'{tag} is cited by the rule {rule!r}',
                  head >= 0 and f'`{tag}`' in doc[head:doc.find('\n\n', head)])

    print('\nthe messages the server sends')
    check('eight, in the order the dispatcher tests them', pushed, SERVER)

    print('\nthe three tracked clients agree')
    first = tables['CosmicSupremacy_TestBed.exe']
    for exe, table in tables.items():
        check(exe, table, first)

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
