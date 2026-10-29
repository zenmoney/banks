"""Exact approved-head merge, lost replies and independent state recovery.

Git and state transports are real disposable repositories. GitHub approval and
merge are controlled API fixtures; these tests never merge a hosted bank PR.
"""
import argparse
import copy
import json
from pathlib import Path
import shutil
from unittest.mock import patch

import test_task_runs as fixture
import test_review_feedback as base_fixture
import approved_merge as merge
import github_pr
import state_publication as state

TARGET,HEAD,PATH=fixture.TARGET,fixture.HEAD,fixture.PATH


class MergeFixture(fixture.TaskFixture):
    def setUp(self):
        super().setUp()
        self.review_status='APPROVED';self.merge_status='CLEAN';self.ci_status='SUCCESS';self.draft=False
        self.approval_commit=self.asset
        self.merge_calls=0;self.lose_reply=False;self.perform_merge=True
        self.reviews=[{'id':7,'node_id':'R7','html_url':f'https://github.com/{TARGET}/pull/146#pullrequestreview-7',
            'user':{'login':'reviewer','type':'User'},'state':'APPROVED','commit_id':self.asset,
            'submitted_at':'2020-01-03T00:00:00Z','body':'Approved'}]
        self.before=self.prior();self.write_state(self.before)

    def raw_pr(self):
        value=super().raw_pr()
        if getattr(self,'merged_at',None):value['closed_at']=self.merged_at
        return value

    def api(self,endpoint,*args):
        if endpoint==f'repos/{TARGET}/git/ref/heads/main':
            return {'ref':'refs/heads/main','object':{'type':'commit','sha':self.ref('refs/heads/main')}}
        if endpoint=='graphql' and any('query=' in a and 'mergeStateStatus' in a for a in args):
            return {'data':{'repository':{'pullRequest':{'number':146,'isDraft':self.draft,
                'reviewDecision':self.review_status,'mergeStateStatus':self.merge_status,
                'headRefOid':self.ref(f'refs/heads/{HEAD}'),'baseRefOid':self.ref('refs/heads/main'),'baseRefName':'main',
                'commits':{'nodes':[{'commit':{'oid':self.ref(f'refs/heads/{HEAD}'),
                    'statusCheckRollup':{'state':self.ci_status} if self.ci_status else None}}]}}}}}
        if endpoint=='graphql' and any('query=' in a and 'reviewDecision' in a for a in args):
            return {'data':{'repository':{'pullRequest':{'reviewDecision':self.review_status}}}}
        if endpoint==f'repos/{TARGET}/pulls/146/merge':
            self.merge_calls+=1
            self.assertIn(f'sha={self.asset}',args)
            self.assertNotIn('--admin',args)
            if self.perform_merge:
                base_fixture.git(self.repo,'merge','--no-ff','--no-edit',self.asset)
                self.merge_commit=base_fixture.git(self.repo,'rev-parse','HEAD')
                base_fixture.git(self.repo,'push','-q','origin','HEAD:refs/heads/main')
                self.merged_at=merge._now();self.closed=True
            if self.lose_reply:raise ValueError('reply lost after mutation')
            return {'merged':self.perform_merge,'sha':self.merge_commit,'message':'fixture result'}
        return super().api(endpoint,*args)

    def prepare_merge(self):
        result=merge.prepare(argparse.Namespace(repo=str(self.repo),remote=None,mr=146,base='main',method='merge'))
        self.operation=Path(result['operation']);self.manifest=result['package'];self.merge_key=result['packageHash']
        return result

    def grant(self):
        grant={'schemaVersion':1,'scope':'approved-merge','decision':'authorize','packageHash':self.merge_key,
            'target':TARGET,'base':'main','id':10,'number':146,'headCommit':self.asset,'method':'merge',
            'authorizer':'Fixture owner','statement':'Explicitly merge this approved fixture head and record it',
            'authorizedAt':merge._now()}
        path=self.root/'merge-authority.json';path.write_bytes(state.encoded(grant));return path

    def execute(self):
        return merge.merge(argparse.Namespace(operation=str(self.operation),authorization=str(self.grant())))

    def publish_state(self,result):
        key=result['packageHash']
        grant={'schemaVersion':1,'scope':'state-publication','decision':'authorize','packageHash':key,
            'target':str(self.remote),'ref':state.REF,'ids':[10],'reviewer':'Fixture owner',
            'statement':'Publish the observed fixture merge, no new checks','authorizedAt':merge._now()}
        path=self.root/'state-authority.json';path.write_bytes(state.encoded(grant))
        return state.dispatch(argparse.Namespace(command='state-publish',session=str(self.operation),package=key,
                                                authorization=str(path),run_authorization=None))


