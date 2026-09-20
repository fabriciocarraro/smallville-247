"""
First contact with GPT-5.6 Luna: narrate real moments from a real town.

Builds a town headless (mock Jev, free, fast) until it has produced genuine
drama, then hands the hottest moments to the narrator for real. Prints the model
line beside the free template line for the same event, so the difference is
visible rather than asserted.

Roughly 6 billed calls, well under a cent. Never prints the key.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, ".")

if not os.environ.get("OPENAI_API_KEY"):
    sys.exit("OPENAI_API_KEY is not in this process's environment")
print(f"key present: {len(os.environ['OPENAI_API_KEY'])} chars\n")

from smallville.narrator import Narrator, _template
from smallville.sim import Simulation

print("building a town until it has something worth saying...")
sim = Simulation(n_agents=50, daily_budget_usd=2.0, seed=7, virtual_time=True)
for _ in range(14400):                       # 20 simulated days
    sim.step()

# the moments a viewer would actually care about
hot = sorted(
    [e for e in sim.town.events
     if e.kind in Narrator.NARRATABLE and e.heat >= Narrator.MIN_HEAT],
    key=lambda e: -e.heat)
picked, seen_kinds = [], set()
for e in hot:                                # spread across event types
    if seen_kinds.count(e.kind) if isinstance(seen_kinds, list) else e.kind in seen_kinds:
        continue
    seen_kinds.add(e.kind)
    picked.append(e)
    if len(picked) >= 6:
        break

print(f"{len(sim.town.chronicle)} things happened over 20 sim days; "
      f"narrating {len(picked)} of them\n")

n = Narrator(daily_budget_usd=5.0)           # generous ceiling for a one-off test
if not n.live:
    sys.exit("narrator did not go live - is the openai SDK installed in this python?")

for i, ev in enumerate(picked, 1):
    payload = [{"kind": ev.kind, "text": ev.text, "heat": ev.heat,
                "time": ev.time_label, "actors": list(ev.actors)}]
    ctx = n._context(payload, sim.town)
    t0 = time.perf_counter()
    try:
        line = n._compose(payload, ctx)
    except Exception as exc:
        print(f"{i}. CALL FAILED: {exc!r}")
        continue
    ms = (time.perf_counter() - t0) * 1000

    print(f"{i}. [{ev.kind}] {ev.text}")
    print(f"   template : {_template(payload)}")
    print(f"   luna     : {line}")
    print(f"   {ms:.0f} ms\n")

print("-" * 72)
s = n.snapshot()
print(f"calls {s['calls']} | spent ${s['spent_usd']:.5f} "
      f"| ${s['spent_usd']/max(1,s['calls']):.5f} per line "
      f"| cached input tokens {s['cached_in']}")
print(f"at one line every 12s, a full day would cost "
      f"${s['spent_usd']/max(1,s['calls'])*7200:.2f}")
