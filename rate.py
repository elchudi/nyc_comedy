#!/usr/bin/env python3
"""Rate the comedians in data/shows.json with a language model, and write ratings.json.

Usage:
    python3 rate.py                      rate the comedians that ratings.json does not have yet
    python3 rate.py --all                rate every comedian again
    python3 rate.py --model opus         another claude model (default: sonnet; haiku knows few names)
    python3 rate.py --workers 2          fewer calls at once (default: 6 for claude, 1 for ollama)
    python3 rate.py --backend ollama     use a local model through Ollama (default model: gemma4:12b)

Backends: "claude" runs the claude CLI; "ollama" posts to a local Ollama server (http://localhost:11434).

The taste comes from taste.txt. When its text changes, every comedian is rated again. Each call sends a batch of names with their venues as context. Batches run in parallel.
The model may answer null for a name it does not know: that comedian stays "Not rated".
"""

import argparse
import json
import re
import requests
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import build

TASTE_FILE = build.ROOT / "taste.txt"
# Per backend: names per call, calls at once, default model. A local model gets small batches: it
# keeps the JSON valid more often, and Ollama answers one call at a time anyway.
BACKENDS = {
    "claude": {"batch": 30, "workers": 6, "model": "sonnet"},
    "ollama": {"batch": 10, "workers": 1, "model": "gemma4:12b"},
}
OLLAMA_URL = "http://localhost:11434/api/chat"
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
        raise RuntimeError(f"claude failed: {result.stderr.strip() or result.stdout.strip()}"[:300])
    events = json.loads(result.stdout)
    events = events if isinstance(events, list) else [events]
    answer = next(event["result"] for event in events if event.get("type") == "result")
    used = next((event["model"] for event in events if event.get("type") == "system"), model or "")
    return answer, used


def ask_ollama(prompt, model):
    """Post the prompt to a local Ollama server. Return (answer text, model name)."""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0, "num_ctx": 8192},
    }
    try:
        response = requests.post(OLLAMA_URL, json=body, timeout=CALL_TIMEOUT_SECONDS)
    except requests.ConnectionError:
        raise RuntimeError("no Ollama server at localhost:11434. Start it with: ollama serve")
    if response.status_code != 200:
        raise RuntimeError(f"ollama failed: {response.text[:200]}")
    data = response.json()
    return data["message"]["content"], data.get("model", model)


ASK = {"claude": ask_claude, "ollama": ask_ollama}


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
    parser.add_argument("--backend", choices=BACKENDS, default="claude", help="claude CLI or local Ollama (default: claude)")
    parser.add_argument("--model", help="model name for the backend (default: sonnet for claude, gemma4:12b for ollama)")
    parser.add_argument("--workers", type=int, help="calls at once (default: 6 for claude, 1 for ollama)")
    args = parser.parse_args()
    backend = BACKENDS[args.backend]
    ask = ASK[args.backend]
    model = args.model or backend["model"]
    workers = args.workers or backend["workers"]
    batch_size = backend["batch"]

    taste = TASTE_FILE.read_text(encoding="utf-8").strip()
    snapshot = json.loads(build.DATA_FILE.read_text(encoding="utf-8"))
    comedians = build.group_by_comedian([show for show in snapshot["shows"] if show["comedians"]])

    existing, names = ({}, {}) if args.all else load_existing(taste)
    todo = [entry for entry in comedians if entry["key"] not in existing]
    batches = [todo[start:start + batch_size] for start in range(0, len(todo), batch_size)]
    print(f"{len(comedians)} comedians, {len(todo)} to rate, {len(batches)} batches, {workers} at once, {args.backend} model {model}")

    def rate_batch(batch):
        """Rate one batch. A call can fail once in a while: try twice, then skip the batch."""
        label = f"{batch[0]['name']} to {batch[-1]['name']}"
        for attempt in (1, 2):
            try:
                answer, model_used = ask(make_prompt(batch, taste), model)
                ratings = parse_answer(answer, batch)
                print(f"  done: {label}", flush=True)
                return ratings, model_used
            except (RuntimeError, ValueError, StopIteration, subprocess.TimeoutExpired) as error:
                print(f"  attempt {attempt} failed for {label}: {error}", flush=True)
        print(f"  skipped: {label}. Run rate.py again to rate these names.", flush=True)
        return {}, ""

    model_used = ""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for ratings, model in pool.map(rate_batch, batches):
            existing.update(ratings)
            model_used = model or model_used
    # A skipped batch leaves its names out of ratings.json, so the next run picks them up.
    names.update((entry["key"], entry["name"]) for entry in todo if entry["key"] in existing)

    rated = {kind: sum(1 for rating in existing.values() if kind in rating) for kind in build.SCALES}
    print(f"rated: {rated} of {len(existing)} tried")
    output = {
        "rated_on": datetime.now(build.NYC).date().isoformat(),
        "taste": taste,
        "model": model_used,
        "note": f"Written by rate.py with the {args.backend} backend. The model answered from general knowledge; "
                "reasons are not verified. An empty entry means the model did not know the name.",
        "ratings": {names[key]: existing[key] for key in sorted(existing, key=lambda key: names[key].casefold())},
    }
    build.RATINGS_FILE.write_text(json.dumps(output, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {build.RATINGS_FILE}")


if __name__ == "__main__":
    main()
