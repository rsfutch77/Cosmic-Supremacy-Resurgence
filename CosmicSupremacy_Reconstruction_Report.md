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

The `data=` field in `savegame` POST requests contains a game state snapshot encoded as `base64( uint32_LE(decompressed_size) + zlib_deflate(structured_binary) )`. The decompressed blob uses a hierarchical section-based format (SAVE/GSET/GLOB/OWNR/SOLA/SHIP/etc.).

The parser recovered from git history (`1b4918c:prototype/server/save_parser.py`) now lives at `server/save_parser.py`. Its GSET key-value decoding still stands; its section discovery has been replaced, because the framing turned out to be self-describing.

#### Section framing — read out of the archive class, not inferred from blobs

Every section, from the outermost `SAVE` down to the smallest leaf, carries the same 8-byte header:

| Offset | Type | Meaning |
|---|---|---|
| `+0` | `char[4]` | tag, e.g. `ROUT` |
| `+4` | `uint32` | bits 0–25 = payload size in bytes, **excluding** this header; bits 26–31 = section version |

- `Archive::BeginSection(tag, version)` at `0x005E6260` writes the tag byte-swapped — which is why an MSVC multi-char constant such as `'ROUT'` (`0x524F5554`) lands in the file as readable ASCII — then a dword holding the version in its top 6 bits and a `0x3FFFFFF` size placeholder.
- `Archive::EndSection` at `0x005E6320` seeks back and patches the low 26 bits with `current_offset - section_start - 8`.
- `Archive::WriteRaw` at `0x005E5E10` is a plain memcpy append with no per-field framing, so a payload is exactly the concatenation of the fields its writer emits, in order.

Two consequences. First, the blob can be walked **generically** — no section's internals need to be understood to find the next one — which supersedes the earlier marker-regex scan that could not tell a real tag from four bytes of float data spelling one. Second, the old SAVE-header reading (`uint16 body_size` + `uint16 version 0x1000` + `uint32 section_count`) is that single dword seen as two halves: version 4 gives a high `uint16` of `0x1000`, and a `body_size` of "decompressed size − 8" is exactly a payload size with the header excluded.

#### ROUT — the ship order, and a non-UI path to constructing one

`ROUT` is the save form of the order object that `Ship:48` points at. This matters beyond parsing: the engine's loader **builds a live order with no user interaction**, which is the capability the AI player and multiplayer both need (see the AI player's STRATEGY.md §6a).

| Role | Address |
|---|---|
| Writer `Route::Write` | `0x004E5310`, called from the ship writer at `0x0056ECC0` |
| Reader `Route::ReadFields` | `0x004E6D40` |
| Factory (allocates + reads) | `0x004E6E90` — `operator new(0x60)` |
| Attach `Ship::SetOrder` | `0x004DA450` — stores at `ship_base+0x38`, i.e. `Ship:48` |

The factory's `operator new(0x60)` independently confirms the 96-byte order-object size previously pinned from heap-block headers.

`ROUT` is **optional per ship**: the writer skips it when the order pointer is null (`test ebx, ebx / je` at `0x0056ECB9`), and on load the reader is entered only if the next tag actually is `ROUT`. `Ship::SetOrder` then rejects the order — freeing it immediately and returning false — if its byte `+0` reads 0, so an injected order must carry a non-zero byte `+0`.

Wire layout, in writer order, all little-endian:

| Wire | Object offset | Meaning |
|---|---|---|
| `u8` | `+0` | non-zero required, or the order is freed on attach |
| `u8` | `+1` | present only when section version ≥ 1; the writer always emits version 1 |
| `3 × f32` | `+4/+8/+12` | origin XYZ |
| `3 × f32` | `+16/+20/+24` | target XYZ |
| `u32` + `n × 28` | via `+28` | leg count, then legs (`_Myfirst` at `+40`) |
| `u32` + `n × 28` | via `+52` | leg count, then legs (`_Myfirst` at `+64`) |
| `f32` | `+76` | route progress |
| `3 × u32` | `+80/+84/+88` | origin XYZ again |
| `u32` | from `+92` | object id behind the reference node, or 0 |

Fixed part 54 bytes; total `54 + 28 × (both leg counts)`. Each leg is `[originXYZ, destXYZ, length]`.

This also **resolves two previously separate readings of the order object into one structure.** The writer hands both `+28` and `+52` to the *same* helper `0x004E5200`, which reads `_Myfirst`/`_Mylast` at `+0xC`/`+0x10` of whatever it is given and divides the span by 28. So `+28` and `+52` are two objects of the same type, each holding a 28-byte-element vector 12 bytes into itself — which is exactly why the known route-leg vector sits at `+40/+44/+48`.

- **Falsifiable prediction, not yet tested:** there is a **second leg vector at `+64/+68/+72`**. On a live order, `(_Mylast − _Myfirst)` there should be a multiple of 28.
- **The intrusive list nodes are not persisted.** The helper touches only the vector, and the reader constructs both `+28` and `+52` from scratch via `0x004A6D30`. An injected `ROUT` therefore supplies leg vectors only, and the engine rebuilds the node chains that make this object impossible to fabricate by hand.
- **One asymmetry to watch:** the writer emits the reference id raw, but the reader **adds the global at `0x00857C58`** to any non-zero id before resolving it through the object registry (`0x004D9CA0`). Injected ids may need to be written relative to that base.

#### DYNO — the enclosing section, and why ROUT alone is not enough

`ROUT` lives inside `DYNO`, not directly inside `SHIP`. From the writer at `0x004DA310`:

| Wire | Object offset | Annotation | Meaning |
|---|---|---|---|
| `u32` | `+0x34` | `Ship:44` | orbit planet id, 0 when not in orbit |
| `SHCO` v2, 9-byte payload | `+0x3c` | `Ship:52` | **payload byte 0 is the order type** |
| `u32` | `+0x50` | `Ship:72` | |
| `u8` | `+0x54` | `Ship:76` | has-orders |
| `u32` | `+0x58` | `Ship:80` | admiral id, 0 when unassigned |
| *only when the order pointer `+0x38` is non-null:* | | | |
| `ROUT` section | `+0x38` | `Ship:48` | the order |
| `u32` | `+0x6c` | `Ship:100` | |

The conditional tail is the tell: an unordered ship's DYNO is `4 + 17 + 4 + 1 + 4 = 30` bytes, an ordered one's is `30 + (8 + ROUT payload) + 4`. A capture with two ordered and two unordered ships gave exactly 30 / 152 / 124 / 30.

So pushing an order takes **three coordinated edits**, not one: the SHCO order-type byte, the has-orders byte, and the appended `ROUT` + `u32`. Writing only the `ROUT` leaves the order type at 0. `server/inject_order.py` does all three.

If the owner check at `0x004DA310+0x52` fails, the writer emits a short `INFO` form instead of the full one — which is what the `'INFO'` branch in the reader at `0x004DBBB2` consumes.

#### Confirmation status — CONFIRMED against engine-produced bytes

`savegame` was triggered on a live client with two ships under orders (a scout with one route leg and a colonize order with two), and every `ROUT` field was compared against the raw 96 bytes read out of the process:

- Both payload sizes were **exactly** `54 + 28 × leg_count` (82 and 110), predicted from the layout before the blob was parsed.
- `order_kind`, `flag_1`, origin XYZ, target XYZ, progress and `+80/84/88` all matched **bit-exactly**; every leg matched, and each leg's length equalled the euclidean distance between its own endpoints (600.0787, 16.8664, 11.0350).
- The parser consumed exactly the declared size in both cases.

Two corrections and one confirmation fell out of the capture:

- **The second leg vector is real and is serialised.** `legs_b` was empty on both orders, but the byte accounting only closes if its `u32` count is present — the 54-byte fixed part includes it. Its 28-byte element size remains binary-derived only, since a non-empty `legs_b` has not been seen.
- **`+80/+84/+88` is the ship's *current* position, not "origin XYZ again".** On both captured orders that triple equalled the `SHIP` section's own position, which differs from the order's origin once the ship has travelled. The earlier reading was taken on orders that had not yet moved, where the two coincide.
- `SAVE`'s payload opens with a `u32` that read `654` — exactly the live EJBO object count — and the DYNO orbit id read `257` for a ship the memory scan independently placed in orbit of planet 257 at distance 0.0000.

#### Serving a blob back: SaveGame is callable off-thread, LoadGame is not

`SaveGame` (`0x0048B350`) runs correctly from a `CreateRemoteThread` stub — it is synchronous down to WinInet's blocking `HttpSendRequestA` and touches no per-thread state. `client/dev_tools/trigger_save.py` does this and produced the 117,024-byte capture above with no user interaction.

**`LoadGame` (`0x0048B5D0`) cannot be called that way.** The load path reaches objects held in thread-local storage; every caller of `0x005D17E0` fetches `this` as:

```
mov eax, [0x0087346C]      ; _tls_index
mov ecx, fs:[0x2C]         ; TEB->ThreadLocalStoragePointer
mov edx, [ecx + eax*4]     ; this module's TLS block, per thread
mov ecx, [edx + 0x48]      ; a pointer living in that block
call 0x005D17E0
```

A remote thread's TLS block is zero-filled, so that slot is NULL and `0x005D17E0` faults on `mov ecx, [eax+0x10]`. Confirmed by doing it: `EXCEPTION_ACCESS_VIOLATION` at `0x005D17F5`, `eax=ecx=0`, on the injected thread, with the game writing its own minidump. This is consistent with the earlier note about a TLS RB-tree in the testbed load path.

Note the thread exit code still read as success — the game's crash handler runs on the faulting thread — so a load trigger must verify the process is still alive rather than trust the return value.

**Consequence:** the load has to run on the **main** thread, which owns the populated TLS block. No hijack was needed in the end — the engine already has a main-thread load path that takes a file (below).

### Pushing a state into the client at startup — the production mechanism

**The client loads a save blob named on its own command line, during startup, on the main thread, with no server and no UI involved.** This is the shape the original online game used: the player never sees a default galaxy, the client comes up already in the pushed state.

The standalone bootstrap is `0x00579620`, whose log strings give the structure away — `'activating stand-alone mode'`, `'loading demo-galaxy'`, `"loading save-game '%s'"`, `'creating a dummy galaxy'`. It branches on `[0x0086F410]`:

