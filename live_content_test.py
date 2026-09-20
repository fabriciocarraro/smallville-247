"""
`talk` wins because it is true. Can a second question do the real work?

Measured live: gossip was on the menu for all 50 agents and chosen zero times,
across two menu shapes. The trimmed-menu hypothesis is dead. The likely reason
is semantic: gossiping IS an ordinary conversation, so when the model is asked
"what do they do" and one option is "starts an ordinary conversation", that
option is never wrong.

So stop making the model rank overlapping options. Let `talk` win, then ask a
separate question about what the conversation CARRIES. The act question decides
whether to engage; the content question decides whether the town has a story.

~18 billed calls.
"""
from __future__ import annotations

import os
import sys
from collections import Counter

sys.path.insert(0, ".")
if not os.environ.get("TYPESAFE_API_KEY"):
    sys.exit("TYPESAFE_API_KEY not in this process's environment")

from smallville.jev import Choice, RealClient
from smallville.scenes import build_scene_request
from smallville.sim import Simulation

sim = Simulation(n_agents=50, daily_budget_usd=2.0, seed=7, virtual_time=True)
for _ in range(900):
    sim.step()

scenes = []
for place in {a.at for a in sim.living()}:
    pool = [a for a in sim.living() if a.at == place]
    if len(pool) >= 5:
        scenes.append((place, pool[:8]))
    if len(scenes) == 6:
        break

CONTENT = {
    "small_talk": "the weather, work, nothing that will be repeated",
    "news":       "passes on something they heard about a third person",
    "personal":   "tells them something private about themselves",
    "grievance":  "raises something the other person did that annoyed them",
    "kindness":   "does or offers something practical for them",
}

client = RealClient()
spend = 0.0
tallies: dict[str, Counter] = {"act": Counter(), "content": Counter()}

for place, people in scenes:
    state, questions, _ = build_scene_request(place, people, sim.town, sim.rng, sim.agents)
    # the new question: given that they are talking, what is it carrying?
    for a in people:
        if f"{a.id}__act" in questions:
            questions[f"{a.id}__content"] = Choice(
                instructions=(f"{a.name} is talking with someone here. "
                              f"What is the conversation actually about?"),
                criteria=CONTENT)
    r = client.system_one(state=state, questions=questions)
    if not r.answers:
        print("  request failed"); continue
    spend += r.cost_usd
    for k, ans in r.answers.items():
        if not ans.choice:
            continue
        if k.endswith("__act"):
            tallies["act"][ans.choice] += 1
        elif k.endswith("__content"):
            tallies["content"][ans.choice] += 1

print(f"spent: ${spend:.5f}\n")
n_act = sum(tallies["act"].values()) or 1
n_con = sum(tallies["content"].values()) or 1
print(f"act      ({n_act} answers): {dict(tallies['act'])}")
print(f"content  ({n_con} answers): {dict(tallies['content'])}")

story = n_con - tallies["content"]["small_talk"]
print(f"\nconversations carrying something: {story}/{n_con} ({100*story/n_con:.0f}%)")
print("news / personal / grievance are what create rumours, expose affairs and")
print("start fights - the three things the town produced none of in an hour.")
