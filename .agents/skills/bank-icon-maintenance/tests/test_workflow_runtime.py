"""Finite orchestration using controlled processes; no hosted or bank publication."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import workflow_runtime as rt
import android_readiness as android
import workflow


class Runtime(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='workflow-process-test-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.session = self.root / 'session with spaces'; self.session.mkdir()
        self.script = self.root / 'controlled_session.py'

    def program(self, code):
        self.script.write_text(code)
        return patch.object(rt, 'CLI', self.script)

    def test_complete_json_is_saved_and_summary_retains_stops(self):
        value = {'status': 'prepared', 'packageHash': 'a'*64,
                 'package': {'large': 'x'*100000}, 'receipt': {'status': 'outcome-unknown', 'nextAction': 'reconcile'},
                 'publicationAuthority': {'status': 'covered'}, 'localSync': {'status': 'warning'},
                 'banks': [{'id': 10, 'point': 'needs-human', 'reason': 'Exact question', 'state': {'large':'x'*100000}}]}
        with self.program('import json; print(json.dumps(' + repr(value) + '))'):
            result = rt.execute(self.session, ['state-show', '--package', 'a'*64])
        self.assertEqual(json.loads(Path(result['resultFile']['path']).read_bytes()), value)
        self.assertEqual(result['result']['receipt']['status'], 'outcome-unknown')
        self.assertEqual(result['result']['banks'][0]['point'], 'needs-human')
        self.assertNotIn('package', result['result'])
        self.assertLess(len(json.dumps(result)), 5000)
        self.assertTrue(result['fullPackageRequiredForReview'])

    def test_full_show_does_not_call_command_twice(self):
        counter = self.root / 'count'
        with self.program(f"from pathlib import Path\np=Path({str(counter)!r}); p.write_text(p.read_text()+'x' if p.exists() else 'x')\nprint('{{\"package\":{{\"exact\":true}},\"status\":\"prepared\"}}')"):
            result = rt.execute(self.session, ['asset-show', '--package', 'a'*64], full=True)
        self.assertEqual(counter.read_text(), 'x')
        self.assertTrue(result['result']['package']['exact'])

    def test_failed_or_unparseable_output_is_not_success(self):
        for output, code in [('not JSON', 0), ('{"error":"denied"}', 1), ('[]', 0)]:
            with self.subTest(output=output), self.program(f'import sys; print({output!r}); sys.exit({code})'):
                result = rt.execute(self.session, ['asset-publish', '--package', 'a'*64])
                self.assertNotEqual(result['exitCode'], 0)
                self.assertNotEqual(result['result'].get('status'), 'mr-confirmed')

    def test_timeout_terminates_children_and_preserves_output_without_retry(self):
        code = "import subprocess,sys,time\nprint('partial',flush=True)\nsubprocess.Popen([sys.executable,'-c','import time; time.sleep(20)'])\ntime.sleep(20)"
        started = time.monotonic()
        with self.program(code), patch.object(rt, 'TIMEOUT', 1.5):
            result = rt.execute(self.session, ['asset-publish', '--package', 'a'*64])
        self.assertLess(time.monotonic()-started, 6)
        self.assertEqual(result['result']['status'], 'command-timeout')
        self.assertIn('reconcile', result['result']['nextAction'])
        self.assertIn('partial', Path(result['resultFile']['path']).read_text())
        self.assertEqual(len(list((self.session / 'tool-results').iterdir())), 1)

    def test_single_coordinator_survives_backgrounded_shell_until_exit(self):
        with rt.coordinator(self.session):
            with self.assertRaisesRegex(ValueError, 'still running'):
                with rt.coordinator(self.session): pass
        with rt.coordinator(self.session): pass

    def test_writer_lock_and_output_symlinks_are_not_followed(self):
        outside = self.root / 'untouched'; outside.write_text('keep')
        (self.session / '.workflow-writer.lock').symlink_to(outside)
        with self.assertRaises(OSError):
            with rt.coordinator(self.session): pass
        self.assertEqual(outside.read_text(), 'keep')
        (self.session / 'tool-results').symlink_to(self.root, target_is_directory=True)
        with self.program('print("{}")'), self.assertRaisesRegex(ValueError, 'symlink'):
            rt.execute(self.session, ['continue'])

    def test_serial_asset_publication_waits_for_each_terminal_process(self):
        log = self.root / 'order'
        script = f'''import json,sys,time
from pathlib import Path
p=Path({str(log)!r})
pkg=sys.argv[sys.argv.index('--package')+1]
with p.open('a') as f: f.write('start '+pkg+'\\n')
time.sleep(.05)
with p.open('a') as f: f.write('end '+pkg+'\\n')
print(json.dumps({{'status':'mr-confirmed'}}))
'''
        packages = ['a'*64, 'b'*64, 'c'*64]
        with self.program(script): result = rt.publish_assets(self.session, packages)
        self.assertEqual(result['status'], 'mr-confirmed')
        self.assertEqual(log.read_text().splitlines(), [word+' '+p for p in packages for word in ('start','end')])

    def test_partial_unknown_denied_and_missing_authority_stop_the_batch(self):
        for stop in ('outcome-unknown', 'publication-failed', 'awaiting-asset-authorization', 'conflict'):
            with self.subTest(stop=stop), self.program(f'import json; print(json.dumps({{"status":{stop!r}}}))'):
                result = rt.publish_assets(self.session, ['a'*64,'b'*64])
                self.assertEqual(len(result['results']), 1)
                self.assertEqual(result['unattempted'], ['b'*64])

    def test_no_arbitrary_commands_merge_retargeting_or_cross_package_grants(self):
        for args in (['merge'], ['start'], ['continue','--session','other'], ['state-publish','--session=other']):
            with self.assertRaises(ValueError): rt.execute(self.session, args)
        for packages in ([True], ['a'*64,'a'*64], ['a'*64,'b'*64]):
            with self.assertRaises(ValueError): rt.publish_assets(self.session, packages, authorization='exact.json')
        self.assertFalse((self.session / 'tool-results').exists())

    def test_help_and_errors_are_machine_readable(self):
        for module, argv in ((workflow, ['unknown']), (android, ['--bad'])):
            stream = io.StringIO()
            with redirect_stdout(stream): code = module.main(argv)
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(stream.getvalue())['status'], 'blocked')


class AndroidPath(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.sdk = Path(temp.name)
        self.bin = self.sdk / 'standalone-bin'; self.bin.mkdir()
        environment = patch.dict(os.environ, {'PATH': str(self.bin)})
        environment.start()
        self.addCleanup(environment.stop)
        for name in ('platform-tools/adb', 'platforms/android-35/android.jar',
                     'build-tools/35.0.0/aapt2','build-tools/35.0.0/d8','build-tools/35.0.0/zipalign'):
            p=self.sdk/name; p.parent.mkdir(parents=True,exist_ok=True); p.write_text('#!/bin/sh\necho device\n'); p.chmod(0o700)

    def installed_avd_prerequisites(self):
        for name in ('emulator/emulator', 'system-images/android-35/default/x86_64/package.xml'):
            p = self.sdk / name; p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('installed'); p.chmod(0o700)

    def new_avd(self, **kwargs):
        return android.probe(self.sdk, 'android-35', '35.0.0',
                             system_image='system-images;android-35;default;x86_64', **kwargs)

    def test_existing_device_does_not_require_android_cli_or_images(self):
        result = android.probe(self.sdk,'android-35','35.0.0',serial='emulator-5554',
                               android_cli=str(self.bin / 'missing'))
        self.assertEqual(result['status'],'tools-ready')
        self.assertNotIn('androidCli',result['tools'])
        self.assertEqual(result['buildCache'],'not-verified')
        self.assertEqual(result['render'],'not-tested')

    def test_new_avd_path_requires_only_installed_prerequisites(self):
        with patch.object(android.subprocess, 'run') as run:
            result = self.new_avd()
        run.assert_not_called()
        self.assertEqual(set(result['missing']), {'androidCli','emulator','systemImage'})
        self.assertIsNone(result['tools']['androidCli'])
        self.assertEqual(result['status'],'unavailable')
        self.assertFalse((self.sdk/'system-images').exists())

    def test_standalone_android_cli_on_path_is_ready_without_running_it(self):
        self.installed_avd_prerequisites()
        cli = self.bin / 'android'; cli.write_text('not a runnable CLI probe'); cli.chmod(0o700)
        with patch.object(android.subprocess, 'run') as run:
            result = self.new_avd()
        run.assert_not_called()
        self.assertEqual(result['status'], 'tools-ready')
        self.assertEqual(result['missing'], [])
        self.assertEqual(result['tools']['androidCli'], str(cli))

    def test_missing_or_nonexecutable_override_does_not_fall_back_to_path(self):
        self.installed_avd_prerequisites()
        default = self.bin / 'android'; default.write_text('installed'); default.chmod(0o700)
        override = self.sdk / 'custom-cli'; override.write_text('not executable'); override.chmod(0o600)
        for cli in (self.sdk / 'missing-cli', override):
            with self.subTest(cli=cli), patch.object(android.subprocess, 'run') as run:
                result = self.new_avd(android_cli=str(cli))
            run.assert_not_called()
            self.assertEqual(result['status'], 'unavailable')
            self.assertEqual(result['missing'], ['androidCli'])
            self.assertIsNone(result['tools']['androidCli'])

    def test_explicit_cli_path_is_accepted_by_command_line(self):
        self.installed_avd_prerequisites()
        cli = self.sdk / 'custom-cli'; cli.write_text('installed'); cli.chmod(0o700)
        stream = io.StringIO()
        with redirect_stdout(stream), patch.object(android.subprocess, 'run') as run:
            code = android.main(['--sdk-root', str(self.sdk), '--platform', 'android-35',
                                 '--build-tools', '35.0.0', '--system-image',
                                 'system-images;android-35;default;x86_64', '--android-cli', str(cli)])
        run.assert_not_called()
        self.assertEqual(code, 0)
        result = json.loads(stream.getvalue())
        self.assertEqual(result['status'], 'tools-ready')
        self.assertEqual(result['tools']['androidCli'], str(cli))

    def test_unready_selected_device_is_not_replaced_by_another(self):
        with patch.object(android.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'offline','')) as run:
            result = android.probe(self.sdk,'android-35','35.0.0',serial='physical-one')
        self.assertEqual(result['status'],'unavailable')
        self.assertEqual(run.call_count,1)
        self.assertEqual(run.call_args.args[0][-3:], ['-s','physical-one','get-state'])

    def test_missing_build_tool_skips_without_any_install_or_device_query(self):
        (self.sdk/'build-tools/35.0.0/aapt2').unlink()
        with patch.object(android.subprocess,'run') as run:
            result = android.probe(self.sdk,'android-35','35.0.0',serial='emulator-5554')
        self.assertEqual(result['missing'], ['aapt2']); run.assert_not_called()

if __name__ == '__main__': unittest.main()
