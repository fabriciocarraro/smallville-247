"""
The voice over the top.

Jev decides; it does not write. So the raw feed reads like a police blotter -
"Iris Halloran and Cato Dahl talked at Market Row" - which is legible but not
watchable. This turns the blotter into broadcast: one or two lines of commentary
that know who these people are and what just changed between them.

Runs on gpt-4.1-nano by default: a model that does not reason at all, which is
what this job wants - one sentence of prose, nothing to think about. Measured
live at $0.00005 per line, against $0.00004 on gpt-5-nano and $0.00012 on
gpt-5.6-luna, whose prose is the best of the three. All of them are cents a day,
which is what lets it speak whenever something happens instead of rationing
itself. Latency is 1-3s, which is why it lives on its own thread.

Three design rules, each one a scar from earlier in this project:

  * It runs on its own thread. Narration is a network call of unpredictable
    latency, and the tick loop already learned what happens when you block it.
  * It is event-driven, not clock-driven. It speaks on deaths, exposures,
    affairs, fights and new couples, so every line lands on something.
  * It is governed in dollars, like the Jev side, because an unattended process
    that calls a paid API on a loop needs a ceiling it cannot talk its way past.

Without OPENAI_API_KEY it degrades to templates rather than going silent.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time

MODEL = "gpt-4.1-nano"

# $ per 1M tokens (input, cached input, output), from each model's page on
# developers.openai.com. Hardcoding one model's rates and then switching models
# silently makes every cost readout wrong, so the table is keyed by model.
PRICING = {
    "gpt-5-nano":   (0.05, 0.005, 0.40),
    "gpt-4.1-nano": (0.10, 0.025, 0.40),
    "gpt-4.1-mini": (0.40, 0.10,  1.60),
    "gpt-5-mini":   (0.25, 0.025, 2.00),
    "gpt-5.6-luna": (0.20, 0.02,  1.20),
    "gpt-5.6-sol":  (1.25, 0.125, 10.00),
}
FALLBACK_PRICING = (0.20, 0.02, 1.20)

# Effort values are not uniform across models and the model pages do not list
# them; the API's own 400 is the authority. Measured against gpt-5-nano:
#
#   effort     out  reasoning  text produced
#   none         -      -      rejected, 400
#   minimal     53      0      yes
#   low/med/hi 192    192      NOTHING - reasoning ate the whole output budget
#   xhigh/max    -      -      rejected, 400
#
# So the ladder stops at "minimal". Stepping further to "low" would not be a
# fallback, it would be an outage that bills three times as much for an empty
# string. "minimal" also bills zero reasoning tokens, which is the practical
# "no reasoning" this app wants.
EFFORT_LADDER = ["none", "minimal"]

# Stable across every call, so it caches. Keep it that way - anything volatile
# in here (a timestamp, a name, a count) would break the prefix and cost 10x.
SYSTEM = """You narrate a simulated town for a live stream. Its people are \
software agents; their choices come from a model, and nobody scripted what \
happens.

You are handed the events that just occurred and the standing facts needed to \
read them. Write ONE sentence - two only if the second genuinely earns its place.

Voice: a wry, warm observer who has watched this town for weeks and knows \
everyone. Present tense. Specific. Never breathless.

