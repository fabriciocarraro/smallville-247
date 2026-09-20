"""
Jev client wrapper.

Mirrors the real TypeSafe SDK surface exactly:

    from typesafe_sdk import Choice, Noul, Score, TypeSafeClient
    client = TypeSafeClient()
    r = client.system_one(state={...}, questions={"k": Choice(...)})
    r.answers["k"].choice / .confidence / .probabilities

so swapping mock -> real is a single env var (TYPESAFE_API_KEY), no code change.

Docs: POST /v1/systemone, model jev-1.13.0, 64k ctx per request
(32k for state + longest question), $0.042/MTok in, output free,
250k tok/s and 1200 req/min account limits.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any

# --- pricing / limits, from docs.typesafe.ai/models.md -----------------------
USD_PER_INPUT_TOKEN = 0.042 / 1_000_000
CTX_TOKENS = 64_000
STATE_CTX_TOKENS = 32_000
REQ_PER_MIN_LIMIT = 1_200
MODEL = "jev-1.13.0"

# Measured against the live API (live_calibrate.py), not guessed:
#   * billed tokens run ~1.89x our ~4-chars/token estimate, so budget reservations
#     must be scaled or the governor lets through twice what it can afford;
#   * Noul answers centre near 0.24 with sd ~0.11.
TOKEN_ESTIMATE_SCALE = 1.70

# Per-question Noul calibration, from 80 live answers each. The three questions
# do NOT share a distribution - adding true/false criteria to each moved two of
# them - so a mock with one shared centre stops predicting what the live model
# will do, and every threshold fitted against it comes out wrong.
#
#   question   live mean   live sd
#   draw          0.234      0.099
#   tell          0.342      0.109
#   warm          0.488      0.184
#
# Expressed as logit mean/sd, since the mock builds a logit and squashes it.
NOUL_CALIBRATION = {
    "draw": (-1.22, 0.50),
    "tell": (-0.67, 0.50),
    "warm": (-0.05, 0.75),
}
NOUL_DEFAULT = (-1.00, 0.55)


# --- question primitives (same constructors as the real SDK) -----------------
@dataclass
class Choice:
    instructions: str
    criteria: dict[str, str]          # up to 255 options


@dataclass
class Score:
    instructions: str
    criteria: list[str]               # 2-10 ordered levels


@dataclass
class Noul:
    instructions: str                 # a statement; answer is P(true), 0-1
    criteria: dict[str, str] | None = None   # optional; keys must be 'true'/'false'


@dataclass
class Answer:
    choice: str | None = None
    score: float | None = None
    noul: float | None = None
    confidence: float = 0.0
    probabilities: dict[str, float] = field(default_factory=dict)


@dataclass
class Response:
    answers: dict[str, Answer]
    input_tokens: int = 0
    latency_ms: float = 0.0

    @property
    def cost_usd(self) -> float:
        return self.input_tokens * USD_PER_INPUT_TOKEN


# --- token accounting --------------------------------------------------------
def estimate_tokens(obj: Any) -> int:
    """~4 chars/token. Good enough for budgeting; the real client reports exact."""
    if not isinstance(obj, str):
        obj = json.dumps(obj, ensure_ascii=False, default=str)
    return max(1, len(obj) // 4)


def request_tokens(state: Any, questions: dict[str, Any]) -> int:
    q = 0
    for key, question in questions.items():
        q += estimate_tokens(key) + estimate_tokens(question.instructions)
        if getattr(question, "criteria", None):
            q += estimate_tokens(question.criteria)
    return estimate_tokens(state) + q


# --- mock --------------------------------------------------------------------
_STOP = {
    "the", "a", "an", "is", "are", "to", "of", "and", "or", "in", "on", "at",
    "for", "with", "this", "that", "it", "its", "be", "has", "have", "who",
    "what", "their", "they", "them", "someone", "about", "from", "as", "by",
}


def _words(text: str) -> set[str]:
    out = set()
    for raw in text.lower().replace("_", " ").split():
        w = "".join(c for c in raw if c.isalnum())
        if len(w) > 2 and w not in _STOP:
            out.add(w)
    return out


def _softmax(scores: list[float], temp: float = 1.0) -> list[float]:
    m = max(scores)
    exps = [math.exp((s - m) / temp) for s in scores]
    total = sum(exps) or 1.0
    return [e / total for e in exps]


class MockClient:
    """
    Deterministic stand-in for Jev.

    Not a language model - it scores each option by lexical affinity with the
    state, then softmaxes. That is enough for the simulation to produce coherent,
    non-random, watchable behaviour today, and it exercises exactly the same
    call/answer shape the real model will.
    """

    is_mock = True

    def __init__(self, seed: int = 7, latency_ms: float = 0.0):
        self.seed = seed
        self.latency_ms = latency_ms
        self.requests = 0
        self.input_tokens = 0

    def _rng(self, *parts: str) -> random.Random:
        h = hashlib.sha256(("|".join(parts) + f"|{self.seed}").encode()).hexdigest()
        return random.Random(int(h[:16], 16))

    def system_one(self, state: Any, questions: dict[str, Any], model: str = MODEL) -> Response:
        t0 = time.perf_counter()
        tokens = request_tokens(state, questions)
        if tokens > CTX_TOKENS:
            raise ValueError(f"request is {tokens} tokens, over the {CTX_TOKENS} context")

        state_text = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False, default=str)
        state_words = _words(state_text)
        nonce = hashlib.sha256(state_text.encode()).hexdigest()[:12]
        answers: dict[str, Answer] = {}

        for key, q in questions.items():
            rng = self._rng(key, nonce)
            if isinstance(q, Choice):
                opts = list(q.criteria.items())
                raw = []
                for name, desc in opts:
                    affinity = len((_words(name) | _words(desc)) & state_words)
                    raw.append(1.35 * affinity + rng.gauss(0, 1.0))
                probs = _softmax(raw, temp=0.85)
                dist = {opts[i][0]: round(p, 4) for i, p in enumerate(probs)}
                pick = max(dist, key=dist.get)
                answers[key] = Answer(choice=pick, probabilities=dist, confidence=round(dist[pick], 4))
            elif isinstance(q, Score):
                raw = []
                for level in q.criteria:
                    affinity = len(_words(level) & state_words)
                    raw.append(1.2 * affinity + rng.gauss(0, 1.0))
                probs = _softmax(raw, temp=0.9)
                expected = sum(i * p for i, p in enumerate(probs))   # score can sit between levels
                dist = {str(i): round(p, 4) for i, p in enumerate(probs)}
                answers[key] = Answer(score=round(expected, 3), probabilities=dist,
                                      confidence=round(max(probs), 4))
            elif isinstance(q, Noul):
                # Calibrated per question against the live model, not invented.
                # A mock that centres every Noul in the same place makes the fast
                # headless tuning loop lie, and every constant fitted against it
                # comes out wrong - which has happened twice in this project.
                suffix = key.rsplit("__", 1)[-1]
                mean, sd = NOUL_CALIBRATION.get(suffix, NOUL_DEFAULT)
                overlap = len(_words(q.instructions) & state_words)
                logit = mean + 0.12 * overlap + rng.gauss(0, sd)
                answers[key] = Answer(noul=round(1 / (1 + math.exp(-logit)), 4))
            else:
                raise TypeError(f"unknown question type for {key!r}: {type(q)}")

        self.requests += 1
        self.input_tokens += tokens
        if self.latency_ms:
            time.sleep(self.latency_ms / 1000)
        return Response(answers=answers, input_tokens=tokens,
                        latency_ms=(time.perf_counter() - t0) * 1000)


class RealClient:
    """Thin adapter over the official SDK so the sim sees one interface.

    Validated against the published SDK reference by `sdk_contract_test.py`,
    which fakes the SDK to the documented signatures and shapes. It has still
    never run against the live service - no key was available - so what remains
    unverified is behaviour, not shape:
      * whether ~50 questions in one request is accepted (the docs state a 64k
        context limit and no question cap; a scene sends 7 x up to 8 people);
      * real latency, and whether the tick time-box holds at 70-500ms per call.
    """

    is_mock = False

    def __init__(self) -> None:
        from typesafe_sdk import RetryPolicy, TypeSafeClient

        # The SDK retries for us by default: max_retries=2, backoff 0.5s doubling
        # to 5s, under a 30s total timeout. That is right for a request/response
        # service and wrong for a real-time loop - one failing call could block
        # a one-second tick for thirty seconds. Retrying is this simulation's job:
        # every tick re-picks its scenes, so a skipped scene costs nothing.
        self._c = TypeSafeClient(retry=RetryPolicy(max_retries=0), timeout=3.0)
        self.requests = 0
        self.input_tokens = 0
        self.estimated = 0        # calls where the API reported no token count
        self.errors = 0
        self._consecutive = 0
        self._blocked_until = 0.0

    def _call(self, state: Any, native: dict, model: str):
        # `model` is a documented keyword-only parameter of system_one.
        return self._c.system_one(state=state, questions=native, model=model)

    def system_one(self, state: Any, questions: dict[str, Any], model: str = MODEL) -> Response:
        from typesafe_sdk import Choice as SChoice, Noul as SNoul, Score as SScore

        native: dict[str, Any] = {}
        for key, q in questions.items():
            if isinstance(q, Choice):
                native[key] = SChoice(instructions=q.instructions, criteria=q.criteria)
            elif isinstance(q, Score):
                native[key] = SScore(instructions=q.instructions, criteria=q.criteria)
            elif isinstance(q, Noul):
                native[key] = (SNoul(instructions=q.instructions, criteria=q.criteria)
                               if q.criteria else SNoul(instructions=q.instructions))
            else:
                raise TypeError(f"unknown question type for {key!r}: {type(q)}")

        # A 24/7 stream cannot die on a rate-limit or a dropped connection - and
        # it must not stall either. An earlier version retried with sleeps inside
        # this call, which blocked the tick loop: with the API down the whole
        # simulation froze instead of degrading. So: never sleep here. Fail fast
        # and open a circuit breaker. The tick loop is already a retry loop,
        # because every tick re-picks its scenes.
        t0 = time.perf_counter()
        now = time.monotonic()
        if now < self._blocked_until:
            return Response(answers={}, input_tokens=0, latency_ms=0.0)

        try:
            r = self._call(state, native, model)
            self._consecutive = 0
        except Exception as exc:
            self.errors += 1
            self._consecutive += 1
            cooldown = min(30.0, 0.5 * 2 ** min(self._consecutive, 6))
            self._blocked_until = now + cooldown
            if self._consecutive in (1, 5, 25) or self._consecutive % 100 == 0:
                print(f"[jev] call failed ({self._consecutive} in a row), "
                      f"pausing {cooldown:.1f}s: {exc!r}")
            return Response(answers={}, input_tokens=0,
                            latency_ms=(time.perf_counter() - t0) * 1000)
        latency = (time.perf_counter() - t0) * 1000

        answers = {
            k: Answer(
                choice=getattr(a, "choice", None),
                score=getattr(a, "score", None),
                noul=getattr(a, "noul", None),
                confidence=getattr(a, "confidence", 0.0) or 0.0,
                probabilities=dict(getattr(a, "probabilities", {}) or {}),
            )
            for k, a in r.answers.items()
        }
        # Billed tokens live at r.usage.input_tokens, NOT r.input_tokens. Reading
        # the wrong attribute silently fell back to the ~4-chars/token estimate,
        # which would have left the whole dollar budget governing a guess instead
        # of the real spend. The estimate stays only as a last resort, and how
        # often it is used is counted so the HUD can admit it.
        usage = getattr(r, "usage", None)
        tokens = getattr(usage, "input_tokens", None) if usage is not None else None
        if tokens is None:
            tokens = request_tokens(state, questions)
            self.estimated += 1
        self.requests += 1
        self.input_tokens += tokens
        return Response(answers=answers, input_tokens=tokens, latency_ms=latency)


def get_client(seed: int = 7):
    """Real Jev when a key is present and the SDK is installed, mock otherwise."""
    if os.environ.get("TYPESAFE_API_KEY"):
        try:
            return RealClient()
        except ImportError:
            print("[jev] TYPESAFE_API_KEY set but typesafe-sdk not installed; falling back to mock")
    return MockClient(seed=seed)
