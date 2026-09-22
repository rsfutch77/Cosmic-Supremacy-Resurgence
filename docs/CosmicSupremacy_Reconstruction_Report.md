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
testconnection         , ping/health check
loadgame               , load a save game
savegame               , save current game state
savegamelist           , list available saves
loadgov                , load governor settings
savegov                , save governor settings
govlist                , list available governors
uploadcivname          , upload civilization name
listcivnames           , list civ names (userid=%d)
getcoa                 , get coat of arms image (coaid=%d)
listcoa                , list coat of arms (userid=%d)
uploadcoa              , upload coat of arms image
passedtutorial         , mark tutorial as complete (userid=%d&pass=%s)
entertestbedgalaxy     , enter test bed galaxy
getplayerfame          , retrieve player fame points
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
    +23           u32 production points accumulated toward the current item
    +27           u8  recruitment rate, a percentage of food diverted into
                      military growth each turn
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
                  moves between the two byte for byte.
    +167          PROD, the production queue, 29 bytes; its payload carries
                  the queue's own fields, at +29 the object id of what is
                  being built, and a nested section naming it, FCLT for a
                  facility
    +255          ENLI, 4 bytes

`WLTH` appears inside `PROD`'s payload rather than beside it, and is replaced by `FCLT` when a facility is queued. The rest of `PLPR` is undecoded and mixes the planet's population, stores and derived economy with the job decision, which is why a submitted job change is carried as the citizen array alone and never as the whole section.

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

- **Falsifiable prediction, not yet tested:** there is a **second leg vector at `+64/+68/+72`**. On a live order, `(_Mylast − _Myfirst)` there should be a multiple of 28.
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

