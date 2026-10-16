"""
ops_sources.py: read the operations, release and privacy sources into items
============================================================================
Used by build_ops_checklist.py. Each reader returns (id, section, text) for
every item in one source, in source order, so the checklist can show every item
whether or not it applies. Sources are in tools/checklists/ops_release_privacy/
(gitignored); see SOURCES.txt there for provenance and licence.
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(REPO, 'tools', 'checklists', 'ops_release_privacy')


def _lines(name):
    return open(os.path.join(SRC, name), encoding='utf-8').read().splitlines()


def firebase_launch():
    """Firebase launch checklist: every bullet, by section. IDs FB-<section>-<n>."""
    out, section, short, n, take = [], None, None, 0, False
    started = False
    for ln in _lines('firebase_launch_checklist.md'):
        t = ln.strip()
        if t.startswith('## General best practices'):
            started = True
        if not started:
            continue
        m = re.match(r'^(#{2,3})\s+(.*)$', t)
        if m:
            name = m.group(2).strip()
            if not name:
                break
            section, n, take = name, 0, False
            short = ''.join(w[0] for w in re.findall(r'[A-Za-z]+', name))[:4].upper()
            continue
        if t == '-':
            take = True
            continue
        if t.startswith('- ') and section:
            n += 1
            out.append((f'FB-{short}-{n}', section, t[2:].strip()))
            take = False
            continue
        if t.startswith('#'):
            continue
        if take and t and section:
            n += 1
            out.append((f'FB-{short}-{n}', section, t))
            take = False
    return out


def ico_aadc():
    """ICO Age appropriate design code: the 15 standards. IDs AADC-1 to AADC-15."""
    out = []
    for ln in _lines('ico_aadc_code_standards_summary.md'):
        m = re.match(r'^- ([A-Z][^:]{3,60}): (.+)$', ln.strip())
        if m:
            out.append((f'AADC-{len(out) + 1}', m.group(1), m.group(2)))
    return out


def ico_games_tips():
    """ICO top tips for games designers. IDs TIP-<section>-<n>; further reading is skipped."""
    out, section, n, s_i, skip = [], None, 0, 0, False
    for ln in _lines('ico_top_tips_for_games_designers_childrens_code.md'):
        t = ln.strip()
        if t.startswith('## '):
            section, n, skip = t[3:].strip(), 0, False
            s_i += 1
            continue
        if t.startswith('#### Further reading'):
            skip = True
            continue
        if section and not skip and t.startswith('- '):
            text = t[2:].strip()
            if text.startswith('We have'):
                continue
            n += 1
            out.append((f'TIP-{s_i}.{n}', section, text))
    return out


def ico_checklist(name, prefix):
    """An ICO self-assessment checklist: each 'Your business ...' statement."""
    out = []
    for ln in _lines(name):
        t = ' '.join(ln.split())
        t = re.sub(r'^.*?Not applicable\s+', '', t)
        m = re.match(r'^(.*?)\s*\(optional\)', t)
        if m and (m.group(1).startswith('Your business') or m.group(1).startswith('If ')):
            out.append((f'{prefix}-{len(out) + 1}', name, m.group(1)))
    return out


def ftc_coppa():
    """FTC COPPA six-step plan: the six steps. IDs COPPA-1 to COPPA-6."""
    out = []
    for ln in _lines('ftc_coppa_six_step_compliance_plan.md'):
        m = re.match(r'^## Step (\d): (.+)$', ln.strip())
        if m:
            out.append((f'COPPA-{m.group(1)}', 'Six-step compliance plan', m.group(2).strip()))
    return out


def tuf_attacks():
    """The Update Framework: the attacks a software updater must withstand. IDs TUF-1 to TUF-10."""
    out, cur = [], None
    on = False
    for ln in _lines('tuf-spec.md'):
        t = ln.strip()
        if t.startswith('### Goals to protect against specific attacks'):
            on = True
            continue
        if on and t.startswith('###'):
            break
        if not on:
            continue
        m = re.match(r'^\+ \*\*(.+?)\*\*\s*(.*)$', t)
        if m:
            cur = [f'TUF-{len(out) + 1}', m.group(1).rstrip('.'), m.group(2)]
            out.append(cur)
        elif cur and t:
            cur[2] += ' ' + t
    return [tuple(x) for x in out]


def smartscreen():
    """Microsoft, minimising SmartScreen warnings in practice. IDs SS-1 to SS-n."""
    out, on = [], False
    for ln in _lines('ms_smartscreen-reputation.md'):
        t = ln.strip()
        if t.startswith('## Minimizing SmartScreen warnings'):
            on = True
            continue
        if on and t.startswith('## '):
            break
        if on and t.startswith('- '):
            text = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', t[2:]).replace('**', '')
            # Microsoft's em-dashes become colons; the house style uses none.
            text = text.replace(' \u2014 ', ': ')
            out.append((f'SS-{len(out) + 1}', 'Minimizing SmartScreen warnings in practice', text))
    return out


if __name__ == '__main__':
    for reader in (firebase_launch, ico_aadc, ico_games_tips, ftc_coppa, tuf_attacks, smartscreen):
        items = reader()
        print(f'== {reader.__name__}: {len(items)}')
        for i in items:
            print(f'  {i[0]:12} {i[1][:28]:28} {i[2][:95]}')
    for n, p in (('ico_controllers_checklist.md', 'ICO-C'), ('ico_information_security_checklist.md', 'ICO-S')):
        items = ico_checklist(n, p)
        print(f'== {p}: {len(items)}')
        for i in items:
            print(f'  {i[0]:8} {i[2][:120]}')
