"""Two follow-ups:
   1) can budget buy back the narrative density that population dilutes?
   2) what does ONE extra question per agent actually cost?
"""
import sys
from collections import Counter
sys.path.insert(0, '.')
from smallville.sim import Simulation
from smallville.scenes import build_scene_request
from smallville.jev import Noul, request_tokens

TICKS = 1800

def run(n, budget):
    s = Simulation(n_agents=n, daily_budget_usd=budget, seed=7, virtual_time=True)
    for _ in range(TICKS):
        s.step()
    ag = s.living()   # the dead skew every average
    appear = Counter()
    for e in s.town.events:
        if e.heat >= 0.55:
            appear.update(e.actors)
    return {
        'think': sum(a.thoughts for a in ag) / n,
        'friends': sum(len([v for v in a.trust.values() if v > 0.45]) for a in ag) / max(1,len(ag)),
        'reach': max((r.reach / n * 100 for r in s.town.rumours.values()), default=0),
        'recurring': sum(1 for v in appear.values() if v >= 3),
        'cost': s.snapshot()['econ']['projected_daily_usd'],
        'sim': s,
    }

print(f"{'config':>22} {'think/agent':>12} {'friends':>8} {'top rumour':>11} {'recurring':>10} {'$/day':>7}")
print('-' * 76)
for n, b in ((150, 8), (300, 8), (300, 16), (300, 24), (600, 32)):
    r = run(n, b)
    print(f"{f'{n} agents @ ${b}/day':>22} {r['think']:>12.1f} {r['friends']:>8.2f} "
          f"{r['reach']:>10.1f}% {r['recurring']:>10} {r['cost']:>7.2f}")

# --- what one more question per agent costs -------------------------------
s = run(150, 8)['sim']
from collections import defaultdict
by = defaultdict(list)
for a in s.agents.values():
    by[a.at].append(a)
place, people = max(by.items(), key=lambda kv: len(kv[1]))
chunk = people[:8]
state, qs, _ = build_scene_request(place, chunk, s.town, s.rng)
base = request_tokens(state, qs)
for a in chunk:
    qs[f'{a.id}__romance'] = Noul(
        instructions=f"{a.name} is drawn to the person they just dealt with, beyond friendship")
plus = request_tokens(state, qs)
print(f"\none extra Noul for all 8 people in a scene: {base} -> {plus} tokens "
      f"(+{(plus-base)/base*100:.1f}%)")
print(f"that is {(plus-base)/8:.0f} tokens per person per decision")
