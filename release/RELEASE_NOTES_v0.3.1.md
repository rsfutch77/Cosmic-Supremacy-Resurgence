Cosmic Supremacy: Resurgence v0.3.1: creating an account with your email
address works.

If you have v0.3.0, replace it with this. See "Updating from v0.3.0" below.

## Fixed in v0.3.1

**Creating an account with your email address.** In v0.3.0, **Create
account** refused every email address with "Accounts are not open yet". It
works now, and the account keeps the galaxies this copy has already joined.
Logging in with Google was not affected.

## Fixed in Multiplayer, for every version

These were fixed in the galaxy itself, so they apply whichever version you
play with:

**Your civilization traits and homeworld are kept.** After you set them, the
game asked for them again every turn and did not apply them. Set them once
more and they stay.

**Governors are kept.** A governor you create, edit or assign to a planet is
still there after the turn. There is no way to unassign a governor, and one
that still governs a planet cannot be deleted.

## Updating from v0.3.0

1. Unzip v0.3.1 into a new folder.
2. Copy the `data` folder from your v0.3.0 folder into the new one, before you
   start the new launcher. It holds your login and your Single Player games.
3. Start the new launcher, and delete the old folder when you are done with it.

## Install

1. Download `CosmicSupremacy-Resurgence-v0.3.1.zip` below.
2. Unzip the whole folder somewhere you can write to. Desktop or Downloads is
   fine. Not Program Files, and not from inside the zip.
3. Run `CosmicSupremacyLauncher.exe` and pick a mode.

Leave the launcher open while you play. It runs the local game server, and
for Multiplayer it is what sends your turn.

Windows may still show "Windows protected your PC" the first time, because a
new signature takes time to earn Windows' trust. Click **More info**, then click **Run anyway**.

## Verifying the download

    SHA-256  <from the build>

    Get-FileHash CosmicSupremacy-Resurgence-v0.3.1.zip -Algorithm SHA256

## Reporting a problem

On the Log tab, press **Send Log** and copy the log reference it shows. Open an
[issue](https://github.com/rsfutch77/Cosmic-Supremacy-Resurgence/issues) with
the reference and what you were doing.
