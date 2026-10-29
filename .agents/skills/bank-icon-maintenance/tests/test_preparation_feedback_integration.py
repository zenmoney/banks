"""Existing complete preparation/receipt fixtures; no live provider or image approval.

These cases require the full checkout. Controlled image/source bytes exercise the
actual preparation and lineage readers, not a real Android/Compose rendering pass.
"""
import copy
import io
import json
import unittest
import zipfile

import test_asset_publication as publication
import test_workflow_source as sources
import admission_fixture
import admission
import asset_results
import review_materials as review
import source_evidence as source
import source_listing
import source_material


class CompletePublicPreparation(unittest.TestCase):
    def test_platform_and_admission_fail_together_for_update_and_first_addition(self):
        for factory, method in ((publication.ApprovedAssetCLI,
                                 'test_approved_candidate_applies_only_exact_icon_on_target_base'),
                                (publication.FirstIconPublicationCLI,
                                 'test_exact_one_path_addition_preserves_metadata_and_source_checkout')):
            with self.subTest(fixture=factory.__name__):
                fixture = factory(method)
                try:
                    fixture.setUp()
                    _, bank, original, _ = asset_results.load_candidate(fixture.session, 10)
                    report = copy.deepcopy(original)
                    platform = next(row for row in report['evidence']['platforms'] if row['available'] is False)
                    platform['observation'] = 'Tool not installed at /private/android-sdk'
                    notes = admission_fixture.notes_for(report)
                    notes['concerns'].append({'kind': 'platform-unavailable', 'basis': 'Missing /private/toolchain',
                                              'target': platform['target'], 'priorUncertainty': None})
                    admitted = admission.build(fixture.session, report, notes)
                    self.assertEqual(admitted['status'], 'admitted')
                    document = admission.validate(fixture.session, report, admitted['admission'])
                    before = {p: p.read_bytes() for folder in ('evidence', 'reports')
                              for p in (fixture.session / folder).rglob('*') if p.is_file()}
                    with self.assertRaises(review.PublicPreflightError) as caught:
                        review.prepare(fixture.session, bank, report, admission=document)
                    fields = [row['field'] for row in caught.exception.issues]
                    self.assertTrue(any(f.startswith('platforms.') and f.endswith('.observation') for f in fields))
                    self.assertTrue(any(f.startswith('admission.judgments.concerns.') and f.endswith('.basis') for f in fields))
                    self.assertNotIn('/private/', str(caught.exception))
                    self.assertEqual({p: p.read_bytes() for p in before}, before)
                    self.assertEqual(json.loads(fixture.gh_state.read_text())['createCalls'], 0)
                finally:
                    fixture.doCleanups()


class CompleteArchivePreparation(unittest.TestCase):
    def test_original_receipt_full_listing_paging_and_exact_member_replay(self):
        fixture = sources.SourceFixture('test_receipt_binds_original_body_and_original_date')
        try:
            fixture.setUp()
            content = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path d="M0 0h10v10z"/></svg>'
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w') as archive:
                for i in range(400):
                    archive.writestr(f'variants/mark-{i:03d}.svg', content)
            fixture.replace_source(stream.getvalue())
            result = source_material.prepare(fixture.session, fixture.receipt_link, 10, fixture.data['baseCommit'])
            self.assertEqual(result['totalMembers'], 400)
            self.assertEqual(result['returned'], source_listing.DEFAULT_LIMIT)
            saved = source.read_json(source.linked(fixture.session, result['listing'], 'listing'))
            self.assertEqual(len(saved['decoded']['members']), 400)
            filtered = source_listing.page(fixture.session, result['listing'], name_contains='mark-399.svg')
            self.assertEqual(filtered['returned'], 1)
            name = filtered['members'][0]['name']
            chosen = source_material.prepare(fixture.session, fixture.receipt_link, 10, fixture.data['baseCommit'], [name])
            manifest, path = source_material.validate(fixture.session, chosen['sourceMaterial'], fixture.receipt_link,
                                                      10, fixture.data['baseCommit'], source.instant(source.now(), 'test'))
            self.assertEqual(manifest['selection'], ['variants/mark-399.svg'])
            self.assertEqual(path.read_bytes(), content)
            self.assertEqual((fixture.session / fixture.body['path']).read_bytes(), stream.getvalue())
            self.assertFalse((fixture.session / 'banks').exists())
        finally:
            fixture.doCleanups()
