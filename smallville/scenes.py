"""
Scene batching - the thing that makes 300 agents affordable.

Naive design: one request per agent per think  -> 300 requests/round.
Here:         one request per *place*, carrying every co-located agent's
              questions, all answered in one parallel pass -> ~18 requests/round.

Two documented patterns do the work:
  * speculative fan-out  - ask `destination` even when the agent may not leave;
    parallel questions cost tokens but almost no time, and output is free.
  * cascade              - Jev decides, plain Python executes. No LLM in the loop.
"""
from __future__ import annotations

import random

from .agents import MOOD_LEVELS, Agent
from .jev import Choice, Noul, Score
from .world import LOCATIONS, PUBLIC, Event, Town

MAX_AGENTS_PER_REQUEST = 8      # keeps state small; docs warn accuracy drops with bloat
MEMORIES_IN_STATE = 2

# Criteria repeat once per agent, so they are the dominant token cost. An earlier
# version trimmed them to fragments to save 35%, which measured fine against the
# mock and was wrong: the live model needs a full clause per option to tell them
# apart. Measured on real calls, terse criteria gave 12% social actions and full
# ones 62%.
ACTIONS = {
    "talk":        "starts an ordinary conversation with someone in the room",
    "confront":    "challenges someone out loud about something they resent",
    "gossip":      "repeats news or a rumour about a third person to someone here",
    "confide":     "tells one trusted person something private about themselves",
    "help":        "does something practical for another person here",
    "work":        "gets on with their own job and speaks to no one",
    "withdraw":    "deliberately stays apart because they want to be left alone",
    "leave":       "walks out of this place entirely",
}


def _feelings(agent: Agent, present: list[Agent]) -> dict[str, str]:
    out = {}
    for other in present:
        if other.id == agent.id:
            continue
        v = agent.trust.get(other.id)
        if v is None:
            continue
        if v > 0.45:
            out[other.name] = "trusts them"
        elif v < -0.3:
            out[other.name] = "resents them"
        elif v > 0.1:
            out[other.name] = "friendly"
    return out


def _destinations(agent: Agent, rng: random.Random) -> dict[str, str]:
    picks = {agent.home: "home", agent.workplace: "work", "square": "the square"}
    for k in rng.sample(PUBLIC, 3):
        picks.setdefault(k, LOCATIONS[k]["label"])
    picks.pop(agent.at, None)
    return dict(list(picks.items())[:6])


# Always available to anyone standing in a room with other people.
ALWAYS = ("talk", "help", "work", "withdraw", "leave")

# What a conversation is carrying.
#
# Asking "what do they do" with `talk` and `gossip` both on the menu makes the
# model rank overlapping options, and `talk` is never wrong - gossiping IS an
# ordinary conversation. Live, that produced 114 consecutive `talk` events: no
# rumours, no confidences, no fights, nothing for the narrator to say. Trimming
# the menu did not help (gossip was available to all 50 and chosen zero times).
#
# So stop competing with the truth. Let `talk` win, then ask separately what the
# conversation carries. Measured live: 0% of conversations carried a story under
# the old design, 15% under this one.
CONTENT = {
    "small_talk": "the weather, work, nothing that will be repeated",
    "news":       "passes on something they heard about a third person",
    "personal":   "tells them something private about themselves",
    "grievance":  "raises something the other person did that annoyed them",
    "kindness":   "does or offers something practical for them",
}
CONTENT_ACTION = {"news": "gossip", "personal": "confide",
                  "grievance": "confront", "kindness": "help"}


def _menu(a: Agent, present: list[Agent]) -> dict[str, str]:
    """The actions this person can actually take, right now.

    Offering the whole list to everyone meant offering impossible things: an
    agent who believes no rumour was still asked whether they gossip, and on the
    first morning that is everyone. Live, the model answered the only option it
    could always justify - `talk` - and the town produced 114 consecutive `talk`
    events, no rumours, and nothing for the narrator to say. Removing the
    impossible options concentrates the probability on the real ones.
    """
    menu = {k: ACTIONS[k] for k in ALWAYS}
    if a.believes:
        menu["gossip"] = ACTIONS["gossip"]
    if a.secret:
        menu["confide"] = ACTIONS["confide"]
    if any(a.trust.get(o.id, 0) < -0.3 for o in present if o.id != a.id):
        menu["confront"] = ACTIONS["confront"]
    return menu


