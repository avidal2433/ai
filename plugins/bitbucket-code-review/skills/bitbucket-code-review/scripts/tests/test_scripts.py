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


class ThreadTests(unittest.TestCase):
    def pub(self, *script):
        t = FakeTransport(*script)
        return bc.Publisher("ws", "repo", 7, transport=t, auth="x"), t

    def test_reply_goes_into_the_thread(self):
        pub, t = self.pub({"id": 10})
        pub.post_reply("(AI) ok", 3)
        self.assertEqual(t.calls[0][2], {"content": {"raw": "(AI) ok"}, "parent": {"id": 3}})

    def ai(self, author="me", **extra):
        return {"content": {"raw": "(AI) x"}, "user": {"account_id": author}, **extra}

    def test_resolves_ai_root_comment(self):
        pub, t = self.pub(self.ai(resolution={}), {"type": "comment_resolution"})
        self.assertEqual(bc.resolve_comment(pub, 3, "me"), (True, "resolved", None))
        self.assertEqual(t.calls[1][:2], ("POST", f"{bc.API}/repositories/ws/repo/pullrequests/7/comments/3/resolve"))

    def test_human_comment_is_left_untouched(self):
        pub, t = self.pub({"content": {"raw": "por favor revisa"}, "user": {"account_id": "me"}})
        self.assertEqual(bc.resolve_comment(pub, 3, "me")[:2], (False, "skipped"))
        self.assertEqual(len(t.calls), 1)

    def test_ai_prefix_from_another_account_is_left_untouched(self):
        pub, t = self.pub(self.ai(author="someone"))
        self.assertEqual(bc.resolve_comment(pub, 3, "me")[:2], (False, "skipped"))
        self.assertEqual(len(t.calls), 1)

    def test_reply_is_not_resolved(self):
        pub, t = self.pub(self.ai(parent={"id": 1}))
        self.assertEqual(bc.resolve_comment(pub, 3, "me")[:2], (False, "skipped"))
        self.assertEqual(len(t.calls), 1)

    def test_already_resolved_counts_as_ok(self):
        pub, _ = self.pub(self.ai(), bc.BitbucketError(409, "resolved"))
        self.assertEqual(bc.resolve_comment(pub, 3, "me"), (True, "already_resolved", None))

    def test_resolve_cli_refuses_without_reviewer_identity(self):
        with mock.patch.dict(os.environ, {"BITBUCKET_EMAIL": "e", "BITBUCKET_API_TOKEN": "t"}, clear=True), \
             mock.patch.object(bc, "reviewer_identity", return_value=None), \
             mock.patch("sys.stderr"):
            self.assertEqual(bc.main(["x", "resolve", "ws", "repo", "7", "3"]), 2)


