# Cosmic Supremacy, Reconstruction Reference
*Binary analysis of `CosmicSupremacy.exe`*

---

## 1. Binary Overview

| Property | Value |
|---|---|
| File | CosmicSupremacy.exe |
| Format | PE32, 32-bit Windows executable (Intel 80386) |
| Subsystem | GUI (windowed application) |
| Packer | **None**, strings are fully readable |
| Framework | Custom C++ (GDI+, COM/OLE, DirectX) |
| Graphics | DirectX 9 (`D3D9.DLL`) |
| Audio | Windows Multimedia (`WINMM.dll`) |
| Networking | WinSock2 (`WS2_32.dll`), WinInet (`WININET.dll`) |

The EXE is **entirely self-contained**, all game assets (images, fonts, UI resources) are embedded directly inside it. No separate data files are required.

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
testconnection         : ping/health check
loadgame               : load a save game
savegame               : save current game state
savegamelist           : list available saves
loadgov                : load governor settings
savegov                : save governor settings
govlist                : list available governors
uploadcivname          : upload civilization name
listcivnames           : list civ names (userid=%d)
getcoa                 : get coat of arms image (coaid=%d)
listcoa                : list coat of arms (userid=%d)
uploadcoa              : upload coat of arms image
passedtutorial         : mark tutorial as complete (userid=%d&pass=%s)
entertestbedgalaxy     : enter test bed galaxy
getplayerfame          : retrieve player fame points
```

### Save/Sync Data Format (Historical)

The `data=` field in `savegame` POST requests contains a game state snapshot encoded as `base64( uint32_LE(decompressed_size) + zlib_deflate(structured_binary) )`. The decompressed blob uses a hierarchical section-based format (SAVE/GSET/GLOB/OWNR/SOLA/SHIP/etc.).

The parser recovered from git history (`1b4918c:prototype/server/save_parser.py`) now lives at `server/dev_tools/save_parser.py`. Its GSET key-value decoding still stands; its section discovery has been replaced, because the framing turned out to be self-describing.

**A payload can hold more than one run of sections, separated by its own fields.** `DYNO` is the case that proved it: the writer at `0x004DA310` emits an orbit id, a `SHCO` section, three more own fields, and then, only when the ship carries an order, a `ROUT` section and a trailing word. Discovery scored each candidate start by its first run alone and kept whichever covered the most bytes, so in an ordered ship's 124-byte `DYNO` the 90-byte `ROUT` run beat the 17-byte `SHCO` run and `SHCO` was reported as absent, while the same ship's idle 30-byte `DYNO` reported `SHCO` correctly because there it was the only run. Fixed September 2026 by scoring each candidate by everything it can chain through small gaps; section counts rose from 419 to 443 on a two-civ turn-2 blob, so the loss was not confined to `DYNO`.

Two consequences worth keeping in mind:

- **A section's own bytes are not a prologue plus an epilogue.** Whatever sits in a gap between two children belongs to the parent, and for a `DYNO` that gap holds the has-orders byte and the admiral id, which is most of what an order consists of. `save_parser.own_ranges` and `own_bytes` return them correctly; three tools were rebuilding payloads as prologue, children, epilogue and silently dropping gap bytes.
- **Children are ordered but not contiguous**, so anything rebuilding a payload from its children must copy the gaps. `filter_blob.py` now deletes the byte ranges it wants gone rather than reassembling from the children it keeps.

**A payload that is mostly its own fields hides its trailing sections.** `PLPR` is about 160 bytes of planet fields followed by `PROD` and `ENLI`, which together cover 18% of it, far below the 50% coverage a run had to reach to be believed. Those sections were therefore invisible, and the production queue lives in `PROD`, so the one thing a build order changes could not be addressed. Ending exactly on the payload boundary is the evidence that replaces coverage: sizes are self-describing, so a chain of two or more sections whose last byte is the payload's last byte has agreed with the framing several times over. With that rule the discovered section count on the archive rose from 53,490 to 96,492, and the checks above still hold on all 73 readable blobs.

### What a planet's `PLPR` holds (September 2026, partial)

    +4            the per-unit output rates, `Planet:96` on the object
    +15           u32 food store
    +19           u32 food store size, what fills it once
    +23           u32 production points accumulated toward the current item
    +27           u8  recruitment rate, a percentage of food diverted into
                      military growth each turn
    +28           u32 food per soldier, 300 in every capture
    +32           u32 the military store; a soldier is made when it reaches
                      the cost above, and the excess carries
    +36           u32 population count
    +40 .. +40+9n nine-byte citizen records, one per unit of population:
                      +0  u8   job id, 0 farmer, 1 worker, 2 scientist,
                               5 miner, 6 banker
                      +3  u32  the owning civ's object id
                      +7  u8   a per-citizen value, permuted by a
                               reassignment rather than changed
                      +1, +2, +8  zero in every record seen
                  The array is kept sorted by job, so moving one farmer to a
                  banker reorders the whole list rather than editing one byte,
                  which is why a naive diff reads as values shifting along.
                  Verified against the live population vector at `Planet:144`,
                  and across every colonised planet in a three-civ galaxy:
                  populations of 4, 7, 7 and 10, each record's owner matching
                  the planet's owner, every job id recognised.

                  The owner id equals the planet's owner on every naturally
                  created planet measured, and what else it is for is not
                  established. A colony founded by a ship that
                  `inject_ship.py` had cloned came up with citizens carrying
                  the DONOR civ's id, because that tool rewrote the ship's
                  owner at payload `+16` and left it at `+77` and `+86`. That
                  is a bug in the tool rather than a property of the game:
                  **the planet's owner owns the working population.** Fixed
                  September 2026 by repointing every remaining reference, but
                  galaxies generated before the fix still carry it.
    +40+9n        u32 stationed-military count, then that many records of the
                  same nine-byte shape. A ship carries its crew in the same
                  form, SHPR+4 the count and SHPR+8 the records, and a record
                  moves between the two byte for byte. In a soldier's or crew
                  member's record, `+1..+2` is an f16, its experience: it
                  rose 0.2 a turn on a planet with a Military Camp, the
                  manual's 0.1 doubled, and a new soldier's reads 0
                  (November 2026, the live Beta galaxy). It reads zero in a
                  citizen's record because citizens do not train.
    +167          PROD, the production queue, 29 bytes when the planet is in
                  no queue; its payload carries the queue's own fields, the
                  object id of what is being built, and a nested section
                  naming it, FCLT for a facility. A queued planet's PROD is
                  longer (41, 49 and 53 bytes seen) by fields not decoded, so
                  no fixed offset finds the design: a ship build's design id
                  is the payload of the SHIP section that ends PROD, its last
                  4 bytes
    +255          ENLI, 4 bytes

`WLTH` appears inside `PROD`'s payload rather than beside it, and is replaced by `FCLT` when a facility is queued. The rest of `PLPR` is undecoded and mixes the planet's population, stores and derived economy with the job decision, which is why a submitted job change is carried as the citizen array alone and never as the whole section.

**The settled block**, the 51 bytes between `PROD` and `ENLI` on a colonised planet, opens with
the population history the food store is sized from (November 2026, from chained engine ticks):

    +0            8 × u16  past populations, newest first; bit 12 (0x1000)
                           marks an entry older than the last birth
    +16           u32      slots filled, 1 at founding, 8 from turn 7
    +24           u32      the owning civ
    +28           f32      turns since founded
    +32           u32      the turn it was founded
    ENLI −5       u8       the ship resource the planet mines, 0 metal to 4 exotics
    ENLI −4       u32      ship resource per miner, before mines and traits

The ore fields hold on every planet, owned or not: across a 547-planet galaxy the type takes
all five values, metal most often (266 planets), and the rate runs 1 to 25. A miner on a
rate-10 planet adds 10 metal a turn and one on a rate-6 planet adds 6, on every mining tick in
the oracle corpus (November 2026).

Each tick pushes the population after any birth, and a birth sets bit 12 on every older entry.
The store size is the manual's `40 + 40 × floor((n − 1)^1.5)` for `n` the average of the
unflagged filled entries, rounded up, so a birth resizes it at once and a loss walks it down
over the following turns: 18 to 15 citizens gave 2840, 2600, 2600, 2600, 2600, 2360. On the
Single Player galaxy the slot count rises 1 to 8 from founding. In the referee archive galaxy,
whose homeworlds the join tools rewrote, it falls at a birth instead (5 to 4, 6 to 4), and what
it counts there is not established.

#### Section framing, read out of the archive class, not inferred from blobs

Every section, from the outermost `SAVE` down to the smallest leaf, carries the same 8-byte header:

| Offset | Type | Meaning |
|---|---|---|
| `+0` | `char[4]` | tag, e.g. `ROUT` |
| `+4` | `uint32` | bits 0–25 = payload size in bytes, **excluding** this header; bits 26–31 = section version |

- `Archive::BeginSection(tag, version)` at `0x005E6260` writes the tag byte-swapped, which is why an MSVC multi-char constant such as `'ROUT'` (`0x524F5554`) lands in the file as readable ASCII, then a dword holding the version in its top 6 bits and a `0x3FFFFFF` size placeholder.
- `Archive::EndSection` at `0x005E6320` seeks back and patches the low 26 bits with `current_offset - section_start - 8`.
- `Archive::WriteRaw` at `0x005E5E10` is a plain memcpy append with no per-field framing, so a payload is exactly the concatenation of the fields its writer emits, in order.

Two consequences. First, the blob can be walked **generically**, no section's internals need to be understood to find the next one, which supersedes the earlier marker-regex scan that could not tell a real tag from four bytes of float data spelling one. Second, the old SAVE-header reading (`uint16 body_size` + `uint16 version 0x1000` + `uint32 section_count`) is that single dword seen as two halves: version 4 gives a high `uint16` of `0x1000`, and a `body_size` of "decompressed size − 8" is exactly a payload size with the header excluded.

#### ROUT, the ship order, and a non-UI path to constructing one

`ROUT` is the save form of the order object that `Ship:48` points at. This matters beyond parsing: the engine's loader **builds a live order with no user interaction**, which is the capability the AI player and multiplayer both need (see the AI player's STRATEGY.md §6a).

| Role | Address |
|---|---|
| Writer `Route::Write` | `0x004E5310`, called from the ship writer at `0x0056ECC0` |
| Reader `Route::ReadFields` | `0x004E6D40` |
| Factory (allocates + reads) | `0x004E6E90`, `operator new(0x60)` |
| Attach `Ship::SetOrder` | `0x004DA450`, stores at `ship_base+0x38`, i.e. `Ship:48` |

The factory's `operator new(0x60)` independently confirms the 96-byte order-object size previously pinned from heap-block headers.

`ROUT` is **optional per ship**: the writer skips it when the order pointer is null (`test ebx, ebx / je` at `0x0056ECB9`), and on load the reader is entered only if the next tag actually is `ROUT`. `Ship::SetOrder` then rejects the order, freeing it immediately and returning false, if its byte `+0` reads 0, so an injected order must carry a non-zero byte `+0`.

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

This also **resolves two previously separate readings of the order object into one structure.** The writer hands both `+28` and `+52` to the *same* helper `0x004E5200`, which reads `_Myfirst`/`_Mylast` at `+0xC`/`+0x10` of whatever it is given and divides the span by 28. So `+28` and `+52` are two objects of the same type, each holding a 28-byte-element vector 12 bytes into itself, which is exactly why the known route-leg vector sits at `+40/+44/+48`.

- **Confirmed: the second leg vector is the path already flown** (November 2026, the live Beta galaxy). A colony ship with a 183.95 leg in the first list and a 76.27 leg in the second stood exactly 183.06 from the first leg's origin at progress 183.06, and the second leg ended where the first began. Progress runs along the first list alone, so a ship arrives when progress reaches the first list's length.
- **The intrusive list nodes are not persisted.** The helper touches only the vector, and the reader constructs both `+28` and `+52` from scratch via `0x004A6D30`. An injected `ROUT` therefore supplies leg vectors only, and the engine rebuilds the node chains that make this object impossible to fabricate by hand.
- **One asymmetry to watch:** the writer emits the reference id raw, but the reader **adds the global at `0x00857C58`** to any non-zero id before resolving it through the object registry (`0x004D9CA0`). Injected ids may need to be written relative to that base.

#### DYNO, the enclosing section, and why ROUT alone is not enough

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

**A scout is the exception: its has-orders byte reads 0 and its `ROUT` is there anyway**
(November 2026, the Single Player scout from turn 0 and two others). The SHCO byte reads 2,
the route holds one leg to the next system, and the engine rewrites it on the tick the scout
arrives. A reader that trusts the has-orders byte misses every scout's route.

**A route's legs stand off each body by 1.02 radii.** The radius is the `f32` at `SUN` and
`PLNT` own payload `+20`: a sun of radius 3.9276 starts a leg 4.0062 out, and a planet of 1.6
starts one 1.632 out. The `ROUT` tail after progress holds the ship's position and, while it
orbits, the id of the body it orbits, 0 once it is moving. Progress is the distance travelled
along the legs, and the ship's position is exactly that point, so a ship placed off its route
jumps onto it at the next tick.

**Colonising is a ship order, not an immediate-effect action.** It is the same three edits with the SHCO byte reading **3** where a move writes **1**, so both come off one extraction path. A human given a turn-2 galaxy and asked for one move changed exactly one section of a 37,851-byte blob, that ship's `DYNO`, 38 bytes to 132.

If the owner check at `0x004DA310+0x52` fails, the writer emits a short `INFO` form instead of the full one, which is what the `'INFO'` branch in the reader at `0x004DBBB2` consumes.

#### Confirmation status, CONFIRMED against engine-produced bytes

`savegame` was triggered on a live client with two ships under orders (a scout with one route leg and a colonize order with two), and every `ROUT` field was compared against the raw 96 bytes read out of the process:

- Both payload sizes were **exactly** `54 + 28 × leg_count` (82 and 110), predicted from the layout before the blob was parsed.
- `order_kind`, `flag_1`, origin XYZ, target XYZ, progress and `+80/84/88` all matched **bit-exactly**; every leg matched, and each leg's length equalled the euclidean distance between its own endpoints (600.0787, 16.8664, 11.0350).
- The parser consumed exactly the declared size in both cases.

Two corrections and one confirmation fell out of the capture:

- **The second leg vector is real and is serialised.** `legs_b` was empty on both orders, but the byte accounting only closes if its `u32` count is present, the 54-byte fixed part includes it. Its 28-byte element size remains binary-derived only, since a non-empty `legs_b` has not been seen.
- **`+80/+84/+88` is the ship's *current* position, not "origin XYZ again".** On both captured orders that triple equalled the `SHIP` section's own position, which differs from the order's origin once the ship has travelled. The earlier reading was taken on orders that had not yet moved, where the two coincide.
- `SAVE`'s payload opens with a `u32` that read `654`, exactly the live EJBO object count, and the DYNO orbit id read `257` for a ship the memory scan independently placed in orbit of planet 257 at distance 0.0000.

#### Serving a blob back: SaveGame is callable off-thread, LoadGame is not

`SaveGame` (`0x0048B350`) runs correctly from a `CreateRemoteThread` stub, it is synchronous down to WinInet's blocking `HttpSendRequestA` and touches no per-thread state. `client/dev_tools/trigger_save.py` does this and produced the 117,024-byte capture above with no user interaction.

**`LoadGame` (`0x0048B5D0`) cannot be called that way.** The load path reaches objects held in thread-local storage; every caller of `0x005D17E0` fetches `this` as:

```
mov eax, [0x0087346C]      ; _tls_index
mov ecx, fs:[0x2C]         ; TEB->ThreadLocalStoragePointer
mov edx, [ecx + eax*4]     ; this module's TLS block, per thread
mov ecx, [edx + 0x48]      ; a pointer living in that block
call 0x005D17E0
```

A remote thread's TLS block is zero-filled, so that slot is NULL and `0x005D17E0` faults on `mov ecx, [eax+0x10]`. Confirmed by doing it: `EXCEPTION_ACCESS_VIOLATION` at `0x005D17F5`, `eax=ecx=0`, on the injected thread, with the game writing its own minidump. This is consistent with the earlier note about a TLS RB-tree in the testbed load path.

Note the thread exit code still read as success, the game's crash handler runs on the faulting thread, so a load trigger must verify the process is still alive rather than trust the return value.

**Consequence:** the load has to run on the **main** thread, which owns the populated TLS block. No hijack was needed in the end, the engine already has a main-thread load path that takes a file (below).

### Pushing a state into the client at startup, the production mechanism

**The client loads a save blob named on its own command line, during startup, on the main thread, with no server and no UI involved.** This is the shape the original online game used: the player never sees a default galaxy, the client comes up already in the pushed state.

The standalone bootstrap is `0x00579620`, whose log strings give the structure away, `'activating stand-alone mode'`, `'loading demo-galaxy'`, `"loading save-game '%s'"`, `'creating a dummy galaxy'`. It branches on `[0x0086F410]`:

| Branch | Path |
|---|---|
| `[0x86F410] != 0` | load the **embedded resource** named `DemoGalaxy`, type `Binary`, from the EXE's `.rsrc`: read it into an archive, `0x0048AD70`, then `0x0056D700` |
| `[0x86F410] == 0` | if `0x0056E7F0` says yes, `"loading save-game '%s'"` then `0x0056DAD0(name, 1)` → `0x0056D700` |

`0x0056E7F0` is the gate, and it is simply:

1. `0x0062BF60()`, argument count; needs at least 1.
2. `0x0062C090(&out, 0)`, the first command-line argument.
3. Its last 4 characters must be **`.dat`** (compared with the same helper used for the `DONE` checks).
4. The file must exist.

So: **`CosmicSupremacy_Resurgence.exe <something>.dat` loads `<something>.dat` as the galaxy.**

**The `.dat` holds the raw, already-decompressed blob**, it starts with `SAVE`, not with base64. The file path is `0x0056DAD0` → `0x005E53E0` (file-backed archive) → `0x0056D700`, and `0x005E53E0` reaches neither the base64 decoder `0x005F5F10` nor the `uint32`+inflate step `0x0048AD70`; it only reads the file into a buffer. The wire format's extra layers belong to the HTTP path:

```
loadgame response :  base64( uint32 + zlib(blob) )   -> 0x005F5F10 -> 0x0048AD70 -> 0x0056D700
.dat on disk      :  blob                            ->                            0x0056D700
```

`server/dev_tools/inject_order.py --dat <path>.dat` writes that form; its `-o` output remains the wire form for `loadgame`.

#### CONFIRMED end to end: a server-authored order flew a ship with nobody at the keyboard

A capture was taken, ship 649, which had no order at all, was given a Move order aimed at planet 259, and the result was written as a `.dat` and passed to a fresh client on the command line. Predictions were registered before the launch.

On load, ship 649 came up with:

| Field | Value |
|---|---|
| `Ship:48` | `0xA044F20`, non-null, allocated by the engine's own `operator new(0x60)` |
| `Ship:52` | `1` (Move), taken from the SHCO order-type byte |
| order origin / target | `(540.9454, 334.3067, 247.3618)` → `(516.7029, 333.0293, 278.5347)`, exactly planet 259 |
| legs | one leg, length `39.5105` |
| progress | `0.000000` |

After one turn:

- position `(532.6622, 333.8702, 258.0130)`, matching the pre-registered prediction to four decimals
- progress `13.5000`, exactly one turn at the design speed
- displacement from the origin exactly `13.5000`, confirming that **exact leg endpoints remove the engine's usual orbit-edge inset**
- travelled + remaining = `39.5105`, the leg length

This closes the blocker in the AI player's STRATEGY.md §6a. Orders no longer need a UI click, a fabricated object, or a remote-thread constructor call: the server writes a blob, the client is handed it at startup, and the engine builds the order with its own allocator and navigates it. Because the object is engine-constructed, it owns its allocations properly, the reason hand-fabrication was ruled out.

Two practical notes. The order type lives in **SHCO**, not in `ROUT`, so a pushed order needs the SHCO byte and the has-orders byte set alongside the appended `ROUT`, `inject_order.py` does all three. And the UI caveat already recorded for retargeted orders applies here too: the Ships tab may lag until a turn boundary, while the engine acts on the order immediately.

### Adding a whole PLAYER to a blob, `inject_civ.py` (August 2026)

**A galaxy is no longer limited to the two civs the client generates.** `server/dev_tools/inject_civ.py` clones an existing civ's `OWNR` section under a new name and object id, and hands the new civ a homeworld by transplanting the donor homeworld's `PLPR` onto an uncolonised planet. `--name` is repeatable, so a two-civ galaxy becomes a twenty-civ galaxy in one pass.

**CONFIRMED live.** A two-civ capture at turn 162 was edited and handed to a fresh client on the command line. It came back up with **three** `Owner` instances; the new civ owned the planet it was given (renamed `"Ceti's HQ"`), held its own cloned `ShipDesign`, and four turn boundaries resolved with no crash. The AI player then drove all three civs.

This closes what the memory report carried as a CRITICAL blocker. In memory a civ needs an allocation with `Owner`'s multiple-inheritance layout plus an insertion into the red-black registry at `0x02D48740`, keyed by an underived hash. In a blob it is bytes, and the deserialiser does all three. **The key is derived from the NAME**, neither existing civ's `Owner:-16` appears anywhere in a capture, both read identically across two independently generated galaxies, and the injected civ came up with a key that was in no file. A new civ therefore needs a unique *name* and nothing else.

#### Layout, measured against a two-civ capture and re-measured after the edit

```
GLXY payload:  u32 civCount ; OWNR x civCount ; SOLA... ; SHIP... ; NEBU...

OWNR payload:  u32 nameLen ; char name[nameLen] ; u32 objectId
               DATA { OWPR, KNPL, EXSY, ASKY, CVTR, SERV,
                      u32 designCount, DSGN x n, NEWS x n, GOVS, ADMS, SPQS, USSE }
               u32                                    (= Owner:4 in memory)

PLNT payload:  u32 objectId ; f32 x, z, y ; u32 ownerObjectId ; f32 ;
               u32 nameLen ; char name[nameLen] ; 6 bytes ; PLPR {...} ; u32