class ApprovedMerge(MergeFixture):
    def test_exact_merge_and_state_do_not_need_old_render_files(self):
        self.prepare_merge()
        self.assertNotIn('worktree',json.loads((self.operation/'session.json').read_text()))
        result=self.execute()
        self.assertEqual(result['mergeStatus'],'merged',result)
        self.assertEqual(self.merge_calls,1)
        self.assertEqual(self.publish_state(result)['status'],'published')
        saved,_=state.remote_record(self.remote,self.ref(state.REF),'banks/state/10.json')
        self.assertEqual(saved['proposals'][-1]['status'],'accepted')
        self.assertEqual(saved['lastAttemptAt'],self.before['lastAttemptAt'])
        self.assertEqual(saved['lastVerifiedAt'],self.before['lastVerifiedAt'])
        self.assertEqual(merge.dispatch(argparse.Namespace(command='merge-reconcile',operation=str(self.operation)))['stateStatus'],'synced')
        self.assertEqual(self.merge_calls,1)

    def test_lost_success_reply_is_read_only_reconciled(self):
        self.prepare_merge();self.lose_reply=True
        result=self.execute()
        self.assertEqual(result['mergeStatus'],'merged')
        self.assertEqual(self.execute()['mergeStatus'],'merged')
        self.assertEqual(self.merge_calls,1)

    def test_unknown_nonmerged_result_never_retries_write(self):
        self.prepare_merge();self.perform_merge=False;self.lose_reply=True
        self.assertEqual(self.execute()['status'],'outcome-unknown')
        self.assertEqual(self.execute()['status'],'outcome-unknown')
        self.assertEqual(self.merge_calls,1)

    def test_new_head_invalidates_approval(self):
        self.reviews[0]['commit_id']='f'*40
        with self.assertRaisesRegex(ValueError,'exact current head'):self.prepare_merge()
        self.assertEqual(self.merge_calls,0)

    def test_changes_requested_or_failed_checks_block_merge(self):
        for name,value in [('review_status','CHANGES_REQUESTED'),('ci_status','FAILURE'),('ci_status','PENDING'),
                           ('merge_status','BLOCKED'),('merge_status','BEHIND'),('draft',True)]:
            old=getattr(self,name);setattr(self,name,value)
            with self.subTest(name=name,value=value),self.assertRaisesRegex(ValueError,'readiness'):
                self.prepare_merge()
            setattr(self,name,old)
        self.assertEqual(self.merge_calls,0)

    def test_later_change_request_overrides_earlier_approval(self):
        self.reviews.append({**self.reviews[0],'id':8,'state':'CHANGES_REQUESTED','submitted_at':'2020-01-04T00:00:00Z'})
        with self.assertRaisesRegex(ValueError,'change request'):self.prepare_merge()

    def test_permissions_are_rechecked_immediately_before_merge(self):
        self.prepare_merge();self.permission='read'
        with self.assertRaises(ValueError):self.execute()
        self.assertEqual(self.merge_calls,0)

    def test_asset_permission_is_not_merge_permission(self):
        self.prepare_merge()
        with self.assertRaisesRegex(ValueError,'not configured'):
            merge.merge(argparse.Namespace(operation=str(self.operation),authorization=None))
        self.assertEqual(self.merge_calls,0)

    def test_wrong_authorized_method_or_head_is_rejected(self):
        self.prepare_merge();path=self.grant()
        value=json.loads(path.read_text());value['method']='squash';path.write_bytes(state.encoded(value))
        with self.assertRaisesRegex(ValueError,'authority'):
            merge.merge(argparse.Namespace(operation=str(self.operation),authorization=str(path)))
        self.assertEqual(self.merge_calls,0)

    def test_state_failure_after_merge_is_recoverable_independently(self):
        self.prepare_merge();result=self.execute()
        disposable=self.root/'old-render-session';disposable.mkdir();shutil.rmtree(disposable)
        (self.root/'state-authority.json').unlink(missing_ok=True)
        repeated=merge.dispatch(argparse.Namespace(command='merge-reconcile',operation=str(self.operation)))
        self.assertEqual(self.publish_state(repeated)['status'],'published')
        self.assertEqual(self.merge_calls,1)

    def test_newer_state_is_preserved_after_successful_merge(self):
        self.prepare_merge()
        current=copy.deepcopy(self.before);current['customHistory']={'someone':'newer write'}
        commit=base_fixture.assets._commit(self.repo,self.root,[('banks/state/10.json',state.encoded(current))],
                                         'concurrent state',self.ref(state.REF))
        base_fixture.git(self.repo,'push','-q','origin',f'{commit}:{state.REF}')
        result=self.execute()
        self.assertEqual(result['status'],'merged');self.assertEqual(result['stateStatus'],'conflict')
        self.assertEqual(self.ref(state.REF),commit)

    def test_manual_approved_pr_can_be_recorded_without_fake_verification(self):
        base_fixture.git(self.remote,'update-ref','-d',state.REF)
        self.prepare_merge();result=self.execute();self.publish_state(result)
        saved,_=state.remote_record(self.remote,self.ref(state.REF),'banks/state/10.json')
        self.assertNotIn('lastAttemptAt',saved);self.assertNotIn('lastVerifiedAt',saved)
        self.assertEqual(saved['proposals'][0]['acceptedIconHash'],state.digest(base_fixture.B))

    def test_approved_listing_is_read_only_and_respects_count(self):
        result=merge.list_approved(argparse.Namespace(repo=str(self.repo),remote=None,base='main',limit=1))
        self.assertEqual([r['mr'] for r in result['selected']],[146])
        self.assertEqual(self.merge_calls,0)
        self.assertFalse((self.repo/'.git/bank-icon-maintenance/operations').exists())
