"""Real append-only accounting; tool fixtures do not establish source truth."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import research


class ResearchAccounting(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.session = Path(tmp.name).resolve() / 'session'
        self.session.mkdir()
        self.base = 'a' * 40
        self.limits = {'bank': {'search': 2, 'fetch': 2, 'browser': 1}}
        environment = patch.dict('os.environ', {
            research.ENVIRONMENT[k]: str(v) for k, v in self.limits['bank'].items()})
        environment.start(); self.addCleanup(environment.stop)
        research.configure(self.session, self.base)

    def reserve(self, id=10, kind='search', request='Bank logo', **kwargs):
        return research.reserve(self.session, self.base, id, kind, request,
                                'Resolve original-vector provenance', **kwargs)

    def test_duplicate_queries_do_not_spend_again_or_run_an_adapter(self):
        first = self.reserve()
        research.finish(self.session, self.base, first['ticket'], {'status': 'found', 'count': 1})
        duplicate = self.reserve(request='  BANK   logo ')
        self.assertEqual(duplicate['status'], 'research-duplicate')
        self.assertEqual(duplicate['result']['count'], 1)
        self.assertEqual(research.configure(self.session, self.base)['used']['search'], 1)

    def test_failures_and_unfinished_actions_consume_budget_across_resume(self):
        one = self.reserve()
        research.finish(self.session, self.base, one['ticket'], {'status': 'failed'})
        self.assertEqual(self.reserve(request='Other logo')['status'], 'research-reserved')
        self.assertEqual(self.reserve(request='Third logo')['status'], 'research-budget-exhausted')
        self.assertIsNone(self.reserve(request='Other logo')['result'])
        self.assertEqual(self.reserve(kind='fetch', request='https://bank.example/a.svg')['status'], 'research-reserved')


    def test_retry_needs_changed_input_reason_and_still_spends(self):
        self.reserve()
        self.assertEqual(self.reserve(retry_reason='Official site recovered')['status'], 'research-reserved')
        self.assertEqual(self.reserve(retry_reason='New retry')['status'], 'research-budget-exhausted')

    def test_modified_journal_fails_closed(self):
        self.reserve()
        (self.session / 'research/event-00000.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.reserve(request='New')

    def test_completion_cannot_be_repeated_or_fabricated(self):
        ticket = self.reserve()['ticket']
        research.finish(self.session, self.base, ticket, {'status': 'ok'})
        with self.assertRaisesRegex(ValueError, 'already complete'):
            research.finish(self.session, self.base, ticket, {'status': 'changed'})
        with self.assertRaisesRegex(ValueError, 'not reserved'):
            research.finish(self.session, self.base, 'b' * 32, {})

    def test_symlink_policy_and_bad_limits_rejected(self):
        path = self.session / 'research/limits.json'
        path.unlink(); path.symlink_to(self.session.parent / 'outside')
        with self.assertRaises(ValueError):
            self.reserve()
        for value in (True, -1, 1001):
            limits = copy.deepcopy(self.limits); limits['bank']['search'] = value
            with self.assertRaises(ValueError): research._limits(limits)

    def test_plan_uses_metadata_not_invented_aliases_or_successor(self):
        value = research.plan({'metadata': {'title': 'Example', 'fullTitle': 'Example Bank', 'url': 'https://example.test/'}})
        self.assertEqual(value['names'], ['Example', 'Example Bank'])
        self.assertIn('site:example.test', value['officialQueries'][0])
        self.assertNotIn('ZenPlugins', json.dumps(value))
        self.assertIn('thumbnails', value['stages'][-1]['action'])

    def test_real_adapter_receives_structured_request_without_write_credentials(self):
        adapter = self.session.parent / 'adapter.py'
        adapter.write_text('import json,sys,os\nr=json.load(sys.stdin)\nassert "GITHUB_TOKEN" not in os.environ\nprint(json.dumps({"query":r["request"]}))\n')
        config = self.session.parent / 'adapter.json'
        config.write_text(json.dumps({'argv': [sys.executable, str(adapter)]}))
        with patch.dict('os.environ', {'GITHUB_TOKEN': 'test-write-secret'}):
            result = research.run_adapter(self.session, {'repo': str(self.session/'repo'), 'baseCommit': self.base},
                                          10, 'search', 'Example logo', 'Find original', config)
        self.assertEqual(result['status'], 'research-tool-complete')
        output = json.loads((self.session / result['output']['path']).read_bytes())
        self.assertEqual(output['query'], 'Example logo')
        self.assertFalse((self.session / 'session.json').exists())

    def test_ticket_cannot_be_completed_by_another_bank(self):
        ticket = self.reserve()['ticket']
        with self.assertRaisesRegex(ValueError, 'another bank'):
            research.finish(self.session, self.base, ticket, {}, expected_id=11)
        research.finish(self.session, self.base, ticket, {}, expected_id=10)
