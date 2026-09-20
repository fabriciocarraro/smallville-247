"""The town: places, clock, public events and rumours."""
from __future__ import annotations

import random
import zlib
from dataclasses import dataclass, field

# x/y are 0-1 of the tile map. Laid out as an actual town: harbour along the
# north shore, civic core around the square, homes and green space to the south.
LOCATIONS: dict[str, dict] = {
    "docks":     {"label": "North Docks",   "x": 0.50, "y": 0.145, "vibe": "cold, working, deals made out of sight"},
    "north_flats": {"label": "North Flats", "x": 0.205, "y": 0.165, "vibe": "homes, doors thin enough to hear through"},
    "radio":     {"label": "Radio Verde",   "x": 0.785, "y": 0.150, "vibe": "the town hears whatever is said here"},
    "bakery":    {"label": "Dawn Bakery",   "x": 0.348, "y": 0.232, "vibe": "early, sweet, the first news of the day"},
    "library":   {"label": "Library",       "x": 0.915, "y": 0.275, "vibe": "hushed, records and old newspapers"},
    "hill":      {"label": "Hill Overlook", "x": 0.085, "y": 0.320, "vibe": "isolated, you go there to be alone or unseen"},
    "cafe":      {"label": "Ember Cafe",    "x": 0.370, "y": 0.355, "vibe": "warm, gossipy, small tables close together"},
    "tavern":    {"label": "The Anchor",    "x": 0.635, "y": 0.330, "vibe": "loud, crowded, drink loosens tongues"},
    "gym":       {"label": "Iron Room",     "x": 0.830, "y": 0.430, "vibe": "competitive, sweaty, blunt talk"},
    "market":    {"label": "Market Row",    "x": 0.270, "y": 0.460, "vibe": "busy, transactional, haggling"},
    "square":    {"label": "Town Square",   "x": 0.505, "y": 0.470, "vibe": "open, public, everyone passes through"},
    "clinic":    {"label": "Clinic",        "x": 0.130, "y": 0.545, "vibe": "tired, private, people tell the truth here"},
    "chapel":    {"label": "Old Chapel",    "x": 0.705, "y": 0.575, "vibe": "quiet, solemn, confessions are kept"},
    "workshop":  {"label": "Workshop",      "x": 0.905, "y": 0.625, "vibe": "focused, tools, things get repaired"},
    "park":      {"label": "Willow Park",   "x": 0.455, "y": 0.660, "vibe": "calm, green, couples and dogs"},
    "bathhouse": {"label": "Bathhouse",     "x": 0.215, "y": 0.715, "vibe": "relaxed, guards come down"},
    "school":    {"label": "Schoolhouse",   "x": 0.690, "y": 0.775, "vibe": "structured, noisy at the edges"},
    "south_flats": {"label": "South Flats", "x": 0.430, "y": 0.845, "vibe": "homes, quiet at night"},
}

# Roads the renderer paves and the town is laid out around.
ROADS: list[tuple[str, str]] = [
    ("square", "cafe"), ("square", "tavern"), ("square", "market"),
    ("square", "park"), ("square", "chapel"), ("square", "gym"),
    ("cafe", "bakery"), ("bakery", "docks"), ("bakery", "north_flats"),
    ("tavern", "docks"), ("tavern", "radio"), ("radio", "library"),
    ("library", "gym"), ("gym", "workshop"), ("chapel", "workshop"),
    ("chapel", "school"), ("park", "school"), ("park", "south_flats"),
    ("park", "bathhouse"), ("bathhouse", "clinic"), ("clinic", "market"),
    ("market", "hill"), ("hill", "north_flats"),
]
SHORE_Y = 0.088          # everything above this is water

HOMES = ["north_flats", "south_flats"]
PUBLIC = [k for k in LOCATIONS if k not in HOMES]

# --- physical town -----------------------------------------------------------
# The map is a tile grid. Locations are buildings on it with a footprint and a
# plaza in front where people gather, so agents stand somewhere real instead of
# collapsing onto a single point.
MAP_W, MAP_H = 128, 80

BUILDINGS: dict[str, dict] = {
    # style drives how the client draws it; w/h are the footprint in tiles
    "square":      {"w": 0,  "h": 0,  "style": "plaza",   "plaza": 7.5},
    "cafe":        {"w": 9,  "h": 7,  "style": "shop",    "roof": "#b8563f"},
    "tavern":      {"w": 11, "h": 8,  "style": "tavern",  "roof": "#8c4a2f"},
    "market":      {"w": 12, "h": 6,  "style": "stalls",  "roof": "#c07a3e"},
    "chapel":      {"w": 9,  "h": 11, "style": "chapel",  "roof": "#5b6b8a"},
    "park":        {"w": 0,  "h": 0,  "style": "park",    "plaza": 8.5},
    "docks":       {"w": 14, "h": 6,  "style": "docks",   "roof": "#6b6257"},
    "radio":       {"w": 8,  "h": 9,  "style": "tower",   "roof": "#7a5c8a"},
    "library":     {"w": 11, "h": 9,  "style": "civic",   "roof": "#4f6b78"},
    "gym":         {"w": 9,  "h": 7,  "style": "shop",    "roof": "#96602f"},
    "clinic":      {"w": 9,  "h": 7,  "style": "civic",   "roof": "#7d8f96"},
    "school":      {"w": 12, "h": 8,  "style": "civic",   "roof": "#a8553f"},
    "bakery":      {"w": 8,  "h": 7,  "style": "shop",    "roof": "#c98c42"},
    "workshop":    {"w": 10, "h": 7,  "style": "shop",    "roof": "#6f6a5e"},
    "hill":        {"w": 0,  "h": 0,  "style": "hill",    "plaza": 6.0},
    "north_flats": {"w": 13, "h": 9,  "style": "flats",   "roof": "#8a6a52"},
    "south_flats": {"w": 13, "h": 9,  "style": "flats",   "roof": "#7a6250"},
    "bathhouse":   {"w": 10, "h": 8,  "style": "bath",    "roof": "#5f8a86"},
}


