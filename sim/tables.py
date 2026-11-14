"""
Stat tables for the released content version, the one every galaxy below 639
selects. Ids are a record's position, which is what a design's part lists and
a planet's facility map store.

Generated from docs/CosmicSupremacy_Stat_Tables.md sections 3 and 4, which were
read out of the client. test_tables.py checks the two still agree.
"""
from dataclasses import dataclass

RESOURCES = ('metal', 'deuterium', 'radioactives', 'crystal', 'exotics')


@dataclass(frozen=True)
class Part:
    name: str
    units: int                      # hitpoint contribution
    space: int                      # used, or provided by a chassis
    cost: int                       # production points
    upkeep: int                     # credits per turn
    resources: tuple                # in RESOURCES order
    stat: int = 0                   # thrust, scanner range or shield strength
    fp: tuple = (0, 0, 0)           # firepower vs light ships, heavy ships, planets
    crew: tuple = (0, 0)            # a chassis's crew minimum and maximum


@dataclass(frozen=True)
class Facility:
    name: str
    tech: int                       # the technology that unlocks it
    space: int
    cost: int
    upkeep: int
    once: bool                      # at most one per planet


ENGINES = (
    Part(name='Nuclear Drive', units=5, space=10, cost=120, upkeep=1, resources=(4, 0, 6, 0, 0), stat=270),
    Part(name='Fusion Drive', units=10, space=20, cost=450, upkeep=2, resources=(5, 15, 0, 0, 0), stat=820),
    Part(name='Quantum Drive', units=40, space=80, cost=2400, upkeep=8, resources=(40, 0, 0, 40, 0), stat=4450),
    Part(name='Gravity Drive', units=70, space=120, cost=4500, upkeep=12, resources=(30, 0, 90, 0, 0), stat=9200),
    Part(name='Anti-Matter Drive', units=20, space=30, cost=1300, upkeep=3, resources=(0, 20, 0, 10, 0), stat=3200),
    Part(name='Dark-Matter Drive', units=50, space=60, cost=3000, upkeep=7, resources=(0, 0, 50, 0, 10), stat=9000),
    Part(name='Singularity Drive', units=280, space=280, cost=16000, upkeep=35, resources=(100, 0, 0, 0, 180), stat=60000),
)

WEAPONS = (
    Part(name='Mass Driver', units=12, space=20, cost=300, upkeep=2, resources=(20, 0, 0, 0, 0), fp=(35, 10, 0)),
    Part(name='Pulse Laser', units=6, space=10, cost=180, upkeep=1, resources=(4, 0, 6, 0, 0), fp=(30, 0, 0)),
    Part(name='Beam Laser', units=48, space=80, cost=1900, upkeep=10, resources=(30, 50, 0, 0, 0), fp=(285, 50, 0)),
    Part(name='Proton Laser', units=30, space=50, cost=1500, upkeep=7, resources=(10, 0, 40, 0, 0), fp=(240, 50, 0)),
    Part(name='Photon Cannon', units=24, space=30, cost=600, upkeep=4, resources=(30, 0, 0, 0, 0), fp=(20, 50, 0)),
    Part(name='Ion-Pulse Cannon', units=96, space=120, cost=3250, upkeep=18, resources=(40, 80, 0, 0, 0), fp=(125, 350, 0)),
    Part(name='Proton Torpedo', units=64, space=80, cost=2650, upkeep=13, resources=(20, 0, 60, 0, 0), fp=(0, 435, 0)),
    Part(name='Anti-Matter Torpedo', units=32, space=40, cost=1350, upkeep=6, resources=(10, 30, 0, 0, 0), fp=(0, 260, 0)),
    Part(name='Particle Cannon', units=160, space=200, cost=8700, upkeep=39, resources=(0, 140, 0, 60, 0), fp=(500, 1550, 0)),
    Part(name='Wormhole Infiltrator', units=48, space=60, cost=3000, upkeep=13, resources=(0, 0, 30, 0, 30), fp=(0, 850, 0)),
    Part(name='Fusion Bomb', units=50, space=50, cost=750, upkeep=4, resources=(10, 40, 0, 0, 0), fp=(0, 0, 500)),
    Part(name='Plasma Bomb', units=200, space=200, cost=3550, upkeep=20, resources=(0, 40, 160, 0, 0), fp=(0, 0, 2700)),
    Part(name='Anti-Matter Bomb', units=80, space=80, cost=1650, upkeep=10, resources=(0, 60, 0, 20, 0), fp=(0, 0, 1350)),
    Part(name='Dark-Matter Bomb', units=120, space=120, cost=3000, upkeep=18, resources=(0, 0, 90, 0, 30), fp=(0, 0, 2700)),
    Part(name='Planet Buster', units=250, space=250, cost=7500, upkeep=43, resources=(0, 0, 0, 50, 200), fp=(0, 0, 7500)),
)

CHASSIS = (
    Part(name='Shuttle', units=20, space=120, cost=225, upkeep=2, resources=(60, 0, 0, 0, 0), crew=(1, 2)),
    Part(name='Corvette', units=80, space=210, cost=600, upkeep=4, resources=(105, 0, 0, 0, 0), crew=(1, 3)),
    Part(name='Frigate', units=180, space=400, cost=1950, upkeep=12, resources=(200, 0, 0, 0, 0), crew=(1, 4)),
    Part(name='Destroyer', units=400, space=650, cost=5700, upkeep=20, resources=(200, 0, 0, 125, 0), crew=(3, 7)),
    Part(name='Cruiser', units=900, space=1050, cost=10800, upkeep=45, resources=(275, 0, 0, 250, 0), crew=(4, 10)),
    Part(name='Battleship', units=1800, space=1500, cost=27000, upkeep=100, resources=(300, 0, 0, 450, 0), crew=(5, 14)),
)

