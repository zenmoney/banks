"""Real validated task/asset/state chain, then DELETE the preparation session.

Provider mutations use controlled HTTP fixtures. Real Git commits and the existing
candidate/admission validators run; synthetic frames are not a model/renderer eval.
"""
import argparse
import copy
import json
from pathlib import Path
import shutil
from unittest.mock import patch

import test_task_runs as fixture
import test_review_feedback as base_fixture
import asset_publication as assets
import task_work as work
import task_recovery as recovery
import state_publication as state
import state_results
import session as sessions
import github_pr

TARGET,PATH,HEAD=fixture.TARGET,fixture.PATH,fixture.HEAD


class RecoveryFixture(fixture.TaskFixture):
    def setUp(self):
        super().setUp()
        self.created=None;self.create_calls=0;self.lost_reply=False

    def api(self,endpoint,*args):
        def pages(rows):return [rows] if '--slurp' in args else rows
        if endpoint==f'repos/{TARGET}/pulls':
            fields=dict(a.split('=',1) for a in args if '=' in a)
            if 'POST' in args:
                self.create_calls+=1
                self.created={**self.raw_pr(),'number':147,'node_id':'NEW147',
                    'html_url':f'https://github.com/{TARGET}/pull/147','state':'open','body':fields['body'],
                    'title':fields['title'],'merged':False,'merged_at':None,'closed_at':None,'merge_commit_sha':None,
                    'head':{'ref':fields['head'],'sha':self.ref('refs/heads/'+fields['head']),'repo':{'full_name':TARGET}}}
                base_fixture.git(self.remote,'update-ref','refs/pull/147/head',self.created['head']['sha'])
                if self.lost_reply:raise ValueError('lost response after actual fixture creation')
                return copy.deepcopy(self.created)
            rows=[self.raw_pr()]+([self.created] if self.created else [])
            if fields.get('state')=='open':rows=[r for r in rows if r['state']=='open']
            if fields.get('head'):rows=[r for r in rows if r['head']['ref']==fields['head'].split(':',1)[1]]
            return pages(rows)
        if endpoint==f'repos/{TARGET}/pulls/147':return copy.deepcopy(self.created)
        if endpoint==f'repos/{TARGET}/pulls/147/files':return pages([{'filename':PATH,'status':'modified'}])
        return super().api(endpoint,*args)

    def asset_grant(self,prepared):
        p=prepared['package']
        grant={'schemaVersion':1,'scope':'asset-publication','decision':'authorize',
            'packageHash':prepared['packageHash'],'id':10,'target':TARGET,'base':'main','baseCommit':p['baseCommit'],
            'head':p['head'],'assetCommit':p['assetCommit'],'materialRef':p['materials']['ref'],
            'materialCommit':p['materials']['commit'],'reviewer':'Fixture owner',
            'statement':'Explicit fixture authorization for the displayed exact publication','authorizedAt':work.now()}
        file=self.root/'asset-authority.json';file.write_bytes(state.encoded(grant));return file

    def publish_asset(self,root,prepared):
        return assets.publish(argparse.Namespace(package=prepared['packageHash'],authorization=str(self.asset_grant(prepared)),
            run_authorization=None),self.session,root,prepared['package'])

    def publish_state(self,result,session=None):
        path=Path(session or self.session);key=result['packageHash']
        grant={'schemaVersion':1,'scope':'state-publication','decision':'authorize','packageHash':key,
            'target':str(self.remote),'ref':state.REF,'ids':[10],'reviewer':'Fixture owner',
            'statement':'Exact fixture task result','authorizedAt':work.now()}
        file=self.root/'state-authority.json';file.write_bytes(state.encoded(grant))
        return state.dispatch(argparse.Namespace(command='state-publish',session=str(path),package=key,
            authorization=str(file),run_authorization=None))

    def state_package(self):
        return state.dispatch(argparse.Namespace(command='state-prepare',session=str(self.session),ids=[10],
            remote=None,resolution=None))