Hard rules:
- Use only what the input states. Never invent a fact, a motive, or a name.
- No moralising, no advice, no addressing the viewer, no "meanwhile".
- Do not restate the event verbatim - say what it MEANS given the history given.
- No preamble and no surrounding quotation marks. Output the line only."""

CACHE_KEY = "smallville-narrator-v1"


def _template(events: list[dict]) -> str:
    """Zero-cost fallback so the caption bar is never empty."""
    e = max(events, key=lambda x: x["heat"])
    t = e["text"].rstrip(".")
    return {
        "death": f"{t}. The chapel bell again.",
        "caught": f"{t}. It was always going to come out.",
        "affair": f"{t} And nobody else knows yet.",
        "conflict": f"{t}. That one will leave a mark.",
        "bond": f"{t}.",
        "rumour": f"{t}. It will be all over town by evening.",
        "arrival": f"{t}",
    }.get(e["kind"], f"{t}.")


class Narrator:
    """Turns the event feed into commentary, on its own thread, on a budget."""

    NARRATABLE = {"death", "caught", "affair", "conflict", "bond", "rumour", "arrival"}
    MIN_HEAT = 0.55

    def __init__(self, daily_budget_usd: float = 1.0, model: str = MODEL,
                 cooldown_s: float = 12.0):
        self.model = model
        rates = PRICING.get(model)
        if rates is None:
            rates = FALLBACK_PRICING
            print(f"[narrator] no pricing on file for {model}; costs shown are "
                  f"estimates at gpt-5.6-luna rates")
        self.usd_in, self.usd_cached, self.usd_out = (r / 1_000_000 for r in rates)
        self._effort = EFFORT_LADDER[0]
        self._verbosity = True          # gpt-4.1-* reject text.verbosity entirely
        self._reasoning = True          # non-reasoning models reject the param itself
        self._warned_empty = False
        self._told: list[str] = []      # rumours recently put in front of the model
        self.daily_budget_usd = daily_budget_usd
        self.cooldown_s = cooldown_s
        self.lines: list[dict] = []
        self.spent_usd = 0.0
        self.calls = 0
        self.errors = 0
        self.cached_in = 0
        self.live = False
        self._q: queue.Queue = queue.Queue(maxsize=32)
        self._seen: set[str] = set()
        self._last_at = 0.0
        self._window: list[tuple[float, float]] = []
        self._client = None
        self._running = False

        if os.environ.get("OPENAI_API_KEY"):
            try:
                from openai import OpenAI
                self._client = OpenAI()
                self.live = True
            except ImportError:
                print("[narrator] OPENAI_API_KEY set but the openai SDK is not "
                      "installed (pip install openai); using templates")
        if not self.live:
            print("[narrator] no OPENAI_API_KEY; narrating from templates (free)")

    # --- budget, in dollars, over a rolling hour ---------------------------
    def _affordable(self) -> bool:
        now = time.monotonic()
        self._window = [(t, u) for t, u in self._window if t >= now - 3600]
        return sum(u for _, u in self._window) < self.daily_budget_usd / 24.0

    @property
    def projected_daily_usd(self) -> float:
        now = time.monotonic()
        return sum(u for t, u in self._window if t >= now - 3600) * 24

    # --- the sim calls this; it must never block ---------------------------
    def offer(self, events: list, town) -> None:
        picked = [
            {"kind": e.kind, "text": e.text, "heat": e.heat, "time": e.time_label,
             "actors": list(e.actors)}
            for e in events
            if e.kind in self.NARRATABLE and e.heat >= self.MIN_HEAT
            and (e.time_label + e.text) not in self._seen
        ]
        if not picked:
            return
        for e in picked:
            self._seen.add(e["time"] + e["text"])
        if len(self._seen) > 4000:
            self._seen = set(list(self._seen)[-1500:])

        if time.monotonic() - self._last_at < self.cooldown_s:
            return
        self._last_at = time.monotonic()
        try:
            self._q.put_nowait((picked, self._context(picked, town)))
        except queue.Full:
            pass                                   # drama outpacing the narrator

    def _context(self, events: list[dict], town) -> dict:
        """Only the standing facts needed to read these events.

        Picking the three loudest rumours every time hands the model the same
        material on every call, and it writes the same line: one live hour
        produced "the workshop break-in" 46 times in 175 lines. Prefer stories
        about the people in THESE events, then fill with ones not used recently.
        """
        actors = {a for e in events for a in e["actors"]}
        pool = sorted(town.rumours.values(), key=lambda r: -r.reach)
        about_them = [r for r in pool if r.about & actors]
        others = [r for r in pool if r not in about_them and r.id not in self._told]
        rumours = (about_them + others)[:3]
        self._told = ([r.id for r in rumours] + self._told)[:8]
        return {
            "time": town.clock.label(),
            "recently_in_town": [c["text"] for c in town.chronicle[-6:]],
            "loudest_rumours": [f"{r.text} ({r.reach} believe it)"
                                for r in rumours if r.reach > 1],
            "graves": len(town.graves),
        }

    def _compose(self, events: list[dict], context: dict) -> str:
        payload = {
            "just_happened": [f"[{e['kind']}] {e['text']}" for e in events],
            "the_time": context["time"],
            "this_town_recently": context["recently_in_town"],
            "what_people_are_saying": context["loudest_rumours"],
            "buried_so_far": context["graves"],
        }
        # Models disagree about this request in more than one way, and a model
        # can need several corrections: gpt-4.1-nano rejects the reasoning
        # parameter AND text.verbosity. So adapt in a loop rather than retrying
        # once - a single retry left every 4.1 call failing on the second
        # complaint after the first was fixed.
        for _ in range(4):
            try:
                r = self._create(payload)
                break
            except Exception as exc:
                if not self._adapt(str(exc)):
                    raise
        else:
            raise RuntimeError(f"{self.model} refused every request shape tried")

        u = r.usage
        cached = getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0
        fresh = max(0, (u.input_tokens or 0) - cached)
        cost = (fresh * self.usd_in + cached * self.usd_cached
                + (u.output_tokens or 0) * self.usd_out)
        self.spent_usd += cost
        self.cached_in += cached
        self._window.append((time.monotonic(), cost))
        self.calls += 1

        text = (r.output_text or "").strip()
        think = getattr(getattr(u, "output_tokens_details", None), "reasoning_tokens", 0) or 0
        if not text and not self._warned_empty:
            self._warned_empty = True
            print(f"[narrator] {self.model} returned no text: {think} of "
                  f"{u.output_tokens} output tokens went to reasoning. Lower the "
                  f"effort or raise max_output_tokens - falling back to templates.")
        return text

    def _adapt(self, msg: str) -> bool:
        """Answer one API complaint by changing the request. True if something
        changed and a retry is worth it.

        Two refusals that read alike need opposite responses:
          "Unsupported VALUE: 'none' is not supported"  -> step the effort ladder
          "Unsupported PARAMETER: 'reasoning.effort'"   -> drop it entirely
        Stepping the ladder on a model that does not reason at all just fails
        again with a different value.
        """
        if "reasoning" in msg and "nsupported parameter" in msg and self._reasoning:
            self._reasoning = False
            print(f"[narrator] {self.model} does not reason at all; "
                  f"dropping the reasoning parameter")
            return True
        if "verbosity" in msg and self._verbosity:
            self._verbosity = False
            print(f"[narrator] {self.model} does not take text.verbosity; dropping it")
            return True
        if ("effort" in msg or "nsupported value" in msg) and self._reasoning                 and self._effort in EFFORT_LADDER[:-1]:
            self._effort = EFFORT_LADDER[EFFORT_LADDER.index(self._effort) + 1]
            print(f"[narrator] {self.model} rejected that reasoning effort; "
                  f"using '{self._effort}'")
            return True
        return False

    def _create(self, payload: dict):
        return self._client.responses.create(
            model=self.model,
            instructions=SYSTEM,
            input=json.dumps(payload, ensure_ascii=False, indent=1),
            # One short sentence of prose: there is nothing here to reason about,
            # and reasoning tokens are billed at the output rate.
            **({"reasoning": {"effort": self._effort}} if self._reasoning else {}),
            **({"text": {"verbosity": "low"}} if self._verbosity else {}),
            max_output_tokens=220,
            # Measured live: this never actually caches. OpenAI's prompt cache
            # needs a prefix of ~1024 tokens and our instructions are a few
            # hundred, so cached_tokens came back 0 on every single call. Kept
            # because it costs nothing and starts working if the instructions
            # ever grow - but do not count on the cached rate at this size.
            prompt_cache_key=CACHE_KEY,
            # This runs unattended for days; don't accumulate transcripts of a
            # town's private life in someone's dashboard.
            store=False,
        )

    def _worker(self) -> None:
        while self._running:
            try:
                events, context = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            line, source = None, "template"
            if self.live and self._affordable():
                try:
                    line, source = self._compose(events, context), self.model
                except Exception as exc:
                    self.errors += 1
                    if self.errors in (1, 5, 25) or self.errors % 100 == 0:
                        print(f"[narrator] call failed ({self.errors}): {exc!r}")
            if not line:
                line, source = _template(events), "template"
            self.lines.append({"text": line, "at": context["time"],
                               "kind": max(events, key=lambda e: e["heat"])["kind"],
                               "source": source})
            del self.lines[:-60]

    def start(self) -> threading.Thread:
        self._running = True
        t = threading.Thread(target=self._worker, daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._running = False

    def snapshot(self) -> dict:
        return {
            "lines": self.lines[-12:][::-1],
            "live": self.live,
            "model": self.model if self.live else "templates",
            "calls": self.calls,
            "spent_usd": round(self.spent_usd, 5),
            "projected_daily_usd": round(self.projected_daily_usd, 3),
            "budget_usd": self.daily_budget_usd,
            "cached_in": self.cached_in,
            "errors": self.errors,
        }
