# Checklist sources

The published checklists and standards that the tailored Resurgence checklists
are built from, one section per area. Each section names a baseline, the
supplements that fill its gaps, and the sources kept for reference only.

All sources were checked against their live pages on 29 September 2026. Local
copies are in `tools/checklists/`, which is gitignored; each area folder that has
one carries a `SOURCES.txt` with provenance and licence per file.

## How the tailored checklists are built

A checklist is written for the full published game, not for the beta. Items the
beta does not reach stay unchecked, so the gap between the beta and a release is
the list of unchecked items.

1. The standard's item is the parent, with its ID and wording (or a paraphrase
   where the licence forbids copying, see below).
2. Our sub-items sit under it and name the concrete surface: the player name
   field, the order blob, the Storage path, the launcher's update check.
3. An item that does not apply is kept and marked N/A with a one-line reason,
   so the gap is a decision on record rather than an omission.
4. An item our design deliberately contradicts is marked as a deviation, with
   the rationale and any mitigation.

## Licence rules

| Class | Sources | What we may do |
|---|---|---|
| Copy verbatim with attribution | OWASP (CC BY-SA 4.0), Firebase and Google developer docs (CC BY 4.0), W3C (W3C Document License), Opquast (CC BY-SA 4.0), A11Y Project (Apache 2.0), Game Accessibility Guidelines, ICO (OGL v3.0), PagerDuty (Apache 2.0), AWS Games Lens (MIT-0), Microsoft Learn docs from the MicrosoftDocs repos (CC BY 4.0), TUF and SLSA (Community Specification License 1.0), NIST and FTC (US government works) | Embed item text. CC BY-SA adaptations stay CC BY-SA. |
| Copy unmodified only, non-commercial | CIS Controls (CC BY-NC-ND 4.0), Google SRE book (CC BY-NC-ND 4.0) | The project is non-commercial, so an unmodified copy is allowed. A tailored checklist is an adaptation, which ND forbids distributing, so these are cited by ID and paraphrased like the class below. |
| Cite and paraphrase only | Xbox Requirements and Xbox Accessibility Guidelines (Microsoft Learn terms), Epic Games Store requirements, NN/g articles, Fair Play Alliance framework (ADL copyright), academic heuristics (ACM, Springer), AbleGamers APX, PEGI POSC (licence unverified), Front-End Checklist (licence ambiguous) | Cite the ID, write the requirement in our own words. |
| Not available | Sony TRC, Nintendo Lotcheck (NDA), Steamworks release checklists (partner login) | Not used. |

---

## 1. Server and application security

**Baseline**

| Source | Version | Size | Local copy |
|---|---|---|---|
| OWASP Application Security Verification Standard (ASVS) | 5.0.0, 30 May 2025 | 345 requirements. L1 = 70, L2 = 253 cumulative. 17 chapters, V1 to V17 | `security/OWASP_Application_Security_Verification_Standard_5.0.0_en.csv` and `.flat.json` |
| OWASP Game Security Framework (GSF) | v0.5.1, public review draft, last commit 30 Mar 2026 | 132 requirements, 7 chapters. The framework names L2 as the level for most online multiplayer games | `security/OWASP_OGSF.md`, `security/OWASP_OGSF_v0.5.1_requirements.csv` (parsed from the Markdown) |

GSF requirement IDs embed the version, so the tailored checklist pins v0.5.1.

**Supplements**

