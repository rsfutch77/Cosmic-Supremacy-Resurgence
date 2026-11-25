"""
test_heads.py: each staged page's own title and description
============================================================
    python site\\tests\\test_heads.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import heads                                                    # noqa: E402

PASS, FAIL = [], []


def check(name, got, want=True):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(f'  {"ok  " if ok else "FAIL"}  {name}')
    if not ok:
        print(f'          wanted {want!r}, got {got!r}')


def title(html):
    return re.search(r'<title>(.*?)</title>', html, re.S).group(1)


def description(html):
    m = re.search(r'<meta name="description" content="([^"]*)" />', html)
    return m.group(1) if m else None


SHARED = ("<meta name='description' content='Challenge your skills in planet "
          "management, diplomacy, ship design, and fleet tactics.' />")


def archive(heading=None, tab=None, text='The galaxy ran for eighty turns and '
            'ended with three empires standing.', head_title='Cosmic Supremacy'):
    tabs = (f"<ul id='tab'><li class=''><a href='/x'>Other</a></li>"
            f"<li class='' id='active'><a href='/y'>{tab}</a></li></ul>") if tab else ''
    cell = f"<td class='content_heading' width='100%'>\n\t{heading}\n</td>" if heading else ''
    return (f"<html><head><title>{head_title}</title>{SHARED}</head><body>"
            f"<div id='left_content'>{tabs}<table>{cell}<tr><td><p>{text}</p></td></tr>"
            f"</table></div><div id='right_content'>Game Preview Download</div>"
            f"</body></html>")


def wiki(body, head_title='Cosmic Supremacy Wiki &raquo; manual:combat'):
    return (f'<html><head><title>{head_title}</title></head><body>'
            f'<div class="dokuwiki"><div class="page">{body}'
            f'<div class="docInfo">x</div></div></div></body></html>')


def main():
    print('archive pages')
    out = heads.retitle(archive(heading='Galaxy Beta 5'), 'galaxy@galid=10.php.html')
    check('a galaxy page is titled by its heading', title(out),
          'Galaxy Beta 5 - Cosmic Supremacy')
    check('and described by its own text, replacing the shared description',
          description(out), 'Galaxy Beta 5: The galaxy ran for eighty turns and '
          'ended with three empires standing.')
    out = heads.retitle(archive(heading='Playing without the Installer',
                                tab='Client Software'), 'download_client.php.html')
    check('a page with a tab is titled by the tab, not its first section',
          title(out), 'Client Software - Cosmic Supremacy')
    out = heads.retitle(archive(tab='Hall Of Fame'),
                        'community_halloffame@action=0&page=4.php.html')
    check('a list page adds its page number to the title', title(out),
          'Hall Of Fame (page 4) - Cosmic Supremacy')
    check('    and to the description', description(out).startswith('Hall Of Fame (page 4): '),
          True)
    out = heads.retitle(archive(text='Error'), 'voting.php.html')
    check('an archived error page takes its name from NAMES', title(out),
          'Vote for the game - Cosmic Supremacy')
    check('    and a page with no text of its own describes itself by its name',
          description(out), 'Vote for the game, from the Cosmic Supremacy archive.')
    long = 'word ' * 80
    out = heads.retitle(archive(tab='News', text=long), 'game_news.php.html')
    check('a long description is cut at a word near 155 characters',
          (description(out).endswith('...'), len(description(out)) < 175), (True, True))
    own = archive(tab='News', head_title='Galaxy One opens')
    check('a title of the page\'s own is kept',
          title(heads.retitle(own, 'game_news.php.html')), 'Galaxy One opens')
    once = heads.retitle(archive(heading='Galaxy Beta 5'), 'galaxy@galid=10.php.html')
    check('running it twice changes nothing',
          heads.retitle(once, 'galaxy@galid=10.php.html'), once)

    print('wiki pages')
    nav = ('<h1>Combat Links</h1><div class="level1"><ul><li><a href="/a">Fleets</a>'
           '</li><li><a href="/b">Ships</a></li></ul></div>')
    box = ('<h1>Volume I: The Basics</h1><div class="level1"></div><h3>Part I</h3>'
           '<ul><li><a href="/c">Front Page</a></li><li><a href="/d">Customization</a>'
           '</li><li><a href="/e">Initial Concerns</a></li></ul>')
    out = heads.retitle(wiki(nav + '<h1>Combat</h1><p>Ships fight when two hostile '
                             'fleets meet in the same system.</p>'), 'wiki/manual/combat.html')
    check('a wiki page keeps its title, which is its own',
          title(out), 'Cosmic Supremacy Wiki &raquo; manual:combat')
    check('    and is described from its heading on, past a box of links',
          description(out), 'Combat: Ships fight when two hostile fleets meet in '
          'the same system.')
    out = heads.retitle(wiki(box + '<h2>Foreign Relations</h2><p>Perhaps better known '
                             'as diplomacy, this part of the game decides wars.</p>'),
                        'wiki/guides/nongolfguide/foreignrelations.html')
    check('a guide page whose own heading follows a navigation box is described from it',
          description(out).startswith('Foreign Relations: Perhaps better known'), True)
    out = heads.retitle(wiki('<h1>FAQ</h1><p>Answers to the questions asked most.</p>',
                             head_title='Cosmic Supremacy Wiki &raquo; faq'),
                        'wiki/faq@do=backlink.html')
    check('a wiki view other than the page adds the view to the title',
          title(out), 'Cosmic Supremacy Wiki » faq (backlinks)')

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
