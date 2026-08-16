**Cosmic Supremacy is playable again, on your own machine.**

This is the first release aimed at players rather than developers. There is no
account, no installer, and no internet connection required. Download, unzip,
double-click one file.

## How to play

1. Download `CosmicSupremacy-Resurgence-v0.1.0.zip` below and unzip the whole
   folder somewhere you can write to — Desktop or Downloads is ideal. Don't run
   it from inside the zip, and don't put it in Program Files.
2. Double-click **CosmicSupremacyLauncher.exe**
3. Pick a mode and click it.

Leave the launcher window open while you play — it is also the local game
server.

> **Windows will warn you the first time.** The launcher isn't code-signed —
> a certificate costs a few hundred dollars a year and this is a free fan
> project. Click **More info**, then **Run anyway**. The launcher's source is in
> this repository if you'd rather build it yourself.

## What's in it

| Mode | What it is |
|------|------------|
| **Play the Tutorial** | The original guided walkthrough. Start here if you've never played. |
| **View the Demo** | The original demo galaxy — a look at the game in an advanced state. |
| **Play Single Player** | A full galaxy against the computer, resolving turns locally, at your own pace. Now with the new Resurgence AI rather than the original's. |
| **Multiplayer** | Coming soon — greyed out for now. |

Saving and loading work. Your games live in `data\saves` next to the launcher;
keep that folder if you move the game elsewhere.

## Known rough edges

- The Tutorial and Demo briefly show an "analyzing system" message on startup.
  That's left over from the original launch sequence — let it finish.
- The tutorial has quirks inherited from the original game. They're known, and
  they aren't caused by this restoration.
- Only one game can run at a time. The launcher will say so rather than letting
  a second one close the first.

## If something goes wrong

Click **show log** at the bottom of the launcher, and attach `data\launcher.log`
and `data\cs_server.log` to an issue. Those three things are what we need.

## Verifying the download

```
SHA256  6aa5942622e161d891072dbd8731aa97fd067572c6dfb4c11e598af4dace3408
```

## Credits

Cosmic Supremacy was created by Erwin around 2006. The original game assets
remain their property and are included here only so the game can be played
again — see `LICENSE.txt` in the zip. This is an unofficial preservation
project, free and never commercial.
