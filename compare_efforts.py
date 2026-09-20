"""
Which effort should the narrator use, and is there a no-reasoning option?

The docs list seven effort values but say each model takes a subset and point at
the model page; the model page does not list them. The API itself is the
authority, so this asks it: every level against the narrator's real request, the
reasoning tokens actually billed, and two genuinely non-reasoning models for
comparison.

    python compare_efforts.py
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, ".")
if not os.environ.get("OPENAI_API_KEY"):
    sys.exit("OPENAI_API_KEY not in this process's environment")

from openai import OpenAI

from smallville.narrator import PRICING, SYSTEM

client = OpenAI()

PAYLOAD = json.dumps({
    "just_happened": ["[caught] Dara Ferro found out about Iris Halloran and Cato Dahl."],
    "the_time": "Tuesday day 8, 19:20",
    "this_town_recently": [
        "Iris Halloran and Cato Dahl began an affair.",
        "Emil Pinto did not keep Ove Ferreira's secret.",
        "Sven Brandt died - old age.",
    ],
    "what_people_are_saying": [
        "Iris Halloran is seeing Cato Dahl behind Dara Ferro's back (7 believe it)"],
    "buried_so_far": 3,
}, ensure_ascii=False, indent=1)

REASONING_MODEL = "gpt-5-nano"
EFFORTS = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]
# genuinely non-reasoning models, for the "can we just not reason" question
PLAIN_MODELS = ["gpt-5-chat-latest", "gpt-4.1-nano"]


def call(model: str, effort: str | None):
    kw = {"reasoning": {"effort": effort}} if effort else {}
    t0 = time.perf_counter()
    r = client.responses.create(
        model=model, instructions=SYSTEM, input=PAYLOAD,
        text={"verbosity": "low"}, max_output_tokens=220, store=False, **kw)
    ms = (time.perf_counter() - t0) * 1000
    u = r.usage
    think = getattr(getattr(u, "output_tokens_details", None), "reasoning_tokens", 0) or 0
    rate = PRICING.get(model, (0.20, 0.02, 1.20))
    cost = (u.input_tokens * rate[0] + u.output_tokens * rate[2]) / 1_000_000
    return ms, u.input_tokens, u.output_tokens, think, cost, (r.output_text or "").strip()


print(f"=== {REASONING_MODEL}: every effort level ===")
print(f"{'effort':>8} {'ms':>6} {'in':>5} {'out':>5} {'think':>6} {'$/line':>9}  line")
print("-" * 104)
usable = []
for e in EFFORTS:
    try:
        ms, i, o, think, cost, text = call(REASONING_MODEL, e)
        usable.append(e)
        print(f"{e:>8} {ms:>6.0f} {i:>5} {o:>5} {think:>6} {cost:>9.6f}  {text[:52]}")
    except Exception as exc:
        msg = str(exc)
        short = "rejected: " + (msg.split("Supported values")[0][-70:].strip()
                                if "Supported" in msg else msg[:70])
        print(f"{e:>8} {'-':>6} {'-':>5} {'-':>5} {'-':>6} {'-':>9}  {short}")

print(f"\naccepted by {REASONING_MODEL}: {usable}")

print("\n=== models that do not reason at all ===")
print(f"{'model':>20} {'ms':>6} {'in':>5} {'out':>5} {'think':>6}  line")
print("-" * 104)
for m in PLAIN_MODELS:
    try:
        ms, i, o, think, cost, text = call(m, None)
        print(f"{m:>20} {ms:>6.0f} {i:>5} {o:>5} {think:>6}  {text[:50]}")
    except Exception as exc:
        print(f"{m:>20} {'-':>6} {'-':>5} {'-':>5} {'-':>6}  {type(exc).__name__}: {str(exc)[:56]}")

print("\nIf 'think' is 0 at an accepted effort, that level already bills no "
      "reasoning tokens - which is the practical 'no reasoning' this app wants.")
