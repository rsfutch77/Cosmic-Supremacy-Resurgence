# Cosmic Supremacy — Resurgence

**Bringing a classic space strategy game back online.**

[Cosmic Supremacy](http://www.cosmicsupremacy.com) was a multiplayer 4X space
strategy game released circa 2006.  Players colonised planets, researched
technology, designed fleets, and competed in persistent galaxies. Long turn based play (hours between ticks) and automated governors enabled deep strategy and always online play for players across timezones battling huge fleets of custom ships.

The server has been offline for years, but the original client EXE still exists.
This project aims to **reverse-engineer the server protocol and build a modern
replacement backend** so the game can be played again.

## Project goals

1. **Understand the original client** — extract assets, map out the HTTP API it
   expects, and document game mechanics (tech tree, ship design, galaxy rules).
2. **Build a compatible server** — a Python (FastAPI) backend that speaks the
   same protocol so the unmodified (patched for localhost) client can connect.
3. **Preserve and share** — make the findings, tools, and server code available
   so anyone who remembers the game can help bring it back.

## Repository layout

```
exploration/          Analysis artifacts from reverse-engineering the EXE

prototype/            Working prototype (patched client + stub server)
  client/             Patched EXE and .csgalaxy launcher files
  server/             Python HTTP stub server
```

## Quick start

1. Install Python 3.10+
2. Start the local server for development:
   ```
   cd prototype/server
   python cs_server.py
   ```
3. Drag the .csgalaxy file onto the .EXE

## Tech stack

| Layer    | Technology |
|----------|------------|
| Client   | Original Windows EXE (MFC / DirectX 9)|
| Server   | Python · FastAPI · SQLite |
| Protocol | HTTP/1.0 POST |

## Status

The project is in early development.  The stub server handles enough of the
protocol to complete the tutorial and load demo galaxies.  Save/load game data is captured in binary-safe format for further analysis.

See `exploration/CosmicSupremacy_Reconstruction_Report.md` for the full
reverse-engineering reference.

## Contributing

This is a preservation and fan project.  If you played Cosmic Supremacy and
want to help, contributions are welcome — whether that's protocol analysis,
server implementation, documentation, or testing.

## License

The original game assets remain the property of their creator, Erwin. We have tried to reach out to Erwin, and hope to involve them in the project. This patch will remain completely free in the spirt of the original game.
