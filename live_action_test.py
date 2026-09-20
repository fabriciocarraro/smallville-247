"""
Why does the real model answer `withdraw` 79% of the time?

Calibration showed a degenerate action distribution: 63 of 80 agents chose
withdraw, 2 chose talk, none gossiped, helped or confronted. A town like that is
silent - no interactions means no rumours, no couples, no drama at all. The mock
never showed this because it picks by lexical affinity, not by reading.

Two suspects, tested against the live model rather than argued about:
  A  criteria were trimmed hard to save tokens (the 49% cut). Too terse to
     discriminate?
  B  the state tells the model what each person is already `doing`, and half of
     them are "keeping to themselves" - which is withdraw, restated. Is the
     model just echoing the status quo back?

~18 billed calls, under a cent.
"""
from __future__ import annotations

import copy
import os
import sys
from collections import Counter

sys.path.insert(0, ".")
if not os.environ.get("TYPESAFE_API_KEY"):
    sys.exit("TYPESAFE_API_KEY not in environment")

from smallville.jev import Choice, RealClient
from smallville.scenes import build_scene_request
from smallville.sim import Simulation

TERSE = {
    "talk":     "friendly, casual, no agenda",
    "confront": "angry, public, about a grievance",
    "gossip":   "passes on news or a rumour",
    "confide":  "shares something private, needs trust",
    "help":     "practical act of service",
    "work":     "ignores the room, does their job",
    "withdraw": "silent, avoids everyone",
    "leave":    "walks out to another place",
}

RICH = {
    "talk":     "starts an ordinary conversation with someone in the room",
    "confront": "challenges someone out loud about something they resent",
    "gossip":   "repeats news or a rumour about a third person to someone here",
    "confide":  "tells one trusted person something private about themselves",
    "help":     "does something practical for another person here",
    "work":     "gets on with their own job and speaks to no one",
    "withdraw": "deliberately stays apart because they want to be left alone",
    "leave":    "walks out of this place entirely",
}

sim = Simulation(n_agents=50, daily_budget_usd=2.0, seed=7, virtual_time=True)
for _ in range(600):
    sim.step()

client = RealClient()
scenes = []
for place in {a.at for a in sim.living()}:
    pool = [a for a in sim.living() if a.at == place]
    if len(pool) >= 6:
        scenes.append((place, pool[:8]))
    if len(scenes) == 6:
        break

VARIANTS = {
    "A terse criteria (current)":        dict(rich=False, drop_doing=False, reframe=False),
    "B richer criteria":                 dict(rich=True,  drop_doing=False, reframe=False),
    "C richer + no 'doing' echo":        dict(rich=True,  drop_doing=True,  reframe=False),
    "D richer + no echo + reframed ask": dict(rich=True,  drop_doing=True,  reframe=True),
}

spend = 0.0
results: dict[str, Counter] = {}

for label, cfg in VARIANTS.items():
    tally: Counter = Counter()
    for place, people in scenes:
        state, questions, _ = build_scene_request(place, people, sim.town, sim.rng, sim.agents)
        state = copy.deepcopy(state)
        if cfg["drop_doing"]:
            for p in state["people_here"]:
                p.pop("doing", None)
        crit = RICH if cfg["rich"] else TERSE
        for a in people:
            key = f"{a.id}__act"
            if key not in questions:
                continue
            instr = (f"{a.name} decides how to spend the next few minutes here. "
                     f"What do they do?") if cfg["reframe"] else \
                    f"What does {a.name} do next in this room?"
            questions[key] = Choice(instructions=instr, criteria=crit)
        r = client.system_one(state=state, questions=questions)
        if not r.answers:
            print(f"  {label}: request failed"); continue
        spend += r.cost_usd
        for k, ans in r.answers.items():
            if k.endswith("__act") and ans.choice:
                tally[ans.choice] += 1
    results[label] = tally

print(f"spent: ${spend:.5f}\n")
order = ["talk", "gossip", "confide", "help", "confront", "work", "withdraw", "leave"]
head = "".join(f"{o[:8]:>9}" for o in order)
print(f"{'variant':<34}{head}   social%")
print("-" * (34 + len(head) + 10))
for label, t in results.items():
    n = sum(t.values()) or 1
    social = sum(t[k] for k in ("talk", "gossip", "confide", "help", "confront"))
    row = "".join(f"{t.get(o, 0):>9}" for o in order)
    print(f"{label:<34}{row}   {100*social/n:>5.0f}%")

print("\n'social%' is what the whole simulation runs on: no interaction, no story.")
