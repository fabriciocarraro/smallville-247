"""
Contract test for RealClient against the documented TypeSafe SDK.

There is no API key here, so the live path can never be exercised for real. The
next best thing is to encode the *documentation* as an executable fake and assert
our adapter satisfies it. Every shape below is copied from:

  docs.typesafe.ai/sdk/python/api/clients/sync.md    client + system_one signature
  docs.typesafe.ai/sdk/python/api/types/questions.md Choice / Score / Noul
  docs.typesafe.ai/sdk/python/api/types/responses.md SystemOneResponse / Usage / answers
  docs.typesafe.ai/sdk/python/api/retries.md         RetryPolicy defaults

The fake uses strict keyword-only signatures, so any drift between our call and
the documented one raises TypeError instead of passing quietly.

    python sdk_contract_test.py
"""
from __future__ import annotations

import sys
import types

sys.path.insert(0, ".")

CHECKS: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> None:
    CHECKS.append((bool(ok), label))


# --------------------------------------------------------------------------
# A fake SDK built strictly to the documented shapes
# --------------------------------------------------------------------------
sdk = types.ModuleType("typesafe_sdk")
seen: dict = {}


class RetryPolicy:
    # documented defaults: max_retries=2, backoff_initial=0.5, backoff_max=5.0,
    # backoff_jitter=0.25, retry on {408, 429, 500-599}
    def __init__(self, *, max_retries: int = 2, backoff_initial: float = 0.5,
                 backoff_max: float = 5.0, backoff_jitter: float = 0.25):
        self.max_retries = max_retries
        self.backoff_initial = backoff_initial
        self.backoff_max = backoff_max
        self.backoff_jitter = backoff_jitter


class Choice:
    def __init__(self, type="choice", instructions=None, criteria=None):
        if criteria is None:
            raise TypeError("Choice.criteria is required")
        self.type, self.instructions, self.criteria = type, instructions, criteria


class Score:
    def __init__(self, type="score", instructions=None, criteria=None):
        if not criteria:
            raise TypeError("Score.criteria must be a nonempty ordered sequence")
        self.type, self.instructions, self.criteria = type, instructions, criteria


class Noul:
    # NoulCriteria is a TypedDict with exactly {'true', 'false'}. The prose docs
    # say "the yes and no outcomes", which is the meaning, not the key names -
    # passing yes/no raises a pydantic extra_forbidden error on every request.
    def __init__(self, type="noul", instructions=None, criteria=None):
        if criteria is not None:
            extra = set(criteria) - {"true", "false"}
            if extra:
                raise TypeError(f"NoulCriteria forbids extra keys: {sorted(extra)}")
        self.type, self.instructions, self.criteria = type, instructions, criteria


