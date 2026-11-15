# Postmortems

One short review per serious incident (Operations Checklist OPS-1). Each follows the PagerDuty
template: summary, impact, timeline, cause, resolution, what went well and poorly, and action
items. A fact the record does not hold is written as not recorded.

---

## 5 October 2026: Smart App Control blocked the referee's client

**Summary.** The referee had run galaxy `h3check` inside Windows Sandbox since 4 October. It
stopped at the first tick that needed the game client: Smart App Control inside the sandbox
refused the unsigned patched client (`WinError 4551`).

**Impact.** `h3check` did not tick until the referee was moved to a signed build. No player
report is recorded, and no player-facing build is known to have been blocked: the block was
seen in the referee's sandbox. The same block was expected on any Windows 11 PC with Smart
App Control on, which every unsigned release risked.

**Timeline.**
- 4 to 5 October: the sandboxed referee runs `h3check` and stops at the first tick with the
  client.
- 6 October: signing work starts (`2626231`); the patched clients are signed with Azure
  Artifact Signing (`71289d7`, decided 7 October).
- 7 October: the signing setup is recorded (`ce76f69`).
- 8 October: the stage signs what it copies (`fcf3256`). Smart App Control also refuses the
  unsigned Python interpreter the referee runs on, so `server/sandbox/bootstrap.ps1` turns it
  off inside the sandbox only (`158c836`, 23:01); the worker published turn 44 at 23:04. The
  download page warns about an unrecognised publisher (Website Checklist R9), and the player
  readme says the launcher is signed (`93923d0`).
- 16 October: the build signs the original client for the Tutorial and the Demo (`cff246a`);
  v0.2.1's notes say every game client in the download is signed.

**Cause.** The patched client and the original 2006 client were unsigned. Smart App Control
blocks unsigned executables it has no reputation for.

**Resolution.** Every executable in a release is signed. Inside the referee's sandbox,
Smart App Control is off, since it also refuses the unsigned Python interpreter; the host's
setting is untouched.

**What went well.** The referee failed safe: no turn was published wrong. Signing was in place
within four days, and the sandbox path was fixed at the same time as the release path.

**What went poorly.** The release had never been run on a PC with Smart App Control on, so the
block was found by the referee, not before a release. A new signature still earns
SmartScreen reputation slowly, so players can still see a warning.

**Action items.**
- [x] Sign every executable in a release (`release/build.ps1`).
- [x] Warn on the download page and in the readme (Website Checklist R9, `PLAYER_README.txt`).
- [ ] SS-2: confirm every release is signed, at each release (Release Checklist).
- [ ] SS-5: record the one signing identity (Release Checklist).
- [ ] SS-6: say what Smart App Control does and what to do (Release Checklist, Website R9).

---

## 29 September 2026: the turn-23 stall

**Summary.** Turn 23 of `h3check` stalled; the referee worker retried, and the turn published
once the cause was fixed. That is the whole of what the record says
(`docs/verification/4 - Operations_Checklist.md`, written 8 October in `fcf3256`).

**Impact.** Not recorded. The Storage write log shows `h3check` turns 20 to 23 published
between 11:57 and 23:59 UTC on 29 September, so the stall ended that day; how long players
waited is not recorded.

**Timeline.**
- 29 September, 09:23 UTC: the worker is restarted (the beta plan's run log).
- 29 September, by 23:59 UTC: turn 23 is published.
- 29 September, 19:40: the operator page gains the referee's heartbeat, its consecutive
  failures, and problems for a worker failing and retrying and for a worker silent before an
  overdue turn (`c66b36c`). The record does not say the stall prompted it.

**Cause.** Not recorded. The fixes made around it are `7ada973` (a worker ends the cs_server its
killed predecessor left, 28 September) and `9e9478d` (a tick starts `advance_turns` with no
console window, 29 September); neither is tied to turn 23 in the record.

**Resolution.** The turn published once the cause was fixed; the fix is not named.

**What went well.** The worker retried on its own and published nothing wrong.

**What went poorly.** Nothing about the stall was written down at the time: not the error, the
cause, the fix or how it was noticed. This review can only say so.

**Action items.**
- [x] The operator page shows the referee's heartbeat and a worker failing and retrying
  (`c66b36c`), and marks itself stale (OPS-4).
- [ ] OPS-3: a failure that repeats the same way N times alerts the operator.
- [ ] OPS-4: a push alert when a turn is overdue by more than one turn length.
- [ ] OPS-1: write an incident's error, cause and fix down when it happens, so the review has
  facts to work from.

Two later stalls are well recorded and may be the one meant here: turn 31 of `h3check` (1 to 3
October, no civ holding a planet, fixed by `hold_empty` in `ebe11cb`), and turn 44 in the
sandboxed referee (9 October, the client not readable within 180 seconds while the host was
locked, 98 retries from 03:04 to 21:10).
