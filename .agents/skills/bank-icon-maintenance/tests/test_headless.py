"""Headless integration through session CLI; controlled exporter is not render proof."""
from session_fixture import make_session
import json
from pathlib import Path
import unittest

import test_attempts as attempts


EXPORTER = r'''#!/usr/bin/env python3
import hashlib, json, os, pathlib, shlex, sys
from datetime import datetime, timezone
from PIL import Image
assert not any(k in os.environ for k in ('DISPLAY', 'WAYLAND_DISPLAY', 'XAUTHORITY'))
assert os.environ['JAVA_TOOL_OPTIONS'] == '-Djava.awt.headless=true'
a = sys.argv[1:]
banks = pathlib.Path(next(x.split('=', 1)[1] for x in a if x.startswith('-PcatalogBanks=')))
id = int(next(x.split('=', 1)[1] for x in a if x.startswith('-PcatalogId=')))
args = shlex.split(next(x.split('=', 1)[1] for x in a if x.startswith('--args=')))
m = pathlib.Path(args[args.index('--manifest')+1]); out = pathlib.Path(args[args.index('--output')+1])
folder = next(p for p in banks.iterdir() if json.loads((p/'info.json').read_text())['id'] == id)
source = next((pathlib.Path(x.split('=',1)[1]) for x in a if x.startswith('-PcatalogCandidate=')), folder/'icon.svg')
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
xml = m.parent/'composeResources'/'drawable'/f'bank_{id}.xml'; xml.parent.mkdir(parents=True, exist_ok=True)
xml.write_bytes(b'<vector />'+source.read_bytes())
pins = {'converter':'com.android.tools:sdk-common:32.4.0','compose':'1.12.0'}
manifest = {'schemaVersion':1,'sourceRoot':str(banks.parent),'banksRoot':str(banks),'pins':pins,'entries':[{'id':id,'error':None,'sourceValidated':True,'sourcePath':str(source) if source.parent != folder else str(source.relative_to(banks.parent)), 'sourceSha256':sha(source),'xmlSha256':sha(xml),'resourceName':f'bank_{id}'}]}
m.write_text(json.dumps(manifest)); out.mkdir()
images=[]
for theme,bg in [('light','#FFFFFF'),('dark','#121212')]:
    image=Image.new('RGBA',(240,240), bg)
    image.putpixel((120,120),(int(sha(source)[:2],16),0,0,255))
    p=out/f'{theme}.png'; image.save(p)
    images.append({'theme':theme,'path':p.name,'sha256':sha(p),'background':bg})
result={'schemaVersion':1,'status':'success','id':id,'source':{'path':str(source),'sha256':sha(source)},'xml':{'resourceName':f'bank_{id}','sha256':sha(xml),'loadedSha256':sha(xml)},'manifest':{'path':str(m),'sha256':sha(m)},'renderer':'offscreen CPU Skia/painterResource','pins':pins,'runtime':{'compose':'1.12.0','skiko':'fixture','skia':'fixture','javaVersion':'21','osName':'fixture','osArch':'fixture'},'density':3,'sizePx':240,'iconSizeDp':54,'observedAt':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'images':images}
(out/'result.json').write_text(json.dumps(result))
print('controlled exporter completed')
'''


