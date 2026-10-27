# Run Summary: v0.4 overnight run (27 October 2026)

All four lanes are merged into `v0.4` (head `f2b8192`). On the combined branch the merge suite passes: admirals_diplomacy 54, rival_citizens 11, fleets 115, fleet_and_queue 9, governors 66, people_and_hurry 18, refusal_notes 33, setup_prompts 37, wipe_civ 57, abandonment 57, rename 3, starting_kit 66, queues 142. Nothing was merged to main, tagged, pushed, deployed, or run against the live referee or Firebase.

- **Admirals and diplomacy:** no save on this machine holds either, so the layouts come from the client's own writer and reader (`ADMS > ADML > ADDT`, treaties as `TRTY` after the end of `SAVE`, stances in `KNPL`). Changes to either are now refused with a note instead of dropped silently, and an admiral id no longer rides along with ship orders. `set_turnlength.py --set waronly=0` makes a galaxy that can hold treaties. Needs: a session in the game, 14 admiral steps and 10 diplomacy steps (list below).
- **Citizens on a rival's planet:** one real case exists, `galaxy_demo` planet 138, where DemoPlayer's colonists live on Neighbor's planet. Changes to people on a planet or ship the submitter does not own were silently ignored and are now refused with a note. Needs: two short client checks from the main checkout (A and B below).
- **Release prep:** every suite that runs without the client passes. The failures it hit were environmental (gitignored folders, a network share). main is merged into v0.4, the manifest says 0.4.0, and `release/RELEASE_NOTES_v0.4.0.md` is drafted. Needs: Robert's answers to the five notes questions below, then the release steps.
- **Referee reads:** the one idle hour read 30 documents, and a 19-hour fit gives about 1,240 a day for the referee alone, under the 2,000 bar. The report item is ticked.

## Needs Robert

**Release notes questions** (from the release-prep lane):
1. The SHA-256 is a placeholder until the signed build exists.
2. Confirm the governor fix's wording: v0.3.1 lost the governors of any civ with news items.
3. Is the 12/48 missed-turn change news to players? There is a `v0.3.2` tag on main with no notes.
4. A v0.3.1 launcher treats a resumed turn with only fleet or queue changes as "carries no orders". Should galaxies get `min_build` 0.4.0?
5. Admirals, diplomacy and rival citizens are still refused. Should the notes say so?

**Release steps:** delete `docs/v0.4_Tracking.md` and this file, merge to main `--no-ff`, tag `v0.4.0`, signed build, `run_all.ps1` with the client, the release checklist, GitHub release, the download page and site deploy from main, and restaging the referee from the tag.

**Rival citizens, client checks** (from the main checkout, launcher closed, checkout `cs_server.py` running):
- A: `player_turn.py serve server\galaxy_demo\turns\0011.b64 --civ DemoPlayer`; on planet 138 try to move one of DemoPlayer's farmers to worker; `player_turn.py collect --name rivalA`.
- B: the same as Neighbor; move three farmers on planet 138 to worker; `collect --name rivalB`. This shows whether the owner's UI reaches another civ's citizens, which the merge currently accepts.

**Admirals session** on `clean.dat` with 30-day turns: a0 untouched; a1 create admiral "a1" (Assault, rule "Attack planets" on one planet); a2 assign it to a ship; a3 a second ship; a4 add "Wait until fully manned" and untick the first rule; a5 rename; a6 create a Scout admiral with "Scout suns in range"; a7 reassign a ship to it; a8 give an admiral-led ship a manual move; a9 look for unassign; a10 delete an idle admiral, then a busy one; a11 assign an admiral to a fleet; a12 take a ship out of that fleet; a13 advance one turn.

**Diplomacy session** on `control_fork_played.dat` with `waronly=0` (GoodGuy and BadGuy at peace): d0 untouched; d1 propose alliance plus shared map plus treaty length; d2 withdraw it; d3 propose credits for resources; d4 decline as BadGuy; d5 propose again and accept as BadGuy; d6 delete the declined treaty; d7 declare war; d8 advance a turn; d9 propose a cease-fire; d10 confirm the sandbox galaxy greys out both menu items.

**Website:** the 29 images for the four uns guide pages, whenever you want to stage them in the game.
