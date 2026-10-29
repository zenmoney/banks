"""Full-checkout CLI/Git integration; HTTP and render observations are controlled fixtures.

No live bank requests, hosted PRs, physical devices, or visual approval claims.
"""
from session_fixture import make_session
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import test_workflow_source as fixtures
from test_workflow_source import BASE, PAST, png

ROOT = Path(__file__).resolve().parents[1]
SESSION_CLI = ROOT / 'scripts/session.py'
WORKFLOW = ROOT / 'scripts/workflow.py'
sys.path.insert(0, str(ROOT / 'scripts'))
import source_evidence as se


@unittest.skipUnless(SESSION_CLI.is_file(), 'full checkout required for canonical CLI integration')
class WorkflowIntegration(unittest.TestCase):
    base_icon = BASE

    def setUp(self):
        if shutil.which("rsvg-convert") is None:
            self.skipTest("real librsvg is required for vector source references")
        temporary = tempfile.TemporaryDirectory(prefix='workflow-integration-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.repo = self.root / 'source checkout'; self.repo.mkdir()
        self.session = self.root / 'session'
        self.git('init','-q')
        self.git('config','user.name','Fixture'); self.git('config','user.email','fixture@example.invalid')
        (self.repo / 'bank-icon-maintenance.json').write_text(json.dumps({'schemaVersion':1,
            'recheckAfterDays':90,'retryUnconfirmedAfterDays':30,'retryErrorAfterDays':7,'selectionLimit':5}))
        folder=self.repo / 'banks/Example_10-ru'; folder.mkdir(parents=True)
        (folder/'info.json').write_text(json.dumps({'id':10,'title':'Example','countryCode':'RU'}))
        if self.base_icon is not None:
            (folder/'icon.svg').write_bytes(self.base_icon)
        self.git('add','.'); self.git('commit','-qm','Synthetic catalog')
        self.started=make_session(self , bank_id=10)
        fixture=fixtures.SourceFixture('runTest'); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        content=(fixture.session/fixture.body['path']).read_bytes()
        final_content=fixture.final.read_bytes()
        old_session=str(fixture.session)
        fixture.session=self.session; fixture.data=self.started; fixture.bank=self.started['banks'][0]
        fixture.receipt['attempts'][0]['command'] = [a.replace(old_session,str(self.session)) for a in fixture.receipt['attempts'][0]['command']]
        fixture.body=fixture.put(fixture.body['path'],content)
        fixture.receipt['baseCommit']=self.started['baseCommit']; fixture.save_receipt()
        fixture.final=self.session/'evidence/10/final.svg'; fixture.put('evidence/10/final.svg',final_content)
        fixture.replace_source(final_content)
        self.fixture=fixture
        self.notes=self.write('attempt-notes.json',fixture.notes())

    def git(self,*args):
        return subprocess.run(['git','-C',str(self.repo),*args],check=True,capture_output=True,text=True).stdout.strip()

    def write(self,name,value):
        path=self.session/'evidence/10'/name; path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value)); return path

    def call(self,script,args,ok=True):
        result=subprocess.run([sys.executable,str(script),*args],capture_output=True,text=True,timeout=40)
        self.assertEqual(result.returncode,0 if ok else 1,result.stdout+result.stderr)
        return json.loads(result.stdout)

    def session_call(self,command,*args,ok=True):
        return self.call(SESSION_CLI,[command,'--session',str(self.session),*args],ok=ok)

    def helper(self,command,*args,ok=True):
        return self.call(WORKFLOW,[command,'--session',str(self.session),*args],ok=ok)

    def start_candidate(self):
        result=self.helper('attempt','--id','10','--notes',str(self.notes),'--retrieval',
                           str(self.session/self.fixture.receipt_link['path']),'--record')
        self.assertEqual(result['result']['banks'][0]['point'],'waiting-proposal')
        return result

    def prepared_view(self):
        self.start_candidate()
        bundle_link,bundle=self.fixture.desktop_bundle()
        bundle['desktop']['observedAt']=se.now()
        self.fixture.put(bundle_link['path'],json.dumps(bundle).encode())
        view=self.helper('candidate-view','--id','10','--final',str(self.fixture.final),
                         '--desktop-bundle',str(self.session/bundle_link['path']))
        return view

    def candidate_notes(self):
        return {'candidate':{'rationale':'Fixture current variant','role':'Compact symbol','variant':'Fixture',
                    'transforms':['Fixture vector adaptation'], 'detailsJudgement':'Fixture observation',
                    'proportionsJudgement':'Fixture observation', 'faithful':True,'visibleDiff':True,
                    'visibleDiffExplanation':'Fixture difference'},
                'sourceJudgement':'Explicit controlled source comparison, not a real bank approval',
                'visualComparison':'Explicit controlled render observation',
                'platforms':[{'target':t,'available':False,'status':'not-tested','observation':'Fixture unavailable'}
                    for t in ('android-compose','android-native','ios-compose','ios-native')]}

    def test_raster_research_cannot_be_recorded_as_prepared_replacement(self):
        self.fixture.replace_source(png("#dd2244"))
        view = self.prepared_view()
        before = (self.session / "session.json").read_bytes()
        prior = (self.session / "evidence/10/validated.json").read_bytes()
        blocked = self.helper("candidate-build", "--id", "10", "--view",
            str(self.session / view["view"]["path"]), "--notes",
            str(self.write("candidate-notes.json", self.candidate_notes())), "--record", ok=False)
        self.assertIn("original vector", blocked["reason"])
        self.assertEqual((self.session / "session.json").read_bytes(), before)
        self.assertEqual((self.session / "evidence/10/validated.json").read_bytes(), prior)

    def test_full_assembly_record_assessment_and_state_prepare_keep_exact_source(self):
        initial_notes=json.loads(self.notes.read_text())
        initial_notes['assessment']['uncertainties']=['Extraction and final presentation still need checking']
        self.notes.write_text(json.dumps(initial_notes))
        view=self.prepared_view()
        old_report=(self.session/'reports/10.json').read_bytes()
        notes=self.write('candidate-notes.json',self.candidate_notes())
        built=self.helper('candidate-build','--id','10','--view',str(self.session/view['view']['path']),
                          '--notes',str(notes),'--record')
        self.assertEqual(built['result']['banks'][0]['point'],'waiting-proposal')
        assessed=self.helper('assessment','--id','10','--notes',str(self.write('assessment-notes.json',
                    {'assessor':'fixture-agent','decision':'suitable','statement':'Actual fixture judgment only'})),'--record')
        self.assertEqual(assessed['exitCode'],0)
        current=json.loads((self.session/'session.json').read_bytes())
        proposal=current['banks'][0]['state']['proposals'][-1]
        self.assertEqual(proposal['sourceCandidate']['path'],self.fixture.body['path'])
        self.assertIn('retrieval',proposal['sourceCandidate'])
        self.assertEqual(proposal['humanReview'],'pending')
        self.assertEqual(old_report,(self.session/'reports/10.json').read_bytes())
        import admission, admission_fixture, asset_results
        _, _, report, _=asset_results.load_candidate(self.session,10)
        preflight=admission_fixture.notes_for(report)
        preflight['concerns']=[{'kind':'fidelity','priorUncertainty':0,'target':None,
            'basis':'Prior extraction question retained; resolved by later inspection',
            'resolution':{'basis':'Synthetic source/final comparison inspected on both themes',
                'sourceRefs':[0], 'evidenceRefs':['sourceComparison','candidate-light','candidate-dark']}}]
        result=self.helper('admission','--id','10','--notes',str(self.write('admission-notes.json',preflight)))
        self.assertEqual(result['status'],'admitted')
        links=admission.bindings(self.session,report,result['admission'])
        self.assertIn(report['evidence']['sourceComparison']['path'],links)
        self.assertEqual(old_report,(self.session/'reports/10.json').read_bytes())
        self.assertEqual((self.repo/'banks/Example_10-ru/icon.svg').read_bytes(),BASE)
        remote=self.root/'state.git'
        subprocess.run(['git','init','--bare','-q',str(remote)],check=True)
        prepared=self.session_call('state-prepare','--remote',str(remote),'--ids','10')
        self.assertIn('packageHash',prepared)
        self.assertEqual(subprocess.run(['git','--git-dir',str(remote),'show-ref'],capture_output=True).returncode,1)
        compact=self.helper('run','--','state-show','--package',prepared['packageHash'])
        self.assertIn('resultFile',compact)
        self.assertNotIn('package',compact['result'])
        self.assertTrue(compact['fullPackageRequiredForReview'])

    def test_missing_source_comparison_rejects_record_and_preserves_prior_state(self):
        view=self.prepared_view()
        built=self.helper('candidate-build','--id','10','--view',str(self.session/view['view']['path']),
                          '--notes',str(self.write('candidate-notes.json',self.candidate_notes())))
        path=self.session/built['input']['path']; payload=json.loads(path.read_bytes())
        payload.pop('sourceComparison'); path.write_text(json.dumps(payload))
        before=(self.session/'session.json').read_bytes()
        result=self.session_call('continue','--candidate',str(path),ok=False)
        self.assertIn('original-source/final comparison',result['error'])
        self.assertEqual(before,(self.session/'session.json').read_bytes())

    def test_derived_source_is_rejected_by_canonical_writer_not_just_builder(self):
        result=self.helper('attempt','--id','10','--notes',str(self.notes),'--retrieval',
                           str(self.session/self.fixture.receipt_link['path']))
        path=self.session/result['input']['path']; payload=json.loads(path.read_bytes())
        payload['candidate'].update(path='evidence/10/final.svg',iconHash=se.sha(self.fixture.final.read_bytes()))
        path.write_text(json.dumps(payload))
        before=(self.session/'session.json').read_bytes()
        result=self.session_call('continue','--evidence',str(path),ok=False)
        self.assertIn('original retrieval body',result['error'])
        self.assertEqual(before,(self.session/'session.json').read_bytes())
        self.assertFalse((self.session/'reports/10.json').exists())

    def test_source_reference_tamper_blocks_deferred_prepared_proposal(self):
        view=self.prepared_view()
        self.helper('candidate-build','--id','10','--view',str(self.session/view['view']['path']),
                    '--notes',str(self.write('candidate-notes.json',self.candidate_notes())),'--record')
        before=(self.session/'session.json').read_bytes()
        value=se.read_json(self.session/view['view']['path'])
        reference=se.read_json(self.session/value['sourceReference']['path'])
        (self.session/reference['images']['light']['path']).write_bytes(png('black',(240,240)))
        result=self.helper('assessment','--id','10','--notes',str(self.write('assessment-notes.json',
                    {'assessor':'fixture-agent','decision':'suitable','statement':'Fixture'})),ok=False)
        self.assertIn('hash mismatch',result['reason'])
        self.assertEqual(before,(self.session/'session.json').read_bytes())

    def test_first_icon_assembly_uses_addition_diff_and_retains_independent_source_evidence(self):
        fixture = WorkflowIntegration('test_full_assembly_record_assessment_and_state_prepare_keep_exact_source')
        fixture.base_icon = None
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        view = fixture.prepared_view()
        self.assertEqual(view['baseFinal'], {'light': None, 'dark': None})
        raw = (fixture.session / 'evidence/10/validated.json').read_bytes()
        built = fixture.helper('candidate-build', '--id', '10', '--view',
            str(fixture.session / view['view']['path']), '--notes',
            str(fixture.write('candidate-notes.json', fixture.candidate_notes())), '--record')
        bank = fixture.session_call('continue')['banks'][0]
        proposal = bank['state']['proposals'][-1]
        package = se.read_json(fixture.session / built['assembly']['input']['path'])
        self.assertIsNone(package['baseIconHash'])
        self.assertIsNone(package['desktop']['baseObservation'])
        self.assertTrue((fixture.session / package['diff']['path']).read_bytes().startswith(b'--- /dev/null\n'))
        self.assertIn('No existing icon.svg at frozen base commit',
                      (fixture.session / package['mrDraft']['path']).read_text())
        self.assertEqual((fixture.session / 'evidence/10/validated.json').read_bytes(), raw)
        self.assertIsNone(proposal['baseIconHash'])
        self.assertNotIn('verifiedIconHash', bank['state'])
        self.assertFalse((Path(fixture.started['worktree']) / 'banks/Example_10-ru/icon.svg').exists())
        fixture.helper('assessment', '--id', '10', '--notes', str(fixture.write('assessment-notes.json',
            {'assessor': 'fixture-agent', 'decision': 'suitable', 'statement': 'Controlled first-icon judgment'})), '--record')
        assessed = fixture.session_call('continue')['banks'][0]
        self.assertEqual(assessed['state']['proposals'][-1]['humanReview'], 'pending')


if __name__ == '__main__': unittest.main()
