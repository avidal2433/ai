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
        self.assertEqual(result['comments'], [{'id': 1, 'parent': 2, 'inline': None, 'body': body}])
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
