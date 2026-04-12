# Cosmic Supremacy — Reconstruction Reference
*Binary analysis of `CosmicSupremacy.exe`*

---

## 1. Binary Overview

| Property | Value |
|---|---|
| File | CosmicSupremacy.exe |
| Format | PE32 — 32-bit Windows executable (Intel 80386) |
| Subsystem | GUI (windowed application) |
| Packer | **None** — strings are fully readable |
| Framework | Custom C++ (GDI+, COM/OLE, DirectX) |
| Graphics | DirectX 9 (`D3D9.DLL`) |
| Audio | Windows Multimedia (`WINMM.dll`) |
| Networking | WinSock2 (`WS2_32.dll`), WinInet (`WININET.dll`) |

The EXE is **entirely self-contained** — all game assets (images, fonts, UI resources) are embedded directly inside it. No separate data files are required.

### PE Sections
| Section | Raw Offset | Size | Contents |
|---|---|---|---|
| `.text` | 0x400 | 3.3 MB | Compiled code |
| `.rdata` | 0x34C800 | 685 KB | Read-only data, strings, constants |
| `.data` | 0x3F7C00 | 189 KB | Initialized data |
| `.tls` | 0x427000 | 7 KB | Thread-local storage |
| `.rsrc` | **0x428C00** | **3.7 MB** | **All embedded assets** (images, fonts, UI) |

---

## 2. Server Infrastructure

The game communicates with a central server over plain **HTTP/1.0**.

- **Registry key**: `SOFTWARE\CosmicSupremacy`
- **Proxy support**: Yes, auto-detects and allows manual proxy configuration

### Known Server API Endpoints (`action=` parameter)
```
testconnection          — ping/health check
loadgame                — load a save game
savegame                — save current game state
savegamelist            — list available saves
loadgov                 — load governor settings
savegov                 — save governor settings
govlist                 — list available governors
uploadcivname           — upload civilization name
listcivnames            — list civ names (userid=%d)
getcoa                  — get coat of arms image (coaid=%d)
listcoa                 — list coat of arms (userid=%d)
uploadcoa               — upload coat of arms image
passedtutorial          — mark tutorial as complete (userid=%d&pass=%s)
entertestbedgalaxy      — enter test bed galaxy
getplayerfame           — retrieve player fame points
```

### Save/Sync Data Format

The `data=` field in `savegame` POST requests contains a **complete game state snapshot** encoded as:

```
base64( uint32_LE(decompressed_size) + zlib_deflate(structured_binary) )
```

**Encoding pipeline (client → server):**
1. Client serializes game state into a structured binary blob (see section layout below)
2. Blob is zlib-compressed (standard deflate, header `78 9C`)
3. A 4-byte little-endian uint32 of the *decompressed* size is prepended
4. The whole thing is base64-encoded into the `data=` POST parameter

**Decoding pipeline (server → client on `loadgame`):**
Reverse the above. The server returns the raw base64 string in the response body.

#### Binary structure (decompressed)

The decompressed blob is a **hierarchical section-based format**. Each section begins with a 4-byte ASCII marker tag. Sections do NOT use a uniform size prefix — each section type has its own fixed field layout known to the C++ client code.

