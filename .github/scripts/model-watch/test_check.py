"""Tests for model-watch/check.py. Run: python3 -m unittest discover -s .github/scripts/model-watch"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import check

# Trimmed from the real models overview page: repeated current ids across families,
# legacy ids, date-suffixed ids, a bare alias and a `-system-card` link.
PAGE = (
    "claude-sonnet-5-5 " * 5
    + "claude-opus-5-5 " * 4
    + "claude-haiku-4-5-20251001 " * 3
    + "claude-sonnet-5 claude-sonnet-4-6 claude-sonnet-4-5-20250929 claude-sonnet-4-20250514 "
    + "claude-sonnet-3-7 claude-sonnet-5-5-system-card claude-haiku-4-5"
)


def pins_json(**models: str) -> Path:
    """A hub pins response; keyword names use `_` for the `-`/`.` in agent keys."""
    body = {"pins": {k.replace("_", "-"): {"model": m, "effort": "medium", "control": "editable"} for k, m in models.items()}}
    f = Path(tempfile.mkdtemp()) / "pins.json"
    f.write_text(json.dumps(body), encoding="utf-8")
    return f


def workflows(**files: str) -> Path:
    d = Path(tempfile.mkdtemp())
    for name, body in files.items():
        (d / f"{name.replace('_', '-')}.yml").write_text(body, encoding="utf-8")
    return d


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = check.main(argv)
    return code, out.getvalue(), err.getvalue()


class ParsingTests(unittest.TestCase):
    def ids(self, html):
        return [m["id"] for m in check.parse_catalog(html)]

    def test_catalog_covers_every_family_and_keeps_date_suffixed_ids_whole(self):
        self.assertEqual(self.ids(PAGE), ["claude-haiku-4-5-20251001", "claude-opus-5-5", "claude-sonnet-5-5"])

    def test_stray_mentions_stay_out_of_the_catalog(self):
        self.assertNotIn("claude-sonnet-9-9", self.ids(PAGE + " claude-sonnet-9-9 claude-sonnet-9-9"))

    def test_catalog_entries_match_the_hub_schema(self):
        entry = check.parse_catalog("claude-opus-5-5 " * 3)[0]
        self.assertEqual(entry, {"id": "claude-opus-5-5", "family": "opus", "display_name": "Claude Opus 5.5"})

    def test_latest_is_per_family_and_ignores_legacy_and_system_cards(self):
        latest = check.latest_by_family(check.parse_catalog(PAGE))
        self.assertEqual(latest, {"sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5", "haiku": "claude-haiku-4-5-20251001"})

    def test_date_suffix_is_not_a_minor_version(self):
        self.assertEqual(check.parse_id("claude-sonnet-4-20250514"), ("sonnet", (4, 0)))
        self.assertEqual(check.parse_id("claude-sonnet-4-5-20250929"), ("sonnet", (4, 5)))

    def test_alias_is_not_an_exact_id(self):
        self.assertIsNone(check.parse_id("sonnet"))
        self.assertFalse(check.is_behind("sonnet", {"sonnet": "claude-sonnet-5-5"}))

    def test_nothing_parseable_gives_no_latest(self):
        self.assertEqual(check.latest_by_family(check.parse_catalog("<html>redesigned</html>")), {})

    def test_yaml_grep_is_scoped_to_the_two_straggler_files(self):
        d = workflows(
            milestone_test_callable="  --model claude-sonnet-4-6\n",
            security_review='  default: "claude-sonnet-4-6"\n',
            implementer_callable="  --model claude-sonnet-4-5\n",
        )
        self.assertEqual(check.collect_yaml_pins(d), {"claude-sonnet-4-6": ["milestone-test-callable.yml", "security-review.yml"]})

    def test_yaml_aliases_report_nothing_to_bump(self):
        d = workflows(milestone_test_callable="  --model sonnet\n", security_review="  --model sonnet\n")
        self.assertEqual(check.collect_yaml_pins(d), {})


class MainTests(unittest.TestCase):
    def setUp(self):
        self.html = Path(tempfile.mkdtemp()) / "models.html"
        self.html.write_text(PAGE, encoding="utf-8")
        self.empty = workflows(security_review="  --model sonnet\n")

    def args(self, pins, *extra, d=None):
        return ["--workflows-dir", str(d or self.empty), "--models-html", str(self.html), "--pins-json", str(pins), *extra]

    def test_aliases_and_current_pins_are_a_quiet_success(self):
        code, out, _ = run(self.args(pins_json(implementer="sonnet", staff_engineer="claude-opus-5-5"), "--dry-run"))
        self.assertEqual(code, 0)
        self.assertIn("no exact-id pin is behind", out)
        self.assertNotIn("request_id", out)

    def test_behind_hub_pin_builds_a_stamped_entry(self):
        code, out, err = run(self.args(pins_json(implementer="claude-sonnet-4-6", lite_planner="sonnet"), "--dry-run"))
        self.assertEqual(code, 0)
        packet = json.loads(out)
        self.assertEqual(packet["variant"], "model-stale")
        self.assertEqual(packet["entry_type"], "follow-up")
        self.assertEqual(packet["agent_name"], "model-watch")
        self.assertEqual(packet["attempts"], ["implementer: claude-sonnet-4-6 (hub setting)"])
        self.assertIn("claude-sonnet-5-5", packet["title"])
        self.assertIn("would POST a catalog of 3 models", err)

    def test_each_family_is_compared_to_its_own_latest(self):
        code, out, _ = run(self.args(pins_json(implementer="claude-sonnet-5-5", staff_engineer="claude-opus-4-8"), "--dry-run"))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["attempts"], ["staff-engineer: claude-opus-4-8 (hub setting)"])

    def test_a_stale_yaml_pin_is_still_reported(self):
        d = workflows(security_review="  --model claude-sonnet-4-6\n")
        code, out, _ = run(self.args(pins_json(implementer="sonnet"), "--dry-run", d=d))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["attempts"], ["claude-sonnet-4-6: security-review.yml (YAML)"])

    def test_request_id_stable_for_same_state_and_changes_when_it_changes(self):
        first = json.loads(run(self.args(pins_json(implementer="claude-sonnet-4-6"), "--dry-run"))[1])["request_id"]
        again = json.loads(run(self.args(pins_json(implementer="claude-sonnet-4-6"), "--dry-run"))[1])["request_id"]
        self.assertEqual(first, again)
        other_pin = json.loads(run(self.args(pins_json(implementer="claude-sonnet-4-5"), "--dry-run"))[1])["request_id"]
        self.assertNotEqual(first, other_pin)
        newer = Path(tempfile.mkdtemp()) / "m.html"
        newer.write_text(PAGE + " claude-sonnet-5-6 " * 4, encoding="utf-8")
        argv = ["--workflows-dir", str(self.empty), "--models-html", str(newer), "--pins-json", str(pins_json(implementer="claude-sonnet-4-6")), "--dry-run"]
        self.assertNotEqual(first, json.loads(run(argv)[1])["request_id"])

    def test_unparseable_page_fails_and_posts_nothing(self):
        self.html.write_text("<html>redesigned</html>", encoding="utf-8")
        with mock.patch.object(check, "post_json") as post:
            code, _, err = run(self.args(pins_json(implementer="claude-sonnet-4-6"), "--service-role-key", "k"))
        self.assertEqual(code, 1)
        self.assertIn("could not determine", err)
        post.assert_not_called()

    def test_pinned_family_missing_from_page_fails_and_posts_nothing(self):
        with mock.patch.object(check, "post_json") as post:
            code, _, err = run(self.args(pins_json(implementer="claude-fable-1-0"), "--service-role-key", "k"))
        self.assertEqual(code, 1)
        self.assertIn("claude-fable-1-0", err)
        post.assert_not_called()

    def test_unreadable_pins_fail_and_post_nothing(self):
        bad = Path(tempfile.mkdtemp()) / "pins.json"
        bad.write_text(json.dumps({"pins": {}}), encoding="utf-8")
        with mock.patch.object(check, "post_json") as post:
            code, _, err = run(self.args(bad, "--service-role-key", "k"))
        self.assertEqual(code, 1)
        self.assertIn("could not read the hub's stored pins", err)
        post.assert_not_called()

    def test_missing_key_fails_rather_than_silently_skipping(self):
        code, _, err = run(self.args(pins_json(implementer="sonnet"), "--service-role-key", ""))
        self.assertEqual(code, 1)
        self.assertIn("QUEUE_SERVICE_ROLE_KEY", err)

    def test_publishes_catalog_then_entry_with_the_same_key(self):
        with mock.patch.object(check, "post_json", return_value=(200, "{}")) as post:
            code, _, _ = run(self.args(pins_json(implementer="claude-sonnet-4-6"), "--service-role-key", "k", "--hub-url", "https://hub.test"))
        self.assertEqual(code, 0)
        (c_hub, c_path, c_key, c_body), (q_hub, q_path, q_key, q_body) = (c.args for c in post.call_args_list)
        self.assertEqual((c_hub, c_path, c_key), ("https://hub.test", check.CATALOG_PATH, "k"))
        self.assertEqual([m["id"] for m in c_body["models"]], ["claude-haiku-4-5-20251001", "claude-opus-5-5", "claude-sonnet-5-5"])
        self.assertEqual((q_path, q_key, q_body["variant"]), (check.QUEUE_PATH, "k", "model-stale"))

    def test_current_pins_still_publish_the_catalog_but_post_no_entry(self):
        with mock.patch.object(check, "post_json", return_value=(200, "{}")) as post:
            code, _, _ = run(self.args(pins_json(implementer="sonnet"), "--service-role-key", "k"))
        self.assertEqual(code, 0)
        self.assertEqual([c.args[1] for c in post.call_args_list], [check.CATALOG_PATH])

    def test_catalog_failure_is_loud_but_does_not_hide_the_nag(self):
        def fake(hub, path, key, body):
            if path == check.CATALOG_PATH:
                raise OSError("boom")
            return 200, "{}"

        with mock.patch.object(check, "post_json", side_effect=fake) as post:
            code, _, err = run(self.args(pins_json(implementer="claude-sonnet-4-6"), "--service-role-key", "k"))
        self.assertEqual(code, 1)
        self.assertIn("catalog POST failed", err)
        self.assertEqual([c.args[1] for c in post.call_args_list], [check.CATALOG_PATH, check.QUEUE_PATH])


if __name__ == "__main__":
    unittest.main()
