"""Execute the evaluation harness with a fake client, not a behavioral model pass."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('behavior_runner', ROOT/'evals/run.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class BehavioralRunner(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='eval-runner-test-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.repo = self.root/'repo'; self.repo.mkdir()
        subprocess.run(['git','init','-q',str(self.repo)], check=True)
        skill = self.repo/runner.SKILL; (skill/'evals').mkdir(parents=True)
        (skill/'SKILL.md').write_text('Old skill')
        (skill/'evals/evals.json').write_text(json.dumps({'evals':[{'id':12,'prompt':'Add first icon','expectations':['Actual original vector','Exact image input','No unsafe writes']}]}))
        self.old = self.commit()
        (skill/'SKILL.md').write_text('New skill')
        self.new = self.commit()
        fixture = self.root/'fixtures/12'; fixture.mkdir(parents=True)
        (fixture/'fixture.json').write_text(json.dumps({'schemaVersion':1,'caseId':12,'externalWrites':False,'prebuiltEvidence':False}))
        (fixture/'source.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
        self.adapter = self.root/'client.py'
        self.adapter.write_text('''import json,sys
from pathlib import Path
r=json.load(sys.stdin)
assert r['freshContext'] is True
assert 'expectations' not in r
install=Path(r['installation'])
assert not (install/'touched').exists()
(install/'touched').write_text('modified only this isolated run')
Path('transcript.jsonl').write_text(json.dumps({'type':'tool_call','name':'fixture-test-not-model'})+'\\n')
Path('final.txt').write_text('Fake client protocol fixture, not an agent pass')
print(json.dumps({'status':'agent-complete','model':'fixture-not-model','client':'test-fixture'}))
''')
        self.config = self.root/'adapter.json'
        self.config.write_text(json.dumps({'argv':[sys.executable,str(self.adapter)]}))

    def commit(self):
        subprocess.run(['git','-C',str(self.repo),'add','.'], check=True)
        subprocess.run(['git','-C',str(self.repo),'-c','user.name=Fixture','-c','user.email=fixture@localhost','commit','-qm','fixture'], check=True)
        return runner.git(self.repo,'rev-parse','HEAD').decode().strip()

    def args(self, execute=False):
        return argparse.Namespace(repo=str(self.repo),old=self.old,new=self.new,fixtures=str(self.root/'fixtures'),
                                  output=str(self.root/'output'),case=[12],repeat=2,timeout=10,
                                  adapter=str(self.config),execute_model_calls=execute)

    def test_prepare_does_not_invoke_model_and_keeps_expectations_untested(self):
        self.adapter.unlink()
        result = runner.run(self.args())
        self.assertEqual(result['status'],'prepared-not-run')
        self.assertEqual(len(result['runs']),4)
        grades=list((self.root/'output/runs').rglob('grading.json'))
        self.assertTrue(all(e['passed'] is None for p in grades for e in json.loads(p.read_bytes())['expectations']))
        self.assertEqual(runner.git(self.repo,'status','--porcelain'),b'')

    def test_real_subprocess_protocol_is_paired_fresh_and_never_invents_tokens_or_score(self):
        result = runner.run(self.args(True))
        self.assertEqual(result['status'],'awaiting-grading')
        self.assertEqual([r['revision'] for r in result['runs']],['old','new','new','old'])
        self.assertTrue(all(r['visualEvaluation']=='not-tested' for r in result['runs']))
        self.assertTrue(all('usage' not in r and 'score' not in r for r in result['runs']))
        self.assertEqual(runner.fingerprint(self.root/'output/runs/12/0/old/fixture'),runner.fingerprint(self.root/'output/runs/12/0/new/fixture'))

    def test_missing_fixture_stops_before_paid_call(self):
        (self.root/'fixtures/12/fixture.json').unlink()
        with self.assertRaises(ValueError): runner.run(self.args(True))
        self.assertFalse((self.root/'output/runs').exists())

    def test_failed_adapter_is_infrastructure_failure_not_a_failed_model(self):
        self.adapter.write_text('raise SystemExit(17)\n')
        result=runner.run(self.args(True))
        self.assertEqual(result['status'],'infrastructure-failed')
        self.assertTrue(all(r['status']=='infrastructure-failed' for r in result['runs']))

    def test_image_event_needs_actual_exact_image_bytes(self):
        root=self.root/'trace';root.mkdir()
        (root/'final.txt').write_text('Observed')
        (root/'image.png').write_bytes(b'not an image')
        event={'type':'image_input','path':'image.png','sha256':runner.digest(b'other')}
        (root/'transcript.jsonl').write_text(json.dumps(event)+'\n')
        with self.assertRaisesRegex(ValueError,'image input hash'):
            runner.observations(root)

    def test_fresh_output_and_committed_revisions_only(self):
        args=self.args(); args.output=str(self.repo/'forbidden')
        with self.assertRaises(ValueError): runner.run(args)
        args=self.args(); args.new='missing-revision'
        with self.assertRaises(subprocess.CalledProcessError): runner.run(args)
