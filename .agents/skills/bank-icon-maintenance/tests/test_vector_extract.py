"""Original vector extraction with real bounded worker and replayed selectors."""
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import source_material as sm
import vector_extract as ve

INLINE = b'<!doctype html><html><body><svg id="logo" viewBox="0 0 24 24"><path fill="#123456" d="M1 1h10v10H1z"/></svg></body></html>'
SPRITE = b'<svg xmlns="http://www.w3.org/2000/svg" style="display:none"><defs><linearGradient id="paint"><stop stop-color="#123456"/></linearGradient></defs><symbol id="logo" viewBox="0 0 24 24"><path fill="url(#paint)" d="M1 1h10v10H1z"/></symbol><symbol id="other" viewBox="0 0 1 1"><circle r="1"/></symbol></svg>'


class VectorExtract(unittest.TestCase):
    def test_inline_listing_is_not_an_automatic_logo_choice(self):
        info, value = sm.decode(INLINE, [])
        self.assertIsNone(value)
        self.assertEqual(info['vectors'], [{'index': 0, 'id': 'logo', 'viewBox': '0 0 24 24', 'symbols': []}])

    def test_inline_id_and_index_preserve_original_geometry(self):
        for selector in ({'svgIndex': 0}, {'svgId': 'logo'}):
            with self.subTest(selector=selector):
                info, value = sm.decode(INLINE, [], selector)
                root = ET.fromstring(value)
                self.assertEqual(root.tag, ve.NS+'svg')
                self.assertEqual(root.find(ve.NS+'path').attrib, {'fill': '#123456', 'd': 'M1 1h10v10H1z'})
                self.assertEqual(info['steps'][0]['selection'], selector)
                self.assertEqual(info['steps'][0]['inputSha256'], hashlib.sha256(INLINE).hexdigest())

    def test_sprite_preserves_viewport_and_shared_defs_without_inventing_backing(self):
        info, value = sm.decode(SPRITE, [], {'symbolId': 'logo'})
        root = ET.fromstring(value)
        self.assertEqual(root.get('viewBox'), '0 0 24 24')
        self.assertIsNone(root.get('style'))
        self.assertIsNotNone(root.find('.//'+ve.NS+'linearGradient'))
        self.assertEqual(len(list(root.iter(ve.NS+'path'))), 1)
        self.assertEqual(info['format'], 'svg')

    def test_nested_container_and_inline_selection_are_one_lineage(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as z: z.writestr('brand/page.html', INLINE)
        info, value = sm.decode(stream.getvalue(), ['brand/page.html'], {'svgId': 'logo'})
        self.assertEqual([step['operation'] for step in info['steps']], ['extract-member', 'extract-vector'])
        self.assertIn(b'M1 1h10v10H1z', value)

    def test_ambiguous_missing_or_bad_selector_fails(self):
        inputs = [(INLINE, {'svgId': 'missing'}), (INLINE, {'svgIndex': -1}),
                  (INLINE, {'svgIndex': True}), (INLINE, {'svgId': 'logo', 'svgIndex': 0}),
                  (SPRITE, {'symbolId': 'missing'}), (SPRITE, {'svgIndex': 0}),
                  (INLINE.replace(b'</body>', INLINE.split(b'<body>')[1].split(b'</body>')[0]+b'</body>'), {'svgId': 'logo'})]
        for value, selector in inputs:
            with self.subTest(selector=selector), self.assertRaises(ValueError): sm.decode(value, [], selector)

    def test_active_raster_and_unresolved_dependencies_are_not_extracted(self):
        for replacement in (b'<script/>', b'<image href="data:image/png;base64,AA=="/>',
                            b'<use href="#missing"/>', b'<path fill="currentColor"/>', b'<path fill="url(#missing)"/>'):
            value = INLINE.replace(b'<path fill="#123456" d="M1 1h10v10H1z"/>', replacement)
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                sm.decode(value, [], {'svgIndex': 0})

    def test_external_html_css_and_inherited_sprite_context_are_limitations(self):
        for value in (INLINE.replace(b'<body>', b'<body style="fill:red">'),
                      INLINE.replace(b'<body>', b'<head><link rel="stylesheet" href="/style.css"></head><body>'),
                      INLINE.replace(b'<body>', b'<style>path{fill:red}</style><body>')):
            with self.assertRaisesRegex(ValueError, 'style context'): sm.decode(value, [], {'svgId': 'logo'})
        with self.assertRaisesRegex(ValueError, 'inherited presentation'):
            sm.decode(SPRITE.replace(b'style="display:none"', b'fill="red"'), [], {'symbolId': 'logo'})

    def test_real_isolated_worker_reproduces_selector_and_detects_input_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp).resolve()/'page.html'; path.write_bytes(INLINE)
            digest = hashlib.sha256(INLINE).hexdigest()
            output = path.with_name('material.svg')
            result = sm.inspect(path, digest, output=output, vector_selection={'svgId': 'logo'})
            self.assertEqual(result['sha256'], hashlib.sha256(output.read_bytes()).hexdigest())
            self.assertEqual(result, sm.inspect(path, digest, vector_selection={'svgId': 'logo'}))
            path.write_bytes(INLINE+b'changed')
            with self.assertRaisesRegex(ValueError, 'download changed'):
                sm.inspect(path, digest, vector_selection={'svgId': 'logo'})
