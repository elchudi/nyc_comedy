#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["requests", "beautifulsoup4"]
# ///
"""Build index.html: a list of comedians playing at NYC comedy venues.

Usage:
    python3 build.py                 fetch lineups, write data/shows.json and index.html
    python3 build.py --nights 14     look further ahead (default: 7 nights)
    python3 build.py --render-only   rebuild index.html from data/shows.json, no network

A show is a dict:
    venue      "The Stand"
    room       "Main room" (can be empty)
    title      show title (can be empty)
    start      "2026-10-03T20:00", NYC local time on the real calendar day
    url        link to the show or its reservation page
    comedians  list of names, in billing order (can be empty)
"""

import argparse
import html
import json
import re
import sys
import time
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
DATA_FILE = ROOT / "data" / "shows.json"
OUTPUT_FILE = ROOT / "index.html"

NYC = ZoneInfo("America/New_York")
USER_AGENT = "nyc-comedy-list/0.1 (personal lineup aggregator)"
TIMEOUT_SECONDS = 30

# A show that starts before 04:00 belongs to the night before.
NIGHT_ENDS_HOUR = 4

CELLAR_PAGE = "https://www.comedycellar.com/new-york-line-up/"
CELLAR_API = "https://www.comedycellar.com/lineup/api/"

STAND_SHOWS = "https://thestandnyc.com/shows"
STAND_SHOW_PATH = re.compile(r"/shows/show/\d+/(\d{4}-\d{2}-\d{2})-(\d{2})(\d{2})\d{2}-\S*")
STAND_MAX_PAGES = 30

NYCC_CALENDAR = "https://newyorkcomedyclub.com/calendar"
# The club has three locations. Its event data identifies them by street address.
NYCC_ROOMS = {
    "241 East 24th Street": "Midtown",
    "236 W 78th Street": "Upper West Side",
    "85 East 4th Street": "East Village",
}
# robots.txt of newyorkcomedyclub.com asks for this crawl delay.
NYCC_DELAY_SECONDS = 3


def clean(text):
    """Decode HTML entities and collapse whitespace."""
    return " ".join(html.unescape(text).split())


def night_of(start):
    """Return the night (a date) that a show start (a datetime) belongs to."""
    return (start - timedelta(hours=NIGHT_ENDS_HOUR)).date()


def each_day(first_day, last_day):
    day = first_day
    while day <= last_day:
        yield day
        day += timedelta(days=1)


# ---------------------------------------------------------------- Comedy Cellar


def parse_cellar(payload):
    """Parse one Comedy Cellar API response. One response holds one night."""
    night = date.fromisoformat(payload["date"])
    soup = BeautifulSoup(payload["show"]["html"], "html.parser")
    shows = []
    for header in soup.select(".set-header"):
        lineup = header.find_next_sibling(class_="lineup")
        clock_text = re.search(r"(\d{1,2}:\d{2})\s*([ap]m)", header.get_text(" ", strip=True), re.I)
        clock = datetime.strptime(" ".join(clock_text.groups()), "%I:%M %p").time()
        # The Cellar lists after-midnight shows under the night before.
        day = night + timedelta(days=1) if clock.hour < NIGHT_ENDS_HOUR else night
        shows.append({
            "venue": "Comedy Cellar",
            # A room name, or the show name for a special.
            "room": clean(header.select_one(".title").get_text()),
            "title": "",
            "start": datetime.combine(day, clock).isoformat(timespec="minutes"),
            # The API's reservation links carry today's date for every night,
            # so they point at the wrong show. Link to the lineup page.
            "url": CELLAR_PAGE,
            "comedians": [clean(name.get_text()) for name in lineup.select(".set-content .name")],
        })
    return shows


