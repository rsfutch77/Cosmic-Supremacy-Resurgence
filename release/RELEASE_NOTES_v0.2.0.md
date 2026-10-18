Cosmic Supremacy: Resurgence v0.2.0, the multiplayer public beta.

Cosmic Supremacy was a game played against other people, one turn every few
hours, in galaxies that ran for months. This release brings that back: a
permanent galaxy with 4-hour turns that anyone can join, alongside the
Tutorial, the Demo and Single Player from v0.1.

It is a beta. The galaxy is real and the turns count, but expect rough edges,
and please report them.

## New in v0.2.0

**Multiplayer.** The Multiplayer tab lists the open galaxies. Press **View** to
read what you would be joining, then **Join** and choose a player name. Your
empire arrives at the next turn, and **Play** appears on its row. Play your
turn, close the game, and the launcher sends your orders. The galaxy resolves
a turn every 4 hours whether or not you have played it.

**A new launcher window,** drawn in the game's own art instead of a plain
Windows dialog.

**Code-signed.** The launcher, the AI opponent and the two Resurgence game
clients are signed.

**Reporting a problem from the launcher.** Press **Send Log** on the Log tab and
the launcher uploads its log and shows you a log reference to quote in your
report. It sends nothing until you press it.

**Update notices.** When a galaxy needs a newer version than yours, the
launcher says so, links this page, and tells you how to keep your empire
when you update.

## Updating from v0.1

Saved games from v0.1 are not supported in v0.2.0. Unzip v0.2.0 into a new
folder, start a new game, and delete the old folder when you are done with it.

## Install

1. Download `CosmicSupremacy-Resurgence-v0.2.0.zip` below.
2. Unzip the whole folder somewhere you can write to. Desktop or Downloads is
   fine. Not Program Files, and not from inside the zip.
3. Run `CosmicSupremacyLauncher.exe` and pick a mode.

Leave the launcher open while you play. It runs the local game server, and
for Multiplayer it is what sends your turn.

Windows may still show "Windows protected your PC" the first time, because a
new signature takes time to earn Windows' trust. Click **More info**, then click **Run anyway**.

## What you can play

| Mode | What it is |
|------|------------|
| Play the Tutorial | The original guided walkthrough. Start here if you have never played. |
| View the Demo | The original demo galaxy, in an advanced state. |
| Play Single Player | A full galaxy against the Resurgence AI, resolving turns on your machine. |
| Multiplayer | The public beta galaxy, 4-hour turns, against other players. Needs an internet connection. |

## Known issues

- The Tutorial and the Demo run the original 2006 client, which is not
  signed. On a Windows 11 PC with Smart App Control turned on, Windows may
  block those two modes. Single Player and Multiplayer are not affected.
- Every new Single Player game starts on the same galaxy.

## Verifying the download

    SHA-256  195ccf167e4f2a93ea3d2fc862d3a4ccaeb4ddd028d2637eb683d8b6eb7bcd0a

    Get-FileHash CosmicSupremacy-Resurgence-v0.2.0.zip -Algorithm SHA256

## Reporting a problem

On the Log tab, press **Send Log** and copy the log reference it shows. Open an
[issue](https://github.com/rsfutch77/Cosmic-Supremacy-Resurgence/issues) with
the reference and what you were doing. If the launcher cannot reach the
internet, attach `data\launcher.log` and `data\cs_server.log` instead.

The restored site, with the full manual and wiki, is at
[cosmicresurgence.com](https://cosmicresurgence.com).
