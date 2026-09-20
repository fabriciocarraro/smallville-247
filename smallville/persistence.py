"""
Save and restore the town.

Without this the whole premise collapses: a 24/7 stream is worth watching
because it is *continuous* - the affair that broke last Tuesday, the grave that
appeared on day 9, the man nobody has trusted since the festival money went
missing. A restart that resets to day 1 throws all of it away.

The file is small (a 50-person town with 30 days of history is well under a
megabyte) and written atomically, so a crash mid-save cannot corrupt it.
"""
from __future__ import annotations

import json
import os
import tempfile
from collections import deque
from dataclasses import fields

from .agents import Agent
from .world import Event, Rumour

FORMAT = 3          # bump when the shape changes; older files are refused, not misread


def _agent_to_dict(a: Agent) -> dict:
    out = {}
    for f in fields(a):
        v = getattr(a, f.name)
        if isinstance(v, deque):
            v = list(v)
        elif isinstance(v, set):
            v = sorted(v)
        out[f.name] = v
    return out


def _agent_from_dict(d: dict) -> Agent:
    known = {f.name for f in fields(Agent)}
    kwargs = {k: v for k, v in d.items() if k in known}
    kwargs["memory"] = deque(kwargs.get("memory", []), maxlen=5)
    kwargs["believes"] = set(kwargs.get("believes", []))
    return Agent(**kwargs)


def snapshot_state(sim) -> dict:
    t = sim.town
    return {
        "format": FORMAT,
        "tick": sim.tick,
        "minutes": t.clock.minutes,
        "target_pop": sim.target_pop,
        "last_day": sim._last_day,
        "n_created": sim._n_created,
        "token_scale": sim._token_scale,
        "spent_tokens": sim.gov.spent_tokens,
        "requests": sim.gov.requests,
        "agents": [_agent_to_dict(a) for a in sim.agents.values()],
        "rumours": [
            {"id": r.id, "text": r.text, "origin": r.origin,
             "believers": sorted(r.believers), "born_day": r.born_day,
             "true": r.true, "about": sorted(r.about), "kind": r.kind,
             "resolved": r.resolved,
             "history": r.history[-40:]}
            for r in t.rumours.values()
        ],
        "rumour_n": t._rumour_n,
        # the last slice of events is enough to seed "what just happened here"
        "events": [
            {"tick": e.tick, "time_label": e.time_label, "place": e.place,
             "kind": e.kind, "text": e.text, "actors": e.actors, "heat": e.heat}
            for e in t.events[-400:]
        ],
        "graves": t.graves,
        "chronicle": t.chronicle,
    }


def save_state(sim, path: str) -> None:
    """Atomic: write a sibling temp file, then rename over the target. A crash
    mid-write leaves the previous good save intact rather than a truncated one."""
    data = snapshot_state(sim)
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)          # atomic on both POSIX and Windows
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_state(sim, path: str) -> bool:
    """Restore a town in place. Returns False when there is nothing usable."""
    if not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[state] {path} is unreadable ({exc}); starting a new town")
        return False

    if d.get("format") != FORMAT:
        print(f"[state] {path} is format {d.get('format')}, this build wants "
              f"{FORMAT}; starting a new town rather than guessing")
        return False

    sim.agents = {}
    for row in d["agents"]:
        a = _agent_from_dict(row)
        sim.agents[a.id] = a

    t = sim.town
    t.clock.minutes = d["minutes"]
    t.rumours = {}
    for r in d["rumours"]:
        t.rumours[r["id"]] = Rumour(
            id=r["id"], text=r["text"], origin=r["origin"],
            believers=set(r["believers"]), born_day=r["born_day"], true=r["true"],
            history=[tuple(h) for h in r.get("history", [])],
            about=set(r.get("about", [])), kind=r.get("kind", "gossip"),
            resolved=r.get("resolved", False))
    t._rumour_n = d.get("rumour_n", len(t.rumours))
    t.events = [Event(**e) for e in d.get("events", [])]
    t.graves = d.get("graves", [])
    t.chronicle = d.get("chronicle", [])

    sim.tick = d["tick"]
    sim.target_pop = d.get("target_pop", sim.target_pop)
    sim._last_day = d.get("last_day", t.clock.day)
    sim._n_created = d.get("n_created", len(sim.agents) - 1)
    sim._names = {a.name for a in sim.agents.values()}
    sim._token_scale = d.get("token_scale", sim._token_scale)
    # Spend is deliberately NOT restored into the governor's live window: the
    # budget is per real day, and a town resumed tomorrow starts with a fresh
    # allowance. Only the lifetime counters carry over, for display.
    sim.gov.spent_tokens = d.get("spent_tokens", 0)
    sim.gov.requests = d.get("requests", 0)

    alive = len(sim.living())
    print(f"[state] resumed {path}: day {t.clock.day + 1}, {alive} alive, "
          f"{len(t.graves)} buried, {len(t.rumours)} rumours in the air")
    return True
