# CosmicSupremacy_patched20.exe — Patching Notes

## Goal
Get the original 2006 Cosmic Supremacy client EXE connecting to a local Python server at `localhost:8888` for SAND (sandbox) galaxy type, using the binary TCP protocol (RQLG/SAVE).

## Binary Layout Reference

| Section | Raw Offset | Virtual Address | Delta (VA = raw + delta) |
|---------|-----------|-----------------|--------------------------|
| .text   | 0x000400  | 0x401000        | +0x400C00                |
| .rdata  | 0x34C800  | 0x74E000        | +0x401800                |
| .data   | 0x3F7C00  | 0x7FA000        | +0x402400                |

Image base: 0x00400000

## Key Winsock IAT Entries (in .rdata)

| IAT Address | Function     | Ordinal |
|-------------|-------------|---------|
| 0x74E74C    | connect     | 4       |
| 0x74E758    | htons       | 9       |
| 0x74E770    | send        | 19      |
| 0x74E774    | recv        | 16      |
| 0x74E790    | closesocket | 3       |
| 0x74E794    | socket      | 23      |

## Architecture Discovery

### Dual Protocol
The game uses two protocols on the same port:
- **HTTP POST** to `/clientinterface.php` — for savegame, login, testconnection, etc.
- **Binary TCP** with 4-byte big-endian magic headers (RQLG, SAVE, LGIN, SUCC, FAIL)

### Connection Flow (0x563310 → 0x562EA0)
The wrapper at **VA 0x563310** (file 0x162710) orchestrates galaxy connection:

```
0x563310:
  +0x08: store galaxy_type at [esi+0x434]
  +0x0E: check [0x86EEF0] (online flag) and galaxy != 0x1A85
  +0x23: call vtable[2]          ← PRE-CALL (ClientUpdateCheck in original)
  +0x27: call 0x562EA0           ← THE CONNECT FUNCTION (our patch target)
  +0x33: save return value in bl
  +0x4A: call vtable[2]          ← POST-CALL
  +0x50: ret 4 (callee cleanup, returns al=bl)
```

### Original 0x562EA0 (352 bytes, orchestrator)
The original function was complex — not a simple connect:
1. **vtable[3]** at +0x3C: validation (abort if [0x86EEF0]!=0 && galaxy!=0x1A85)
2. **vtable[2]** at +0x5B: pre-connection step (ClientUpdateCheck)
3. **0x5F1D50** at +0x66: creates raw socket via `socket(2,1,0)`, stores at [ecx+4]
4. **String setup** at +0x6F: builds server info string at [esi+0x4AC]
5. **0x5F3650** at +0x121: the actual TCP connect (thiscall, 2 stack args)
6. **0x5E5B00** at +0x13D: stream reset (custom handle, NOT Winsock close)

### Global Flags (BSS zone, set at runtime)
- `[0x86EEF0]` — online mode flag (non-zero when online)
- `[0x86EEF1]` — alternate server flag
- `[0x86EEF4]` — alternate port value

### Object Layout (esi = connection object)
- `[esi+0x00]` — vtable pointer
- `[esi+0x04]` — Winsock socket handle
- `[esi+0x08]` — connected flag (byte)
- `[esi+0x434]` — galaxy_type (dword)
- `[esi+0x438]` — stream object pointer
- `[esi+0x43C]` — custom stream handle (NOT a socket! closed via 0x672E90)
- `[esi+0x4AC]` — server info string (std::string, MSVC SSO)
- `[esi+0x4CC]` — flag byte (set to 0 on connect)
- `[esi+0x4CD]` — flag byte (set to 1 on connect)

---

## Patches Applied

### Patch A — Rewritten 0x562EA0 (file 0x1622A0, 150 bytes)
Replaced the entire original 352-byte orchestrator with a minimal direct Winsock connect:

```asm
push ebx, esi, edi
mov esi, ecx              ; this pointer
; socket(AF_INET=2, SOCK_STREAM=1, 0)
push 0, 1, 2
call [0x74E794]           ; socket()
cmp eax, -1
je fail
mov [esi+4], eax          ; store socket handle
mov edi, eax              ; keep in edi for later use
; Build sockaddr_in on stack
sub esp, 16
mov word [esp], 0x0002    ; AF_INET
mov word [esp+2], 0xB822  ; port 8888 big-endian
mov dword [esp+4], 0x0100007F  ; 127.0.0.1
mov dword [esp+8], 0
mov dword [esp+12], 0
; connect(socket, &addr, 16)
push 16
lea eax, [esp+4]
push eax
push edi
call [0x74E74C]           ; connect()
add esp, 16               ; clean up sockaddr
test eax, eax
jne fail
; Success path
mov byte [esi+8], 1       ; connected = true
mov byte [esi+0x4CC], 0
mov byte [esi+0x4CD], 1
mov al, 1
pop edi, esi, ebx
ret

fail:
xor al, al
pop edi, esi, ebx
ret
```

**Note**: Original function had `ret 8` (callee cleanup of 2 args). Our replacement uses plain `ret` because 0x563310 calls us with no stack args (just `mov ecx, esi; call 0x562EA0`).

### Patch B — NOP at VA 0x563000 (file 0x162400)
Single byte `C3` (ret) — disables whatever function was at 0x563000. Purpose from previous session; always preserved.

### Patch C — NOP vtable[2] calls in 0x563310 (file 0x162710)
Two 2-byte NOPs:
- **Pre-call** at +0x23 (file 0x162733): `FF D2` → `90 90`
- **Post-call** at +0x4A (file 0x16275A): `FF D2` → `90 90`

**Why**: vtable[2] sends `ClientUpdateCheck V1.2\r\n<version>\r\n` on the socket at [esi+4]. In the original game, vtable[2] was called inside 0x562EA0 *before* socket creation, so it created its own connection. In our patched flow, the post-call uses our socket, sends ClientUpdateCheck, gets a response, then **closes the socket** — destroying the connection before RQLG can be sent.

