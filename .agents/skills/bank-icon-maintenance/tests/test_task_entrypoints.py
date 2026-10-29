"""Reported PR69 entry-point regressions, with real argparse and disposable Git.

Only provider/remote boundaries are fixtures. No CLI guard, decision validator,
state writer or task dispatcher is replaced. No production or model-image runs.
"""
from contextlib import redirect_stdout
import argparse
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch

import test_task_recovery as fixture
import test_approved_merge as merge_fixture
import task_work as work
import tasks
import environment
import feedback_intake as intake
import session as sessions
import state_publication as state
import workflow_navigation as nav

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'scripts/session.py'


def invoke(*arguments):
    output = io.StringIO()
    with patch.object(sys, 'argv', [str(CLI), *map(str, arguments)]), redirect_stdout(output):
        code = sessions.main()
    return code, json.loads(output.getvalue())


class SessionlessCLI(unittest.TestCase):
    def test_sessionless_commands_fail_structurally_without_traceback(self):
        with tempfile.TemporaryDirectory() as name:
            missing = str(Path(name) / 'absent')
            commands = [
                ['task-list', '--repo', missing, '--task', 'rework-changes'],
                ['merge-list', '--repo', missing],
                ['merge-prepare', '--repo', missing, '--mr', '1', '--method', 'squash'],
                ['task-recover', '--operation', missing],
                ['merge-publish', '--operation', missing],
                ['merge-reconcile', '--operation', missing],
            ]
            for args in commands:
                with self.subTest(command=args[0]):
                    result = subprocess.run([sys.executable, str(CLI), *args], cwd=name,
                        capture_output=True, text=True, timeout=20)
                    self.assertEqual(result.returncode, 1)
                    self.assertNotIn('Traceback', result.stderr)
                    self.assertTrue(json.loads(result.stdout)['error'])
                    self.assertFalse(Path(missing).exists())

    def test_missing_optional_session_is_not_invented(self):
        for args in (argparse.Namespace(operation='/not/a/session'), argparse.Namespace(session=None)):
            before = vars(args).copy()
            environment.canonicalize_session_arguments(args)
            self.assertEqual(vars(args), before)

    def test_wrapper_explains_start_before_looking_for_nonexistent_session(self):
        with tempfile.TemporaryDirectory() as name:
            missing = str(Path(name) / 'not-yet-created')
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/workflow.py'),
                'run', '--session', missing, '--', 'task-start', '--repo', name,
                '--task', 'rework-changes', '--id', '10', '--mr', '146'],
                capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 1)
            reason = json.loads(result.stdout)['reason']
            self.assertIn('session.py', reason)
            self.assertIn('existing session', reason)
            self.assertFalse(Path(missing).exists())


