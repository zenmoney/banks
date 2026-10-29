"""Operator budgets through real accounting/CLI; no live searches or bank changes."""
import argparse
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import research
import session as sessions
import workflow
import environment
import test_task_runs as tasks_fixture


class PerBankBudget(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.session = Path(tmp.name).resolve()
        self.base = 'a' * 40
        clean = {k: v for k, v in os.environ.items() if k not in research.ENVIRONMENT.values()}
        self.env = patch.dict(os.environ, clean, clear=True)
        self.env.start(); self.addCleanup(self.env.stop)

    def reserve(self, id=10, kind='search', request='Find original', **kwargs):
        return research.reserve(self.session, self.base, id, kind, request, 'Resolve original vector', **kwargs)

    def test_single_defaults_and_sources(self):
        value = research.configure(self.session, self.base, ids=[10])
        self.assertEqual(value['policy']['schemaVersion'], 2)
        self.assertEqual(value['policy']['sources'], dict.fromkeys(research.KINDS, 'default'))
        self.assertNotIn('run', value['policy']['limits'])
        self.assertEqual(value['banks']['10']['remaining'], research.DEFAULTS)

    def test_partial_env_override_reports_exact_origin(self):
        os.environ['BANK_ICON_RESEARCH_FETCH_LIMIT'] = '7'
        value = research.configure(self.session, self.base, ids=[10])
        self.assertEqual(value['policy']['limits']['bank']['fetch'], 7)
        self.assertEqual(value['policy']['sources']['fetch'], 'BANK_ICON_RESEARCH_FETCH_LIMIT')
        self.assertEqual(value['policy']['sources']['search'], 'default')

    def test_invalid_env_is_not_silently_replaced(self):
        for name in research.ENVIRONMENT.values():
            for raw in ('', ' ', '1.5', '-1', '+1', '1e2', 'true', '1001', '2\n', '1_0', '０'):
                with self.subTest(name=name, raw=raw), patch.dict(os.environ, {name: raw}):
                    with self.assertRaisesRegex(ValueError, name):
                        research.settings()
        self.assertFalse((self.session / 'research').exists())

    def test_boundary_values_zero_and_1000(self):
        for raw in ('0', '1000'):
            with patch.dict(os.environ, dict.fromkeys(research.ENVIRONMENT.values(), raw)):
                self.assertEqual(set(research.settings()['limits']['bank'].values()), {int(raw)})

    def test_frozen_attempt_ignores_later_env_even_invalid_values(self):
        os.environ['BANK_ICON_RESEARCH_SEARCH_LIMIT'] = '2'
        original = research.configure(self.session, self.base, ids=[10])
        self.reserve()
        os.environ['BANK_ICON_RESEARCH_SEARCH_LIMIT'] = 'invalid'
        resumed = research.configure(self.session, self.base, ids=[10])
        self.assertEqual(resumed['policySha256'], original['policySha256'])
        self.assertEqual(resumed['banks']['10']['remaining']['search'], 1)
        self.assertEqual(research.describe(self.session, self.base, [10]), resumed)
        (self.session / 'new-attempt').mkdir()
        with self.assertRaises(ValueError):
            research.configure(self.session / 'new-attempt', self.base)

    def test_independent_banks_have_no_hidden_run_limit(self):
        os.environ['BANK_ICON_RESEARCH_SEARCH_LIMIT'] = '2'
        for id in range(10, 20):
            for request in ('one', 'two'):
                result = self.reserve(id, request=request)
                self.assertEqual(result['status'], 'research-reserved')
                self.assertNotIn('run', result['remaining'])
            self.assertEqual(self.reserve(id, request='three')['status'], 'research-budget-exhausted')
        self.assertEqual(research.configure(self.session, self.base)['used']['search'], 20)

    def test_one_exhausted_kind_does_not_prevent_other_actions_or_finish(self):
        os.environ['BANK_ICON_RESEARCH_SEARCH_LIMIT'] = '1'
        first = self.reserve()
        self.assertEqual(self.reserve(request='another')['status'], 'research-budget-exhausted')
        research.finish(self.session, self.base, first['ticket'], {'status': 'failed'}, expected_id=10)
        self.assertEqual(self.reserve(kind='fetch', request='https://example.test/a.svg')['status'], 'research-reserved')
        self.assertEqual(self.reserve(kind='browser', request='page')['status'], 'research-reserved')
        self.assertEqual(self.reserve(11)['status'], 'research-reserved')

    def test_duplicate_reuses_result_and_explicit_retry_spends(self):
        os.environ['BANK_ICON_RESEARCH_SEARCH_LIMIT'] = '2'
        first = self.reserve()
        research.finish(self.session, self.base, first['ticket'], {'status': 'found'})
        self.assertEqual(self.reserve(request=' FIND   ORIGINAL ')['status'], 'research-duplicate')
        self.assertEqual(self.reserve(retry_reason='New official release')['remaining']['bank'], 0)
        self.assertEqual(self.reserve(retry_reason='Source changed again')['status'], 'research-budget-exhausted')


    def test_zero_is_disabled_not_unlimited(self):
        os.environ['BANK_ICON_RESEARCH_SEARCH_LIMIT'] = '0'
        self.assertEqual(self.reserve()['status'], 'research-budget-exhausted')
        self.assertEqual(research.configure(self.session, self.base)['used']['search'], 0)

    def test_source_and_limit_mutation_do_not_erase_existing_expenses(self):
        self.reserve()
        path = self.session / 'research/limits.json'
        original = path.read_bytes()
        value = json.loads(original); value['limits']['bank']['search'] = 1000
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.reserve(request='again')
        path.write_bytes(original)
        self.assertEqual(research.configure(self.session, self.base)['used']['search'], 1)

    def test_read_only_preview_does_not_initialize_or_reserve(self):
        value = research.describe(self.session, self.base)
        self.assertFalse(value['frozen'])
        self.assertFalse((self.session / 'research').exists())

    def test_wrong_base_and_symlink_still_fail_closed(self):
        research.configure(self.session, self.base)
        with self.assertRaises(ValueError):
            research.configure(self.session, 'b' * 40)
        path = self.session / 'research/limits.json'
        real = self.session / 'saved.json'; real.write_bytes(path.read_bytes())
        path.unlink(); path.symlink_to(real)
        with self.assertRaises(ValueError):
            research.configure(self.session, self.base)

    def test_lock_blocks_another_worker_without_reserving(self):
        with research._lock(self.session):
            with self.assertRaisesRegex(ValueError, 'in progress'):
                self.reserve()
        self.assertEqual(self.reserve()['status'], 'research-reserved')

    

class StartAndDoctor(tasks_fixture.TaskFixture):
    def test_task_start_freezes_env_before_the_first_work_order(self):
        with patch.dict(os.environ, {'BANK_ICON_RESEARCH_SEARCH_LIMIT': '2'}):
            self.start_task(self.prior_rejected())
        self.assertEqual(self.data['researchBudget']['banks']['10']['remaining']['search'], 2)
        self.decide()
        with patch.dict(os.environ, {'BANK_ICON_RESEARCH_SEARCH_LIMIT': '999'}):
            first = workflow.work_order(self.session, 10)
            order = json.loads((self.session / first['workOrder']['path']).read_bytes())
            self.assertEqual(order['remainingSearchBudget'], 2)
            research.reserve(self.session, self.base, 10, 'search', 'one', 'Source needed')
            second = workflow.work_order(self.session, 10)
            later = json.loads((self.session / second['workOrder']['path']).read_bytes())
            self.assertEqual(later['remainingSearchBudget'], 1)
            self.assertEqual(later['researchBudget']['policySha256'], order['researchBudget']['policySha256'])

    def test_doctor_existing_session_reads_frozen_budget_without_writes(self):
        with patch.dict(os.environ, {'BANK_ICON_RESEARCH_SEARCH_LIMIT': '3'}):
            self.start_task(self.prior_rejected())
        before = {p: p.read_bytes() for p in (self.session / 'research').iterdir() if p.is_file()}
        args = argparse.Namespace(repo=None, session=str(self.session), base='HEAD', render=False, catalog=None, github=False)
        with patch.dict(os.environ, {'BANK_ICON_RESEARCH_SEARCH_LIMIT': 'bad'}):
            result = environment.doctor(args)
        self.assertEqual(result['researchBudget']['banks']['10']['remaining']['search'], 3)
        self.assertTrue(result['researchBudget']['frozen'])
        self.assertTrue(all(p.read_bytes() == content for p, content in before.items()))

    def test_doctor_malformed_session_is_structured_and_read_only(self):
        path = self.root / 'malformed-session'
        path.mkdir()
        saved = path / 'session.json'
        saved.write_text('{}')
        args = argparse.Namespace(repo=None, session=str(path), base='HEAD', render=False,
                                  catalog=None, github=False)
        result = environment.doctor(args)
        self.assertEqual(result['status'], 'blocked')
        self.assertIsNone(result['researchBudget'])
        self.assertEqual(saved.read_text(), '{}')
        self.assertFalse((path / 'research').exists())

    def test_invalid_start_configuration_creates_no_task_or_worktree(self):
        target = self.root / 'must-not-exist'
        args = argparse.Namespace(repo=str(self.repo), session=str(target), base='main', ids=['10'],
                                  auto_select=True, icons='all', now=None, remote=None, offline=False)
        with patch.dict(os.environ, {'BANK_ICON_RESEARCH_BROWSER_LIMIT': '-1'}):
            with self.assertRaisesRegex(ValueError, 'BANK_ICON_RESEARCH_BROWSER_LIMIT'):
                sessions.create_session(args.repo, args.session, 10, base=args.base, remote=args.remote)
        self.assertFalse(target.exists())

    def test_old_budget_version_is_rejected_without_rewriting_expenses(self):
        self.start_task(self.prior_rejected())
        path = self.session / 'research/limits.json'
        limits = {'run': dict.fromkeys(research.KINDS, 1), 'bank': dict.fromkeys(research.KINDS, 2)}
        path.write_text(json.dumps({'schemaVersion': 1, 'baseCommit': self.base, 'limits': limits}) + '\n')
        original = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'unsupported research budget version'):
            research.reserve(self.session, self.base, 10, 'search', 'one', 'source needed')
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list((self.session / 'research').glob('event-*.json')), [])

    def test_new_cli_override_flags_fail_without_resetting_the_budget(self):
        self.start_task(self.prior_rejected()); self.decide()
        path = self.session / 'research/limits.json'
        original = path.read_bytes()
        limits = self.session / 'limits-input.json'
        limits.write_text(json.dumps({'bank': dict(research.DEFAULTS)}))
        for argv in (['research-budget', '--session', str(self.session), '--limits', str(limits)],
                     ['work-order', '--session', str(self.session), '--id', '10', '--search-budget', '1']):
            with self.subTest(command=argv[0]):
                out = io.StringIO()
                with redirect_stdout(out):
                    code = workflow.main(argv)
                self.assertEqual(code, 1)
                self.assertIn('unrecognized arguments', json.loads(out.getvalue())['reason'])
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(list((self.session / 'research').glob('event-*.json')), [])