### Patch D — NOP DIAG send (file 0x162300, 24 bytes)
NOPed the diagnostic `send("DIAG")` block that was used to verify socket+connect works. This was temporary instrumentation — confirmed the TCP connection succeeds, then removed.

---

## Bugs Found & Fixed

### 1. Vtable Corruption Crash (0x65005200)
**Cause**: Early version wrote IP bytes (127.0.0.1) to [esi+0..3], overwriting the vtable pointer with 0x0100007F.
**Fix**: Removed those writes; IP goes on the stack-based sockaddr_in instead.

### 2. ClientUpdateCheck Popup ("SUCC")
**Cause**: Server responded with SUCC magic bytes to the text-based ClientUpdateCheck protocol.
**Fix**: Server now responds with `\r\n` (empty line = no update needed). Game proceeds silently.

### 3. Stream Handle Crash (0x672ED5, ESI=0x5D0)
**Cause**: Wrote socket handle to [esi+0x43C] thinking it was the socket field. Actually it's a custom stream handle closed via 0x672E90 (not closesocket). Socket handle value was dereferenced as an object pointer.
**Fix**: Removed all writes to [esi+0x43C] and [esi+0x444].

### 4. Socket Killed by vtable[2] Post-Call
**Cause**: After our function creates the socket, 0x563310's post-call vtable[2] sends ClientUpdateCheck on it, gets the response, then closes the connection. Game shows "failed to connect" because the socket is dead before RQLG.
**Fix**: NOPed both vtable[2] calls in 0x563310 (Patch C above).

---

## Key Discoveries

1. **DIAG test proved socket+connect works**: Sending 4 bytes immediately after connect() showed up on the server, confirming Winsock calls are functional.

2. **vtable[2] reuses [esi+4] socket**: It doesn't create its own connection — it sends on whatever socket is at [esi+4]. The data stream was `DIAG` + `ClientUpdateCheck V1.2\r\n813186540\r\n` on one TCP connection.

3. **Single TCP connection architecture**: The game uses ONE persistent TCP connection for both ClientUpdateCheck and binary protocol (RQLG/SAVE/etc.). In the original flow, ClientUpdateCheck happened on its own connection (before socket creation), then a new socket was created for binary protocol.

4. **0x5E5B00 manages a custom stream abstraction**: The field at [esi+0x43C] is NOT a Winsock socket — it's a handle to an internal stream object closed via function 0x672E90.

---

## Session 2 — Deep Architecture Trace & Send Path Fix

### The Send Path Mystery

After the TCP connection worked (confirmed by DIAG test), the game still wasn't sending RQLG. Deep trace revealed why:

#### Stream Wrapper Architecture
The game uses a **stream wrapper** object (0x8C bytes, created by `0x562030→0x5611A0`) that sits between game logic and the connection object:

| Offset | Field | Purpose |
|--------|-------|---------|
| `[sw+0x00]` | vtable | Stream wrapper vtable at 0x77839C |
| `[sw+0x04]` | CRT fd | File descriptor for CRT `_write()` |
| `[sw+0x0C]` | mode | 0=TCP, 1=HTTP |
| `[sw+0x30]` | buffer ptr | Stream buffer base address |
| `[sw+0x3C]` | data size | Current data size in buffer |
| `[sw+0x78]` | conn obj | Connection object pointer |
| `[sw+0x7C]` | header validated | Set by 0x561100 |
| `[sw+0x7D]` | all data received | Set when recv completes |
| `[sw+0x80]` | original magic | Saved first 4 bytes before CMND wrap |
| `[sw+0x84]` | bytes sent | Running total for send loop |
| `[sw+0x88]` | pending flag | Set to 1 when data prepared but not sent |
| `[sw+0x89]` | process flag | **CRITICAL**: 0x561A00 skips if this is 0 |

#### The [sw+0x89] Discovery
`0x5611A0` (stream wrapper constructor) sets `[sw+0x89] = 1` **only when mode=0** (TCP). With mode=1 (HTTP), `[sw+0x89] = 0`, causing `0x561A00` to return immediately without processing the stream. This was the root cause.

#### RQLG Send Code Path (0x57A6B0)
```
0x563310     → connect (our patched 0x562EA0)
0x56DC30     → create stream wrapper (mode from 0x56208A)
0x5E6260     → write RQLG header (magic + flags) to stream buffer
0x5E5E10     → write galaxy_type (4 bytes) to stream buffer
0x5E6320     → flush (no-op when CRT fd=0)
0x561950(0)  → CMND wrap + conditional send (arg=0 = prepare only)
0x56EBA0     → receive loop (calls 0x561A00 internally)
0x561260     → check result
```

#### 0x561950 — The Send Gate
Called with `arg=0` from all 56 call sites in the online game flow. The function:
1. Calls `0x561350` (prep)
2. Byte-swaps first 4 bytes, checks if already CMND
3. If not CMND, calls `0x561710` to encrypt + wrap in CMND
4. **Checks arg**: if arg=0, sets `[sw+0x88]=1` (pending) and returns **without sending**
5. If arg≠0, calls `vtable[4]` to transmit via connection object

In the original HTTP design, the send happens inside vtable[7] (0x563660 does a full HTTP POST round-trip containing the pending CMND data).

#### 0x561A00 — The Receive Loop
1. Checks `[sw+0x89]`: if 0, returns immediately (this was the block)
2. Reads first 8 bytes via `vtable[7]` (stream header: magic + flags/size)
3. Validates header via `0x561100` (checks 4 ASCII chars)
4. Extracts payload size from flags/size dword (lower 26 bits)
5. Reads remaining payload bytes via `vtable[7]`
6. Calls `0x561310` to check if data is CMND-wrapped
7. If CMND, calls `0x5617D0` to decrypt/unwrap
8. If not CMND, skips unwrap — data used as-is

