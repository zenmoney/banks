"""Navigation observes canonical stages, never manufactures visual decisions."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import workflow
import workflow_navigation as navigation
import test_workflow_source as fixtures


class Navigation(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.session = Path(temp.name).resolve()
        self.data = {'baseCommit': 'a'*40}
        self.bank = {'id': 10, 'iconHash': None, 'point': 'identity-evidence'}
        p = patch.object(workflow, '_context', return_value=(self.data, self.bank))
        p.start(); self.addCleanup(p.stop)

    def test_next_has_concrete_paths_and_does_not_create_files(self):
        before = list(self.session.iterdir())
        value = navigation.next_step(self.session, 10)
        self.assertEqual(value['stage'], 'research')
        self.assertFalse(value['baseline']['present'])
        for command in value['commands']:
            self.assertTrue(command['runnable'])
            self.assertIn(str(self.session), command['argv'])
        self.assertEqual(list(self.session.iterdir()), before)

    def test_templates_leave_all_judgments_unset_and_do_not_record(self):
        value = navigation.notes_template(self.session, 10, 'attempt')
        notes = json.loads((self.session/value['notes']['path']).read_bytes())
        self.assertIsNone(notes['outcome'])
        self.assertIsNone(notes['assessment']['identity']['finding'])
        self.assertTrue(all(row['status'] is None for row in notes['platforms']))
        self.assertNotIn('baseCommit', notes)
        self.assertFalse((self.session/'session.json').exists())
        with self.assertRaises(ValueError): navigation.notes_template(self.session, 10, 'assessment')

    def test_stopped_bank_does_not_get_research_or_publish_command(self):
        self.bank.update(point='needs-human', reason='Identity conflict')
        value = navigation.next_step(self.session, 10)
        self.assertEqual(value['commands'], [])
        self.assertEqual(value['reason'], 'Identity conflict')

    def test_completed_bank_routes_to_state_without_reopening(self):
        self.bank['point'] = 'waiting-mr'
        result = navigation.next_step(self.session, 10)
        self.assertEqual(result['commands'][0]['argv'][2], 'state-prepare')

    def test_raw_candidate_marks_unresolved_command_inputs(self):
        self.bank.update(point='waiting-proposal', report='reports/10.json', state={'proposals': [{'status': 'pending'}]})
        result = navigation.next_step(self.session, 10)
        self.assertEqual(result['stage'], 'prepare-candidate')
        self.assertFalse(result['commands'][1]['runnable'])
        self.assertIn('FINAL_SVG', result['commands'][1]['requiredInputs'])

    def test_active_remote_proposal_without_local_evidence_needs_original_session(self):
        self.bank.update(point='waiting-proposal', state={'proposals': [{'status': 'pending'}]})
        result = navigation.next_step(self.session, 10)
        self.assertEqual(result['stage'], 'resume-original-session')
        self.assertFalse(result['commands'])

    def test_first_addition_view_returns_exact_final_frames(self):
        fixture = fixtures.SourceFixture('runTest'); fixture.base_icon = None
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        bundle_link, bundle = fixture.desktop_bundle()
        reference, _, _ = fixture.make_view()
        with patch.object(workflow, '_context', return_value=(fixture.data, fixture.bank)), \
                patch.object(workflow, '_raw_candidate', return_value=({}, fixture.raw)), \
                patch.object(workflow.source, 'prepare_reference', return_value=json.loads((fixture.session/reference['path']).read_bytes())['sourceReference']):
            result = workflow.candidate_view(fixture.session, 10, str(fixture.final), bundle_link['path'])
        self.assertEqual(result['baseline'], {'present': False, 'sha256': None})
        self.assertEqual(result['baseFinal'], {'light': None, 'dark': None})
        self.assertEqual(result['finalFrames'], {theme: bundle['desktop'][theme] for theme in ('light','dark')})
