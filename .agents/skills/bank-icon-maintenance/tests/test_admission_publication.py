"""New asset writes cannot bypass preflight; controlled Git/provider, no hosted banks."""
import json
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import admission
import admission_fixture
import asset_publication
import test_asset_publication as fixtures


class AdmissionPublication(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ApprovedAssetCLI('test_approved_candidate_applies_only_exact_icon_on_target_base')
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

    def test_missing_preflight_blocks_new_application(self):
        f = self.fixture
        f.admission_path = None
        result = f.cli('asset-prepare', '--id', '10', '--target', 'fixture/catalog',
                       '--base', 'main', '--head', 'icons/10-reviewed', ok=False)
        self.assertIn('requires workflow admission', result['error'])
        self.assertFalse((f.session/'asset-publication/10/worktree').exists())
        self.assertIsNone(f.remote_ref('icons/10-reviewed'))

    def test_declined_quality_cannot_be_overruled_by_human_approval_and_is_durable(self):
        f = self.fixture
        import asset_results
        _, _, report, _ = asset_results.load_candidate(f.session, 10)
        notes = admission_fixture.notes_for(report)
        notes['quality']['smallSize'].update(status='fail', basis='Known unreadable wordmark; disclosed, not waived')
        result = admission.build(f.session, report, notes)
        f.admission_path = str(f.session/result['admission']['path'])
        denied = f.cli('asset-prepare', '--id', '10', '--target', 'fixture/catalog',
                       '--base', 'main', '--head', 'icons/10-reviewed', ok=False)
        self.assertIn('quality.smallSize', denied['error'])
        self.assertIsNone(f.remote_ref('icons/10-reviewed'))
        state = f.cli('state-prepare', '--ids', '10', '--remote', str(f.remote))
        self.assertIn(f.admission_path, state['package']['bindings'])
        self.assertEqual(state['package']['records'][0]['after']['proposals'][-1]['status'], 'pending')

    def test_frozen_admission_tamper_blocks_show(self):
        f = self.fixture
        package = f.prepare()
        self.assertEqual(package['package']['schemaVersion'], 2)
        path = Path(f.admission_path)
        path.write_bytes(path.read_bytes()+b'\n')
        denied = f.cli('asset-show', '--package', package['packageHash'], ok=False)
        self.assertIn('changed', denied['error'])

    def test_workflow_assembles_real_preflight_without_filling_judgments(self):
        import workflow
        f = self.fixture
        import asset_results
        _, _, report, _ = asset_results.load_candidate(f.session, 10)
        path = f.session/'evidence/10/admission-notes.json'
        path.write_text(json.dumps(admission_fixture.notes_for(report)))
        result = workflow.assemble_admission(f.session, 10, str(path))
        self.assertEqual(result['status'], 'admitted')
        path.write_text(json.dumps(admission.template()))
        with self.assertRaises(ValueError): workflow.assemble_admission(f.session, 10, str(path))
