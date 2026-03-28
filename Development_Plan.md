The patched `CosmicSupremacy_patched.exe` launches, connects to our server, and renders exactly as the original. All the UI, 3D rendering, game logic, and assets are already compiled into the EXE. **We do not rebuild the frontend.** The reconstruction is a backend implementation only.

### What this means
- The EXE is the client. Players install and run `CosmicSupremacy_patched.exe` on Windows.
- The server is the only new code we write. It must speak the game's original HTTP/1.0 protocol.
- Bug fixes go in via targeted binary patches to the EXE
- Source code extraction / recompilation is not pursued — not practical for a release-build MFC/C++ app.

### Minimum patch to run locally
`CosmicSupremacy_patched.exe` — the original binary with four patches applied:
1. `0x0017926c` — `JE` → `JMP`: forces debug server mode (localhost:8888)
2. `0x003776e0` — `www.cosmicsupremacy.com` → `127.0.0.1:8888` (login host redirect)
3. `0x003776f8` — `cosmicsupremacy.com` → `127.0.0.1:8888` (login host redirect)
4. `0x00378b98` — `88.116.31.107` → `127.0.0.1` (galaxy server IP redirect)

Future bug-fix patches follow the same pattern: locate in Ghidra, patch bytes, document offset + before/after.

### Backend Stack
- **FastAPI (Python)** — speaks the original `HTTP/1.0 POST application/x-cosmicsupremacy` protocol natively; no protocol translation layer needed
- **SQLite** (development) → **Firebase Firestore or PostgreSQL** (production) for persistent storage
- Auth is handled server-side; Replaces original auth

### What the Server Stores
| Data | Storage | Notes |
|------|---------|-------|
| User accounts | DB table | userid (int PK), username, bcrypt password hash |
| Session tokens | DB table or cache | The "passhash" the EXE sends is actually a server-issued JWT |
| Game save blobs | DB table | Opaque binary/text — stored and served without parsing |
| Governor blobs | DB table | Same — opaque, client-owned |
| Galaxy registry | DB table | Active galaxies, type, player list, current turn |
| Civ names | DB table | One per user |
| Coats of arms | File/blob storage | Per-user images uploaded by EXE |
| Player fame | DB table | Accumulated across galaxies |

### Web Portal (Lightweight)
The server also serves a minimal HTML portal at `GET /` (the game opens a browser to this on first run). It provides galaxy browsing, account creation, and `.csgalaxy` file downloads. This is plain HTML/CSS — no React, no build step.

---

## Phase 1 — Data Format Discovery + Working Single-Player

**Goal:** Get the patched EXE connecting to a real server and completing a full game loop. Understand the save blob format. Single-player tutorial galaxy functional end-to-end.

**Steps:**
1. Run the patched EXE against `cs_server.py`, load the Demo Galaxy, play several turns — capture every request/response pair in `cs_server.log`
2. Decode the `data=` blob in `savegame` / `loadgame` — determine if it is opaque (store-and-return) or requires server-side parsing for multiplayer turn reconciliation
3. Implement proper single-player save/load cycle: EXE saves → server persists → EXE reloads correctly
4. Implement user registration and login against the real DB (SQLite in dev)
5. Serve `.csgalaxy` files from the web portal for Tutorial and Demo galaxy types
6. Confirm the 36-step tutorial walkthrough (Section 19) completes successfully

---

## Phase 2 — Multiplayer + Production Hosting

**Goal:** Multiple players, hosted publicly, all galaxy types working.

**Pre-work:**
1. Pull cosmicsupremacy.com Wayback archive — fill remaining stat gaps and get the original web portal design as reference for our HTML portal
2. Resolve save blob format (from Phase 1 logs) — determine if turn reconciliation requires server-side parsing
3. Write full project plan with milestones

**Key Systems (Priority Order):**
1. Multi-player turn sync — hold state between players, advance turn when all submit (or timeout)
2. Galaxy lifecycle — Sandbox, Unranked, Ranked galaxy types with join/leave/expire logic
3. Galaxy-Fame leaderboard — persistent scoring across galaxies
4. Production hosting — swap SQLite for Firebase Firestore or PostgreSQL, TLS termination
5. HTTPS patch to EXE — WinInet `INTERNET_FLAG_SECURE` flag so passwords travel over TLS
6. Web portal — galaxy listing, registration, `.csgalaxy` download, fame leaderboard
7. Known bug fixes — binary patches for any player-reported bugs from the original game

---

## Phase 3 - Recreate Website?