**Top-level layout:**
```
SAVE  — File header: uint16(decompressed_size - 8) + uint16(0x1000 version) + uint32(section_count)
GSET  — Galaxy settings (key-value pairs, see below)
GLOB  — Global game state (turn number, current player, credits, etc.)
TMGX  — Timing / galaxy tick data
RSMA  — Research / map data (20 bytes, zeroed in testbed)
NWDB  — News database
GLXY  — Galaxy metadata
OWNR  — Player 1 block (contains all sub-sections below)
  DATA  — Player data header
  OWPR  — Owner properties (resources, flags, color, position)
  KNPL  — Known planets
  EXSY  — Explored systems (names, visibility)
  HQAS  — HQ / assets
  CVTR  — Civilization traits (tech levels per category)
  SERV  — Server-side player data
  DSGN  — Ship designs
  SDPR  — Ship design properties (name, component slots)
  GOVS  — Governor settings
  ADMS  — Admiral settings
  SPQS  — Ship production queues
  USSE  — User settings / preferences
OWNR  — Player 2 block (same sub-sections)
  ...
SOLA  — Solar system 1
  SUN   — Star data (type, position, color, luminosity)
  PLNT  — Planet 1
    PLPR  — Planet properties (size, resources, habitability, color)
    PROD  — Production state
    WLTH  — Wealth / resource stockpiles
    ENLI  — Enlisted / garrison data
  PLNT  — Planet 2 ...
  ...
SOLA  — Solar system 2 ...
  ...
SHIP  — Ship instance (position, fleet, HP)
  DYNO  — Dynamic object data
  SHCO  — Ship components
  SHPR  — Ship properties
ROUT  — Fleet route / waypoints
NEBU  — Nebula data (3 in testbed galaxy)
```

#### GSET key-value encoding

The GSET section uses a typed key-value format:
```
uint32(section_size) + uint32(entry_count) + entries...
```

Each entry: `uint32(name_len) + ascii_name + uint32(type_code) + value`

| Type | Value format | Example |
|---|---|---|
| 0 | `uint8(has_custom) + int32(value)` | `maxusers = 100` |
| 1 | `uint8(has_custom) + int32(val1) + int32(val2)` | `rank = (0, 999)` |
| 2 | `int16(value)` | `speed = 0` |
| 3 | `uint32(str_len) + chars` | `name = ''` |
| 4 | `uint8(has_custom) + uint32(count) + count × uint32` | `homeworld_properties = [300, 32, 30, 40, 7]` |

Known GSET keys (from testbed galaxy):
`name`, `speed`, `team`, `xp`, `sandbox`, `2d`, `rank`, `maxusers` (100),
`turnlength` (3600s), `density`, `primetime`, `primetime_turnlength`,
`startticks`, `sectorsize` (200), `homeworld_properties`, `juicyplanets`,
`colonyships`, `planetspersystem`, `startcredits` (200),
`regularplanet_properties`, `juicyplanet_properties`, `tech_multiplier` (800),
`corruption_multiplier` (100), `reputation_multiplier` (100),
`homeworld_changes` (30), `civilization_changes` (5), `premium` (1),
`score_breakeven` (20), `colonymodule_multiplier` (100), `waronly`,
`hse_multiplier` (100), `autoattack`

#### GLOB section (variable length)

At turn 0: 25 bytes — minimal header with no active player data.
From turn 1 onward: 48 bytes — includes the name of the last player who acted
(e.g. "BadGuy" for the AI opponent in testbed), suggesting a "last mover" or
turn-ownership field.

#### Key findings for multiplayer

1. **The save blob is a COMPLETE game state snapshot** — it contains ALL players
   (both OWNR blocks), ALL solar systems, ALL planets, ALL ships. Every player's
   save contains the entire galaxy.

2. **GSET is invariant** — galaxy settings are identical across all turns and all
   saves within the same galaxy. They are set once at galaxy creation.

3. **The server can treat save blobs as opaque store-and-return.** The client
   handles all game logic, serialization, and deserialization. The server's role
   is to store the canonical game state and distribute it to players on load.

4. **For multiplayer turn reconciliation**, the likely original model was:
   - All players download the same canonical state at the start of their turn
   - Each player makes moves locally and saves back
   - The server stores the latest save as the canonical state
   - Turn advancement (tick) is server-controlled — the server decides when to
     advance and which player's save becomes the new canonical state

5. **The server does NOT need to parse save blob internals** for basic
   multiplayer functionality. It only needs to manage which blob is current per
   galaxy and control turn timing.

---

## 3. Galaxy Types