*How it was found, before anything was run.* Ruled out the server (no score-related `action=` has ever reached the stub, the log carries only `savegame`, `testconnection`, `listcivnames`, `listcoa`, `getcoa`, so the list is client-side); ruled out a missing per-civ section (`OWPR`, `CVTR`, `SERV`, `GOVS`, `ADMS`, `SPQS`, `USSE` are byte-identical across all three civs in a turn-5 capture, and score itself lives on the object at `Owner:28`); ruled out `KNPL` (at turn 185 every civ knows every other, the injected one included, 36-byte records keyed by the other civ's object id, second word the turn contact was made). What was left was the one per-civ field a clone duplicates: across three separately generated galaxies it reads **20 for `GoodGuy` and 21 for `BadGuy`, always**, and every injected civ carried its donor's value.

*The A/B that confirmed it.* Two `.dat`s built from the same turn-185 capture, **differing in four bytes**, `Ceti`'s `Owner:4`, 20 (colliding with `GoodGuy`) versus 22. Same galaxy, same scores, same contact state, loaded one after the other:

| | `Ceti` `Owner:4` | Diplomacy list | Overview score list |
|---|---|---|---|
| **A** | 20, collides with `GoodGuy` | all three civs | `GoodGuy` and `BadGuy` only, **and it stays that way across turn boundaries** (185 → 191+) |
| **B** | 22 | all three civs | all three, **but only after the first turn boundary** |

So the score list holds one row per distinct `Owner:4`, and the collision, not the injection, is what hid the civ. The A row matters as much as the B row: ticking turns does not fix a collision, so the boundary is not what was missing.

**What `Owner:4` actually is remains open.** `ejbo_annotations.json` recorded it as `Human=0, AI=21`, which suggested an account id rebound to the local player on load. **That is falsified**: live, the local player's civ reads **20**, matching its blob value. What is measured is narrower, the field is per-civ, distinct across generated civs, and the score list keys on it. A slot index, a roster index and an account id all fit.

#### Open items

[ ] **The score list is built at a turn boundary, not at load, so a civ with a correct `Owner:4` is still missing for the whole of a galaxy's first turn.** Confirmed on the B galaxy: parked at turn 185 straight off the `.dat`, the score list showed two rows; after one boundary it showed three, with no other change. This is the residual limitation of the fix and it is **not addressed**, it bites every freshly injected galaxy until its first tick, and a galaxy parked at 3600s turns stays wrong for an hour. Two ways round it, neither implemented: tick one boundary before showing the list (`fast_turns.py <secs>` collapses the current turn, one write starts the clock), or render the standing somewhere else entirely, since the numbers are readable per civ off `Owner:28` without the client's table. Worth revisiting properly: find what the boundary path does to the table that the load path does not, which is also the question of whether anything *else* the UI shows is only refreshed at a boundary.

[ ] **`EXSY` is cloned, not reset, and that is a cheat rather than a cosmetic defect.** An injected civ inherits the donor's explored-systems map, so cloning a developed donor tells the newcomer where the home planets are. Two things keep it from mattering yet and neither is a fix: the AI player does not read `EXSY` (it keeps its own discovery set, persisted per civ name), and cloning from a turn-1 capture inherits an empty map, so only the developed-donor fixture leaks. The fix needs the record layout, which is not decoded, the leading `u32` reads 63 on a civ that had explored 105 systems, so it is not a plain count and the obvious guess is already falsified. **That `EXSY` is the explored-systems map is itself inferred** from the tag and from its size tracking exploration across two civs; confirm it while decoding it.

[ ] **An injected civ gets no ships.** `SHIP`/`DYNO` records are galaxy-level rather than per-civ, so a starting colony ship is a separate job. Against civs that start with two, that is a real handicap at turn 1, the civ can build its own only if the donor's homeworld had a shipyard.

[ ] **Homeworld placement is one hardcoded policy.** New civs are placed as far from every claimed planet as possible, which is the right default for testing an AI (contact becomes something it has to earn) but not the only one worth having, random placement is what a real generator does and is the only way to test a hostile neighbour two systems away, and clustered or per-team placement follow from it. Wants a `--placement` option rather than a second function.

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

`SaveGame` at `0x0048B350` has exactly one caller in the binary, the Save/Load dialog at `0x0048B950`, which is reached through a message map, and the normal game has no such dialog. So a server cannot ask a client to submit its state; `client/dev_tools/trigger_save.py` gets a blob out by calling `SaveGame` in a remote thread. That is not memory *editing*, it invokes the engine's own routine and writes no game state, but it is still process injection, so the read direction is not yet a clean protocol operation.

There is a much better candidate for what the original did. `0x0056EBC0` writes an **`STCO`** section that includes ship routes via the same `Route::Write` used by `ROUT`, gated on `[0x0086F4F8]` and `[0x0086F1A2]`, and reached from `0x00574E10` inside the testbed init path at `0x00577160`. That looks like per-turn order submission, far lighter than uploading a whole galaxy.

[ ] decode the **`STCO` section and the path that uploads it** (`0x0056EBC0`, gated on `0x0086F4F8` /
`0x0086F1A2`, reached via `0x00574E10` from `0x00577160`). It serialises ship routes with the same
`Route::Write` as `ROUT`, which makes it the strongest candidate for the original per-turn
order-submission format, the missing half of the multiplayer loop. If it is what it looks like, the
server learns player intent from a small `STCO` upload instead of a full save, and the remote-thread
`SaveGame` call in `trigger_save.py` becomes a dev convenience rather than the mechanism.

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

[ ] **What those 30 bytes are is not established.** The `NWDB` pair reads `1` against `2` in ASCII,
which would be a count rendered into news text, and a count rendered at generation time is carried by
the blob while one recomputed at read time is not. That distinction is this section's own standard
and is the thread to pull first.

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
            ...     six part lists
            u32     the owning civ's object id, at the end
      +1    u8      zero, counted by the DSGN length word

    the civ's design count, a u32 in DATA's own bytes before its first DSGN

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
client could draw, not what the stock one does.

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

How many items an engagement raises is also unsettled. The `cycle.dat` battle raised two on one turn
at one position, one in each civ's `OWNR`, which looked like each side's own view of it. The
two-player battle raised one, for the defender only. One of those is a special case and it is not yet
known which.

[ ] **A losing attacker can be told nothing at all, and why is unexplained.** In the two-player
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
        u32 system id, u32 sun id, str sun name, u32 undecoded, u32 planet count
        per planet:
            u32   last known owner civ id, 0 for never seen
            u32   an observed quantity, undecoded
            str   planet name

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

[ ] **Clearing another civ's remembered map is not fog-correct, and it is what
`wipe_civ.forget_civ` currently does.** Strictly, a civ that has not looked again
should keep remembering what it last saw: this section already records that
`EXSY` is a cache which goes stale on purpose, and that one civ held a planet as
owned by 206 while another had the correct 202. Wiping an abandoned player
currently clears every civ's memory of them, which is a deliberate trade made
because a planet anybody may now colonise, carrying a dead empire's name and a
capital marker, misleads a player about the board rather than about history. The
fog-correct version clears only the wiped civ's own table and lets rivals keep
theirs until they re-scout. Not attempted, and it needs a rule for what a rival
sees when they do look again.

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

Consequences. **Any blob comparison has to mask this dword**, or two honest computations of the same
turn look different, which is exactly what a canonical hash must not do. The
three-bytes-plus-a-top-byte split is suspicious enough that the record boundary may be off by one,
with the final byte a flag in its own right. And it is worth sweeping the other sections for the same
signature, which is cheap now that it is known: a field that changes between two otherwise identical
runs, or whose top byte becomes `0xFF` after a load.

[ ] `KNPL` is worth decoding properly rather than piecemeal, for this field. The second reason
recorded here, that a `KNPL` still naming removed planets is why a fog-filtered blob fails to load,
is **falsified**: its records are keyed by **civ** id rather than planet id despite the name, and
dropping systems removes no civs. See "Filtering a galaxy" below.

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

Still open: whether the **civ-trait** allowance re-grants the same way. The trait popup did not
reappear on either of the last two pushes, so it has not been possible to test, and its commit path
is unmeasured beyond the fact that it writes `Owner:756` and `Owner:360` on first use.

[ ] stop the **"Customize Your Home World" popup reappearing after a loaded save**, the trigger
behind the bug above. Observed on the first successful `.dat` load: the galaxy came back correctly,
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

[ ] **Not yet mapped to behaviour:** `speed`, `density`, `rank`, `startticks`, `premium`, `score_breakeven`, `civilization_changes`, `hse_multiplier`, `colonymodule_multiplier`. `civilization_changes = 5` is a good guess at the civ-trait budget, matching the trait block at `Owner:744…940`, but that is inference.

[ ] **`corruption_multiplier = 100` is the first handle on corruption we have.** Corruption has resisted location in memory entirely, see the memory report, and here is a galaxy-level knob for it. Changing it and watching what moves is a far cheaper search than diffing planets, and it is the same trick that finally located `Planet:368`: make the thing vary.

**`turnlength` drives the game's clock, and the engine re-applies it AT EVERY TURN BOUNDARY.** A galaxy set to 75 was observed writing 75 back over a driven 15 at each boundary, so a galaxy configured above the floor runs at its configured rate with nothing driving it, `ai.py --drive` is only needed to go faster than the game wants to.

Two things make this easy to get wrong, and both cost this project a false conclusion:

* **At LOAD the live value at `0x0080AA08` reads 3600 regardless of the config.** Reading it there and stopping says the setting does nothing. The refresh runs at a turn BOUNDARY, and on a 3600-second galaxy the first boundary is an hour away, so nothing can be observed without driving turns first.
* **Below 60 the value never appears at all.** Two sites in the binary clamp it, `cmp esi, 60 ; mov [0x0080AA08], esi ; jg skip ; mov [0x0080AA08], 60`, so a galaxy configured at 10 presents as 60 every turn, which reads like the engine ignoring the config rather than honouring it and rounding up.

* **The in-game countdown is tied to neither the config nor the live value.** A blob carrying `turnlength = 43200` came up with `0x0080AA08` reading 3600 and the UI showing 45 minutes. Nothing was found that reads back what the display is derived from, so a turn clock that has to be trusted belongs outside the client.

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

### Patches 12–17, Turn pipeline bypasses (T1–T5, 22 bytes)

Six patch sites bypass server sync checks in the turn pipeline so turns can fire without a real game server. These are applied only to the Resurgence EXE (not TestBed).

**`CosmicSupremacy_Resurgence.exe` is `CosmicSupremacy_TestBed.exe` plus exactly these 22 bytes and nothing else**, measured by a whole-file comparison in September 2026. The two builds are otherwise identical, so the choice between them is exactly the choice of whether a client may compute a turn.

| file | VA | TestBed | Resurgence | what it is |
|---|---|---|---|---|
| `0x16CFF0` | `0x0056DBF0` | `80 7C 24 04 00 75 0C` | `B0 01 C3 90 90 90 90` | a predicate rewritten to `mov al,1; ret`, so a sync check always passes |
| `0x16D4EF` | `0x0056E0EF` | `0F 85 94 00 00 00` | `90` × 6 | `JNZ 0x56E189`, the first guard on the homeworld prompt |
| `0x16D533` | `0x0056E133` | `74 54` | `90 90` | `JZ 0x56E189`, the second guard on the homeworld prompt |
| `0x17701A` | `0x00577C1A` | `51 7C 85` | `A0 F1 86` | an operand repointed from `0x857C51` to `0x86F1A0` |
| `0x17702A` | `0x00577C2A` | `74 58` | `90 90` | a galaxy-join branch removed |
| `0x17902D` | `0x00579C2D` | `74 0E` | `90 90` | a load-path branch removed |

**Two of the six land on the homeworld customisation prompt, not on the turn pipeline at all.** `0x0056E0D0` is the routine that decides whether to offer the prompt; with both of its guards NOPped it offers it unconditionally, on every session. That is the whole explanation for the prompt appearing on every pushed turn, and it was ours rather than the engine's.

### The three one-time setup prompts (September 2026)

Each is a Win32 dialog resource, not an in-engine overlay, so the main window's title still reads `Galaxy Map` while one is up. `client/dev_tools/list_dialogs.py` enumerates them by walking the process's windows, since the title cannot be trusted.

| id | title | decision routine | gate |
|---|---|---|---|
| 210 | Customize Your Home World | `0x0056E0D0` | `[session+0x19B]`, and `0x00508C60(session)` |
| 218 | Customize Your Civilization | — | — |
| 225 | Pick your Civilization Name and Coat of Arms | `0x0056E700` | the local civ's `Owner:384` must be non-zero |

`0x0052A8D0`, the "session" those guards read, is **the local player's `Owner` object**: it loads `[0x00857904]`, dereferences it and returns the object address minus `0x2C`. Since the reference cell holds the object address minus 8, a guard reading `[session+0x1B4]` is reading `Owner:384`.

**`Owner:384` is a count, not a flag.** A civ's `OWPR` section is exactly `138 + Owner:384` bytes long, measured at values 0, 1 and 7, so writing a value appends that many one-byte records. Their meaning is undecoded and the civilisation traits are the obvious candidate, `GSET.civilization_changes` being 5. Setting the field does silence prompt 225 and does survive a save and reload, and it is still the wrong fix, because it fabricates per-civ state to suppress a cosmetic dialog. `client/dev_tools/patch_hide_setup_prompts.py` returns from `0x0056E700` instead, one byte, fabricating nothing.

**The homeworld click counters are not `.data` globals.** `0x00842AE4` through `0x00842AF0` have no absolute references in `.text`. They are fields of the prompt's own singleton, which lives at `0x00840BC8`, at offsets `0x1F1C` through `0x1F28`, and `0x00498E10` zeroes all four every time the prompt is prepared. So they read zero after a load because the dialog is constructed once per process and cleared on every open, not because a save failed to carry them.

**Side effect:** Applying T1–T5 removes the Next Turn button from the UI. This is intentional for the multiplayer build, turns are advanced externally via `fast_turns.py`, not by player clicks.

### What T1–T5 skip, read out of the file (September 2026)

**None of the six removes a call**, and the code on both sides of every one of them is still
present, so a patched client runs the same routines the original does.

| site | what it reaches |
|---|---|
| T1 `0x0056DBF0` | a leaf predicate rewritten to return true. Its own two calls are getters; of its two callers, one selects the string `Connected` over `Disconnected` and the other runs a block it would otherwise skip |
| T2 `0x0056E0EF`, T3 `0x0056E133` | both guards of `0x0056E0D0`, the homeworld prompt decision routine, not the turn pipeline at all |
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

[ ] The same function also carries an **upper** bound, `cmp ebx, 0xA8C0` (43,200 = 12 hours) at `0x0052D34F`. Untouched, and presumably the longest turn the original game offered.

### Summary

Patches 1–4 (54 bytes) redirect all network traffic from the dead production servers (`www.cosmicsupremacy.com`, `cosmicsupremacy.com`, and a hardcoded IP) to `127.0.0.1:8888`, where the local stub server (`cs_server.py`) listens. Patch 1 converts a conditional branch (JZ) to an unconditional jump (JMP), forcing the client to always take the "success" path past a connection-validation check.

Patches 5–11 (13 bytes) bypass save/load validation checks in the game’s persistence code, which are needed for testbed galaxy saves to function against the local stub server.

Patches 12–17 (22 bytes, T1–T5) bypass turn-pipeline sync checks, enabling external turn control. Applied only to the Resurgence EXE.

### EXE Variants

| EXE | Patches | Next Turn Button | Galaxy File | Purpose |
|-----|---------|-----------------|-------------|---------|
| `CosmicSupremacy.exe` | None | Yes |, | Unmodified original |
| `CosmicSupremacy_TestBed.exe` | 1–11 | Yes | `TestBedGalaxy_local.csgalaxy` | Manual testing with interactive turn button |
| `CosmicSupremacy_Resurgence.exe` | 1–17 (incl. T1–T5) | No | `SandboxGalaxy_local.csgalaxy` | Production multiplayer, turns controlled by `fast_turns.py` |
| `CosmicSupremacy_Resurgence.exe` | + 18–27 (optional) | No | any | AI development, sub-60s turns. **Do not ship**: it changes a game rule, not plumbing, and a galaxy built on it runs faster than the original ever allowed |
| `CosmicSupremacy_Player.exe` | 1–11, plus the one byte for prompt 225 | Never created | a pushed `.dat` | Multiplayer player build. TestBed without T1–T5, so it cannot compute a turn and the setup prompts stay gated |

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
- Saves are named `TestBed Save 1`, `TestBed Save -1`, etc. with `gameid=-1` as the client's "allocate new slot" sentinel.
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

[ ] **The lock records who last claimed the client, not who is using it.** `take_client_lock` clears
a stale lock by asking whether the holder's pid is alive, which was written for a holder that died
mid-hold, since a legitimate hold lasts a whole turn and a timeout would break it. A holder that
exits normally and deliberately leaves the client running produces the same stale lock, and the next
tool through is waved past it. Seen live: a script that opened a galaxy for inspection finished, left
the client up, and left a lock naming a dead pid while other work was still in flight on that
machine. A free lock and a free machine are independent facts, and the tooling cannot tell them
apart.

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

[ ] **HAZARD, `CommitPopulation` auto-conscripts any population above `space/10`.**
Anything calling it must expect the citizen vector to shift underneath a stale index,
which is why `actions.conscript` re-reads the list live rather than trusting the
snapshot.

