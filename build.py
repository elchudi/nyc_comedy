#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["requests", "beautifulsoup4"]
# ///
"""Build index.html: a table of comedians playing at NYC comedy venues.

Usage:
    python3 build.py                 fetch lineups, write data/shows.json and index.html
    python3 build.py --nights 14     look further ahead (default: 7 nights)
    python3 build.py --render-only   rebuild index.html from data/shows.json, no network

A show is a dict:
    venue      "The Stand"
    room       "Main room" (can be empty)
    location   "The Stand": the address to go to. One venue can have several.
    title      show title (can be empty)
    start      "2026-10-03T20:00", NYC local time on the real calendar day
    url        link to the show or its reservation page
    comedians  list of names, in billing order (can be empty)

ratings.json is hand-kept. It rates comedians on two scales, each with a reason: see SCALES.
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
RATINGS_FILE = ROOT / "ratings.json"
OUTPUT_FILE = ROOT / "index.html"

NYC = ZoneInfo("America/New_York")
USER_AGENT = "nyc-comedy-list/0.1 (personal lineup aggregator)"
TIMEOUT_SECONDS = 30

# A show that starts before 04:00 belongs to the night before.
NIGHT_ENDS_HOUR = 4

# The two rating scales. Tier id in ratings.json -> (label on the page, meaning), best first.
PROFILE_TIERS = {
    "star": ("Big name", "headlines theaters, known beyond comedy fans"),
    "known": ("Known", "has specials or TV credits, a name that comedy fans know"),
    "rising": ("Rising star", "a newer comedian with clear momentum"),
}
# Fit with one taste. ratings.json names the taste.
TASTE_TIERS = {
    "strong": ("Strong fit", "dark, personal or absurd comedy with a strong point of view"),
    "partial": ("Partial fit", "shares some of that edge or honesty"),
    "different": ("Different style", "clean, light, or built on crowd work and act-outs"),
}
# Rating kind -> its scale. A rating in ratings.json has the keys "<kind>" and "<kind>_why".
SCALES = {"profile": PROFILE_TIERS, "taste": TASTE_TIERS}

CELLAR_PAGE = "https://www.comedycellar.com/new-york-line-up/"
CELLAR_API = "https://www.comedycellar.com/lineup/api/"

STAND_SHOWS = "https://thestandnyc.com/shows"
STAND_SHOW_PATH = re.compile(r"/shows/show/\d+/(\d{4}-\d{2}-\d{2})-(\d{2})(\d{2})\d{2}-\S*")
STAND_MAX_PAGES = 30
# The show list gives The Stand West as a room, but it is a second address (407 W 15th St).
STAND_WEST = "The Stand West"

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
            # The rooms are in two buildings on the same corner. Count them as one location.
            "location": "Comedy Cellar",
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
        room_tag = row.select_one(".list-show-room")
        room = clean(room_tag.get_text()) if room_tag else ""
        # Each row holds the lineup twice: a desktop grid and a mobile slider.
        names = [clean(small.get_text()) for small in row.select("p.lh-sm small")]
        shows.append({
            "venue": "The Stand",
            "room": room,
            "location": STAND_WEST if room == STAND_WEST else "The Stand",
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
            room = NYCC_ROOMS.get(address, clean(event["location"]["name"]))
            shows.append({
                "venue": "New York Comedy Club",
                "room": room,
                "location": f"New York Comedy Club, {room}",
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


# -------------------------------------------------------------------- Comedians


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


def load_ratings():
    """Read ratings.json. Return it with the ratings keyed by comedian key instead of name."""
    data = json.loads(RATINGS_FILE.read_text(encoding="utf-8"))
    ratings = {}
    for name, rating in data["ratings"].items():
        for kind, tiers in SCALES.items():
            if kind not in rating:
                continue
            if rating[kind] not in tiers:
                sys.exit(f"{RATINGS_FILE.name}: {name} has unknown {kind} tier {rating[kind]!r}")
            if not rating.get(f"{kind}_why"):
                sys.exit(f"{RATINGS_FILE.name}: {name} has a {kind} rating without {kind}_why")
        # Use the same key as group_by_comedian, so a rating under either billing matches.
        ratings[name_key(ALIASES.get(name_key(name), name))] = rating
    return {"rated_on": data["rated_on"], "taste": data["taste"], "ratings": ratings}


# ----------------------------------------------------------------------- Render


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
    night = night_of(datetime.fromisoformat(show["start"])).isoformat()
    return (
        f'<li data-night="{night}" data-location="{esc(show["location"])}">'
        f'<time datetime="{esc(show["start"])}">{esc(format_start(show["start"]))}</time> '
        f"<span>{place_html}{title_html}</span></li>"
    )


def render_rating(rating, kind):
    """Render one rating cell. `kind` is a key of SCALES."""
    esc = html.escape
    tier = rating.get(kind)
    if not tier:
        return '<td class="rating"><span class="unrated">Not rated</span></td>'
    label, _ = SCALES[kind][tier]
    return (
        f'<td class="rating"><span class="tier {kind}-{tier}">{esc(label)}</span>'
        f'<span class="why">{esc(rating[f"{kind}_why"])}</span></td>'
    )


def render_comedian(entry, rating):
    """Render one table row. `rating` is the comedian's entry in ratings.json, or {}."""
    esc = html.escape
    shows_html = "\n".join(render_show(show) for show in entry["shows"])
    return (
        f'<tr class="comedian" data-name="{esc(entry["key"])}" '
        f'data-profile="{rating.get("profile", "")}" data-taste="{rating.get("taste", "")}">\n'
        f'<th scope="row">{esc(entry["name"])} '
        f'<span class="count">{plural(len(entry["shows"]), "show")}</span></th>\n'
        f"{render_rating(rating, 'profile')}\n"
        f"{render_rating(rating, 'taste')}\n"
        f'<td><ul class="shows">\n{shows_html}\n</ul></td>\n'
        f"</tr>"
    )


