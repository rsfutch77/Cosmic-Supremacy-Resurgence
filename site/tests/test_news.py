"""
test_news.py: the home page's news items
========================================
    python site\\tests\\test_news.py
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import news                                                     # noqa: E402

PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def write(folder, name, text):
    with open(os.path.join(folder, name), 'w', encoding='utf-8') as fh:
        fh.write(text)


def main():
    work = tempfile.mkdtemp()
    try:
        folder = os.path.join(work, 'news')
        os.makedirs(folder)
        write(folder, '2026-09-13-welcome.html', 'Welcome\n<p>first</p>\n')
        write(folder, '2026-11-20-a.html', 'Patch <b>day</b>\n<p>second</p>')
        write(folder, '2026-11-20-b.html', 'Later the same day\n<p>third</p>')
        write(folder, 'notes.txt', 'not an item')

        print('\nthe items')
        rows = news.items(folder)
        check('every dated file is an item, and nothing else', len(rows), 3)
        check('newest first, the later file first on the same day',
              [t for _d, t, _b in rows],
              ['Later the same day', 'Patch <b>day</b>', 'Welcome'])
        check('the date reads as the old site wrote it', rows[-1][0], '13.09.2026')
        html = news.render(rows)
        check('a title is text, so markup in it is escaped',
              'Patch &lt;b&gt;day&lt;/b&gt;' in html, True)
        check('a body is HTML, kept as written', '<p>second</p>' in html, True)

        print('\nthe page')
        page = os.path.join(work, 'home.html')
        write(work, 'home.html', '<table>\n' + news.MARKER + '\n</table>')
        check('the marker is replaced and the items counted',
              news.render_into(page, folder), 3)
        with open(page, encoding='utf-8') as fh:
            text = fh.read()
        check('no marker is left', news.MARKER in text, False)
        check('a page without the marker is left alone',
              news.render_into(page, folder), None)
        check('a missing folder renders no items', news.items(os.path.join(work, 'x')), [])
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
