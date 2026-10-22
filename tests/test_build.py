"""Tests for build.py. Run from the repo root:  python3 -m unittest discover tests

The fixtures are small cuts of real venue responses. No test uses the network.
The page's filter script is not covered here: see tests/test_filters.py.
"""

import json
import sys
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build

FIXTURES = Path(__file__).parent / "fixtures"

RATINGS = {
    "rated_on": "2026-10-03",
    "taste": "deep comedy",
    "model": "test-model",
    "ratings": {
        "ian lara": {
            "profile": "rising",
            "profile_why": "HBO special <Romantic Comedy>",
            "taste": "different",
            "taste_why": "Smooth relationship material",
        },
        "louis katz": {"taste": "partial", "taste_why": "Smart and dark"},
    },
}


def show(**fields):
    base = {
        "venue": "The Stand",
        "room": "Main room",
        "location": "The Stand",
        "title": "",
        "start": "2026-10-03T20:00",
        "url": "https://thestandnyc.com/shows/show/1/x",
        "comedians": [],
    }
    return {**base, **fields}


def snapshot(shows):
    return {"generated": "2026-10-03T14:10", "first_day": "2026-10-03", "last_day": "2026-10-09", "shows": shows}


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

    def test_all_rooms_are_one_location(self):
        self.assertEqual(self.late["room"], "MacDougal Street")
        self.assertEqual({self.early["location"], self.late["location"]}, {"Comedy Cellar"})


class ParseStandTest(unittest.TestCase):
    def setUp(self):
        page = (FIXTURES / "stand_shows.html").read_text(encoding="utf-8")
        (self.full, self.west, self.empty), self.next_url = build.parse_stand(page)

    def test_reads_start_and_link_from_the_show_url(self):
        self.assertEqual(self.full["start"], "2026-10-03T20:00")
        self.assertEqual(
            self.full["url"],
            "https://thestandnyc.com/shows/show/13926/2026-10-03-200000-the-stand-presents",
        )

    def test_reads_room_without_odd_spaces(self):
        self.assertEqual(self.full["room"], "Main room")

    def test_the_stand_west_is_its_own_location(self):
        self.assertEqual(self.full["location"], "The Stand")
        self.assertEqual(self.west["room"], "The Stand West")
        self.assertEqual(self.west["location"], "The Stand West")

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

    def test_names_the_room_and_location_from_the_street_address(self):
        self.assertEqual(self.full["room"], "Upper West Side")
        self.assertEqual(self.full["location"], "New York Comedy Club, Upper West Side")
        self.assertEqual(self.empty["location"], "New York Comedy Club, Midtown")

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


class LoadRatingsTest(unittest.TestCase):
    """Uses tests/fixtures/ratings.json. load_ratings() exits on an unknown tier or a missing reason."""

    def setUp(self):
        self.loaded = build.load_ratings(FIXTURES / "ratings.json", FIXTURES / "no-such-taste.txt")

    def test_keys_ratings_like_group_by_comedian(self):
        self.assertEqual(self.loaded["ratings"]["luis j gomez"]["profile"], "known")
        self.assertIn("modi", self.loaded["ratings"])  # the alias of Modi Rosenfeld

    def test_keeps_taste_and_model(self):
        self.assertEqual(self.loaded["taste"], "deep comedy, test taste")
        self.assertEqual(self.loaded["model"], "test-model")
        self.assertEqual(self.loaded["rated_on"], "2026-10-03")

    def test_an_empty_entry_is_a_name_the_model_did_not_know(self):
        self.assertEqual(self.loaded["ratings"]["tanner simon"], {})

    def test_without_a_file_nothing_is_rated(self):
        loaded = build.load_ratings(FIXTURES / "no-such-file.json", FIXTURES / "no-such-taste.txt")
        self.assertEqual(loaded["ratings"], {})
        self.assertIsNone(loaded["rated_on"])


