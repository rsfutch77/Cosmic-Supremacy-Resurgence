Cosmic Supremacy: Resurgence v0.4.0: fleets and production queues in
multiplayer.

If you have v0.3.1, replace it with this. See "Updating from v0.3.1" below.
Multiplayer galaxies now need v0.4.0, and a v0.3.1 launcher says so when you
press Play.

## New in v0.4.0

**Fleets.** In a multiplayer galaxy you can form a fleet, rename it, give it
orders, add and remove ships, merge one fleet into another, and disband it.
Until now the server broke every fleet back into single ships.

**Production queues.** In a multiplayer galaxy you can create, edit, reorder
and delete production queues, including scans, and put planets on a queue. A
planet you build something else on by hand goes back to its queue when that
item is done, and setting a planet to Wealth takes it off the queue.

## Fixed in v0.4.0

**Governors are kept for every empire.** In v0.3.1 an empire with items in its
news list could still lose its governor changes.

**A reclaimed empire's fleets go with it.** When an empire is reclaimed after
too many missed turns, its fleets and the ships in them are removed along with
the rest of it.

## The website

[cosmicresurgence.com](https://cosmicresurgence.com) brings back the hall of
fame, the news and the galaxy history from the archive, and drops pages and
buttons that no longer worked.

## Updating from v0.3.1

1. Unzip v0.4.0 into a new folder.
2. Copy the `data` folder from your v0.3.1 folder into the new one, before you
   start the new launcher. It holds your login and your Single Player games.
3. Start the new launcher, and delete the old folder when you are done with it.

## Install

1. Download `CosmicSupremacy-Resurgence-v0.4.0.zip` below.
2. Unzip the whole folder somewhere you can write to. Desktop or Downloads is
   fine. Not Program Files, and not from inside the zip.
3. Run `CosmicSupremacyLauncher.exe` and pick a mode.

Leave the launcher open while you play. It runs the local game server, and
for Multiplayer it is what sends your turn.

Windows may still show "Windows protected your PC" the first time, because a
new signature takes time to earn Windows' trust. Click **More info**, then click **Run anyway**.

## Verifying the download

    SHA-256  87f764d056182e75c52aa859fb1b3b56b31fa13370068446261b8baf83fd7db6

    Get-FileHash CosmicSupremacy-Resurgence-v0.4.0.zip -Algorithm SHA256

## Reporting a problem

On the Log tab, press **Send Log** and copy the log reference it shows. Open an
[issue](https://github.com/rsfutch77/Cosmic-Supremacy-Resurgence/issues) with
the reference and what you were doing.