#### Connection Object Vtable (0x7785B8)
```
[0x00] = 0x562DF0  destructor
[0x04] = 0x5F1D50  socket creation
[0x08] = 0x5F1DB0  ClientUpdateCheck
[0x0C] = 0x5F1FA0  validation
[0x10] = 0x5635F0  SEND (vtable[4]) — online: HTTP POST, offline: Winsock send()
[0x14] = 0x563580  send variant
[0x18] = 0x563C20  transfer
[0x1C] = 0x563BC0  RECV (vtable[7]) — online: HTTP round-trip, offline: Winsock recv()
[0x20] = 0x5F2E90  check
[0x24] = 0x5F1FC0  connect+stream setup
```

**vtable[4] offline path** (`0x563636`): Calls `0x5F27E0` directly — pure Winsock `send()` on `[conn+4]`, sends in 64KB chunks.

**vtable[7] offline path** (`0x563C02`): Calls `0x5F2D10` directly — pure Winsock `recv()` on `[conn+4]`.

Both check `[0x86EEF0]` (online flag) at entry. Online flag is non-zero at runtime, so the online path is taken by default.

#### CMND Protocol
`0x561710` encrypts data via `0x5F5BF0` and wraps in CMND header. `0x5617D0` does the reverse on receive. Encryption key/algorithm unknown, but the wrapping can be bypassed entirely since `0x561310` (receive-side) already checks for CMND magic and skips unwrap if not present.

### Patches Applied (Session 2)

### Patch D2 — ~~Mode=0~~ REVERTED in patched19 at VA 0x56208A (file 0x16148A)
~~`6A 01` → `6A 00` (push 1→push 0)~~
**REVERTED to `6A 01` (mode=1/HTTP)** in patched19.exe. See Session 3 notes for why.
Originally changed mode to TCP(0) to enable `[sw+0x89]=1`. Reverted because mode=0 prevents flush from fixing the placeholder payload size in the stream header (see Session 3).

### Patch E — vtable[4] force offline at VA 0x5635FA (file 0x1629FA)
`74` → `EB` (je→jmp)
Forces vtable[4] to always take the offline path, which calls `0x5F27E0` (Winsock `send()`) directly instead of trying the HTTP POST path (which needs server URL strings that are empty).

### Patch F — vtable[7] force offline at VA 0x563BCA (file 0x162FCA)
`74` → `EB` (je→jmp)
Forces vtable[7] to always take the offline path, which calls `0x5F2D10` (Winsock `recv()`) directly instead of trying the HTTP round-trip path.

### Patch G — Force send in 0x561950 at VA 0x561995 (file 0x160D95)
`74 4F` → `90 90` (je→NOP NOP)
Removes the `arg==0` check that skips the vtable[4] send call. Now `0x561950` always sends via vtable[4] after CMND wrapping (or skipping it per Patch H).

### Patch H — Skip CMND encryption at VA 0x561987 (file 0x160D87)
`74 07` → `EB 07` (je→jmp)
Always skips the CMND wrap call (`0x561710`). Raw stream-format data (e.g. RQLG header + galaxy_type) is sent directly, unencrypted. The receive side handles this naturally — `0x561310` detects non-CMND data and `0x5617D0` (decrypt) is skipped.

### Patch I — Force [sw+0x89]=1 at VA 0x561214 (file 0x160614) *(Session 3)*
`75 07` → `90 90` (jne→NOP NOP)
Forces the stream wrapper process flag to 1 regardless of mode. Required because reverting D2 (mode=1 for flush fix) would otherwise leave `[sw+0x89]=0`, causing `0x561A00` (receive) to skip stream processing.

### Patch J — vtable[5] force offline at VA 0x56358A (file 0x16298A) *(Session 3)*
`74 4C` → `EB 4C` (je→jmp)
Forces vtable[5] (`0x563580`) to always take the offline path. The online path calls `0x562EA0` (connect), creates a new socket, then sends data via HTTP POST (`0x563370`) — destroying our existing socket and sending unwanted ClientUpdateCheck data. Offline path calls `0x5F2790` for raw send.

**Discovery**: vtable[5] has THREE callers of `0x562EA0` (Patch A) in the binary. The online paths of vtable[4] and vtable[5] both re-connect before sending. Patch E fixed vtable[4]; Patch J fixes vtable[5].

### Patch K — Disable vtable[2] (ClientUpdateCheck) at VA 0x5F1DB0 (file 0x1F11B0) *(Session 3)*
`55` → `C3` (push ebp → ret)
Makes vtable[2] return immediately without doing anything. This is the nuclear option: even if vtable[2] is called from ANY location (not just the two we NOPed in 0x563310), it now does nothing.

**Why**: DIAG diagnostic test revealed ClientUpdateCheck text was still being sent on our socket after Patches C1/C2. The raw text `ClientUpdateCheck V1.2\r\n<version>\r\n` arrived right after DIAG on the same TCP connection. Since all vtable[2] call sites in 0x563310 were already NOPed, the call must come from elsewhere. Disabling the function itself eliminates all possible callers.

---

## Stream Format

With CMND bypass, messages use the raw stream format:

| Offset | Size | Endianness | Description |
|--------|------|------------|-------------|
| 0 | 4 | **Big-endian** | Magic (ASCII, e.g. `RQLG`, `SAVE`) — byte-swapped by `0x5E6260` |
| 4 | 4 | **Little-endian** | Flags/Size dword (native x86): upper 6 bits = flags, lower 26 bits = payload size |
| 8 | N | native | Payload (N = payload size from above) |

**IMPORTANT**: Mixed endianness! Magic is big-endian, flags/size is little-endian (native x86 dword).

