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
server, and it is where you end your turn.

> **Windows will warn you the first time.** The launcher isn't code-signed —
> a certificate costs a few hundred dollars a year and this is a free fan
> project. Click **More info**, then **Run anyway**. The launcher's source is in
> this repository if you'd rather build it yourself.

## What's in it

| Mode | What it is |
|------|------------|
| **Play the Tutorial** | The original guided walkthrough. Start here if you've never played. |
| **View the Demo** | The original demo galaxy — a look at the game in an advanced state. |
| **Play Single Player** | A full galaxy against the Resurgence AI, resolving turns locally, at your own pace. |
| **Multiplayer** | Coming soon — greyed out for now. |

## Single Player

**A computer opponent.** Single Player is played against the Resurgence AI,
which expands, colonises, researches, builds and crews ships, declares war and
attacks. It plays under the same fog of war you do — it has to find you before
it can fight you.

**Turn controls live in the launcher.** A Single Player game shows a control bar
in the launcher window with **Next Turn**, **Save** and **Load**, plus the
current turn number.

**Next Turn waits for your opponent.** The button greys out and reads
*"BadGuy is thinking"* until the AI has finished deciding. That is not a
cosmetic delay — advancing early would make the opponent silently skip the turn
entirely. It typically takes well under a second.

**Saving.** Save writes a file into `data\games\` next to the launcher, and Load
restarts the game from one. Keep that folder if you move the game elsewhere.

## Known rough edges

- **The Single Player galaxy is the same every game.** Replayable galaxies are
  planned; for now every new game starts from the same map.
- **Saves are named by turn number only**, so several games in progress are
  distinguishable only by how far along they are.
- **The opponent declares war without a diplomatic penalty.** A human player
  pays a reputation cost for declaring; the AI currently does not, and you are
  not notified when it happens. A known imbalance, not a design choice.
- The AI is a genuine 4X player but not a strong one — it will out-expand an
  idle player comfortably, and an attentive one should beat it.
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
SHA256  47e577c88705eafe12351fe90f9a8852158eaf285d289537dec696f623ce23fe
```

## For developers

`release/manifest.json` is the single source of truth for what a mode is. Beyond
the EXE and galaxy pairing it carries `ai` (which civ the opponent plays) and
`controls` (whether the launcher shows its turn controls).

Single Player launches `CosmicSupremacy_Resurgence.exe` rather than
`CosmicSupremacy_TestBed.exe` — measured, not preference: launched on a `.dat`,
TestBed loads the save correctly and then never resolves a turn, while
Resurgence advances normally. See `docs/DEVELOPMENT.md`.

## Credits

Cosmic Supremacy was created by Erwin around 2006. The original game assets
remain their property and are included here only so the game can be played
again — see `LICENSE.txt` in the zip. This is an unofficial preservation
project, free and never commercial.
