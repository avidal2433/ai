#!/usr/bin/env python3
"""
Tests sin red para los scripts de la skill.

  python3 -m unittest discover -s bitbucket-code-review/scripts/tests -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
sys.path.insert(0, SCRIPTS)

import bitbucket_comments as bc  # noqa: E402
import diff_line_map  # noqa: E402
import semantic_release_check as src  # noqa: E402


class NormalizeTests(unittest.TestCase):
    def test_links_and_crlf_from_bitbucket(self):
        raw = ("refactor(navigation): add cache - [(PR:3787)](https://bitbucket.org/x/y/pull-requests/3787)"
               "\r\n\r\nTask: [#TEAM-2369](https://app.clickup.com/t/1)")
        self.assertEqual(src.normalize(raw), "refactor(navigation): add cache - (PR:3787)\n\nTask: #TEAM-2369")

    def test_underscore_bold(self):
        raw = "refactor(navigation): add cache - __(PR:3787)__\n\nTask: __#TEAM-2369__"
        self.assertEqual(src.normalize(raw), "refactor(navigation): add cache - (PR:3787)\n\nTask: #TEAM-2369")

    def test_star_bold_and_escapes(self):
        self.assertEqual(src.normalize("**fix(auth):** login - (PR:1)\nTask: \\#APP-9"),
                         "fix(auth): login - (PR:1)\nTask: #APP-9")

    def test_snake_case_scope_is_kept(self):
        self.assertEqual(src.normalize("fix(order_items): x - (PR:1)"), "fix(order_items): x - (PR:1)")

    def test_collapses_blank_lines_and_trims(self):
        self.assertEqual(src.normalize("  a  \n\n\n\n b \n\n"), "a\n\nb")


class ValidateTests(unittest.TestCase):
    POLICY = {"header_pattern": r"fix: .+ - \(PR:(?P<pr>\d+)\)",
              "require_task": True, "message": "Usa el formato documentado en el repo."}

    def test_no_policy_does_not_impose_project_rules(self):
        for raw in ("", "Plain description", "test: more tests", "build: image"):
            r = src.validate(raw)
            self.assertFalse(r["applicable"])
            self.assertIsNone(r["valid"])
            self.assertIsNone(r["comment_body"])

    def test_explicit_policy(self):
        r = src.validate("fix: empty input - (PR:12)\nTask: #TEAM-1", 12, self.POLICY)
        self.assertTrue(r["valid"])
        r = src.validate("fix: empty input - (PR:13)\nTask: #TEAM-1", 12, self.POLICY)
        self.assertEqual(r["errors"], ["pr_id_mismatch"])
        self.assertIn("12", r["comment_body"])

    def test_missing_task_only_when_required(self):
        self.assertEqual(src.validate("fix: input - (PR:12)", 12, self.POLICY)["errors"], ["task_missing"])

    def test_extract_links_independently_of_header(self):
        text = "Details [ticket](https://app.clickup.com/t/86abc123) " + \
               "https://app.clickup.com/t/123/TEAM-9\nTask: **#TEAM-9**"
        self.assertEqual(src.extract_tasks(text), ["86abc123", "TEAM-9"])
        self.assertEqual(src.extract_tasks("https://example.com/t/123\nRelated #123"), [])

    def test_invalid_policy_is_configuration_error(self):
        for policy in ({}, {"message": "x", "require_task": "false"}):
            with self.assertRaises(ValueError):
                src.validate("", policy=policy)


class CliTests(unittest.TestCase):
    SCRIPT = os.path.join(SCRIPTS, "semantic_release_check.py")

    def test_default_skips_validation(self):
        out = subprocess.run([sys.executable, self.SCRIPT, "--description", "Plain text"],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0)
        self.assertIsNone(json.loads(out.stdout)["valid"])

    def test_policy_failure_exit_1(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "policy.json")
            with open(path, "w") as f:
                json.dump(ValidateTests.POLICY, f)
            out = subprocess.run([sys.executable, self.SCRIPT, "--description", "Plain text",
                                  "--policy", path], capture_output=True, text=True)
            self.assertEqual(out.returncode, 1, out.stderr)
            self.assertFalse(json.loads(out.stdout)["valid"])

    def test_no_args_exit_2(self):
        out = subprocess.run([sys.executable, self.SCRIPT], capture_output=True)
        self.assertEqual(out.returncode, 2)


DIFF = """diff --git a/app/Foo.php b/app/Foo.php
--- a/app/Foo.php
+++ b/app/Foo.php
@@ -10,4 +10,5 @@
 line10
-old11
+new11
+new12
 line13
