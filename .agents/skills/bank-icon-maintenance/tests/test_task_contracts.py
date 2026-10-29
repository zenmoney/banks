"""Task routing and specific provenance gates, without model-output assertions."""
import unittest
from unittest.mock import patch

import test_task_runs as fixture
import test_feedback_evidence as visual
import test_standing_authorization as policy
import task_work as work
import feedback_evidence
import workflow_navigation as nav
import standing_authorization as standing
import approved_merge


class Tasks(fixture.TaskFixture):
    def test_selected_attachment_adds_full_reference_gate_to_closed_task(self):
        url='https://example.invalid/selected.png'
        self.comments[0]['body']+=f' <img src="{url}"/>'
        self.nodes['COMMENT1']['body']=self.comments[0]['body']
        self.start_task(self.prior_rejected())
        notes={'action':'repair','requirements':[{'url':self.comments[0]['html_url'],'quote':self.review_text}],
               'rationale':'Selected version of the repair','selectedUrl':url}
        work.record_decision(self.session,self.data,self.selected,notes)
        fixture.sessions.save(self.session,self.data)
        self.assertEqual(work.attachment_context(self.session,self.data,self.selected)['request']['selectedUrl'],url)
        self.fresh_candidate()
        with self.assertRaisesRegex(ValueError,'reviewer-attachment'):
            self.prepare_candidate()

    def test_unobserved_attachment_cannot_be_declared_selected(self):
        self.start_task(self.prior_rejected())
        with self.assertRaisesRegex(ValueError,'attachment'):
            work.record_decision(self.session,self.data,self.selected,{'action':'repair',
                'requirements':[{'url':self.comments[0]['html_url'],'quote':self.review_text}],
                'rationale':'Not a source','selectedUrl':'https://example.invalid/fake.png'})

    def test_workflow_routes_reviewer_decision_without_old_session(self):
        self.start_task(self.prior_rejected())
        result=nav.next_step(self.session,10)
        self.assertEqual(result['stage'],'interpret-review')
        self.assertIn('selectedUrl',result['notesTemplate'])
        self.decide()
        result=nav.next_step(self.session,10)
        self.assertEqual(result['stage'],'research')
        self.assertTrue(all('reconsider' not in c['argv'] for c in result['commands']))

    def test_foreign_repository_proposal_cannot_match_by_number_only(self):
        before=self.prior_rejected();before['proposals'][0]['mr']['target']='other/catalog'
        with self.assertRaisesRegex(ValueError,'different repository'):
            self.start_task(before)

    def test_ordinary_task_cannot_release_saved_keep_current(self):
        before=self.prior_rejected()
        with self.assertRaisesRegex(ValueError,'rejection|rejected'):
            self.start_task(before,'update-existing')


class TaskReference(unittest.TestCase):
    put=visual.RequestedReference.put
    save_receipt=visual.RequestedReference.save_receipt
    desktop_bundle=visual.RequestedReference.desktop_bundle
    base_icon=visual.RequestedReference.base_icon
    def setUp(self):
        visual.RequestedReference.setUp(self)
        del self.bank['feedbackContext'];self.bank['taskContext']={'path':'current-task','sha256':'a'*64}
        self.addCleanup(patch.stopall)
        patch.object(work,'attachment_context',return_value=self.ctx).start()

    def test_selected_task_raster_binds_to_exact_final_frames(self):
        feedback_evidence.validate(self.session,self.data,self.bank,self.payload)
        self.payload['reviewerFeedback']['sourceSha256']='b'*64
        with self.assertRaises(ValueError):feedback_evidence.validate(self.session,self.data,self.bank,self.payload)

    def test_selected_task_reference_cannot_be_cropped_or_claimed_without_judgment(self):
        self.payload['reviewerFeedback']['judgement']['sameVariant']=False
        with self.assertRaises(ValueError):feedback_evidence.validate(self.session,self.data,self.bank,self.payload)


class MergePolicy(policy.PolicyFixture, unittest.TestCase):
    def test_old_asset_state_grants_do_not_expand_to_merge(self):
        value,_=standing.load(self.repo,self.session)
        self.assertIsNone(value.get('merge'))
        self.assertIsNot(value['actions'].get('mergeApproved'),True)

    def test_owner_explicit_merge_opt_in_is_scoped_and_revocable(self):
        self.grant['actions']['mergeApproved']=True
        self.grant['mergeMethods']=['squash']
        self.write_grant()
        manifest={'repo':str(self.repo),'target':'fixture/catalog','base':'main','id':4443,
                  'method':'squash','mr':{'number':1,'headCommit':'a'*40}}
        self.assertEqual(approved_merge.authorize(self.session,manifest,'b'*64,None)['action'],'mergeApproved')
        manifest['method']='merge'
        with self.assertRaises(ValueError):approved_merge.authorize(self.session,manifest,'b'*64,None)
        self.grant['actions']['mergeApproved']=False;self.grant.pop('mergeMethods');self.write_grant()
        with self.assertRaises(ValueError):approved_merge.authorize(self.session,manifest,'b'*64,None)



