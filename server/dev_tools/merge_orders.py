"""
merge_orders.py: build the next authoritative blob from players' returned saves
================================================================================
    python merge_orders.py authoritative.b64 \
        --from DemoPlayer=playerA.b64 --from BadGuy=playerB.b64 --dat merged.dat
    python merge_orders.py authoritative.b64 --from BadGuy=playerB.b64 --list

The rule is merge orders, never states: only the parts of a returned state that
express the submitter's intent are taken, and only for objects that civ owns.
Every submission is judged against the state as it was served, never against
the blob as it accumulates other players' orders. Ownership is decided by the
served blob, so rewriting an owner field acquires nothing; the change is
dropped and named.

What is accepted
----------------
Each rule names the original client's commands it stands for
(`online_commands.py`); a command's offline branch is the change it makes in a
submission. `COMMANDS` lists all 56, those with no rule among them.

**Ship orders** (`STCO`), `SHIP > DYNO`. A move or a colonise is three edits
inside the ship's `DYNO`: the `SHCO` order-type byte (1 move, 3 colonise), the
has-orders byte, and an appended `ROUT` plus `u32`. Taking the order is copying
the `DYNO`.

**Research topic** (`STRS`), `OWNR > DATA > OWPR`, the technology id written
twice, 8 bytes apart, after the completed technologies (`research_offsets`): at
`+32` and `+40` only while a civ has its two starting technologies. Copied
field by field: the rest of `OWPR` is the civ's property block (coat-of-arms
count, flags).

**Current production** (`STPR`, `HRPR`), `PLNT > PLPR > PROD`: what the planet
is building, its own fields and a nested section naming it (`WLTH` for nothing,
`FCLT` with the facility type at `+13`, `SCAN`, or `SHIP` with a design id).
Copied whole, unless the served item was hurried, which the UI does not let be
swapped, it names any other kind, or it starts a facility the client would not
allow (`facility_rules.can_build`: researched, fits the free space, the
hyperspace pair, the per-planet limit), judged on the served planet and
research. The planet's link to a production queue, `+2..+8`, is part of the
copy and is judged with the queues below.

**Production queues** (`NWSQ`, `MDSQ`, `DLSQ`, `STSQ`), `OWNR > DATA > SPQS`: a
`u32` count, then one `SPRQ` per queue holding its object id, a `u32` count of
the planets using it, a byte, and a `PRQU` with the name and the entries in the
order the UI shows them. An entry is an entry id, a count, an infinite flag, a
count mode and an item: a facility type (item type 0), a design (item type 8)
or a scan (item type 9; route is 2, fleet 3). The client's queue editor offers
nothing else. A planet joins a queue through its `PROD`: a flag at `+2`, the
queue's id at `+3`, and at `+7` the id of the entry it is building, 0 after a
manual override; Wealth clears all three and is the only way out. Carried for
the civ's own queues and planets: creating, editing, reordering and deleting
queues and entries, and the links with the production that comes with them. A
new queue takes a free object id as a governor does and its planets follow it;
a ship entry follows a renumbered design. Refused: an entry naming a design the
civ does not hold in the merged galaxy, a facility type the galaxy lacks, a
scan the scanning screen does not list, any other item type, which the editor
never writes, a planet naming a queue or entry the merged galaxy lacks, and
deleting a queue a planet still uses. The planet counts are recounted from the
merged links. The civ's `OWPR` `+132..+143` move with any production change,
queued or not, and are not carried.

**Ship designs** (`CRSD`, `TGSD`, `MOSD`, `RNSD`, `DLSD`), `OWNR > DATA >
DSGN`. A new design arrives
as a second `DSGN`/`SDPR` pair, the civ's design count raised by one and
`SAVE+0` raised to the new object id. The client allocates a new object id as
one past the object count, so two players designing on the same turn claim the
same id; whoever is merged second is renumbered, along with the id in the
`DSGN` payload and in any production queue that names it. A queue naming a
design the galaxy does not hold makes the engine refuse the whole galaxy, so
such a queue is refused. Deactivating or reactivating one of the civ's own
designs is carried: the design's last byte (`ShipDesign+0x114`), 1 inactive.
A name is 1 to 32 printable characters with no leading or trailing space or
tab, not `Unnamed` in any case, and unlike the civ's other designs' names in
any case (`0x0053A380`). Renaming one of the civ's own designs rewrites the
name in its `SDPR` and is carried for any design. Modifying one rewrites its
`SDPR` parts and deleting one removes its `DSGN` and lowers the count; both
are carried only for a design not in use as served, as the Ship Design screen
opens a design in use read-only and greys Delete (`0x0050E490`): no ship of
the civ's is built to it, no planet of its builds it, no queue of its lists
it, and no admiral rule of its holds its id. The editor keeps a modified
design's name and owner. A civ's last design is not deleted, as the referee
finds the count from a design. A new or a modified design with no scanner is
refused: the editor starts every design with one, and the client closes on a
design without one (`0x005479C0`). The space and cloaking checks the editor's
OK makes (`0x004A5D40`) are not encoded for a new or a modified design.

`OWPR` `+49`, `+77`, `+78`, `+100` and `+110` also move in a design submission
and are not carried: they move without a design too and differ between
identical designs, and `+100` is a counter the engine advances by 7 every tick.

**People** (`CHCZ`, `CRAS`, `RTMI`): the citizen array in `PLNT > PLPR`, the
stationed military array that follows it, and a ship's crew in `SHPR`, judged
as one thing because conscription and crewing move people between them. A
submission never ticks, so a civ's people grow in none of the arrays within
it. Retiring stationed military erases the chosen soldiers' records
(`0x004F5220`) and nothing else, so everybody missing from a submission is a
soldier retired; a citizen is drafted first. Also: no soldier becomes a
citizen again, no existing soldier record is altered, and nobody changes
hands. The per-citizen value and opaque bytes are judged against the state
served, across all three arrays at once. Across a tick the total does change:
the engine's recruitment makes soldiers from food.

`PLPR+36` is a `u32` population count and `PLPR+40` begins that many
nine-byte records, kept sorted by job:

    +0  u8   job id, 0 farmer, 1 worker, 2 scientist, 5 miner, 6 banker
    +3  u32  the owning civ's object id
    +7  u8   a per-citizen value, permuted by a reassignment rather than changed

Soldiers are the same record with job id 3, in a second array that follows: a
`u32` count at `PLPR+40+9*citizens`, then the records. A ship's crew is counted
at `SHPR+4` with the records at `SHPR+8`. Moving somebody moves the record
byte for byte. Only these arrays are copied, never the rest of `PLPR`. The
per-citizen owner id usually equals the planet's owner, but a colony keeps the
owner of the colonists that founded it, so a civ can hold citizens on another
civ's planet. The checks compare how many people each owner holds, not their
jobs. People are carried only on the submitter's own planets and ships; a
change on a planet or ship another civ owns is refused and named, its own
citizens included. The per-civ save writes such a planet as a population with
no citizens (`0x4EE641`) and such a ship with no crew (`0x4E8640`), so the
original client never managed them.

**Military recruitment rate** (`PLRR`), `PLPR+27`, a percentage in one byte.

**Facility sales** (`SLFC`), the planet's facility list in `PLPR`: a `u32`
count, then `(type, count)` pairs. The planet screen's Sell sells one at a
time (`PlanetProperties::SellFacility`, `0x004F8580`): the type's count drops
by one, or the type leaves the list, and the civ is paid 5% of the type's
cost from the client's facility table, halved in a speed galaxy and rounded
up. Selling a hyperspace transmitter takes it out of the civ's energy links
and selling a receiver empties the planet's. Carried for the civ's own
planets, as lower counts of the types served; the referee pays the credits. A
hurried ship keeps the shipyard and a hurried scan the Central Defense Agency
(`0x004F5160`). A facility that appears, and a sale on another civ's planet,
are refused.

**Energy links** (`STEL`), a planet's `ENLI`: a `u32` count of the
transmitter planets a hyperspace receiver draws on, then their ids. The
receiver's menu lists the civ's own planets holding a transmitter
(`0x005251E0`) and a pick adds or removes one (`0x0046B4E0`). Carried for the
civ's own planets with a receiver, as any set of those planets, judged after
the sales above; an empty list is carried too.

**The setup prompts' confirms** (`CUCV`, `CUHW`), once each. The civ-trait
prompt writes the ten trait values in `OWNR > CVTR` and sets a flag in `OWPR`;
the homeworld prompt adds to the homeworld's three rate bytes (`PLPR+4..+6`)
and its space (`PLPR+11`) and sets another. A confirm is carried only when its
flag was clear as served and is set in the submission, and only as the dialog
could have made it: trait values the dialog gives and rates within 25..80,
spending `GSET.civilization_changes` or `GSET.homeworld_changes` exactly, on
the planet the client takes for the homeworld (the one founded earliest).
Without the confirm the prompt would come back on every served turn.

**The civ name and coat of arms** (`CNCA`), once. Dialog 225 writes the name,
the "Reveal True Player Name" checkbox and the arms id at the end of `OWPR`,
and is offered while the name is empty. Carried only while the name is empty as
served, and only as the dialog's OK allows: 3 to 30 printable characters after
trimming spaces and tabs, none matching another civ's player name or chosen
name in any case, and an arms id from the player's own uploads (`arms`), or
none. A name another civ took earlier in the same merge counts.

**System renaming** (`SONA`), the `SUN ` section's name at `+24` in its
payload. Allowed for a civ owning more than half of the system's settled
planets in the served state; unowned planets do not count.

**Planet renaming** (`SONA`), `PLNT` own payload `+24`: a `u32` length then the
characters. Carried for planets the submitter owns. `EXSY` also holds names but
is each civ's cache of names seen, not where a rename is authored, so it is
excluded.

**Governors** (`NWGV`, `MDGV`, `DLGV`, `STGV`), `OWNR > DATA > GOVS`, and the
planet's link to one, the last `u32` of its `PLNT`. Creating a governor appends
a `GVNR` and raises `SAVE+0` to its id, so a new one is renumbered like a
design when another player's design, governor or production queue took the id.
Editing rewrites the `GVNR` in place, and deleting removes it. Assigning sets
the planet's link; the client offers no way to unassign. Carried for the civ's
own governors and planets, as records whose sections fit, with a printable name
and at least one rule, which the client requires; the rules themselves are the
engine's to run. Deleting a governor that still governs a planet is refused.
Each `GVNR` counts the planets it governs, and the referee recounts that from
the merged links.

**Admirals** (`NWAD`, `MDAD`, `DLAD`, `STAD`), `OWNR > DATA > ADMS`, and a
ship's link to one, the `u32` after the has-orders byte in its `DYNO`.
Creating, editing and deleting are carried as for governors: a new one raises
`SAVE+0` and is renumbered when another player's new object took its id, and
its ships follow. Carried for the civ's own admirals, as records with a
printable name, one of the four types, and rules the client's reader knows,
each a `RULE` byte and its section; the rules are the engine's to run. A ship
may name 0 or any admiral its civ holds as merged. Deleting one that still
commands a ship is refused, as the client greys it out. Each `ADML` counts the
ships and fleets naming it, and the referee recounts that. A fleet names its
admiral in its own `DYNO` as a ship does, and may name 0 or any admiral its civ
holds. A ship leaving a fleet keeps the fleet's admiral or has none.

**Diplomacy** (`NWTR`, `MDTR`, `DLTR`), the treaty list after SAVE's last
section and each civ's stance toward the civs it has met, in its `KNPL`.
Carried as the client takes each step, applied to both civs at once:

- Proposing: a new `TRTY` from the submitter to a civ it has met, dated this
  turn, of a kind the client offers, holding items the client writes.
  Renumbered like a design. A declaration of war is the same with kind 1 and
  status 3; it ends every treaty in force between the two and sets both
  stances to War, and is refused while they are at war or a cease-fire or
  peace still binds them. In a galaxy whose `GSET` holds `waronly = 1` every
  new treaty but war is refused, as the client greys "Propose New Treaty"
  there (`0x442510`).
- Withdrawing the submitter's own proposal; declining one made to it;
  deleting a declined one from its side.
- Accepting one made to it: status, length (its `TRLE`, or what a stance
  treaty binds for) and start turn as the client writes them. The referee
  moves the lump sums itself, from the merged credits and resources, and
  refuses the acceptance when the payer is short. It copies a shared galaxy
  map (`SHGM`) too, since neither civ's view holds the other's `EXSY`: the
  giver's systems merge into the receiver's, and the receiver meets each civ
  they name. A cease-fire, peace or alliance ends the stance treaty or war
  before it and sets both stances.

A step on a treaty that the other party already changed in this merge is
refused. Per-turn payments, shared sight and battle reports, and a treaty
running out are the engine's.

**Display choices** (`STCL`, `TGST`, `TGSS`), in the civ's own `KNPL` record
for each civ it has met: whether that civ's territory (`+29`) and ships (`+30`)
are drawn, and its colour, by stance (`+31` set) or the `B, G, R, A` colour at
`+32`. Carried for the records the civ was served, each flag 0 or 1. They
change only what the submitter's own map shows.

**The end-galaxy vote** (`SVUS`), the last byte of the civ's `OWNR > DATA > USSE`, 1
when the Overview Screen's "Vote to End the Galaxy" box is ticked. Carried as 0
or 1. Nothing acts on it; the referee archives the votes and the operator page
shows them. The rest of `USSE` is the civ's interface settings and is not read.

**Messages** (`SDNW`, `CHNW`, `SEEN`, `EDNW`), news items naming their title
and body in `NWDB`, the galaxy's string table (`news.py`). A message is taken when the
submitter's new `SENT` item names civs it has met, each once, and each of them
holds a new `RECEIVED` item with the same body, titled `Message from
<submitter> - Subject: <subject>` and naming the submitter, all dated this turn
with no position. Its strings are renumbered into the merged table, its text is
kept to the send dialog's rows and a bounded subject, holds no control
character but line breaks and tabs in the body, and has its markup characters
replaced by plain stand-ins. Each recipient's new-news flag is set. The
submitter's own items may change their important mark, and its new-news flag
may be cleared. A note to oneself is a kind-8 item in the civ's own list with
its title, `Note - Subject: <subject>`, and its text inline and no string
ids, recipients or position, dated this turn; the Note dialog needs a subject
and a text (`0x004B0D80`) and limits neither. Writing one appends it with no
new-news flag, and editing one (`EDNW`) replaces it whole, keeping only its
important mark. Any other change to a news list is refused.

**Scan reports** (`DLSC`), after the civ's designs in `OWNR > DATA`: a `u32`
count, then one section per report named for its class (`SCPL`, `SCSY`,
`SCRO`, `SCFL`, `SCML`, `SCTE`, `SCEG`, `SCLN`). The Scanning page deletes any
of the civ's own reports (`0x0043C610`), with no other effect. Carried as the
reports served, in order, less those deleted. A scan (`DOSC`) goes online and
the referee answers it in its own client (`scan.py`). The new reports a
submission holds are never taken from it: up to the count the referee
answered the civ, they are expected, and the referee adds its own copies
before the tick. Any more are refused and named, and those scans stay in
stock.

**Wormholes** (`CNWH`), `GLXY > WORM`: the object header, a `u32`, a string
and last the id of the wormhole it is linked to, 0 for none. The Connect To
menu opens on the civ's own unlinked wormhole and offers its other unlinked
ones at any distance (`0x005080B0`); `CNWH` links the two to each other. An
ally's wormhole is connected by a treaty instead, and no player command
clears a link. Carried as two of the civ's own wormholes, unlinked as served,
each naming the other.

**Market trades** (`TRRS`), `GLOB > RSMA`: five `i32` counts of the units the
market holds, which a sale raises and a purchase lowers. How far each count
moved is the civ's net trade in that resource. The referee prices it from the
market as served, the one the player traded against, with the civ's trait 42,
then moves the credits, stock and count itself; the submitted credits and
stocks are not read. Sales go first. A sale of more than the civ holds, or a
purchase it cannot pay for, is refused. Several trades in one resource are
priced as one, so the credits can differ from the client's by a few when a
player splits them.

**Fleets** (`MCFL`, `RNFL`, `DISS`, and `STCO` for orders), `GLXY > FLET`: the
object header (object id, x, z, y, owner), a `DYNO` with the fleet's orders,
and `FLDT`, a `u32`-length name, a `u32` count and one `SHPR` per ship. Ships
leave `GLXY` while in a fleet. A `SHPR` ends with the id of the last fleet its
ship was in, then the ship's own id. Forming, renaming, a fleet's orders
(copied whole, as a ship's are), ships leaving and joining, one fleet joining
another, and disbanding are carried for the civ's own fleets, as where the
submission leaves each of its ships. Every ship keeps its `SHPR` as served, its
crew judged with the people below; a ship leaving takes the fleet's position
and its own submitted `DYNO`, and a fleet keeps its header. A new fleet raises
`SAVE+0` and is renumbered like a design when another player's new object took
its id. It is refused unless its id is new, it names the submitter, its name
fits the client's 32 characters, and it holds at least one ship, each the
submitter's as served, placed once in the submission, and all at one position.
A ship joins only the civ's own fleet, at that fleet's position. Moving or
giving away a fleet, a fleet not laid out as measured, and any change to
another civ's fleet are refused. A refused step leaves its ships where they
were served.

What is refused
---------------
Everything not listed above is not accepted, among it another civ's admirals,
a treaty ended any other way, and a stance moved without a treaty. Every
refusal is logged and recorded against the civ in the optional `notes` argument
to `merge`; `referee.py` writes it beside the turn and `player_turn.follow`
prints it. A submission
that cannot be read is dropped whole and named, with anything already taken
from it rolled back.
"""
import argparse
import collections
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import save_parser as sp
import inject_civ as icv
import inject_design as idg
import facility_rules
import news
import exsy
import knpl

# The original client's 56 commands, each with the rule above that carries its
# effect, or None and what the command does. A command with no rule is refused
# when it changes the galaxy, or changes nothing a submission carries.
COMMANDS = {
    'STCO': ('Ship orders', 'a ship or fleet course and order'),
    'STRS': ('Research topic', 'the topic being researched'),
    'STPR': ('Current production', 'what a planet builds'),
    'HRPR': ('Current production', 'hurries what a planet builds'),
    'NWSQ': ('Production queues', 'a new queue'),
    'MDSQ': ('Production queues', 'edits a queue'),
    'DLSQ': ('Production queues', 'deletes a queue'),
    'STSQ': ('Production queues', "sets a planet's queue"),
    'CRSD': ('Ship designs', 'a new design'),
    'TGSD': ('Ship designs', "toggles a design's inactive byte"),
    'CHCZ': ('People', 'ChangeCitizenJobs'),
    'CRAS': ('People', "moves people between a planet's military and a crew"),
    'PLRR': ('Military recruitment rate', "a planet's recruitment rate"),
    'CUCV': ("The setup prompts' confirms", 'Customize Your Civilization'),
    'CUHW': ("The setup prompts' confirms", 'Customize Your Home World'),
    'CNCA': ('The civ name and coat of arms', 'name, checkbox and arms id'),
    'SONA': ('Planet renaming', 'names a planet or a system'),
    'NWGV': ('Governors', 'a new governor'),
    'MDGV': ('Governors', 'edits a governor'),
    'DLGV': ('Governors', 'deletes a governor'),
    'STGV': ('Governors', "sets a planet's governor"),
    'NWAD': ('Admirals', 'a new admiral'),
    'MDAD': ('Admirals', 'edits an admiral'),
    'DLAD': ('Admirals', 'deletes an admiral'),
    'STAD': ('Admirals', "sets a ship's admiral"),
    'NWTR': ('Diplomacy', 'proposes a treaty or declares war'),
    'MDTR': ('Diplomacy', 'accepts, declines or withdraws a treaty'),
    'DLTR': ('Diplomacy', 'deletes a treaty from its side'),
    'STCL': ('Display choices', "a civ's colour, or colour by stance"),
    'TGST': ('Display choices', "draws a civ's territory or not"),
    'TGSS': ('Display choices', "draws a civ's ships or not"),
    'SDNW': ('Messages', 'sends a message or writes a note'),
    'CHNW': ('Messages', 'clears flags on news items'),
    'SEEN': ('Messages', 'clears one of three new-item flags'),
    'TRRS': ('Market trades', 'buys or sells resources'),
    'MCFL': ('Fleets', 'puts selected ships into a new or named fleet'),
    'RNFL': ('Fleets', 'renames a fleet'),
    'DISS': ('Fleets', 'disbands a fleet'),
    # Runs a governor on one planet or all it governs; the other rules
    # judge what its rules change.
    'RNGV': (None, 'runs a governor now'),
    'SLFC': ('Facility sales', 'sells a facility'),
    'RTMI': ('People', 'retires stationed military'),
    'STEL': ('Energy links', "sets a planet's hyperspace energy links, `ENLI`"),
    'CNWH': ('Wormholes', "connects two of the civ's wormholes"),
    'DLSC': ('Scan reports', 'deletes a scan report'),
    'MOSD': ('Ship designs', 'modifies a design'),
    'RNSD': ('Ship designs', 'renames a design'),
    'DLSD': ('Ship designs', 'deletes a design'),
    'EDNW': ('Messages', 'edits a note'),
    # Refused and named: the report is the referee's to make (Scan reports).
    'DOSC': (None, 'carries out scans'),
    # Only the vote byte of the civ's `USSE` is carried.
    'SVUS': ('The end-galaxy vote', 'saves user settings, the vote among them'),
    # Online only: the offline branch changes nothing.
    'RQTM': (None, 'asks for the time after a galaxy loads'),
    'RSMA': (None, "asks for the market's prices"),
    'RQTN': (None, 'asks the replay server for one turn (0x0056E550)'),
    'RQSV': (None, 'asks for a fresh save when the client is out of sync'),
    'STTP': (None, 'a setting from the team dialog'),
    'ALIV': (None, 'keep-alive'),
}

# The research topic is written twice in a civ's OWPR, 8 bytes apart, after a
# map (u32 count at +8, 8-byte pairs) and the completed technologies (u32 count,
# [id, cost] records). They sit at +32 only while the civ has its two starting
# technologies and an empty map.
OWPR_MAP_COUNT_OFF = 8
OWPR_RECORD = 8
RESEARCH_FIELD_COUNT = 2
RESEARCH_FIELD_LEN = 4

# The setup prompts' flags, one byte each in OWPR, this far past the start of
# the second research field: Owner:359 (homeworld prompt, dialog 210) and
# Owner:360 (civ-trait prompt, dialog 218). Each prompt's OK sets its own.
HOMEWORLD_FLAG_OFF = OWPR_RECORD + 29
CIV_FLAG_OFF = OWPR_RECORD + 30

# What dialog 225 chose closes OWPR: the civ name (u32 length, characters), the
# "Reveal True Player Name" checkbox (one byte) and the coat-of-arms id (i32).
IDENTITY_TAIL = 5                  # the checkbox and the arms id
NO_ARMS = -1
CIV_NAME_MIN, CIV_NAME_MAX = 3, 30  # after trimming, as the dialog's OK checks
CIV_NAME_TRIM = b' \t'

# The civ traits in CVTR: a u32 count at +16, then (index, value) pairs of
# u32s, one per trait the dialog shows. A confirm overwrites each value with
# what the clicks spent on it give.
CVTR_COUNT_OFF = 16
CVTR_PAIRS_OFF = 20
CVTR_PAIR = 8

# The civ-trait dialog's rules, from its table at 0x7FCD88 and value function
# 0x515910: per trait in index order, its name, the fewest clicks allowed, the
# value each click count gives from there, and what one click costs in
# changes. A negative cost is a trait that pays changes for being raised.
_OUTPUT = (-16, -8, 0, 8, 15, 21, 25, 28)
_STRIKE = (-20, -10, 0, 10, 19, 26, 31, 34)
TRAIT_RULES = (
    ('food consumption per citizen', -2, (-4, -2, 0, 2, 4), -1),
    ('income per citizen', -2, (-4, -2, 0, 2, 4), 1),
    ('food output', -2, _OUTPUT, 1),
    ('production output', -2, _OUTPUT, 1),
    ('science output', -2, _OUTPUT, 1),
    ('mining output', -2, (-24, -12, 0, 12, 23, 33, 41, 48), 1),
    ('ship speed', -2, (-10, -5, 0, 5, 9, 13, 16, 19), 1),
    ('ship firepower', -2, _STRIKE, 1),
    ('planetary defense', -2, _STRIKE, 1),
    ('troops', -2, _STRIKE, 1),
)

# The homeworld dialog's fields in PLPR: three signed rate bytes (food,
# production, science) and the planet space, a u16. A confirm adds the clicks
# to each rate, and raises space to a floor before adding five per click.
HOMEWORLD_RATE_OFFS = (4, 5, 6)
HOMEWORLD_SPACE_OFF = 11
HOMEWORLD_RATES = (25, 80)          # a rate the dialog leaves, inclusive
HOMEWORLD_SPACE_FLOOR = 350
HOMEWORLD_SPACE_STEP = 5
HOMEWORLD_SPACE_CLICKS = 20

# A planet's name, in PLNT's own payload: a u32 length then the characters.
PLANET_NAME_OFF = 24
MAX_NAME = 63

# What a client writes into a SUN whose stored name is empty.
DEFAULT_SUN_NAME = b'Unnamed'

# The citizen array inside a planet's PLPR.
POP_COUNT_OFF = 36
POP_ARRAY_OFF = 40
POP_RECORD = 9
POP_ARRIVED_OFF = 7             # u16, the turn the citizen was born or arrived
JOB_IDS = {0: 'farmer', 1: 'worker', 2: 'scientist', 5: 'miner', 6: 'banker'}

# A soldier is the same nine-byte record with this job id, kept in a second
# array after the citizens.
MILITARY_JOB = 3

# Hurrying production moves three fields:
#
#     PLPR+23, u32            production points accumulated, raised to the cost
#     PROD payload byte 0     0 -> 1
#     OWNR, id_at+20, u32     credits, less 4 per point bought
#
# The manual's rule: 4 credits per production point left, and the item must be
# at least half finished. The item's total cost is not in the blob; the points
# bought are the distance the progress field moved. See hurry_acceptable.
#
# A ship's crew, inside SHPR: a u32 count then the same nine-byte records the
# planet keeps its soldiers in.
CREW_COUNT_OFF = 4
CREW_ARRAY_OFF = 8

# A design is a `DSGN` section inside the owning civ's `OWNR > DATA`, a sibling
# of `OWPR` and `SERV`, carrying its own object id and one `SDPR` child:
#
#     DSGN v4   u32 objectId ; SDPR ; one trailing byte
#     SDPR v0   u32 nameLen ; name ; six part lists ; u32 owningCivObjectId
#
# Immediately before a civ's first `DSGN`, in `DATA`'s own bytes, sits a `u32`
# count of that civ's designs. The readers are `inject_design.py`'s.
DESIGN_COUNT_BEFORE = 4     # the count dword, this far before the first DSGN
MAX_DESIGN_NAME = 32        # the edit controls hold 32 (EM_LIMITTEXT at 0x004B087C)
RESERVED_DESIGN_NAME = 'UNNAMED'   # what an empty name becomes, in any case

# A civ's governors are in its `OWNR > DATA > GOVS`: a u32 count, then one
# `GVNR` per governor, in creation order:
#
#     GVNR v0   u32 objectId ; u32 planets governed ; one byte ; GVDT
#     GVDT v1   u32 nameLen ; name ; two bytes ; u32 rule count ;
#               per rule a RULE (one byte, the rule's checkbox) then the
#               rule's own section (RLSP, RLPR, RLRR ...)
#
# A planet names its governor in the u32 after its `PLPR`, the last four bytes
# of the `PLNT`, 0 for none. The client offers no way to unassign one.
GOVS_COUNT = 4
GVNR_GOVERNS_AT = 4         # in the GVNR payload, after the object id
GVNR_FIXED = 9              # object id, planets governed and the one byte
GVDT_AFTER_NAME = 2
RULE_FLAG = 1
GOVERNOR_LINK = 4           # the PLNT's trailing u32

# Where a queued ship names its design, as an offset into a whole `PROD`
# section: past PROD's own 8-byte header, then the nested `SHIP` section whose
# header begins at +21 and whose payload is the design's object id.
PROD_DESIGN_AT = 8 + 29

# `OWPR` +49, +77, +78, +100 and +110 move in the same submission as a new
# design and are not carried: they also move without a design, and differ
# between submissions of identical designs. +100 is a u32 the engine advances
# by 7 every tick.

# PLPR+27 is the military recruitment rate, one byte, a percentage. PLPR+23 is
# a u32 of production points accumulated.
RECRUIT_OFF = 27
PROGRESS_OFF = 23

