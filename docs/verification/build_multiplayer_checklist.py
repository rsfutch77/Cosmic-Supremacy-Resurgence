"""
build_multiplayer_checklist.py: write Multiplayer_Checklist.md
    python docs/verification/build_multiplayer_checklist.py

**M items**, the multiplayer experience. Their sources may not be reproduced
(Xbox Requirements, Epic Games Store testing guide, Pinelle et al. 2009, Fair
Play Alliance, PEGI Online Safety Code), so each is written in this project's
words and cites the original by ID.

**GAG items**, accessibility: every Game Accessibility Guidelines entry,
reproduced (gameaccessibilityguidelines.com, "no restriction on use beyond
republishing under a different name"), each with one disposition:

    apply   a checkbox, with sub-items
    done    ticked, with what verified it
    limit   the original client cannot do it without patching the binary;
            listed so it is stated to players, not hidden
    na      not applicable, with the reason

GAG IDs are assigned by category, level and position: GAG-Mot-B1 is Motor,
Basic, first row. A guideline under several categories carries the same
decision in each.

Sources are read from tools/checklists/multiplayer/ (gitignored).
"""
import csv
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
GAG = os.path.join(REPO, 'tools', 'checklists', 'multiplayer', 'GAG-checklist.csv')
OUT = os.path.join(HERE, '2 - Multiplayer_Checklist.md')

CLIENT = 'The client is the original binary; its interface cannot change without patching it.'

