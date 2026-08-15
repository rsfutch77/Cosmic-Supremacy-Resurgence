COSMIC SUPREMACY - RESURGENCE
=============================

A fan restoration of the 2006 multiplayer 4X space strategy game.
This build plays entirely on your own computer. No account, no internet
connection, nothing to install.


HOW TO PLAY
-----------

1. Unzip this whole folder somewhere you can write to - your Desktop or
   Downloads folder is perfect. Do NOT run it from inside the zip file, and
   do not put it in Program Files.

2. Double-click  CosmicSupremacyLauncher.exe

3. Pick a mode and click it. That's it.

Leave the launcher window open while you play. It is also the local game
server, and closing it will stop the game from saving.


"WINDOWS PROTECTED YOUR PC"
--------------------------

You will probably see a blue warning box the first time you run the launcher.
This is expected. It appears because the launcher is not code-signed - a
certificate costs a few hundred dollars a year, and this is a free fan project.

To continue: click "More info", then "Run anyway".

If you would rather not, everything here is open source. You can read the
launcher's code and build it yourself from the project repository.


THE MODES
---------

Play the Tutorial   The original guided walkthrough. Start here if you have
                    never played Cosmic Supremacy. It teaches colonising,
                    research, ship design and combat.

Watch the Demo      The original demo galaxy. A look at the game with nothing
                    at stake.

Play TestBed        A full single-player galaxy against the computer. Turns
                    resolve on your own machine.


KNOWN ISSUES IN THIS RELEASE
----------------------------

- The Tutorial and the Demo briefly show an "analyzing system" message when they
  start. That is left over from the original game's launch sequence. Let it
  finish.

- The tutorial has some rough edges inherited from the original game. They are
  known and are not caused by this restoration.

- In TestBed, the in-game Save and Load buttons do not work yet. This is the
  next thing being fixed.

- Only one game can run at a time. The launcher will tell you if one is already
  open rather than letting a second one close it.


IF SOMETHING GOES WRONG
-----------------------

Click "show log" at the bottom right of the launcher. That pane, plus these two
files, is exactly what we need to diagnose a problem:

    data\launcher.log     what the launcher found and started
    data\cs_server.log    what the game asked the server for

Please include all three when reporting an issue at

    https://github.com/rsfutch77/Cosmic-Supremacy-Resurgence/issues


WHAT'S IN THIS FOLDER
---------------------

CosmicSupremacyLauncher.exe   The launcher. This is the only thing you run.
README.txt                    This file.
game\                         The game client and galaxy files.
data\                         Created on first run. Logs and saved games.


CREDITS AND LICENCE
-------------------

Cosmic Supremacy was created by Erwin. The original game assets remain their
property. This is an unofficial preservation project, is not affiliated with or
endorsed by the original author, and is free - it will never be sold and never
carry advertising.

If you played the original and want to help bring it back, the project welcomes
contributions.
