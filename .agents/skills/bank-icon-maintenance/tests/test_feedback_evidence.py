"""Independent requested-raster/final pixel bindings; no simulated model approval."""
import copy
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

import test_workflow_source as fixtures
from test_workflow_source import ASSET, PAST, png
import feedback_evidence as feedback
import feedback_revision as revision
import source_evidence as se


class RequestedReference(unittest.TestCase):
    put = fixtures.SourceFixture.put
    save_receipt = fixtures.SourceFixture.save_receipt
    desktop_bundle = fixtures.SourceFixture.desktop_bundle
    base_icon = fixtures.SourceFixture.base_icon

    def setUp(self):
        fixtures.SourceFixture.setUp(self)
        self.bank['feedbackContext'] = {'path': 'fixture-context.json', 'sha256': 'c'*64}
        self.ctx = {'requestKey': 'a'*64, 'request': {'selectedUrl': ASSET, 'recordedAt': PAST,
            'event': {'source': {'url': 'https://github.com/fixture/catalog/pull/146'}}}}
        patched = patch.object(revision, 'context', return_value=self.ctx)
        patched.start(); self.addCleanup(patched.stop)
        self.bundle_link, self.bundle = self.desktop_bundle()
        self.view = feedback.view(self.session, self.data, self.bank, self.receipt_link, self.final, self.bundle_link)
        self.payload = {'checkedAt': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            'candidate': se.link(self.session, self.final), 'desktop': self.bundle['desktop'],
            'comparison': self.bundle['comparison'], 'reviewerFeedback': feedback.assemble(
                self.session, self.data, self.bank, self.view['view'],
                {'sameVariant': True, 'statement': 'Explicit controlled comparison assertion'})}

    def validate(self, payload=None):
        return feedback.validate(self.session, self.data, self.bank, payload or self.payload)

    def test_full_raster_reference_is_bound_to_both_exact_final_frames(self):
        self.validate()
        self.assertEqual(set(self.view['pairs']), set(se.BACKGROUNDS))
        links = feedback.links(self.session, self.payload)
        self.assertIn(self.receipt_link, links)
        self.assertIn(self.body, links)
        self.assertIn(se.link(self.session, self.final), links)

    def test_missing_or_negative_judgment_does_not_approve_variant(self):
        for judgement in (None, {}, {'sameVariant': None, 'statement': 'unchecked'},
                          {'sameVariant': False, 'statement': 'different logo'},
                          {'sameVariant': True, 'statement': ''}):
            bad = copy.deepcopy(self.payload); bad['reviewerFeedback']['judgement'] = judgement
            with self.assertRaises(ValueError): self.validate(bad)

    def test_missing_reviewer_comparison_is_not_optional_for_revision(self):
        bad = copy.deepcopy(self.payload); bad.pop('reviewerFeedback')
        with self.assertRaises(ValueError): self.validate(bad)

    def test_different_request_or_attachment_hash_is_rejected(self):
        for key in ('requestKey', 'sourceSha256'):
            bad = copy.deepcopy(self.payload); bad['reviewerFeedback'][key] = 'b'*64
            with self.assertRaises(ValueError): self.validate(bad)

    def test_download_must_start_at_selected_url_and_follow_request(self):
        self.ctx['request']['selectedUrl'] = 'https://example.test/different.png'
        with self.assertRaises(ValueError): self.validate()
        self.ctx['request']['selectedUrl'] = ASSET
        self.ctx['request']['recordedAt'] = '2021-01-01T00:00:00Z'
        with self.assertRaisesRegex(ValueError, 'not fetched for'): self.validate()

    def test_changed_attachment_or_final_frame_invalidates_evidence(self):
        (self.session / self.body['path']).write_bytes(png('#ffffff'))
        with self.assertRaises(ValueError): self.validate()
        (self.session / self.body['path']).write_bytes(png('#dd2244'))
        path = self.payload['comparison']['light']['candidate']['path']
        (self.session / path).write_bytes(png('#ffffff', (240, 240)))
        with self.assertRaises(ValueError): self.validate()

    def test_unsolicited_reference_cannot_create_revision_authority(self):
        del self.bank['feedbackContext']
        with self.assertRaisesRegex(ValueError, 'unsolicited'): self.validate()

