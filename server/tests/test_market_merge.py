"""
test_market_merge.py: trades with the resource market are carried
=================================================================
    server\\.venv\\Scripts\\python.exe server\\tests\\test_market_merge.py

A civ's sales and purchases on the Ship-Resources screen move `GLOB > RSMA`,
the units the market holds. The referee reads each count's move as the civ's
net trade, prices it from the market as served, and moves the credits, stock
and count itself.

The quote is pinned to engine measurements: 600 metal sold for 585, 100
deuterium bought for 1,200 with the next quote 1,208 (STRATEGY.md, market row),
and trait 42 at 25 giving sale prices 125/188/250/375/1,125 and a metal
purchase of 600 (memory report, the resource market). The merges run on
`client/SinglePlayerGalaxy.dat`: two civs, 200 credits and 630 metal each.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for d in (os.path.join(ROOT, 'server'),
          os.path.join(ROOT, 'server', 'dev_tools')):
    if d not in sys.path:
        sys.path.insert(0, d)

import inject_civ as icv                                        # noqa: E402
import merge_orders as mo                                       # noqa: E402

GALAXY = os.path.join(ROOT, 'client', 'SinglePlayerGalaxy.dat')
METAL, DEUTERIUM, RADIOACTIVES = 0, 1, 2
PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def quiet(_msg=''):
    pass


def traded(blob, moves):
    """The blob with the market's counts moved as a client's trades move them."""
    counts = mo.market_of(blob)
    for kind, units in moves.items():
        counts[kind] += units
    return mo.set_market(blob, counts)


def merged(served, submissions):
    notes = {}
    return mo.merge(served, submissions, log=quiet, notes=notes), notes


def main():
    served = open(GALAXY, 'rb').read()
    civs = {o['name']: o['oid'] for o in icv.owner_records(served)}
    me, them = civs['DemoPlayer'], civs['BadGuy']

    print('the market is read from GLOB')
    check('an untouched galaxy holds nothing', mo.market_of(served), [0] * 5)
    check('a count written comes back',
          mo.market_of(mo.set_market(served, [1, -2, 3, -4, 5])),
          [1, -2, 3, -4, 5])

    print('the quote matches the engine')
    check('600 metal sell for 585', mo.market_total(METAL, 600, 0, 0), 585)
    check('100 deuterium cost 1,200',
          mo.market_total(DEUTERIUM, 100, 0, 0, buying=True), 1200)
    check('and the next 100 cost 1,208',
          mo.market_total(DEUTERIUM, 100, -100, 0, buying=True), 1208)
    check('trait 42 at 25 raises the sale prices',
          [mo.market_total(k, 100, 0, 25) for k in range(5)],
          [125, 188, 250, 375, 1125])
    check('and lowers a metal purchase to 600',
          mo.market_total(METAL, 100, 0, 25, buying=True), 600)
    check('a sale never goes below a third of the base',
          mo.market_price(METAL, 10 ** 9), 33)
    check('Doctrine: Trade grants trait 42 its 50',
          (mo.market_trait([0, 1, 49]), mo.market_trait([0, 1])), (50, 0))

    print('a sale is carried and priced by the referee')
    sub = traded(served, {METAL: 600})
    out, notes = merged(served, [('DemoPlayer', sub)])
    check('the credits rise by the quote', mo.credits_of(out, me), 200 + 585)
    check('the stock falls by the units sold',
          mo.resources_of(out, me)[METAL], 30)
    check('the market holds them', mo.market_of(out), [600, 0, 0, 0, 0])
    check('nothing is refused', notes, {})

    print('submitted credits and stocks are not read')
    sub = mo.set_credits(traded(served, {METAL: 600}), me, 99999)
    sub = mo.set_resources(sub, me, [9999] * 5)
    out, _notes = merged(served, [('DemoPlayer', sub)])
    check('the referee sets them from the state served',
          (mo.credits_of(out, me), mo.resources_of(out, me)),
          (785, [30, 350, 260, 0, 0]))

    print('sales pay for purchases in the same submission')
    out, notes = merged(served, [('DemoPlayer', traded(
        served, {METAL: 600, RADIOACTIVES: -40}))])
    check('40 radioactives cost 640 out of 785',
          (mo.credits_of(out, me), mo.resources_of(out, me)[RADIOACTIVES]),
          (785 - 640, 300))
    check('the market gave them up', mo.market_of(out), [600, 0, -40, 0, 0])

    print('a trade the civ cannot make is refused')
    out, notes = merged(served, [('DemoPlayer', traded(served, {METAL: 700}))])
    check('selling more than it holds',
          (mo.credits_of(out, me), mo.market_of(out),
           any('700 metal DROPPED, 630 held' in n
               for n in notes.get('DemoPlayer', []))),
          (200, [0] * 5, True))
    out, notes = merged(served, [('DemoPlayer', traded(
        served, {DEUTERIUM: -100}))])
    check('buying what it cannot pay for',
          (mo.credits_of(out, me), mo.market_of(out),
           any('1200 credits needed and 200 held' in n
               for n in notes.get('DemoPlayer', []))),
          (200, [0] * 5, True))

    print('every civ trades at the market as served')
    out, _notes = merged(served, [
        ('DemoPlayer', traded(served, {METAL: 600})),
        ('BadGuy', traded(served, {METAL: 600}))])
    check('both are paid the served price',
          (mo.credits_of(out, me), mo.credits_of(out, them)), (785, 785))
    check('and the market holds both sales',
          mo.market_of(out), [1200, 0, 0, 0, 0])

    print('a purchase is carried')
    rich = mo.set_credits(served, me, 5000)
    out, _notes = merged(rich, [('DemoPlayer', traded(
        rich, {DEUTERIUM: -100}))])
    check('a civ without Doctrine: Trade pays the base price',
          mo.credits_of(out, me), 5000 - 1200)

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
