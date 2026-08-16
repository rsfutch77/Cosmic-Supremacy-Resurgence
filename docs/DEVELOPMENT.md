# Development

Everything needed to work on the project. If you only want to *play*, you do not
need any of this — see the [README](../README.md) and download a release.

## Project goals

1. **Understand the original client** — extract assets, map out the HTTP API it
   expects, and document game mechanics (tech tree, ship design, galaxy rules).
2. **Build a compatible server** — a Python backend that speaks the same
   protocol so the unmodified (patched for localhost) client can connect.
3. **Preserve and share** — make the findings, tools, and server code available
   so anyone who remembers the game can help bring it back.

## Repository layout

```
client/                  Client EXEs and .csgalaxy pass files
  dev_tools/             Memory viewer, snapshotting, turn-driving scripts
    ai_player/           Heuristic AI that plays a full 4X game
server/                  Python stub server
  dev_tools/             Save-blob parsing and injection tools
release/                 Player-facing launcher and release build script
docs/                    This file, the development plan, and the RE reports
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
command-line argument — the path to a `.csgalaxy` — which is all that dragging
the file onto the EXE ever did:

```powershell
client\CosmicSupremacy_TestBed.exe client\TestBedGalaxy_local.csgalaxy
```

Or run the launcher straight from the checkout, which finds `client\` instead of
a release's `game\` folder and saves you a build:

```powershell
server\.venv\Scripts\python.exe release\launcher.py
```

## The three client EXEs

All three are the same 8 MB original binary with different byte patches applied.
**None of them is the pristine original** — `CosmicSupremacy.exe` is the least
modified, not unmodified. Its name is historical and misleading.

| EXE | Server target | Modified to | Used for |
|-----|---------------|-------------|----------|
| `CosmicSupremacy.exe` | `www.cosmicsupremacy.com` | skip some startup checks; still briefly shows the "analyzing system" popup | Tutorial, Demo |
| `CosmicSupremacy_TestBed.exe` | `127.0.0.1:8888` | TestBed galaxy join and load paths | TestBed galaxies |
| `CosmicSupremacy_Resurgence.exe` | `127.0.0.1:8888` | no "analyzing system" popup; works with custom turn lengths and the AI harness | Sandbox galaxy, AI harness |

Neither Tutorial nor Demo reaches the internet — no DNS lookup for the old domain
occurs, so the `www.cosmicsupremacy.com` string above is never contacted. They
differ from each other on the local server, measured rather than assumed:

| Mode | Contacts `localhost:8888`? |
|------|----------------------------|
| Tutorial | Yes — `testconnection` at startup, one of the checks this EXE was modified to take |
| Demo | No — zero requests and zero connection attempts across a 45-second run with both loopback listeners up |

### Bind both loopback addresses, not just 127.0.0.1

The client connects to the *name* `localhost`, and Windows resolves that to `::1`
before `127.0.0.1`. A server bound only to IPv4 leaves the client's first attempt
in `SynSent` against a port nothing is listening on; it reaches the server only
after giving up and retrying over IPv4. Measured: with an IPv4-only listener, no
request arrived in 35 seconds of polling; with a `::1` listener also up, the
first `testconnection` arrived in the same millisecond the client started.

`release/launcher.py` therefore starts two listeners, `127.0.0.1` and `::1`.
Two loopback sockets rather than one dual-stack socket on `::`, because `::`
would also accept LAN connections. `run_server.ps1` still binds IPv4 `0.0.0.0`
and has the same first-connection delay.

`Resurgence` and `TestBed` differ from each other by 22 bytes of code patches.
Reconciling all three into one binary is open work — `Resurgence` is the
furthest along and the natural target. When it happens the launcher needs only a
`release/manifest.json` edit.

### Patching

Bug fixes go in as targeted binary patches, not source changes — the client is a
release-build MFC/C++ app and recompilation is not pursued. Locate in Ghidra,
patch bytes, document the offset and the before/after. Existing patch tools:

- `client/dev_tools/patch_turn_floor.py` — lowers the engine's 60-second minimum
  turn length to 1 second, so a 300-turn game takes minutes instead of hours.
- `client/dev_tools/patch_hide_next_turn.py` — hides the Next Turn button in the
  TestBed dialog while leaving Save and Load working.

Both refuse to run unless they find exactly the bytes they expect, and both
write a `.bak` first, so a wrong offset fails loudly instead of corrupting the
client.

## Server

`server/cs_server.py` is a stdlib-only HTTP server — despite `requirements.txt`,
nothing in it imports FastAPI, uvicorn, bcrypt, PyJWT or aiosqlite. That is what
lets the release launcher embed it in-process rather than shipping a second
executable.

It handles all 15 game API actions. Protocol, from binary analysis:

- `HTTP/1.0 POST` to `/clientinterface.php?`
- `Content-Type: application/x-cosmicsupremacy`
- Body: `action=<name>&userid=<int>&passhash='<hash>'&...`

Environment variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `CSPORT` | `8888` | Listen port |
| `CS_DATA_DIR` | the `server/` directory | Where the log, `saves/`, governor blobs and `loadgame_blob.b64` live |
| `CS_GALAXY_DIR` | unset | Where `/enter-demo` looks for the demo pass file |

`CS_DATA_DIR` exists for the frozen launcher: PyInstaller unpacks the module into
a temporary directory that Windows deletes on exit, so anything written relative
to `__file__` there would vanish with it.

Every `savegame` POST is persisted verbatim to `saves/` — the raw base64 exactly
as it came off the wire, plus a `.json` sidecar and the full request body.
Nothing is overwritten; each save gets a sequence number so a series can be
diffed. `server/dev_tools/save_parser.py` and `diff_saves.py` read them.

## Building a release

```powershell
powershell -ExecutionPolicy Bypass -File .\release\build.ps1
powershell -ExecutionPolicy Bypass -File .\release\build.ps1 -Version 0.2.0 -Clean
```

This freezes `release/launcher.py` into a single PyInstaller executable, stages
the player-facing folder, and zips it into `dist/`. Build dependencies live in
`release/.venv-build`, kept separate from `server/.venv` so a build never
perturbs the dev environment.

**`release/manifest.json` is the single source of truth** for what a mode is —
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

Test the result from the staged folder rather than the repo — that is the only
layout a player will ever have.

### Pre-release tests

```powershell
powershell -ExecutionPolicy Bypass -File .\release\tests\run_all.ps1
```

Five runs, a few minutes. Close the launcher first — one already holding port
8888 makes the tests silently reuse it instead of exercising their own server.
The last four start and kill the real game, so leave the machine alone.

| Test | Covers |
|------|--------|
| `test_save_protocol.py` | The wire protocol against cs_server directly, no game: slot allocation from the `gameid=-1` sentinel, `savegamelist` format, blob round-trip, saving over a slot, delimiter injection in a save name |
| `test_status_cycle.py <mode>` | Drives the real launcher and client: click, report running, kill, recover |
| `test_external_status.py` | The same for a client started outside the launcher, where there is no child handle |

### Release checklist

1. `build.ps1 -Clean` and confirm the version is right.
2. Run `release\tests\run_all.ps1` — all green.
3. Run the staged launcher and click through every visible mode.
4. Confirm `data/` is created next to the launcher and both logs appear.
5. Tag `v<version>` and attach the `.zip` to a GitHub release, with the
   SHA256 the build printed.

The launcher is unsigned, so players will see a SmartScreen warning on first
run. This is expected and is documented in the release's `README.txt`.

## Dev tools

`client/dev_tools/` reads and drives a live client by inspecting its memory:

- `ejbo_viewer.py` / `ejbo_viewer.html` — live game-state viewer
- `snapshot.py`, `checkpoint.py` — capture and restore game state
- `game_cycle.py`, `fast_turns.py`, `advance_turns.py` — drive turns
- `ai_player/` — the heuristic AI; see its [STRATEGY.md](../client/dev_tools/ai_player/STRATEGY.md)

`server/dev_tools/` works on save blobs: `save_parser.py`, `diff_saves.py`, and
the `inject_*.py` family for planting civs, designs, ships and orders.

## Reference

- [Development_Plan.md](Development_Plan.md) — phases, priorities, backlog
- [CosmicSupremacy_Reconstruction_Report.md](CosmicSupremacy_Reconstruction_Report.md) — the full reverse-engineering reference
- [CosmicSupremacy_Memory_Reconstruction_Report.md](CosmicSupremacy_Memory_Reconstruction_Report.md) — memory layout and structures
