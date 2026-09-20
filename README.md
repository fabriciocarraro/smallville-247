# Smallville 24/7

A town that lives continuously — people pair off, cheat, are found out, fall ill and die —
on a Jev budget you set in dollars per day.

The Stanford *generative agents* paper ran 25 agents slowly and expensively because every
agent-decision was an LLM completion. Jev changes the unit economics: decisions are typed,
answered in parallel, and output is free. Population and spend are both dials: **50 people
at $2/day** by default, measured up to 600 at $32. The number is a setting, not a surprise.

How many should you run? [sweep.py](sweep.py) answers it by measurement, and the honest
answer is **fewer than you want to**. At a fixed $8/day, going from 150 to 300 agents halves
relationship depth and drops top rumour reach from 86% to 47%; 600 is bad at any budget
tested. Worse, tripling the budget at 300 agents buys reach and tempo but *not* intimacy —
`scene_priority` weights staleness, so more money buys breadth, not depth. Small towns have
better stories.

```bash
python run.py
```

50 people at $2/day, on `http://127.0.0.1:8765`. No dependencies — Python 3.10+ stdlib only.

Population and budget are independent dials, and the cost per *decision* is flat
(~520 tokens) regardless of population — scene batching holds at scale. So doubling the
town does not cost more per decision; it splits the same budget across twice as many
people. Measured over 10 simulated days:

| config | decisions/agent | friends/agent | couples formed |
|---|---:|---:|---:|
| 50 people @ $2/day | 122 | 6.29 | 9 |
| 100 people @ $2/day | 62 | 4.25 | 3 |
| 100 people @ $4/day | 126 | 6.43 | 10 |

`--budget 4` at 100 people reproduces the density of 50 people at $2 almost exactly.

```bash
python run.py --agents 150 --budget 8
```

```bash
python run.py --headless --ticks 2400
```

## Using real Jev

Set your key in your own shell and install the SDK. Nothing in the code changes — the badge
in the header flips from `MOCK JEV` to `LIVE JEV`.

```bash
pip install typesafe-sdk
```

`get_client()` in [jev.py](smallville/jev.py) returns `RealClient` as soon as
`TYPESAFE_API_KEY` is present in the environment, and `MockClient` otherwise. The mock is
not a language model — it scores options by lexical affinity with the state and softmaxes —
but it speaks the identical call/answer shape, so you can tune the whole simulation design
without spending anything.

## The two decisions that make this affordable

### 1. One request per *room*, not per agent

The naive design is one request per agent per think: 300 requests a round. Instead,
[scenes.py](smallville/scenes.py) groups co-located agents and puts **everyone in the room
into a single request** — the state (time, place, what just happened, who is here) is shared
anyway, and Jev answers all the questions in one parallel pass.

300 agents across 18 locations → **~18 requests instead of 300.**

Each agent contributes 6 questions to their room's request:

| key | type | what it decides |
|---|---|---|
| `act` | `Choice` | talk / confront / gossip / confide / help / work / withdraw / leave |
| `who` | `Choice` | which person present they direct it at |
| `to` | `Choice` | where they go *if* they leave — speculative, usually discarded |
| `mood` | `Score` | how they feel afterwards |
| `warm` | `Noul` | do they come out feeling closer to that person |
| `tell` | `Noul` | will they repeat what they just heard |

`to` is asked every time even though most agents don't leave. That is the documented
**speculative fan-out** pattern: parallel questions cost tokens but almost no time, and a
second round-trip would cost both.

### 2. Criteria are the cost, not the state

Profiling one 8-agent request:

```
state tokens      :  612   (18%)
questions total   : 2835   (82%)
  act   1075     who   626     to   591     mood  215     warm  175     tell  153
```

`act` dominated because its 8 option descriptions are repeated once per agent. Rewriting
them from sentences (`"confront someone here about something that angers them"`) to the
discriminating signal only (`"angry, public, about a grievance"`) took the request from
**3,447 → 1,774 tokens, a 49% cut**, with the option *names* still carrying the meaning.

If you add agents to a room, re-profile. Criteria duplication is superlinear in the thing
you are most tempted to make verbose.

