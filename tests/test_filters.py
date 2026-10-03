"""Browser test for the filter script of the page. It needs Chromium and is skipped without it.

The test renders a small page, sets filter combinations in headless Chromium,
and reads back which comedians and shows stay visible.

Run from the repo root:  python3 -m unittest discover tests
"""

import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build

BROWSER = next(
    (path for name in ("chromium", "chromium-browser", "google-chrome") if (path := shutil.which(name))),
    None,
)


def show(start, location, comedians):
    return {
        "venue": location,
        "room": "",
        "location": location,
        "title": "",
        "start": start,
        "url": "https://example.com/show",
        "comedians": comedians,
    }


SNAPSHOT = {
    "generated": "2026-10-03T14:10",
    "first_day": "2026-10-03",
    "last_day": "2026-10-09",
    "shows": [
        show("2026-10-03T20:00", "The Stand", ["Ian Lara", "Louis Katz"]),
        # After midnight: the calendar day is Oct 4, the night is Oct 3.
        show("2026-10-04T00:30", "Comedy Cellar", ["Ian Lara", "Luis J. Gomez"]),
        show("2026-10-05T21:00", "The Stand West", ["Ian Lara", "Nobody Known"]),
    ],
}

RATINGS = {
    "rated_on": "2026-10-03",
    "taste": "deep comedy",
    "ratings": {
        "ian lara": {"profile": "rising", "profile_why": "x", "taste": "different", "taste_why": "x"},
        "louis katz": {"taste": "partial", "taste_why": "x"},
    },
}

# (controls to set, comedians that stay visible with their number of visible shows)
CASES = [
    ({}, {"Ian Lara": 3, "Louis Katz": 1, "Luis J. Gomez": 1, "Nobody Known": 1}),
    ({"night": "2026-10-03"}, {"Ian Lara": 2, "Louis Katz": 1, "Luis J. Gomez": 1}),
    ({"location": "The Stand West"}, {"Ian Lara": 1, "Nobody Known": 1}),
    ({"night": "2026-10-03", "location": "Comedy Cellar"}, {"Ian Lara": 1, "Luis J. Gomez": 1}),
    ({"night": "2026-10-05", "location": "The Stand"}, {}),
    ({"profile": "rising"}, {"Ian Lara": 3}),
    ({"profile": "star known rising"}, {"Ian Lara": 3}),
    ({"profile": "star"}, {}),
    ({"taste": "strong partial"}, {"Louis Katz": 1}),
    ({"taste": "different", "location": "The Stand"}, {"Ian Lara": 1}),
    ({"name": "Luis J."}, {"Luis J. Gomez": 1}),
    ({"name": "zzz"}, {}),
]

# Runs after the page script, so it can use its `controls` and `applyFilters`.
HARNESS = """<script>
const results = %s.map((filters) => {
  for (const control of Object.values(controls)) control.value = '';
  for (const [key, value] of Object.entries(filters)) controls[key].value = value;
  applyFilters();
  const visible = {}, counts = {};
  for (const comedian of document.querySelectorAll('.comedian')) {
    if (getComputedStyle(comedian).display === 'none') continue;
    const name = comedian.querySelector('th').firstChild.textContent.trim();
    visible[name] = [...comedian.querySelectorAll('.shows li')]
      .filter((li) => getComputedStyle(li).display !== 'none').length;
    counts[name] = comedian.querySelector('.count').textContent;
  }
  return {
    visible,
    counts,
    shown: document.getElementById('shown').textContent,
    emptyNote: getComputedStyle(document.getElementById('empty')).display !== 'none',
    selected: Object.fromEntries(Object.entries(filters).map(([key]) => [key, controls[key].value])),
  };
});
const out = document.createElement('pre');
out.id = 'results';
out.textContent = JSON.stringify(results);
document.body.appendChild(out);
</script></body>"""


@unittest.skipUnless(BROWSER, "needs Chromium")
class FilterScriptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        harness = HARNESS % json.dumps([filters for filters, _ in CASES])
        page = build.render(SNAPSHOT, RATINGS).replace("</body>", harness)
        # The file is in the tests directory because a snap Chromium cannot read /tmp.
        with tempfile.NamedTemporaryFile("w", suffix=".html", dir=Path(__file__).parent, encoding="utf-8") as file:
            file.write(page)
            file.flush()
            dom = subprocess.run(
                [BROWSER, "--headless=new", "--disable-gpu", "--no-sandbox", "--dump-dom", f"file://{file.name}"],
                capture_output=True,
                text=True,
                timeout=120,
            ).stdout
        match = re.search(r'<pre id="results">(.*?)</pre>', dom, re.S)
        cls.results = json.loads(html.unescape(match.group(1))) if match else None

    def test_filters_show_the_right_comedians_and_shows(self):
        self.assertIsNotNone(self.results, "the page script failed in the browser")
        for (filters, want), result in zip(CASES, self.results):
            with self.subTest(filters=filters):
                # A value that is not an <option> of its select does not stay selected.
                self.assertEqual(result["selected"], filters)
                self.assertEqual(result["visible"], want)
                self.assertEqual(result["counts"], {name: build.plural(count, "show") for name, count in want.items()})
                self.assertEqual(result["shown"], f"{build.plural(len(want), 'comedian')} shown")
                self.assertEqual(result["emptyNote"], not want)


if __name__ == "__main__":
    unittest.main()
