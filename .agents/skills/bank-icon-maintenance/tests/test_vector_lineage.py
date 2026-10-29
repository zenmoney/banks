"""Receipt-backed extraction and cache tests; controlled HTTP, not source truth."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import source_material as material
import source_evidence as source
import workflow
import test_source_material as fixtures
import test_workflow_source as wf_fixtures

HTML = b'<html><body><svg id="mark" viewBox="0 0 20 20"><path fill="#f30" d="M2 2h16v16H2z"/></svg><svg id="other" viewBox="0 0 20 20"><circle r="8" cx="10" cy="10"/></svg></body></html>'


class VectorLineage(fixtures.MaterialFixture):
    def test_html_selector_is_receipt_bound_and_replayed_after_tampering(self):
        receipt, raw = self.fetched(HTML)
        selected = material.prepare(self.session, receipt, 10, self.data['baseCommit'], vector_selection={'svgId': 'mark'})
        binding = selected['sourceMaterial']
        value, file = self.validate(binding, raw)
        self.assertEqual(value['steps'][0]['operation'], 'extract-vector')
        self.assertEqual(value['vectorSelection'], {'svgId': 'mark'})
        self.assertIn(b'#f30', file.read_bytes())
        changed = self.change_manifest(binding, lambda v: v.update(vectorSelection={'svgId': 'other'}))
        with self.assertRaisesRegex(ValueError, 'reproducible extraction'):
            self.validate(changed, raw)
        self.assertEqual((self.session / value['download']['path']).read_bytes(), HTML)

    def test_drop_selector_cannot_reclassify_html_as_direct_svg(self):
        receipt, raw = self.fetched(HTML)
        result = material.prepare(self.session, receipt, 10, self.data['baseCommit'], vector_selection={'svgIndex': 0})
        changed = self.change_manifest(result['sourceMaterial'], lambda v: v.pop('vectorSelection'))
        with self.assertRaisesRegex(ValueError, 'reproducible extraction'):
            self.validate(changed, raw)


class FetchCache(unittest.TestCase):
    def setUp(self):
        self.fixture = wf_fixtures.SourceFixture()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_cached_fetch_reuses_validated_original_receipt_and_rejects_tamper(self):
        f = self.fixture
        result = {**f.receipt, 'receipt': str(f.session / f.receipt_link['path'])}
        with patch.object(workflow, '_context', return_value=(f.data, f.bank)), \
                patch('fetch_source.fetch', return_value=result) as fetch:
            first = workflow.fetch(f.session, 10, f.receipt['requestedUrl'])
            second = workflow.fetch(f.session, 10, f.receipt['requestedUrl'])
            self.assertEqual(fetch.call_count, 1)
            self.assertTrue(second['cached'])
            self.assertEqual(first['receipt'], second['receipt'])
            (f.session / f.body['path']).write_bytes(b'changed')
            with self.assertRaises(ValueError):
                workflow.fetch(f.session, 10, f.receipt['requestedUrl'])
            self.assertEqual(fetch.call_count, 1)

    def test_cache_rejects_receipt_byte_changes_even_with_identical_body(self):
        f = self.fixture
        result = {**f.receipt, 'receipt': str(f.session / f.receipt_link['path'])}
        with patch.object(workflow, '_context', return_value=(f.data, f.bank)), \
                patch('fetch_source.fetch', return_value=result) as fetch:
            workflow.fetch(f.session, 10, f.receipt['requestedUrl'])
            receipt = f.session / f.receipt_link['path']
            receipt.write_bytes(receipt.read_bytes() + b'\n')
            with self.assertRaisesRegex(ValueError, 'hash|digest|changed'):
                workflow.fetch(f.session, 10, f.receipt['requestedUrl'])
            self.assertEqual(fetch.call_count, 1)
