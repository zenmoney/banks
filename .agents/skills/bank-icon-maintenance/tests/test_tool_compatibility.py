"""Installed tool acceptance, not certification of arbitrary tool releases.

Provider/session lookups are controlled; JDK executables are real local stubs.
No Gradle build, installation, network operation or bank maintenance is run.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import environment


class ToolCompatibility(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="tool-compatibility-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.catalog = self.root / "icon-catalog"
        self.catalog.mkdir()
        self.marker = self.root / "gradle-was-run"
        wrapper = self.catalog / "gradlew"
        wrapper.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(self.marker)!r}).touch()\n")
        wrapper.chmod(0o755)
        self.jdk = self.root / "jdk with spaces"
        (self.jdk / "bin").mkdir(parents=True)
        self.args = argparse.Namespace(repo=str(self.root), session=None, base="HEAD",
                                       render=True, catalog=str(self.catalog), github=False)
        self.session_module = types.ModuleType("session")
        self.session_module.SessionError = RuntimeError
        self.session_module.policy_at = lambda *args: None

        def git(repo, *args, **kwargs):
            values = {("rev-parse", "--show-toplevel"): str(self.root),
                      ("rev-parse", "--verify", "HEAD^{commit}"): "a" * 40,
                      ("ls-tree", "a" * 40, "--", "banks"): "040000 tree fixture\tbanks"}
            return subprocess.CompletedProcess(args, 0, values[args] + "\n", "")

        self.session_module.git = git
        self.authority_module = types.ModuleType("standing_authorization")
        self.authority_module.configured = lambda: False
        self.authority_module.describe = lambda *args: {"status": "not-configured"}
        self.modules = {"session": self.session_module, "standing_authorization": self.authority_module}

    def tool(self, name, output, code=0):
        executable = self.jdk / "bin" / name
        executable.write_text(f"#!{sys.executable}\nimport sys\nassert sys.argv[1:] == ['-version']\n"
                              f"print({output!r})\nsys.exit({code})\n")
        executable.chmod(0o755)
        return executable

    def doctor(self, version="25.0.1", **overrides):
        self.tool("java", f'openjdk version "{version}"')
        self.tool("javac", f"javac {version}")
        with patch.dict(sys.modules, self.modules), patch.dict(os.environ, {"JAVA_HOME": str(self.jdk)}):
            for name, (text, code) in overrides.items():
                self.tool(name, text, code)
            result = environment.doctor(self.args)
        self.assertFalse(self.marker.exists())
        return result

    def test_available_jdk_versions_are_not_a_runtime_allowlist(self):
        for version in ("17.0.12", "21.0.1", "25.0.1", "99-ea-fixture"):
            with self.subTest(version=version):
                result = self.doctor(version)
                self.assertEqual(result["status"], "ready", result)
                check = next(row for row in result["checks"] if row["name"] == "jdk")
                self.assertEqual(check["status"], "ok")
                self.assertIn(version, check["detail"]["java"])
                self.assertIn(version, check["detail"]["javac"])
                self.assertEqual(next(row["status"] for row in result["checks"] if row["name"] == "gradle-cache"), "warning")

    def test_java_home_keeps_precedence_over_path(self):
        with patch.object(environment.shutil, "which", side_effect=AssertionError("JAVA_HOME was ignored")):
            self.assertEqual(self.doctor()["status"], "ready")

    def test_path_is_used_only_without_java_home(self):
        self.tool("java", 'openjdk version "25.0.1"')
        self.tool("javac", 'javac 25.0.1')
        env = dict(os.environ)
        env.pop("JAVA_HOME", None)
        with patch.dict(sys.modules, self.modules), patch.dict(os.environ, env, clear=True), \
                patch.object(environment.shutil, "which", side_effect=lambda name: str(self.jdk / "bin" / name)) as which:
            result = environment.doctor(self.args)
        self.assertEqual(result["status"], "ready", result)
        self.assertEqual([call.args[0] for call in which.call_args_list], ["java", "javac"])
        self.assertFalse(self.marker.exists())

    def test_broken_java_or_javac_still_blocks(self):
        for name in ("java", "javac"):
            with self.subTest(tool=name):
                result = self.doctor(**{name: ("controlled unavailable tool", 23)})
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(next(row["status"] for row in result["checks"] if row["name"] == "jdk"), "error")

    def test_empty_diagnostic_is_not_readiness(self):
        result = self.doctor(java=("", 0))
        self.assertEqual(result["status"], "blocked")
        self.assertIn("no version information", next(row["detail"] for row in result["checks"] if row["name"] == "jdk"))

    def test_missing_selected_jdk_does_not_fall_back_to_path(self):
        with patch.dict(sys.modules, self.modules), \
                patch.dict(os.environ, {"JAVA_HOME": str(self.root / "missing")}), \
                patch.object(environment.shutil, "which", side_effect=AssertionError("unexpected fallback")):
            result = environment.doctor(self.args)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(next(row["status"] for row in result["checks"] if row["name"] == "jdk"), "error")

    def test_python_minimum_is_not_an_upper_or_patch_pin(self):
        for version, expected in (((3, 9, 99), "error"), ((3, 10, 0), "ok"), ((3, 13, 7), "ok"), ((3, 99, 0), "ok")):
            with self.subTest(version=version), patch.object(environment.sys, "version_info", version):
                result = self.doctor()
                self.assertEqual(next(row["status"] for row in result["checks"] if row["name"] == "python"), expected)

    


if __name__ == "__main__":
    unittest.main()