```

`GLXY`'s leading `u32` is the civ count, the same count-then-records idiom as the design count in front of a civ's first `DSGN`, where a record added without bumping the count is simply never read.

**Ownership is carried by object id in more places than the obvious one.** Besides `PLNT`'s `ownerObjectId` and the owner id that ends every `SDPR`, **every citizen record inside a `PLPR` carries its civ's id**, which is why the donor's id repeats at a 9-byte stride through a populated homeworld. A clone that patches only the identity fields leaves a homeworld full of the donor's citizens. `inject_civ.py` does a blanket 4-byte replacement across the cloned range instead.

**A homeworld is a `PLPR` transplant.** The target planet keeps its own head fields, object id, position, and receives the donor homeworld's whole `PLPR`, so it inherits that planet's space, population, food and facilities. Planet space lives in `PLPR` (it is `Planet:104`, inside the `PlanetProperties` embedded at `Planet:88`), so the target rock's own size is irrelevant and the two homeworlds come out identical, which is what a fair start needs anyway.

**Diplomacy comes with the clone.** The donor's relation records name the *other* civs by id, and those ids are not the donor's, so the blanket repointing leaves them intact: a clone of a civ at war starts at war with the same enemies, which have no matching record for it. Observed. A fresh-start galaxy should clone a civ at peace.

#### Designs for every civ

`inject_design.py`'s uniqueness check was galaxy-wide and is now **per civ**, which is what the game itself does, every galaxy starts with each civ owning a design called `Colony Ship`. `client/dev_tools/game_cycle.py --all-civs` uses that to give each `--design` to every civ in the blob, taking one template design id per `OWNR` straight from the blob rather than from the running client. An AI-vs-AI galaxy needs it: at turn 0 a civ owns nothing but a colony ship, so without it one side can scout and the other cannot.

#### `Owner:4` must be unique per civ, the missing-from-the-score-list bug (CONFIRMED, August 2026)

**An injected civ was present in the diplomacy list but absent from the overview score list, because a clone inherited its donor's `Owner:4`.** `Owner:4` is the `u32` that ends the `OWNR` payload (see the layout above). `add_civ`'s blanket `replace_u32` rewrites the donor's *object id*, which this is not, so the clone kept it. **`inject_civ.py` now assigns `max(existing) + 1`**, and `--userid` forces a value, pass one another civ already holds and the bug comes back, which is how the control below was built.

*How it was found, before anything was run.* Ruled out the server (no score-related `action=` has ever reached the stub, the log carries only `savegame`, `testconnection`, `listcivnames`, `listcoa`, `getcoa`, so the list is client-side); ruled out a missing per-civ section (`OWPR`, `CVTR`, `SERV`, `GOVS`, `ADMS`, `SPQS`, `USSE` are byte-identical across all three civs in a turn-5 capture, and score itself lives on the object at `Owner:28`); ruled out `KNPL` (at turn 185 every civ knows every other, the injected one included, 36-byte records keyed by the other civ's object id, third word the turn contact was made). What was left was the one per-civ field a clone duplicates: across three separately generated galaxies it reads **20 for `GoodGuy` and 21 for `BadGuy`, always**, and every injected civ carried its donor's value.

*The A/B that confirmed it.* Two `.dat`s built from the same turn-185 capture, **differing in four bytes**, `Ceti`'s `Owner:4`, 20 (colliding with `GoodGuy`) versus 22. Same galaxy, same scores, same contact state, loaded one after the other:

| | `Ceti` `Owner:4` | Diplomacy list | Overview score list |
|---|---|---|---|
| **A** | 20, collides with `GoodGuy` | all three civs | `GoodGuy` and `BadGuy` only, **and it stays that way across turn boundaries** (185 → 191+) |
| **B** | 22 | all three civs | all three, **but only after the first turn boundary** |

So the score list holds one row per distinct `Owner:4`, and the collision, not the injection, is what hid the civ. The A row matters as much as the B row: ticking turns does not fix a collision, so the boundary is not what was missing.

**What `Owner:4` actually is remains open.** `ejbo_annotations.json` recorded it as `Human=0, AI=21`, which suggested an account id rebound to the local player on load. **That is falsified**: live, the local player's civ reads **20**, matching its blob value. What is measured is narrower, the field is per-civ, distinct across generated civs, and the score list keys on it. A slot index, a roster index and an account id all fit.

#### Open items

**The score list is saved in `GLOB` and only a turn boundary rebuilds it.** The GLOB v17 payload is `u32 turn, i32, u8, u32 rowCount`, then one row per `Owner:4`: `u32 Owner:4, u32 nameLen, name, u32 Owner:28, f32 Owner:32, u8 flag` (writers `0x52BFD0`/`0x52BE30`, reader `0x52DA20`). Load (`0x56D700`) reads the rows back as saved; the rebuild `0x52DFE0`, called from the tick at `0x56D240`, runs `Owner::ComputeScore`, which also adds to `Owner:24` and `Owner:32`, so it cannot simply be called at load. **`inject_civ.add_civ` now writes the newcomer's row**, carrying the donor's tail as the clone's `OWNR` does, and leaves a list with no rows (turn 0) for the first boundary; `server/tests/test_score_row.py` covers it.

[x] **Confirm the injected score row live.** Load a turn-9 or later galaxy with a civ injected by the new `add_civ`: the Overview list shows every civ before any boundary, and still does after one, with the newcomer's score recomputed. Then a turn-0 injection: the list is empty for everyone until the first boundary. **Confirmed** (14 November 2026, the client's map at `0x857940` read after each load): turn 9 of `galaxy_demo` with `Newcomer` injected loads with its three rows, the newcomer's among them, and after one boundary still has three, with scores recomputed (the newcomer 0 to 1526). `SinglePlayerGalaxy.dat` with `Newcomer` injected loads with no rows, and the first boundary lists all three civs. A civ whose user id is 0, `DemoPlayer` in `galaxy_demo`, has no row before or after a boundary, injected galaxy or not. At load, `0x56D6C0` rebuilds a list that is empty past turn 0, so a blob that drops `GLOB`'s rows comes back listed.

[x] **The score list also feeds battle scoring.** `0x4430F0` reads it for the score ratio and skips a civ with no row, so a missing row changed battle scoring too, not only the display. Check whether any other caller reads the map at `0x857940`. **It does not feed battle scoring.** `0x4430F0` is the reputation forecast that the diplomacy screen (`0x444470`) and the declare-war dialog (`0x4BDE50`) show, and nothing in the battle code reaches the map: battle reputation (`0x518480`, from `0x51EEE5`) takes the scores it is given. The map's other users are the Overview screen, which copies it (`0x419700`) and walks it from the reputation panel (`0x57BE20`, called from `0x413E20`), the save's writer (`0x52BFD0`), the load (`0x52DA20`, `0x56D6C0`), the tick's rebuild (`0x52DFE0`) and its clears. A missing row hid a civ from the Overview and the forecast, nothing more.

[x] **A clone inherits its donor's display name and score.** The `OWPR` display-name override (`civ+0x1A0`) and `Owner:28`/`Owner:32` are copied, so from the first boundary an injected clone of a renamed donor shows under the donor's display name, and a joiner starts with the donor's score. Same kind of inheritance as `EXSY` below. **Fixed for joins** (`joins.fresh_start`, `test_fair_start.py`, 13 November 2026): a joiner's score is set to zero in its score row and in `OWPR`, where `Owner:28` sits 78 bytes before the length field of the civ name dialog 225 chose (measured against every civ's score row on the sandbox at turn 163), and the civ name and arms are cleared, so the client offers dialog 225 and the merge takes the joiner's own choice.

[x] **`EXSY` is cloned, not reset, and that is a cheat rather than a cosmetic defect.** An injected civ inherits the donor's explored-systems map, so cloning a developed donor tells the newcomer where the home planets are. Two things keep it from mattering yet and neither is a fix: the AI player does not read `EXSY` (it keeps its own discovery set, persisted per civ name), and cloning from a turn-1 capture inherits an empty map, so only the developed-donor fixture leaks. The fix needs the record layout, which is not decoded, the leading `u32` reads 63 on a civ that had explored 105 systems, so it is not a plain count and the obvious guess is already falsified. **That `EXSY` is the explored-systems map is itself inferred** from the tag and from its size tracking exploration across two civs; confirm it while decoding it. **Fixed for joins:** `joins.forget_inherited_map` empties the joiner's `EXSY`.

[x] **A joiner inherits the donor's civ traits, bonuses included.** Seen on screen 28 September 2026: a civ that joined at turn 11 carried the donor's food-output trait bonus. `add_civ` clones the donor's `OWNR` whole and the traits are inside it, so this is the same mechanism as `EXSY` and the food stores rather than a separate defect. Accepted for the beta, where everybody starts with the same extra anyway, and wrong afterwards: a new civ should get the homeworld button back and choose its own traits. Everything the fix needs is now located: the trait values are `OWNR > CVTR`, (index, value) pairs after a 20-byte header (`merge_orders.TRAIT_RULES`), and the two prompts are offered while `Owner:359` (homeworld) and `Owner:360` (traits) are clear, both one-byte flags in `OWPR` (see "The three one-time setup prompts"). So `add_civ` should give the joiner the untouched trait values and clear both flags, and the referee then takes the joiner's confirm like anyone else's. **Fixed for joins** (`joins.fresh_start`, 13 November 2026): every trait is set to 0, the value a civ holds before its first confirm, and `Owner:360` is cleared. Given the galaxy's turn 0, the joiner's homeworld takes the dialog fields of a homeworld whose prompt was never confirmed there, and `Owner:359` is cleared; without turn 0 the homeworld stays as cloned and the join log says so.

[x] **Does a joiner inherit the donor's corruption?** Unknown, and it matters more than it looks. A civ joining a galaxy whose seat one is failing late would take on that civ's corruption along with its warehouses, which could cripple a newcomer at the moment they arrive. Corruption has **resisted location in memory entirely** (see the memory report) and is plausibly derived at display time from population, buildings and military rather than stored, in which case copying the planet copies the corruption and there is no field to clear. `GSET.corruption_multiplier = 100` is a galaxy-level knob and the cheapest way to make it vary. Settle whether it is stored or derived first: the two answers need completely different fixes, and only one of them has a field to blank. Observed at −0.5% on a turn-11 joiner, which is unremarkable on its own and says nothing about a late-game donor. Corruption is derived, from the civ's planet count and its reputation (`docs/Fast_Sim_Plan.md` §5b), so the field to blank is reputation: see the next item. **Answered:** a joiner holds its own planets, and `joins.fresh_start` zeroes its reputation, so it starts with a new civ's corruption. Not yet seen on screen.

[x] **A joiner may keep the donor's reputation.** `Owner:32` is the empire reputation float that the corruption function reads (`fast-sim`, `docs/Fast_Sim_Plan.md` §5b), not a second score field, and `Owner:36` holds reputation gained this tick. `joins.fresh_start` zeroes the f32 in the joiner's `GLOB` score row through `set_score`, but in `OWPR` it zeroes only `Owner:28`, so the `OWPR` copy of `Owner:32` and `Owner:36` stay as cloned. Find which copy the client loads, then join a civ cloned from a donor with nonzero reputation and read its corruption against a fresh civ's. **Fixed for joins** (`joins.fresh_start`, `test_fair_start.py`, 14 November 2026): the joiner's `Owner:32` is zeroed in `OWPR`, `inject_civ.REPUTATION_BACK` bytes before the civ name's length field and four words after the score, which is where `sim/load.py` reads it. A reload reads `Owner:32` back from `OWPR` (`docs/Fast_Sim_Plan.md` §5b), and `set_score` zeroes the score row's own copy, which the next boundary rebuilds. `Owner:36` has no located place in the save.

[x] **An injected civ gets no ships.** `SHIP`/`DYNO` records are galaxy-level rather than per-civ, so a starting colony ship is a separate job. Against civs that start with two, that is a real handicap at turn 1, the civ can build its own only if the donor's homeworld had a shipyard. **Fixed:** `starting_kit` adds `STARTING_SHIPS` hulls, two, as a generated civ starts with.

[ ] **Homeworld placement is one hardcoded policy.** New civs are placed as far from every claimed planet as possible, which is the right default for testing an AI (contact becomes something it has to earn) but not the only one worth having, random placement is what a real generator does and is the only way to test a hostile neighbour two systems away, and clustered or per-team placement follow from it. Wants a `--placement` option rather than a second function.

- [x] **A galaxy injected with a colonisation in flight plays on in the client.** The colony ships' `ROUT` sections come out byte-identical after injection, and across the tick archive the planet a colonise order names is the planet that changes hands two ticks later, 7 times out of 7. In the client (`join_acceptance.py k1`, 2 November 2026), DemoPlayer's two colony ships were sent from turn 11 of the demo galaxy and Joiner was added at the same turn; played 60 turns with and without the join, the near target #141 went to DemoPlayer on turn 13 and the far target #6 on turn 63 in both, with no divergence in any ship's orders, and Joiner's homeworld grew from 7 to 28. `k1load` opened the joined galaxy as Joiner, in the TestBed build, since this checkout has no `CosmicSupremacy_Player.exe`.

### Audit: is memory editing still needed for multiplayer? (August 2026)

**Verdict: no, not for state sync, with one confirmed gap and one caveat.** Both directions of the multiplayer loop now run through the save blob, and everything the memory-editing tools write is carried by it. What memory access remains is either test harness or a small amount of `.data` bookkeeping the blob does not serialise.

#### The blob is a fixpoint

A save was captured, loaded into a fresh client via the `.dat` path, and captured again. The two blobs are **byte-identical**, 117,877 bytes, 1,317 sections (`server/dev_tools/diff_saves.py`). Nothing in the blob is lost, reordered or regenerated differently by a round trip.

Note what that test cannot see: state that is absent from the blob *entirely* is missing from both captures, so it compares equal while still being lost. The gap below was found that way, not by this test.

#### Everything the memory tools write is blob-covered

Checked against the live client after a `.dat` load, with nothing but the blob to build from:

| Tool | Writes | Carried by the blob? |
|---|---|---|
| `set_population.py` | citizen vector `Planet:144/148/152` | **yes**, both homeworlds came back with 7 citizens |
| `set_facility.py` | `Planet:204` map, `Planet:208` count | **yes**, both came back with 3 facility types |
| `ai_player` order origination | `Ship:48` order object | **yes, and superseded**, `ROUT` builds it engine-natively |
| `advance_turns.py` / `fast_turns.py` | turn length `0x0080AA08` | n/a, turn pacing, and `GSET.turnlength` restored 3600 anyway |
| `snapshot.py` | whole process | n/a, test reproducibility |
| `patch_hide_next_turn.py` | UI byte patch | n/a, cosmetic |

The live census after the load, 538 `Planet`, 108 `Sun`, 4 `Ship`, 2 `ShipDesign`, 2 `Owner`, sums to **654**, exactly the object count in the `SAVE` payload's first dword. The turn counter came back as `2`, matching the `turn=` the save was taken at.

This also settles an asymmetry flagged earlier as unconfirmed: the loader adds `[0x00857C58]` to every non-zero object-reference id, and that global reads **0**, so ids can be written absolutely.

#### The gap: `.data` bookkeeping the blob does not serialise

The four homeworld customisation click counters at `0x00842AE4`–`0x00842AF0` all read **0** after a load, against a `GSET homeworld_changes` budget of 30, while the *effect* is present, the homeworld (planet 257) carrying space 450 against a 300 base. So the client believes nothing has been spent and re-offers the whole allowance. This was previously recorded here as worse than a duplicated allowance, on the reading that a zero-click confirm would reset the homeworld over whatever the server restored. **Measurement in September 2026 showed it is exactly a duplicated allowance, and that duplication is unbounded**, see the corrected TODO below.

**What is lost is narrower than it looked.** The counters are a record of what has been *spent*, and nothing in the economy reads them: rewriting them mid-game does not change the next tick. The customisation's whole effect lives on two per-planet fields that the blob does carry, space at `Planet:104` and the per-unit output rates at `Planet:96` (`PLPR+4` on the wire). So a pushed state comes up economically correct and only the allowance is wrong. The memory report's homeworld-customisation section has the controlled experiment.

The general shape still matters more than this instance: **game state living in `.data` rather than on an object cannot be in the blob**, because the blob serialises objects. Any other such counter has the same problem, and this one turned out to be a bookkeeping counter rather than a gameplay input, which is a reason to enumerate the class rather than to relax about it.

#### The caveat: the client never uploads state on its own

`SaveGame` at `0x0048B350` has exactly one caller in the binary, the Save/Load dialog at `0x0048B950`, which is reached through a message map, and the normal game has no such dialog. So a server cannot ask a client to submit its state; `client/dev_tools/trigger_save.py` gets a blob out by calling `SaveGame` in a remote thread. That is not memory *editing*, it invokes the engine's own routine and writes no game state, but it is still process injection. `patch_save_message.py` gives every shipped client a handler that saves on a window message instead (see "A save without a remote thread").

`STCO` was the candidate for a per-turn order upload. **It is not one.** It is one of the original client's 56 server commands, sent the moment a player sets a ship's course, and the original had no per-turn upload at all: see "The original client's online path" below.

- [x] decode the **`STCO` section and the path that uploads it** (`0x0056EBC0`). Decoded statically
  on 14 November 2026: `STCO` v0 is the "set course" command, sent when a player orders a ship, and
  the server's answer sets the order. It is not a per-turn submission, and the referee and the merge
  keep the save capture. What it changes for them is below.

[x] find the rest of the **state the blob cannot carry**. The played-vs-loaded comparison was run in
September 2026, and paired with a **functional** test, because a structural diff on its own cannot
say whether what it finds matters.

Two arms from one fixture: arm P played turn 2 to 22; arm L loaded the blob P captured at 22. Both
were then ticked onward and compared.

| comparison | result |
|---|---|
| P vs L at turn 22, in memory | 205 objects each; the human civ differs in one field, the AI civ in about thirty |
| P vs L at turn 32, blobs | 38,310 bytes each, 2 bytes differ |
| P vs L at turn 62, blobs | 38,662 bytes each, 4 bytes differ |

**Forty turns past the fork the simulation output is byte-identical**, apart from the `KNPL` field
described below. So whatever the blob fails to carry, the tick does not read.

What it fails to carry is derived rather than authoritative: the **engine AI's working block** on its
own `Owner` (`+196`, `+224..268`, `+408..412`, `+456..460`, `+488..492`, `+524..540`, `+580..584`,
`+604..608`, with `+1200..1236` going to `-1`), the **`ShipDesign` derived-stat cache** which
invalidates to `-1` on load, and the **`Planet:20` / `Planet:512`** appearance and render counters.
The human civ loses one field. The engine AI made identical decisions for 40 turns with its working
block zeroed, so that block is rebuilt per turn rather than accumulated.

The homeworld click counters at `0x00842AE4`–`0x00842AF0` remain a member of the class and are
covered separately below; `client/dev_tools/homeworld_clicks.py` reads and restores them.

Scope of the claim: one fixture, two civs, 40 turns, no combat and no diplomacy. A war is the obvious
next stress, since combat is where an accumulated AI state would most plausibly show up.

**The war stress was run, and the claim does not survive it (September 2026).** Two arms from one
fixture, 70 turns each: arm P played through a two-sided battle, arm L loaded the blob P forked from
and was ticked the same distance. Once the `Unnamed` star names are stripped the two blobs still
differ by **30 bytes**, and the difference is real rather than cosmetic. It is two dwords and two
ASCII digits in `NWDB`, one byte at `OWPR+0`, and a run in the `PLPR` of the planet the battle
happened over, where the played branch's growth series sits one step ahead. Ticking both branches 20
further turns leaves them nine bytes apart in LENGTH, exactly one nine-byte population record: the
battle planet holds 19 citizens down the played line and 20 down the loaded one. The difference
cashes out rather than washing out.

Four things pin it to the battle and each was measured rather than assumed. A control on the same
fixture with the same conscription, the same staging and the same ship movement, differing only in
that peace was written instead of war, came out byte-identical once names were cleared. A second
independent arming session reproduced both hashes. The two branches agree about what happened, same
news items, same turn, same categories and ids, same coordinates and the same two hulls lost, so the
disagreement is in derived state rather than in the outcome. And **the referee's own case is
unaffected**: loaded against loaded, which is what a referee does, agreed byte for byte across three
invocations of the same two-sided battle.

**It does not reach the turn loop, and the reason is structural.** Neither side of the loop is ever
in the played position. The referee loads a blob and advances exactly one turn. A player never
crosses a boundary at all, since `player_turn.serve` holds the clock at `0x0080AA08` after load and
the player build is the one without T1-T5, so no boundary fires in a session and no combat resolves
in one. Where it does bite is the AI-played galaxy: `duel.py` drives many boundaries in a single
client session with actuator writes between them, which is precisely the played arm of this
experiment. Such a galaxy cannot be captured, reloaded and continued as the same galaxy, and
per-turn measurements taken across such a run are not reproducible from the blob.

[ ] **Three of the 30 bytes are still unnamed.** The `NWDB` digits are the planetary-defense damage
percent in the two battle reports (strings 9 and 11): the loaded branch reads `12% (1 Citizen
Killed)` and the played branch `11%`. A rerun of the loaded arm on 14 November 2026 (war_fork.dat,
turn 110 to 180, the current `referee.tick`) reproduces it. The client formats that percent at
`0x00522EF0` from a double, truncated with `_ftol`, clamped to 0..100 and printed into the `planetary-defense damage:` line at `0x0076D63C` when
it writes the report, so the blob carries the text and nothing recomputes it on read. The same one
citizen killed is a smaller share in the played branch, which matches its `PLPR` growth series
sitting one step ahead before the battle. So the digit is a symptom: the divergence is in the
battle planet's population growth before the fight. The two dwords and the `OWPR+0` byte are not
yet named. Next step: diff `war_fork_L` against `war_fork_played.dat` by section, give the two
`NWDB` dwords and `OWPR+0` their field names, and find the earliest turn the planet's `PLPR` differs
between the arms.

#### The original client's online path (November 2026)

Read from the disassembly on 14 November 2026; nothing here has run against a server. The four
clients share every address. `client/dev_tools/online_commands.py` lists the commands from an
exe, and `server/tests/test_online_commands.py` pins the table.

**The original client is a socket client, and every player action is a command.** A galaxy pass
is base64 of `<KIND> <host> <port> <passhash> <player>`, and the client opens a TCP connection to
that host and port (`Socket.cpp`, `ws2_32`). Each action's routine follows one shape:

1. Online when the channel at `[0x0086F198]` belongs to the connection at `[0x0086F194]`, the
   connection is up (`0x00562CC0`) and `[0x0086F1A2]` is not 1: it opens a message (`0x00562030`),
   writes one section named for the command, sends it (`0x00561950`), blocks for the answer
   (`0x0056E930(1)`), and applies the answer only if it reads `SUCC` (`0x005612A0`). Nothing
   changes locally before the server answers.
2. Otherwise, when `[0x0086F1A0]` is set, it applies the action locally. This is the path every
   client in this project takes: a `.dat` argument sets the flag (`0x0056E7F0`), and so do the
   `standalone` option, a `TUTO`, `DEMO` or `TEBE` pass, and T4a in the Resurgence build.
3. Neither: nothing happens.

`[0x0086F1A2]` is set when a pass names port 4710 (`0x00577EF4`). `[0x0086F4F8]` is the Tutorial
being on; `STCO` calls the Tutorial's order check (`0x0057E670`) before anything else, and raises
Tutorial event 14 after a local Colonize order.

**`STCO` v0, set course** (`0x0056EBC0`, called from `0x00475A8B`, `0x0047C347` and `0x00575232`): a
`u32` ship id, the command record (`0x004E9CE0`), and the route when there is one (`Route::Write`,
`0x004E5310`; the table in §2 calls this call site "the ship writer"). The answer carries a `ROUT`
that the client reads (`0x004E6E90`) and sets on the ship (`0x004DA450`), then applies the command
(`0x004D9DC0`). Offline it sets the route it was given and applies the command directly.

**The wire is the save's own section format**: a tag, then a `u32` with the version in its top 6
bits and the length in the low 26. The client wraps each outgoing command in `CMND` v0, a `u32` and
the command's section compressed with zlib (`0x00561710`); whether that `u32` is the compressed or
the original length is not settled. A frame is accepted with a version up to 100 and a length up to
10,000,000 (`0x00561350`).

**What the server sends** is read by the dispatcher at `0x00573EA0`, polled from the main loop,
which sends `ALIV` when it has been idle:

| tag | handler | what it does |
|---|---|---|
| `TICK` | `0x0056DD10` | clears `[0x0086F1A1]`, the galaxy-is-current flag |
| `SAVE`, `UPDT` | `0x0056DDA0` | loads the galaxy with the loader a `.dat` uses (`0x0056D700`), sets `[0x0086F1A1]` and sends `RQTM` |
| `NEWS` | `0x00573940` | while `[0x0086F1A1]` is set, one `NEWS` record and a byte, added to the local civ's list (`Owner::AddNews`, `0x00543720`) and shown at once |
| `ALIV`, `RSMA`, `TIME`, `TRKP` | `0x0056E400`, `0x0056DCE0`, `0x0056E460`, `0x0056E620` | not read further |

**The 56 commands**, in address order. Those named were read or are named elsewhere in this report;
the rest are tags only.

`RQTM` `RSMA` `RQTN` `RQSV` **`STCO`** set course · `SLFC` `TGSD` `MOSD` `NWTR` `MDTR` `SEEN` `DISS`
`DLTR` `STCL` `HRPR` `NWGV` `DLGV` `MDGV` `STGV` `NWAD` `DLAD` `MDAD` `STAD` `NWSQ` `MDSQ` `DLSQ`
`STSQ` `RNGV` **`CUHW`** and **`CUCV`** the two customisation prompts · `SVUS` `PLRR` `STEL` `CNWH`
`RNFL` `SONA` `RNSD` `STPR` `STTP` `STRS` `ALIV` **`CHCZ`** `ChangeCitizenJobs` (§13) · `RTMI`
`CHNW` `TGST` `TGSS` `CRAS` `DLSD` `DLSC` **`SDNW`** a message or note · `TRRS` `CRSD` `DOSC`
`MCFL` `EDNW` **`CNCA`** the civ name, its checkbox and the arms id

**What this changes for the referee and the merge.** The original server was authoritative per
action: it saw each command as it was made, decided it, and the client showed only what came back.
There was no per-turn upload to recover. The other direction is the one this project already
uses: a server could push a whole galaxy as `SAVE`, which the client loads with the same routine a
`.dat` reaches. The project's loop, a client applying actions locally, a
capture at the end of the turn and a merge that reads the actions out of the difference, is the same
authority moved to the turn boundary, and nothing in the referee or the merge depends on `STCO`.
Two things follow:

- The 56 commands are the whole list of what a player can do. Every change the merge accepts
  should be the effect of one of them, and each command's offline branch, which is what produced
  the submission, is the rule to encode. A change no command can make is refused.
- A server that spoke this protocol would see each action as it happens, which is what "Messages
  arrive between ticks" needs. A connected client sends every command online and waits for the
  answer, so such a server has to answer all 56, not only `SDNW`.

- [ ] **Each merge rule names the command it stands for.** Done when every rule in `merge_orders.py`
  cites its command, and every command either has a rule or is noted as refused or cosmetic. Next
  step: read each routine's offline branch, starting with the 50 not named above.

#### The engine is deterministic (September 2026)

**A turn is a pure function of state plus orders.** Three separate process launches drove one fixture
from turn 2 to turn 22, each with the clock frozen at the target before capture, and produced blobs
of 38,190 bytes and 422 sections every time. The 20 turns are real work: **21,700 of 37,851 bytes
change over them**, 57% of the blob, and three new sections appear. Across the three runs every one
of those bytes agreed except a single dword.

This is what makes replay, audit and dispute resolution available, and it means a referee can be
**transient**: a turn can be recomputed on any host and checked against an archived hash.

#### A running client invents a default star name

`SUN ` payload, 36 bytes with no name:

    +0   u32  object id
    +4   f32  x
    +8   f32  z
    +12  f32  y
    +16  u32  zero in every sun seen
    +20  f32  a radius or magnitude
    +24  u32  name length
    +28  char name[length]
    then u32, u32

A blob carries an empty name, length zero. A client that has been running for a
while writes `Unnamed`, seven characters, and the section grows to 43 bytes. A
client that has just loaded writes the empty name back faithfully.

Measured September 2026 while testing determinism across a war: a played branch
and a loaded branch, 70 turns from one fork, came out 756 bytes apart, which is
exactly 108 suns times 7 bytes, and **byte-identical once the names are
stripped**. The same 756-byte difference appears with no war at all, so it is a
property of the client's display defaults rather than of the simulation.

The practical consequence is small and worth knowing: a galaxy captured from a
long-running client shrinks by that much on its first tick and is stable after.
`canonical.py` does not mask it, because masking a name field to forgive a
default would also forgive a change to it.

`Unnamed` is the default a running client writes into this field when the blob
leaves it empty.

### `GLOB`, which civ the loading client plays

`GLOB` carries one `u32` holding the object id of the civ the loading client will play. The same
blob stamped 198 comes up as one civ and stamped 202 as the other, UI included, which is what makes
per-player distribution a data operation rather than a per-player build.

**The field is not at a fixed offset.** `GLOB` holds a variable-length list of the players the
galaxy knows about, each entry a user id and a name, so the id moves as soon as any civ has met
another: `+40` with no contact, `+65` once one entry exists, with `GLOB` itself growing from 87 to
112 bytes. The stable landmark is the tag that follows it.

    ... u32 99999 ; u32 localPlayerObjectId ; 'TMGX' ...

`server/dev_tools/set_blob_player.py` finds `TMGX`, steps back four bytes, and checks the value
against the civs the blob holds before writing. A blob written at an assumed offset is rejected by
the client with an exception dialog.

Two other fields carry no part of this and are dead ends: the order of the two `OWNR` sections, and
`Owner:4`, the trailing `u32` of the `OWNR` payload.

At runtime the selection comes from a **TLS red-black tree of player slots**, the roster the testbed
galaxy join populates. `0x0052DE10` walks it and takes the first slot whose `+0x38` is non-zero,
reading the civ's object id from `+0x30`; `0x00537BF0` maps that id to a reference cell through the
map at `0x00857C7C`, and the cell is stored at `0x00857904`, which is the cell the setup-prompt
guards in section 6 read through.

### `DSGN`, a ship design on the wire, and what `SAVE+0` counts

A design is a self-contained subtree under the owning civ's `OWNR > DATA`.

    DSGN v4, 67 bytes for a typical design
      +0    u32     the design's own object id
      SDPR  section, v0
            u32     name length, then the name, unpadded
            six part lists, each a u32 count then that many u32 part ids:
                    chassis, scanners, engines, weapons, modules, shields
            u32     the owning civ's object id, at the end
      +1    u8      zero, counted by the DSGN length word

    the civ's design count, a u32 in DATA's own bytes before its first DSGN

**The part lists are in the order of the `ShipDesign` vectors** (`:128` chassis, `:152` scanners,
`:176` engines, `:200` weapons, `:224` modules), and a part id is the record's position in its
component table (Stat Tables §3). An engine fitted three times is listed three times. Read across
1,081 designs on disk in November 2026, and checked against the parts each design was built from:
a Colony Ship is `[0] [0] [0,0,0] [] [0] []`, a Shuttle with a Neutron Scanner, three Nuclear Drives
and a Colony Module, which fills the Shuttle's 120 space exactly; `b1` lists weapon 10, the Fusion
Bomb; the hand-made `e1x2` and `e1x4` list engine 1 twice and four times. `inject_design.read_design`
and `sim/load.py` read them.

[ ] The sixth list is taken to be shields, the one category left, but it is empty in all 1,081
designs on disk. Confirm by capturing a design with a shield fitted.

**The trailing byte is uniform.** Surveyed across every blob on disk in September 2026, 327 blobs
and 1015 `DSGN` records: every record is v4 with an `SDPR` v0 child, in all 1015 the byte after the
`SDPR` child is exactly one byte wide and zero, and every per-civ design count agreed with the
records found. A synthesised design that omits it is a byte short with its length word a byte low,
which the client reads as an edited design. Every design in the rehearsal galaxies carries exactly
one scanner, id 0, the ones a person made by clicking included.

**`SAVE+0` is the highest object id in use, not a count of objects.** The two readings agree
whenever ids are dense, and this galaxy's are not: it holds two gaps, so count and highest id differ
by two, and the field tracks the id.

| state | `SAVE+0` | objects | highest id |
|---|---|---|---|
| turn 5 | 205 | 203 | 205 |
| a design spliced in, ticked to turn 6 | 206 | 204 | 206 |
| ticked to turn 9, the hull built | 207 | 205 | 207 |

The client allocates a new object id as one past the object count. Splicing a design in without
raising `SAVE+0` leaves the engine's next allocation landing on the id that design is using, and the
galaxy then holds two objects under one id.

**A queue naming a design the galaxy does not hold is fatal to the whole galaxy, not to the order.**
The client starts, reads it and exits in about four seconds. A design and the `PROD` entry naming it
have to be carried or dropped together.

**Four `OWPR` bytes move alongside a new design and none of them is derived from it.** `+49`, `+77`,
`+78`, `+100` and `+110` were surveyed across 18 turns: `+49`, `+77` and `+78` also move on turns
where no design was created, `+100` is a `u32` the engine advances by 7 every tick on its own, and
`+110` is the only one the engine never rewrites across a tick. One player made the same design
three times with only the name differing and the fields disagreed across the three, so none is a
function of the design. What they are is not established.

### Hurry production, and the price the blob does not carry

Clicking hurry moves exactly three fields:

    PLPR+23, u32          production points accumulated, 110 -> 200
    PROD payload byte 0   0 -> 1
    OWNR, id_at+20, u32   credits, 10065 -> 9705

`360 = 4 * (200 - 110)`, reproducing the manual verbatim: four credits per production point left,
and the current production must be at least half finished. 110 of 200 is 55%.

**The total cost of an item is nowhere in the blob.** What is recoverable is the distance the
progress field moved between two states, and the price per point is fixed, so a hurry can be priced
from the two ends without knowing what the item costs.

### Filtering a galaxy, and what the engine hides on its own

**Object ids tile the galaxy contiguously, and a system's id is the first id of its block.** System
1 owns 1..7, system 8 owns 8..13, up to 193 owning 193..197, with civs and ships above at 198..209.
Deleting a `SOLA` in the middle punches a hole in that space; deleting from the end truncates it.

Whether a filtered blob loads is positional rather than a matter of how much was removed:

    drop 1 system, anywhere (1, 8, 14, 130)      loads
    drop [180, 186] / [186, 193]                 loads
    truncate systems >= 142, nine of them        loads
    drop [1, 14] / [8, 14] / [124, 130]          fails
    any projection keeping only 1 or 3 systems   fails

Hole size is not the axis either: the surviving `[180, 186]` hole is 13 objects and the fatal
`[124, 130]` hole is 11. **The failure is a deliberate bail rather than a crash**, exit code 1 about
four seconds in, with a minidump carrying no exception stream and every thread in a shutdown wait. A
successful load writes no dump, so there is no control to diff against.

Four causes were tested and none of them is it. Not `EXSY`: dropping 29 systems with the tables
untouched fails the same way. Not `KNPL`: its records are keyed by civ id, and dropping systems
removes no civs. Not `SAVE+12`, which reads 32 before and after filtering and changed nothing when
set to the surviving system count. Not a ship in transit: `SHIP` sections are children of `GLXY`
rather than of `SOLA`, so dropping a system can never drop a ship, and a blob whose removed planet
was the orbit of two ships still loads. Renumbering the kept systems into a contiguous block rather
than deleting them would turn every projection into the case that works; it has not been tried.

**Orders survive filtering.** Three uncolonised systems removed at turn 15, served to a real client,
ordered, captured and merged against the **unfiltered** authoritative blob, with an unfiltered
control beside it: both took four orders across four order types, dropped none, and produced
byte-identical merged results. The captures came back at the served system and object counts, 29 and
190 against the control's 32 and 209, which is what shows the client ran the smaller galaxy rather
than rebuilding what was removed.

**The engine hides more than a full-state blob suggests.** A client holding the whole galaxy still
draws only the planets in systems that civ has entered, ownership included, and another civ's ships
only within scan range, about 30 units. So what full-state distribution leaks is what a modified
client could draw, not what the stock one does, with one exception: in a system the civ has entered
and left, the stock client draws the live planets, so a player watches it grow. See "Each civ is
served the systems it has left as it last saw them".

### `NEWS`, how an engagement is established, and who is told about it

A `NEWS` payload is a fixed 44-byte record inside a civ's `OWNR` `DATA` block. What is settled is the
**turn** at `+8` and a **position** at `+32`, which is zero for items that have no place. An
engagement raises a positioned item on the turn it resolves, at the coordinates it resolved at.
`two_sided_war.py contacts` diffs them between two blobs, which is what turns "a ship is missing"
into "an event happened here, then".

**A missing hull is not evidence of combat**, which is why this record matters: a warship can be lost
in transit having met nothing, and a reading that took a missing hull for a battle was wrong in
exactly that way. Fleet speed is per hull and cannot be generalised from one, measured on
`cycle.dat`: `b1` covers about 3.4 units a turn and `f1` about 6.4, so over a 450-unit separation one
arrives inside a 70-turn window and the other does not.

What is **not** settled is `+20` and `+24`. Both are small ascending per-civ numbers and `+20` is
often `+24` minus one, so they read more like a chain than like a category and an id. An early guess
that `+20` was a category with fixed meanings did not survive a second galaxy: the value that carried
no position in one carried one in the other. Do not read meaning into them.

An engagement raises one item for each civ at it, each written from that civ's side. The `cycle.dat`
battle raised two on one turn at one position, one in each civ's `OWNR`. The two-player battle raised
one, for the defender only, and that was the special case: see the next item.

[x] **A losing attacker can be told nothing at all, and why is unexplained.** In the two-player
battle the attacker lost two warships at the defender's homeworld and received **no `NEWS` record of
any kind**, while the defender received one per engagement. Measured by counting raw `NEWS` sections
against the `OWNR` byte ranges rather than by trusting an attribution helper: two sections in the
whole blob, both inside the defender's range. Three explanations have been tested and all three fail.
**Not the merge**, since the same fixture ticked with no players, no submissions and no merge at all
produced the identical result, and the merge applies orders before the tick while the news is
generated during it. **Not presence**, since an unarmed attacker-owned ship placed at the battle site
was destroyed there and its owner still received nothing. **Not discovery**, since the attacker's
`EXSY` holds the defender's system. What remains is the difference between the two galaxies:
`cycle.dat` is turn 110 with developed empires, the two-player galaxy is turn 2 out of
`make_multiplayer_galaxy`. Worth settling, because a player losing a fleet and being told nothing
about where is a bad enough experience to be worth knowing whether it is the engine's rule or an
artefact of how these galaxies are made.

**It was the attacker's `Owner:4`, which was 0** (November 2026). `Owner::AddNews` (`0x543720`)
returns at once when `Owner:4` is 0, the sentinel that also stops the empire tick. Until 19 October
`make_multiplayer_galaxy` left seat one at 0 (`fix_owner_ids` now replaces it), and seat one,
`GoodGuy`, was the attacker. Otherwise the engine tells every side: the battle pass (`0x523AC0`)
groups objects by position and fights where two or more civs meet, and the report builder
(`0x5218E0`) registers the owner of every group there, its losses included, and gives each a type 2
item written from its side.

- **Corpus:** in all 37 battles in `sim/corpus`, every civ at the battle got an item, and every civ
  there holds a non-zero `Owner:4`.
- **A/B in the client:** `capture_live_attack_ship_t0123`, ticked once as captured and once with
  `GoodGuy`'s `Owner:4` set to 0. At 20 `GoodGuy` got its battle report; at 0 it got no item at
  all, not even its failed-build notice. `BadGuy` got its report both times.

**The original game told the attacker.** The manual's battle-report page explains a report as read
from one participant's side, listed first, with allies next and enemies last. The shipped TestBed
galaxies give the human seat `Owner:4` 0, a civ the client does not simulate, so for that seat the
original server, not the client, must have written the report. That last step is inferred: no
original server traffic carrying `NEWS` is captured.

**The referee sends nothing extra.** The engine writes every civ's report, and the served view keeps
each civ's own list (`news.own_view`). What the referee must guarantee is that no civ holds 0:
`referee.simulate_every_civ` gives such a civ the lowest unused id from 20 up before every tick,
which also restores its research and score (`server/tests/test_tick_owner_ids.py`).

[x] **Does a joined civ inherit its donor's `NEWS`?** `inject_civ.add_civ` clones a donor's `OWNR`
whole, and `NEWS` lives inside that `OWNR`'s `DATA` block, so the same reasoning that made a joiner
inherit the donor's `EXSY` applies here and nothing empties `NEWS` the way `joins` now empties
`EXSY`. Observed on 28 September in a real client: a civ joined at turn 11 of `galaxy_demo` and its
news tab was **empty**, which is consistent with the donor having no news to inherit rather than with
the field being cleared. The donor in that galaxy is seat one at turn 11 and has fought nothing. **It does, and joins now empty it** (`joins.forget_inherited_news`, `test_messages_merge.py`, 13 November 2026): the clone holds the donor's list, messages included, and the join empties it and clears the new-news flag. The strings stay in `NWDB`; no item names them, and the served view drops them.

The test is a donor that **does** have news: take a galaxy where a civ holds `NEWS` records, inject
against that civ, and read the newcomer's tab. If the items come across, a player joins knowing where
somebody else's battles happened, which is a smaller version of the `EXSY` map leak and the same
class of bug. If they do not, something clears it and that something is worth naming.

#### The `NEWS` record and player messages (November 2026)

Read from the `NEWS` v3 writer (`0x5355B0`) and reader (`0x535E70`), and measured in one Single
Player session on 9 November 2026 (`server/tests/fixtures/messages`).

| Offset, with empty strings | Type | Field |
|---|---|---|
| `+0` | `u32` | type: 1 a received message, 6 the sender's copy, 8 a player's note |
| `+4` | `u32` | flags, bit 0 "Mark As Important" |
| `+8` | `u32` | turn |
| `+12` | `u32` length, then bytes | title, empty when the title id names a string |
| `+16` | `u32` length, then bytes | body, the same |
| `+20` | `u32` | title id in `NWDB` |
| `+24` | `u32` | body id in `NWDB` |
| `+28` | `u32` count, then `u32` ids | the civs the item names |
| `+32` | 3 × `f32` | position, zero for a message |

Everything after the title moves by the title's length, and everything after the body by the
body's. Every item measured names its title and body by id and writes both strings empty, so the
unsettled `+20` and `+24` above are the two string ids. The reader turns a type 7 into a type 1
marked important, and a type 5 into `0x43`. A civ's list is in its `OWNR > DATA`, a `u32` count and
one `NEWS` per item, between the designs and `GOVS` (writer `0x539C90`, reader `0x5427B0`).

**`NWDB` v1**, after `GLOB`, holds the strings: a `u32` next id, a `u32` count, then per string a
`u32` id, a `u32` hash, a `u32` length and the text, Latin-1 with no terminator (writer
`0x5371C0`, reader `0x536CF0`). The hash is Jenkins' one-at-a-time over the bytes as signed chars,
with A to Z lowered and `\` read as `/` (`0x5E5230`); it matches all 39 strings in the captures.
Interning a string (`0x5370A0`) returns the id of an equal string already held, so two items with
one text share an id. A save holds the strings of every civ's news, not only its own.

**Sending a message** (OK handler `0x4BAB40`) needs a subject, a body and at least one recipient.
The body is refused above 18 rows, each line counting its length divided by 64, plus one. The
subject has no limit and neither field is filtered. The client appends a type 1 item titled
`Message from <sender> - Subject: <subject>` to each recipient's list, naming the sender, and a
type 6 item titled `Message to <recipients> - Subject: <subject>` to the sender's, naming the
recipients; both name one body string. `GoodGuy` sending `s1` added strings 9 to 11; `BadGuy`'s
reply was titled `Re: s1`. The text is stored as typed: `ë` as one Latin-1 byte, line breaks as
`\r\n`, and `<b>` and `|` raw. The news tab hides `<b>` (Robert, 9 November 2026), and copying
the text shows it, so the text controls read `<` as markup; the delimiters at `0x750E10` are
`|`, `>`, `<` and `%`. Marking an item important sets bit 0 of its flags. Online the client sends
`SDNW` v0 holding one `NEWS` instead.

**The new-news flag** is a byte in `OWPR`, 81 bytes before the civ name's length field. The reply
set GoodGuy's to 1, and opening the news cleared BadGuy's, along with the byte two after it.

**The reader trusts its input.** A count sizes a vector, and a string length sizes the string,
before anything is read, so a huge value fails the load. A length past the section's end leaves
the rest of the record misread. An id is not checked to name a civ.

- [x] **Player messages are carried.** `merge_orders.py` takes a message when the submitter's new
  type 6 item names civs it has met and each holds a matching type 1 item, renumbers the strings
  into the merged `NWDB`, stores the text with the markup characters swapped for `(`, `)`, `/`
  and `pct`, and sets each recipient's new-news flag; its own important marks and its read flag
  are carried too, and anything else is refused with a note (`test_messages_merge.py`). On
  9 November 2026 a round trip on `fixtures/messages/m0` went through the referee's path in the
  client: GoodGuy's message from its served view, merged, read by BadGuy from its view as
  `(b)hi(/b) from the merge/test 100pct` and `Zoë`, a reply restored and merged with no note, and
  the reply read by GoodGuy from its view with the news tab marked new (Robert). Done when a
  message sent each way on the sandbox arrives with the next turn. On the
  sandbox galaxy, referee staged at `00d7548` (13 November 2026, turns 161 to 163, deadlines
  brought forward): tmog66 sent `sandbox s1` to guest-test and `sandbox s2` to guest-test and
  Elder Race, guest-test read both on screen and replied, and the reply reached tmog66; every
  step was taken with no change dropped, and each civ's served view held only its own news. A
  message to two recipients is as inferred: each recipient's item names the sender and the
  other recipients, and the sender's title joins their names with `, ` (an unmet civ shows as
  `Stranger`). The client titles a message with the sender's chosen civ name (`tmog66-`); the
  referee at `00d7548` wrote the player name, and the merge keeps the client's title from
  `ab2e6d3`.
- [ ] **Messages arrive between ticks, close to instantly.** A message travels inside its
  sender's submission, so it reaches the recipient with the next tick, up to a whole turn later.
  Per tick is acceptable for now (Robert, 13 November 2026). The original did it over its socket:
  the send routine at `0x00575970` writes one `NEWS` (writer `0x005355B0`) into an `SDNW` v0
  command and waits for `SUCC`, and the server pushes a `NEWS` section (the record and a `u8`) to
  each connected recipient, whose handler at `0x00573940` adds it with `Owner::AddNews` and
  refreshes the news view with no tick. A `.dat` client takes the standalone branch and never
  connects, so the launcher's client sends nothing. A TestBed started on a galaxy pass
  (`SAND 127.0.0.1 47110 ...`, 14 November 2026) shows the connection dialog with
  `Changing connection settings...` and opens no socket in 45 seconds. That text is one state of
  the connection state machine on `[0x0086F1AC]` (around `0x00579B3D`); the only `connect` call
  is at `0x005F20EB`. Next step: decode which state reaches `0x005F20EB` and what the dialog
  waits for before it, then record the opening exchange against a local listener. Done when a
  message sent in the client reaches a recipient whose launcher is open within a minute, shows in
  the recipient's news without waiting for the tick, and is still in the turn the referee
  publishes.
- [x] **Each civ is served only its own news.** `fog.project` empties every other civ's news list
  and keeps only the strings its own list names, with the next id unchanged (`news.own_view`),
  and `fog.restore` puts them back ahead of anything the submission added, so a restored view is
  the authoritative blob (`test_fog.py`, `test_messages_merge.py`). Before, each player's copy of
  a turn held every civ's news and every string. Both served views in the round trip above
  loaded in the client and sent a message to a civ whose list they held empty. The privacy page
  says each message travels only in its sender's and recipients' copies; it is live once the
  site is deployed.

### `SERV`, the Inactivity penalty and what keeps it at zero (October 2026)

    SERV v5   u32  Owner:652
              u32  baseline, Owner:716, the turn the civ was last seen active
              u8   flag, Owner:712, set while the civ is active this turn

Read at `0x00545E14` and written at `0x0053C2E5`. `Owner:720`, the pending
flag `set_activity.py` writes in memory, is not saved.

The food tooltip's `Inactivity: -N%` grows a point a turn from 72 turns after
the baseline. At each boundary `0x00543B90` sets the baseline to the turn
before when the flag is set, then copies `Owner:720` into the flag. Nothing in
the client sets `Owner:720`, so a referee that loads a blob and ticks once
never refreshes a baseline, and every civ in a served galaxy decays from turn
73.

**The referee sets the flag for each civ whose submission it took**
(`server/activity.py`, from `referee.apply_orders`). CONFIRMED on a three-civ
galaxy at turn 11 with one civ flagged: after the tick that civ read baseline 11
and flag 0, and the other two read 0 and 0. A civ that stops playing decays from
72 turns after its last submission.

**Single Player's Next Turn sets both flags in memory for every civ**
(`launcher.mark_civs_active`). The engine copies `Owner:720` forward, so the
flags stay set. CONFIRMED on the Single Player galaxy over three turns: both
civs' baselines read 0, 1, 2. The snapshot this takes adds about a second to
each Next Turn.

**The boundary skips any civ with `Owner:4` = 0** (`cmp [esi+0x38], 0` at
`0x00543C28`), the same civ that never ticks research. A flag set on one is
neither applied nor cleared.

`python server/activity.py <blob>` prints every civ's baseline, flag and
penalty.

### `EXSY`, a civ's explored systems and what it calls them

    +0   u32  record count
    then, per record
         u32  object id
         u32  a second id, equal to the first in the rows seen
         u32  name length
         char name[length]
         ...  further fields, undecoded

A three-civ galaxy gave tables of 111, 159 and 266 bytes, one per civ, holding
`Unnamed` for things nobody has renamed and `BadGuy's HQ` for one that has been.

