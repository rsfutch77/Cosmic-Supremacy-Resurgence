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
          patches.pausable_video(patches.PREVIEW_VIDEO) in out, True)
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

    print('the Game Preview can be paused (WCAG 2.2.2)')
    video = "<video width='180' height='132' autoplay loop >"
    check('an autoplaying video gets controls and is muted',
          patches.pausable_video(video),
          "<video width='180' height='132' autoplay loop controls muted>")
    check('    on the archive pages too, after the Flash patch',
          "autoplay loop controls muted>" in patches.apply_all(ARCHIVED)[0], True)
    check('    and a second run changes nothing',
          patches.pausable_video(patches.pausable_video(video)), patches.pausable_video(video))

    print('the main landmark (WCAG 1.3.1)')
    check('the main column is marked',
          patches.mark_main("<body><div id='left_content'>x</div>"),
          "<body><div id='left_content' role='main'>x</div>")
    check('    else the wiki page div, as on the 404 page',
          patches.mark_main('<div class="page">x</div>'), '<div class="page" role="main">x</div>')
    once = patches.mark_main("<div id='left_content'>x</div><div class=\"page\">y</div>")
    check('    once only, and a second run changes nothing',
          (once.count('role='), patches.mark_main(once)), (1, once))
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