"""


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.files = diff_line_map.parse(DIFF)

    def test_inline_exact_keeps_existing_prefix(self):
        p = bc.plan_entry({"body": "(AI) x", "path": "app/Foo.php", "line": 11, "kind": "to"}, self.files)
        self.assertEqual((p["mode"], p["line"], p["body"]), ("inline", 11, "(AI) x"))

    def test_nearest_line_and_prefix_added(self):
        p = bc.plan_entry({"body": "x", "path": "app/Foo.php", "line": 14}, self.files)
        self.assertEqual((p["mode"], p["line"], p["body"]), ("inline", 12, "(AI) x"))
        self.assertIn("usada la 12", p["reason"])

    def test_fallback_when_line_far(self):
        p = bc.plan_entry({"body": "x", "path": "app/Foo.php", "line": 40}, self.files)
        self.assertEqual(p["mode"], "global_fallback")
        self.assertEqual(p["body"], "(AI) [No inline por mapeo de diff: app/Foo.php:40] x")

    def test_fallback_when_path_not_in_diff(self):
        p = bc.plan_entry({"body": "x", "path": "app/Other.php", "line": 11}, self.files)
        self.assertEqual(p["mode"], "global_fallback")

    def test_global(self):
        p = bc.plan_entry({"body": "x"}, self.files)
        self.assertEqual((p["mode"], p["path"], p["body"]), ("global", None, "(AI) x"))

    def test_from_line(self):
        p = bc.plan_entry({"body": "x", "path": "app/Foo.php", "line": 11, "kind": "from"}, self.files)
        self.assertEqual((p["mode"], p["kind"]), ("inline", "from"))

    def test_resolves_a_prefix(self):
        p = bc.plan_entry({"body": "x", "path": "b/app/Foo.php", "line": 11}, self.files)
        self.assertEqual((p["mode"], p["path"]), ("inline", "app/Foo.php"))


class FakeTransport:
    def __init__(self, *script):
        self.script = list(script)
        self.calls = []

    def __call__(self, method, url, payload, auth):
        self.calls.append((method, url, payload))
        nxt = self.script.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return 200, nxt


class PublishFlowTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(bc.time, "sleep", lambda s: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.files = diff_line_map.parse(DIFF)

    def publish(self, entry, *script):
        t = FakeTransport(*script)
        pub = bc.Publisher("ws", "repo", 7, transport=t, auth="x")
        return bc.publish_entry(pub, bc.plan_entry(entry, self.files)), t

    def test_inline_ok(self):
        r, t = self.publish({"body": "x", "path": "app/Foo.php", "line": 11},
                            {"id": 1, "inline": {"path": "app/Foo.php", "to": 11}})
        self.assertEqual((r["mode"], r["id"]), ("inline", 1))
        self.assertEqual(t.calls[0][2], {"content": {"raw": "(AI) x"}, "inline": {"path": "app/Foo.php", "to": 11}})

    def test_inline_400_falls_back_to_global(self):
        r, t = self.publish({"body": "x", "path": "app/Foo.php", "line": 11},
                            bc.BitbucketError(400, "bad"), {"id": 2})
        self.assertEqual((r["mode"], r["id"]), ("global_fallback", 2))
        self.assertNotIn("inline", t.calls[1][2])
        self.assertEqual(t.calls[1][2]["content"]["raw"], "(AI) [No inline por mapeo de diff: app/Foo.php:11] x")

    def test_inline_mismatch_deletes_and_falls_back(self):
        r, t = self.publish({"body": "x", "path": "app/Foo.php", "line": 11},
                            {"id": 3, "inline": {"path": "app/Foo.php", "to": 99}}, None, {"id": 4})
        self.assertEqual((r["mode"], r["id"]), ("global_fallback", 4))
        self.assertEqual(t.calls[1][0], "DELETE")
        self.assertTrue(t.calls[1][1].endswith("/pullrequests/7/comments/3"))

    def test_5xx_retries_once(self):
        r, t = self.publish({"body": "x"}, bc.BitbucketError(503, "down"), {"id": 5})
        self.assertEqual((r["mode"], r["id"]), ("global", 5))
        self.assertEqual(len(t.calls), 2)

    def test_401_is_raised(self):
        with self.assertRaises(bc.BitbucketError):
            self.publish({"body": "x"}, bc.BitbucketError(401, "nope"))


class PublishCliTests(unittest.TestCase):
    SCRIPT = os.path.join(SCRIPTS, "bitbucket_comments.py")

    def test_dry_run_reports_modes_without_network(self):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", d]))
        with open(os.path.join(d, "diff.patch"), "w", encoding="utf-8") as f:
            f.write(DIFF)
        with open(os.path.join(d, "publish.json"), "w", encoding="utf-8") as f:
            json.dump([{"body": "a", "path": "app/Foo.php", "line": 11},
                       {"body": "b", "path": "app/Foo.php", "line": 14},
                       {"body": "c", "path": "app/Foo.php", "line": 40},
                       {"body": "d"}], f)
        out = subprocess.run([sys.executable, self.SCRIPT, "publish", "ws", "repo", "7",
                              os.path.join(d, "publish.json"), "--diff", os.path.join(d, "diff.patch"),
                              "--dry-run"], capture_output=True, text=True, env={})
        self.assertEqual(out.returncode, 0, out.stderr)
        lines = [json.loads(ln) for ln in out.stdout.splitlines()]
        self.assertEqual([ln["mode"] for ln in lines[:4]], ["inline", "inline", "global_fallback", "global"])
        self.assertEqual(lines[4], {"summary": {"inline": 2, "global": 1, "fallback": 1, "failed": 0}})


if __name__ == "__main__":
    unittest.main()