Header validation (`0x561100`): checks bytes 0-3 are uppercase letters, digits, or spaces.

Size extraction: `total_message_size = 8 + (LE_dword[4:8] & 0x03FFFFFF)`.

#### Placeholder Size Issue (Session 3 discovery)
`0x5E6260` writes the header with **placeholder** payload_size = `0x3FFFFFF` (67M). The actual size is fixed later by the **flush function** (`0x5E6320`) — but **only in mode=1 (HTTP)**. Mode=0's flush path calls `0x5E5BE0` which is a no-op for fd=0.

---

---

## Session 3 — Placeholder Size Bug & Endianness Fix

### Root Cause: Why RQLG Never Arrived at Server

Connection 61360 was accepted but the server saw no data. Investigation revealed TWO interlocking issues:

#### Issue 1: Placeholder Payload Size (0x3FFFFFF)
`0x5E6260` writes the 8-byte stream header with `payload_size = 0x3FFFFFF` (67,108,863 bytes) as a placeholder. The size is meant to be fixed by the **flush function** (`0x5E6320`):

- **Mode 1 (HTTP) flush** → Reads `[sw+0x44]` linked list to find header offset, calculates actual `payload_size = [sw+0x40] - header_offset - 8`, writes it into the header. ✓
- **Mode 0 (TCP) flush** → Calls `0x5E5BE0` (CRT write wrapper) which is a no-op when fd=0. Does NOT fix the size. ✗

With Patch D2 (mode=0), the header was sent with the 67M placeholder. The server then waited for 67M bytes of payload that never came.

#### Issue 2: Mixed Endianness
- Magic (bytes 0-3): big-endian (byte-swapped by `0x5E6260`)
- Flags/Size (bytes 4-7): **little-endian** (native x86 `mov dword`)

The server was reading both as big-endian (`struct.unpack('>I', ...)`).

### Fix Applied

**Client**: Revert Patch D2 (mode back to 1/HTTP) so flush fixes the header. Add Patch I to force `[sw+0x89]=1` regardless of mode.

**Server**: Changed `flags_size` parsing from `'>I'` (big-endian) to `'<I'` (little-endian). Also changed `build_stream_response` to write flags_size as little-endian. Added sanity check for unreasonably large payload sizes (>1MB).

### Patch I — Force [sw+0x89]=1 at VA 0x561214 (file 0x160614)
`75 07` → `90 90` (jne→NOP NOP)

In the stream wrapper constructor (`0x5611A0`), `[sw+0x89]` is set to 1 **only when mode=0**:
```
0x561214: jne 0x56121D    ; if mode != 0, skip
0x561216: mov byte [esi+0x89], 1
```
Patch I NOPs the jne, so `[sw+0x89]=1` is always set regardless of mode. This enables `0x561A00` (receive) to process the stream even with mode=1.

Combined with D2 revert (mode=1): flush fixes the header AND receive still works.

### Key Architecture Details Discovered

#### 0x5E6260 (write stream header)
1. Byte-swaps magic to big-endian
2. Builds flags_size with placeholder `0x3FFFFFF`
3. Writes 8 bytes to buffer via `0x5E5E10`
4. Pushes current write offset to `[sw+0x44]` linked list (for later size fix by flush)

#### 0x5E6320 (flush) — Mode-Dependent Behavior
- **Mode 0**: Calls `0x5E5BE0(arg, 0)` → no-op for CRT fd=0. Does NOT fix header size.
- **Mode 1**: Reads `[sw+0x44]` linked list for header offset. Calculates `actual_size = [sw+0x40] - offset - 8`. Writes corrected flags_size into header buffer in-place.

#### Stream Manager → Stream Wrapper → Connection Object Chain
```
[0x86F198] = stream_manager (0x58 bytes, constructed by 0x562230)
  [sm+0x3C] = connection_object = [0x86F194]

[0x56DC30] → jmp 0x562030 → allocates 0x8C bytes → constructor 0x5611A0
  [sw+0x78] = [sm+0x3C] = connection_object

Confirmed: [sw+0x78] == [0x86F194] — same connection object used for connect AND send/recv.
```

---

## Session 4 — ClientUpdateCheck Response Fix & vtable[6] Patch

### Root Cause: Why Game Stayed on Connection Settings Popup

After Patches J/K eliminated ClientUpdateCheck contamination on the binary socket, the game still wouldn't proceed past the connection settings popup. Investigation revealed TWO issues:

#### Issue 1: Wrong ClientUpdateCheck Response
The standalone ClientUpdateCheck function at **VA 0x56C770** (separate from vtable[2]) sends the check, receives the response, then compares it against specific keywords at VA 0x56C950+:

```
0x56C950: push 8; push "UpToDate"; push response; push 0; call compare
0x56C96B: test eax, eax → if "UpToDate", set [0x86EFAD]=1, proceed
0x56C978: compare response to "Corrupt" → error/update path
0x56C98E: compare response to "Update" → update path
0x56C9A4: compare response to "Patch" → patch path
```

String references confirmed:
- `VA 0x778B8C`: `"UpToDate"` — success, proceed past popup
- `VA 0x778B84`: `"Corrupt"` — error state
- `VA 0x778B7C`: `"Update"` — triggers update
- `VA 0x778B74`: `"Patch"` — triggers patching

Our server was responding with `\r\n` (empty line), which doesn't match any keyword. Game stayed on popup.

**Fix**: Server now responds with `UpToDate\r\n` for raw TCP and `UpToDate` for HTTP POST.

#### Issue 2: vtable[6] (transfer) Not Patched
The function at 0x56C770 uses **vtable[5]** (`0x563580`, Patch J forced offline) to SEND the ClientUpdateCheck data, and **vtable[6]** (`0x563C20`, "transfer") to RECEIVE the response.