That 1,774 is one profiled 8-agent scene. The **live fleet average is ~2,106 tokens/request**,
because scenes vary in size and because state grows as the town accumulates history —
`feels` widens with the trust graph and `town_news` carries more rumours on day 4 than on
day 1. Memory is capped (`maxlen=5`, two in state), so it converges rather than runs away,
but budget against the live average, not the profile.

## What actually binds: money, not the rate limit

Worth knowing before you build anything real-time on Jev — I had this backwards at first.

| | limit | what this sim uses |
|---|---|---|
| requests/min | 1,200 (≈20/s) | **~1.1/s** — 20x of headroom |
| cost | $0.042/MTok in, output free | **$8.13/day measured** |

Scene batching puts request rate so far under the account limit that it stops mattering.
The binding constraint is spend. So [budget.py](smallville/budget.py) is a **token bucket
priced in dollars**: you set `--budget 8`, it derives ~2,200 input tokens/sec, and scenes
compete for it.

They compete on `scene_priority()` — staleness, crowd size, and **friction** (how many
people in the room actively dislike each other). A crowded tavern after a fight earns a
request; one person alone at home does not. Skipped scenes aren't a failure mode, they're
the mechanism: in a typical run ~2,600 scenes are evaluated and ~28,000 are skipped.

Two things that took a debugging pass and are worth keeping:

- The bucket starts full (20s burst), so a *cumulative* cost average reads ~4x high for the
  first minute and paints the HUD red for no reason. `projected_daily_usd()` uses a rolling
  90-second window.
- On Windows, `SO_REUSEADDR` lets a second server bind the same port without erroring, so a
  stale process keeps serving old code while the new one looks healthy. Kill by command
  line, not by port.

## Jev decides, Python executes

No LLM is in the loop. `apply_scene()` turns typed answers into state changes
deterministically — trust deltas, mood, memory, rumour propagation. That is the documented
**cascade** pattern and it's why a tick is cheap and reproducible.

Confidence gating is used where it matters: a `confront` with `confidence < 0.45` degrades
into `withdraw` rather than starting a fight. Consequential actions get a higher bar,
exactly as the docs prescribe — and because RLCD calibration means higher confidence really
does mean higher accuracy, this is a meaningful filter rather than a superstition.

## The town is drawn, not diagrammed

Everything on screen is generated in code at load time — there are no image assets,
no sprite sheets, nothing downloaded. [web/index.html](web/index.html) is still a single
self-contained file.

**The map** is a 128×80 tile grid rendered once into an offscreen canvas: a harbour with
a ragged shoreline and breaking foam, roads that bow instead of running straight, a paved
square with a stone well, a terraced hill with a rock lip, a park with a pond, smallholdings,
picket fences, and woodland planted in **stands rather than confetti** — real tree cover
grows in clumps, and a dense treeline on the map edge makes the town read as enclosed.

Open spaces needed an explicit keep-clear radius. Hill, park and square have no building
footprint, so without one the woodland grew straight over them and they disappeared.

**Buildings** are procedural per style — chapel, tavern, civic, shop, tower, flats, bath,
stalls, docks. Each gets a front-facing roof that darkens from ridge to eave with tile
courses, an overhanging eave shadow, a wall face with boards, a door and windows, plus a
crown: a steeple, a radio mast, a hanging sign, an awning, columns, chimneys.

**People** are 11×16 sprites generated from a hash of each agent's id — skin, hair, hair
length, hat, trousers, boots — with the shirt keyed to their job, so you can read the town's
professions by colour. 300 agents × 2 walk frames are baked into one atlas at load.

The single biggest readability win was **outlining**. Each sprite is scanned after drawing
and every transparent pixel touching an opaque one is filled dark. Without it, 300 people
dissolve into the grass at wide zoom; with it, each one reads as a person.

**Light** tracks the simulated clock. At night a blue tint drops over the scene, every
window lights warm with a soft bloom, and lamp posts pool light around the square and the
main doors. It is the shot that sells the stream.

**The camera auto-cuts.** A 24/7 feed is unwatchable from orbit, so the newest event with
`heat > 0.62` wins a cut: the camera eases to that building, holds ~7s at 2.8×, then pulls
back to wide. A gold frame marks "on air".