class Usage:
    def __init__(self, input_tokens, output_tokens=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class NoulAnswer:            # documented: NO confidence, NO probabilities
    def __init__(self, noul):
        self.type, self.noul = "noul", noul


class ChoiceAnswer:
    def __init__(self, choice, confidence, probabilities):
        self.type, self.choice = "choice", choice
        self.confidence, self.probabilities = confidence, probabilities


class ScoreAnswer:           # probabilities keyed by INT, plus a legend
    def __init__(self, score, confidence, legend, probabilities):
        self.type, self.score, self.confidence = "score", score, confidence
        self.legend, self.probabilities = legend, probabilities


class SystemOneResponse:
    def __init__(self, model, usage, answers):
        self.model, self.usage, self.answers = model, usage, answers


class TypeSafeClient:
    def __init__(self, *, api_key=None, model=None, retry=None, timeout=None,
                 headers=None, transport=None, http_client=None, base_url=None):
        seen["ctor"] = {"api_key": api_key, "model": model, "retry": retry,
                        "timeout": timeout}

    def system_one(self, state, questions, *, model=None, retry=None, timeout=None,
                   extra_headers=None, extra_body=None, response_model=None):
        seen["call"] = {"state": state, "questions": questions, "model": model}
        answers = {}
        for key, q in questions.items():
            if isinstance(q, Choice):
                first = next(iter(q.criteria))
                answers[key] = ChoiceAnswer(first, 0.83, {k: 0.1 for k in q.criteria})
            elif isinstance(q, Score):
                answers[key] = ScoreAnswer(2.4, 0.61, {i: d for i, d in enumerate(q.criteria)},
                                           {i: 0.2 for i in range(len(q.criteria))})
            elif isinstance(q, Noul):
                answers[key] = NoulAnswer(0.72)
            else:
                raise TypeError(f"unknown question object: {type(q)}")
        return SystemOneResponse(model="jev-1.13.0",
                                 usage=Usage(input_tokens=seen.get("tokens", 4321)),
                                 answers=answers)


for name, obj in (("RetryPolicy", RetryPolicy), ("Choice", Choice), ("Score", Score),
                  ("Noul", Noul), ("TypeSafeClient", TypeSafeClient), ("Usage", Usage),
                  ("SystemOneResponse", SystemOneResponse)):
    setattr(sdk, name, obj)
sys.modules["typesafe_sdk"] = sdk

from smallville.jev import Choice as MyChoice, Noul as MyNoul, RealClient, Score as MyScore
from smallville.scenes import build_scene_request
from smallville.sim import Simulation

# --------------------------------------------------------------------------
print("Contract: RealClient vs the documented SDK\n")

c = RealClient()

# 1. retries must be OFF - the SDK default blocks up to 30s inside one call
ctor = seen["ctor"]
check(ctor["retry"] is not None and ctor["retry"].max_retries == 0,
      "client disables SDK retries (max_retries=0), so no hidden blocking backoff")
check(isinstance(ctor["timeout"], (int, float)) and 0 < ctor["timeout"] <= 10,
      f"client sets a short timeout ({ctor['timeout']}s), not the 30s default")

# 2. the three question types survive translation, including Noul criteria
qs = {
    "q_choice": MyChoice("pick one", {"a": "first", "b": "second"}),
    "q_score": MyScore("rate it", ["low", "mid", "high"]),
    "q_noul": MyNoul("it is true", criteria={"true": "y", "false": "n"}),
    "q_noul_bare": MyNoul("no criteria given"),
}
r = c.system_one(state={"hello": "world"}, questions=qs)
sent = seen["call"]["questions"]
check(isinstance(sent["q_choice"], Choice) and sent["q_choice"].criteria == {"a": "first", "b": "second"},
      "Choice forwarded with its criteria mapping")
check(isinstance(sent["q_score"], Score) and list(sent["q_score"].criteria) == ["low", "mid", "high"],
      "Score forwarded as an ordered sequence")
check(isinstance(sent["q_noul"], Noul) and sent["q_noul"].criteria == {"true": "y", "false": "n"},
      "Noul criteria use the TypedDict keys true/false, not yes/no")
check(isinstance(sent["q_noul_bare"], Noul) and sent["q_noul_bare"].criteria is None,
      "Noul without criteria still valid")

# 3. `model` is a documented keyword-only parameter and must actually arrive
check(seen["call"]["model"] == "jev-1.13.0", "model reaches system_one as a keyword")

# 4. answers map correctly, including a Noul that carries NO confidence
check(r.answers["q_choice"].choice == "a" and r.answers["q_choice"].confidence == 0.83,
      "ChoiceAnswer -> .choice / .confidence / .probabilities")
check(r.answers["q_score"].score == 2.4, "ScoreAnswer -> .score (probability-weighted)")
check(r.answers["q_noul"].noul == 0.72, "NoulAnswer -> .noul")
check(r.answers["q_noul"].confidence == 0.0,
      "a Noul has no confidence in the docs; reading it must not explode")

# 5. THE ONE THAT MATTERED: billed tokens come from usage.input_tokens
check(r.input_tokens == 4321 and c.estimated == 0,
      "input tokens read from usage.input_tokens, not estimated")

# 6. and the estimate is a labelled fallback, not a silent one
seen["tokens"] = None
r2 = c.system_one(state={"hello": "world"}, questions=qs)
check(r2.input_tokens > 0 and c.estimated == 1,
      "when the API reports no usage, the estimate is used AND counted")

# 7. a real scene request survives the whole translation
seen["tokens"] = 9999
s = Simulation(n_agents=12, daily_budget_usd=2.0, seed=7, virtual_time=True)
s.client = c
for _ in range(40):
    s.step()
state, questions, _ = build_scene_request("square", s.living()[:6], s.town, s.rng, s.agents)
r3 = c.system_one(state=state, questions=questions)
check(len(r3.answers) == len(questions) and r3.input_tokens == 9999,
      f"a full {len(questions)}-question scene round-trips and bills from usage")

# 8. the governor must be charging billed tokens, not guesses
charged_from_api = s.gov.spent_tokens > 0 and c.estimated == 1
check(charged_from_api, "the dollar budget is driven by API-reported tokens")

# --------------------------------------------------------------------------
print()
failed = 0
for ok, label in CHECKS:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    failed += not ok
print(f"\n{len(CHECKS) - failed}/{len(CHECKS)} passed")

print("""
Still NOT covered by this test, because only a live key can settle it:
  * whether ~50 questions in one request is accepted (docs state no question cap,
    only the 64k context, and this sim sends 7 questions x up to 8 people)
  * real latency, and whether the tick time-box holds at 70-500ms per call
  * whether Jev's real answer distributions behave like the mock's, which is what
    every tuning constant in scenes.py was fitted against
""")
sys.exit(1 if failed else 0)
