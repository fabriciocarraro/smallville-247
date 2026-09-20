"""Agents: identity, memory, relationships. Kept deliberately small - every
byte here becomes input tokens, and the docs warn accuracy degrades with
context bloat."""
from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field

from .world import HOMES, PUBLIC, gather_spot, spot_key

FIRST = [
    "Iris", "Rafa", "Noor", "Emil", "Sasha", "Bo", "Lena", "Tariq", "Mira", "Joon",
    "Cleo", "Dara", "Ove", "Nadia", "Kofi", "Ines", "Yuri", "Alma", "Teo", "Ruth",
    "Sven", "Amara", "Luca", "Petra", "Kiran", "Vera", "Omar", "Faye", "Hugo", "Sol",
    "Nils", "Zora", "Ravi", "Elsa", "Pike", "Juno", "Odin", "Wren", "Cato", "Nyla",
    "Bram", "Isla", "Dmitri", "Suri", "Enzo", "Thea", "Malik", "Rosa", "Finn", "Ayla",
]
LAST = [
    "Vega", "Okonkwo", "Halloran", "Sato", "Brandt", "Duarte", "Ferro", "Novak",
    "Adeyemi", "Kessler", "Moreno", "Larkin", "Osei", "Renna", "Vlahos", "Dahl",
    "Barros", "Quill", "Ivanov", "Marchetti", "Nakamura", "Solberg", "Ferreira",
    "Whitlock", "Abara", "Pinto", "Grieve", "Ostrow", "Callahan", "Rue",
]
JOBS = [
    ("baker", "bakery"), ("barkeep", "tavern"), ("doctor", "clinic"),
    ("teacher", "school"), ("dockhand", "docks"), ("radio host", "radio"),
    ("librarian", "library"), ("trainer", "gym"), ("priest", "chapel"),
    ("mechanic", "workshop"), ("grocer", "market"), ("barista", "cafe"),
    ("gardener", "park"), ("attendant", "bathhouse"), ("courier", "square"),
]
TRAITS = [
    "blunt", "warm", "anxious", "ambitious", "loyal", "nosy", "proud", "generous",
    "jealous", "calm", "reckless", "secretive", "cheerful", "bitter", "curious",
    "stubborn", "tender", "cynical", "devout", "restless",
]
GOALS = [
    "wants to be trusted by everyone",
    "wants to win the election",
    "wants to leave town before winter",
    "wants to repair one broken friendship",
    "wants to be the first to know everything",
    "wants to be left alone",
    "wants to organise the Lantern Festival",
    "wants to find out who the stranger is",
    "wants to pay off a debt quietly",
    "wants someone specific to notice them",
]
SECRETS = [
    "took money from the festival fund",
    "is in love with someone who is taken",
    "saw who broke into the workshop",
    "is not who they said they were when they arrived",
    "is quietly sick and hiding it",
    "has been reading other people's mail",
    "plans to leave without telling anyone",
    "lied about where they were last night",
    "",  # most people have no secret
    "",
    "",
]

MOOD_LEVELS = ["low and withdrawn", "flat", "steady", "bright", "elated"]


