"""
First contact with the live API: one scene, one request.

Answers the three things a contract test cannot:
  1. is a ~50-question scene request accepted at all?
  2. what is the real latency, and does the tick time-box survive it?
  3. do Jev's answer distributions resemble the mock's, which every tuning
     constant in scenes.py was fitted against?

Run it with the venv python and TYPESAFE_API_KEY in the environment. It makes
exactly ONE billed call (~1k input tokens, about $0.00004). Never prints the key.
"""
from __future__ import annotations

import os
import statistics
import sys
import time

sys.path.insert(0, ".")

key = os.environ.get("TYPESAFE_API_KEY")
if not key:
    sys.exit("TYPESAFE_API_KEY is not in this process's environment")
print(f"key present: {len(key)} chars\n")

from smallville.jev import Noul, RealClient, request_tokens
from smallville.scenes import build_scene_request
from smallville.sim import Simulation

# Build a scene exactly as the simulation would, using the mock to warm the town
sim = Simulation(n_agents=50, daily_budget_usd=2.0, seed=7, virtual_time=True)
for _ in range(400):
    sim.step()

people = sorted(sim.living(), key=lambda a: a.at)[:8]
place = people[0].at
people = [a for a in sim.living() if a.at == place][:8]
if len(people) < 2:
    people = sim.living()[:8]
    place = people[0].at

state, questions, _ = build_scene_request(place, people, sim.town, sim.rng, sim.agents)
estimate = request_tokens(state, questions)
print(f"scene: {place} | {len(people)} people | {len(questions)} questions "
      f"| our estimate {estimate} tokens")

client = RealClient()
t0 = time.perf_counter()
r = client.system_one(state=state, questions=questions)
wall = (time.perf_counter() - t0) * 1000

if not r.answers:
    sys.exit(f"\nNO ANSWERS. errors={client.errors} - see the [jev] line above.")

print(f"\n--- it worked ---")
print(f"  latency        : {wall:.0f} ms   (docs claim 70-500 ms)")
print(f"  billed tokens  : {r.input_tokens}  (estimate was {estimate}, "
      f"off by {100*(estimate-r.input_tokens)/r.input_tokens:+.0f}%)")
print(f"  cost this call : ${r.cost_usd:.6f}")
print(f"  answers        : {len(r.answers)} / {len(questions)} questions")
print(f"  fell back to estimate: {client.estimated} time(s)")

# --- the comparison that matters: real vs mock answer distributions ---------
nouls = {k: a.noul for k, a in r.answers.items() if a.noul is not None}
choices = {k: a for k, a in r.answers.items() if a.choice is not None}
scores = {k: a for k, a in r.answers.items() if a.score is not None}

print(f"\n--- distributions (the tuning constants were fitted to the mock) ---")
for label, keys in (("draw", "__draw"), ("warm", "__warm"), ("tell", "__tell")):
    vals = [v for k, v in nouls.items() if k.endswith(keys)]
    if vals:
        print(f"  {label:<5} n={len(vals):>2}  mean {statistics.mean(vals):.3f}  "
              f"min {min(vals):.3f}  max {max(vals):.3f}")
print(f"  MOCK reference: draw mean 0.587 (this is what PAIR_ATTRACTION assumed)")

acts = [a.choice for k, a in choices.items() if k.endswith("__act")]
if acts:
    from collections import Counter
    print(f"\n  actions chosen : {dict(Counter(acts))}")
    confs = [a.confidence for k, a in choices.items() if k.endswith("__act")]
    print(f"  act confidence : mean {statistics.mean(confs):.3f} "
          f"min {min(confs):.3f} max {max(confs):.3f}")
    print(f"                   (confront degrades to withdraw below 0.45)")

if scores:
    sv = [a.score for a in scores.values()]
    print(f"  mood scores    : mean {statistics.mean(sv):.2f} range "
          f"{min(sv):.2f}-{max(sv):.2f}  (0-4 scale)")

print(f"\n  sample answers:")
for k in list(r.answers)[:5]:
    a = r.answers[k]
    v = a.choice if a.choice is not None else (a.score if a.score is not None else a.noul)
    print(f"    {k:<16} {v}")
