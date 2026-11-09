Cosmic Supremacy: Resurgence v0.5.0: diplomacy, admirals and the market in
multiplayer, and a launcher that updates itself.

If you have v0.4.1, replace it with this. See "Updating from v0.4.1" below.
Multiplayer galaxies still accept v0.4.1, so you can update when it suits you.
This is the last update you have to download by hand.

## New in v0.5.0

**The launcher updates itself.** When a newer release is out, the launcher
offers it. It downloads the release, checks its signature, and replaces itself
without asking for administrator rights. Your `data` folder is not touched, and
if the new version fails to start, the old one comes back.

**Diplomacy and admirals.** In a multiplayer galaxy you can propose, accept and
refuse treaties, declare war and change stances, and an accepted lump sum moves
once. Admirals you create and give rules to stay assigned to their ships and act
on them.

**The market.** Sales and purchases you make in a multiplayer galaxy go through,
priced from the market as you saw it.

**Coats of arms.** A coat of arms you drag into the civilization name dialog is
uploaded and shown to other players once it is approved. Your civilization's
name and its reveal checkbox are kept too.

**Logging in keeps your guest empire.** Galaxies this install joined as a guest
move to your account when you log in.

## Better in v0.5.0

**Outages are named.** When your turn cannot be read or sent, the launcher says
whether the problem is your internet connection, the galaxy server or file
storage. It notices a lost connection within seconds. A turn you finish while
offline is kept and sent once the connection is back, and the launcher says
"turn not sent yet" until then.

**The Galaxies page.**

- Each row shows the deadline in your local time as well as the time left.
- Play greys out while your turn is being sent and processed.
- A galaxy whose turns are overdue reads "stopped".
- If a listing fails, the last rows stay with "?" where the numbers were.
- Logging in or out lists the galaxies again straight away.
- The join notice says how many missed turns in a row remove an empire.

**The game window under the launcher.** The status bar shows only the turn
number. Its countdown was wrong under the launcher, and the launcher shows the
deadline instead.

**Systems you have left.** A system you no longer see is shown as you last saw
it, instead of disappearing.

**The Single Player opponent.** It spends its starting traits, keeps its
research going, and stops farming once its planets have enough food.

## Updating from v0.4.1

1. Unzip v0.5.0 into a new folder.
2. Copy the `data` folder from your v0.4.1 folder into the new one, before you
   start the new launcher. It holds your login and your Single Player games.
3. Start the new launcher, and delete the old folder when you are done with it.

From v0.5.0 on, the launcher updates itself and keeps `data` where it is.

## Install

1. Download `CosmicSupremacy-Resurgence-v0.5.0.zip` below.
2. Unzip the whole folder somewhere you can write to. Desktop or Downloads is
   fine. Not Program Files, and not from inside the zip.
3. Run `CosmicSupremacyLauncher.exe` and pick a mode.

Leave the launcher open while you play. It runs the local game server, and
for Multiplayer it is what sends your turn.

Windows may still show "Windows protected your PC" the first time, because a
new signature takes time to earn Windows' trust. Click **More info**, then click **Run anyway**.

## Verifying the download

    SHA-256  <filled in after the build>

    Get-FileHash CosmicSupremacy-Resurgence-v0.5.0.zip -Algorithm SHA256

## Reporting a problem

On the Log tab, press **Send Log** and copy the log reference it shows. Open an
[issue](https://github.com/rsfutch77/Cosmic-Supremacy-Resurgence/issues) with
the reference and what you were doing.
