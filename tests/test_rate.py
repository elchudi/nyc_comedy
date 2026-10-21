"""Tests for rate.py. No test calls the claude CLI."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rate


def entry(name, venue="The Stand", title=""):
    return {"name": name, "key": rate.build.name_key(name), "shows": [{"venue": venue, "title": title}]}


class PromptTest(unittest.TestCase):
    def test_prompt_holds_the_taste_both_scales_and_the_names_with_context(self):
        prompt = rate.make_prompt([entry("Zakir Khan", "New York Comedy Club", "Zakir Khan Live (IN HINDI)")], "dark  and personal\ncomedy")
        self.assertIn('fit with this taste: "dark and personal comedy"', prompt)
        self.assertIn('"star" = headlines theaters', prompt)
        self.assertIn('"strong" = the act is close to this taste', prompt)
        self.assertIn("- Zakir Khan (New York Comedy Club; shows: Zakir Khan Live (IN HINDI))", prompt)
        self.assertIn("Reply with one JSON object only", prompt)


class ParseAnswerTest(unittest.TestCase):
    def test_keeps_valid_ratings_and_matches_names_by_key(self):
        answer = '''Here you go:
        {"Luis J Gomez": {"profile": "known", "profile_why": "Legion of Skanks host.", "taste": "different", "taste_why": " Shock  and raunch "},
         "Tanner Simon": {"profile": null, "profile_why": null, "taste": null, "taste_why": null}}'''
        ratings = rate.parse_answer(answer, [entry("Luis J. Gomez"), entry("Tanner Simon")])
        self.assertEqual(
            ratings["luis j gomez"],
            {"profile": "known", "profile_why": "Legion of Skanks host", "taste": "different", "taste_why": "Shock and raunch"},
        )
        self.assertEqual(ratings["tanner simon"], {})

    def test_drops_a_bad_tier_or_a_missing_reason_but_keeps_the_other_scale(self):
        answer = '{"A B": {"profile": "legend", "profile_why": "x", "taste": "strong", "taste_why": "Dark rants"}, "C D": {"profile": "star", "profile_why": "", "taste": null, "taste_why": null}}'
        ratings = rate.parse_answer(answer, [entry("A B"), entry("C D")])
        self.assertEqual(ratings["a b"], {"taste": "strong", "taste_why": "Dark rants"})
        self.assertEqual(ratings["c d"], {})

    def test_a_name_the_model_skipped_is_not_marked_as_tried(self):
        ratings = rate.parse_answer('{"A B": {"profile": null, "profile_why": null, "taste": null, "taste_why": null}}', [entry("A B"), entry("C D")])
        self.assertEqual(set(ratings), {"a b"})


if __name__ == "__main__":
    unittest.main()
