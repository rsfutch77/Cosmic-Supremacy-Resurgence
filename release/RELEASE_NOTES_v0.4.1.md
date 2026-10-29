Cosmic Supremacy: Resurgence v0.4.1

If you have v0.4.0, replace it with this. See "Updating from v0.4.0" below.
Multiplayer galaxies still accept v0.4.0, so you can update when it suits you.

## Fixed in v0.4.1

**The Inactivity penalty no longer grows while you play.** From turn 73, the
food tooltip showed an "Inactivity" line that grew by one point every turn. It
cut food, and cut military recruitment even harder, until a game was
unplayable. The game uses it to wind down empires whose players have left, but
it was hitting every empire, active or not.

- **Multiplayer:** this was fixed on the server, so every launcher has it
  already. An empire that sends its turn is back to 0% the turn after. An
  empire whose player stops sending turns still starts to lose ground 72 turns
  after its last one.
- **Single Player:** fixed in this launcher. A saved game that already shows
  the penalty is back to 0% within a turn of pressing **Next Turn**, for both
  empires.

## Updating from v0.4.0

1. Unzip v0.4.1 into a new folder.
2. Copy the `data` folder from your v0.4.0 folder into the new one, before you
   start the new launcher. It holds your login and your Single Player games.
3. Start the new launcher, and delete the old folder when you are done with it.

## Install

1. Download `CosmicSupremacy-Resurgence-v0.4.1.zip` below.
2. Unzip the whole folder somewhere you can write to. Desktop or Downloads is
   fine. Not Program Files, and not from inside the zip.
3. Run `CosmicSupremacyLauncher.exe` and pick a mode.

Leave the launcher open while you play. It runs the local game server, and
for Multiplayer it is what sends your turn.

Windows may still show "Windows protected your PC" the first time, because a
new signature takes time to earn Windows' trust. Click **More info**, then click **Run anyway**.

## Verifying the download

    SHA-256  c43b1900def4989ed4d3a760592280ba85b09473bd1b64c4dfbaa5ce14f7abac

    Get-FileHash CosmicSupremacy-Resurgence-v0.4.1.zip -Algorithm SHA256