def build_scene_request(place: str, present: list[Agent], town: Town, rng: random.Random,
                        agents: dict | None = None):
    """Returns (state, questions, name_to_id). One request covers the whole scene."""
    loc = LOCATIONS[place]
    here = town.recent(3, place=place)
    agents = agents or {a.id: a for a in present}
    partner_names = {a.id: (agents[a.partner].name if a.partner in agents else None)
                     for a in present if a.partner}

    state = {
        "time": town.clock.label(),
        "part_of_day": town.clock.phase,
        "place": {"name": loc["label"], "feel": loc["vibe"]},
        "town_news": town.headlines(2),
        "just_happened_here": [e.text for e in here] or ["nothing yet today"],
        "people_here": [
            {
                "name": a.name,
                "who": a.one_line(),
                "mood": a.mood_word(),
                # `doing` used to sit here. Half the town is "keeping to
                # themselves" at any moment, which is the `withdraw` option
                # restated - and the model dutifully echoed it back. Removing
                # this one field took social actions from 12% to 50%.
                "remembers": list(a.memory)[-MEMORIES_IN_STATE:],
                "feels": _feelings(a, present),
                **({"with": partner_names.get(a.id)} if partner_names.get(a.id) else {}),
                **({"private": a.secret} if a.secret else {}),
            }
            for a in present
        ],
    }

    questions: dict[str, object] = {}
    name_to_id = {a.name: a.id for a in present}

    for a in present:
        # No "nobody" option. It used to be here and the live model took it half
        # the time, which silently converted a chosen social action into a move -
        # half of every interaction in the town was lost on this one line. The
        # ways to not engage already live in `act` (work, withdraw, leave), so
        # once a social action is chosen this is a forced choice among people
        # actually in the room. Measured: 43% targeted with it, 100% without.
        others = {o.name: f"the {o.job}, {o.mood_word()}"
                  for o in present if o.id != a.id}

        questions[f"{a.id}__act"] = Choice(
            instructions=(f"{a.name} decides how to spend the next few minutes "
                          f"here. What do they do?"),
            criteria=_menu(a, present),
        )
        questions[f"{a.id}__content"] = Choice(
            instructions=(f"{a.name} is talking with someone here. "
                          f"What is the conversation actually about?"),
            criteria=CONTENT,
        )
        questions[f"{a.id}__who"] = Choice(
            instructions=f"Who does {a.name} direct that at?",
            criteria=others,
        )
        # speculative: only read when act == leave
        questions[f"{a.id}__to"] = Choice(
            instructions=f"If {a.name} leaves, where do they go?",
            criteria=_destinations(a, rng),
        )
        questions[f"{a.id}__mood"] = Score(
            instructions=f"How does {a.name} feel after this?",
            criteria=MOOD_LEVELS,
        )
        # Noul takes optional criteria naming the two outcomes. The keys are
        # `true`/`false` - the prose docs say "yes and no", but NoulCriteria is a
        # TypedDict with true/false and rejects anything else. The model
        # card for jev-1.13 says it answers literally and struggles with implied
        # conditions, so spelling both poles out is close to free accuracy.
        questions[f"{a.id}__warm"] = Noul(
            instructions=f"{a.name} comes out of this moment feeling closer to the person they dealt with",
            criteria={"true": "warmer towards them than before",
                      "false": "unchanged, colder, or dealt with nobody"},
        )
        questions[f"{a.id}__tell"] = Noul(
            instructions=f"{a.name} wants to repeat what they just heard to someone else later",
            criteria={"true": "eager to pass it on", "false": "will keep it to themselves"},
        )
        # +21 tokens per person per decision (+6% on a request). The cheapest
        # drama in the whole system: this one answer drives couples AND affairs.
        questions[f"{a.id}__draw"] = Noul(
            instructions=f"{a.name} is drawn to the person they dealt with, beyond friendship",
            criteria={"true": "romantic or physical attraction",
                      "false": "friendship, indifference, or dislike"},
        )
    return state, questions, name_to_id