def fetch_cellar(session, first_day, last_day):
    shows = []
    for day in each_day(first_day, last_day):
        query = {"date": day.isoformat(), "venue": "newyork", "type": "lineup"}
        response = session.post(
            CELLAR_API,
            data={"action": "cc_get_shows", "json": json.dumps(query)},
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        shows += parse_cellar(response.json())
        time.sleep(1)
    return shows


# -------------------------------------------------------------------- The Stand


def parse_stand(page):
    """Parse one page of The Stand's show list. Return (shows, next page URL or None)."""
    soup = BeautifulSoup(page, "html.parser")
    shows = []
    for row in soup.select("div.show_row"):
        link = row.select_one("h2.showtitle a")
        # The show URL carries the full date and time. The visible date has no year.
        path = STAND_SHOW_PATH.search(link["href"])
        day, hour, minute = path.groups()
        room = row.select_one(".list-show-room")
        # Each row holds the lineup twice: a desktop grid and a mobile slider.
        names = [clean(small.get_text()) for small in row.select("p.lh-sm small")]
        shows.append({
            "venue": "The Stand",
            "room": clean(room.get_text()) if room else "",
            "title": clean(link.get_text()),
            "start": f"{day}T{hour}:{minute}",
            "url": urljoin(STAND_SHOWS, path.group(0)),
            "comedians": list(dict.fromkeys(names)),
        })
    more = next((a for a in soup.find_all("a", href=True) if "More Shows" in a.get_text()), None)
    return shows, urljoin(STAND_SHOWS, more["href"]) if more else None


def fetch_stand(session, first_day, last_day):
    shows = {}
    url = STAND_SHOWS
    for _ in range(STAND_MAX_PAGES):
        response = session.get(url, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        page_shows, url = parse_stand(response.text)
        # Key by URL: a show can appear on two pages.
        shows.update((show["url"], show) for show in page_shows)
        if not page_shows or not url:
            break
        # The list runs in date order. Stop after the first page that passes the last night.
        latest = max(show["start"] for show in page_shows)
        if night_of(datetime.fromisoformat(latest)) > last_day:
            break
        time.sleep(1)
    return list(shows.values())


# --------------------------------------------------------- New York Comedy Club


def parse_nycc(page):
    """Parse one month of the New York Comedy Club calendar (schema.org event data)."""
    soup = BeautifulSoup(page, "html.parser")
    shows = []
    for block in soup.find_all("script", type="application/ld+json"):
        data = json.loads(block.string)
        for event in data if isinstance(data, list) else [data]:
            if event.get("@type") != "ComedyEvent":
                continue
            address = clean(event["location"]["address"]["streetAddress"])
            shows.append({
                "venue": "New York Comedy Club",
                "room": NYCC_ROOMS.get(address, clean(event["location"]["name"])),
                "title": clean(event["name"]),
                # startDate is NYC local time plus a UTC offset. Keep the local part.
                "start": event["startDate"][:16],
                "url": urljoin(NYCC_CALENDAR, event["url"]),
                "comedians": [clean(person["name"]) for person in event.get("performer") or []],
            })
    return shows


def fetch_nycc(session, first_day, last_day):
    # The last night runs into the next calendar day, and that day can start a new month.
    days = each_day(first_day, last_day + timedelta(days=1))
    months = sorted({day.strftime("%Y-%m") for day in days})
    shows = []
    for index, month in enumerate(months):
        if index:
            time.sleep(NYCC_DELAY_SECONDS)
        response = session.get(f"{NYCC_CALENDAR}/{month}", timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        shows += parse_nycc(response.text)
    return shows


# Each fetch function takes (session, first_day, last_day) and returns a list of shows.
VENUES = [
    ("Comedy Cellar", fetch_cellar),
    ("The Stand", fetch_stand),
    ("New York Comedy Club", fetch_nycc),
]


def fetch_all(nights):
    now = datetime.now(NYC).replace(tzinfo=None)
    first_day = night_of(now)
    last_day = first_day + timedelta(days=nights - 1)
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    shows = []
    for venue, fetch in VENUES:
        print(f"Fetching {venue} ...", flush=True)
        venue_shows = [
            show
            for show in fetch(session, first_day, last_day)
            if first_day <= night_of(datetime.fromisoformat(show["start"])) <= last_day
        ]
        with_lineup = sum(1 for show in venue_shows if show["comedians"])
        print(f"  {len(venue_shows)} shows, {with_lineup} with a lineup")
        # No lineups at all means the site changed. Stop before a wrong page is written.
        if not with_lineup:
            sys.exit(f"{venue}: found no lineups. The site layout may have changed.")
        shows += venue_shows

    return {
        "generated": now.isoformat(timespec="minutes"),
        "first_day": first_day.isoformat(),
        "last_day": last_day.isoformat(),
        "shows": sorted(shows, key=lambda show: (show["start"], show["venue"], show["room"])),
    }


# ----------------------------------------------------------------------- Render


# Lineup entries that are a show name, not a person. Use name_key() form.
NOT_COMEDIANS = {"come to papa"}

# One comedian with two billings. Maps the name_key() of a billing to the name to show.
# Hand-kept: add a line when the page shows one person twice.
ALIASES = {
    "modi rosenfeld": "Modi",
    "colin cab washington": "Cab Washington",
}


def name_key(name):
    """Return a key that matches one comedian across venues: no case, accents or periods."""
    plain = unicodedata.normalize("NFKD", name.replace("’", "'").replace(".", ""))
    plain = "".join(char for char in plain if not unicodedata.combining(char))
    return " ".join(plain.casefold().split())


def group_by_comedian(shows):
    """Return [{"name", "key", "shows"}] sorted by name. Each show list is in time order."""
    comedians = {}
    for show in sorted(shows, key=lambda show: show["start"]):
        for name in show["comedians"]:
            if name_key(name) in NOT_COMEDIANS:
                continue
            name = ALIASES.get(name_key(name), name)
            key = name_key(name)
            entry = comedians.setdefault(key, {"name": name, "key": key, "shows": []})
            entry["shows"].append(show)
    return sorted(comedians.values(), key=lambda entry: entry["key"])


def format_day(moment):
    return f"{moment:%a %b} {moment.day}"


def format_start(start):
    """Format an ISO local time as 'Sat Oct 3 · 8:00 PM'."""
    moment = datetime.fromisoformat(start)
    return f"{format_day(moment)} · {moment.strftime('%I:%M %p').lstrip('0')}"


def plural(count, noun):
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def render_show(show):
    esc = html.escape
    place = f"{show['venue']}, {show['room']}" if show["room"] else show["venue"]
    # Scraped links go into href. Allow https only.
    if show["url"].startswith("https://"):
        place_html = f'<a href="{esc(show["url"])}">{esc(place)}</a>'
    else:
        place_html = esc(place)
    title_html = f' <span class="title">{esc(show["title"])}</span>' if show["title"] else ""
    return (
        f'<li><time datetime="{esc(show["start"])}">{esc(format_start(show["start"]))}</time> '
        f"<span>{place_html}{title_html}</span></li>"
    )


def render_comedian(entry):
    esc = html.escape
    shows_html = "\n".join(render_show(show) for show in entry["shows"])
    return (
        f'<li class="comedian" data-name="{esc(entry["key"])}">\n'
        f'<h2>{esc(entry["name"])} <span class="count">{plural(len(entry["shows"]), "show")}</span></h2>\n'
        f"<ul>\n{shows_html}\n</ul>\n"
        f"</li>"
    )


PAGE_STYLE = """
:root { color-scheme: light dark; --muted: #6b6b6b; --line: #d9d9d9; --accent: #b3261e; }
@media (prefers-color-scheme: dark) { :root { --muted: #a0a0a0; --line: #3a3a3a; --accent: #ff8a80; } }
body { font: 16px/1.5 system-ui, sans-serif; max-width: 46rem; margin: 0 auto; padding: 1.5rem 1rem 4rem; }
h1 { font-size: 1.7rem; margin: 0 0 .25rem; }
.summary, footer { color: var(--muted); font-size: .9rem; }
.summary { margin: 0 0 1rem; }
#filter { font: inherit; width: 100%; box-sizing: border-box; padding: .5rem .75rem;
  border: 1px solid var(--line); border-radius: .5rem; }
#comedians { list-style: none; padding: 0; margin: 1rem 0 0; }
.comedian { padding: .75rem 0; border-top: 1px solid var(--line); }
.comedian h2 { font-size: 1.1rem; margin: 0 0 .25rem; }
.count { color: var(--muted); font-size: .8rem; font-weight: normal; margin-left: .35rem; }
.comedian ul { list-style: none; padding: 0; margin: 0; font-size: .92rem; }
.comedian ul li { display: grid; grid-template-columns: 10.5rem 1fr; gap: 0 .5rem; padding: .1rem 0; }
@media (max-width: 30rem) { .comedian ul li { grid-template-columns: 1fr; padding: .25rem 0; } }
time { font-variant-numeric: tabular-nums; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.title { color: var(--muted); font-size: .85rem; margin-left: .35rem; }
#empty { color: var(--muted); padding: 1rem 0; }
footer { margin-top: 2rem; border-top: 1px solid var(--line); padding-top: 1rem; }
footer p { margin: .25rem 0; }
"""

PAGE_SCRIPT = """
const filter = document.getElementById('filter');
const comedians = document.querySelectorAll('.comedian');
const empty = document.getElementById('empty');
filter.addEventListener('input', () => {
  const query = filter.value.trim().toLowerCase();
  let shown = 0;
  for (const comedian of comedians) {
    comedian.hidden = !comedian.dataset.name.includes(query);
    if (!comedian.hidden) shown += 1;
  }
  empty.hidden = shown > 0;
});
"""


def render(snapshot):
    esc = html.escape
    shows = snapshot["shows"]
    listed = [show for show in shows if show["comedians"]]
    comedians = group_by_comedian(listed)
    venues = [venue for venue, _ in VENUES if any(show["venue"] == venue for show in listed)]

    first_day = date.fromisoformat(snapshot["first_day"])
    last_day = date.fromisoformat(snapshot["last_day"])
    summary = (
        f"{format_day(first_day)} to {format_day(last_day)}, {last_day.year} · "
        f"{plural(len(comedians), 'comedian')} · {plural(len(listed), 'show')} · "
        f"{', '.join(venues)}"
    )

    coverage = []
    for venue in venues:
        with_lineup = [show for show in listed if show["venue"] == venue]
        missing = sum(1 for show in shows if show["venue"] == venue and not show["comedians"])
        # Venues publish lineups a few days ahead. Say how far each one reaches.
        last_night = max(night_of(datetime.fromisoformat(show["start"])) for show in with_lineup)
        line = f"{venue}: {plural(len(with_lineup), 'show')} listed, lineups up to {format_day(last_night)}."
        if missing:
            line += f" {plural(missing, 'show')} not listed: no lineup published."
        coverage.append(f"<p>{esc(line)}</p>")

    comedians_html = "\n".join(render_comedian(entry) for entry in comedians)
    coverage_html = "\n".join(coverage)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Comedians playing in NYC</title>
<style>{PAGE_STYLE}</style>
</head>
<body>
<header>
<h1>Comedians playing in NYC</h1>
<p class="summary">{esc(summary)}</p>
<input type="search" id="filter" placeholder="Filter by name" aria-label="Filter comedians by name" autocomplete="off">
</header>
<main>
<ul id="comedians">
{comedians_html}
</ul>
<p id="empty" hidden>No comedian matches this filter.</p>
</main>
<footer>
{coverage_html}
<p>Times are New York time. A show after midnight has the date of the new day.</p>
<p>Lineups change. Check with the venue before you go.</p>
<p>Generated {esc(format_start(snapshot["generated"]))}.</p>
</footer>
<script>{PAGE_SCRIPT}</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="Build index.html, a list of comedians playing in NYC.")
    parser.add_argument("--nights", type=int, default=7, help="number of nights to cover, from tonight (default: 7)")
    parser.add_argument("--render-only", action="store_true", help="rebuild index.html from data/shows.json, no network")
    args = parser.parse_args()

    if args.render_only:
        snapshot = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    else:
        snapshot = fetch_all(args.nights)
        DATA_FILE.parent.mkdir(exist_ok=True)
        DATA_FILE.write_text(json.dumps(snapshot, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote {DATA_FILE}")

    OUTPUT_FILE.write_text(render(snapshot), encoding="utf-8")
    print(f"Wrote {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