MODULES = (
    Part(name='Colony Module', units=20, space=90, cost=150, upkeep=4, resources=(90, 0, 0, 0, 0)),
    Part(name='Troop Bay', units=30, space=90, cost=450, upkeep=10, resources=(90, 0, 0, 0, 0)),
    Part(name='Large Pilot Cabin', units=50, space=100, cost=600, upkeep=10, resources=(100, 0, 0, 0, 0)),
    Part(name='Cloaking Device', units=20, space=30, cost=2400, upkeep=5, resources=(0, 0, 0, 90, 0)),
    Part(name='Bio Bombs', units=100, space=100, cost=4200, upkeep=14, resources=(0, 0, 100, 0, 100)),
    Part(name='Wormhole Generator', units=300, space=200, cost=30000, upkeep=12, resources=(0, 80, 100, 100, 1000)),
)

SCANNERS = (
    Part(name='Neutron Scanner', units=5, space=0, cost=100, upkeep=1, resources=(2, 0, 0, 0, 0), stat=30),
    Part(name='Tachyon Scanner', units=10, space=10, cost=300, upkeep=2, resources=(2, 0, 8, 0, 0), stat=50),
    Part(name='Subspace Scanner', units=15, space=20, cost=600, upkeep=5, resources=(0, 0, 0, 20, 0), stat=70),
    Part(name='Hyperspace Scanner', units=30, space=20, cost=1200, upkeep=10, resources=(10, 0, 0, 0, 10), stat=100),
)

SHIELDS = (
    Part(name='Magneto Shield', units=20, space=20, cost=450, upkeep=2, resources=(20, 0, 0, 0, 0), stat=10),
    Part(name='Quantum Shield', units=50, space=30, cost=1000, upkeep=4, resources=(0, 0, 0, 30, 0), stat=25),
    Part(name='Anti-Grav Shield', units=80, space=40, cost=1650, upkeep=6, resources=(0, 0, 40, 0, 0), stat=50),
    Part(name='Gaussian Shield', units=350, space=140, cost=7350, upkeep=24, resources=(80, 0, 0, 60, 0), stat=250),
    Part(name='Warp Shield', units=200, space=80, cost=5100, upkeep=15, resources=(0, 0, 60, 0, 20), stat=200),
)


FACILITIES = (
    Facility(name='Farm', tech=1, space=7, cost=200, upkeep=7, once=False),
    Facility(name='Factory', tech=2, space=7, cost=400, upkeep=10, once=False),
    Facility(name='Shipyard', tech=1, space=25, cost=500, upkeep=17, once=True),
    Facility(name='Automated Factory', tech=40, space=11, cost=1800, upkeep=18, once=False),
    Facility(name='University', tech=18, space=9, cost=700, upkeep=16, once=False),
    Facility(name='Science Lab', tech=19, space=15, cost=2200, upkeep=24, once=False),
    Facility(name='Military Camp', tech=18, space=14, cost=1500, upkeep=12, once=False),
    Facility(name='Military Academy', tech=20, space=34, cost=9000, upkeep=28, once=True),
    Facility(name='Central Defense Agency', tech=23, space=26, cost=6000, upkeep=30, once=True),
    Facility(name='Light Turret', tech=27, space=3, cost=600, upkeep=8, once=False),
    Facility(name='Heavy Turret', tech=30, space=6, cost=3000, upkeep=32, once=False),
    Facility(name='Shield Generator', tech=29, space=14, cost=2000, upkeep=45, once=False),
    Facility(name='Propaganda Office', tech=36, space=9, cost=1200, upkeep=12, once=False),
    Facility(name='Mine', tech=35, space=6, cost=600, upkeep=13, once=False),
    Facility(name='Robo Mine', tech=37, space=12, cost=1300, upkeep=20, once=False),
    Facility(name='Bunker', tech=38, space=5, cost=700, upkeep=10, once=False),
    Facility(name='Banking Center', tech=39, space=12, cost=1800, upkeep=17, once=False),
    Facility(name='Planetary Fortress', tech=51, space=8, cost=1200, upkeep=15, once=False),
    Facility(name='Hyperspace Transmitter', tech=69, space=9, cost=2500, upkeep=20, once=False),
    Facility(name='Hyperspace Receiver', tech=69, space=16, cost=8000, upkeep=50, once=True),
    Facility(name='Command Center', tech=78, space=10, cost=1500, upkeep=13, once=False),
)

LIGHT_CHASSIS = frozenset((0, 1, 2))        # 3 to 5 are heavy

# A design's part lists, in the order its SDPR stores them.
CATEGORIES = ('chassis', 'scanners', 'engines', 'weapons', 'modules', 'shields')
TABLES = {'chassis': CHASSIS, 'scanners': SCANNERS, 'engines': ENGINES,
          'weapons': WEAPONS, 'modules': MODULES, 'shields': SHIELDS}
