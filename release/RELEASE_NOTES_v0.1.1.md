Cosmic Supremacy: Resurgence v0.1.1 , a bug-fix release for the two faults
players reported against v0.1.0. Both were in Single Player, and the first one
made the game unwinnable.

If you have v0.1.0, replace it with this. Saved games from v0.1.0 still load,
but a save made before the fix carries the fault with it , see below.

## Fixed

**Research did nothing.** Selecting a technology and ending the turn added no
research points, for the whole game, so the technology tree was unreachable and
the computer opponent eventually out-teched you by default. Your treasury kept
growing the entire time, which is what made it look like the game was working.

The cause was four bytes in the shipped galaxy file marking your empire as one
the game should not simulate , a leftover from how the galaxy was generated.
Your research, your score and your empire statistics were all being skipped.
Fixed in the galaxy this release ships.

**The computer opponent remembered your last game.** It kept the map it had
explored, keyed to the galaxy rather than to the game, and Single Player always
starts the same galaxy. So from your second game onward it began already
knowing where everything was, including your home system, and could send a
colony ship straight at you on turn one without ever having scouted. It also
stopped the launcher from waiting for the opponent to take its turn.

Starting a new game now clears what the opponent learned. Loading a save keeps
it, which is correct , that is the same galaxy it explored.

## Not fixed yet

**The opponent stays peaceful for the first hour or so.** It cannot build an
armed ship until it has researched its first weapon, and at the starting rate
that takes a while, after which the ship still has to be built, crewed and
flown to you. This is working as built rather than broken, but it makes the
early game quiet. It is on the list.

## If you were already playing a v0.1.0 game

A save made under v0.1.0 has the fault baked into it, so loading it into v0.1.1
will not bring your research back. Starting a new game will. Nothing is wrong
with the old save otherwise , it just will not research.

## Install

1. Download `CosmicSupremacy-Resurgence-v0.1.1.zip` below.
2. Unzip the whole folder somewhere you can write to. Desktop or Downloads is
   fine. Not Program Files, and not from inside the zip.
3. Run `CosmicSupremacyLauncher.exe` and pick a mode.

Leave the launcher open while you play. It is also the local game server, and
closing it stops the game from saving.

Windows will show "Windows protected your PC" the first time, because the build
is not code-signed. Click **More info**, then **Run anyway**. The launcher's
source is [in this repository](https://github.com/rsfutch77/Cosmic-Supremacy-Resurgence/blob/v0.1.1/release/launcher.py) if you would rather build it
yourself.

## What you can play

| Mode | What it is |
|------|------------|
| Play the Tutorial | The original guided walkthrough. Start here if you have never played. |
| View the Demo | The original demo galaxy, in an advanced state. |
| Play Single Player | A full galaxy against the new Resurgence AI, resolving turns on your machine. |
| Multiplayer | Not in this release. |

## Verifying the download

    SHA-256  1e77d478dacfbb7a3ce6dbca2272a5d90a1a33eac2cad5cfe18b0eba7082947f

    Get-FileHash CosmicSupremacy-Resurgence-v0.1.1.zip -Algorithm SHA256

## Reporting a problem

Click **show log** at the bottom of the launcher, then attach that pane plus
`data\launcher.log` and `data\cs_server.log` to an
[issue](https://github.com/rsfutch77/Cosmic-Supremacy-Resurgence/issues).

Thanks to the players who reported these , both came from the first round of
feedback after v0.1.0.

The restored site, with the full manual and wiki, is at
[cosmicresurgence.com](https://cosmicresurgence.com).