**When the engine refreshes a row** (November 2026, the Single Player chain). Every turn
the civ owns a planet in the system, and every turn any planet of the system is within the
scanner range of one of the civ's ships. A scout with a 30-unit scanner refreshed system 135
from a planet 19.3 away, then left it at turn 22 once the nearest planet was 32.6 away. A
refresh stamps the new turn and each planet's owner and population. The row's planets are
the system's in the order `SOLA` holds them. A civ whose `Owner:4` is 0 keeps its rows
frozen unless it is the local player, the id in `GLOB` that `set_blob_player` reads. The
DemoPlayer seat's rows froze at turn 2 in the referee's ticks, where another civ was local,
and refreshed in a capture where DemoPlayer was. Its research store stayed at 0 in both.

**The golden age's turns left are in `CVTR`**, the `u32` at payload `+12` in the header
before the trait pairs. After Doctrine: Golden Age completed it read 99, 98, 97 on
successive turns, and 0 before. `Owner:744` was the live object's counter, with 9999 as its
idle value.

**These are cached, not authored.** One civ's table gained `Neighbor's HQ`
purely from loading a turn, with that player having taken no action, so `EXSY`
records what a civ has seen rather than what it has decided. A rename is
authored on the object: `PLNT` own payload `+24` is a `u32` length then the
characters, and renaming is gated in game on owning the majority of the planet,
which makes a name authoritative galaxy data rather than a private label.

**Renaming a system writes the `SUN ` section's name**, at the same `+24` in its
own payload. Measured on a galaxy where one civ held five of a system's six
planets, which is the game's own gate. The renamer's `EXSY` cache picked the new
name up and the other civ's did not, so the other player keeps seeing the old
name until they observe the change.

#### `EXSY` drives what the client draws for a planet nobody owns (September 2026)

**The layout above is superseded by `server/dev_tools/exsy.py`, which walks the
whole table.** The undecoded tail is a per-planet array, and it is the part that
matters:

    u32   system count
    per system:
        u32 system id, u32 sun id, str sun name, u32 turn last looked, u32 planet count
        per planet:
            u32   owner civ id when last looked, 0 for none
            u32   population when last looked
            str   planet name

The two `u32`s that were undecoded here are decoded in "Each civ is served the
systems it has left as it last saw them" below.

`parse` raises unless the walk lands exactly on the end, and `build(parse(x))`
is byte-identical, verified 21 of 21 tables across 7 blobs.

**The client draws a planet's hover label and its system's 3D name from here,
not from the live record.** Found by wiping a civ and looking: with `BadGuy`
removed from a turn-180 galaxy, planet 384 read `owner = 0` with an empty name
and a blank `PLPR`, and its ownership icon disappeared, while hovering it still
returned `BadGuy's HQ` and the system still carried its 3D label. Both civs'
tables still held `BadGuy's HQ` against last-known-owner 660. Clearing those two
entries removed both draws, confirmed on screen.