- **click** — toggle auto-cut / wide
- **F** — punch in or out
- **S** — stream mode: map only, for an OBS window capture (fits a 16:10 window exactly)
- **hover** — who that person is

Agents also *walk*. They hold a stable standing spot per place (a phyllotaxis spiral, so a
crowd looks like a crowd and nobody swaps chairs every tick), the server steps them toward
it each tick, and the client interpolates at 60fps between the 1Hz updates. Squares use an
annulus rather than a disc — otherwise 35 people stand on top of the well and it vanishes.

Two things worth knowing if you extend it: the live frame is 25KB (names are served once
via `/api/roster`, not 300× per second), and the spot key uses `zlib.crc32` rather than
`hash()`, because Python salts string hashing per process and the whole town would reshuffle
on every restart.

## Couples, affairs, death

Three life layers sit on top of the trust graph. Together they cost **one extra question**.

### The whole thing runs on one Noul

```python
questions[f"{a.id}__draw"] = Noul(
    instructions=f"{a.name} is drawn to the person they dealt with, beyond friendship")
```

Measured: **+21 tokens per person per decision, +6% on a request.** That single answer
drives both couples and affairs, because the difference between them is not what the model
answers — it is whether the two people are already attached. Jev judges the pull; Python
decides what the pull means.

Attraction accumulates only on contact that went somewhere (`talk`, `confide`, `help`,
`gossip`), and decays daily, so a spark has to be fed to survive. When two people want each
other at `0.60` *mutually* and trust each other at `0.35` mutually:

- both single → **a couple**, announced to the town
- either attached → **an affair**, announced to nobody

### Tuning is measurement, not taste

The first build produced zero couples in ten simulated days. The instinct is to blame the
mock; the numbers said otherwise. `__draw` came back with mean **0.587** and a healthy
spread — 72% above the threshold. The fault was the *integration rate*: the most-seen pair
in town meets ~12× per 5 sim days, each meeting moved attraction by +0.044, and daily decay
of 0.035 ate most of it. Attraction plateaued at **0.418**, just under the 0.62 bar.

Raising the gain to 0.55 and easing decay to 0.022 fixed it, and the selectivity is the
point: a pair meeting 12× clears the bar, a pair meeting 6× does not. Couples stay rare
enough to mean something.

### Affairs are secrets, and the town already leaks secrets

An affair writes `is seeing X behind Y's back` into the cheater's `secret` field — which
means it drops straight into machinery that already worked: `confide` → a confidant with a
`nosy`/`bitter`/`jealous`/`reckless` trait leaks → rumour → bystanders overhear → spread.

Rumours carry `about` (the ids they concern) and `kind="affair"`, so when the betrayed
partner ends up in a rumour's believers, the sim detects it: trust crashes to −0.95, the
relationship ends, and the chronicle records who found out about whom.

The feed marks affairs with **"only you know"**. The viewer sees the betrayal the moment it
starts; the town finds out days later, or never. That gap is the engine of a soap.

A 25-day run, 50 people, $2/day, nobody scripted a line of it:

```
day  9  [ affair]  Dara Ferro and Omar Rue began an affair.
day  9  [ caught]  Iris Halloran found out about Dara Ferro and Omar Rue.
day 12  [together] Dara Ferro and Omar Rue are together.
```

### Story pace is coupled to spend

One simulated day is 720 ticks, so at the default one-second tick a day takes **12 real
minutes** and the first affairs land around day 3 — roughly 40 minutes into a stream. To
run the story faster you must also pay faster, because the governor is a token bucket in
*real* time: quarter-second ticks need 4× the tokens per second to keep the same tokens per
tick.

```bash
python run.py --tick 0.25 --budget 8
```

That is 3 real minutes per simulated day. Measured against the default over 25 sim days,
the dynamics hold up: 6 vs 8 couples, 11 vs 9 affairs, 8 vs 5 deaths, 396 vs 439 decisions
per agent — same ballpark, differences inside run-to-run variance. Changing `--tick` alone,
without the budget, does *not* preserve this.