class HeadlessCLI(unittest.TestCase):
    setUp = attempts.AttemptCLI.setUp
    bank = attempts.AttemptCLI.bank
    commit = attempts.AttemptCLI.commit
    cli = attempts.AttemptCLI.cli
    evidence = attempts.AttemptCLI.evidence

    def exporter(self):
        catalog = self.root / 'catalog'
        catalog.mkdir()
        script = catalog / 'gradlew'
        script.write_text(EXPORTER)
        script.chmod(0o755)
        return catalog

    def export(self, *extra, ok=True):
        return self.cli('desktop-export', '--id', '10', '--catalog', str(self.catalog),
                        '--output', 'evidence/10/export', *extra, ok=ok)

    def test_current_export_is_evidence_not_verification_or_approval(self):
        started = make_session(self)
        self.catalog = self.exporter()
        prior = (self.session / 'session.json').read_bytes()
        result = self.export()
        self.assertEqual((self.session / 'session.json').read_bytes(), prior)
        desktop = result['desktop']
        self.assertEqual(desktop['renderer'], 'offscreen CPU Skia/painterResource')
        self.assertNotIn('visualComparison', desktop)
        desktop['visualComparison'] = 'Controlled test judgment, not an actual visual assessment'
        from datetime import datetime, timezone
        path = self.evidence(started, desktop=desktop,
            checkedAt=datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'))
        accepted = self.cli('continue', '--evidence', str(path))
        self.assertEqual(accepted['banks'][0]['point'], 'attempt-complete')
        self.assertNotIn('lastVerifiedAt', accepted['banks'][0]['state'])
        self.assertEqual((Path(started['worktree'])/'banks/Alpha_10-ru/icon.svg').read_bytes(),
                         (self.repo/'banks/Alpha_10-ru/icon.svg').read_bytes())

    def test_candidate_export_builds_bound_before_after_and_remains_pending(self):
        import test_candidates as candidates
        fixture = candidates.CandidateCLI('test_preparation_preserves_original_report_and_current_icon_pending_human_review')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.session = fixture.session
        self.catalog = self.exporter()
        prior = (self.session / 'session.json').read_bytes()
        result = self.export('--candidate', str(fixture.evidence / 'final.svg'))
        self.assertEqual((self.session / 'session.json').read_bytes(), prior)
        from datetime import datetime, timezone
        fixture.package['checkedAt'] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        fixture.package['desktop'] = {**result['desktop'], 'visualComparison': 'Controlled final judgment'}
        for theme in ('light', 'dark'):
            fixture.package['comparison'][theme].update(result['comparison'][theme])
        row = fixture.package['comparison']['light']
        original = row['base']
        row['base'] = row['source']
        fixture.update_package()
        fixture.cli('continue', '--candidate', str(fixture.package_path), ok=False)
        self.assertEqual((self.session / 'session.json').read_bytes(), prior)
        row['base'] = original
        fixture.update_package()
        prepared = fixture.cli('continue', '--candidate', str(fixture.package_path))
        proposal = prepared['banks'][0]['state']['proposals'][-1]
        self.assertEqual(proposal['humanReview'], 'pending')
        self.assertNotIn('approval', proposal)
        from PIL import Image
        for theme in ('light', 'dark'):
            row = result['comparison'][theme]
            with Image.open(self.session / row['beforeAfter']['path']) as pair:
                with Image.open(self.session / row['base']['path']) as base:
                    self.assertEqual(pair.crop((0, 0, 240, 240)).tobytes(), base.convert('RGBA').tobytes())
                with Image.open(self.session / row['candidate']['path']) as final:
                    self.assertEqual(pair.crop((240, 0, 480, 240)).tobytes(), final.convert('RGBA').tobytes())

    def test_rejects_mutated_export_bindings_and_preserves_session(self):
        import hashlib
        from datetime import datetime, timezone
        started = make_session(self)
        self.catalog = self.exporter()
        bundle = self.export()
        desktop = bundle['desktop']
        desktop['visualComparison'] = 'Controlled test judgment'
        receipt_path = self.session / desktop['invocation']['path']
        result_path = self.session / desktop['result']['path']
        receipt = json.loads(receipt_path.read_bytes())
        result = json.loads(result_path.read_bytes())
        prior = (self.session / 'session.json').read_bytes()
        cases = [('id', 20), ('status', 'failed'), ('schemaVersion', 2),
                 ('density', 2), ('sizePx', 54), ('iconSizeDp', 55),
                 ('renderer', 'window'), ('source', {'path': result['source']['path'], 'sha256': '0'*64}),
                 ('xml', {**result['xml'], 'loadedSha256': '0'*64}),
                 ('manifest', {**result['manifest'], 'sha256': '0'*64}),
                 ('images', result['images'][:1]), ('runtime', {**result['runtime'], 'compose': 'old'}),
                 ('pins', {**result['pins'], 'converter': 'old'})]
        for key, value in cases:
            with self.subTest(result=key):
                changed = {**result, key: value}
                result_path.write_text(json.dumps(changed))
                desktop['result']['sha256'] = hashlib.sha256(result_path.read_bytes()).hexdigest()
                updated = {**receipt, 'result': desktop['result']}
                receipt_path.write_text(json.dumps(updated))
                desktop['invocation']['sha256'] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
                path = self.evidence(started, desktop=desktop,
                    checkedAt=datetime.now(timezone.utc).isoformat().replace('+00:00','Z'))
                self.cli('continue', '--evidence', str(path), ok=False)
                self.assertEqual((self.session / 'session.json').read_bytes(), prior)
        result_path.write_text(json.dumps(result))
        desktop['result']['sha256'] = hashlib.sha256(result_path.read_bytes()).hexdigest()
        for key, value in [('baseCommit', 'f'*40), ('id', 20), ('baseIconHash', '0'*64),
                           ('exitCode', 2), ('command', ['not-the-exporter'])]:
            with self.subTest(invocation=key):
                receipt_path.write_text(json.dumps({**receipt, 'result': desktop['result'], key: value}))
                desktop['invocation']['sha256'] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
                path = self.evidence(started, desktop=desktop,
                    checkedAt=datetime.now(timezone.utc).isoformat().replace('+00:00','Z'))
                self.cli('continue', '--evidence', str(path), ok=False)
                self.assertEqual((self.session / 'session.json').read_bytes(), prior)

    def test_existing_output_is_never_overwritten(self):
        make_session(self)
        self.catalog = self.exporter()
        bundle = self.export()
        root = self.session / 'evidence/10/export'
        before = {str(p): p.read_bytes() for p in root.rglob('*') if p.is_file()}
        self.export(ok=False)
        self.assertEqual({str(p): p.read_bytes() for p in root.rglob('*') if p.is_file()}, before)


    def test_missing_partial_and_failed_export_never_create_observation(self):
        make_session(self)
        self.catalog = self.exporter()
        before = (self.session / 'session.json').read_bytes()
        for label, suffix in [('missing', "(out/'result.json').unlink()"),
                              ('partial', "(out/'dark.png').unlink()"),
                              ('failed', "sys.exit(2)")]:
            with self.subTest(failure=label):
                (self.catalog / 'gradlew').write_text(EXPORTER + '\n' + suffix + '\n')
                output = 'evidence/10/' + label
                self.cli('desktop-export', '--id', '10', '--catalog', str(self.catalog),
                         '--output', output, ok=False)
                self.assertFalse((self.session / output / 'desktop.json').exists())
                receipt = json.loads((self.session / output / 'invocation.json').read_bytes())
                self.assertEqual(receipt['exitCode'], 2 if label == 'failed' else 0)
                self.assertEqual((self.session / 'session.json').read_bytes(), before)

    def test_changed_or_missing_saved_files_reject_consumption(self):
        from datetime import datetime, timezone
        started = make_session(self)
        self.catalog = self.exporter()
        desktop = self.export()['desktop']
        desktop['visualComparison'] = 'Controlled judgment'
        prior = (self.session / 'session.json').read_bytes()
        manifest = self.session / desktop['manifest']
        files = [manifest, manifest.parent/'composeResources/drawable/bank_10.xml',
                 self.session / desktop['light']['path'], self.session / desktop['dark']['path'],
                 self.session / desktop['result']['path'], self.session / desktop['invocation']['path'],
                 Path(started['worktree']) / 'banks/Alpha_10-ru/icon.svg']
        for path in files:
            original = path.read_bytes()
            for remove in (False, True):
                with self.subTest(file=path.name, missing=remove):
                    if remove:
                        path.unlink()
                    else:
                        path.write_bytes(original + b'changed')
                    evidence = self.evidence(started, desktop=desktop,
                        checkedAt=datetime.now(timezone.utc).isoformat().replace('+00:00','Z'))
                    result = self.cli('continue', '--evidence', str(evidence), ok=path.name == 'icon.svg')
                    if path.name == 'icon.svg':
                        self.assertEqual(result['banks'][0]['point'], 'needs-human')
                        (self.session / 'session.json').write_bytes(prior)
                    self.assertFalse((self.session / 'evidence/10/validated.json').exists())
                    path.write_bytes(original)

    def test_first_icon_export_has_only_real_final_images_and_rejects_fabricated_base(self):
        import test_candidates as candidates
        from datetime import datetime, timezone
        from PIL import Image
        fixture = candidates.CandidateCLI('test_preparation_preserves_original_report_and_current_icon_pending_human_review')
        fixture.base_icon = None
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.session = fixture.session
        self.catalog = self.exporter()
        before = (self.session / 'session.json').read_bytes()
        blocked = self.export(ok=False)
        self.assertIn('No current icon.svg', blocked['error'])
        self.assertFalse((self.session / 'evidence/10/export').exists())
        result = self.export('--candidate', str(fixture.evidence / 'final.svg'))
        self.assertIsNone(result['desktop']['baseObservation'])
        self.assertFalse((self.session / 'evidence/10/export/base').exists())
        for theme in ('light', 'dark'):
            row = result['comparison'][theme]
            self.assertIsNone(row['base'])
            self.assertIsNone(row['beforeAfter'])
            self.assertEqual(row['candidate'], result['desktop'][theme])
            with Image.open(self.session / row['candidate']['path']) as final:
                self.assertEqual(final.size, (240, 240))
                self.assertEqual(final.getpixel((120, 120)), (int(candidates.digest(candidates.FINAL)[:2], 16), 0, 0, 255))
        self.assertEqual((self.session / 'session.json').read_bytes(), before)
        fixture.package['checkedAt'] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        fixture.package['desktop'] = {**result['desktop'], 'visualComparison': 'First actual final icon; no accepted icon existed'}
        for theme in ('light', 'dark'):
            fixture.package['comparison'][theme].update(result['comparison'][theme])
        fixture.package['comparison']['light']['base'] = result['desktop']['light']
        fixture.update_package()
        fixture.cli('continue', '--candidate', str(fixture.package_path), ok=False)
        self.assertEqual((self.session / 'session.json').read_bytes(), before)
        fixture.package['comparison']['light']['base'] = None
        fixture.update_package()
        prepared = fixture.cli('continue', '--candidate', str(fixture.package_path))
        self.assertEqual(prepared['banks'][0]['state']['proposals'][-1]['humanReview'], 'pending')
        self.assertFalse((Path(fixture.started['worktree']) / 'banks' / fixture.folder / 'icon.svg').exists())


if __name__ == '__main__':
    unittest.main()
