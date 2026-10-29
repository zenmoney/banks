"""Portable tasks on disposable Git and controlled provider responses.

Synthetic candidate/renderer judgments exercise contracts, not model image quality.
No production catalog, service state or actual GitHub writes are used.
"""
import argparse
import copy
import json
import shutil
from pathlib import Path
import unittest
from unittest.mock import patch

import test_review_feedback as fixture
import test_candidates as candidates
import admission_fixture
import task_work as work
import tasks
import github_pr
import feedback_intake as intake
import session as sessions
import state_publication as state
import state_results
import candidate
import asset_results
import asset_publication as assets

TARGET,HEAD,PATH=fixture.TARGET,fixture.HEAD,fixture.PATH


class TaskFixture(fixture.FeedbackFixture):
    file=candidates.CandidateCLI.file

    def setUp(self):
        super().setUp()
        self.closed=False
        self.body='Original proposed bank icon'
        self.merged_at=None
        self.merge_commit=None
        self.review_text='Сделать инверсию для темной и светлой темы'
        self.comments=[{'id':1,'node_id':'COMMENT1','html_url':f'https://github.com/{TARGET}/pull/146#issuecomment-1',
            'body':self.review_text,'user':{'login':'reviewer','type':'User'}}]
        self.nodes['COMMENT1']={'__typename':'IssueComment','id':'COMMENT1',
            'url':self.comments[0]['html_url'],'body':self.review_text,'createdAt':'2020-01-03T00:00:00Z',
            'lastEditedAt':None,'author':self.editor,'editor':None}
        patch.object(intake,'repository_from_remote',return_value=TARGET).start()

    def raw_pr(self):
        value=super().raw_pr()
        value.update(state='closed' if getattr(self,'closed',False) else 'open',
            merged=getattr(self,'merged_at',None) is not None,merged_at=getattr(self,'merged_at',None),merge_commit_sha=getattr(self,'merge_commit',None),
            closed_at='2020-01-04T00:00:00Z' if getattr(self,'closed',False) else None)
        value['user']={'login':'author','type':'User'}
        return value

    def api(self,endpoint,*args):
        if endpoint=='graphql' and any('query=' in a and 'reviewDecision' in a for a in args):
            return {'data':{'repository':{'pullRequest':{'reviewDecision':'CHANGES_REQUESTED'}}}}
        if endpoint==f'repos/{TARGET}/pulls' and ('state=open' in args) and getattr(self,'closed',False):
            return [[]] if '--slurp' in args else []
        if endpoint.startswith(f'repos/{TARGET}/pulls?state='):
            rows=[self.raw_pr()] if (('state=closed' in endpoint)==getattr(self,'closed',False)) else []
            return [rows] if '--slurp' in args else rows
        if endpoint==f'repos/{TARGET}/pulls' and any(a.startswith('head=') for a in args):
            requested=next(a.split(':',1)[1] for a in args if a.startswith('head='))
            if requested!=HEAD:
                return [[]]
        return super().api(endpoint,*args)

    def prior_rejected(self):
        self.closed=True
        before=self.prior()
        before['proposals'][-1].update(status='rejected',rejectionReason=None)
        return before

    def start_task(self,before,task='rework-rejected',name='fresh-session'):
        self.write_state(before)
        self.session=self.root/name
        result=tasks.start(argparse.Namespace(repo=str(self.repo),session=str(self.session),base='main',
                          remote=None,task=task,id=10,mr=146 if task in work.REWORK else None))
        self.started=result
        self.data=json.loads((self.session/'session.json').read_text())
        self.selected=self.data['banks'][0]
        return result

    def decide(self,action='repair'):
        notes={'action':action,'requirements':[{'url':self.comments[0]['html_url'],'quote':self.review_text}],
               'rationale':'Actual synthetic test decision, not publication authority'}
        result=work.record_decision(self.session,self.data,self.selected,notes)
        if self.selected['point']=='task-complete':
            self.selected['state']=work.no_attempt(self.session,self.data,self.selected,
                                                   state_results._prior_state(self.data,10))
        sessions.save(self.session,self.data)
        return result

    def fresh_candidate(self):
        self.evidence=self.session/'evidence/10'
        self.base_icon=fixture.A
        at=work.now()
        self.file('raw.svg',candidates.RAW)
        attempt={'schemaVersion':1,'id':10,'baseCommit':self.base,'iconHash':state.digest(fixture.A),
            'checkedAt':at,'outcome':'candidate','reason':'Fresh original fixture, resolving actual task requirements',
            'nextAction':'Prepare and inspect new candidate','desktop':None,'platforms':[], 'recheckRequired':False,
            'sources':[{'kind':'official','url':candidates.SOURCE,'assetUrl':candidates.ASSET,
                'checkedAt':at,'observation':'Controlled original vector source','independenceGroup':'fixture'}],
            'assessment':{**{k:{'finding':'confirmed','basis':'Controlled test source judgment'} for k in ('identity','currency','variant')},
                'independenceRationale':'Synthetic protocol test','uncertainties':[]},
            'candidate':{'path':'evidence/10/raw.svg','iconHash':state.digest(candidates.RAW),
                'sourceUrl':candidates.SOURCE,'sourceAssetUrl':candidates.ASSET,'rationale':'Synthetic original vector'}}
        path=self.evidence/'input.json';path.write_bytes(state.encoded(attempt))
        sessions.record_attempt(self.session,self.data,self.selected,path)
        sessions.save(self.session,self.data)
        state_results.validate_results(self.session,[10])
        with patch.object(candidates,'OLD',at):
            self.payload=candidates.CandidateCLI.create_package(self)
        draft=(self.session/self.payload['mrDraft']['path']).read_text().replace('Alpha','Example')
        self.payload['mrDraft']=self.file('mr-draft.txt',draft.encode())
        self.payload['taskResponse']=[{'url':self.comments[0]['html_url'],'quote':self.review_text,
                                     'resolution':'The new synthetic final and both new theme frames meet this requirement.'}]
        self.payload['checkedAt']=work.now()
        self.package_path.write_bytes(state.encoded(self.payload))

    def prepare_candidate(self):
        candidate.record_candidate(self.session,self.data,self.selected,self.package_path)
        sessions.save(self.session,self.data)
        _,rows,_=state_results.validate_results(self.session,[10])
        return rows[0]

    def prepare_asset(self,head):
        self.prepare_candidate()
        p=self.selected['state']['proposals'][-1]
        assessment={'schemaVersion':1,'scope':'candidate-assessment','decision':'suitable','id':10,
            'baseCommit':self.base,'baseIconHash':state.digest(fixture.A),'candidateIconHash':state.digest(candidates.FINAL),
            'reviewPackage':p['reviewPackage'],'assessor':'Fixture assessor','statement':'Synthetic test-only visual judgment',
            'assessedAt':work.now(),'provenance':self.file('assessor.txt',b'Explicit synthetic test observation')}
        path=self.evidence/'assessment.json';path.write_bytes(state.encoded(assessment))
        self.data=asset_results.approve(self.session,10,path,agent=True);self.selected=self.data['banks'][0]
        _,_,report,_=asset_results.load_candidate(self.session,10)
        admission=admission_fixture.save(self.session,report)
        output=self.session/'asset-publication';output.mkdir(exist_ok=True)
        prepared=assets.prepare(argparse.Namespace(id=10,target=TARGET,base='main',head=head,
                              admission=admission,public_text=None),self.session,output)
        assets._validate_local(self.session,output,prepared['package'])
        return output,prepared