def venues_of(shows):
    """Return the venues of these shows in VENUES order. A venue that is not in VENUES goes last."""
    order = [venue for venue, _ in VENUES]
    found = {show["venue"] for show in shows}
    return [venue for venue in order if venue in found] + sorted(found - set(order))


def render_options(options):
    """Render <option> tags from (value, label) pairs."""
    esc = html.escape
    return "\n".join(f'<option value="{esc(value)}">{esc(label)}</option>' for value, label in options)


PAGE_STYLE = """
:root { color-scheme: light dark; --muted: #6b6b6b; --line: #d9d9d9; --accent: #b3261e;
  --star: #8a5a00; --known: #0b5cad; --rising: #1a7f37; --fit: #7a3fd0; }
@media (prefers-color-scheme: dark) { :root { --muted: #a0a0a0; --line: #3a3a3a; --accent: #ff8a80;
  --star: #e3b341; --known: #6cb6ff; --rising: #56d364; --fit: #c297ff; } }
/* The rules below set display on rows and list items. Without this, hidden has no effect on them. */
[hidden] { display: none !important; }
body { font: 16px/1.5 system-ui, sans-serif; max-width: 70rem; margin: 0 auto; padding: 1.5rem 1rem 4rem; }
h1 { font-size: 1.7rem; margin: 0 0 .25rem; }
.summary, #shown, footer { color: var(--muted); font-size: .9rem; }
.summary { margin: 0 0 1rem; }
#filters { display: flex; flex-wrap: wrap; gap: .5rem; }
#filters input, #filters select { font: inherit; padding: .45rem .6rem; border: 1px solid var(--line);
  border-radius: .5rem; background: Field; color: FieldText; }
#filters input { flex: 1 1 12rem; min-width: 0; }
#shown { margin: .5rem 0 0; }
table { border-collapse: collapse; width: 100%; margin-top: .75rem; }
thead th { text-align: left; font-size: .75rem; text-transform: uppercase; letter-spacing: .05em;
  color: var(--muted); padding: .4rem .75rem .4rem 0; }
.comedian > th, .comedian > td { vertical-align: top; text-align: left; padding: .7rem .75rem .7rem 0;
  border-top: 1px solid var(--line); }
.comedian > th { width: 11rem; font-size: 1.05rem; }
.count { display: block; color: var(--muted); font-size: .8rem; font-weight: normal; }
.comedian > .rating { width: 13rem; font-size: .88rem; }
.tier { display: inline-block; font-size: .78rem; font-weight: 600; padding: 0 .55rem;
  border: 1px solid; border-radius: 1rem; }
.profile-star { color: var(--star); background: color-mix(in srgb, var(--star) 14%, transparent); }
.profile-known { color: var(--known); }
.profile-rising { color: var(--rising); }
.taste-strong { color: var(--fit); background: color-mix(in srgb, var(--fit) 14%, transparent); }
.taste-partial { color: var(--fit); }
.taste-different { color: var(--muted); }
.why { display: block; color: var(--muted); margin-top: .15rem; }
.unrated { color: var(--muted); font-size: .8rem; }
.shows { list-style: none; padding: 0; margin: 0; font-size: .92rem; }
.shows li { display: grid; grid-template-columns: 10.5rem 1fr; gap: 0 .5rem; padding: .1rem 0; }
time { font-variant-numeric: tabular-nums; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.title { color: var(--muted); font-size: .85rem; }
#empty { color: var(--muted); padding: 1rem 0; }
footer { margin-top: 2rem; border-top: 1px solid var(--line); padding-top: 1rem; }
footer p { margin: .25rem 0; }
@media (max-width: 48rem) {
  thead, .unrated { display: none; }
  table, tbody, .comedian { display: block; }
  .comedian { padding: .7rem 0; border-top: 1px solid var(--line); }
  .comedian > th, .comedian > td, .comedian > .rating { display: block; width: auto; padding: 0 0 .3rem; border-top: 0; }
  .count { display: inline; margin-left: .35rem; }
  .shows li { grid-template-columns: 1fr; padding: .25rem 0; }
}
"""