### Death costs nothing

Aging, illness, accidents and mortality are pure Python — **Jev is never asked to kill
anyone**, so the only irreversible event in the sim is also the only free one. The hazard
rises with age, plus illness, plus a little for working the docks or the workshop, tuned to
~0.45 deaths per simulated day in a town of 50: someone dies roughly every half hour of real
time. Grief then propagates along the trust graph, partners are widowed, and a headstone
appears beside the chapel.

**Arrivals are not optional.** At that death rate a 50-person village empties inside a day
of streaming, so newcomers land at the docks — which is where the town's existing "nobody
knows who invited them" arc already lived. They also get two or three weak ties on arrival,
because an agent who knows nobody can never be gossiped about and would stay socially
invisible forever.

Over 25 simulated days: 6 new couples, 11 affairs, 3 exposed, 8 deaths, 8 arrivals,
population flat at 50, **$1.56/day actual against a $2 ceiling.**

One HUD bug worth keeping in mind if you fork this: the spend projection divided by the age
of the oldest retained sample rather than by the window length. In a small town requests are
bursty and that sample is often much younger than 90s, which overstated spend by ~2×. Divide
by the window.

## Audit: eight bugs, and what they teach

Every one of these was found by writing a test that tried to prove the bug existed, not by
reading the code and feeling uneasy. All eight are fixed and each fix has a check that fails
against the old behaviour.

**Adding mortality broke three things that had nothing to do with mortality.** That is the
lesson worth keeping: the death feature was correct; what broke were the systems that had
quietly assumed nobody ever leaves.

| # | bug | effect | measured |
|---|---|---|---|
| 1 | standing spot keyed on `enumerate()` index | every death visibly relocated most of the town | **44 of 49** people jumped → now **0** |
| 2 | client never dropped agents the server stopped sending | the dead stood on the map forever | `live.delete` appeared nowhere |
| 3 | sprite atlas baked once at page load | everyone who arrived later was **invisible** | `atlasIndex[newcomer] === undefined` |
| 4 | hover read `roster.people[id]` unguarded | hovering a newcomer threw on every mousemove | `Cannot read properties of undefined` |
| 5 | pairing up did not end a running affair | a partner with a live affair and no secret — undiscoverable forever | reproduced in 12 lines |
| 6 | death left the survivor's secret naming the dead | `"is seeing <someone in a grave> behind…"` could still leak | reproduced |
| 7 | a missing `__draw` answer defaulted to `0.0` | a dropped answer *destroyed* attraction (−0.24) instead of leaving it alone | now `None`, and skipped |
| 8 | rumours, chronicle, graves and dead agents never pruned | unbounded growth in a process meant to run for months | 929→200, 901→400, 200→60 |
| 9 | the live API path had no error handling at all — and my first fix *blocked* | a rate-limit would stall the whole tick loop | 200 failed calls: minutes → **1ms** |

Three of these (1, 2, 3) compound into the same outcome: run the stream for a day and the
visible town becomes entirely wrong — immortal ghosts standing among invisible newcomers.
None of it would have shown up in a short demo.

Details worth carrying to your own build:

- **Never key spatial state on list position.** `enumerate(self.living())` looks harmless
  until the list can shrink. It is now `spot_key(agent_id, place)` over `crc32`, which is
  also why it survives a restart — Python salts string `hash()` per process.
- **A client cache needs an eviction path from the day it is written.** `live` was a `Map`
  that only ever grew, and the atlas was built exactly once. Both were fine until the
  population stopped being fixed.
- **Pruning must preserve referential integrity.** Culling rumours orphans every
  `agent.believes` id pointing at them, so `_prune` intersects the sets afterwards
  (verified: 0 orphans) and protects `kind="affair"` rumours from the cull regardless of
  reach — those are load-bearing for the story.
- **A missing model answer is not a zero.** Treating "no answer" as the bottom of the scale
  is a silent data-quality bug that looks like tuning.