def tile_center(place: str) -> tuple[float, float]:
    loc = LOCATIONS[place]
    return loc["x"] * MAP_W, loc["y"] * MAP_H


def spot_key(agent_id: str, place: str) -> int:
    """Which standing spot a person takes in a place.

    Keyed on identity, NEVER on position in a list: an earlier version used the
    enumerate() index over the living, so a single death shifted every later
    index and 44 of 49 people visibly walked to a new spot.
    """
    return (zlib.crc32(agent_id.encode()) * 31 + zlib.crc32(place.encode())) % 64


def gather_spot(place: str, key: int) -> tuple[float, float]:
    """A stable standing spot in the plaza in front of a building.

    Deterministic in `key` so a person keeps their place instead of jittering
    every tick, and spread on a phyllotaxis spiral so crowds look like crowds
    rather than a ring.
    """
    import math

    cx, cy = tile_center(place)
    b = BUILDINGS[place]
    plaza = b.get("plaza", 0.0)
    radius = plaza or (max(b["w"], b["h"]) * 0.42 + 3.0)
    # Open squares have something at the centre (the well, the pond), so people
    # ring them instead of standing on top. Buildings need no such hole.
    inner = 3.0 if plaza else 0.0
    i = key % 64
    a = i * 2.399963229728653                      # golden angle
    r = inner + (radius - inner) * math.sqrt((i + 0.6) / 64)
    # bias downward: people stand in front of a building, not inside its roof
    return cx + r * math.cos(a), cy + r * math.sin(a) * 0.72 + (b["h"] * 0.30)

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@dataclass
class Clock:
    minutes: int = 7 * 60          # start at 07:00 day 1
    minutes_per_tick: int = 2

    def tick(self) -> None:
        self.minutes += self.minutes_per_tick

    @property
    def day(self) -> int:
        return self.minutes // 1440

    @property
    def hour(self) -> int:
        return (self.minutes % 1440) // 60

    @property
    def minute(self) -> int:
        return self.minutes % 60

    @property
    def phase(self) -> str:
        h = self.hour
        if h < 6:
            return "night"
        if h < 11:
            return "morning"
        if h < 15:
            return "midday"
        if h < 19:
            return "afternoon"
        if h < 23:
            return "evening"
        return "night"

    def label(self) -> str:
        return f"{DAYS[self.day % 7]} day {self.day + 1}, {self.hour:02d}:{self.minute:02d}"


@dataclass
class Rumour:
    id: str
    text: str
    origin: str
    believers: set[str] = field(default_factory=set)
    born_day: int = 0
    true: bool = False
    history: list = field(default_factory=list)      # (tick, reach) for the spread chart
    about: set = field(default_factory=set)          # agent ids the story concerns
    kind: str = "gossip"                             # gossip | affair | death
    resolved: bool = False                           # an affair story breaks only once

    @property
    def reach(self) -> int:
        return len(self.believers)


@dataclass
class Event:
    tick: int
    time_label: str
    place: str
    kind: str                       # talk | conflict | bond | rumour | move | town
    text: str
    actors: list[str] = field(default_factory=list)
    heat: float = 0.0               # drives clip-worthiness ranking


# Town-level storylines that give agents something to collide over.
TOWN_ARCS = [
    {"id": "election",  "text": "The town votes for a new mayor in six days. Vega and Okonkwo are both running."},
    {"id": "shortage",  "text": "The North Docks shipment did not arrive. The market is short on supplies."},
    {"id": "newcomer",  "text": "A stranger arrived last night and nobody knows who invited them."},
    {"id": "festival",  "text": "The Lantern Festival is being organised for the end of the week, if anyone organises it."},
]


class Town:
    def __init__(self, seed: int = 7):
        self.rng = random.Random(seed)
        self.clock = Clock()
        self.events: list[Event] = []
        self.rumours: dict[str, Rumour] = {}
        self.arcs = list(TOWN_ARCS)
        self._rumour_n = 0
        self.graves: list[dict] = []          # {name, day} - drawn beside the chapel
        self.chronicle: list[dict] = []       # weddings, affairs, deaths: the recap reel

    def log(self, ev: Event) -> None:
        self.events.append(ev)
        if len(self.events) > 4000:
            del self.events[:1000]

    def recent(self, n: int = 12, place: str | None = None) -> list[Event]:
        pool = [e for e in self.events if place is None or e.place == place]
        return pool[-n:]

    def headlines(self, n: int = 2) -> list[str]:
        out = [a["text"] for a in self.arcs[:1]]
        hot = sorted(self.rumours.values(), key=lambda r: -r.reach)[:n]
        out += [f'People are saying: "{r.text}" ({r.reach} believe it)' for r in hot if r.reach > 2]
        return out[:n + 1]

    def new_rumour(self, text: str, origin: str, true: bool = False,
                   about: set | None = None, kind: str = "gossip") -> Rumour:
        self._rumour_n += 1
        r = Rumour(id=f"r{self._rumour_n}", text=text, origin=origin,
                   believers={origin}, born_day=self.clock.day, true=true,
                   about=set(about or ()), kind=kind)
        self.rumours[r.id] = r
        return r
