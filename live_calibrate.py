"""
Calibrate against the live model. ~16 billed calls, roughly half a cent.

The first live call showed three things the mock could never have told us:
  * our token estimate is ~47% low, and the governor reserves with the estimate
  * a 56-question request took 952ms, not the 70-500ms the docs advertise
  * Noul answers centre far lower than the mock's (draw 0.179 vs 0.587), which
    is the number every romance constant in scenes.py was fitted against

This measures all three properly so the constants can be re-fitted to reality.
"""
from __future__ import annotations

import os
import statistics
import sys
import time
from collections import Counter, defaultdict

sys.path.insert(0, ".")
if not os.environ.get("TYPESAFE_API_KEY"):
    sys.exit("TYPESAFE_API_KEY not in environment")

from smallville.jev import RealClient, request_tokens
from smallville.scenes import build_scene_request
from smallville.sim import Simulation

sim = Simulation(n_agents=50, daily_budget_usd=2.0, seed=7, virtual_time=True)
for _ in range(600):
    sim.step()

client = RealClient()
by_size: dict[int, list] = defaultdict(list)
nouls: dict[str, list] = defaultdict(list)
acts: Counter = Counter()
confs: list[float] = []
scores: list[float] = []
spend = 0.0

places = [p for p in {a.at for a in sim.living()}]
print(f"{'people':>7} {'questions':>10} {'est':>7} {'billed':>7} {'ratio':>6} {'ms':>7}")
print("-" * 50)

for size in (2, 4, 6, 8):
    for trial in range(4):
        pool = [a for a in sim.living() if a.at == places[trial % len(places)]]
        if len(pool) < size:
            pool = sim.living()
        people = pool[:size]
        place = people[0].at
        state, questions, _ = build_scene_request(place, people, sim.town, sim.rng, sim.agents)
        est = request_tokens(state, questions)

        t0 = time.perf_counter()
        r = client.system_one(state=state, questions=questions)
        ms = (time.perf_counter() - t0) * 1000
        if not r.answers:
            print(f"{size:>7} {len(questions):>10}  FAILED (errors={client.errors})")
            continue
        spend += r.cost_usd
        by_size[size].append((ms, est, r.input_tokens, len(questions)))
        print(f"{size:>7} {len(questions):>10} {est:>7} {r.input_tokens:>7} "
              f"{r.input_tokens/est:>6.2f} {ms:>7.0f}")

        for k, a in r.answers.items():
            if a.noul is not None:
                nouls[k.split("__")[1]].append(a.noul)
            elif a.choice is not None and k.endswith("__act"):
                acts[a.choice] += 1
                confs.append(a.confidence)
            elif a.score is not None:
                scores.append(a.score)

print(f"\ntotal spent on this calibration: ${spend:.5f}\n")

print("=== 1. latency vs scene size ===")
for size in sorted(by_size):
    rows = by_size[size]
    lat = [r[0] for r in rows]
    q = rows[0][3]
    print(f"  {size} people ({q:>2} questions): p50 {statistics.median(lat):>6.0f} ms   "
          f"min {min(lat):>5.0f}  max {max(lat):>5.0f}")
print("  a 1s tick can fit roughly one request; size drives latency directly")

print("\n=== 2. token estimate calibration ===")
ratios = [r[2] / r[1] for rows in by_size.values() for r in rows]
print(f"  billed / estimated: mean {statistics.mean(ratios):.3f}  "
      f"median {statistics.median(ratios):.3f}  "
      f"range {min(ratios):.2f}-{max(ratios):.2f}")
print(f"  -> multiply request_tokens() by {statistics.median(ratios):.2f} when reserving budget")

print("\n=== 3. answer distributions, real vs mock ===")
MOCK = {"draw": 0.587, "warm": 0.5, "tell": 0.5}
for k in sorted(nouls):
    v = nouls[k]
    print(f"  {k:<5} n={len(v):>3}  mean {statistics.mean(v):.3f}  "
          f"median {statistics.median(v):.3f}  sd {statistics.pstdev(v):.3f}  "
          f"range {min(v):.2f}-{max(v):.2f}   (mock ~{MOCK.get(k, 0.5)})")
if confs:
    print(f"  act confidence  mean {statistics.mean(confs):.3f}  "
          f"median {statistics.median(confs):.3f}  min {min(confs):.3f}")
    print(f"  actions: {dict(acts)}")
if scores:
    print(f"  mood score      mean {statistics.mean(scores):.2f}  "
          f"range {min(scores):.2f}-{max(scores):.2f}")

d = nouls.get("draw", [])
if d:
    m = statistics.mean(d)
    print(f"\n=== what this means for romance ===")
    print(f"  current: (draw - 0.44) * 0.55  ->  at the real mean: "
          f"{(m - 0.44) * 0.55:+.4f} per interaction")
    if m < 0.44:
        print(f"  NEGATIVE: attraction would decay on every interaction and no")
        print(f"  couple could ever form. The pivot must move to about the median,")
        print(f"  and the gain must rise to cover the much smaller spread.")
        p70 = sorted(d)[int(len(d) * 0.7)]
        print(f"  suggested pivot (p70 of real draws): {p70:.3f}")