vtable[6] checks `[0x86EEF0]` (online flag) at entry, just like vtable[4]/[5]/[7]:
```
0x563C21: cmp byte [0x86EEF0], 0
0x563C34: je 0x563C5E          ; offline path → direct to 0x5F2960
          ; online path: call 0x563660 (HTTP round-trip) first, then 0x5F2960
```

Online path calls `0x563660` (complex HTTP transfer function) which would fail on our raw TCP socket. Since `[0x86EEF0]` is non-zero, the je is not taken.

**Fix**: Patch L forces the offline path.

### Patch L — vtable[6] force offline at VA 0x563C34 (file 0x163034) *(Session 4)*
`74` → `EB` (je→jmp)
Forces vtable[6] (`0x563C20`) to always take the offline path, which goes directly to `0x5F2960` (the actual receive) without calling `0x563660` (HTTP round-trip). Same pattern as Patches E, F, J.

### Key Architecture Detail: Standalone ClientUpdateCheck (VA 0x56C770)
This is a separate function from vtable[2] (`0x5F1DB0`). Flow:
1. Creates connection object via `operator new(0x4D0)` at 0x56C82C
2. Calls `0x563310` with galaxy_type=`0x1A85` (special value that skips vtable[2] calls inside 0x563310)
3. `0x563310` → our patched `0x562EA0` → TCP connect to localhost:8888
4. Builds "ClientUpdateCheck V1.2\r\n\<version>\r\n" message
5. Sends via vtable[5] (offline path via Patch J) → `0x5F2790` → Winsock `send()`
6. Receives via vtable[6] (offline path via Patch L) → `0x5F2960` → Winsock-based recv
7. Compares response to "UpToDate" at 0x56C950
8. If match: sets `[0x86EFAD]=1`, proceeds past connection popup
9. If no match: stays on connection popup

### Connection Object Vtable — Complete Offline Patch Set
All four send/recv vtable entries now forced to offline:
```
[0x10] = vtable[4] 0x5635F0  SEND    — Patch E (0x5635FA: 74→EB)
[0x14] = vtable[5] 0x563580  SEND    — Patch J (0x56358A: 74→EB)
[0x18] = vtable[6] 0x563C20  RECV    — Patch L (0x563C34: 74→EB)
[0x1C] = vtable[7] 0x563BC0  RECV    — Patch F (0x563BCA: 74→EB)
```

---

## Session 5 — Galaxy Dialog Bypass & LGIN Unblocking

### Root Cause: Why SAVE Response Didn't Lead to LGIN

After receiving SAVE with "#Galaxy@Pass#abcdef" payload, the game never sent LGIN. The connection just timed out. Investigation revealed the SAVE payload processing involves a modal dialog:

#### Discovery: SAVE Payload Processing Flow (VA 0x57A870-0x57A8F4)

The galaxy work function processes the SAVE payload through a complex dialog-based UI system:

```
0x57A875: cmp [esp+0x4C], 0        ; check SAVE payload length
0x57A879: je 0x57A8F4              ; if empty, skip to fallback
0x57A87B: cmp [0x819628], 0         ; check global flag
0x57A87D: jne 0x57A8F4             ; if non-zero, skip to fallback

; Parser path:
0x57A88C: call 0x499B40             ; create parser from SAVE payload
0x57A8A0: call 0x65BA31             ; DoModal — show galaxy selection dialog
0x57A8A5: cmp eax, 1                ; IDOK?
0x57A8A8: jne 0x57A8DE             ; if not OK, skip extraction
0x57A8B6: call 0x57A3E0             ; extract field from parser+0x1494
0x57A8C8: call 0x4053B0             ; copy to local string

; LGIN gate (both must be non-zero):
0x57A909: cmp [esp+0xAC], 0         ; "#Galaxy@Pass#" string length (always 13) ✓
0x57A916: cmp [esp+0x8C], 0         ; extracted field length (0 = blocked!) ✗
0x57A91D: je 0x57AB50              ; if zero, SKIP LGIN entirely
```

#### 0x65BA31 — DoModal (Galaxy Selection Dialog)

This function is vtable[83] of the parser object (vtable 0x761754). It:
1. Loads Windows **dialog resource RT_DIALOG ID 201** (0xC9) via FindResourceA/LoadResource/LockResource
2. Gets parent HWND from 0x65B5A7
3. Creates modeless dialog via `CreateDialogIndirectParamA` (0x65B87B)
4. Runs modal message loop via 0x6574F7
5. Returns `[esi+0x44]` — the dialog result (IDOK=1 on success)

**Dialog procedure** at 0x65B291: Handles WM_INITDIALOG (0x110) by calling vtable[84] = **0x499DD0 (OnInitDialog)**.

#### 0x499DD0 — OnInitDialog (SAVE Payload Parsing)

The OnInitDialog parses the SAVE payload:
1. Splits `[parser+0x1474]` (the payload string) by **"###" delimiter** (string at 0x7618C0)
2. Iterates through key-value pairs (each element is 0x20 bytes in the split array)
3. For pairs where key starts with **"1"** (string at 0x756818): stores value to local
4. Also processes **"Player:"** (string at 0x7618C4) fields via [parser+0xD58]
5. Adds items to list control at [parser+0x338]
6. The selected item value should end up at [parser+0x1494]

**Expected SAVE payload format**: `1###galaxy_name###` (key-value pairs delimited by "###")

#### 0x57A3E0 — Field Extraction

Reads `[parser+0x1494]` and returns it. This is the field that must be non-empty for LGIN.

#### Fallback Path (0x57A8F4)

If `[0x819628] == 0x200`, uses global string at 0x819614 instead of dialog extraction. Both start at 0 in the EXE, so this path isn't taken normally.

#### Why the Dialog Fails in Our Context