| Galaxy | Description |
|---|---|
| **Tutorial Galaxy** | Guided tutorial, ~10–15 min. Custom traits, Hyperspace Grid, and Custom Civ-Traits disabled |
| **Demo Galaxy** | Demo / sample galaxy |
| **Test-Bed Galaxy** | Developer testing galaxy |
| **Sandbox Galaxy** | Persistent, always-running galaxy for new players. Empire expires after N turns. Attack immunity for first N turns. Planet cap (colony + conquest). No Galaxy-Fame earned |
| **Unranked Galaxy** | Regular competitive galaxy, for players earning their first Galaxy-Fame points |
| **Ranked Galaxy** | Competitive galaxy requiring accumulated Galaxy-Fame to enter |

Galaxy file format extension: `.csgalaxy` / `galaxy%d_%s.cs`

## 4. Embedded Assets

The `.rsrc` section (3.7 MB) contains all game assets embedded directly in the binary:

| Asset Type | Count | Notes |
|---|---|---|
| PNG images | ~375 valid | Icons, UI elements, planet textures |
| JPEG images | ~11 valid | Planet/star textures |
| TrueType Fonts | Several | UI font data |
| BMP images | 5 | Legacy bitmaps |

### Notable Image Sizes Found
| Size | Count | Likely Use |
|---|---|---|
| 512×256 | 12+ | Planet surface textures (sphere-mapped) |
| 1024×512 | 1 | Large galaxy background / main texture |
| 256×256 | 6+ | Ship/icon textures |
| 128×128 | 3 | Ship/model thumbnails |
| 297×323, 293×257, etc. | 6 | Planet editor textures (ground/cloud layers) |
| 64×64, 32×32 | 12+ | UI icons |
| 20×13, 13×13, 37×26 | 100+ | Small UI icon sprites |

Developer asset path found in binary: `D:\Development\Games\SpaceCivilizations\Release\CosmicSupremacy.pdb`

---

## 5. C++ Class Hierarchy (From RTTI)

The binary contains full C++ Run-Time Type Information. Key game classes:

### Core Game Objects
```
SpaceObject
  ├── StaticSpaceObject
  │     ├── Sun
  │     ├── Planet
  │     └── GalaxyNebula
  └── DynamicSpaceObject (ships, fleets)

SolarSystem
Fleet
Wormhole
```

### Ship Component Classes
```
ShipPart
  ├── ShipChassis
  ├── ShipEngine
  ├── ShipWeapon
  ├── ShipShield
  ├── ShipScanner
  └── ShipModule

ShipPartCtrl<ShipEngine>
ShipPartCtrl<ShipChassis>
ShipPartCtrl<ShipModule>
ShipPartCtrl<ShipScanner>
```

### Game Logic Classes
```
Admiral
  └── AdmiralRule* (19 rule subtypes)

Governor
  ├── GovernorCondition* (18+ condition types)
  └── GovernorRule* (13+ action types)

Treaty
  └── TreatyItem

Production / ProductionQueue
Facility
Ship / ShipDesign / ShipProduction
Scan (various subtypes)
Technology
```

### UI / Dialog Classes
```
MainWindow
MapWindow
PlanetViewPage
ShipsPage
ResearchPage
TreatiesPage
ScanningPage
ReconPage
OverviewPage

-- Dialogs --
AdmiralDlg, GovernorDlg, TreatyDlg, NewShipDesignDlg
BattleCalculatorPage, LoadSaveDlg, TutorialDlg
BioBombingConfirmationDlg, SendMessageDlg
CreateHomeWorldDlg, CivilizationNameDlg
CustomizeCivilizationDlg, PlanetEditor
```

### Rendering Classes
```
Texture, VertexBuffer, IndexBuffer
PrimitiveTriangleFan, PrimitiveTriangleList, PrimitiveTriangleStrip
RotatingPlanetCtrl (3D planet display widget)
RenderTargetCtrl
PlanetSurfaceCalculator
PlanetSurfaceEffect
  ├── EffectCreatePlanet
  ├── EffectGasGiant
  ├── EffectGradient
  ├── EffectProcedural
  ├── EffectRipple
  ├── EffectSmoothPoles
  ├── EffectSphereMapping
  └── EffectWrap
```

