"""One current planner: numeric missing-ID order, bounded eligibility and CLI removal.

Real disposable catalogs/state; only the remote GitHub read boundary is controlled.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from test_task_runs import TaskFixture
from test_review_feedback import A, git
import asset_publication as assets
import github_pr
import state_publication as state
import tasks

CLI = Path(__file__).resolve().parents[1] / 'scripts/session.py'


class TaskSelection(TaskFixture):
    def setUp(self):
        super().setUp()
        self.closed = True

    def add(self, id, *, icon=False, title='Added'):
        folder = f'banks/{title}_{id}-ru'
        self.put(folder + '/info.json', state.encoded({'id': id, 'title': title, 'countryCode': 'RU'}))
        if icon:
            self.put(folder + '/icon.svg', A)

    def records(self, records):
        commit = assets._commit(self.repo, self.root,
            [(f'banks/state/{id}.json', state.encoded(value)) for id, value in records.items()],
            'durable fixture results', None)
        git(self.repo, 'push', '-q', 'origin', f'{commit}:{state.REF}')
        return commit

    def select(self, task='add-missing', limit=2):
        return tasks.plan(argparse.Namespace(repo=str(self.repo), remote=None, base='main', task=task, limit=limit))

    def test_missing_ids_descend_numerically_before_limit_independent_of_attempt_age(self):
        for id in (9, 100, 21, 90):
            self.add(id)
        self.commit()
        self.records({100: {'schemaVersion': 1, 'lastAttemptAt': '2020-01-02T00:00:00Z', 'lastAttemptResult': 'unconfirmed'},
                      90: {'schemaVersion': 1, 'lastAttemptAt': '2020-01-01T00:00:00Z', 'lastAttemptResult': 'error'}})
        self.assertEqual([b['id'] for b in self.select()['selected']], [100, 90])
        self.assertEqual([b['id'] for b in self.select(limit=4)['selected']], [100, 90, 21, 9])

    def test_retry_identity_human_stop_and_open_mr_do_not_consume_selection_limit(self):
        for id in (100, 90, 200, 180, 160, 400):
            self.add(id)
        self.add(300, icon=True)
        self.add(400, title='Duplicate')
        self.commit()
        prior = self.records({200: {'schemaVersion': 1, 'lastAttemptAt': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                                   'lastAttemptResult': 'error'},
                              180: {'schemaVersion': 1, 'lastAttemptAt': '2020-01-01T00:00:00Z', 'lastAttemptResult': 'needs-human'}})
        original = github_pr.open_for_path
        def opened(target, path):
            return [{'number': 77}] if path == 'banks/Added_160-ru/icon.svg' else original(target, path)
        with patch.object(github_pr, 'open_for_path', side_effect=opened):
            result = self.select()
        self.assertEqual([b['id'] for b in result['selected']], [100, 90])
        self.assertTrue({160, 180, 400}.issubset({b['id'] for b in result['stops']}))
        self.assertEqual(git(self.remote, 'rev-parse', state.REF), prior)

    def test_existing_icons_keep_attempt_age_then_ascending_id(self):
        for id in (30, 40):
            self.add(id, icon=True)
        self.commit()
        self.records({10: {'schemaVersion': 1, 'lastAttemptAt': '2020-01-02T00:00:00Z', 'lastAttemptResult': 'unconfirmed'},
                      40: {'schemaVersion': 1, 'lastAttemptAt': '2020-01-01T00:00:00Z', 'lastAttemptResult': 'error'}})
        self.assertEqual([b['id'] for b in self.select('update-existing', 4)['selected']], [20, 30, 40, 10])

    def test_empty_missing_plan_creates_no_maintenance_session(self):
        before = set(self.root.iterdir())
        self.assertEqual(self.select()['selected'], [])
        self.assertEqual(set(self.root.iterdir()), before)

    def test_explicit_task_creates_only_requested_bank_and_continues_without_selection_phase(self):
        self.add(100); self.add(9); self.commit()
        session = self.root / 'current'
        result = tasks.start(argparse.Namespace(repo=str(self.repo), session=str(session), base='main',
            remote=None, task='add-missing', id=9, mr=None))
        self.assertEqual(result['schemaVersion'], 2)
        self.assertEqual(result['selectedIds'], [9])
        for old in ('approvedSelection', 'mode', 'iconSelection', 'localSeed'):
            self.assertNotIn(old, result)
        resumed = subprocess.run([sys.executable, str(CLI), 'continue', '--session', str(session)],
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
        self.assertEqual(json.loads(resumed.stdout)['selectedIds'], [9])

    def test_removed_commands_and_old_session_version_are_rejected_without_writes(self):
        missing = self.root / 'not-created'
        for command in (['start', '--repo', str(self.repo), '--session', str(missing), '--auto-select'],
                        ['continue', '--session', str(missing), '--approve-selection'],
                        ['continue', '--session', str(missing), '--outcome', 'old.json']):
            with self.subTest(command=command):
                response = subprocess.run([sys.executable, str(CLI), *command], capture_output=True, text=True, timeout=30)
                self.assertEqual(response.returncode, 1)
                self.assertIn('invalid arguments', json.loads(response.stdout)['error'])
                self.assertFalse(missing.exists())
        import session as sessions
        current = self.root / 'current'
        value = sessions.create_session(self.repo, current, 20)
        value['schemaVersion'] = 1
        path = current / 'session.json'
        path.write_bytes(state.encoded(value))
        original = path.read_bytes()
        response = subprocess.run([sys.executable, str(CLI), 'continue', '--session', str(current)],
                                  capture_output=True, text=True, timeout=30)
        self.assertEqual(response.returncode, 1)
        self.assertIn('invalid session', json.loads(response.stdout)['error'])
        self.assertEqual(path.read_bytes(), original)

    def test_failed_task_attachment_leaves_a_blocked_workspace_not_an_ungated_start(self):
        # The record has a present icon and therefore cannot be an add-missing task.
        session = self.root / 'failed-task'
        with self.assertRaisesRegex(ValueError, 'without an icon'):
            tasks.start(argparse.Namespace(repo=str(self.repo), session=str(session), base='main',
                remote=None, task='add-missing', id=20, mr=None))
        data = json.loads((session / 'session.json').read_bytes())
        self.assertEqual(data['banks'][0]['point'], 'blocked')
        self.assertIn('task initialization failed', data['banks'][0]['reason'])
        result = subprocess.run([sys.executable, str(CLI), 'continue', '--session', str(session)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['banks'][0]['point'], 'blocked')
