Cosmic Supremacy: Resurgence v0.2.1: a bug-fix release for the multiplayer
public beta.

If you have v0.2.0, replace it with this. Your multiplayer empire and your
saved games carry over; see "Updating from v0.2.0" below.

## Fixed

**The Tutorial and the Demo are signed.** In v0.2.0 they ran an unsigned copy
of the original 2006 client, and on a Windows 11 PC with Smart App Control
turned on, Windows could block those two modes. Every game client in the
download is now signed.

**The Galaxies page shows your turn as played as soon as it is sent.** Sending
your turn used to leave the row reading "your turn" until the page was next
refreshed. It now reads "turn played" straight away.

**Clearer wording** in the launcher's hint while a join request waits for the
next turn.

## Updating from v0.2.0

1. Unzip v0.2.1 into a new folder.
2. Copy the `data` folder from your v0.2.0 folder into the new one, before you
   start the new launcher. It holds your saved games and the sign-in your
   multiplayer empire belongs to. Without it, the new launcher joins as a new
   player and cannot play your empire.
3. Start the new launcher, and delete the old folder when you are done with it.

## Install

1. Download `CosmicSupremacy-Resurgence-v0.2.1.zip` below.
2. Unzip the whole folder somewhere you can write to. Desktop or Downloads is
   fine. Not Program Files, and not from inside the zip.
3. Run `CosmicSupremacyLauncher.exe` and pick a mode.

Leave the launcher open while you play. It runs the local game server, and
for Multiplayer it is what sends your turn.

Windows may still show "Windows protected your PC" the first time, because a
new signature takes time to earn Windows' trust. Click **More info**, then click **Run anyway**.

## Verifying the download

    SHA-256  116f324a4472a66201f8213918f7d3be362c4cebb4c526ea479d01ab88debf7f

    Get-FileHash CosmicSupremacy-Resurgence-v0.2.1.zip -Algorithm SHA256