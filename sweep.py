"""How many agents should a $8/day town have? Measure, do not guess.

Same budget, same wall-clock, same seed - only the population changes.
"""
import sys
from collections import Counter

sys.path.insert(0, '.')
from smallville.sim import Simulation

TICKS = 1800          # 30 min of real time; 2.5 simulated days
BUDGET = 8.0

print(f"{'agents':>7} {'think/agent':>12} {'ever acted':>11} {'friends':>8} {'enemies':>8} "
      f"{'top rumour':>11} {'recurring':>10} {'$/day':>7}")
print('-' * 84)

for n in (40, 80, 150, 300, 600):
    s = Simulation(n_agents=n, daily_budget_usd=BUDGET, seed=7, virtual_time=True)
    for _ in range(TICKS):
        s.step()

    ag = s.living()   # the dead skew every average
    thoughts = sum(a.thoughts for a in ag) / n
    acted = sum(1 for a in ag if a.thoughts > 0) / n * 100
    friends = sum(len([v for v in a.trust.values() if v > 0.45]) for a in ag) / n
    enemies = sum(len([v for v in a.trust.values() if v < -0.3]) for a in ag) / n
    reach = max((r.reach / n * 100 for r in s.town.rumours.values()), default=0)

    # a "recurring character" is someone who shows up in 3+ memorable moments:
    # the thing that makes a viewer care about a name
    appear = Counter()
    for e in s.town.events:
        if e.heat >= 0.55:
            appear.update(e.actors)
    recurring = sum(1 for v in appear.values() if v >= 3)

    e = s.snapshot()['econ']
    print(f"{n:>7} {thoughts:>12.1f} {acted:>10.0f}% {friends:>8.2f} {enemies:>8.2f} "
          f"{reach:>10.1f}% {recurring:>10} {e['projected_daily_usd']:>7.2f}")
