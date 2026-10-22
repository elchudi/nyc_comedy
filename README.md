# NYC comedy list

Who is playing the NYC comedy clubs this week, and how well each comedian fits **your** taste.


## Use it

```
python3 build.py                 # download this week's lineups
python3 rate.py                  # rate every comedian against taste.txt
python3 build.py --render-only   # write the page
```

Open `index.html`. Filter by name, night, location, profile and taste fit.

## Tell it your taste

Edit `taste.txt`. Plain words, a few lines:

> Lighthearted comedy for a night out with my parents: warm, clean, observational, upbeat.
> No dark themes, no politics, no crude material.

Run `rate.py` and `build.py --render-only` again. About 30 seconds.

Three ready examples are in `examples/`: **deep-comedy**, **lighthearted**, **latin-storytelling**. Each folder has the taste, the ratings and the finished page.

## You need

- Python 3.11 with `requests` and `beautifulsoup4` (`uv run build.py` installs them).
- The `claude` CLI, logged in. Or a local model: `rate.py --backend ollama` with Ollama and `gemma4:12b`.

## Good to know

- Venues: Comedy Cellar, The Stand, New York Comedy Club. Lineups appear about 5 nights ahead. A venue that is down or blocked is skipped and named in the page footer.
- Ratings are a language model's opinion from memory, not checked. Names it does not know show "Not rated".
- Lineups change. Check with the club before you go.

Tests: `python3 -m unittest discover tests`. Details for developers: `CLAUDE.md`.