# M items: (id, requirement in our words, sources, sub-items)
M = [
    ('Joining a galaxy', [
        ('M1', 'A player can find a galaxy, see what it is (turn length, turn number, how many players, whether it is open) and ask to join, from the launcher, without reading documentation first.',
         'Pinelle et al. 2009, heuristic 1 (simple session management) and 2 (flexible matchmaking); Xbox XR-064 (joinable sessions)',
         ['The Games tab lists every open galaxy with its turn length, turn, roster size and status.',
          'A closed galaxy is shown as closed and offers no Join.']),
        ('M2', 'A player who joins a galaxy already in progress is told what happens next and when: their request is waiting, it is decided at the next turn boundary, and when that is.',
         'Xbox XR-045 privilege 254 (join-in-progress sessions); Pinelle heuristic 9 (reduce game-based delays)',
         ['After Join, the row says the request is waiting and shows the time of the next boundary.',
          'Joining with a name the galaxy already has, or one the rules refuse, is answered at the button, before the request is filed.']),
        ('M3', 'A refused join says why, and what the player can do about it, and the launcher shows it without the player asking.',
         'Epic Games Store testing guide, Player Experience (inform the player how to resolve an issue)',
         ['A refused request shows the reason on the galaxy row and a way to try again.',
          'The reason never names another player\'s sign-in.']),
        ('M4', 'A newly seated player starts on a fair footing with the galaxy\'s other newcomers, and is told where their empire is.',
         'Pinelle heuristic 7 (protected training for beginners)',
         ['The welcome note names the system and planet.',
          'Newcomers are levelled to the same starting kit.',
          'A newcomer is not placed beside an established empire with no protection: decide whether a grace period applies.']),
    ]),
    ('Playing turns', [
        ('M5', 'Turn timing is never precise: a player can act at any time during a long turn, and the deadline is shown in their local time.',
         'Game Accessibility Guidelines, "Do not make precise timing essential to gameplay"; Pinelle heuristic 9',
         ['The launcher shows the next deadline as a local time and as time remaining.',
          'Submitting early, and resubmitting before the deadline, both work; the last submission counts.']),
        ('M6', 'A player knows, without opening the game, whether they have submitted this turn and whether the galaxy has moved on.',
         'Pinelle heuristic 5 (meaningful awareness information); Xbox XR-067, XR-070 (recently-played and friends presence, by analogy)',
         ['The galaxy row shows "submitted" or "not yet" for this turn, and who else has submitted.',
          'A new turn is noticed by the launcher without a restart.']),
        ('M7', 'A player who stops playing is warned before their empire is reclaimed, and a reclaimed player can come back.',
         'Fair Play Alliance, "Building a Penalty and Reporting System" (warnings before penalties; a path back)',
         ['A warning is shown on the turn the player is next served, once, before the reclaim.',
          'The number of missed turns before a reclaim is stated in the galaxy\'s rules or the join notice.',
          'A reclaimed player who joins again is seated as a newcomer, and is told so.']),
        ('M8', 'Every player can tell which turn they are looking at, and a stale turn is never presented as current.',
         'Pinelle heuristic 5',
         ['The launcher and the client agree on the turn number.',
          'A turn the launcher could not refresh is labelled with how old it is.']),
    ]),
    ('Connection and service loss', [
        ('M9', 'When the relay, Storage or the network is unreachable, the launcher says which, in words a player can act on, keeps any unsent orders, and retries on its own.',
         'Xbox XR-074 and test cases 074-01 (WAN disconnection), 074-07 (dynamic connectivity loss); Epic Games Store testing guide, Offline Mode (no misleading or uninformative messages; tell the player how to resolve it)',
         ['Unplug the network during a turn: the launcher says the network is down, not that the galaxy failed.',
          'Restore it: the launcher recovers and sends the orders without the player starting again.',
          'Stop the relay (emulator): the launcher says the service is unavailable.']),
        ('M10', 'When the referee is down or late, players are told the turn is late rather than left guessing, and nothing they submitted is lost.',
         'Xbox XR-074 test case 074-08 (pre-launch downtime), by analogy; Xbox XR-003 (multiplayer stable regardless of the number of players)',
         ['A turn more than one turn length overdue is shown as late on the galaxy row.',
          'Submissions made while the referee is down are counted when it returns.']),
        ('M11', 'A sleeping or hibernating PC, or one that was off for days, comes back to the right turn with its seat intact.',
         'Xbox XR-074 test cases 074-03, 074-04 (suspend, sleep and hibernate)',
         ['Sleep the PC through a turn boundary; on wake the launcher shows the new turn.',
          'A sign-in older than an hour still works: the ID token is renewed (Epic testing guide, token renewal after 60 minutes).']),
        ('M12', 'A launcher too old for the galaxy is told so, and how to update, and is not allowed to submit orders the referee would misread.',
         'Xbox XR-003',
         ['Below the minimum build, the launcher refuses to play and offers the update. Until the launcher updates itself (Reconstruction Report section 14), it links the release page.',
          'The seat survives the update: the updater leaves the `data` folder alone. Until then, the message says to keep `data`.']),
    ]),
    ('Identity and seats', [
        ('M13', 'A player\'s seat survives a reinstall, a new PC, or a lost data folder, by a recovery route that cannot be used to take someone else\'s seat.',
         'Xbox XR-013 (account linking and unlinking); Security_Checklist ASVS V6.4.4',
         ['The launcher tells a player what to back up to keep their seat.',
          'The operator procedure for moving a seat asks for evidence beyond the support code.']),
        ('M14', 'A player\'s name works everywhere it is shown: the launcher, the client, the operator page and the news.',
         'Epic Games Store testing guide (non-Latin display names); Security_Checklist ASVS V2.2.1',
         ['Names follow the rules in `turn_store.name_problem`, and the launcher explains them at the button.',
          'The launcher runs from a folder whose path has non-ASCII characters (`C:\\Users\\Zo\u00eb\\...`) and from a path with spaces.']),
        ('M15', 'No player can see another player\'s sign-in identifier, IP address, or any personal data beyond their chosen name.',
         'Xbox XR-014 (player data); PEGI Online Safety Code provision 1 (privacy)',
         ['Roster, notes and the archive show civ names only.']),
    ]),
    ('Talking to other players', [
        ('M16', 'Players can send each other messages, and the game gives them the tools to coordinate diplomacy.',
         'Pinelle heuristic 3 (appropriate communication tools) and 4 (support coordination); heuristic 8 (support social interaction)',
         ['Messages between players are delivered at the next turn boundary; test one each way.',
          'The launcher or the site tells players how messages work in a turn-based galaxy (they arrive with the turn).']),
        ('M17', 'A player can stop receiving messages from another player.',
         'Xbox XR-015, test cases 015-02 (muting) and 015-03 (blocked users)',
         ['Decide how: a block list kept by the referee that drops messages from a blocked civ, since the client has no block control.']),
        ('M18', 'Message text cannot harm the receiver: it is limited in length and characters, and a message that would crash a client is not delivered.',
         'Xbox XR-018 (text-string verification); Security_Checklist ASVS V1.3.10 and V2.2.1',
         ['As the security checklist items.']),
    ]),
    ('Reporting and enforcement', [
        ('M19', 'Published community guidelines say what is not allowed (harassment, hate, cheating through modified clients, abuse of the operator) and what happens when it occurs.',
         'PEGI Online Safety Code provision 5 (community standards); Fair Play Alliance, "Creating and Maintaining Community Guidelines"',
         ['A page on the site, linked from the launcher and the join notice.']),
        ('M20', 'A player can report another player, a message, or a suspected cheat, and receives an acknowledgement.',
         'Xbox XR-018 test case 018-01 (reporting); PEGI provision 3 (reporting); Fair Play Alliance (acknowledge every report)',
         ['A report route: from the launcher (with galaxy, turn and civ attached), or a form or address on the site.',
          'Every report is acknowledged, even when no action follows.']),
        ('M21', 'Reports are investigated against the archive, and penalties are proportionate, explained to the player, time-limited where possible, and repeat offences are tracked.',
         'Fair Play Alliance, "Building a Penalty and Reporting System" (investigation, penalty duration, path back to good standing, repeat offenders)',
         ['Penalties: warning; message block; seat reclaim; sign-in disabled (Security_Checklist ASVS V7.4.2).',
          'A record of penalties per sign-in, kept by the operator.']),
        ('M22', 'Content that breaks the guidelines can be removed.',
         'PEGI Online Safety Code provision 4 (removal of inappropriate content)',
         ['The operator can drop a message, and rename a civ whose name breaks the guidelines (`seat_tool.py` or a successor).']),
    ]),
    ('Age, ratings and stated policies', [
        ('M23', 'The privacy policy and the age the game is meant for are stated before a player joins.',
         'PEGI Online Safety Code provisions 1 (privacy policy) and 2 (age-rated content); see the privacy checklist',
         ['The join notice links the privacy page.',
          '[x] Age floor decided: 13 and up (8 October 2026).',
          'State it in the join notice and on the site.']),
        ('M24', 'If the game ever carries advertising or sponsorship, it is identified as such.',
         'PEGI Online Safety Code provision 6 (advertising policy)',
         ['None today; record that.']),
    ]),
]

