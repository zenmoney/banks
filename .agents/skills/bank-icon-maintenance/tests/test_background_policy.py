"""Background prompts reach real navigation without deciding shape or approval.

Wording/visual policy is reviewed in the guide and behavioral evaluations, not
certified by substring assertions. These tests cover the tool's actual contract.
"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import workflow
import workflow_navigation as navigation

GUIDE = ROOT / 'references/icon-background.md'


class BackgroundNavigation(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='background-policy-')
        self.addCleanup(temp.cleanup)
        self.session = Path(temp.name).resolve()
        self.data = {'baseCommit': 'a' * 40}
        self.bank = {'id': 10, 'point': 'identity-evidence', 'iconHash': None}
        context = patch.object(workflow, '_context', return_value=(self.data, self.bank))
        context.start()
        self.addCleanup(context.stop)

    def assertGuidance(self, value):
        review = value['backgroundReview']
        self.assertEqual(set(review), {'guide', 'questions'})
        self.assertEqual(Path(review['guide']), GUIDE)
        self.assertTrue(GUIDE.is_file())
        self.assertEqual(set(review['questions']), {'backing-role', 'official-display', 'whole-emblem-fit',
                         'small-size-legibility', 'emblem-fidelity', 'adaptation-provenance'})
        self.assertTrue(all(isinstance(question, str) and question.strip()
                            for question in review['questions'].values()))

    def test_research_and_preparation_get_independent_guidance_without_mutation(self):
        for present in (False, True):
            for point in ('identity-evidence', 'waiting-proposal'):
                with self.subTest(present=present, point=point):
                    self.bank.update(point=point, iconHash='b' * 64 if present else None,
                                     report='reports/10.json', state={'proposals': [{'status': 'pending'}]})
                    before = copy.deepcopy((self.data, self.bank))
                    first = navigation.next_step(self.session, 10)
                    self.assertGuidance(first)
                    first['backgroundReview']['questions']['official-display'] = 'local edit'
                    second = navigation.next_step(self.session, 10)
                    self.assertNotEqual(second['backgroundReview']['questions']['official-display'], 'local edit')
                    self.assertEqual((self.data, self.bank), before)
                    self.assertEqual(list(self.session.iterdir()), [])

    def test_prepared_assessment_gets_prompts_without_changing_package(self):
        payload = {'comparison': {theme: {'candidate': {'path': 'existing-' + theme},
                                          'beforeAfter': None} for theme in ('light', 'dark')}}
        root = self.session / 'evidence/10'
        root.mkdir(parents=True)
        path = root / 'prepared.json'
        path.write_text(json.dumps(payload))
        link = navigation.source.link(self.session, path)
        self.bank.update(point='waiting-proposal', report='reports/10.json',
                         state={'proposals': [{'reviewPackage': link}]})
        original = path.read_bytes()
        with patch('asset_results.load_candidate', return_value=(self.data, self.bank,
                                                                {'reviewPackage': link}, None)):
            result = navigation.next_step(self.session, 10)
        self.assertEqual(result['stage'], 'assess-candidate')
        self.assertGuidance(result)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(result['images']['baseFinal'], {'light': None, 'dark': None})
        self.assertEqual(result['images']['finalFrames'],
                         {theme: payload['comparison'][theme]['candidate'] for theme in ('light', 'dark')})

    def test_blank_judgments_do_not_gain_schema_fields_or_approval(self):
        for stage in ('attempt', 'candidate', 'assessment'):
            with self.subTest(stage=stage):
                self.bank.update(point='identity-evidence' if stage == 'attempt' else 'waiting-proposal',
                                 report='reports/10.json', state={'proposals': [
                                     {'reviewPackage': {'path': 'unused', 'sha256': 'c' * 64}} if stage == 'assessment' else {}]})
                result = navigation.notes_template(self.session, 10, stage)
                self.assertGuidance(result)
                notes = json.loads((self.session / result['notes']['path']).read_bytes())
                self.assertNotIn('backgroundReview', notes)
                if stage == 'candidate':
                    self.assertEqual(set(notes), {'candidate', 'visualComparison', 'sourceJudgement', 'platforms'})
                    self.assertIsNone(notes['candidate']['faithful'])
                    self.assertIsNone(notes['candidate']['variant'])
                    self.assertEqual(notes['candidate']['transforms'], [])
                elif stage == 'assessment':
                    self.assertEqual(notes, {'assessor': None, 'decision': None, 'statement': None})
                else:
                    self.assertIsNone(notes['assessment']['variant']['finding'])
                self.assertFalse((self.session / 'session.json').exists())

    def test_stops_and_completed_results_do_not_start_background_work(self):
        for point in ('needs-human', 'blocked', 'waiting-mr', 'attempt-complete'):
            with self.subTest(point=point):
                self.bank.update(point=point, reason='existing stop')
                result = navigation.next_step(self.session, 10)
                self.assertNotIn('backgroundReview', result)
                self.assertNotIn('prepare-candidate', result.get('stage', ''))
        self.bank.update(point='waiting-proposal', state={'proposals': [{'status': 'pending'}]})
        self.bank.pop('report', None)
        result = navigation.next_step(self.session, 10)
        self.assertEqual(result['stage'], 'resume-original-session')
        self.assertNotIn('backgroundReview', result)
        self.assertEqual(result['commands'], [])
