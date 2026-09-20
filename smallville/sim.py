"""The tick loop. Plain Python moves the town; Jev is only asked at decision points."""
from __future__ import annotations

import random
import threading
import zlib
import time
from collections import defaultdict

from .agents import make_agent, make_population
from .budget import Governor, scene_priority
from .budget import USD_PER_INPUT_TOKEN  # noqa: F401  (re-export convenience)
from .jev import TOKEN_ESTIMATE_SCALE, get_client
from .narrator import Narrator
from .persistence import load_state, save_state
from .scenes import MAX_AGENTS_PER_REQUEST, apply_scene, build_scene_request
from .world import (BUILDINGS, HOMES, LOCATIONS, MAP_H, MAP_W, PUBLIC, ROADS,
                    SHORE_Y, Event, Town, gather_spot, spot_key)


class Simulation:
    def __init__(self, n_agents: int = 300, daily_budget_usd: float = 8.0,
                 seed: int = 7, tick_seconds: float = 1.0, virtual_time: bool = False,
                 state_path: str | None = None, save_every: int = 60,
                 narrator: Narrator | None = None):
        self.rng = random.Random(seed)
        self.town = Town(seed=seed)
        self.agents = make_population(n_agents, seed=seed)
        self.client = get_client(seed=seed)
        self.gov = Governor(daily_budget_usd=daily_budget_usd)
        self.tick_seconds = tick_seconds
        self.virtual_time = virtual_time
        self.tick = 0
        self.started = time.monotonic()
        self.lock = threading.Lock()
        self.running = False
        self.last_latencies: list[float] = []
        self.scenes_evaluated = 0
        self.scenes_skipped = 0
        self.scenes_deferred = 0
        self.target_pop = n_agents
        self._last_day = 0
        self._n_created = n_agents - 1
        self._names = {a.name for a in self.agents.values()}
        # The governor reserves budget using our ~4-chars/token estimate but is
        # charged the real figure. Live, the real one is ~1.89x larger, so an
        # unscaled reservation lets through nearly twice what it can afford.
        # Seeded from the measured constant, then adapted from what is billed.
        self._token_scale = 1.0 if getattr(self.client, "is_mock", True) else TOKEN_ESTIMATE_SCALE
        self.state_path = state_path
        self.save_every = save_every
        self.narrator = narrator
        if state_path:
            load_state(self, state_path)
        if not self.town.rumours:
            self._seed_rumours()

    def _seed_rumours(self) -> None:
        """Put a few true stories in circulation before anyone speaks.

        `gossip` requires believing something. On a brand new town nobody does,
        so the action is impossible for everyone and the mill never starts.
        """
        alive = self.living()
        tellers = [a for a in alive if a.secret][:4]
        for a in tellers:
            r = self.town.new_rumour(text=f"{a.name} {a.secret}", origin=a.id, true=True)
            a.secret = ""                       # it is out; it is a rumour now
            for other in self.rng.sample(alive, min(6, len(alive))):
                r.believers.add(other.id)
                other.believes.add(r.id)
        if tellers:
            print(f"[town] seeded {len(tellers)} stories already in circulation")

    # --- deterministic layer: routine + travel, costs nothing ----------------
    def _routine(self) -> None:
        phase = self.town.clock.phase
        for a in self.living():
            if a.plan_ticks > 0:
                a.plan_ticks -= 1
                continue
            if self.rng.random() > 0.06:
                continue
            if phase == "night":
                a.at = a.home
                a.plan = "asleep"
                a.energy = min(1.0, a.energy + 0.08)
            elif phase in ("morning", "midday") and self.rng.random() < 0.6:
                a.at = a.workplace
                a.plan = f"working as {a.job}"
            else:
                a.at = self.rng.choice(PUBLIC)
                a.plan = "out and about"

    def _walk(self) -> None:
        """Move everyone a step toward where they are standing. Agents cross the
        town on foot; the client interpolates between ticks for smooth motion."""
        for a in self.living():
            tx, ty = gather_spot(a.at, spot_key(a.id, a.at))
            dx, dy = tx - a.px, ty - a.py
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < 0.06:
                continue
            if abs(dx) > 0.35:
                a.facing = 1 if dx > 0 else -1
            speed = 1.35 if dist > 6 else 0.5           # stride out, then settle
            step = min(dist, speed)
            a.px += dx / dist * step
            a.py += dy / dist * step

    # --- life and death ------------------------------------------------------
    def living(self) -> list:
        return [a for a in self.agents.values() if not a.dead]

    def _log(self, place: str, kind: str, text: str, actors: list, heat: float) -> None:
        self.town.log(Event(tick=self.tick, time_label=self.town.clock.label(),
                            place=place, kind=kind, text=text, actors=actors, heat=heat))

    def _kill(self, a, cause: str) -> None:
        a.dead = True
        a.died_day = self.town.clock.day
        a.cause = cause
        self.town.graves.append({"name": a.name, "day": a.died_day, "cause": cause})
        self.town.chronicle.append({"day": a.died_day, "kind": "death",
                                    "text": f"{a.name} died - {cause}."})
        self._log("chapel", "death", f"{a.name} died. {cause.capitalize()}.", [a.id], 1.0)

        # Grief travels along the trust graph. This is the only irreversible
        # thing in the sim, so it is the only thing that rewrites everyone at once.
        for other in self.living():
            bond = other.trust.get(a.id, 0.0)
            if other.partner == a.id:
                other.partner = None
                other.grief = 1.0
                other.mood = max(0.0, other.mood - 2.0)
                other.remember(f"{a.name}, my partner, died")
                self._log("chapel", "death", f"{other.name} is widowed.", [other.id], 0.92)
            elif bond > 0.4:
                other.grief = max(other.grief, 0.55)
                other.mood = max(0.0, other.mood - 0.9)
                other.remember(f"{a.name} died")
            if other.affair == a.id:
                other.affair = None
                # otherwise the survivor keeps a secret about someone in a grave,
                # which can still leak and expose an affair that no longer exists
                if other.secret.startswith("is seeing"):
                    other.secret = ""
            if a.name in other.secret:
                other.secret = ""
            other.trust.pop(a.id, None)
            other.attraction.pop(a.id, None)

        # The dead stop believing things. Without this they stayed in every
        # rumour's believer set and reach outgrew the living population - the
        # spread panel, which is the whole point of that panel, could read
        # "52 people (104%)".
        for r in self.town.rumours.values():
            r.believers.discard(a.id)

    def _discover_affairs(self) -> None:
        """An affair ends when the rumour reaches the person it is about."""
        for r in self.town.rumours.values():
            # A story only breaks once. Without this the rumour outlived the
            # affair it exposed: the cheater took a new partner, that partner
            # already believed the old rumour, and the whole discovery fired
            # again days later - the same betrayal "found out" three times.
            if r.kind != "affair" or r.resolved:
                continue
            for cheater_id in list(r.about):
                a = self.agents.get(cheater_id)
                if not a or a.dead or not a.partner:
                    continue
                p = self.agents.get(a.partner)
                if not p or p.dead or p.id not in r.believers:
                    continue
                # You cannot discover an affair you were in. After an affair
                # becomes the relationship, the new partner IS the other party,
                # which produced "Cato Dahl found out about Iris Halloran and
                # Cato Dahl".
                if p.id == a.affair or p.name == a.affair_name:
                    continue
                other = self.agents.get(a.affair)
                p.trust[a.id] = -0.95
                a.trust[p.id] = max(-0.6, a.trust.get(p.id, 0) - 0.5)
                p.mood = max(0.0, p.mood - 2.2)
                p.remember(f"found out {a.name} was seeing someone else")
                a.secret = ""
                a.partner = p.partner = None
                a.affair = None
                if other:
                    other.affair = None
                    p.trust[other.id] = -0.8
                # An affair can fizzle before the news breaks, which left the
                # single most dramatic line in the simulation reading "found out
                # about Iris Halloran and someone". The name is remembered past
                # the end of the affair precisely for this moment.
                name = other.name if other else (a.affair_name or "someone else")
                self.town.chronicle.append(
                    {"day": self.town.clock.day, "kind": "caught",
                     "text": f"{p.name} found out about {a.name} and {name}."})
                r.resolved = True
                self._log(p.at, "caught",
                          f"{p.name} found out about {a.name} and {name}.",
                          [p.id, a.id], 1.0)

    def _prune(self) -> None:
        """This process is meant to run for months. Rumours, the chronicle, the
        graveyard and the dead all grew without bound; only `events` was capped.
        Rumours matter most: `headlines()` sorts all of them on every scene
        request, and `_discover_affairs` scans them every tick."""
        t = self.town
        if len(t.rumours) > 300:
            keep = sorted(t.rumours.values(),
                          key=lambda r: (r.kind == "affair", r.reach, r.born_day),
                          reverse=True)[:200]
            keep_ids = {r.id for r in keep}
            t.rumours = {r.id: r for r in keep}
            for a in self.agents.values():
                a.believes &= keep_ids
        del t.chronicle[:-400]
        del t.graves[:-60]

        # Dead agents are still referenced by name in other people's memories,
        # so keep a recent tail rather than dropping them the moment they die.
        dead = [a for a in self.agents.values() if a.dead]
        if len(dead) > 120:
            for a in sorted(dead, key=lambda x: x.died_day or 0)[:len(dead) - 80]:
                self.agents.pop(a.id, None)

    def _lifecycle(self) -> None:
        """Day-scale mechanics. All deterministic - Jev is never asked to kill
        anyone, so none of this costs a token."""
        day = self.town.clock.day
        if day == self._last_day:
            return
        self._last_day = day
        rng = self.rng

        for a in self.living():
            if day and day % 8 == 0:
                a.age += 1
            a.grief = max(0.0, a.grief - 0.25)
            for k in list(a.attraction):                  # a spark must be fed
                a.attraction[k] = max(0.0, a.attraction[k] - 0.022)

            # An affair that stops being fed ends quietly. Without this the only
            # exits are exposure and death, so cheaters just accumulate.
            if a.affair:
                o = self.agents.get(a.affair)
                if not o or o.dead or max(a.attraction.get(a.affair, 0),
                                          o.attraction.get(a.id, 0)) < 0.34:
                    if o and not o.dead:
                        o.affair = None
                        if o.secret.startswith("is seeing"):
                            o.secret = ""
                    a.affair = None
                    if a.secret.startswith("is seeing"):
                        a.secret = ""

            # People acquire new private things. A secret is consumed when it is
            # confided and again when it is seeded as a rumour, and nothing used
            # to replace it: after a couple of weeks almost nobody had anything
            # to confide, only 6 confidences happened in 25 days, and no affair
            # could ever be exposed because exposure runs through confiding.
            if not a.secret and not a.affair and rng.random() < 0.055:
                from .agents import SECRETS
                pick = rng.choice([x for x in SECRETS if x])
                a.secret = pick
                a.remember(f"something I have told nobody: {pick}")

            if not a.sick and rng.random() < 0.012:
                a.sick = True
                a.remember("started feeling ill")
            elif a.sick and rng.random() < 0.34:
                a.sick = False
                a.remember("got better")

            # Calibrated for a watchable stream: ~0.4 deaths/sim-day in a town
            # of 50, i.e. someone dies roughly every 30 minutes of real time.
            hazard = max(0.0, (a.age - 48) / 1300)        # rises with age
            if a.sick:
                hazard += 0.025
            if a.at in ("docks", "workshop"):
                hazard += 0.0016
            if rng.random() < hazard:
                self._kill(a, "illness" if a.sick else
                           "an accident" if a.at in ("docks", "workshop") else "old age")

        # Without arrivals the town empties: at this death rate a 50-person
        # village is gone inside a day of streaming. Newcomers land at the docks,
        # which is also where the town's "who invited them" arc already lives.
        self._prune()

        alive = len(self.living())
        while alive < self.target_pop and rng.random() < 0.55:
            self._n_created += 1
            aid = f"a{self._n_created:03d}"
            a = make_agent(aid, rng, self._names, day=day, newcomer=True)
            self.agents[aid] = a
            # someone always shows the new arrival around, or they would be
            # socially invisible forever and never enter a single story
            for other in rng.sample(self.living(), min(3, len(self.living()))):
                if other.id != aid:
                    a.trust[other.id] = round(rng.uniform(0.0, 0.25), 2)
                    other.trust[aid] = round(rng.uniform(-0.1, 0.2), 2)
            alive += 1
            self._log("docks", "arrival",
                      f"{a.name} arrived in town, a {a.job}. Nobody knows who invited them.",
                      [aid], 0.75)
            self.town.chronicle.append({"day": day, "kind": "arrival",
                                        "text": f"{a.name} arrived in town."})

    def _now(self) -> float:
        return self.tick * self.tick_seconds if self.virtual_time else time.monotonic()

    def _scene_heat(self, place: str) -> float:
        return sum(e.heat for e in self.town.recent(4, place=place)) / 4

    # --- one tick ------------------------------------------------------------
    def step(self) -> list[Event]:
        self.tick += 1
        self.town.clock.tick()
        self._lifecycle()
        self._discover_affairs()
        self._routine()
        self._walk()
        self.gov.refill(self.tick_seconds if self.virtual_time else None)

        awake = [a for a in self.living() if a.plan != "asleep"]
        by_place: dict[str, list] = defaultdict(list)
        for a in awake:
            by_place[a.at].append(a)

        scored = sorted(
            ((scene_priority(p, ppl, self.tick, self._scene_heat(p)), p, ppl)
             for p, ppl in by_place.items() if len(ppl) >= 2),
            reverse=True,
            key=lambda t: t[0],
        )

        # Time-box the tick. The mock answers in ~1ms so a burst of scenes is
        # free; real Jev is 70-500ms, and the burst bucket can authorise ten
        # scenes at once after an idle stretch. Sequentially that is seconds of
        # overrun on a one-second tick, and the clock would visibly stutter.
        # Scenes we drop here are not lost - the next tick re-scores them.
        t_start = time.perf_counter()
        time_budget = self.tick_seconds * 0.6

        produced: list[Event] = []
        for _, place, people in scored:
            if not self.virtual_time and time.perf_counter() - t_start > time_budget:
                self.scenes_deferred += 1
                break
            chunk = sorted(people, key=lambda a: a.last_thought_tick)[:MAX_AGENTS_PER_REQUEST]
            state, questions, _ = build_scene_request(place, chunk, self.town, self.rng,
                                                      self.agents)
            from .jev import request_tokens
            cost = int(request_tokens(state, questions) * self._token_scale)
            if not self.gov.can_afford(cost):
                self.scenes_skipped += 1
                continue
            r = self.client.system_one(state=state, questions=questions)
            self.gov.charge(r.input_tokens, now=self._now())
            if r.input_tokens:                       # keep the reservation honest
                raw = max(1, request_tokens(state, questions))
                self._token_scale += 0.1 * (r.input_tokens / raw - self._token_scale)
            self.scenes_evaluated += 1
            self.last_latencies.append(r.latency_ms)
            del self.last_latencies[:-50]
            produced += apply_scene(place, chunk, r.answers, self.town,
                                    self.tick, self.agents, self.rng)

        # Hand the new events to the narrator. It queues and returns at once -
        # the writing happens on its own thread, never on the tick.
        if self.narrator is not None and produced:
            self.narrator.offer(produced, self.town)
        return produced

    def save(self) -> None:
        if not self.state_path:
            return
        try:
            with self.lock:
                save_state(self, self.state_path)
        except Exception as exc:                         # a failed save must not
            print(f"[state] save failed: {exc!r}")       # take the town down

    def run(self) -> None:
        self.running = True
        while self.running:
            t0 = time.perf_counter()
            try:
                with self.lock:
                    self.step()
                if self.state_path and self.tick % self.save_every == 0:
                    save_state(self, self.state_path)    # already holding nothing
            except Exception as exc:                     # keep a 24/7 stream alive
                print(f"[sim] tick {self.tick} failed: {exc!r}")
            time.sleep(max(0.0, self.tick_seconds - (time.perf_counter() - t0)))
        self.save()

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self.run, daemon=True)
        t.start()
        return t

    def roster(self) -> dict:
        """Static per-agent data. Fetched once; the renderer seeds each sprite's
        look from the id, so appearance costs nothing per frame."""
        return {
            "map": {"w": MAP_W, "h": MAP_H},
            "roads": ROADS,
            "shore_y": SHORE_Y,
            "buildings": {k: {**BUILDINGS[k], "label": LOCATIONS[k]["label"],
                              "x": LOCATIONS[k]["x"] * MAP_W,
                              "y": LOCATIONS[k]["y"] * MAP_H}
                          for k in LOCATIONS},
            "people": {a.id: {"n": a.name, "j": a.job, "age": a.age}
                       for a in self.agents.values()},
        }

    # --- projection for the web view ----------------------------------------
    def snapshot(self, feed: int = 40) -> dict:
        with self.lock:
            elapsed = (self.tick * self.tick_seconds) if self.virtual_time else (time.monotonic() - self.started)
            alive = self.living()
            counts: dict[str, int] = defaultdict(int)
            for a in alive:
                if a.plan != "asleep":
                    counts[a.at] += 1

            hot, seen_kind = [], {}
            for e in sorted(self.town.events[-300:], key=lambda e: -e.heat):
                if seen_kind.get(e.kind, 0) >= 2:
                    continue
                seen_kind[e.kind] = seen_kind.get(e.kind, 0) + 1
                hot.append(e)
                if len(hot) >= 6:
                    break
            rumours = sorted(self.town.rumours.values(), key=lambda r: -r.reach)[:6]
            movers = sorted(self.agents.values(), key=lambda a: -a.thoughts)[:8]
            lat = self.last_latencies or [0.0]

            return {
                "tick": self.tick,
                "time": self.town.clock.label(),
                "phase": self.town.clock.phase,
                "mock": getattr(self.client, "is_mock", True),
                "agents": len(alive),
                "awake": sum(1 for a in alive if a.plan != "asleep"),
                "agents_live": [
                    {"i": a.id, "x": round(a.px, 1), "y": round(a.py, 1),
                     "f": a.facing, "s": 1 if a.plan == "asleep" else 0,
                     "e": a.emote if self.tick - a.emote_tick < 6 else ""}
                    for a in alive
                ],
                "places": [
                    {"id": k, "label": v["label"], "x": v["x"], "y": v["y"],
                     "n": counts.get(k, 0)}
                    for k, v in LOCATIONS.items()
                ],
                # Movement is already visible on the map; in the feed it drowns out
                # every social event, so the feed carries only things that happened
                # *between* people.
                "feed": [
                    {"t": e.time_label, "place": LOCATIONS[e.place]["label"],
                     "pid": e.place, "tick": e.tick,
                     "kind": e.kind, "text": e.text, "heat": round(e.heat, 2)}
                    for e in [x for x in self.town.events if x.kind != "move"][-feed:][::-1]
                ],
                "hot": [{"text": e.text, "heat": round(e.heat, 2), "t": e.time_label} for e in hot],
                "rumours": [
                    {"text": r.text, "reach": r.reach, "true": r.true,
                     # the living, not every agent object ever created: the dead are
                     # still in self.agents until pruning, and dividing by them
                     # quietly understated every rumour's reach
                     "pct": round(100 * r.reach / max(1, len(alive)), 1),
                     "history": r.history[-60:]}
                    for r in rumours
                ],
                "movers": [
                    {"name": a.name, "job": a.job, "mood": a.mood_word(),
                     "thoughts": a.thoughts, "at": LOCATIONS[a.at]["label"],
                     "plan": a.plan,
                     "friends": len([v for v in a.trust.values() if v > 0.45]),
                     "enemies": len([v for v in a.trust.values() if v < -0.3])}
                    for a in movers
                ],
                "couples": [
                    {"a": a.name, "b": self.agents[a.partner].name,
                     "since": a.together_day + 1,
                     "cheating": bool(a.affair) or bool(self.agents[a.partner].affair)}
                    for a in alive
                    if a.partner in self.agents and a.id < a.partner
                    and not self.agents[a.partner].dead
                ],
                # The viewer sees these; the town does not. Dramatic irony is
                # the whole engine of a soap, so it gets its own panel.
                "affairs": [
                    {"a": a.name, "b": self.agents[a.affair].name,
                     "exposed_risk": round(max(
                         (r.reach / max(1, len(alive))
                          for r in self.town.rumours.values()
                          if r.kind == "affair" and a.id in r.about), default=0.0), 3)}
                    for a in alive
                    if a.affair in self.agents and a.id < a.affair
                    and not self.agents[a.affair].dead
                ],
                "narration": self.narrator.snapshot() if self.narrator else None,
                "chronicle": self.town.chronicle[-14:][::-1],
                "graves": self.town.graves[-12:],
                "econ": {
                    "spent_usd": round(self.gov.spent_usd, 4),
                    "projected_daily_usd": round(self.gov.projected_daily_usd(elapsed, now=self._now()), 2),
                    "budget_usd": self.gov.daily_budget_usd,
                    "requests": self.gov.requests,
                    "tokens": self.gov.spent_tokens,
                    "req_per_sec": round(self.gov.requests / max(1.0, elapsed), 2),
                    "agent_thoughts": sum(a.thoughts for a in self.agents.values()),
                    "population": len(alive),
                    "scenes_evaluated": self.scenes_evaluated,
                    "scenes_skipped": self.scenes_skipped,
                    "scenes_deferred": self.scenes_deferred,
                    "token_counts_estimated": getattr(self.client, "estimated", 0),
                    "token_scale": round(self._token_scale, 2),
                    "p50_latency_ms": round(sorted(lat)[len(lat) // 2], 1),
                    "elapsed_s": round(elapsed, 1),
                },
            }