class TaskRecovery(RecoveryFixture):
    def test_lost_pr_reply_and_deleted_session_recovers_state_without_second_pr(self):
        before=self.prior_rejected();self.start_task(before);self.decide();self.fresh_candidate()
        root,prepared=self.prepare_asset('bank-icon-maintenance/new-10');self.lost_reply=True
        result=self.publish_asset(root,prepared)
        self.assertEqual(result['status'],'mr-confirmed',result)
        intents=list((self.repo/'.git/bank-icon-maintenance/operations').glob('asset-*'))
        self.assertEqual(len(intents),1)
        shutil.rmtree(self.session)
        recovered=recovery.prepare(intents[0])
        self.assertEqual(recovered['package']['records'][0]['after']['proposals'][-1]['mr']['number'],147)
        self.assertEqual(self.publish_state(recovered,intents[0])['status'],'published')
        self.assertEqual(recovery.prepare(intents[0])['status'],'state-synced')
        self.assertEqual(self.create_calls,1)
        saved,_=state.remote_record(self.remote,self.ref(state.REF),'banks/state/10.json')
        self.assertEqual(saved['lastVerifiedAt'],before['lastVerifiedAt'])
        self.assertEqual(saved['taskResults'][-1]['outcome'],'published')
        self.assertFalse(self.session.exists())

    def test_open_rework_publishes_once_then_recovers_after_session_deletion(self):
        self.start_task(self.prior(),'rework-changes');self.decide();self.fresh_candidate()
        root,prepared=self.prepare_asset(HEAD)
        result=self.publish_asset(root,prepared)
        self.assertEqual(result['status'],'mr-confirmed',result)
        intent=next((self.repo/'.git/bank-icon-maintenance/operations').glob('asset-*'))
        shutil.rmtree(self.session)
        # GitHub updates refs/pull/N/head when the PR branch advances. The bare
        # repository fixture needs the corresponding provider-side ref update.
        base_fixture.git(self.remote,'update-ref','refs/pull/146/head',self.ref('refs/heads/'+HEAD))
        recovered=recovery.prepare(intent)
        self.assertEqual(self.publish_state(recovered,intent)['status'],'published')
        self.assertEqual(self.comment_calls,1);self.assertEqual(self.create_calls,0)
        self.assertEqual(self.body,prepared['package']['body'])

    def test_raw_pending_then_final_result_advance_only_own_confirmed_stages(self):
        self.start_task(self.prior_rejected());self.decide();self.fresh_candidate()
        self.assertEqual(self.publish_state(self.state_package())['status'],'published')
        root,prepared=self.prepare_asset('bank-icon-maintenance/new-10')
        self.assertEqual(self.publish_state(self.state_package())['status'],'published')
        self.assertEqual(self.publish_asset(root,prepared)['status'],'mr-confirmed')
        final=self.state_package()
        self.assertEqual(self.publish_state(final)['status'],'published')
        self.assertEqual(self.create_calls,1)

    def test_late_feedback_edit_prevents_the_first_external_write(self):
        self.start_task(self.prior_rejected());self.decide();self.fresh_candidate()
        root,prepared=self.prepare_asset('bank-icon-maintenance/new-10')
        self.comments[0]['body']='Changed requirements after package preparation'
        with self.assertRaisesRegex(ValueError,'discussion changed'):
            self.publish_asset(root,prepared)
        self.assertIsNone(assets._ref(self.repo,TARGET,prepared['package']['headRef']))
        self.assertEqual(self.create_calls,0)

    def test_partial_refs_after_lost_session_are_not_permission_to_create_a_duplicate(self):
        self.start_task(self.prior_rejected());self.decide();self.fresh_candidate()
        root,prepared=self.prepare_asset('bank-icon-maintenance/new-10')
        intent=recovery.retain(self.session,prepared['package'])
        p=prepared['package']
        base_fixture.git(root/'objects.git','push','-q',str(self.remote),f"{p['assetCommit']}:{p['headRef']}")
        shutil.rmtree(self.session)
        with self.assertRaisesRegex(ValueError,'partial/ambiguous'):
            recovery.prepare(intent)
        self.assertEqual(self.create_calls,0)

    def test_missing_icon_task_uses_null_baseline_without_entity_creation(self):
        (self.repo/PATH).unlink();self.base=self.commit()
        base_fixture.git(self.repo,'push','-q','origin','HEAD:main')
        self.closed=True
        self.start_task({'schemaVersion':1},'add-missing')
        self.assertIsNone(self.selected['iconHash'])
        self.assertEqual(self.selected['point'],'identity-evidence')
        self.assertEqual(self.selected['metadataHash'],self.started['banks'][0]['metadataHash'])

    def test_legacy_orphan_candidate_can_be_replaced_after_no_output_audit(self):
        self.closed=True
        before={'schemaVersion':1,'lastAttemptAt':'2020-01-01T00:00:00Z','lastAttemptResult':'candidate',
            'proposals':[{'status':'pending','baseIconHash':self.bank['iconHash'],'candidateIconHash':'a'*64,
                          'candidate':{'path':'deleted-session/raw.svg','iconHash':'a'*64}}]}
        self.start_task(before,'update-existing')
        self.assertEqual(self.selected['point'],'identity-evidence')
        ctx=work.context(self.session,self.data,self.selected)
        effective=work.observed_state(ctx,before)
        self.assertEqual(effective['retiredLocalCandidates'][0]['proposal'],before['proposals'][0])
        self.assertEqual(effective['proposals'],[])
        self.assertIn('orphanAudit',ctx)

    def test_second_rework_uses_published_history_after_deleting_real_first_session(self):
        self.start_task(self.prior_rejected());self.decide();self.fresh_candidate()
        root,prepared=self.prepare_asset('bank-icon-maintenance/new-10')
        self.publish_asset(root,prepared)
        self.publish_state(self.state_package())
        old=self.session;shutil.rmtree(old)
        self.created.update(state='closed',closed_at=work.now())
        comment={'id':2,'node_id':'COMMENT147','html_url':f'https://github.com/{TARGET}/pull/147#issuecomment-2',
                 'body':'Исправить новый вариант','user':{'login':'reviewer','type':'User'}}
        self.comments=[comment];self.review_text=comment['body']
        self.nodes['COMMENT147']={'__typename':'IssueComment','id':'COMMENT147','url':comment['html_url'],
            'body':comment['body'],'createdAt':work.now(),'lastEditedAt':None,'author':self.editor,'editor':None}
        real=self.api
        def current_api(endpoint,*args):
            if endpoint==f'repos/{TARGET}/issues/147/comments':return [comment]
            if endpoint in (f'repos/{TARGET}/pulls/147/reviews',f'repos/{TARGET}/pulls/147/comments'):return []
            return real(endpoint,*args)
        with patch.object(github_pr,'_api',side_effect=current_api):
            self.session=self.root/'second-independent-run'
            from tasks import start
            result=start(argparse.Namespace(repo=str(self.repo),session=str(self.session),base='main',remote=None,
                                            task='rework-rejected',id=10,mr=147))
            self.data=json.loads((self.session/'session.json').read_text());self.selected=self.data['banks'][0]
            self.decide();self.fresh_candidate()
            _,records,_=state_results.validate_results(self.session,[10])
        self.assertFalse(old.exists())
        self.assertEqual(records[0]['after']['proposals'][-1]['supersedesMR'],self.created['html_url'])
        self.assertEqual(records[0]['after']['proposals'][-2]['status'],'rejected')
        self.assertEqual(self.create_calls,1)
        self.assertNotIn('localSeed',self.data)
