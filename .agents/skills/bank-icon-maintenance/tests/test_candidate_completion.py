"""Local Git/real validators; provider reads are fixtures, no bank publication."""
import argparse
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from test_task_recovery import RecoveryFixture
from test_task_entrypoints import invoke
import admission
import admission_fixture
import asset_results
import asset_publication as assets
import session as sessions
import source_evidence as source
import state_publication as state
import state_results
import task_completion as completion
import task_work as work
import tasks
import workflow


class CandidateCompletion(RecoveryFixture):
    def stop(self, outcome='error', stage='admission', evidence=None):
        result = completion.finish(argparse.Namespace(session=str(self.session), id=10,
            outcome=outcome, stage=stage, reason='Actual fixture stop: candidate could not finish validation',
            evidence=evidence))
        self.data = json.loads((self.session/'session.json').read_bytes())
        self.selected = self.data['banks'][0]
        return result

    def test_assessed_candidate_finishes_and_publishes_reason_without_active_pending(self):
        before=self.prior_rejected(); self.start_task(before); self.decide(); self.fresh_candidate()
        self.prepare_asset('bank-icon-maintenance/new-10')
        _, _, report, _ = asset_results.load_candidate(self.session, 10)
        notes=admission_fixture.notes_for(report); notes['quality']['fidelity']['status']='fail'
        denied=admission.build(self.session,report,notes)
        old={p:p.read_bytes() for p in (self.session/'evidence').rglob('*') if p.is_file()}
        code, result=invoke('task-finish','--session',self.session,'--id','10',
            '--outcome','unconfirmed','--stage','admission','--reason','Known fidelity check failed',
            '--evidence',denied['admission']['path'])
        self.assertEqual(code,0,result)
        self.data=json.loads((self.session/'session.json').read_bytes());self.selected=self.data['banks'][0]
        self.assertEqual(result['status'],'task-finished')
        after=self.selected['state']
        self.assertEqual(after['lastAttemptResult'],'unconfirmed')
        self.assertFalse(any(p['status']=='pending' for p in after['proposals']))
        self.assertEqual(after['taskResults'][-1]['completion']['stage'],'admission')
        self.assertEqual(after['retiredLocalCandidates'][-1]['proposal']['agentVisualAssessment'],'suitable')
        self.assertEqual(after['lastVerifiedAt'], before['lastVerifiedAt'])
        self.assertTrue(all(p.read_bytes()==content for p,content in old.items()))
        self.assertEqual(self.publish_state(self.state_package())['status'],'published')
        self.assertEqual(self.state_package()['status'],'no-state-changes')
        with self.assertRaises(ValueError): asset_results.load_candidate(self.session,10)
        self.assertEqual((self.create_calls,self.comment_calls),(0,0))

    def test_raw_checkpoint_can_finish_and_advance_only_own_published_stage(self):
        self.start_task(self.prior_rejected()); self.decide(); self.fresh_candidate()
        self.assertEqual(self.publish_state(self.state_package())['status'],'published')
        before=copy.deepcopy(self.selected['state'])
        self.stop(stage='preparation')
        after=self.selected['state']
        self.assertEqual(completion.before_completion(after),before)
        self.assertEqual(self.publish_state(self.state_package())['status'],'published')
        with self.assertRaises(ValueError):work.overlay({'before':{},'after':after},before,False)

    def test_failed_same_mr_repair_restores_open_proposal_and_keeps_stop_history(self):
        before=self.prior()
        # More than one prior revision must survive a failed subsequent repair.
        previous=copy.deepcopy(before['proposals'][0]); previous['reason']='Earlier retained revision'
        before['proposals'][0]['revisionHistory']=[previous]
        self.start_task(before,'rework-changes'); self.decide(); self.fresh_candidate()
        self.assertEqual(self.publish_state(self.state_package())['status'],'published')
        self.stop('needs-human',stage='preparation')
        self.assertEqual(self.selected['state']['proposals'],before['proposals'])
        self.assertEqual(self.publish_state(self.state_package())['status'],'published')
        plan=tasks.plan(argparse.Namespace(repo=str(self.repo),base='main',remote=None,
                                         task='rework-changes',limit=5))
        self.assertEqual(plan['selected'],[])
        self.assertEqual((self.create_calls,self.comment_calls),(0,0))

    def test_partial_or_unknown_publication_cannot_be_retired(self):
        self.start_task(self.prior_rejected()); self.decide(); self.fresh_candidate()
        root,prepared=self.prepare_asset('bank-icon-maintenance/new-10'); p=prepared['package']
        original=(self.session/'session.json').read_bytes()
        receipt=root/f"{prepared['packageHash']}.receipt.json"
        receipt.write_bytes(state.encoded({'status':'outcome-unknown','creationAttempted':True}))
        with self.assertRaisesRegex(ValueError,'reconcile'):self.stop()
        receipt.unlink()
        pointer=self.session/f"task-operation-{prepared['packageHash']}.json"
        pointer.write_text('{}')
        package_file=root/f"{prepared['packageHash']}.json"; saved=package_file.read_bytes();package_file.unlink()
        with self.assertRaisesRegex(ValueError,'lost its package'):self.stop()
        package_file.write_bytes(saved);pointer.unlink()
        state.git(root/'objects.git','push',str(self.remote),f"{p['materials']['commit']}:{p['materials']['ref']}")
        with self.assertRaisesRegex(ValueError,'partial'):self.stop()
        self.assertEqual((self.session/'session.json').read_bytes(),original)

    def test_completion_and_old_evidence_tampering_fail_state_validation(self):
        self.start_task(self.prior_rejected()); self.decide(); self.fresh_candidate()
        self.stop(stage='preparation')
        report=self.session/self.selected['report']
        original=report.read_bytes(); value=json.loads(original); value['after']['verifiedIconHash']='a'*64
        report.write_bytes(state.encoded(value))
        with self.assertRaises(ValueError):state_results.validate_results(self.session,[10])
        report.write_bytes(original)
        original_input=self.session/'evidence/10/validated.json'
        original_input.write_bytes(original_input.read_bytes()+b' ')
        # Original canonical evidence must still be bound by the raw candidate chain.
        # Raw reports compare JSON semantically, so alter an actual field, not whitespace.
        v=json.loads(original_input.read_bytes()); v['reason']='forged'; original_input.write_bytes(state.encoded(v))
        with self.assertRaises(ValueError):state_results.validate_results(self.session,[10])

    def test_error_and_unconfirmed_retry_after_existing_intervals_without_tmp_dependencies(self):
        policy={'retryErrorAfterDays':7,'retryUnconfirmedAfterDays':30,'recheckAfterDays':90}
        now=datetime.now(timezone.utc)
        for outcome,days in (('error',7),('unconfirmed',30),('candidate',7)):
            saved={'schemaVersion':1,'lastAttemptAt':now.isoformat().replace('+00:00','Z'),
                   'lastAttemptResult':outcome, 'proposals':[{'status':'pending'}] if outcome=='candidate' else []}
            self.assertFalse(sessions.check_due(saved,None,now+timedelta(days=days-1),policy))
            self.assertTrue(sessions.check_due(saved,None,now+timedelta(days=days),policy))
        stopped={'lastAttemptAt':now.isoformat().replace('+00:00','Z'),'lastAttemptResult':'error',
                 'lastVerifiedAt':now.isoformat().replace('+00:00','Z'),'verifiedIconHash':'a'*64,
                 'taskResults':[{'processedAt':now.isoformat().replace('+00:00','Z'),'completion':{'stage':'admission'}}]}
        self.assertTrue(sessions.check_due(stopped,'a'*64,now+timedelta(days=7),policy))
        stopped.update(lastAttemptResult='needs-human', proposals=[{'status':'accepted', 'mr':{'state':'merged'}}])
        point, eligible, _ = sessions.state_decision(stopped, 'b'*64, now+timedelta(days=365), policy, False)
        self.assertEqual(point, 'needs-human'); self.assertFalse(eligible)
        self.closed=True; self.start_task({'schemaVersion':1},'update-existing')
        self.fresh_candidate(); self.stop(stage='preparation')
        self.assertEqual(self.publish_state(self.state_package())['status'],'published')
        self.session=self.root/'new-attempt'
        tasks.start(argparse.Namespace(repo=str(self.repo),base='main',remote=None,session=str(self.session),
                                       task='update-existing',id=10,mr=None))
        order=workflow.work_order(self.session,10)
        value=source.read_json(source.linked(self.session,order['workOrder'],'work order'))
        self.assertEqual(value['previousCandidate']['completion']['outcome'],'error')
        self.assertTrue(value['previousCandidate']['sources'])
