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

### Save/Sync Data Format (Historical)

The `data=` field in `savegame` POST requests contains a game state snapshot encoded as `base64( uint32_LE(decompressed_size) + zlib_deflate(structured_binary) )`. The decompressed blob uses a hierarchical section-based format (SAVE/GSET/GLOB/OWNR/SOLA/SHIP/etc.). Full format was documented in earlier sessions but is no longer the primary approach for state sync — see "Live Memory Object System" below.

**Note:** Save blob manipulation has been superseded by direct memory access. The save format documentation is preserved in git history (commit prior to Session 10 cleanup) for reference.

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

### Patches 12–17 — Turn pipeline bypasses (T1–T5, 22 bytes)

Six patch sites bypass server sync checks in the turn pipeline so turns can fire without a real game server. These are applied only to the Resurgence EXE (not TestBed). See Section 11 for full patch table.

**Side effect:** Applying T1–T5 removes the Next Turn button from the UI. This is intentional for the multiplayer build — turns are advanced externally via `fast_turns.py`, not by player clicks.

### Summary

Patches 1–4 (54 bytes) redirect all network traffic from the dead production servers (`www.cosmicsupremacy.com`, `cosmicsupremacy.com`, and a hardcoded IP) to `127.0.0.1:8888`, where the local stub server (`cs_server.py`) listens. Patch 1 converts a conditional branch (JZ) to an unconditional jump (JMP), forcing the client to always take the "success" path past a connection-validation check.

Patches 5–11 (13 bytes) bypass save/load validation checks in the game’s persistence code, which are needed for testbed galaxy saves to function against the local stub server.

Patches 12–17 (22 bytes, T1–T5) bypass turn-pipeline sync checks, enabling external turn control. Applied only to the Resurgence EXE.

### EXE Variants

| EXE | Patches | Next Turn Button | Galaxy File | Purpose |
|-----|---------|-----------------|-------------|---------|
| `CosmicSupremacy.exe` | None | Yes | — | Unmodified original |
| `CosmicSupremacy_TestBed.exe` | 1–11 | Yes | `TestBedGalaxy_local.csgalaxy` | Manual testing with interactive turn button |
| `CosmicSupremacy_Resurgence.exe` | 1–17 (incl. T1–T5) | No | `SandboxGalaxy_local.csgalaxy` | Production multiplayer — turns controlled by `fast_turns.py` |

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

---

## 11. Live Memory Object System (EJBO)

All game objects (planets, ships, admirals, etc.) carry a 4-byte tag `EJBO` (`0x45 0x4a 0x42 0x4f`) in memory. A typical game has ~191 EJBO instances. Objects live on the heap — addresses change between launches but are stable within a session.

### Common object layout (relative to EJBO tag)

| Offset | Content |
|---|---|
| −12 | Second vftable — **only for multiple-inheritance classes** (e.g. `ShipDesign`). Ordinary field data otherwise. |
| −8  | Primary vftable (used for classification) |
| −4  | Object ID (global sequential counter) |
|  0  | `EJBO` tag |
| +4  | Varies by type (float or zero) |
| +8+ | Object-specific fields (name, stats, etc.) |

**Correction (July 2026):** an earlier revision of this table described −12 as "type
descriptor pointer #1", present on every object. It is not. Measured across a live
TestBed galaxy, `Planet` holds a varying heap value at −12 and `Sun` holds
`0x8800xxxx`; only `ShipDesign` has a genuine second vftable there
(`0x00771DF8`, from multiple inheritance). **The object header is 8 bytes, not 12.**
Classification must read −8 and treat −12 as a field unless RTTI validates it.

The 4×4 identity matrix (float 1.0 = `0x3f800000` on the diagonal) seen before some
objects belongs to transform-carrying classes, not to every EJBO object — densely
packed `Sun` instances are only 104 bytes apart end to end.

### Class identification via RTTI (July 2026)

The pointer at EJBO−8 is an **MSVC vftable**, and the binary retains full RTTI, so
object classes do not need a hand-maintained lookup table. The chain is:

```
vftable[-4]      -> RTTICompleteObjectLocator
locator + 0      -> signature (must be 0)
locator + 12     -> TypeDescriptor
TypeDescriptor+8 -> mangled name, e.g. ".?AVPlanet@@" -> "Planet"
```

Walking this over the whole `.rdata` section yields **559 vftables covering 434
classes**. `ejbo_viewer.py` now resolves names live through this chain (cached per
vftable), so any newly-encountered object type labels itself instead of landing in
an "Unknown" bucket. The former hardcoded map is kept only as a fallback.

Two labels were wrong or missing before this:

| Old viewer label | Actual RTTI class | Note |
|---|---|---|
| `CivStats` (`0x007707E0`) | **`Owner`** | Matches the `OWNR` save-blob section. Annotations re-keyed `CivStats:*` → `Owner:*`. |
| `Unknown` (32 live objects) | **`Sun`** (`0x0076992C`) | Annotations re-keyed `Unknown:*` → `Sun:*`. |

### Type descriptor pointers (RTTI-confirmed)

Instantiated in a live TestBed galaxy (201 objects, all classified):

| Pointer | Class | Live count |
|---|---|---|
| `0x00768DDC` | Planet | 160 |
| `0x0076992C` | Sun | 32 |
| `0x00768B04` | Ship (instances — HP/coords as floats) | 4 |
| `0x007707E0` | Owner (civilisation-level stats) | 2 |
| `0x00771DF8` / `0x00771DF0` | ShipDesign (two vftables, multiple inheritance) | 2 |
| `0x00784934` / `0x0078492C` | Admiral | 0 in this save |

Vftables located but not instantiated in the save inspected — these are the classes
still to be categorised:

| Pointer | Class | | Pointer | Class |
|---|---|---|---|---|
| `0x00768904` | Fleet | | `0x00776CE0` | ProductionQueue |
| `0x0076830C` | SolarSystem | | `0x00776444` | ShipProduction |
| `0x00769AA8` | Wormhole | | `0x00752B44` | Production |
| `0x00767DC8` | GalaxyNebula | | `0x00752BA4` | Facility |
| `0x00776F80` | Treaty | | `0x00752B6C` | Scan |
| `0x00787230` / `0x00787228` | Governor | | `0x007698B8` | StaticSpaceObject |

### Object extents — why the read window matters

The viewer reads a fixed window after each EJBO tag. If that window is wider than the
object, it displays **neighbouring heap allocations as if they were fields**, and any
annotation made on them is meaningless. Sizes are derived at scan time from the stride
between consecutive same-class tags in a dense array (`min(repeated stride) − header`),
additionally capped so no window reaches the next object's header:

| Class | Stride | Header | Usable after tag | Previous window |
|---|---|---|---|---|
| Planet | 608 / 616 | 8 | **596** | 192 — **69% of every Planet was invisible** |
| Sun | 104 | 8 | **92** | 192 — 88 bytes of neighbour shown as Sun fields |
| Ship | sparse | 8 | 156 (configured) | 192 |
| Owner | sparse | 8 | 192 (configured) | 192 |
| ShipDesign | sparse | 12 | 192 (configured) | 192 |

A class needs **≥3 instances in a dense array** for its stride to repeat and therefore be
measurable; with fewer, the viewer falls back to a configured value and says so.

**Bounding sparse classes.** `Ship`, `ShipDesign` and `Owner` are never densely arrayed,
so stride cannot be measured. Two other signals work:

1. **Structural agreement.** Within an object, instances agree on the *kind* of value at
   each offset (all zero, all heap pointers, all floats). Past the end they are unrelated
   allocations and that agreement collapses. FF-sentinel base templates and `.data`
   statics must be excluded from the comparison or everything reads as disagreement.
2. **The NT heap block header.** A dword pair of the form `0x0803xxxx` / `0x0804xxxx`
   with a matching low half marks the next allocation. It appears immediately after
   `ShipDesign` (+272) and `Owner` (+1360).