# GAG decisions, by guideline text
NA = lambda why: ('na', why, [])
LIMIT = ('limit', CLIENT, [])


def LIMIT_PLUS(*subs):
    return ('limit', CLIENT, list(subs))


G = {
    'Allow controls to be remapped / reconfigured': LIMIT,
    'Ensure controls are as simple as possible, or provide a simpler alternative': LIMIT_PLUS('The launcher\'s controls are a few buttons: keep it so.'),
    'Ensure that all areas of the user interface can be accessed using the same input method as the gameplay': ('apply', None, ['The launcher is fully usable with the mouse alone, and with the keyboard alone (Tab, Enter, Space).']),
    'Include an option to adjust the sensitivity of controls': LIMIT,
    'Ensure interactive elements / virtual controls are large and well spaced, particularly on small or touch screens': ('apply', None, ['Launcher buttons are at least 24 by 24 pixels at 100% scaling and well spaced.', 'Client: LIMIT.']),
    'Support more than one input device': LIMIT,
    'Make interactive elements that require accuracy (eg. cursor/touch controlled menu options) stationary': LIMIT,
    'Include an option to adjust the game speed': ('apply', 'The turn length is the game speed.', ['Galaxies can run at different turn lengths (the operator sets it per galaxy), and each galaxy\'s turn length is shown before joining (M1).']),
    'Avoid repeated inputs (button-mashing/quick time events)': LIMIT,
    'If producing a PC game, support windowed mode for compatibility with overlaid virtual keyboards': ('apply', None, ['Check whether the client runs windowed; record the answer in the accessibility statement.']),
    'Avoid / provide alternatives to requiring buttons to be held down': LIMIT,
    'Allow interfaces to be rearranged': LIMIT,
    'Allow interfaces to be resized': LIMIT_PLUS('The launcher scales with Windows display scaling and its window can be resized.'),
    'Provide a macro system': NA('Not for a four-hour turn game.'),
    'Allow for varied body types in VR': NA('No VR.'),
    'Allow play in both portrait and landscape': NA('Desktop only.'),
    'Include a cool-down period (post acceptance delay) of 0.5 seconds between inputs': LIMIT,
    'Provide very simple control schemes that are compatible with assistive technology devices, such as switch or eye tracking': LIMIT,
    'Allow the game to be started without the need to navigate through multiple levels of menus': ('apply', None, ['From the launcher, one click on a galaxy row starts the game on that galaxy.']),
    'Use an easily readable default font size': ('apply', None, ['Launcher text is at least 9 pt at 100% scaling.', 'Client: LIMIT.']),
    'Use simple clear language': ('apply', None, ['Launcher messages, the join notice and the site use plain language; error messages say what to do.']),
    'Use simple clear text formatting': ('apply', 'As above, for the launcher and the site.', []),
    'Include tutorials': ('apply', None, ['The launcher offers the original Tutorial mode; check it starts and completes.', 'A short "how a multiplayer turn works" page, linked from the join notice.']),
    'Allow players to progress through text prompts at their own pace': ('apply', 'Turn-based with four-hour turns; no prompt times out.', []),
    'Avoid flickering images and repetitive patterns': ('apply', None, ['Check the client\'s battle and starfield effects and the site\'s animated images for flashing (website checklist WCAG 2.3.1).']),
    'Include contextual in-game help/guidance/tips': ('apply', None, ['The manual on the site is linked from the launcher.', 'Client: LIMIT.']),
    'Indicate / allow reminder of current objectives during gameplay': NA('A sandbox 4X with no scripted objectives.'),
    'Indicate / allow reminder of controls during gameplay': LIMIT_PLUS('The manual\'s controls page is linked from the launcher.'),
    'Include a means of practicing without failure, such as a practice level or sandbox mode': ('done', 'The launcher offers Tutorial, Demo and Single Player alongside Multiplayer (Singleplayer_UI_Checklist A1).', []),
    'Employ a simple, clear narrative structure': NA('No narrative.'),
    'If using a long overarching narrative, provide summaries of progress': ('apply', 'No narrative, but a long galaxy is a long story.', ['A turn summary for a returning player (what changed since they last played) is worth considering.']),
    'Ensure no essential information (especially instructions) is conveyed by text alone, reinforce with visuals and/or speech': LIMIT,
    'Give a clear indication that interactive elements are interactive': ('apply', None, ['Launcher buttons look like buttons and change on hover (Singleplayer_UI_Checklist A3).']),
    'Provide an option to turn off / hide background movement': LIMIT,
    'Support voice chat as well as text for multiplayer games': NA('Text only, by decision: voice adds moderation and privacy load out of proportion to a four-hour turn game.'),
    'Provide gameplay thumbnails with game saves': NA('Multiplayer saves live on the server.'),
    'Provide separate volume controls or mutes for effects, speech and background/music': LIMIT_PLUS('Check what the client\'s options offer and state it.'),
    'Ensure sound / music choices for each key objects / events are distinct from each other': LIMIT,
    'Ensure sound / music choices for key objects / events are distinct from each other': LIMIT,
    'Provide a choice of text colour, low/high contrast choice as a minimum': LIMIT,
    'Highlight important words': ('apply', None, ['Launcher and join notice: the deadline, the galaxy and the next action stand out.']),
    'Provide an option to disable blood and gore': NA('No blood or gore.'),
    'Provide pre-recorded voiceovers for all text, including menus and installers': LIMIT,
    'Avoid any sudden unexpected movement or events': LIMIT,
    'Allow all narrative and instructions to be replayed': ('apply', None, ['The Tutorial can be replayed from the launcher.']),
    'Use symbol-based chat (smileys etc)': LIMIT,
    'Provide an option to turn off / hide all non interactive elements': LIMIT,
    'Ensure no essential information is conveyed by a colour alone': ('apply', None, ['Launcher: status uses words as well as the coloured dot (Singleplayer_UI_Checklist A1).', 'Client: LIMIT (civ colours on the map).']),
    'If the game uses field of view (3D engine only), set an appropriate default for expected viewing environment': NA('2D map.'),
    'Avoid VR simulation sickness triggers': NA('No VR.'),
    'Provide high contrast between text/UI and background': ('apply', None, ['Launcher text contrast at least 4.5:1.', 'Client: LIMIT.']),
    'If the game uses field of view (3D engine only), allow a means for it to be adjusted': NA('2D map.'),
    'Avoid (or provide option to disable) any difference between controller movement and camera movement, such as weapon/walk bob': NA('No first-person camera.'),
    'Use surround sound': NA('Not relevant to a strategy map.'),
    'Provide an option to turn off / hide background animation': LIMIT,
    'Ensure screenreader support for mobile devices': NA('No mobile version.'),
    'Provide an option to adjust contrast': LIMIT,
    'Provide a choice of cursor / crosshair colours / designs': LIMIT,
    'Ensure manual / website are provided in a screenreader friendly format': ('apply', 'As the website checklist (WCAG 2.2 AA).', []),
    'Avoid placing essential temporary information outside the player\'s eye-line': LIMIT,
    'Allow the font size to be adjusted': LIMIT_PLUS('Launcher follows Windows text scaling.'),
    'Provide a pingable sonar-style audio map': NA('Not relevant to a strategy map.'),
    'Provide a voiced GPS': NA('Not relevant.'),
    'Allow easy orientation to / movement along compass points': NA('Not relevant.'),
    'Ensure screenreader support, including menus & installers': ('apply', None, ['Launcher: check what a screen reader (NVDA, Narrator) reads on the Games tab and the join flow, and record it.', 'There is no installer.', 'Client: LIMIT.']),
    'Use distinct sound / music design for all objects and events': LIMIT,
    'Simulate binaural recording': NA('Not relevant.'),
    'Provide an audio description track': NA('No video or cutscenes in the game.'),
    'Provide subtitles for all important speech': NA('The game has no speech.'),
    'Ensure no essential information is conveyed by sounds alone': ('apply', None, ['The launcher never signals a new turn or a refusal by sound alone.', 'Client: LIMIT.']),
    'If any subtitles / captions are used, present them in a clear, easy to read way': NA('No subtitles.'),
    'Keep background noise to minimum during speech': NA('No speech.'),
    'Provide subtitles for supplementary speech': NA('No speech.'),
    'Ensure subtitles/captions are or can be turned on before any sound is played': NA('No speech.'),
    'Provide captions or visuals for significant background sounds': LIMIT,
    'Provide a visual indication of who is currently speaking': NA('No voice chat.'),
    'Allow subtitle/caption presentation to be customised': NA('No subtitles.'),
    'Support text chat as well as voice for multiplayer': ('done', 'Communication is text only (in-game messages).', []),
    'Support text chat as well as voice for online multiplayer': ('done', 'Communication is text only (in-game messages).', []),
    'Provide visual means of communicating in multiplayer': ('done', 'In-game messages and diplomacy are visual.', []),
    'Ensure that all important supplementary information (eg. the direction you are being shot from) conveyed by audio is replicated visually': LIMIT,
    'Provide a stereo/mono toggle': LIMIT,
    'Ensure that subtitles/captions are cut down to and presented at an appropriate words-per-minute for the target age-group': NA('No subtitles.'),
    'Provide signing': NA('No video.'),
    'Ensure that speech input is not required, and included only as a supplementary / alternative input method': ('done', 'The game takes no speech input.', []),
    'Base speech recognition on individual words from a small vocabulary (eg. \u2018yes\u2019 \u2018no\u2019 \u2018open\u2019) instead of long phrases or multi-syllable words': NA('No speech input.'),
    'Base speech recognition on hitting a volume threshold (eg. 50%) instead of words': NA('No speech input.'),
    'Provide details of accessibility features on packaging and/or website': ('apply', None, ['An accessibility statement on the site: what the launcher supports, and the LIMIT items in this list as known limitations of the original client.']),
    'Provide details of accessibility features in-game': ('apply', None, ['The launcher links the accessibility statement.']),
    'Offer a wide choice of difficulty levels': NA('Multiplayer opponents are people. AI opponents (a planned feature) would bring difficulty levels.'),
    'Ensure that all settings are saved/remembered': ('apply', None, ['Launcher settings and the player identity survive a restart and an update (M12).']),
    'Solicit accessibility feedback': ('apply', None, ['The accessibility statement asks for feedback and gives a contact.']),
    'Allow difficulty level to be altered during gameplay, either through settings or adaptive difficulty': NA('As difficulty levels.'),
    'Include some people with impairments amongst play-testing participants': ('apply', None, ['Ask in the beta for testers who use assistive technology or have colour vision deficiency.']),
    'Offer a means to bypass gameplay elements that aren\u2019t part of the core mechanic, via settings or in-game skip option': LIMIT,
    'Include assist modes such as auto-aim and assisted steering': NA('No aiming or steering.'),
    'Provide a manual save feature': NA('Multiplayer state is saved by the referee every turn.'),
    'Provide an autosave feature': ('done', 'The referee saves every turn; the archive keeps each one.', []),
    'Allow a preference to be set for playing online multiplayer with/without others who are using accessibility features that could give a competitive advantage': NA('No such features.'),
    'Allow gameplay to be fine-tuned by exposing as many variables as possible': NA('Galaxy settings are the operator\'s.'),
    'Include every relevant category of impairment (motor, cognitive etc) amongst play-testing participants, in representative numbers based on age/demographic of target audience': ('apply', 'As the Intermediate play-testing guideline.', []),
    'Allow settings to be saved to different profiles, at either game or platform level': NA('One profile per install.'),
    'Realtime text <-> speech transcription': NA('No voice chat.'),
    'Ensure that multiple simultaneous actions (eg. click/drag or swipe) are not required, and included only as a supplementary / alternative input method': LIMIT_PLUS('The launcher needs no drag or chorded input.'),
    'Ensure that all key actions can be carried out by digital controls (pad / keys / presses), with more complex input (eg. analogue, speech, gesture) not required, and included only as supplementary / alternative input methods': ('done', 'Mouse and keyboard only; no analogue, speech or gesture input anywhere.', []),
    'Ensure that all key actions can be carried out by digital controls (pads / keys / presses), with more complex input (eg. analogue, gesture) not required, and included only as supplementary / alternative input methods': ('done', 'Mouse and keyboard only; no analogue or gesture input anywhere.', []),
    'Do not make precise timing essential to gameplay: offer alternatives, actions that can be carried out while paused, or a skip mechanism': ('apply', 'Four-hour turns, and a turn is resolved by the referee, not in real time.', ['As M5: the deadline is shown, and the last submission before it counts.']),
    'Avoid (or provide option to disable) any difference between controller movement and camera movement, such as weapon/walk bobbing or mouse smoothing': NA('No first-person camera.'),
    'Avoid placing essential temporary information outside the player’s eye-line': LIMIT,
    'Provide separate volume controls or mutes for effects, speech and background / music': LIMIT_PLUS('Check what the client\'s options offer and state it.'),
    'Allow a preference to be set for playing online multiplayer with players who will only play with / are willing to play without voice chat': NA('No voice chat.'),
    'Ensure that all important supplementary information (eg. the direction you are being shot from) conveyed by audio is replicated in text / visuals': LIMIT,
    'Include every relevant category of impairment (motor, cognitive etc) amongst play-testing participants, in representative numbers based on age / demographic of target audience': ('apply', 'As the Intermediate play-testing guideline (GAG-Gen-I2).', []),
}