- **Never sleep inside a real-time loop.** Bug 9 is the interesting one, because the first
  fix caused it: retry-with-backoff is the reflex for a flaky API, but sleeping inside
  `system_one` blocks the tick. With the API down the simulation froze rather than degrading.
  The tick loop is *already* a retry loop — every tick re-picks its scenes — so the client
  fails fast and opens a circuit breaker instead. Verified: 200 consecutive failures cost
  1ms and exactly one real API attempt, 600 ticks run in 0.87s with the API dead, and the
  breaker resets cleanly (250 requests, 0 errors) once it recovers.

## Validating against the official docs

The integration was built from the docs read at the start, then never re-checked — so it
was audited a second time, line by line, against the published SDK reference. That pass
found **two critical defects that a mock can never catch**, because they live in the
adapter between our code and the SDK, which the mock replaces wholesale.

**Billed tokens are at `r.usage.input_tokens`, not `r.input_tokens`.** The adapter read the
wrong attribute and silently fell back to the ~4-chars/token estimate. Everything else here
works — the token bucket, the dollar ceiling, the HUD — but on the live API the entire cost
control would have been governing a *guess* instead of real spend. That is the headline
feature of the project, running blind. The fallback now exists only when the API reports no
usage, and how often that happens is counted and surfaced.

**The SDK retries with blocking backoff by default.** `max_retries=2`, backoff 0.5s doubling
to 5.0s, under a 30s total timeout. Perfect for a request/response service, wrong for a
one-second tick: a single failing call could stall the whole simulation for thirty seconds,
which silently voids the non-blocking circuit breaker from the previous audit. The client
now constructs with `RetryPolicy(max_retries=0)` and a 3s timeout, and retrying stays where
it belongs — the tick loop, which re-picks its scenes every second anyway.

Two smaller things came out of the same pass:

- **`Noul` accepts `criteria`** naming what yes and no mean. The model card for jev-1.13
  says it answers literally and struggles with implied conditions, so all three Nouls now
  spell out both poles. Cost: 933 → 978 tokens per request, about 5%.
- **The tick is now time-boxed.** The mock answers in ~1ms, so a burst of scenes is free;
  real Jev is 70–500ms and the burst bucket can authorise ten scenes at once. Sequentially
  that is seconds of overrun on a one-second tick. Scenes past 60% of the tick budget are
  deferred to the next tick rather than dropped.

### The contract test

[sdk_contract_test.py](sdk_contract_test.py) encodes the documentation as an executable
fake — strict keyword-only signatures, `Usage`, `NoulAnswer` with no `confidence`,
`ScoreAnswer.probabilities` keyed by int — and asserts the adapter satisfies it. **15/15
pass.** Reintroducing the two defects above drops it to 9/15, so it has teeth.

```bash
python sdk_contract_test.py
```

### Then the key arrived, and the live model rewrote half the tuning

It runs live now. Everything below was found in the first twenty minutes and
under two cents of billed calls — and **none of it was findable against a mock**.

**`NoulCriteria` keys are `true`/`false`, not `yes`/`no`.** The prose says "the
yes and no outcomes", which is the meaning, not the key names — it is a TypedDict
that rejects anything else. Every request would have failed pydantic validation on
first contact. Reading the docs was not enough; installing the SDK and inspecting
the real types was.

**Our token estimate is 1.89× low.** The governor reserves budget with the
estimate and is charged the real figure, so it would have let through nearly twice
what it could afford. The reservation is now scaled by a ratio seeded from the
measurement and adapted from what is actually billed — live it converged to 1.83–1.86
on its own.

**Latency is flat at ~350ms from 14 to 56 questions.** The parallel pass is real:
scene batching costs tokens, not time, so bigger scenes are strictly better. The
one 952ms call was a cold start.

**Every Noul threshold in the codebase was dead.** Real Noul answers centre at
**0.238** with sd 0.106 and a maximum of 0.54 — the mock centred at 0.587. Thresholds
written as 0.5 / 0.55 / 0.62 / 0.72 all sat above the observed maximum, so no talk
was ever warm, nothing was confided, and not one affair could be exposed. They are now
expressed as measured quantiles of the live distribution, and the mock has been
re-fitted to match (mean 0.247, sd 0.105) so fast headless tuning is predictive again.

