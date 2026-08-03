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

The parser recovered from git history (`1b4918c:prototype/server/save_parser.py`) now lives at `server/dev_tools/save_parser.py`. Its GSET key-value decoding still stands; its section discovery has been replaced, because the framing turned out to be self-describing.

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

So pushing an order takes **three coordinated edits**, not one: the SHCO order-type byte, the has-orders byte, and the appended `ROUT` + `u32`. Writing only the `ROUT` leaves the order type at 0. `server/dev_tools/inject_order.py` does all three.

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

`server/dev_tools/inject_order.py --dat <path>.dat` writes that form; its `-o` output remains the wire form for `loadgame`.

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

### Audit: is memory editing still needed for multiplayer? (August 2026)

**Verdict: no, not for state sync — with one confirmed gap and one caveat.** Both directions of the multiplayer loop now run through the save blob, and everything the memory-editing tools write is carried by it. What memory access remains is either test harness or a small amount of `.data` bookkeeping the blob does not serialise.

#### The blob is a fixpoint

A save was captured, loaded into a fresh client via the `.dat` path, and captured again. The two blobs are **byte-identical** — 117,877 bytes, 1,317 sections (`server/dev_tools/diff_saves.py`). Nothing in the blob is lost, reordered or regenerated differently by a round trip.

Note what that test cannot see: state that is absent from the blob *entirely* is missing from both captures, so it compares equal while still being lost. The gap below was found that way, not by this test.

#### Everything the memory tools write is blob-covered

Checked against the live client after a `.dat` load, with nothing but the blob to build from:

| Tool | Writes | Carried by the blob? |
|---|---|---|
| `set_population.py` | citizen vector `Planet:144/148/152` | **yes** — both homeworlds came back with 7 citizens |
| `set_facility.py` | `Planet:204` map, `Planet:208` count | **yes** — both came back with 3 facility types |
| `ai_player` order origination | `Ship:48` order object | **yes, and superseded** — `ROUT` builds it engine-natively |
| `advance_turns.py` / `fast_turns.py` | turn length `0x0080AA08` | n/a — turn pacing, and `GSET.turnlength` restored 3600 anyway |
| `snapshot.py` | whole process | n/a — test reproducibility |
| `patch_hide_next_turn.py` | UI byte patch | n/a — cosmetic |

The live census after the load — 538 `Planet`, 108 `Sun`, 4 `Ship`, 2 `ShipDesign`, 2 `Owner` — sums to **654**, exactly the object count in the `SAVE` payload's first dword. The turn counter came back as `2`, matching the `turn=` the save was taken at.

This also settles an asymmetry flagged earlier as unconfirmed: the loader adds `[0x00857C58]` to every non-zero object-reference id, and that global reads **0**, so ids can be written absolutely.

#### The gap: `.data` bookkeeping the blob does not serialise

The four homeworld customisation click counters at `0x00842AE4`–`0x00842AF0` all read **0** after a load, against a `GSET homeworld_changes` budget of 30 — while the *effect* is present, the homeworld (planet 257) carrying space 450 against a 300 base. So the client believes nothing has been spent and re-offers the whole allowance. This is the popup, and it is a **correctness** problem: a player could bank another 30 increments on every pushed state. Tracked as the TODO below.

The general shape matters more than this instance: **game state living in `.data` rather than on an object cannot be in the blob**, because the blob serialises objects. Any other such counter has the same problem.

#### The caveat: the client never uploads state on its own

`SaveGame` at `0x0048B350` has exactly one caller in the binary — the Save/Load dialog at `0x0048B950`, which is reached through a message map, and the normal game has no such dialog. So a server cannot ask a client to submit its state; `client/dev_tools/trigger_save.py` gets a blob out by calling `SaveGame` in a remote thread. That is not memory *editing* — it invokes the engine's own routine and writes no game state — but it is still process injection, so the read direction is not yet a clean protocol operation.

There is a much better candidate for what the original did. `0x0056EBC0` writes an **`STCO`** section that includes ship routes via the same `Route::Write` used by `ROUT`, gated on `[0x0086F4F8]` and `[0x0086F1A2]`, and reached from `0x00574E10` inside the testbed init path at `0x00577160`. That looks like per-turn order submission — far lighter than uploading a whole galaxy.

[ ] decode the **`STCO` section and the path that uploads it** (`0x0056EBC0`, gated on `0x0086F4F8` /
`0x0086F1A2`, reached via `0x00574E10` from `0x00577160`). It serialises ship routes with the same
`Route::Write` as `ROUT`, which makes it the strongest candidate for the original per-turn
order-submission format — the missing half of the multiplayer loop. If it is what it looks like, the
server learns player intent from a small `STCO` upload instead of a full save, and the remote-thread
`SaveGame` call in `trigger_save.py` becomes a dev convenience rather than the mechanism.