CATS = {'Motor': 'Mot', 'Cognitive': 'Cog', 'Vision': 'Vis', 'Hearing': 'Hea',
        'Speech': 'Spe', 'General': 'Gen'}
LEVELS = {'Basic': 'B', 'Intermediate': 'I', 'Advanced': 'A'}
MARK = {'apply': '- [ ]', 'done': '- [x]', 'limit': '- LIMIT', 'na': '- N/A'}


def gag_rows():
    cat = level = None
    n = 0
    for row in csv.reader(open(GAG, encoding='utf-8')):
        text = (row[1] if len(row) > 1 else '').strip().replace(' – ', ': ')
        if not text or text.startswith('(') or text == 'Guideline':
            continue
        if text in CATS:
            cat, level = text, None
            continue
        if text in LEVELS:
            level, n = text, 0
            continue
        if text.startswith('\u00a9') or text.startswith('Last updated') or text.startswith('Provided freely'):
            continue
        if cat and level:
            n += 1
            yield f'GAG-{CATS[cat]}-{LEVELS[level]}{n}', cat, level, text


def render(head, d):
    if d is None:
        return [f'- [ ] {head}', '  - TO TAILOR']
    status, note, subs = d
    lines = [f'{MARK[status]} {head}']
    if note:
        lines.append(f'  - N/A: {note}' if status == 'na' else f'  - {note}')
    for s in subs:
        lines.append(f'  - [x] {s[4:]}' if s.startswith('[x] ') else f'  - [ ] {s}')
    return lines


