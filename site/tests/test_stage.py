"""
test_stage.py: link rewriting during staging
============================================
    python site\\tests\\test_stage.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import stage                                                    # noqa: E402

PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def main():
    print('a link to a staged page keeps its anchor')
    staged = {'support_terms.php.html', 'wiki/guides/customs.html'}
    check('the footer\'s Privacy link',
          stage.rewrite_links('<a href="/support_terms.php.html#privacy">', 'wiki/manual/x.html', staged),
          ('<a href="/support_terms.php.html#privacy">', 1))
    check('a relative wiki link to a section',
          stage.rewrite_links("<a href='customs.html#civilization-traits'>", 'wiki/guides/x.html', staged),
          ("<a href='/wiki/guides/customs.html#civilization-traits'>", 1))
    check('an in-page anchor is left alone',
          stage.rewrite_links('<a href="#top">', 'wiki/guides/x.html', staged), ('<a href="#top">', 0))
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