class ReviewBodyCLI(fixture.RecoveryFixture):
    def body_fixture(self, raw, kind='review'):
        self.review_text = intake.clean_body(raw)
        self.comments = []; self.reviews = []; self.inline = []
        url = f'https://github.com/{fixture.TARGET}/pull/146'
        node_id = 'SOURCE1'
        if kind == 'body':
            self.body = raw
            node_id = 'PR_146'
        else:
            anchor = {'review': 'pullrequestreview-', 'comment': 'issuecomment-', 'inline': 'discussion_r'}[kind]
            url += '#' + anchor + '1'
            row = {'id': 1, 'node_id': node_id, 'html_url': url, 'body': raw,
                   'user': {'login': 'reviewer', 'type': 'User'}, 'state': 'CHANGES_REQUESTED'}
            {'review': self.reviews, 'comment': self.comments, 'inline': self.inline}[kind].append(row)
            self.nodes[node_id] = {'__typename': intake._TYPES[kind], 'id': node_id, 'url': url,
                'body': raw, 'createdAt': '2020-01-03T00:00:00Z', 'lastEditedAt': None,
                'author': self.editor, 'editor': None}
        return {'action': 'repair', 'requirements': [{'url': url, 'quote': self.review_text}],
                'rationale': 'Explicit fixture requirement, not a claim of rendered or published work.'}

    def start_cli(self, before, name='session', *, seed=True):
        if seed:
            self.write_state(before)
        self.session = self.root / name
        code, result = invoke('task-start', '--repo', self.repo, '--session', self.session,
                              '--base', 'main', '--task', 'rework-changes', '--id', '10', '--mr', '146')
        self.assertEqual(code, 0, result)
        return result

    def review_cli(self, notes):
        path = self.session / 'evidence/review-notes.json'
        path.write_bytes(state.encoded(notes))
        code, result = invoke('task-review', '--session', self.session, '--id', '10', '--notes', path)
        self.data = json.loads((self.session / 'session.json').read_text())
        self.selected = self.data['banks'][0]
        return code, result

    def test_task_list_reaches_real_discovery_through_main(self):
        self.write_state(self.prior())
        code, result = invoke('task-list', '--repo', self.repo, '--base', 'main',
                              '--task', 'rework-changes')
        self.assertEqual(code, 0, result)
        self.assertEqual(result['status'], 'task-plan')
        self.assertEqual([(r['id'], r['mr']) for r in result['selected']], [(10, 146)])
        self.assertEqual(result['stops'], [])

    def test_cib_keep_current_with_trailing_space_publishes_state_not_new_verification(self):
        raw = 'Оставляем предыдущий вариант: синяя арка лучше читается в тёмной теме. '
        notes = self.body_fixture(raw); notes['action'] = 'keep-current'
        before = self.prior(); self.start_cli(before)
        code, result = self.review_cli(notes)
        self.assertEqual(code, 0, result)
        source = result['decision']['sources'][0]
        self.assertEqual(source['body'], raw)
        self.assertEqual(source['bodySha256'], state.digest(raw.encode()))
        self.assertEqual(source['text'], raw.strip())
        code, package = invoke('state-prepare', '--session', self.session, '--ids', '10')
        self.assertEqual(code, 0, package)
        self.assertEqual(self.publish_state(package)['status'], 'published')
        code, repeat = invoke('state-prepare', '--session', self.session, '--ids', '10')
        self.assertEqual(code, 0, repeat)
        self.assertEqual(repeat['status'], 'no-state-changes')
        after, _ = state.remote_record(self.remote, self.ref(state.REF), 'banks/state/10.json')
        for field in ('lastAttemptAt', 'lastVerifiedAt', 'verifiedIconHash', 'proposals'):
            self.assertEqual(after[field], before[field])
        self.assertEqual(after['taskResults'][0]['outcome'], 'keep-current')
        self.assertEqual(self.comment_calls, 0)
        self.assertFalse((self.session / 'reports').exists())
        # A completed decision cannot consume another slot in the next pass.
        code, plan = invoke('task-list', '--repo', self.repo, '--base', 'main',
                            '--task', 'rework-changes')
        self.assertEqual(code, 0, plan)
        self.assertEqual(plan['selected'], [])

    def test_uzum_repair_keeps_full_multiline_requirement_and_raw_body(self):
        raw = 'Нужен квадрат, как в сторе: S-плашка обрезается.\r\nУвеличить U и сохранить обе заливки. \r\n'
        notes = self.body_fixture(raw); self.start_cli(self.prior())
        code, result = self.review_cli(notes)
        self.assertEqual(code, 0, result)
        self.assertEqual(result['status'], 'identity-evidence')
        self.assertEqual(result['decision']['requirements'], notes['requirements'])
        self.assertEqual(result['decision']['sources'][0]['body'], raw)
        self.assertEqual(nav.next_step(self.session, 10)['stage'], 'research')
        self.assertEqual(self.ref(state.REF), self.data['stateCommit'])

    def test_all_message_kinds_keep_raw_evidence_and_normalized_task_text(self):
        for index, kind in enumerate(('comment', 'inline', 'body')):
            with self.subTest(kind=kind):
                marker = 'a' * 64
                raw = (' \nИсправить обрезание и размер знака.\t\n'
                       f'<!-- bank-icon-feedback:{marker}:begin -->generated comparison'
                       f'<!-- bank-icon-feedback:{marker}:end --> \n')
                notes = self.body_fixture(raw, kind)
                before = self.prior(); self.start_cli(before, f'session-{index}', seed=index == 0)
                code, result = self.review_cli(notes)
                self.assertEqual(code, 0, result)
                self.assertEqual(result['decision']['sources'][0]['body'], raw)
                work.decision(self.session, self.data, self.selected, live=True)

    def test_raw_source_changes_after_binding_still_block_even_for_whitespace(self):
        notes = self.body_fixture('Исправить обрезание. '); self.start_cli(self.prior())
        code, result = self.review_cli(notes); self.assertEqual(code, 0, result)
        self.nodes['SOURCE1']['body'] += ' '
        with self.assertRaisesRegex(ValueError, 'source/editor/permission changed'):
            work.decision(self.session, self.data, self.selected, live=True)

    def test_raw_and_normalized_hash_tampering_are_both_rejected(self):
        notes = self.body_fixture('Исправить обрезание. '); self.start_cli(self.prior())
        code, result = self.review_cli(notes); self.assertEqual(code, 0, result)
        original = result['decision']
        for field in ('bodySha256', 'textSha256', 'text'):
            with self.subTest(field=field):
                altered = copy.deepcopy(original)
                altered['sources'][0][field] = 'b' * 64
                path = self.session / f'evidence/10/forged-{field}.json'
                path.write_bytes(state.encoded(altered))
                from source_evidence import link
                bank = {**self.selected, 'taskDecision': link(self.session, path)}
                with self.assertRaisesRegex(ValueError, 'source'):
                    work.decision(self.session, self.data, bank)

    def test_changed_meaning_or_revoked_editor_cannot_record_decision(self):
        notes = self.body_fixture('Исправить обрезание. '); self.start_cli(self.prior())
        original = (self.session / 'session.json').read_bytes()
        self.permission = 'read'
        code, result = self.review_cli(notes)
        self.assertEqual(code, 1); self.assertIn('write/maintain/admin', result['error'])
        self.assertEqual((self.session / 'session.json').read_bytes(), original)
        self.permission = 'write'
        self.reviews[0]['body'] = self.nodes['SOURCE1']['body'] = 'Оставить старый знак.'
        code, result = self.review_cli(notes)
        self.assertEqual(code, 1); self.assertIn('discussion changed', result['error'])
        self.assertEqual((self.session / 'session.json').read_bytes(), original)

    def test_saved_pre_fix_context_retries_without_rewriting_old_evidence(self):
        notes = self.body_fixture('Исправить обрезание. '); self.start_cli(self.prior())
        # task-start already persisted cleaned text before the fix; keep that snapshot.
        before = {p: p.read_bytes() for p in (self.session / 'evidence').rglob('*') if p.is_file()}
        code, result = self.review_cli(notes)
        self.assertEqual(code, 0, result)
        self.assertTrue(all(p.read_bytes() == content for p, content in before.items()))


    def test_task_recover_uses_real_sessionless_cli_after_deleting_session(self):
        self.start_task(self.prior_rejected()); self.decide(); self.fresh_candidate()
        root, prepared = self.prepare_asset('bank-icon-maintenance/new-10')
        self.assertEqual(self.publish_asset(root, prepared)['status'], 'mr-confirmed')
        operations = list((self.repo / '.git/bank-icon-maintenance/operations').glob('asset-*'))
        self.assertEqual(len(operations), 1)
        shutil.rmtree(self.session)
        code, recovered = invoke('task-recover', '--operation', operations[0])
        self.assertEqual(code, 0, recovered)
        self.assertEqual(self.publish_state(recovered, operations[0])['status'], 'published')
        self.assertEqual(self.create_calls, 1)
        self.assertFalse(self.session.exists())


