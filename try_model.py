"""Does a given model accept the narrator's exact request shape, and what does
it cost and sound like? Run before switching the default.

    python try_model.py gpt-5-nano
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, ".")

MODEL = sys.argv[1] if len(sys.argv) > 1 else "gpt-5-nano"
if not os.environ.get("OPENAI_API_KEY"):
    sys.exit("OPENAI_API_KEY not in this process's environment")

from openai import OpenAI

from smallville.narrator import SYSTEM

client = OpenAI()
payload = {
    "just_happened": ["[caught] Dara Ferro found out about Iris Halloran and Cato Dahl."],
    "the_time": "Tuesday day 8, 19:20",
    "this_town_recently": [
        "Iris Halloran and Cato Dahl began an affair.",
        "Emil Pinto did not keep Ove Ferreira's secret.",
        "Sven Brandt died - old age.",
    ],
    "what_people_are_saying": ["Iris Halloran is seeing Cato Dahl behind Dara Ferro's back (7 believe it)"],
    "buried_so_far": 3,
}

print(f"model: {MODEL}\n")

# The narrator sends reasoning.effort='none' and text.verbosity='low'. Older
# models may reject either, so try the full shape then degrade, and report which
# shape actually worked - that is what the narrator will have to use.
shapes = [
    ("full (effort=none, verbosity=low)",
     dict(reasoning={"effort": "none"}, text={"verbosity": "low"})),
    ("effort=minimal, verbosity=low",
     dict(reasoning={"effort": "minimal"}, text={"verbosity": "low"})),
    ("effort=low only", dict(reasoning={"effort": "low"})),
    ("no reasoning, no verbosity", {}),
]

for label, extra in shapes:
    try:
        t0 = time.perf_counter()
        r = client.responses.create(
            model=MODEL,
            instructions=SYSTEM,
            input=json.dumps(payload, ensure_ascii=False, indent=1),
            max_output_tokens=220,
            store=False,
            **extra,
        )
        ms = (time.perf_counter() - t0) * 1000
        u = r.usage
        text = (r.output_text or "").strip()
        reasoning_out = getattr(getattr(u, "output_tokens_details", None),
                                "reasoning_tokens", 0) or 0
        print(f"OK   {label}")
        print(f"     {ms:.0f} ms | in {u.input_tokens} out {u.output_tokens} "
              f"(reasoning {reasoning_out})")
        print(f"     {text!r}")
        if not text:
            print("     WARNING: empty output - the budget went to reasoning tokens")
        print()
        break
    except Exception as exc:
        print(f"NO   {label}\n     {type(exc).__name__}: {str(exc)[:190]}\n")
else:
    sys.exit("no request shape worked for this model")
