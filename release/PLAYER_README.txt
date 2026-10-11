COSMIC SUPREMACY - RESURGENCE
=============================

A fan restoration of the 2006 multiplayer 4X space strategy game.
No account and no installer. The Tutorial, the Demo and Single Player run
entirely on your own computer; Multiplayer needs an internet connection.

    https://cosmicresurgence.com


HOW TO PLAY
-----------

1. Unzip this whole folder somewhere you can write to - your Desktop or
   Downloads folder

2. Double-click CosmicSupremacyLauncher.exe

3. Pick a mode

Leave the launcher window open while you play. It is also the local game
server, and closing it will stop the game from saving.


"WINDOWS PROTECTED YOUR PC"
--------------------------

The launcher is code-signed, and the publisher is Robert Futch. Windows may
still show a blue warning box the first time you run it, because a new
signature takes time to earn Windows' trust.

To continue: click "More info", check that the publisher reads
Robert Futch, then click "Run anyway". If the publisher reads "Unknown
publisher", do not run it; download the game again from
cosmicresurgence.com.

The launcher is open source. You can read its code and build it yourself
from the project repository.

The Tutorial and the Demo run the original 2006 game client, which is not
signed. On a Windows 11 PC with Smart App Control turned on, Windows may
block those two modes. Single Player and Multiplayer run signed clients.

If Windows says it needs Microsoft's WebView2 Runtime, the launcher asks
before installing it. Windows 11 already has it.


THE MODES
---------

Play the Tutorial   The original guided walkthrough. Start here if you have
                    never played Cosmic Supremacy. It teaches colonising,
                    research, ship design and combat.

View the Demo      The original demo galaxy. A look at the game with nothing
                    at stake.

Play Single Player  A full single-player galaxy against the computer. Turns
                    resolve on your own machine.

Multiplayer         Persistent galaxies against other players, the way the
                    original was played. Opens the list of galaxies: press
                    View on one to read what you would be joining, then Join.
                    Your empire arrives at the next turn and Play appears on
                    its row. Needs an internet connection.

UPDATING TO A NEW VERSION
-------------------------

Unzip the new version into a new folder, then copy the data folder from the
old one into it before you start the new launcher. The data folder holds your
saved games and the sign-in your multiplayer empire belongs to; a new
launcher without it joins as a new player and cannot play your old empire.


THE MANUAL AND THE ORIGINAL SITE
--------------------------------

The original site, www.cosmicsupremacy.com, is gone. Nothing there works any
more, and links to it from old forum posts and search results are dead.

The archived site has been rebuilt and is back online at a new address. Use
this one - it has the full game manual and wiki:

    https://cosmicresurgence.com


IF SOMETHING GOES WRONG
-----------------------

Click the Log tab at the top of the launcher. That page, plus these two files,
is exactly what we need to diagnose a problem:

    data\launcher.log     what the launcher found and started
    data\cs_server.log    what the game asked the server for

Please include all three when reporting an issue at

    https://github.com/rsfutch77/Cosmic-Supremacy-Resurgence/issues