| Source | Covers |
|---|---|
| Firebase Security Checklist (updated 24 Sep 2026) and "Avoid insecure rules" | App Check, API key restrictions, Security Rules and their emulator tests, anonymous auth, Functions safety, dev/staging separation |
| OWASP API Security Top 10, 2023 | API1 and API3 (one player reading or writing another's documents or blobs), API4 (Functions cost abuse) |
| OWASP Cheat Sheets: Deserialization, File Upload, Input Validation, Denial of Service, Secrets Management | The order blob as untrusted input to the engine |
| OWASP Desktop App Security Top 10, 2021 | The launcher: signing, DLL preloading, secrets on disk |
| CIS Controls v8.1, IG1 (56 safeguards) | The home referee PC as a host: inventory, patching, admin accounts, backups |
| NIST SP 800-218 SSDF v1.1 | Development process; the practices spreadsheet is usable as-is |

**Reference only:** OWASP Top 10:2025 (awareness index), OWASP WSTG v4.2 (a test
method, useful later as how-to for ASVS items), Microsoft SDL practices, CIS GCP
Benchmark (version unverified, mostly services we do not run).

**Chapters of ASVS expected to be N/A or thin:** V3 Web Frontend (static site
only), V10 OAuth and OIDC, V17 WebRTC.

## 2. Multiplayer experience, accessibility, and player safety

No single published checklist covers this area. The recommended combination:

**Baseline**

| Source | Version | Size | Local copy |
|---|---|---|---|
| Game Accessibility Guidelines (GAG) | Unversioned, maintained since 2012 | Basic 27, Intermediate 48, Advanced 27. Categories: Motor, Cognitive, Vision, Hearing, Speech, General | `multiplayer/GAG-checklist.xlsx`, `.csv` |
| Pinelle et al., "Usability Heuristics for Networked Multiplayer Games", ACM GROUP 2009 | | 10 heuristics | Paraphrase only |

The ten networked-game heuristics: simple session management, flexible
matchmaking, appropriate communication tools, support coordination, meaningful
awareness information, identifiable avatars, protected training for beginners,
support social interaction, reduce game-based delays, manage bad behaviour.

**Supplements (paraphrase with ID citations)**

| Source | Items that apply |
|---|---|
| Xbox Requirements v16.4 (Sep 2026) | XR-003 title quality, XR-074 loss of connectivity (test cases 074-01 to 074-08), XR-132 service rate limits, XR-014 player data, XR-015 player communication (mute, block), XR-018 UGC reporting and text filtering, XR-045 privilege 254 (async turn-based and join-in-progress) |
| Epic Games Store testing guide | Offline mode messaging, first-launch login, token renewal after 60 minutes, non-Latin display names and install paths, trust and safety |
| Fair Play Alliance, "Building a Penalty and Reporting System" (Dec 2020) | Report acknowledgement, investigation, penalty duration, path back to good standing, repeat offenders |
| PEGI Online Safety Code | Six provisions: privacy policy, age-rated content, reporting, removal, community standards, advertising |
| Xbox Accessibility Guidelines v3.2 | Depth for XAG 112 to 116 (UI navigation, focus, context, error messages, time limits) and 120 to 122 (communication, documentation, support) |

**Reference only:** AbleGamers APX patterns ("Leave It There" and "Total Recall"
fit returning players), PLAY and HEP heuristics, Celia Hodent's work, ESA
Accessible Games Initiative tags, Neptune's Pride turn model as a comparison.

## 3. Website

**Baseline**

| Source | Version | Size | Local copy |
|---|---|---|---|
| WCAG 2.2, Level A and AA | W3C Recommendation, 12 Dec 2024 | 55 success criteria at A and AA (86 including AAA) | `website/wcag22.json`, `website/wcag22_success_criteria.csv` |
| Opquast Digital Quality Framework | v5.0, 24 Sep 2025 | 245 rules, 14 themes | `website/opquast_digital-quality_v5_EN_2025.pdf`, `.txt` |

**Supplements**

| Source | Covers |
|---|---|
| Lighthouse v13.5.0 and Core Web Vitals (LCP 2.5 s, INP 200 ms, CLS 0.1 at the 75th percentile) | Automated gate per deploy |
| MDN HTTP Observatory tests, with the OWASP Secure Headers list as input | Headers in `firebase.json`. The OWASP list's `Cache-Control: no-store` and `Clear-Site-Data` are wrong for a cached static site |
| Google SEO Starter Guide and Search Essentials | Titles, canonicals, and indexing of the archive pages |
| Apache release download page rules, Microsoft SmartScreen reputation guidance | The launcher download page |
| WCAG-EM 2.0 (Jul 2026) | Choosing a representative sample of the mirrored archive instead of auditing every page |
| NN/g 10 heuristics and 113 homepage guidelines | Human review pass (link only) |

**Reference only:** W3C Easy Checks and the A11Y Project checklist (fast, but
deliberately partial), HHS Research-Based Guidelines (2006), GOV.UK Service
Standard, Front-End Checklist. **Skip:** Section 508 and EN 301 549, which
restate WCAG for our purposes.

## 4. Operations and reliability

**Baseline:** Firebase Launch Checklist (updated 24 Sep 2026), with the Firebase
Security Checklist from section 1. It notes that budget alerts are not caps.

**Supplements**

| Source | Covers |
|---|---|
| PagerDuty Incident Response docs (Apache 2.0) | Severity levels, roles, during and after an incident, postmortem template, external communication. Local copy: `ops_release_privacy/pagerduty_incident_response_docs/` |
| Google Cloud Well-Architected Framework, Reliability pillar | Principles 7 and 8, recovery testing and data-loss recovery testing |
| AWS Well-Architected Games Industry Lens v1.0.0 (MIT-0) | The only games-specific question set found, in AWS terms. Local copy: `ops_release_privacy/aws_games_lens_wafgames-v1.0.0.json` |

**Reference only:** Google SRE Launch Coordination Checklist (about 40 items,
datacenter scale), NIST SP 800-61 Rev. 3 (Apr 2025), Heroic Labs launch
readiness page.

## 5. Windows release and distribution

**Baseline**

| Source | Covers |
|---|---|
| Microsoft Learn, "SmartScreen reputation for Windows app developers" | Unsigned files restart reputation at zero each version; EV no longer bypasses SmartScreen; Smart App Control may block unsigned files |
| Microsoft Learn, "Code signing options for Windows app developers" (29 Aug 2026) | Azure Artifact Signing, OV, EV, Store signing, SignPath for open source; eligibility by country and entity type |
| Windows Desktop App Certification Requirements v3.3 (2014, retired program), groups 5, 6, 9, 10 | Clean reversible install and uninstall, signed executables, asInvoker, correct folders, no autostart, user data in AppData. Link only |

**Supplements:** Defender false-positive submission portal (procedural), TUF
specification v1.0.36 attack list for the launcher's self-update, SSDF
spreadsheet for build and release, SLSA v1.2 build track L1 to L2.

**Reference only:** Microsoft Store Policies v7.20 (binding only if listed in
the Store), Reproducible Builds, Steamworks release process.

## 6. Privacy and legal

General guidance, not legal advice. Every item here is checked against the
controlling text before it is relied on, and the privacy policy and terms go to
counsel.

**Baseline**

| Source | Covers |
|---|---|
| ICO Controllers checklist and Information security checklist | UK GDPR baseline for a small controller |
| ICO Age Appropriate Design Code (15 standards), with ICO "Top tips for games designers" (7 tips) | Applies if UK children are likely to access the game; the scope test is in `ico_aadc_services_covered.md` |

**Supplements**

| Source | Covers |
|---|---|
| FTC COPPA Six-Step Compliance Plan (May 2026, reflecting the amended rule) | Whether we are directed to children or have actual knowledge of under-13 players. Rule text, 16 CFR Part 312, not yet read |
| Ofcom Online Safety Act guidance for games and the Illegal Content Codes for user-to-user services | Applies if players can message each other and UK players exist. Ofcom returned 403 to automated fetches; read manually |
| IARC | Ratings are obtainable only through a participating storefront |

**Unverified, to check against the regulation text:** GDPR and UK GDPR Art. 27
(representative for a controller outside the EU or UK), GDPR Art. 8 (age of
digital consent by member state), CCPA thresholds beyond the revenue figure.

**Reference only:** ESRB Privacy Certified (paid), CCPA (thresholds almost
certainly not met), Council of Europe guidelines for online game providers.

---

## Deviations already known

Items in the baseline sources that the current design contradicts on purpose.
Each needs a rationale, and a mitigation where one exists, in the tailored
checklist.

| Source item | What it says | Our design |
|---|---|---|
| OWASP GSF 1.3.5 | Send clients only the data they need | Every player holds the whole galaxy; the engine's own visibility rules hide rivals from an unmodified client |
| Firebase Security Checklist, Anonymous authentication | Anonymous auth is for warm onboarding; non-public data should require a non-anonymous provider | Identity is an anonymous uid plus a username |

## Gaps no source covers

These need items we write ourselves, in the same parent and sub-item format.

- A home PC as the authoritative referee: power and ISP loss, the lock and lease
  rules, and the move to a cloud VM.
- Asynchronous seat lifecycle: joining a live galaxy mid-game, idle and
  abandoned empires, recovering a seat after a reinstall (J4).
- Orders as a binary blob loaded into a closed-source engine: the Deserialization
  cheat sheet is the nearest source and assumes code we control.
- Reviving an abandoned game's IP: no checklist exists.