So a planet's display is assembled from two sources at once, the live record and
the viewer's own remembered map, and only the viewer's explored systems show the
remembered half. That is visible only from a seat that has explored the system,
which is itself the confirmation.

[x] **Clearing another civ's remembered map is not fog-correct, and it is what
`wipe_civ.forget_civ` currently does.** Strictly, a civ that has not looked again
should keep remembering what it last saw: this section already records that
`EXSY` is a cache which goes stale on purpose, and that one civ held a planet as
owned by 206 while another had the correct 202. Wiping an abandoned player
currently clears every civ's memory of them, which is a deliberate trade made
because a planet anybody may now colonise, carrying a dead empire's name and a
capital marker, misleads a player about the board rather than about history. The
fog-correct version clears only the wiped civ's own table and lets rivals keep
theirs until they re-scout. Not attempted, and it needs a rule for what a rival
sees when they do look again. **Fixed** (14 November 2026): `forget_civ` clears
the wiped civ's own table, and in a rival's only the rows `fog.project` serves
live, a system looked at this turn or one the rival holds a planet in. A row
from an earlier look keeps the dead empire, and the rival's view shows it as
remembered. The rule for looking again is the engine's: it refreshes the row on
the next look, and the row then reads the planet empty. `--delete-owner` still
clears every table, since it refuses a galaxy where another civ names the wiped
one (`test_fog.py`).

The "206 against the correct 202" above is not staleness. In `galaxy_demo`
Neighbor (206) joined as a clone of BadGuy (202), before `joins` emptied a
joiner's `EXSY`, and its inherited table names BadGuy's capital as Neighbor's
own planet.

#### Each civ is served the systems it has left as it last saw them (November 2026)

Reported by a player: planets in a system they had visited and left kept
growing on their map, and new colonies appeared there. The manual's Recon
Screen page says scouted information is from the last time the system was
scouted and may be outdated, and that the sun's tooltip names that turn. So the
original game showed a left system as it was last seen.

**The two undecoded `EXSY` fields are that memory: per system the turn last
looked, per planet the population then.** Measured across 44 local blobs and
the tracked `admirals` and `diplomacy` fixtures. Every row whose turn equals the
blob's turn matches the live owner and population exactly, except in
tool-edited forks and turn-0 generator output, where the client had not yet
refreshed the table. In `diplomacy/d9.b64` (turn 181) BadGuy last looked at
GoodGuy's home at turn 34 and remembers populations 8, 0, 8, 0, 10, 0 against
live 15, 8, 14, 0, 17, 12; the two zeros are colonies founded since.

**A system a civ holds a planet in is not refreshed.** In `galaxy_demo`
DemoPlayer's home row reads turn 2 at turn 11.

**The client draws population and ownership from the live record, and the
referee published one blob for everyone.** So the engine held the right answer
and nothing served it.

`server/fog.py` serves it. A system is stale for a civ when its `EXSY` turn is
before the blob's turn and the civ holds no planet in it. Each planet there
that is or was colonised is served as remembered:

- remembered empty: owner 0, name cleared, no citizens
- remembered owned: owner and population from memory, every citizen a farmer
  of that owner; the name becomes the remembered one when the owner changed
- either way: no soldiers or facilities, the `PROD` of a never-colonised
  planet, and the owner's running counters in the `PLPR` header at 0

A planet whose memory names the viewer (the joiner case above) or a civ the
galaxy does not hold keeps its live owner and population, with the same
fields taken out. A planet is left live when the viewer has citizens on it or
its `PLPR` does not walk.

Nothing is added or removed, so a view keeps every object id, system and
planet. It passes `screen_submission`'s galaxy check, the high-water id is
unchanged, and the client's `min_suns` guard sees every sun. That is what the
system-dropping `project_blob.py` above could not do.

The referee projects each civ after the tick and publishes a view only for a
civ whose view differs (`publish(..., views=)`, recorded under `views` on the
galaxy document or in `state.json`). Every store and the relay serve a caller
by `turn_store.served_copy`; `player_turn.served_blob` asks the store.
Before the merge, `fog.restore` puts the authoritative planets back into each
submission. A civ cannot order another civ's planet, so nothing is lost, and
the merge then takes a fogged civ's orders exactly as an unfogged one's
(`server/tests/test_fog.py`).

**A view loads in the real client and holds the remembered planets.** BadGuy's
view of `d9` came up at turn 181, and client memory read GoodGuy's home planets
as populations 8, 0, 8, 0, 10, 0, with the two later colonies unowned.

[x] **Confirm on screen that the map draws them**, and that the planet sphere's
size follows population. **Confirmed** (14 November 2026) on BadGuy's view of
`d9` with the counters zeroed: the map draws GoodGuy's home system as
remembered, populations 8, 0, 8, 0, 10, 0, with the two later colonies unowned
and unnamed, and the sphere sizes follow those populations rather than the live
ones. The sun reads last scouted at turn 34, and a planet's tooltip shows
none of the live counters.

[x] **A view keeps the live header fields of a rewritten `PLPR`**: the output
rates at `+4`, food and production counters, and, when the owner is
unchanged, the facility list. Whether the stock client shows any of them for
another civ's planet is not known. **Fixed** (14 November 2026): every colonised
planet in a stale system is served as remembered, changed or not. The food
store (`+15`), production progress (`+23`), recruitment rate (`+27`) and
military store (`+32`) read 0, every citizen is a farmer of the remembered
owner, and there are no soldiers, no facilities and an idle queue. The output
rates, food store size and food per soldier stay, since they belong to the
planet and the recon screen shows its base outputs. The manual's Planet-View
screen covers only planets the player owns (`test_fog.py`).

[ ] **A rival's planet in a system the viewer is in is served live**, counters,
queue, soldiers and facilities included, and so is every other civ's `OWNR`:
treasury, research, designs and its own `KNPL`. Whether the original server
sent another civ's running state at all is not known; the client's screens
show it only after a scan.

[ ] **A remembered planet keeps four live bytes**: the byte between its soldiers
and its facility list, the word after the facility list, and the governor id in
the `PLNT`'s last `u32`. None is decoded.

[x] **A caller without a seat is served the whole turn.** Under `first-use`
that is every player before their first submission, and any signed-in user who
asks. An older turn is also served whole, since views are kept for the current
turn only. **Fixed** (13 November 2026): the referee publishes, beside each
civ's view, `turn_store.PUBLIC_VIEW`, the turn with every civ's news withheld,
and the relay sends a caller with no view of its own there. The store keeps the
previous turn's views, so a launcher that asks for a turn as the next is
published still gets its own, and the relay serves a turn whole only while it
is current and was published with no views. Any other turn is refused
(`test_relay_function.py`). The referee has to be restaged before the relay is
deployed: a relay that refuses earlier turns while the referee still deletes the
previous turn's views would stop a launcher caught in that window.

[x] **The folder store and `turn_server` do not enforce views.** A folder
store's player reads the files directly, and `turn_server` serves every caller
the turn. **Fixed** (14 November 2026): one rule, `turn_store.served_copy`,
picks the copy for every store and the relay. A civ the views name gets its
own; a roster civ they do not name gets the turn, which is its view; any other
caller gets the view without news. A launcher reads through
`served_turn`. `turn_server` takes the views as `POST /view/<n>/<civ>` before
the turn, serves `GET /turn/<n>?civ=` by the rule and the referee's
`?whole=1` whole, and a folder store keeps the previous turn's views as the
relay does. The relay used to send a roster civ the views do not name to the
view without news, which withholds that civ's own news; it follows the rule
now, and the referee publishes the view without news whenever it publishes any
view (`test_views.py`, `test_fog.py`, `test_relay_function.py`). The relay has
to be redeployed for its half.

`turn_server` has no authentication, so a caller who names another civ gets
that civ's view, as it can already submit as any civ. A folder is readable
whole by anyone who can open it; the views keep an honest launcher to its own.

#### The trailing dword of every `KNPL` payload is unreliable

**One field per civ, immediately before that civ's `EXSY` header, is not dependable data.** Measured
across six captures:

| | played | loaded from a blob |
|---|---|---|
| human civ | `0x004DC7F6`, stable across every run | `0xFF4DC7F6` |
| AI civ | `0x09F63E88`, `0x0975F050`, `0x09F60C58`, `0x09F68FF0`, a different value every run | `0xFFF63E88` |

The **low three bytes are carried faithfully** through a save and load; the **top byte** reads `0x00`
or `0x09` in a client that played and `0xFF` in one that loaded, whatever the blob held. The AI civ's
low bytes also vary between otherwise identical runs, and that is the only nondeterminism found
anywhere in the format.

**The dword is the last record's custom colour** (`KNPL` record `+32`, below). The record's
constructor (`0x532E60`) never sets it, so a civ that never picked a colour carries whatever was
in memory, and the reader forces the top byte to `0xFF` (`0x533365`). The record boundary is
where the reader puts it: the colour is one `u32`, and the top byte is its alpha.

Consequences. **Any blob comparison has to mask this dword**, or two honest computations of the same
turn look different, which is exactly what a canonical hash must not do. The
three-bytes-plus-a-top-byte split is suspicious enough that the record boundary may be off by one,
with the final byte a flag in its own right. And it is worth sweeping the other sections for the same
signature, which is cheap now that it is known: a field that changes between two otherwise identical
runs, or whose top byte becomes `0xFF` after a load.

[x] `KNPL` is worth decoding properly rather than piecemeal, for this field. The second reason
recorded here, that a `KNPL` still naming removed planets is why a fog-filtered blob fails to load,
is **falsified**: its records are keyed by **civ** id rather than planet id despite the name, and
dropping systems removes no civs. See "Filtering a galaxy" below. **Decoded** (14 November 2026):
`server/dev_tools/knpl.py` reads and writes the table whole, and every tracked fixture's 78 tables
build back byte for byte (`test_knpl.py`). A record is the map key, then the members the reader
`0x5332E0` walks: the civ's handle (the same id), the turn of first contact, the stance as a
`u32`, a `u8` set on the declarer's side when war is declared, a `u16` and a `u8` set on the
declarer's side in the war's first tick, an `i32` that is -1 until war and then 100, a `u16` set
with them (149 in `d8`), a `u16` and a `u8` that read 0 everywhere, then the three display flags
and the colour. First contact (`0x534F90`) passes the current turn and the opening stance: war
under `waronly`, by team in a team game, else neutral.

[ ] **Name the four war fields** at `+17`, `+19`, `+20` and `+24`. The captures place them on the
declarer's side and the war's first tick, which fits the manual's "declared the war and attacked
first" reputation rule; the code that reads them is not found.

[ ] **`canonical.py` masks only the last record's colour.** Every record's colour is unset until
a player picks one, so a civ that meets its second rival in a tick carries unset bytes in a record
that is not the last, and a recomputed turn can differ there. Masking every record's colour changes
the stored hash of every galaxy with three or more civs, so it waits for a hash version.

[x] **NOT A DATA-LOSS BUG. It is an unbounded re-grant exploit (measured September 2026).** This
item previously read "BUG, DATA LOSS, confirming the customisation popup overwrites server-restored
homeworld state" and called it the highest-severity defect in the save-push path. Both halves of
that were wrong, and the correction came from working the dialog rather than the disassembly.

*There is no zero-click confirm.* The homeworld popup's buttons are **OK** and **Decide Later**, and
OK is not available until points have been spent. The accidental path is Decide Later, and Decide
Later **writes nothing**: a full byte diff of both `Owner`s and both `Planet`s across it came back
clean apart from `Planet:512`, which drifts on its own between snapshots and is a render counter.
It also suppressed the prompt for at least the next two turns.

*A confirm does not reset anything.* The commit writes only the slots that were spent on and leaves
the others alone. On a pushed state carrying `[62, 30, 40]`, spending 15 production and 15 science
produced `[62, 45, 55]`: food untouched at its customised value.

*What it actually does is add, without bound.* Capturing that state, pushing it again, and spending
30 more on production produced `[62, 75, 55]`, so the rule is `current + clicks`, not
`base + clicks`. The full 30-point allowance is offered again on every push. A player who reloads
repeatedly can drive a homeworld's rates arbitrarily high. Space stayed 350 across both commits, so
the flat `+50` is not re-applied per commit either.

*The civ-trait popup does not touch any of it.* Both `Owner` objects were byte-identical across the
homeworld commits.

**Severity depends entirely on the server architecture, which is the point worth carrying forward.**
Under a server that stores what a client sends, this is fully exploitable and rates climb every
turn. Under the order-merge design in `Multiplayer_Turn_Sync_Design.md`, `Planet:96` is not on the
order whitelist, so the server rejects the change and the exploit is structurally contained; what
remains is a cosmetic desync between what the player sees and what the referee computes, until the
next push overwrites it. Suppressing the popup is still worth doing, and it is no longer urgent.

*Since October 2026 the merge carries one confirm per civ*, so a player's homeworld and traits
survive into the next turn, and the prompt stops coming back. `merge_orders.setup_orders` takes a
confirm only when the served flag is clear and the submitted one set, and only as the dialog could
have made it: the homeworld rates in 25..80, space as the floor of 350 plus whole five-point
clicks, the trait values the dialog's table gives, each spending exactly `GSET.homeworld_changes`
or `GSET.civilization_changes`. A second confirm, or a change with no confirm, is refused with a
note, so the re-grant stays contained.

The **civ-trait** commit, read out of the client in October 2026, overwrites rather than adds:
`0x00498EC0` writes each trait as its value for the clicks spent on it, so a second commit replaces
the first instead of stacking. Its rules (the click range, value per click count and cost of each
trait) are in `merge_orders.TRAIT_RULES`, and the trait values are saved in `OWNR > CVTR` as
(index, value) pairs after a 20-byte header.

[x] stop the **"Customize Your Home World" popup reappearing after a loaded save**, the trigger
behind the bug above. Resolved October 2026: the gate is `Owner:359`, a flag the popup's own OK sets
and `OWPR` carries, and the popup ignored it only because patches T2/T3 NOPped both of its guards in
the Resurgence client. The guards are restored (see "Patches 12–17"), and the referee carries a
confirm into the next turn. What follows is the investigation as it stood.

Observed on the first successful `.dat` load: the galaxy came back correctly,
workers, ships and settings all carried over, but the client re-offered homeworld and civ
customisation a second time, which on its own would also let a player bank a fresh allowance on every
pushed state.

The cause is that **what marks customisation as already spent lives outside the save blob**. The four
homeworld click counts are `.data` globals at `0x00842AE4`–`0x00842AF0`, not fields on any object, so
nothing in a `SAVE` blob can restore them and a fresh process starts them at `0`, measured directly:
all four read `0` after a load while the homeworld already carried space 450 against a 300 base. The
same question applies to the civ-trait allowance. `GSET` carries the budgets, `homeworld_changes`
(30) and `civilization_changes` (5), but a budget is not a record of what has been used. Three
things to separate: whether the popup trigger is the per-tick check at `[esi+0x4988]` that
`FUN_0x496830` reads (see the `listcivnames` notes in `cs_server.py`, where an empty `coaid` also
leaves the civ permanently "unconfigured"), whether the spent counts are supposed to come back from
`OWNR`/`CVTR` rather than from `.data`, and whether the real server suppressed the popup by answering
`listcivnames`/`listcoa` differently once a civ was configured.

Deferred deliberately: it does not block order push, which is confirmed working. But treat any pushed
state as re-opening the customisation window until this is settled.

---

## 2a. Galaxy settings, the `GSET` block (August 2026)

**Every save blob opens with the galaxy's full configuration, and it is the closest thing we have to the original server's galaxy-creation form.** 32 settings, decoded by `save_parser.parse_gset` and dumped by `server/dev_tools/set_turnlength.py`. This matters beyond curiosity: a replacement server has to *decide* all of these when it creates a galaxy, and until now we have been inferring the game's constants from memory one at a time.