## 6. Client Patching (EXE Modifications)

The original `CosmicSupremacy.exe` connects to the production server infrastructure which has been offline for years. To run the game locally, 67 bytes were modified across 11 patch sites — no code was added or removed, only existing values were overwritten in place.

### Patch 1 — Connection-validation bypass (1 byte)

| Offset | Original | Patched | Effect |
|---|---|---|---|
| `0x0017926c` | `74` (JZ — jump if zero) | `EB` (JMP — unconditional jump) | Bypasses a server-validation branch so the client proceeds without a live connection check |

### Patches 2–4 — Network redirects (53 bytes)

Two null-terminated hostname strings and one hardcoded IP in `.rdata` were overwritten to point to localhost:

| Offset | Original | Patched |
|---|---|---|
| `0x003776e0` | `www.cosmicsupremacy.com` (23 bytes) | `127.0.0.1:8888` + null padding |
| `0x003776f8` | `cosmicsupremacy.com` (19 bytes) | `127.0.0.1:8888` + null padding |
| `0x00378b98` | `xx.xxx.xx.xxx` (14 bytes) | `127.0.0.1` + null padding |

### Patches 5–11 — Save/load validation bypasses (13 bytes)

Seven conditional branches in the save/load validation path (near `0x00175xxx`) were converted to unconditional jumps or NOPs to allow testbed galaxy saves to be stored and retrieved without a fully authenticated session:

| Offset | Original | Patched | Effect |
|---|---|---|---|
| `0x00175943` | `0F 87 3B 04 00 00` (JA rel32, 6 bytes) | `90 90 90 90 90 90` (6× NOP) | Removes a bounds-check jump that rejects save operations |
| `0x0017597b` | `75` (JNZ) | `EB` (JMP) | Forces save-validation success path |
| `0x00175c1f` | `74` (JZ) | `EB` (JMP) | Bypasses save-format version check |
| `0x00175c60` | `74` (JZ) | `EB` (JMP) | Bypasses save-data integrity check |
| `0x00175c9c` | `77 03` (JA rel8, 2 bytes) | `90 90` (2× NOP) | Removes save-slot limit check |
| `0x00175cf1` | `74` (JZ) | `EB` (JMP) | Bypasses save-permissions check |
| `0x00175d20` | `75` (JNZ) | `EB` (JMP) | Forces load-validation success path |

### Summary

Patches 1–4 (54 bytes) redirect all network traffic from the dead production servers (`www.cosmicsupremacy.com`, `cosmicsupremacy.com`, and a hardcoded IP) to `127.0.0.1:8888`, where the local stub server (`cs_server.py`) listens. Patch 1 converts a conditional branch (JZ) to an unconditional jump (JMP), forcing the client to always take the "success" path past a connection-validation check.

Patches 5–11 (13 bytes) bypass save/load validation checks in the game’s persistence code, which are needed for testbed galaxy saves to function against the local stub server.

---

## 7. Galaxy Connection Token Format (`.csgalaxy` files)

The client uses `.csgalaxy` files as connection tokens. Each file contains a single line of **base64-encoded text** that decodes to a space-separated string:

```
<TYPE> <SERVER_IP> <PORT_OFFSET> <PASSWORD> <PLAYER_NAME>
```

### Field breakdown

| Field | Example | Purpose |
|---|---|---|
| TYPE | `DEMO`, `TUTO`, `TEBE` | Galaxy type — determines client behaviour (e.g. tutorial vs. full game vs. testbed) |
| SERVER_IP | `127.0.0.1` | Server address to connect to |
| PORT_OFFSET | `0` | Port offset from the base port |
| PASSWORD | `abcdef` | Auth token — sent as `pass=` in API calls |
| PLAYER_NAME | `DemoPlayer` | Default player identity |

### Token examples