# --- romance -----------------------------------------------------------------
# Re-fitted to the LIVE model. Measured over 80 real answers, `draw` centres at
# mean 0.238 / median 0.215 / sd 0.106 - nothing like the mock's 0.587. The old
# pivot of 0.44 sat above almost every real answer, so attraction decayed on
# every single interaction and no couple could ever have formed.
# Thresholds, re-fitted against 80 live answers per question.
#
# These started as one shared quantile table, which was defensible while all
# three Nouls centred on 0.24. They no longer do: adding the true/false criteria
# to each question moved two of the three distributions, and a single table is
# now wrong for both.
#
#   question   mean   median   sd     range        what moved it
#   draw       0.234  0.210   0.099  0.08-0.52    unchanged
#   tell       0.342  0.330   0.109  0.14-0.60    +40% after criteria were added
#   warm       0.488  0.540   0.184  0.13-0.82    doubled after criteria were added
#
# So each question gets thresholds from ITS OWN distribution. Read them as
# quantiles: WARM_WELL is roughly the 70th percentile of warm, TELL_BETRAY the
# 90th of tell. Re-run live_calibrate.py and re-fit if the questions change -
# changing a question's wording changes its distribution.
# A story stops being news once most of the town has it, and stops being
# interesting after a few days. Without this the mill never lets go of anything:
# measured over one live hour, rumours went from 63% of all events in the first
# half to 94% in the second, talk collapsed from 29% to 1.8%, and the narrator
# said "the workshop break-in" 46 times because the same three saturated stories
# were the loudest in every single prompt.
RUMOUR_SATURATED = 0.60    # believed by this share of the living: no longer news
RUMOUR_STALE_DAYS = 5      # older than this: yesterday's paper

WARM_WELL = 0.60       # p70 of warm: this conversation actually landed
WARM_CONFIDE = 0.48    # p42 of warm: enough trust to say something private
TELL_SPREAD = 0.37     # p60 of tell: worth repeating, becomes a rumour
TELL_BETRAY = 0.43     # p80 of tell: it also has to be a leaky character,
                       # and two gates multiplied made this never fire

# Attraction. The pivot sits at the MEDIAN of draw, so half of contacts build and
# half decay; the gain is set so a pair in the top third of mutual interest
# (draw ~0.31) needs roughly eight meetings to cross PAIR_ATTRACTION, which the
# most-seen pair in a 50-person town reaches in about five simulated days.
#
# The previous pivot of 0.25 was fitted to a 12-answer sample and sat above the
# median, so attraction decayed on most contacts: five live days produced zero
# new couples and zero affairs.
DRAW_PIVOT = 0.21
DRAW_GAIN = 0.85
PAIR_ATTRACTION = 0.60
PAIR_TRUST = 0.35


def _romance(a: Agent, b: Agent, agents: dict, town: Town, tick: int, log,
             place_label: str) -> None:
    """Mutual pull, mutually trusted. Either a couple forms, or one does."""
    if a.id == b.id or a.affair == b.id or a.partner == b.id:
        return
    if min(a.attraction.get(b.id, 0), b.attraction.get(a.id, 0)) < PAIR_ATTRACTION:
        return
    if min(a.trust.get(b.id, 0), b.trust.get(a.id, 0)) < PAIR_TRUST:
        return

    if not a.partner and not b.partner:
        # Either of them may be the unattached half of someone else's affair.
        # Leaving that running produced a partner with a live affair and no
        # secret - an affair that could never be discovered.
        for x in (a, b):
            if x.affair and x.affair != (b.id if x is a else a.id):
                o = agents.get(x.affair)
                if o is not None:
                    o.affair = None
                    if o.secret.startswith("is seeing"):
                        o.secret = ""
                x.affair = None
                if x.secret.startswith("is seeing"):
                    x.secret = ""
        a.partner, b.partner = b.id, a.id
        a.together_day = b.together_day = town.clock.day
        a.remember(f"got together with {b.name}")
        b.remember(f"got together with {a.name}")
        town.chronicle.append({"day": town.clock.day, "kind": "together",
                               "text": f"{a.name} and {b.name} are together."})
        log("bond", f"{a.name} and {b.name} are together.", [a.id, b.id], 0.95)
        return

    # At least one of them is attached. Nobody announces this one.
    if a.affair or b.affair:
        return
    a.affair, b.affair = b.id, a.id
    a.affair_name, b.affair_name = b.name, a.name
    for x, y in ((a, b), (b, a)):
        if x.partner and x.partner in agents:
            x.secret = f"is seeing {y.name} behind {agents[x.partner].name}'s back"
    town.chronicle.append({"day": town.clock.day, "kind": "affair",
                           "text": f"{a.name} and {b.name} began an affair."})
    # kind="affair" so the UI can mark it: the viewer knows, the town does not.
    log("affair", f"{a.name} and {b.name}, alone at {place_label}. Nobody saw.",
        [a.id, b.id], 0.99)


# --- deterministic execution of the model's decisions ------------------------