**Six of them independently confirm numbers this project measured by completely different routes**, which is the strongest evidence that the block is what it appears to be:

| setting | value | independently measured as |
|---|---|---|
| `planetspersystem` | `(4, 6)` | 4–6 planets per system, counted across 108 systems |
| `startcredits` | `200` | `Owner:8` reads 200 at turn 0 |
| `colonyships` | `2` | every civ starts with exactly two colony ships |
| `homeworld_properties` | `[300, 32, 30, 40, 7]` | the customisation base table at `0x02CA8410` = `[300, 32, 30, 40]`, space, food/farmer, production/worker, science/scientist |
| `homeworld_changes` | `30` | the 30 increments the homeworld popup distributes |
| `tech_multiplier` | `800` | `GetScale` `0x0054A6F0` returns 800; `research.py`'s `COST_SCALE` |

The full block, from a live galaxy:

```
name  ''            speed 0        team 0       xp 0          sandbox 0    2d 0
rank (0, 999)       maxusers 100   density 0    sectorsize 200
turnlength 3600     primetime (-1, -1)          primetime_turnlength -1
startticks (-1, -1)
homeworld_properties      [300, 32, 30, 40, 7]
regularplanet_properties  [150, 290, 25, 40]
juicyplanet_properties    [220, 310, 32, 42]      juicyplanets 2
planetspersystem (4, 6)   colonyships 2           startcredits 200
tech_multiplier 800       corruption_multiplier 100   reputation_multiplier 100
colonymodule_multiplier 100   hse_multiplier 100
homeworld_changes 30      civilization_changes 5  score_breakeven 20
premium 1   waronly 1     autoattack 1
```

**Turn length was a per-galaxy choice, not a constant.** Galaxies ran anywhere from minutes to hours per turn, which is what made the game playable across time zones, the design the README describes as "long turn based play (hours between ticks)". `turnlength` is that choice in seconds.

**`primetime` and `primetime_turnlength` are a scheduled fast window.** A galaxy could run at its normal slow rate most of the day and switch to a much shorter turn during peak hours, early evening, when most players are home and want to see things happen. `primetime` is the `(start, end)` pair and `primetime_turnlength` the rate inside it; all three read `-1` here, meaning unused. Not needed for single-player or AI work, recorded because a faithful server has to implement it and nothing else documents it.

[x] **Not yet mapped to behaviour:** `speed`, `density`, `rank`, `startticks`, `premium`, `score_breakeven`, `hse_multiplier`, `colonymodule_multiplier`. **Mapped** (14 November 2026) from the callers of `get_setting` and the original site's galaxy pages in the wayback mirror; the index follows each name:

- `speed` (1): a speed-galaxy flag, read as a byte at 18 sites. In a speed galaxy the turn-length refresh `0x52D0E0` takes its length from `SpeedTicks.txt` beside the client or at `c:\`, 3600 by default, and timers shorten: 50 becomes 25 at `0x55C720`, and `0x54B166` halves a value. The early-game window in which battle earns no reputation (`0x518480`) applies only when it is 0.
- `density` (9): selects a column of five tables at `0x767F08` for the galaxy generator (`0x4D25A0`, beside `GalaxyNebula`): 0 gives (8, 10, 1, 3, 11), 4 gives (2, 3, 2, 6, 5). What each of the five controls is not traced.
- `rank` (6): the player ranks allowed in, (0, 999) for all; the site showed (0, 0) as "Unranked Players Only". A minimum above 0 makes the galaxy ranked, which the welcome text states (`0x546430`). Below content version 170, a ranked galaxy or one with more than one team offers a new civ the homeworld and trait prompts.
- `startticks` (12): a fast opening, (tick, seconds): before that tick the refresh uses the shorter turn when it is above 60 (`0x52D497`).
- `premium` (26): premium features for every seat. From content version 170 it alone decides whether a new civ is offered the homeworld and trait prompts (`0x537C80`, `0x537D00`), and it gates custom traits (`0x413AA0`), the hyperspace energy grid screen (`0x4254D0`) and doctrines in the research table (`0x54A970`).
- `score_breakeven` (27): the bias on the score ratio, the enemy's score times (100 - breakeven) / 100, in battle (`0x51ED50`) and the reputation rule (`0x518480`). The site's galaxies show 20 and 35.
- `hse_multiplier` (30): scales hyperspace energy by value / 100 (`0x5251E0`).
- `colonymodule_multiplier` (28): multiplies the colony module's price, which grows with what the civ holds (`0x516580`; the design screen `0x4408A0` shows it).

Our galaxies carry `speed` 0, `density` 0, `rank` (0, 999), no `startticks`, `premium` 1, `score_breakeven` 20 and both multipliers at 100.

[ ] **The `GLOB` loader also writes settings.** `0x52DA20` sets the content version and settings 0, 1, 2, 4, 5, 7 and 8 (`name`, `speed`, `team`, `sandbox`, `2d`, `maxusers`, `turnlength`) from values it reads, some only past a `GLOB` version. Which versions carry them, and whether `GSET` or `GLOB` wins at load, is not traced. `set_turnlength.py` writes `GSET` alone and is honoured, so for `turnlength` `GSET` wins or `GLOB` agrees. A tool that changes any of the others writes both until this is settled.

**`civilization_changes` is the civ-trait dialog's budget**, confirmed October 2026. Settings live in a vector in registration order, read by `get_setting(index)` at `0x004D4320`; `civilization_changes` is index 25. Its two readers are `0x00498F30`, which returns the budget less the clicks spent and feeds both the dialog's "You have N changes available" line and its OK handler, and `0x00537D00`, which marks a new civ's traits as already chosen (`Owner:360` = 1) when the value is negative. A turn-0 save with the value raised from 5 to 7 opened the dialog reading "You have 7 changes available".

[x] **`corruption_multiplier = 100` is the first handle on corruption we have.** Corruption has resisted location in memory entirely, see the memory report, and here is a galaxy-level knob for it. Changing it and watching what moves is a far cheaper search than diffing planets, and it is the same trick that finally located `Planet:368`: make the thing vary. **Answered:** the corruption function `0x0053A0D0` scales its result by `corruption_multiplier` / 100 (`docs/Fast_Sim_Plan.md` §5b).

- [ ] **The sandbox turns `waronly` off** (Robert, 14 November 2026: the sandbox will not be war-only). `waronly = 1` allows no alliances. Neither `galaxy_tool.py` nor `make_multiplayer_galaxy.py` writes `GSET`, so a new galaxy keeps whatever its generated blob carries. Decide `sandbox` at the same time: it is the fifth `GSET` entry, and `GetGameOption(4)` gates the client's build-up protection in the declare-war validator at `0x004BDE50`, 300 turns from `0x0076F378`. The original Newbie Sandbox Galaxy ran 10-minute turns, so 300 turns was about two days; at 4-hour turns it is 50 days. The client enforces protection only in its UI, and `merge_orders.py` refuses diplomacy outright, with a note naming each treaty and stance it dropped. Done when the sandbox runs with `waronly = 0`, an alliance between two civs survives a tick, and its `sandbox` value is chosen on purpose.

**`turnlength` drives the game's clock, and the engine re-applies it AT EVERY TURN BOUNDARY.** A galaxy set to 75 was observed writing 75 back over a driven 15 at each boundary, so a galaxy configured above the floor runs at its configured rate with nothing driving it, `ai.py --drive` is only needed to go faster than the game wants to.

Two things make this easy to get wrong, and both cost this project a false conclusion:

* **At LOAD the live value at `0x0080AA08` reads 3600 regardless of the config.** Reading it there and stopping says the setting does nothing. The refresh runs at a turn BOUNDARY, and on a 3600-second galaxy the first boundary is an hour away, so nothing can be observed without driving turns first.
* **Below 60 the value never appears at all.** Two sites in the binary clamp it, `cmp esi, 60 ; mov [0x0080AA08], esi ; jg skip ; mov [0x0080AA08], 60`, so a galaxy configured at 10 presents as 60 every turn, which reads like the engine ignoring the config rather than honouring it and rounding up.

- [x] **The in-game countdown disagrees with the launcher.** It is tied to neither the config nor the live value: a blob carrying `turnlength = 43200` came up with `0x0080AA08` reading 3600 and the UI showing 45 minutes. Nothing was found that reads back what the display is derived from, so the launcher's deadline is the only clock to trust. Either find what the UI reads and feed it the server's deadline, or hide or replace the in-game countdown. Done when the in-game clock matches the launcher's deadline for a whole turn, or no longer shows. Hidden, 8 Nov 2026: the status bar is built from format strings in `.rdata`, each read once in the function at `0x00405D00`, and `client/dev_tools/patch_status_bar.py` cuts the multiplayer client's to "Turn: %d" and blanks its Connected and Disconnected label (patches 42–45). The build applies it to `CosmicSupremacy_Player.exe` only. Seen on screen in 0.5.0.5.

**60 seconds is therefore the engine's real minimum turn**, which is worth knowing independently: the original game had no reason to support anything faster, and a training loop does. Lowering it is one byte per site (`3c` → `01`), keeping the clamp and moving its floor.

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

### What the client's own generator produces (September 2026)

**A generation does not hand its two civs equal worlds.** The second civ's homeworld reads the same
two `PLPR` bytes in every generation captured here, `+4` = 32 and `+11` = 44, while the first civ's
read 62/94 or 42/194: the first carries the homeworld customisation the setup screens apply and the
second gets the engine's opponent default. `PLPR+4` is `Planet:96`, the per-unit output rates, and
it decides games. Eight turns with no orders from anyone grew seat one from 7 citizens to 9 and left
the other seats at 7; with the bytes levelled the same run gives 9, 9, 9. A civ added by
`inject_civ.py` inherits whichever of the two it was cloned from.

**The second civ's first ship starts under a Scout order**, type 2 with an 82-byte `ROUT`, at turn 0
before anyone has played, in all five generations captured. A ship advancing along it looks exactly
like a ship being given an order, unless the `DYNO` is compared against the state as served.

**The engine issues no orders for a civ during a tick.** Measured on a galaxy where every ship
starts with order type 0, no `ROUT` and has-orders clear: eight turns, three civs, nothing
submitted, and no ship gained an order, no production queue changed, no research field was set. The
only movement in the whole blob was population growth. Stamping the tick client as the second civ
rather than the first gave the same answer, so it is not an artefact of which seat the client plays.

**A civ nobody plays therefore coasts and then stalls.** Over 40 turns with no orders: population
grew on every planet, every new citizen went to farming and nothing rebalanced, the queued facility
completed and the queue went to the empty marker and was never refilled, one civ held its research
topic and the other had none and never chose one, and nothing was built or settled. Governors and
admirals are the original's answer to that and they run rules a player wrote.

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

### There is no opponent AI in the binary (September 2026)

333 RTTI class names cover the whole game model, `Owner`, `Planet`, `Ship`, `Fleet`, `Production`,
`Treaty` and `ShipDesign` among them, and not one of them is an AI. The only decision machinery is
the 57 `Governor*` and `Admiral*` classes, which run rules a player wrote. Absence from RTTI is not
proof, since a non-polymorphic AI would leave no type descriptor, but every other system here is a
polymorphic class and the decision machinery that does exist is richly so. This agrees with the
measurement in section 3 that a civ nobody plays receives no orders during a tick.

---

## 6. Client Patching (EXE Modifications)

The original `CosmicSupremacy.exe` connects to the production server infrastructure which has been offline for years. To run the game locally, 67 bytes were modified across 11 patch sites, no code was added or removed, only existing values were overwritten in place.

### Patch 1, Connection-validation bypass (1 byte)

| Offset | Original | Patched | Effect |
|---|---|---|---|
| `0x0017926c` | `74` (JZ, jump if zero) | `EB` (JMP, unconditional jump) | Bypasses a server-validation branch so the client proceeds without a live connection check |

### Patches 2–4, Network redirects (53 bytes)

Two null-terminated hostname strings and one hardcoded IP in `.rdata` were overwritten to point to localhost:

| Offset | Original | Patched |
|---|---|---|
| `0x003776e0` | `www.cosmicsupremacy.com` (23 bytes) | `127.0.0.1:8888` + null padding |
| `0x003776f8` | `cosmicsupremacy.com` (19 bytes) | `127.0.0.1:8888` + null padding |
| `0x00378b98` | `xx.xxx.xx.xxx` (14 bytes) | `127.0.0.1` + null padding |

### Patches 5–11, Save/load validation bypasses (13 bytes)

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

### Patches 12–17, Turn pipeline bypasses (T1–T5, 14 bytes)

Four patch sites bypass server sync checks in the turn pipeline so turns can fire without a real game server. These are applied only to the Resurgence EXE (not TestBed).

**`CosmicSupremacy_Resurgence.exe` is `CosmicSupremacy_TestBed.exe` plus exactly these 14 bytes and nothing else**, measured by a whole-file comparison in October 2026. The two builds are otherwise identical, so the choice between them is exactly the choice of whether a client may compute a turn.

| file | VA | TestBed | Resurgence | what it is |
|---|---|---|---|---|
| `0x16CFF0` | `0x0056DBF0` | `80 7C 24 04 00 75 0C` | `B0 01 C3 90 90 90 90` | a predicate rewritten to `mov al,1; ret`, so a sync check always passes |
| `0x17701A` | `0x00577C1A` | `51 7C 85` | `A0 F1 86` | an operand repointed from `0x857C51` to `0x86F1A0` |
| `0x17702A` | `0x00577C2A` | `74 58` | `90 90` | a galaxy-join branch removed |
| `0x17902D` | `0x00579C2D` | `74 0E` | `90 90` | a load-path branch removed |

**T2 and T3 were removed in October 2026.** Until then the build also NOPped `0x16D4EF` (`0F 85 94 00 00 00`, `JNZ 0x56E189`) and `0x16D533` (`74 54`, `JZ 0x56E189`), the two guards of `0x0056E0D0`, the routine that decides whether to offer the homeworld prompt. They were never on the turn pipeline. With both NOPped the prompt ignored its own flag, so it came back at every launch and every hour of play however often it was confirmed, and each confirm added another full allowance (see the re-grant measurements in the memory report). The bytes are TestBed's again, a client with the turn advance still ticks, and a save whose civ has confirmed opens with no prompt.

### The three one-time setup prompts (September 2026)

Each is a Win32 dialog resource, not an in-engine overlay, so the main window's title still reads `Galaxy Map` while one is up. `client/dev_tools/list_dialogs.py` enumerates them by walking the process's windows, since the title cannot be trusted.

| id | title | decision routine | offered while |
|---|---|---|---|
| 210 | Customize Your Home World | `0x0056E0D0` | `Owner:359` is 0 and `0x00508C60(session)` finds a homeworld |
| 218 | Customize Your Civilization | `0x0056E190` | `Owner:360` is 0 |
| 225 | Pick your Civilization Name and Coat of Arms | `0x0056E700` | `Owner:384`, the civ name's length, is 0 |

All three are called in turn from the main loop at `0x00579C3D`, and each also waits while another of the three is open. Each keeps a real-time stopwatch (`0x86F2F0`, `0x86F308`, `0x86F320`; `0x005E3360` restarts one at an offset): a prompt is offered once 10 seconds have passed since the client started, and when it is shown its stopwatch is restarted 3600 seconds in the future, so a prompt whose gate stays open comes back on every launch and an hour after each showing.

`0x0052A8D0`, the "session" those guards read, is **the local player's `Owner` object**: it loads `[0x00857904]`, dereferences it and returns the object address minus `0x2C`. Since the reference cell holds the object address minus 8, a guard reading `[session+0x1B4]` is reading `Owner:384`.

**`Owner:359` and `Owner:360` are one-byte flags, set by the prompts' own OK.** `0x005716E3` sets 359 and `0x00571878` sets 360, on the offline path of each OK handler (`[0x86F1A0]` set and no live server connection). The online path sends `CUCV` (218) to the server and sets nothing locally, leaving the flag to the server's reply. Both flags are written to `OWPR` by `0x0053B230`, after the research fields: the homeworld flag 29 bytes past the end of the second research field and the civ flag 30. `0x00537D00` derives 360 from galaxy settings for a new civ and for an `OWPR` older than version 12.

**`Owner:384` is the length of the civ name, not a count of records.** The chosen name is a VC9 `std::string` at `session+0x1A0`, whose size field is `session+0x1B4`, so `OWPR` is `138 + Owner:384` bytes because the name's characters are written into it. `0x00538470`, the civ's display name, returns that string when the length is non-zero and the player name otherwise. Prompt 225 is therefore offered until a name has been chosen. Measured across saves: `testname4` stores 9. Writing the length without the characters does silence the prompt, and it is still the wrong fix, because it fabricates a name. Returning from `0x0056E700`, one byte, silences it without fabricating anything; a patch did that for the player client until the referee could carry the dialog's choice.

**Prompt 225's OK stores three things through `0x00579230`:** the name, the "Reveal True Player Name" checkbox and the coat-of-arms id. Online it sends them as a `CNCA` command and changes nothing locally. Offline it trims spaces and tabs, then refuses a name outside 3 to 30 characters ("Civilization Name needs to be between 3 and 30 characters long") or one that matches another civ's player name or chosen name ("A similar Civilization Name already exists"). The match ignores case: `0x00404B30` upper-cases both names with `toupper` before comparing, and the civ's own player name is not checked. A name it accepts goes to `civ+0x1A0`, the checkbox to `+0x19D` and the id to `+0x1C0`. The dialog also has a "Decide Later" button.

**`OWPR` (version 19) ends with those three fields.** `0x0053B230` writes them last: the name as a `u32` length and its characters, the checkbox as one byte, the id as an `i32`, −1 for none. Every civ in the local saves and sandbox captures ends `00 00 00 00 | 00 | ff ff ff ff`: no name, player name hidden, no arms.

**A coat-of-arms id only means something to the server that issued it.** The client draws another civ's arms by asking its own server for `getapprovedcoa` with that id. In multiplayer each install's `cs_server.py` numbers its own uploads from 1, so two players' ids collide, and nothing carries an image to the other installs. In the original, the server kept every image, and the action's name suggests it approved them before other players saw them. The archived forum calls the name and arms a Premium feature, with the arms drawn beside every planet the civ owns.

**A refused fetch is not cached, so arms approved later appear at the next launch.** `0x00517590` walks every civ and calls `0x00517260` with its `+0x1C0` id: `getcoa` for the local civ, `getapprovedcoa` for the rest. An id of 0 or less fetches nothing. An id already in the texture cache (`0x857820`) with a texture is not fetched again, and a `FAIL` answer returns without adding the id to the cache. It runs once as the client loads a galaxy (its one caller is `0x0056D9F4`): in the sandbox on 8 November 2026 a client asked for an id once at load, asked nothing more while it stayed open, and drew the arms after a restart. In multiplayer the launcher restarts the client every turn, so an approval shows from the next turn.

**In multiplayer the relay keeps the coats of arms.** While the launcher plays a galaxy it sets `cs_server.MP_STORE`, and `uploadcoa`, `listcoa`, `getcoa` and `getapprovedcoa` go to the relay's `/<galaxy>/arms` routes instead of `data/coa`. The relay numbers uploads across the whole project (`<prefix>_arms_meta/counter`), so no two galaxies share an id. Each upload is kept in `<prefix>_arms/<id>`, pending, and listed under its sign-in in `<prefix>_arms_owners/<uid>`. Only an account seated on the galaxy may upload, as a PNG of at most 256 KiB and 256 pixels each way, and at most 20 per sign-in. The owner is served a pending upload, and anyone else is served it only once `server/dev_tools/arms_tool.py` approves it. A refused upload is served to nobody. The referee reads each seated civ's uploads that were not refused and passes them to the merge, and records them in the turn's archive so `verify_turn` can rerun the turn. A folder or LAN galaxy keeps no arms, so its `cs_server.py` lists none and refuses an upload. In multiplayer `listcivnames` answers nothing, so the dialog offers no stored name, and `uploadcivname` is not kept: the referee carries the name.

**The homeworld click counters are not `.data` globals.** `0x00842AE4` through `0x00842AF0` have no absolute references in `.text`. They are fields of the prompt's own singleton, which lives at `0x00840BC8`, at offsets `0x1F1C` through `0x1F28`, and `0x00498E10` zeroes all four every time the prompt is prepared. So they read zero after a load because the dialog is constructed once per process and cleared on every open, not because a save failed to carry them.

- [x] **A one-time civilisation setup step should replace `patch_hide_setup_prompts.py`.** In the original, a player chose a name and coat of arms once and the blob carried it, which kept `Owner:384`, the name's length, non-zero and the prompt away. Locally generated galaxies never go through that flow, so the patch stands in for it and the name is left unset. The setup step is the game's own dialog 225. Done when the patch is no longer applied, the merge carries a name, its checkbox and an arms id once, and only as the dialog could have made them, and other players see arms only once the operator approves them: the relay keeps each upload under a galaxy-wide id, and every install's `cs_server.py` asks the relay for `getapprovedcoa`. In the sandbox on 8 November 2026 a v0.5 build showed dialog 225, the referee carried `'tmog66-'` and arms 1 into turn 135, and the name showed with the dialog gone. Elder Race's client was answered `FAIL` for arms 1 while it was pending and drew none, and drew it after approval and a restart.

**Side effect:** Applying T1–T5 removes the Next Turn button from the UI. This is intentional for the multiplayer build, turns are advanced externally via `fast_turns.py`, not by player clicks.

### What T1–T5 skip, read out of the file (September 2026)

**None of them removes a call**, and the code on both sides of every one of them is still
present, so a patched client runs the same routines the original does.

| site | what it reaches |
|---|---|
| T1 `0x0056DBF0` | a leaf predicate rewritten to return true. Its own two calls are getters; of its two callers, one selects the string `Connected` over `Disconnected` and the other runs a block it would otherwise skip |
| T2 `0x0056E0EF`, T3 `0x0056E133` | both guards of `0x0056E0D0`, the homeworld prompt decision routine, not the turn pipeline at all. Removed in October 2026 |
| T4a `0x00577C1A` | a single store repointed |
| T4b `0x00577C2A` | skips `0x00577C84`, the galaxy-join rejection path |
| T5 `0x00579C2D` | NOPs a `JZ` whose target `0x00579C3D` is the next basic block, so both paths converge. Its only effect is that `0x0056DE20` may run, which reads the turn counter at `0x008578E8`, increments it and announces. That is the turn advance itself, and it is why T5 is the patch that lets a client tick |

### The multiplayer player build, and why it has nothing to click

A player's client must not compute a turn and must not be offered the setup prompts, and both follow
from the build rather than from a patch. `CosmicSupremacy_TestBed.exe` is the Resurgence binary
without T1–T5, so the engine's own sync checks are intact, it waits at 00:00 for a server tick the
way the original did, and prompts 210 and 218 stay gated as shipped. `game_cycle.resolve_exe` makes
the build a parameter: players get testbed, the referee keeps resurgence.

**The TestBed dialog's Next Turn, Load and Save buttons never reach the screen on the served path.**
They are dialog resource 222. A player build on a two-player galaxy at turn 110 has no window
carrying control `0x0425`, `0x0426` or `0x0483`, and no control whose text contains "Next Turn", so
the dialog is never created. `client/dev_tools/patch_hide_next_turn.py` clears `WS_VISIBLE` in that
template at file offset `0x007D6BD3` and has been applied to nothing, which is why every binary in
the tree still reads `0x50` there. A template's visibility bit matters only to a dialog something
creates. What gates dialog 222 is not established.

### Patches 18–27, Turn-length floor, 60s → 1s (10 bytes, August 2026)

**Optional, and the only patch here that changes game RULES rather than plumbing.** Applied by `client/dev_tools/patch_turn_floor.py --apply`, reverted by `--revert`, with a `.preturnfloor.bak` written beside the EXE.

The engine re-applies `GSET.turnlength` at every turn boundary (see §2a) and clamps it to a **minimum of 60 seconds**. Sixty is a sensible floor for the game this was, turns ran from minutes to hours, and the wrong one for developing an AI against it: at 75s a 300-turn game takes 6.3 hours, at 3s it takes 15 minutes, and three civs' worth of decisions cost 0.5s of that.

Ten single-byte immediates, `0x3C` → `0x01`. No instruction lengths change, no branch targets move, nothing relocates. The clamp still exists and still guards against a zero or negative turn length; it guards at 1 second instead of 60.

| # | file | VA | instruction |
|---|---|---|---|
| 18 | `0x12C743` | `0x0052D343` | `mov edx, 60`, floor taken as a max against it |
| 19 | `0x12C78B` | `0x0052D38B` | `cmp esi, 60` |
| 20 | `0x12C79A` | `0x0052D39A` | `mov dword [0x0080AA08], 60` |
| 21 | `0x12C8C2` | `0x0052D4C2` | `cmp ecx, 60` |
| 22 | `0x12C8E5` | `0x0052D4E5` | `cmp ecx, 60` |
| 23 | `0x12C8F1` | `0x0052D4F1` | `mov ecx, 60` |
| 24 | `0x12C90C` | `0x0052D50C` | `cmp ecx, 60` |
| 25 | `0x12C910` | `0x0052D510` | `mov ecx, 60` |
| 26 | `0x16D8D3` | `0x0056E4D3` | `cmp eax, 60` |
| 27 | `0x16D8E2` | `0x0056E4E2` | `mov dword [0x0080AA08], 60` |

**CONFIRMED:** galaxy configured `turnlength = 3`, one write to `0x0080AA08` to trigger the first boundary, then **15 boundaries in 40 seconds at a mean of 2.7s** with nothing driving and the live value holding at 3.

Two things cost time here and are worth passing on:

* **Patching the two obvious sites did nothing.** Only #20 and #27 write the immediate straight to memory; the rest load 60 into a REGISTER first (`mov ecx, 60 ; mov [0x0080AA08], ecx`), which does not match a search for `mov [addr], imm`. The live value kept coming up 60 on a client whose patched bytes verified correct on disk.
* **Read the RUNNING process to tell the two failures apart.** "The patch does not work" and "the patch is not where I think it is" present identically. Reading the bytes back out of the live process settled it in one step.

[x] The same function also carries an **upper** bound, `cmp ebx, 0xA8C0` (43,200 = 12 hours) at `0x0052D34F`. Untouched, and presumably the longest turn the original game offered. **Not a limit on `turnlength`.** The compare sits in the speed-galaxy branch of `0x52D0E0` and tests the length read from `SpeedTicks.txt`: above 12 hours the file's second value becomes the turn length, and `[0x80AA04]`, which `GLOB` saves, marks the speed phase. A galaxy with `speed` 0 never reaches it, and the normal path from `0x52D497` shows no upper bound.

### Patches 28–31, Setup and renderer messages drop the old host (1,571 bytes, October 2026)

**Release only.** `client/dev_tools/patch_launch_messages.py --source <exe> --out <path>` writes a patched copy, and `release/build.ps1` uses it in place of copying each client into a release's `game\` folder. No tracked binary carries these bytes, and the script refuses an `--out` that git tracks.

A client started without a galaxy pass, which is what double-clicking one in `game\` does, tries to register the `.csgalaxy` file association and then shows one of two message boxes. Both describe the original site's flow: visit the portal, sign up, download a Galaxy Pass, drag it onto the exe. The portal they name is the patch 2 host string, so a checkout client reads `Visit 127.0.0.1:8888`.

**The patch 2 host string is display text only.** A static initializer copies it into the global string at `0x0086F104`, and every read of that global is one of three `sprintf` calls, all of them message text. The connection host is the patch 3 string, held in the global at `0x0086F124` and read by different code. So the messages can change without moving either host.

| # | file | VA | slot | original | replacement |
|---|---|---|---|---|---|
| 28 | `0x378490` | `0x00779C90` | 586 bytes | `%s setup partly failed! ...` | `%s is started from the launcher, not from this file.` followed by the steps to open `CosmicSupremacyLauncher.exe` one folder up |
| 29 | `0x3786E0` | `0x00779EE0` | 444 bytes | `%s setup completed successfully. ...` | the same text as 28 |
| 30 | `0x378C60` | `0x0077A460` | 40 bytes | `Please login via the %s portal.\nVisit %s` | `Restart %s from the launcher.` |
| 31 | `0x377930` | `0x00779130` | 1,223 bytes | `... email that file to problems@%s. ...` | the same text with that paragraph naming the project's GitHub issue tracker |

Each of 28–30 is a format string whose first argument is the product name, `Cosmic Supremacy`, followed by the host, the extension and the exe name in an order that differs per message. Each replacement keeps only that leading `%s`. The callers remove their own arguments from the stack, so the trailing arguments `sprintf` leaves unread are harmless.

**Patch 31 is the Direct3D failure dialog**, shown after `Failed to initialize the Renderer.` when `CreateDevice` fails. It asks the player to email a DXDIAG report to `problems@` the host, so a localhost client reads `problems@127.0.0.1:8888`. Its caller at `0x0057295F` reads the **patch 3** global at `0x0086F124`, not the patch 2 global above, and passes it before the path of the client's `log.txt`. So the connection host does reach one piece of display text. The replacement rewrites only the paragraph that names the address: it points at the issue tracker, consumes the host with `%.0s` so none of it is printed, and leaves the log path on the second conversion. That paragraph is shortened to fit the URL in the original slot.

Each replacement is written into its original's slot and the remainder is NUL-filled, so no string moves and no length grows. The strings sit at the same addresses in all four clients, and a whole-file comparison of each patched copy against its source finds every changed byte between `0x377BB2` and `0x377DF6` (patch 31, 547 bytes) or between `0x378493` and `0x378C87` (28–30, 1,024 bytes).

**CONFIRMED:** a patched `CosmicSupremacy_TestBed.exe` started with no arguments from a non-administrator account shows the new text in the setup failed dialog.

[x] Patch 31 is seen on screen (14 November 2026). A scratch copy of the patched TestBed with the success branch at `0x005728D0` (`74 4F`, `je 0x00572921`) NOPped takes the `CreateDevice` failure path on a working machine. Its dialog reads `Failed to initialize the Renderer.`, `DirextX CreateDevice returned an unknown error: 00000000`, then the patched paragraph: attach the saved file to an issue at the tracker URL with the latest log file, saved at the client's `log.txt` path. No host appears in it. The forced copy is a probe only and never ships.

### Patches 32–35, Main window opens 10% larger (16 bytes, October 2026)

**Release only**, like 28–31: `client/dev_tools/patch_window_size.py` patches each client `release/build.ps1` stages. The bytes are at the same offsets in all four clients.

The main window's `OnCreate` (`0x00404CA0`) first restores the `MainWindowPos` value from `HKCU\Software\CosmicSupremacy` through a `WindowPosition` helper (`0x0062CB30`). The value is 24 bytes, `x, y, w, h`, their sum, and a Jenkins one-at-a-time hash of `"%d%d%d%d"` over the four, and it is applied only if both checks pass, the centre and top-centre lie in a monitor's work area, Ctrl is not held, and the byte at `0x0081A843` is set. The Tutorial clears that byte at `0x0057E96C`. Otherwise the default comes from `0x00403CB0`, a fixed 950 by 609 placed 40 pixels in from the right and bottom of the screen, floored at 60. The Tutorial's layout (`0x0057E597`) calls the same function.

| # | file | VA | original | patched |
|---|---|---|---|---|
| 32 | `0x30C4` | `0x00403CC4` | `lea ecx, [edi-0x3DE]`, screen width − 990 | − 1,085 |
| 33 | `0x30D6` | `0x00403CD6` | `lea edx, [eax-0x289]`, screen height − 649 | − 710 |
| 34 | `0x30EF` | `0x00403CEF` | `add ecx, 0x3B6`, width 950 | 1,045 |
| 35 | `0x30F5` | `0x00403CF5` | `add edx, 0x261`, height 609 | 670 |

A saved `MainWindowPos` still wins outside the Tutorial, so an existing player sees the change only after holding Ctrl at startup or in the Tutorial. **CONFIRMED** on screen in the Tutorial.

### Patches 36–40, The Tutorial (October 2026)

**Release only**: `client/dev_tools/patch_tutorial.py`, applied to `CosmicSupremacy.exe`, which runs the Tutorial and the Demo. They fix GitHub issues #1 to #4.

The steps are a `char*[27]` at `0x00819688` (`.data`), indexed by step id. ShowNext (`0x0057E3A0`) sets the current step at `0x0086F4F4` from the next one at `0x0086F504`, or shows a pending popup (ids 25 and 26, state 1 in `int[27]` at `0x0086F488`) first. Event(id) (`0x0057DF60`, cdecl) unlocks Next through `TutorialDlg::Unlock` (`0x004C1C60`, thiscall, `ret 4`) only when `id` is the current step. Every step's condition is hard-coded at its Event call site; step 6 has none. The text renderer takes `\x08` as a bold toggle, `\x0C<Icon>` for an icon, and `\r<rrggbb>` / `\r<default>` for colour.

| # | where | what |
|---|---|---|
| 36 | dialog 206 template, file `0x7D3836`–`0x7D38A2` | the panel and its text area 24 dialog units wider and 48 taller (219 by 295 → 243 by 343, text 211 by 272 → 235 by 320), and the Next button moved with them. The text area neither scrolls nor wraps, so the longest steps had lost their last lines and the widest lines their last word |
| 37 | call `0x004C1D04` → stub `0x006D30A0` (106 bytes in `int3` padding) | the Next handler's call to Unlock goes through a stub that calls Unlock, then for step 14 or 17 walks the `Ship` registry (`0x00854628`) and raises the step's Event when a local ship already holds a Colonize (type 3, `ship+0x3C`) or Move (type 1) order. Step 17 accepts any Move order, not only one to the nearest sun |
| 38 | `0x004FA6ED` (`mov edx,[esi]; mov eax,[edx+0x74]`) → call stub `0x006D3110` (73 bytes) | in `PlanetProperties::Tick` (`0x004FA490`), where the current item's cost is fetched: on step 21, for a local planet whose item (`+0xD0`) is a `ShipDesign` (vftable `0x00771DF8`), storage (`+0x20`) is raised to the cost, so the Scout Ship is finished on the next tick. Facilities are never matched |
| 39 | `0x004FA7E6` (`call Event` after `push 21`) → stub `0x006D3170` (98 bytes) | raises Event(21) as before, then on steps 20 to 22 adds one Stationed Military to a local planet with none, the way recruitment does (`0x004F4E00`): `Citizen::Citizen(3, owner)` (`0x005134D0`) and `push_back` on the military list (`PlanetProperties+0x44`, `0x004CB560`), then Event(26), the Stationed Military popup |
| 40 | new section `.tutor` (23,040 bytes raw), and 22 entries of the step table | the steps' tasks drawn in `ffcc33`, step 11 telling the player to select both Colony Ships (a double-click selects one), step 20 and 21 saying the ship takes one turn, step 22 offering a draft for Credits. The edited texts no longer fit their `.rdata` slots |

Section 40 needs room for one more header: `.text` starts at file `0x400` and the five headers end at `0x2E0`. The image has no relocations, no certificate and no overlay, so the section is appended at the end of the file at the next section-aligned VA, `0x00C2D000`, and `SizeOfImage` grows to match. A patched client maps with `LoadLibraryExW(LOAD_LIBRARY_AS_IMAGE_RESOURCE)`.

**CONFIRMED** on screen by a play-through: the panel holds every step, the tasks are coloured, orders given early count, the Scout Ship comes one turn after step 21 with a soldier.

### Patch 41, Single Player sends a chosen civ name (6 bytes, October 2026)

**Release only**: `client/dev_tools/patch_civ_name.py`, applied to `CosmicSupremacy_Resurgence.exe`.

Dialog 225, "Pick your Civilization Name and Coat of Arms", is a modeless singleton (`0x00496B00`) whose OK handler is `0x00496F20`. It reads the name from combo 1010, writes it into the local civ through `0x00579230` (offline: `Owner+0x1A0`, the coat of arms at `+0x1C0`, the reveal flag at `+0x19D`, all saved in `OWPR`), and sends `uploadcivname` only when `CB_GETCURSEL` is −1. The server lists the stored name, the dropdown selects it, and typing over the selection leaves it selected, so the name was never sent and each session offered `DemoEmpire` again.

| # | file | VA | original | patched |
|---|---|---|---|---|
| 41 | `0x96905` | `0x00497505` | `jne 0x004975F7` (`0F 85 EC 00 00 00`) | six `nop` |

**CONFIRMED:** a name typed in the dialog lands in the save's `OWPR`, and with the patch it also arrives as `uploadcivname`, is kept in `civs.json`, and is offered first on the next New Game.

**Coats of arms.** `listcoa` is read with `operator>>` until a read fails (`0x00497B90`), so its answer is space-separated ids. `getcoa`, and `getapprovedcoa` for another civ's id (`0x00517260`), are base64-decoded (`0x005F5F10`), with `FAIL` for none. An image dragged into the dialog (`0x004981A0`) is scaled to 56 by 66 through GDI+ (`0x004977A0`), saved as PNG and sent with `uploadcoa`, whose answer is the new id. The client caches each id's texture and never refetches it, so ids are not reused.

[x] **Confirm the coat of arms on screen:** a dragged image survives OK, is listed next session, and is drawn beside the civ's planets. Seen in the sandbox on 8 November 2026, through the relay. The listing is checked by `test_relay_arms.py`, since the dialog does not come back once a name is set.

### Patches 42–45, Multiplayer status bar shows only the turn (81 bytes, November 2026)

**Release only**: `client/dev_tools/patch_status_bar.py`, applied to `CosmicSupremacy_Player.exe`, the TestBed copy `release/build.ps1` stages for multiplayer. It runs after patches 28–35 and, like them, refuses an `--out` that git tracks.

Under the launcher the client's countdown is wrong (see the in-game countdown item in the todo list) and its Connected or Disconnected label says nothing the launcher does not say better. The status bar is built in the function at `0x00405D00` from four format strings in `.rdata`, each read at one site there, and the formatter at `0x005E35E0` ignores an argument its format no longer prints. So each string is rewritten in its own slot: a label keeps its colour tags and its word becomes spaces, and a countdown string ends after the turn number, NUL-filled to its old length.

| # | file | VA | read at | original | patched |
|---|---|---|---|---|---|
| 42 | `0x34E990` | `0x00750190` | `0x004060CF` | `\r<%s>Disconnected    \r<000000>` | the word as spaces |
| 43 | `0x34E9B0` | `0x007501B0` | `0x00406082` | `\r<000000>Connected    \r<000000>` | the word as spaces |
| 44 | `0x34E9E8` | `0x007501E8` | `0x00405FB1` | `Turn: %d%s     Next Turn in \r<000000>%s` | `Turn: %d%s` |
| 45 | `0x34EA10` | `0x00750210` | `0x00405F5E` | `Turn: %d     Game Starts in \r<000000>%s` | `Turn: %d` |

`test_status_bar_patch.py` checks that only these 81 bytes change, that each string is read once near `0x00405D00`, and that the build's chain of patches leaves them patched.

[x] Seen on screen on 8 November 2026 in 0.5.0.5: under the launcher the bar shows "Turn: N" and no countdown or connection label.

### Summary

Patches 1–4 (54 bytes) redirect all network traffic from the dead production servers (`www.cosmicsupremacy.com`, `cosmicsupremacy.com`, and a hardcoded IP) to `127.0.0.1:8888`, where the local stub server (`cs_server.py`) listens. Patch 1 converts a conditional branch (JZ) to an unconditional jump (JMP), forcing the client to always take the "success" path past a connection-validation check.

Patches 5–11 (13 bytes) bypass save/load validation checks in the game’s persistence code, which are needed for testbed galaxy saves to function against the local stub server.

Patches 12–17 (14 bytes, T1, T4a, T4b and T5) bypass turn-pipeline sync checks, enabling external turn control. Applied only to the Resurgence EXE.

Patches 28–31 (1,571 bytes) rewrite the setup and login messages to send a player to the launcher, and the renderer failure message to send a report to the issue tracker instead of an email address at the host. Applied only to the clients a release ships.

Patches 32–35 (16 bytes) open the main window 10% larger by default. Applied to every client a release ships.

Patches 36–40 fix the Tutorial: a larger panel, early orders that count, a one-turn Scout Ship with a free soldier, and coloured tasks in a new `.tutor` section. Applied only to `CosmicSupremacy.exe` in a release.

Patch 41 (6 bytes) makes Single Player's civ name dialog send the name on every OK. Applied only to `CosmicSupremacy_Resurgence.exe` in a release.

Patches 42–45 (81 bytes) cut the multiplayer status bar to the turn number, dropping its countdown and connection label. Applied only to `CosmicSupremacy_Player.exe` in a release.

### EXE Variants

| EXE | Patches | Next Turn Button | Galaxy File | Purpose |
|-----|---------|-----------------|-------------|---------|
| `CosmicSupremacy.exe` | None | Yes |, | Unmodified original |
| `CosmicSupremacy_TestBed.exe` | 1–11 | Yes | `TestBedGalaxy_local.csgalaxy` | Manual testing with interactive turn button |
| `CosmicSupremacy_Resurgence.exe` | 1–17 (incl. T1–T5) | No | `SandboxGalaxy_local.csgalaxy` | Production multiplayer, turns controlled by `fast_turns.py` |
| `CosmicSupremacy_Resurgence.exe` | + 18–27 (optional) | No | any | AI development, sub-60s turns. **Do not ship**: it changes a game rule, not plumbing, and a galaxy built on it runs faster than the original ever allowed |
| `CosmicSupremacy_Player.exe` | 1–11 | Never created | a pushed `.dat` | Multiplayer player build, made from TestBed by `build.ps1`. Without T1–T5 it cannot compute a turn, and the setup prompts, 225 included, open as shipped |

Every client in a release's `game\` folder also carries patches 28–31, the unmodified original included.

---

## 7. Galaxy Connection Token Format (`.csgalaxy` files)

The client uses `.csgalaxy` files as connection tokens. Each file contains a single line of **base64-encoded text** that decodes to a space-separated string:

```
<TYPE> <SERVER_IP> <PORT_OFFSET> <PASSWORD> <PLAYER_NAME>
```

### Field breakdown

| Field | Example | Purpose |
|---|---|---|
| TYPE | `DEMO`, `TUTO`, `TEBE` | Galaxy type, determines client behaviour (e.g. tutorial vs. full game vs. testbed) |
| SERVER_IP | `127.0.0.1` | Server address to connect to |
| PORT_OFFSET | `0` | Port offset from the base port |
| PASSWORD | `abcdef` | Auth token, sent as `pass=` in API calls |
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
- `userid=0` and `pass=abcdef` come directly from the `.csgalaxy` token, no separate login step.

**Tick behaviour**
- Tutorial galaxy advances at ~1 tick/minute with no server involvement.
- Tick timing is controlled client-side (confirmed by `c:\\SpeedTicks.txt` debug string in binary).
- The "Ticks Halted" state in the Demo galaxy is a server-controlled pause, the server must release it. Mechanism TBD (likely part of the `loadgame` response blob).

**Save blob**
- `savegame` was never called during tutorial, game state was not persisted.
- Save blob format remains unknown; must be captured from a real (non-tutorial) galaxy session.

**`testconnection` response**
- Must return the exact string `READY` (confirmed from binary string `'tutorial communication test response from server: '%s''`).
- Any other response causes the connection dialog to show "failed to connect".

---

## 9. Test-Bed Galaxy Protocol Findings (March 2026)

Key observations from running the patched EXE with a `TEBE` type `.csgalaxy` token:

**Connection flow**
1. Client calls `testconnection` (same as tutorial/demo, must return `READY`)
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
- A new save arrives with `gameid=-1`, the client's "allocate new slot" sentinel, and the name typed in the Save dialog (`Unnamed` by default). The `TestBed Save N` names seen early on were made up by the prototype server.
- The server should allocate the next available positive integer ID when it receives `gameid=-1`, since the client treats negative IDs as invalid when loading from `savegamelist`.
- Save/load cycle works identically to other galaxy types, the server stores and returns the binary blob opaquely.
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
| `testconnection` | `READY` |, | Any other string → "failed to connect" |
| `savegame` | `DONE` | `0x0048b350` / `0x403f00`: `strncmp(response, "DONE", 4)` | `OK` or any other string → "Failed to save the Save-Game" dialog |
| `savegamelist` | `<gameid>#SPC#<name>#SPC#<turn>#NEXT#...#NEXT#DONE` |, | Empty body → "Failed to retrieve list of saved games". `DONE` alone = valid empty list |
| `loadgame` | `DONE#VER#<6-char-version>#DATA#<base64-blob>` | `0x0048b5d0` / `0x40a640` | Version `000000` = identity cipher (no transform). Non-zero version applies byte-level cipher to data. See below |
| `savegov` | `DONE` | `0x4a0c3f` | Same `strncmp` pattern as `savegame` |
| `govlist` | `DONE` |, | `DONE` alone = valid empty list |
| `loadgov` | `DONE#VER#<6-char-version>#DATA#<base64-blob>` |, | Same format as `loadgame` |
| `listcivnames` | `<civname>#SPC#<coaid>#NEXT#DONE` | `FUN_0x497f93` / `0x5e3de0` | If coaid is empty/null, the "Customize Your Home World" popup reappears every tick |
| `listcoa` | `<coaid>#NEXT#DONE` |, | Empty response → no COA registered → some UI elements missing |
| `uploadcivname` | `OK` |, | No response-body check in client |
| `entertestbedgalaxy` | `OK` |, | Empty body or `OK` both work |
| `passedtutorial` | `OK` |, | No response-body check in client |