PAGE_SCRIPT = """
const controls = {
  name: document.getElementById('filter-name'),
  night: document.getElementById('filter-night'),
  location: document.getElementById('filter-location'),
  profile: document.getElementById('filter-profile'),
  taste: document.getElementById('filter-taste'),
};
const comedians = document.querySelectorAll('.comedian');
const empty = document.getElementById('empty');
const shown = document.getElementById('shown');

// A rating option holds the tiers it accepts, separated by spaces. An empty value accepts all.
function tierMatches(accepted, tier) {
  return accepted === '' || accepted.split(' ').includes(tier);
}

function applyFilters() {
  // data-name has no periods: "Luis J. Gomez" is stored as "luis j gomez".
  const name = controls.name.value.trim().toLowerCase().replaceAll('.', '');
  const night = controls.night.value;
  const location = controls.location.value;
  let comediansShown = 0;
  for (const comedian of comedians) {
    let showsShown = 0;
    for (const show of comedian.querySelectorAll('.shows li')) {
      const nightMatches = !night || show.dataset.night === night;
      const locationMatches = !location || show.dataset.location === location;
      show.hidden = !(nightMatches && locationMatches);
      if (!show.hidden) showsShown += 1;
    }
    const ratingMatches = tierMatches(controls.profile.value, comedian.dataset.profile)
      && tierMatches(controls.taste.value, comedian.dataset.taste);
    comedian.hidden = showsShown === 0 || !ratingMatches || !comedian.dataset.name.includes(name);
    comedian.querySelector('.count').textContent = showsShown === 1 ? '1 show' : showsShown + ' shows';
    if (!comedian.hidden) comediansShown += 1;
  }
  empty.hidden = comediansShown > 0;
  shown.textContent = comediansShown === 1 ? '1 comedian shown' : comediansShown + ' comedians shown';
}

for (const control of Object.values(controls)) control.addEventListener('input', applyFilters);
applyFilters();
"""


