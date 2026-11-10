# Cosmic Supremacy: Resurgence

**A classic space strategy game, brought back online.**

**[cosmicresurgence.com](https://cosmicresurgence.com)**, the restored site,
including the original manual and wiki.

Cosmic Supremacy was a multiplayer 4X space
strategy game released around 2006. You colonised planets, researched
technology, designed your own fleets, and fought for a galaxy. Turns took hours,
governors ran your empire while you slept, and players across every timezone
commanded enormous custom-built fleets against each other.

The servers went dark years ago and it appears the original backend was lost in an accident. This project brings the game back.

The original site, `www.cosmicsupremacy.com`, is gone. Nothing there works, and
links to it in old forum posts and search results are dead. The archived site is
rebuilt and served at **[cosmicresurgence.com](https://cosmicresurgence.com)**,
use that one.

**Not a developer? You can still help.** Vote on the next feature or suggest how
the AI should play, see
[Help without writing code](docs/DEVELOPMENT.md#help-without-writing-code).

---

## Play it

**[⬇ Download the latest release](https://github.com/rsfutch77/Cosmic-Supremacy-Resurgence/releases/latest)**

1. **Unzip the whole folder** somewhere you can write to, Desktop or Downloads
   is perfect. Don't run it from inside the zip, and don't put it in Program
   Files.
2. **Double-click `CosmicSupremacyLauncher.exe`**
3. **Pick a mode and click it.**

That's the whole thing. No installer and nothing to configure. The Tutorial,
the Demo and Single Player need no account and no internet connection. Leave
the launcher window open while you play, it is also the local game server.

The launcher updates itself. When a new release is out, it offers it and
installs it in place, keeping your login and your saved games.

> **You may see "Windows protected your PC" the first time.** The launcher and
> the game are code-signed, but a new signature takes time to earn Windows'
> trust. Click **More info**, then **Run anyway**. If you'd rather not take my
> word for it, the launcher's source is [right here](release/launcher.py) and
> you can build it yourself.

### What you can play

| Mode | What it is |
|------|------------|
| **Play the Tutorial** | The original guided walkthrough. Start here if you've never played, it teaches colonising, research, ship design and combat. |
| **View the Demo** | The original demo galaxy. A look at an advanced game. |
| **Play Single Player** | A full single-player galaxy against the computer, resolving turns on your own machine at your own pace. Now with the new, smarter Resurgence AI. |
| **Multiplayer** | Persistent galaxies against other players, the way the original was played. Turns resolve every few hours, and the launcher sends your orders. Log in with an email address or a Google account to join one. |

### Something went wrong?

On the launcher's **Log** tab, press **Send Log** and copy the log reference it
shows. [Open an issue](https://github.com/rsfutch77/Cosmic-Supremacy-Resurgence/issues)
with the reference and what you were doing.

---

## Status

The Tutorial, the Demo and Single Player run entirely on your own PC. Single
Player is played against the Resurgence AI, a rules-based opponent written for
this project.

Multiplayer is in beta on one sandbox galaxy. Fleets, production queues,
governors, admirals, diplomacy and the market all go through. Messages between
players do not yet.

## Help bring it back

This is a preservation and fan project, and contributions are welcome, protocol
analysis, server implementation, documentation, or just playing it and telling us
what broke.

Start with **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)** for setup, the server,
the backlog, and how to build a release. The
[reverse-engineering report](docs/CosmicSupremacy_Reconstruction_Report.md) has
the deep detail.

## Use of Generative AI

I'm a fan of the original game. GenAI will not be used to create new art assets for the game, and the "AI" players will not use GenAI, the game itself is all the original code as written by the original developers with the minimal necessary patches described in plain English. My goal with this project was to relaunch the orginal game with the original code, just with a modern server to replace the server that was lost. To that end, I only use GenAI to help me understand how this puzzle fits together, decoding how the original server used to work and which commands it used to send.  Cybersecurity was also not left up to GenAI to decide. The rebuilt server uses modern best practice security checklists from Google and Microsoft that you can find here in the repo. 

## Licence

Cosmic Supremacy was created by Erwin, and the original game assets remain their
property. I have tried to reach out to involve them in the project, and would be happy to turn over the assets so that they can restore the server that was lost. 

This is an unofficial restoration, not affiliated with or endorsed by the
original author. It is free, in the spirit of the original game, it will never
be sold and will never carry advertising.

The code written for this project, the server, the launcher, the development
tools and the documentation, is released under the
[MIT License](LICENSE), so anyone is free to build on it.

That licence covers **only** the new work. The original game binaries
(`CosmicSupremacy*.exe`) and the `.csgalaxy` files are not mine to license: they
remain the property of their author and are included purely so the game can be
played again. They will be removed on request by the copyright holder.
