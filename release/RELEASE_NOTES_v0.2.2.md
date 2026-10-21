Cosmic Supremacy: Resurgence v0.2.2: a bug-fix release for the multiplayer
public beta.

If you have v0.2.1, replace it with this. Your multiplayer empire and your
saved games carry over; see "Updating from v0.2.1" below.

## Fixed

**The launcher tells you when your turn cannot be sent because your empire
belongs to another copy of the game.** If you moved or reinstalled the game
without its `data` folder, the launcher signs in as a new player, and the
server turns down every turn it sends. 

## Updating from v0.2.1

1. Unzip v0.2.2 into a new folder.
2. Copy the `data` folder from your v0.2.1 folder into the new one, before you
   start the new launcher. It holds your saved games and the sign-in your
   multiplayer empire belongs to. Without it, the new launcher joins as a new
   player and cannot play your empire.
3. Start the new launcher, and delete the old folder when you are done with it.

The same applies whenever you move the game: move the whole folder, `data`
included. Run only one copy at a time.

## Install

1. Download `CosmicSupremacy-Resurgence-v0.2.2.zip` below.
2. Unzip the whole folder somewhere you can write to. Desktop or Downloads is
   fine. Not Program Files, and not from inside the zip.
3. Run `CosmicSupremacyLauncher.exe` and pick a mode.

Leave the launcher open while you play. It runs the local game server, and
for Multiplayer it is what sends your turn.

Windows may still show "Windows protected your PC" the first time, because a
new signature takes time to earn Windows' trust. Click **More info**, then click **Run anyway**.

## Verifying the download

    SHA-256  (filled in after the signed build)

    Get-FileHash CosmicSupremacy-Resurgence-v0.2.2.zip -Algorithm SHA256