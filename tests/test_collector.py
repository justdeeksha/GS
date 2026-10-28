"""Checks that the collector behaves. Run with:  python -m unittest discover tests"""

import os
import sys
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import collect  # noqa: E402

TODAY = date(2026, 10, 28)
HERE = Path(__file__).parent


class StatusTests(unittest.TestCase):
    def test_labels_come_from_dates(self):
        s = collect.work_out_status
        self.assertEqual(s(date(2026, 10, 1), date(2026, 11, 30), TODAY), "on now")
        self.assertEqual(s(date(2026, 10, 1), date(2026, 11, 2), TODAY), "closing soon")
        self.assertEqual(s(date(2026, 10, 30), date(2026, 12, 1), TODAY), "opening soon")
        self.assertEqual(s(date(2026, 12, 1), date(2026, 12, 9), TODAY), "upcoming")
        self.assertEqual(s(date(2026, 9, 1), date(2026, 10, 24), TODAY), "ended")
        self.assertEqual(s(None, None, TODAY), "check dates")

    def test_same_event_different_spelling_is_merged(self):
        a = {"title": "Salt and Ash", "start_date": "2026-10-10", "source_name": "A", "notes": "", "status": "on now"}
        b = {"title": "Salt & Ash", "start_date": "2026-10-10", "source_name": "B", "notes": "", "status": "on now"}
        rows = collect.remove_duplicates([a, b])
        self.assertEqual(len(rows), 1)
        self.assertIn("B", rows[0]["source_name"])


class ClaudeStepTests(unittest.TestCase):
    def fake_reply(self, text):
        message = SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])
        client = mock.MagicMock()
        client.messages.create.return_value = message
        return client

    def test_reads_events_from_reply(self):
        html = (HERE / "pages" / "practice-gallery-two.html").read_text()
        source = {"name": "Practice Gallery Two", "city": "Delhi NCR"}
        reply = ('Here you go: [{"title": "Soft Machines", "start_date": "2026-10-15", "end_date": "2026-11-15", '
                 '"venue": "Practice Gallery Two", "kind": "exhibition", "event_url": null, "notes": ""}, '
                 '{"title": "Riso for Beginners", "start_date": "2026-11-07", "end_date": "2026-11-07", '
                 '"start_time": "11:00", "end_time": "14:00", "venue": null, "price": "Rs 1,800", '
                 '"kind": "workshop", "notes": "booking link not on page"}]')
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test"}), \
             mock.patch("anthropic.Anthropic", return_value=self.fake_reply(reply)):
            events = collect.from_claude(html, source, "https://example.com/two/whats-on")
        self.assertEqual([e["title"] for e in events], ["Soft Machines", "Riso for Beginners"])
        rows = collect.finish(events, {**source, "area": "Lado Sarai",
                                       "default_venue": "Practice Gallery Two, Lado Sarai",
                                       "url": "https://example.com/two/whats-on"}, "claude", TODAY)
        self.assertTrue(all(r["needs_review"] == "yes" for r in rows))
        workshop = rows[1]
        self.assertEqual(workshop["status"], "upcoming")  # 7 Nov is 10 days after 28 Oct
        self.assertIn("missing: venue", workshop["notes"])

    def test_bad_reply_gives_no_events(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test"}), \
             mock.patch("anthropic.Anthropic", return_value=self.fake_reply("Sorry, I can't read that.")):
            events = collect.from_claude("<p>x</p>", {"name": "X", "city": "Delhi NCR"}, "https://example.com")
        self.assertEqual(events, [])

    def test_no_key_means_skip(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(collect.from_claude("<p>x</p>", {"name": "X", "city": "Delhi NCR"}, "https://example.com"))


if __name__ == "__main__":
    unittest.main()