**The state was handing the model its own answer.** 79% of agents chose `withdraw`.
The cause was one field: `doing`, which for half the town reads "keeping to
themselves" — the `withdraw` option, restated. Measured across four live variants:

| variant | social actions |
|---|---|
| terse criteria, `doing` present (as shipped) | 12% |
| richer criteria, `doing` present | **0%** |
| richer criteria, no `doing` | 50% |
| richer criteria, no `doing`, reframed question | **62%** |

Richer criteria alone made it *worse*. Removing one field fixed it. Note this also
reverses the earlier "criteria are the cost, trim them hard" optimisation: the 49%
token cut measured fine against the mock and cost the live model its ability to
discriminate.

**And half of what survived was still thrown away.** `who` offered a `nobody`
option, and the live model took it ~50% of the time — which turns a chosen social
action into a move. The ways to not engage already live in `act`, so `who` is now a
forced choice among people in the room. Measured: 43% targeted with `nobody`, 100%
without. In the live town this took social events from 3 per 366 decisions to 40.

Live right now: **$1.98/day against a $2 ceiling**, p50 314ms, 1806 tokens/request,
zero fallbacks to the estimate.

### What still cannot be settled from here whether ~50 questions in one
request is accepted, real latency, and — the big one — **whether Jev's answer distributions
resemble the mock's.** Every tuning constant in `scenes.py` was fitted against a mock whose
`__draw` noul happened to average 0.587. If the real model centres somewhere else, couples
and affairs will fire at the wrong rate and want re-tuning. The measurement scripts are
there to redo it in minutes.

## Running it for real

### The town survives a restart

[persistence.py](smallville/persistence.py) saves everything that makes the place
continuous — the trust graph, who is with whom, who is cheating, the graveyard, the
rumours in flight, the chronicle — and restores it on start. Without it a 24/7 stream
loses its whole premise: nobody follows a soap that resets to day 1 on every deploy.

Saves are atomic (temp file, then rename) so a crash mid-write leaves the previous good
save intact. A 50-person town with 13 simulated days is ~266 KB. Verified by round trip:
the restored town matches the original exactly, down to the sum of every trust edge.

```bash
python run.py --fresh
```

Live spend is deliberately *not* restored — the budget is per real day, so a town resumed
tomorrow starts with a fresh allowance.

### A voice over the top

[narrator.py](smallville/narrator.py) turns the blotter into broadcast. Jev decides but
does not write, so the raw feed reads like a police log; the narrator gives the stream its
lower third. Three rules, each one a scar from earlier in this project:

- **Its own thread.** The tick loop already learned what a blocking network call does.
- **Event-driven, not clock-driven.** It speaks on deaths, exposures, affairs, fights and
  new couples, so every line lands on something and the cost is bounded by the drama.
- **Governed in dollars**, because an unattended process calling a paid API on a loop needs
  a ceiling it cannot talk its way past.

It runs on **`gpt-4.1-nano`** by default - a model with no reasoning at all, which
suits a job that is one sentence of prose with nothing to think about. Measured per line, on the same event:

| model | per line | reasoning | the line it wrote |
|---|---:|---|---|
| `gpt-5-nano` | $0.00004 | effort `minimal` | *…which tightens the town's rumor mill around who's in what* |
| `gpt-4.1-nano` | $0.00005 | none at all | *…a revelation that may ripple through their delicate ties* |
| `gpt-5.6-luna` | $0.00011 | effort `none` | *Dara Ferro now joins the growing circle of people learning that romance in this town rarely stays private for long.* |
| `gpt-4.1-mini` | $0.00021 | none at all | *…confirms what half the town was whispering* |

All four are cents a day. Luna costs about three times nano and writes noticeably better
prose; that is the whole trade. `--narrator-model` switches it.

**Effort values are not portable, and the model pages do not list them.** The API's own
400 is the authority. Measured against `gpt-5-nano`:

| effort | output tokens | of which reasoning | text produced |
|---|---:|---:|---|
| `none` | — | — | **rejected, 400** |
| `minimal` | 53 | 0 | yes |
| `low` / `medium` / `high` | 192 | 192 | **nothing** — reasoning ate the entire budget |
| `xhigh` / `max` | — | — | **rejected, 400** |