class RenderTest(unittest.TestCase):
    def test_page_lists_comedian_with_time_and_place(self):
        page = build.render(snapshot([show(comedians=["Ian Lara"])]), RATINGS)
        self.assertIn('<th scope="row">Ian Lara', page)
        self.assertIn("Sat Oct 3 · 8:00 PM", page)
        self.assertIn('<a href="https://thestandnyc.com/shows/show/1/x">The Stand, Main room</a>', page)
        self.assertIn("1 comedian · 1 show", page)

    def test_shows_both_ratings_with_reasons(self):
        page = build.render(snapshot([show(comedians=["Ian Lara"])]), RATINGS)
        self.assertIn('data-profile="rising" data-taste="different"', page)
        self.assertIn('<span class="tier profile-rising">Rising star</span>', page)
        self.assertIn('<span class="tier taste-different">Different style</span>', page)
        self.assertIn("Smooth relationship material", page)

    def test_comedian_can_have_one_rating_or_none(self):
        page = build.render(snapshot([show(comedians=["Louis Katz", "Nobody Known"])]), RATINGS)
        self.assertIn('data-name="louis katz" data-profile="" data-taste="partial"', page)
        self.assertIn('data-name="nobody known" data-profile="" data-taste=""', page)
        self.assertEqual(page.count("Not rated</span>"), 3)

    def test_footer_counts_ratings_and_names_the_taste(self):
        page = build.render(snapshot([show(comedians=["Ian Lara", "Louis Katz", "Nobody Known"])]), RATINGS)
        self.assertIn("Profile: 1 of 3 comedians rated.", page)
        self.assertIn("Taste fit: 2 rated against one taste, deep comedy.", page)

    def test_offers_each_night_and_location_as_a_filter(self):
        shows = [
            show(comedians=["Ian Lara"]),
            show(start="2026-10-05T21:00", location="The Stand West", room="The Stand West", comedians=["Ian Lara"]),
        ]
        page = build.render(snapshot(shows), RATINGS)
        self.assertIn('<option value="2026-10-03">Sat Oct 3</option>', page)
        self.assertIn('<option value="2026-10-05">Mon Oct 5</option>', page)
        self.assertIn('<option value="The Stand West">The Stand West</option>', page)
        self.assertIn('<li data-night="2026-10-05" data-location="The Stand West">', page)

    def test_after_midnight_show_filters_with_the_night_before(self):
        page = build.render(snapshot([show(start="2026-10-04T00:30", comedians=["Ian Lara"])]), RATINGS)
        self.assertIn('<li data-night="2026-10-03" data-location="The Stand">', page)
        self.assertIn("Sun Oct 4 · 12:30 AM", page)
        self.assertNotIn('<option value="2026-10-04">', page)

    def test_escapes_scraped_text_and_rating_reasons(self):
        page = build.render(snapshot([show(title="<script>alert(1)</script>", comedians=["A <b>B</b>", "Ian Lara"])]), RATINGS)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("A &lt;b&gt;B&lt;/b&gt;", page)
        self.assertIn("HBO special &lt;Romantic Comedy&gt;", page)

    def test_does_not_link_a_url_that_is_not_https(self):
        page = build.render(snapshot([show(url="javascript:alert(1)", comedians=["Ian Lara"])]), RATINGS)
        self.assertNotIn("javascript:", page)

    def test_footer_names_the_model_or_asks_for_a_rating_run(self):
        page = build.render(snapshot([show(comedians=["Ian Lara"])]), RATINGS)
        self.assertIn("judgment of Claude (test-model) on Sat Oct 3, 2026", page)
        page = build.render(snapshot([show(comedians=["Ian Lara"])]), {"rated_on": None, "taste": "x", "model": "", "ratings": {}})
        self.assertIn("No ratings yet. Run rate.py", page)
        self.assertEqual(page.count("Not rated</span>"), 2)

    def test_warns_when_ratings_are_for_an_older_taste(self):
        page = build.render(snapshot([show(comedians=["Ian Lara"])]), {**RATINGS, "stale": True})
        self.assertIn("taste.txt changed after these ratings were made", page)

    def test_names_a_venue_that_could_not_be_fetched(self):
        snap = snapshot([show(comedians=["Ian Lara"])])
        snap["failed"] = {"Comedy Cellar": "403 Client Error"}
        page = build.render(snap, RATINGS)
        self.assertIn("Comedy Cellar: not fetched this time (403 Client Error).", page)

    def test_reports_shows_without_a_lineup(self):
        shows = [show(comedians=["Ian Lara"]), show(start="2026-10-08T20:00")]
        page = build.render(snapshot(shows), RATINGS)
        self.assertIn("The Stand: 1 show listed, lineups up to Sat Oct 3. 1 show not listed: no lineup published.", page)


if __name__ == "__main__":
    unittest.main()