| File | Base64 | Decoded |
|---|---|---|
| DemoGalaxy.csgalaxy (original) | `REVNTyA4OC4xMTYuMzEuMTA3IDAgYWJjZGVmIERlbW9QbGF5ZXI=` | `DEMO xx.xxx.xx.xxx 0 abcdef DemoPlayer` |
| DemoGalaxy_local.csgalaxy | `REVNTyAxMjcuMC4wLjEgMCBhYmNkZWYgRGVtb1BsYXllcg==` | `DEMO 127.0.0.1 0 abcdef DemoPlayer` |
| TutorialGalaxy_local.csgalaxy | `VFVUTyAxMjcuMC4wLjEgMCBhYmNkZWYgRGVtb1BsYXllcg==` | `TUTO 127.0.0.1 0 abcdef DemoPlayer` |
| TestbedGalaxy_local.csgalaxy | `VEVCRSAxMjcuMC4wLjEgMCBhYmNkZWYgVGVzdEJlZFBsYXllcg==` | `TEBE 127.0.0.1 0 abcdef TestBedPlayer` |

### Known type codes (from binary at `0x003783b4`)

| Code | Galaxy Type | Notes |
|---|---|---|
| `TEBE` | Test-Bed | Developer testing galaxy; triggers `entertestbedgalaxy` endpoint |
| `DEMO` | Demo | Sample galaxy |
| `TUTO` | Tutorial | Guided tutorial; runs almost entirely client-side |

The `_local` variants are identical to the originals except the server IP is changed to `127.0.0.1`. The `userid` sent in API calls (`userid=0`) is derived from the port offset field; the `pass` value comes directly from the password field.

---

## 8. Phase 1 Protocol Findings (Tutorial Run, March 2026)

Key observations from running the patched EXE through the complete tutorial galaxy:

**Server traffic**
- Only two server calls were ever made during the entire tutorial:
  1. `GET /clientinterface.php?action=testconnection` → must return `READY` (not `OK`)
  2. `GET /clientinterface.php?action=passedtutorial&userid=0&pass=abcdef` → at tutorial completion
- No `login`, `loadgame`, `savegame`, or any other call. The tutorial runs **entirely client-side**.
- `userid=0` and `pass=abcdef` come directly from the `.csgalaxy` token — no separate login step.

**Tick behaviour**
- Tutorial galaxy advances at ~1 tick/minute with no server involvement.
- Tick timing is controlled client-side (confirmed by `c:\\SpeedTicks.txt` debug string in binary).
- The "Ticks Halted" state in the Demo galaxy is a server-controlled pause — the server must release it. Mechanism TBD (likely part of the `loadgame` response blob).

**Save blob**
- `savegame` was never called during tutorial — game state was not persisted.
- Save blob format remains unknown; must be captured from a real (non-tutorial) galaxy session.

**`testconnection` response**
- Must return the exact string `READY` (confirmed from binary string `'tutorial communication test response from server: '%s''`).
- Any other response causes the connection dialog to show "failed to connect".

---

## 9. Test-Bed Galaxy Protocol Findings (March 2026)

Key observations from running the patched EXE with a `TEBE` type `.csgalaxy` token:

**Connection flow**
1. Client calls `testconnection` (same as tutorial/demo — must return `READY`)
2. Client POSTs to `entertestbedgalaxy` with a large `pass` payload
3. On success, the client enters the galaxy and begins the full game loop (savegamelist, savegame, loadgame)

**`entertestbedgalaxy` payload**
- The `action=entertestbedgalaxy` is sent in the **URL query string**, not the POST body. The server must parse the action from the URL, not just the body.
- POST body: `userid=0&pass=<large_base64_blob>` (12,032 chars)
- The `pass` field contains the `.csgalaxy` token repeated 16 times, each copy separated by lines of 32-digit hex counters (`00000000...00000000` through `00000000...0000000f`). Total: ~12 KB.
- The binary references `TestBedPlayer` as the hardcoded player name and provides colour-coded teams: Blue, Red, Orange, Purple.
- Server returning `OK` (empty body or "OK") is sufficient for the client to proceed.

