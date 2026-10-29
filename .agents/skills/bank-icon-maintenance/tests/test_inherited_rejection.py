"""Canonical CLI completion of existing rejection stops, without new state writes."""
import copy
import json
import unittest

import test_attempts as attempts
import test_state_check as fixture
from test_attempts import git, put


class InheritedRejection(unittest.TestCase):
    bank = fixture.StateCheckCLI.bank
    commit = fixture.StateCheckCLI.commit
    cli = fixture.StateCheckCLI.cli
    start = fixture.StateCheckCLI.start
    check = fixture.StateCheckCLI.check

    def setUp(self):
        fixture.StateCheckCLI.setUp(self)
        self.record = {'schemaVersion':1, 'lastAttemptAt':'2020-01-01T00:00:00Z',
                       'lastAttemptResult':'candidate', 'proposals':[
            {'status':'rejected', 'mr':{'state':'closed'}, 'rejectionReason':None}]}
        attempts.AttemptCLI.state(self, self.record)
        git(self.repo, 'push', '-q', 'origin', fixture.REF)
        self.old_head = git(self.remote, 'rev-parse', fixture.REF)

    def test_existing_stop_requires_no_fake_attempt_or_permission_question(self):
        started = self.start()
        self.assertEqual(started['banks'][0]['point'], 'needs-human')
        result = self.check(True)
        self.assertEqual(result['banks'][0]['resultKind'], 'inherited-stop-no-new-attempt')
        self.assertEqual(git(self.remote, 'rev-parse', fixture.REF), self.old_head)
        saved = json.loads(git(self.remote, 'show', fixture.REF + ':banks/state/10.json'))
        self.assertEqual(saved, self.record)
        self.assertFalse((self.session / 'reports/10.json').exists())
        self.assertFalse((self.session / 'evidence/10').exists())

    def test_completed_stop_does_not_hide_missing_attempt_in_next_session(self):
        self.start()
        self.check(True)
        self.session = self.root / "second"
        self.start(20)
        result = self.check()
        self.assertEqual([(r['id'], r['complete']) for r in result['banks']], [(20, False)])

    def test_modified_local_state_is_not_a_noop(self):
        self.start()
        path = self.session / 'session.json'
        data = json.loads(path.read_text())
        data['banks'][0]['state']['lastAttemptAt'] = '2026-09-25T00:00:00Z'
        put(path,data)
        self.assertFalse(self.check()['banks'][0]['complete'])

    def test_unreported_local_evidence_is_not_discarded(self):
        self.start()
        put(self.session / 'evidence/10/attempt.json', {'unfinished':'retain me'})
        self.assertFalse(self.check()['banks'][0]['complete'])

    def test_changed_remote_rejection_cannot_be_reported_as_synced(self):
        self.start()
        branch = git(self.repo, 'branch', '--show-current')
        git(self.repo, 'checkout', '-q', 'banks-icon-agent-state')
        changed = copy.deepcopy(self.record)
        changed['proposals'][-1]['rejectionReason'] = 'new human decision not in this session'
        put(self.repo / 'banks/state/10.json', changed)
        self.commit()
        git(self.repo, 'push', '-q', 'origin', fixture.REF)
        git(self.repo, 'checkout', '-q', branch)
        self.assertFalse(self.check()['banks'][0]['complete'])

    def test_no_remote_read_no_success(self):
        self.start()
        self.remote.rename(self.root / 'unavailable.git')
        self.assertFalse(self.check()['banks'][0]['complete'])
