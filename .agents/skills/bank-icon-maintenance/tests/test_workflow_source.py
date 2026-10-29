"""Original-source bindings and real image composition, not model visual approval.

Synthetic saved HTTP receipts and window-render fixtures exercise real validators.
The optional librsvg case runs the installed executable, not a simulated renderer.
"""
import copy
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import source_evidence as se
import workflow
from evidence import RENDERER, sha, validate_attempt
from PIL import Image

PAST = '2020-01-01T00:00:00Z'
PAGE = 'https://bank.example/'
ASSET = PAGE + 'source.png'
BASE = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 54 54"><circle r="18"/></svg>'
FINAL = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 54 54"><circle r="19"/></svg>'


def png(color, size=(64, 64)):
    stream = io.BytesIO()
    Image.new('RGBA', size, color).save(stream, format='PNG')
    return stream.getvalue()


class SourceFixture(unittest.TestCase):
    base_icon = BASE

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='source-workflow-test-')
        self.addCleanup(tmp.cleanup)
        self.session = Path(tmp.name).resolve()
        self.data = {'baseCommit': 'b' * 40, 'worktree': str(self.session / 'worktree')}
        self.bank = {'id': 10, 'title': 'Example', 'folder': 'banks/Example_10-ru',
                     'iconHash': sha(self.base_icon) if self.base_icon is not None else None,
                     'point': 'identity-evidence', 'metadata': {'id': 10, 'title': 'Example'}}
        if self.base_icon is not None:
            self.put('worktree/' + self.bank['folder'] + '/icon.svg', self.base_icon)
        self.bank['metadataHash'] = self.put('worktree/' + self.bank['folder'] + '/info.json',
                                            json.dumps(self.bank['metadata']).encode())['sha256']
        self.body = self.put('evidence/10/fetch/attempt-1/body.bin', png('#dd2244'))
        self.receipt = {'schemaVersion': 1, 'id': 10, 'baseCommit': self.data['baseCommit'],
                        'status': 'fetched', 'requestedUrl': ASSET, 'finalUrl': ASSET, 'caRetry': 'not-needed',
                        'startedAt': PAST, 'finishedAt': PAST, 'body': {**self.body, 'bytes': len(png('#dd2244'))},
                        'attempts': [{'url': ASSET, 'directory': 'attempt-1', 'trust': 'default',
                                      'startedAt': PAST, 'finishedAt': PAST, 'exitCode': 0, 'httpStatus': 200,
                                      'command': ['/usr/bin/curl', '--disable', '--silent', '--show-error', '--proto', '=https',
                                                  '--connect-timeout', '10', '--max-time', '30', '--max-filesize', str(se.MAX_BYTES),
                                                  '--dump-header', str(self.session/'evidence/10/fetch/attempt-1/headers.txt'),
                                                  '--output', str(self.session/'evidence/10/fetch/attempt-1/body.bin'),
                                                  '--write-out', '%{http_code}\n%{content_type}\n', '--url', ASSET],
                                      'body': {'path': 'attempt-1/body.bin', 'sha256': self.body['sha256'],
                                               'bytes': len(png('#dd2244'))}}]}
        self.save_receipt()
        self.final = self.session / 'evidence/10/final.svg'
        self.put('evidence/10/final.svg', FINAL)

    def put(self, relative, content):
        path = self.session / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return {'path': relative, 'sha256': sha(content)}

    def save_receipt(self):
        self.receipt_link = self.put('evidence/10/fetch/receipt.json', json.dumps(self.receipt).encode())
        self.raw = {'path': self.body['path'], 'iconHash': self.body['sha256'], 'sourceUrl': PAGE,
                    'sourceAssetUrl': ASSET, 'rationale': 'Observed replacement', 'retrieval': self.receipt_link}

    def replace_source(self, content):
        self.body = self.put(self.body['path'], content)
        self.receipt['body'] = {**self.body, 'bytes': len(content)}
        self.receipt['attempts'][-1]['body'].update(sha256=sha(content), bytes=len(content))
        self.save_receipt()

    def validate_source(self, raw=None):
        return se.validate_source(self.session, raw or self.raw, self.data, self.bank, datetime.now(timezone.utc))

    def desktop_bundle(self):
        xml = self.put('evidence/10/generated/composeResources/drawable/bank_10.xml', b'<vector/>')
        manifest = {'schemaVersion': 1, 'sourceRoot': self.data['worktree'],
                    'banksRoot': self.data['worktree'] + '/banks',
                    'pins': {'converter': 'fixture', 'compose': 'fixture'},
                    'entries': [{'id': 10, 'sourcePath': str(self.final), 'sourceSha256': sha(FINAL),
                                 'resourceName': 'bank_10', 'sourceValidated': True, 'xmlSha256': xml['sha256']}]}
        m = self.put('evidence/10/generated/manifest.json', json.dumps(manifest).encode())
        desktop = {'renderer': RENDERER, 'observedAt': PAST, 'manifest': m['path'], 'manifestSha256': m['sha256']}
        comparison = {}
        for theme in se.BACKGROUNDS:
            desktop[theme] = self.put(f'evidence/10/final-{theme}.png', png('#44bb22', (240, 240)))
            if self.bank['iconHash'] is None:
                desktop['baseObservation'] = None
                comparison[theme] = {'base': None, 'candidate': desktop[theme], 'beforeAfter': None}
                continue
            before = self.put(f'evidence/10/base-{theme}.png', png('#2244bb', (240, 240)))
            pair = Image.new('RGBA', (480, 240))
            pair.paste(Image.open(self.session / before['path']), (0, 0))
            pair.paste(Image.open(self.session / desktop[theme]['path']), (240, 0))
            out = io.BytesIO(); pair.save(out, format='PNG')
            comparison[theme] = {'base': before, 'candidate': desktop[theme],
                                 'beforeAfter': self.put(f'evidence/10/base-final-{theme}.png', out.getvalue())}
        bundle = {'desktop': desktop, 'comparison': comparison}
        return self.put('evidence/10/bundle.json', json.dumps(bundle).encode()), bundle

    def make_view(self):
        reference = se.prepare_reference(self.session, self.raw, self.data, self.bank)
        bundle_link, bundle = self.desktop_bundle()
        view = se.prepare_comparison(self.session, self.raw, self.data, self.bank, self.final, bundle_link, reference)
        ref = se.read_json(self.session / reference['path'])
        payload = {'candidate': se.link(self.session, self.final),
                   'desktop': {**bundle['desktop'], 'visualComparison': 'Explicit fixture judgment'},
                   'comparison': {theme: {**bundle['comparison'][theme], 'source': ref['images'][theme]}
                                  for theme in se.BACKGROUNDS}}
        return view, reference, payload

    def test_receipt_binds_original_body_and_original_date(self):
        receipt, file = self.validate_source()
        self.assertEqual(file, self.session / self.body['path'])
        self.assertEqual(receipt['finishedAt'], PAST)
        changed = {**self.raw, 'path': 'evidence/10/final.svg', 'iconHash': sha(FINAL)}
        with self.assertRaisesRegex(ValueError, 'original retrieval body'):
            self.validate_source(changed)

    def test_copy_with_identical_bytes_cannot_replace_original_response(self):
        copy_link = self.put('evidence/10/working.png', (self.session / self.body['path']).read_bytes())
        with self.assertRaisesRegex(ValueError, 'original retrieval body'):
            self.validate_source({**self.raw, 'path': copy_link['path']})

    def test_body_receipt_wrong_id_url_base_and_future_are_rejected(self):
        original = copy.deepcopy(self.receipt)
        for change in ({'id': 11}, {'baseCommit': 'a' * 40}, {'status': 'failed'},
                       {'finalUrl': 'http://bank.example/source.png'}, {'finishedAt': '2999-01-01T00:00:00Z'},
                       {'requestedUrl': PAGE + 'different.png'}, {'caRetry': 'used'}):
            with self.subTest(change=change):
                self.receipt = {**copy.deepcopy(original), **change}; self.save_receipt()
                with self.assertRaises(ValueError): self.validate_source()

    def test_insecure_or_unobserved_retry_does_not_pass(self):
        self.receipt['attempts'][0]['command'].insert(2, '--insecure'); self.save_receipt()
        with self.assertRaises(ValueError): self.validate_source()
        self.receipt['attempts'][0]['command'].remove('--insecure')
        duplicate = copy.deepcopy(self.receipt['attempts'][0]); duplicate['directory'] = 'attempt-2'
        duplicate['command'] = [x.replace('/attempt-1/', '/attempt-2/') for x in duplicate['command']]
        self.receipt['attempts'].append(duplicate); self.save_receipt()
        with self.assertRaisesRegex(ValueError, 'redirect'): self.validate_source()

    def test_changed_original_bytes_or_receipt_fail(self):
        (self.session / self.body['path']).write_bytes(png('#ffffff'))
        with self.assertRaisesRegex(ValueError, 'hash mismatch'): self.validate_source()
        self.replace_source(png('#dd2244'))
        (self.session / self.receipt_link['path']).write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'): self.validate_source()

    def test_symlinks_and_cross_bank_receipts_fail(self):
        (self.session / self.body['path']).unlink()
        (self.session / self.body['path']).symlink_to(self.final)
        with self.assertRaisesRegex(ValueError, 'symlink'): self.validate_source()
        wrong = self.put('evidence/11/fetch/receipt.json', json.dumps(self.receipt).encode())
        with self.assertRaisesRegex(ValueError, 'another bank'): self.validate_source({**self.raw, 'retrieval': wrong})

    def test_source_reference_and_pairs_use_original_not_final(self):
        view, ref_link, payload = self.make_view()
        value = se.validate_comparison(self.session, view, self.raw, payload, self.data, self.bank, datetime.now(timezone.utc))
        reference = se.read_json(self.session / ref_link['path'])
        self.assertEqual(reference['retrievedAt'], PAST)
        self.assertNotIn('faithful', value)
        self.assertNotIn('suitable', value)
        with Image.open(self.session / value['pairs']['light']['path']) as pair:
            self.assertEqual(pair.getpixel((120, 120))[:3], (221, 34, 68))
            self.assertEqual(pair.getpixel((360, 120))[:3], (68, 187, 34))
        self.assertNotEqual(payload['comparison']['light']['source'], payload['comparison']['light']['candidate'])

    def test_relabelled_final_image_as_source_is_rejected(self):
        view, _, payload = self.make_view()
        payload['comparison']['light']['source'] = payload['comparison']['light']['candidate']
        with self.assertRaisesRegex(ValueError, 'replaced'):
            se.validate_comparison(self.session, view, self.raw, payload, self.data, self.bank, datetime.now(timezone.utc))

    def test_self_consistent_reference_rewrite_cannot_change_raster_source(self):
        ref = se.prepare_reference(self.session, self.raw, self.data, self.bank)
        value = se.read_json(self.session / ref['path'])
        raster = self.put(value['raster']['path'], png('#aabbcc'))
        value['raster'] = raster
        ref = self.put(ref['path'], json.dumps(value).encode())
        with self.assertRaisesRegex(ValueError, 'pixels differ'):
            se.validate_reference(self.session, ref, self.raw, self.data, self.bank, datetime.now(timezone.utc))

    def test_crop_retains_full_source_and_rejects_out_of_bounds(self):
        ref = se.prepare_reference(self.session, self.raw, self.data, self.bank,
                                   crop={'box': [0, 0, 32, 64], 'basis': 'Fixture symbol half'})
        value = se.validate_reference(self.session, ref, self.raw, self.data, self.bank, datetime.now(timezone.utc))
        with Image.open(self.session / value['raster']['path']) as image: self.assertEqual(image.size, (64, 64))
        with self.assertRaises(ValueError):
            se.prepare_reference(self.session, self.raw, self.data, self.bank,
                                 crop={'box': [0, 0, 100, 100], 'basis': 'Outside'})

    def test_ico_keeps_downloaded_bytes_and_decodes_actual_source(self):
        stream = io.BytesIO(); Image.new('RGBA', (64, 64), '#dd2244').save(stream, format='ICO')
        content = stream.getvalue(); self.replace_source(content)
        ref = se.prepare_reference(self.session, self.raw, self.data, self.bank)
        value = se.validate_reference(self.session, ref, self.raw, self.data, self.bank, datetime.now(timezone.utc))
        self.assertEqual(value['sourceFormat'], 'ICO')
        self.assertEqual((self.session / self.body['path']).read_bytes(), content)

    def test_source_svg_external_content_stops_before_renderer(self):
        for fragment in ('<image href="https://evil.invalid/x"/>', '<script/>', '<style>@import "x";</style>',
                         '<rect fill="url(https://evil.invalid/x)"/>', '<use href="file:///tmp/x"/>'):
            with self.subTest(fragment=fragment):
                self.replace_source(('<svg xmlns="http://www.w3.org/2000/svg">' + fragment + '</svg>').encode())
                with patch.object(se, '_run') as run, self.assertRaises(ValueError):
                    se.prepare_reference(self.session, self.raw, self.data, self.bank)
                run.assert_not_called()

    @unittest.skipUnless(shutil.which('rsvg-convert'), 'real librsvg is not installed')
    def test_real_librsvg_receipt_uses_downloaded_svg_not_working_file(self):
        self.replace_source(b'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64" fill="#dd2244"/></svg>')
        ref = se.prepare_reference(self.session, self.raw, self.data, self.bank)
        value = se.validate_reference(self.session, ref, self.raw, self.data, self.bank, datetime.now(timezone.utc))
        self.assertEqual(value['invocation']['command'][-1], str(self.session / self.body['path']))
        self.assertIn('version', value['invocation'])
        value['invocation']['command'][-1] = str(self.final)
        changed = self.put(ref['path'], json.dumps(value).encode())
        with self.assertRaisesRegex(ValueError, 'original downloaded SVG'):
            se.validate_reference(self.session, changed, self.raw, self.data, self.bank, datetime.now(timezone.utc))

    def notes(self):
        sources = [{'kind': kind, 'url': PAGE, 'assetUrl': ASSET if kind == 'official' else None,
                    'checkedAt': PAST, 'observation': 'Fixture observation', 'independenceGroup': kind}
                   for kind in ('official', 'zenplugins-issue', 'zenplugins-pr')]
        return {'outcome': 'candidate', 'reason': 'Source-supported change', 'nextAction': 'Prepare candidate',
                'sources': sources, 'assessment': {**{key: {'finding': 'confirmed', 'basis': 'Fixture'}
                       for key in ('identity', 'currency', 'variant')}, 'independenceRationale': 'Fixture', 'uncertainties': []},
                'platforms': [], 'recheckRequired': False, 'recheckRationale': 'Discovery only',
                'candidate': {'sourceUrl': PAGE, 'rationale': 'Fixture change'}}

    def test_attempt_builder_derives_bindings_and_never_writes_state(self):
        with patch.object(workflow, '_context', return_value=(self.data, self.bank)):
            result = workflow.assemble_attempt(self.session, 10, self.notes(), retrieval_path=self.receipt_link['path'])
        value = validate_attempt(self.session / result['input']['path'], self.data, self.bank)
        self.assertEqual(value['candidate']['path'], self.body['path'])
        self.assertEqual(value['candidate']['retrieval'], self.receipt_link)
        self.assertEqual(value['sources'][0]['checkedAt'], PAST)
        self.assertIsNone(value['desktop'])
        self.assertFalse((self.session / 'banks').exists())
        self.assertFalse((self.session / 'session.json').exists())

    def test_attempt_builder_refuses_mechanical_overrides_and_missing_receipt(self):
        with patch.object(workflow, '_context', return_value=(self.data, self.bank)):
            with self.assertRaises(ValueError): workflow.assemble_attempt(self.session, 10, self.notes())
            with self.assertRaises(ValueError):
                workflow.assemble_attempt(self.session, 10, {**self.notes(), 'iconHash': 'a'*64},
                                          retrieval_path=self.receipt_link['path'])

    def test_work_order_contains_exact_paths_without_mandatory_provider_search(self):
        with patch.dict('os.environ', {'BANK_ICON_RESEARCH_SEARCH_LIMIT': '3'}), \
                patch.object(workflow, '_context', return_value=(self.data, self.bank)):
            result = workflow.work_order(self.session, 10)
        order = se.read_json(self.session / result['workOrder']['path'])
        self.assertTrue(Path(order['paths']['evidenceDirectory']).is_dir())
        self.assertEqual(order['remainingSearchBudget'], 3)
        self.assertEqual(order['requiredSearches'], [])
        self.assertNotIn('ZenPlugins', order['instructions'])
        self.assertNotIn('humanReview', order)
        self.assertTrue(order['paths']['icon'].endswith(self.bank['folder'] + '/icon.svg'))

    def test_duplicate_json_and_evidence_alias_boundary(self):
        path = self.session / 'duplicate.json'; path.write_text('{"x":1,"x":2}')
        with self.assertRaisesRegex(ValueError, 'duplicate'): se.read_json(path)
        alias = self.session.parent / (self.session.name + '-alias')
        alias.symlink_to(self.session, target_is_directory=True); self.addCleanup(alias.unlink)
        resolved = workflow._path(self.session, str(alias / self.body['path']), str(alias))
        self.assertEqual(resolved, self.session / self.body['path'])
        with self.assertRaises(ValueError): workflow._path(self.session, 'evidence/10/../x')

    def test_first_icon_source_final_pixels_remain_independent_and_exact(self):
        (self.session / 'worktree' / self.bank['folder'] / 'icon.svg').unlink()
        self.bank['iconHash'] = None
        view, _, payload = self.make_view()
        checked = datetime.now(timezone.utc)
        value = se.validate_comparison(self.session, view, self.raw, payload, self.data, self.bank, checked)
        self.assertIsNone(payload['desktop']['baseObservation'])
        for theme in se.BACKGROUNDS:
            self.assertIsNone(payload['comparison'][theme]['base'])
            self.assertIsNone(payload['comparison'][theme]['beforeAfter'])
            with Image.open(self.session / value['pairs'][theme]['path']) as pair:
                self.assertEqual(pair.getpixel((120, 120))[:3], (221, 34, 68))
                self.assertEqual(pair.getpixel((360, 120))[:3], (68, 187, 34))
        pair_path = self.session / value['pairs']['light']['path']
        original_pair = pair_path.read_bytes()
        for x in (120, 360):
            with self.subTest(changed_half=x):
                with Image.open(io.BytesIO(original_pair)) as original:
                    changed = original.convert('RGBA')
                changed.putpixel((x, 120), (1, 2, 3, 255))
                stream = io.BytesIO(); changed.save(stream, format='PNG')
                forged = copy.deepcopy(value)
                forged['pairs']['light'] = self.put(str(pair_path.relative_to(self.session)), stream.getvalue())
                forged_view = self.put('evidence/10/forged-comparison.json', json.dumps(forged).encode())
                with self.assertRaisesRegex(ValueError, 'pixels differ'):
                    se.validate_comparison(self.session, forged_view, self.raw, payload, self.data, self.bank, checked)
        pair_path.write_bytes(original_pair)
        payload['comparison']['light']['source'] = payload['comparison']['light']['candidate']
        with self.assertRaisesRegex(ValueError, 'replaced'):
            se.validate_comparison(self.session, view, self.raw, payload, self.data, self.bank, checked)



if __name__ == '__main__': unittest.main()
