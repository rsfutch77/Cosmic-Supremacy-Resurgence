Cosmic Supremacy: Resurgence v0.1.0 , the first playable build of the
restoration. The 2006 client, patched to talk to a server that runs on your own
machine, packaged with a launcher that starts both.

No account, no installer, no internet connection. Unzip it and run it.

## Install

1. Download `CosmicSupremacy-Resurgence-v0.1.0.zip` below.
2. Unzip the whole folder somewhere you can write to. Desktop or Downloads is
   fine. Not Program Files, and not from inside the zip.
3. Run `CosmicSupremacyLauncher.exe` and pick a mode.

Leave the launcher open while you play. It is also the local game server, and
closing it stops the game from saving.

Windows will show "Windows protected your PC" the first time, because the build
is not code-signed. Click **More info**, then **Run anyway**. The launcher's
source is [in this repository](https://github.com/rsfutch77/Cosmic-Supremacy-Resurgence/blob/v0.1.0/release/launcher.py) if you would rather build it
yourself.

## What you can play

| Mode | What it is |
|------|------------|
| Play the Tutorial | The original guided walkthrough. Start here if you have never played. |
| View the Demo | The original demo galaxy, in an advanced state. |
| Play Single Player | A full galaxy against the new Resurgence AI, resolving turns on your machine. |
| Multiplayer | Not in this release. |

## Verifying the download

    SHA-256  83ece812f1601a5b417b789aecf8752b92d270a843c400872d2b83ada16f6f97

    Get-FileHash CosmicSupremacy-Resurgence-v0.1.0.zip -Algorithm SHA256

## Reporting a problem

Click **show log** at the bottom of the launcher, then attach that pane plus
`data\launcher.log` and `data\cs_server.log` to an
[issue](https://github.com/rsfutch77/Cosmic-Supremacy-Resurgence/issues).

The restored site, with the full manual and wiki, is at
[cosmicresurgence.com](https://cosmicresurgence.com).
