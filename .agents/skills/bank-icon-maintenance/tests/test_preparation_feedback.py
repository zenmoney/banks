"""Public diagnostics, blank admission inputs, and bounded source discovery.

End-to-end preparation/receipt cases live in test_preparation_feedback_integration;
archive safety and publication integrity are tested by their owning suites.
"""
import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import admission
import research
import review_materials as review
import source_evidence as source
import source_listing as listing


def details():
    return {'sources': [{'observation': 'Observed symbol', 'independenceGroup': 'official'}],
            'assessment': {**{name: {'finding': 'confirmed', 'basis': 'Observed'}
                              for name in ('identity', 'currency', 'variant')},
                           'independenceRationale': 'Same source', 'uncertainties': []},
            'candidate': {'rationale': 'Supported', 'transforms': ['None']},
            'visualComparison': 'Compared on both themes',
            'platforms': [{'target': 'android-compose', 'status': 'not-tested', 'available': False,
                           'observation': 'Tooling unavailable'}],
            'admission': {'judgments': {'concerns': [{'kind': 'platform-unavailable', 'basis': 'Tooling unavailable'}]}}}


class PublicPreflight(unittest.TestCase):
    def project(self, value, overrides=None, **kwargs):
        return review._project_narratives(value, Path('/tmp/fixture-session'),
            [('evidence/10/raw.svg', {'name': 'source.svg', 'sha256': 'a' * 64})], overrides, **kwargs)

    def test_diagnostics_cover_late_fields_and_never_echo_values_or_untrusted_keys(self):
        value = details()
        secret = 'ghp_' + 'x' * 30
        value['sources'][0]['observation'] = 'Authorization: Bearer ' + secret
        value['platforms'][0]['observation'] = 'Missing /private/sdk'
        value['admission']['judgments']['concerns'][0]['basis'] = 'Missing /private/jdk'
        value['taskResponse'] = [{'quote': 'See /private/reference', 'resolution': 'bad\tcontrol'}]
        value['metadata'] = {secret: '/private/secret'}
        original = copy.deepcopy(value)
        with self.assertRaises(review.PublicPreflightError) as caught:
            self.project(value, {secret: secret}, title='Bank /private/title')
        issues = caught.exception.issues
        for field, code in (
                ('sources.0.observation', 'credential-like-text'),
                ('platforms.0.observation', 'local-path'),
                ('admission.judgments.concerns.0.basis', 'local-path'),
                ('taskResponse.0.quote', 'local-path'),
                ('taskResponse.0.resolution', 'control-character'), ('title', 'local-path')):
            self.assertIn({'field': field, 'code': code}, issues)
        self.assertTrue(any('<key-' in row['field'] for row in issues))
        for rendered in (str(caught.exception), json.dumps(issues)):
            self.assertNotIn(secret, rendered)
            self.assertNotIn('/private', rendered)
        self.assertEqual(value, original)

    def test_public_override_cannot_hide_admission_or_forge_a_platform_pass(self):
        value = details()
        value['platforms'][0]['observation'] = 'Missing /private/sdk'
        original = copy.deepcopy(value)
        overrides = {'platforms.0.observation': 'Android tooling unavailable; not tested.'}
        public = self.project(value, overrides)
        self.assertEqual(public['platforms'][0], {**value['platforms'][0], 'observation': overrides['platforms.0.observation']})
        self.assertEqual(value, original)
        key = 'admission.judgments.concerns.0.basis'
        value['admission']['judgments']['concerns'][0]['basis'] = 'Missing /private/sdk'
        with self.assertRaises(review.PublicPreflightError) as caught:
            self.project(value, {**overrides, key: 'No concern'})
        self.assertEqual({(row['field'], row['code']) for row in caught.exception.issues},
                         {(key, 'local-path'), ('publicText.' + key, 'not-editable-narrative')})

    def test_render_rechecks_late_public_fields(self):
        value = details()
        value['admission']['judgments']['concerns'][0]['basis'] = 'ghp_' + 'x' * 30
        with self.assertRaises(review.PublicPreflightError) as caught:
            review.render({'details': value, 'files': []}, 'fixture', 'banks', 'f' * 40)
        self.assertIn({'field': 'admission.judgments.concerns.0.basis', 'code': 'credential-like-text'},
                      caught.exception.issues)