The dialog (resource 201) is meant for user interaction — selecting a galaxy from a list. In our automated sandbox flow, the dialog either:
- Fails to create properly (no valid parent HWND context)
- Shows but waits for user input that never comes
- Returns != 1 (IDCANCEL or error), causing extraction to be skipped

Either way, [parser+0x1494] stays empty → [esp+0x8C] = 0 → LGIN is blocked.

### Parser Object Structure (0x499B40, ~0x14B4 bytes)

```
[+0x00]    vtable = 0x761754 (final, after inheritance chain)
[+0x54]    type ID (0xC9 = dialog resource 201)
[+0x58]    type ID low 16 bits (0xC9)
[+0x5C]    0 (zeroed, unused for dialog load)
[+0x60]    0 (zeroed, unused for dialog load)
[+0x68]    flag (0, from constructor arg chain)
[+0xA0]    sub-object vtable = 0x76174C
[+0x324]   sub-object vtable = 0x761740
[+0x338]   list control (0x485EE0 init, items added by OnInitDialog)
[+0x6F8]   sub-object (0x409000 init)
[+0xA28]   sub-object (0x409000 init)
[+0xD58]   player list (0x415510 init, "Player:" prefix)
[+0x1474]  std::string: SAVE payload (arg1 of constructor)
[+0x1494]  std::string: extracted field (populated by dialog, read by 0x57A3E0)
```

### Patch M — Skip dialog call at VA 0x57A8A0 (file 0x179CA0)
`E8 8C 11 0E 00 83 F8 01 75 34` → `B8 01 00 00 00 90 90 90 90 90`
(call 0x65BA31; cmp eax,1; jne → mov eax,1; NOPs)

Replaces the 0x65BA31 DoModal call with `mov eax, 1` (force IDOK) and NOPs. The extraction at 0x57A3E0 still runs, reading an empty string from [parser+0x1494]. This alone doesn't unblock LGIN — Patch N is also needed.

### Patch N — NOP LGIN gate check at VA 0x57A91D (file 0x179D1D)
`0F 84 2D 02 00 00` → `90 90 90 90 90 90`
(je 0x57AB50 → 6x NOP)

Removes the check that skips LGIN when the extracted field string is empty. Combined with Patch M, this allows LGIN to proceed with "#Galaxy@Pass#" + empty extracted field + additional data.

### SAVE Payload Format

Updated server to send `1###SAND###` (key-value pairs, "###" delimited):
- Key "1" → galaxy name "SAND" (matches OnInitDialog parsing)
- Format: `key###value###` for future compatibility

### Protocol Sequence (after Patches M+N)

```
Client → Server: RQLG (4-byte version payload)
Server → Client: SAVE (payload: "1###SAND###")
  [Patch M skips dialog, forces eax=1]
  [Extraction reads empty string from parser+0x1494]
  [Patch N bypasses empty-string gate check]
Client → Server: LGIN ("#Galaxy@Pass#" + empty + additional_data)
Server → Client: SUCC (empty payload)
  Game shows "logged in successfully"
  Game shows "retrieving galaxy-data from the server..."
Client → Server: (new connection for galaxy data, may send RQTN)
Server → Client: SAVE (galaxy data)
```

### New Binary Message: RQTN (0x5251544E)

Discovered at VA 0x56E550: sent when game detects out-of-sync or out-of-date state.
Payload: 4 bytes. Response: SAVE with galaxy data.
Only sent when `[0x86F1A2] != 0` (out-of-sync flag).

## Current Status
- TCP connection to localhost:8888 confirmed working
- ClientUpdateCheck protocol handled — responds "UpToDate"
- vtable[2] interference eliminated (Patch K)
- vtable[4]/[5]/[6]/[7] all forced to offline Winsock paths
- CMND encryption bypassed (Patch H)
- 0x561950 forced to always call send (Patch G)
- Flush correctly fixes header payload size (mode=1)
- [sw+0x89]=1 forced for receive processing (Patch I)
- Server endianness fixed (magic=BE, flags_size=LE)
- RQLG→SAVE exchange confirmed working (Session 4)
- **Patch M bypasses galaxy selection dialog**
- **Patch N unblocks LGIN with empty extracted field**
- **Ready for LGIN→SUCC test**

## Expected Test Flow (Updated)
1. Game connects → ClientUpdateCheck → "UpToDate" → proceed
2. Galaxy work function sends RQLG (version payload)
3. Server responds SAVE ("1###SAND###")
4. Patch M skips dialog, forces success
5. Patch N bypasses empty-field gate check
6. Game sends LGIN ("#Galaxy@Pass#" + "" + data)
7. Server responds SUCC
8. Game shows "logged in successfully"
9. Game requests galaxy data (new connection, possibly RQTN)
10. Server responds SAVE (galaxy data)

## Server Protocol Handling (cs_server.py)
The server auto-detects protocol by peeking first bytes:
- `POST`/`GET` → HTTP handler
- `ClientUpdateCheck` → responds with `UpToDate\r\n`, then continues listening for binary
- Stream-format messages → parses 8-byte header (magic=BE, flags_size=LE), extracts magic + payload, responds in same format
- Handles: RQLG→SAVE, LGIN→SUCC, RQTN→SAVE, STCO→SUCC

## Patch Summary (All 15 Patches in patched20.exe)