# A planet's food, as the client shows it: store over capacity, two adjacent
# u16 in PLPR. An unowned planet reads 0/40 and a generated homeworld 0/600;
# capacity grows with development, not population alone.
FOOD_STORE_OFF = 15
FOOD_CAP_OFF = 19

# A planet's facilities, as (type, count) pairs. They follow the military array
# after one byte: a u32 count, that many pairs of u32, then four zero bytes.
#
# An unbuilt colony carries none; a generated homeworld carries types 2, 6 and
# 9 once each. Type names are not decoded.
FACILITY_AFTER_MILITARY = 1
FACILITY_RECORD = 8

# Selling a facility (`SLFC`). The share is a double at 0x00753CF8 that the
# engine stores as a float; above the epsilon (0x00753CF0) it rounds up.
SELL_SHARE = 0.05000000074505806
SELL_EPSILON = 1e-7
SHIPYARD = 2                   # facility types the Sell button protects
CDA = 8

# The block between a planet's `PROD` and its `ENLI`, as offsets from the end
# of `PROD`: the population history, the turns-since-acquisition counter, the
# conqueror list, the rebellion counter, then the resource deposit. Each list
# entry is a ruler's influence on the people; a planet's loyalty is derived
# from it and each citizen's origin and arrival turn (`sim.economy.planet_loyalty`).
# One ruler gives 51 bytes, each further ruler 16 more; an empty list (an
# owner set without colonising, only in synthetic galaxies) gives 35.
#
# The founding turn is a u32 here and again at `PLPR+0`; a homeworld reads 0.
# The population field equals the citizen count, and the seven u16 after it
# are earlier turns' populations (newest first, some carrying 0x1000, all zero
# at generation). A generated homeworld reads 1 at +16 and mines 10 a turn.
SETTLED_LEN = 51
SETTLED_POP_OFF = 0             # u16
SETTLED_HISTORY_OFF = 2         # SETTLED_HISTORY_LEN u16s
SETTLED_HISTORY_LEN = 7
SETTLED_COUNTER_OFF = 16        # u32, turns since acquisition, capped at 8 (Planet:368)
INFLUENCE_COUNT_OFF = 20        # u32, rulers in the conqueror list
INFLUENCE_AT = 24               # the list (Planet:384)
INFLUENCE_RECORD = 16           # owner id, f32 influence, first turn, last turn
RULING = -1                     # the last turn of the current ruler's entry
SETTLED_OWNER_OFF = 24          # u32, the first ruler: the owner, on a planet never taken
SETTLED_AGE_OFF = 28            # f32, its influence: +1 a turn plus soldiers, up to 100
SETTLED_FOUNDED_OFF = 32        # u32, the turn its rule began
SETTLED_LAST_OFF = 47           # u32, the deposit's rate per miner, with one ruler
UNREST_AFTER_LIST = 0           # u16, turns in a row below 30% loyalty (Planet:396)
FOUNDED_OFF = 0                 # u32 at PLPR+0, the founding turn again
PROD_HURRIED_OFF = 8            # PROD's own payload begins after its header
CREDITS_AFTER_ID = 20           # past the object id, which is itself past
                                # OWNR's length-prefixed name
HURRY_PER_POINT = 4


def ship_index(blob):
    """{shipObjectId: (owner, dyno_bytes)} for every ship carrying a DYNO."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    out = {}
    for sec in glxy.find('SHIP'):
        oid, _x, _z, _y, owner = struct.unpack_from('<IfffI', blob, sec.payload)
        dyno = next(sec.find('DYNO'), None)
        if dyno is not None:
            out[oid] = (owner, bytes(blob[dyno.start:dyno.end]))
    return out


# A fleet, GLXY > FLET: the 20-byte object header (object id, x, z, y, owner),
# a DYNO with the fleet's orders, then FLDT: a u32-length name, a u32 member
# count and one SHPR per member. Member ships leave GLXY as SHIPs while in a
# fleet. A SHIP is the same header, a DYNO and a SHPR.
OBJECT_HEADER = 20
OBJECT_POS = slice(4, 16)       # x, z, y, three f32
OBJECT_OWNER_AT = 16
SHIP_VERSION = 0
FLET_VERSION = 0
FLDT_VERSION = 1
MAX_FLEET_NAME = 32             # the client cuts a longer name to this

# A SHPR ends with 12 bytes after its crew: a f32, the id of the last fleet the
# ship was in, and the ship's own id. The last fleet outlives the fleet.
SHPR_TAIL = 12
SHPR_FLEET_FROM_END = 8         # then the ship's own id, the last four bytes


def _section_bytes(tag, version, payload):
    return tag + struct.pack('<I', (version << sp.VERSION_SHIFT) | len(payload)) + payload


def shpr_ids(shpr):
    """(last fleet, own id) from a SHPR payload, or None unless its crew and
    tail fill it exactly."""
    crew = crew_of(shpr)
    if crew is None or len(shpr) != (CREW_ARRAY_OFF + len(crew) * POP_RECORD
                                     + SHPR_TAIL):
        return None
    return struct.unpack_from('<II', shpr, len(shpr) - SHPR_FLEET_FROM_END)


def with_last_fleet(shpr, fleet_oid):
    """A SHPR payload naming a different last fleet."""
    out = bytearray(shpr)
    struct.pack_into('<I', out, len(out) - SHPR_FLEET_FROM_END, int(fleet_oid))
    return bytes(out)


def _children(blob, at, end):
    """[(tag, version, whole section, payload)] filling blob[at:end] exactly,
    or None."""
    secs = _sections(blob, at, end)
    if secs is None:
        return None
    return [(tag, struct.unpack_from('<I', blob, p - 4)[0] >> sp.VERSION_SHIFT,
             bytes(blob[p - 8:e]), bytes(blob[p:e])) for tag, p, e in secs]


def fleet_name_acceptable(name):
    """(ok, reason) for a submitted fleet name."""
    if len(name) > MAX_FLEET_NAME:
        return False, (f'the name is {len(name)} bytes and the client cuts '
                       f'one to {MAX_FLEET_NAME}')
    if any(b < 0x20 or b > 0x7e for b in name):
        return False, 'the name holds bytes outside printable ASCII'
    return True, ''


def _read_object(blob, sec):
    """The header fields every SHIP and FLET starts with."""
    head = bytes(blob[sec.payload:sec.payload + OBJECT_HEADER])
    if len(head) < OBJECT_HEADER:
        return {'tag': sec.tag, 'sec': sec, 'oid': None, 'owner': None,
                'pos': None, 'record': bytes(blob[sec.start:sec.end]),
                'why': 'it is shorter than an object header'}
    return {'tag': sec.tag, 'sec': sec, 'oid': struct.unpack_from('<I', head)[0],
            'owner': struct.unpack_from('<I', head, OBJECT_OWNER_AT)[0],
            'pos': head[OBJECT_POS], 'record': bytes(blob[sec.start:sec.end]),
            'why': ''}


def read_ship(blob, sec):
    """A GLXY SHIP as a dict: oid, owner, pos, dyno (the whole section), shpr
    (its payload), shpr_version, last_fleet, and why, '' when it is a header,
    a DYNO and a SHPR naming the ship."""
    out = _read_object(blob, sec)
    old = next(sec.find('SHPR'), None)
    out.update(dyno=None, shpr_version=None, last_fleet=None,
               shpr=bytes(blob[old.payload:old.end]) if old else None)
    if out['why']:
        return out
    kids = _children(blob, sec.payload + OBJECT_HEADER, sec.end)
    if not kids or [k[0] for k in kids] != [b'DYNO', b'SHPR']:
        out['why'] = 'it is not a header, a DYNO and a SHPR'
        return out
    ids = shpr_ids(kids[1][3])
    if ids is None or ids[1] != out['oid']:
        out['why'] = 'its SHPR does not end by naming it'
        return out
    out.update(dyno=kids[0][2], shpr=kids[1][3], shpr_version=kids[1][1],
               last_fleet=ids[0])
    return out


def repair_donor_tails(blob, log=print):
    """`blob` with every GLXY SHIP whose SHPR ends by naming another ship
    rewritten to name itself, in place.

    A ship `inject_ship.py` cloned before `f4fb9f7` names its donor there,
    and inside a fleet that id is the ship's only identity. The object
    header's id is the ship's.
    """
    out = None
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'), None)
    for sec in (glxy.children if glxy is not None else ()):
        if sec.tag != b'SHIP':
            continue
        ship = read_ship(blob, sec)
        if ship['why'] != 'its SHPR does not end by naming it':
            continue
        shpr = next(sec.find('SHPR'))
        ids = shpr_ids(ship['shpr'])
        if ids is None:
            continue
        out = out if out is not None else bytearray(blob)
        struct.pack_into('<I', out, shpr.end - 4, ship['oid'])
        log(f'  ship {ship["oid"]}: its SHPR named ship {ids[1]}, now '
            f'itself')
    return bytes(out) if out is not None else blob


def read_fleet(blob, sec):
    """A GLXY FLET as a dict: oid, owner, pos, dyno (the whole section), name,
    members [(ship id, SHPR payload, SHPR version)] in order, and why, '' when
    it has the layout measured."""
    out = _read_object(blob, sec)
    out.update(dyno=None, name=None, members=[])
    if out['why']:
        return out
    kids = _children(blob, sec.payload + OBJECT_HEADER, sec.end)
    if (sec.version != FLET_VERSION or not kids
            or [k[0] for k in kids] != [b'DYNO', b'FLDT']
            or kids[1][1] != FLDT_VERSION):
        out['why'] = 'it is not a header, a DYNO and an FLDT as measured'
        return out
    fldt = kids[1][3]
    n = struct.unpack_from('<I', fldt)[0] if len(fldt) >= 4 else None
    if n is None or 8 + n > len(fldt):
        out['why'] = 'its name runs past its FLDT'
        return out
    count = struct.unpack_from('<I', fldt, 4 + n)[0]
    members = _children(fldt, 8 + n, len(fldt))
    if (members is None or len(members) != count
            or any(tag != b'SHPR' for tag, *_r in members)):
        out['why'] = f'its FLDT does not hold the {count} SHPR(s) it counts'
        return out
    ids = [shpr_ids(payload) for _t, _v, _w, payload in members]
    if None in ids:
        out['why'] = 'a member SHPR does not end by naming its ship'
        return out
    out.update(dyno=kids[0][2], name=bytes(fldt[4:4 + n]),
               members=[(own, payload, version) for (_f, own), (_t, version,
                        _w, payload) in zip(ids, members)])
    return out


def space_objects(blob):
    """(tree, GLXY, [object]): GLXY's SHIPs and FLETs in order, each read by
    read_ship or read_fleet, its section under 'sec'."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    objects = []
    for sec in glxy.children:
        if sec.tag == b'SHIP':
            objects.append(read_ship(blob, sec))
        elif sec.tag == b'FLET':
            objects.append(read_fleet(blob, sec))
    return tree, glxy, objects


def fleet_index(blob):
    """{fleetObjectId: fleet} for every FLET, read by read_fleet."""
    return {o['oid']: o for o in space_objects(blob)[2] if o['tag'] == b'FLET'}


def fleet_orders(served, sub, civ_oid, names, admirals=(), remap=None):
    """(layout, refusals) for one civ's fleets, judged against the state served.

    `admirals` are the ids of the admirals the civ holds as merged, and `remap`
    the new ones renumbered, {submitted id: merged id}.

    layout is None when nothing changes, else how the civ's ships and fleets
    end the turn:

        target      {ship: the fleet it ends in, or None}, for each ship
        fleets      {fleet: {name, dyno, members, pos, new}}, each with a ship
        out         {ship: DYNO section} for each ship leaving a fleet
        last_fleet  {ship: the last fleet its submitted SHPR names}, singles
        appended    [('ship' | 'fleet', id)], the new objects in submitted order
        log         [line], one per change taken

    Fleet ids are as submitted; a new one is renumbered when it is applied. A
    ship whose move is refused stays where it was served, and a fleet left
    with no ship is gone, as disbanding leaves it.
    """
    s_objects = space_objects(served)[2]
    t_objects = space_objects(sub)[2]
    high = struct.unpack_from('<I', served, idg.HIGH_WATER_ID)[0]
    refusals, log = [], []

    def who(civ):
        return names.get(civ, civ)

    def label(fid, name):
        return f"fleet {fid} ({name.decode('latin1')!r})" if name is not None \
            else f'fleet {fid}'

    served_fleets = {o['oid']: o for o in s_objects if o['tag'] == b'FLET'}
    mine_fleets = {fid: f for fid, f in served_fleets.items()
                   if f['owner'] == civ_oid and not f['why']}
    at, owner_of, pos_of, field_of = {}, {}, {}, {}
    for o in s_objects:
        if o['why']:
            continue
        if o['tag'] == b'SHIP':
            at[o['oid']], owner_of[o['oid']] = None, o['owner']
            pos_of[o['oid']], field_of[o['oid']] = o['pos'], o['last_fleet']
        else:
            for m, shpr, _v in o['members']:
                at[m], owner_of[m], pos_of[m] = o['oid'], o['owner'], o['pos']
                field_of[m] = shpr_ids(shpr)[0]
    mine = {s for s, owner in owner_of.items() if owner == civ_oid}

    # Where the submission puts each ship, and its readable fleets.
    counted = collections.Counter(o['oid'] for o in t_objects
                                  if o['tag'] == b'FLET')
    places = collections.defaultdict(list)
    singles, fleets_t, pinned = {}, {}, set()
    for o in t_objects:
        if o['tag'] == b'SHIP':
            places[o['oid']].append(None)
            singles[o['oid']] = o
            continue
        fid, why = o['oid'], o['why']
        if not why and counted[fid] > 1:
            why = f'the submission holds {counted[fid]} fleets under its id'
        if why:
            was = served_fleets.get(fid)
            if was is None:
                refusals.append(f'{label(fid, None)} DROPPED, {why}')
            elif was['owner'] != civ_oid:
                if o['record'] != was['record']:
                    refusals.append(f'fleet {fid}: DROPPED, owned by '
                                    f"{who(was['owner'])}")
            else:
                if o['record'] != was['record'] or counted[fid] > 1:
                    refusals.append(f'fleet {fid}: changes DROPPED, {why}; it '
                                    f'stays as served')
                pinned |= {m for m, _s, _v in was['members']}
            continue
        fleets_t[fid] = o
        for m, _s, _v in o['members']:
            places[m].append(fid)

    for fid, f in fleets_t.items():
        was = served_fleets.get(fid)
        if was is not None and was['owner'] != civ_oid \
                and f['record'] != was['record']:
            refusals.append(f"fleet {fid}: DROPPED, owned by {who(was['owner'])}")

    # New fleets: formed this turn, from ships this civ was served together.
    accepted = {}
    for fid, f in fleets_t.items():
        if fid in served_fleets:
            continue
        members = [m for m, _s, _v in f['members']]
        stray = next((m for m in members if m not in mine), None)
        twice = next((m for m in members
                      if len(places[m]) > 1 or m in pinned), None)
        name_ok, name_why = fleet_name_acceptable(f['name'])
        if fid <= high:
            why = f'it is new and {fid} is not a new object id'
        elif f['owner'] != civ_oid:
            why = f"it names {who(f['owner'])} as its owner"
        elif not name_ok:
            why = name_why
        elif not members:
            why = 'it holds no ship'
        elif stray is not None:
            why = f'ship {stray} is not one this civ was served'
        elif twice is not None:
            why = f'ship {twice} is placed more than once'
        elif len({pos_of[m] for m in members}) != 1:
            why = 'its ships are not all at one position'
        else:
            accepted[fid] = f
            continue
        refusals.append(f'{label(fid, f["name"])} DROPPED, {why}; its ships '
                        f'stay as they were')

    # Where each of the civ's ships ends the turn.
    target, out = {}, {}
    for s in sorted(mine):
        here, there = at[s], places.get(s, [])
        if s in pinned or not there or there == [here]:
            target[s] = here
            continue
        target[s] = here
        if len(there) > 1:
            refusals.append(f'ship {s}: fleet change DROPPED, the submission '
                            f'places it {len(there)} times')
            continue
        there = there[0]
        if there is None:
            if singles[s]['why']:
                refusals.append(f'ship {s}: leaving fleet {here} DROPPED, '
                                f"{singles[s]['why']}")
            else:
                # It may keep the fleet's admiral or have none.
                dyno, why = admiral_kept(singles[s]['dyno'], (
                    dyno_admiral(served_fleets[here]['dyno']) or 0, 0),
                    'a ship leaving a fleet keeps its admiral or has none')
                if why:
                    refusals.append(f'ship {s}: {why}')
                target[s], out[s] = None, dyno
        elif there in accepted:
            target[s] = there
        elif there in mine_fleets:
            if pos_of[s] != mine_fleets[there]['pos']:
                refusals.append(f'ship {s}: joining fleet {there} DROPPED, it '
                                f"is not at the fleet's position")
            else:
                target[s] = there
        elif there in served_fleets:
            refusals.append(f'ship {s}: joining fleet {there} DROPPED, the '
                            f"fleet is owned by {who(served_fleets[there]['owner'])}")

    # The fleets that end the turn with a ship in them.
    fleets = {}
    for fid in list(mine_fleets) + list(accepted):
        listed = [m for m, _s, _v in fleets_t[fid]['members']] \
            if fid in fleets_t else []
        kept = [m for m, _s, _v in mine_fleets[fid]['members']] \
            if fid in mine_fleets else []
        members = [m for m in listed if target.get(m) == fid]
        members += [m for m in kept if target.get(m) == fid and m not in members]
        was = mine_fleets.get(fid)
        now = fleets_t.get(fid)
        if not members:
            if was is not None:
                log.append(f'fleet {fid}: disbanded, no ship is left in it')
            continue
        if was is None:
            dyno, why = ship_admiral(now['dyno'], 0, admirals, remap or {})
            if why:
                refusals.append(f'fleet {fid}: {why}')
            fleets[fid] = {'name': now['name'], 'dyno': dyno,
                           'members': members, 'pos': pos_of[members[0]],
                           'new': True}
            log.append(f"{label(fid, now['name'])}: formed from ship(s) "
                       f'{members}')
            continue
        name, dyno = was['name'], was['dyno']
        if now is not None:
            if (now['pos'], now['owner']) != (was['pos'], was['owner']):
                refusals.append(f'fleet {fid}: position or owner DROPPED, a '
                                f'submission neither moves nor gives away a '
                                f'fleet')
            if now['name'] != was['name']:
                ok, why = fleet_name_acceptable(now['name'])
                if ok:
                    name = now['name']
                    log.append(f"fleet {fid}: renamed "
                               f"{was['name'].decode('latin1')!r} -> "
                               f"{name.decode('latin1')!r}")
                else:
                    refusals.append(f'fleet {fid}: rename DROPPED, {why}')
            if now['dyno'] != was['dyno']:
                taken, why = ship_admiral(
                    now['dyno'], dyno_admiral(was['dyno']) or 0, admirals,
                    remap or {})
                if why:
                    refusals.append(f'fleet {fid}: {why}')
                if taken != was['dyno']:
                    dyno = taken
                    log.append(f'fleet {fid}: order taken ({len(was["dyno"])} '
                               f'-> {len(dyno)} bytes)')
        fleets[fid] = {'name': name, 'dyno': dyno, 'members': members,
                       'pos': was['pos'], 'new': False}
        joined = [m for m in members if at[m] != fid]
        if joined:
            log.append(f'fleet {fid}: joined by ship(s) {joined}')
    for s in sorted(out):
        log.append(f'ship {s}: left fleet {at[s]}')

    last_fleet = {s: singles[s]['last_fleet'] for s in mine
                  if target[s] is None and s in singles
                  and not singles[s]['why']}
    refusals = list(dict.fromkeys(refusals))    # a twice-held id is said once
    if not log and all(v == field_of[s] for s, v in last_fleet.items()):
        return None, refusals

    appended = []
    for o in t_objects:
        if o['tag'] == b'SHIP' and o['oid'] in out:
            appended.append(('ship', o['oid']))
        elif o['tag'] == b'FLET' and o['oid'] in accepted:
            appended.append(('fleet', o['oid']))
    return {'target': target, 'fleets': fleets, 'out': out,
            'last_fleet': last_fleet, 'appended': appended,
            'log': log}, refusals


def set_fleets(blob, civ_oid, layout, renumbered=None):
    """`blob` with one civ's ships and fleets laid out as `layout` says (see
    fleet_orders), every enclosing size corrected.

    Each ship keeps the SHPR `blob` holds for it, crew included, naming the
    fleet it is in, or for a single ship the last fleet it was in. A fleet
    keeps its header and a new one takes its ships' position. Ships and fleets
    that are new to GLXY follow its last SHIP or FLET, where the client puts
    them. `renumbered` maps a new fleet's submitted id to the id it takes.
    """
    renumbered = renumbered or {}
    tree, glxy, objects = space_objects(blob)
    shpr, pos_of = {}, {}
    for o in objects:
        if o['owner'] != civ_oid or o['why']:
            continue
        if o['tag'] == b'SHIP':
            shpr[o['oid']] = (o['shpr'], o['shpr_version'])
            pos_of[o['oid']] = o['pos']
        else:
            for m, payload, version in o['members']:
                shpr[m] = (payload, version)
                pos_of[m] = o['pos']
    final = {renumbered.get(fid, fid) for fid in layout['fleets']}

    def last(s):
        held = shpr_ids(shpr[s][0])[0]
        want = layout['last_fleet'].get(s)
        if want is None:
            return held
        want = renumbered.get(want, want)
        return want if want == held or want in final else held

    def ship_bytes(s, pos, dyno, version=SHIP_VERSION):
        payload, shpr_version = shpr[s]
        head = struct.pack('<I', s) + pos + struct.pack('<I', civ_oid)
        return _section_bytes(b'SHIP', version, head + dyno + _section_bytes(
            b'SHPR', shpr_version, with_last_fleet(payload, last(s))))

    def fleet_bytes(fid):
        f, oid = layout['fleets'][fid], renumbered.get(fid, fid)
        fldt = (struct.pack('<I', len(f['name'])) + f['name']
                + struct.pack('<I', len(f['members']))
                + b''.join(_section_bytes(b'SHPR', shpr[m][1],
                                          with_last_fleet(shpr[m][0], oid))
                           for m in f['members']))
        head = struct.pack('<I', oid) + f['pos'] + struct.pack('<I', civ_oid)
        return _section_bytes(b'FLET', FLET_VERSION, head + f['dyno']
                              + _section_bytes(b'FLDT', FLDT_VERSION, fldt))

    edits, after = [], None
    for o in objects:
        sec = o['sec']
        after = sec.end
        if o['owner'] != civ_oid or o['why']:
            continue
        if o['tag'] == b'SHIP':
            if o['oid'] not in layout['target']:
                continue
            if layout['target'][o['oid']] is None:
                new = ship_bytes(o['oid'], o['pos'], o['dyno'], sec.version)
            else:
                new = b''
        else:
            new = fleet_bytes(o['oid']) if o['oid'] in layout['fleets'] else b''
        if new != o['record']:
            edits.append((sec.start, sec.end, new))
    added = b''.join(fleet_bytes(oid) if kind == 'fleet'
                     else ship_bytes(oid, pos_of[oid], layout['out'][oid])
                     for kind, oid in layout['appended'])
    if added:
        if after is None:
            raise SystemExit('GLXY holds no SHIP or FLET to add after')
        edits.append((after, after, added))
    body = bytearray(blob[glxy.payload:glxy.end])
    for start, end, new in sorted(edits, reverse=True):
        body[start - glxy.payload:end - glxy.payload] = new
    return sp.replace_payload(blob, tree, glxy, bytes(body))


def planet_index(blob):
    """{planetObjectId: (owner, prod, plpr_payload, (len, name))}."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    out = {}
    for sola in (c for c in glxy.children if c.tag == b'SOLA'):
        for sec in sola.find('PLNT'):
            oid, _x, _z, _y, owner = struct.unpack_from('<IfffI', blob,
                                                        sec.payload)
            plpr = next(sec.find('PLPR'), None)
            prod = next(plpr.find('PROD'), None) if plpr else None
            out[oid] = (owner,
                        bytes(blob[prod.start:prod.end]) if prod else None,
                        bytes(blob[plpr.payload:plpr.end]) if plpr else None,
                        planet_name(blob, sec))
    return out


def citizens_of(raw):
    """[(job, owner, extra, rest_bytes)] from a PLPR payload, or None."""
    if len(raw) < POP_ARRAY_OFF + 4:
        return None
    n = struct.unpack_from('<I', raw, POP_COUNT_OFF)[0]
    end = POP_ARRAY_OFF + n * POP_RECORD
    if end > len(raw):
        return None
    out = []
    for i in range(n):
        r = raw[POP_ARRAY_OFF + i * POP_RECORD:
                POP_ARRAY_OFF + (i + 1) * POP_RECORD]
        out.append((r[0], struct.unpack_from('<I', r, 3)[0], r[7],
                    bytes((r[1], r[2], r[8]))))
    return out


def arrived(citizen):
    """The turn a `citizens_of` record was born or arrived."""
    _job, _owner, extra, rest = citizen
    return extra | rest[2] << 8


def citizen_bytes(raw):
    """The array's own bytes, for copying, or None when it is unreadable."""
    if citizens_of(raw) is None:
        return None
    n = struct.unpack_from('<I', raw, POP_COUNT_OFF)[0]
    return raw[POP_ARRAY_OFF:POP_ARRAY_OFF + n * POP_RECORD]


def population(blob, civ_oid):
    """({planet: (citizens, military)}, {ship: crew}) for one civ, or None."""
    planets, ships = {}, {}
    for oid, (owner, _prod, plpr, _nm) in planet_index(blob).items():
        if owner != civ_oid:
            continue
        cz = citizens_of(plpr)
        _at, mil = military_of(plpr)
        if cz is None or mil is None:
            return None, None
        planets[oid] = (cz, mil)
    for oid, (owner, shpr) in _ship_crew_index(blob).items():
        if owner != civ_oid:
            continue
        crew = crew_of(shpr or b'')
        if crew is None:
            return None, None
        ships[oid] = crew
    return planets, ships


def _owner_of(record):
    return struct.unpack_from('<I', record, 3)[0]


def _record(raw):
    """(job, owner, extra, rest) from a raw nine-byte soldier or crew record.

    The same shape `citizens_of` returns.
    """
    return (raw[0], struct.unpack_from('<I', raw, 3)[0], raw[7],
            bytes((raw[1], raw[2], raw[8])))


def _everyone(planets, ships):
    citizens, soldiers = [], []
    for _oid, (cz, mil) in planets.items():
        citizens += cz
        soldiers += mil
    for _oid, crew in ships.items():
        soldiers += crew
    return citizens, soldiers


def people_acceptable(served, submitted, civ_oid):
    """(ok, reason) for everything a civ did to its own people in one turn.

    What a submission may do, given that it never ticks:

        move soldiers between the civ's own planets and ships
        turn a citizen into a soldier
        reassign jobs among citizens
        retire soldiers, who leave the galaxy

    What it may not do:

        add people
        turn a soldier back into a citizen
        alter an existing soldier's record
        change who owns anybody
        invent per-citizen values

    Every person missing from a submission is a soldier retired, as no other
    command removes anybody; a citizen is drafted first. Across a tick the
    engine's recruitment makes soldiers from food without costing a citizen.
    """
    import collections
    pa, sa = population(served, civ_oid)
    pb, sb = population(submitted, civ_oid)
    if pa is None or pb is None:
        return False, 'the citizen or military array is unreadable'
    if set(pa) != set(pb) or set(sa) != set(sb):
        return False, 'the set of planets or ships changed within the turn'

    cit_a, sol_a = _everyone(pa, sa)
    cit_b, sol_b = _everyone(pb, sb)
    retired = len(cit_a) + len(sol_a) - len(cit_b) - len(sol_b)
    if retired < 0:
        return (False, f'{len(cit_a) + len(sol_a)} people served and '
                f'{len(cit_b) + len(sol_b)} came back')
    if len(cit_b) > len(cit_a):
        return (False, f'{len(cit_a)} citizen(s) served and {len(cit_b)} came '
                f'back; no command turns a soldier back into a citizen')
    gone = collections.Counter(sol_a) - collections.Counter(sol_b)
    if sum(gone.values()) > retired:
        return (False, "an existing soldier's record was rewritten rather than "
                "moved or retired")
    owners_a = collections.Counter([c[1] for c in cit_a]
                                   + [_owner_of(r) for r in sol_a])
    owners_b = collections.Counter([c[1] for c in cit_b]
                                   + [_owner_of(r) for r in sol_b])
    if owners_b - owners_a:
        return False, 'somebody changed hands'
    bad = {c[0] for c in cit_b} - set(JOB_IDS)
    if bad:
        return False, f'unknown job id(s) {sorted(bad)}'

    # What a record carries may not be invented: judged against what was
    # served (a civ may carry whatever the engine gave it, including non-zero
    # opaque bytes), across citizens, soldiers and crew at once, because
    # conscription rewrites only the job.
    all_a = [_record(r) for r in sol_a] + list(cit_a)
    all_b = [_record(r) for r in sol_b] + list(cit_b)
    if collections.Counter(c[3] for c in all_b) - collections.Counter(
            c[3] for c in all_a):
        return (False, 'opaque bytes in a people record were invented rather '
                'than carried')
    if collections.Counter(c[2] for c in all_b) - collections.Counter(
            c[2] for c in all_a):
        return False, 'per-citizen values were invented rather than reordered'
    return True, ''


