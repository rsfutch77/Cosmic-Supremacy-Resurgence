"""
test_battle_reports.py: an ally's copy of a battle report names no design
=========================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_battle_reports.py

`fixtures/diplomacy/cs3` is turn 181 of a Single Player session where GoodGuy
fought Ceti at Ceti's homeworld while allied to BadGuy, sharing battle reports.
GoodGuy and BadGuy hold items naming the same strings, so BadGuy reads
GoodGuy's design names `f1` and `b1`; Ceti's own copy shows them as
`Shuttle-Class`.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools')):
    if d not in sys.path:
        sys.path.insert(0, d)

import save_parser as sp                                        # noqa: E402
import news                                                     # noqa: E402
import battle_reports as br                                     # noqa: E402

FIX = os.path.join(HERE, 'fixtures', 'diplomacy', 'cs3.b64')
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def report(blob, civ):
    """(item, title, body) of a civ's one battle report."""
    strings = news.read_table(blob)[1]
    item = next(i for i in news.items_of(blob, civ) if i['type'] == br.BATTLE)
    return (item, strings.get(item['title_id'], item['title']),
            strings.get(item['body_id'], item['body']))


def labels(body):
    return {m.group(2).rstrip() for m in br.LABEL.finditer(body)}


def main():
    import inject_civ as icv
    blob = sp.load_any(FIX)
    owners = {o['name']: o['oid'] for o in icv.owner_records(blob)}
    good, bad, ceti = owners['GoodGuy'], owners['BadGuy'], owners['Ceti']

    print('the capture')
    g_item, g_title, g_body = report(blob, good)
    b_item, _bt, b_body = report(blob, bad)
    _ci, _ct, c_body = report(blob, ceti)
    check("CONTROL: BadGuy's item names GoodGuy's strings",
          (b_item['title_id'], b_item['body_id']),
          (g_item['title_id'], g_item['body_id']))
    check("CONTROL: so BadGuy reads GoodGuy's designs",
          {b'f1', b'b1'} <= labels(b_body))
    check("CONTROL: Ceti's copy shows them by class",
          b'Shuttle-Class' in labels(c_body) and not {b'f1', b'b1'} & labels(c_body))
    check('the title names who fought', br.parties(g_title),
          [b'GoodGuy', b'Ceti'])
    check("GoodGuy's designs are Shuttles", {k: v for k, v in
          br.design_classes(blob)[good].items() if k in (b'f1', b'b1')},
          {b'f1': b'Shuttle-Class', b'b1': b'Shuttle-Class'})

    print('\nafter for_outsiders')
    lines = []
    out = br.for_outsiders(blob, log=lines.append)
    o_item, o_title, o_body = report(out, bad)
    check("BadGuy's copy no longer names GoodGuy's designs",
          {b'f1', b'b1'} & labels(o_body), set())
    check('and shows them by class', b'Shuttle-Class' in labels(o_body))
    check('its title is the same string', o_item['title_id'],
          g_item['title_id'])
    check('its body is its own', o_item['body_id'] != g_item['body_id'])
    check("only the labels changed",
          br.LABEL.sub(lambda m: m.group(1), o_body),
          br.LABEL.sub(lambda m: m.group(1), g_body))
    check("GoodGuy's own report is untouched", report(out, good)[1:],
          (g_title, g_body))
    check("and so is Ceti's", report(out, ceti)[2], c_body)
    check('the log says whose copy changed',
          any("BadGuy's copy of GoodGuy vs Ceti" in ln for ln in lines))
    check('every table string still carries its hash',
          news.write_table(out, *news.read_table(out)) == out)
    check('the galaxy parses', sp.parse_blob(out)[0].tag, b'SAVE')
    check('a second pass changes nothing', br.for_outsiders(out,
          log=lambda *a: None), out)

    print('\na title that names the empire, not the player')
    import merge_orders as mo
    _n, reveal, arms = mo.civ_identity(blob, good)
    named = mo.set_civ_identity(blob, good, b'Good Empire', reveal, arms)
    next_id, strings = news.read_table(named)
    strings[g_item['title_id']] = g_title.replace(b': GoodGuy vs', b': Good Empire vs')
    named = news.write_table(named, next_id, strings)
    check('CONTROL: the title now reads Good Empire vs Ceti',
          br.parties(report(named, good)[1]), [b'Good Empire', b'Ceti'])
    out2 = br.for_outsiders(named, log=lambda *a: None)
    check("GoodGuy's own report is still its own", report(out2, good)[2],
          g_body)
    check("BadGuy's copy is still rewritten",
          {b'f1', b'b1'} & labels(report(out2, bad)[2]), set())

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
