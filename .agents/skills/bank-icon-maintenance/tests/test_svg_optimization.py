"""Real helper/CLI tests; synthetic optimizer output is not visual proof.

Real-SVGO tests use the installed SVGO_BINARY or PATH tool; CI supplies test tooling.
No test installs tools, publishes remote state, or executes maintenance itself.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT.parents[2] / "svgo.config.mjs"
RUNNER = ROOT / "scripts/optimize_svg.py"
sys.path.insert(0, str(ROOT / "scripts"))
import optimize_svg

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><path d="M0 0h32v32H0z"/></svg>\n'








class OptimizerCLI(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="svgo-helper-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.session = self.root / "session with spaces"
        self.worktree = self.session / "worktree"
        self.worktree.mkdir(parents=True)
        self.git("init", "-q")
        (self.worktree / "svgo.config.mjs").write_bytes(CONFIG.read_bytes())
        self.git("add", "svgo.config.mjs")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@localhost", "commit", "-qm", "config")
        self.session_file = self.session / "session.json"
        self.data = {"worktree": str(self.worktree), "baseCommit": self.git("rev-parse", "HEAD").strip()}
        self.session_file.write_text(json.dumps(self.data))
        self.evidence = self.session / "evidence/10"
        self.evidence.mkdir(parents=True)
        self.source = self.evidence / "working.svg"
        self.source.write_bytes(SVG)
        self.output = self.evidence / "trial"
        self.binary = self.root / "svgo"
        self.binary.write_text(
            f"#!{sys.executable}\n"
            "import os, sys\nfrom pathlib import Path\n"
            "if sys.argv[1:] == ['--version']:\n"
            "    if os.environ.get('TEST_PROBE_FAIL'): sys.exit(17)\n"
            "    print(os.environ.get('TEST_SVGO_VERSION', '4.0.2')); sys.exit(0)\n"
            "assert sys.argv[1] == '--config'\n"
            "assert sys.argv[3:] == ['--input', '-', '--output', '-', '--quiet']\n"
            "data = sys.stdin.buffer.read()\n"
            "if os.environ.get('TEST_MUTATE'): Path(os.environ['TEST_MUTATE']).write_bytes(b'changed')\n"
            "if not os.environ.get('TEST_EMPTY'): sys.stdout.buffer.write(data)\n"
            "print('controlled CLI, not real optimization', file=sys.stderr)\n"
            "sys.exit(int(os.environ.get('TEST_SVGO_EXIT', '0')))\n"
        )
        self.binary.chmod(0o755)
        self.saved_session = self.session_file.read_bytes()
        self.saved_config = (self.worktree / "svgo.config.mjs").read_bytes()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.worktree), *args], text=True, timeout=10)

    def invoke(self, *, source=None, output=None, environment=None, session=None, binary=None):
        result = subprocess.run([sys.executable, str(RUNNER), "--session", str(session or self.session),
                                 "--svgo", str(binary or self.binary), "--input", str(source or self.source),
                                 "--output", str(output or self.output)],
                                cwd=self.root, capture_output=True, text=True, timeout=15,
                                env={**os.environ, **(environment or {})})
        self.assertEqual(result.stderr, "", result.stderr)
        return result.returncode, json.loads(result.stdout)

    def test_trial_preserves_exact_source_config_and_session(self):
        code, result = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "trial-produced")
        for name, expected in (("before.svg", SVG), ("optimized.svg", SVG), ("svgo.config.mjs", self.saved_config)):
            self.assertEqual((self.output / name).read_bytes(), expected)
        self.assertEqual(result["inputSha256"], result["outputSha256"])
        self.assertEqual(self.session_file.read_bytes(), self.saved_session)
        self.assertEqual(self.source.read_bytes(), SVG)
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertNotIn("verified", result)

    def test_repeat_never_overwrites_prior_trial(self):
        self.invoke()
        before = {p.name: p.read_bytes() for p in self.output.iterdir()}
        code, result = self.invoke()
        self.assertEqual((code, result["status"]), (1, "blocked"))
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.output.iterdir()})

    def test_installed_versions_are_recorded_not_allowlisted(self):
        # Controlled executables all implement the required CLI. This tests the
        # version gate, not compatibility of every real SVGO release.
        for version in ("3.3.2", "4.0.2", "4.1.0", "99.0.0-fixture"):
            with self.subTest(version=version):
                output = self.evidence / ("trial-" + version)
                code, result = self.invoke(output=output, environment={"TEST_SVGO_VERSION": version})
                self.assertEqual((code, result["status"]), (0, "trial-produced"))
                receipt = json.loads((output / "svgo-receipt.json").read_bytes())
                self.assertEqual(result["version"], version)
                self.assertEqual(receipt["version"], version)
                self.assertEqual(receipt["configSha256"], optimize_svg._sha(self.saved_config))
                self.assertEqual(self.source.read_bytes(), SVG)
                self.assertEqual(self.session_file.read_bytes(), self.saved_session)
                self.assertNotIn("verified", result)

    def test_failed_version_probe_is_not_an_optimization_trial(self):
        code, result = self.invoke(environment={"TEST_PROBE_FAIL": "1"})
        self.assertEqual((code, result["status"]), (0, "skipped"))
        self.assertIn("version probe failed", result["reason"])
        self.assertFalse(self.output.exists())
        self.assertEqual(self.source.read_bytes(), SVG)

    def test_missing_executable_is_a_skip_not_an_approval(self):
        code, result = self.invoke(binary=self.root / "missing")
        self.assertEqual((code, result["status"]), (0, "skipped"))
        self.assertIn("working", result["nextAction"])
        self.assertFalse(self.output.exists())

    

    def test_invalid_paths_remain_blocked(self):
        alias = self.evidence / "alias.svg"
        alias.symlink_to(self.source)
        other = self.session / "evidence/20"
        other.mkdir()
        for source, output in ((alias, self.output), (self.source, self.root / "outside"),
                               (self.source, self.evidence / "../escaped"), (self.source, other / "trial")):
            with self.subTest(source=source, output=output):
                self.assertEqual(self.invoke(source=source, output=output)[0], 1)
        self.assertFalse(self.output.exists())

    def test_external_session_alias_is_allowed_without_resolving_evidence_symlinks(self):
        alias = self.root / "alias"
        alias.symlink_to(self.session, target_is_directory=True)
        code, result = self.invoke(session=alias, source=alias / "evidence/10/working.svg",
                                   output=alias / "evidence/10/trial")
        self.assertEqual((code, result["status"]), (0, "trial-produced"))

    def test_changed_config_is_not_an_optimizer_skip(self):
        (self.worktree / "svgo.config.mjs").write_text("export default {};\n")
        code, result = self.invoke()
        self.assertEqual((code, result["status"]), (1, "blocked"))
        self.assertIn("frozen session base", result["reason"])
        self.assertFalse(self.output.exists())

    def test_missing_committed_config_is_an_honest_older_base_skip(self):
        self.git("rm", "svgo.config.mjs")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@localhost", "commit", "-qm", "older base")
        self.data["baseCommit"] = self.git("rev-parse", "HEAD").strip()
        self.session_file.write_text(json.dumps(self.data))
        self.assertEqual(self.invoke()[1]["status"], "skipped")
        self.assertFalse(self.output.exists())

    def test_nonzero_exit_and_empty_output_keep_diagnostics(self):
        for env in ({"TEST_SVGO_EXIT": "23"}, {"TEST_EMPTY": "1"}):
            with self.subTest(env=env):
                output = self.evidence / next(iter(env))
                code, result = self.invoke(environment=env, output=output)
                self.assertEqual((code, result["status"]), (0, "skipped"))
                receipt = json.loads((output / "svgo-receipt.json").read_bytes())
                self.assertEqual(receipt["status"], "tool-failed")
                self.assertNotIn("outputSha256", receipt)
                self.assertTrue((output / "svgo.log").read_bytes())
                self.assertEqual(self.source.read_bytes(), SVG)

    def test_mutation_during_failed_optimization_is_integrity_failure(self):
        code, result = self.invoke(environment={"TEST_MUTATE": str(self.source), "TEST_SVGO_EXIT": "23"})
        self.assertEqual((code, result["status"]), (1, "blocked"))
        receipt = json.loads((self.output / "svgo-receipt.json").read_bytes())
        self.assertEqual(receipt["status"], "integrity-failed")
        self.assertNotIn("outputSha256", receipt)
        self.assertEqual((self.output / "before.svg").read_bytes(), SVG)

    def test_timeout_is_bounded_and_receipt_remains(self):
        actual = optimize_svg._run
        def boundary(command, **kwargs):
            if "--quiet" in command:
                self.assertEqual(kwargs["timeout"], 30)
                raise subprocess.TimeoutExpired(command, 30)
            return actual(command, **kwargs)
        with patch.object(optimize_svg, "_run", side_effect=boundary):
            with self.assertRaises(subprocess.TimeoutExpired):
                optimize_svg.optimize(self.session, self.binary, self.source, self.output)
        receipt = json.loads((self.output / "svgo-receipt.json").read_bytes())
        self.assertEqual(receipt["status"], "tool-failed")
        self.assertEqual(self.source.read_bytes(), SVG)

    def test_timeout_terminates_child_process_group(self):
        pidfile = self.root / "child.pid"
        program = ("import subprocess,sys,time; from pathlib import Path; "
                   "p=subprocess.Popen([sys.executable,'-S','-c','import time; time.sleep(60)']); "
                   f"Path({str(pidfile)!r}).write_text(str(p.pid)); time.sleep(60)")
        with self.assertRaises(subprocess.TimeoutExpired):
            optimize_svg._run([sys.executable, "-S", "-c", program], timeout=2)
        self.assertTrue(pidfile.exists())
        child = int(pidfile.read_text())
        # A reaped or zombie process has stopped; no running child should survive.
        result = subprocess.run(["ps", "-p", str(child), "-o", "stat="], capture_output=True, text=True, timeout=5)
        self.assertTrue(result.returncode != 0 or result.stdout.strip().startswith("Z"), result.stdout)

    def test_help_and_argument_errors_are_machine_usable(self):
        result = subprocess.run([sys.executable, str(RUNNER), "--help"], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertIn("--session", result.stdout)
        result = subprocess.run([sys.executable, str(RUNNER)], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["status"], "blocked")
        self.assertEqual(result.stderr, "")


class RealSvgo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binary = os.environ.get("SVGO_BINARY") or shutil.which("svgo")
        if not cls.binary:
            raise unittest.SkipTest("SVGO is not installed; real optimization was not tested")
        cls.version = subprocess.check_output([cls.binary, "--version"], text=True, timeout=10).strip()

    def optimize(self, svg):
        result = subprocess.run([self.binary, "--config", str(CONFIG), "--input", "-",
                                 "--output", "-", "--quiet"], input=svg.encode(),
                                capture_output=True, check=True, timeout=30)
        return ET.fromstring(result.stdout)

    def test_viewport_title_description_and_shared_css_survive(self):
        root = self.optimize('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32"><title>Bank</title><desc>Bank emblem</desc><style>.mark{fill:#ff0000}</style><path class="mark" d="M1 1h4v4H1z"/><path class="mark" d="M10 10h4v4h-4z"/></svg>')
        self.assertEqual(root.attrib["viewBox"], "0 0 32 32")
        self.assertEqual(root.attrib["width"], "32")
        self.assertEqual(root.find("{*}title").text.strip(), "Bank")
        self.assertEqual(root.find("{*}desc").text.strip(), "Bank emblem")
        self.assertIsNone(root.find("{*}style"))
        fills = [element.get("fill") for element in root.iter() if element.get("fill")]
        self.assertTrue(fills)
        self.assertTrue(all(fill in ("red", "#f00", "#ff0000") for fill in fills))

    def test_conditional_css_is_not_blindly_deleted(self):
        root = self.optimize('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><style>@media (prefers-color-scheme:dark){.mark{fill:white}}</style><path class="mark" d="M1 1h30v30H1z"/></svg>')
        styles = list(root.iter("{http://www.w3.org/2000/svg}style"))
        self.assertTrue(styles)
        self.assertIn("prefers-color-scheme", styles[0].text)

    def test_raster_is_retained_for_separate_safety_rejection(self):
        root = self.optimize('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><image width="32" height="32" href="data:image/png;base64,AA=="/></svg>')
        self.assertIsNotNone(root.find("{*}image"))


if __name__ == "__main__":
    unittest.main()
