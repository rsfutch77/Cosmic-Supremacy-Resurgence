"""
build_ops_checklist.py , write Ops_Release_Privacy_Checklist.md
===============================================================
    python docs/verification/build_ops_checklist.py

Three parts in one checklist: running the service, releasing the launcher, and
handling players' data. Source items come from `ops_sources.py`, every item of
each source in order. Each gets one disposition:

    apply      a checkbox, with sub-items
    done       ticked, with what verified it
    cond       applies only if a pending decision goes that way (named)
    deviation  the design contradicts it on purpose, with the rationale
    na         not applicable, with the reason

A sub-item beginning "[x] " is ticked. Items written by this project, for what
no source covers, are OPS, REL and PRIV items and say where they come from.

**Legal content.** The privacy sources are regulators' guidance, quoted as
published. Nothing here is legal advice, and a point not checked against the
controlling text is marked "unverified".
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ops_sources as src

OUT = os.path.join(HERE, '4 - Ops_Release_Privacy_Checklist.md')

AGE = ('the age-floor decision (PRIV-1)')
UNDER13 = ('the game is judged directed to children under 13, or the operator keeps an under-13 player '
           'after learning of them, which the 13+ floor and the COPPA-1 procedure are meant to prevent')


def NA(why):
    return ('na', why, [])


def COND(when, subs=()):
    return ('cond', f'Applies if {when}.', list(subs))


NO_MOBILE = NA('No mobile or Apple app; the launcher is a Windows desktop program.')

# ── Firebase launch checklist ────────────────────────────────────────────────
FB = {
    'FB-GBPF-1': ('apply', None, ['[x] The relay and the stores run against the Local Emulator Suite in their tests (`functions/emulators.py`).', 'Every relay deploy is preceded by the relay and store test suites (`functions/deploy.py` could refuse to deploy when they fail).']),
    'FB-GBPF-2': ('apply', None, ['Evaluate App Check for the relay. A desktop app has no platform attestation provider; the custom-provider route needs a secret in the launcher, which a player can extract. Record the decision either way.']),
    'FB-GBPF-3': ('done', 'Reviewed in `docs/verification/private/Security_Checklist.md` (ASVS and GSF, with the Firebase security checklist as a supplement).', []),
    'FB-GBPF-4': NA('No Remote Config. Feature rollout is per galaxy and per build (the `min_build` gate).'),
    'FB-GBPF-5': NA('Crashlytics has no desktop Python SDK. Crash reports are the launcher logs sent on request, and the client\'s minidumps.'),
    'FB-KYPP-1': ('done', 'The project is on the Blaze plan (relay deploy requires it).', []),
    'FB-KYPP-2': ('apply', None, ['Confirm a budget with alert thresholds exists on the billing account, and who receives the alerts.']),
    'FB-KYPP-3': ('apply', 'Noted: alerts are not caps.', []),
    'FB-KYPP-4': ('apply', None, ['[x] The auto-stop-billing extension is installed (its functions are listed in the project, 7 October 2026).', 'Confirm its threshold and test what it disables: stopping billing takes the live website down with the relay.']),
    'FB-KYPP-5': ('apply', None, ['[x] The relay caps itself at 10 instances (`functions/main.py`).', 'Set a spend cap for Cloud Functions if the console offers one for this project.']),
    'FB-KYPP-6': ('apply', None, ['Record a measured day of usage against the estimates (plan items H12, H13).']),
    'FB-MSYF-1': ('deviation', 'One project, `cs-resurgence`, serves the live website and the galaxies, and tests run against emulators rather than a second project. A separate development project is the fix; until then deploys are scoped (`functions/deploy.py`, rules by hand).', []),
    'FB-MSYF-2': ('apply', 'As FB-MSYF-3 to FB-MSYF-10.', []),
    'FB-MSYF-3': ('done', 'A lien blocks deleting `cs-resurgence` (resourcemanager.projects.delete, origin operator, created 8 October 2026). Remove it deliberately before any deletion.', []),
    'FB-MSYF-4': ('apply', None, ['Tag `cs-resurgence` as Prod in the console.']),
    'FB-MSYF-5': NA('A personal project; a Google Cloud organization needs a domain the project does not have.'),
    'FB-MSYF-6': ('apply', None, ['Add a second Owner (a trusted person, or a second account of the operator\'s with its own MFA), so the project survives a lost account.']),
    'FB-MSYF-7': NA('One member.'),
    'FB-MSYF-8': ('apply', None, ['[x] Service accounts hold only what they use: `cs-relay` and `cs-referee` (Security_Checklist ASVS V13.2.2).', 'The default compute account still holds Editor.']),
    'FB-MSYF-9': ('apply', None, ['Confirm the operator receives billing, quota and security alerts at an address they read.']),
    'FB-MSYF-10': ('apply', None, ['Restrict the web API key (`server/fb_auth.py`) to Identity Toolkit and Secure Token, the only APIs the launcher calls with it.']),
    'FB-FAL-1': NA('No Firebase AI Logic.'),
    'FB-GA-1': NA('No analytics.'), 'FB-GA-2': NA('No analytics.'), 'FB-GA-3': NA('No analytics.'),
    'FB-GA-4': NA('No analytics.'), 'FB-GA-5': NO_MOBILE,
    'FB-AC-1': NO_MOBILE, 'FB-AC-2': ('apply', 'As FB-GBPF-2.', []),
    'FB-A-1': ('deviation', 'Anonymous authentication is the identity model on purpose: a player is an install, with no email collected. Its risks are recorded in the security checklist (ASVS V7.3.1, V2.4.1).', ['Disable every other provider that is enabled and unused.']),
    'FB-A-2': COND('Google sign-in is added (plan H4)'),
    'FB-A-3': NA('Authentication sends no email: anonymous sign-in only.'),
    'FB-A-4': NA('No SMS.'), 'FB-A-5': NO_MOBILE, 'FB-A-6': NO_MOBILE,
    'FB-A-7': ('apply', None, ['Prune the Authentication authorized domains to the ones in use (the site\'s domains, `localhost` only if a tool needs it).']),
    'FB-CF-1': ('done', 'Deny-all rules, deployed and read back on 7 October 2026 (Security_Checklist ASVS V8.3.1).', []),
    'FB-CF-2': NO_MOBILE,
    'FB-CM-1': NA('No Cloud Messaging.'), 'FB-CM-2': NA('No Cloud Messaging.'),
    'FB-CS-1': ('done', 'Deny-all rules, deployed and read back on 7 October 2026.', []),
}
for k in ('FB-C-1', 'FB-C-2', 'FB-C-3', 'FB-C-4', 'FB-C-5', 'FB-C-6', 'FB-C-7'):
    FB[k] = NA('No Crashlytics (FB-GBPF-5).')
FB.update({
    'FB-DL-1': NA('No Dynamic Links.'),
    'FB-FM-1': NA('No Firebase ML.'), 'FB-FM-2': NA('No Firebase ML.'),
    'FB-PM-1': NA('No Performance Monitoring.'), 'FB-PM-2': NA('No Performance Monitoring.'),
    'FB-RD-1': NA('No Realtime Database.'), 'FB-RD-2': NA('No Realtime Database.'), 'FB-RD-3': NA('No Realtime Database.'),
    'FB-RC-1': NA('No Remote Config.'),
    'FB-RC-2': ('done', 'The equivalent exists without Remote Config: a galaxy\'s `min_build` refuses an older launcher and says how to update (plan item L3).', []),
})

# ── The Update Framework: attacks on software update ─────────────────────────
NO_UPDATER = 'The launcher does not update itself: the player downloads a new zip from the release page (one-click update was removed, plan item L3).'
TUF = {
    'TUF-1': ('apply', NO_UPDATER, ['The download page publishes the SHA-256 of each zip and explains how to check it (Website_Checklist R8).', 'Signed builds (M3) make a replaced file detectable by Windows itself.']),
    'TUF-2': ('apply', 'The launcher does download turns and notes from the relay and Storage.', ['The launcher caps the size of a turn, note or answer it will read, and stops reading past it.']),
    'TUF-3': NA(NO_UPDATER + ' No dependencies are fetched at run time.'),
    'TUF-4': NA(NO_UPDATER),
    'TUF-5': ('apply', 'The analogue here is turns: a launcher served the same old turn must notice.', ['Multiplayer_Checklist M8: a turn the launcher could not refresh is labelled with its age.']),
    'TUF-6': NA('No mirrors. One release page (GitHub) and one relay.'),
    'TUF-7': NA(NO_UPDATER),
    'TUF-8': ('apply', None, ['The update notice never points to a build older than the one running.', 'A galaxy\'s `min_build` only rises.']),
    'TUF-9': ('apply', None, ['The code-signing identity (M3) is the one key that vouches for every build. Keep it in a managed service (Azure Artifact Signing) behind an MFA-protected account, not as a file on the build PC.', 'The GitHub account that publishes releases has MFA.']),
    'TUF-10': ('apply', None, ['The update notice links one hard-coded release page, never a URL taken from the relay or a galaxy.']),
}

# ── Microsoft: minimising SmartScreen warnings ───────────────────────────────
SS = {
    'SS-1': ('deviation', 'Not listing in the Microsoft Store (decided 8 October 2026). SmartScreen reputation comes from signing every release with one identity (SS-2, SS-5). Without a storefront there is no IARC rating, since IARC is reached only through a participating store; the age floor (PRIV-1) is stated by the project instead.', []),
    'SS-2': ('apply', None, ['Sign the launcher and the client on every release (plan item M3). Unsigned, Smart App Control can block the client outright (seen in Windows Sandbox, 5 October 2026).']),
    'SS-3': ('apply', None, ['Release order: build, sign, then zip, then compute the SHA-256. Nothing is patched after signing; the client\'s binary patches happen before.']),
    'SS-4': ('apply', None, ['The launcher behaves like an ordinary program: no bundled extras, no autostart, no elevation, nothing written outside its folder (REL-1).']),
    'SS-5': ('apply', None, ['One signing identity for every release; record it here once chosen.']),
    'SS-6': ('apply', None, ['[x] The download page warns about the unrecognised publisher.', 'Add what Smart App Control does and what to do (Website_Checklist R9).']),
}

# ── ICO Age appropriate design code (Children's code) ────────────────────────
UK_KIDS = 'UK children are likely to access the game, which the ICO judges by likelihood, not intent'
TEENS = ('Applies: the game is for ages 13 and up (decided 8 October 2026), and a player aged 13 to 17 '
         'is a child under the code.')


def KIDS(subs=()):
    return ('apply', TEENS, list(subs))
AADC = {
    'AADC-1': KIDS(['Record the best-interests assessment as part of the DPIA (AADC-2).']),
    'AADC-2': ('apply', TEENS, ['Write a data protection impact assessment using the ICO\'s sample games DPIA (TIP-1).']),
    'AADC-3': ('apply', None, ['[x] Age floor decided: 13 and up (PRIV-1).', 'Decide how it is applied: state it in the join notice and terms only, or also ask for an age (or a 13+ confirmation) at join.']),
    'AADC-4': ('apply', None, ['The privacy page and the join notice are short and in plain language (Website_Checklist Opquast 15).']),
    'AADC-5': KIDS(['No streaks, rewards for daily return or time-pressure purchases; four-hour turns already avoid most of this.']),
    'AADC-6': ('apply', None, ['The community guidelines and terms are enforced as written (Multiplayer_Checklist M19 to M22).']),
    'AADC-7': KIDS(['Messages from strangers: decide whether a child\'s default is to receive them.']),
    'AADC-8': ('apply', None, ['[x] Collected: sign-in identifier, player name, game state, messages, and launcher logs only when the player sends one. No email, age, location or payment.', 'Keep it that way; record any new field here first.']),
    'AADC-9': ('apply', None, ['Data is shared with no third party beyond the hosting provider (Google, as processor).']),
    'AADC-10': NA('No geolocation is collected.'),
    'AADC-11': NA('No parental controls are offered.'),
    'AADC-12': NA('No profiling: no analytics, no advertising, no recommendations.'),
    'AADC-13': ('apply', None, ['No prompt nudges a player to give more data or weaken a setting.']),
    'AADC-14': NA('No connected toy or device.'),
    'AADC-15': ('apply', None, ['A prominent way to ask for one\'s data or its deletion, and to report a concern (PRIV-4, Multiplayer_Checklist M20).']),
}

TIP = {
    'TIP-1.1': KIDS(['Ask beta players about who plays (age bands), as part of the DPIA.']),
    'TIP-1.2': ('apply', None, ['Document the game\'s appeal to children: a 2000s 4X strategy game with text diplomacy and four-hour turns. This is the evidence for AADC-2.']),
    'TIP-1.3': ('apply', None, ['Review the assessment once the beta shows who is playing.']),
    'TIP-1.4': NA('No randomised rewards or loot boxes.'),
    'TIP-2.1': KIDS(['As AADC-3.']),
    'TIP-2.2': KIDS([]),
    'TIP-2.3': COND('an age question is asked at join', ['A player refused for age cannot simply join again with another age at once (a cooldown).']),
    'TIP-3.1': KIDS([]),
    'TIP-3.2': KIDS([]),
    'TIP-3.3': KIDS([]),
    'TIP-4.1': ('apply', None, ['[x] No optional use of personal data exists today (no analytics or marketing).']),
    'TIP-4.2': ('done', 'Four-hour turns are natural breaks, and progress is saved by the referee every turn.', []),
    'TIP-4.3': NA('No product placement, advertising or sponsorship.'),
    'TIP-5.1': NA('No parental controls.'),
    'TIP-5.2': NA('No privacy settings to change.'),
    'TIP-5.3': KIDS(['What other players see is the civ name only (Multiplayer_Checklist M15).']),
    'TIP-5.4': NA('No voice chat.'),
    'TIP-6.1': NA('No advertising.'),
    'TIP-6.2': NA('No profiling.'),
    'TIP-6.3': NA('No marketing and no accounts.'),
    'TIP-6.4': NA('No marketing.'),
    'TIP-7.1': NA('No offers or purchases.'),
    'TIP-7.2': KIDS([]),
    'TIP-7.3': NA('No social media competitions or partnerships.'),
    'TIP-7.4': NA('No click-through tracking.'),
    'TIP-7.5': NA('No purchases.'),
}

ICO = {
    'ICO-C-1': ('apply', 'As PRIV-2.', []),
    'ICO-C-2': ('apply', 'As PRIV-2.', []),
    'ICO-C-3': ('apply', None, ['Name the lawful basis for each kind of data, likely legitimate interests or contract (running the game the player asked to join). Unverified: confirm with the ICO\'s lawful-basis guidance.']),
    'ICO-C-4': NA('Consent is not the basis relied on (ICO-C-3).'),
    'ICO-C-5': NA('As ICO-C-4.'),
    'ICO-C-6': COND('consent becomes a basis (ICO-C-3)'),
    'ICO-C-7': NA('No processing for vital interests.'),
    'ICO-C-8': COND('legitimate interests is the basis (ICO-C-3)', ['Record the three-part test: purpose, necessity, balance.']),
    'ICO-C-9': ('apply', None, ['Find whether UK GDPR applies to a US-based non-commercial operator with UK players, and whether a data protection fee or an exemption applies. Unverified: needs the ICO\'s fee guidance and territorial-scope text, or counsel (PRIV-6).']),
    'ICO-S-1': ('apply', 'As the security checklist, which is that assessment.', []),
    'ICO-S-2': ('apply', None, ['A short security policy: who is responsible, what is protected, how incidents are handled (OPS-1).']),
    'ICO-S-3': ('apply', 'One operator: responsibility is theirs; record it in the policy.', []),
    'ICO-S-4': ('apply', None, ['Google\'s data processing terms cover Firebase and Google Cloud; record that they are accepted for the project.']),
}

COPPA = {
    'COPPA-1': ('apply', None, [
        '[x] The game is for ages 13 and up (decided 8 October 2026), so it is not meant for children under 13. Record the reasoning against the FTC\'s factors (subject matter, visuals, audience) in the privacy records.',
        'Procedure when the operator learns a player is under 13 (actual knowledge): delete their data, reclaim the seat, and tell them why.',
        'The FTC\'s list of personal information includes a persistent identifier (the sign-in identifier), and a screen name where it functions as online contact information (player names can be messaged in game).']),
    'COPPA-2': COND(UNDER13, ['The privacy page (PRIV-3) meets COPPA\'s content requirements.']),
    'COPPA-3': COND(UNDER13),
    'COPPA-4': COND(UNDER13),
    'COPPA-5': COND(UNDER13),
    'COPPA-6': ('apply', None, ['A written retention and deletion schedule (PRIV-5) is good practice whatever COPPA-1 finds, and required if it finds coverage.']),
}

# ── items no source covers ───────────────────────────────────────────────────
OPS = [
    ('OPS-1', 'An incident process: what counts as an incident, how severe it is, who acts, how players are told, and that each serious one gets a written review.',
     'PagerDuty Incident Response documentation (severity levels, roles, external communication, postmortem template)',
     ['Define three severities: a galaxy stalled or data at risk; a feature broken for everyone; a problem for one player.',
      'Where players are told: the operator page is the operator\'s; players need a status line in the launcher or a page on the site.',
      'Postmortems: the 29 September turn-23 stall and the 5 October Smart App Control block each get a short written review.']),
    ('OPS-2', 'Every live galaxy can be restored from what Firebase holds, and the restore has been tried.',
     'Google Cloud Well-Architected Framework, Reliability pillar, principle 8 (test data-loss recovery)',
     ['[x] The referee survives a reboot with its local files deleted, because the galaxy is in Firebase (plan item H3).',
      'Restore a galaxy to an earlier turn from its archive on a copy, and record the steps.',
      'Decide whether Firestore and Storage need backups beyond the archive (a deleted project loses both; FB-MSYF-3).']),
    ('OPS-3', 'Recovery is tested, not assumed: the referee is stopped, killed and restarted on purpose and the galaxy carries on.',
     'Google Cloud Well-Architected Framework, Reliability pillar, principle 7 (test recovery)',
     ['[x] Turn 23 stall (29 September 2026): the worker retried, and the turn published once the cause was fixed.',
      'A failure that repeats identically N times alerts the operator instead of retrying for hours (Security_Checklist ASVS V16.5.3).']),
    ('OPS-4', 'The operator learns of a stalled galaxy without having to look.',
     'This project; no source covers a home PC acting as the referee',
     ['[x] The operator page marks itself stale (plan item N3).',
      'A push alert (email or phone) when a turn is overdue by more than one turn length.']),
    ('OPS-5', 'The referee\'s machine is a known single point of failure, and its failure modes have a plan: power loss, ISP loss, Windows Update reboots, the operator travelling.',
     'This project; no source covers a home PC acting as the referee',
     ['Write down what happens to each live galaxy in each case, and what players are told.',
      'Move the referee to a cloud VM (the next phase) or to the Windows Sandbox once the client is signed.']),
    ('OPS-6', 'The project does not depend on one person\'s memory: the runbook is written and someone else could follow it.',
     'Firebase launch checklist FB-MSYF-6 (more than one owner), extended to knowledge',
     ['`docs/Public_Beta_Plan.md` "Operating it" is the runbook; check it covers starting, closing and restoring a galaxy, rebinding a seat, and deploying the relay.']),
    ('OPS-7', 'Cost stays inside the budget at the target scale, with headroom.',
     'Firebase launch checklist FB-KYPP; plan items H9, H12, H13',
     ['A measured day at beta scale, against the free-tier allowances and the budget.']),
]

REL = [
    ('REL-1', 'The launcher installs and uninstalls cleanly: unzipping is installing, deleting the folder is uninstalling, and nothing is left behind.',
     'Windows Desktop App Certification Requirements v3.3 (Microsoft, 2014), requirement group 5 (clean, reversible install), paraphrased',
     ['The download page says nothing is written outside the folder. Check it: where the identity file, logs, the client\'s files and any registry keys go.',
      'If anything is written to `%APPDATA%` or the registry, the page and the uninstall note say so.']),
    ('REL-2', 'The launcher runs without administrator rights, does not start with Windows, and keeps user data where a standard user can write it.',
     'Windows Desktop App Certification Requirements v3.3, requirement groups 9 and 10, paraphrased',
     ['The launcher and the client run as `asInvoker` (no elevation prompt).',
      'Unzipping into `C:\\Program Files` fails for a standard user because the data folder is inside it: say so on the download page, or move user data to `%LOCALAPPDATA%`.',
      'No autostart entry.']),
    ('REL-3', 'Every release is reproducible from the repository and documented: version, release notes, the zip, and its SHA-256 on the release page and the download page.',
     'Apache "Release Download Pages for Projects"; NIST SP 800-218 SSDF practice PS (protect the software)',
     ['`release/build.ps1` builds from a clean checkout at a tagged commit.',
      'The release notes name what changed for players.',
      'The download page and the release page agree (Website_Checklist R8).']),
    ('REL-4', 'Each release is checked against the antivirus engines players use, and false positives are reported to the vendor before the release is announced.',
     'Microsoft Security Intelligence file submission (software developer, expected "clean")',
     ['Submit each release to Microsoft Defender.',
      'Scan with a multi-engine scanner and record the result; report false positives to each vendor that flags it.']),
    ('REL-5', 'Each release passes the verification checklists that cover what changed before it is published.',
     'This project',
     ['Singleplayer_UI_Checklist for the client and the launcher\'s single-player path.',
      'Multiplayer_Checklist M items for joining and playing a turn against a live galaxy.',
      'Security_Checklist items that name the launcher.']),
    ('REL-6', 'A signing option is chosen that the operator can actually enrol in.',
     'Microsoft Learn, "Code signing options for Windows app developers" (29 August 2026)',
     ['Azure Artifact Signing (about $9.99 a month) accepts individuals in the USA and Canada only; check the operator\'s eligibility.',
      'Record the choice in plan item M3.']),
]

PRIV = [
    ('PRIV-1', 'The age floor is decided and stated: the age the game is for, and what is done about younger players.',
     'ICO Children\'s code standard 3; FTC COPPA step 1; PEGI Online Safety Code provision 2',
     ['[x] Decided 8 October 2026: teens and up, 13+. Players aged 13 to 17 make the ICO Children\'s code apply; under-13s are excluded, which keeps COPPA\'s consent steps out of scope (COPPA-1).',
      'State it in the join notice, the terms and the privacy page.']),
    ('PRIV-2', 'A record of the personal data the project holds: what, where, why, for how long, and who can see it.',
     'ICO controllers checklist (information audit; documentation)',
     ['Sign-in identifier (Firebase Auth, the galaxy seat map, logs).',
      'Player and civ names (galaxy documents, the archive, every player\'s copy of the galaxy).',
      'In-game messages (inside the turn blobs, held by every player in the galaxy).',
      'Launcher logs (Firestore, redacted, kept for a fixed number of days).',
      'IP addresses (Google\'s request logs for the relay, Hosting and Storage).',
      'Join requests and penalties (Firestore, operator records).']),
    ('PRIV-3', 'A privacy page, linked from the launcher, the join notice and every page of the site.',
     'ICO Children\'s code standard 4; FTC COPPA step 2; Opquast 15; PEGI provision 1',
     ['Written from PRIV-2, in plain language.']),
    ('PRIV-4', 'A player can ask for their data and its deletion, and the operator has a procedure that works with anonymous identities.',
     'ICO Children\'s code standard 15; ICO controllers checklist; FTC COPPA step 5',
     ['The player gives their support code; the operator finds their seat, logs and join records.',
      'What deletion means when the player\'s civ name and messages are inside every other player\'s copy of past turns: record the answer (likely, rename the civ in future turns and delete logs and records).']),
    ('PRIV-5', 'A retention schedule, applied automatically where possible.',
     'FTC COPPA step 6 (written retention and deletion policy); ICO data minimisation',
     ['[x] Launcher logs expire after `LOG_KEEP_DAYS` (`expire_at` on each record).',
      '[x] Submissions are pruned after `SUBMISSION_TURNS_KEPT` turns.',
      'Join records, closed galaxies and their archives, the referee\'s local logs and captures: set a period for each.',
      'Cloud Logging retention for the relay.']),
    ('PRIV-6', 'Which data protection laws apply is established, not assumed.',
     'This project; regulators\' guidance',
     ['UK GDPR and EU GDPR for players in the UK and EU, given a non-commercial operator outside both. Unverified: whether Article 27 (a representative in the UK or EU) applies, and the age of digital consent under GDPR Article 8, which varies by member state. Check against the regulation text or with counsel.',
      'US COPPA (COPPA-1). US state laws such as CCPA are very likely out of scope for a non-commercial project below their thresholds; confirm the thresholds against the statute.']),
    ('PRIV-7', 'The UK Online Safety Act duties for user-to-user services are assessed, since players can message each other.',
     'Ofcom, "Gaming: know the online safety risks, the rules, and how to comply" and the Illegal Content Codes of Practice for user-to-user services',
     ['Read Ofcom\'s gaming guidance by hand (it refused automated fetches) and use Ofcom\'s applicability checker.',
      'If in scope: an illegal content risk assessment, and the reporting and complaints measures (overlapping Multiplayer_Checklist M19 to M22).']),
    ('PRIV-8', 'The privacy page and the terms are reviewed by counsel before the public release.',
     'Checklist_Sources section 6',
     []),
    ('PRIV-9', 'The rights holder\'s permission for the revival is recorded, along with what it covers (the client, the art, the manual, the name).',
     'This project; no checklist covers reviving an abandoned game',
     ['Keep the written permission with the project\'s records, and say on the site what the revival is permitted to do.']),
]

MARK = {'apply': '- [ ]', 'done': '- [x]', 'cond': '- COND', 'deviation': '- DEVIATION', 'na': '- N/A'}


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


def section(title, items, table, counts, label):
    out, current = ['', f'### {title}'], None
    c = counts.setdefault(label, {})
    for iid, sect, text in items:
        if sect != current and sect != title:
            current = sect
            out += ['', f'#### {sect}', '']
        elif current is None:
            current = sect
            out += ['']
        d = table.get(iid)
        k = d[0] if d else 'untailored'
        c[k] = c.get(k, 0) + 1
        out += render(f'**{iid}** {" ".join(text.split())}', d)
    return out


def authored(items, counts, label):
    out = ['']
    c = counts.setdefault(label, {})
    for iid, req, source, subs in items:
        c['apply'] = c.get('apply', 0) + 1
        out += [f'- [ ] **{iid}** {req}', f'  - Source: {source}.']
        out += [f'  - [x] {s[4:]}' if s.startswith('[x] ') else f'  - [ ] {s}' for s in subs]
    return out


def main():
    counts = {}
    ops = (section('Firebase launch checklist', src.firebase_launch(), FB, counts, 'Firebase launch checklist')
           + ['', '### Operating the service (OPS)'] + authored(OPS, counts, 'OPS items'))
    rel = (section('Minimising SmartScreen warnings (Microsoft)', src.smartscreen(), SS, counts, 'Microsoft SmartScreen')
           + section('Attacks on software update (The Update Framework)', src.tuf_attacks(), TUF, counts, 'TUF attacks')
           + ['', '### Releasing the launcher (REL)'] + authored(REL, counts, 'REL items'))
    priv = (['', '### Decisions and records (PRIV)'] + authored(PRIV, counts, 'PRIV items')
            + section('ICO Age appropriate design code (Children\'s code), the 15 standards', src.ico_aadc(), AADC, counts, 'ICO Children\'s code')
            + section('ICO top tips for games designers', src.ico_games_tips(), TIP, counts, 'ICO games tips')
            + section('ICO controllers checklist (lawful basis and transparency)',
                      src.ico_checklist('ico_controllers_checklist.md', 'ICO-C'), ICO, counts, 'ICO controllers')
            + section('ICO information security checklist (first section)',
                      src.ico_checklist('ico_information_security_checklist.md', 'ICO-S'), ICO, counts, 'ICO security')
            + section('FTC COPPA six-step compliance plan', src.ftc_coppa(), COPPA, counts, 'FTC COPPA'))

    order = ['apply', 'done', 'cond', 'deviation', 'na', 'untailored']
    totals = ['| Source | apply | done | cond | deviation | N/A | to tailor | total |',
              '|---|---|---|---|---|---|---|---|']
    for name, c in counts.items():
        totals.append(f'| {name} | ' + ' | '.join(str(c.get(k, 0)) for k in order)
                      + f' | {sum(c.values())} |')

    head = [
        '# Operations, release and privacy checklist',
        '',
        'Running the service, releasing the launcher, and handling players\' '
        'data. Rerun the items a change touches; run the release items before '
        'every release.',
        '',
        'Written for the published game. Unchecked items are the gap to it.',
        '',
        '**Not legal advice.** The privacy sections quote regulators\' guidance '
        'as published. Whether a law applies, and what it requires, is for the '
        'controlling text or counsel (PRIV-6, PRIV-8). Points not checked against '
        'the controlling text say "unverified".',
        '',
        '**Sources**, each reproduced or paraphrased as its licence allows:',
        '',
        '- Firebase launch checklist (Google, updated 24 September 2026), CC BY 4.0, items reproduced.',
        '- Microsoft Learn, "SmartScreen reputation for Windows app developers" (2026), CC BY 4.0, items reproduced; "Code signing options for Windows app developers" (29 August 2026), cited.',
        '- The Update Framework specification v1.0.36 (5 August 2026), Community Specification License 1.0, the attack list reproduced.',
        '- ICO, "Age appropriate design: a code of practice for online services", the 15 standards, and "Top tips for games designers", Open Government Licence v3.0, reproduced.',
        '- ICO controllers checklist and information security checklist, OGL v3.0, the statements reproduced. The saved copies hold the first section of each; the rest is online and still to be gone through.',
        '- FTC, "Children\'s Online Privacy Protection Rule: A Six-Step Compliance Plan for Your Business" (May 2026), a US Government work, the steps reproduced.',
        '- Cited and paraphrased: PagerDuty Incident Response documentation; Google Cloud Well-Architected Framework, Reliability pillar; Windows Desktop App Certification Requirements v3.3; Apache "Release Download Pages for Projects"; NIST SP 800-218; Ofcom\'s online safety guidance for games.',
        '',
        'Generated by `build_ops_checklist.py` from `ops_sources.py`, beside this file.',
        '',
        '## How to read an item',
        '',
        '- `- [ ]` applies and is not yet verified.',
        '- `- [x]` verified, with what verified it.',
        '- `- COND` applies only if a condition holds; the item names it.',
        '- `- DEVIATION` the design contradicts it on purpose; the rationale follows.',
        '- `- N/A` does not apply, with the reason.',
        '',
        '## Totals',
        '',
    ] + totals + ['', '## Operations'] + ops + ['', '## Release'] + rel + ['', '## Privacy'] + priv
    with open(OUT, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(head) + '\n')
    print(f'wrote {OUT}')
    for line in totals[2:]:
        print(line)


if __name__ == '__main__':
    main()