So `minimal` is not merely the cheapest level on nano, it is the only usable one at this
`max_output_tokens`: at `low` and above the model spends the whole allowance thinking and
returns an empty string, which costs three times as much for no line at all. `minimal`
also bills **zero reasoning tokens**, which is the practical "no reasoning" this app wants
— OpenAI documents no way to disable reasoning on a reasoning model, only to pick a
non-reasoning one.

Because of all that, the narrator **adapts itself to whatever model it is given**, in a
loop rather than a single retry — `gpt-4.1-nano` needs two separate corrections:

- *"Unsupported **value**: 'none'…"* → step down the effort ladder (`none` → `minimal`)
- *"Unsupported **parameter**: 'reasoning.effort'"* → drop reasoning entirely. These two
  read alike and need opposite responses; stepping the ladder on a model that cannot reason
  just fails again with a different value.
- *"…'low' is not supported…"* on `text.verbosity` → drop that too

Two more settings, verified against the installed SDK rather than assumed:

- `store=False` — this runs unattended for days; don't accumulate transcripts of a town's
  private life in a dashboard.
- `prompt_cache_key` — kept, but **measured live it never actually caches**: OpenAI's
  prompt cache needs a prefix of ~1024 tokens and the instructions are a few hundred, so
  `cached_tokens` came back 0 on every call. It starts working if the prompt grows.

It falls back to free templates when `OPENAI_API_KEY` is absent rather than going silent.

```bash
python run.py --narrate-budget 2 --narrator-model gpt-5.6-luna
```

[try_model.py](try_model.py) checks a candidate model against the narrator's exact request
shape before it becomes the default; [compare_efforts.py](compare_efforts.py) produced the
effort table above.

**On signing in with a Codex/ChatGPT account:** it does not work for this. OpenAI's auth
documentation is explicit — *"For general OpenAI API calls, continue to use Platform API
keys."* Codex OAuth grants a workspace-scoped credential for Codex itself, not for the
Responses API. Community plugins do reuse those tokens against other endpoints, but that is
unsupported, breaks without notice, and stretches terms written for a subscription product.
At Luna's prices the subscription angle is not worth it anyway: set `OPENAI_API_KEY` from
platform.openai.com and a day of narration costs less than a coffee.

### Hosting

The sim is a **persistent stateful process** — a one-second tick loop holding a live world
and an SSE stream. That single fact decides the platform:

| | verdict | why |
|---|---|---|
| Cloudflare Pages / Workers | **no** | static hosting plus request-scoped functions. There is nowhere for a tick loop to live, and no process to hold the town between requests. |
| Netlify | **no** | same shape: static site plus short-lived functions. |
| Zeabur (or any VPS/container host) | **yes** | a long-running container is exactly what this needs. |

A [Dockerfile](Dockerfile) is included. The image is small — stdlib Python plus two SDKs.
Two things matter at deploy time:

- **Mount a volume at `/data`.** Without it the container's filesystem is ephemeral and
  every redeploy resets the town to day 1.
- **Set `TYPESAFE_API_KEY`** (and `OPENAI_API_KEY` for narration) as service secrets.
  `HOST` and `PORT` are read from the environment already.

Note the server binds `127.0.0.1` by default and refuses to start if the port is already
serving — that guard exists because Windows `SO_REUSEADDR` let a stale process keep
answering while a restart looked healthy, which cost time twice in this project.

### Streaming it

Hosting the sim is not the same as streaming it. The audience watches video, not the page,
so something must render and capture a browser. Two stages:

1. **Sim hosted, stream from your desk.** The town lives 24/7 on Zeabur with its history
   intact; you point a browser at the hosted URL in stream mode (`S`) and run OBS when you
   want to broadcast. Simple, and the continuity is preserved either way.
2. **Everything in the container.** Add headless Chromium and ffmpeg pushing RTMP to
   Twitch. Truly unattended, meaningfully more moving parts.

Start at 1. The hard-won part — a town whose history survives — is already done.

## Why rumours are the shot that travels

The map and the feed are nice. The panel people will screenshot is **rumour reach**.