#### `loadgame` response parsing (detailed)

The client parses `loadgame` responses as follows (from binary analysis at `0x0048b5d0`):

1. `strncmp(response, "DONE#VER#", 9)`, must be 0 (success flag)
2. `substr(response, 9, 6)`, extracts 6-char version string into a decoder object
3. `find("#DATA#")` in full response, locates the data marker
4. `substr(pos_of_DATA + 6, end)`, the raw base64 blob
5. Base64-decode → strip 4-byte header → zlib-decompress → game state

The 6-char version string is used as a key for a stream cipher (`0x411110` decoder factory). Version `000000` produces an all-zero key → identity transform (XOR with 0x00 = no change), so the blob passes through unmodified. The original server likely used non-zero version strings to obfuscate save data in transit.

---

---

## 11. Live Memory Object System (EJBO)

Moved to **`CosmicSupremacy_Memory_Reconstruction_Report.md`**, the field-by-field
reconstruction of the in-memory object model, together with its open annotation TODOs.

It is no longer the multiplayer mechanism (see the Audit in Section 2), but it remains the
way to drive the game in-process, which is faster than a save / edit / relaunch cycle and is
the intended basis for training a custom AI. It is also the only way to reach game state that
lives in `.data` rather than on an object, which a blob cannot carry.

---

## 12. Save/Load Capture & Automation (June 2026, corrected August 2026)

### Save blob captured, format confirmed against real data
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

> The tooling this section originally described, `save_codec.py`, `game_controller.py`,
> `game_data/multiplayer_saves/`, no longer exists and never appears in git history under those
> names. The current equivalents are `server/dev_tools/save_parser.py`,
> `server/dev_tools/inject_order.py`, `server/dev_tools/diff_saves.py` and the `savegame`
> persistence in `cs_server.py`, which writes to `server/saves/`.

### External save/load trigger, ACHIEVABLE (corrected, August 2026)

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
  exactly one caller in the binary, the Save/Load dialog at `0x0048B950`, reached through a
  message map, and normal play has no such dialog.

Corrected:

- **Save is triggerable externally.** `SaveGame` runs correctly from a `CreateRemoteThread` stub:
  it is synchronous down to WinInet's blocking `HttpSendRequestA` and touches no per-thread
  state. The three things the old note called fragile are all pinned,
  `bool __thiscall SaveGame(this, int gameid, std::string *gamename)` with a `ret 8` epilogue, and
  `this` is never read. `client/dev_tools/trigger_save.py` does it; it produced a 117,024-byte
  blob with nobody at the keyboard.
- **Load does not need a trigger at all.** The client loads a `.dat` named on its own command line
  during startup, on the main thread (Section 2). No dialog, no server, no injection.
- **Load cannot be triggered from a remote thread**, which is worth recording as the reason the
  startup path is the right answer rather than a convenience: the load path dereferences objects
  held in thread-local storage, and a created thread's TLS block is zero-filled. Confirmed by
  crashing it, `EXCEPTION_ACCESS_VIOLATION` at `0x005D17F5` with `eax=ecx=0`. The thread exit
  code still read as success, because the crash handler runs on the faulting thread.
- The **`savegamelist` turn-bump** idea (advertise a higher turn to trip the client's out-of-sync
  check) was never confirmed on the client and is now unnecessary.

### A save without a remote thread (follow-up, October 2026)

The launcher makes the client save by injecting a call to `SaveGame` in a remote thread
(`client/dev_tools/trigger_save.py`). That was the shipping path for the public beta. The route
that removes the remote thread is a resident handler in the client, and every shipped client now
carries one.

What bounds it:

- `SaveGame` (`0x0048B350`) has one call site, the Save/Load dialog's `WM_COMMAND` handler
  (`0x0048B950`, control `0x0483`, map entry `0x0075F39C`), which reads its arguments from the
  dialog. The player build never creates that dialog, so scripting it with window messages
  cannot work.
- `SaveGame` is `ret 8`, ignores `this`, and is synchronous: the same three facts the injected
  stub relies on.
- `.text` has a 3,948-byte run of padding at `0x006D2094` that can hold the stub.
- The launcher half is `client/dev_tools/trigger_save_message.py`, which sends the registered
  window message, and `game_cycle.trigger_save` uses it. A stock client ignores the message.

- [x] **A player build saves on a sent message** (14 November 2026). `patch_save_message.py`
  hooks `AfxWndProc` at `0x00654892` and puts the stub at `0x006D20A0`. The stub answers the
  registered message `CosmicSupremacyResurgenceSave` by calling `SaveGame(wParam, "resident")` on
  the client's own main thread and returns its result. `build.ps1` applies it to every client it
  stages, and `release/tests/test_save_handler_patch.py` pins it. The launcher sends the message with
  `SendMessageTimeoutW`, so it knows whether the save ran. `CS_SAVE_VIA_MESSAGE` now defaults to
  the client's own answer: the message path when the running exe carries the handler, the
  injected save otherwise. `1` or `0` forces either one. A patched TestBed on
  `SinglePlayerGalaxy.dat` answered both a direct send and `game_cycle.trigger_save` with
  `SaveGame` returning 1, and both captures reached `cs_server` (12,412 bytes, turn 0).

A resident stub and a window-procedure hook are themselves a pattern antivirus heuristics watch
for, so this moves the sensitive act into a signed static binary rather than removing it.

### One game process per machine, and what the lock does not know

A machine has one game process, and the tools that drive it are not naturally exclusive. A host that
both plays and referees has two things wanting the single client, and nothing arbitrates: a referee
waking on its deadline will close a player's client mid-turn, or an experiment will close the
referee's. `game_cycle.take_client_lock` settles it. `launch` claims the machine's one game process
naming what it is for, `close_client` releases it, and a second tool is refused with the holder's pid
and purpose rather than silently winning the race. The referee waits, because it can afford to and a
player mid-turn cannot afford for it not to; a player's serve refuses at once. The lock is advisory,
nothing stops a tool calling `Popen` itself, and every path in this project goes through `launch`. On
separate machines the problem does not exist, which is why it survived this long unnoticed.

- [x] **The lock records who last claimed the client, not who is using it.** `take_client_lock`
  cleared a stale lock by asking whether the holder's pid is alive, which was written for a holder
  that died mid-hold, since a legitimate hold lasts a whole turn and a timeout would break it. A
  holder that exits normally and deliberately leaves the client running produced the same stale
  lock, and the next tool through was waved past it. Seen live: a script that opened a galaxy for
  inspection finished, left the client up, and left a lock naming a dead pid while other work was
  still in flight on that machine. **Fixed** (14 November 2026): `launch` writes the pid of the
  client it starts on a second line of the lock, `client <pid>`. A lock whose holder has exited is
  stale only when that client has exited too. While it runs the client was left open: another
  purpose is refused with the holder, its purpose and the client named, `close_client` refuses it,
  and only the purpose that left it takes it over, which is how the referee recovers from its own
  crash. `python game_cycle.py --close` closes a client left open once nobody is using it, and
  still refuses a live holder. A lock written before this has no client line and is treated as
  before (`server/tests/test_client_lock.py`).

### Conclusion (corrected)

**Save-blob sync is the primary path for multiplayer.** The blob is a complete state snapshot, it
round-trips byte-exactly, the server can push one into a client at startup with no user
interaction, and a server-authored ship order has been confirmed to fly a ship that nobody
clicked. See Section 2 for the format, the push mechanism and the audit.

Live-memory access is retained for what it is genuinely better at, driving the game in-process
without a relaunch, which is the intended basis for training a custom AI, and for reaching game
state that lives in `.data` rather than on an object, which a blob cannot carry. It is documented
in `CosmicSupremacy_Memory_Reconstruction_Report.md`.

---

### What We Are NOT Doing

- **OAuth / social login**, requires new UI windows in the EXE; not feasible via patching

---

## 13. Conscription: the production/population path (August 2026)

Derived from the binary in `client/dev_tools/findings_production_path.md` §1.0-1.6,
which `client/dev_tools/ai_player/remote.py` cites by name. **That file was deleted
once in a cleanup pass and its contents existed nowhere else**, none of these
symbols appeared anywhere under `docs/`, so the load-bearing findings are recorded
here as well. Every signature below is confirmed from the callee and both call sites.

| Function | Address | Signature |
|---|---|---|
| `ChangeCitizenJobs` | `0x00574180` | `__cdecl(Planet* alloc, container* indices, int newJobId, bool bFromMilitaryList)` |
| `GetDraftCost` | `0x00516060` | `__thiscall(int count)`, `ret 4`; ECX = `Owner_primary + 0x30C` |
| `CommitPopulation` | `0x004F5280` | performs the migration between the two vectors |

**The draft price.** `cost = sum over k in 0..count-1 of 5 * (T + k) + 105`, where `T`
is the civ's military across the WHOLE empire, every planet's `Planet:168` count plus
every owned ship's crew. Verified live against the engine six times across two civs.
Conscription is therefore cheapest early and rises as the fleet grows.

**A citizen and a stationed military unit are the same 16-byte record.** Drafting is a
job change to id 3 plus a migration between two vectors, not an append to a military
list. This is what makes `actions.conscript_to_crew` legitimate: a crew member is that
same record again, so a draft can go straight onto a ship.

**On the wire the engine moves the record rather than rebuilding it**, confirmed in September 2026
by a player drafting a citizen on a planet and posting the new unit to a ship, captured on the same
turn it was served so no tick is inside the diff. One byte of the nine-byte record changes, the job:

    citizen  000000940200001500
    crew     030000940200001500

A soldier is therefore traceable back to the citizen they were, and citizens, garrison and ship
crew have to be read as one population: a record that keeps its bytes across the move leaves the
sight of any rule that looks only at citizens.

**Container wrappers.** `Planet:132` and `Planet:156` hold their vector 12 bytes in,
the same idiom as the order object's `+28`/`+52` list heads.

**Drafting is FREE below content version 150.** The gate is `[0x0080AA00] >= 150`;
under it the engine skips the whole cost block.

**HAZARD, `CommitPopulation` auto-conscripts any population above `space/10`.**
Anything calling it must expect the citizen vector to shift underneath a stale index,
which is why `actions.conscript` re-reads the list live rather than trusting the
snapshot.

---

## 14. Operating the multiplayer backend (October 2026)

What is left for after the beta is kept here.

- [x] **Measure the referee's reads after the poll change.** `referee_worker.py` read the galaxy
  every 5 seconds for the whole turn, about 16,800 Firestore reads a day per galaxy, measured on
  `h3check` on 5 October 2026 with no launcher open. Its gap now follows the deadline
  (`poll_gap`: a twentieth of the distance, between `POLL` 5 s and `POLL_CEILING` 120 s, and the
  ceiling while closed), which works out near 1,100 reads a day for an idle four-hour galaxy. An
  earlier deadline or a reopen is noticed up to 120 s late. Measured on the sandbox galaxy, whose
  launchers are never all closed for a day: the one hour with no relay request (27 October,
  02:00 UTC) read 30 documents, and a fit over 19 hours from 26 October 13:00 UTC gives 31.5 reads
  an hour, 0.81 per relay request and 80.4 more in each tick hour, no hour off by more than 9.
  That is about 1,240 reads a day for the referee alone, an upper bound since the tick hours
  include launchers fetching the new turn. `server/dev_tools/firestore_reads.py --hours N` lists
  the hours.

- [x] **Citizens a player owns on someone else's planet are refused, as the client does.** A colony keeps the owner of the colonists that founded it. In `galaxy_demo`,
  Neighbor's planet 138 was founded on turn 7 by a ship whose crew a join had left owned by
  DemoPlayer, and holds two DemoPlayer farmers; the engine ticked it through turn 30 (`h3check`)
  and citizens born there are Neighbor's. No other save in the checkout, the war fixtures
  included, has anyone on another civ's planet. The merge passed over a submitter's changes
  there without a note; it now refuses any change to people on a planet or ship another civ
  owns, naming the planet, its owner and what the submitter did to its own citizens there
  (`test_rival_citizens.py`). The planet's owner reassigns everyone on it, other civs'
  citizens included, and that is carried, because the checks count people per owner and not
  jobs. Neighbor's client moved the last farmer in the array on `h3check` turns 17 and 18, a
  Neighbor citizen both times. Measured on 27 October 2026 on `galaxy_demo` turn 11. DemoPlayer's
  planet list holds only its own planets, and double-clicking either of Neighbor's on the map
  opens nothing, so DemoPlayer cannot reach its farmers on 138 and the refusal matches. Neighbor
  opens 138, the tooltip names each farmer's civ, and moving three farmers to worker took both of
  Neighbor's and one of DemoPlayer's (`rivalB`, `save_005`); the merge carries that in either
  order with no note, alongside DemoPlayer's untouched `rivalA`. The manual's loyalty page
  describes a ruler's influence over a planet's population growing each turn; whether the citizen
  owner field itself ever changes to the planet's owner is not measured.

**The merge checks that an order is the player's own, not that the UI would allow it.** A
technology the civ cannot research, or a queue entry it cannot afford, is copied as given. The
client enforces those rules itself, so only an edited client or a memory edit gets past them,
and the merge never takes a civ's credits or stocks from a submission: the referee charges
hurries, trades and treaty sums itself. Checking every order type is not planned; it is taken up if cheating shows up in a galaxy
(Robert, 13 November 2026).

- [x] **Logging in is played through the live relay.** The relay knows a player by uid, and
  creating an account or linking Google keeps the guest's. On 4 November 2026 an email login
  and a Google login from a checkout launcher each listed the sandbox, submitted, logged out
  and back in, and submitted again. A login adopts the account's stored name; Play follows the
  seat the install joined as (`seat_to_play`), so a rename does not lose the seat.

- [x] **The operator view shows who still plays as a guest.** A submission records the
  caller's `firebase.sign_in_provider` under `seat_providers`, and the operator view lists each
  seat's provider and the seated guests. On 4 November 2026 the sandbox showed every seat on
  Google and no seated guests.

- [x] **Play by name has an end switch.** `seat_tool.py <galaxy> guests refuse` makes the
  relay refuse a token whose provider is `anonymous`, and the launcher shows "log in to play".
  It ships off; the sandbox still allows guests.

- [x] **The sandbox still lets guests in.** A guest is warned before it joins or plays, once a
  day, that galaxies will soon be played by account only (`GUEST_ONLY_TEXT` in the launcher).
  Done when `seat_tool.py firebase://cs-resurgence/sandbox guests refuse` is set, after the operator view shows
  no seated guests, and the launcher update that follows rewords the warning to say guests are
  refused. Set on 12 November 2026 with `--anyway` (Robert): every seated civ on the roster
  signs in with Google, and the one seat the check stopped on, Sygor, is not on the roster and
  has not submitted since sign-ins were recorded. The bot civs submit with the operator's
  credentials, not through the relay, and are not affected. The reworded warning is in v0.6.0.

- [x] **The merge parses the whole blob again for nearly every read.** Each accessor in
  `merge_orders.py` and `news.py` calls `sp.parse_blob` itself, so one merge of a 38 KB test blob
  parses it 83 times (13 November 2026, `test_admirals_diplomacy_merge.py` under `cProfile`).
  v0.6's display, message and news reads added about a dozen, and the admirals and queues merge
  tests now take over 30 minutes. A live tick is still dominated by the game client. Done when
  a merge parses each blob it holds once per change, and both tests run in a few minutes.
  **Fixed** (`save_parser.parse_blob`, 14 November 2026): a `bytes` blob's tree is cached by its
  content, the last 16 kept, with the `Section` objects shared and never changed by a caller.
  One merge of the test blob went from 83 parses to none beyond the first, the admirals test
  from 72 minutes to 65 seconds and the queues test from 51 minutes to 49 seconds.

- [x] **A guest's seats follow its install to an existing account.** On login the launcher
  posts both the guest's and the account's tokens, and the relay moves each seat and each
  waiting join to the account, keeping any the account already holds in that galaxy. On
  3 November 2026 the sandbox seat `guest-test` moved from a guest to a Google account, and on
  4 November a waiting guest join moved before its turn.

- [x] **The launcher updates itself** (`release/updater.py`, `test_updater.py`). It reads the
  latest release on GitHub, downloads its one zip within the listed size, checks the SHA-256
  against both the asset digest and the release notes, and requires every `.exe` and `.dll` to
  carry a valid Artifact Signing signature with the pinned subject, issuer and identity EKU. The
  staged launcher must report the release's build, newer than the running one. Update waits for
  the game to close, swaps every entry but `data` and `update` into `update\previous`, and puts
  the old build back when the new one does not report its window within 90 s. A signed 0.5.0
  replaced itself with 0.5.0.1 from a local feed on 1 November 2026, keeping the login, and
  refused an unsigned build; the no-start rollback is covered only by `test_updater.py`. A
  launcher built without `-UpdateFeed` reads only the real release page. The TUF items in
  `5 - Release_Checklist.md` and Multiplayer_Checklist M12 are its acceptance list.

- [x] **Order types nobody has measured are not merged:** admirals and diplomacy proposals.
  Both are refused with a note naming what was dropped (`test_admirals_diplomacy_merge.py`); until
  27 October 2026 a new admiral or treaty was ignored without one, and an admiral id in a ship's
  `DYNO` rode along with its orders. Both are measured (27 and 28 October 2026) and every step
  made in the UI is refused with a note, or taken when it is only an order. Carrying diplomacy
  means changing both civs at once, since accepting a treaty moves its lump sums immediately.

- [x] **Admirals and diplomacy run through the live referee.**
  `merge_orders.py` carries a civ's own admirals and their ships, and each diplomacy step the
  client was seen to take, applying an acceptance's lump sums and both stances itself; every
  step of the admiral and diplomacy sessions merges to what the client wrote
  (`test_admirals_diplomacy_merge.py`). On the sandbox galaxy (1 November 2026, turns 93 to
  97), with bot civs submitting bytes built from the client's recorded records: Beta's Scout
  admiral stayed assigned across two ticks and gave its idle ship an engine scout order; Beta
  proposed a trade of 5 metal for 1 credit a turn over 10 turns, Sir Blorpington accepted it the
  next turn, and after the tick it was in force from turn 96 with the metal moved once (630 to
  625 and 635). The two civs had not met, so their contact was written into turn 95 at the
  publish. Still refused: an admiral on a fleet. Built on the UI's word, not a save: a peace
  binding for 50 turns and an alliance for 100, a credit lump sum as one `u32`, and no war while
  a cease-fire or peace binds.
  - [x] An admiral made in the client UI and a treaty answered in it go through a live galaxy
    (13 November 2026, sandbox turns 166 to 168): tmog66 made admiral 337 "scout2a" and gave it a
    ship, and proposed trade 338 to guest-test; guest-test accepted it on turn 167, and turn 168
    holds it active with start 167 and length 5. No refusal on either turn.
  - [x] A per-turn credit payment is seen moving after an accepted trade (13 November 2026):
    guest-test, idle, gained 100 credits a turn from 163 to 167 and 101 on turn 168, the first tick
    of trade 338's 1 credit a turn. The payer's side is not separable from tmog66's own spending,
    and the trade running out after 5 turns is assumed, not seen: the game's tick ends it, and
    no test covers a submission merged after a treaty ends.

- [x] **Governors run through the live referee.** Measured in Single Player and merged in
  `test_governors_merge.py`. On the sandbox galaxy (2 November 2026, turns 97 to 98), Beta
  submitted governor 260 "g3", built from the Single Player record of a "Set Recruitment Rate"
  rule at 10%, on home planet 186. After the tick 186 still names governor 260 and its
  recruitment rate reads 10%; Beta's other planets have no governor and stay at 0%. When Beta's
  bot set 186 back to 0% on turn 99, the governor restored 10% by turn 103, so the rule acts
  every tick, not only on assignment.
  - [x] A governor made in the client UI goes through a live galaxy. Robert, 2 November: a hurry
    governor in tmog66's empire works.

- [x] **Play stays live on the galaxies page while a turn is processing.** `row_action` offers
  Play on every seated row whatever the turn loop is doing, and the guard is only in
  `_take_turn_loop` once pressed: Play on the processing galaxy asks the loop to reopen the
  game, and Play on another galaxy with the game closed switches without asking, holding the
  window for up to 20 s in `stop_multiplayer`. Done when, from orders sent until the next turn
  opens, the followed galaxy's Play and every other row's Play are greyed out with a line saying
  the turn is being processed. `Launcher.turn_busy` greys every row's Play while the orders are
  sending and from the deadline until the next turn opens, and the line under the table says
  which. Before the deadline Play stays live, since it reopens the game to change the orders
  sent, and a stopped or unreachable galaxy keeps it, since the relay takes orders until the turn
  changes. `test_turn_busy.py`; not yet seen on screen.

- [ ] **The website's Galaxies panel lists the open galaxies.** Every archived page's panel
  shows a "Coming soon" note in place of the 2023 list (`replace_galaxy_list` in
  `site/patches.py`). The launcher reads the relay's listing (`GET /`, `galaxy_directory`), but
  that route needs a sign-in and sends no CORS header, so a page on cosmicresurgence.com cannot
  read it. Decided (Robert, 14 November 2026):
  - the referee publishes a small public file each tick, and the page reads that; the relay
    gets no route without a sign-in;
  - the panel lists each open galaxy's title only, with no links; the file's rows leave room
    for links, player counts and descriptions later;
  - the sandbox is listed, and is the only galaxy for now.

  Done when the panel on cosmicresurgence.com lists the sandbox by title from that file, and
  keeps the "Coming soon" note when the file cannot be read or lists nothing.

  Built in v0.7: `referee_worker` calls `public_listing.publish` after each tick, which writes
  `public/galaxies.json` (`{"galaxies": [{"title": ...}]}`, open galaxies only, cached five
  minutes) to the store's bucket; `beta_storage.rules` lets anyone read that one object; the
  panel holds the note and `site/overrides/scripts/galaxies.js` replaces it with the titles.
  `test_public_listing.py` checks the rule on the emulators, and the script was seen filling
  the panel from a local stand-in. Left before it shows live:
  - [ ] Deploy the storage rules: `firebase deploy --only storage`.
  - [ ] Set the bucket's CORS: `gcloud storage buckets update
    gs://cs-resurgence.firebasestorage.app --cors-file=server/beta_storage_cors.json`.
  - [ ] Restage the referee (release step 10), so it publishes the file.
  - [ ] Deploy the site, then confirm the panel lists the sandbox.

- [ ] **The operator page shows when players have voted to end a galaxy** (Robert, 14 November
  2026). Players may vote to restart the sandbox; the operator decides, so the page only shows
  the count. Done when a vote cast with the client's "vote to end galaxy" button reaches the
  referee through a submission, and `operator_view` shows each galaxy's votes against its
  seated civs. Depends on decoding the button (Memory report, **"vote to end galaxy"**).

