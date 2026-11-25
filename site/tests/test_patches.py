"""
test_patches.py: site-wide patches with a check of their own
============================================================
    python site\\tests\\test_patches.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import patches                                                  # noqa: E402

PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


ARCHIVED = """<div class='section_content'>
	    <p> </p>
<embed id='preview' height='133' width='177' flashvars='' allowscriptaccess='always' allowfullscreen='false' wmode='opaque' quality='high' bgcolor='#242e38' name='preview' src='/images/preloadFLV.swf' type='application/x-shockwave-flash'/>

	</div>"""


def main():
    print('the Game Preview box (Website Checklist R5)')
    out, hit = patches.apply_all(ARCHIVED)
    check('the Flash embed is gone', ('preloadFLV' in out, '<embed' in out),
          (False, False))
    check('and the trailer plays as the override pages play it',
          patches.PREVIEW_VIDEO in out, True)
    check('the patch says so', 'play the Game Preview without Flash' in hit, True)
    override = os.path.join(os.path.dirname(HERE), 'overrides', 'game_news.php.html')
    with open(override, encoding='utf-8') as fh:
        page = fh.read()
    check('the markup is the override pages\' own',
          "<source type='video/mp4' src='/images/preview.mp4' ></source>" in page
          and "<video width='180' height='132' autoplay loop >" in page, True)
    check('a page with no embed is left alone',
          patches.replace_flash_preview(page), page)

    print('the page\'s language (Lighthouse html-has-lang)')
    check('a bare html element says English',
          patches.declare_language('<html>\n<head>'), '<html lang="en">\n<head>')
    wiki = '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en" lang="en" dir="ltr">'
    check('one that states a language is left alone',
          patches.declare_language(wiki), wiki)
    check('and running it twice changes nothing',
          patches.declare_language(patches.declare_language('<html>')), '<html lang="en">')
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
