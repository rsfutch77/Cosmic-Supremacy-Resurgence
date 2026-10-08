# Single Player UI Verification Checklist

Human-eyes tests for the Single Player mode of a release build. You do not need to be a developer to run this. You do need to know how the game is meant to play.

## How to use it

Work top to bottom. Each test says what to do and what should happen. Tick it,
or write down what happened instead.

- Sections A to D are the first five minutes of a session.
- Sections E to L need a game that has run for a while, roughly turn 15 or later,
  so there is something to look at.
- Section M is the end of the session.

Some tests need the opponent to have done something (met you, built ships,
declared war). If the game has not got there yet, skip and come back rather than
marking a fail.

## Before you start

1. Unzip a fresh release folder somewhere writable. Do not test from a folder
   that has an older `data\` in it, a stale save index hides real bugs.
2. Nothing else Cosmic Supremacy should be running.
3. Start `CosmicSupremacyLauncher.exe`.

Your empire is **DemoPlayer** unless you rename it. The computer opponent is
**BadGuy**. Both names are expected.

---

## A. The launcher

| | Test | Do | Expect |
|---|---|---|---|
| A1 | Window opens | Double-click the launcher | The window appears, four modes listed: Tutorial, Demo, Single Player, Multiplayer. Status dot turns green and says the server is up |
| A2 | Mode text reads correctly | Read the Single Player card | Title and blurb are spelled correctly, no placeholder text, no cut-off words at the edge of the card |
| A3 | Hover feedback | Move the mouse over the Single Player button | The button lightens. It goes back when you move away |
| A4  | Game starts | Click **Play Single Player** | The game window opens within a few seconds. Launcher status reads "Single Player is running" in green |
| A5  | Controls appear | Look at the launcher after the game window opens | A bar appears with **Next Turn**, **Save**, **Load**, and a turn readout on the left |
| A6 | Turn readout | Read the left of the control bar | It shows the same turn number the game is showing |
| A7 | Second game refused | With the game running, click **Play Single Player** again | A dialog explains a game is already running and nothing new starts. The running game is untouched |
| A8 | Log pane | Click **show log** at the bottom | A black pane opens with recent lines including `[ai]` lines from the opponent. Click again and it closes |
| A9 | Controls disappear | Quit the game from inside the game, leave the launcher open | The control bar goes away and the status stops saying the game is running, within a few seconds |

## B. Starting a session

| | Test | Do | Expect |
|---|---|---|---|
| B1  | Galaxy loads | Wait for the game to finish loading | You are looking at a galaxy map with your homeworld, no error dialog, no "Failed to load Save-Game" |
| B2 | Home world prompt | Watch the first few seconds | The "Customize Your Home World" prompt appears. This is expected in this build, see Known limitations |
| B3  | Civ name accepted | Type a new civilisation name and confirm | The name is accepted, and appears afterwards wherever your empire is named (status bar, Diplomacy, Overview highscores) |
| B4 | Civ name survives a restart | Quit, relaunch Single Player | The name you chose is still there, not back to DemoPlayer |
| B5 | Coat of arms dialog | Open the coat of arms prompt if offered | The dialog opens and closes without an error. The image itself is blank, see Known limitations |
| B6 | Window behaviour | Alt-Tab away and back. Move the window | The game redraws correctly, no black or frozen areas |

## C. The status bar

Along the bottom of the game window. Every item here should be readable and
should agree with what the rest of the UI says.

| | Test | Do | Expect |
|---|---|---|---|
| C1  | Connected | Read the left end | It says **Connected**, not blinking red Disconnected |
| C2  | Disconnect is honest | Close the launcher while the game is up | It changes to a blinking red **Disconnected**. Restart the launcher before continuing |
| C3 | Player name | Read next to the connection status | Your civilisation name, matching what you set in B3 |
| C4  | Planet count | Compare with the Planets screen | The planet icon number equals the number of rows on the Planets screen |
| C5  | Ship count | Compare with the Ships screen | The ship icon number equals your ship count, fleets counted as their ships |
| C6  | Credits and income | Compare with the Overview financial report | Credits, then income per turn in brackets. A negative income is shown in red |
| C7 | Tick counter | Watch the right end | It shows a countdown or a halted state, and does not show a garbage value or a date from 1970 |

## D. The galaxy map

| | Test | Do | Expect |
|---|---|---|---|
| D1  | Camera controls | Pan, zoom in, zoom out, rotate | The view moves smoothly, nothing disappears, the map does not turn black |
| D2  | Select a planet | Left-click your homeworld | A white selection box appears around it, and the matching row highlights in the main window |
| D3 | Select back | Click a planet row in the Planets screen | The matching planet gets selected on the map |
| D4  | Centre camera | ALT-double-click a row in the Ships or Planets screen | The map jumps to centre on that object |
| D5  | Planet context menu | Right-click your homeworld on the map | The menu lists production options, Enter Planet View, Rename, Center Camera, and the rest. Nothing is blank or says "null" |
| D6  | Planet tooltip | Hover a planet | A tooltip shows the planet properties and commodities, values filled in |
| D7  | Sun tooltip | Hover a sun you have scouted | A tooltip appears including when the system was last scouted |
| D8 | Multi-select | CTRL-click two of your planets | Both get selection boxes, and a right-click offers the same production to both |
| D9  | Territory colour | Look at your territory, then at BadGuy's once you have met | Yours is cyan by default, theirs is a different colour, and the two are told apart easily |
| D10 | Change colour | Diplomacy screen, right-click BadGuy, Change Color | The map redraws their territory and ships in the new colour |

## E. Planets screen and Planet View

| | Test | Do | Expect |
|---|---|---|---|
| E1  | All planets listed | Open the Planets screen | Every planet you own is there, once, with name, system and coordinates |
| E2  | Columns populated | Read across a row | Population, Space, Ships in Orbit, Food, Production, Research, Income, Resources, Loyalty, Corruption all have values, not dashes or zeroes everywhere |
| E3  | Sorting | Click a column header, then click again | The list reorders, and reorders the other way on the second click |
| E4 | Sort criteria menu | Hover or right-click a column header | The list of sort criteria pops up and picking one visibly changes the order |
| E5 | Facility icons | Look next to a planet name after building a shipyard | The shipyard icon appears next to the name |
| E6 | Facility tooltip | Hover a planet name | A tooltip lists the facilities built there, and matches what you actually built |
| E7  | Enter Planet View | Double-click a planet row | The Planet View screen opens on that planet |
| E8  | Planet View heading | Read the heading | Planet name with coordinates, system name, population and military count, colonisation date, loyalty and corruption |
| E9  | Job reassignment shows | Click a citizen, right-click, change the job | The citizen icon changes job, and the resource tables update immediately, without waiting for a turn |
| E10  | Resource table adds up | Change one farmer to a scientist | Food output drops and science output rises in the table on the same click |
| E11 | Resource tooltips | Hover each row of the resource table | Tooltips explain the calculation, with numbers, not empty boxes |
| E12  | Food storage bar | Look at the food storage box | It shows a fill level, and the civilian and military split, and the level agrees with the surplus you are making |
| E13 | Recruitment slider | Drag the recruitment rate slider | The percentage updates as you drag, and the split between the two food boxes changes with it |
| E14  | Choose production | Click the current production icon | The production menu opens, listing facilities, ships and scans. Items you cannot build are greyed out with a tooltip saying why |
| E15  | Production takes | Pick a facility | The Planet View and the Planets screen both show it as the current production, with a turns-to-complete estimate |
| E16  | Production queue | Open the queue window, add three items, reorder by dragging | The queue shows what you added, in the order you left it, and it is still right when you reopen the window |
| E17 | Hurry production | Once something is over half done, open the production menu | The hurry option is enabled and shows a credit cost. If you cannot afford it, it is disabled |
| E18 | Ships in orbit box | Look at a planet with ships in orbit | Your ships are listed. Selecting one shows its crew underneath |
| E19  | Crew transfer | Move a crewman from planet to ship and back | Both the ship crew box and the planet population update immediately |
| E20 | Planetary defense box | Build a light turret, then look | The defense box shows the new strength |
| E21 | Browse planets | Use PageUp, PageDown and the mouse wheel in Planet View | You move through your planets in the order the Planets screen is sorted in |

## F. Ships and ship design

| | Test | Do | Expect |
|---|---|---|---|
| F1  | Ships listed | Open the Ships screen | Every ship you own, named for its design, with Condition, Speed, Firepower, Shields, Hitpoints, Upkeep, Crew, Command and ETA filled in |
| F2  | Undermanned in red | Look at a ship with too few crew | The crew number is red, and the ship icon is red |
| F3  | Give an order | Right-click a ship, choose Move To, hover a target | The cursor changes to the order icon, a route line is drawn, and a tooltip gives distance and ETA |
| F4  | Order takes | Click the target | The Command column shows the order and the ETA column counts down over following turns |
| F5  | Illegal target refused | Choose Colonize Planet and hover a planet that is already colonised | The cursor changes to the no pointer, no route is drawn, and a tooltip says why |
| F6 | Fleet handling | Select two ships, form a fleet, expand it with the [+] | The fleet appears as one row, expands to show its ships, and can be renamed |
| F7  | Ship design screen | Open it | Every design is listed, including the three seeded ones, with cost, resources, crew and firepower filled in |
| F8  | Create a design | Build a new design from components you have researched | The designer accepts it, it appears in the list, and it appears in the planet production menu |
| F9  | Design without a scanner | Try to save a design with no scanner fitted | The UI should not let you, or should warn. Note what happens either way, this one has crashed the game before |
| F10 | Deactivate a design | Untick the Active box | The design disappears from the production menu but stays in the list |

## G. Research

| | Test | Do | Expect |
|---|---|---|---|
| G1  | Tree renders | Open the Research screen | The full tree draws, colour coded: researched dark blue, available light blue, unavailable grey, current orange |
| G2  | Header line | Read the top | Technology name, turns to complete, points accumulated over points needed, empire science output, surplus and wastage |
| G3  | Pick a technology | Click an available one | It turns orange, the header updates to it, and accumulated points are not lost |
| G4  | Pick a distant one | Click a technology several steps away | The path to it turns brown, and the first prerequisite becomes the current research |
| G5 | Tech tooltips | Hover a technology name, then an item inside a box | Both give a description, and available technologies also give difficulty and estimated time |
| G6  | Completion shows | Play until a technology finishes | It turns dark blue, a news entry appears, and any unlocked components show up in the ship designer |

## H. Overview

| | Test | Do | Expect |
|---|---|---|---|
| H1  | Population overview | Open the Overview screen | The population graphic splits by job, and the totals match the Planets screen |
| H2  | Financial report | Read it | Credits and debits itemised, and the total matches the income figure in the status bar |
| H3 | Ship statistics | Read the ships report | Unmanned count, firepower and units, agreeing with the Ships screen |
| H4  | Ship resources | Read the resource box | Five resources, stock and per-turn gain, with the stock matching what your miners have produced |
| H5  | Highscores | Read the right-hand side | Both you and BadGuy appear, with scores and ranks, and the scores change over turns |
| H6 | Tooltips | Hover each figure | Every one gives a tooltip, none is blank |

## I. News

| | Test | Do | Expect |
|---|---|---|---|
| I1  | Events appear | Play a few turns, then open the News screen | Entries for research completed, colonies founded, production finished, ordered by turn |
| I2  | Filters work | Untick Battle Reports, then tick it again | The list shrinks and grows accordingly, with no leftovers of the filtered category |
| I3 | Turn range filter | Set a turn range | Only messages in that range are listed |
| I4 | Mark important | Right-click an entry, Mark as Important, then filter to important only | Only the marked entry is shown |
| I5 | Delete | Right-click an entry, Delete Item | It goes, and stays gone after switching screens and back |
| I6  | Jump to location | ALT-double-click an entry with a location | The map centres on where it happened |
| I7  | Battle report opens | Once a battle has happened, click a battle report | The full report opens, with both sides' losses filled in, and hovering the numbers gives more detail |
| I8 | Note | Create a private note | It saves, appears under Notes, and can be edited by double-clicking |

## J. Recon and scanning

| | Test | Do | Expect |
|---|---|---|---|
| J1  | Scouted planets | Send a scout out a few systems, then open the Recon screen | The systems you scouted are listed, with owner blank for uncolonised ones, and base food, production, science and resource values |
| J2  | Ships in vicinity | Once a BadGuy ship comes in range | It appears in Ships In Vicinity, with owner BadGuy and a location |
| J3 | Threat box | Once you are at war or neutral with BadGuy | Their ships appear in Ships Posing A Threat, with the approach arrow and distance |
| J4 | Recon filters | Filter by unsettled planets only | Only uncolonised planets remain listed |
| J5  | Scanning screen | Open it | Seven scan types listed on the right, each with a stock count. The counts are 0 until you build a CDA and some scans |
| J6  | Build and use a scan | Build a CDA, build a planetary scan, right-click a BadGuy planet and use it | The scan count drops by one, a scan report appears on the left, and double-clicking it opens their planet in Planet View |
| J7 | Scan on an empty planet | Try a planetary scan on an uncolonised planet | You are not charged a scan |

## K. Diplomacy

| | Test | Do | Expect |
|---|---|---|---|
| K1  | Opponent appears | Play until you meet BadGuy | They appear in Known Players, with the turn you met them and their known planet count |
| K2  | Stance shown | Read the Current Stance column | It reads the stance you actually have, and the tooltip explains it |
| K3  | Declare war | Right-click BadGuy, Declare War, and read the warning | The reputation cost is shown before you commit, and cancelling really cancels |
| K4  | Stance updates | After declaring | The stance column changes to war, their ships turn red on the map and in Recon, and a news entry records it |
| K5 | Propose a treaty | Right-click, Propose new treaty, fill it in, send | It appears in the treaties list as Pending, at the turn you sent it |
| K6 | Territory tickboxes | Untick Territory for BadGuy | Their territory stops being drawn on the map. Tick it back and it returns |

Note: BadGuy does not answer messages or treaties. Only your side of the
exchange is under test here.

## L. Taking a turn

This is the heart of Single Player. The launcher drives the turn, so watch both
windows.

| | Test | Do | Expect |
|---|---|---|---|
| L1  | Opponent gating | Watch the launcher turn readout right after a turn lands | It reads "turn N · BadGuy is thinking" and **Next Turn** is greyed out |
| L2  | Button returns | Wait | Once the opponent is done, the note clears and **Next Turn** becomes clickable again |
| L3 | Stuck opponent | If the opponent ever stops responding | After a wait the readout says "BadGuy not responding" and the button comes back, so you are never trapped |
| L4  | Turn advances | Click **Next Turn** | Buttons grey out while it works, the launcher log says it is ending the turn, and the game's turn number goes up by one |
| L5  | Window stays alive | Watch the launcher during the turn | It does not go white or say Not Responding. It is also the server, so it must keep answering |
| L6  | Game updates | Look at the game after the turn | Food, production, research, population and credits have all moved, and the news screen has new entries |
| L7  | Ships moved | Watch a ship with a destination | It is closer on the map, and the ETA has dropped by one |
| L8  | Screens refresh | Sit on the Planet View when the turn lands, then use the mouse wheel and sorting | Values update in place. Note if sorting or the mouse wheel misbehaves until you switch screens, this was an original bug and we want to know if it survived |
| L9  | Opponent is really playing | Over ten turns, watch BadGuy in the Overview highscores and on the map | Their score rises, their planet count rises, their ships move. A flat line means the opponent is not running |
| L10 | Ten turns in a row | Click Next Turn ten times, a few seconds apart | Every one lands. No dialog, no freeze, no turn skipped or doubled |

## M. Save, load and shutdown

| | Test | Do | Expect |
|---|---|---|---|
| M1  | Save | Click **Save** in the launcher | Buttons grey out briefly, then the log says it saved, and names the file |
| M2  | No error in game | Watch the game window during the save | No "Failed to save the Save-Game" dialog appears |
| M3  | Save file exists | Open `data\games` next to the launcher | A file named `savegame_turn<N>.dat` with the turn you saved at |
| M4  | Load dialog | Click **Load** | A file picker opens in `data\games`, then a confirmation warns that loading restarts the game and unsaved progress is lost |
| M5  | Cancel is safe | Cancel at the confirmation | Nothing happens. The running game is untouched |
| M6  | Load restores | Load the save from M1 | The game closes and reopens on the saved galaxy, at the saved turn, with your planets, ships, research and orders as they were |
| M7  | Opponent restarts too | After a load, take a turn | BadGuy is still playing, the gating in L1 still works |
| M8 | Save twice | Save at two different turns, then load the older one | Both files are listed, and the older one really loads the older state |
| M9  | Clean shutdown | Close the game, then the launcher | Both close without an error dialog and neither leaves a process running |
| M10 | Reopen | Start the launcher again and click Single Player | A new game starts cleanly. It will be the same starting galaxy, see Known limitations |

---

## Known limitations, do not report these

These are understood and already recorded. They are not bugs to file.

1. **The "Customize Your Home World" prompt appears every session.** The Single
   Player build is the one that can compute turns without a server, and the two
   go together. Correctly gated in the other modes.
2. **The coat of arms image is blank.** The server returns a placeholder image.
   The dialog working is worth testing, the picture is not.
3. **Fame is always 0.** The server returns a fixed value.
4. **Every new game is the same galaxy.** Single Player ships one pre-built
   galaxy. Per-session generation is a known open item.
5. **Saves are always named `singleplayer`.** Only the turn number tells two
   saves apart. A name prompt is a known open item.

## Reporting

For anything that fails, we need:

- The test number, for example **L4**.
- What you expected and what you saw.
- The turn number.
- A screenshot, especially for anything about layout, colour or a tooltip.
- `data\launcher.log` and `data\cs_server.log` from the release folder.

If the game crashed, say what you clicked immediately before, and whether it was
at a turn boundary. Those two facts decide where we look first.
