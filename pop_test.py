import sys
from collections import Counter
sys.path.insert(0,'.')
from smallville.sim import Simulation

TICKS = 7200   # 10 simulated days
print(f"{'config':>18} {'dec/agente':>11} {'tok/req':>8} {'tok/decisao':>12} "
      f"{'amigos':>7} {'casais':>7} {'casos':>6} {'$/dia':>7}")
print('-'*80)
for n, b in ((50, 2.0), (100, 2.0), (100, 4.0)):
    s = Simulation(n_agents=n, daily_budget_usd=b, seed=7, virtual_time=True)
    for _ in range(TICKS): s.step()
    ag = s.living()
    k = Counter(c['kind'] for c in s.town.chronicle)
    e = s.snapshot()['econ']
    friends = sum(len([v for v in a.trust.values() if v > 0.45]) for a in ag)/len(ag)
    per_dec = e['tokens']/max(1, e['agent_thoughts'])
    print(f"{f'{n}p @ ${b:.0f}/dia':>18} {e['agent_thoughts']/len(ag):>11.0f} "
          f"{e['tokens']/max(1,e['requests']):>8.0f} {per_dec:>12.0f} {friends:>7.2f} "
          f"{k['together']:>7} {k['affair']:>6} {e['projected_daily_usd']:>7.2f}")
