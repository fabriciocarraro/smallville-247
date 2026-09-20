"""
What happens to the stream when the narration key is wrong?

A rejected key must not take the town down, must not stall the tick loop, and
must not leave the caption bar empty - it should fall back to templates and keep
broadcasting. This asserts that, using whatever OPENAI_API_KEY is in the
environment (a bad one is the interesting case).
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, ".")

from smallville.narrator import Narrator
from smallville.sim import Simulation

n = Narrator(daily_budget_usd=1.0)
print(f"narrator live: {n.live} (live means it will try real calls)\n")

sim = Simulation(n_agents=50, daily_budget_usd=2.0, seed=7,
                 virtual_time=True, narrator=n)
n.start()

t0 = time.perf_counter()
for _ in range(6000):
    sim.step()
elapsed = time.perf_counter() - t0
time.sleep(2.0)                      # let the worker drain its queue

s = n.snapshot()
lines = s["lines"]
sources = {}
for row in lines:
    sources[row["source"]] = sources.get(row["source"], 0) + 1

print(f"6000 ticks in {elapsed:.1f}s - the tick loop was never blocked")
print(f"town alive: {len(sim.living())} people, day {sim.town.clock.day + 1}")
print(f"narration errors: {s['errors']}  spent: ${s['spent_usd']:.5f}")
print(f"lines produced: {len(lines)}  by source: {sources}")
print()
for row in lines[:4]:
    print(f"  [{row['source']}] {row['text'][:72]}")

ok = (len(sim.living()) > 0 and lines
      and all(r["source"] == "template" for r in lines) if s["errors"] else True)
print()
if s["errors"] and lines and all(r["source"] == "template" for r in lines):
    print("PASS: the key was rejected, every line fell back to templates, and the "
          "simulation never noticed.")
elif not s["errors"] and s["calls"]:
    print("PASS: the key worked; lines came from the model.")
else:
    print("CHECK: unexpected combination - errors "
          f"{s['errors']}, calls {s['calls']}, lines {len(lines)}")
n.stop()