class TaskRuns(TaskFixture):
    def test_rejected_task_starts_without_parent_files_or_reason_publication(self):
        before=self.prior_rejected()
        old=self.root/'deleted-old-session';old.mkdir();(old/'prior-report.json').write_text('old')
        before['proposals'][0]['sourceCandidate']={'path':str(old/'raw.svg'),'iconHash':state.digest(fixture.B)}
        shutil.rmtree(old)
        result=self.start_task(before)
        self.assertEqual(self.selected['point'],'task-review')
        self.decide();self.fresh_candidate();row=self.prepare_candidate()
        self.assertEqual(row['before'],before)
        self.assertEqual(len(row['after']['proposals']),2)
        self.assertEqual(row['after']['proposals'][0]['status'],'rejected')
        self.assertEqual(row['after']['proposals'][1]['supersedesMR'],self.raw_pr()['html_url'])
        self.assertNotIn('localSeed',self.data)
        self.assertFalse(old.exists())
        self.assertEqual(self.ref(state.REF),self.data['stateCommit'])
        self.assertEqual(row['after']['lastVerifiedAt'],before['lastVerifiedAt'])

    def test_keep_current_records_decision_without_fake_attempt_or_freshness(self):
        before=self.prior_rejected();self.start_task(before);self.decide('keep-current')
        _,rows,_=state_results.validate_results(self.session,[10])
        after=rows[0]['after']
        self.assertEqual(after['lastAttemptAt'],before['lastAttemptAt'])
        self.assertEqual(after['lastVerifiedAt'],before['lastVerifiedAt'])
        self.assertEqual(after['taskResults'][0]['outcome'],'keep-current')
        self.assertEqual(after['proposals'][0]['rejectionReason']['action'],'keep-current')

    def test_open_changes_rework_reuses_mr_and_preserves_original_description(self):
        before=self.prior();self.start_task(before,'rework-changes');self.decide();self.fresh_candidate()
        root,prepared=self.prepare_asset(HEAD)
        package=prepared['package']
        self.assertEqual(package['revision']['sourceHead'],self.asset)
        self.assertEqual(package['body'],self.body)
        self.assertIn('Reviewer requirements addressed',package['reviewComment'])
        self.assertEqual(len(self.selected['state']['proposals']),1)
        self.assertEqual(self.selected['state']['proposals'][0]['revisionHistory'],before['proposals'])

    def test_new_closed_rework_mr_contains_link_and_resolutions(self):
        self.start_task(self.prior_rejected());self.decide();self.fresh_candidate()
        _,prepared=self.prepare_asset('bank-icon-maintenance/new-10')
        self.assertNotIn('revision',prepared['package'])
        self.assertIn(self.comments[0]['html_url'],prepared['package']['body'])
        self.assertIn('Reviewer requirements addressed',prepared['package']['body'])

    def test_candidate_cannot_drop_review_resolution(self):
        self.start_task(self.prior_rejected());self.decide();self.fresh_candidate()
        self.payload.pop('taskResponse');self.package_path.write_bytes(state.encoded(self.payload))
        with self.assertRaisesRegex(ValueError,'resolution'):
            self.prepare_candidate()

    def test_invalid_identity_remains_blocked(self):
        self.put('banks/Other_10-ru/info.json',state.encoded({'id':10,'title':'Other','countryCode':'RU'}));self.base=self.commit()
        with self.assertRaises(ValueError):self.start_task(self.prior_rejected())

    def test_no_permission_is_inferred_from_review_text(self):
        self.start_task(self.prior_rejected());self.permission='read'
        with self.assertRaisesRegex(ValueError,'access'):
            self.decide()

    def test_changed_discussion_blocks_candidate_publication_not_history(self):
        self.start_task(self.prior_rejected());self.decide();self.fresh_candidate()
        self.comments[0]['body']='New reviewer instruction'
        with self.assertRaisesRegex(ValueError,'discussion changed'):
            self.prepare_asset('bank-icon-maintenance/new-10')

    def test_stale_open_state_does_not_require_global_outcome_scan(self):
        before=self.prior();self.closed=True
        self.start_task(before);self.decide();self.fresh_candidate()
        row=self.prepare_candidate()
        self.assertEqual(row['after']['proposals'][0]['status'],'rejected')
        self.assertEqual(row['before']['proposals'][0]['status'],'open')

    def test_explicit_limit_overrides_policy_default(self):
        self.closed=True
        for id in range(30,60,10):
            self.put(f'banks/Example_{id}-ru/info.json',state.encoded({'id':id,'title':'Example','countryCode':'RU'}))
            self.put(f'banks/Example_{id}-ru/icon.svg',fixture.A)
        self.base=self.commit()
        result=tasks.plan(argparse.Namespace(repo=str(self.repo),remote=None,base='main',task='update-existing',limit=4))
        self.assertEqual(len(result['selected']),4)
        self.assertEqual(result['limit'],4)

    def test_legacy_state_and_paths_are_not_rewritten_at_start(self):
        before=self.prior_rejected();self.start_task(before)
        value,_=state.remote_record(self.remote,self.ref(state.REF),'banks/state/10.json')
        self.assertEqual(value,before)
        self.assertEqual(self.selected['state'],before)