| Branch | Path |
|---|---|
| `[0x86F410] != 0` | load the **embedded resource** named `DemoGalaxy`, type `Binary`, from the EXE's `.rsrc`: read it into an archive, `0x0048AD70`, then `0x0056D700` |
| `[0x86F410] == 0` | if `0x0056E7F0` says yes, `"loading save-game '%s'"` then `0x0056DAD0(name, 1)` → `0x0056D700` |

`0x0056E7F0` is the gate, and it is simply:

1. `0x0062BF60()` — argument count; needs at least 1.
2. `0x0062C090(&out, 0)` — the first command-line argument.
3. Its last 4 characters must be **`.dat`** (compared with the same helper used for the `DONE` checks).
4. The file must exist.

So: **`CosmicSupremacy_Resurgence.exe <something>.dat` loads `<something>.dat` as the galaxy.**

**The `.dat` holds the raw, already-decompressed blob** — it starts with `SAVE`, not with base64. The file path is `0x0056DAD0` → `0x005E53E0` (file-backed archive) → `0x0056D700`, and `0x005E53E0` reaches neither the base64 decoder `0x005F5F10` nor the `uint32`+inflate step `0x0048AD70`; it only reads the file into a buffer. The wire format's extra layers belong to the HTTP path:

```
loadgame response :  base64( uint32 + zlib(blob) )   -> 0x005F5F10 -> 0x0048AD70 -> 0x0056D700
.dat on disk      :  blob                            ->                            0x0056D700
```

`server/inject_order.py --dat <path>.dat` writes that form; its `-o` output remains the wire form for `loadgame`.

#### CONFIRMED end to end: a server-authored order flew a ship with nobody at the keyboard

A capture was taken, ship 649 — which had no order at all — was given a Move order aimed at planet 259, and the result was written as a `.dat` and passed to a fresh client on the command line. Predictions were registered before the launch.

On load, ship 649 came up with:

| Field | Value |
|---|---|
| `Ship:48` | `0xA044F20` — non-null, allocated by the engine's own `operator new(0x60)` |
| `Ship:52` | `1` (Move), taken from the SHCO order-type byte |
| order origin / target | `(540.9454, 334.3067, 247.3618)` → `(516.7029, 333.0293, 278.5347)`, exactly planet 259 |
| legs | one leg, length `39.5105` |
| progress | `0.000000` |

After one turn:

- position `(532.6622, 333.8702, 258.0130)` — matching the pre-registered prediction to four decimals
- progress `13.5000`, exactly one turn at the design speed
- displacement from the origin exactly `13.5000`, confirming that **exact leg endpoints remove the engine's usual orbit-edge inset**
- travelled + remaining = `39.5105`, the leg length

This closes the blocker in the AI player's STRATEGY.md §6a. Orders no longer need a UI click, a fabricated object, or a remote-thread constructor call: the server writes a blob, the client is handed it at startup, and the engine builds the order with its own allocator and navigates it. Because the object is engine-constructed, it owns its allocations properly — the reason hand-fabrication was ruled out.

Two practical notes. The order type lives in **SHCO**, not in `ROUT`, so a pushed order needs the SHCO byte and the has-orders byte set alongside the appended `ROUT` — `inject_order.py` does all three. And the UI caveat already recorded for retargeted orders applies here too: the Ships tab may lag until a turn boundary, while the engine acts on the order immediately.

[ ] stop the **"Customize Your Home World" popup reappearing after a loaded save**. Observed on the
first successful `.dat` load: the galaxy came back correctly — workers, ships and settings all
carried over — but the client re-offered homeworld customisation and civ customisation a second
time, which would let a player bank a second round of upgrades every time a server pushed a state.
The likely cause is that **what marks customisation as already spent lives outside the save blob**.
The four homeworld click counts are `.data` globals at `0x00842AE4`–`0x00842AF0`, not fields on any
object, so nothing in a `SAVE` blob can restore them and a fresh process starts them at `0`; the
same question applies to the civ-trait allowance. `GSET` carries the budgets — `homeworld_changes`
(30) and `civilization_changes` (5) — but a budget is not a record of what has been used. Three
things to separate: whether the popup trigger is the per-tick check at `[esi+0x4988]` that
`FUN_0x496830` reads (see the `listcivnames` notes in `cs_server.py`, where an empty `coaid` also
leaves the civ permanently "unconfigured"), whether the spent counts are supposed to come back from
`OWNR`/`CVTR` rather than from `.data`, and whether the real server suppressed the popup by answering
`listcivnames`/`listcoa` differently once a civ was configured. Until it is settled, treat a pushed
state as re-opening the customisation window — a correctness problem for multiplayer, not a cosmetic
one. Deferred deliberately: it does not block order push, which is confirmed working.

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

### Ships in orbit — `Ship:44`, and it is stored not derived (July 2026)

Every orderless ship observed across three galaxies sat at **distance exactly 0.00** from a
planet, while every ship with a Move or Scout order was 4–15 units off. That made "in orbit =
co-located" the obvious hypothesis. **It is wrong.**

Teleporting an orderless ship 15 units off its planet left the UI still listing it as in orbit.
The relationship is held in **`Ship:44`**, a reference node pointing at the `Planet`:

| Ship | orderType | `Ship:44` | Distance to that planet |
|---|---|---|---|
| #649 | none | **`Planet#225`** | **15.00** — displaced, still in orbit |
| #654 | none | **`Planet#473`** | 0.00 |
| #650 | Move | `null-node` | — |
| #653 | Scout | `null-node` | — |

So orbit is a stored reference that happens to *coincide* with co-location, because the game
parks orbiting ships on the planet's coordinates. Position and orbit are independent: writing
coordinates moves the ship without changing what it orbits, and the reference is what the UI reads.

This is the **fourth** instance of the reference-node idiom — after `Planet:40`/`Ship:40` (owner),
`Ship:80` (admiral) and `Planet:496` (governor). Setting a ship into orbit programmatically means
writing the node pointer, not the coordinates.

Also resolved in the same pass: **`Ship:108` is the ship-design link**, a reference node to the
`ShipDesign` the ship was built from, with each civ's ships resolving to that civ's own design
objects. And `Ship:56` is confirmed to be a reference-node slot that is null on every ship in
every state observed, so it is none of owner, orbit, admiral or design.

**Method note.** Three galaxies of circumstantial evidence pointed the wrong way, and the
displacement test is what broke it — the ship kept its orbit while its coordinates said otherwise.
A correlation that holds in every sample can still be a consequence rather than a cause.

### Fleets absorb their ships — `Fleet` (July 2026)

Creating a fleet named `f1` from two ships produced the **first live `Fleet` instance** in this
project, and it is **EJBO-tagged** (vftable `0x00768904`). The memory note that `Fleet` "exists but
has no instances yet" is now obsolete.

**The ships stopped existing as `Ship` objects.** The scan went from 4 ships to 2 — both survivors
belonging to the AI — and the two human ships `#649` and `#656` vanished. Their old addresses now
read a recycled heap tag. They were folded into the fleet as **inline 44-byte records**:

| Record offset | Content |
|---|---|
| `+0` | `ShipProperties` vftable `0x00768C3C` |
| `+4` | `ShipDesign` reference node |
| `+8` | pointer |
| `+12`, `+16` | uninitialised (stale text bytes) |
| `+20/+24/+28` | inner **crew vector** — 16-byte elements, same shape as `Planet:144` citizens |
| `+32` | condition float |
| `+36` | the **fleet's** object id |
| `+40` | the **ship's** object id |

A 2-ship fleet gave `Fleet:116` span 88 = 2 × 44, and the records reported ship ids **649 and 656** —
exactly the two that disappeared. Record 0 carries 2 crew elements; record 1's crew vector is null,
matching the uncrewed ship observed before the fleet was formed.

`Fleet` shares the `SpaceObject` base layout with `Ship`: **`Fleet:40` is the owner link and
`Fleet:44` the in-orbit planet, at the same offsets as on `Ship`**. `Fleet:56` and `Fleet:80` are the
same null reference slots.

**The fleet name is at `Fleet:132`** (length `+148`, capacity `+152`) — and notably *not* at the
offsets every other named class uses: `Planet`/`Sun` at `+52`, `Admiral`/`Governor` at `+20`. The
"name is always at a fixed base-class offset" pattern does not hold.

**`Ship:56` is not the fleet link.** That was the prediction — a null reference slot on every ship,
with no fleets in existence. It stayed null. There is no per-ship fleet pointer because a ship in a
fleet is not a `Ship` object at all.

**Consequence for state sync:** enumerating ships by scanning EJBO tags **misses every ship in a
fleet**. Any ship inventory must walk `Fleet:116` records as well, and ship identity survives the
transition (the object id is preserved inside the record), so ids remain usable as keys.

### Ship orders: `Ship:48`, and ETA is derived (July 2026)

A Move-ordered ship compared against an idle ship of the **same owner in the same orbit** differed
in exactly three fields — `+48`, `+52` (order type) and `+76` (has orders). `Ship:48` is therefore
the **order object** pointer, present only while an order exists.

**The target is stored as coordinates, not as a pointer:**

| Order object | Content |
|---|---|
| `+4/+8/+12` | origin XYZ — the ship's current position |
| **`+16/+20/+24`** | **destination XYZ** — matched Planet #153's coordinates exactly |
| `+80/+84/+88` | origin XYZ again |
| `+92` | reference node to the **origin** planet |

Its first dword is not a vftable, so this is a plain struct rather than one of the RTTI classes.
That explains why every earlier scan for a `Planet` reference on `Ship` found only the orbit link at
`+44`: there is no destination pointer to find.

**ETA in turns is not stored.** It is `ceil(distance / speed)`: a UI ETA of `5` matched a distance of
`61.219` between the order object's origin and destination divided by the design's speed of `13.5`,
giving `4.535`. No `int32`, `float` or `uint16` equal to 5 exists anywhere on the `Ship` or in 256
bytes of the order object. `ceil`, `round` and `floor+1` all give 5 at this value, so the rounding
rule is not yet pinned — a case where the quotient falls below `.5` would separate them.

That makes ETA the fourth displayed value confirmed to be computed rather than stored, after
maximum population, military rank/experience, and loyalty/corruption's suspected derivation.

**Two annotations falsified.** `Ship:88` was recorded as "only present when ship in motion" but reads
exactly `100.0f` on **every** ship regardless of order state — a constant that looks like a percentage
at full value. `Ship:92` and `Ship:96` read `0` on every ship sampled, so their motion readings remain
unverified; no ship has yet been observed while genuinely under way, which is the state that would
test them.

**Move and Scout differ in what they target.** Retasking the same ship from Move to Scout changed
`Ship:52` from `1` to `2` and changed the order object's destination from a planet to a **sun**:

| Order | `Ship:52` | Destination XYZ matches |
|---|---|---|
| Move | 1 | exactly a `Planet` |
| Scout | 2 | exactly a `Sun` — distance `0.0000` on two independent scouting ships, nearest planet 12.6 units away |
| **Conquer** | **5** | exactly an **enemy-owned** `Planet` — `0.0000` on the rival homeworld, nearest sun 28.6 units away, issued from a design named `troopship` |
| **Attack** | **4** | confirmed on a fleet — the order lives on `Fleet:52`, and every member record inherits it rather than carrying its own |

So a scout targets a *system* while move and conquer target *planets*, and the destination's identity
distinguishes the orders as reliably as the id does. Ids `6` and anything above `7` remain
unaccounted for and should cover bio-bomb and create-wormhole.

[ ] identify the bio-bomb and create-wormhole order ids in `Ship:52` — needs a bio ship and a wormhole ship

**`Ship:76` is not simply "an order exists".** It reads `1` on the local player's ordered ships and `0`
on the AI's, even when the AI ship carries a valid order type and a fully populated order object. It
looks like a **pending/unsubmitted-orders flag for the local civ** — which would matter directly for
turn submission in multiplayer. Two observations only. It also read a non-boolean `980156416` on a
freshly built ship, so it may be uninitialised until an order is first set. Use `Ship:52` to ask
whether an order exists.

### Combat damage: condition is the only per-ship state (July 2026)

A fleet attack on a defended planet produced a useful spread of outcomes, captured by snapshotting
condition per **ship id** before and after so the diff survives ships being absorbed or deleted:

| Ship | Design | Before | After |
|---|---|---|---|
| #684 | `invader2` | 1.0000 | **0.0056** |
| #685 | `invader2` | 1.0000 | **0.6301** |
| #683 | `invader2` | 1.0000 | **deleted** |
| #674–#679 | `Scout` | 1.0000 | 1.0000 — untouched |
| #672 | rival `Colony Ship` | 1.0000 | **deleted** |

**`Ship:136` is a 0.0–1.0 damage fraction**, and it is the *only* per-ship damage state.
`ShipDesign:72` shield and `ShipDesign:52/56` base/effective HP did not move — they are design-level
constants, so a damaged ship does not track residual shielding separately. The same value sits at
`+32` of each `Fleet:116` member record.

**Destroyed ships are deleted, not flagged.** A killed ship's `Ship` object disappears entirely and
its 44-byte fleet record is removed, dropping the fleet's record count from 8 to 7. There is no
tombstone or zero-condition corpse, so any sync must treat a missing id as destruction rather than
looking for a death flag.

**Only the armed ships took damage.** All five zero-firepower `Scout` ships in the same fleet came
through at exactly 1.0000 while every `invader2` was hit or destroyed. Whether that is because
combat engages only armed ships or because it targets them first is not established from one battle.

### Deleting a ship safely — what is and is not possible (July 2026)

Needed for any layout or roster normalisation. The two cases are very different.

**Fleet member record — removable, with a caveat.** `Fleet:116/120/124` is a `std::vector` of
44-byte records, and the game's own deletion of a destroyed ship was an **erase, not a
reallocation**: after losing one of eight ships the fleet read size 7 with **capacity 9**, so
`begin` and `cap` were untouched and only `end` moved. That is reproducible from outside:

```
to erase record i of n:
  memmove(begin + i*44, begin + (i+1)*44, (n-1-i)*44)   # shift the tail down
  write end = end - 44                                   # shrink the size
  leave begin and cap alone                              # capacity is unchanged
```

**Caveat — it leaks.** Each record's inner crew vector at `record+20/24/28` points at a *separate*
heap allocation. Erasing the record drops the only pointer to it, and external code cannot call the
game's allocator to free it. The leak is harmless within a session but is not clean. The design
reference node at `record+4` may also be reference-counted; dropping it without decrementing is
unverified.

**Standalone `Ship` object — the registry has now been found.** See the section below; the earlier
blocker was that the container was unknown, and it is not any field of `Owner`, `Planet` or `Fleet`.
The heap allocation still cannot be freed from outside, so removal means unregistering and accepting
a leak.

