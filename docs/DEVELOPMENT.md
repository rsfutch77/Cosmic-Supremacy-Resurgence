# Development

Everything needed to work on the project. If you only want to *play*, you do not
need any of this, see the [README](../README.md) and download a release. If you
want to help but do not write code, skip to
[Help without writing code](#help-without-writing-code).

## Project goals

1. **Understand the original client**, extract assets, map out the HTTP API it
   expects, and document game mechanics (tech tree, ship design, galaxy rules).
2. **Build a compatible server**, a Python backend that speaks the same
   protocol so the unmodified (patched for localhost) client can connect.
3. **Preserve and share**, make the findings, tools, and server code available
   so anyone who remembers the game can help bring it back.
4. **Let players steer it**, the people who play the game decide what
   gets built next. See
   [Help without writing code](#help-without-writing-code).

## Help without writing code

You don't need to be a developer to shape this project. Two things matter most,
and both happen on the

with nothing more than a GitHub account.

**Pick the next feature.** Go to the [issue tracker](https://github.com/rsfutch77/Cosmic-Supremacy-Resurgence/issues)
If one of those features matters to you, comment 
or add a 👍 reaction to an issue that already asks for it. If what you want is
not on the list, open an issue describing it.

**Make the AI play better.** The computer opponent follows a written strategy,
[STRATEGY.md](../client/dev_tools/ai_player/STRATEGY.md), organised by the four
X's: explore, expand, exploit, exterminate. Veteran knowledge of gaming strategy is the key to making a fun AI to play against.
Leave a comment or recommend a change. 

**Test the game.** Playing the game and reporting what broke, as described in the
[README](../README.md#something-went-wrong), helps just as much.

## Repository layout

```
client/                  Client EXEs and .csgalaxy pass files
  dev_tools/             Memory viewer, snapshotting, turn-driving scripts
    ai_player/           Heuristic AI that plays a full 4X game
server/                  Python stub server
  dev_tools/             Save-blob parsing and injection tools
release/                 Player-facing launcher and release build script
docs/                    This file, the plans, and the RE reports
dist/                    Build output (gitignored)
```

## Setup

Requires Windows and Python 3.10+. `setup.ps1` installs [uv](https://docs.astral.sh/uv/),
pins the interpreter from `.python-version`, and builds `server\.venv`. Nothing
is installed system-wide except uv itself.

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

Start the stub server:

```powershell
.\run_server.ps1              # port 8888, the port the patched client expects
.\run_server.ps1 -Port 9000
```

Then launch a client with a galaxy pass file. The client takes exactly one
command-line argument, the path to a `.csgalaxy`, which is all that dragging
the file onto the EXE ever did:

```powershell
client\CosmicSupremacy_TestBed.exe client\TestBedGalaxy_local.csgalaxy
```

Or run the launcher straight from the checkout, which finds `client\` instead of
a release's `game\` folder and saves you a build:

```powershell
server\.venv\Scripts\python.exe release\launcher.py
```

## The server

`server/cs_server.py` is the stub backend, plain `http.server` from the standard
library with no framework. It answers the game API the client knows how to send,
`HTTP/1.0 POST /clientinterface.php` with `Content-Type:
application/x-cosmicsupremacy` and a body of
`action=<name>&userid=<int>&passhash='<hash>'&...`. Every action and its response
format is in the [reconstruction report](CosmicSupremacy_Reconstruction_Report.md).

It also serves a small page at `GET /`, which is where the game opens a browser
on first run, and `GET /enter-demo`, which hands back
`DemoGalaxy_local.csgalaxy` as a download.

What it keeps, all under `CS_DATA_DIR`, the checkout's `server\` by default and
the release's `data\` when the launcher sets it:

| What | Where |
|------|-------|
| Save blobs | `saves\`, a `.b64` and a `.json` sidecar per save, listed in `saves\index.json` |
| Governor blobs | `save_gov_<id>.dat` |
| Request log | `cs_server.log` |

Both blob kinds are stored and returned verbatim, the server never parses one.
Civ names live in memory for the life of the process, and coat-of-arms uploads
are accepted and discarded, `getcoa` answers with a 1x1 placeholder PNG. Neither
survives a restart, which is enough for local play and is a gap for a hosted
galaxy.

Multiplayer state is separate. `server/turn_store.py` holds a galaxy's turn blobs
and player submissions behind one interface with three implementations, a
directory, HTTP to `turn_server.py`, and Firestore plus Cloud Storage. Its
module docstring says which one a player gets and which one the referee gets.

## The three client EXEs

All three are the same 8 MB original binary with different byte patches applied.
**None of them is the pristine original**, `CosmicSupremacy.exe` is the least
modified, not unmodified. Its name is historical and misleading.

| EXE | Server target | Modified to | Used for |
|-----|---------------|-------------|----------|
| `CosmicSupremacy.exe` | `www.cosmicsupremacy.com` | skip some startup checks; still briefly shows the "analyzing system" popup | Tutorial, Demo |
| `CosmicSupremacy_TestBed.exe` | `127.0.0.1:8888` | TestBed galaxy join and load paths | TestBed galaxies |
| `CosmicSupremacy_Resurgence.exe` | `127.0.0.1:8888` | no "analyzing system" popup; works with custom turn lengths and the AI harness | Sandbox galaxy, AI harness |

A fourth exists but is not tracked: **`CosmicSupremacy_Player.exe`**, built by
`client/dev_tools/patch_hide_setup_prompts.py --build`. It is
`CosmicSupremacy_TestBed.exe` plus one byte that stops the coat-of-arms prompt
opening, and it is what a served multiplayer turn runs on. It is gitignored
because a patched client in the working tree is one `git commit -a` away from
entering the history.

**Resurgence is TestBed plus exactly 22 bytes at six sites** and nothing else,
measured by whole-file comparison. Those six are the T1-T5 turn-pipeline
bypasses, enumerated in the reconstruction report, and they are what let a client
advance a turn with no server. Two of them also sit on the guards for the
homeworld setup prompt, which is why that prompt appears on every session in the
Resurgence build and is correctly gated everywhere else. So the choice of build
is really the choice of whether a client may compute a turn:

| build | ticks without a server | setup prompts | role |
|-------|------------------------|---------------|------|
| Resurgence | yes | always offered | the referee, the AI harness |
| TestBed | no | gated as shipped | , |
| Player | no | none | a player's served turn |

`client/dev_tools/game_cycle.py` takes the build as a parameter
(`resolve_exe("player")`), and `server/player_turn.py` defaults players to the
player build while `server/referee.py` keeps Resurgence.

Neither Tutorial nor Demo reaches the internet, no DNS lookup for the old domain
occurs, so the `www.cosmicsupremacy.com` string above is never contacted. They
differ from each other on the local server, measured rather than assumed:

| Mode | Contacts `localhost:8888`? |
|------|----------------------------|
| Tutorial | Yes, `testconnection` at startup, one of the checks this EXE was modified to take |
| Demo | No, zero requests and zero connection attempts across a 45-second run with both loopback listeners up |

## Backlog

Single-player work. Multiplayer is tracked separately.

### [ ] Let the player name a save

Save writes `data\games\savegame_turn<N>.dat` and the name the engine records is
always `singleplayer`. A player with several games in flight has only the turn
number to tell them apart. Wants a name prompt, passed through to
`gamectl.Client.save_game` (which already takes one) and used for the filename.

### [ ] Per-session galaxy generation, for replayability

Single Player ships one pre-seeded `.dat`, so **every new game is the same
galaxy**. That is a regression against the pass-file path, which generates a
fresh galaxy on every launch, measured, two launches gave 170 vs 159 planets
with different sun layouts and different homeworld positions.

The fix reuses pieces that all already work: launch on the pass file to generate
a fresh galaxy, trigger `SaveGame`, inject the AI's designs into the blob
(`make_single_player_galaxy.py` does exactly this), then relaunch on the result.
Roughly a minute of "preparing your galaxy" at the start of a new game.

Deferred deliberately, the fixed galaxy is playable, and the buttons and the
seeding were worth proving first.

## Building a release

```powershell
powershell -ExecutionPolicy Bypass -File .\release\build.ps1
powershell -ExecutionPolicy Bypass -File .\release\build.ps1 -Version 0.2.0 -Clean
```

This freezes `release/launcher.py` into a single PyInstaller executable, stages
the player-facing folder, and zips it into `dist/`. Build dependencies live in
`release/.venv-build`, kept separate from `server/.venv` so a build never
perturbs the dev environment.

**`release/manifest.json` is the single source of truth** for what a mode is,
which EXE, which galaxy file, and whether it is shown. `build.ps1` reads it to
decide which client binaries to copy, so adding or retargeting a mode is a data
edit, not a code change.

The output is not committed: `dist/` duplicates the 8 MB client binaries already
tracked in `client/`, is regenerable from the manifest, and belongs on a GitHub
release instead of in the history.

PyInstaller emits the executable **directly into the staged folder**, so exactly
one launcher exists on disk after a build. This is deliberate: building
elsewhere and copying leaves a second, fully runnable launcher in the build tree
with no `game\` folder beside it, which fails with "Game files not found" and is
the first thing anyone double-clicks.

The build refuses to start while a launcher is running, since a live one holds
its own exe and `data\` open and the staging wipe would fail on a file lock.

Test the result from the staged folder rather than the repo, that is the only
layout a player will ever have.

### Pre-release tests

```powershell
powershell -ExecutionPolicy Bypass -File .\release\tests\run_all.ps1
```

Five runs, a few minutes. Close the launcher first, one already holding port
8888 makes the tests silently reuse it instead of exercising their own server.
The last four start and kill the real game, so leave the machine alone.

| Test | Covers |
|------|--------|
| `test_save_protocol.py` | The wire protocol against cs_server directly, no game: slot allocation from the `gameid=-1` sentinel, `savegamelist` format, blob round-trip, saving over a slot, delimiter injection in a save name |
| `test_status_cycle.py <mode>` | Drives the real launcher and client: click, report running, kill, recover |
| `test_external_status.py` | The same for a client started outside the launcher, where there is no child handle |

### Release checklist

1. `build.ps1 -Clean` and confirm the version is right.
2. Run `release\tests\run_all.ps1`, all green.
3. Run the staged launcher and click through every visible mode.
4. Confirm `data/` is created next to the launcher and both logs appear.
5. Tag `v<version>` and attach the `.zip` to a GitHub release, with the
   SHA256 the build printed.

The launcher is unsigned, so players will see a SmartScreen warning on first
run. This is expected and is documented in the release's `README.txt`.

## Dev tools

`client/dev_tools/` reads and drives a live client by inspecting its memory:

- `ejbo_viewer.py` / `ejbo_viewer.html`, live game-state viewer
- `snapshot.py`, `checkpoint.py`, capture and restore game state
- `game_cycle.py`, `fast_turns.py`, `advance_turns.py`, drive turns
- `make_single_player_galaxy.py`, build a galaxy with the AI's designs seeded in
- `xrefs.py`, static cross-references: who calls this address, and what else does that
  caller call. Decodes `E8 rel32` and 32-bit absolute operands only, so it is blind to
  virtual dispatch; a negative result is weak evidence, a positive one is checkable.
  Validate it on a known address before trusting it on a new one
- `find_refs.py`, the runtime counterpart: who currently points at this object, searching
  every reference form (`tag-8`, `tag-12`, `tag-52`) because searching one finds a fraction
- `ai_player/`, the heuristic AI; see its [STRATEGY.md](../client/dev_tools/ai_player/STRATEGY.md)

`server/dev_tools/` works on save blobs: `save_parser.py`, `diff_saves.py`, and
the `inject_*.py` family for planting civs, designs, ships and orders.

## Reference

- [CosmicSupremacy_Reconstruction_Report.md](CosmicSupremacy_Reconstruction_Report.md), the full reverse-engineering reference
- [CosmicSupremacy_Memory_Reconstruction_Report.md](CosmicSupremacy_Memory_Reconstruction_Report.md), memory layout and structures
- [CosmicSupremacy_Stat_Tables.md](CosmicSupremacy_Stat_Tables.md), component, facility and
  formula stats from the manual, joined to the object ids the client uses
- [verification/](verification/), the verification checklists run when a change touches their
  area, and [Checklist_Sources.md](verification/Checklist_Sources.md), the published standards
  they are built from
- [Singleplayer_UI_Checklist.md](verification/Singleplayer_UI_Checklist.md), the human-eyes test
  pass for a release build
