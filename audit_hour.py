"""
Audit a collected run: is it stable, is it dramatic, is the narration any good?

    python audit_hour.py [hour.jsonl]

Reads the JSONL a collector produced and answers the three questions worth
asking before pointing a camera at this thing for real:
  1. did it stay up, and did cost or latency drift
  2. did anything actually happen, and at what rate
  3. is the narration worth reading, or does it have a tic
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from collections import Counter

PATH = sys.argv[1] if len(sys.argv) > 1 else "hour.jsonl"
rows = [json.loads(l) for l in open(PATH, encoding="utf-8")]
by = lambda t: [r for r in rows if r.get("rec") == t]          # noqa: E731

start, end = by("start"), by("end")
events, narr, chron, vitals = by("event"), by("narration"), by("chronicle"), by("vitals")
fails = by("poll_failed")


def rule(title: str) -> None:
    print(f"\n{'=' * 4} {title} {'=' * (66 - len(title))}")


# ---------------------------------------------------------------- 1. stability
rule("1. STABILITY")
if end:
    e = end[0]
    print(f"  ran {start[0]['minutes']:.0f} min, {e['samples']} samples, "
          f"{e['poll_failures']} poll failures")
print(f"  collected {len(events)} events, {len(narr)} narration lines, "
      f"{len(chron)} chronicle entries")

if vitals:
    first, last = vitals[0], vitals[-1]
    cost = [v["jev_usd_day"] for v in vitals]
    lat = [v["jev_p50_ms"] for v in vitals]
    print(f"  jev spend  first {first['jev_usd_day']:.2f}  last {last['jev_usd_day']:.2f}  "
          f"median {statistics.median(cost):.2f} $/day")
    print(f"  jev p50    first {first['jev_p50_ms']:.0f}  last {last['jev_p50_ms']:.0f}  "
          f"median {statistics.median(lat):.0f} ms")
    print(f"  estimated token counts used: {last.get('jev_estimated', '?')} "
          f"(0 means every request billed from the API's own figure)")
    print(f"  scenes deferred for time: {last.get('deferred', '?')}")
    print(f"  narration errors: {last.get('narr_errors', '?')}  "
          f"spend ${last.get('narr_usd', 0):.4f}")
    over = sum(1 for c in cost if c > 2.0 * 1.08)
    print(f"  samples over the $2 ceiling (+8% tolerance): {over}/{len(cost)}")

# ------------------------------------------------------------------- 2. drama
rule("2. WHAT HAPPENED")
kinds = Counter(e["kind"] for e in events)
tot = len(events) or 1
for k, v in kinds.most_common():
    print(f"  {k:<10} {v:>4}  {100 * v / tot:>5.1f}%")
hot = sum(1 for e in events if e["heat"] >= 0.55)
print(f"\n  narratable (heat >= 0.55): {hot} = {100 * hot / tot:.0f}%")

# did the mix drift over the run?
half = len(events) // 2
if half:
    a, b = Counter(e["kind"] for e in events[:half]), Counter(e["kind"] for e in events[half:])
    print(f"\n  first half vs second half:")
    for k in sorted(set(a) | set(b)):
        pa, pb = 100 * a[k] / max(1, half), 100 * b[k] / max(1, len(events) - half)
        flag = "  <-- drifted" if abs(pa - pb) > 15 else ""
        print(f"    {k:<10} {pa:>5.1f}% -> {pb:>5.1f}%{flag}")

if chron:
    print(f"\n  the chronicle ({len(chron)} entries):")
    for c in chron:
        print(f"    day {c['day'] + 1:<3} [{c['kind']:<8}] {c['text'][:62]}")
else:
    print("\n  the chronicle is EMPTY - no couples, affairs, deaths or arrivals")

# --------------------------------------------------------------- 3. narration
rule("3. NARRATION")
if not narr:
    print("  no lines")
    sys.exit(0)

texts = [n["text"] for n in narr]
src = Counter(n["source"] for n in narr)
print(f"  {len(texts)} lines  sources: {dict(src)}")
if end:
    print(f"  rate: one line every {start[0]['minutes'] * 60 / len(texts):.0f} s")

lens = [len(t) for t in texts]
sentences = [len(re.findall(r"[.!?](?:\s|$)", t)) for t in texts]
print(f"  length   median {statistics.median(lens):.0f} chars  "
      f"min {min(lens)}  max {max(lens)}")
print(f"  sentences per line: {dict(Counter(sentences))}  (the prompt asks for ONE)")
long_lines = sum(1 for n in lens if n > 190)
print(f"  lines over 190 chars: {long_lines} ({100 * long_lines / len(texts):.0f}%)")

# a tic shows up as the same opening or the same phrase again and again
openings = Counter(" ".join(t.split()[:3]).rstrip(",") for t in texts)
print(f"\n  most repeated openings:")
for o, c in openings.most_common(5):
    print(f"    {c:>3}x  {o}")

words = re.findall(r"[a-z']{5,}", " ".join(texts).lower())
common = Counter(words).most_common(12)
print(f"\n  most repeated words (5+ letters):")
print("    " + ", ".join(f"{w} x{c}" for w, c in common))

# phrases that recur verbatim are the clearest tic signal
grams = Counter()
for t in texts:
    w = re.findall(r"[a-z']+", t.lower())
    for i in range(len(w) - 2):
        grams[" ".join(w[i:i + 3])] += 1
rep = [(g, c) for g, c in grams.most_common(8) if c > 2]
print(f"\n  three-word phrases used more than twice:")
if rep:
    for g, c in rep:
        print(f"    {c:>3}x  {g}")
else:
    print("    none - no verbatim repetition")

print(f"\n  a sample, evenly spaced through the run:")
step = max(1, len(texts) // 6)
for t in texts[::step][:6]:
    print(f"    - {t[:150]}")
