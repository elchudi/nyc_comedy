#!/usr/bin/env python3
"""Rate the comedians in data/shows.json with the claude CLI, and write ratings.json.

Usage:
    python3 rate.py                 rate the comedians that ratings.json does not have yet
    python3 rate.py --all           rate every comedian again
    python3 rate.py --model sonnet  pass a model to the claude CLI (default: the CLI's default)

The taste comes from taste.txt. When its text changes, every comedian is rated again. Each call sends a batch of names with their venues as context.
The model may answer null for a name it does not know: that comedian stays "Not rated".
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime

import build

TASTE_FILE = build.ROOT / "taste.txt"
BATCH_SIZE = 30
CALL_TIMEOUT_SECONDS = 600

PROMPT = """You rate stand-up comedians for a web page. Answer from your own knowledge. Do not use tools.

Two scales.
profile: how known the comedian is. {profile_scale}
taste: fit with this taste: "{taste}" {taste_scale} "different" is about style, not quality.

Rules.
- Rate a scale only when you are confident you know this comedian and their act. Otherwise use null for that scale.
- Each reason is at most 12 words and states facts you are sure of. No invented credits.
- Names can be ambiguous. The venues listed are NYC comedy clubs this week; use them as context.
- Reply with one JSON object only, no prose, no code fence. Keys are the names exactly as given. Each value is {{"profile": tier or null, "profile_why": string or null, "taste": tier or null, "taste_why": string or null}}.

Comedians:
{names}
"""


def scale_text(tiers):
    return " ".join(f'"{tier}" = {meaning}.' for tier, (_, meaning) in tiers.items())


def describe(entry):
    """One prompt line for a comedian: name, venues, and a show title or two as context."""
    venues = sorted({show["venue"] for show in entry["shows"]})
    titles = sorted({show["title"][:60] for show in entry["shows"] if show["title"]})[:2]
    context = ", ".join(venues) + (f"; shows: {' | '.join(titles)}" if titles else "")
    return f"- {entry['name']} ({context})"


def make_prompt(entries, taste):
    return PROMPT.format(
        profile_scale=scale_text(build.PROFILE_TIERS),
        taste=" ".join(taste.split()),
        taste_scale=scale_text(build.TASTE_TIERS),
        names="\n".join(describe(entry) for entry in entries),
    )


def ask_claude(prompt, model):
    """Run the claude CLI once. Return (answer text, model name)."""
    # The prompt goes on stdin: "--tools" takes a list and would swallow a prompt argument.
    command = ["claude", "-p", "--output-format", "json", "--no-session-persistence", "--tools", ""]
    if model:
        command += ["--model", model]
    result = subprocess.run(command, input=prompt, capture_output=True, text=True, timeout=CALL_TIMEOUT_SECONDS)
    if result.returncode != 0:
        sys.exit(f"claude failed: {result.stderr.strip() or result.stdout.strip()}")
    events = json.loads(result.stdout)
    events = events if isinstance(events, list) else [events]
    answer = next(event["result"] for event in events if event.get("type") == "result")
    used = next((event["model"] for event in events if event.get("type") == "system"), model or "")
    return answer, used


def parse_answer(answer, entries):
    """Return {comedian key: rating} from the model's JSON. Keep only valid tiers with a reason."""
    text = answer[answer.find("{"): answer.rfind("}") + 1]
    data = json.loads(text)
    by_key = {build.name_key(name): value for name, value in data.items()}
    ratings = {}
    for entry in entries:
        value = by_key.get(entry["key"])
        if not isinstance(value, dict):
            print(f"  no answer for {entry['name']}")
            continue
        rating = {}
        for kind, tiers in build.SCALES.items():
            tier, why = value.get(kind), value.get(f"{kind}_why")
            if tier in tiers and isinstance(why, str) and why.strip():
                rating[kind] = tier
                rating[f"{kind}_why"] = " ".join(why.split()).rstrip(".")
            elif tier is not None:
                print(f"  dropped {kind} for {entry['name']}: {tier!r}, {why!r}")
        ratings[entry["key"]] = rating
    return ratings


def load_existing(taste):
    """Return (ratings by key, names by key) from ratings.json. A changed taste starts over."""
    if not build.RATINGS_FILE.exists():
        return {}, {}
    data = json.loads(build.RATINGS_FILE.read_text(encoding="utf-8"))
    if " ".join(data.get("taste", "").split()) != " ".join(taste.split()):
        print("taste.txt changed: every comedian is rated again")
        return {}, {}
    ratings = {build.name_key(name): rating for name, rating in data["ratings"].items()}
    names = {build.name_key(name): name for name in data["ratings"]}
    return ratings, names


def main():
    parser = argparse.ArgumentParser(description="Rate comedians with the claude CLI and write ratings.json.")
    parser.add_argument("--all", action="store_true", help="rate every comedian again")
    parser.add_argument("--model", help="model for the claude CLI")
    args = parser.parse_args()

    taste = TASTE_FILE.read_text(encoding="utf-8").strip()
    snapshot = json.loads(build.DATA_FILE.read_text(encoding="utf-8"))
    comedians = build.group_by_comedian([show for show in snapshot["shows"] if show["comedians"]])

    existing, names = ({}, {}) if args.all else load_existing(taste)
    todo = [entry for entry in comedians if entry["key"] not in existing]
    print(f"{len(comedians)} comedians, {len(todo)} to rate, in batches of {BATCH_SIZE}")

    model_used = ""
    for start in range(0, len(todo), BATCH_SIZE):
        batch = todo[start:start + BATCH_SIZE]
        print(f"batch {start // BATCH_SIZE + 1}: {batch[0]['name']} to {batch[-1]['name']} ...", flush=True)
        answer, model_used = ask_claude(make_prompt(batch, taste), args.model)
        existing.update(parse_answer(answer, batch))
        names.update((entry["key"], entry["name"]) for entry in batch)

    rated = {kind: sum(1 for rating in existing.values() if kind in rating) for kind in build.SCALES}
    print(f"rated: {rated} of {len(existing)} tried")
    output = {
        "rated_on": datetime.now(build.NYC).date().isoformat(),
        "taste": taste,
        "model": model_used,
        "note": "Written by rate.py with the claude CLI. The model answered from general knowledge; "
                "reasons are not verified. An empty entry means the model did not know the name.",
        "ratings": {names[key]: existing[key] for key in sorted(existing, key=lambda key: names[key].casefold())},
    }
    build.RATINGS_FILE.write_text(json.dumps(output, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {build.RATINGS_FILE}")


if __name__ == "__main__":
    main()
