"""Review regressions: exact gates, not certification of subjective image judgments."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import admission
import source_evidence as se
import admission_fixture


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='admission-test-')
        self.addCleanup(temporary.cleanup)
        self.session = Path(temporary.name)
        image = self.session / 'evidence/10/fixture.png'
        image.parent.mkdir(parents=True)
        image.write_bytes(b'explicit binding fixture; not a render')
        link = se.link(self.session, image)
        self.report = {'id': 10, 'reviewPackage': {'path': 'evidence/10/package.json', 'sha256': 'a'*64},
                       'candidate': {'path': 'evidence/10/final.svg', 'sha256': 'f'*64},
                       'renderBinding': {'checkedAt': '2020-01-01T00:00:00Z'},
                       'before': {'proposals': [{'sources': [{'url': 'https://bank.example/'}],
                                                'assessment': {'uncertainties': []}}]},
                       'evidence': {'baseIconHash': 'b'*64, 'candidate': {'faithful': True, 'visibleDiff': True},
                                    'comparison': {theme: {role: link for role in ('source', 'candidate', 'base')}
                                                   for theme in ('light', 'dark')},
                                    'platforms': [{'target': 'android-native', 'available': False, 'status': 'not-tested'}]}}
        self.notes = admission_fixture.notes_for(self.report)

    def make(self):
        result = admission.build(self.session, self.report, self.notes)
        admission.validate(self.session, self.report, result['admission'], positive=False)
        return result

    def test_baseline_unknown_does_not_block_supported_replacement(self):
        self.assertEqual(self.make()['status'], 'admitted')

    def test_every_known_quality_failure_blocks_even_with_faithful_true(self):
        for name in admission.CHECKS:
            for status in ('fail', 'unresolved'):
                with self.subTest(name=name, status=status):
                    self.notes = admission_fixture.notes_for(self.report)
                    self.notes['quality'][name].update(status=status, basis='Observed defect, disclosed but not waived')
                    result = self.make()
                    self.assertEqual(result['status'], 'blocked')
                    with self.assertRaisesRegex(ValueError, 'publication blocked'):
                        admission.validate(self.session, self.report, result['admission'])

    def test_capital_one_lockup_cannot_be_called_a_monogram(self):
        self.notes['asset'].update(finalRole='monogram', accompanyingText=True)
        self.notes['quality']['smallSize']['basis'] = 'Fails at 24 px; formal status accidentally remains pass'
        self.assertEqual(self.make()['status'], 'blocked')

    def test_pinned_tab_mask_is_not_a_full_color_icon(self):
        self.notes['asset']['sourceRole'] = 'mask'
        self.assertEqual(self.make()['status'], 'blocked')

    def test_regional_asset_requires_record_applicability(self):
        self.notes['asset']['scope'] = 'regional'
        self.notes['asset']['applicability']['status'] = 'unresolved'
        self.assertEqual(self.make()['status'], 'blocked')
        self.notes['asset']['applicability']['status'] = 'pass'
        self.assertEqual(self.make()['status'], 'admitted')

    def test_rename_or_legal_merger_does_not_define_historical_purpose(self):
        self.notes['record'].update(purpose='historical', period='Before rename')
        self.notes['record']['legalLifecycle']['status'] = 'merged'
        self.assertEqual(self.make()['status'], 'blocked')

    def test_explicit_historical_purpose_and_period_can_pass(self):
        path = self.session / 'evidence/10/owner-note.txt'
        path.write_text('Fixture owner asks to retain this historical brand period.')
        self.notes['record'].update(purpose='historical', period='Fixture historical period',
                                     basisKind='human-decision', purposeEvidence=path.relative_to(self.session).as_posix())
        self.notes['record']['brandLifecycle']['status'] = 'retired'
        self.assertEqual(self.make()['status'], 'admitted')

    def test_legal_merger_with_live_brand_is_not_automatically_retired(self):
        self.notes['record']['legalLifecycle']['status'] = 'merged'
        self.assertEqual(self.make()['status'], 'admitted')

    def test_jupiter_new_domain_match_does_not_invent_old_logo_rebrand(self):
        self.notes['change']['kind'] = 'rebrand'
        self.assertEqual(self.make()['status'], 'blocked')
        self.notes['change'].update(baselineAttribution='supported', transitionSourceRefs=[0])
        self.assertEqual(self.make()['status'], 'admitted')

    def test_first_addition_cannot_claim_fake_baseline(self):
        self.report['evidence']['baseIconHash'] = None
        self.assertEqual(self.make()['status'], 'blocked')
        self.notes = admission_fixture.notes_for(self.report)
        self.assertEqual(self.make()['status'], 'admitted')

    def test_true_lettermarks_wordmarks_and_gradients_not_blanket_rejected(self):
        for role in ('lettermark', 'wordmark', 'symbol'):
            self.notes['asset']['finalRole'] = role
            self.notes['quality']['fidelity']['basis'] = 'Original gradient preserved; readable in controlled fixture'
            self.assertEqual(self.make()['status'], 'admitted')

    def test_all_prior_uncertainties_must_be_disposed_without_dropping_them(self):
        self.report['before']['proposals'][-1]['assessment']['uncertainties'] = ['Uncertain candidate colors']
        with self.assertRaisesRegex(ValueError, 'every prior uncertainty'):
            self.make()
        self.notes['concerns'] = [{'kind': 'variant', 'basis': 'Uncertain candidate colors', 'target': None, 'priorUncertainty': 0}]
        self.assertEqual(self.make()['status'], 'blocked')

    def test_theme_failure_cannot_use_platform_unavailability_target(self):
        self.notes['concerns'] = [{'kind': 'contrast', 'basis': 'Dark theme fails', 'target': 'android-native', 'priorUncertainty': None}]
        with self.assertRaisesRegex(ValueError, 'only unavailable'):
            self.make()
        self.notes['concerns'][0]['target'] = None
        self.assertEqual(self.make()['status'], 'blocked')

    def test_unavailable_mobile_does_not_block_but_available_untested_is_not_unavailable(self):
        self.notes['concerns'] = [{'kind': 'platform-unavailable', 'basis': 'SDK fixture absent',
                                  'target': 'android-native', 'priorUncertainty': None}]
        self.assertEqual(self.make()['status'], 'admitted')
        self.report['evidence']['platforms'][0]['available'] = True
        with self.assertRaisesRegex(ValueError, 'exact unavailable'):
            self.make()

    def test_new_bytes_wrong_bank_and_stale_evidence_rejected(self):
        result = self.make()
        for field, replacement in (('id', 11), ('candidate', {'sha256': 'e'*64}),
                                    ('reviewPackage', {'path': 'evidence/10/other.json', 'sha256': 'b'*64})):
            report = {**self.report, field: replacement}
            with self.assertRaisesRegex(ValueError, 'differs'):
                admission.validate(self.session, report, result['admission'])
        (self.session/'evidence/10/fixture.png').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            admission.validate(self.session, self.report, result['admission'])

    def test_self_consistent_hash_cannot_turn_failed_checks_into_admitted(self):
        self.notes['quality']['darkTheme']['status'] = 'fail'
        result = self.make()
        path = self.session / result['admission']['path']
        document = se.read_json(path)
        document.update(decision='admitted', blockers=[])
        path.write_text(json.dumps(document))
        with self.assertRaisesRegex(ValueError, 'contradicts'):
            admission.validate(self.session, self.report, se.link(self.session, path))

    def test_null_templates_future_time_bad_indices_and_duplicate_keys_rejected(self):
        with self.assertRaises(ValueError):
            admission.inspect(self.session, self.report, admission.template())
        for refs in ([True], [1], [-1], [0,0]):
            self.notes['asset']['role']['sourceRefs'] = refs
            with self.assertRaises(ValueError): self.make()
        self.notes = admission_fixture.notes_for(self.report)
        self.notes['concerns'] = [None]
        with self.assertRaisesRegex(ValueError, 'concern:'):
            self.make()
        self.notes['concerns'] = []
        result = self.make(); path = self.session / result['admission']['path']
        document = se.read_json(path); document['checkedAt'] = '2999-01-01T00:00:00Z'
        path.write_text(json.dumps(document))
        with self.assertRaisesRegex(ValueError, 'interval'):
            admission.validate(self.session, self.report, se.link(self.session, path))
        path.write_text('{"schemaVersion":1,"schemaVersion":1}')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            admission.validate(self.session, self.report, se.link(self.session, path))

    def test_declined_preflight_is_retained_for_state_and_no_existing_file_rewritten(self):
        self.notes['quality']['fit']['status'] = 'fail'
        first = self.make(); before = (self.session / first['admission']['path']).read_bytes()
        second = self.make()
        self.assertNotEqual(first['admission']['path'], second['admission']['path'])
        self.assertEqual(before, (self.session / first['admission']['path']).read_bytes())
        self.assertIn(first['admission']['path'], admission.retained(self.session, self.report))
        self.assertFalse((self.session/'session.json').exists())

    def resolution_fixture(self):
        self.report['before']['proposals'][-1]['assessment']['uncertainties']=['Confirm full emblem extraction and final presentation']
        root=self.session/'evidence/10'
        (root/'final.svg').write_bytes(b'<svg/>')
        self.report['candidate']=se.link(self.session,root/'final.svg')
        image=self.report['evidence']['comparison']['light']['source']
        reference={'source':self.report['candidate'],'raster':image}
        (root/'reference.json').write_text(json.dumps(reference))
        (root/'bundle.json').write_text('{}')
        view={'id':10,'final':self.report['candidate'], 'sourceReference':se.link(self.session,root/'reference.json'),
              'desktopBundle':se.link(self.session,root/'bundle.json'),'pairs':{'light':image,'dark':image}}
        (root/'source-comparison.json').write_text(json.dumps(view))
        self.report['evidence']['sourceComparison']=se.link(self.session,root/'source-comparison.json')
        self.notes['concerns']=[{'kind':'fidelity','basis':'Original standalone SVG absent; extraction was unverified',
            'priorUncertainty':0,'target':None,'resolution':{'basis':'Whole original source and both final themes inspected for this exact candidate',
            'sourceRefs':[0],'evidenceRefs':['sourceComparison','candidate-light','candidate-dark']}}]

    def test_resolved_fidelity_uses_exact_existing_evidence_without_erasing_uncertainty(self):
        self.resolution_fixture()
        concern=self.notes['concerns'][0]; resolution=concern.pop('resolution')
        first=self.make(); before=(self.session/first['admission']['path']).read_bytes()
        self.assertEqual(first['blockers'],['concern.fidelity'])
        concern['resolution']=resolution
        result=self.make(); self.assertEqual(result['status'],'admitted')
        self.assertEqual((self.session/first['admission']['path']).read_bytes(),before)
        links=admission.bindings(self.session,self.report,result['admission'])
        self.assertIn('evidence/10/source-comparison.json',links)
        self.assertIn('evidence/10/reference.json',links)
        self.assertEqual(len(self.report['before']['proposals'][-1]['assessment']['uncertainties']),1)

    def test_resolution_cannot_waive_failures_or_use_missing_unrelated_or_changed_evidence(self):
        self.resolution_fixture(); initial=copy.deepcopy(self.notes)
        for mutate in (lambda r:r.update(basis=''),lambda r:r.update(evidenceRefs=[]),
                       lambda r:r.update(evidenceRefs=['unknown']),lambda r:r.update(sourceRefs=[True])):
            self.notes=copy.deepcopy(initial); mutate(self.notes['concerns'][0]['resolution'])
            with self.assertRaises(ValueError): self.make()
        self.notes=copy.deepcopy(initial); self.notes['quality']['fidelity']['status']='fail'
        self.assertIn('quality.fidelity',self.make()['blockers'])
        self.notes=copy.deepcopy(initial); self.report['evidence']['candidate']['faithful']=False
        self.assertIn('candidate fidelity or meaningful change failed',self.make()['blockers'])
        self.report['evidence']['candidate']['faithful']=True
        result=self.make()
        (self.session/'evidence/10/source-comparison.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'hash mismatch'):
            admission.validate(self.session,self.report,result['admission'])