**Testbed game loop**
- After entering, the client immediately requests `savegamelist` to enumerate existing saves.
- Saves are named `TestBed Save 1`, `TestBed Save -1`, etc. with `gameid=-1` as the client's "allocate new slot" sentinel.
- The server should allocate the next available positive integer ID when it receives `gameid=-1`, since the client treats negative IDs as invalid when loading from `savegamelist`.
- Save/load cycle works identically to other galaxy types — the server stores and returns the binary blob opaquely.
- Ticks advance client-side (same as tutorial), allowing immediate gameplay without server-driven tick scheduling.

---

## 10. Known Server API Data Payloads

### Request formats

| Operation | POST Body |
|---|---|
| Login | `userid=%d&pass=%s` |
| Authenticated requests | `userid=%d&passhash='%s'` |
| Save game | `userid=%d&passhash='%s'&gameid=%d&gamename='%s'&turn=%d&version=%d&data=%s` |
| Save governor | `userid=%d&passhash='%s'&govid=%d&govname='%s'&version=%d&data=%s` |
| Upload civ name | `userid=%d&passhash='%s'&civname='%s'` |
| Get COA | `action=getcoa&coaid=%d` |
| Upload COA | `action=uploadcoa` + image data |
| Mark tutorial done | `action=passedtutorial&userid=%d&pass=%s` |

### Response formats (confirmed by binary analysis, April 2026)

The client uses `#SPC#` as the field delimiter and `#NEXT#` as the record delimiter in list responses. All list responses must end with `DONE` as the final record.

| Action | Expected Response | Binary Reference | Notes |
|---|---|---|---|
| `testconnection` | `READY` | — | Any other string → "failed to connect" |
| `savegame` | `DONE` | `0x0048b350` / `0x403f00`: `strncmp(response, "DONE", 4)` | `OK` or any other string → "Failed to save the Save-Game" dialog |
| `savegamelist` | `<gameid>#SPC#<name>#SPC#<turn>#NEXT#...#NEXT#DONE` | — | Empty body → "Failed to retrieve list of saved games". `DONE` alone = valid empty list |
| `loadgame` | `DONE#VER#<6-char-version>#DATA#<base64-blob>` | `0x0048b5d0` / `0x40a640` | Version `000000` = identity cipher (no transform). Non-zero version applies byte-level cipher to data. See below |
| `savegov` | `DONE` | `0x4a0c3f` | Same `strncmp` pattern as `savegame` |
| `govlist` | `DONE` | — | `DONE` alone = valid empty list |
| `loadgov` | `DONE#VER#<6-char-version>#DATA#<base64-blob>` | — | Same format as `loadgame` |
| `listcivnames` | `<civname>#SPC#<coaid>#NEXT#DONE` | `FUN_0x497f93` / `0x5e3de0` | If coaid is empty/null, the "Customize Your Home World" popup reappears every tick |
| `listcoa` | `<coaid>#NEXT#DONE` | — | Empty response → no COA registered → some UI elements missing |
| `uploadcivname` | `OK` | — | No response-body check in client |
| `entertestbedgalaxy` | `OK` | — | Empty body or `OK` both work |
| `passedtutorial` | `OK` | — | No response-body check in client |

#### `loadgame` response parsing (detailed)

The client parses `loadgame` responses as follows (from binary analysis at `0x0048b5d0`):

1. `strncmp(response, "DONE#VER#", 9)` — must be 0 (success flag)
2. `substr(response, 9, 6)` — extracts 6-char version string into a decoder object
3. `find("#DATA#")` in full response — locates the data marker
4. `substr(pos_of_DATA + 6, end)` — the raw base64 blob
5. Base64-decode → strip 4-byte header → zlib-decompress → game state

The 6-char version string is used as a key for a stream cipher (`0x411110` decoder factory). Version `000000` produces an all-zero key → identity transform (XOR with 0x00 = no change), so the blob passes through unmodified. The original server likely used non-zero version strings to obfuscate save data in transit.

---

### What We Are NOT Doing

- **OAuth / social login** — requires new UI windows in the EXE; not feasible via patching

---                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             