def foreign_people_refusals(planets, crews, submitted, civ_oid, names):
    """[reason] for people changed on a planet or ship another civ owns.

    `planets` and `crews` are `planet_index` and `_ship_crew_index` of the
    state served. `people_acceptable` judges only what the submitter owns, so
    a change anywhere else is named here rather than passed over. A civ's own
    citizens on such a planet are named with what was done to them.
    """
    out = []
    after = planet_index(submitted)
    for oid, (owner, _prod, plpr, _nm) in sorted(planets.items()):
        if owner == civ_oid or oid not in after or plpr is None:
            continue
        sub_plpr = after[oid][2]
        if sub_plpr is None:
            continue
        if (citizen_bytes(plpr), military_of(plpr)[1]) == (
                citizen_bytes(sub_plpr), military_of(sub_plpr)[1]):
            continue
        why = (f"planet {oid}: people DROPPED, owned by "
               f"{names.get(owner, owner) if owner else 'nobody'}")
        cz_a, cz_b = citizens_of(plpr), citizens_of(sub_plpr)
        if cz_a is not None and cz_b is not None:
            own_a = [c[0] for c in cz_a if c[1] == civ_oid]
            own_b = [c[0] for c in cz_b if c[1] == civ_oid]
            if sorted(own_a) != sorted(own_b):
                why += (f"; {names.get(civ_oid, civ_oid)}'s citizens there "
                        f"went {_tally(own_a) or 'none'} -> "
                        f"{_tally(own_b) or 'none'}, and people on another "
                        f"civ's planet are not carried")
        out.append(why)
    after = _ship_crew_index(submitted)
    for oid, (owner, shpr) in sorted(crews.items()):
        if owner == civ_oid or oid not in after:
            continue
        if crew_of(shpr or b'') != crew_of(after[oid][1] or b''):
            out.append(f"ship {oid}: crew DROPPED, owned by "
                       f"{names.get(owner, owner)}")
    return out


# A wormhole is a `WORM` v0 among `GLXY`'s children, after its objects (writer
# 0x004FEF50): the object header (id, x, z, y, owner), a u32, a u32-length
# string, and last the object id of the wormhole it is linked to, 0 for none.
WORM_LINK_BACK = 4


def wormholes(blob):
    """{wormholeObjectId: (owner, linked id, payload bytes)}."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    out = {}
    for sec in glxy.find('WORM'):
        if sec.size < OBJECT_HEADER + WORM_LINK_BACK:
            continue
        oid, _x, _z, _y, owner = struct.unpack_from('<IfffI', blob,
                                                    sec.payload)
        link = struct.unpack_from('<I', blob, sec.end - WORM_LINK_BACK)[0]
        out[oid] = (owner, link, bytes(blob[sec.payload:sec.end]))
    return out


def set_wormhole_link(blob, oid, link):
    """`blob` with one wormhole's link set. Size does not change."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    for sec in glxy.find('WORM'):
        if struct.unpack_from('<I', blob, sec.payload)[0] == oid:
            out = bytearray(blob)
            struct.pack_into('<I', out, sec.end - WORM_LINK_BACK, link)
            return bytes(out)
    raise KeyError(oid)


def wormhole_orders(served, submitted, civ_oid):
    """([(a, b)], refusals): the pairs of the civ's own wormholes it connected.

    The Connect To menu (`0x005080B0`) opens on the civ's own unlinked
    wormhole and lists every other unlinked one it owns, at any distance;
    `CNWH` links the two to each other. An ally's wormhole is offered too, but
    picking it proposes a treaty instead. A link is never cleared by a player.
    """
    was, now = wormholes(served), wormholes(submitted)
    pairs, refused, seen = [], [], set()
    for oid in sorted(set(was) | set(now)):
        if oid not in was or oid not in now:
            refused.append(f'wormhole {oid} DROPPED, it was '
                           f'{"added" if oid in now else "removed"}')
            continue
        (owner, link_a, raw_a), (_o, link_b, raw_b) = was[oid], now[oid]
        if raw_a == raw_b or oid in seen:
            continue
        other = now.get(link_b)
        why = None
        if raw_a[:-WORM_LINK_BACK] != raw_b[:-WORM_LINK_BACK]:
            why = 'it came back changed beyond its link'
        elif owner != civ_oid:
            why = f"it is civ {owner}'s"
        elif link_a:
            why = 'it was already linked'
        elif link_b not in was or link_b == oid:
            why = f'it names {link_b}, which is not another wormhole'
        elif was[link_b][0] != civ_oid:
            why = (f'wormhole {link_b} is not the civ\'s; an ally\'s is '
                   f'connected by treaty')
        elif was[link_b][1] or other[1] != oid:
            why = f'wormhole {link_b} was linked, or does not name it back'
        elif other[2][:-WORM_LINK_BACK] != was[link_b][2][:-WORM_LINK_BACK]:
            why = f'wormhole {link_b} came back changed beyond its link'
        if why:
            refused.append(f'wormhole {oid} connection DROPPED, {why}')
            seen.add(oid)
            continue
        pairs.append((oid, link_b))
        seen.update((oid, link_b))
    return pairs, refused


def systems(blob):
    """{sunObjectId: (planet_owner_counts, planet_total, (len, name))}."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    out = {}
    for sola in (c for c in glxy.children if c.tag == b'SOLA'):
        sun = next((s for s in sola.children if s.tag == b'SUN '), None)
        if sun is None:
            continue
        oid = struct.unpack_from('<I', blob, sun.payload)[0]
        owners, total = {}, 0
        for sec in sola.find('PLNT'):
            total += 1
            o = struct.unpack_from('<I', blob, sec.payload + 16)[0]
            if o:
                owners[o] = owners.get(o, 0) + 1
        out[oid] = (owners, total, object_name(blob, sun))
    return out


def set_system_name(blob, sun_oid, name: bytes):
    """Write a system's name onto its SUN section."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    for sola in (c for c in glxy.children if c.tag == b'SOLA'):
        for sun in sola.children:
            if sun.tag != b'SUN ':
                continue
            if struct.unpack_from('<I', blob, sun.payload)[0] != sun_oid:
                continue
            at = sun.payload + PLANET_NAME_OFF
            old = struct.unpack_from('<I', blob, at)[0]
            body = (bytes(blob[sun.payload:at]) + struct.pack('<I', len(name))
                    + name + bytes(blob[at + 4 + old:sun.end]))
            return sp.replace_payload(blob, tree, sun, body)
    raise SystemExit(f'system {sun_oid} not found')


def object_name(blob, sec):
    """(length, name_bytes) from a PLNT or SUN section, or None."""
    own = sp.own_bytes(blob, sec)
    if len(own) < PLANET_NAME_OFF + 4:
        return None
    n = struct.unpack_from('<I', own, PLANET_NAME_OFF)[0]
    if n > MAX_NAME or PLANET_NAME_OFF + 4 + n > len(own):
        return None
    return n, bytes(own[PLANET_NAME_OFF + 4:PLANET_NAME_OFF + 4 + n])


def planet_name(blob, sec):
    """(length, name_bytes) from a PLNT section, or None if unreadable."""
    return object_name(blob, sec)


def name_acceptable(name: bytes):
    """(ok, reason) for a submitted planet name."""
    if len(name) > MAX_NAME:
        return False, f'name is {len(name)} bytes, over {MAX_NAME}'
    if any(b < 0x20 or b > 0x7e for b in name):
        return False, 'name holds bytes outside printable ASCII'
    return True, ''


def set_planet_name(blob, planet_oid, name: bytes):
    """Write a planet's name. Its length may change, so sizes are corrected."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    for sola in (c for c in glxy.children if c.tag == b'SOLA'):
        for sec in sola.find('PLNT'):
            if struct.unpack_from('<I', blob, sec.payload)[0] != planet_oid:
                continue
            at = sec.payload + PLANET_NAME_OFF
            old = struct.unpack_from('<I', blob, at)[0]
            body = (bytes(blob[sec.payload:at]) + struct.pack('<I', len(name))
                    + name + bytes(blob[at + 4 + old:sec.end]))
            return sp.replace_payload(blob, tree, sec, body)
    raise SystemExit(f'planet {planet_oid} not found')


def research_offsets(blob, owpr):
    """Offsets within OWPR's payload of the research fields, or None when
    the lists before them do not fit the section."""
    p, size = owpr.payload, owpr.size
    if size < OWPR_MAP_COUNT_OFF + 4:
        return None
    m = struct.unpack_from('<I', blob, p + OWPR_MAP_COUNT_OFF)[0]
    n_at = OWPR_MAP_COUNT_OFF + 4 + m * OWPR_RECORD
    if n_at + 4 > size:
        return None
    n = struct.unpack_from('<I', blob, p + n_at)[0]
    first = n_at + 4 + n * OWPR_RECORD
    offs = tuple(first + i * OWPR_RECORD for i in range(RESEARCH_FIELD_COUNT))
    if offs[-1] + RESEARCH_FIELD_LEN > size:
        return None
    return offs


def completed_research(blob, civ_oid):
    """The technology ids a civ has completed, in order, or None."""
    owpr = _owpr(blob, civ_oid)
    if owpr is None:
        return None
    p = owpr.payload
    m = struct.unpack_from('<I', blob, p + OWPR_MAP_COUNT_OFF)[0]
    n_at = p + OWPR_MAP_COUNT_OFF + 4 + m * OWPR_RECORD
    n = struct.unpack_from('<I', blob, n_at)[0]
    return [struct.unpack_from('<I', blob, n_at + 4 + i * OWPR_RECORD)[0]
            for i in range(n)]


def research_of(blob, civ_oid):
    """The bytes of a civ's research fields, or None when OWPR is unreadable."""
    owpr = _owpr(blob, civ_oid)
    if owpr is None:
        return None
    return tuple(bytes(blob[owpr.payload + off:owpr.payload + off + RESEARCH_FIELD_LEN])
                 for off in research_offsets(blob, owpr))


def _owpr(blob, civ_oid):
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    for o in glxy.children:
        if o.tag != b'OWNR':
            continue
        n = struct.unpack_from('<I', blob, o.payload)[0]
        if struct.unpack_from('<I', blob, o.payload + 4 + n)[0] != civ_oid:
            continue
        owpr = next(o.find('OWPR'), None)
        if owpr is None or research_offsets(blob, owpr) is None:
            return None
        return owpr
    return None


def civ_by_name(blob, name):
    for o in icv.owner_records(blob):
        if o['name'] == name:
            return o
    raise SystemExit(f"no civ named {name!r}; saw "
                     f"{[o['name'] for o in icv.owner_records(blob)]}")


def _replace_child(blob, parent_finder, new_bytes):
    """Swap one section for bytes of any length, fixing every enclosing size."""
    tree = sp.parse_blob(blob)
    target = parent_finder(blob, tree)
    if target is None:
        raise SystemExit('the section to replace is not in this blob')
    parent, sec = target
    body = (bytes(blob[parent.payload:sec.start]) + new_bytes +
            bytes(blob[sec.end:parent.end]))
    return sp.replace_payload(blob, tree, parent, body)


def replace_dyno(blob, ship_oid, new_dyno):
    def find(b, tree):
        glxy = next(tree[0].find('GLXY'))
        for sec in glxy.find('SHIP'):
            if struct.unpack_from('<I', b, sec.payload)[0] != ship_oid:
                continue
            dyno = next(sec.find('DYNO'), None)
            return (sec, dyno) if dyno else None
        return None
    return _replace_child(blob, find, new_dyno)


def replace_prod(blob, planet_oid, new_prod):
    def find(b, tree):
        glxy = next(tree[0].find('GLXY'))
        for sola in (c for c in glxy.children if c.tag == b'SOLA'):
            for sec in sola.find('PLNT'):
                if struct.unpack_from('<I', b, sec.payload)[0] != planet_oid:
                    continue
                plpr = next(sec.find('PLPR'), None)
                if plpr is None:
                    return None
                prod = next(plpr.find('PROD'), None)
                return (plpr, prod) if prod else None
        return None
    return _replace_child(blob, find, new_prod)


def progress_of(plpr):
    """Production points accumulated on a planet, or None."""
    if plpr is None or len(plpr) < PROGRESS_OFF + 4:
        return None
    return struct.unpack_from('<I', plpr, PROGRESS_OFF)[0]


def design_sections(blob):
    """([(civObjectId, DATA section, [DSGN sections])], tree) for every civ.

    The tree comes back with them because `sp.replace_payload` has to be given
    the one its target was parsed from.
    """
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    out = []
    for o in glxy.children:
        if o.tag != b'OWNR':
            continue
        n = struct.unpack_from('<I', blob, o.payload)[0]
        oid = struct.unpack_from('<I', blob, o.payload + 4 + n)[0]
        data = next((c for c in o.children if c.tag == b'DATA'), None)
        if data is None:
            continue
        out.append((oid, data, [c for c in data.children if c.tag == b'DSGN']))
    return out, tree


def design_index(blob):
    """{designObjectId: {civ, name, parts, claims, record}} for every design.

    `civ` is the civ whose `OWNR` the design sits in and `claims` is the civ its
    own payload names. `name`, `parts` and `claims` are None when the record
    does not parse.
    """
    out = {}
    for civ_oid, _data, designs in design_sections(blob)[0]:
        for d in designs:
            oid = struct.unpack_from('<I', blob, d.payload)[0]
            try:
                _oid, name, parts, claims = idg.read_design(blob, d.start)
            except Exception:                                   # noqa: BLE001
                name, parts, claims = None, None, None
            out[oid] = {'civ': civ_oid, 'name': name, 'parts': parts,
                        'claims': claims,
                        'record': bytes(blob[d.start:d.end])}
    return out


def designs_of(blob, civ_oid):
    """The design object ids one civ owns."""
    return {oid for oid, d in design_index(blob).items() if d['civ'] == civ_oid}


def next_object_id(blob):
    """An object id nothing in this blob is using.

    `SAVE+0` is the highest object id in use, not a count of objects, and the
    engine allocates its next object at that value plus one. A carried design
    must raise it (`set_high_water`).
    """
    return idg.max_object_id(blob) + 1


def set_high_water(blob, oid):
    """Raise `SAVE`'s object counter to cover an object just added."""
    out = bytearray(blob)
    if struct.unpack_from('<I', out, idg.HIGH_WATER_ID)[0] < oid:
        struct.pack_into('<I', out, idg.HIGH_WATER_ID, int(oid))
    return bytes(out)


def with_design_id(record, oid):
    """A `DSGN` record carrying a different object id."""
    out = bytearray(record)
    struct.pack_into('<I', out, 8, int(oid))
    return bytes(out)


def with_prod_design(prod, oid):
    """A `PROD` section whose queued ship names a different design."""
    out = bytearray(prod)
    struct.pack_into('<I', out, PROD_DESIGN_AT, int(oid))
    return bytes(out)


def design_name_acceptable(name, others=()):
    """(ok, reason) for a design name, as the client's check `0x0053A380` and
    the edit controls it follows allow. `others` are the civ's other designs'
    names."""
    if name is None:
        return False, 'the record does not parse'
    raw = name.encode('latin1')
    if not raw:
        return False, 'the name is empty'
    if len(raw) > MAX_DESIGN_NAME:
        return (False, f'the name is {len(raw)} bytes and the buffer the '
                f'client offers holds {MAX_DESIGN_NAME}')
    if any(b < 0x20 or b > 0x7e for b in raw):
        return False, 'the name holds bytes outside printable ASCII'
    if name != name.strip(' \t'):
        return False, 'the name has spaces the client trims'
    if name.upper() == RESERVED_DESIGN_NAME:
        return False, f'{name!r} is reserved for a design left unnamed'
    if name.upper() in {o.upper() for o in others if o is not None}:
        return (False, f'this civ already owns one named {name!r} and the '
                f'client requires a name to be unique within a civ, '
                f'whatever its case')
    return True, ''


def designs_in_use(blob, civ_oid):
    """{designObjectId: reason} for a civ's designs the Ship Design screen
    shows as in use (`0x0050E490`): built into one of its ships, being built
    or queued on one of its planets or queues, or named by one of its
    admirals' rules. In use, a design opens read-only and Delete is greyed.

    An admiral's rules are not decoded, so any rule holding the design's id
    as a u32 counts."""
    out = {}
    mine = designs_of(blob, civ_oid)
    for ship, (owner, shpr) in sorted(_ship_crew_index(blob).items()):
        if owner == civ_oid and shpr and len(shpr) >= 4:
            d = struct.unpack_from('<I', shpr, 0)[0]
            out.setdefault(d, f'ship {ship} is built to it')
    for planet, (owner, prod, _plpr, _nm) in sorted(planet_index(blob).items()):
        if owner == civ_oid and prod_builds(prod) is not None:
            out.setdefault(prod_builds(prod), f'planet {planet} is building it')
    for qid, record in queues_of(blob, civ_oid) or ():
        for _e, item_type, item, _at in read_queue(record)[0] or ():
            if item_type == ITEM_SHIP:
                out.setdefault(item, f'production queue {qid} lists it')
    for aid, record in admirals_of(blob, civ_oid) or ():
        for d in mine:
            if struct.pack('<I', d) in record[8 + ADML_FIXED:]:
                out.setdefault(d, f'admiral {aid} names it in a rule')
    return {d: why for d, why in out.items() if d in mine}


def remove_design(blob, civ_oid, design_oid):
    """`blob` without one of a civ's designs, its design count lowered."""
    secs, tree = design_sections(blob)
    for oid, data, designs in secs:
        if oid != civ_oid:
            continue
        hit = next((d for d in designs if struct.unpack_from(
            '<I', blob, d.payload)[0] == design_oid), None)
        if hit is None:
            raise KeyError(design_oid)
        count_at = designs[0].start - DESIGN_COUNT_BEFORE - data.payload
        body = bytearray(blob[data.payload:data.end])
        struct.pack_into('<I', body, count_at, len(designs) - 1)
        del body[hit.start - data.payload:hit.end - data.payload]
        return sp.replace_payload(blob, tree, data, bytes(body))
    raise SystemExit(f'civ {civ_oid} is not in this blob')


def replace_design(blob, design_oid, record):
    """`blob` with one design's whole `DSGN` section replaced."""
    secs, tree = design_sections(blob)
    for _oid, data, designs in secs:
        for d in designs:
            if struct.unpack_from('<I', blob, d.payload)[0] != design_oid:
                continue
            body = bytearray(blob[data.payload:data.end])
            body[d.start - data.payload:d.end - data.payload] = record
            return sp.replace_payload(blob, tree, data, bytes(body))
    raise KeyError(design_oid)


def add_design(blob, civ_oid, record):
    """Append a `DSGN` to a civ's `DATA` and bump its design count.

    Every enclosing size is corrected. The count dword is only locatable
    relative to the civ's first `DSGN`, so a civ owning none is refused.
    """
    secs, tree = design_sections(blob)
    for oid, data, designs in secs:
        if oid != civ_oid:
            continue
        if not designs:
            raise SystemExit(f'civ {civ_oid} owns no design to append after, '
                             f'and the count dword is only locatable from one')
        first, last = designs[0], designs[-1]
        count_at = first.start - DESIGN_COUNT_BEFORE
        if count_at < data.payload:
            raise SystemExit(f'civ {civ_oid} has no design count before its '
                             f'first DSGN')
        count = struct.unpack_from('<I', blob, count_at)[0]
        if count != len(designs):
            raise SystemExit(f'civ {civ_oid} holds {len(designs)} design(s) '
                             f'and a count field reading {count}')
        body = bytearray(blob[data.payload:data.end])
        struct.pack_into('<I', body, count_at - data.payload, count + 1)
        at = last.end - data.payload
        body[at:at] = record
        return sp.replace_payload(blob, tree, data, bytes(body))
    raise SystemExit(f'civ {civ_oid} is not in this blob')


# The client's scanner-range getter `0x005479C0` throws on an empty scanner
# list, and the Ship Design tab, the editor's stats and a fleet's scan range
# all call it. The editor starts every design with a scanner.
NO_SCANNER = ('it has no scanner, and the client closes on a design without '
              'one')


def design_orders(served, submitted, civ_oid, in_use=None):
    """(new, toggled, edited, deleted, refusals) for one civ's ship designs.

    `served` and `submitted` are `design_index` results and `in_use` is
    `designs_in_use` of the state served. `new` is [(objectId, design)] for
    designs the served state lacks, `toggled` [(objectId, inactive)] for a
    record that differs only in its last byte, `edited` [(objectId, record)]
    for a rename or a modified design, and `deleted` the object ids deleted.
    Touching another civ's design is refused and named.
    """
    in_use = in_use or {}
    new, toggled, edited, deleted, refused = [], [], [], [], []
    names = {k: v['name'] for k, v in submitted.items() if v['civ'] == civ_oid}

    for oid in sorted(set(served) - set(submitted)):
        label = f'design {oid} ({served[oid]["name"]!r})'
        if served[oid]['civ'] != civ_oid:
            refused.append(f"{label} DROPPED, it is missing from the "
                           f"submission and is another civ's")
        elif oid in in_use:
            refused.append(f'{label} deletion DROPPED, it is in use: '
                           f'{in_use[oid]}, and the client greys Delete out')
        elif not any(v['civ'] == civ_oid for v in submitted.values()):
            refused.append(f'{label} deletion DROPPED, it is the civ\'s last '
                           f'design and the referee finds the design count '
                           f'from a design')
        else:
            deleted.append(oid)

    for oid in sorted(set(served) & set(submitted)):
        was, now = served[oid], submitted[oid]
        if now['record'] == was['record']:
            continue
        label = f'design {oid} ({was["name"]!r})'
        if was['civ'] != civ_oid or now['civ'] != civ_oid:
            refused.append(f"{label} DROPPED, it is another civ's")
            continue
        if (len(now['record']) == len(was['record'])
                and now['record'][:-1] == was['record'][:-1]
                and now['record'][-1] in (0, 1)):
            toggled.append((oid, bool(now['record'][-1])))
            continue
        if now['name'] is None or was['name'] is None:
            refused.append(f'{label} DROPPED, the record does not parse')
            continue
        if (now['record'][-1] not in (0, 1)
                or now['claims'] != was['claims']
                or _design_versions(now['record'])
                != _design_versions(was['record'])):
            refused.append(f'{label} DROPPED, it came back changed beyond its '
                           f'name, parts and inactive byte')
            continue
        if now['name'] != was['name']:
            ok, why = design_name_acceptable(
                now['name'], [n for k, n in names.items() if k != oid])
            if not ok:
                refused.append(f'{label} rename DROPPED, {why}')
                continue
        if now['parts'] != was['parts'] and oid in in_use:
            refused.append(f'{label} edit DROPPED, it is in use: '
                           f'{in_use[oid]}, and the client opens it read-only')
            continue
        if now['parts'] != was['parts'] and not now['parts']['scanners']:
            refused.append(f'{label} edit DROPPED, {NO_SCANNER}')
            continue
        edited.append((oid, now['record']))

    for oid in sorted(set(submitted) - set(served)):
        d = submitted[oid]
        if d['civ'] != civ_oid:
            refused.append(f'design {oid} ({d["name"]!r}) DROPPED, it arrived '
                           f"under another civ's OWNR")
            continue
        ok, why = design_name_acceptable(
            d['name'], [n for k, n in names.items() if k != oid])
        if not ok:
            refused.append(f'design {oid} DROPPED, {why}')
            continue
        if d['claims'] != civ_oid:
            refused.append(f'design {oid} ({d["name"]!r}) DROPPED, its payload '
                           f'names civ {d["claims"]} as the owner, not the '
                           f'submitter')
            continue
        if not d['parts']['scanners']:
            refused.append(f'design {oid} ({d["name"]!r}) DROPPED, {NO_SCANNER}')
            continue
        new.append((oid, d))
    return new, toggled, edited, deleted, refused


def _design_versions(record):
    """The `DSGN` and `SDPR` section versions of a whole `DSGN` record."""
    return (struct.unpack_from('<I', record, 4)[0] >> sp.VERSION_SHIFT,
            struct.unpack_from('<I', record, 16)[0] >> sp.VERSION_SHIFT)


# A civ's scan reports follow its designs in `DATA` (writer 0x0053B5F0): a u32
# count, then one section per report named for its class (`SCPL` planetary,
# `SCSY`, `SCRO` route, `SCFL`, `SCML`, `SCTE`, `SCEG`, `SCLN`), each holding
# the type id, a `SCAN` header and the class's own payload. The civ's news
# count follows them.
REPORT_TAGS = (b'SCPL', b'SCSY', b'SCRO', b'SCFL', b'SCML', b'SCTE', b'SCEG',
               b'SCLN')


def reports_of(blob, civ_oid):
    """[whole report section] of a civ in order, or None when the block after
    its designs does not read as one."""
    found = _report_block(blob, civ_oid)
    return None if found is None else found[3]


def _report_block(blob, civ_oid):
    """(tree, DATA section, (start, end) of the block, [reports]) or None."""
    secs, tree = design_sections(blob)
    for oid, data, designs in secs:
        if oid != civ_oid:
            continue
        if not designs:
            return None
        at = designs[-1].end
        if at + 4 > data.end:
            return None
        n = struct.unpack_from('<I', blob, at)[0]
        p, out = at + 4, []
        for _ in range(n):
            if p + 8 > data.end or bytes(blob[p:p + 4]) not in REPORT_TAGS:
                return None
            end = p + 8 + (struct.unpack_from('<I', blob, p + 4)[0]
                           & sp.SIZE_MASK)
            if end > data.end:
                return None
            out.append(bytes(blob[p:end]))
            p = end
        return tree, data, (at, p), out
    return None


def set_reports(blob, civ_oid, reports):
    """`blob` with a civ's scan reports replaced by `reports`."""
    found = _report_block(blob, civ_oid)
    if found is None:
        raise SystemExit(f'civ {civ_oid} has no readable scan report block')
    tree, data, (at, end), _old = found
    body = bytearray(blob[data.payload:data.end])
    body[at - data.payload:end - data.payload] = (
        struct.pack('<I', len(reports)) + b''.join(reports))
    return sp.replace_payload(blob, tree, data, bytes(body))


def report_orders(served, submitted):
    """(kept, deleted, made, why) for a civ's scan reports.

    `kept` is what the civ keeps of the reports served, `deleted` how many it
    deleted, `made` how many new reports the submission holds, and `why` a
    reason when the served reports came back other than in order, less some.
    """
    kept = [r for r in submitted if r in served]
    made = len(submitted) - len(kept)
    it = iter(served)
    if not all(any(r == s for s in it) for r in kept):
        return list(served), 0, made, 'the reports served came back reordered'
    return kept, len(served) - len(kept), made, ''


def set_design_inactive(blob, design_oid, inactive):
    """`blob` with a design's last byte, `ShipDesign+0x114`, set: 1 inactive,
    0 active. The design's own section ends with it."""
    for _civ, _data, designs in design_sections(blob)[0]:
        for d in designs:
            if struct.unpack_from('<I', blob, d.payload)[0] == design_oid:
                out = bytearray(blob)
                out[d.end - 1] = 1 if inactive else 0
                return bytes(out)
    raise KeyError(design_oid)


# What a PROD can name in its nested section: nothing (piling up wealth), a
# facility, a scan, or a ship by design id.
PROD_KINDS = (b'WLTH', b'FCLT', b'SCAN', b'SHIP')
PROD_QUEUE_OFF = 2          # queue flag, queue object id, u16 entry id
PROD_LIST_OFF = 9           # u32 count of four-byte pairs
PROD_FACILITY_OFF = 13      # u32 facility type, after an empty list
PROD_KIND_OFF = 21          # the nested section, after an empty list


