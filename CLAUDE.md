# NYC comedy list

`build.py` fetches lineups from NYC comedy venues and writes `index.html`: a table of comedians, each with two ratings and their shows.

## Run

```
python3 build.py                 # fetch, write data/shows.json and index.html (about 20 requests, 30 seconds)
python3 build.py --nights 14     # default is 7 nights, from tonight
python3 build.py --render-only   # rebuild index.html from data/shows.json, no network
python3 -m unittest discover tests
```

Needs `requests` and `beautifulsoup4`. `uv run build.py` installs them from the script header.
`tests/test_filters.py` runs the page script in headless Chromium. Without Chromium, it is skipped.

## Files

- `build.py`: everything. One `parse_*` and one `fetch_*` function per venue, then the HTML render.
- `ratings.json`: hand-kept ratings of comedians. See "Ratings".
- `data/shows.json`: the last fetch. Generated. `--render-only` reads it.
- `index.html`: the page. Generated. Do not edit by hand.
- `tests/test_build.py`: parser, ratings and render tests. They use `tests/fixtures/`, small cuts of real responses.
- `tests/test_filters.py`: browser test of the filters, on a small fixed page.
- `.scratch_pads/` (not in git): saved full responses, `make_fixtures.py`, `wikipedia_check.py`, `filter_check.py` (filters against live data), screenshots.

## The page

One row per comedian. Columns: Comedian, Profile, Taste fit, Shows.
Filters: name, night, location, profile, taste fit. A filter on night or location also hides the shows that do not match.
Rows are in name order.

## Venues

| Venue | Source | Notes |
|---|---|---|
| Comedy Cellar | `POST https://www.comedycellar.com/lineup/api/`, one call per night | Lineups reach about 6 nights ahead. |
| The Stand | `https://thestandnyc.com/shows`, 20 shows per page, follow "More Shows" | Date and time come from the show URL. |
| New York Comedy Club | `https://newyorkcomedyclub.com/calendar/YYYY-MM`, schema.org `ComedyEvent` JSON | Lineups reach about 5 nights ahead. robots.txt asks for a 3-second delay. |

Gotham Comedy Club is not included. Its site publishes show titles only, no comedian names.

To add a venue: write `parse_x` and `fetch_x`, set `location` on each show, add one line to `VENUES`, add a fixture and tests.

## Locations

A show has a `venue`, a `room` and a `location`. The location is the address to go to. The location filter uses it.

- Comedy Cellar: one location. Its rooms are in two buildings on the same corner.
- The Stand (116 E 16th St) and The Stand West (407 W 15th St) are two locations. The site lists The Stand West as a room.
- New York Comedy Club: three locations: Midtown, Upper West Side, East Village.

## Ratings

`ratings.json` maps a comedian name to at most two ratings. Each rating has a tier and a short reason (`profile` + `profile_why`, `taste` + `taste_why`).

- `profile`: `star` (big name), `known`, `rising` (rising star).
- `taste`: `strong`, `partial`, `different`. It is the fit with one taste, named in the `taste` field of the file: the deep comedy of Tim Heidecker, Marc Maron, Louis C.K. and Bill Burr. The file says how to read that.
- Raunch never raises a taste rating. The readers are not into raunchy comedy, and not put off by it. A mostly raunchy act is `different`, or `partial` when it also has real depth.

Claude wrote the ratings from general knowledge on the `rated_on` date. They are judgments and the reasons are not verified against sources. Rate a name only when the tier and the reason are known with confidence. A comedian with no entry shows "Not rated".

New lineups bring new names. To rate them: list the names without an entry, then add entries. `.scratch_pads/wikipedia_check.py` shows which names have a Wikipedia article; common names can match the wrong person.

`build.py` stops if a tier is unknown or a reason is missing. The scales and their page labels are in `SCALES`.

## Rules that the code follows

- A show `start` is NYC local time on the real calendar day. The Cellar bills a 12:30 AM show under the night before; `parse_cellar` moves it to the next day.
- A night ends at 04:00 (`NIGHT_ENDS_HOUR`). `night_of()` decides which night a show belongs to. The window and the night filter count in nights.
- A show with no published lineup stays in `data/shows.json` but not on the page. The page footer counts these shows.
- If a venue returns no lineup at all, the build stops and writes nothing. This usually means the site layout changed.
- Comedy Cellar shows link to the lineup page. The reservation links in the API carry today's date for every night, so they are wrong for later nights.
- `name_key()` merges spellings of one name (case, accents, apostrophes, periods). Ratings use the same key.
- `ALIASES` and `NOT_COMEDIANS` in `build.py` are hand-kept. Add a line when the page shows one person twice, or shows a show name as a person.
- All scraped text is HTML-escaped. Only `https://` links become links.
- The page CSS sets `display` on rows and list items, so it needs the `[hidden] { display: none !important }` rule for the filters to hide them.