class ReviewStateTests(unittest.TestCase):
    PR = f"{bc.API}/repositories/ws/repo/pullrequests/7"
    REQUESTED = {"participants": [{"user": {"account_id": "me"}, "state": "changes_requested"}]}
    EVENT = {"values": [{"changes_requested": {"date": "2026-10-21T20:25:46+00:00", "user": {"account_id": "me"}}}]}

    def pub(self, *script):
        t = FakeTransport(*script)
        return bc.Publisher("ws", "repo", 7, transport=t, auth="x"), t

    def marker(self, created_on="2026-10-21T20:25:48+00:00", author="me"):
        return {"values": [{"content": {"raw": bc.CHANGES_BODY}, "created_on": created_on,
                            "user": {"account_id": author}}]}

    def test_request_posts_the_marker_after_requesting(self):
        pub, t = self.pub({"state": "changes_requested"}, {"id": 9})
        self.assertEqual(bc.request_changes(pub), {"ok": True, "status": "changes_requested", "marker": 9, "error": None})
        self.assertEqual([c[:2] for c in t.calls], [("POST", f"{self.PR}/request-changes"), ("POST", f"{self.PR}/comments")])
        self.assertTrue(t.calls[1][2]["content"]["raw"].startswith(bc.CHANGES_MARK))

    def test_failed_request_posts_no_marker(self):
        pub, t = self.pub(bc.BitbucketError(400, "merged"))
        result = bc.request_changes(pub)
        self.assertEqual((result["ok"], result["status"]), (False, "failed"))
        self.assertEqual(len(t.calls), 1)

    def test_request_without_marker_is_reported(self):
        pub, _ = self.pub({"state": "changes_requested"}, bc.BitbucketError(401, "nope"))
        result = bc.request_changes(pub)
        self.assertEqual((result["ok"], result["marker"]), (True, None))
        self.assertIn("no se retirará sola", result["error"])

    def test_withdraws_a_request_with_its_marker(self):
        pub, t = self.pub(self.REQUESTED, self.EVENT, self.marker(), None)
        self.assertEqual(bc.withdraw_changes(pub, "me")["status"], "withdrawn")
        self.assertEqual(t.calls[-1][:2], ("DELETE", f"{self.PR}/request-changes"))

    def test_keeps_a_request_set_by_hand_after_an_ai_review(self):
        ai_comment = {"values": [{"content": {"raw": "(AI) hallazgo"}, "created_on": "2026-10-21T20:20:00+00:00",
                                  "user": {"account_id": "me"}}]}
        pub, t = self.pub(self.REQUESTED, self.EVENT, ai_comment, None)
        self.assertEqual(bc.withdraw_changes(pub, "me")["status"], "skipped")
        self.assertNotIn("DELETE", [c[0] for c in t.calls])

    def test_keeps_a_later_manual_request_despite_an_old_marker(self):
        pub, t = self.pub(self.REQUESTED, self.EVENT, self.marker("2026-10-20T10:00:00+00:00"), None)
        self.assertEqual(bc.withdraw_changes(pub, "me")["status"], "skipped")
        self.assertNotIn("DELETE", [c[0] for c in t.calls])

    def test_marker_from_another_account_does_not_count(self):
        pub, t = self.pub(self.REQUESTED, self.EVENT, self.marker(author="someone"), None)
        self.assertEqual(bc.withdraw_changes(pub, "me")["status"], "skipped")
        self.assertNotIn("DELETE", [c[0] for c in t.calls])

    def test_nothing_to_withdraw_without_a_current_request(self):
        pub, t = self.pub({"participants": [{"user": {"account_id": "me"}, "state": None}]})
        self.assertEqual(bc.withdraw_changes(pub, "me")["status"], "skipped")
        self.assertEqual(len(t.calls), 1)

    def test_withdraw_cli_refuses_without_reviewer_identity(self):
        with mock.patch.dict(os.environ, {"BITBUCKET_EMAIL": "e", "BITBUCKET_API_TOKEN": "t"}, clear=True), \
             mock.patch.object(bc, "reviewer_identity", return_value=None), \
             mock.patch("sys.stderr"):
            self.assertEqual(bc.main(["x", "request-changes", "ws", "repo", "7", "--withdraw"]), 2)

    def finding(self, created_on, raw="(AI) hallazgo", author="me", **extra):
        return {"content": {"raw": raw}, "created_on": created_on, "user": {"account_id": author}, **extra}

    def test_request_does_not_repeat_a_current_request(self):
        pub, t = self.pub(self.REQUESTED, self.EVENT, self.marker())
        self.assertEqual(bc.request_changes(pub, "me"),
                         {"ok": True, "status": "already_requested", "marker": None, "error": None})
        self.assertEqual([c[0] for c in t.calls], ["GET", "GET", "GET"])

    def test_request_is_renewed_after_new_findings(self):
        comments = {"values": self.marker()["values"] + [self.finding("2026-10-22T15:28:43+00:00")]}
        pub, t = self.pub(self.REQUESTED, self.EVENT, comments, None, {"state": "changes_requested"}, {"id": 9})
        self.assertEqual(bc.request_changes(pub, "me"),
                         {"ok": True, "status": "changes_requested", "marker": 9, "error": None})
        self.assertEqual([c[:2] for c in t.calls[3:]], [("DELETE", f"{self.PR}/request-changes"),
                                                        ("POST", f"{self.PR}/request-changes"),
                                                        ("POST", f"{self.PR}/comments")])

    def test_a_reply_after_the_request_also_renews_it(self):
        reply = self.finding("2026-10-22T15:28:44+00:00", parent={"id": 1})
        pub, t = self.pub(self.REQUESTED, self.EVENT, {"values": self.marker()["values"] + [reply]},
                          None, {"state": "changes_requested"}, {"id": 9})
        self.assertEqual(bc.request_changes(pub, "me")["status"], "changes_requested")
        self.assertIn("DELETE", [c[0] for c in t.calls])

    def test_findings_before_the_request_or_from_others_do_not_renew_it(self):
        comments = {"values": self.marker()["values"] + [
            self.finding("2026-10-21T20:25:40+00:00"),
            self.finding("2026-10-22T15:28:43+00:00", author="someone"),
            self.finding("2026-10-22T15:28:43+00:00", raw="por favor revisa"),
            self.finding("2026-10-22T15:28:43+00:00", deleted=True),
            self.finding("2026-10-22T15:28:43+00:00", raw=ApproveTests.LEGACY_MARKER)]}
        pub, t = self.pub(self.REQUESTED, self.EVENT, comments)
        self.assertEqual(bc.request_changes(pub, "me")["status"], "already_requested")
        self.assertEqual(len(t.calls), 3)

    def test_a_request_set_by_hand_is_not_renewed(self):
        pub, t = self.pub(self.REQUESTED, self.EVENT, {"values": [self.finding("2026-10-22T15:28:43+00:00")]})
        result = bc.request_changes(pub, "me")
        self.assertEqual((result["ok"], result["status"]), (True, "already_requested"))
        self.assertIn("no se renueva", result["error"] or "")
        self.assertNotIn("DELETE", [c[0] for c in t.calls])

    def test_a_renewal_that_cannot_request_again_says_the_request_was_withdrawn(self):
        comments = {"values": self.marker()["values"] + [self.finding("2026-10-22T15:28:43+00:00")]}
        pub, t = self.pub(self.REQUESTED, self.EVENT, comments, None, bc.BitbucketError(400, "merged"))
        result = bc.request_changes(pub, "me")
        self.assertEqual((result["ok"], result["status"]), (False, "failed"))
        self.assertIn("se retiró la solicitud anterior", result["error"])
        self.assertNotEqual(t.calls[-1][1], f"{self.PR}/comments")

    def test_request_replaces_the_account_approval(self):
        pub, t = self.pub(ApproveTests.APPROVED, None, {"state": "changes_requested"}, {"id": 9})
        self.assertEqual(bc.request_changes(pub, "me")["status"], "changes_requested")
        self.assertEqual([c[:2] for c in t.calls[1:3]],
                         [("DELETE", f"{self.PR}/approve"), ("POST", f"{self.PR}/request-changes")])