def _prod_list_len(prod):
    """Bytes taken by PROD's list at +9: a u32 count of four-byte pairs. It
    has been empty in every capture, but what follows it moves with it."""
    return 4 * struct.unpack_from('<I', prod, 8 + PROD_LIST_OFF)[0]


def prod_kind(prod):
    """The four-byte tag of what a PROD builds, or None."""
    if prod is None or len(prod) < 8 + PROD_LIST_OFF + 4:
        return None
    at = 8 + PROD_KIND_OFF + _prod_list_len(prod)
    if len(prod) < at + 4:
        return None
    return bytes(prod[at:at + 4])


def prod_queue(prod):
    """The production-queue link in a PROD: the flag at +2, the queue's object
    id at +3 and the u16 at +7. All zero on a planet in no queue."""
    if prod is None or len(prod) < 8 + PROD_LIST_OFF:
        return None
    return bytes(prod[8 + PROD_QUEUE_OFF:8 + PROD_LIST_OFF])


def queue_link(prod):
    """(flag, queue object id, entry id) of a PROD, or None.

    The entry id names the queue entry the planet is building, 0 after a
    manual override."""
    link = prod_queue(prod)
    return None if link is None else struct.unpack('<BIH', link)


def with_prod_queue(prod, oid):
    """A `PROD` section linked to a different queue object id."""
    out = bytearray(prod)
    struct.pack_into('<I', out, 8 + PROD_QUEUE_OFF + 1, int(oid))
    return bytes(out)


def prod_facility(prod):
    """The facility type a PROD builds, or None when it builds no facility."""
    if prod_kind(prod) != b'FCLT':
        return None
    return struct.unpack_from('<I', prod, 8 + PROD_FACILITY_OFF
                              + _prod_list_len(prod))[0]


def facility_refusal(blob, completed, plpr, facility_type):
    """Why a facility may not be started on a planet, or '' when it may."""
    if plpr is None:
        return 'the planet has no PLPR'
    ok, why = facility_rules.can_build(blob, completed, plpr,
                                       facilities_of(plpr) or [], facility_type)
    return '' if ok else why


def prod_builds(prod):
    """The design a queue entry names, or None if it names none.

    A `PROD` payload ends with a nested section saying what is queued: `FCLT`
    with an empty payload for a facility, `WLTH` for nothing, and `SHIP` with a
    four-byte payload holding the design's object id.
    """
    if prod is None or len(prod) < 8 + 33:
        return None
    body = prod[8:]
    if body[21:25] != b'SHIP':
        return None
    size = struct.unpack_from('<I', body, 25)[0] & sp.SIZE_MASK
    if size < 4 or len(body) < 33:
        return None
    return struct.unpack_from('<I', body, 29)[0]


def military_of(plpr):
    """(offset of the count, [records]) for a planet's stationed military.

    Soldiers are the same nine-byte record as citizens, in a second array that
    begins where the citizen array ends: a `u32` count, then that many records,
    then the rest of the planet's fields.
    """
    if plpr is None or len(plpr) < POP_ARRAY_OFF + 4:
        return None, None
    pop = struct.unpack_from('<I', plpr, POP_COUNT_OFF)[0]
    at = POP_ARRAY_OFF + pop * POP_RECORD
    if at + 4 > len(plpr):
        return None, None
    n = struct.unpack_from('<I', plpr, at)[0]
    end = at + 4 + n * POP_RECORD
    if end > len(plpr):
        return None, None
    return at, [bytes(plpr[at + 4 + i * POP_RECORD:
                           at + 4 + (i + 1) * POP_RECORD]) for i in range(n)]


def crew_of(shpr):
    """[records] for a ship's crew, or None.

    `SHPR+4` is the count and the records follow at `+8`.
    """
    if shpr is None or len(shpr) < CREW_ARRAY_OFF:
        return None
    n = struct.unpack_from('<I', shpr, CREW_COUNT_OFF)[0]
    end = CREW_ARRAY_OFF + n * POP_RECORD
    if end > len(shpr):
        return None
    return [bytes(shpr[CREW_ARRAY_OFF + i * POP_RECORD:
                       CREW_ARRAY_OFF + (i + 1) * POP_RECORD]) for i in range(n)]


def set_military(blob, planet_oid, records):
    """Write a planet's stationed military. The section's size changes."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    for sola in (c for c in glxy.children if c.tag == b'SOLA'):
        for sec in sola.find('PLNT'):
            if struct.unpack_from('<I', blob, sec.payload)[0] != planet_oid:
                continue
            plpr = next(sec.find('PLPR'), None)
            if plpr is None:
                raise SystemExit(f'planet {planet_oid} has no PLPR')
            raw = bytes(blob[plpr.payload:plpr.end])
            at, old = military_of(raw)
            if at is None:
                raise SystemExit(f'planet {planet_oid} has no readable military')
            tail = raw[at + 4 + len(old) * POP_RECORD:]
            body = (raw[:at] + struct.pack('<I', len(records))
                    + b''.join(records) + tail)
            return sp.replace_payload(blob, tree, plpr, body)
    raise SystemExit(f'planet {planet_oid} not found')


def _shpr_section(blob, glxy, ship_oid):
    """A ship's SHPR section, in its SHIP or in the fleet it is in, or None.
    Raises when its SHIP has none."""
    for sec in glxy.children:
        if (sec.tag == b'SHIP'
                and struct.unpack_from('<I', blob, sec.payload)[0] == ship_oid):
            shpr = next(sec.find('SHPR'), None)
            if shpr is None:
                raise SystemExit(f'ship {ship_oid} has no SHPR')
            return shpr
        if sec.tag == b'FLET':
            for shpr in sec.find('SHPR'):
                ids = shpr_ids(bytes(blob[shpr.payload:shpr.end]))
                if ids is not None and ids[1] == ship_oid:
                    return shpr
    return None


def set_crew(blob, ship_oid, records):
    """Write a ship's crew, in a fleet or not. The section's size changes."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    shpr = _shpr_section(blob, glxy, ship_oid)
    if shpr is not None:
        raw = bytes(blob[shpr.payload:shpr.end])
        old = crew_of(raw)
        if old is None:
            raise SystemExit(f'ship {ship_oid} has no readable crew')
        tail = raw[CREW_ARRAY_OFF + len(old) * POP_RECORD:]
        body = (raw[:CREW_COUNT_OFF] + struct.pack('<I', len(records))
                + raw[CREW_COUNT_OFF + 4:CREW_ARRAY_OFF]
                + b''.join(records) + tail)
        return sp.replace_payload(blob, tree, shpr, body)
    raise SystemExit(f'ship {ship_oid} not found')


def recruit_of(plpr):
    """A planet's military recruitment rate as a percentage, or None."""
    if plpr is None or len(plpr) <= RECRUIT_OFF:
        return None
    return plpr[RECRUIT_OFF]


def food_of(plpr):
    """A planet's (food store, food capacity), or None.

    The pair the client renders as `store/capacity`.
    """
    if plpr is None or len(plpr) < FOOD_CAP_OFF + 2:
        return None
    return (struct.unpack_from('<H', plpr, FOOD_STORE_OFF)[0],
            struct.unpack_from('<H', plpr, FOOD_CAP_OFF)[0])


def set_food(blob, planet_oid, store, capacity):
    """Write a planet's food pair. Size does not change."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    for sola in (c for c in glxy.children if c.tag == b'SOLA'):
        for sec in sola.find('PLNT'):
            if struct.unpack_from('<I', blob, sec.payload)[0] != planet_oid:
                continue
            plpr = next(sec.find('PLPR'), None)
            if plpr is None:
                raise SystemExit(f'planet {planet_oid} has no PLPR')
            out = bytearray(blob)
            struct.pack_into('<H', out, plpr.payload + FOOD_STORE_OFF,
                             int(store) & 0xFFFF)
            struct.pack_into('<H', out, plpr.payload + FOOD_CAP_OFF,
                             int(capacity) & 0xFFFF)
            return bytes(out)
    raise SystemExit(f'planet {planet_oid} not found')


def _planet_plpr(blob, planet_oid):
    """(tree, PLPR section) for one planet, or raise naming what is missing."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    for sola in (c for c in glxy.children if c.tag == b'SOLA'):
        for sec in sola.find('PLNT'):
            if struct.unpack_from('<I', blob, sec.payload)[0] != planet_oid:
                continue
            plpr = next(sec.find('PLPR'), None)
            if plpr is None:
                raise SystemExit(f'planet {planet_oid} has no PLPR')
            return tree, plpr
    raise SystemExit(f'planet {planet_oid} not found')


def _facility_at(plpr):
    """Offset of the facility count within a PLPR payload, or None."""
    at, mil = military_of(plpr)
    if at is None:
        return None
    c = at + 4 + len(mil) * POP_RECORD + FACILITY_AFTER_MILITARY
    if c + 4 > len(plpr):
        return None
    n = struct.unpack_from('<I', plpr, c)[0]
    if c + 4 + n * FACILITY_RECORD > len(plpr):
        return None
    return c


def facilities_of(plpr):
    """A planet's facilities as [(type, count)], or None."""
    if plpr is None:
        return None
    c = _facility_at(plpr)
    if c is None:
        return None
    n = struct.unpack_from('<I', plpr, c)[0]
    return [struct.unpack_from('<II', plpr, c + 4 + i * FACILITY_RECORD)
            for i in range(n)]


def set_facilities(blob, planet_oid, pairs):
    """Write a planet's facility list. Size changes with the pair count."""
    tree, plpr = _planet_plpr(blob, planet_oid)
    raw = bytes(blob[plpr.payload:plpr.end])
    c = _facility_at(raw)
    if c is None:
        raise SystemExit(f'planet {planet_oid} has no readable facility list')
    n = struct.unpack_from('<I', raw, c)[0]
    body = (raw[:c] + struct.pack('<I', len(pairs))
            + b''.join(struct.pack('<II', t, k) for t, k in pairs)
            + raw[c + 4 + n * FACILITY_RECORD:])
    return sp.replace_payload(blob, tree, plpr, body)


def sale_refund(blob, facility_type, count):
    """Credits the engine pays for selling `count` of a facility type.

    `PlanetProperties::SellFacility` (`0x004F8580`) pays the type's cost from
    the client's facility table (`0x00529D80`), halved in a speed galaxy, times
    0.05 held as a float and rounded up, for each one sold.
    """
    costs = facility_rules.costs(facility_rules.content_version(blob))
    if not 0 <= facility_type < len(costs):
        return 0
    cost = costs[facility_type] // (2 if gset_value(blob, 'speed') else 1)
    each = struct.unpack('<f', struct.pack('<f', cost * SELL_SHARE))[0]
    whole = int(each)
    return (whole + 1 if each - whole > SELL_EPSILON else whole) * count


def sale_refusal(prod, facility_type):
    """Why the planet screen's Sell button refuses a facility type, or None.

    `0x004F5160`: a hurried item keeps the facility building it, the shipyard
    for a ship and the Central Defense Agency for a scan.
    """
    if not is_hurried(prod):
        return None
    kind = prod_kind(prod)
    if facility_type == SHIPYARD and kind == b'SHIP':
        return 'the shipyard is building a hurried ship'
    if facility_type == CDA and kind == b'SCAN':
        return 'the Central Defense Agency is building a hurried scan'
    return None


def facility_sales(served, submitted):
    """({type: count sold}, reason) between two facility lists.

    A sale lowers a type's count or removes the type; nothing else changes a
    planet's list within a turn. The reason is empty when the change is sales.
    """
    have, now = dict(served), dict(submitted)
    if len(have) != len(served) or len(now) != len(submitted):
        return {}, 'a facility type is listed twice'
    if [t for t, _k in submitted] != [t for t, _k in served if t in now]:
        return {}, 'the list is not the one served, less what was sold'
    sold = {}
    for t, k in served:
        left = now.get(t, 0)
        if left > k:
            return {}, f'facility {t} went {k} -> {left}; no command builds one'
        if left < k:
            sold[t] = k - left
    if any(k < 1 for k in now.values()):
        return {}, 'a type is listed with no facility'
    return sold, ''


def enli_of(blob):
    """{planetObjectId: [transmitter planet ids]} from each planet's `ENLI`."""
    out = {}
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    for sola in (c for c in glxy.children if c.tag == b'SOLA'):
        for sec in sola.find('PLNT'):
            plpr = next(sec.find('PLPR'), None)
            enli = next(plpr.find('ENLI'), None) if plpr else None
            if enli is None:
                continue
            n = struct.unpack_from('<I', blob, enli.payload)[0]
            if enli.size != 4 + 4 * n:
                continue
            oid = struct.unpack_from('<I', blob, sec.payload)[0]
            out[oid] = list(struct.unpack_from(f'<{n}I', blob,
                                               enli.payload + 4))
    return out


def set_enli(blob, planet_oid, ids):
    """Write a planet's energy links. Size changes with the count."""
    tree, plpr = _planet_plpr(blob, planet_oid)
    enli = next(plpr.find('ENLI'), None)
    if enli is None:
        raise SystemExit(f'planet {planet_oid} has no ENLI')
    body = struct.pack(f'<I{len(ids)}I', len(ids), *ids)
    return sp.replace_payload(blob, tree, enli, body)


def energy_link_refusal(planet_oid, links, civ_oid, served_planets):
    """Why the receiver's link menu could not have written `links`, or None.

    The menu (`0x0046B7xx`) opens on a planet with a receiver and lists the
    civ's own planets holding a transmitter (`0x005251E0`); a pick adds or
    removes that planet (`0x0046B4E0`).
    """
    owner, _prod, plpr, _nm = served_planets[planet_oid]
    if owner != civ_oid:
        return 'the planet is not the civ\'s'
    if dict(facilities_of(plpr) or ()).get(facility_rules.RECEIVER, 0) < 1:
        return 'the planet has no hyperspace receiver'
    if len(set(links)) != len(links):
        return 'a transmitter is linked twice'
    for t in links:
        o, _p, tp, _n = served_planets.get(t, (None, None, None, None))
        if o != civ_oid or dict(facilities_of(tp) or ()).get(
                facility_rules.TRANSMITTER, 0) < 1:
            return (f'planet {t} is not one of the civ\'s planets with a '
                    f'hyperspace transmitter')
    return None


def _block_at(blob, plpr):
    """(blob offset, rulers) of a planet's settled block, or None when it has
    none. A planet taken from another civ lists more than one ruler."""
    prod = next(plpr.find('PROD'), None)
    enli = next(plpr.find('ENLI'), None)
    if prod is None or enli is None:
        return None
    at = prod.end
    if enli.start - at < SETTLED_LEN:
        return None
    n = struct.unpack_from('<I', blob, at + INFLUENCE_COUNT_OFF)[0]
    if n < 1 or enli.start - at != SETTLED_LEN + INFLUENCE_RECORD * (n - 1):
        return None
    return at, n


def _settled_at(blob, plpr):
    """Blob offset of a settled block with one ruler, whose fields sit at the
    fixed offsets above, or None."""
    found = _block_at(blob, plpr)
    return found[0] if found and found[1] == 1 else None


def influence_of(blob, plpr):
    """([(owner, influence, first turn, last turn)], unrest) from a planet's
    settled block, or None. The current ruler's last turn reads RULING."""
    found = _block_at(blob, plpr)
    if found is None:
        return None
    at, n = found
    rulers = [struct.unpack_from('<IfIi', blob, at + INFLUENCE_AT + INFLUENCE_RECORD * k)
              for k in range(n)]
    unrest = struct.unpack_from('<H', blob, at + INFLUENCE_AT + INFLUENCE_RECORD * n
                                + UNREST_AFTER_LIST)[0]
    return rulers, unrest


def settled_of(blob, planet_oid):
    """The settled block's fields for one planet, or None when it has none."""
    _tree, plpr = _planet_plpr(blob, planet_oid)
    at = _settled_at(blob, plpr)
    if at is None:
        return None
    u16 = lambda off: struct.unpack_from('<H', blob, at + off)[0]
    u32 = lambda off: struct.unpack_from('<I', blob, at + off)[0]
    return {
        'pop': u16(SETTLED_POP_OFF),
        'history': [u16(SETTLED_HISTORY_OFF + 2 * i)
                    for i in range(SETTLED_HISTORY_LEN)],
        'counter': u32(SETTLED_COUNTER_OFF),
        'owner': u32(SETTLED_OWNER_OFF),
        'age': struct.unpack_from('<f', blob, at + SETTLED_AGE_OFF)[0],
        'founded': u32(SETTLED_FOUNDED_OFF),
        'founded_head': struct.unpack_from('<I', blob,
                                           plpr.payload + FOUNDED_OFF)[0],
        'last': u32(SETTLED_LAST_OFF),
    }


def set_settled(blob, planet_oid, pop=None, history=None, counter=None,
                age=None, founded=None, last=None):
    """Write the named settled-block fields. Size does not change.

    `founded` is written in both places a planet carries it. The owner field
    is not writable here: it is the civ's object id and `add_civ` sets it with
    every other owner reference.
    """
    _tree, plpr = _planet_plpr(blob, planet_oid)
    at = _settled_at(blob, plpr)
    if at is None:
        raise SystemExit(f'planet {planet_oid} has no settled block')
    out = bytearray(blob)
    if pop is not None:
        struct.pack_into('<H', out, at + SETTLED_POP_OFF, int(pop) & 0xFFFF)
    if history is not None:
        if len(history) != SETTLED_HISTORY_LEN:
            raise ValueError(f'history holds {SETTLED_HISTORY_LEN} turns')
        for i, v in enumerate(history):
            struct.pack_into('<H', out, at + SETTLED_HISTORY_OFF + 2 * i,
                             int(v) & 0xFFFF)
    if counter is not None:
        struct.pack_into('<I', out, at + SETTLED_COUNTER_OFF, int(counter))
    if age is not None:
        struct.pack_into('<f', out, at + SETTLED_AGE_OFF, float(age))
    if founded is not None:
        struct.pack_into('<I', out, at + SETTLED_FOUNDED_OFF, int(founded))
        struct.pack_into('<I', out, plpr.payload + FOUNDED_OFF, int(founded))
    if last is not None:
        struct.pack_into('<I', out, at + SETTLED_LAST_OFF, int(last))
    return bytes(out)


def set_recruit(blob, planet_oid, value):
    """Write a planet's recruitment rate. Size does not change."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    for sola in (c for c in glxy.children if c.tag == b'SOLA'):
        for sec in sola.find('PLNT'):
            if struct.unpack_from('<I', blob, sec.payload)[0] != planet_oid:
                continue
            plpr = next(sec.find('PLPR'), None)
            if plpr is None:
                raise SystemExit(f'planet {planet_oid} has no PLPR')
            out = bytearray(blob)
            out[plpr.payload + RECRUIT_OFF] = int(value) & 0xFF
            return bytes(out)
    raise SystemExit(f'planet {planet_oid} not found')


def is_hurried(prod):
    """Has the current item been paid off? None when there is no PROD."""
    if prod is None or len(prod) <= PROD_HURRIED_OFF:
        return None
    return bool(prod[PROD_HURRIED_OFF])


def credits_of(blob, civ_oid):
    """A civ's credits, or None when the civ is not in this blob."""
    rec = next((o for o in icv.owner_records(blob) if o['oid'] == civ_oid), None)
    if rec is None:
        return None
    at = rec['id_at'] + CREDITS_AFTER_ID
    if at + 4 > len(blob):
        return None
    return struct.unpack_from('<I', blob, at)[0]


def set_credits(blob, civ_oid, value):
    """Write a civ's credits. Size does not change."""
    rec = next((o for o in icv.owner_records(blob) if o['oid'] == civ_oid), None)
    if rec is None:
        raise SystemExit(f'civ {civ_oid} is not in this blob')
    out = bytearray(blob)
    struct.pack_into('<I', out, rec['id_at'] + CREDITS_AFTER_ID,
                     int(value))
    return bytes(out)


def set_progress(blob, planet_oid, value):
    """Write a planet's accumulated production points. Size does not change."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    for sola in (c for c in glxy.children if c.tag == b'SOLA'):
        for sec in sola.find('PLNT'):
            if struct.unpack_from('<I', blob, sec.payload)[0] != planet_oid:
                continue
            plpr = next(sec.find('PLPR'), None)
            if plpr is None:
                raise SystemExit(f'planet {planet_oid} has no PLPR')
            out = bytearray(blob)
            struct.pack_into('<I', out, plpr.payload + PROGRESS_OFF, int(value))
            return bytes(out)
    raise SystemExit(f'planet {planet_oid} not found')


def hurry_acceptable(served_plpr, submitted_plpr, credits):
    """(ok, why, cost, points) for a claimed hurry.

    The manual's rule: the points bought are how far the submission advanced
    the progress field, at four credits each. Refused when the item was less
    than half finished or the civ was not served enough credits. Hurrying
    completes the item, so the submitted progress is the item's total.
    """
    before, after = progress_of(served_plpr), progress_of(submitted_plpr)
    if before is None or after is None:
        return False, 'the progress field is unreadable', 0, 0
    points = after - before
    if points <= 0:
        return False, f'no progress was bought ({before} -> {after})', 0, 0
    if before * 2 < after:
        return (False, f'only {before} of {after} points were done, and the '
                f'engine requires half', 0, points)
    cost = points * HURRY_PER_POINT
    if credits is None:
        return False, 'the civ has no readable credit field', cost, points
    if credits < cost:
        return (False, f'{cost} credits needed and {credits} served', cost,
                points)
    return True, '', cost, points


def set_citizens(blob, planet_oid, new_array):
    """Write a planet's citizen array in place. Its length does not change."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    for sola in (c for c in glxy.children if c.tag == b'SOLA'):
        for sec in sola.find('PLNT'):
            if struct.unpack_from('<I', blob, sec.payload)[0] != planet_oid:
                continue
            plpr = next(sec.find('PLPR'), None)
            if plpr is None:
                raise SystemExit(f'planet {planet_oid} has no PLPR')
            at = plpr.payload + POP_ARRAY_OFF
            out = bytearray(blob)
            out[at:at + len(new_array)] = new_array
            return bytes(out)
    raise SystemExit(f'planet {planet_oid} not found')


