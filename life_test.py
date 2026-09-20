"""Do couples, affairs and deaths actually happen - and at a watchable rate?"""
import sys
from collections import Counter
sys.path.insert(0, '.')
from smallville.sim import Simulation

DAYS = 25
TICKS = 720 * DAYS          # 720 ticks = 1 simulated day

s = Simulation(n_agents=50, daily_budget_usd=2.0, seed=7, virtual_time=True)
start_couples = sum(1 for a in s.living() if a.partner) // 2
pop = []
for t in range(TICKS):
    s.step()
    if t % 720 == 0:
        pop.append(len(s.living()))

k = Counter(c['kind'] for c in s.town.chronicle)
alive = s.living()
e = s.snapshot()['econ']

print(f"50 people, $2/day, {DAYS} simulated days ({TICKS} ticks)\n")
print(f"  population by day : {pop}")
print(f"  couples at start  : {start_couples}")
print(f"  couples now       : {sum(1 for a in alive if a.partner)//2}")
print(f"  new couples       : {k['together']}")
print(f"  affairs started   : {k['affair']}")
print(f"  affairs EXPOSED   : {k['caught']}")
print(f"  deaths            : {k['death']}")
print(f"  arrivals          : {k['arrival']}")
print(f"  currently cheating: {sum(1 for a in alive if a.affair)//2}")
print(f"  sick right now    : {sum(1 for a in alive if a.sick)}")
print(f"  cost              : ${e['projected_daily_usd']:.2f}/day  "
      f"({e['agent_thoughts']/max(1,len(alive)):.0f} decisions each)")

print("\n  --- the chronicle ---")
for c in s.town.chronicle[:16]:
    print(f"   day {c['day']+1:>2}  [{c['kind']:>8}]  {c['text']}")
