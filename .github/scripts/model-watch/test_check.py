"""Tests for model-watch/check.py. Run: python3 -m unittest discover -s .github/scripts/model-watch"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import check

# Trimmed from the real models overview page: repeated current id, legacy ids,
# date-suffixed ids, a bare alias and a `-system-card` link.
PAGE = (
    "claude-sonnet-5-5 " * 5
    + "claude-sonnet-5 claude-sonnet-4-6 claude-sonnet-4-5-20250929 claude-sonnet-4-20250514 "
    + "claude-sonnet-3-7 claude-sonnet-5-5-system-card claude-opus-5-5 claude-haiku-4-5"
)


def workflows(**files: str) -> Path:
    d = Path(tempfile.mkdtemp())
    for name, body in files.items():
        (d / f"{name}.yml").write_text(body, encoding="utf-8")
    return d


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = check.main(argv)
    return code, out.getvalue(), err.getvalue()


class ParsingTests(unittest.TestCase):
    def test_latest_ignores_legacy_dates_aliases_and_system_cards(self):
        self.assertEqual(check.latest_from_page(PAGE), (5, 5))

    def test_stray_single_mention_is_not_latest(self):
        self.assertEqual(check.latest_from_page(PAGE + " claude-sonnet-9-9"), (5, 5))

    def test_date_suffix_is_not_a_minor_version(self):
        self.assertEqual(check.latest_from_page("claude-sonnet-4-20250514 " * 4), (4, 0))

    def test_no_sonnet_found(self):
        self.assertIsNone(check.latest_from_page("claude-opus-5-5 " * 5))

    def test_pins_collected_per_file_and_opus_ignored_for_versions(self):
        d = workflows(a="  --model claude-sonnet-4-6\n", b="  --model claude-sonnet-4-6\n  --model claude-opus-4-8\n")
        pins = check.collect_pins(d)
        self.assertEqual(pins["claude-sonnet-4-6"], ["a.yml", "b.yml"])
        self.assertIsNone(check.parse_version("claude-opus-4-8"))


class MainTests(unittest.TestCase):
    def setUp(self):
        self.html = Path(tempfile.mkdtemp()) / "models.html"
        self.html.write_text(PAGE, encoding="utf-8")

    def args(self, d, *extra):
        return ["--workflows-dir", str(d), "--models-html", str(self.html), *extra]

    def test_all_current_is_quiet_success(self):
        code, out, _ = run(self.args(workflows(a="--model claude-sonnet-5-5\n"), "--dry-run"))
        self.assertEqual(code, 0)
        self.assertIn("all sonnet pins current", out)
        self.assertNotIn("request_id", out)

    def test_behind_builds_entry_listing_each_stale_pin(self):
        d = workflows(a="--model claude-sonnet-4-6\n", b="--model claude-sonnet-5-5\n")
        code, out, _ = run(self.args(d, "--dry-run"))
        self.assertEqual(code, 0)
        packet = json.loads(out)
        self.assertEqual(packet["entry_type"], "follow-up")
        self.assertEqual(packet["agent_name"], "model-watch")
        self.assertEqual(packet["attempts"], ["claude-sonnet-4-6: a.yml"])
        self.assertIn("claude-sonnet-5-5", packet["title"])

    def test_request_id_stable_for_same_state_and_changes_when_it_changes(self):
        d1 = workflows(a="--model claude-sonnet-4-6\n")
        first = json.loads(run(self.args(d1, "--dry-run"))[1])["request_id"]
        again = json.loads(run(self.args(d1, "--dry-run"))[1])["request_id"]
        self.assertEqual(first, again)
        d2 = workflows(a="--model claude-sonnet-4-5\n")
        self.assertNotEqual(first, json.loads(run(self.args(d2, "--dry-run"))[1])["request_id"])
        newer = Path(tempfile.mkdtemp()) / "m.html"
        newer.write_text(PAGE + " claude-sonnet-5-6 " * 4, encoding="utf-8")
        second = json.loads(run(["--workflows-dir", str(d1), "--models-html", str(newer), "--dry-run"])[1])["request_id"]
        self.assertNotEqual(first, second)

    def test_unparseable_page_fails_without_posting(self):
        self.html.write_text("<html>redesigned</html>", encoding="utf-8")
        code, _, err = run(self.args(workflows(a="--model claude-sonnet-4-6\n")))
        self.assertEqual(code, 1)
        self.assertIn("could not determine", err)

    def test_no_pins_found_refuses_all_clear(self):
        code, _, err = run(self.args(workflows(a="name: nothing\n")))
        self.assertEqual(code, 1)
        self.assertIn("no Sonnet --model pins", err)

    def test_behind_without_key_fails_rather_than_silently_skipping(self):
        code, _, err = run(self.args(workflows(a="--model claude-sonnet-4-6\n"), "--service-role-key", ""))
        self.assertEqual(code, 1)
        self.assertIn("QUEUE_SERVICE_ROLE_KEY", err)


if __name__ == "__main__":
    unittest.main()