class AdmissionContract(unittest.TestCase):
    def test_template_exposes_choices_without_filling_judgments(self):
        import workflow
        import workflow_navigation as navigation
        with tempfile.TemporaryDirectory() as root:
            session = Path(root)
            for present in (False, True):
                with self.subTest(baseline_present=present):
                    bank = {'id': 10, 'iconHash': 'a' * 64 if present else None,
                            'point': 'waiting-proposal', 'report': 'reports/10.json',
                            'state': {'proposals': [{'reviewPackage': {'path': 'fixture', 'sha256': 'b' * 64}}]}}
                    before = copy.deepcopy(bank)
                    with patch.object(workflow, '_context', return_value=({'baseCommit': 'c' * 40}, bank)), \
                         patch.object(navigation.feedback_evidence, 'required_for', return_value=False):
                        result = navigation.notes_template(session, 10, 'admission')
                    contract = result['admissionSchema']
                    self.assertIn('current-use', contract['enums']['record.basisKind'])
                    self.assertIn('absent', contract['enums']['change.baselineAttribution'])
                    self.assertEqual('first-addition' in contract['baselineChoices']['change.kind'], not present)
                    self.assertEqual('absent' in contract['baselineChoices']['change.baselineAttribution'], not present)
                    self.assertIn('record.purposeEvidence', contract['conditions']['historical']['requiredNonempty'])
                    self.assertIn('change.transitionSourceRefs', contract['conditions']['rebrand']['requiredNonempty'])
                    notes = source.read_json(source.linked(session, result['notes'], 'notes'))
                    self.assertEqual(notes, admission.template())
                    self.assertIsNone(notes['record']['purpose'])
                    self.assertIsNone(notes['asset']['originalVector']['status'])
                    self.assertTrue(all(row['status'] is None for row in notes['quality'].values()))
                    self.assertNotIn('admissionSchema', notes)
                    self.assertEqual(bank, before)


class HostHints(unittest.TestCase):
    def test_website_hints_preserve_metadata_without_inventing_observed_urls(self):
        url = 'https://www.example.com/Brand/Logo?lang=en'
        for raw, host, official in (
                ('zen.com', 'zen.com', None), (url, 'www.example.com', url),
                ('ZEN.COM.', 'zen.com', None), ('пример.рф', 'xn--e1afmkfd.xn--p1ai', None),
                ('http://example.com', 'example.com', 'http://example.com')):
            with self.subTest(raw=raw):
                bank = {'metadata': {'title': 'Example', 'url': raw}}
                before = copy.deepcopy(bank)
                result = research.plan(bank)
                self.assertEqual(result['officialHost'], host)
                self.assertEqual(result['officialUrl'], official)
                self.assertEqual(result['officialQueries'], [f'site:{host} logo brand press media'])
                self.assertEqual(bank, before)
        for raw in ('http://example.com/logo.svg', 'zen.com'):
            with self.subTest(retrieval=raw), self.assertRaises(ValueError):
                source._https(raw)

    def test_invalid_host_hints_never_become_official_queries(self):
        for raw in (None, '', 123, 'zen.com/path', '//zen.com', 'user@zen.com', 'zen.com:443',
                    '/tmp/icon.svg', 'file:///tmp/icon.svg', 'ftp://zen.com', 'https://user:pass@zen.com',
                    'https://zen.com:bad', 'https://[bad', 'zen.com\n', 'ze n.com', '-zen.com',
                    'zen..com', 'localhost', '127.0.0.1', 'foo.local', 'a' * 64 + '.com',
                    'zen.com?query', 'zen.com#tag', 'zen.com\\bad', 'zen.com%00'):
            with self.subTest(raw=raw):
                result = research.plan({'metadata': {'title': 'Example', 'url': raw}})
                self.assertEqual(result['officialQueries'], [])
                self.assertIsNone(result['officialUrl'])