**Recommended approach: let the game do the deleting.** The engine removes ships cleanly, including
the fleet-record erase and the sub-allocation, so the safe path is to *drive* it rather than
imitate it — write `Ship:136` condition to `0.0` (or the fleet record's `+32`) and let combat or turn
resolution reap the ship. **Untested**, but it uses only a write we know is safe on a field we know
the engine reads, and it delegates every structural change to the code that owns those structures.

For unwanted ships that must merely be got out of the way, coordinates are writable and render live
(see the layout section), so relocating is strictly safer than deleting.

[ ] test whether writing condition 0.0 makes the engine reap a ship cleanly on the next turn
[ ] test whether the engine frees a planet's citizen vector when a colony is captured or abandoned — if it does, a `VirtualAllocEx` buffer repointed into `Planet:144` would be freed by the game's allocator and crash; conquer one of our own colonies to find out

### Ship designs: five part-category vectors at a 24-byte stride (July 2026)

Two designs differing by **exactly one weapon** — both carrying one engine and the mandatory
scanner, everything else empty — isolated the structure. A design does not hold one list of parts;
it holds **one `std::vector` per part category**, 8 bytes per element, element `[0]` being the
part's own vftable:

| Offset | Category |
|---|---|
| `ShipDesign:128` | `ShipChassis` |
| `ShipDesign:152` | `ShipScanner` |
| `ShipDesign:176` | `ShipEngine` |
| `ShipDesign:200` | `ShipWeapon` |
| `ShipDesign:224` | `ShipModule` |

The stride is exactly 24 bytes with no gaps, so `ShipShield` is almost certainly `+248` —
unoccupied in every design observed so far.

**`ShipDesign:176` was mislabelled.** It was recorded as "Ship Parts", because a 1/2/3-engine test
gave spans of 8/16/24 and that looked like a parts list growing by one part. It is the **engine**
vector; the earlier test varied only engines, so a per-category vector and a whole-parts list fit the
data identically. The `s1`/`s2` pair separated them: adding a weapon left `+176` untouched at one
element.

**The weapon vector was outside the read window.** `ShipDesign`'s extent was the unmeasured default
of 192, and `+200` sits past it, so weapons were structurally invisible — not missing from the object,
just never read. Extent raised to 320, which also brought `ShipModule` at `+224` into view; a
`Colony Ship` carries 3 engines and 1 module.

**Corroboration:** `ShipScanner` at `+152` holds exactly one element on every design, matching the
UI's rule that the scanner is mandatory but changeable.

[ ] decode the individual part objects (`ShipEngine`, `ShipWeapon`, `ShipShield`, `ShipScanner`, `ShipModule`) — not needed for sync, which copies values between clients rather than interpreting them; the category vectors above are enough to replicate a design

### Writing population — the state-restore blocker is solved (July 2026)

Restoring a player's population is unavoidable for state sync, and population is a
`std::vector` of 16-byte citizen records at `Planet:144`, so it initially looked to need a heap
reallocation that external code cannot perform. It does not. Three cases, in increasing risk, all
now implemented in `set_population.py`:

| Case | Method | Risk |
|---|---|---|
| **Reduce** | shrink `end` | none — capacity retained, nothing leaked |
| **Grow within capacity** | write records into spare capacity, advance `end` | none |
| **Grow beyond capacity** | `VirtualAllocEx` a buffer, copy, repoint `begin`/`end`/`cap` | see below |

The third case is made safe by **sizing the new buffer to the planet's hard maximum population**
(`Planet:104` high half ÷ 10). Population cannot legally exceed that, so the engine never needs to
grow the vector, never reallocates, and therefore never calls its own `free()` on a pointer its
allocator did not hand out. The failure mode that normally makes `VirtualAllocEx` dangerous is
designed out rather than hoped away.

**Verified in game.** A homeworld was grown from 18 to 35 (its maximum) through the
`VirtualAllocEx` path, allocating 560 bytes in-process, and the UI showed 35. The count mirror in
the low half of `Planet:352` must be updated alongside the vector.

**The UI does not repaint on its own.** The new population only appeared after alt-tabbing away and
back. Memory is correct immediately; only the display lags. Same class of behaviour as the
coordinate writes, and worth knowing before concluding that a write "did not work".

**Cost:** the planet's original buffer leaks, one leak per beyond-capacity write. Harmless within a
session.

**Zero-risk alternative:** drive a planet to maximum population once with `advance_turns.py`.
Capacity only ever grows, so afterwards every legal population is writable in-capacity forever, with
no foreign allocation at all.

### Staging enemy assets, and a limit on coordinate writes (July 2026)

Testing a fleet scan needed a visible enemy target near our own space. Two attempts, and the
first failed in an instructive way.

**Coordinate writes do not stick on a ship with an active order.** Relocating an enemy ship from
781 units away to 26 units from our homeworld read back correctly, but by the next observation the
engine had put it back at d=781. That ship was under a Scout order, and turn resolution recomputes
position along the route. The earlier finding that ship coordinates are authoritative came from
relocating an **orderless** ship, and was generalised too far.

**Consequence for layout injection:** any relocation pass must clear `Ship:52`/`Ship:76` first, or
the engine silently undoes the move on the next turn.

**Ownership is writable, and the game fully accepts it.** `Ship:40` is a reference node to an
`Owner`; pointing it at the rival civ's owner-node — taken from one of *their* ships rather than
fabricated — re-owns the ship. A ship flipped this way rendered as an enemy vessel parked in our
home system and the UI offered enemy-only actions against it. Three writes, all reversible: clear
the order, set the coordinates, repoint `+40`.

That gives a way to stage arbitrary enemy assets without fabricating objects, which matters because
**a `Fleet` cannot be fabricated**: it would have to be registered in whatever container the engine
enumerates fleets from, and that container has never been located — the same gap that makes deleting
a standalone `Ship` unsafe.

**Scan targeting rules observed:** a fleet scan **cannot** run against a lone ship, only against an
actual fleet. A route scan **can** target a ship, but shows little unless that ship has a route.
The way to give an enemy ship a genuine route is to let the game create the order on one of our own
ships first and flip ownership afterwards — `Ship:40` and `Ship:48` are independent, so the route
survives the change of owner and no foreign allocation is involved.

### Diplomacy: war is a heap `Treaty` object (August 2026)

Declaring war from a peaceful start, diffed over a seconds-wide interval, produced exactly one
meaningful change: **a new heap `Treaty` object**. **No `Owner` field changed at all.**

| Offset | Content |
|---|---|
| `+4` | first party — reference node to the declaring civ's `Owner` |
| `+8` | second party — the other civ's `Owner` |
| `+12` | `1` |
| `+16` | **treaty type — `3` for a declaration of war** |
| `+20` | `-1`, plausibly "no expiry" |
| `+24`, `+28` | **`50`, the exact turn war was declared** |

Real data ends around `+32`; beyond that is unrelated UTF-16 UI text, so the object is roughly
40 bytes rather than the 192 default window.

**`Owner:1264 … 1300` is not diplomacy.** Those ten reference slots stayed null through the
declaration, and were null in a separate game that was already at war. The earlier "per-rival
diplomacy/treaty slots" reading is withdrawn.

**A `.data` `Treaty` also exists** (`0x00852C54`, seen in a different game) and is a static template
or dialog buffer. Check the address range before treating a `Treaty` instance as live — that
static's presence carries no diplomatic meaning, which cost one wrong conclusion earlier.

**Remaining diplomacy work**, all deferred — war was the piece that mattered for the prototype:

[ ] record the other `Treaty:16` type values by proposing each treaty kind — only `3` (war) is known, and the ids may not be contiguous
[ ] decode the propose-treaty flow: whether a proposal creates a `Treaty` immediately or only on acceptance, and what `Treaty:12` and `Treaty:20` mean once a treaty has a duration (RTTI has a `TreatyLength` class)
[ ] find where "send message" state lives — RTTI has `SendMessageDlg` and `MessageListener`, so there is likely a message list per civ
[ ] find the per-civ colour field behind "change color" — likely a plain value on `Owner`, and a cheap write test would confirm it
[ ] locate the `Fleet` registry so a fabricated fleet can be registered — make a fleet, scan memory for pointers to it, then find the `.data` vector holding the array, exactly as the `Treaty` registry was found

### `Owner` is a multiple-inheritance class — the civ name is in front of the tag (August 2026)

**`Owner`'s allocation starts 52 bytes before its EJBO tag.** Every read window in this project
began at `tag − 8`, so `Owner`'s first 52 bytes — including the **civilisation name** — were
invisible for the entire investigation.

| Offset | Content |
|---|---|
| `−52` | **primary vftable** `0x007707D0`, RTTI `Owner` — the allocation starts here |
| `−48` | pointer to the shared galaxy welcome text (identical on both civs) |
| `−44` | **civ name**, `std::string` SSO buffer — `'GoodGuy'` / `'BadGuy'` |
| `−28` / `−24` | name `_Mysize` / `_Myres` (15, the SSO buffer size) |
| `−20` … `−12` | unidentified; `−12` is pointer-shaped on the human civ only |
| `−8` | **second vftable** `0x007707E0`, RTTI also `Owner` |
| `−4` | object id |
| `0` | `'EJBO'` |

**Two different `Owner*` values point at the same object**, and both occur in practice:

- the **reference-node idiom** stores `tag − 8` (the secondary base) — this is what
  `Planet:40`, `Ship:40`, `Fleet:40` and the local-player global all resolve through;
- the **known-players vector** stores `tag − 52` (the primary base).

Any code resolving an `Owner*` must accept **either**, or it silently sees half the graph. This
is not a curiosity: a full-memory sweep for `tag − 8` found **2** references to a civ, while the
same sweep for `tag − 52` found **10**. The first sweep is what produced the earlier wrong
conclusion that "nothing outside the `Treaty` references the rival civ".

`ShipDesign` was already known to have a second vftable at `−12`, so this is the second
multiple-inheritance class found and the pattern should now be *assumed* rather than discovered:
**before trusting any class's field list, check whether a vftable sits further back than `−8`.**
`measure_extents` already probes `tag − 12`; it does not probe deeper, so a class whose primary
base is further back than that will still be truncated at the front.

### Known players — a `.data` discovery vector (August 2026)

The diplomacy page's "known players" box starts empty and fills when a rival is found. A static
scan of an already-at-war game found nothing, because it searched for the wrong `Owner*` form
(above). Forcing the transition settled it.

**Method.** A fresh game, both civs mutually unaware, `Planet#142` "GoodGuy's HQ" at
(345, 256, 102) and `Planet#320` "BadGuy's HQ" at (720, 124, 58). Rather than fly there over
many turns, the human's **orderless** `Ship#637` was teleported to 25 units off the rival HQ by
writing `Ship:4/8/12` — coordinates are authoritative on ships with no order. One turn was
advanced *before* the teleport as a **control**, and one after, so ordinary turn churn could be
subtracted. The control turn changed 33 `.data` words and created no objects; the discovery turn
added exactly one structure that the control did not:

```
.data 0x0082AA28  [begin, end, cap]   0,0,0  ->  one 8-byte record
    record[0]  civ = Owner#638 (tag-52)   where = Planet#320
```

So the record is **`{Owner* civ, Planet* whereFirstSeen}`** — who you met and where you met them.
`cap == end`, so the vector was grown to hold exactly one entry.

Two candidates from the same diff were **discarded**: `0x0082DED4/D8` points at a `Font` record
holding `"Arial 12"` — a font cached because a new UI element rendered for the first time. It
correlates perfectly with discovery and means nothing.

The UI was confirmed to list the rival once the record existed. **The write test was started but
not completed**: the vector was emptied and restored, but no reading of the box was taken while it
was empty, so this remains a correlation. It is a strong one — a `.data` vector going from empty to
one record on exactly the discovery turn, containing exactly the discovered civ — but `Owner:68`
and `Owner:200` both cleared a comparable bar and were later falsified, so it is recorded as
unconfirmed.

[ ] finish the write test: empty `0x0082AA28`, read the box (alt-tab to force a repaint), restore
[ ] check whether the box is *derived* from this vector or from the owners of known planets — a
galaxy-wide "known planets" list may be the real primary structure

#### Plan for loading known players: the ship shuffle

There is **no slack to grow the list in place.** The array's heap block is exactly 8 bytes, with
the next block's header immediately after it and `cap == end`:

```
0x0A551900  heap block header
0x0A551908  [Owner#638, Planet#320]   <- 8 bytes, the whole allocation
0x0A551910  next block's header
```

So writing a second record would corrupt the neighbouring block. That splits the problem:

- **Rewriting existing slots is safe** — the array is engine-owned and correctly sized, and
  changing which `Owner*`/`Planet*` occupies a slot involves no allocation.
- **Adding slots needs a foreign allocation** — `VirtualAllocEx` plus repointing `begin/end/cap`,
  which hands the engine a pointer its allocator does not own and will eventually `free()`. Same
  unresolved risk as the citizen-vector buffer.

**The chosen approach is therefore the ship shuffle, used as a one-time capacity bootstrap rather
than as a fallback.** On loading a game, teleport an orderless ship to each rival's home planet so
the engine itself discovers every player and allocates the array at full size; from then on the
contents are rewritten by memory. This keeps the array engine-owned and never repoints the vector.
Teleporting makes each contact immediate instead of a multi-turn flight (write `Ship:4/8/12` on a
ship with no order, then advance one turn).

One property still unmeasured, because this galaxy has only two players: **whether the engine grows
this vector by exact reallocation or in doubling steps.** The 8-byte block with `cap == end`
suggests exact. If so, the bootstrap has to run to the full player count in one pass, since any
later natural discovery would reallocate and discard whatever we had written.

[ ] **CRITICAL — create a third player.** Everything above is untestable beyond two civs, and any
galaxy with more than two players needs civ fabrication anyway: a new `Owner` (multiple-inheritance
layout, allocation starting 52 bytes before the tag, name string at `−44`), plus registration in
the `Owner` registry, which has not been located yet. This gates multiplayer above 1v1 and it gates
measuring the growth behaviour above.

### The News tab — deferred in full (August 2026)

Not investigated. Deferred deliberately: news is a *record of* events the prototype already syncs
through other structures, so it is presentation rather than authoritative state, and nothing in the
minimum playable loop depends on it.

[ ] **investigate the News tab as a unit.** Expected shape, by analogy with what is already
mapped: a per-civ `std::vector` of event records, most likely on `Owner` (compare `Owner:320`, the
scan-report vector) or in a `.data` registry (compare the `Ship` and `Treaty` registries).
Attackable by the method that worked for scan reports and for known players — take a snapshot, cause
exactly one newsworthy event over a short interval, and diff, using a control turn to subtract
ordinary churn. Wide-interval diffs produced two false candidates for scanning and must be avoided.
Worth checking whether entries are localised strings or event ids with parameters, since that
decides whether news can be synced at all or has to be regenerated per client.

RTTI was checked first, and the result narrows the search before any memory is touched. The only
matching class is **`NewsPage`** — a UI page, with siblings `BattleReportDlg`, `ShareBattleReports`,
`PlanetaryScanReport`, `SendMessageDlg`, `MessageListener`. **There is no news *data* class at all.**
Two readings fit: either news items are plain strings or POD structs with no vftable (hence no RTTI
entry, and no EJBO tag either), or `NewsPage` composes its text at render time from objects that are
already mapped. The second would mean there is nothing to sync — which is the outcome to test
first, since it is cheap and would close the tab outright.

There is also a **`Log`** class, and the game's log buffer is already known to this project: it is
what overwrote `Admiral`'s tail past `+88` and forced that class's extent down from a stride of 288.
If news is backed by that buffer, the same allocation is already partly characterised.

### The Overview tab (August 2026)

Two buttons plus the civilisation traits.

#### The civ-trait block — `Owner:744 … 940`, mapped in one pass

The ~50 game-start adjustable traits are a **contiguous block of 4-byte ints on `Owner`**, each a
plain percentage with `0` meaning unmodified, and all zero on the AI civ.

Mapping them one at a time would have taken fifty readings. Instead **one distinct probe value was
written to every offset in the block** (11, 12, 13 … 60), the on-screen trait list was read once,
and each trait named its own offset by the number it displayed: `offset = 744 + (value − 11) × 4`.
Originals were saved to disk first and restored afterwards, verified byte-for-byte.

The UI order is **not** the memory order — "planetary defence firepower" and "stationed military
training" appear near the bottom of the list but sit at `Owner:780`/`784` — so a positional guess
would have mismapped most of the block. Distinct values were what made a single pass work.

Two independent checks confirm the method: ship speed displayed 18 → `Owner:772`, and firepower 19
→ `Owner:776`, both exactly where the pre-write values (5 and 10, matching the UI's +5% and +10%)
already placed them. Full offset list is in the annotations.

**Doctrine and technology modifiers are not stored in the block.** The clearest case: light ship
speed held probe `30` and displayed `50`, which is `30 +` the researched doctrine's `+20`. So the
stored value is the base trait only, and the UI adds research effects at display time from the
`Owner:108` completed list. **The server must sync the base trait plus the research list, never the
displayed number** — syncing the displayed value would double-count every modifier.

**A golden age adds a flat +50 to the four output bonuses.** Discovered by accident: food,
production, science and mining displayed `probe + 50` while every other trait displayed its probe
exactly. `Owner:744` took probe `11` and the engine decremented it to `10` on the next turn, with
the UI reading "golden age turns = 10" at that moment — so `Owner:744` is the golden-age countdown.
Its idle value is `9999` rather than `0` while the UI shows 0 turns, so 9999 is probably a "no
golden age" sentinel; that part is inferred, not tested.

**`Owner:932/936/940` are the only offsets the engine overwrote** — it accepted probes `58/59/60`
and reset all three to `0` on the next turn, while everything from 748 to 928 held. `932` is the
leading candidate for **dark age turns**, cleared because a golden age was active, but that is a
hypothesis built on one observation.

Four items remain open:

[ ] resolve `Owner:788` (ship shield strength): probe `22` displayed as **−12**, but the doctrine's
−10 shield penalty predicts **+12**. Either the sign was transcribed wrong or the penalty is not a
flat −10 — and since every other doctrine-affected trait fits the additive model exactly, this one
disagreement is worth settling before the model is trusted
[ ] separate `Owner:792`: the screen showed `23` for **both** "income per citizen" and "ship units
bonus", and only this offset holds 23, so one of the two is derived or lives outside the block
[ ] identify `Owner:748` and `Owner:752`, which held probes `12`/`13` that no trait displayed.
"Food consumption per citizen" is the candidate: it read `10` before the pass and an unexplained
`232` during it
[ ] confirm which of `Owner:920/924/928` is **anonymous scanning** — the UI renders it as a
checkbox rather than a number and read "enabled" while all three were non-zero

#### Cash and the resource market

`Owner:8` is **current cash** — CONFIRMED against the UI showing `$2026` at the instant memory read
2026, with the AI at 1766 simultaneously. Earlier readings had both civs equal, which made "per-civ"
look unproven; a symmetric start was the reason.

**Buy price is derived, not stored: it is exactly 8× the sell price** across all five resources
(99→792, 149→1192, 199→1592, 300→2400, 900→7200). Only the sell price needs syncing.

**Resource stocks are `Owner:1128`** — a `std::vector` of five 8-byte `[resourceId, amount]` records,
ids `0` metal, `1` deuterium, `2` radioactives, `3` crystal, `4` exotics. Confirmed against the UI
and again by metal tracking a `530 → 583` change at the same address.

This **resolves the "5-entry keyed table"** that had been an unexplained `Owner` field since the
diplomacy work; the earlier `(0,630) (1,350) (2,260) (3,0) (4,0)` reading was simply that civ's
stocks. It is also the only vector observed whose **capacity exceeds its end** (48 vs 40) — research
and known-players both grow to an exact fit — so a sixth resource could be appended in place.

**Prices are computed, not stored.** A sweep of 369 MB, *including 285 MB of read-only data that
earlier sweeps never covered*, found no contiguous `[100,150,200,300,900]` at any stride from 4 to
24; a layout-agnostic search found **zero** 512-byte windows containing all five values in any order.
They are compiled constants.

**`Owner:916` drives both prices** — CONFIRMED by a single-variable write test with a pre-registered
prediction. Writing 25, with nothing else changed, produced sell prices `125/188/250/375/1125`,
matching all five predicted values exactly including `187.5 → 188`:

```
sell = base × (1 + Owner:916 / 100)
buy  = base × 8 × (1 − Owner:916 / 100)
base = [100, 150, 200, 300, 900]      in Owner:1128 resource-id order
```

Positive is good in both directions: it raises sell revenue and lowers purchase cost (metal buy
`800 → 600` as sell went `100 → 125`). That also explains why buy is exactly 8× sell only when the
modifier is 0.

**So the syncable market state is just `Owner:916` plus the `Owner:1128` stocks** — no price table.

> **Method note.** The `×1.54` reading that first suggested this relationship was taken while the
> *entire trait block was scrambled* by the probe pass — fifty variables at once, and the buy/sell
> ratio was distorted to 2.39× as well. It was a correct hunch from unusable evidence, and it was
> only worth acting on once re-run as a clean one-variable test. The same instinct with no follow-up
> test is what produced `Owner:68` and `Owner:200`.

[ ] explain the residual price drift: an early clean reading gave `99/149/199/300/900`, one below
base on the three resources with non-zero stock and exactly base on the two with zero stock, while a
later clean reading gave base exactly. Small, and irrelevant to sync if the server writes `Owner:916`
and the stocks directly, but it means prices are not a pure function of the modifier
[ ] confirm the **buy**-price formula with a second value of `Owner:916` — it currently rests on one
data point, unlike the sell formula which is confirmed on five
[ ] determine whether trading, turn resolution, or both move the market, and whether AI trading
affects the human's prices

> **Scanner bug worth remembering:** the first price sweep "found" the sequence at a dozen
> consecutive addresses, which is arithmetically impossible for an increasing sequence. The cause
> was **NaN**: every comparison against NaN is false, so `abs(v - want) > tol` silently passes and a
> block of NaN bytes matches any float sequence. Float scans must reject NaN and infinities
> explicitly.

[ ] decode the **"vote to end galaxy"** button. Deferred: it is a galaxy-lifecycle action rather
than per-turn state, so nothing in the playable loop needs it. Expect a per-civ vote flag plus a
tally, and note that the original game resolved this server-side, so the client may hold only the
local vote and rely on a server response — which would make it unsyncable without the real server
protocol and would close the item.

### Audit sweep — closed items, and the Owner registry (August 2026)

A pass over every annotated class, looking for unannotated fields adjacent to known
structures and for annotations still carrying open questions.

**Three things were closed rather than tracked.**

*No class other than `Owner` and `ShipDesign` hides front matter.* Every class was tested for a
same-class vftable further back than `−8`; only `Owner` (−52) and `ShipDesign` (−12) have one.
`Planet`, `Ship` and `Sun` are single-inheritance, so their varying negative offsets are heap block
headers and the preceding object's tail, not missed fields. Worth stating explicitly, because the
civ name had just been found hiding 52 bytes in front of the tag and the same could plausibly have
been true elsewhere. It is not. Recorded on each class's `−8` annotation so it is not reopened.

*`Ship:20/24/28/32/36` are screen projection* — present only while the ship is on screen, changing
with camera angle and zoom. Render state, recomputed per frame, never syncable. Closed.

*The ~27 `Planet` "always zero" offsets are one finding, not 27 questions* — collapsed to a single
shared annotation and one TODO below. The caveat matters: every save observed so far has been a
young galaxy, so "always zero" is weak evidence for "unused".

**`Owner:-16` is the key of an Owner registry.** Each civ's `−16` value is mirrored in a two-word
node `[key, Owner*]`, and those nodes belong to an **MSVC `std::map` whose head is `0x02D48740`**.
Walking it enumerates exactly the live civs, each node's key matching that civ's `−16` exactly:

```
head 0x02D48740   [_Left = leftmost, _Parent = root, _Right = rightmost]
  node 0x0A525A80   key 0x2DB018D6 -> Owner#634 (GoodGuy)
  node 0x0A5257A0   key 0x6C157D63 -> Owner#638 (BadGuy)
```

Same idiom as the `Planet:204` facility map. **This is the registry the "create a third player" item
was blocked on** — though inserting into a red-black tree by hand is materially harder than
appending to the `std::vector` registries used for `Ship` and `Treaty`, and the key looks like a
hash whose derivation is unknown, so a fabricated civ needs a key that will not collide.

**`Owner:-12` is a one-byte bool, and this one nearly became a wrong annotation.** It reads
`0x02E44501` on the human civ, and `0x02E44500` genuinely *is* the address of a live `SolarSystem`
object — a very convincing "tagged pointer to the home system". It is wrong: the AI civ reads
`0x00000001`, a null pointer, and the AI certainly has a home system. MSVC writes a `bool` as one
byte and leaves the adjacent three untouched, so the human's `45 E4 02` is residue from the previous
occupant of that allocation. **Read only the low byte.** The general lesson: a plausible pointer in
a struct can be three bytes of stale allocator residue behind a one-byte field, and the check that
catches it is comparing the same offset across two instances.

[ ] re-check the ~27 dormant `Planet` offsets in a developed save before treating them as unused
[ ] identify `Owner:924` and `Owner:928` alongside the existing `Owner:920` item — one of the three
is "anonymous scanning", which the UI renders as a checkbox rather than a number
[ ] understand why the engine **zeroes** `Owner:932/936/940` on the next turn while accepting writes
to every trait from 748 to 928; `932` is the dark-age-turns candidate, and a field the engine
actively rejects is worth understanding before trying to load state into it
[ ] identify `Ship:56` and `Fleet:56` — the same slot on both classes, holding the static null node
on every instance ever observed including ships under orders and in fleets, so nothing has been seen
to fill it
[ ] identify `Owner:356`, a packed uint16 pair that moved `0x00000001 -> 0x00010001` when a
technology completed
[ ] identify `Owner:1208/1212/1216`, a float triple in X/Z/Y layout present only on the AI civ and
zero on the human — plausibly an AI strategy target, in which case it is irrelevant to sync
[ ] identify `Admiral:48` (1 on all admirals) and `Admiral:56` (1 on the admiral with a ship
assigned, 0 on the others, tracking `Admiral:8`)
[ ] decide whether `Sun` needs annotating at all — only 8 fields are known and `Sun:44/80/84/88`
vary across all 108 suns, but the galaxy is client-generated from a seed, so suns may need no
syncing whatsoever. Settle the question before spending effort on the fields
[ ] settle `Planet:96`: 499 distinct values across 525 planets looks like real per-planet state, but
both homeworlds share the identical pointer-shaped value `0x01278BE0`, which fits the appearance
block already documented around `Planet:20-32`. Probably cosmetic; cheap to confirm

### Ship designs: the full registry table, and two falsifications (August 2026)

Tested against a game with four designs of known composition — `Colony Ship` (shuttle, 3 nuclear
drives, colony module), `bomber` (shuttle, 1 drive, fusion bomb), `mass` (shuttle, fusion drive,
magneto shield, mass driver), `corv` (**corvette**, 1 drive, large pilot cabin). One non-shuttle and
one 3-engine design were enough to separate several fields at once.

**Part records are `[vftable, subtypeId]` pairs, and the ids are now decoded.** Each of the five
category vectors holds 8-byte records where the vftable is identical across every part in that
category — so the vftable names the category and the second word identifies the part. RTTI resolves
all five:

| Offset | RTTI class | Subtype ids confirmed |
|---|---|---|
| `+128` | `ShipChassis` | `0` shuttle, `1` corvette |
| `+152` | `ShipScanner` | `0` neutron scanner (on all five designs) |
| `+176` | `ShipEngine` | `0` nuclear drive, `1` fusion drive |
| `+200` | `ShipWeapon` | `0` mass driver, `10` fusion bomb — **not contiguous** |
| `+224` | `ShipModule` | `0` colony module, `1` troop bay, `2` large pilot cabin |

Element count is the part count: the Colony Ship holds three id-`0` engine records for its 3 nuclear
drives. Absence of a category is an **empty vector**, not a sentinel. This largely closes the
deferred "decode individual part objects" item — enough to copy a design between clients.

**`ShipDesign:80` is NOT chassis size — that reading was wrong and is withdrawn.** It reads `7` on
the corvette `corv` *and* on the **shuttle** `troop`, and `2` on the other three. I had concluded
"chassis" from two designs that both read 7 and assumed they shared a hull; the user corrected that
`troop` is a shuttle. What those two actually share is a **personnel module** — large pilot cabin
(id 2) and troop bay (id 1) — against a base of `2` with no module or with a colony module. Leading
reading is crew/unit capacity, which fits the "ship units bonus" trait at `Owner:792`. Untested.

**The real chassis id is the second word of the `ShipDesign:128` vector** — `0` on four shuttles, `1`
on the single corvette.

**`ShipDesign:76` is a payload-delivery flag**, with three readings falsified along the way:

| design | module fitted (id) | `:76` | `:80` |
|---|---|---|---|
| Colony Ship | colony module (0) | **2** | 2 |
| troop | troop bay (1) | **2** | **7** |
| corv | large pilot cabin (2) | 1 | **7** |
| bomber | — (fusion bomb) | 1 | 2 |
| mass | — (mass driver, shield) | 1 | 2 |

Not chassis, not colonisation-capable (the troop design reads 2 and cannot colonise), not "carries a
module" (the pilot-cabin design has one and reads 1). Module ids `0` and `1` both **deliver a payload
to a planet**, enabling colonise and conquer. Note `:76` and `:80` key off the *same* module id on
two different axes — payload versus personnel — which is why any single-design comparison was always
going to conflate them.

> **Two wrong conclusions came from the same mistake here:** treating "these two designs agree" as
> "these two designs share the property I have in mind". Five designs varying one thing each is what
> separated chassis, engine count, payload and crew; two designs agreeing on a number proved nothing.
> The `:80` error was caught only because the user knew the hull sizes and contradicted the claim.

**`Owner:440/444/448` is not the ship-design list — falsified.** It reads **empty on both civs** with
four user designs present. The earlier "3 elements on the civ that owned 3 designs" was coincidence.

**There is no `ShipDesign` registry.** A sweep of `.data` for vectors containing *only* EJBO object
pointers — accepting both `tag-8` and multiple-inheritance base forms — found none for `ShipDesign`,
so designs are not registered the way other classes are. For sync they can simply be enumerated by
scanning for the EJBO tag, which is how the viewer finds them anyway.

That sweep did complete the registry table, adding two that were unknown:

| Address | Class | Entries |
|---|---|---|
| `0x00854628` | `Ship` | one per ship |
| `0x008553C4` | **`Planet`** | 536, every planet |
| `0x00856384` | **`Sun`** | 108, every sun |
| `0x0086ED78` | `Treaty` | one per treaty |
| `0x02D48740` | `Owner` | a `std::map`, not a vector — see the audit section |

> **A phantom was rejected in the same pass.** A 33-entry "`Planet` registry" appeared at
> `0x00856388`, four bytes after the `Sun` registry. It is the Sun registry's **`end` field**: a triple
> read there is `(end, cap, next)`, its "capacity" is `0x3F333333` — the float `0.7` — and the 33
> planets are just the array that follows the Sun array in the heap. This is the third time
> overlapping `[begin, end, cap]` triples have manufactured a fake container, after `Owner:1132` and
> the first known-players attempt. **Any vector-shaped scan must reject triples that overlap a
> known vector's fields.**

**`Owner:-48` is a `ShipDesign*`, and the "galaxy welcome text" annotation is withdrawn.** Both civs
hold the same value, resolving to `ShipDesign#650 tag-12` with a valid `ShipDesign` vftable — the
Colony Ship *base* template rather than the computed one. The earlier label came from seeing ASCII
near the pointer target in a different game without resolving RTTI, which is the same failure mode as
the `Owner:-12` "SolarSystem pointer". In that earlier game the target's first word was `0x636C6500`,
not a vftable, so the field does not hold a `ShipDesign` in every game and the reading is not yet
complete.

[ ] settle `Owner:-48`: confirmed a `ShipDesign*` (the Colony Ship base template) in one game but
pointing at text-like memory in another. Leading reading is the civ's default/starting design
[ ] pin down `ShipDesign:76` and `ShipDesign:80` together: build a design mixing a **troop bay with
weapons** to see whether `:76` stays 2, and a design with **two personnel modules** to see whether
`:80` scales past 7 (which would confirm it as a capacity rather than a flag)
[ ] extend the part-id tables by fitting each remaining part type once — the weapon ids are already
known to be non-contiguous (`0` then `10`), so the ranges cannot be inferred and must be observed

### Homeworld customisation — four click counts in `.data` (August 2026)

The start-of-game popup offers four adjustable options for the homeworld — space, food, production
and science — with 30 increments to distribute. **What is stored is the click counts, not the
results.**

```
0x00842AE4   space       clicks
0x00842AE8   food        clicks
0x00842AEC   production  clicks
0x00842AF0   science     clicks
```

CONFIRMED across three snapshots: all four read `0` before any click; after a first round of
`space 2, food 3, production 5, science 7` the record read exactly that; after loading the remaining
13 increments into space it read `15, 3, 5, 7`, totalling the 30 available. The **space slot tracking
`0 → 2 → 15` across two separate rounds of clicking** is what identifies it, rather than a single
end-state match.

**Everything the UI shows is derived from those counts plus a base table.** The defaults live at
`0x02CA8410` as `[300, 32, 30, 40]` — space, food, production, science — and the UI computes:

```
space              = 300 + 50 + 5 x spaceClicks      = 300 + 50 + 75  = 425
food per farmer    = 32 + foodClicks                 = 32 + 3         = 35
production/worker  = 30 + productionClicks           = 30 + 5         = 35
science/scientist  = 40 + scienceClicks              = 40 + 7         = 47
```

The flat **+50 on space** applies on top of the clicks. Only the space result is written to an object
(`Planet:104` high 16 bits); the three per-unit outputs appear **nowhere in memory** as ints or
floats, in any grouping or stride. They join ETA, military rank and maximum population as
derived-not-stored values.

**So the syncable state is the four click counts**, and a client given those reproduces every visible
number itself.

Two things this cost, both worth recording as method:

*The popup writes nothing while open.* All 17 first-round increments left `Planet` untouched — space
stayed at 300. The staged values were duplicated across UI widgets: **1385 words** moved by one of the
four expected deltas, in 610 clusters, and the only windows containing one of each had irregular
strides. Nothing there was a record. Had space not been a known field acting as a control, that diff
would have looked like a promising lead.

*The confirmation diff was clean per-object and filthy globally.* Only 9 offsets changed on the
customised planet and **zero** on the rival's, but memory-wide the same step changed **425,677**
small-valued words. A whole-memory sweep was therefore useless while the two-planet comparison was
decisive. Picking the right comparison mattered more than the size of the sweep.

*Three `.data` sequences read `[3, 5, 7]`* and only one of them was the record; the other three were
static constants that never changed across any snapshot. The value pattern alone would have picked
the wrong address.

#### Multiplayer consequence — a real gap, not a hypothetical

**Every player customises their own civ traits and their own homeworld.** The two systems store
their results very differently, and only one of them survives being replicated to other clients.

*Civ traits are safe.* `Owner:744…940` is per-`Owner` state — non-zero on the human civ and zero on
the AI in the same game — so replicating the `Owner` carries a civ's traits and every client agrees.

*Homeworld space is safe.* It is baked into `Planet:104`, per planet.

*Food, production and science are not.* They are recomputed every turn from `base table + click
counts`, and the click counts appear to be **one global `.data` record**, not one per civ. This
matters because **turn resolution runs client-side and each client simulates every civ's economy,
not just its own** — directly evidenced by the food work, where a single turn on the human's client
produced HQ +153, new colony +99, **and rival HQ +80**. If two players each customise, every client
would compute the *other's* homeworld output from its own local record, and the simulations would
drift a little every turn, silently, with nothing in the UI showing the discrepancy.

**Cheap prototype fix:** require all players to leave the homeworld food/production/science at
default. Zero clicks means every client computes `32 / 30 / 40` for every homeworld and they agree by
construction. The constraint applies only to those three values — space can vary freely per player
because it is genuinely stored per planet.

**Best lead for solving it properly:** the per-unit outputs may be stored per *citizen* rather than
per planet. Population is a list of 16-byte citizen records at `Planet:144`, and the search for the
output values turned up repeating `[35, 47]` pairs at a 40-byte stride in the heap. Both were read at
turn 0 with no population, so a planet with citizens is the state in which to look.

[ ] check whether the per-unit outputs appear in the `Planet:144` citizen records once a planet has
population — if they do, they are per-planet after all and the multiplayer gap closes
[ ] confirm whether the click record is per-civ or global: customise, then look for a sibling
four-word slot holding the AI's zeros. An attempt at this was inconclusive because the game was
restarted mid-check
[ ] confirm the `+50` space constant and the `[300, 32, 30, 40]` base table are galaxy-wide rather
than per-galaxy-type — both were read in a single game, and galaxy type is known to vary other
parameters. The user's working assumption is that these defaults are constant for every galaxy
[ ] **suppress both customisation popups for a returning player.** The civ-trait and homeworld
popups fire at game start, and a player rejoining a galaxy already has their traits and modifiers in
the loaded state. This is not merely cosmetic: confirming the homeworld popup is what *writes* the
click record and applies the `+50` space commit, so a returning player who is shown the popup and
confirms it with zero clicks would silently reset their homeworld to base values and overwrite
whatever the server restored. Options, in rough order of preference: (a) write the loaded traits and
click counts into memory *before* the popup would appear and auto-confirm it, so the engine's own
commit path produces a consistent result; (b) patch the client to skip the popup entirely, which
risks leaving the commit-time side effects unapplied; (c) let it appear and re-write the state
afterwards, which is the least safe because the commit also touched 425,677 words of galaxy setup.
Whichever is chosen, the restore must run *after* the commit, not before it

### Registries are per class, not global (August 2026)

`0x00854628` was recorded as "the object registry". It is not — it holds **only `Ship`s**. The war
`Treaty` was referenced by nothing in any EJBO object, and scanning all writable memory for pointers
to it found its own separate `.data` vector:

| `.data` header | Registry | Observed contents |
|---|---|---|
| `0x00854628` | `Ship` | 5 entries, all ships |
| `0x0086ED78` | `Treaty` | 1 entry, the war treaty |

So the engine keeps **one `std::vector` registry per class**, each with a fixed `.data` header of
`[begin, end, cap]`. That reframes object fabrication: registering a made-up `Fleet` needs the
**`Fleet` registry**, not the ship one, and each class's registry must be located separately by the
same technique — create one instance, scan memory for pointers to it, then find the `.data` vector
pointing at the array that holds it.

### The object registry — `0x00854628` (July 2026)

The blocker behind both "cannot safely delete a ship" and "cannot fabricate a fleet" was not knowing
where the engine registers objects. It is a **`std::vector` of object pointers whose header lives at
a fixed `.data` address**:

```
0x00854628   begin   -> the pointer array
0x0085462C   end     -> one past the last entry
0x00854630   cap
```

Observed with 9 live ships: `begin = 0x06515DA8`, `end = cap = 0x06515DCC`, i.e. size 9, capacity 9,
and the array held all nine ship **object starts** (`EJBO − 8`) in order.

**How it was found.** Earlier attempts only checked EJBO *fields* on `Owner`, `Planet` and `Fleet`
and concluded no container existed. Scanning **all 596 MB of writable memory** for pointers to known
ship objects instead produced a run of 9 references at stride 4 — a flat array — and a single
pointer to that array's start, in `.data`. The lesson is that the earlier negative result was a
consequence of searching only inside EJBO objects.

**What this enables.**

- **Unregistering** an object is a vector erase in the same shape as the fleet-record erase:
  shift the tail down 4 bytes and decrement `end`. The object's own allocation still leaks, since
  external code cannot call the engine's `operator delete`.
- **Registering** a fabricated object means appending its `EJBO − 8` address and advancing `end`.
  Capacity is currently equal to size, so an append needs the array relocated first — allocate a
  larger array with `VirtualAllocEx`, copy, add the new entry, then repoint `begin`/`end`/`cap`.
  The same reasoning that makes the population buffer safe applies: size the new array generously so
  the engine never needs to grow it and therefore never frees a pointer it did not allocate.

**Still to verify before relying on it:** whether this vector is the *only* registry (a fabricated
object may also need to appear in per-owner or per-system structures), and whether the engine walks
it for rendering as well as logic. Fabricating a `Fleet` also needs a correct 44-byte member-record
array and a valid vftable, so the object itself is more work than the registration.

[ ] verify the `0x00854628` registry is sufficient to make a fabricated object live — register a copy of an existing Ship and see whether the game renders and ticks it

### The Scanning tab — reports located (August 2026)

Staging a scannable enemy fleet (see the ownership-flip section) made the whole tab readable.

**`Owner:320/324/328` is the scan-reports vector** — 4-byte pointers, one per completed scan. It grew
from 1 to 2 elements the moment a fleet scan finished, in a seconds-wide diff.

Each element points at a report object with a consistent layout:

| Offset | Content |
|---|---|
| `+0` | vftable identifying the scan class — **`RouteScan` `0x007765B4`**, **`FleetScan` `0x0077613C`** |
| `+4` | scan type id, matching the `Owner:200` descriptors: **2 = route, 3 = fleet** |
| `+12` | **the turn the scan was taken** — read 488 and 508 with the game at turn 508 |
| `+16/+20/+24` | the target's coordinates — the fleet scan's matched `Fleet #673` exactly |
| `+28` | reference node to the **scanned civ's** `Owner` |
| `+44/+48/+52` | results vector; its **first element points at the scanned object** (`Ship #669` for the route scan, `Fleet #673` for the fleet scan) |

**Two type-descriptor vectors on `Owner`, not inventories.** `Owner:200` holds 8-byte
`[vftable, typeId]` `Scan` descriptors and `Owner:172` holds the same shape for `Facility` — ids 0, 2,
4, 6 matching the facilities the civ can build. Both are empty on a civ that has unlocked nothing.
These describe what a civ **can** do, as distinct from what it **has** (the per-planet facility map at
`Planet:204`).

**Two falsifications, both caught by write tests.**

- **`Owner:68` is not the available-scan count.** Writing 8 changed nothing in the UI. It does
  increment `0 → 1 → 2` as scans are produced but did **not** decrement when one was used, so the best
  remaining reading is a lifetime total. The earlier `CONFIRMED` label rested on two correlated
  increments and no causal test.
- **`Owner:200` is not the inventory either.** Setting both descriptor entries to the same type id did
  not change what the scans tab offered.

The lesson is specific and cheap: **a write test costs one command and settles direction of
causation**, which two consistent correlations do not. Both fields had passed the correlation bar.

The scan *inventory* — how many of each type are held — remains unlocated.

[ ] locate the scan inventory: the count of each held scan type. `Owner:68` and the `Owner:200` descriptor vector are both excluded by write test; take a short-interval snapshot across producing or consuming a single scan
[ ] run each remaining scan type and record its type id and report-object class — only route (2, `RouteScan`) and fleet (3, `FleetScan`) are known, and the ids are not contiguous so others may sit outside 2–3
[ ] check whether each scan type's results vector at report`+44` has a different element layout — route and fleet both begin with a pointer to the scanned object, but the remainder differs in length (44 vs 52 bytes) and has not been decoded
[ ] Recon tab — believed to have no backing state in memory; confirm by diffing across opening and using it, and record the negative result either way

### The reference-node idiom

Ownership is one instance of a general pattern. An object-to-object reference is stored
as a pointer to a small **node**, whose first dword is the target's allocation start
(EJBO tag − 8); a shared **static null node at `0x00857C54`** (first dword `0`) means
"no target". Confirmed instances:

| Field | Target | Null case |
|---|---|---|
| `Planet:40` | `Owner` | 157 of 160 planets |
| `Ship:40` | `Owner` | — all 3 ships owned |
| `Ship:44` | `Planet` in orbit of | ships under orders |
| `Ship:80` | `Admiral` | 2 of 3 ships unassigned |
| `Ship:108` | `ShipDesign` | — always set |
| `Planet:496` | `Governor` | unassigned planets |
| `Ship:56` | unknown | every ship, every state |

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
  — the civilisation name is not among them. It is at **`Owner:-44`**, *in front* of the
  tag; see the multiple-inheritance section below.

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
| `0x00857904` | ptr | **Local player** — reference node whose `node[0]` is the human civ's `Owner` at `tag − 8`. Confirmed across two independent games (`Owner#641`, then `Owner#634` = `'GoodGuy'`). This answers "which `Owner` am I" without needing a war to read `Treaty:4` |
| `0x0082AA28` | vector | **Known players** — `[begin, end, cap]`; one 8-byte `{Owner*, Planet*}` record per discovered rival, empty until first contact. Write test still pending |
| `0x00871430+` | mixed | Global serializer buffer (app context, file paths, UI config) |

**`0x00844B68` is *not* the local player**, though it held the same reference node in the first
game observed. In a second game it points at the static null node while `0x00857904` correctly
tracks the human civ. A single game would have produced two "confirmed" globals, one of them
wrong — the same trap as the `.data` `Treaty` static.

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

### Doctrines share the technology research slot (August 2026)

The research UI has two selectable trees, technologies and **doctrines**. They are **not separate
state.** Selecting a doctrine while a technology was already selected, diffed over a seconds-wide
interval with no turn advance, changed **exactly two words in the entire process**:

```
Owner#634  +144   12 -> 43       (current science topic)
Owner#634  +152   12 -> 43       (selected science topic)
Owner#638  unchanged
```

No other `Owner` field moved, no object was created, and no vector changed length. So:

- **One research slot, one item at a time**, across both trees. Selecting a doctrine *replaces* the
  technology in progress rather than running alongside it.
- **There is no "which tree" flag.** If one existed it would have had to change when switching from
  a technology to a doctrine, and nothing did — so the **id alone identifies the tree**, and
  `Owner:144` fully expresses the research target regardless of which tree it came from. Nothing
  extra to sync.
- Doctrine ids share the id space with technology ids: `3` was the tech "Cold Fusion", `12` another
  technology, `43` a doctrine. Whether doctrines occupy a distinct high range is not established
  from three samples.

**One `.data` red herring, recorded so it is not chased again.** A 102-character `std::string`
appeared at `0x008578C8` (`_Mysize` at `+16`, `_Myres` at `+20`, chars off-heap because the length
exceeds the 15-byte SSO buffer). It reads:

```
Increases the planetary shield strength by <Shield>40% (<Units>3000 units)
(Tech-Level 0: +0% Bonus)
```

That is not the doctrine that was selected — the selected one was "Mobility" (light ships +20%
speed, all ship shields −10%). It is a **hover/tooltip render buffer** holding whatever the mouse
last passed over, complete with embedded colour-escape markers. It correlates perfectly with the
action and carries no state.

#### Completion: one list for both trees, `[id, cost]` records

The doctrine was driven to completion by advancing turns unattended, with a rolling one-turn
snapshot so the completion was bracketed by a single turn:

```
turn 39   progress=1560   topic=43   done=16B
turn 40   progress=0      topic=-1   done=24B
```

`Owner:108` grew by one 8-byte element, so **completed technologies and completed doctrines share
one vector.** Records are `[id, cost]`:

```
[0] id=0    cost=0        starting techs, granted rather than researched
[1] id=1    cost=0
[2] id=43   cost=1600     the doctrine, and the exact progress it took
```

**`Owner:12` accrues whether or not a topic is selected.** The AI sat at `topic = -1` for the whole
game and still climbed to 1600, gaining 40/turn in lockstep with the human. So it is a research
**stockpile**, not progress toward a topic, and completion fires when the stockpile reaches the
selected topic's cost. It **carries the remainder** rather than zeroing. The earlier annotation
described it as "progress toward the current topic and not a lifetime total", which was right about
the reset and wrong about the accrual.

This has a sync consequence: **restoring `Owner:12` too high will instantly complete whatever topic
is set** on the next turn.

#### Loading research state — let the engine append the record

`Owner:108` has `cap == end` after every growth, so there is no slack to append a record by hand;
doing so would need `VirtualAllocEx` and a pointer the engine will later free. It is unnecessary,
because completion is driven entirely by two writable fields. **CONFIRMED by write test:**

```
write   Owner:144 = 12, Owner:152 = 12, Owner:12 = 10000
advance one turn
result  progress=7640  topic=-1  done 24B -> 32B  cap 32B
        [3] id=12 cost=2400        written by the engine, array reallocated
```

`10000 + 40 − 2400 = 7640`, so the remainder carry is proven rather than inferred, and the engine
reallocated the array with its own allocator (the array's address changed).

**So the procedure for loading a civ's research state is:** for each completed item, write the id to
`Owner:144`/`Owner:152`, write a stockpile above its cost to `Owner:12`, and advance one turn — the
engine appends `[id, cost]` with the correct cost and applies the item's modifiers client-side.
Then write the true stockpile to `Owner:12` once at the end, or the civ is left holding the leftover
surplus. This costs one turn per completed item and needs no foreign allocation, which is the same
trade the known-players ship shuffle makes.

[ ] check whether more than one item can complete per turn — the stockpile survived at 7640, well
above the next cost, so a single turn might absorb several if the topic is rewritten between them,
which would collapse the load to far fewer turns
[ ] establish whether doctrine ids occupy a distinct range from technology ids — three samples
(`3`, `12` technologies; `43` a doctrine) hint at it but do not show it, and it only matters if the
server ever has to validate an id rather than copy it

**`Owner:344` is withdrawn as a research candidate.** It doubled 120 → 240 bytes on doctrine
completion, having also doubled on a technology completion in an earlier game — so the correlation
has now held twice. Its contents do not support it: ASCII fragments on the human civ (`"BoxTicket"`,
`"the xy-map|click"`, `">GoodGuy"`) and `SolarSystem` pointers on the AI's. It is neither a research
list nor the "available/unlocked build options" previously guessed, and a field that changes on the
right events with the wrong contents is exactly the shape that produced the falsified `Owner:68`
and `Owner:200`.

[ ] identify `Owner:344` on its own terms — mixed ASCII and `SolarSystem` pointers across civs
suggests the 8-byte record framing is wrong, so start by establishing the real element stride

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

**Resolved — `Planet:100` byte 3 is the recruitment rate.** The field reads `0x3C646464` on the
military-base planet and `0x00646464` on every other planet including uncolonised ones. Bytes 0–2
are `100/100/100`, matching a UI showing loyalty at 100% everywhere, so one of them is still the
leading loyalty candidate. **Byte 3 is the recruitment-rate percentage**, confirmed in a later game
against a UI showing 0% (minimum), 20% and 40% (maximum) on three planets:

| Planet | UI recruitment rate | `Planet:100` byte 3 |
|---|---|---|
| p1 | 0% (minimum) | **0** |
| p2 | 20% | **20** |
| p3 | 40% (maximum) | **40** |

It was the only field in the object whose value set across those planets was `{0, 20, 40}`, and 532
other planets read `0`. The earlier guess that byte 3 was "the military base's effect" was half
right: the field is the recruitment rate, and the base plausibly raises its ceiling — the reading of
`60` came from the one planet that had a military base, exceeding the `40` maximum observed without
one. Suggestive, not proven.

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

### Remaining `Planet` unknowns, and why a fresh save cannot settle them (July 2026)

Four open fields were worked in a freshly started game. Only one resolved, and the reason the
others did not is worth recording: **the two civilisations in a fresh galaxy are byte-for-byte
symmetric** — both homeworlds read population 7, space 300, food 58/600 and 3 facility types. That
is the same condition that made `Owner:24` undecidable for three sessions. Discriminating a field
requires the observations to differ.

**Resolved — `Planet:104` low 16 bits are not a companion to space.** They are zero on every
colonised planet and non-zero on only 9 of 538, all uncolonised, always a multiple of 256
(`0x3900`, `0x2300`, `0xF700`…). Those 9 are the same planets carrying the other appearance floats,
so the low half belongs to that non-gameplay block. Only the high half is meaningful.

**Strong evidence — `Planet:516` is view state.** It reads `92.16` on the human's homeworld, the
planet being viewed, and `0.0` on the AI's, despite the two being otherwise identical in every
gameplay field. Together with its values swapping between two planets over one turn, that settles
it as animation or view state rather than game state.

**Unresolved — `Planet:356/360/364`.** All three are zero on both colonised planets in a fresh
save, while uncolonised planets hold float bit patterns there (`0x3EDB6DB8`), i.e. uninitialised
memory shared with the appearance block. In an older, more developed save the group held packed
`uint16` pairs alongside `Planet:352`.

**Unresolved — `Planet:512`.** Populated for only 4 of 108 systems, and only the home system's
value (`369`) is a plausible integer; the other three hold float patterns. 104 of 108 systems do
carry a single uniform value across their planets, so the per-system reading holds, but the meaning
does not follow from it.

**Unresolved — the float in the `Planet:384` element.** It read `61.23 / 44.72 / 13.0` in a
developed save and exactly `1.0` on both homeworlds in a fresh one, so it starts at 1.0 and grows:
a progress value or multiplier rather than a static property.

### Colonising a second system settled two of the three (July 2026)

**`Planet:512` is per-civ, not per-system — reversing an earlier correction.** Founding a colony in a
second system made that system's four planets carry the **identical** value as the home system's four
(`1094` on all eight), while the rival's system read `0`. A genuinely per-system quantity could not be
equal across two systems. The per-system reading only ever looked right because *uncolonised* planets
inside an occupied system receive the value too, which is what made it look like a property of the
system rather than of the occupier.

It is a **monotonic counter on a minutes-scale cadence, independent of the turn number**: it rose
`1094 → 1095 → 1096` across reads a few minutes apart while the turn counter stayed at `10`, and was
completely stable across 14 seconds. Turns are an hour long (the on-screen timer counts down from
59:59), so many increments fit inside a single turn. **The unit is not established** — elapsed
minutes, a sub-turn tick, or something else. What is established is that it is neither a turn count
nor a per-second timer. That also explains the erratic deltas recorded earlier (`+90`, `+4`, `+7`,
`+227`): those observations were minutes apart in wall-clock time, not a fixed number of turns. It
appears nowhere in `Owner`.

**The `Planet:384` float is probably planet age in turns.** At turn 10 it read exactly `10.0` on both
homeworlds (colonised turn 0) and exactly `0.0` on the colony founded that same turn:

| Planet | colonised turn | age | float |
|---|---|---|---|
| #225 homeworld | 0 | 10 | **10.0** |
| #473 rival homeworld | 0 | 10 | **10.0** |
| #116 new colony | 10 | 0 | **0.0** |

Not yet settled, because the developed save read `61.23 / 44.72 / 13.0` for ages `47 / 41 / 15`, which
does not fit. Either that was a different quantity or the turn number assumed there was wrong.
**Prediction to test:** after N further turns it should read `10+N / 10+N / N`.

**`Planet:356/360/364` fill in as a planet develops.** On the 10-turn-old homeworld with population 7,
this group *and* `Planet:352` all read `0x00070007` — the same packed `(pop, pop)`. On the colony
founded that turn, population 2, the group is `0` and `Planet:352` reads `lo=2, hi=0`. So only
`Planet:352`'s low half is live from the start, and the rest accumulate — population history or
targets rather than a second live counter.

### Both confirmed at turn 13 (July 2026)

**The `Planet:384` float is planet age in turns — confirmed by pre-registered prediction.** At turn 10
it read `10.0 / 10.0 / 0.0` for ages `10 / 10 / 0`. The prediction that three further turns would give
`13.0 / 13.0 / 3.0` was then verified exactly on all three planets. Since the developed save's
`61.23 / 44.72 / 13.0` does not fit ages computed as `47 / 41 / 15`, the turn number *derived* in that
save was probably wrong rather than this reading — the turn counter had not been located at that point.

**`Planet:352 … 364` is a population history ring.** Read as **eight `uint16` slots, newest first,
one per turn, capped at 8 entries**. Bit `0x1000` marks any entry older than the current value:

| Planet | slots (`*` = `0x1000` set) | live entries | age + 1 |
|---|---|---|---|
| #116, colonised t10, pop 3 | `3, 2*, 2*, 2*, 0, 0, 0, 0` | 4 | **4** ✓ |
| #225, colonised t0, pop 8 | `8, 8, 8, 7*, 7*, 7*, 7*, 7*` | 8 | 14, capped at 8 |

Slot 0 — the low half of `Planet:352` — is the current population and agrees with the `Planet:144`
citizen-list element count. This explains why the group appeared to hold "packed `uint16` pairs with
`lo == hi`" in earlier sessions: that is just two adjacent turns holding the same population.

**`Planet:368` is not part of the ring.** It reads `8` on 537 planets and `4` only on the newest
colony, so it is neither population nor the array. It remains unidentified, and the earlier
falsification of it as an ownership flag stands.

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
- **`ejbo_annotations.json`** — Persistent field labels keyed `<RTTI class>:<offset>`. Offsets may
  be **negative** for multiple-inheritance classes whose allocation starts before the tag
  (`Owner:-44` is the civ name).

Two file-level hazards, both of which have already destroyed annotations once:

- **Encoding.** The file contains em-dashes. Read and write it with an explicit
  `encoding="utf-8"`; Windows defaults to cp1252 and a single default-encoded save turns every
  em-dash into mojibake across the whole file.
- **Duplicate keys.** These are legal JSON and resolve silently to the **last** value, so the file
  can carry two annotations for one offset while the viewer shows only one — and the next rewrite
  deletes the hidden one permanently. `Planet:356/360/364` each sat duplicated for several
  commits, with the *detailed* text shadowed by a terse stub. `load_annotations()` now warns on
  duplicates at scan time.

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