def render(snapshot, ratings):
    esc = html.escape
    shows = snapshot["shows"]
    listed = [show for show in shows if show["comedians"]]
    comedians = group_by_comedian(listed)
    venues = venues_of(listed)

    first_day = date.fromisoformat(snapshot["first_day"])
    last_day = date.fromisoformat(snapshot["last_day"])
    summary = (
        f"{format_day(first_day)} to {format_day(last_day)}, {last_day.year} · "
        f"{plural(len(comedians), 'comedian')} · {plural(len(listed), 'show')} · "
        f"{', '.join(venues)}"
    )

    nights = sorted({night_of(datetime.fromisoformat(show["start"])) for show in listed})
    night_options = [("", "All nights")] + [(night.isoformat(), format_day(night)) for night in nights]
    locations = [
        location
        for venue in venues
        for location in sorted({show["location"] for show in listed if show["venue"] == venue})
    ]
    location_options = [("", "All locations")] + [(location, location) for location in locations]
    # A rating option's value lists the tiers it accepts: see tierMatches in PAGE_SCRIPT.
    profile_options = [("", "Profile: any"), (" ".join(PROFILE_TIERS), "Big name, known or rising")]
    profile_options += [(tier, label) for tier, (label, _) in PROFILE_TIERS.items()]
    taste_options = [("", "Taste fit: any"), ("strong partial", "Strong or partial fit")]
    taste_options += [(tier, label) for tier, (label, _) in TASTE_TIERS.items()]

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

    rated_on = date.fromisoformat(ratings["rated_on"])
    rated = {kind: 0 for kind in SCALES}
    for entry in comedians:
        for kind in SCALES:
            rated[kind] += kind in ratings["ratings"].get(entry["key"], {})
    legend = {
        kind: " ".join(f"{label}: {meaning}." for label, meaning in tiers.values())
        for kind, tiers in SCALES.items()
    }

    comedians_html = "\n".join(
        render_comedian(entry, ratings["ratings"].get(entry["key"], {})) for entry in comedians
    )
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
<div id="filters">
<input type="search" id="filter-name" placeholder="Filter by name" aria-label="Filter by name" autocomplete="off">
<select id="filter-night" aria-label="Filter by night">
{render_options(night_options)}
</select>
<select id="filter-location" aria-label="Filter by location">
{render_options(location_options)}
</select>
<select id="filter-profile" aria-label="Filter by profile">
{render_options(profile_options)}
</select>
<select id="filter-taste" aria-label="Filter by taste fit">
{render_options(taste_options)}
</select>
</div>
<p id="shown" aria-live="polite"></p>
</header>
<main>
<table>
<thead>
<tr><th scope="col">Comedian</th><th scope="col">Profile</th><th scope="col">Taste fit</th><th scope="col">Shows</th></tr>
</thead>
<tbody>
{comedians_html}
</tbody>
</table>
<p id="empty" hidden>No comedian matches these filters.</p>
</main>
<footer>
<p>Profile: {rated["profile"]} of {plural(len(comedians), 'comedian')} rated. {esc(legend["profile"])}</p>
<p>Taste fit: {rated["taste"]} rated against one taste, {esc(ratings["taste"])}. {esc(legend["taste"])} "Different style" is about style, not quality.</p>
<p>The ratings are Claude's judgment on {esc(format_day(rated_on))}, {rated_on.year}, from general knowledge. They are not verified. "Not rated" is not a bad rating.</p>
{coverage_html}
<p>Times are New York time. A show after midnight has the date of the new day. The night filter keeps it with the night before.</p>
<p>Lineups change. Check with the venue before you go.</p>
<p>Generated {esc(format_start(snapshot["generated"]))}.</p>
</footer>
<script>{PAGE_SCRIPT}</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="Build index.html, a table of comedians playing in NYC.")
    parser.add_argument("--nights", type=int, default=7, help="number of nights to cover, from tonight (default: 7)")
    parser.add_argument("--render-only", action="store_true", help="rebuild index.html from data/shows.json, no network")
    args = parser.parse_args()

    ratings = load_ratings()
    if args.render_only:
        snapshot = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    else:
        snapshot = fetch_all(args.nights)
        DATA_FILE.parent.mkdir(exist_ok=True)
        DATA_FILE.write_text(json.dumps(snapshot, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote {DATA_FILE}")

    OUTPUT_FILE.write_text(render(snapshot, ratings), encoding="utf-8")
    print(f"Wrote {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