[ ] find the rest of the **`.data`-resident game state** the blob cannot carry. The fixpoint test is
blind to it by construction, so the test that would close this is a **played-vs-loaded comparison**:
take a client whose state arose from play, snapshot its objects and `.data` semantically, load a blob
captured from it into a fresh client, and diff. Doing it after a load proves nothing, because the
non-blob state is already at defaults on both sides. The homeworld click counters at
`0x00842AE4`–`0x00842AF0` are the one confirmed member of this class so far, found via the popup
rather than by search.

[ ] stop the **"Customize Your Home World" popup reappearing after a loaded save**. Observed on the
first successful `.dat` load: the galaxy came back correctly — workers, ships and settings all
carried over — but the client re-offered homeworld customisation and civ customisation a second
time, which would let a player bank a second round of upgrades every time a server pushed a state.
**It is worse than a second helping of upgrades.** Confirming the popup is what *writes* the click
record and applies the `+50` space commit, so a returning player who is shown the popup and confirms
it with zero clicks would **silently reset their homeworld to base values and overwrite whatever the
server restored**. The memory report's "suppress both customisation popups" item carries the
implementation options and the warning that any restore must run *after* the engine's commit, not
before it.

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

Moved to **`CosmicSupremacy_Memory_Reconstruction_Report.md`** — the field-by-field
reconstruction of the in-memory object model, together with its open annotation TODOs.

It is no longer the multiplayer mechanism (see the Audit in Section 2), but it remains the
way to drive the game in-process, which is faster than a save / edit / relaunch cycle and is
the intended basis for training a custom AI. It is also the only way to reach game state that
lives in `.data` rather than on an object, which a blob cannot carry.

---

## 12. Save/Load Capture & Automation (June 2026, corrected August 2026)

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

The `%2B` encoding was independently re-confirmed in August 2026: a 38,960-character capture
contained 748 `%2B` and no literal `+`. Any code reading `data=` must percent-decode it, and must
not use a form parser that also turns `+` into a space.

> The tooling this section originally described — `save_codec.py`, `game_controller.py`,
> `game_data/multiplayer_saves/` — no longer exists and never appears in git history under those
> names. The current equivalents are `server/dev_tools/save_parser.py`,
> `server/dev_tools/inject_order.py`, `server/dev_tools/diff_saves.py` and the `savegame`
> persistence in `cs_server.py`, which writes to `server/saves/`.

### External save/load trigger — ACHIEVABLE (corrected, August 2026)

**This section previously concluded that no external trigger existed. That was wrong**, and the
fallback it dismissed as "fragile" is what worked. Both directions now run unattended. What
survives from the original findings is the part about the *engine's own* triggers.

Still true:

- **No dedicated sync endpoint.** The client API is only `savegame` / `savegamelist` / `loadgame`
  (plus civ / coa / gov / tutorial). There is no "submit orders" call.
- **Turn resolution does not upload a save.** Firing a turn via the countdown write at
  `0x0080AA08` resolves it client-side with no `savegame` POST. Nothing crosses the network
  during normal ticks.
- **In-engine, the Save Game menu is the only save trigger.** `SaveGame` at `0x0048B350` has
  exactly one caller in the binary — the Save/Load dialog at `0x0048B950`, reached through a
  message map — and normal play has no such dialog.

Corrected:

- **Save is triggerable externally.** `SaveGame` runs correctly from a `CreateRemoteThread` stub:
  it is synchronous down to WinInet's blocking `HttpSendRequestA` and touches no per-thread
  state. The three things the old note called fragile are all pinned —
  `bool __thiscall SaveGame(this, int gameid, std::string *gamename)` with a `ret 8` epilogue, and
  `this` is never read. `client/dev_tools/trigger_save.py` does it; it produced a 117,024-byte
  blob with nobody at the keyboard.
- **Load does not need a trigger at all.** The client loads a `.dat` named on its own command line
  during startup, on the main thread (Section 2). No dialog, no server, no injection.
- **Load cannot be triggered from a remote thread**, which is worth recording as the reason the
  startup path is the right answer rather than a convenience: the load path dereferences objects
  held in thread-local storage, and a created thread's TLS block is zero-filled. Confirmed by
  crashing it — `EXCEPTION_ACCESS_VIOLATION` at `0x005D17F5` with `eax=ecx=0`. The thread exit
  code still read as success, because the crash handler runs on the faulting thread.
- The **`savegamelist` turn-bump** idea (advertise a higher turn to trip the client's out-of-sync
  check) was never confirmed on the client and is now unnecessary.

### Conclusion (corrected)

**Save-blob sync is the primary path for multiplayer.** The blob is a complete state snapshot, it
round-trips byte-exactly, the server can push one into a client at startup with no user
interaction, and a server-authored ship order has been confirmed to fly a ship that nobody
clicked. See Section 2 for the format, the push mechanism and the audit.

Live-memory access is retained for what it is genuinely better at — driving the game in-process
without a relaunch, which is the intended basis for training a custom AI — and for reaching game
state that lives in `.data` rather than on an object, which a blob cannot carry. It is documented
in `CosmicSupremacy_Memory_Reconstruction_Report.md`.

---

### What We Are NOT Doing

- **OAuth / social login** — requires new UI windows in the EXE; not feasible via patching

---                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             