class ArchiveViews(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.session = Path(tmp.name)
        self.link = {'path': 'evidence/10/source-material-test/listing.json', 'sha256': 'a' * 64}
        self.doc = {'selection': [], 'download': {'sha256': 'b' * 64},
                    'decoded': {'kind': 'selection-required', 'format': 'zip', 'steps': [],
                        'members': [{'name': f'variant-{n:03d}.svg', 'bytes': 20, 'directory': False,
                                     'encrypted': False} for n in range(400)]}}

    def page(self, **kwargs):
        return listing._page(self.session, self.link, self.doc, **kwargs)

    def test_pages_are_bounded_and_preserve_every_exact_member_name(self):
        seen, cursor = [], 0
        while cursor is not None:
            result = self.page(offset=cursor)
            self.assertLessEqual(result['returned'], listing.DEFAULT_LIMIT)
            self.assertLessEqual(listing._size(result), listing.MAX_RESPONSE)
            seen.extend(row['name'] for row in result['members'])
            following = result['nextOffset']
            if following is not None:
                self.assertGreater(following, cursor)
                self.assertEqual(result['nextPageArgv'][-2:], ['--offset', str(following)])
            cursor = following
        self.assertEqual(seen, [row['name'] for row in self.doc['decoded']['members']])

    def test_filter_is_literal_and_does_not_select_material(self):
        result = self.page(name_contains='VARIANT-01')
        self.assertEqual(result['matchedMembers'], 10)
        self.assertEqual(result['returned'], 10)
        self.assertIsNone(result['nextOffset'])
        self.assertEqual(self.page(name_contains='*.svg')['matchedMembers'], 0)
        self.assertNotIn('material', result)

    def test_byte_limit_handles_long_unicode_names_and_invalid_pagination(self):
        for n, row in enumerate(self.doc['decoded']['members']):
            row['name'] = f'{n}-' + 'банк' * 100 + '.svg'
        result = self.page(limit=100)
        self.assertTrue(0 < result['returned'] < 100)
        self.assertLessEqual(listing._size(result), listing.MAX_RESPONSE)
        self.assertEqual(result['nextOffset'], result['returned'])
        for kw in ({'offset': -1}, {'offset': 401}, {'offset': True}, {'limit': 0}, {'limit': 101},
                   {'limit': True}, {'name_contains': '\n'}, {'name_contains': 'a' * 257}):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                self.page(**kw)
        self.assertEqual(self.page(offset=400)['members'], [])

    def test_listing_and_download_tampering_are_rejected_without_writing_state(self):
        download = self.session / 'evidence/10/download.bin'
        download.parent.mkdir(parents=True)
        download.write_bytes(b'fixture resource; decoding is covered by the integration test')
        root = self.session / 'evidence/10/source-material-test'
        root.mkdir()
        receipt_link = {'path': 'evidence/10/receipt.json', 'sha256': 'c' * 64}
        receipt = {'body': {'sha256': source.sha(download.read_bytes())}}
        with patch.object(source, 'retrieval', return_value=(receipt, download)):
            result = listing.save_listing(self.session, root, receipt_link, receipt, download,
                                          10, 'a' * 40, [], self.doc['decoded'])
            before = (root / 'listing.json').read_bytes()
            full = source.read_json(source.linked(self.session, result['listing'], 'listing'))
            self.assertEqual(len(full['decoded']['members']), 400)
            second = listing.page(self.session, result['listing'], offset=result['nextOffset'])
            self.assertEqual(second['members'][0]['name'], 'variant-020.svg')
            self.assertEqual((root / 'listing.json').read_bytes(), before)
            download.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                listing.page(self.session, result['listing'])
            (root / 'listing.json').write_text('{}')
            with self.assertRaises(ValueError):
                listing.page(self.session, result['listing'])
        self.assertFalse((self.session / 'banks').exists())

    def test_listing_cli_errors_are_structured_without_echoing_input(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = listing.main(['--unknown', 'secret'])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out.getvalue())['status'], 'blocked')
        self.assertNotIn('secret', out.getvalue())
        self.assertFalse((self.session / 'evidence').exists())


if __name__ == '__main__':
    unittest.main()
