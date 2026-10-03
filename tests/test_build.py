"""Tests for build.py. Run from the repo root:  python3 -m unittest discover tests

The fixtures are small cuts of real venue responses. No test uses the network.
"""

import json
import sys
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build

FIXTURES = Path(__file__).parent / "fixtures"


def show(**fields):
    base = {
        "venue": "The Stand",
        "room": "Main room",
        "title": "",
        "start": "2026-10-03T20:00",
        "url": "https://thestandnyc.com/shows/show/1/x",
        "comedians": [],
    }
    return {**base, **fields}


class ParseCellarTest(unittest.TestCase):
    def setUp(self):
        payload = json.loads((FIXTURES / "cellar_api.json").read_text(encoding="utf-8"))
        self.early, self.late = build.parse_cellar(payload)

    def test_reads_room_time_and_lineup(self):
        self.assertEqual(self.early["room"], "Village Underground")
        self.assertEqual(self.early["start"], "2026-10-03T18:00")
        self.assertEqual(self.early["comedians"][:2], ["Ray Ellin", "Dean Edwards"])
        self.assertEqual(len(self.early["comedians"]), 6)

    def test_after_midnight_show_gets_the_next_calendar_day(self):
        self.assertEqual(self.late["start"], "2026-10-04T00:30")

    def test_links_to_the_lineup_page(self):
        self.assertEqual(self.late["url"], build.CELLAR_PAGE)


class ParseStandTest(unittest.TestCase):
    def setUp(self):
        page = (FIXTURES / "stand_shows.html").read_text(encoding="utf-8")
        (self.full, self.empty), self.next_url = build.parse_stand(page)

    def test_reads_start_and_link_from_the_show_url(self):
        self.assertEqual(self.full["start"], "2026-10-03T20:00")
        self.assertEqual(
            self.full["url"],
            "https://thestandnyc.com/shows/show/13926/2026-10-03-200000-the-stand-presents",
        )

    def test_reads_room_without_odd_spaces(self):
        self.assertEqual(self.full["room"], "Main room")

    def test_lists_each_comedian_once(self):
        self.assertEqual(
            self.full["comedians"],
            ["Rachel Feinstein", "Aaron Berg", "Ian Lara", "Brittany Brave", "Mike Vecchione", "Esther Povitsky"],
        )

    def test_show_without_lineup_has_no_comedians(self):
        self.assertEqual(self.empty["title"], "The Stand Presents")
        self.assertEqual(self.empty["comedians"], [])

    def test_finds_the_next_page(self):
        self.assertEqual(self.next_url, "https://thestandnyc.com/shows/P20")

    def test_last_page_has_no_next_page(self):
        self.assertEqual(build.parse_stand("<html><body></body></html>"), ([], None))


class ParseNyccTest(unittest.TestCase):
    def setUp(self):
        page = (FIXTURES / "nycc_calendar.html").read_text(encoding="utf-8")
        self.full, self.empty = build.parse_nycc(page)

    def test_reads_event(self):
        self.assertEqual(self.full["start"], "2026-10-01T19:30")
        self.assertEqual(self.full["comedians"][0], "Mike Cannon")
        self.assertEqual(len(self.full["comedians"]), 5)
        self.assertTrue(self.full["url"].startswith("https://newyorkcomedyclub.com/events/"))

    def test_names_the_room_from_the_street_address(self):
        self.assertEqual(self.full["room"], "Upper West Side")
        self.assertEqual(self.empty["room"], "Midtown")

    def test_event_without_performers_has_no_comedians(self):
        self.assertEqual(self.empty["comedians"], [])


class NightOfTest(unittest.TestCase):
    def test_evening_show_belongs_to_its_own_day(self):
        self.assertEqual(build.night_of(datetime(2026, 10, 3, 20, 0)), date(2026, 10, 3))

    def test_show_after_midnight_belongs_to_the_night_before(self):
        self.assertEqual(build.night_of(datetime(2026, 10, 4, 0, 30)), date(2026, 10, 3))

    def test_night_ends_at_four(self):
        self.assertEqual(build.night_of(datetime(2026, 10, 4, 4, 0)), date(2026, 10, 4))


class GroupByComedianTest(unittest.TestCase):
    def test_merges_spellings_of_one_name(self):
        shows = [
            show(start="2026-10-03T20:00", comedians=["Mariel O’Reilly", "Luis J. Gomez", "Mycal Dédé"]),
            show(start="2026-10-04T20:00", comedians=["Mariel O'Reilly", "Luis J Gomez", "mycal dede"]),
        ]
        comedians = build.group_by_comedian(shows)
        self.assertEqual([entry["name"] for entry in comedians], ["Luis J. Gomez", "Mariel O’Reilly", "Mycal Dédé"])
        self.assertEqual([len(entry["shows"]) for entry in comedians], [2, 2, 2])

    def test_merges_known_aliases(self):
        shows = [show(comedians=["Modi Rosenfeld"]), show(start="2026-10-04T20:00", comedians=["Modi"])]
        (modi,) = build.group_by_comedian(shows)
        self.assertEqual(modi["name"], "Modi")
        self.assertEqual(len(modi["shows"]), 2)

    def test_skips_show_names(self):
        comedians = build.group_by_comedian([show(comedians=["Come To Papa", "Tom Papa"])])
        self.assertEqual([entry["name"] for entry in comedians], ["Tom Papa"])

    def test_sorts_names_and_keeps_shows_in_time_order(self):
        shows = [
            show(start="2026-10-05T20:00", comedians=["Zed Last", "Amy First"]),
            show(start="2026-10-03T20:00", comedians=["Zed Last"]),
        ]
        comedians = build.group_by_comedian(shows)
        self.assertEqual([entry["name"] for entry in comedians], ["Amy First", "Zed Last"])
        self.assertEqual([s["start"] for s in comedians[1]["shows"]], ["2026-10-03T20:00", "2026-10-05T20:00"])


class RenderTest(unittest.TestCase):
    def snapshot(self, shows):
        return {"generated": "2026-10-03T14:10", "first_day": "2026-10-03", "last_day": "2026-10-09", "shows": shows}

    def test_page_lists_comedian_with_time_and_place(self):
        page = build.render(self.snapshot([show(comedians=["Ian Lara"])]))
        self.assertIn("<h2>Ian Lara", page)
        self.assertIn("Sat Oct 3 · 8:00 PM", page)
        self.assertIn('<a href="https://thestandnyc.com/shows/show/1/x">The Stand, Main room</a>', page)
        self.assertIn("1 comedian · 1 show", page)

    def test_escapes_scraped_text(self):
        page = build.render(self.snapshot([show(title="<script>alert(1)</script>", comedians=["A <b>B</b>"])]))
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("A &lt;b&gt;B&lt;/b&gt;", page)

    def test_does_not_link_a_url_that_is_not_https(self):
        page = build.render(self.snapshot([show(url="javascript:alert(1)", comedians=["Ian Lara"])]))
        self.assertNotIn("javascript:", page)

    def test_reports_shows_without_a_lineup(self):
        shows = [show(comedians=["Ian Lara"]), show(start="2026-10-08T20:00")]
        page = build.render(self.snapshot(shows))
        self.assertIn("The Stand: 1 show listed, lineups up to Sat Oct 3. 1 show not listed: no lineup published.", page)


if __name__ == "__main__":
    unittest.main()