def main():
    counts = {}
    gag, cat, level = [], None, None
    for gid, c, lv, text in gag_rows():
        if c != cat:
            cat = c
            gag += ['', f'### {c}']
        if (c, lv) != (cat, level) or lv != level:
            level = lv
            gag += ['', f'#### {c}, {lv}', '']
        d = G.get(text)
        k = d[0] if d else 'untailored'
        counts[k] = counts.get(k, 0) + 1
        gag += render(f'**{gid}** {text}', d)

    mitems = []
    for section, items in M:
        mitems += ['', f'### {section}', '']
        for mid, req, source, subs in items:
            mitems += [f'- [ ] **{mid}** {req}', f'  - Source: {source}.']
            mitems += [f'  - [x] {s[4:]}' if s.startswith('[x] ') else f'  - [ ] {s}' for s in subs]
    n_m = sum(len(i) for _, i in M)

    order = ['apply', 'done', 'limit', 'na', 'untailored']
    totals = ['| Source | apply | done | limit | N/A | to tailor | total |',
              '|---|---|---|---|---|---|---|',
              f'| M items | {n_m} | 0 | 0 | 0 | 0 | {n_m} |',
              '| GAG | ' + ' | '.join(str(counts.get(k, 0)) for k in order)
              + f' | {sum(counts.values())} |']

    head = [
        '# Multiplayer experience checklist',
        '',
        'Joining, playing turns, losing the connection, keeping a seat, talking to '
        'other players, reporting them, and accessibility. Rerun the items a '
        'change touches: the launcher, the relay, the referee, the join notice, '
        'the site\'s player-facing pages.',
        '',
        'Written for the published game. Unchecked items are the gap to it.',
        '',
        '**Sources.** The M items are written in this project\'s words and cite '
        'their sources by ID, because those sources may not be reproduced: Xbox '
        'Requirements for Xbox Games v16.4 (Microsoft, September 2026), Epic Games '
        'Store testing guide, Pinelle, Wong, Stach, Gutwin, "Usability Heuristics '
        'for Networked Multiplayer Games" (ACM GROUP 2009), Fair Play Alliance and '
        'ADL, "Disruption and Harms in Online Gaming Framework" and its companion '
        'guides (December 2020), and the PEGI Online Safety Code. The GAG items '
        'reproduce the Game Accessibility Guidelines checklist '
        '(gameaccessibilityguidelines.com, spreadsheet last updated 20 September '
        '2017), which places no restriction on use beyond republishing it under '
        'another name. See `Checklist_Sources.md` for the comparison.',
        '',
        'Generated by `build_multiplayer_checklist.py` beside this file.',
        '',
        '## How to read an item',
        '',
        '- `- [ ]` applies and is not yet verified.',
        '- `- [x]` verified, with what verified it.',
        '- `- LIMIT` the original client cannot meet it without patching the '
        'binary. These are not failures to hide: they go into the accessibility '
        'statement (GAG-Gen-B1) so players know before they start.',
        '- `- N/A` does not apply, with the reason.',
        '',
        '**What no source covered.** No published checklist addresses a '
        'persistent galaxy that players join mid-game, abandoned empires, or '
        'recovering a seat after a reinstall. M2, M4, M7, M13 and M10 are written '
        'from this project\'s plan items for that reason.',
        '',
        '## Totals',
        '',
    ] + totals + ['', '## Multiplayer experience (M)'] + mitems + \
        ['', '## Accessibility (Game Accessibility Guidelines)'] + gag
    with open(OUT, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(head) + '\n')
    print(f'wrote {OUT}')
    for line in totals[2:]:
        print(line)


if __name__ == '__main__':
    main()