Resulting extents: `Ship` **152**, `Owner` **1344**. `Owner` is by far the largest class
found and was previously guessed at 192 — **seven eighths of it was invisible**, which is
why the civilisation name could not be located. `MAX_READ_AFTER` had to be raised from 608
to 2048 to accommodate it.

`ShipDesign` is left conservatively at **192 and is not resolved**. Its instances share
heap pointers at +188/+212/+236/+260 on a regular 24-byte spacing, and `+260` holds the
human player's owner node on all four designs — but one design shows foreign UTF-16 text
at +192/+216/+240, which cannot happen inside a live object. Either the objects are
~264 bytes with one instance's fields misread, or ~190 bytes and the regular spacing
belongs to a neighbouring allocation of a repeated kind. Unresolved; do not annotate
`ShipDesign` above +190 without settling it.

The Sun overrun was provable: at `Sun+96` the value read `0x0076992C` — the next Sun's
own vftable. Five annotations that sat in that region
(`Unknown:128/136/140/144/148`, recorded as "updates on timer" / "updates when right
clicking") were reading unrelated heap data, including fragments of an HTTP
`form-urlencoded` string, and have been **deleted** rather than migrated.

Known open discrepancy: `Owner:144` / `Owner:152` are annotated as current/selected
science topic, but the constructor analysis below puts the current science topic at
`[civ+0x38]` = `Owner:4`. One of the two is wrong; unresolved. Live `Owner:4` reads
`0` and `21` for the two civilisations, which is consistent with a tech id and
therefore with the constructor analysis.

### Field-shape signatures (July 2026)

The game is built with MSVC, so standard-library members have recognisable layouts.
Matching these turns guesswork into confirmation and identifies three or four fields
at a time.

**`std::string`** — small-string optimisation: a 16-byte inline character buffer,
then `_Mysize` (length), then `_Myres` (capacity, **always 15** while the string is
short). Confirmed on two classes:

| Class | Buffer | Length | Capacity | Evidence |
|---|---|---|---|---|
| Planet | `+52 … +67` | `Planet:68` | `Planet:72` | All 160 planets: `_Mysize` equals the measured string length; `_Myres` is 15 on every one. `"DemoPlayer's HQ"` = 15, `"BadGuy's HQ"` = 11. |
| ShipDesign | `+8 … +23` | `ShipDesign:24` | `ShipDesign:28` | Both designs `"Colony Ship"`, length 11, capacity 15. |

This corrected an earlier misreading: `Planet:52/56/60/64` were labelled "Planet Name
Byte 1–4" as though they were four separate fields, and `Planet:68` looked like a
gameplay value (it reads 15 and 11 on the two colonised planets — plausibly a
population) when it is just the name length. `Admiral:20` is very likely the same
shape with length at `Admiral:36` and capacity at `Admiral:40`, unverified — no
`Admiral` instances existed in the save inspected.

Uninitialised SSO buffers hold stale bytes, so a printable-looking buffer means
nothing on its own: 17 of the 158 unnamed planets have printable bytes at `+52`, all
of which are float bit patterns such as `0x3F800000` (= 1.0f). **`_Mysize` is the only
reliable "is this named" test.**

**`std::vector`** — three consecutive pointers: first, last, end-of-capacity.
`Planet:144/148/152` and `Planet:384/392` both match, and both are null on all 158
uncolonised planets, so they hold per-colony collections (facilities, production
queue, or stationed ships — not yet distinguished). Element blocks are 112 and 16
bytes respectively, and `last == end` in both, so size equals capacity.

### Colonised-planet field group

Only two of the 160 planets are colonised (`DemoPlayer's HQ` #134, `BadGuy's HQ` #193),
matching the two `Owner` objects. Exactly nine offsets are non-zero on those two and
zero on all others — a tight candidate set for the whole ownership/colony group:

| Offset | Values (HQ #134 / #193) | Reading |
|---|---|---|
| `+68` | 15 / 11 | name length (resolved — **not** a gameplay field) |
| `+144/148/152` | pointer triple | `std::vector`, 112 bytes of elements |
| `+208` | 3 / 3 | unknown |
| `+368` | 1 / 1 | unknown |
| `+384/388/392` | pointer triple | `std::vector`, 16 bytes of elements |

### Object ownership — resolved (July 2026)

Ownership is a **two-hop indirection at offset +40**, which is why an initial scan for
Planet fields pointing directly at an `Owner` object found nothing:

```
owner = *(void**)( *(void**)(obj + 40) )      // -> Owner object at (EJBO tag - 8)
```

`obj+40` holds a pointer to a small owner node; the node's first dword is the `Owner`
object's allocation start, i.e. its EJBO tag minus the 8-byte header (consistent with
the corrected header size above). Unowned objects point at a **static null-owner node
at `0x00857C54` in `.data`**, whose first dword is `0`.

Verified across every object in a live TestBed galaxy — 160/160 planets resolve, no
failures:

| `Planet+40` | Resolves to | Count |
|---|---|---|
| `0x00857C54` (static) | null owner | 157 |
| `0x0A6CF368` | `Owner #194` — the human player | 2 (`DemoPlayer's HQ`, new colony) |
| `0x0A6CF568` | `Owner #198` — the AI rival | 1 (`BadGuy's HQ`) |

**The same field at the same offset carries ownership on `Ship`**, so `+40` is a member
of a shared base class (`SpaceObject` or similar) rather than a per-class field.
Ship #196 → `Owner #194`; ships #200 and #201 → `Owner #198`.

This is the field per-player state sync depends on, and it generalises: any EJBO class
descending from the same base can be attributed to a civilisation by reading one
pointer and dereferencing it once.

### The reference-node idiom

Ownership is one instance of a general pattern. An object-to-object reference is stored
as a pointer to a small **node**, whose first dword is the target's allocation start
(EJBO tag − 8); a shared **static null node at `0x00857C54`** (first dword `0`) means
"no target". Confirmed instances:

| Field | Target | Null case |
|---|---|---|
| `Planet:40` | `Owner` | 157 of 160 planets |
| `Ship:40` | `Owner` | — all 3 ships owned |
| `Ship:80` | `Admiral` | 2 of 3 ships unassigned |
| `Ship:56` | unknown | all 3 ships |

`Admiral` carries **no** owner link — no field on it dereferences to an `Owner`, so it is
not a `SpaceObject`. Its civilisation is presumably implied by the container it lives in.
The Ship→Admiral link is also **one-way**: no `Admiral` field points back at its ship.

### Admiral (July 2026)

Three admirals (`adm1`, `adm2`, `adm3`, identical type and instructions, `adm1` holding
the one remaining ship) gave a measurable stride and a controlled differential.

- **Extent: 276 bytes** (stride 288, 8-byte header) — previously a guessed 192.
- **Name is a `std::string`** at the predicted offsets: buffer `+20 … +35`, length
  `Admiral:36`, capacity `Admiral:40`. Note `+40` is the string capacity here, *not* an
  owner link — the `+40` offset is only ownership on `SpaceObject` descendants.
- `Admiral:4` holds a **second vftable** (`0x0078492C`), so `Admiral` is another
  multiple-inheritance class — but with the extra vftable *after* the tag, not at −12
  as on `ShipDesign`. The 8-byte header is unaffected.
- `Admiral:8` **confirmed** as ships-assigned: `1` on `adm1`, `0` on the other two.
  `Admiral:56` tracks it exactly and is not yet distinguished from it.

A fourth `Admiral` object exists at `0x0083A8D8` — **in `.data`, not on the heap** — with
object id `0` and a copy of the most recently created admiral's name. It is a static
template or dialog working buffer, not a game entity, and should be filtered out of any
sync that enumerates admirals. Its presence is also why the "stride must repeat" rule
matters: the gap from it to the first heap admiral is 166,927,544 bytes.

### ShipDesign — all stat annotations verified (July 2026)

Three designs (`ship1`, `ship2`, `ship3`) differing only in engine count (1, 2, 3 Nuclear
Drives) confirmed every existing `ShipDesign` stat annotation at once. Each field moves
by a fixed amount per engine:

| Field | ship1 | ship2 | ship3 | Per engine |
|---|---|---|---|---|
| `:40` Thrust | 270 | 540 | 810 | +270 |
| `:44` Build-Cost | 445 | 565 | 685 | +120 |
| `:48` Upkeep-Cost | 4 | 5 | 6 | +1 |
| `:52` / `:56` Base / Effective HP | 30 | 35 | 40 | +5 |
| `:84` Available Space | 110 | 100 | 90 | **−10** |
| `:92` Metal cost | 66 | 70 | 74 | +4 |
| `:100` Radioactives cost | 6 | 12 | 18 | +6 |
| `:36` Speed | 9.00 | 15.43 | 20.25 | non-linear (derived) |

`:36` Speed being non-linear while `:40` Thrust is exactly linear is consistent with speed
being thrust divided by a mass that also grows with engine count.

New: **`ShipDesign:176/180/184` is the ship parts vector** — element span 8, 16, 24 bytes
for 1, 2, 3 engines, i.e. **8 bytes per part**.

Correction: an earlier note said each design creates two template objects, a "computed"
one and a "base" one with `0xFFFFFFFF` sentinels. That holds for `Colony Ship` (#195
computed, #199 base) but **not** for the three user-created designs, which have one object
each. Five `ShipDesign` objects exist for four designs.

### Owner — extent corrected, contents partly mapped

With the extent raised from 192 to 1344 the class shows 345 fields instead of 57. Notable:

- **`Owner:1264 … +1300`** — a **10-slot reference-node array**, every slot the static null
  node on both civs. Ten slots matches a per-rival diplomacy or treaty table.
- **`Owner:440/444/448`** — a `std::vector` of 3 elements present only on the human civ,
  which is also the only civ with user-created ship designs. Likely the design list.
- **`Owner:1208/1212/1216`** — a float triple reading 740.47 / 337.23 / 702.13 on the AI and
  zero on the human, matching the X/Z/Y coordinate layout used elsewhere.
- Four `std::string` members at `+368`, `+528`, `+1144`, `+1312`, **all empty on both civs**
  — so the civilisation name is not among them in this save.

Two existing annotations are now in doubt:

- **`Owner:24` "TurnX10"** reads `0` for the human and `20` for the AI. The turn number is
  global and cannot differ per civilisation, so this label is wrong.
- **`Owner:8` "Total Gold"** reads `232` for both civs (and `200` for both in an earlier
  observation). Possible, but it should be confirmed to be per-civ rather than a global.

### How it was found

The decisive step was a **controlled differential**: colonising a second planet under
the existing civilisation, in the same solar system. That made "owned by civ X" and
"is colonised" separable for the first time — a true owner field must be *identical*
on two planets of the same civ and *different* on the rival's, which collapsed 158
candidate offsets to eight. Two prior observations were needed to get there, and one
earlier candidate had to be discarded:

- `Planet:68` looked like a population count (15 and 11 on the two colonised planets)
  and is the name length.
- `Planet:368` looked like a colonisation flag (`1` on colonised, `0` elsewhere) until
  the second observation showed every value shifted by 2 (`3` on homeworlds, `2` on
  uncolonised, `1` on the day-old colony). Only **bit 0** is the ownership bit; the
  higher bits move galaxy-wide for reasons not yet understood.
- `Planet:208` is not a colonisation marker either — it reads `3` on both homeworlds
  but `0` on the newly founded colony, so it is homeworld-specific.

The new colony was also **unnamed**, so the name field cannot be used to detect
colonisation; it was located instead by its owner node and by proximity to the
existing homeworld (20.4 units, same system).

### Ship design templates vs ship instances

Ship *designs* and ship *instances* are separate EJBO objects. Each ship design creates two template objects: a "computed" template (all stats filled in) and a "base" template (`0xFFFFFFFF` sentinels in runtime fields).

Ship instances use type pointer `0x00768B04`. Instance HP and coordinates are stored as **floats**, not integers. The HP field is called "condition" in-game (0.0–1.0 range).

### Galaxy layout is client-generated, and coordinates are writable (July 2026)

**The galaxy is generated by the client, not supplied by the server.** Two fresh games produced
different layouts — 108 suns both times but **525 vs 544 planets**, and no coordinate in common.
The server log explains why: across a whole session the client requested `testconnection`,
`entertestbedgalaxy`, `savegamelist`, `listcivnames`, `listcoa` and `getcoa` — **`loadgame` was
never requested once**. `cs_server.py` answers `savegame` with `DONE` while discarding the body
("stub, not persisted"), advertises a hardcoded `savegamelist` entry at turn 0, and returns an
empty blob from `loadgame`. With nothing to load, every client builds its own galaxy.

**Sun and Planet coordinates are writable, render live, and drive game logic.** Verified on a
system renamed `home` with the view zoomed in:

| Test | Result |
|---|---|
| Write `Planet:4/8/12` on the homeworld (+30 X, system radius ~28) | planet **visibly moved** away from its siblings |
| Same write applied while the system was **off screen** | position was simply correct on return — **no refresh needed** |
| Write `Sun:4/8/12` on the home star (+30 X, planets untouched) | star **visibly moved** off its own centre |
| A ship ordered to that star | **re-routed to the new position** — logic reads the written value, not a cached one |
| A ship already orbiting that star, when the star moved back | **stayed behind in empty space** |

So the coordinates are authoritative for both rendering and pathfinding, which makes
layout normalisation by memory injection viable.

**Every positioned object is independent.** Moving a star does not carry its planets, and does not
carry ships orbiting it — the ship is simply left in the void where the star used to be. `Sun`,
`Planet` and `Ship` each hold their own absolute coordinates with no parent transform, which the
absence of any per-object matrix already suggested (the 4×4 matrices near these objects are pure
identity with zero translation). Any layout injection must therefore be a **single coordinated pass
over suns, planets and ships together**, not a per-object edit.

**Two earlier negatives were false.** A whole-system move of +100 X and a single-planet move of
+150 X both wrote successfully and appeared to do nothing. Neither object was on screen — one was
a system chosen by ownership proximity that was never confirmed in view, the other was
`Planet #54` at `(81.6, 296.0, 500.3)`, nowhere near the home system at `(663, 138, 262)`. The
apparent "planets don't move" conclusion was an artifact of not verifying what was visible.

**Correction:** `Planet:20/24/28/32` are **not** screen-projection fields. They read `0` on every
planet while planets were actively rendering. That reading was carried over from `Ship:20` without
being tested on `Planet`, and it cost an attempt at an "objective" render signal that did not exist.

**What this does and does not enable.** Coordinates can be overwritten, so a galaxy's *geometry*
can be normalised across clients. Object *counts* cannot — memory writes cannot create the 19
`Planet` objects one client has and another lacks, nor destroy surplus ones. Any normalisation must
therefore shrink to the lowest common count and park the remainder out of the way, rather than
making two galaxies genuinely identical.

[ ] orbit rings do not follow a moved star or planet — cosmetic only, revisit if layout injection ships

### Confirmed: memory writes are functional

Writing to ship instance fields via WriteProcessMemory produces immediate in-game effects:
- **HP/Condition**: writing a new float value changes the displayed HP
- **Position coordinates**: writing new XYZ floats **teleports the ship** to the new position

This proves that external memory manipulation is a viable approach for multiplayer state sync.

### Game state signals in `.data` section

| Address | Type | Interpretation |
|---|---|---|
| `0x0080AA08` | int32 | **Turn countdown** (seconds remaining). Writing a small value triggers full turn resolution. |
| `0x0082929c` / `0x008292a0` | int pair | Last-clicked X/Y coordinates |
| `0x00853d24` | int | Action/sequence counter (monotonic) |
| `0x0082a828`, `0x0082a8dc`, `0x00854c70` | flags | Dirty flags — set when pending orders exist |
| `0x0082a900…0x0082a920` | ptr[] | Linked-list head/tail/sentinel of order records |
| `0x008292c8` | ASCII | Countdown timer string (display-only, overwritten by render loop) |
| `0x0086F1A1` | byte | Sync flag — 0=paused, 1=running |
| `0x008578E8` | int32 | **Turn number** — read 48 with the UI showing turn 48, and 49 after one advance |
| `0x00871430+` | mixed | Global serializer buffer (app context, file paths, UI config) |

### Turn control (key discovery, April 2026)

The turn countdown at `0x0080AA08` is the authoritative timer. Writing a small integer (e.g. `1`) causes the game to count down and fire a full turn resolution — ships move, resources tick, production advances, all handled client-side. The server does NOT need to reimplement any game logic.

The address is stable across launches but shows `0xFFFFFFFF` before a galaxy is loaded (value comes from GSET `turnlength` at runtime, default 3600 = 60 minutes).

**Turn patches T1–T5** (22 bytes total, from `patched17`) bypass server sync checks so turns fire without a real game server:

| Patch | File offset | Effect |
|---|---|---|
| T1 | `0x0016CFF0` | Force turn-ready check → always TRUE |
| T2 | `0x0016D4EF` | NOP turn guard JNZ |
| T3 | `0x0016D533` | NOP turn guard JZ |
| T4a | `0x0017701A` | Redirect write to sync flag at `0x86F1A0` |
| T4b | `0x0017702A` | NOP turn guard JZ after sync flag write |
| T5 | `0x0017902D` | NOP turn guard JZ |

Other timer-related addresses (`0x008292C8` ASCII string, EJBO #160 offset −28 ones-complement timer) are display-only copies overwritten by the render loop — not useful for control.

### Research/science accrual — TestBed “Next Turn” vs. full turn resolution (June 2026)

**Symptom:** In a TestBed galaxy, the TestBed-only “Next Turn” button advances the human player's per-planet economy (food `Planet:112`, construction `Planet:120`) and advances AI empires' research, but the human player's research progress (`Owner:12`) never increases — even with a technology selected and the science-topic field set.

**Root cause:** The TestBed “Next Turn” button performs only a partial per-planet tick. The empire-level science→research accrual for the local player is part of the *full* turn resolution, which is fired by the turn countdown at `0x0080AA08` reaching a small value — NOT by the TestBed button. Triggering a real turn via `fast_turns.py` (which writes `0x0080AA08`) advances the human's research correctly. Confirmed in a non-TestBed game, June 2026.

**Takeaway:** When authoritative/full resolution is required, drive turns via the `0x0080AA08` countdown (as `fast_turns.py` does), not the TestBed “Next Turn” button. The TestBed button is a partial-tick shortcut and should not be relied on for empire-level (`Owner`) effects such as research.

**Update (July 2026) — the TestBed problem is broader than the button.** Driving a full
turn via `0x0080AA08` on the **TestBed** build still did not accrue the human player's
research: across roughly ten turns the AI's `Owner` moved (research 80 → 480, gold
232 → 402, score 1536 → 1646) while the human's `Owner:12`, `:24` and `:28` all stayed
at `0` and only gold moved. **The human's `Owner` object is essentially not ticked on
TestBed at all**, regardless of how the turn is fired.

On the **Resurgence** build (T1–T5 applied) everything ticks correctly. One turn moves
both civs identically:

| Field | Before | After | Per turn |
|---|---|---|---|
| `Owner:8` Total Gold | 248 | 264 | +16 |
| `Owner:12` Research Progress | 120 | 160 | +40 |
| `Owner:24` TurnX10 | 30 | 40 | +10 (turn 3 → 4) |
| `Owner:28` Score | 1566 | 1576 | +10 |

Those figures come from a **symmetric** position (both civs 1 planet, 2 ships), and
symmetric civs cannot distinguish a per-civ field from a global one. Repeating the
measurement after the human colonised a second planet separates them:

| Field | Human (2 planets) | AI (1 planet) | Verdict |
|---|---|---|---|
| `Owner:8` Total Gold | **+52** | **+16** | per-civ, scales with holdings |
| `Owner:28` Score | **+20** | **+10** | per-civ, +10 per owned planet per turn |
| `Owner:24` | **+20** | **+10** | per-civ — **not** a turn counter |
| `Owner:12` Research | +40 | +40 | unchanged by asymmetry; still unresolved |

**`Owner:24` is not TurnX10.** It was flagged suspect earlier in this session, then
rehabilitated on the strength of the symmetric reading above — that rehabilitation was
premature and is withdrawn. Under asymmetry the two civs read different absolute values
(90 vs 70) and different per-turn deltas, which a global turn counter cannot do. Its
delta matches `Owner:28` exactly every turn, so it is most likely a score component or
a per-turn score accumulator rather than a turn count.

The lesson generalises: **a field can only be shown to be per-civ by making the civs
differ.** Three separate readings of `Owner:24` — TestBed (0 vs 20), symmetric Resurgence
(equal), asymmetric Resurgence (90 vs 70) — supported three different conclusions, and
only the last is decisive.

Correction to tooling notes: `fast_turns.py` works. Writing `0x0080AA08` collapses the
on-screen timer immediately and fires turns. An intermediate conclusion that the address
was only the turn *length* and could not affect the running turn was wrong — remaining
time is *computed* from it rather than stored, which is also why no dword anywhere in
99 MB of writable memory holds remaining-seconds, remaining-milliseconds, or a float of
either.

**`Owner:12` research — resolved.** Assigning scientists separated it decisively:

| Field | Human (2 planets, scientists) | AI (1 planet) |
|---|---|---|
| `Owner:12` Research | 1276 → 1515 (**+239**) | 480 → 520 (**+40**) |
| `Owner:8` Gold | +57 | +26 |
| `Owner:24` | +20 | +10 |
| `Owner:28` Score | **+30** | +10 |

`Owner:12` is genuinely per-civ. The earlier identical `+40`/turn readings were real
equality of science output, not a shared global — the one-turn-old colony contributed
nothing until scientists were assigned to it.

This run also splits `Owner:24` from `Owner:28`, which had moved together up to now:

- **`Owner:24` is the per-planet score component** — `+10` per owned planet per turn,
  stable across three measurements and completely unaffected by assigning scientists.
- **`Owner:28` is the total score** — it went from `+20` to `+30` per turn for the same
  civ when scientists were added, so it carries at least one research or economy term on
  top of the planet component.

**Correction to the constructor-derived layout.** `Owner:32` and `Owner:36` were recorded
as science output and surplus (from `[civ+0x54]` / `[civ+0x58]`). **Both read `0` for both
civs while research accrues at 239/turn**, so that mapping is wrong — as was
`[civ+0x38]` → `Owner:4` for the current topic. Every field the constructor analysis
placed has now failed direct observation; the empirically verified layout is
`Owner:12` progress, `Owner:144`/`:152` topic.

Incidental: `ShipDesign` #635 (the used-up Colony Ship) is progressively converting into a
base template — `+36`/`+40` became `-1.0f` and `+56/60/64/68/72/112` became `0xFFFFFFFF`
across two turns. That is how the FF-sentinel templates described earlier come to exist.

### `Planet:368` is not an ownership flag — falsified (July 2026)

`Planet:368` was recorded as "bit 0 = colonised/owned", on the strength of two
observations in an earlier galaxy where colonised planets read odd values (1, then 3) and
uncolonised ones read even (0, then 2). Direct measurement in the current galaxy kills it:

| `Planet:368` | Owned? | Count |
|---|---|---|
| 8 | no | 522 |
| **8** | **yes** | **2** (both homeworlds) |
| 5 | yes | 1 (the one-turn-old colony) |

Both homeworlds share a value with every unowned planet in the galaxy. The correlation was
coincidental — two samples of a field that moves for unrelated reasons. Its change
frequency is equally unstable: 525 of 525 planets on some turns, 1 of 525 on others.

**Ownership has exactly one reliable source: the `Planet:40` reference node.** That has now
resolved correctly across two galaxies and 685 planets with no failures.

This is the third annotation in this series to survive two observations and fail the
third — after `Owner:24` and the `Planet:68` population reading. Two agreeing samples of
a moving field are not evidence.

### Food confirmed (July 2026)

`Planet:112` Current Food Stored is solid. Over one turn it changed on **exactly** the
three colonised planets and nothing else: homeworld `274 → 427` (+153), the new colony
`0 → 99` (+99), the rival homeworld `198 → 278` (+80). It has now behaved this way across
three separate turns, with the set of changing planets tracking the set of colonies
each time.

Two new per-civ-looking `Planet` fields surfaced in the same diff:

- **`Planet:512`** is *identical on every planet of a civ* (1465 on both human planets) and
  `0` on the rival's homeworld, growing `+90`/turn. That is per-civ data replicated into
  each planet, not a per-planet value.
- **`Planet:516`** holds floats (3.16, 75.27) that **swapped between the two human planets**
  over a single turn, and reads `0` on the rival's — almost certainly render or animation
  state rather than game state.

`Planet:352/356/360/364` are a four-slot group of packed `uint16` pairs where both halves
hold the same value, and bit `0x1000` in each half is a flag that gets set and cleared
(`0x10050005 → 0x00050005`). Homeworlds read 7 and 8; the new colony reads 5 and 4.

### Planet collections and the build selection (July 2026)

**`Planet:144` is a vector of 16-byte elements**, each laid out
`[0, flags, owner-node, small-int]`, where the owner-node is the same reference node the
`+40` ownership link uses and points at the planet's own `Owner`. Element counts are 5, 7
and 8 on the three colonised planets.

**`Planet:352` is that vector's element count.** Its low `uint16` reads exactly 5, 7 and 8
on those same planets. This gives the packed-pair group its first confirmed meaning.

**`Planet:384` is a single-element vector**, `[owner-node, float, int, 0xFFFFFFFF]`, present
on every colonised planet. It is **not** the production queue: the `int` held at 6 when the
queued build was switched from a farm to a shipyard. Its float reads 15.0 on both
homeworlds and 9.0 on the one-turn-old colony.

**`Planet:284` is the next-building selection — confirmed.** Three distinct builds gave
three distinct values, and each time it was the only gameplay field on the planet to move:

| Build selected | `Planet:284` |
|---|---|
| Farm | 0 |
| Shipyard | 2 |
| University | 4 |

Because 0, 2 and 4 were also valid slot numbers in the planet's 7-element `Planet:144`
vector, the value could have been an index rather than a type id. Selecting the **same**
building (university) on a planet with a **9-element** list settles it: `Planet:284` read
**4 on both**, which an index into differently-sized lists cannot do.

**`Planet:284` is a building type id.** Selecting a **military camp** repeated the test and
read `6` on both planets, independently confirming the conclusion.

| Building | `Planet:284` |
|---|---|
| Farm | 0 — *see caveat* |
| Shipyard | 2 |
| University | 4 |
| Military camp | 6 |

The ids observed so far are **even and consecutive**, which makes `farm = 0` plausible as
the first table entry.

**There is no "nothing selected" state.** A planet not constructing is *generating wealth*,
which is a real production mode — the binary carries a `PilingUpWealth` class at
`0x00769ADC` alongside `GovernorRuleSwitchToPilingUpWealth` and
`GovernorRuleSwitchToProductionQueue`. The earlier framing of `0` as "no selection" was
wrong.

**`Planet:284` holds the last selected building and is not cleared by wealth mode.**
Switching both planets to generating-wealth left it at `6` (military camp). It is therefore
the *chosen building*, not the *current activity*, and the wealth/build mode itself is
recorded somewhere else — not in any planet field that separates the two civs
(only `+16`, `+284`, `+508`, `+512`, `+572/576/580` do, none of them a plausible mode flag).

**Values are committed promptly, not lagged.** This was worth ruling out, because if
selections reached memory a step late then every id in the table would be shifted by one
row — `wealth=0, farm=2, shipyard=4, university=6` fits the same observations. The test was
to advance a full turn with no UI action: `+284` stayed at `6`, so there is no pending
update and the table above stands as measured.

`farm = 0` is accordingly credible: the other three ids registered promptly when selected,
and `0,2,4,6` is even and consecutive with no gaps. It is not independently proven, since a
planet that has never chosen a building also reads `0` (the AI's homeworld does).

### Production classes are not EJBO-tagged — but they are reachable

`Production`, `Facility`, `ProductionQueue`, `SharedProductionQueue`, `ShipProduction`,
`Scan` and `Treaty` all exist as C++ classes with vftables, but **no live EJBO object uses
any of them**, so an EJBO scan cannot see them.

**They are reachable by pointer.** Ordinary pointer fields inside EJBO objects point at
untagged C++ objects, and because every polymorphic class keeps its vftable at offset 0,
resolving that first dword against the 434-class RTTI map names the target immediately.
This generalises the technique to the whole object graph, not just tagged objects.

### `Planet:296` — the current production object (July 2026)

`Planet:296` is a **polymorphic pointer to whatever the planet is currently producing**:

| Planet state | `Planet:296` | Class at that address |
|---|---|---|
| Building a farm | `0x0A113240` (heap) | **`Facility`** (`0x00752BA4`) |
| Generating wealth | `0x0080B540` (`.data`) | **`PilingUpWealth`** (`0x00769ADC`) |

Generating-wealth is represented by a **shared static singleton** at `0x0080B540` that every
idle planet points at — the same idiom as the static null-owner node at `0x00857C54`. Both
wealth-mode planets pointed at the identical address. `ShipProduction` is the expected value
when a planet is building ships.

So the production mode is read by resolving one pointer, and `Planet:344` corroborates it
(1 while constructing, 0 while generating wealth).

Other pointers resolved out of the same object in one pass: `Planet:460`/`464` →
`PlanetProperties`, `Planet:492` → another `Planet`, `Planet:500` → `Texture`.

### Names: planet and system (July 2026)

Renaming two planets to `p1`/`p2` and the system to `s1` confirmed both fields against
the UI:

| Name | Field | Length | Capacity |
|---|---|---|---|
| Planet | `Planet:52` | `Planet:68` | `Planet:72` |
| **System / star** | **`Sun:52`** | `Sun:68` | `Sun:72` |

**The system name lives on the `Sun` object, not on a `SolarSystem` object** — the star is
the system. `Sun:52` uses the identical `std::string` SSO layout as `Planet:52`, and
`Sun:72` reads 15 on all 108 suns. Exactly one sun is named (`Sun #257` = `s1`); the other
107 have length 0.

This independently corroborates the `Planet:512` per-solar-system finding. The six planets
that share a `Planet:512` value — `#258`–`#263` — are precisely the six nearest `Sun #257`,
at 13.5 to 38.3 units, with the next-closest planet 106 units out. Two unrelated methods
agree on the same system membership.

**Method note.** The system name was found by scanning all writable memory for the SSO
signature directly (buffer text, `_Mysize` matching its length, `_Myres` == 15) and then
walking back to the owning object's vftable. That is more reliable than following pointers
into candidate objects: an earlier attempt resolved 59 pointers to `SolarSystem` and then
scanned 512 bytes of each for strings, which found UI labels like `Light-Firepower` — a
read-window overrun into neighbouring allocations, the same class of error the per-class
extents exist to prevent. Those `SolarSystem` results are inconclusive, not evidence.

### `Planet:92` — colonised-since turn (July 2026)

A third planet colonised specifically for the test gave three distinct UI values to match:
`p1 = 0`, `p2 = 6`, `p3 = 32`. **`Planet:92` held exactly `0 / 6 / 32`** and was the only
offset among 158 visible fields to do so — no match under a ×10 or ×100 encoding either.
All 522 uncolonised planets read `0`.

As with the farm build id, **`0` is ambiguous**: it means both "homeworld, colonised at turn
0" and "never colonised". The `Planet:40` owner link disambiguates, and should be checked
first whenever this field is read.

Three unrelated annotations were corroborated in the same dump: `Planet:116` Max Food
Storage reads 760 / 920 / **80** — the day-old colony has the small cap seen on new colonies
previously; `Planet:284` holds `6` only on the planet with a building selected; and
`Planet:368` reads 8 / 7 / 1 across three planets owned by the *same* civ, further confirming
it has nothing to do with ownership.

### Population is the `Planet:144` citizen list — earlier reading retracted (July 2026)

UI population for three planets (`8/30`, `10/19`, `6/27`) did not match **any** integer field
in `Planet`, in the `PlanetProperties` objects at `Planet:460/464`, at ×10/×100 scalings, in
16-bit halves, or in individual bytes. It is not stored as a number at all.

**`Planet:144` holds one 16-byte element per population unit**, and `Planet:352` is its count.
The element's trailing value is the **turn that citizen was added**:

| Planet | `Planet:92` colonised | citizen turnAdded values |
|---|---|---|
| p1 | 0 | 0,0,0,0,0,0,**16,20** |
| p2 | 6 | 0,0,**8,10,13,17,21,26,30,36,44** |
| p3 | 32 | 0,0,**34,36,38,41,44** |

Every value is ≥ that planet's colonisation turn, the homeworld carries six entries at turn 0
(its starting population), and later entries strictly increase. The structure validates itself.

Counts read 8 / 11 / 7 against a UI showing 8 / 10 / 6, because p2 and p3 each gained a
citizen between the UI being read and memory being sampled — the game was live. Population
growth is also the correct explanation for the count changes seen in earlier sessions
(5/7/8 → 7/9/9 → 8/11/7).

**Retraction.** `Planet:144` was previously recorded as a build-options list whose count grew
"when research unlocked new buildings". That was wrong on both counts — it is the population
list, and it grows because population grows. The coincidence held because more buildings *were*
researched during the same interval that population increased.

`Planet:384`'s single element also gave up a field: its third dword reads 0 / 6 / 32,
matching `Planet:92` exactly, so that element is a per-planet colonisation record.

### Military is also a list — `Planet:168` (July 2026)

Military counts of 3 / 2 / 0, read from the UI and matched against a snapshot taken
immediately after, are **not** stored as a number either — no match as an integer, scaled
integer, 16-bit half, byte or float in `Planet`, `PlanetProperties`, or within 256 bytes of
any pointer out of `Planet`.

Applying the lesson from population — count the vectors, not the numbers —
**`Planet:168/172/176` was the only `(begin,end)` pair in the entire object whose element
count is 3 / 2 / 0.** One 16-byte element per military unit, and the element has the *same
shape* as a citizen:

```
[ rank, ?, owner-node, turnAdded(low16) ]
```

All observed units are rank 3, the owner-node resolves to the planet's own `Owner`, and
`turnAdded` values (37, 42, 46 on p1; 41, 45 on p2) sit above the planet's colonisation turn
exactly as citizens' do. A planet with no military has all three pointers **null**, and across
all 525 planets a non-empty military list occurs only on owned planets.

So two of the four population-panel stats are per-unit collections rather than counters. That
is now the first thing to try for any remaining count-shaped stat.

### Military units: everything but upkeep is derived from `turnAdded` (July 2026)

The UI shows four values per stationed military unit — upkeep, rank, experience and turns to
next rank. Only **upkeep** is stored. Element layout for `Planet:168`:

```
+0        upkeep            (3 on every unit observed)
+4  lo16  unidentified
+6, +14   uninitialised padding holding stale heap bytes
+8        owner-node -> the planet's own Owner
+12 lo16  turnAdded
```

Rank, experience and next-rank-turns are **not present in the element** in any byte, uint16,
dword or float form, and a full-memory sweep for the experience floats is useless as evidence:
`1.0` occurs 97,614 times and `2.0` 8,085 times in the address space, so any "nearby match" is
noise. Only `0.2` is rare (123 occurrences).

They are all derived. Solving for the single current-turn value that makes three units share
one experience rate gives **turn 47 at 0.2 experience per turn**, and every displayed value
then follows:

| Unit | `turnAdded` | age | `0.2 × age` | UI exp | `floor(exp/2)` | UI rank | `(2(rank+1) − exp)/0.2` | UI next |
|---|---|---|---|---|---|---|---|---|
| 1 | 37 | 10 | **2.0** | 2.0 | **1** | 1 | **10** | 10 |
| 2 | 42 | 5 | **1.0** | 1.0 | **0** | none | **5** | 5 |
| 3 | 46 | 1 | **0.2** | 0.2 | **0** | none | **9** | 9 |

```
experience         = RATE * (currentTurn - turnAdded)
rank               = floor(experience / 2)
turns to next rank = (2 * (rank + 1) - experience) / RATE
```

Nine predictions from one free parameter, all exact. For state sync this means a military unit
needs only its upkeep, owner and creation turn transferred.

**`RATE` is per-planet, and a military base doubles it.** p2 has no military base; its two
units read 0.7 and 0.3 experience at ages 7 and 3, giving **0.1 per turn**. p1 does have one,
and its three units gave **0.2 per turn**. Neither rate fits the other planet: at 0.1, p1's
units would imply the game was simultaneously at turns 57, 52 and 48. So base rate is 0.1 and
the military base doubles it — confirming that the facility's "decreases time to recruit"
description covers experience gain, not just recruitment time.

**Built facilities are not reachable as `Facility` objects.** p1 has a completed military base
but **no** pointer to a `Facility` anywhere in the object; p2's only `Facility` pointer is
`Planet:296`, its in-progress farm. Every other vector-shaped `(begin,end,cap)` triple on all
three planets is empty. So completed facilities are stored some other way — most likely packed
flags or counts rather than objects.

**Lead:** `Planet:100` reads `0x3C646464` on p1 and `0x00646464` on **every other planet,
including uncolonised ones**. Bytes 0–2 are `100/100/100`, matching a UI that shows loyalty at
100% everywhere, so one of them is very likely loyalty. Byte 3 is `60` on p1 alone — the only
clean p1-distinctive value found — making it the best candidate for the military base's effect.
This is a single-planet correlation and needs a second planet with a military base to confirm.

### `Planet:208` counts distinct facility *types* (July 2026)

A UI facility listing — p1 with farm, shipyard, military camp and light turret; p2 with just a
farm; p3 with none — matched `Planet:208` reading **4 / 1 / 0**. The AI homeworld reads `3` and
all 521 uncolonised planets read `0`. The value is mirrored at `PlanetProperties+120`.

**It counts distinct types, not facilities.** After a university was added to p1 and a *second
farm* to p2, the field read **5 / 1 / 0**: p1's five types each present once, and p2 still `1`
despite the UI showing a `2` under its farm icon. Every earlier reading is consistent with the
types interpretation too, so the count reading was never distinguishable until one planet held
two of the same facility.

This resolves an annotation that had sat unexplained for several sessions as "`= 3` on both
homeworlds, `0` on the newly founded colony": a homeworld simply starts with three facilities,
so the field was always a count.

**Only the count is stored.** Which facilities are built is **not** a bitmask, **not** a vector,
and **not** a per-type count array:

- A mask with popcount 4/1/0 whose p2 bit is a subset of p1's — which must hold, since both have
  a farm — exists nowhere in `Planet` or `PlanetProperties`.
- No `(begin,end)` pair at any element size from 2 to 66 bytes gives those counts.
- A per-type count array (p1 five entries at 1, p2 one entry at 1, p3 all zero) was searched at
  1-, 2- and 4-byte widths over lengths 8–32 across both objects. Nothing.
- p1 has **no** pointer to a `Facility` object despite five built facilities.

Adding a university to p1 moved `Planet:208` from 4 to 5, confirming it increments on
completion. A **bitmask is now ruled out in principle**, not just empirically: a second farm on
one planet means the same facility type can be held more than once, which a mask cannot express.

One near-miss worth recording: searching for a structure with counts 5/2/0 matched `Planet:168`,
the *military* list, which happened to hold 5 and 2 units at that moment. The facility count
field disagreeing is what caught it.

### `Planet:204` is the facility map — identities and counts (July 2026)

The facilities are held in an **MSVC `std::map`/`std::set` keyed by facility type id**, rooted at
`Planet:204`. Nothing in `Planet`'s own fields encodes them, which is why every flat search failed.

Head node: `[_Left = leftmost, _Parent = root, _Right = rightmost]`. An **empty map points all
three at itself** — p3 does exactly that. Each tree node is `[_Left, _Parent, _Right]` followed by
an **embedded `Facility`**:

```
node +0   _Left
node +4   _Parent          (head+4 is the tree root)
node +8   _Right
node +12  Facility vftable  0x00752BA4
node +16  facility TYPE ID
node +20  COUNT of that facility on this planet
```

Walking it in-order reproduces the UI exactly:

| Planet | `Planet:208` | Map contents | Total |
|---|---|---|---|
| p1 | 5 | farm×1, shipyard×1, university×1, military camp×1, **type 9**×1 | 5 |
| p2 | 1 | **farm×3** | 3 |
| p3 | 0 | *(empty)* | 0 |
| AI HQ | 3 | shipyard×1, military camp×1, type 9×1 | 3 |

Since p1's listing was farm, shipyard, military camp, **light turret** and university, **type 9 =
light turret**. `Planet:208` is the **map size**, verified equal to the walked node count on **all
525 planets**.

Two corrections follow:

- Facility type ids share the `Planet:284` build-selection id space, and **they are not all even**.
  `light turret = 9` breaks the "even and consecutive" pattern noted when only farm (0),
  shipyard (2), university (4) and military camp (6) had been observed — that was an artifact of
  which four buildings happened to be tested first.
- **`PlanetProperties` is a copy of the `Planet` field block shifted by exactly 88 bytes**
  (`Planet:92`→`+4`, `:100`→`+12`, `:104`→`+16`, `:112`→`+24`, `:116`→`+28`, `:120`→`+32`,
  `:208`→`+120`). Searching it was therefore never going to find anything `Planet` lacked, which
  retroactively explains several fruitless scans.

**Method note.** The completion diff that was supposed to crack this found nothing, because
snapshots only cover EJBO objects and the map lives in untagged heap nodes. What worked was
following pointers two levels deep and testing every offset against the known triple — the map
node turned up as a hit at 1-, 2- **and** 4-byte widths simultaneously, which is the signature of
a small integer in a dword rather than a coincidental byte match.
[ ] annotate the building-queue setting — not critical, players can play without it

### The turn counter — `0x008578E8` (July 2026)

The turn number is a **`.data` global at `0x008578E8`**, not a field on any EJBO object. It was
found by capturing every address holding `48` while the UI showed turn 48, advancing exactly one
turn, and keeping only those that became `49`. Three of 52 candidates survived:

| Address | Value | Verdict |
|---|---|---|
| **`0x008578E8`** | 49 | `.data` global — **the turn counter** |
| `0x0A164A64` | 490 | `Owner #638 +24` — coincidence, see below |
| `0x0A1D0D3C` | 49 | `Governor #643 +140` — coincidence, 1 of 3 governors only |

This finally explains the `Owner:24` saga. That field is the **per-planet score component**,
`+10` per owned planet per turn. The AI civilisation owns **exactly one planet** and started from
zero, so its value increments `+10` every turn and is numerically identical to `turn × 10`
*forever* — it read `490` at turn 49. Three separate readings of that field supported three
different conclusions, and the reason is now concrete rather than mysterious: the coincidence
only breaks on a civ whose planet count is not 1, which is precisely the asymmetry test that
eventually falsified it.

Recording the address rather than a per-object field also matters for sync: the turn is global
state, and every `turnAdded` in the citizen and military lists is only interpretable against it.

### Citizen element layout and job ids (July 2026)

A UI job breakdown — p1 with 4 farmers / 3 workers / 2 miners, p2 with 8 farmers /
2 scientists / 1 banker — pinned the job field to the **first dword of each citizen
element**. It was the only field in the 16 bytes whose value distribution matched both
planets' shapes:

| `+0` | on p1 | on p2 | Job |
|---|---|---|---|
| 0 | **4** | **8** | farmer |
| 1 | **3** | — | worker |
| 2 | — | **2** | scientist |
| 5 | **2** | — | miner |
| 6 | — | **1** | banker |

`farmer = 0` is corroborated on two planets independently (4 and 8 citizens), which is what
makes the mapping trustworthy rather than a single-shape fit. Ids **3 and 4 are unobserved** —
two more job types exist in the gap and would be filled by assigning them on any planet.

Confirmed element layout for `Planet:144`:

```
+0   job id
+4   flags (0x004F0000 on most entries, 0 on some)
+8   owner-node -> the planet's own Owner
+12  turnAdded (low 16 bits)
```

The `+4` flag does not correspond cleanly to `turnAdded` being zero, so it is not simply a
"starting population" marker; unexplained.

### Maximum population is derived from planet space — `Planet:104` (July 2026)

Maximum population has no field of its own. **`Planet:104` holds planet space in its high
16 bits**, and max population is `floor(space / 10)`:

| Planet | `Planet:104` hi16 (space) | `floor(/10)` | UI max pop |
|---|---|---|---|
| p1 | 300 | 30 | 30 |
| p2 | 198 | 19 | 19 |
| p3 | **271** | **27** | 27 |

p2 fixes the rounding rule as truncation (198 → 19.8 → 19), and p3's space was **predicted to
fall in 270–279 before being read** — it is 271. Space is non-zero on all 525 planets
(range 156–450), so it is intrinsic to the planet rather than a consequence of colonisation.
The low 16 bits are `0` on 509 of 525 planets and remain unidentified.

**Still not located:** loyalty (100% on all three) and corruption
(+0.68% / −0.61% / −0.47%). Searched as integers, scaled integers, 16-bit halves, bytes and
floats across `Planet` and `PlanetProperties`, and as vector element counts. Loyalty at a
uniform 100% carries no discriminating information and cannot be found until one planet
differs; corruption is plausibly derived at display time.

[ ] test whether loyalty and corruption are derived rather than stored — populate one planet's population, buildings and military fully, and check whether both values follow from those inputs without any field of their own changing.

### Governor — located and confirmed (July 2026)

Two governors (`g1`, `g2`) with `g1` assigned to the homeworld. Everything checked out
against the UI state:

- **`Governor` IS EJBO-tagged** (vftables `0x00787230` / `0x00787228`). Three objects
  exist for two governors: `#642` = `g1`, `#643` = `g2`, plus a **static template at
  `0x00849484` in `.data`** holding a copy of the last-edited governor — the same idiom as
  the static `Admiral` at `0x0083A8D8`, and it must likewise be filtered out of any
  enumeration.
- **Name is a `std::string` at exactly the `Admiral` offsets**: buffer `+20 … +35`,
  length `Governor:36`, capacity `Governor:40`. Both names read length 2.
- **`Planet:496` is the governor assignment.** It is a reference node whose `node[0]` is
  the assigned `Governor`; the homeworld resolved to `Governor #642` (`g1`), matching what
  was assigned in the UI, and no other planet points at a governor. That is the same
  reference-node idiom as `Planet:40` ownership and `Ship:80` admiral assignment — the
  third confirmed instance of it.
- **`Governor:60` is the rule-chain head**, a reference node resolving to a
  `GovernorRule*` object (`GovernorRuleSustainPlanet` on all three). This is the entry
  point for decoding governor instructions, which the 40-odd `GovernorCondition*` and
  `GovernorRule*` classes in RTTI describe.

`Governor`'s extent is not measurable — the two heap instances are 768 bytes apart but a
single stride never repeats, so the read window stays at the configured 192. The object is
at most 760 bytes.

### `Planet:284` farm id confirmed

`farm = 0` is now proven rather than inferred. The earlier objection was that a planet which
had never chosen a building also reads `0`. Setting a farm on the colony — which had already
selected a shipyard, a university and a military camp — moved it `6 → 0`, while the homeworld
left on wealth stayed at `6`. Final table:

| Building | `Planet:284` |
|---|---|
| Farm | 0 |
| Shipyard | 2 |
| University | 4 |
| Military camp | 6 |

**Correction:** an earlier note said `ShipDesign` #635 was progressively converting into an
FF-sentinel base template. It is not — the same fields reverted to real values
(`Speed`, `Thrust`, `Effective Hitpoints` 60, metal 164, radioactives 18). The object
toggles between populated and sentinel states rather than decaying into one, so the
explanation offered for the origin of base templates is withdrawn.

Note this is the **next-building** slot only. The game also has an optional build queue,
which was deliberately left unused during these tests and is a separate structure.

**Retraction — `Planet:144` is not a build-options list keyed by type id.** Its elements'
trailing ints (`0,0,8,10,13,17,21` on the 7-element planet) do not correspond to
`Planet:284`: the selected university is `4`, and `4` appears nowhere in the list. On the
homeworld the selected element's trailing int is `0`, and seven of nine entries are `0`.
What survives is weaker but solid: the vector's element count is mirrored in `Planet:352`,
and it grows on every colonised planet when research unlocks buildings. Its purpose is
unidentified.

**Correction — `Planet:512` is per-solar-system, not per-civ.** It was recorded last
session as identical across a civ's planets. The switch diff shows all six planets
`#258`–`#263` moving together, and four of those are **uncolonised**, so it cannot be
per-civ. Six contiguous planet ids sharing a value and ticking in lockstep is a solar
system — the human's home system, which is also where the second colony was founded. It
reads `0` on the rival's system.

**`Owner` runtime layout** (confirmed via constructor at `0x005459A0`; vftable `0x007707E0`).
This class was previously labelled `CivStats`; its RTTI name is `Owner` and annotation keys
now use `Owner:*`:

| Runtime offset | EJBO offset | Field |
|---|---|---|
| `[civ+0x38]` | `Owner:4` | Current science topic (tech id); `-1` = none (“No Research”) |
| `[civ+0x40]` | `Owner:12` | Current research progress |
| `[civ+0x54]` / `[civ+0x58]` | `Owner:32/36` | Science output / surplus (floats) |

Research-topic display and the `No Research` branch are at `0x00554DE1` (`cmp [civ+0x38], -1`).

### Tools

- **`ejbo_viewer.py`** — Web-based dashboard that scans for all EJBO objects, names each one from the binary's RTTI, sizes the read window per class from measured object stride, displays fields with annotations, and supports double-click editing (poke) via WriteProcessMemory. Auto-reconnects when the game restarts. Extent decisions are printed at scan time as `[extent] …` lines.
- **`ejbo_annotations.json`** — Persistent field labels keyed `<RTTI class>:<offset>`.

Full memory research notes: `MEMORY_RESEARCH.md`

---

## 12. Save/Load Capture & Automation (June 2026)

### Save blob captured — format confirmed against real data
A manual in-game **Save Game** (TestBed galaxy) produced the first real `savegame`
POST ever captured:

- Body (`Content-Length` 12,853): `userid=0&passhash='0'&gameid=-1&gamename='3'&turn=2&version=1&data=<…>`
- `gameid=-1` is the "allocate new slot" sentinel (Section 9).
- The `data=` field is URL-encoded for `x-www-form-urlencoded`: `+` → `%2B`, `/` left literal.

Decoding confirms the historical format (Section 2) exactly:

```
data = base64( uint32_LE(decompressed_size) + zlib_deflate(state) )
```

- Leading bytes after base64-decode: `C6 92 00 00 78 9C` → size **37,574**, then the zlib magic `78 9C`.
- The inflated blob is the documented hierarchical tag structure: `SAVE` → `GSET` with
  readable field keys (`name, speed, team, xp, sandbox, 2d, rank, maxusers, turnlength,
  density, primetime, primetime_turnlength, startticks, sectorsize, …`).
- `loadgame` response version `000000` (identity cipher) round-trips the blob unchanged.

**Tooling added:**
- `save_codec.py` — decode/encode the `data=` field. `from-log <cs_server.log>` extracts
  every savegame blob (handles the `body+` line-wrapping); `walk` dumps the section/tag tree.
- `cs_server.py` — `savegame` now **persists** the blob (allocates a positive `gameid` from
  the `-1` sentinel), `savegamelist` enumerates real saves, `loadgame` returns the stored blob.
  Saves persist to `game_data/multiplayer_saves/` and hot-reload on each `savegamelist`/`loadgame`.

### External save/load trigger — NOT achievable
Automated multiplayer sync requires firing save (upload) and load (download) from outside the
EXE on a schedule. Findings:

- **No dedicated sync endpoint.** The complete client API is only `savegame` / `savegamelist` /
  `loadgame` (plus civ / coa / gov / tutorial). There is no "submit orders" or "turn status"
  call — all state sync rides on those three primitives and the client's internal
  `out-of-sync` / `not up-to-date` check.

- **Turn resolution does NOT upload a save.** Firing a full turn via the countdown write at
  `0x0080AA08` (`game_controller.py fire-turn`, same mechanism as `fast_turns.py`) resolves the
  turn client-side but produces **no `savegame` POST** in `cs_server.log`. Confirmed on the
  TestBed build, which still carries the turn-pipeline sync checks that the Resurgence T1–T5
  patches remove. This is consistent with the original observation that nothing crosses the
  network during normal ticks.

- **Manual Save Game is the only confirmed save trigger.** A `savegame` upload occurs only on an
  explicit in-game **Save Game** menu action. No external/programmatic trigger was found.

- **Load auto-trigger — implemented server-side, UNCONFIRMED on the client.** `cs_server.py` can
  advertise a higher `turn` in `savegamelist` (`game_controller.py push` / `bump`), which is
  expected to trip the client's out-of-sync check and cause an automatic `loadgame`. The server
  side is verified (list advertises the bumped turn; `loadgame` serves the pushed blob, which
  decodes back to the exact pushed state). Whether the **real client** actually auto-loads on the
  bump was not confirmed.

**Untried fallbacks (higher effort, not pursued):**
- Win32 `PostMessage(WM_COMMAND, <Save-Game cmd id>)` to drive the Save menu headlessly
  (`game_controller.py save-menu`; `CMD_SAVEGAME` is a TODO — find via Resource Hacker / Spy++).
  Would still need to auto-confirm the `LoadSaveDlg` name/confirm dialog.
- Direct save-routine call via `CreateRemoteThread` / thread hijack — fragile (calling
  convention, `this` pointer, arguments).

### Conclusion
Save/load-blob sync (the "save → upload → load" approach) is **not viable for automated
multiplayer**: there is no programmatic save trigger in the protocol, and the turn pipeline does
not upload state. The save blob **format is now fully understood** and usable for **manual**
snapshots, persistence, and host-migration, but not for unattended per-turn sync. **The primary
path remains EJBO live-memory field sync (Section 11)** — it requires no in-game save/load and is
already proven (memory writes produce immediate in-game effects; turns are driven via `0x0080AA08`).

---

### What We Are NOT Doing

- **OAuth / social login** — requires new UI windows in the EXE; not feasible via patching

---                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             