| Patch | VA | File Offset | Original | New | Purpose |
|-------|------|-----------|----------|-----|---------|
| A | 0x562EA0 | 0x1622A0 | (130 bytes) | Custom Winsock connect | Direct TCP connect to 127.0.0.1:8888 |
| B | 0x563000 | 0x162400 | Various | C3 (ret) | Disable original connect function |
| C1 | 0x563333 | 0x162733 | FF D2 | 90 90 | NOP vtable[2] pre-connect |
| C2 | 0x56335A | 0x16275A | FF D2 | 90 90 | NOP vtable[2] post-connect |
| D2 | 0x56208A | 0x16148A | 6A 01 | 6A 01 | REVERTED (keep mode=HTTP) |
| E | 0x5635FA | 0x1629FA | 74 | EB | vtable[4] force offline send |
| F | 0x563BCA | 0x162FCA | 74 | EB | vtable[7] force offline recv |
| G | 0x561995 | 0x160D95 | 74 4F | 90 90 | Force send in 0x561950 |
| H | 0x561987 | 0x160D87 | 74 07 | EB 07 | Skip CMND encryption |
| I | 0x561214 | 0x160614 | 75 07 | 90 90 | Force [sw+0x89]=1 |
| J | 0x56358A | 0x16298A | 74 4C | EB 4C | vtable[5] force offline send |
| K | 0x5F1DB0 | 0x1F11B0 | 55 | C3 | Disable vtable[2] entirely |
| L | 0x563C34 | 0x163034 | 74 | EB | vtable[6] force offline recv |
| M | 0x57A8A0 | 0x179CA0 | E8..75 34 | B8 01..NOPs | Skip galaxy dialog, force IDOK |
| N | 0x57A91D | 0x179D1D | 0F 84 2D 02 | 90x6 | NOP empty-field LGIN gate |
| O | 0x57A875 | 0x179C75 | 39 5C 24 4C 74 79 3B C3 75 75 | E9 A9 00 00 00 90x5 | Skip parser, jump to LGIN |

### Patch O — Skip parser entirely at VA 0x57A875 (file 0x179C75) *(Session 6)*

**Root cause analysis (Session 6):**
The ESP stack offset theory from Session 5 was WRONG. Detailed `ret` instruction analysis showed:
- 0x5E6260: `ret 8` (cleans 2 args) ✓
- 0x5E5E10: `ret 8` (cleans 2 args) ✓
- 0x5E6320: plain `ret` (no stack args) ✓
- 0x561950: `ret 4` (cleans 1 arg) ✓
- 0x561260: `ret 4` (cleans the `push ecx` at 0x57A7DF) ✓

All RQLG send pushes are cleaned by callees. The `push ecx` at 0x57A7DF (string ptr for receive)
is cleaned by 0x561260's `ret 4`. **ESP is balanced at 0 throughout.**

The REAL problem: the parser constructor at 0x499B40 creates a ~0x14B4 byte MFC dialog object
on the stack at [esp+0x100]. Even with DoModal skipped (Patch M), the constructor's base class
chain (0x4BB1C0 → 0x4095E0 → 0x65B383) initializes window management structures that likely
require a valid MFC context. The constructor crashes, the SEH handler at 0x71313C catches it,
and the function shows the default error message "The server is currently down" (string at
0x77A58C, initialized at 0x57A704 on first entry).

**Fix:** Jump from 0x57A875 directly to 0x57A923 (the LGIN "logging in..." code), completely
bypassing the parser block (0x57A87F–0x57A8F2), the [esp+0xAC] check, and the [esp+0x8C] check.

All strings needed for LGIN are already initialized before 0x57A875:
- ESP0+0x98: "#Galaxy@Pass#" string (constructed at 0x57A84A)
- ESP0+0x78: empty extracted field string (initialized at 0x57A84F)
- ESP0+0xB8: extra data (initialized at 0x57A936, inside LGIN code)

**Patch (10 bytes at file 0x179C75):**
```
Original: 39 5C 24 4C 74 79 3B C3 75 75
          cmp [esp+0x4C], ebx; je 0x57A8F4; cmp eax, ebx; jne 0x57A8F4
New:      E9 A9 00 00 00 90 90 90 90 90
          jmp 0x57A923; nop; nop; nop; nop; nop
```

**Note:** Patches M and N are now redundant (the parser block they patch is skipped entirely)
but remain in the binary harmlessly.

## ESP Analysis Reference (Session 6)

Stack tracking through 0x57A79B–0x57A875 (ESP0 = ESP after function prologue):

| Instruction | Operation | ESP Delta | Notes |
|------------|-----------|-----------|-------|
| 0x57A79B | push 1 | -4 | RQLG magic flag |
| 0x57A79D | push 0x52514C47 | -8 | RQLG magic |
| 0x57A7A4 | call 0x5E6260 | 0 | ret 8 cleans both |
| 0x57A7A9 | push 4 | -4 | Version data size |
| 0x57A7AF | push eax | -8 | Version data ptr |
| 0x57A7B2 | call 0x5E5E10 | 0 | ret 8 cleans both |
| 0x57A7B9 | call 0x5E6320 | 0 | Flush, plain ret |
| 0x57A7BE | push ebx | -4 | Send finalize arg |
| 0x57A7C1 | call 0x561950 | 0 | ret 4 cleans push |
| 0x57A7DF | push ecx | -4 | String ptr for receive |
| 0x57A7EB | call 0x56EBA0 | -4 | Plain ret, NO cleanup |
| 0x57A7F2 | call 0x561260 | 0 | **ret 4 cleans the push ecx** |
| 0x57A83E | push 0x77A54C | -4 | "#Galaxy@Pass#" literal |
| 0x57A84A | call 0x404AB0 | 0 | ret 4 cleans push |
| 0x57A875 | (check point) | **0** | ESP balanced ✓ |

## Session 7 — Merged patched24 (patched20 + patched17 game-load bypass)

### Problem
patched20 (patches A-O) gets stuck at "logging in..." because LGIN data never reaches the server over binary TCP. Multiple attempts to fix this (Patches P, Q) all failed. Root cause unknown — possibly socket closed after RQLG processing, pointer invalidation, or SEH-caught exception.

