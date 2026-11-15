"""
test_tables.py: sim/tables.py agrees with the stat tables document
==================================================================
    server\\.venv\\Scripts\\python.exe sim\\tests\\test_tables.py

The component and facility tables in docs/CosmicSupremacy_Stat_Tables.md were
read out of the client. sim/tables.py carries them as data; this re-reads the
document and compares every row.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from sim import tables                                          # noqa: E402

DOC = os.path.join(ROOT, 'docs', 'CosmicSupremacy_Stat_Tables.md')
PASS, FAIL = [], []
RESOURCE_COLUMNS = ('Metal', 'Deut', 'Radio', 'Cryst', 'Exot')
STAT_COLUMN = {'Engines': 'Thrust', 'Scanners': 'Range', 'Shields': 'Strength'}


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def rows_after(text, heading):
    """[{column: cell}] of the first table at or after a line starting
    `heading`."""
    lines = text[text.index(heading):].splitlines()
    if not lines[0].startswith('|'):
        lines = lines[1:]
    rows = []
    for line in lines:
        if line.startswith('|'):
            rows.append([c.strip() for c in line.strip('|').split('|')])
        elif rows:
            break
    head = rows[0]
    return [dict(zip(head, r)) for r in rows[2:]]


def num(cell):
    return 0 if cell in ('-', '') else int(cell)


def main():
    text = open(DOC, encoding='utf-8').read()
    for title, table in (('Engines', tables.ENGINES), ('Weapons', tables.WEAPONS),
                         ('Chassis', tables.CHASSIS), ('Modules', tables.MODULES),
                         ('Scanners', tables.SCANNERS), ('Shields', tables.SHIELDS)):
        rows = rows_after(text, f'### {title}')
        check(f'{title}: row count', len(table), len(rows))
        for i, (row, part) in enumerate(zip(rows, table)):
            want = (row['Name'], num(row['Units']), num(row['Space']),
                    num(row['Cost']), num(row['Upkeep']),
                    tuple(num(row[c]) for c in RESOURCE_COLUMNS))
            got = (part.name, part.units, part.space, part.cost, part.upkeep,
                   part.resources)
            check(f'{title} {i} {part.name}', got, want)
            if title in STAT_COLUMN:
                check(f'{title} {i} stat', part.stat, num(row[STAT_COLUMN[title]]))
            if title == 'Weapons':
                check(f'{title} {i} firepower', part.fp,
                      (num(row['FP light']), num(row['FP heavy']),
                       num(row['FP planet'])))
            if title == 'Chassis':
                check(f'{title} {i} crew', part.crew,
                      tuple(int(c) for c in row['Crew'].split('-')))
    rows = rows_after(text, '| Id | Facility |')
    check('facilities: row count', len(tables.FACILITIES), len(rows))
    for i, (row, f) in enumerate(zip(rows, tables.FACILITIES)):
        want = (row['Facility'], int(row['Unlocked by'].split()[0]),
                int(row['Space']), int(row['Cost']), int(row['Upkeep']),
                row['1/planet'] == 'yes')
        check(f'facility {i} {f.name}',
              (f.name, f.tech, f.space, f.cost, f.upkeep, f.once), want)
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
