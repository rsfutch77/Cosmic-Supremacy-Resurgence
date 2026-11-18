Cosmic Supremacy: Resurgence v0.7.1

If you have v0.7.0, the launcher offers this update and installs it for you.
Multiplayer galaxies still accept v0.7.0, so you can update when it suits you.

## Fixed in v0.7.1

These were fixed on the multiplayer server, so every launcher has them
already.

**Defending yourself no longer costs reputation.** When another empire
declared war on you, fighting back could still cost you reputation, as if you
had started the war. The empire that declared a war is now recorded as having
declared it, including for wars already under way, and the reputation lost
this way has been given back.

**A planet you leave without a governor stays without one.** Clearing a
planet's governor was undone at the next turn, and the old governor came
back.

**The diplomacy screen counts only the planets you know about.** The Planets
column counted every planet a player owns, including ones in systems you have
never explored. It now matches what your Recon screen shows.

**Turns end on a fixed schedule.** A galaxy's next turn now ends one turn
length after the last one ended, so a four-hour galaxy ends its turns at the
same times every day. Before, each turn also waited for the previous one to
be processed, and the times crept later by a minute or two a turn.

## Known issues

- Carrying out a scan in multiplayer is still undone at the next turn.
- A coat of arms chosen before you name your empire, then left with
  **Decide Later**, is not kept, and the box may show one you uploaded in
  another galaxy.

## Updating from v0.7.0

When you start the launcher, it downloads v0.7.1 and shows **Update to
v0.7.1**. Close the game, click it, and the launcher restarts into v0.7.1,
keeping your `data` folder where it is.

## Install

1. Download `CosmicSupremacy-Resurgence-v0.7.1.zip` below.
2. Unzip the whole folder somewhere you can write to. Desktop or Downloads is
   fine. Not Program Files, and not from inside the zip.
3. Run `CosmicSupremacyLauncher.exe` and pick a mode.

Leave the launcher open while you play. It runs the local game server, and
for Multiplayer it is what sends your turn.

Windows may still show "Windows protected your PC" the first time, because a
new signature takes time to earn Windows' trust. Click **More info**, then click **Run anyway**.

## Verifying the download

    SHA-256  3afe004617ac353185bd746e8b0fbdd1fd9a3bbca5cda2c1258fe7b298277a0b

    Get-FileHash CosmicSupremacy-Resurgence-v0.7.1.zip -Algorithm SHA256