class ApproveTests(unittest.TestCase):
    PR = ReviewStateTests.PR
    HEAD = "5aedfc7f4539cde06e213a71a1e82c4b5ba48cf0"
    REF = {"target": {"hash": HEAD}}
    BRANCH = f"{bc.API}/repositories/ws/repo/refs/branches/feature%2Fgds"
    APPROVED = {"participants": [{"user": {"account_id": "me"}, "state": "approved"}]}
    GREEN = {"values": [{"name": "Pipeline", "state": "SUCCESSFUL"}]}
    LEGACY_MARKER = "(AI) Aprobado: la revisión no encontró defectos pendientes en `5aedfc7f4539`."

    def pr(self, state=None, **extra):
        return {"state": "OPEN", "draft": False,
                "source": {"commit": {"hash": self.HEAD[:12]}, "branch": {"name": "feature/gds"},
                           "repository": {"full_name": "ws/repo"}},
                "author": {"account_id": "author"},
                "participants": [{"user": {"account_id": "me"}, "state": state}], **extra}

    def pub(self, *script):
        t = FakeTransport(*script)
        return bc.Publisher("ws", "repo", 7, transport=t, auth="x"), t

    def test_approves_the_reviewed_commit_without_a_comment(self):
        pub, t = self.pub(self.pr(), self.REF, self.GREEN, {"approved": True}, self.REF)
        self.assertEqual(bc.approve(pub, "me", self.HEAD),
                         {"ok": True, "status": "approved", "marker": None, "error": None})
        self.assertEqual([c[:2] for c in t.calls[1:]],
                         [("GET", self.BRANCH),
                          ("GET", f"{bc.API}/repositories/ws/repo/commit/{self.HEAD}/statuses?pagelen=100"),
                          ("POST", f"{self.PR}/approve"), ("GET", self.BRANCH)])

    def test_needs_the_full_reviewed_sha(self):
        for commit in (self.HEAD[:12], self.HEAD.upper(), None):
            pub, t = self.pub()
            self.assertEqual(bc.approve(pub, "me", commit)["status"], "skipped")
            self.assertEqual(t.calls, [])

    def test_does_not_approve_a_pr_that_changed_after_the_review(self):
        moved = {"target": {"hash": self.HEAD[:12] + "0" * 28}}
        pub, t = self.pub(self.pr(), moved)
        self.assertEqual(bc.approve(pub, "me", self.HEAD)["status"], "skipped")
        self.assertNotIn("POST", [c[0] for c in t.calls])

    def test_withdraws_an_approval_when_the_pr_moves_while_approving(self):
        moved = {"target": {"hash": "f" * 40}}
        pub, t = self.pub(self.pr(), self.REF, self.GREEN, {"approved": True}, moved, None)
        result = bc.approve(pub, "me", self.HEAD)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(t.calls[-1][:2], ("DELETE", f"{self.PR}/approve"))
        self.assertEqual([c[1] for c in t.calls].count(f"{self.PR}/comments"), 0)

    def test_does_not_approve_closed_draft_or_own_prs(self):
        for pr in (self.pr(state=None, draft=True), {**self.pr(), "state": "MERGED"},
                   {**self.pr(), "author": {"account_id": "me"}}):
            pub, t = self.pub(pr)
            self.assertEqual(bc.approve(pub, "me", self.HEAD)["status"], "skipped")
            self.assertEqual(len(t.calls), 1)

    def test_does_not_approve_with_failed_or_running_checks(self):
        for state in bc.BLOCKING_CHECKS:
            pub, t = self.pub(self.pr(), self.REF, {"values": [{"name": "Pipeline", "state": state}]})
            result = bc.approve(pub, "me", self.HEAD)
            self.assertEqual(result["status"], "skipped")
            self.assertIn("Pipeline", result["error"])
            self.assertNotIn("POST", [c[0] for c in t.calls])

    def test_keeps_a_current_approval(self):
        pub, t = self.pub(self.pr("approved"), self.REF, self.GREEN)
        self.assertEqual(bc.approve(pub, "me", self.HEAD)["status"], "already_approved")
        self.assertEqual(len(t.calls), 3)

    def test_replaces_an_ai_change_request(self):
        pub, t = self.pub(self.pr("changes_requested"), self.REF, self.GREEN, ReviewStateTests.EVENT,
                          ReviewStateTests().marker(), None, {"approved": True}, self.REF)
        self.assertEqual(bc.approve(pub, "me", self.HEAD)["status"], "approved")
        self.assertEqual([c[:2] for c in t.calls[5:7]],
                         [("DELETE", f"{self.PR}/request-changes"), ("POST", f"{self.PR}/approve")])

    def test_keeps_a_change_request_set_by_hand(self):
        pub, t = self.pub(self.pr("changes_requested"), self.REF, self.GREEN, ReviewStateTests.EVENT, {"values": []})
        self.assertEqual(bc.approve(pub, "me", self.HEAD)["status"], "skipped")
        self.assertNotIn("POST", [c[0] for c in t.calls])

    def test_withdraws_the_account_approval(self):
        pub, t = self.pub(self.APPROVED, None)
        self.assertEqual(bc.withdraw_approval(pub, "me")["status"], "withdrawn")
        self.assertEqual(t.calls[-1][:2], ("DELETE", f"{self.PR}/approve"))

    def test_withdraw_skips_without_a_current_approval(self):
        pub, t = self.pub(self.pr())
        self.assertEqual(bc.withdraw_approval(pub, "me")["status"], "skipped")
        self.assertNotIn("DELETE", [c[0] for c in t.calls])

    def test_approve_cli_needs_identity_and_a_commit(self):
        with mock.patch.dict(os.environ, {"BITBUCKET_EMAIL": "e", "BITBUCKET_API_TOKEN": "t"}, clear=True), \
             mock.patch("sys.stderr"):
            self.assertEqual(bc.main(["x", "approve", "ws", "repo", "7"]), 2)
            self.assertEqual(bc.main(["x", "approve", "ws", "repo", "7", "--commit", self.HEAD[:12]]), 2)
            with mock.patch.object(bc, "reviewer_identity", return_value=None):
                self.assertEqual(bc.main(["x", "approve", "ws", "repo", "7", "--commit", self.HEAD]), 2)


