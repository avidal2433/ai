"""Regresiones de compactación, contexto opcional y líneas del diff; sin red."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import clickup_task
import diff_line_map
import history_context as history
import removal_audit
import review_context
import review_delta


class ContextTests(unittest.TestCase):
    def test_compact_retains_evidence_and_removes_api_noise(self):
        body = 'Evidence ' * 1000
        pr = {'id': 1, 'description': '[task](https://app.clickup.com/t/abc123)',
              'links': {'unused': 'noise'}}
        result = review_context.summarize(pr,
            [{'old': {'path': 'old'}, 'new': {'path': 'new'}, 'status': 'renamed'}],
            [{'id': 1, 'content': {'raw': body}, 'parent': {'id': 2}},
             {'id': 3, 'deleted': True}], [{'state': 'FAILED', 'name': 'tests'}])
        self.assertEqual(result['tasks'], ['abc123'])
        self.assertEqual(result['comments'], [{'id': 1, 'parent': 2, 'body': body}])
        self.assertIsNone(result['previous_review'])
        self.assertEqual(result['files'][0]['old_path'], 'old')
        self.assertNotIn('links', result)
        self.assertEqual(result['checks'], {'FAILED': 1})
        self.assertEqual(result['check_runs'], [{'name': 'tests', 'state': 'FAILED', 'url': None}])

    def test_internal_task_does_not_require_workspace(self):
        with mock.patch.dict(os.environ, {'CLICKUP_API_KEY': 'test'}, clear=True), \
             mock.patch.object(clickup_task, 'fetch_task', return_value={'id': 'abc'}) as fetch, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(clickup_task.main(['script', 'abc']), 0)
            fetch.assert_called_once_with('abc', None, 'test', by_custom_id=False)

    def test_custom_task_requires_workspace(self):
        with mock.patch.dict(os.environ, {'CLICKUP_API_KEY': 'test'}, clear=True), \
             mock.patch.object(clickup_task, 'fetch_task') as fetch, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(clickup_task.main(['script', 'TEAM-1']), 1)
            fetch.assert_not_called()

    def test_diff_content_cannot_replace_file_headers(self):
        diff = 'diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1,2 +1,2 @@\n--- value\n+++ value\n unchanged\n'
        self.assertEqual(diff_line_map.parse(diff), {'a.txt': {'to': [1], 'from': [1]}})


class CommentResolutionTests(unittest.TestCase):
    def summarize(self, comments):
        return {c['id']: c for c in review_context.summarize({}, [], comments, [])['comments']}

    def test_empty_resolution_from_listing_is_resolved_and_truncates_the_thread(self):
        long = 'x' * (review_context.RESOLVED_BODY_CHARS + 50)
        result = self.summarize([
            {'id': 1, 'content': {'raw': long}, 'resolution': {}, 'inline': {'path': 'a.php', 'to': 3, 'from': None}},
            {'id': 2, 'content': {'raw': long}, 'parent': {'id': 1}},
            {'id': 3, 'content': {'raw': long}, 'resolution': None}])
        self.assertTrue(result[1]['resolved'])
        self.assertEqual(result[1]['inline'], {'path': 'a.php', 'to': 3})
        self.assertTrue(result[1]['truncated'] and result[2]['truncated'])
        self.assertNotIn('resolved', result[2])
        self.assertFalse(result[3]['resolved'])
        self.assertEqual(result[3]['body'], long)

    def test_resolver_and_date(self):
        result = self.summarize([{'id': 1, 'content': {'raw': 'a'}, 'resolution': {
            'user': {'display_name': 'Autor'}, 'created_on': '2026-10-13T14:48:29+00:00'}}])
        self.assertEqual((result[1]['resolved_by'], result[1]['resolved_on']),
                         ('Autor', '2026-10-13T14:48:29+00:00'))


class ReviewDeltaTests(unittest.TestCase):
    def commit(self, repo, files, message, date):
        for path, content in files.items():
            Path(repo, path).write_text(content)
        env = {**os.environ, 'GIT_COMMITTER_DATE': date, 'GIT_AUTHOR_DATE': date}
        subprocess.run(['git', '-C', repo, 'add', '.'], check=True)
        subprocess.run(['git', '-C', repo, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', message],
                       check=True, env=env)
        return self.rev(repo, 'HEAD')

    def rev(self, repo, ref):
        return subprocess.run(['git', '-C', repo, 'rev-parse', ref], check=True,
                              capture_output=True, text=True).stdout.strip()

    def ai(self, cid, path, created_on, resolution=None):
        return {'id': cid, 'content': {'raw': '(AI) x'}, 'created_on': created_on,
                'user': {'account_id': 'reviewer'}, 'resolution': resolution,
                'inline': {'path': path, 'to': 1} if path else None}

    def test_own_commits_merge_conflicts_and_threads_to_recheck(self):
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as out:
            git = lambda *a: subprocess.run(['git', '-C', repo, *a], check=True, capture_output=True)
            git('init', '-q', '-b', 'main')
            self.commit(repo, {'c.txt': 'base\n'}, 'base', '2026-10-01T10:00:00+00:00')
            git('checkout', '-q', '-b', 'feature')
            reviewed = self.commit(repo, {'f.txt': 'a\n', 'c.txt': 'feature\n'}, 'a', '2026-10-02T10:00:00+00:00')
            git('checkout', '-q', 'main')
            destination = self.commit(repo, {'c.txt': 'main\n'}, 'main', '2026-10-03T10:00:00+00:00')
            git('checkout', '-q', 'feature')
            subprocess.run(['git', '-C', repo, 'merge', '-q', 'main'], capture_output=True)
            Path(repo, 'c.txt').write_text('resolved\n')
            self.commit(repo, {}, 'merge main', '2026-10-04T10:00:00+00:00')
            source = self.commit(repo, {'g.txt': 'b\n'}, 'b', '2026-10-05T10:00:00+00:00')
            other = {'user': {'account_id': 'author', 'display_name': 'Autor'}}
            comments = [
                self.ai(1, 'f.txt', '2026-10-02T12:00:00+00:00', {**other, 'created_on': '2026-10-03T09:00:00+00:00'}),
                self.ai(2, 'g.txt', '2026-10-02T12:00:00+00:00'),
                self.ai(3, 'f.txt', '2026-10-02T11:00:00+00:00', {**other, 'created_on': '2026-10-02T11:30:00+00:00'}),
                self.ai(4, 'f.txt', '2026-10-02T12:00:00+00:00', {'user': {'account_id': 'reviewer'},
                                                                   'created_on': '2026-10-03T09:00:00+00:00'}),
                {'id': 5, 'content': {'raw': 'ok'}, 'parent': {'id': 1}, 'created_on': '2026-10-03T08:00:00+00:00',
                 'user': {'display_name': 'Autor'}}]
            Path(out, 'review_context.json').write_text(json.dumps({
                'source': {'commit': source[:12]}, 'destination': {'commit': destination[:12]},
                'previous_review': {'commit': reviewed[:12], 'reviewed_at': '2026-10-02T12:00:00+00:00'}}))
            Path(out, 'comments.json').write_text(json.dumps(comments))
            with mock.patch.dict(os.environ, {'BITBUCKET_ACCOUNT_ID': 'reviewer'}):
                result = review_delta.delta(Path(out), Path(repo))
        self.assertEqual([(c['subject'], c['files']) for c in result['own_commits']], [('b', ['g.txt'])])
        self.assertEqual([(m['subject'], m['conflict_files']) for m in result['merges']],
                         [('merge main', ['c.txt'])])
        self.assertEqual([(t['id'], t['resolved'], t['replies'], t['path_touched_after']) for t in result['threads']],
                         [(1, True, 1, False), (2, False, 0, True)])

    def test_without_previous_review_covers_the_whole_pr(self):
        with tempfile.TemporaryDirectory() as out:
            Path(out, 'review_context.json').write_text(json.dumps({'previous_review': None}))
            self.assertEqual(review_delta.delta(Path(out), Path(out)), {'previous_review': None})


class PreviousReviewTests(unittest.TestCase):
    ACTIVITY = [
        {'update': {'date': '2026-10-13T19:47:31+00:00', 'source': {'commit': {'hash': 'second'}}}},
        {'comment': {'id': 9}},
        {'update': {'date': '2026-10-13T13:15:54+00:00', 'source': {'commit': {'hash': 'first'}}}},
    ]

    def comment(self, raw, created_on, author='reviewer', **extra):
        return {'content': {'raw': raw}, 'created_on': created_on, 'user': {'account_id': author}, **extra}

    def test_commit_pushed_before_the_latest_ai_comment(self):
        comments = [self.comment('(AI) a', '2026-10-13T14:56:37+00:00'),
                    self.comment('(AI) b', '2026-10-13T20:13:03+00:00'),
                    self.comment('human', '2026-10-13T21:00:00+00:00'),
                    self.comment('(AI) deleted', '2026-10-13T22:00:00+00:00', deleted=True)]
        self.assertEqual(review_context.previous_review(comments, self.ACTIVITY, 'reviewer'),
                         {'commit': 'second', 'reviewed_at': '2026-10-13T20:13:03+00:00'})

    def test_ai_prefix_from_another_account_is_ignored(self):
        comments = [self.comment('(AI) a', '2026-10-13T14:56:37+00:00'),
                    self.comment('(AI) spoof', '2026-10-13T20:13:03+00:00', author='pr-author')]
        self.assertEqual(review_context.previous_review(comments, self.ACTIVITY, 'reviewer')['commit'], 'first')

    def test_none_without_identity_ai_comments_or_activity(self):
        ai = [self.comment('(AI) a', '2026-10-13T20:00:00+00:00')]
        self.assertIsNone(review_context.previous_review(ai, self.ACTIVITY, None))
        self.assertIsNone(review_context.previous_review([self.comment('human', '2026-10-13T20:00:00+00:00')],
                                                         self.ACTIVITY, 'reviewer'))
        self.assertIsNone(review_context.previous_review(ai, [], 'reviewer'))


class ReviewerStateTests(unittest.TestCase):
    PARTICIPANTS = [{'user': {'account_id': 'other'}, 'state': 'approved'},
                    {'user': {'account_id': 'reviewer'}, 'state': 'changes_requested'}]

    def test_state_of_the_reviewer_account(self):
        result = review_context.summarize({'participants': self.PARTICIPANTS}, [], [], [], reviewer='reviewer')
        self.assertEqual(result['reviewer_state'], 'changes_requested')

    def test_none_without_identity_or_participation(self):
        self.assertIsNone(review_context.reviewer_state(self.PARTICIPANTS, None))
        self.assertIsNone(review_context.reviewer_state(self.PARTICIPANTS, 'absent'))
        self.assertIsNone(review_context.reviewer_state(None, 'reviewer'))


class ChangesRequestedByAiTests(unittest.TestCase):
    PARTICIPANTS = [{'user': {'account_id': 'reviewer'}, 'state': 'changes_requested'}]

    def event(self, date, author='reviewer'):
        return {'changes_requested': {'date': date, 'user': {'account_id': author}}}

    def comment(self, raw, created_on, author='reviewer'):
        return {'id': 1, 'content': {'raw': raw}, 'created_on': created_on, 'user': {'account_id': author}}

    def by_ai(self, activity, comments, participants=None):
        return review_context.summarize({'participants': participants or self.PARTICIPANTS}, [], comments, [],
                                        activity, 'reviewer')['changes_requested_by_ai']

    def test_marker_published_with_the_request(self):
        self.assertTrue(self.by_ai([self.event('2026-10-21T20:25:46+00:00')],
                                   [self.comment(review_context.CHANGES_MARK + ' x', '2026-10-21T20:25:48Z')]))

    def test_request_set_by_hand_minutes_after_an_ai_review(self):
        self.assertFalse(self.by_ai([self.event('2026-10-20T17:41:27+00:00')],
                                    [self.comment('(AI) hallazgo', '2026-10-20T17:33:04+00:00')]))

    def test_latest_request_decides(self):
        self.assertFalse(self.by_ai([self.event('2026-10-14T21:56:24+00:00'), self.event('2026-10-20T17:41:27+00:00')],
                                    [self.comment(review_context.CHANGES_MARK, '2026-10-14T21:56:25+00:00')]))

    def test_needs_a_current_request_and_identity(self):
        activity = [self.event('2026-10-21T20:25:46+00:00')]
        comments = [self.comment(review_context.CHANGES_MARK, '2026-10-21T20:25:48+00:00')]
        self.assertFalse(self.by_ai(activity, comments, [{'user': {'account_id': 'reviewer'}, 'state': None}]))
        self.assertFalse(review_context.changes_requested_by_ai(activity, comments, None))

    def test_marker_is_not_a_thread_to_recheck(self):
        comments = [self.comment(review_context.CHANGES_MARK, '2026-10-21T20:25:48+00:00'),
                    {**self.comment(review_context.APPROVAL_MARK, '2026-10-21T20:25:49+00:00'), 'id': 3},
                    {**self.comment('(AI) hallazgo', '2026-10-21T20:25:40+00:00'), 'id': 2}]
        result = review_delta.threads(comments, 'reviewer', [], '2026-10-21T20:00:00+00:00')
        self.assertEqual([t['id'] for t in result], [2])


class ApprovedByAiTests(unittest.TestCase):
    APPROVED = [{'user': {'account_id': 'reviewer'}, 'state': 'approved'}]
    ACTIVITY = [{'approval': {'date': '2026-10-22T15:00:00+00:00', 'user': {'account_id': 'reviewer'}}}]

    def comment(self, raw, created_on='2026-10-22T15:00:02Z'):
        return {'id': 1, 'content': {'raw': raw}, 'created_on': created_on, 'user': {'account_id': 'reviewer'}}

    def by_ai(self, comments, participants=None):
        return review_context.summarize({'participants': participants or self.APPROVED}, [], comments, [],
                                        self.ACTIVITY, 'reviewer')['approved_by_ai']

    def test_any_current_approval_of_the_account_counts(self):
        self.assertTrue(self.by_ai([]))
        self.assertTrue(self.by_ai([self.comment(review_context.CHANGES_MARK)]))
        self.assertTrue(self.by_ai([self.comment(review_context.APPROVAL_MARK, '2026-10-21T15:00:00Z')]))

    def test_needs_a_current_approval(self):
        comments = [self.comment(review_context.APPROVAL_MARK)]
        self.assertFalse(self.by_ai(comments, [{'user': {'account_id': 'reviewer'}, 'state': None}]))


class HistoryTests(unittest.TestCase):
    def test_destination_is_not_assumed_to_be_diff_base(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'pr.json').write_text(json.dumps({'destination': {'commit': {'hash': 'a' * 40}}}))
            with mock.patch.object(history, 'git', return_value=''), \
                 contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(history.resolve_blame_ref('repo', directory), ('HEAD', False))
                self.assertEqual(history.resolve_blame_ref('repo', directory, 'b' * 40), ('b' * 40, True))

    def test_selected_paths_and_no_api_report_truthfully(self):
        diff = ''.join(f'diff --git a/{p} b/{p}\n--- a/{p}\n+++ b/{p}\n@@ -1 +1 @@\n-old\n+new\n' for p in ('a', 'b'))
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'diff.patch').write_text(diff)
            with mock.patch.object(history, 'git', return_value='true'), \
                 mock.patch.object(history, 'file_history', return_value={'prior_prs': [9], 'commits_last_180d': 1, 'last_touched': '2026-09-01'}) as log, \
                 mock.patch.object(history, 'blame', return_value=[]), \
                 mock.patch.object(history, 'prior_comments') as api, \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(history.main(['script', 'ws', 'repo', 'local', directory,
                                               '--paths', 'a', '--no-api']), 0)
                result = json.loads(Path(directory, 'history.json').read_text())
                self.assertEqual(list(result['files']), ['a'])
                self.assertEqual(result['prior_prs_queried'], [])
                log.assert_called_once_with('local', 'a', 40, 'HEAD')
                api.assert_not_called()

    def test_prior_discussion_is_not_silently_truncated(self):
        body = 'x' * 1400 + ' This was resolved.'
        with mock.patch.object(history, 'api_get', return_value={'values': [
            {'id': 3, 'parent': {'id': 2}, 'content': {'raw': body}, 'inline': {'path': 'a', 'to': 1}}
        ]}):
            result = history.prior_comments('ws', 'repo', [9], {'a'}, 'test')
        self.assertEqual(result[0]['body'], body)
        self.assertEqual(result[0]['parent'], 2)


class RemovalAuditTests(unittest.TestCase):
    def test_reports_dangling_and_orphaned_symbols(self):
        with tempfile.TemporaryDirectory() as repo:
            files = {
                'app/Consumer.php': 'new Gadget();\n',
                'app/Fields.php': "class Fields { public const string USER = 'user'; public const string NAME = 'n'; }\n",
                'app/Other.php': 'Fields::NAME;\n',
                'lang/en/login.php': "'messages' => ['reset' => 'Reset'],\n",
            }
            for path, content in files.items():
                Path(repo, path).parent.mkdir(parents=True, exist_ok=True)
                Path(repo, path).write_text(content)
            git = lambda *a: subprocess.run(['git', '-C', repo, *a], check=True, capture_output=True)
            git('init', '-q')
            git('add', '.')
            git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'head')
            patch = ('diff --git a/app/Gadget.php b/app/Gadget.php\n--- a/app/Gadget.php\n+++ /dev/null\n'
                     '@@ -1,3 +0,0 @@\n-class Gadget {\n-    $x = Fields::USER . Fields::NAME;\n'
                     "-    trans('login.messages.reset');\n")
            result = removal_audit.audit(removal_audit.parse_patch(patch), repo, 'HEAD')
        self.assertEqual([d['symbol'] for d in result['dangling']], ['Gadget'])
        self.assertEqual(sorted(o['symbol'] for o in result['orphans']),
                         ['Fields::USER', 'login.messages.reset'])


if __name__ == '__main__':
    unittest.main()
