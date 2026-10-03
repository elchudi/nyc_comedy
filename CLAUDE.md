# NYC comedy list

`build.py` fetches lineups from NYC comedy venues and writes `index.html`: a list of comedians, each with their shows.

## Run

```
python3 build.py                 # fetch, write data/shows.json and index.html (about 20 requests, 30 seconds)
python3 build.py --nights 14     # default is 7 nights, from tonight
python3 build.py --render-only   # rebuild index.html from data/shows.json, no network
python3 -m unittest discover tests
```

Needs `requests` and `beautifulsoup4`. `uv run build.py` installs them from the script header.

## Files

- `build.py`: everything. One `parse_*` and one `fetch_*` function per venue, then the HTML render.
- `data/shows.json`: the last fetch. Generated. `--render-only` reads it.
- `index.html`: the page. Generated. Do not edit by hand.
- `tests/test_build.py`: parser and render tests. They use `tests/fixtures/`, small cuts of real responses.
- `.scratch_pads/` (not in git): saved full responses, `make_fixtures.py`, screenshots.

## Venues

| Venue | Source | Notes |
|---|---|---|
| Comedy Cellar | `POST https://www.comedycellar.com/lineup/api/`, one call per night | Lineups reach about 6 nights ahead. |
| The Stand | `https://thestandnyc.com/shows`, 20 shows per page, follow "More Shows" | Date and time come from the show URL. |
| New York Comedy Club | `https://newyorkcomedyclub.com/calendar/YYYY-MM`, schema.org `ComedyEvent` JSON | Three locations. Lineups reach about 5 nights ahead. robots.txt asks for a 3-second delay. |

Gotham Comedy Club is not included. Its site publishes show titles only, no comedian names.

To add a venue: write `parse_x` and `fetch_x`, add one line to `VENUES`, add a fixture and tests.

## Rules that the code follows

- A show `start` is NYC local time on the real calendar day. The Cellar bills a 12:30 AM show under the night before; `parse_cellar` moves it to the next day.
- A night ends at 04:00 (`NIGHT_ENDS_HOUR`). `night_of()` decides which night a show belongs to. The window is counted in nights.
- A show with no published lineup stays in `data/shows.json` but not on the page. The page footer counts these shows.
- If a venue returns no lineup at all, the build stops and writes nothing. This usually means the site layout changed.
- Comedy Cellar shows link to the lineup page. The reservation links in the API carry today's date for every night, so they are wrong for later nights.
- `name_key()` merges spellings of one name (case, accents, apostrophes, periods).
- `ALIASES` and `NOT_COMEDIANS` in `build.py` are hand-kept. Add a line when the page shows one person twice, or shows a show name as a person.
- All scraped text is HTML-escaped. Only `https://` links become links.