class MergeEntryCLI(merge_fixture.MergeFixture):
    def test_list_prepare_publish_and_reconcile_use_sessionless_main(self):
        code, listed = invoke('merge-list', '--repo', self.repo, '--limit', '1')
        self.assertEqual(code, 0, listed)
        self.assertEqual([r['mr'] for r in listed['selected']], [146])
        code, prepared = invoke('merge-prepare', '--repo', self.repo, '--mr', '146', '--method', 'merge')
        self.assertEqual(code, 0, prepared)
        self.operation = Path(prepared['operation'])
        self.manifest = prepared['package']; self.merge_key = prepared['packageHash']
        self.lose_reply = True
        code, merged = invoke('merge-publish', '--operation', self.operation, '--authorization', self.grant())
        self.assertEqual(code, 0, merged)
        self.assertEqual(merged['mergeStatus'], 'merged')
        self.assertEqual(self.publish_state(merged)['status'], 'published')
        code, settled = invoke('merge-reconcile', '--operation', self.operation)
        self.assertEqual(code, 0, settled)
        self.assertEqual(settled['stateStatus'], 'synced')
        self.assertEqual(self.merge_calls, 1)


class TaskNoopGuards(unittest.TestCase):
    def test_noop_requires_own_confirmed_publication_and_exact_remote_state(self):
        after = {'schemaVersion': 1, 'taskResults': [{'key': 'a' * 64,
            'task': 'rework-changes', 'outcome': 'keep-current'}]}
        original = {'before': {'schemaVersion': 1}, 'after': after}
        self.assertEqual(work.overlay(original, copy.deepcopy(after), True), after)
        with self.assertRaisesRegex(ValueError, 'another writer'):
            work.overlay(original, copy.deepcopy(after), False)
        changed = {**after, 'foreignField': 'retain me'}
        with self.assertRaises(ValueError):
            work.overlay(original, changed, True)
        self.assertEqual(changed['foreignField'], 'retain me')
