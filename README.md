# Cosmic Supremacy — Resurgence

**A classic space strategy game, brought back online.**

[Cosmic Supremacy](http://www.cosmicsupremacy.com) was a multiplayer 4X space
strategy game released around 2006. You colonised planets, researched
technology, designed your own fleets, and fought for a galaxy. Turns took hours,
governors ran your empire while you slept, and players across every timezone
commanded enormous custom-built fleets against each other.

The servers went dark years ago. This project brings the game back.

---

## Play it

**[⬇ Download the latest release](https://github.com/rsfutch77/Cosmic-Supremacy-Resurgence/releases/latest)**

1. **Unzip the whole folder** somewhere you can write to — Desktop or Downloads
   is perfect. Don't run it from inside the zip, and don't put it in Program
   Files.
2. **Double-click `CosmicSupremacyLauncher.exe`**
3. **Pick a mode and click it.**

That's the whole thing. No account, no installer, no internet connection for offline play, and
nothing to configure. Leave the launcher window open while you play — it is also
the local game server.

> **You will see "Windows protected your PC" the first time.** That is expected.
> It appears because the launcher isn't code-signed, which costs a few hundred
> dollars a year and this is a free fan project. Click **More info**, then
> **Run anyway**. If you'd rather not take my word for it, the launcher's
> source is [right here](release/launcher.py) and you can build it yourself.

### What you can play

| Mode | What it is |
|------|------------|
| **Play the Tutorial** | The original guided walkthrough. Start here if you've never played — it teaches colonising, research, ship design and combat. |
| **View the Demo** | The original demo galaxy. A look at an advanced game. |
| **Play TestBed** | A full single-player galaxy against the computer, resolving turns on your own machine at your own pace. Now with the new, smarter Resurgence AI. |

### Something went wrong?

Click **show log** at the bottom of the launcher, and grab `data\launcher.log`
and `data\cs_server.log` from the release folder. Those three things are exactly
what we need — please include them when you
[open an issue](https://github.com/rsfutch77/Cosmic-Supremacy-Resurgence/issues).

---

## Status

The client and stub server support fully playable single-player gameplay: ships
move, turns tick, and saves round-trip correctly. The server handles all 15 game
API actions with response formats confirmed by binary analysis. Civilisation
customisation and save/load persistence work. A heuristic AI capable of playing a
full 4X game is in beta.

Multiplayer and hosted galaxies are not part of this release. All play is local.

## Help bring it back

This is a preservation and fan project, and contributions are welcome — protocol
analysis, server implementation, documentation, or just playing it and telling us
what broke.

Start with **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)** for setup, the server
protocol, and how to build a release. The
[reverse-engineering report](docs/CosmicSupremacy_Reconstruction_Report.md) and
the [development plan](docs/Development_Plan.md) have the deep detail.

## Licence

Cosmic Supremacy was created by Erwin, and the original game assets remain their
property. We have tried to reach out, and hope to involve them in the project.

This is an unofficial restoration, not affiliated with or endorsed by the
original author. It is free, in the spirit of the original game — it will never
be sold and will never carry advertising.
