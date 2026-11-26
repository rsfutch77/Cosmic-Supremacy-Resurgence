"""
test_patches.py: site-wide patches with a check of their own
============================================================
    python site\\tests\\test_patches.py
"""
import os
import re
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

    print('text alternatives (WCAG 1.1.1)')
    check('a score graph says what it is',
          patches.describe_images("<img border='0' src='/scoregraphs/scoregraph_374.png'>"),
          "<img border='0' src='/scoregraphs/scoregraph_374.png' alt=\"Graph of the players' scores by turn\">")
    check('a screenshot takes its caption',
          patches.describe_images("<img border='0' src='/images/screen4.png'><br>\n The Diplomacy/Treaties Screen: lists"),
          "<img border='0' src='/images/screen4.png' alt=\"The Diplomacy/Treaties Screen\"><br>\n The Diplomacy/Treaties Screen: lists")
    check('a linked logo is named',
          patches.describe_images("<a href='/downloads/CS_Logo_60x50.jpg'><img border='0' src='/downloads/CS_Logo_60x50.jpg'></a>"),
          "<a href='/downloads/CS_Logo_60x50.jpg'><img border='0' src='/downloads/CS_Logo_60x50.jpg' alt=\"Cosmic Supremacy logo\"></a>")
    news = '<img width="200" height="200" src="/images/News2.png" />'
    check('a news illustration is decoration',
          patches.describe_images(news), '<img width="200" height="200" src="/images/News2.png" alt="" />')
    check('    and an image with alt is left alone',
          patches.describe_images(patches.describe_images(news)), patches.describe_images(news))

    print('score table headings (WCAG 1.3.1)')
    scores = ("<div align='center'><table id='list'<tr><td align='right'><b>#</b></td><td width='20'></td>"
              "<td><b>Playername</b></td><td align='center'><b>Fame<br>Earned</b></td></tr>"
              "<tr><td align='right'><b>1</b></td></tr>")
    out = patches.label_score_tables(scores)
    check('the first row is column headings, and the table tag closes',
          out, "<div align='center'><table id='list'><tr><th scope='col' align='right'>#</th><td width='20'></td>"
               "<th scope='col' align='left'>Playername</th><th scope='col' align='center'>Fame<br>Earned</th></tr>"
               "<tr><td align='right'><b>1</b></td></tr>")
    check('    and a second run changes nothing', patches.label_score_tables(out), out)
    grid = "<table id='list' width='100%'><tr align=center><td>"
    check('the galaxy history grid is layout',
          patches.mark_galaxy_grid(grid), "<table id='list' width='100%' role='presentation'><tr align=center><td>")
    check('    once', patches.mark_galaxy_grid(patches.mark_galaxy_grid(grid)), patches.mark_galaxy_grid(grid))

    print('the accessibility stylesheet, the viewport and target size (1.4.10, 2.5.8)')
    head = "<head><link href='/css/page.css' rel='stylesheet' type='text/css' />\n</head><body>"
    out = patches.apply_all(head)[0]
    check('the stylesheet comes after the archive\'s, with the viewport',
          out.index('page.css') < out.index('viewport') < out.index('access.css') < out.index('</head>'), True)
    check('    and a second run adds neither again', patches.apply_all(out)[0], out)
    css = os.path.join(os.path.dirname(HERE), 'overrides', 'css', 'access.css')
    check('    and the stylesheet is in the overrides', os.path.isfile(css), True)
    top = ('<a class="nolink" href="#dokuwiki__top"><input type="button" class="button" '
           'value="Back to top" onclick="window.scrollTo(0, 0)" title="Back to top" /></a>&nbsp;')
    check('the back-to-top button becomes one link',
          patches.plain_back_to_top(top), patches.BACK_TO_TOP + '&nbsp;')
    art = ('<img src="/wiki/_media/manual/karei-s-art.jpg" alt="" border="0" width="640" height="480" '
           'class="resizeImage" />')
    sized = patches.size_front_art(art)
    check('the front page art offers three sizes', len(re.findall(r'\.jpg \d+w', sized)), 3)
    check('the front page art keeps its drawn size', 'width="640" height="480" class="resizeImage"' in sized)
    check('the art elsewhere keeps its full size',
          patches.size_front_art('<img src="/wiki/_media/manual/karei-s-art.jpg" class="media" />'),
          '<img src="/wiki/_media/manual/karei-s-art.jpg" class="media" />')
    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
