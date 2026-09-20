"""
Smallville 24/7 - 300 agents, one Jev request per room.

    python run.py                          # mock, 300 agents, $8/day budget
    python run.py --agents 60 --budget 2
    python run.py --headless --ticks 40    # no server, prints what happened

Real Jev: export TYPESAFE_API_KEY=... and pip install typesafe-sdk. Nothing else changes.
"""
from __future__ import annotations

import argparse
import os
import webbrowser

from smallville.narrator import Narrator
from smallville.server import serve
from smallville.sim import Simulation


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--agents", type=int, default=50)
    p.add_argument("--budget", type=float, default=2.0, help="USD/day ceiling for Jev calls")
    # PORT/HOST from the environment so a container platform can place it
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8765)))
    p.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"),
                   help="0.0.0.0 to accept connections from outside the box")
    p.add_argument("--tick", type=float, default=1.0, help="real seconds per tick")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--headless", action="store_true")
    p.add_argument("--ticks", type=int, default=0, help="headless: run N ticks then report")
    p.add_argument("--no-open", action="store_true")
    p.add_argument("--state", default="state.json",
                   help="where the town is saved; it resumes from here on start")
    p.add_argument("--fresh", action="store_true",
                   help="start a new town, discarding any save, and persist from here")
    p.add_argument("--narrate-budget", type=float, default=1.0,
                   help="USD/day ceiling for narration; 0 disables the model "
                        "and falls back to free templates")
    p.add_argument("--narrator-model", default="gpt-4.1-nano")
    args = p.parse_args()

    # --fresh used to mean "run with no persistence at all", so a fresh town
    # could never be resumed later. It now means what it says: discard the old
    # save, then keep saving.
    if args.fresh and not args.headless and os.path.exists(args.state):
        os.replace(args.state, args.state + ".bak")
        print(f"[state] --fresh: previous town moved to {args.state}.bak")

    narrator = None
    if not args.headless:
        narrator = Narrator(daily_budget_usd=args.narrate_budget,
                            model=args.narrator_model)
        if args.narrate_budget <= 0:
            narrator.live = False

    sim = Simulation(n_agents=args.agents, daily_budget_usd=args.budget,
                     seed=args.seed, tick_seconds=args.tick,
                     virtual_time=args.headless,
                     state_path=None if args.headless else args.state,
                     narrator=narrator)
    kind = "MOCK (no API key)" if getattr(sim.client, "is_mock", True) else "REAL Jev"
    print(f"[smallville] {args.agents} agents | {kind} | ${args.budget}/day ceiling")

    if args.headless:
        for _ in range(args.ticks or 40):
            sim.step()
        snap = sim.snapshot()
        print(f"\n{snap['time']}  tick {snap['tick']}  awake {snap['awake']}/{snap['agents']}")
        e = snap["econ"]
        print(f"requests {e['requests']}  tokens {e['tokens']:,}  "
              f"spent ${e['spent_usd']:.4f}  thoughts {e['agent_thoughts']}  "
              f"scenes {e['scenes_evaluated']} ok / {e['scenes_skipped']} skipped")
        print("\n-- hottest moments --")
        for h in snap["hot"]:
            print(f"  [{h['heat']:.2f}] {h['text']}")
        print("\n-- rumours --")
        for r in snap["rumours"]:
            print(f"  {r['reach']:>3} believe ({r['pct']}%): {r['text']}")
        return

    if narrator:
        narrator.start()
        who = narrator.model if narrator.live else "templates (free)"
        print(f"[narrator] {who} | ${args.narrate_budget}/day ceiling")
    sim.start()
    httpd = serve(sim, port=args.port, host=args.host)
    url = f"http://127.0.0.1:{args.port}"
    print(f"[smallville] live view: {url}")
    if not args.no_open:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        sim.running = False
        print("\n[smallville] stopped")


if __name__ == "__main__":
    main()
