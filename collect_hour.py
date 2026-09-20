"""
Sample the running town for an hour so the whole run can be read back.

The live feed only keeps its last 40 entries and the narration its last 60, so
watching for an hour and then looking at the panel shows you the last two
minutes. This polls the server and appends anything new to a JSONL file, which
is what gets reviewed afterwards.

    python collect_hour.py [minutes] [out.jsonl]
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

MINUTES = float(sys.argv[1]) if len(sys.argv) > 1 else 62.0
OUT = sys.argv[2] if len(sys.argv) > 2 else "hour.jsonl"
URL = "http://127.0.0.1:8765/api/state"
EVERY = 20.0

seen_events: set[str] = set()
seen_lines: set[str] = set()
seen_chron: set[str] = set()
deadline = time.time() + MINUTES * 60
samples = failures = 0

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write(json.dumps({"rec": "start", "wall": time.strftime("%H:%M:%S"),
                         "minutes": MINUTES}) + "\n")
    fh.flush()

    while time.time() < deadline:
        try:
            with urllib.request.urlopen(URL, timeout=10) as r:
                d = json.loads(r.read().decode())
        except (urllib.error.URLError, OSError, ValueError) as exc:
            failures += 1
            fh.write(json.dumps({"rec": "poll_failed", "wall": time.strftime("%H:%M:%S"),
                                 "error": repr(exc)[:160]}) + "\n")
            fh.flush()
            time.sleep(EVERY)
            continue

        samples += 1
        for e in reversed(d.get("feed", [])):
            k = e["t"] + e["text"]
            if k not in seen_events:
                seen_events.add(k)
                fh.write(json.dumps({"rec": "event", **e}) + "\n")

        n = d.get("narration") or {}
        for line in reversed(n.get("lines", [])):
            k = line["at"] + line["text"]
            if k not in seen_lines:
                seen_lines.add(k)
                fh.write(json.dumps({"rec": "narration", **line}) + "\n")

        for c in reversed(d.get("chronicle", [])):
            k = f"{c['day']}|{c['text']}"
            if k not in seen_chron:
                seen_chron.add(k)
                fh.write(json.dumps({"rec": "chronicle", **c}) + "\n")

        # a periodic health snapshot, so a drift in cost or latency is visible
        # afterwards rather than having to be inferred from the end state
        if samples % 6 == 1:
            e_, nn = d["econ"], n
            fh.write(json.dumps({
                "rec": "vitals", "wall": time.strftime("%H:%M:%S"),
                "sim_time": d["time"], "alive": d["agents"],
                "jev_usd_day": e_["projected_daily_usd"],
                "jev_reqs": e_["requests"], "jev_p50_ms": e_["p50_latency_ms"],
                "jev_estimated": e_["token_counts_estimated"],
                "deferred": e_["scenes_deferred"],
                "narr_calls": nn.get("calls"), "narr_usd": nn.get("spent_usd"),
                "narr_errors": nn.get("errors"),
                "couples": len(d.get("couples", [])),
                "affairs": len(d.get("affairs", [])),
                "graves": len(d.get("graves", [])),
            }) + "\n")
        fh.flush()
        time.sleep(EVERY)

    fh.write(json.dumps({"rec": "end", "wall": time.strftime("%H:%M:%S"),
                         "samples": samples, "poll_failures": failures,
                         "events": len(seen_events), "narration": len(seen_lines),
                         "chronicle": len(seen_chron)}) + "\n")
print(f"collected {len(seen_events)} events, {len(seen_lines)} narration lines, "
      f"{len(seen_chron)} chronicle entries over {MINUTES:.0f} min "
      f"({failures} poll failures)")