@dataclass
class Agent:
    id: str
    name: str
    age: int
    job: str
    workplace: str
    home: str
    traits: list[str]
    goal: str
    secret: str
    at: str
    mood: float = 2.0
    energy: float = 1.0
    plan: str = "idle"
    plan_ticks: int = 0
    last_thought_tick: int = -999
    # physical position in tile coords - agents walk, they do not teleport
    px: float = 0.0
    py: float = 0.0
    facing: int = 1                 # 1 right, -1 left
    emote: str = ""                 # bubble the renderer floats over their head
    emote_tick: int = -999
    trust: dict[str, float] = field(default_factory=dict)         # id -> -1..1
    memory: deque = field(default_factory=lambda: deque(maxlen=5))
    believes: set[str] = field(default_factory=set)               # rumour ids
    thoughts: int = 0
    # --- the three life layers -----------------------------------------------
    partner: str | None = None                                    # agent id
    attraction: dict[str, float] = field(default_factory=dict)    # id -> 0..1
    affair: str | None = None                                     # id, if cheating
    affair_name: str = ""                                         # kept after it ends, for the reveal
    together_day: int = 0
    dead: bool = False
    died_day: int | None = None
    cause: str = ""
    grief: float = 0.0
    sick: bool = False
    arrived_day: int = 0

    def feel_drawn(self, other_id: str, delta: float) -> float:
        v = max(0.0, min(1.0, self.attraction.get(other_id, 0.0) + delta))
        self.attraction[other_id] = v
        # attraction rides in no request, but it still grows without bound in
        # memory; keep only what could plausibly still matter
        if len(self.attraction) > 12:
            for k, _ in sorted(self.attraction.items(), key=lambda kv: kv[1])[:4]:
                if k != self.partner and k != self.affair:
                    self.attraction.pop(k, None)
        return v

    # --- compact projections used to build request state ---------------------
    def one_line(self) -> str:
        bits = f"{self.age}, {self.job}, {'/'.join(self.traits)}"
        return f"{bits}; {self.goal}"

    def mood_word(self) -> str:
        return MOOD_LEVELS[max(0, min(4, int(round(self.mood))))]

    def remember(self, text: str) -> None:
        self.memory.append(text)

    def bond(self, other_id: str, delta: float) -> float:
        v = max(-1.0, min(1.0, self.trust.get(other_id, 0.0) + delta))
        self.trust[other_id] = v
        return v

    def closest(self, n: int = 3) -> list[tuple[str, float]]:
        return sorted(self.trust.items(), key=lambda kv: -kv[1])[:n]

    def worst(self) -> tuple[str, float] | None:
        if not self.trust:
            return None
        k = min(self.trust, key=self.trust.get)
        return (k, self.trust[k]) if self.trust[k] < -0.25 else None


def make_agent(aid: str, rng: random.Random, used: set[str],
               day: int = 0, newcomer: bool = False) -> Agent:
    for _ in range(80):
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        if name not in used:
            break
    else:
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)} {rng.randint(2, 99)}"
    used.add(name)

    job, workplace = rng.choice(JOBS)
    a = Agent(
        id=aid,
        name=name,
        age=rng.randint(18, 78),
        job=job,
        workplace=workplace,
        home=rng.choice(HOMES),
        traits=rng.sample(TRAITS, 2),
        goal=rng.choice(GOALS),
        secret=rng.choice(SECRETS),
        # newcomers walk in off the docks, which is also where the town's
        # "nobody knows who invited them" arc starts
        at="docks" if newcomer else rng.choice(PUBLIC + HOMES),
        mood=round(rng.uniform(1.4, 3.0), 2),
        arrived_day=day,
    )
    a.px, a.py = gather_spot(a.at, spot_key(aid, a.at))
    return a


def make_population(n: int = 300, seed: int = 7) -> dict[str, Agent]:
    rng = random.Random(seed)
    used: set[str] = set()
    agents = {f"a{i:03d}": make_agent(f"a{i:03d}", rng, used) for i in range(n)}

    # Seed a sparse social graph so the town starts with history, not strangers.
    ids = list(agents)
    for aid in ids:
        for other in rng.sample(ids, rng.randint(2, 5)):
            if other != aid:
                v = round(rng.uniform(-0.4, 0.7), 2)
                agents[aid].trust[other] = v
                agents[other].trust[aid] = round(v + rng.uniform(-0.2, 0.2), 2)

    # A town this age already has couples. Pair a few off so affairs have
    # something to betray from the first minute instead of hour six.
    singles = [a for a in agents.values() if a.age >= 22]
    rng.shuffle(singles)
    for i in range(0, min(len(singles) - 1, int(n * 0.34)), 2):
        x, y = singles[i], singles[i + 1]
        if x.partner or y.partner:
            continue
        x.partner, y.partner = y.id, x.id
        x.trust[y.id] = max(x.trust.get(y.id, 0), 0.7)
        y.trust[x.id] = max(y.trust.get(x.id, 0), 0.7)
        x.attraction[y.id] = y.attraction[x.id] = 0.8
    # A town this old already has stories in the air. Without a few, `gossip` is
    # an impossible action for everyone on day one, and the mill never starts:
    # no gossip means no rumours means nothing to gossip about.
    return agents