- [x] **The home page carries news items.** The home page is `site/overrides/game_news.php.html`,
  served at `/`, and holds one post, "Welcome to Resurgence" (13 September 2026). Done when a
  new item can be added with a date, newest first, without editing the archived markup by hand.
  Each item is a file in `site/news/`, `YYYY-MM-DD-anything.html` with the title on its first
  line; `stage.py` renders them newest first at the home page's `<!-- news items -->` marker
  (`site/news.py`, `site/tests/test_news.py`, `site/README.md`). The welcome post moved there
  and renders as before.

- [x] **The launcher shows a news banner below the Multiplayer button** on the Play page
  (`#modes` in `release/webui/index.html`). It is not linked to the home page's news: each is
  written on its own. The launcher reads the line from a small file on cosmicresurgence.com, so
  a line changes with a site deploy, not a release (Robert, 14 November 2026). Done when the
  banner shows the current line as plain text, shows nothing when there is none, and a
  launcher that cannot reach the site still opens. The new request is recorded in the Privacy
  Checklist. The launcher reads `site/overrides/launcher_news.json` (`{"text": ""}` for none)
  two seconds after it opens and hourly after, off the UI thread; a failed read is logged and
  keeps the last line. The page draws the line inside the Multiplayer row, as text
  (`test_launcher_news.py`, in `run_all.ps1`). Seen on the Play page served locally, not yet
  in a built launcher. The Privacy Checklist's PRIV-2 IP item names the request; its line was
  edited by hand to match `build_ops_checklist.py`, since the checklist sources are not on
  this machine to regenerate from.

- [x] **A release step asks whether to update the home page news or the launcher banner.** One
  step in `docs/DEVELOPMENT.md`'s release checklist, before the site deploy at step 9, asking
  about each separately, since they are not linked. Both are site files, so step 9's deploy
  carries either change. Added once both of the items above exist. It is part of step 9, so
  step 10 keeps its number.

**Measured in one Single Player session at turn 0 (October 2026,
`server/tests/fixtures/tier4`):**

- **Deactivating a design** flips only its `DSGN`'s last byte, `0` active and `1` inactive. Carried.
- **Choosing what a planet builds** writes `PROD`: `+1` build-active, the facility type at `+13`,
  and the nested kind (`WLTH`, `FCLT`, `SCAN`, or `SHIP` with a design id). Carried when the
  client's `CanBuildFacility` would allow it (`facility_rules.py`).
- **Adding a planet to a production queue** creates a queue object under the civ,
  `OWNR > DATA > SPQS` (a `u32` count, then `SPRQ` holding the object id and a `PRQU` of entries),
  and links the planet's `PROD` to it at `+2` (flag), `+3` (the queue's object id) and `+7`.
  Carried, with the queue itself (see the 25 October measurements below).
- **A fleet** is `GLXY > FLET`: the 20-byte object header (a new object id, raising `SAVE+0`),
  a `DYNO` with the fleet's orders, and `FLDT`: a `u32`-length name (default `Battle Fleet`, cut
  to 32 characters), a `u32` member count and one `SHPR` per member. Members leave `GLXY` as
  `SHIP`s while in the fleet; a ship taken out comes back as a `SHIP` with its own id, and a
  one-ship fleet survives until disbanded. A `SHPR` ends with a `f32`, the id of the last fleet
  its ship was in (`SHPR+30` with two crew), kept after the ship leaves, and the ship's own id.
  Carried (`test_fleets_merge.py`).
- **A `SHPR` begins with the ship's design id** (`+0`), then the crew count at `+4`. **The
  tail's `f32` is the ship's condition**, `Ship:136`, 0.0 to 1.0. CONFIRMED November 2026 on
  the war blobs ticked by `sim/oracle.py capture`: ship 678 reads 1.0 before the turn-110
  battle, 0.9238 after it, and 1.0 again a turn later, repaired. Every other ship on disk
  reads 1.0.
- **A ship cloned before 27 September 2026 can name its donor in that tail.** 115 ships in 86
  blobs end with another ship's id: the third civ's Colony Ship 208 in the `referee_work` galaxy
  names 200, and the staged `bgf1` hulls 203 and 204 in `battle_a.dat` name 193. `inject_ship.py`
  copied the donor's record whole until `f4fb9f7`; since then it rewrites the trailing id, and
  `inject_civ.starting_kit` clones through it. The engine carries the stale id forward every tick
  without complaint, so the galaxies made earlier still hold it. The object header's id is the
  ship's. `merge_orders.read_ship` refuses such a ship as unreadable, which leaves it out of the
  fleet merge; `sim/load.py` reads it by the header.

[ ] Decide whether `merge_orders.read_ship` should accept a ship whose tail names its donor. As it
stands a player in an older galaxy cannot put such a ship in a fleet. Check first whether any live
galaxy holds one.
- **Individual ships cannot be renamed**; only fleets can.

**Measured in one Single Player session at turn 0 (23 October 2026,
`server/tests/fixtures/governors`):**

- **A governor** is a `GVNR` in the civ's `OWNR > DATA > GOVS`, after a `u32` count: its object
  id, a `u32` count of the planets it governs, one byte, and a `GVDT` holding the name and the
  rules, each rule a one-byte `RULE` (its checkbox) followed by the rule's own section (`RLSP`
  for maintain population, `RLPR` and `RLRR` with a nested `COND`). Creating one appends a
  `GVNR` and raises `SAVE+0` to its id; the client will not create one without a rule.
- **Assigning** writes the governor's id into the planet's last `u32`, after its `PLPR`, and
  moves the planets-governed counts. The client offers no way to unassign.
- **Editing** rewrites the `GVNR` in place under the same id. **Deleting** from the selector
  removes the `GVNR`; the governor library on the local server keeps its own copy, so Load
  brings it back. Carried; deleting one that still governs a planet is refused.

**Measured in two Single Player sessions on 25 October 2026 (`server/tests/fixtures/tier5`):**
production queues on `clean.dat` at turn 111 (`q*`), fleets on `SinglePlayerGalaxy.dat` at turn 0
(`f*`). Both ran with the turn length set to 30 days so that no turn resolved between steps, and
turns were advanced with `gamectl.Client.advance_turn`. Each step is one change made in the UI.

- **A production queue** is an `SPRQ` in the civ's `OWNR > DATA > SPQS`, whose `u32` count goes
  from 0 to 1. `SPRQ` holds the queue's object id, a `u32` count of planets using it and a `u8`,
  then a `PRQU` (version 1): a `u16` entry-id counter (2 with one entry, 4 after a second was
  added, still 4 after one was deleted), a `u32`-length name, a `u32` entry count and the entries.
  A queue no planet uses is saved and survives a reload. A civ's `SPQS` and `GOVS` sit after
  its news items (`NEWS`) in `DATA`, which `save_parser` walks past.
- **A queue entry** is stored in the order the UI shows: a `u16` entry id (1 for the farm, 3 for
  the Colony Ship; it moves with its entry when reordered), a `u16` count, a `u8` infinite flag,
  a `u8` count mode (1 "build up to"), a `u32` item type (0 facility, 8 ship, 9 scan), then a
  `u32` naming the item: the facility type (0 for a farm), the design id, or the scan (2 route,
  3 fleet, measured 27 October 2026; the other four scans were not yet researched). The queue
  editor offers facilities, ships and scans and nothing else, so no other item type comes from
  the UI; a queue of every facility researched (0, 2, 4, 6, 8 and 9) holds type 0 throughout
  (`tier5/qs3`). Adding, deleting,
  reordering and editing an entry change only these bytes and the enclosing lengths. The client's
  writer (`0x556560`) and reader (`0x556D20`) know item types 0 to 9: types 1 to 6 have nothing
  after the type, and 7 and 9 a `u32`. What types 1 to 7 name has not been measured, since the
  editor never writes them. The reader drops a ship entry whose design does not resolve.
- **Assigning a queue to a planet** raises the queue's planet count and links the planet's `PROD`
  (`+2` flag, `+3` queue id, `+7` the `u16` id of the entry the planet is building). The planet
  switches to the queue's item at once, dropping what it was building, and the civ's `OWPR`
  `+132..+143` move with it. They move with a manual override too, so they follow production in
  general, not the queue.
- **Overriding a queued planet's production** by hand keeps the queue link and sets `+7` to 0.
  When the item completes the engine returns the planet to the queue by itself (measured across
  turns 113 to 128).
- **Setting a queued planet to Wealth** is the only way out of a queue: the link is cleared and
  the queue's planet count drops.
- **Deleting a queue** is allowed once no planet uses it, and leaves `SPQS` byte-identical to a
  civ that never had one.
- **Renaming a fleet** rewrites only the name in `FLDT`.
- **A fleet's move order** is the same three edits as a ship's, in the `FLET`'s own `DYNO`; the
  members' records do not change.
- **A ship leaving a fleet** comes back with empty orders. **Joining an existing fleet** is the
  exact reverse: the blob after leaving and rejoining is byte-identical to the one before.
- **Disbanding** removes the `FLET` and returns every member with empty orders. `SAVE+0` is not
  lowered.

- [x] **Fleets are carried, and one has run through the live referee.** Forming, renaming,
  fleet orders, ships leaving and joining, one fleet joining another, and disbanding are merged in
  `test_fleets_merge.py`, and each Single Player step comes out of the merge byte for byte as the
  client saved it. A new fleet takes a free id as designs and governors do, and every ship in it is
  checked: the submitter's as served, placed once, all at one position. Five blobs the merge wrote
  itself (two civs' fleets with one renumbered, a ship left out of an ordered fleet, crew moved
  inside one, one fleet merged into another, a renumbered fleet a ship left) load in the
  Resurgence client, resolve a turn and save again with their fleets intact; the ordered fleet
  moves along its route and the ship left out stays put (25 October 2026). In the live sandbox
  galaxy (referee staged at `a715489`), Beta's fleet 227 of ships 221 and 214, submitted on turn
  56, was still a fleet of both in turn 57, and Beta had no refusal note (26 October 2026). Beta
  never had two ships together, so for the test turn 56 was republished at its publish with 214
  moved beside 221, the deadline kept and the turn-55 archive record updated, and the fleet was
  submitted by script; turn 57 was then republished with the fleet split back into idle ships and
  214 where it was. Elder Race, whose launcher fetched turn 56 before the edit, got the expected
  note `ship 214: DROPPED, owned by Beta` and lost nothing.

- [x] **Reclaiming a civ removes its fleets.** `wipe_civ.wipe` cuts a civ's `FLET`s with the ships
  inside them, as well as its `SHIP`s, and `check` fails while one is left. A galaxy with
  DemoPlayer's ordered fleet wiped (tier5 `f3`) loads in the Resurgence client as BadGuy, resolves
  a turn and saves again (26 October 2026).

- [x] **Production queues are carried.**
  `merge_orders.py` carries creating, editing, reordering and deleting queues and entries, and
  planets joining, overriding and leaving them (`test_queues_merge.py`). A new queue is
  renumbered like a design when another player's object took its id, ship entries follow a
  renumbered design, and the planet counts are recounted from the merged links. Five blobs the
  merge wrote (a queue assigned, shared by two planets, overridden, two civs' queues with one
  renumbered, a queue naming a renumbered design) load in the Resurgence client, resolve a turn and
  save again with their queues intact (25 October 2026). In the live sandbox galaxy, tmog66's
  queue of a facility and a ship, assigned to four planets on turn 55, came through the referee
  (staged at `a715489`) with every planet still linked and building one of its entries in turn
  56, and no refusal notes (26 October 2026). Scan entries are carried for the six scans the
  scanning screen lists; a queue of two scans and six facilities made in the UI merges back
  byte for byte (`tier5/qs0` to `qs3`). Refused, with a note: an entry of item type 1 to 7,
  which the client's reader accepts but its queue editor never writes.

**Read off the client on 27 October 2026, not yet measured:** admirals and diplomacy. No save on
this machine holds an admiral or a treaty (all 440 captures under `client`, `server/saves`,
`server/tests/fixtures` and `server/galaxy_demo` have an empty `ADMS` in every civ and an empty
treaty list), so each layout below is the writer's and reader's, checked only for the empty case.

- **An admiral** is an `ADML` in the civ's `OWNR > DATA > ADMS`, after a `u32` count (writer
  `0x53B4A0`, `ADMS` read at `0x53D73C`, each `ADML` by `0x5A6FF0`): its object id, a `u32` count of the ships it commands
  (`Admiral:8`), one byte, and an `ADDT` (`0x5A4F20`): a `u32`-length name, a `u32` that is
  inferred to be the admiral type (the manual's transport, scout, defense and assault), a `u32`
  rule count, and per rule a one-byte `RULE` (its checkbox) followed by the rule's section. The
  same shape as a governor. The reader's rule factory (`0x5A4590`) knows 24 tags: `ATPL`, `ATSH`,
  `DFPL`, `FSHP`, `ICSH`, `LSSL`, `RECQ`, `RTCN`, `STLS`, `RLYP`, `RZPT`, `SPTS`, `TSCQ`, `TMMR`,
  `TMSC`, `STPF`, `TRSM`, `WUFL`, `WLSL` (the 19 `AdmiralRule*` classes) and `ASFL`, `ASAD`,
  `MASH`, `SCSP`, `SCRG` (`AddShipsToFleet`, `AssignNewShipsToAdmiral`, `ManShips`,
  `ScoutSpecificSuns`, `ScoutSunsInRange`). An unknown tag falls through to a store through a
  null pointer, so a rule the client does not know is likely to crash the load. Several rules
  hold lists of object ids (planets, suns, ships) and `RLYP`/`RZPT` six `u32`s that are probably
  a reference point and angles.
- **Assigning** an admiral writes its id into the ship's or fleet's `DYNO`, the `u32` after the
  has-orders byte (`DYNO+26`, `Ship:80`).
- **A treaty**, a proposal or a declaration of war among them, is not under any civ: the client
  keeps one registry (`0x86ED6C`) and `SaveGame` writes all of it after `SAVE`'s last section as
  a `u32` count and one `TRTY` v2 per treaty (writer `0x55A4D0`, reader `0x55BD00`). `TRTY` holds
  the treaty's object id and a `TRDT` v1: the two parties' civ ids, a `u32` kind (`1` declares
  war, from the setter `0x55AD90`, which also empties both item lists), four `u32`s not yet
  decoded (the memory capture of a war read `3`, `-1` and the turn twice), two bytes, then two
  item lists, inferred to be what the proposer demands and offers. An item is its own section:
  `CRLS`, `CRPT`, `RSLS`, `RSPT` (credits and resources, lump sum and per turn), `SLOS`, `SHBR`,
  `SHGM`, `SHWH` (sharing), `TRLE` (treaty length). Offline the client applies a proposal itself
  (`0x55C3E0`, from `0x56F310`); accept, decline, withdraw and delete go through `0x56F470`
  with an action 0 to 3. The memory report's `Treaty:16 = 3` for war is the field after the kind;
  the kind is `Treaty:12`.
- **A civ's stance** toward each civ it has met is in its `KNPL` v12: a `u32` count, then a
  36-byte record keyed by the other civ's id. The stance (0 neutral, 1 war, 2 cease-fire, 3
  peace, 4 alliance) is the `u32` at record `+12`: it reads 3 in `control_fork_played.dat` and 1
  in `war_fork_played.dat`, the same fork after a declaration of war. The whole record is in
  `server/dev_tools/knpl.py`.
- **`waronly`** (GSET index 29) reads in the client as two things: first contact sets the stance
  to war (`0x534FF3`), and the diplomacy screen's context menu adds "Propose New Treaty" greyed
  out (`0x4425BF`). The second does not hold in the UI: on `control_fork_played.dat`, `waronly
  = 1`, "Propose New Treaty" and "Declare War" are both available and every treaty kind can be
  chosen (28 October 2026). Sandbox players can propose treaties and declare war.

**Measured in one Single Player session on 27 October 2026 (`server/tests/fixtures/admirals`):**
admirals on `clean.dat` as GoodGuy, turns 110 to 112, with the turn length set to 30 days.
Turn 111 ticked on its own between `a2` and `a3`, once, without anyone ending the turn. Every
layout read off the client above held.

- **Creating an admiral** appends an `ADML`, raises the count, and raises `SAVE+0` to its id
  (674, then 675). The client writes it when the admiral is first assigned or saved, not while
  the editor is open. An assault admiral's type reads 2, a scout's 3.
- **A rule** is its checkbox byte and its section, as read. "When conquering transfer X
  stationed military" is `TSCQ` with X as a `u32`. "Man ships of a design in orbit of a planet
  with stationed military, if rank" is `MASH`, 22 bytes, holding the planet's object id (265,
  GoodGuy's HQ) among values not yet told apart. "Scout systems within N units of a sun" is
  `SCRG`, 37 bytes, ending with 260, likely the home sun's id, and N. Unticking a rule clears its byte and keeps
  the rule. Renaming rewrites only the name.
- **Assigning** writes the admiral's id into the ship's `DYNO`, the `u32` after the has-orders
  byte, and moves the ships-commanded counts. Choosing "none" writes 0. A manual move order
  keeps the ship's admiral.
- **Deleting** removes the whole `ADML` and lowers the count; `SAVE+0` stays. The delete button
  is greyed out while the admiral commands a ship.
- **A turn** leaves the admiral record as it was. The `SHPR` of the colony ship under "man ships
  fully" grew by 9 bytes in the tick, likely crew the rule brought aboard.
- **An admiral on a fleet** (9 November 2026, `fixtures/admirals` `af0` and `af1`, DemoPlayer's
  fleet 202 on `tier5/f1`): assigning admiral 203 wrote its id into the `FLET`'s own `DYNO`, the
  `u32` after the has-orders byte as for a ship, and the `ADML` counts one command, the fleet.
- **Not measured:** the other choices in each rule, and the other rule types.

**Measured in one Single Player session on 27 and 28 October 2026
(`server/tests/fixtures/diplomacy`):** diplomacy on `control_fork_played.dat` written with
`waronly = 0` and 30-day turns, at peace, turns 180 and 181, switching between GoodGuy and BadGuy
with `set_blob_player.py` to answer. Every capture's GSET reads `waronly = 1`, so the client
either ignores the setting on load or the capture rewrites it.

- **`TRDT`** is the proposer's and recipient's civ ids, the kind (0 trade, 1 war, 2 cease-fire,
  3 peace, 4 alliance, read from the client's tables), the status (0 pending, 1 binding, 2
  declined, 3 non-binding, 4 canceled; a war is created at 3 and a binding treaty reaches 3 when
  its length runs out, so 1 and 3 are both in force; `merge_orders.NON_BINDING` is that 3), an
  `i32` length (-2 until accepted), an `i32` start turn (-1 until accepted), the turn proposed,
  one deleted byte per party, then the proposer's demands and its offers.
- **Proposing** adds a `TRTY` and raises `SAVE+0` to its id. A military alliance stores no length
  item; the UI fixes it at 100 turns. A cease-fire also stores none; the UI says it binds for 20
  turns and a peace for 50. Map sharing is an `SHGM` in both lists. A trade with 10 metal as a
  lump sum is an `RSLS` (amount, resource 0 for metal); 1 credit per turn for 10 turns is a `CRPT`
  and a `TRLE` in the same list.
- **Withdrawing** a proposal removes the `TRTY`. **Declining** sets the status to 2.
  **Deleting** a declined treaty sets only the deleting party's byte and keeps the record.
- **Accepting** sets the status to 1, the length from the `TRLE` (or 20 for the cease-fire) and the
  start turn, and at once moves the lump sum between both civs' `OWPR` (metal 630 to 625 and 630 to
  635). Accepting the cease-fire also removed the war treaty and set both stances to 2.
- **Declaring war** removes the active trade treaty, adds a kind 1 treaty with status 3, length
  -1 and the turn twice, and sets both civs' stance to 1. The UI shows a civ at war in red,
  cease-fire in yellow and peace in green; grey before contact, likely stance 0.
- **A turn** leaves the treaties as they were.
- **A per-turn payment** reaches the recipient on the first tick after acceptance (sandbox, 13
  November 2026).
- **Not measured:** peace and alliance acceptance, a treaty running out (assumed to work, since
  the game's tick ends it), and the other item types.

- [x] **Admirals and diplomacy are carried as measured** (`merge_orders.py`,
  `test_admirals_diplomacy_merge.py`). An admiral is judged like a governor: a new id, a printable
  name, a type below 4 and only the 24 rule tags the reader knows. A ship names 0 or an admiral its
  civ holds as merged, following a renumbering; deleting one still in command is refused; the
  referee recounts each `ADML`'s ships. A treaty step is taken only as the client takes it,
  against the treaty as served, and refused when the other party changed that treaty earlier in
  the merge. On acceptance the referee moves the lump sums from the merged `OWPR` resources (a
  `u32` count of 5 at the second research field `+55`, then a resource byte and a `u32` per
  resource) and credits, and refuses it when the payer is short. Refused: an admiral on a fleet,
  another civ's admirals, a treaty ended any other way, and a stance moved without a treaty.
- [x] **A rival's colour and its territory checkbox are lost at every turn.** The diplomacy
  screen's Change Color (right-click a civ) and the checkbox beside each civ, which shows or
  hides its sphere of control on the map, do not survive to the next served turn (Robert, 6
  November 2026). `merge_orders.py` carries only what it lists, so whatever the client saves for
  them is dropped, and silently, since only a moved stance is named. Both are in the 36-byte
  `KNPL` record, read off the client (writer `0x532F20`, reader `0x5332E0`): `+29` a `u8`, the
  territory shown (the toggle `0x574930`), `+30` a `u8`, the rival's ships shown, `+31` a `u8`, 1
  to colour the civ by stance and 0 to use `+32`, a `u32` colour as `B, G, R, A` (Change Color's
  OK, `0x489B23`, through `0x5342D0`). Measured on 9 November 2026 (`fixtures/messages` `m3` to
  `m6`): Change Color to green wrote `+31` 0 and `+32` `01 F8 0D FF`, ticking the territory box
  wrote `+29` 1, and turning ship display off wrote `+30` 0, each in GoodGuy's record for BadGuy
  only. The merge carries all four for the submitter's own records (`test_display_merge.py`). The constructor sets `+29` to 0, `+30` and `+31`
  to 1. The player's own colour, territory and ships flag are `OWPR` fields at `Owner+0x210`,
  `+0x214` and `+0x215`. Both are the viewer's own display choices, so
  carrying them needs no legality check beyond the record being the submitter's own. Done when
  a Single Player capture pair locates both fields, the merge carries them for the submitter's
  own `KNPL`, and a colour and a hidden territory set on one turn are still there on the next.
  **Fixed** (13 November 2026): on the live sandbox tmog66 set guest-test to colour
  0xF904E9 with territory and ships shown on turn 166, and turn 167 and tmog66's served view
  both kept them, confirmed on screen by Robert.