class TransportTests(unittest.TestCase):
    def sent_headers(self, payload):
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = b""
        with mock.patch.object(bc.urllib.request, "urlopen", return_value=resp) as urlopen:
            bc.request("POST", "https://example.test", payload, auth="x")
        return {k.lower(): v for k, v in urlopen.call_args[0][0].header_items()}

    def test_bodyless_request_omits_json_content_type(self):
        self.assertNotIn("content-type", self.sent_headers(None))

    def test_json_body_declares_its_content_type(self):
        self.assertEqual(self.sent_headers({"a": 1})["content-type"], "application/json")


class WorktreeTests(unittest.TestCase):
    SCRIPT = os.path.join(SCRIPTS, "pr_worktree.sh")

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", self.tmp]))
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.repo, "vendor"))
        for path, content in (("app.txt", "code"), ("vendor/lib.txt", "dep"), (".env", "X=1"),
                              (".gitignore", "vendor/\n.env\n")):
            with open(os.path.join(self.repo, path), "w", encoding="utf-8") as f:
                f.write(content)
        git = lambda *a: subprocess.run(["git", "-C", self.repo, *a], check=True, capture_output=True, text=True)
        git("init", "-q")
        git("add", "app.txt", ".gitignore")
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "head")
        self.sha = git("rev-parse", "HEAD").stdout.strip()
        self.wt = os.path.join(self.tmp, "wt")

    def run_script(self, *args):
        return subprocess.run(["bash", self.SCRIPT, *args], capture_output=True, text=True)

    def test_create_copies_links_and_removes(self):
        out = self.run_script("create", self.repo, self.sha, self.wt, "--copy", "vendor",
                              "--link", ".env", "--mkdir", "storage/logs", "--check", "true")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertFalse(os.path.islink(os.path.join(self.wt, "vendor")))
        self.assertTrue(os.path.isfile(os.path.join(self.wt, "vendor", "lib.txt")))
        self.assertTrue(os.path.islink(os.path.join(self.wt, ".env")))
        self.assertTrue(os.path.isdir(os.path.join(self.wt, "storage", "logs")))
        self.assertEqual(self.run_script("remove", self.repo, self.wt).returncode, 0)
        self.assertFalse(os.path.exists(self.wt))

    def test_paths_outside_the_repository_are_rejected(self):
        for flag, path in (("--copy", "../outside"), ("--link", "/etc/hosts"), ("--mkdir", "a/../../b")):
            out = self.run_script("create", self.repo, self.sha, self.wt, flag, path)
            self.assertEqual(out.returncode, 2, (flag, path))
            self.assertFalse(os.path.exists(self.wt))

    def test_failed_check_exits_3_without_worktree(self):
        out = self.run_script("create", self.repo, self.sha, self.wt, "--check", "false")
        self.assertEqual(out.returncode, 3)
        self.assertIn("PRERREQUISITO NO DISPONIBLE", out.stderr)
        self.assertFalse(os.path.exists(self.wt))


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