A rumour told out loud in a room is overheard by bystanders (35% each), which turns linear
telling into an exponential curve. In a 4-sim-day headless run:

```
148 believe (49.3%): Amara Osei plans to leave without telling anyone
 91 believe (30.3%): Sven Solberg admitted: is not who they said they were
 39 believe (13.0%): Enzo Ferro is in love with someone who is taken
```

Nobody wrote that storyline. A secret was confided, one character with a `nosy`/`bitter`/
`jealous`/`reckless` trait leaked it, and it crossed half the town on its own. Secrets only
leak through characters who plausibly leak — without that gate every secret escaped and the
feed became one long betrayal.

## Files

The simulation:

| | |
|---|---|
| [jev.py](smallville/jev.py) | client + mock, token accounting, pricing constants |
| [scenes.py](smallville/scenes.py) | request building and deterministic execution, the core |
| [budget.py](smallville/budget.py) | dollar-priced token bucket, scene prioritisation |
| [sim.py](smallville/sim.py) | tick loop, routine movement, snapshot for the UI |
| [agents.py](smallville/agents.py) | identity, memory, trust graph, attraction, mortality |
| [world.py](smallville/world.py) | 18 locations, clock, rumours, town arcs |
| [persistence.py](smallville/persistence.py) | atomic save and load, so a restart resumes the same town |
| [narrator.py](smallville/narrator.py) | the writing layer: own thread, own budget, template fallback |

Running it:

| | |
|---|---|
| [run.py](run.py) | entry point and flags |
| [server.py](smallville/server.py) | stdlib HTTP + SSE, `/api/roster` (static) + `/api/stream` |
| [web/index.html](web/index.html) | the whole renderer: terrain, buildings, sprites, camera |
| [Dockerfile](Dockerfile) | the image, and the volume the town has to be saved on |

The measurements. Every number in this README came out of one of these, and
they are here so you can re-run them rather than take my word for it:

| | |
|---|---|
| [sweep.py](sweep.py) · [sweep2.py](sweep2.py) | scene batching economics: what one request per room actually buys |
| [life_test.py](life_test.py) · [pop_test.py](pop_test.py) | couples, affairs and death over simulated time; population stability |
| [sdk_contract_test.py](sdk_contract_test.py) | the documented SDK as an executable contract |
| [live_smoke.py](live_smoke.py) · [live_calibrate.py](live_calibrate.py) | first contact, then latency/token/distribution calibration |
| [live_action_test.py](live_action_test.py) · [live_who_test.py](live_who_test.py) | the two live A/B tests that found the silent-town bugs |
| [live_menu_test.py](live_menu_test.py) | the hypothesis that impossible options caused it, which the model disproved |
| [live_content_test.py](live_content_test.py) | the separate content question that ended the silent town |
| [live_determinism_test.py](live_determinism_test.py) | the same request three times, to see what moves |
| [live_narrate_test.py](live_narrate_test.py) · [try_model.py](try_model.py) | first contact with a narrator model, and vetting a replacement |
| [compare_efforts.py](compare_efforts.py) | which reasoning effort the narrator should use, asked of the API rather than the docs |
| [narrator_resilience_test.py](narrator_resilience_test.py) | what the town does when the writing API dies |
| [collect_hour.py](collect_hour.py) · [audit_hour.py](audit_hour.py) | collect a long run to JSONL, then audit it for drift, drama and narration tics |

Collected run data is not committed. It is generated, not authored, so
`collect_hour.py` regenerates it and `audit_hour.py` reads whatever file you
point it at.

## Before you stream it

- **Verify the cost against real Jev for an hour before leaving it running.** Every number
  above is measured against the mock with an estimator (~4 chars/token); the real client
  reports exact token counts, and the projection will shift.
- The mock over-picks `confide` because of how lexical affinity lands. Expect the behaviour
  mix to change — probably improve — on the real model. Re-tune `scene_priority` weights then,
  not now.
- Jev is text-only and reads literally. Don't move counting, date arithmetic, or anything
  code can compute exactly into a question.
- Nothing here generates prose. If you want narration over the stream, that's a second,
  cheap LLM reading the event feed — budget it separately.