### Solution: Merge with patched17
patched17 was an earlier binary that bypasses the entire login/galaxy-load sequence and jumps straight into a running galaxy with ticks. It has patches in the **game state loading area** (0x49xxxx, 0x56DBF0, 0x577C1A, etc.) that are completely non-overlapping with patched20's networking patches.

**patched24 = patched20 (networking A-O) + patched17 (game-load bypass)**

All 500 patched bytes verified — no conflicts between the two patch sets.

### Patches from patched17 (game-load bypass, not in patched20)

| File Offset | VA | Size | Original | New | Purpose |
|-------------|------|------|----------|-----|---------|
| 0x005519 | 0x406119 | 1 | 15 | 5C | Unknown (early init) |
| 0x095C81 | 0x496881 | 6 | 0F 8D.. | E9 01.. | Force jump in galaxy init |
| 0x0964E3 | 0x4970E3 | 6 | 0F 8D.. | E9 06.. | Force jump in galaxy init |
| 0x096905 | 0x497505 | 6 | 0F 85.. | 90x6 | NOP conditional skip |
| 0x096CCA | 0x4978CA | 2 | 74 6D | 90 90 | NOP conditional jump |
| 0x09810B | 0x498D0B | 5 | CCx5 | E9 22 01.. | Code cave: jump to handler |
| 0x098129 | 0x498D29 | 1 | 05 | E1 | Adjust offset/flag |
| 0x098232 | 0x498E32 | 5 | CCx5 | 5F 5E 5D 5B C3 | Pop regs + ret (exit handler) |
| 0x098278 | 0x498E78 | 1 | 74 | EB | Force jump (je→jmp) |
| 0x0D4BC8 | 0x4D57C8 | 2 | 10 0E | 1E 00 | Data patch |
| 0x16CFF0 | 0x56DBF0 | 7 | 80 7C.. | B0 01 C3 90x4 | Force return true + NOP |
| 0x16D4EF | 0x56E0EF | 6 | 0F 85.. | 90x6 | NOP conditional skip |
| 0x16D533 | 0x56E133 | 2 | 74 54 | 90 90 | NOP conditional jump |
| 0x17701A | 0x577C1A | 3 | 51 7C 85 | A0 F1 86 | Load from [0x86F1A0] instead |
| 0x17702A | 0x577C2A | 2 | 74 58 | 90 90 | NOP conditional jump |
| 0x17902D | 0x579C2D | 2 | 74 0E | 90 90 | NOP conditional jump |
| 0x377785 | 0x778F85 | 2 | 20 AC | 00 34 | String/data patch |

These patches collectively bypass the game's login verification, galaxy selection dialog, and server authentication checks, allowing the game to load directly into a running galaxy without completing the LGIN handshake.

### Patch R — Skip "Synchronizing Data..." wait at VA 0x405C8E (file 0x00508E)

`75` → `EB` (jne→jmp)

The game's main tick loop checks `[0x86F1A1]` — a sync flag that's 0 until the server confirms data sync via binary TCP (SAVE/SUCC responses set it to 1 at 0x56DD78/0x56E52A). When the flag is 0, the game shows "Synchronizing Data..." and loops without processing ticks.

With the p17 game-load bypass (which skips the binary TCP RQLG/LGIN/SUCC handshake), no TCP connection gets established, so the flag never gets set. The game loads into the galaxy but hangs at "Synchronizing Data..." when the first turn tick completes.

Fix: Force the sync check to always skip the wait, so the game proceeds with tick processing regardless of sync state. This is safe for single-player sandbox mode.

**patched25.exe = patched24 + Patch R**

### patched26 — Revert Patches G, H, I (forced send pipeline)

With the p17 game-load bypass, no binary TCP connection gets established (p17 skips 0x563310 → 0x562EA0). Patches G/H/I from patched20 force the send pipeline to always send immediately via vtable[4], which tries to call Winsock `send()` on an invalid/dead socket — causing the game to hang (spinning wheel) whenever it tries to send data (e.g., ship movement commands).

**Reverted patches:**
- **G** (0x160D95): `90 90` → `74 4F` — restore original `je` in 0x561950, sends are deferred (set pending flag) instead of forced
- **H** (0x160D87): `EB` → `74` — restore original `je` in 0x561950, CMND wrapping check is normal
- **I** (0x160614): `90 90` → `75 07` — restore original `jne` in constructor, process flag set only for mode=0

With these reverted, the send function behaves as in the original game: when called with arg=0, it sets `[sw+0x88]=1` (pending) and returns immediately without blocking. Since there's no TCP connection, the pending data is never sent, and the game continues normally. This is the same behavior as p17 standalone.

**patched26.exe = patched25 minus G/H/I = p17 bypass + p20 connect/vtable/dialog + Patch R**

## File Inventory
- `CosmicSupremacy.exe` — original unmodified binary (reference for disassembly)
- `CosmicSupremacy_patched17.exe` — historical: first working login bypass (game-load patches only)
- `CosmicSupremacy_patched20.exe` — historical: full networking stack (patches A-O, LGIN broken)
- `CosmicSupremacy_patched24.exe` — superseded (merged p20 + p17, missing Patch R)
- `CosmicSupremacy_patched25.exe` — superseded (hangs on ship commands due to forced send patches)
- `CosmicSupremacy_patched26.exe` — superseded (still hung on ship commands, vtable offline patches conflict)
- `CosmicSupremacy_patched27.exe` — **CURRENT canonical**: p17 + Patch R (working single-player)
- `cs_server.py` — dual-protocol Python server (HTTP + raw stream binary)
- `galaxy_generator.py` — generates fresh galaxy blobs from template for loadgame
- `save_parser.py` — decodes/encodes save blob binary format
- `template_sandbox_t0.dat` — reference turn-0 save blob used by galaxy_generator
- `SandboxGalaxy_local.csgalaxy` — galaxy pass file for SAND type
