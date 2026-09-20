"""
Is Jev deterministic? Same scene, same questions, asked twice.

It matters for understanding the town. If identical state gives identical
answers, then nothing about a person's behaviour is random - every difference
between two people, or between the same person at two moments, comes from a
difference in what the model was told. And then "why did she do that" always has
an answer you can read.

~6 billed calls.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, ".")
if not os.environ.get("TYPESAFE_API_KEY"):
    sys.exit("TYPESAFE_API_KEY not in this process's environment")

from smallville.jev import RealClient
from smallville.scenes import build_scene_request
from smallville.sim import Simulation

sim = Simulation(n_agents=50, daily_budget_usd=2.0, seed=7, virtual_time=True)
for _ in range(2600):
    sim.step()

by: dict = {}
for a in sim.living():
    by.setdefault(a.at, []).append(a)
place, people = max(by.items(), key=lambda kv: len(kv[1]))
people = people[:6]

client = RealClient()
state, questions, _ = build_scene_request(place, people, sim.town, sim.rng, sim.agents)

runs = []
for i in range(3):
    r = client.system_one(state=state, questions=questions)
    if not r.answers:
        sys.exit("call failed")
    runs.append(r)

a, b, c = runs
keys = sorted(a.answers)


def value(ans):
    if ans.choice is not None:
        return ans.choice
    if ans.score is not None:
        return round(ans.score, 3)
    return round(ans.noul, 4)


same_ab = sum(1 for k in keys if value(a.answers[k]) == value(b.answers[k]))
same_ac = sum(1 for k in keys if value(a.answers[k]) == value(c.answers[k]))
print(f"{len(keys)} questions, the exact same request sent three times\n")
print(f"  run 1 vs run 2: {same_ab}/{len(keys)} identical")
print(f"  run 1 vs run 3: {same_ac}/{len(keys)} identical")

drift = [k for k in keys if value(a.answers[k]) != value(b.answers[k])]
if drift:
    print(f"\n  differed ({len(drift)}):")
    for k in drift[:6]:
        print(f"    {k}: {value(a.answers[k])} vs {value(b.answers[k])}")
else:
    print("\n  Identical. The town is not rolling dice - it is reading state.")

# and the distribution behind a single answer, which is where the nuance lives
sample = next(k for k in keys if k.endswith("__act"))
print(f"\nthe probabilities behind one answer ({sample}):")
for opt, p in sorted(a.answers[sample].probabilities.items(), key=lambda kv: -kv[1]):
    bar = "#" * int(p * 40)
    print(f"  {opt:<10} {p:>6.3f} {bar}")
print(f"  -> chosen: {a.answers[sample].choice} "
      f"(confidence {a.answers[sample].confidence:.3f})")
print("\nThe model reports a full distribution; we take the top one. The spread is")
print("real information - a 0.9 choice and a 0.3 choice are not the same decision.")