def apply_scene(place: str, present: list[Agent], answers, town: Town, tick: int,
                agents: dict[str, Agent], rng: random.Random) -> list[Event]:
    events: list[Event] = []
    by_name = {a.name: a for a in present}
    label = town.clock.label()
    place_label = LOCATIONS[place]["label"]

    EMOTE = {"conflict": "!", "bond": "+", "rumour": "?", "talk": "~",
             "affair": "*", "caught": "!", "death": "+"}

    def log(kind: str, text: str, actors: list[str], heat: float) -> None:
        ev = Event(tick=tick, time_label=label, place=place, kind=kind,
                   text=text, actors=actors, heat=heat)
        town.log(ev)
        events.append(ev)
        mark = EMOTE.get(kind)
        if mark:
            for aid in actors:
                who = agents.get(aid)
                if who is not None:
                    who.emote, who.emote_tick = mark, tick

    for a in present:
        act = answers.get(f"{a.id}__act")
        if act is None or act.choice is None:
            continue
        a.thoughts += 1
        a.last_thought_tick = tick

        mood_a = answers.get(f"{a.id}__mood")
        if mood_a is not None and mood_a.score is not None:
            a.mood = round(0.6 * a.mood + 0.4 * mood_a.score, 2)

        who = answers.get(f"{a.id}__who")
        target_name = who.choice if who else "nobody"
        target = by_name.get(target_name) if target_name != "nobody" else None
        warm = (answers.get(f"{a.id}__warm").noul if answers.get(f"{a.id}__warm") else 0.5)
        tell = (answers.get(f"{a.id}__tell").noul if answers.get(f"{a.id}__tell") else 0.0)
        # None, not 0.0: a dropped answer used to read as "repelled" and
        # subtract 0.24 from attraction rather than leaving it alone.
        _d = answers.get(f"{a.id}__draw")
        drawn = _d.noul if _d is not None and _d.noul is not None else None
        conf = act.confidence

        choice = act.choice

        # A conversation that carries something becomes that thing. The model
        # almost always answers `talk` because `talk` is almost always true;
        # the content question is where the story actually gets decided.
        if choice == "talk":
            content = answers.get(f"{a.id}__content")
            if content is not None and content.choice in CONTENT_ACTION:
                routed = CONTENT_ACTION[content.choice]
                # keep the preconditions the direct options had
                if routed == "gossip" and not a.believes:
                    routed = None
                elif routed == "confide" and not a.secret:
                    routed = None
                elif routed == "confront" and target is not None                         and a.trust.get(target.id, 0) > -0.15:
                    routed = None
                if routed:
                    choice = routed

        # Confidence gating, straight from the docs: consequential moves need a
        # higher bar than harmless ones. A low-confidence confrontation just
        # becomes unease instead of a fight.
        if choice == "confront" and conf < 0.45:
            choice = "withdraw"

        if choice == "leave" or target is None and choice in {"talk", "confront", "gossip", "confide", "help"}:
            dest = answers.get(f"{a.id}__to")
            if dest and dest.choice and dest.choice in LOCATIONS:
                a.plan, a.plan_ticks = f"heading to {LOCATIONS[dest.choice]['label']}", 2
                a.at = dest.choice
                log("move", f"{a.name} left {place_label} for {LOCATIONS[dest.choice]['label']}.",
                    [a.id], 0.1)
            continue

        if choice == "talk" and target:
            d = 0.16 if warm > WARM_WELL else 0.05
            a.bond(target.id, d)
            target.bond(a.id, d * 0.8)
            a.plan = f"talking with {target.name}"
            a.remember(f"talked with {target.name}, it went {'well' if warm > WARM_WELL else 'politely'}")
            target.remember(f"{a.name} came over to talk")
            log("talk", f"{a.name} and {target.name} talked at {place_label}.", [a.id, target.id], 0.2)

        elif choice == "confront" and target:
            a.bond(target.id, -0.34)
            target.bond(a.id, -0.28)
            a.mood = max(0.0, a.mood - 0.5)
            target.mood = max(0.0, target.mood - 0.7)
            a.plan = f"arguing with {target.name}"
            a.remember(f"confronted {target.name} in public")
            target.remember(f"{a.name} confronted me in front of people")
            log("conflict", f"{a.name} confronted {target.name} at {place_label}.",
                [a.id, target.id], 0.85 + 0.15 * conf)

        elif choice == "gossip" and target:
            living = sum(1 for x in agents.values() if not x.dead) or 1
            today = town.clock.day
            fresh = [r for r in town.rumours.values()
                     if r.id in a.believes and r.id not in target.believes
                     and r.reach < living * RUMOUR_SATURATED
                     and today - r.born_day <= RUMOUR_STALE_DAYS]
            if fresh:
                # People repeat what is new and shocking, not what everyone has
                # already heard. Picking by reach made this rich-get-richer: a
                # freshly leaked affair starts at reach 1 and could never beat an
                # established rumour, so affair stories died at 1.3 people and not
                # one betrayal was ever discovered. Scandal first, then novelty.
                r = max(fresh, key=lambda x: (x.kind == "affair", x.born_day, -x.reach))
                r.believers.add(target.id)
                target.believes.add(r.id)
                target.remember(f"{a.name} told me: {r.text}")
                # Said out loud in a room: bystanders overhear. This is what turns
                # linear telling into the exponential curve you actually want on screen.
                for other in present:
                    if other.id not in (a.id, target.id) and r.id not in other.believes:
                        if rng.random() < 0.35:
                            r.believers.add(other.id)
                            other.believes.add(r.id)
                r.history.append((tick, r.reach))
                log("rumour", f'{a.name} told {target.name}: "{r.text}"', [a.id, target.id],
                    0.55 + min(0.3, r.reach / 60))
            else:
                # A rumour needs something worth repeating. Mundane memories
                # ("we talked") die here instead of polluting the rumour mill.
                #
                # A memory of being TOLD something is excluded too, and that one
                # is not obvious: without it, hearing a rumour spawned a brand
                # new rumour wrapping the old text, so stories grew into
                # "A told me: B told me: C told me: ..." - 132 of 172 rumours
                # were nested retellings, the worst 249 characters long, all of
                # it billed as state on every request. A story someone already
                # heard should spread as itself, not fork.
                juicy = [m for m in a.memory
                         if "told me:" not in m
                         and any(w in m for w in
                                 ("admitted", "confronted", "secret", "in front of"))]
                if tell > TELL_SPREAD and juicy:
                    r = town.new_rumour(text=juicy[-1], origin=a.id)
                    r.believers.add(target.id)
                    a.believes.add(r.id)
                    target.believes.add(r.id)
                    log("rumour", f'{a.name} started talk at {place_label}: "{juicy[-1]}"',
                        [a.id, target.id], 0.6)
            a.bond(target.id, 0.08)

        elif choice == "confide" and target:
            if a.secret and warm > WARM_CONFIDE:
                target.remember(f"{a.name} admitted: {a.secret}")
                a.bond(target.id, 0.3)
                target.bond(a.id, 0.22)
                log("bond", f"{a.name} confided something private to {target.name}.",
                    [a.id, target.id], 0.7)
                # Betrayal needs both intent AND a character who leaks. Without the
                # trait gate every secret leaked and the feed became one long betrayal.
                leaky = any(t in ("nosy", "bitter", "jealous", "reckless") for t in target.traits)
                if tell > TELL_BETRAY and leaky:
                    is_affair = a.secret.startswith("is seeing")
                    r = town.new_rumour(
                        text=f"{a.name} {a.secret}", origin=target.id, true=True,
                        about={a.id} if is_affair else set(),
                        kind="affair" if is_affair else "gossip")
                    target.believes.add(r.id)
                    r.history.append((tick, r.reach))
                    log("rumour", f"{target.name} did not keep {a.name}'s secret.",
                        [target.id], 0.9)
                a.secret = ""
            else:
                a.bond(target.id, 0.1)
                if rng.random() < 0.3:      # texture, not a flood
                    log("talk", f"{a.name} almost told {target.name} something, then stopped.",
                        [a.id, target.id], 0.3)

        elif choice == "help" and target:
            a.bond(target.id, 0.2)
            target.bond(a.id, 0.26)
            target.mood = min(4.0, target.mood + 0.3)
            target.remember(f"{a.name} helped me")
            log("bond", f"{a.name} helped {target.name} at {place_label}.", [a.id, target.id], 0.35)

        # Attraction only moves on contact that went somewhere. It decays for
        # everyone else in the tick loop, so a spark has to be fed to survive.
        if target is not None and drawn is not None and choice in (
                "talk", "confide", "help", "gossip"):
            a.feel_drawn(target.id, (drawn - DRAW_PIVOT) * DRAW_GAIN)
            _romance(a, target, agents, town, tick, log, place_label)

        if choice == "work":
            a.plan = f"working as {a.job}"
            a.energy = max(0.0, a.energy - 0.05)

        if choice == "withdraw":
            a.plan = "keeping to themselves"
            a.mood = round(a.mood - 0.1, 2)

    return events
