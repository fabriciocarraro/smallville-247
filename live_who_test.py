"""
The town is live but nearly silent: ~366 agent-decisions produced 3 social events.

The action fix raised social choices to 62%, so the loss is downstream. Prime
suspect is the `who` question: if it answers "nobody", apply_scene turns a social
action into a move, and the interaction never happens. Same failure shape as the
`doing` echo - an option the model finds safe, chosen by default.

Variants tested live, ~12 billed calls:
  A  current: {name: job, ..., "nobody": "no one in particular"}
  B  richer descriptions per person, "nobody" kept but made explicit
  C  no "nobody" option at all - they are in a room with people, pick one
"""
from __future__ import annotations

import os
import sys
from collections import Counter

sys.path.insert(0, ".")
if not os.environ.get("TYPESAFE_API_KEY"):
    sys.exit("TYPESAFE_API_KEY not in environment")

from smallville.jev import Choice, RealClient
from smallville.scenes import build_scene_request
from smallville.sim import Simulation

sim = Simulation(n_agents=50, daily_budget_usd=2.0, seed=7, virtual_time=True)
for _ in range(600):
    sim.step()

scenes = []
for place in {a.at for a in sim.living()}:
    pool = [a for a in sim.living() if a.at == place]
    if len(pool) >= 5:
        scenes.append((place, pool[:7]))
    if len(scenes) == 4:
        break

client = RealClient()
spend = 0.0
out: dict[str, Counter] = {}


def who_criteria(a, present, variant):
    if variant == "A":
        c = {o.name: o.job for o in present if o.id != a.id}
        c["nobody"] = "no one in particular"
    elif variant == "B":
        c = {o.name: f"the {o.job}, {o.mood_word()}" for o in present if o.id != a.id}
        c["nobody"] = "keeps to themselves and addresses no one"
    else:
        c = {o.name: f"the {o.job}, {o.mood_word()}" for o in present if o.id != a.id}
    return c


for variant in ("A", "B", "C"):
    tally: Counter = Counter()
    for place, people in scenes:
        state, questions, _ = build_scene_request(place, people, sim.town, sim.rng, sim.agents)
        for a in people:
            key = f"{a.id}__who"
            if key in questions:
                questions[key] = Choice(
                    instructions=f"Who does {a.name} direct that at?",
                    criteria=who_criteria(a, people, variant))
        r = client.system_one(state=state, questions=questions)
        if not r.answers:
            print(f"  variant {variant}: call failed"); continue
        spend += r.cost_usd
        for k, ans in r.answers.items():
            if k.endswith("__who") and ans.choice:
                tally["nobody" if ans.choice == "nobody" else "a person"] += 1
    out[variant] = tally

print(f"spent: ${spend:.5f}\n")
print(f"{'variant':<44}{'a person':>10}{'nobody':>9}{'targeted%':>11}")
print("-" * 74)
labels = {"A": "A current ({name: job} + nobody)",
          "B": "B richer person descriptions + nobody",
          "C": "C no 'nobody' option at all"}
for v, t in out.items():
    n = sum(t.values()) or 1
    print(f"{labels[v]:<44}{t['a person']:>10}{t['nobody']:>9}{100*t['a person']/n:>10.0f}%")
print("\nEvery 'nobody' turns a social action into a move: the interaction is lost.")
