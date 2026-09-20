"""
Does trimming the action menu to what each person can actually do change what
the live model picks?

An hour of live observation produced 114 events, every one of them `talk` at
heat 0.20 - no gossip, no confiding, no conflict, so no rumours, no affairs and
nothing for the narrator to say. The suspicion: the menu offered impossible
actions (gossip to someone who believes no rumour) and the model answered with
the only option it could always justify.

This asks the live model the same scenes both ways. ~16 billed calls.
"""
from __future__ import annotations

import os
import sys
from collections import Counter

sys.path.insert(0, ".")
if not os.environ.get("OPENAI_API_KEY") and not os.environ.get("TYPESAFE_API_KEY"):
    sys.exit("TYPESAFE_API_KEY not in this process's environment")

from smallville.jev import Choice, RealClient
from smallville.scenes import ACTIONS, _menu, build_scene_request
from smallville.sim import Simulation

sim = Simulation(n_agents=50, daily_budget_usd=2.0, seed=7, virtual_time=True)
for _ in range(900):                     # a day and a bit, as the live town was
    sim.step()

scenes = []
for place in {a.at for a in sim.living()}:
    pool = [a for a in sim.living() if a.at == place]
    if len(pool) >= 5:
        scenes.append((place, pool[:8]))
    if len(scenes) == 6:
        break

client = RealClient()
results: dict[str, Counter] = {}
spend = 0.0

for label, trimmed in (("full menu (every action, always)", False),
                       ("trimmed menu (only what they can do)", True)):
    tally: Counter = Counter()
    for place, people in scenes:
        state, questions, _ = build_scene_request(place, people, sim.town, sim.rng, sim.agents)
        if not trimmed:
            for a in people:
                k = f"{a.id}__act"
                if k in questions:
                    questions[k] = Choice(instructions=questions[k].instructions,
                                          criteria=ACTIONS)
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
print(f"{'menu':<38}{head}   not-talk")
print("-" * (38 + len(head) + 11))
for label, t in results.items():
    n = sum(t.values()) or 1
    other = n - t["talk"]
    print(f"{label:<38}" + "".join(f"{t.get(o, 0):>9}" for o in order)
          + f"   {100*other/n:>5.0f}%")

print("\nThe town needs gossip, confiding and conflict specifically: those are what")
print("create rumours, expose affairs and give the narrator something to say.")
for label, t in results.items():
    dramatic = t["gossip"] + t["confide"] + t["confront"]
    n = sum(t.values()) or 1
    print(f"  {label:<38} dramatic actions: {dramatic}/{n} ({100*dramatic/n:.0f}%)")