def set_people(blob, planet_oid, citizens: bytes, military):
    """Write both of a planet's people arrays. Sizes change.

    The arrays are adjacent and each is length-prefixed, so both are written
    together.
    """
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    for sola in (c for c in glxy.children if c.tag == b'SOLA'):
        for sec in sola.find('PLNT'):
            if struct.unpack_from('<I', blob, sec.payload)[0] != planet_oid:
                continue
            plpr = next(sec.find('PLPR'), None)
            if plpr is None:
                raise SystemExit(f'planet {planet_oid} has no PLPR')
            raw = bytes(blob[plpr.payload:plpr.end])
            at, old = military_of(raw)
            if at is None:
                raise SystemExit(f'planet {planet_oid} has no readable military')
            tail = raw[at + 4 + len(old) * POP_RECORD:]
            body = (raw[:POP_COUNT_OFF]
                    + struct.pack('<I', len(citizens) // POP_RECORD) + citizens
                    + struct.pack('<I', len(military)) + b''.join(military)
                    + tail)
            return sp.replace_payload(blob, tree, plpr, body)
    raise SystemExit(f'planet {planet_oid} not found')


def set_research(blob, civ_oid, values):
    """Write a civ's research fields in place. Sizes do not change."""
    owpr = _owpr(blob, civ_oid)
    if owpr is None:
        raise SystemExit(f'civ {civ_oid} has no readable OWPR')
    out = bytearray(blob)
    for off, value in zip(research_offsets(blob, owpr), values):
        out[owpr.payload + off:owpr.payload + off + RESEARCH_FIELD_LEN] = value
    return bytes(out)


def _setup_flag_offsets(blob, owpr):
    """Absolute offsets of the (homeworld, civ) setup flags in this OWPR."""
    second = owpr.payload + research_offsets(blob, owpr)[-1]
    return second + HOMEWORLD_FLAG_OFF, second + CIV_FLAG_OFF


def setup_flags(blob, civ_oid):
    """(homeworld, civ) setup-prompt flags of a civ, or None when unreadable."""
    owpr = _owpr(blob, civ_oid)
    if owpr is None:
        return None
    hw, cv = _setup_flag_offsets(blob, owpr)
    if cv >= owpr.end:
        return None
    return blob[hw], blob[cv]


def set_setup_flag(blob, civ_oid, which, value=1):
    """Write one setup flag, 'homeworld' or 'civ'. Sizes do not change."""
    owpr = _owpr(blob, civ_oid)
    if owpr is None:
        raise SystemExit(f'civ {civ_oid} has no readable OWPR')
    hw, cv = _setup_flag_offsets(blob, owpr)
    out = bytearray(blob)
    out[hw if which == 'homeworld' else cv] = value
    return bytes(out)


def _cvtr(blob, civ_oid):
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    for o in glxy.children:
        if o.tag != b'OWNR':
            continue
        n = struct.unpack_from('<I', blob, o.payload)[0]
        if struct.unpack_from('<I', blob, o.payload + 4 + n)[0] == civ_oid:
            return next(o.find('CVTR'), None)
    return None


def traits_of(blob, civ_oid):
    """A civ's trait values in index order, or None unless CVTR holds exactly
    the dialog's traits, each once and in order."""
    cvtr = _cvtr(blob, civ_oid)
    if cvtr is None or cvtr.size < CVTR_PAIRS_OFF:
        return None
    count = struct.unpack_from('<I', blob, cvtr.payload + CVTR_COUNT_OFF)[0]
    if (count != len(TRAIT_RULES)
            or cvtr.size != CVTR_PAIRS_OFF + count * CVTR_PAIR):
        return None
    pairs = [struct.unpack_from('<Ii', blob, cvtr.payload + CVTR_PAIRS_OFF
                                + i * CVTR_PAIR) for i in range(count)]
    if [index for index, _v in pairs] != list(range(count)):
        return None
    return tuple(v for _i, v in pairs)


def set_traits(blob, civ_oid, values):
    """Write a civ's trait values in place. Sizes do not change."""
    cvtr = _cvtr(blob, civ_oid)
    if cvtr is None:
        raise SystemExit(f'civ {civ_oid} has no CVTR')
    out = bytearray(blob)
    for i, v in enumerate(values):
        struct.pack_into('<i', out, cvtr.payload + CVTR_PAIRS_OFF
                         + i * CVTR_PAIR + 4, v)
    return bytes(out)


def traits_acceptable(values, budget):
    """(ok, why) for trait values a civ-trait confirm submitted.

    Each value must be one the dialog gives, and the clicks behind them must
    spend the galaxy's allowance exactly, since OK refuses while any is left.
    Values overwrite rather than add, so the served values do not matter.
    """
    spent = 0
    for (name, fewest, table, cost), v in zip(TRAIT_RULES, values):
        if v not in table:
            return False, f'{name} {v:+d} is not a value the dialog gives'
        spent += (fewest + table.index(v)) * cost
    if spent != budget:
        return False, (f'the traits spend {spent} change(s), and the galaxy '
                       f'allows exactly {budget}')
    return True, ''


def gset_value(blob, name):
    """A galaxy setting's value, or None."""
    gset = next((s for s in sp.flatten(sp.parse_blob(blob))
                 if s.tag == b'GSET'), None)
    if gset is None:
        return None
    return sp.parse_gset(blob, gset).get(name, {}).get('value')


def homeworld_fields(plpr):
    """(rates, space) of a PLPR payload: the fields the homeworld dialog edits."""
    rates = tuple(struct.unpack_from('<b', plpr, off)[0]
                  for off in HOMEWORLD_RATE_OFFS)
    return rates, struct.unpack_from('<H', plpr, HOMEWORLD_SPACE_OFF)[0]


def homeworlds(planets, civ_oid):
    """The planets the client could take for a civ's homeworld.

    0x00508C60 takes the civ's planet founded earliest, from PLPR+0. A tie is
    broken by an order the blob does not show, so every planet tied for the
    earliest is a candidate.
    """
    founded = {oid: struct.unpack_from('<I', plpr, FOUNDED_OFF)[0]
               for oid, (owner, _p, plpr, _n) in planets.items()
               if owner == civ_oid and plpr is not None}
    if not founded:
        return set()
    first = min(founded.values())
    return {oid for oid, turn in founded.items() if turn == first}


def homeworld_acceptable(served_plpr, submitted_plpr, budget):
    """(ok, why) for a homeworld confirm, from the planet as served."""
    (rates_s, space_s) = homeworld_fields(served_plpr)
    (rates_t, space_t) = homeworld_fields(submitted_plpr)
    lo, hi = HOMEWORLD_RATES
    clicks = 0
    for kind, before, after in zip(('food', 'production', 'science'),
                                   rates_s, rates_t):
        if after != before and not lo <= after <= hi:
            return False, f'{kind} rate {after} is outside {lo}..{hi}'
        clicks += after - before
    added = space_t - max(space_s, HOMEWORLD_SPACE_FLOOR)
    if (added % HOMEWORLD_SPACE_STEP
            or not 0 <= added // HOMEWORLD_SPACE_STEP <= HOMEWORLD_SPACE_CLICKS):
        return False, f'space {space_s} -> {space_t} is not a click count'
    clicks += added // HOMEWORLD_SPACE_STEP
    if clicks != budget:
        return False, (f'the homeworld spends {clicks} change(s), and the '
                       f'galaxy allows exactly {budget}')
    return True, ''


def set_homeworld(blob, planet_oid, submitted_plpr):
    """Copy the homeworld dialog's fields from a submitted PLPR. In place."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    for sola in (c for c in glxy.children if c.tag == b'SOLA'):
        for sec in sola.find('PLNT'):
            if struct.unpack_from('<I', blob, sec.payload)[0] != planet_oid:
                continue
            plpr = next(sec.find('PLPR'), None)
            if plpr is None:
                raise SystemExit(f'planet {planet_oid} has no PLPR')
            out = bytearray(blob)
            for off in HOMEWORLD_RATE_OFFS + (HOMEWORLD_SPACE_OFF,
                                              HOMEWORLD_SPACE_OFF + 1):
                out[plpr.payload + off] = submitted_plpr[off]
            return bytes(out)
    raise SystemExit(f'planet {planet_oid} not found')


def setup_orders(served, sub, civ_oid, served_planets, sub_planets):
    """([(kind, detail)], [refusal]) for the setup prompts' confirms.

    Each prompt is one-time: its confirm is carried when the flag was clear as
    served and is set in the submission, and only as the dialog could have
    made it. kind is 'civ' with the trait values, or 'homeworld' with
    (planet, submitted PLPR). Anything else that moves those fields is refused.
    """
    taken, refusals = [], []
    flags_s, flags_t = setup_flags(served, civ_oid), setup_flags(sub, civ_oid)
    if flags_s is None or flags_t is None:
        return taken, refusals

    traits_s, traits_t = traits_of(served, civ_oid), traits_of(sub, civ_oid)
    confirmed = not flags_s[1] and flags_t[1]
    if traits_t is None and confirmed:
        refusals.append('civilization traits DROPPED, CVTR is not the '
                        "dialog's ten traits")
    elif traits_t is not None and (confirmed or traits_t != traits_s):
        if flags_s[1]:
            refusals.append('civilization traits DROPPED, they were already '
                            'customised')
        elif not confirmed:
            refusals.append('civilization traits DROPPED, changed without the '
                            "prompt's confirm")
        else:
            ok, why = traits_acceptable(traits_t, gset_value(
                served, 'civilization_changes'))
            if ok:
                taken.append(('civ', traits_t))
            else:
                refusals.append(f'civilization traits DROPPED, {why}')

    moved = {oid for oid, (owner, _p, plpr, _n) in served_planets.items()
             if owner == civ_oid and plpr is not None
             and oid in sub_planets and sub_planets[oid][2] is not None
             and homeworld_fields(plpr) != homeworld_fields(sub_planets[oid][2])}
    confirmed = not flags_s[0] and flags_t[0]
    if moved or confirmed:
        if flags_s[0]:
            refusals.append('homeworld customisation DROPPED, it was already '
                            'customised')
        elif not confirmed:
            refusals.append('homeworld customisation DROPPED, changed without '
                            "the prompt's confirm")
        elif len(moved) != 1:
            refusals.append(f'homeworld customisation DROPPED, it changes '
                            f'{len(moved)} planets, not one')
        else:
            (oid,) = moved
            if oid not in homeworlds(served_planets, civ_oid):
                refusals.append(f'homeworld customisation DROPPED, planet '
                                f'{oid} is not the homeworld')
            else:
                ok, why = homeworld_acceptable(
                    served_planets[oid][2], sub_planets[oid][2],
                    gset_value(served, 'homeworld_changes'))
                if ok:
                    taken.append(('homeworld', (oid, sub_planets[oid][2])))
                else:
                    refusals.append(f'homeworld customisation DROPPED, {why}')
    return taken, refusals


def _identity_at(blob, owpr):
    """Absolute offset of the civ name's length field in this OWPR, or None.

    The name is the only variable field after the research, so it is found
    from the end: the length that matches the distance to the tail.
    """
    tail = owpr.end - IDENTITY_TAIL
    for n in range(MAX_NAME + 1):
        at = tail - n - 4
        if at < owpr.payload:
            return None
        if struct.unpack_from('<I', blob, at)[0] == n:
            return at
    return None


def civ_identity(blob, civ_oid):
    """(name bytes, reveal byte, arms id) of a civ, or None when unreadable."""
    owpr = _owpr(blob, civ_oid)
    if owpr is None:
        return None
    at = _identity_at(blob, owpr)
    if at is None:
        return None
    tail = owpr.end - IDENTITY_TAIL
    return (bytes(blob[at + 4:tail]), blob[tail],
            struct.unpack_from('<i', blob, tail + 1)[0])


def set_civ_identity(blob, civ_oid, name, reveal, arms):
    """Write a civ's name, checkbox and arms id. OWPR grows with the name."""
    tree, owpr = _civ_section(blob, civ_oid, 'OWPR')
    at = None if owpr is None else _identity_at(blob, owpr)
    if at is None:
        raise SystemExit(f'civ {civ_oid} has no readable OWPR')
    payload = (bytes(blob[owpr.payload:at]) + struct.pack('<I', len(name))
               + name + bytes([reveal]) + struct.pack('<i', arms))
    return sp.replace_payload(blob, tree, owpr, payload)


def _civ_name_key(name):
    return name.strip(CIV_NAME_TRIM).upper()


def identity_orders(served, sub, blob, civ_oid, names, arms):
    """((name, reveal, arms) or None, [refusal]) for dialog 225's choice.

    Carried once, while the name is empty as served, and only as the dialog
    could have made it. `blob` is the galaxy being built, so a name another
    civ took earlier in this merge counts. `arms` holds the arms ids this
    player may use.
    """
    was, now = civ_identity(served, civ_oid), civ_identity(sub, civ_oid)
    if was is None or now is None or was == now:
        return None, []
    name, reveal, coat = now
    if was[0]:
        return None, ['civilization name DROPPED, it was already chosen']
    if not name:
        return None, ['civilization name DROPPED, the checkbox or coat of '
                      'arms changed without a name']
    ok, why = name_acceptable(name)
    if not ok:
        return None, [f'civilization name DROPPED, {why}']
    if not CIV_NAME_MIN <= len(name.strip(CIV_NAME_TRIM)) <= CIV_NAME_MAX:
        return None, [f'civilization name DROPPED, it needs to be between '
                      f'{CIV_NAME_MIN} and {CIV_NAME_MAX} characters long']
    if reveal not in (0, 1):
        return None, [f'civilization name DROPPED, the reveal checkbox is '
                      f'{reveal}']
    if coat != NO_ARMS and coat not in arms:
        return None, [f"civilization name DROPPED, coat of arms {coat} is "
                      f"not one of this player's uploads"]
    key = _civ_name_key(name)
    for oid, other in names.items():
        if oid == civ_oid:
            continue
        theirs = civ_identity(blob, oid)
        if key in (_civ_name_key(other.encode('latin1')),
                   theirs and _civ_name_key(theirs[0])):
            return None, [f'civilization name DROPPED, a similar name is '
                          f'already used by {other}']
    return now, []


def _civ_section(blob, civ_oid, tag):
    """(tree, the civ's first section with this tag), None when it has none."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    for o in glxy.children:
        if o.tag != b'OWNR':
            continue
        n = struct.unpack_from('<I', blob, o.payload)[0]
        if struct.unpack_from('<I', blob, o.payload + 4 + n)[0] == civ_oid:
            return tree, next(o.find(tag), None)
    return tree, None


def _govs(blob, civ_oid):
    """(tree, the civ's GOVS section), the section None when it has none."""
    return _civ_section(blob, civ_oid, 'GOVS')


def _sections(raw, at, end):
    """[(tag, payload start, payload end)] of the sections filling raw[at:end]
    exactly, or None."""
    out = []
    while at < end:
        if end - at < 8:
            return None
        size = struct.unpack_from('<I', raw, at + 4)[0] & sp.SIZE_MASK
        if at + 8 + size > end:
            return None
        out.append((bytes(raw[at:at + 4]), at + 8, at + 8 + size))
        at += 8 + size
    return out


def governors_of(blob, civ_oid):
    """[(objectId, whole GVNR section)] of a civ in creation order, or None
    unless its GOVS holds exactly its count of GVNR sections."""
    _tree, govs = _govs(blob, civ_oid)
    if govs is None or govs.size < GOVS_COUNT:
        return None
    count = struct.unpack_from('<I', blob, govs.payload)[0]
    secs = _sections(blob, govs.payload + GOVS_COUNT, govs.end)
    if (secs is None or len(secs) != count
            or any(tag != b'GVNR' or end - at < GVNR_FIXED
                   for tag, at, end in secs)):
        return None
    return [(struct.unpack_from('<I', blob, at)[0], bytes(blob[at - 8:end]))
            for _tag, at, end in secs]


def governor_ids(blob):
    """Every governor's object id, whichever civ holds it."""
    ids = set()
    for o in icv.owner_records(blob):
        ids.update(oid for oid, _r in governors_of(blob, o['oid']) or [])
    return ids


def read_governor(record):
    """(name, why) for a whole GVNR section: the name as bytes and '', or None
    and what is wrong with it. The rules are judged only as sections that fit;
    the engine runs them."""
    body = _sections(record, 8 + GVNR_FIXED, len(record))
    if not body or len(body) != 1 or body[0][0] != b'GVDT':
        return None, 'its record is not one GVDT'
    _tag, at, end = body[0]
    if end - at < 4:
        return None, 'its GVDT is cut short'
    n = struct.unpack_from('<I', record, at)[0]
    rules_at = at + 4 + n + GVDT_AFTER_NAME
    if rules_at + 4 > end:
        return None, 'its name runs past its GVDT'
    name = bytes(record[at + 4:at + 4 + n])
    rules = struct.unpack_from('<I', record, rules_at)[0]
    secs = _sections(record, rules_at + 4, end)
    if secs is None or len(secs) != 2 * rules:
        return None, f'its GVDT does not hold the {rules} rule(s) it counts'
    for (flag, f_at, f_end), (rule, _a, _e) in zip(secs[::2], secs[1::2]):
        if flag != b'RULE' or f_end - f_at != RULE_FLAG or not rule.startswith(b'RL'):
            return None, 'a rule is not a RULE followed by its own section'
    if not rules:
        return None, 'it has no rules, which the client does not allow'
    if not name:
        return None, 'the name is empty'
    ok, why = name_acceptable(name)
    return (name, '') if ok else (None, why)


def with_governor_id(record, oid):
    """A GVNR section carrying a different object id."""
    out = bytearray(record)
    struct.pack_into('<I', out, 8, int(oid))
    return bytes(out)


def with_governed(record, count):
    """A GVNR section saying it governs `count` planets."""
    out = bytearray(record)
    struct.pack_into('<I', out, 8 + GVNR_GOVERNS_AT, int(count))
    return bytes(out)


def set_governors(blob, civ_oid, records):
    """Rewrite a civ's GOVS to hold these whole GVNR sections, in order."""
    tree, govs = _govs(blob, civ_oid)
    if govs is None:
        raise SystemExit(f'civ {civ_oid} has no GOVS')
    payload = struct.pack('<I', len(records)) + b''.join(records)
    return sp.replace_payload(blob, tree, govs, payload)


def governor_links(blob):
    """{planetObjectId: governorObjectId}, 0 for a planet with none."""
    return {struct.unpack_from('<I', blob, s.payload)[0]:
            struct.unpack_from('<I', blob, s.end - GOVERNOR_LINK)[0]
            for s in sp.flatten(sp.parse_blob(blob)) if s.tag == b'PLNT'}


def set_governor_link(blob, planet_oid, gov_oid):
    """Name a planet's governor. Sizes do not change."""
    for s in sp.flatten(sp.parse_blob(blob)):
        if (s.tag == b'PLNT'
                and struct.unpack_from('<I', blob, s.payload)[0] == planet_oid):
            out = bytearray(blob)
            struct.pack_into('<I', out, s.end - GOVERNOR_LINK, int(gov_oid))
            return bytes(out)
    raise SystemExit(f'planet {planet_oid} not found')


def governor_orders(served, sub, civ_oid, served_planets, names):
    """(new, edited, deleted, links, refusals) for a civ's governors, judged
    against the state served.

    new and edited are [(objectId, GVNR)], new ones under the id submitted;
    deleted is a list of ids; links is [(planet, governor)]. The number of
    planets each governor governs is the referee's to count, so it is ignored
    here. Deleting a governor that would still govern a planet, and
    unassigning one, are not something the client was seen to do, and are
    refused.
    """
    refusals = []
    for o in icv.owner_records(served):
        if (o['oid'] != civ_oid
                and governors_of(sub, o['oid']) != governors_of(served, o['oid'])):
            refusals.append(f"governors DROPPED, they belong to {o['name']}")
    mine_s, mine_t = governors_of(served, civ_oid), governors_of(sub, civ_oid)
    if mine_s is None or mine_t is None:
        if mine_t != mine_s:
            refusals.append('governors DROPPED, GOVS does not hold its count '
                            'of governors')
        return [], [], [], [], refusals
    held, kept = dict(mine_s), dict(mine_t)
    high = struct.unpack_from('<I', served, idg.HIGH_WATER_ID)[0]

    new, edited = [], []
    for oid, record in mine_t:
        if oid in held and with_governed(record, 0) == with_governed(held[oid], 0):
            continue
        if oid not in held and oid <= high:
            refusals.append(f'governor {oid} DROPPED, it is new and {oid} is '
                            f'not a new object id')
            continue
        name, why = read_governor(record)
        if name is None:
            refusals.append(f'governor {oid} DROPPED, {why}')
            continue
        (edited if oid in held else new).append((oid, record))

    usable = set(held) | {oid for oid, _r in new}
    links_s, links_t = governor_links(served), governor_links(sub)
    links = []
    for planet, gov in sorted(links_t.items()):
        was = links_s.get(planet)
        if was is None or gov == was:
            continue
        owner = served_planets[planet][0] if planet in served_planets else None
        if owner != civ_oid:
            refusals.append(f'planet {planet}: governor DROPPED, owned by '
                            f'{names.get(owner, owner)}')
        elif gov == 0:
            refusals.append(f'planet {planet}: governor DROPPED, unassigning '
                            f'one is not something the client offers')
        elif gov not in usable:
            refusals.append(f'planet {planet}: governor DROPPED, {gov} is not '
                            f'a governor this civ holds')
        else:
            links.append((planet, gov))

    final = dict(links_s)
    final.update(links)
    deleted = []
    for oid, record in mine_s:
        if oid in kept:
            continue
        governs = sorted(p for p, g in final.items() if g == oid)
        if governs:
            refusals.append(f'governor {oid} deletion DROPPED, it still '
                            f'governs planet(s) {governs}')
        else:
            deleted.append(oid)
    return new, edited, deleted, links, refusals


# A civ's admirals are in its `OWNR > DATA > ADMS`, after `GOVS`: a u32 count,
# then one `ADML` per admiral, in creation order. The writer is 0x53B4A0,
# 0x5A51B0 and 0x5A4F20, the reader 0x53D73C and 0x5A6FF0:
#
#     ADML v0   u32 objectId ; u32 ships assigned ; one byte ; ADDT
#     ADDT v0   u32 nameLen ; name ; u32 type ; u32 rule count ;
#               per rule a RULE (one byte, its checkbox) then the rule's own
#               section
#
# A ship or fleet names its admiral in its DYNO, the u32 after the has-orders
# byte, 0 for none.
ADMS_COUNT = 4
ADML_SHIPS_AT = 4           # in the ADML payload, after the object id
ADML_FIXED = 9              # object id, ships assigned and the one byte
ADMIRAL_TYPES = 4           # the manual's four; assault reads 2, scout 3
DYNO_ORBIT = 4              # the orbit planet's id, before SHCO
DYNO_AFTER_SHCO = 5         # a u32 and the has-orders byte, then the admiral
# The tags the reader's rule factory (0x5A4590) knows. Any other is stored
# through a null pointer, which is likely to crash the load.
ADMIRAL_RULES = frozenset((
    b'ATPL', b'ATSH', b'DFPL', b'FSHP', b'ICSH', b'LSSL', b'RECQ', b'RTCN',
    b'STLS', b'RLYP', b'RZPT', b'SPTS', b'TSCQ', b'TMMR', b'TMSC', b'STPF',
    b'TRSM', b'WUFL', b'WLSL', b'ASFL', b'ASAD', b'MASH', b'SCSP', b'SCRG'))


def admirals_of(blob, civ_oid):
    """[(objectId, whole ADML section)] of a civ in order, or None unless its
    ADMS holds exactly its count of ADML sections."""
    _tree, adms = _civ_section(blob, civ_oid, 'ADMS')
    if adms is None or adms.size < ADMS_COUNT:
        return None
    count = struct.unpack_from('<I', blob, adms.payload)[0]
    secs = _sections(blob, adms.payload + ADMS_COUNT, adms.end)
    if (secs is None or len(secs) != count
            or any(tag != b'ADML' or end - at < ADML_FIXED
                   for tag, at, end in secs)):
        return None
    return [(struct.unpack_from('<I', blob, at)[0], bytes(blob[at - 8:end]))
            for _tag, at, end in secs]


def admiral_name(record):
    """The name in a whole ADML section, or None when its ADDT cannot be read."""
    body = _sections(record, 8 + ADML_FIXED, len(record))
    if not body or body[0][0] != b'ADDT' or body[0][2] - body[0][1] < 4:
        return None
    _tag, at, end = body[0]
    n = struct.unpack_from('<I', record, at)[0]
    return bytes(record[at + 4:at + 4 + n]) if at + 4 + n <= end else None


def admiral_label(oid, record):
    name = admiral_name(record)
    return f'admiral {oid}' + (f" ({name.decode('latin1')!r})"
                               if name is not None else '')


def read_admiral(record):
    """(name, why) for a whole ADML section: the name as bytes and '', or None
    and what is wrong with it. The rules are judged only as sections the
    client knows that fit; the engine runs them."""
    body = _sections(record, 8 + ADML_FIXED, len(record))
    if not body or len(body) != 1 or body[0][0] != b'ADDT':
        return None, 'its record is not one ADDT'
    _tag, at, end = body[0]
    if end - at < 4:
        return None, 'its ADDT is cut short'
    n = struct.unpack_from('<I', record, at)[0]
    kind_at = at + 4 + n
    if kind_at + 8 > end:
        return None, 'its name runs past its ADDT'
    name = bytes(record[at + 4:kind_at])
    kind, rules = struct.unpack_from('<II', record, kind_at)
    if kind >= ADMIRAL_TYPES:
        return None, f'its type {kind} is not one the client offers'
    secs = _sections(record, kind_at + 8, end)
    if secs is None or len(secs) != 2 * rules:
        return None, f'its ADDT does not hold the {rules} rule(s) it counts'
    for (flag, f_at, f_end), (rule, _a, _e) in zip(secs[::2], secs[1::2]):
        if flag != b'RULE' or f_end - f_at != RULE_FLAG:
            return None, 'a rule is not a RULE followed by its own section'
        if rule not in ADMIRAL_RULES:
            return None, f'rule {rule!r} is not one the client knows'
    if not name:
        return None, 'the name is empty'
    ok, why = name_acceptable(name)
    return (name, '') if ok else (None, why)


def with_admiral_id(record, oid):
    """An ADML section carrying a different object id."""
    out = bytearray(record)
    struct.pack_into('<I', out, 8, int(oid))
    return bytes(out)


def _with_ships(record, count):
    out = bytearray(record)
    struct.pack_into('<I', out, 8 + ADML_SHIPS_AT, int(count))
    return bytes(out)


def set_admirals(blob, civ_oid, records):
    """Rewrite a civ's ADMS to hold these whole ADML sections, in order."""
    tree, adms = _civ_section(blob, civ_oid, 'ADMS')
    if adms is None:
        raise SystemExit(f'civ {civ_oid} has no ADMS')
    payload = struct.pack('<I', len(records)) + b''.join(records)
    return sp.replace_payload(blob, tree, adms, payload)


def admiral_orders(served, sub, civ_oid):
    """(new, edited, deleted, refusals) for a civ's admirals, judged against
    the state served.

    new and edited are [(objectId, ADML)], new ones under the id submitted;
    deleted is a list of ids, carried only once no ship names them. The
    number of ships each admiral commands is the referee's to count, so it is
    ignored here.
    """
    refusals = []
    for o in icv.owner_records(served):
        if (o['oid'] != civ_oid
                and admirals_of(sub, o['oid']) != admirals_of(served, o['oid'])):
            refusals.append(f"admirals DROPPED, they belong to {o['name']}")
    mine_s, mine_t = admirals_of(served, civ_oid), admirals_of(sub, civ_oid)
    if mine_s is None or mine_t is None:
        if mine_t != mine_s:
            refusals.append('admirals DROPPED, ADMS does not hold its count of '
                            'admirals')
        return [], [], [], refusals
    held, kept = dict(mine_s), dict(mine_t)
    high = struct.unpack_from('<I', served, idg.HIGH_WATER_ID)[0]

    new, edited = [], []
    for oid, record in mine_t:
        if oid in held and _with_ships(record, 0) == _with_ships(held[oid], 0):
            continue
        if oid not in held and oid <= high:
            refusals.append(f'{admiral_label(oid, record)} DROPPED, it is new '
                            f'and {oid} is not a new object id')
            continue
        name, why = read_admiral(record)
        if name is None:
            refusals.append(f'{admiral_label(oid, record)} DROPPED, {why}')
            continue
        (edited if oid in held else new).append((oid, record))
    deleted = [oid for oid, _r in mine_s if oid not in kept]
    return new, edited, deleted, refusals


def dyno_admiral_at(dyno):
    """Where a whole DYNO section holds its admiral id, or None."""
    at = 8 + DYNO_ORBIT
    if len(dyno) < at + 8 or dyno[at:at + 4] != b'SHCO':
        return None
    at += 8 + (struct.unpack_from('<I', dyno, at + 4)[0] & sp.SIZE_MASK)
    at += DYNO_AFTER_SHCO
    return at if at + 4 <= len(dyno) else None


def dyno_admiral(dyno):
    """The admiral id a whole DYNO section names, 0 for none, or None."""
    at = dyno_admiral_at(dyno) if dyno is not None else None
    return struct.unpack_from('<I', dyno, at)[0] if at is not None else None


def with_dyno_admiral(dyno, oid):
    """A whole DYNO section naming a different admiral."""
    out = bytearray(dyno)
    struct.pack_into('<I', out, dyno_admiral_at(dyno), int(oid))
    return bytes(out)


def admiral_kept(dyno, allowed, why):
    """(dyno, refusal): a submitted DYNO naming one of the `allowed` admiral
    ids, the first of them the one served, or the same DYNO naming that one
    and the refusal of the change."""
    now = dyno_admiral(dyno)
    if now is None or now in allowed:
        return dyno, ''
    what = (f'unassigning admiral {allowed[0]}' if now == 0
            else f'admiral {now} assignment')
    return with_dyno_admiral(dyno, allowed[0]), f'{what} DROPPED, {why}'


def ship_admiral(dyno, served_admiral, usable, remap):
    """(dyno, refusal) for a ship's submitted DYNO. A new admiral it names
    follows its renumbering; one the civ does not hold is put back to the
    admiral served, and named."""
    now = dyno_admiral(dyno)
    if now is None or now == served_admiral:
        return dyno, ''
    if now in remap:
        now = remap[now]
        dyno = with_dyno_admiral(dyno, now)
    if now == 0 or now in usable:
        return dyno, ''
    return (with_dyno_admiral(dyno, served_admiral),
            f'admiral {now} assignment DROPPED, {now} is not an admiral this '
            f'civ holds')


def admiral_commands(blob, civ_oid):
    """{admiralObjectId: how many of the civ's ships and fleets name it}."""
    named = [d for o, d in ship_index(blob).values() if o == civ_oid]
    named += [f['dyno'] for f in fleet_index(blob).values()
              if f['owner'] == civ_oid and f['dyno'] is not None]
    return collections.Counter(dyno_admiral(d) for d in named)


# Treaties, proposals among them, follow the last section in SAVE: a u32
# count, then one TRTY per treaty. The client writes every treaty it holds
# (0x55A4D0, reached from SaveGame with no player) and reads them back into its
# registry (0x55BD00):
#
#     TRTY v2   u32 objectId ; TRDT
#     TRDT v1   u32 proposer ; u32 recipient ; u32 kind ; u32 status ;
#               i32 length (-2 unset) ; i32 start turn (-1 unset) ;
#               u32 turn proposed ; a deleted byte per party ;
#               u32 count, the proposer's demands ; u32 count, its offers
#
# Each item is its own section. A lump sum in the demands is paid by the
# recipient, one in the offers by the proposer, both when the treaty is
# accepted; per-turn items are the engine's. A civ's stance toward each civ it
# has met is in its `KNPL`: a u32 count, then a 36-byte record per civ, keyed
# by that civ's object id, the stance at +12.
TREATY_COUNT = 4
TRTY_PARTIES_AT = 4 + 8     # in the TRTY payload, past the id and TRDT's header
TRDT_FIXED = 30             # parties, kind, status, length, start, proposed, bytes
KNPL_COUNT = 4
KNPL_RECORD = 36
KNPL_STANCE_AT = 12
# u16, loyalty this civ's citizens of the record's civ lose to it: bio-bombing
# that civ adds the bombs dropped, capped at 100 (0x4F6DE0 through 0x534D50),
# and the loyalty of a citizen of that origin under this civ drops by it (0x4ED270).
KNPL_BIO_PENALTY_AT = 26
# The viewer's display choices for the civ a KNPL record names, read off the
# client's writer (0x532F20) and reader (0x5332E0).
KNPL_SHOW_TERRITORY_AT = 29     # u8, 1 draws the civ's territory
KNPL_SHOW_SHIPS_AT = 30         # u8, likely 1 draws the civ's ships
KNPL_BY_STANCE_AT = 31          # u8, 1 colours the civ by stance, 0 by its colour
KNPL_COLOUR_AT = 32             # u32, B, G, R, A; the reader forces A to 0xFF
KNPL_FLAGS = (KNPL_SHOW_TERRITORY_AT, KNPL_SHOW_SHIPS_AT, KNPL_BY_STANCE_AT)
STANCES = {0: 'Neutral', 1: 'War', 2: 'Cease-Fire', 3: 'Peace', 4: 'Alliance'}
KINDS = {0: 'trade', 1: 'war', 2: 'cease-fire', 3: 'peace', 4: 'alliance'}
WAR = 1
PROPOSED, ACCEPTED, DECLINED, NON_BINDING = 0, 1, 2, 3
UNSET_LENGTH, UNSET_START = -2, -1
STANCE_OF = {2: 2, 3: 3, 4: 4}          # the stance a kind sets when accepted
# What a stance treaty binds for when it carries no TRLE, as the UI says.
# Only the cease-fire's 20 is measured in the save.
BINDS_FOR = {2: 20, 3: 50, 4: 100}
BINDING = (2, 3)            # a cease-fire or a peace in force forbids war
TREATY_ITEMS = frozenset((b'CRLS', b'CRPT', b'RSLS', b'RSPT', b'SLOS', b'SHBR',
                          b'SHGM', b'SHWH', b'TRLE'))
CREDIT_SUM = b'CRLS'        # u32 amount; not measured, read like CRPT
RESOURCE_SUM = b'RSLS'      # u32 amount ; u32 resource, 0 metal
TREATY_LENGTH = b'TRLE'     # u32 turns
# Copied once on acceptance (0x55D090): one in the demands gives the
# recipient's EXSY to the proposer, one in the offers the proposer's to the
# recipient, demands first.
MAP_SHARE = b'SHGM'
# A KNPL record as first contact makes it (0x532E60), less the civ and turn.
FIRST_CONTACT = {'stance': 0, 'declared': 0, 'w17': 0, 'battled': 0,
                 'inactivity': -1, 'cooldown': 0, 'biobombed': 0,
                 'leader_known': 0, 'territory': 0, 'ships': 1,
                 'by_stance': 1, 'colour': 0xFF000000}
RESOURCE_KINDS = 5
# A civ's resources in OWPR: a u32 count this far past the start of the second
# research field, then a u8 resource and a u32 amount per resource.
RESOURCES_AFTER_RESEARCH = 55
RESOURCE_RECORD = 5


def treaties(blob):
    """{objectId: whole TRTY section} after SAVE's last section, in order, or
    None unless a u32 count of TRTY sections fills that tail exactly."""
    save = sp.parse_blob(blob)[0]
    at = save.children[-1].end if save.children else save.payload
    if save.end - at < TREATY_COUNT:
        return None
    count = struct.unpack_from('<I', blob, at)[0]
    secs = _sections(blob, at + TREATY_COUNT, save.end)
    if (secs is None or len(secs) != count
            or any(tag != b'TRTY' or end - p < TRTY_PARTIES_AT + 12
                   for tag, p, end in secs)):
        return None
    return {struct.unpack_from('<I', blob, p)[0]: bytes(blob[p - 8:end])
            for _tag, p, end in secs}


def set_treaties(blob, records):
    """Rewrite the treaty list after SAVE's last section to these whole TRTY
    sections, in order."""
    tree = sp.parse_blob(blob)
    save = tree[0]
    head = bytes(blob[save.payload:save.children[-1].end])
    return sp.replace_payload(blob, tree, save, head + struct.pack(
        '<I', len(records)) + b''.join(records))


def treaty_parties(record):
    """(first party, second party, kind) of a whole TRTY section."""
    return struct.unpack_from('<III', record, 8 + TRTY_PARTIES_AT)


def read_trdt(record):
    """A whole TRTY section's fields as a dict, its two item lists as
    [(tag, payload)], or None unless it is one TRDT the lists fill exactly."""
    body = _sections(record, 8 + 4, len(record))
    if not body or len(body) != 1 or body[0][0] != b'TRDT':
        return None
    _tag, at, end = body[0]
    if end - at < TRDT_FIXED + 4:
        return None
    (proposer, recipient, kind, status, length, start,
     proposed) = struct.unpack_from('<IIIIiiI', record, at)
    t = {'proposer': proposer, 'recipient': recipient, 'kind': kind,
         'status': status, 'length': length, 'start': start,
         'proposed': proposed, 'deleted': tuple(record[at + 28:at + 30])}
    at += TRDT_FIXED
    for side in ('demands', 'offers'):
        if at + 4 > end:
            return None
        n = struct.unpack_from('<I', record, at)[0]
        items, cut = [], at + 4
        for _i in range(n):
            if end - cut < 8:
                return None
            size = struct.unpack_from('<I', record, cut + 4)[0] & sp.SIZE_MASK
            if cut + 8 + size > end:
                return None
            items.append((bytes(record[cut:cut + 4]),
                          bytes(record[cut + 8:cut + 8 + size])))
            cut += 8 + size
        t[side], at = items, cut
    return t if at == end else None


def with_trdt(record, status=None, length=None, start=None, deleted=None):
    """A whole TRTY section with some of its TRDT fields rewritten."""
    out = bytearray(record)
    at = 8 + TRTY_PARTIES_AT
    if status is not None:
        struct.pack_into('<I', out, at + 12, int(status))
    if length is not None:
        struct.pack_into('<i', out, at + 16, int(length))
    if start is not None:
        struct.pack_into('<i', out, at + 20, int(start))
    if deleted is not None:
        out[at + 28:at + 30] = bytes(deleted)
    return bytes(out)


def with_treaty_id(record, oid):
    """A TRTY section carrying a different object id."""
    out = bytearray(record)
    struct.pack_into('<I', out, 8, int(oid))
    return bytes(out)


def item_refusal(t):
    """Why a treaty's items are not ones the client writes, or ''."""
    for tag, payload in t['demands'] + t['offers']:
        if tag not in TREATY_ITEMS:
            return f'item {tag!r} is not one the client offers'
        if tag == RESOURCE_SUM and (
                len(payload) != 8
                or struct.unpack_from('<I', payload, 4)[0] >= RESOURCE_KINDS):
            return 'a resource lump sum is not an amount and a resource'
        if tag in (CREDIT_SUM, TREATY_LENGTH) and len(payload) != 4:
            return f'item {tag!r} is not one u32'
    return ''


def lump_sums(items):
    """(credits, {resource: amount}) the lump sums in one item list add up to."""
    credits, resources = 0, collections.Counter()
    for tag, payload in items:
        if tag == CREDIT_SUM:
            credits += struct.unpack_from('<I', payload)[0]
        elif tag == RESOURCE_SUM:
            amount, kind = struct.unpack_from('<II', payload)
            resources[kind] += amount
    return credits, resources


def map_shares(t):
    """[(giver, receiver)] of the galaxy maps accepting `t` shares, in the
    order the client applies them."""
    out = []
    if any(tag == MAP_SHARE for tag, _p in t['demands']):
        out.append((t['recipient'], t['proposer']))
    if any(tag == MAP_SHARE for tag, _p in t['offers']):
        out.append((t['proposer'], t['recipient']))
    return out


def share_map(blob, giver, receiver, turn):
    """(blob, civs met) with the giver's EXSY merged into the receiver's, as
    `0x529290` does: a system the receiver lacks is added, and one it holds
    is replaced when the giver looked later. The receiver then meets every
    civ its table names as an owner (`0x534F90`). None when either table or
    the receiver's KNPL does not walk."""
    _tree, g = _civ_section(blob, giver, 'EXSY')
    _tree, r = _civ_section(blob, receiver, 'EXSY')
    if g is None or r is None:
        return None
    try:
        given = exsy.parse(blob[g.payload:g.end])
        rows = {s['system']: s for s in exsy.parse(blob[r.payload:r.end])}
    except (ValueError, struct.error):
        return None
    for s in given:
        held = rows.get(s['system'])
        if held is None or s['turn'] > held['turn']:
            rows[s['system']] = s
    tree, r = _civ_section(blob, receiver, 'EXSY')
    blob = sp.replace_payload(blob, tree, r, exsy.build(
        [rows[k] for k in sorted(rows)]))

    tree, kn = _civ_section(blob, receiver, 'KNPL')
    if kn is None:
        return None
    try:
        records = knpl.parse(blob[kn.payload:kn.end])
    except (ValueError, struct.error):
        return None
    civs = {o['oid'] for o in icv.owner_records(blob)}
    named = {p['owner'] for s in rows.values() for p in s['planets']}
    met = sorted((named & civs) - {receiver} - {k['civ'] for k in records})
    stance = WAR if gset_value(blob, 'waronly') == 1 else 0
    for civ in met:
        records.append(dict(FIRST_CONTACT, civ=civ, handle=civ, met=turn,
                            stance=stance))
    if met:
        records.sort(key=lambda k: k['civ'])
        blob = sp.replace_payload(blob, tree, kn, knpl.build(records))
    return blob, met


def treaty_length(t):
    """The length accepting a treaty gives it: its TRLE, else what a stance
    treaty binds for, else None."""
    for tag, payload in t['demands'] + t['offers']:
        if tag == TREATY_LENGTH:
            return struct.unpack_from('<I', payload)[0]
    return BINDS_FOR.get(t['kind'])


def stances(blob):
    """{(civ, other civ): stance code} for every civ whose KNPL reads as a
    count of whole records."""
    out = {}
    for o in icv.owner_records(blob):
        _tree, knpl = _civ_section(blob, o['oid'], 'KNPL')
        if knpl is None or knpl.size < KNPL_COUNT:
            continue
        count = struct.unpack_from('<I', blob, knpl.payload)[0]
        if knpl.size != KNPL_COUNT + count * KNPL_RECORD:
            continue
        for i in range(count):
            at = knpl.payload + KNPL_COUNT + i * KNPL_RECORD
            other, = struct.unpack_from('<I', blob, at)
            out[(o['oid'], other)] = struct.unpack_from(
                '<I', blob, at + KNPL_STANCE_AT)[0]
    return out


def bio_penalties(blob):
    """{(civ, other civ): loyalty penalty} for every non-zero bio-bomb penalty
    in a KNPL that reads as a count of whole records."""
    out = {}
    for o in icv.owner_records(blob):
        for other, (at, _rec) in (knpl_records(blob, o['oid']) or {}).items():
            n = struct.unpack_from('<h', blob, at + KNPL_BIO_PENALTY_AT)[0]
            if n:
                out[(o['oid'], other)] = n
    return out


def set_stance(blob, civ_oid, other, code):
    """Write a civ's stance toward another it has met. Sizes do not change."""
    _tree, knpl = _civ_section(blob, civ_oid, 'KNPL')
    count = struct.unpack_from('<I', blob, knpl.payload)[0]
    for i in range(count):
        at = knpl.payload + KNPL_COUNT + i * KNPL_RECORD
        if struct.unpack_from('<I', blob, at)[0] == other:
            out = bytearray(blob)
            struct.pack_into('<I', out, at + KNPL_STANCE_AT, int(code))
            return bytes(out)
    raise SystemExit(f'civ {civ_oid} has not met {other}')


def knpl_records(blob, civ_oid):
    """{other civ: (record offset, 36 record bytes)} from a civ's KNPL, or None
    when it does not read as a count of whole records."""
    _tree, knpl = _civ_section(blob, civ_oid, 'KNPL')
    if knpl is None or knpl.size < KNPL_COUNT:
        return None
    count = struct.unpack_from('<I', blob, knpl.payload)[0]
    if knpl.size != KNPL_COUNT + count * KNPL_RECORD:
        return None
    out = {}
    for i in range(count):
        at = knpl.payload + KNPL_COUNT + i * KNPL_RECORD
        out[struct.unpack_from('<I', blob, at)[0]] = (
            at, blob[at:at + KNPL_RECORD])
    return out


def display_of(record):
    """A KNPL record's display choices: the three flags and the colour."""
    return record[KNPL_SHOW_TERRITORY_AT:KNPL_RECORD]


def describe_display(raw):
    """"territory shown, ships hidden, colour 0x00FF00" for a log line."""
    terr, ships, by_stance = raw[0], raw[1], raw[2]
    b, g, r = raw[3], raw[4], raw[5]
    colour = 'by stance' if by_stance else f'colour 0x{r:02X}{g:02X}{b:02X}'
    return (f"territory {'shown' if terr else 'hidden'}, ships "
            f"{'shown' if ships else 'hidden'}, {colour}")


def display_orders(served, sub, civ_oid, names):
    """([(other civ, display bytes)], refusals) for the submitter's display
    choices in its own KNPL, judged against the state served.

    Only records the civ was served are read, and each flag must be 0 or 1.
    """
    was, now = knpl_records(served, civ_oid), knpl_records(sub, civ_oid)
    if was is None or now is None:
        if was is not None:
            return [], ['display choices DROPPED, the KNPL does not hold its '
                        'count of records']
        return [], []
    changes, refusals = [], []
    for other in sorted(set(was) & set(now)):
        old, new = display_of(was[other][1]), display_of(now[other][1])
        if old == new:
            continue
        bad = [new[k - KNPL_SHOW_TERRITORY_AT] for k in KNPL_FLAGS
               if new[k - KNPL_SHOW_TERRITORY_AT] not in (0, 1)]
        if bad:
            refusals.append(f'display of {names.get(other, other)} DROPPED, '
                            f'a flag reads {bad[0]}')
            continue
        changes.append((other, new))
    return changes, refusals


def set_display(blob, civ_oid, other, raw):
    """Write a civ's display choices for another it has met. Sizes do not
    change."""
    at, _record = knpl_records(blob, civ_oid)[other]
    out = bytearray(blob)
    out[at + KNPL_SHOW_TERRITORY_AT:at + KNPL_RECORD] = raw
    return bytes(out)


# A civ's `USSE` is the block the client's `SVUS` command sent (writer 0x57ED00,
# reader 0x57EE30). The vote byte is read from version 2 on.
USSE_SIZE = 45
USSE_VOTE_VERSION = 2
END_VOTE_AT = 44     # Owner+0x2F8, the "Vote to End the Galaxy" tick-box


def _usse(blob, civ_oid):
    """The civ's USSE section, or None unless it is one that holds the vote."""
    _tree, usse = _civ_section(blob, civ_oid, 'USSE')
    if (usse is None or usse.size != USSE_SIZE
            or usse.version < USSE_VOTE_VERSION):
        return None
    return usse


def end_vote_of(blob, civ_oid):
    """The civ's end-galaxy vote byte, or None when its USSE cannot be read."""
    usse = _usse(blob, civ_oid)
    return None if usse is None else blob[usse.payload + END_VOTE_AT]


def set_end_vote(blob, civ_oid, value):
    """Write a civ's end-galaxy vote. Sizes do not change."""
    usse = _usse(blob, civ_oid)
    if usse is None:
        raise SystemExit(f'civ {civ_oid} has no readable USSE')
    out = bytearray(blob)
    out[usse.payload + END_VOTE_AT] = int(value)
    return bytes(out)


def end_votes(blob):
    """{civ name: True when it has voted to end the galaxy}, for every civ
    whose USSE can be read."""
    out = {}
    for o in icv.owner_records(blob):
        vote = end_vote_of(blob, o['oid'])
        if vote is not None:
            out[o['name']] = bool(vote)
    return out


def vote_orders(served, sub, civ_oid):
    """(the submitter's new vote or None, refusals), judged against the state
    served."""
    was, now = end_vote_of(served, civ_oid), end_vote_of(sub, civ_oid)
    if was is None or now is None or was == now:
        return None, []
    if now not in (0, 1):
        return None, [f'end-galaxy vote DROPPED, it reads {now}']
    return now, []


def _resources_at(blob, civ_oid):
    """Where a civ's resource count sits, or None unless the count is
    RESOURCE_KINDS records naming 0, 1, 2 ... in order."""
    owpr = _owpr(blob, civ_oid)
    if owpr is None:
        return None
    at = research_offsets(blob, owpr)[1] + RESOURCES_AFTER_RESEARCH
    if at + 4 + RESOURCE_KINDS * RESOURCE_RECORD > owpr.size:
        return None
    at += owpr.payload
    if (struct.unpack_from('<I', blob, at)[0] != RESOURCE_KINDS
            or any(blob[at + 4 + i * RESOURCE_RECORD] != i
                   for i in range(RESOURCE_KINDS))):
        return None
    return at


def resources_of(blob, civ_oid):
    """A civ's resources, metal first, or None when they cannot be found."""
    at = _resources_at(blob, civ_oid)
    if at is None:
        return None
    return [struct.unpack_from('<I', blob, at + 5 + i * RESOURCE_RECORD)[0]
            for i in range(RESOURCE_KINDS)]


def set_resources(blob, civ_oid, values):
    """Write a civ's resources. Sizes do not change."""
    at = _resources_at(blob, civ_oid)
    out = bytearray(blob)
    for i, v in enumerate(values):
        struct.pack_into('<I', out, at + 5 + i * RESOURCE_RECORD, int(v))
    return bytes(out)


def diplomacy_orders(served, sub, civ_oid, names):
    """(orders, refusals) for a civ's diplomacy, judged against the state
    served.

    Each order is (step, treaty id, submitted TRTY): 'propose', 'war',
    'withdraw', 'decline', 'delete' or 'accept'. A new treaty is under the id
    submitted. What a declaration of war or an acceptance does to other
    treaties and to both stances is the referee's to apply, so those changes
    in the submission are not judged on their own.
    """
    refusals, orders = [], []

    def who(civ):
        return names.get(civ, civ)

    def label(oid, t):
        if t is None:
            return f'treaty {oid}'
        return (f"treaty {oid} ({KINDS.get(t['kind'], t['kind'])}, "
                f"{who(t['proposer'])} to {who(t['recipient'])})")

    was, now = treaties(served), treaties(sub)
    if was is None or now is None:
        if was != now:
            refusals.append('diplomacy DROPPED, the treaty list does not hold '
                            'its count of treaties')
        return [], refusals
    turn = icv.turn_of(served)
    high = struct.unpack_from('<I', served, idg.HIGH_WATER_ID)[0]
    war_only = gset_value(served, 'waronly') == 1
    s_stances, t_stances = stances(served), stances(sub)
    met = {other for civ, other in s_stances if civ == civ_oid}
    removed = []
    for oid in sorted(set(was) | set(now)):
        a, b = was.get(oid), now.get(oid)
        if a == b:
            continue
        ts = read_trdt(a) if a is not None else None
        tt = read_trdt(b) if b is not None else None
        name = label(oid, tt or ts)
        if a is None:
            why = new_treaty_refusal(tt, oid, civ_oid, high, turn, met, who,
                                     war_only)
            if why:
                refusals.append(f'{name} DROPPED, {why}')
            else:
                orders.append(('war' if tt['kind'] == WAR else 'propose',
                               oid, b))
            continue
        if ts is None:
            refusals.append(f'{name} DROPPED, the treaty served cannot be read')
            continue
        side = ((0 if ts['proposer'] == civ_oid else 1)
                if civ_oid in (ts['proposer'], ts['recipient']) else None)
        if side is None:
            refusals.append(f'{name} DROPPED, it is not a treaty this civ is '
                            f'party to')
        elif b is None:
            if ts['status'] == PROPOSED and side == 0:
                orders.append(('withdraw', oid, None))
            else:
                removed.append((oid, ts, name))
        elif tt is None:
            refusals.append(f'{name} DROPPED, its record cannot be read')
        elif ts['status'] == PROPOSED and side == 1 and tt['status'] in (
                ACCEPTED, DECLINED):
            if tt['status'] == ACCEPTED and item_refusal(ts):
                refusals.append(f'{name} DROPPED, {item_refusal(ts)}')
                continue
            length = treaty_length(ts)
            if length is None:
                length = tt['length']
            want = (with_trdt(a, ACCEPTED, length, turn) if tt['status'] == ACCEPTED
                    else with_trdt(a, DECLINED))
            if b != want:
                refusals.append(f'{name} DROPPED, the answer changes more than '
                                f'its status, length and start turn as the '
                                f'client does')
            else:
                orders.append(('accept' if tt['status'] == ACCEPTED
                               else 'decline', oid, b))
        elif (ts['status'] == DECLINED and not ts['deleted'][side]
              and b == with_trdt(a, deleted=tuple(
                  1 if i == side else ts['deleted'][i] for i in range(2)))):
            orders.append(('delete', oid, b))
        else:
            refusals.append(f'{name} DROPPED, changing a treaty this way is '
                            f'not something the client offers')

    # Treaties that went with a declaration of war or an acceptance, and the
    # stances they set, are the referee's to change; anything else is named.
    ends = set()
    for step, oid, b in orders:
        t = read_trdt(b) if b is not None else None
        if step == 'war' or (step == 'accept' and t['kind'] in STANCE_OF):
            ends.add(frozenset((t['proposer'], t['recipient'])))
    for oid, ts, name in removed:
        pair = frozenset((ts['proposer'], ts['recipient']))
        if pair not in ends or ts['status'] not in (ACCEPTED, NON_BINDING):
            refusals.append(f'{name} DROPPED, ending a treaty this way is not '
                            f'something the client offers')
    named = set()
    for civ, other in sorted((k for k in set(s_stances) & set(t_stances)
                              if s_stances[k] != t_stances[k]),
                             key=lambda k: (k[0] != civ_oid, k)):
        pair = frozenset((civ, other))
        if pair in ends or pair in named:
            continue
        named.add(pair)
        refusals.append(
            f'stance of {who(civ)} toward {who(other)} DROPPED, '
            f'{STANCES.get(s_stances[(civ, other)], s_stances[(civ, other)])} '
            f'-> {STANCES.get(t_stances[(civ, other)], t_stances[(civ, other)])}'
            f', a stance moves only with a treaty or a declaration of war')
    return orders, refusals


def new_treaty_refusal(t, oid, civ_oid, high, turn, met, who, war_only=False):
    """Why a treaty a submission adds is not a proposal or a declaration of
    war the client makes, or ''. `war_only` is the galaxy's `waronly`."""
    if t is None:
        return 'its record cannot be read'
    if oid <= high:
        return f'it is new and {oid} is not a new object id'
    if t['proposer'] != civ_oid:
        return f"it is proposed by {who(t['proposer'])}"
    if t['recipient'] == civ_oid or t['recipient'] not in met:
        return f"this civ has not met {who(t['recipient'])}"
    if t['kind'] not in KINDS:
        return f"kind {t['kind']} is not one the client offers"
    if t['kind'] == WAR:
        if ((t['status'], t['length'], t['start'], t['proposed'], t['deleted'],
             t['demands'], t['offers'])
                != (NON_BINDING, -1, turn, turn, (0, 0), [], [])):
            return 'it is not laid out as a declaration of war this turn'
        return ''
    if ((t['status'], t['length'], t['start'], t['proposed'], t['deleted'])
            != (PROPOSED, UNSET_LENGTH, UNSET_START, turn, (0, 0))):
        return 'it is not laid out as a proposal made this turn'
    if war_only:
        return 'the galaxy is war-only, where the client proposes no treaty'
    return item_refusal(t)


def _between(record, pair):
    return frozenset(treaty_parties(record)[:2]) == pair


def _stance_in_force(record, pair):
    """A war, cease-fire, peace or alliance in force between the pair."""
    t = read_trdt(record)
    return (t is not None and _between(record, pair)
            and (t['kind'] == WAR or t['kind'] in STANCE_OF)
            and t['status'] in (ACCEPTED, NON_BINDING))


def take_treaty_step(blob, served_treaties, step, oid, record, civ_oid, turn,
                     who):
    """(blob, line, refusal, oid) for one diplomacy order applied to the
    galaxy being built. A step on a treaty that another submission already
    changed this turn is refused. A new treaty may take a different id."""
    merged = treaties(blob)
    if merged is None:
        return blob, '', 'diplomacy DROPPED, the merged treaty list is unreadable', oid
    if step not in ('propose', 'war') and merged.get(oid) != served_treaties[oid]:
        return (blob, '', f'treaty {oid} DROPPED, the other party changed it '
                f'this turn', oid)
    t = read_trdt(record if record is not None else served_treaties[oid])
    pair = frozenset((t['proposer'], t['recipient']))
    other = t['recipient'] if t['proposer'] == civ_oid else t['proposer']
    kind = KINDS.get(t['kind'], t['kind'])

    if step in ('propose', 'war'):
        if step == 'war':
            if stances(blob).get((civ_oid, other)) == WAR:
                return (blob, '', f'treaty {oid} DROPPED, {who(civ_oid)} is '
                        f'already at war with {who(other)}', oid)
            for r in merged.values():
                f = read_trdt(r)
                if (f and _between(r, pair) and f['kind'] in BINDING
                        and f['status'] == ACCEPTED
                        and f['start'] + f['length'] > turn):
                    return (blob, '', f"treaty {oid} DROPPED, the "
                            f"{KINDS[f['kind']]} with {who(other)} binds "
                            f"until turn {f['start'] + f['length']}", oid)
            merged = {k: r for k, r in merged.items()
                      if not (_between(r, pair)
                              and read_trdt(r)['status'] == ACCEPTED)}
        new = oid
        if new <= idg.max_object_id(blob):
            new = next_object_id(blob)
        blob = set_high_water(blob, new)
        merged[new] = with_treaty_id(record, new)
        blob = set_treaties(blob, list(merged.values()))
        if step == 'war':
            blob = set_stance(blob, civ_oid, other, WAR)
            blob = set_stance(blob, other, civ_oid, WAR)
            line = f'treaty {new}: war declared on {who(other)}'
        else:
            line = f'treaty {new}: {kind} proposed to {who(other)}'
        if new != oid:
            line += f', submitted as {oid} and renumbered'
        return blob, line, '', new

    if step == 'withdraw':
        del merged[oid]
        line = f'treaty {oid}: {kind} proposal to {who(other)} withdrawn'
    elif step == 'decline':
        merged[oid] = record
        line = f'treaty {oid}: {kind} from {who(other)} declined'
    elif step == 'delete':
        merged[oid] = record
        line = f'treaty {oid}: declined {kind} deleted from this side'
    else:
        # Accepting: the lump sums move between both civs at once.
        unpaid = blob
        pays =((t['recipient'], t['proposer'], t['demands']),
                (t['proposer'], t['recipient'], t['offers']))
        wallet = {c: [credits_of(blob, c), resources_of(blob, c)]
                  for c in pair}
        for payer, payee, items in pays:
            credits, resources = lump_sums(items)
            if not credits and not resources:
                continue
            if None in wallet[payer] or None in wallet[payee]:
                return (blob, '', f'treaty {oid} DROPPED, a lump sum cannot '
                        f'be moved: the credits or resources of one civ '
                        f'cannot be read', oid)
            short = [f'{credits} credits'] if credits > wallet[payer][0] else []
            short += [f'{n} of resource {k}' for k, n in sorted(
                resources.items()) if n > wallet[payer][1][k]]
            if short:
                return (blob, '', f"treaty {oid} DROPPED, {who(payer)} cannot "
                        f"pay {', '.join(short)}", oid)
            wallet[payer][0] -= credits
            wallet[payee][0] += credits
            for k, n in resources.items():
                wallet[payer][1][k] -= n
                wallet[payee][1][k] += n
        for c, (credits, resources) in wallet.items():
            if credits is not None and credits != credits_of(blob, c):
                blob = set_credits(blob, c, credits)
            if resources is not None and resources != resources_of(blob, c):
                blob = set_resources(blob, c, resources)
        shared = []
        for giver, receiver in map_shares(t):
            done = share_map(blob, giver, receiver, turn)
            if done is None:
                return (unpaid, '', f'treaty {oid} DROPPED, the galaxy map of '
                        f'{who(giver)} cannot be shared: a known-systems or '
                        f'known-civs table does not read', oid)
            blob, met = done
            shared.append(f"{who(giver)}'s galaxy map shared with "
                          f"{who(receiver)}" + (
                              f", who meets {', '.join(who(c) for c in met)}"
                              if met else ''))
        merged[oid] = record
        if t['kind'] in STANCE_OF:
            # The stance treaty it replaces ends, and a war with it.
            merged = {k: r for k, r in merged.items()
                      if k == oid or not _stance_in_force(r, pair)}
        line = '; '.join([f'treaty {oid}: {kind} from {who(other)} accepted']
                         + shared)
    blob = set_treaties(blob, list(merged.values()))
    if step == 'accept' and t['kind'] in STANCE_OF:
        blob = set_stance(blob, civ_oid, other, STANCE_OF[t['kind']])
        blob = set_stance(blob, other, civ_oid, STANCE_OF[t['kind']])
    return blob, line, '', oid


# The resource market is GLOB's RSMA, right after a four-byte TMGX: five i32
# counts in resource order, the units the market holds (written by 0x512250,
# read by 0x5122B0). A sale adds to its count and a purchase takes from it,
# and each tick takes 1,000 off every positive count (0x511C30). Every civ
# trades at the same counts.
MARKET = b'RSMA'
MARKET_BEFORE = b'TMGX'
MARKET_BEFORE_SIZE = 4
RESOURCE_NAMES = ('metal', 'deuterium', 'radioactives', 'crystal', 'exotics')
# The engine's table at 0x7FC9E8, one row per resource.
MARKET_BASE = (100.0, 150.0, 200.0, 300.0, 900.0)   # sale price per 100 units
MARKET_DIVISOR = 100.0          # held units that take one credit off the price
MARKET_BUY_FACTOR = 8.0         # a purchase costs this times the sale price
MARKET_LOT = 100                # units priced at one count before it moves
# The quotes' curve constants, floats the engine widens to double.
MARKET_FALLOFF = 4.999999987376214e-07
MARKET_SOFT = 0.800000011920929
MARKET_HARD = 0.20000000298023224
# Trait 42 adds its percentage to a sale and takes it off a purchase. The
# civ-trait dialog does not offer it; only Doctrine: Trade grants it.
MARKET_TRAIT_GRANTS = {49: 50}


def _market_at(blob):
    """Where RSMA's counts start, or None unless GLOB holds it after TMGX."""
    glob = next(sp.parse_blob(blob)[0].find('GLOB'), None)
    if glob is None:
        return None
    at = blob.find(MARKET, glob.payload + 8 + MARKET_BEFORE_SIZE, glob.end)
    while at >= 0:
        size = sp.read_header(blob, at)[2]
        before = sp.read_header(blob, at - 8 - MARKET_BEFORE_SIZE)
        if (size == RESOURCE_KINDS * 4 and at + 8 + size <= glob.end
                and before[0] == MARKET_BEFORE
                and before[2] == MARKET_BEFORE_SIZE):
            return at + 8
        at = blob.find(MARKET, at + 1, glob.end)
    return None


def market_of(blob):
    """The units the market holds, metal first, or None when unreadable."""
    at = _market_at(blob)
    if at is None:
        return None
    return list(struct.unpack_from(f'<{RESOURCE_KINDS}i', blob, at))


def set_market(blob, counts):
    """Write the market's counts. Sizes do not change."""
    out = bytearray(blob)
    struct.pack_into(f'<{RESOURCE_KINDS}i', out, _market_at(blob), *counts)
    return bytes(out)


def _f32(x):
    return struct.unpack('<f', struct.pack('<f', x))[0]


def _rounded(x):
    """The engine's rounding: half away from zero."""
    return int(x + 0.5) if x >= 0 else int(x - 0.5)


def market_price(kind, held, buying=False):
    """The engine's price for 100 units while the market holds `held`,
    before trait 42 (the lot loop of 0x511EF0 and 0x512090)."""
    base = _f32(MARKET_BASE[kind])
    if held > 0:
        t = _f32(float(held))
        held = _rounded(_f32(t * (MARKET_SOFT / (t * MARKET_FALLOFF * t + 1)
                                  + MARKET_HARD)))
    price = max(int(base - held / MARKET_DIVISOR), _rounded(_f32(base / 3)))
    return int(price * MARKET_BUY_FACTOR) if buying else price


def market_total(kind, units, held, trait, buying=False):
    """What `units` fetch or cost, as the engine quotes them: one lot at a
    time, each priced at the count the lots before it leave."""
    total = done = 0
    while units > done:
        lot = min(units - done, MARKET_LOT)
        total += market_price(kind, held - done if buying else held + done,
                              buying) * lot // 100
        done += lot
    return total + _rounded(_f32((-trait if buying else trait) * total / 100))


def market_trait(completed):
    """A civ's trait 42 from its completed research."""
    return sum(MARKET_TRAIT_GRANTS.get(t, 0) for t in completed)


# Messages between players. The send dialog (0x4BAB40) needs a subject, a body
# and a recipient, and refuses a body over MESSAGE_ROWS rows, each line counting
# its length // MESSAGE_ROW + 1. Offline the client appends a RECEIVED item to
# each recipient's list and a SENT item to the sender's, both naming their
# title and body in the string table.
MESSAGE_ROWS = 18
MESSAGE_ROW = 64
SUBJECT_MAX = 80            # the dialog sets none
FROM_TITLE = b'Message from %s - Subject: '
TO_TITLE = b'Message to '
SUBJECT_SEP = b' - Subject: '
NO_POSITION = b'\0' * 12
# The text controls read these as markup, so each is stored as a plain stand-in.
MARKUP = {ord('<'): b'(', ord('>'): b')', ord('|'): b'/', ord('%'): b'pct'}
BODY_CONTROLS = b'\r\n\t'
# A note to oneself (`SDNW` with a kind-8 item, `EDNW`), from the Note dialog
# (resource 0xBC): a subject and a text, neither empty, kept inline in the
# item with no string ids. The edit controls hold the Windows default.
NOTE_TITLE = b'Note - Subject: '
NOTE_MAX = 32767


def message_rows(body):
    """Rows the send dialog counts for a body."""
    return sum(len(line) // MESSAGE_ROW + 1 for line in body.split(b'\n'))


def plain_text(text, controls=b''):
    """`text` with markup stand-ins, or None when it holds a byte the client
    cannot show: a control other than `controls`, or 0x7F to 0x9F."""
    out = bytearray()
    for c in text:
        if c in MARKUP:
            out += MARKUP[c]
        elif (c < 0x20 and c not in controls) or 0x7F <= c <= 0x9F:
            return None
        else:
            out.append(c)
    return bytes(out)


def _same_item(a, b, ignore_flags=0):
    keys = ('type', 'turn', 'title', 'body', 'title_id', 'body_id', 'ids',
            'position')
    return (all(a[k] == b[k] for k in keys)
            and (a['flags'] & ~ignore_flags) == (b['flags'] & ~ignore_flags))


def _new_news_at(blob, civ_oid):
    """Offset of a civ's new-news flag, or None when its OWPR does not read."""
    owpr = _owpr(blob, civ_oid)
    at = _identity_at(blob, owpr) if owpr is not None else None
    if at is None:
        return None
    return at - news.NEW_NEWS_BACK


def note_refusal(item, turn):
    """Why a kind-8 item is not a note as the Note dialog writes one, or None.

    The dialog (`0x00538DD0`, and `0x0040EF10` to edit) titles it `Note -
    Subject: <subject>`, dates it this turn and names nobody and nothing; an
    edit keeps only the important mark of the flags.
    """
    if item['type'] != news.NOTE:
        return 'it is not a note'
    if (item['title_id'] or item['body_id'] or item['ids']
            or item['position'] != NO_POSITION or item['turn'] != turn
            or item['flags'] & ~news.IMPORTANT):
        return 'it is not a note as the client writes one'
    if not item['title'].startswith(NOTE_TITLE):
        return "its title is not the client's"
    subject = item['title'][len(NOTE_TITLE):]
    if not subject or not item['body']:
        return 'its subject or text is empty'
    if len(subject) > NOTE_MAX or len(item['body']) > NOTE_MAX:
        return f'its subject or text is over {NOTE_MAX} characters'
    if (any(c < 0x20 or c == 0x7F for c in subject)
            or any((c < 0x20 and c not in BODY_CONTROLS) or c == 0x7F
                   for c in item['body'])):
        return 'it holds a control character the dialog does not take'
    return None


def message_orders(served, sub, civ_oid, names):
    """(orders, refusals) for a civ's news, judged against the state served.

    `orders` holds `flags`, [(index, flags)] for its own items marked or
    unmarked important, `read` when it opened its news, and `messages`, each
    {'to', 'subject', 'body', 'sent_title', 'from_title', 'sent_flags', 'ids'}:
    a message as sent, its text not yet made plain. `from_title` is the
    recipients' title, naming the submitter as its client did.
    """
    orders = {'flags': [], 'read': False, 'messages': [], 'notes': [],
              'notes_edited': []}
    refusals = []

    def who(civ):
        return names.get(civ, civ)

    was_table, now_table = news.read_table(served), news.read_table(sub)
    civs = [o['oid'] for o in icv.owner_records(served)]
    was = {c: news.items_of(served, c) for c in civs}
    now = {c: news.items_of(sub, c) for c in civs}
    if was_table is None or any(v is None for v in was.values()):
        return orders, refusals
    if now_table is None or any(now[c] is None for c in civs):
        return orders, ['news DROPPED, a news list or the string table does '
                        'not read']
    first_new, served_strings = was_table
    strings = now_table[1]

    turn = icv.turn_of(served)
    mine_was, mine_now = was[civ_oid], now[civ_oid]
    if (len(mine_now) < len(mine_was)
            or not all(_same_item(a, b, news.IMPORTANT)
                       or (a['type'] == news.NOTE
                           and note_refusal(b, turn) is None)
                       for a, b in zip(mine_was, mine_now))):
        return orders, ['news DROPPED, its list does not begin with the items '
                        'served']
    for i, (a, b) in enumerate(zip(mine_was, mine_now)):
        if not _same_item(a, b, news.IMPORTANT):
            orders['notes_edited'].append((i, b))
        elif a['flags'] != b['flags']:
            orders['flags'].append((i, b['flags']))

    identity = civ_identity(served, civ_oid)
    me = {str(names.get(civ_oid, '')).encode('latin-1')}
    if identity and identity[0]:
        me.add(identity[0])
    met = {other for civ, other in stances(served) if civ == civ_oid}

    def text_of(sid):
        """The submitted text of a string id, or None when the id is neither
        served with that text nor new in this submission."""
        if sid in served_strings:
            if strings.get(sid) == served_strings[sid]:
                return served_strings[sid]
            return None
        return strings.get(sid) if sid >= first_new else None

    def plain_item(item):
        return (item['title'] == item['body'] == b'' and item['turn'] == turn
                and item['position'] == NO_POSITION)

    received = []                       # (recipient, item, title, body)
    for civ in civs:
        if civ == civ_oid:
            continue
        # Another civ's list is served empty (`news.own_view`), or whole to a
        # submitter given the full galaxy. Items past either are new.
        a, b = was[civ], now[civ]
        if len(b) >= len(a) and all(_same_item(x, y) for x, y in zip(a, b)):
            b = b[len(a):]
        for item in b:
            title, body = text_of(item['title_id']), text_of(item['body_id'])
            if (item['type'] != news.RECEIVED or item['flags'] or title is None
                    or body is None or not plain_item(item)):
                refusals.append(f"news item for {who(civ)} DROPPED, it is not "
                                f"a message as the client writes one")
                continue
            received.append((civ, item, title, body))

    for item in mine_now[len(mine_was):]:
        if item['type'] == news.NOTE:
            why = note_refusal(item, turn)
            if why:
                refusals.append(f'note DROPPED, {why}')
            else:
                orders['notes'].append(item)
            continue
        title, body = text_of(item['title_id']), text_of(item['body_id'])
        to = item['ids']
        why = None
        if (item['type'] != news.SENT or title is None or body is None
                or not plain_item(item) or item['flags'] & ~news.IMPORTANT):
            why = 'it is not a message as the client writes one'
        elif not to or len(set(to)) != len(to) or civ_oid in to:
            why = 'its recipients are not other civs, each once'
        elif not set(to) <= met:
            why = (f"it names a civ {who(civ_oid)} has not met: "
                   f"{', '.join(str(who(c)) for c in sorted(set(to) - met))}")
        elif not title.startswith(TO_TITLE) or SUBJECT_SEP not in title:
            why = "its title is not the client's"
        if why:
            refusals.append(f'message DROPPED, {why}')
            continue
        subject = title.split(SUBJECT_SEP, 1)[1]
        heads = {FROM_TITLE % name + subject for name in me}
        matched = []
        for civ in to:
            hit = next((r for r in received if r[0] == civ and r[3] == body
                        and r[2] in heads and civ_oid in r[1]['ids']
                        and set(r[1]['ids']) <= set(to) | {civ_oid}), None)
            if hit is None:
                break
            matched.append(hit)
        if len(matched) != len(to):
            refusals.append(f'message {subject!r} DROPPED, a recipient does '
                            f'not hold it as the client writes it')
            continue
        for hit in matched:
            received.remove(hit)
        orders['messages'].append({
            'to': to, 'subject': subject, 'body': body, 'sent_title': title,
            'from_title': matched[0][2], 'sent_flags': item['flags'],
            'ids': {civ: hit[1]['ids'] for civ, hit in zip(to, matched)}})
    for civ, _item, _title, _body in received:
        refusals.append(f"news item for {who(civ)} DROPPED, no message "
                        f"{who(civ_oid)} sent matches it")

    flag_at, sub_flag_at = _new_news_at(served, civ_oid), _new_news_at(sub, civ_oid)
    if (flag_at is not None and sub_flag_at is not None
            and served[flag_at] == 1 and sub[sub_flag_at] == 0):
        orders['read'] = True
    return orders, refusals


def take_messages(blob, civ_oid, orders, turn, names):
    """(blob, refusals, log lines) with a civ's news orders applied to the
    galaxy being built."""
    refusals, lines = [], []
    for index, flags in orders['flags']:
        blob = news.set_flags(blob, civ_oid, index, flags)
        lines.append(f"news item {index}: "
                     f"{'important' if flags & news.IMPORTANT else 'not important'}")
    if orders['read']:
        at = _new_news_at(blob, civ_oid)
        if at is not None:
            out = bytearray(blob)
            out[at] = 0
            blob = bytes(out)
            lines.append('news read')
    for m in orders['messages']:
        subject = plain_text(m['subject'])
        body = plain_text(m['body'], BODY_CONTROLS)
        sent = plain_text(m['sent_title'])
        received = plain_text(m['from_title'])
        label = f"message {m['subject'][:30]!r}"
        why = None
        if None in (subject, body, sent, received):
            why = 'it holds characters the client cannot show'
        elif not subject.strip() or not body.strip():
            why = 'its subject or body is empty'
        elif len(subject) > SUBJECT_MAX:
            why = f'its subject is over {SUBJECT_MAX} characters'
        elif message_rows(body) > MESSAGE_ROWS:
            why = f"its body is over the send dialog's {MESSAGE_ROWS} rows"
        elif any(_new_news_at(blob, c) is None for c in m['to']):
            why = 'a recipient cannot be read'
        if why:
            refusals.append(f'{label} DROPPED, {why}')
            continue
        next_id, strings = news.read_table(blob)
        from_id, next_id = news.intern(next_id, strings, received)
        body_id, next_id = news.intern(next_id, strings, body)
        to_id, next_id = news.intern(next_id, strings, sent)
        blob = news.write_table(blob, next_id, strings)
        for civ in m['to']:
            blob = news.append_item(blob, civ, news.build_item({
                'type': news.RECEIVED, 'flags': 0, 'turn': turn,
                'title': b'', 'body': b'', 'title_id': from_id,
                'body_id': body_id, 'ids': m['ids'][civ],
                'position': NO_POSITION}))
            at = _new_news_at(blob, civ)
            out = bytearray(blob)
            out[at] = 1
            blob = bytes(out)
        blob = news.append_item(blob, civ_oid, news.build_item({
            'type': news.SENT, 'flags': m['sent_flags'], 'turn': turn,
            'title': b'', 'body': b'', 'title_id': to_id, 'body_id': body_id,
            'ids': m['to'], 'position': NO_POSITION}))
        lines.append(f"{label} to "
                     f"{', '.join(str(names.get(c, c)) for c in m['to'])}")
    for index, item in orders.get('notes_edited', ()):
        blob = news.replace_item(blob, civ_oid, index, news.build_item(item))
        lines.append(f"note {index} edited")
    for item in orders.get('notes', ()):
        blob = news.append_item(blob, civ_oid, news.build_item(item))
        lines.append(f"note {item['title'][len(NOTE_TITLE):][:30]!r} written")
    return blob, refusals, lines


def market_orders(served, sub):
    """([(resource, units)], refusals) for a civ's market trades: how far each
    count moved between the state served and the submission, positive sold.
    A submission never ticks, so only the submitter's trades move them."""
    was, now = market_of(served), market_of(sub)
    if was is None or now is None:
        return [], ([] if was == now else
                    ['market trades DROPPED, RSMA cannot be read'])
    return [(k, n - w) for k, (w, n) in enumerate(zip(was, now)) if n != w], []


# A civ's production queues are in its `OWNR > DATA > SPQS`: a u32 count, then
# one `SPRQ` per queue:
#
#     SPRQ v0   u32 objectId ; u32 planets using it ; one byte ; PRQU
#     PRQU v1   u16 entry-id counter ; u32 nameLen ; name ; u32 entry count ;
#               the entries, in the order the UI shows them
#
# An entry is a u16 entry id, a u16 count, a u8 infinite flag, a u8 count mode
# (1 "build up to") and a u32 item type, then a u32 naming the item for the
# types the reader follows with one: the facility type for 0, the design's
# object id for 8, the scan for 9. The writer is 0x556560 and the reader
# 0x556D20.
#
# `SPQS` is found by its tag in `DATA` and believed when it holds its count of
# `SPRQ` sections.
SPQS_COUNT = 4
SPRQ_USING_AT = 4           # in the SPRQ payload, after the object id
SPRQ_FIXED = 9              # object id, planets using it and the one byte
PRQU_VERSION = 1
QUEUE_ENTRY = 10            # entry id, count, two flags and the item type
ITEM_FACILITY = 0
ITEM_SHIP = 8
ITEM_SCAN = 9
SCAN_KINDS = 6                  # the scanning screen's six; route is 2, fleet 3
ITEM_ARGUMENT = {0, 7, 8, 9}    # item types followed by a u32
ITEM_TYPES = 10                 # the reader's switch covers 0..9


def _spqs(blob):
    """(tree, {civObjectId: (DATA, SPQS start, SPQS end) or None}) for every
    civ. A civ's entry is None unless its DATA holds exactly one SPQS holding
    its count of SPRQ sections."""
    tree = sp.parse_blob(blob)
    glxy = next(tree[0].find('GLXY'))
    out = {}
    for o in glxy.children:
        if o.tag != b'OWNR':
            continue
        n = struct.unpack_from('<I', blob, o.payload)[0]
        civ = struct.unpack_from('<I', blob, o.payload + 4 + n)[0]
        data = next((c for c in o.children if c.tag == b'DATA'), None)
        found = []
        at = blob.find(b'SPQS', data.payload, data.end - 8) if data else -1
        while at >= 0:
            end = (at + 8 + (struct.unpack_from('<I', blob, at + 4)[0]
                             & sp.SIZE_MASK))
            if at + 8 + SPQS_COUNT <= end <= data.end:
                count = struct.unpack_from('<I', blob, at + 8)[0]
                secs = _sections(blob, at + 8 + SPQS_COUNT, end)
                if (secs is not None and len(secs) == count
                        and all(tag == b'SPRQ' and e - p >= SPRQ_FIXED
                                for tag, p, e in secs)):
                    found.append((at, end))
            at = blob.find(b'SPQS', at + 1, data.end - 8)
        out[civ] = (data,) + found[0] if len(found) == 1 else None
    return tree, out


def queues_by_civ(blob):
    """{civObjectId: [(objectId, whole SPRQ section)] in order, or None when
    the civ's SPQS cannot be found}."""
    out = {}
    for civ, found in _spqs(blob)[1].items():
        if found is None:
            out[civ] = None
            continue
        _data, at, end = found
        secs = _sections(blob, at + 8 + SPQS_COUNT, end)
        out[civ] = [(struct.unpack_from('<I', blob, p)[0],
                     bytes(blob[p - 8:e])) for _tag, p, e in secs]
    return out


def queues_of(blob, civ_oid):
    """[(objectId, whole SPRQ section)] of a civ in order, or None when its
    SPQS cannot be found."""
    return queues_by_civ(blob).get(civ_oid)


def queue_ids(blob):
    """Every production queue's object id, whichever civ holds it."""
    return {oid for queues in queues_by_civ(blob).values()
            for oid, _r in queues or ()}


def set_queues(blob, civ_oid, records):
    """Rewrite a civ's SPQS to hold these whole SPRQ sections, in order."""
    tree, civs = _spqs(blob)
    if civs.get(civ_oid) is None:
        raise ValueError(f'civ {civ_oid} has no readable SPQS')
    data, at, end = civs[civ_oid]
    version = struct.unpack_from('<I', blob, at + 4)[0] >> sp.VERSION_SHIFT
    spqs = sp.build_section(b'SPQS', version, struct.pack('<I', len(records))
                            + b''.join(records))
    body = bytes(blob[data.payload:at]) + spqs + bytes(blob[end:data.end])
    return sp.replace_payload(blob, tree, data, body)


def read_queue(record):
    """([(entry id, item type, item, item offset)], why) for a whole SPRQ
    section: the entries and '', or None and what is wrong with it. The item
    and its offset are None for an item type with nothing after it."""
    body = _sections(record, 8 + SPRQ_FIXED, len(record))
    if not body or len(body) != 1 or body[0][0] != b'PRQU':
        return None, 'its record is not one PRQU'
    _tag, at, end = body[0]
    version = struct.unpack_from('<I', record, at - 4)[0] >> sp.VERSION_SHIFT
    if version != PRQU_VERSION:
        return None, f'its PRQU is version {version}, not {PRQU_VERSION}'
    if end - at < 6:
        return None, 'its PRQU is cut short'
    name_end = at + 6 + struct.unpack_from('<I', record, at + 2)[0]
    if name_end + 4 > end:
        return None, 'its name runs past its PRQU'
    ok, why = name_acceptable(bytes(record[at + 6:name_end]))
    if not ok:
        return None, why
    count = struct.unpack_from('<I', record, name_end)[0]
    entries, p = [], name_end + 4
    for _i in range(count):
        if p + QUEUE_ENTRY > end:
            break
        eid = struct.unpack_from('<H', record, p)[0]
        itype = struct.unpack_from('<I', record, p + 6)[0]
        p += QUEUE_ENTRY
        if itype >= ITEM_TYPES:
            return None, (f'entry {eid} has item type {itype}, which the '
                          f'engine does not read')
        item = item_at = None
        if itype in ITEM_ARGUMENT:
            if p + 4 > end:
                break
            item, item_at = struct.unpack_from('<I', record, p)[0], p
            p += 4
        entries.append((eid, itype, item, item_at))
    if len(entries) != count or p != end:
        return None, f'its PRQU does not hold the {count} entries it counts'
    ids = [e[0] for e in entries]
    if 0 in ids or len(set(ids)) != len(ids):
        return None, 'its entry ids are not distinct and non-zero'
    return entries, ''


def with_queue_id(record, oid):
    """An SPRQ section carrying a different object id."""
    out = bytearray(record)
    struct.pack_into('<I', out, 8, int(oid))
    return bytes(out)


def with_using(record, count):
    """An SPRQ section saying `count` planets use it."""
    out = bytearray(record)
    struct.pack_into('<I', out, 8 + SPRQ_USING_AT, int(count))
    return bytes(out)


def with_queue_designs(record, remap):
    """An SPRQ section whose ship entries follow designs renumbered in
    `remap`."""
    entries, _why = read_queue(record)
    out = bytearray(record)
    for _eid, itype, item, at in entries or ():
        if itype == ITEM_SHIP and item in remap:
            struct.pack_into('<I', out, at, int(remap[item]))
    return bytes(out)


def queue_refusal(record, designs, facility_types):
    """Why a queue may not be carried, or '' when it may.

    `designs` are the design ids the civ holds in the merged galaxy. The
    client's queue editor writes only facilities, ships and scans, so any
    other item type is refused.
    """
    entries, why = read_queue(record)
    if entries is None:
        return why
    for eid, itype, item, _at in entries:
        if itype == ITEM_FACILITY:
            if item >= facility_types:
                return (f'entry {eid} names facility type {item}, and the '
                        f'galaxy has {facility_types}')
        elif itype == ITEM_SHIP:
            if item not in designs:
                return (f'entry {eid} builds design {item}, which the civ '
                        f'does not own in the merged galaxy')
        elif itype == ITEM_SCAN:
            if item >= SCAN_KINDS:
                return (f'entry {eid} names scan {item}, and the scanning '
                        f'screen has {SCAN_KINDS}')
        else:
            return (f'entry {eid} has item type {itype}; the client queues '
                    f'only facilities, ships and scans')
    return ''


def queue_links(blob):
    """{planetObjectId: (flag, queue object id, entry id)} for every planet
    with a readable PROD."""
    out = {}
    for oid, (_owner, prod, _plpr, _name) in planet_index(blob).items():
        link = queue_link(prod)
        if link is not None:
            out[oid] = link
    return out


def link_refusal(prod, prod_served, queues):
    """Why a planet's queue link may not be carried, or '' when it may.

    `queues` is {queue object id: entry ids} for the queues the civ holds in
    the merged galaxy. Leaving a queue clears the flag.
    """
    link = queue_link(prod)
    if link == queue_link(prod_served):
        return ''
    flag, qid, entry = link
    if flag == 0:
        return ''
    if flag != 1:
        return f'its queue link flag reads {flag}'
    if qid not in queues:
        return (f'it names production queue {qid}, which is not one of the '
                f"civ's queues in the merged galaxy")
    if entry and entry not in queues[qid]:
        return (f'it is on entry {entry} of production queue {qid}, which '
                f'that queue does not hold')
    return ''


def queue_orders(served, sub, civ_oid, designs, remap, facility_types):
    """(new, edited, deleted, refusals) for a civ's production queues, judged
    against the state served.

    new and edited are [(objectId, SPRQ)], new ones under the id submitted,
    their ship entries following `remap`, the designs renumbered in this
    merge. deleted is a list of ids; the caller deletes one only once no
    planet uses it. The planet counts are the referee's to recount, so they
    are ignored here.
    """
    refusals = []
    all_s, all_t = queues_by_civ(served), queues_by_civ(sub)
    for o in icv.owner_records(served):
        if o['oid'] != civ_oid and all_t.get(o['oid']) != all_s.get(o['oid']):
            refusals.append(f"production queues DROPPED, they belong to "
                            f"{o['name']}")
    mine_s, mine_t = all_s.get(civ_oid), all_t.get(civ_oid)
    if mine_s is None or mine_t is None:
        if mine_t != mine_s:
            refusals.append('production queues DROPPED, SPQS does not hold '
                            'its count of queues')
        return [], [], [], refusals
    if len({oid for oid, _r in mine_t}) != len(mine_t):
        refusals.append('production queues DROPPED, two of them share an '
                        'object id')
        return [], [], [], refusals
    held, kept = dict(mine_s), dict(mine_t)
    high = struct.unpack_from('<I', served, idg.HIGH_WATER_ID)[0]

    new, edited = [], []
    for oid, record in mine_t:
        if oid in held and with_using(record, 0) == with_using(held[oid], 0):
            continue
        if oid not in held and oid <= high:
            refusals.append(f'production queue {oid} DROPPED, it is new and '
                            f'{oid} is not a new object id')
            continue
        record = with_queue_designs(record, remap)
        why = queue_refusal(record, designs, facility_types)
        if why:
            refusals.append(f'production queue {oid} DROPPED, {why}')
            continue
        (edited if oid in held else new).append((oid, record))
    deleted = [oid for oid, _r in mine_s if oid not in kept]
    return new, edited, deleted, refusals


def _tally(jobs):
    """"6 farmer, 2 worker" from a list of job ids, for a log line."""
    import collections
    c = collections.Counter(jobs)
    return ', '.join(f'{n} {JOB_IDS.get(j, j)}' for j, n in sorted(c.items()))


def _ship_crew_index(blob):
    """{shipObjectId: (owner, SHPR payload bytes)}, a fleet's ships under the
    fleet's owner."""
    out = {}
    for o in space_objects(blob)[2]:
        if o['tag'] == b'SHIP':
            out[o['oid']] = (o['owner'], o['shpr'])
        elif not o['why']:
            for m, shpr, _v in o['members']:
                out[m] = (o['owner'], shpr)
    return out


def merge(blob, submissions, log=print, notes=None, arms=None, scans=None):
    """submissions: [(civ_name, submitted_blob)]. Returns the merged blob.

    `notes` is an optional `{civ: [reason]}` that every refusal is recorded in
    as well as logged. `referee.py` writes it beside the turn so
    `player_turn.follow` can print it into the launcher's log. `arms` is an
    optional `{civ: {coat-of-arms id}}` of the uploads each player may choose;
    without it only no arms is carried. `scans` is an optional `{civ: count}`
    of the reports the referee answered each civ this turn.
    """
    served_ships = ship_index(blob)
    served_members = {m: f['owner'] for f in fleet_index(blob).values()
                      for m, _s, _v in f['members']}
    served_planets = planet_index(blob)
    served_crews = _ship_crew_index(blob)
    served_designs = design_index(blob)
    # Research as it was served, like the three indexes above: every
    # submission is compared against the state served, not the merged blob.
    served_research = {o['oid']: research_of(blob, o['oid'])
                       for o in icv.owner_records(blob)}
    served_completed = {o['oid']: completed_research(blob, o['oid']) or []
                        for o in icv.owner_records(blob)}
    names = {o['oid']: o['name'] for o in icv.owner_records(blob)}
    served_treaties = treaties(blob) or {}
    served_market = market_of(blob)
    served = blob
    accepted = dropped = 0

    for civ_name, sub in submissions:
        def drop(reason, _civ=civ_name):
            """Refuse one change: log it and record it in `notes`."""
            nonlocal dropped
            dropped += 1
            log(f"    {reason}")
            if notes is not None:
                notes.setdefault(_civ, []).append(reason)

        # A submission that raises while being read is dropped whole and the
        # blob is put back to what it was before this civ, so one bad
        # submission cannot end the merge. `SystemExit` is not caught:
        # `civ_by_name` raises it for a civ the galaxy does not hold, a caller
        # error that `referee.screen_submission` refuses earlier.
        before, kept = blob, accepted
        try:
            civ = civ_by_name(blob, civ_name)
            mine = civ['oid']
            log(f"{civ_name} (object {mine}):")

            # Scan reports first: a new one is taken out of the submission,
            # which the rules below then read without it.
            had, now = reports_of(served, mine), reports_of(sub, mine)
            if had is not None and now is not None and had != now:
                kept, deleted, made, why = report_orders(had, now)
                answered = min(made, (scans or {}).get(civ_name, 0))
                if answered:
                    log(f"    {answered} scan report(s) the referee answered, "
                        f"added from its own record")
                if made > answered:
                    drop(f"{made - answered} scan report(s) DROPPED: a scan "
                         f"is carried only when the referee answered it "
                         f"online; the scans stay in stock")
                if why:
                    drop(f"scan reports DROPPED, {why}")
                elif deleted:
                    blob = set_reports(blob, mine, kept)
                    log(f"    {deleted} scan report(s) deleted")
                    accepted += 1
                sub = set_reports(sub, mine, kept)
            elif had is not None and now is None:
                drop("scan reports DROPPED, the submission's block after the "
                     "designs does not read as reports")

            # Ship designs, before the production queues that may name them.
            # Judged against `served_designs`, the state as it was served.
            remap = {}
            new_designs, toggled, edited, deleted, refusals = design_orders(
                served_designs, design_index(sub), mine,
                designs_in_use(served, mine))
            for why in refusals:
                drop(why)
            for oid, inactive in toggled:
                blob = set_design_inactive(blob, oid, inactive)
                log(f"    design {oid}: "
                    f"{'deactivated' if inactive else 'reactivated'}")
                accepted += 1
            for oid, record in edited:
                blob = replace_design(blob, oid, record)
                log(f"    design {oid}: edited, now "
                    f"{design_index(blob)[oid]['name']!r}")
                accepted += 1
            for was, d in new_designs:
                oid = was
                if oid <= idg.max_object_id(blob):
                    # Another player's new object already took this id, so
                    # this one is renumbered. The only check made against the
                    # merged blob: an id is free or taken in the galaxy being
                    # built.
                    oid = next_object_id(blob)
                    remap[was] = oid
                blob = set_high_water(blob, oid)
                blob = add_design(blob, mine, with_design_id(d['record'], oid))
                log(f"    design {oid}: {d['name']!r} taken"
                    + (f", submitted as {was} and renumbered"
                       if oid != was else ""))
                accepted += 1
            for oid in deleted:
                blob = remove_design(blob, mine, oid)
                log(f"    design {oid}: deleted")
                accepted += 1
            mine_designs = designs_of(blob, mine)

            # Production queues, after the designs their entries may name and
            # before the planets that join them. A new one takes a free id as
            # a governor does; a deleted one goes after the planets have moved.
            new_queues, edited_queues, deleted_queues, refusals = queue_orders(
                served, sub, mine, mine_designs, remap,
                facility_rules.type_count(blob))
            for why in refusals:
                drop(why)
            queue_remap = {}
            if new_queues or edited_queues:
                records = dict(queues_of(blob, mine))
                for oid, record in edited_queues:
                    records[oid] = record
                    log(f"    production queue {oid}: edited")
                    accepted += 1
                for was, record in new_queues:
                    oid = was
                    if oid <= idg.max_object_id(blob):
                        # Taken in the galaxy being built, by another
                        # player's design, governor or queue.
                        oid = next_object_id(blob)
                        queue_remap[was] = oid
                    blob = set_high_water(blob, oid)
                    records[oid] = with_queue_id(record, oid)
                    log(f"    production queue {oid}: taken"
                        + (f", submitted as {was} and renumbered"
                           if oid != was else ""))
                    accepted += 1
                blob = set_queues(blob, mine, list(records.values()))
            usable = {oid: {e[0] for e in read_queue(r)[0] or ()}
                      for oid, r in queues_of(blob, mine) or ()
                      if oid not in deleted_queues}

            # Admirals, before the ships that may name them. A new one takes
            # a free id; deleting one waits until the ships have moved.
            new_adms, edited_adms, deleted_adms, refusals = admiral_orders(
                served, sub, mine)
            for why in refusals:
                drop(why)
            admiral_remap = {}
            if new_adms or edited_adms:
                records = dict(admirals_of(blob, mine))
                for oid, record in edited_adms:
                    records[oid] = record
                    log(f"    {admiral_label(oid, record)}: edited")
                    accepted += 1
                for was, record in new_adms:
                    oid = was
                    if oid <= idg.max_object_id(blob):
                        # Taken in the galaxy being built by another player's
                        # new object.
                        oid = next_object_id(blob)
                        admiral_remap[was] = oid
                    blob = set_high_water(blob, oid)
                    records[oid] = with_admiral_id(record, oid)
                    log(f"    {admiral_label(oid, record)}: taken"
                        + (f", submitted as {was} and renumbered"
                           if oid != was else ""))
                    accepted += 1
                blob = set_admirals(blob, mine, list(records.values()))
            mine_admirals = {oid for oid, _r in admirals_of(blob, mine) or ()}
            # Fleets, against the state served, before the ship orders and
            # crews that look for ships where the fleets leave them. A new
            # fleet takes a free id like a design.
            layout, refusals = fleet_orders(served, sub, mine, names,
                                            mine_admirals, admiral_remap)
            for why in refusals:
                drop(why)
            if layout is not None:
                renumbered = {}
                for was in sorted(f for f, v in layout['fleets'].items()
                                  if v['new']):
                    oid = was
                    if oid <= idg.max_object_id(blob):
                        oid = next_object_id(blob)
                        renumbered[was] = oid
                    blob = set_high_water(blob, oid)
                blob = set_fleets(blob, mine, layout, renumbered)
                for line in layout['log']:
                    log(f"    {line}")
                    accepted += 1
                for was, oid in renumbered.items():
                    log(f"    fleet {oid}: submitted as {was} and renumbered")

            # ship orders
            for ship_oid, (_claimed, dyno) in sorted(ship_index(sub).items()):
                if ship_oid not in served_ships:
                    if ship_oid in served_members:
                        # Out of a fleet: the fleet step judged it.
                        owner = served_members[ship_oid]
                        if owner != mine:
                            drop(f"ship {ship_oid}: DROPPED, owned by "
                                 f"{names.get(owner, owner)}")
                        continue
                    drop(f"ship {ship_oid}: DROPPED, not in the state served")
                    continue
                owner, as_served = served_ships[ship_oid]
                if dyno == as_served:
                    continue
                if owner != mine:
                    drop(f"ship {ship_oid}: DROPPED, owned by "
                         f"{names.get(owner, owner)}")
                    continue
                dyno, why = ship_admiral(dyno, dyno_admiral(as_served) or 0,
                                         mine_admirals, admiral_remap)
                if why:
                    drop(f"ship {ship_oid}: {why}")
                if dyno == as_served:
                    continue
                blob = replace_dyno(blob, ship_oid, dyno)
                log(f"    ship {ship_oid}: order taken "
                    f"({len(as_served)} -> {len(dyno)} bytes)")
                accepted += 1

            # production queues and job allocation
            for planet_oid, (_claimed, prod, plpr, name) in sorted(
                    planet_index(sub).items()):
                if planet_oid not in served_planets:
                    drop(f"planet {planet_oid}: DROPPED, not in the state "
                         f"served")
                    continue
                owner, prod_served, plpr_served, name_served = \
                    served_planets[planet_oid]

                # A queue naming a renumbered design follows the new id.
                if prod is not None and prod_builds(prod) in remap:
                    prod = with_prod_design(prod, remap[prod_builds(prod)])
                # A planet joining a renumbered production queue follows it.
                link = queue_link(prod)
                if link and link[0] == 1 and link[1] in queue_remap:
                    prod = with_prod_queue(prod, queue_remap[link[1]])

                if name is None and name_served is not None:
                    # planet_name returns None for a corrupt or overlong name.
                    drop(f"planet {planet_oid}: rename DROPPED, the name "
                         f"field is unreadable or longer than {MAX_NAME} "
                         f"bytes")
                elif (name is not None and name_served is not None
                      and name != name_served):
                    if owner != mine:
                        drop(f"planet {planet_oid}: rename DROPPED, owned by "
                             f"{names.get(owner, owner)}")
                    else:
                        ok, why = name_acceptable(name[1])
                        if not ok:
                            drop(f"planet {planet_oid}: rename DROPPED, {why}")
                        else:
                            blob = set_planet_name(blob, planet_oid, name[1])
                            log(f"    planet {planet_oid}: renamed "
                                f"{name_served[1].decode('latin1')!r} -> "
                                f"{name[1].decode('latin1')!r}")
                            accepted += 1

                if prod != prod_served:
                    if owner != mine:
                        drop(f"planet {planet_oid}: production DROPPED, "
                             f"owned by {names.get(owner, owner)}")
                    elif prod is None or prod_served is None:
                        drop(f"planet {planet_oid}: production DROPPED, no "
                             f"PROD section on one side")
                    elif is_hurried(prod_served):
                        # Paid off and finishing at the tick; the UI does not
                        # let it be swapped.
                        drop(f"planet {planet_oid}: production DROPPED, the "
                             f"item being built was hurried and cannot be "
                             f"changed")
                    elif prod_kind(prod) not in PROD_KINDS:
                        drop(f"planet {planet_oid}: production DROPPED, it "
                             f"builds {prod_kind(prod)!r}, which is not a "
                             f"facility, a ship, a scan or wealth")
                    elif why := link_refusal(prod, prod_served, usable):
                        # A planet naming a queue the merged galaxy lacks
                        # would point at nothing.
                        drop(f"planet {planet_oid}: production DROPPED, "
                             f"{why}")
                    elif (prod_facility(prod) is not None
                          and prod_facility(prod) != prod_facility(prod_served)
                          and (why := facility_refusal(
                              blob, served_completed.get(mine, []),
                              plpr_served, prod_facility(prod)))):
                        drop(f"planet {planet_oid}: production DROPPED, "
                             f"facility {prod_facility(prod)} cannot be built: "
                             f"{why}")
                    elif (prod_builds(prod) is not None
                          and prod_builds(prod) not in mine_designs):
                        # A queue naming a design the galaxy does not hold makes
                        # the engine refuse the whole galaxy. Checked against
                        # the submitter's designs in the merged galaxy, the
                        # file about to be published.
                        drop(f"planet {planet_oid}: production DROPPED, it "
                             f"builds design {prod_builds(prod)}, which "
                             f"{civ_name} does not own in the merged galaxy")
                    elif is_hurried(prod) and not is_hurried(prod_served):
                        # The submission carries a hurry's outcome (item
                        # complete, credits spent). The referee prices it from
                        # the served state and deducts the credits itself.
                        ok, why, cost, points = hurry_acceptable(
                            plpr_served, plpr, credits_of(blob, mine))
                        if not ok:
                            drop(f"planet {planet_oid}: hurry DROPPED, {why}")
                        else:
                            blob = replace_prod(blob, planet_oid, prod)
                            blob = set_progress(blob, planet_oid,
                                                progress_of(plpr))
                            blob = set_credits(blob, mine,
                                               credits_of(blob, mine) - cost)
                            log(f"    planet {planet_oid}: production "
                                f"hurried, {points} point(s) for {cost} "
                                f"credits")
                            accepted += 1
                    else:
                        blob = replace_prod(blob, planet_oid, prod)
                        log(f"    planet {planet_oid}: production taken")
                        accepted += 1

                if plpr is None or plpr_served is None:
                    continue
                rate, rate_served = recruit_of(plpr), recruit_of(plpr_served)
                if rate is not None and rate != rate_served:
                    if owner != mine:
                        drop(f"planet {planet_oid}: recruitment rate DROPPED, "
                             f"owned by {names.get(owner, owner)}")
                    elif not 0 <= rate <= 100:
                        drop(f"planet {planet_oid}: recruitment rate DROPPED, "
                             f"{rate} is not a percentage")
                    else:
                        blob = set_recruit(blob, planet_oid, rate)
                        log(f"    planet {planet_oid}: recruitment rate "
                            f"{rate_served}% -> {rate}%")
                        accepted += 1

            # Facilities sold, then the energy links, judged on what the
            # sales leave standing.
            sub_planets = planet_index(sub)
            for planet_oid, (owner, prod_served, plpr_served, _nm) in sorted(
                    served_planets.items()):
                if planet_oid not in sub_planets:
                    continue
                had = facilities_of(plpr_served)
                now = facilities_of(sub_planets[planet_oid][2])
                if had is None or now is None or had == now:
                    continue
                if owner != mine:
                    drop(f"planet {planet_oid}: facility sale DROPPED, owned "
                         f"by {names.get(owner, owner)}")
                    continue
                sold, why = facility_sales(had, now)
                why = why or next(filter(None, (
                    sale_refusal(prod_served, t) for t in sold)), '')
                if why:
                    drop(f"planet {planet_oid}: facility sale DROPPED, {why}")
                    continue
                refund = sum(sale_refund(blob, t, k) for t, k in sold.items())
                blob = set_facilities(blob, planet_oid, now)
                blob = set_credits(blob, mine, credits_of(blob, mine) + refund)
                log(f"    planet {planet_oid}: sold "
                    + ', '.join(f'{k} of facility {t}'
                                for t, k in sorted(sold.items()))
                    + f" for {refund} credits")
                accepted += 1
            standing = planet_index(blob)
            served_links, sub_links = enli_of(served), enli_of(sub)
            for planet_oid, links in sorted(sub_links.items()):
                if links == served_links.get(planet_oid, links):
                    continue
                if planet_oid not in standing:
                    continue
                why = energy_link_refusal(planet_oid, links, mine, standing)
                if why and not links and standing[planet_oid][0] == mine:
                    why = None          # the receiver was sold
                if why:
                    drop(f"planet {planet_oid}: energy link DROPPED, {why}")
                    continue
                blob = set_enli(blob, planet_oid, links)
                log(f"    planet {planet_oid}: energy links "
                    f"{served_links.get(planet_oid)} -> {links}")
                accepted += 1
            # A sale takes the sold transmitters out of the civ's links and
            # empties a sold receiver's, as the engine does at once.
            for planet_oid, links in sorted(enli_of(blob).items()):
                if standing.get(planet_oid, (None,))[0] != mine or not links:
                    continue
                if energy_link_refusal(planet_oid, [], mine, standing):
                    kept = []
                else:
                    kept = [t for t in links if not energy_link_refusal(
                        planet_oid, [t], mine, standing)]
                if kept != links:
                    blob = set_enli(blob, planet_oid, kept)

            # A queue is deleted only once no planet uses it as merged, and
            # each counts the planets that do.
            merged_queues = queues_of(blob, mine)
            if merged_queues:
                links = queue_links(blob)
                using = collections.Counter(
                    qid for flag, qid, _e in links.values() if flag == 1)
                records = []
                for oid, record in merged_queues:
                    if oid in deleted_queues:
                        users = sorted(p for p, (flag, qid, _e)
                                       in links.items()
                                       if flag == 1 and qid == oid)
                        if users:
                            drop(f"production queue {oid} deletion DROPPED, "
                                 f"planet(s) {users} still use it")
                        else:
                            log(f"    production queue {oid}: deleted")
                            accepted += 1
                            continue
                    records.append(with_using(record, using[oid]))
                if records != [r for _oid, r in merged_queues]:
                    blob = set_queues(blob, mine, records)

            # Everybody this civ holds, judged together (see people_acceptable).
            ok, why = people_acceptable(blob, sub, mine)
            planets_before, ships_before = population(blob, mine)
            planets_after, ships_after = population(sub, mine)
            if planets_after is None:
                pass                   # unreadable; people_acceptable said so
            elif not ok:
                drop(f"people DROPPED, {why}")
            else:
                for oid in sorted(planets_after):
                    cz_b, mil_b = planets_before[oid]
                    cz_a, mil_a = planets_after[oid]
                    if (cz_b, mil_b) == (cz_a, mil_a):
                        continue
                    blob = set_people(blob, oid, citizen_bytes(
                        planet_index(sub)[oid][2]), mil_a)
                    what = []
                    if [c[0] for c in cz_b] != [c[0] for c in cz_a]:
                        what.append(f'{_tally([c[0] for c in cz_b])} -> '
                                    f'{_tally([c[0] for c in cz_a])}')
                    if len(mil_b) != len(mil_a):
                        what.append(f'{len(mil_b)} -> {len(mil_a)} stationed')
                    log(f"    planet {oid}: "
                        f"{'; '.join(what) or 'people moved'}")
                    accepted += 1
                for oid in sorted(ships_after):
                    if ships_before[oid] == ships_after[oid]:
                        continue
                    blob = set_crew(blob, oid, ships_after[oid])
                    log(f"    ship {oid}: crew {len(ships_before[oid])} -> "
                        f"{len(ships_after[oid])}")
                    accepted += 1
            # People on planets and ships other civs own, against the state
            # served: never copied, always named.
            for why in foreign_people_refusals(served_planets, served_crews,
                                               sub, mine, names):
                drop(why)

            # Wormholes connected, judged against the state served.
            pairs, refusals = wormhole_orders(served, sub, mine)
            for why in refusals:
                drop(why)
            for a, b in pairs:
                blob = set_wormhole_link(set_wormhole_link(blob, a, b), b, a)
                log(f"    wormholes {a} and {b}: connected")
                accepted += 1

            # system names, which belong to whoever holds most of the system
            served_systems = systems(blob)
            for sun_oid, (_o, _t, name) in sorted(systems(sub).items()):
                if sun_oid not in served_systems:
                    drop(f"system {sun_oid}: DROPPED, not in the state served")
                    continue
                owners, total, name_served = served_systems[sun_oid]
                if name_served is None:
                    continue
                if name is None:
                    drop(f"system {sun_oid}: rename DROPPED, the name field "
                         f"is unreadable or longer than {MAX_NAME} bytes")
                    continue
                if name == name_served:
                    continue
                if name[1] == DEFAULT_SUN_NAME:
                    # Never a rename: a running client writes `Unnamed` into
                    # every SUN the blob left empty, and the default must not
                    # erase a name a player set. Skipped without a note.
                    continue
                # The majority is of the settled planets only, as the client
                # allows: the only settled planet in a system may name it.
                held = owners.get(mine, 0)
                settled = sum(owners.values())
                if held * 2 <= settled:
                    drop(f"system {sun_oid}: rename DROPPED, {civ_name} "
                         f"holds {held} of the {settled} settled planet(s) in "
                         f"a system of {total}, not a majority")
                    continue
                ok, why = name_acceptable(name[1])
                if not ok:
                    drop(f"system {sun_oid}: rename DROPPED, {why}")
                    continue
                blob = set_system_name(blob, sun_oid, name[1])
                log(f"    system {sun_oid}: renamed "
                    f"{name_served[1].decode('latin1')!r} -> "
                    f"{name[1].decode('latin1')!r}")
                accepted += 1

            # research topic
            theirs = research_of(sub, mine)
            topic_served = served_research.get(mine)
            if (theirs is not None and topic_served is not None
                    and theirs != topic_served):
                blob = set_research(blob, mine, theirs)
                topic = struct.unpack_from('<I', theirs[0], 0)[0]
                log(f"    research: topic taken (id {topic})")
                accepted += 1

            # The one-time setup prompts' confirms, against the state served.
            taken, refusals = setup_orders(served, sub, mine, served_planets,
                                           planet_index(sub))
            for why in refusals:
                drop(why)
            for kind, detail in taken:
                if kind == 'civ':
                    blob = set_traits(blob, mine, detail)
                    log(f"    civilization traits taken: {list(detail)}")
                else:
                    oid, plpr = detail
                    blob = set_homeworld(blob, oid, plpr)
                    rates, space = homeworld_fields(plpr)
                    log(f"    homeworld {oid} customised: rates {rates}, "
                        f"space {space}")
                blob = set_setup_flag(blob, mine, kind)
                accepted += 1

            # The civ name and coat of arms, once, against the state served
            # and the names already taken in the galaxy being built.
            chosen, refusals = identity_orders(
                served, sub, blob, mine, names, (arms or {}).get(civ_name, ()))
            for why in refusals:
                drop(why)
            if chosen is not None:
                blob = set_civ_identity(blob, mine, *chosen)
                name, reveal, coat = chosen
                log(f"    civilization name taken: "
                    f"{name.decode('latin1')!r}"
                    f"{', player name revealed' if reveal else ''}"
                    f"{f', coat of arms {coat}' if coat != NO_ARMS else ''}")
                accepted += 1

            # Governors, against the state served. A new one takes a free id,
            # and the planets-governed counts follow the links as merged.
            new_govs, edited, deleted, links, refusals = governor_orders(
                served, sub, mine, served_planets, names)
            for why in refusals:
                drop(why)
            if new_govs or edited or deleted or links:
                records = dict(governors_of(blob, mine))
                renumbered = {}
                for oid, record in edited:
                    records[oid] = record
                    log(f"    governor {oid}: "
                        f"{read_governor(record)[0].decode('latin1')!r} edited")
                    accepted += 1
                for was, record in new_govs:
                    oid = was
                    if oid <= idg.max_object_id(blob):
                        # Taken in the galaxy being built, by another player's
                        # design, governor or production queue.
                        oid = next_object_id(blob)
                        renumbered[was] = oid
                    blob = set_high_water(blob, oid)
                    records[oid] = with_governor_id(record, oid)
                    log(f"    governor {oid}: "
                        f"{read_governor(record)[0].decode('latin1')!r} taken"
                        + (f", submitted as {was} and renumbered"
                           if oid != was else ""))
                    accepted += 1
                for planet_oid, gov in links:
                    gov = renumbered.get(gov, gov)
                    blob = set_governor_link(blob, planet_oid, gov)
                    log(f"    planet {planet_oid}: governor {gov} assigned")
                    accepted += 1
                for oid in deleted:
                    del records[oid]
                    log(f"    governor {oid}: deleted")
                    accepted += 1
                governs = collections.Counter(governor_links(blob).values())
                blob = set_governors(blob, mine, [
                    with_governed(r, governs[oid]) for oid, r in records.items()])

            # An admiral is deleted only once no ship names it as merged, and
            # each counts the ships and fleets that do.
            merged_adms = admirals_of(blob, mine)
            if merged_adms:
                commands = admiral_commands(blob, mine)
                records = []
                for oid, record in merged_adms:
                    if oid in deleted_adms:
                        if commands[oid]:
                            drop(f"{admiral_label(oid, record)} deletion "
                                 f"DROPPED, it still commands {commands[oid]} "
                                 f"ship(s) or fleet(s)")
                        else:
                            log(f"    {admiral_label(oid, record)}: deleted")
                            accepted += 1
                            continue
                    records.append(_with_ships(record, commands[oid]))
                if records != [r for _oid, r in merged_adms]:
                    blob = set_admirals(blob, mine, records)

            # Diplomacy, judged against the state served and applied to the
            # galaxy being built, both parties at once.
            orders, refusals = diplomacy_orders(served, sub, mine, names)
            for why in refusals:
                drop(why)
            for step, oid, record in orders:
                blob, line, why, _oid = take_treaty_step(
                    blob, served_treaties, step, oid, record, mine,
                    icv.turn_of(served), lambda c: names.get(c, c))
                if why:
                    drop(why)
                else:
                    log(f"    {line}")
                    accepted += 1

            # The submitter's own display choices for each civ it has met.
            shown, refusals = display_orders(served, sub, mine, names)
            for why in refusals:
                drop(why)
            for other, raw in shown:
                blob = set_display(blob, mine, other, raw)
                log(f"    display of {names.get(other, other)}: "
                    f"{describe_display(raw)}")
                accepted += 1

            # The submitter's end-galaxy vote, which nothing acts on.
            vote, refusals = vote_orders(served, sub, mine)
            for why in refusals:
                drop(why)
            if vote is not None:
                blob = set_end_vote(blob, mine, vote)
                log(f"    end-galaxy vote: {'cast' if vote else 'withdrawn'}")
                accepted += 1

            # News: messages to other civs, and its own items marked
            # important or read.
            orders, refusals = message_orders(served, sub, mine, names)
            for why in refusals:
                drop(why)
            blob, refusals, lines = take_messages(
                blob, mine, orders, icv.turn_of(served), names)
            for why in refusals:
                drop(why)
            for line in lines:
                log(f"    {line}")
                accepted += 1

            # Market trades, after the lump sums. Each is priced from the
            # market as served, the one the player traded against, and the
            # referee moves the credits, stocks and counts itself. Sales go
            # first, so their credits can pay for purchases.
            trades, refusals = market_orders(served, sub)
            for why in refusals:
                drop(why)
            credits, stock = credits_of(blob, mine), resources_of(blob, mine)
            if trades and (credits is None or stock is None):
                drop("market trades DROPPED, the civ's credits or resources "
                     "cannot be read")
                trades = []
            if trades:
                counts = market_of(blob)
                trait = market_trait(served_completed.get(mine, ()))
                for kind, units in sorted(trades, key=lambda t: t[1] < 0):
                    name = RESOURCE_NAMES[kind]
                    held = served_market[kind]
                    if units > 0:
                        if units > stock[kind]:
                            drop(f"market: sale of {units} {name} DROPPED, "
                                 f"{stock[kind]} held")
                            continue
                        paid = market_total(kind, units, held, trait)
                        credits += paid
                        log(f"    market: {units} {name} sold for {paid} "
                            f"credits")
                    else:
                        paid = market_total(kind, -units, held, trait,
                                            buying=True)
                        if paid > credits:
                            drop(f"market: purchase of {-units} {name} "
                                 f"DROPPED, {paid} credits needed and "
                                 f"{credits} held")
                            continue
                        credits -= paid
                        log(f"    market: {-units} {name} bought for {paid} "
                            f"credits")
                    stock[kind] -= units
                    counts[kind] += units
                    accepted += 1
                blob = set_credits(blob, mine, credits)
                blob = set_resources(blob, mine, stock)
                blob = set_market(blob, counts)

            # Research belonging to anyone else, against the state as served.
            for other in (o for o in icv.owner_records(blob)
                          if o['oid'] != mine):
                a = research_of(sub, other['oid'])
                b = served_research.get(other['oid'])
                if a is not None and b is not None and a != b:
                    drop(f"research: DROPPED, belongs to {other['name']}")
        except Exception as exc:                                # noqa: BLE001
            blob, accepted = before, kept
            drop(f"submission DROPPED, it could not be read, "
                 f"{type(exc).__name__}: {exc}; nothing from it was taken")

    log(f"{accepted} order(s) taken, {dropped} change(s) dropped")
    return blob


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('authoritative')
    ap.add_argument('--from', dest='subs', action='append', default=[],
                    metavar='CIV=FILE', help='a player submission; repeatable')
    ap.add_argument('--dat', help='write the decompressed result here')
    ap.add_argument('-o', '--out', help='write a .b64 capture here')
    ap.add_argument('--list', action='store_true',
                    help='show what the rules can see, change nothing')
    a = ap.parse_args()

    blob = sp.load_any(a.authoritative)
    names = {o['oid']: o['name'] for o in icv.owner_records(blob)}
    if a.list:
        for oid, (owner, dyno) in sorted(ship_index(blob).items()):
            print(f"  ship   {oid:<5} owner {names.get(owner, owner):<14} "
                  f"DYNO {len(dyno)} bytes "
                  f"{'(under orders)' if len(dyno) > 38 else '(idle)'}")
        for oid, (owner, prod, plpr, nm) in sorted(planet_index(blob).items()):
            if not owner:
                continue
            cz = citizens_of(plpr) if plpr else None
            label = nm[1].decode('latin1') if nm and nm[0] else '(unnamed)'
            print(f"  planet {oid:<5} owner {names.get(owner, owner):<14} "
                  f"{label!r:<20} PROD {len(prod) if prod else 'none'} bytes, "
                  f"{_tally([c[0] for c in cz]) if cz else 'no population'}")
            if cz and any(c[1] != owner for c in cz):
                # Each citizen in array order: job, owner, per-citizen value.
                print(' ' * 9 + ', '.join(
                    f"{JOB_IDS.get(j, j)} {names.get(o, o)} {v}"
                    for j, o, v, _r in cz))
        for oid, f in sorted(fleet_index(blob).items()):
            label = f['why'] or (f"{f['name'].decode('latin1')!r}, ships "
                                 f"{[m for m, _s, _v in f['members']]}")
            print(f"  fleet  {oid:<5} owner {names.get(f['owner'], f['owner']):<14} "
                  f"{label}")
        for o in icv.owner_records(blob):
            r = research_of(blob, o['oid'])
            topic = struct.unpack_from('<I', r[0], 0)[0] if r else None
            print(f"  civ    {o['oid']:<5} {o['name']:<14} "
                  f"research topic {topic}")
        return

    submissions = []
    for spec in a.subs:
        if '=' not in spec:
            raise SystemExit(f"--from wants CIV=FILE, got {spec!r}")
        civ_name, path = spec.split('=', 1)
        submissions.append((civ_name, sp.load_any(path)))
    if not submissions:
        raise SystemExit('nothing to merge; pass at least one --from')

    merged = merge(blob, submissions)
    sp.parse_blob(merged)
    print(f"merged blob: {len(merged):,} bytes (was {len(blob):,})")

    if a.dat:
        if not a.dat.lower().endswith('.dat'):
            raise SystemExit('--dat path must end in .dat')
        open(a.dat, 'wb').write(merged)
        print(f"wrote {a.dat}")
    if a.out:
        open(a.out, 'wb').write(sp.encode_save(merged))
        print(f"wrote {a.out}")


if __name__ == '__main__':
    main()
