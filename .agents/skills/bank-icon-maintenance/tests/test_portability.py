"""Regression checks for checkout bytes, session aliases and read-only preflight.

Controlled exporters and JDK stubs test contracts, not real rendering or OS ports.
"""
from session_fixture import make_session
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_attempts as attempts
import test_headless as headless_tests

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import environment
from asset_publication import _bank_base


class PortabilityCLI(unittest.TestCase):
    setUp = attempts.AttemptCLI.setUp
    bank = attempts.AttemptCLI.bank
    commit = attempts.AttemptCLI.commit
    cli = attempts.AttemptCLI.cli
    evidence = attempts.AttemptCLI.evidence
    exporter = headless_tests.HeadlessCLI.exporter

    def command(self, *args, env=None, ok=True):
        result = subprocess.run([sys.executable, str(attempts.SCRIPT), *args],
            capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0 if ok else 1, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def multiline_catalog(self):
        folder = self.repo / "banks/Alpha_10-ru"
        (folder / "icon.svg").write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg">\n</svg>\n')
        (folder / "info.json").write_bytes((folder / "info.json").read_bytes() + b"\n")
        self.commit()
        return folder

    def alias_session(self):
        physical = self.root / "physical"
        physical.mkdir()
        alias = self.root / "alias"
        alias.symlink_to(physical, target_is_directory=True)
        self.session = alias / "session"
        return physical / "session"

    def test_autocrlf_does_not_change_frozen_hashes_or_user_configuration(self):
        folder = self.multiline_catalog()
        original = {name: (folder / name).read_bytes() for name in ("icon.svg", "info.json")}
        attempts.git(self.repo, "config", "core.autocrlf", "true")
        attempts.git(self.repo, "config", "core.eol", "crlf")
        started = make_session(self)
        bank = started["banks"][0]
        self.assertEqual(bank["point"], "identity-evidence")
        for name, raw in original.items():
            self.assertEqual((Path(started["worktree"]) / bank["folder"] / name).read_bytes(), raw)
            self.assertEqual((folder / name).read_bytes(), raw)
        self.assertEqual(bank["iconHash"], hashlib.sha256(original["icon.svg"]).hexdigest())
        _bank_base(self.repo, started["baseCommit"], bank)
        self.assertEqual(attempts.git(self.repo, "config", "core.autocrlf"), "true")
        self.assertEqual(attempts.git(self.repo, "config", "core.eol"), "crlf")
        self.cli("continue")

    def test_explicit_transforming_attributes_stop_before_review(self):
        self.multiline_catalog()
        (self.repo / ".gitattributes").write_text("banks/** text eol=crlf\n")
        self.commit()
        started = make_session(self)
        bank = started["banks"][0]
        self.assertEqual(bank["point"], "blocked")
        self.assertIn("checkout bytes differ from the committed blob", bank["reason"])
        self.assertIsNone(bank["state"])
        self.assertFalse((self.session / "reports").exists())

    def test_already_committed_crlf_bytes_are_not_normalized(self):
        (self.repo / ".gitattributes").write_text("banks/** -text\n")
        raw = b'<svg xmlns="http://www.w3.org/2000/svg">\r\n</svg>\r\n'
        (self.repo / "banks/Alpha_10-ru/icon.svg").write_bytes(raw)
        self.commit()
        started = make_session(self)
        bank = started["banks"][0]
        self.assertEqual(bank["point"], "identity-evidence")
        self.assertEqual((Path(started["worktree"]) / bank["folder"] / "icon.svg").read_bytes(), raw)
        _bank_base(self.repo, started["baseCommit"], bank)

    def test_alias_session_can_continue_with_aliased_evidence_path(self):
        canonical = self.alias_session()
        started = make_session(self)
        self.assertEqual(started["worktree"], str(canonical / "worktree"))
        path = self.evidence(started)
        result = self.cli("continue", "--evidence", str(path))
        self.assertEqual(result["banks"][0]["point"], "attempt-complete")
        self.cli("pause")
        self.cli("continue")

    def test_alias_root_does_not_allow_symlinked_evidence(self):
        self.alias_session()
        started = make_session(self)
        path = self.evidence(started)
        outside = self.root / "outside.json"
        path.replace(outside)
        path.symlink_to(outside)
        prior = (self.session / "session.json").read_bytes()
        result = self.cli("continue", "--evidence", str(path), ok=False)
        self.assertIn("regular file under session/evidence", result["error"])
        self.assertEqual((self.session / "session.json").read_bytes(), prior)

    def test_export_uses_saved_repo_and_accepts_external_session_alias(self):
        self.alias_session()
        started = make_session(self)
        catalog = self.exporter()
        destination = self.repo / "icon-catalog"
        catalog.rename(destination)
        prior = (self.session / "session.json").read_bytes()
        result = self.cli("desktop-export", "--id", "10", "--output", "evidence/10/export")
        receipt = json.loads((self.session / result["desktop"]["invocation"]["path"]).read_bytes())
        self.assertEqual(receipt["cwd"], str(destination))
        self.assertEqual(receipt["session"], str(self.session.resolve()))
        self.assertEqual((self.session / "session.json").read_bytes(), prior)
        self.assertNotIn("visualComparison", result["desktop"])

    def test_alias_root_does_not_hide_forbidden_review_dot_segments(self):
        import test_reviewed_unchanged as reviewed
        fixture = reviewed.ReviewedUnchangedCLI(
            "test_reviewed_current_variant_verifies_only_new_assessment_and_preserves_original_record")
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        alias = self.root / "review-session-alias"
        alias.symlink_to(fixture.session, target_is_directory=True)
        fixture.session = alias
        prior = (alias / "session.json").read_bytes()
        result = fixture.cli("continue", "--review", str(alias) + "/evidence/10/./review.json", ok=False)
        self.assertIn("traversal", result["error"])
        self.assertEqual((alias / "session.json").read_bytes(), prior)

    def test_missing_catalog_fails_without_consuming_export_output(self):
        make_session(self)
        prior = (self.session / "session.json").read_bytes()
        result = self.cli("desktop-export", "--id", "10", "--output", "evidence/10/export", ok=False)
        self.assertIn("--catalog", result["error"])
        self.assertFalse((self.session / "evidence/10/export").exists())
        self.assertEqual((self.session / "session.json").read_bytes(), prior)

    def test_doctor_is_read_only_and_uses_saved_commit_on_resume(self):
        before = set(self.root.rglob("*"))
        result = self.command("doctor", "--repo", str(self.repo))
        self.assertEqual(result["status"], "ready")
        self.assertEqual(set(self.root.rglob("*")), before)
        self.assertEqual(attempts.git(self.repo, "status", "--porcelain"), "")
        started = make_session(self)
        prior = (self.session / "session.json").read_bytes()
        # The source HEAD changes, but doctor must inspect the saved session base.
        (self.repo / "bank-icon-maintenance.json").unlink()
        self.commit()
        result = self.command("doctor", "--session", str(self.session))
        base = next(c["detail"] for c in result["checks"] if c["name"] == "base-policy")
        self.assertEqual(base["baseCommit"], started["baseCommit"])
        self.assertEqual((self.session / "session.json").read_bytes(), prior)
        self.command("doctor", "--repo", str(self.repo), ok=False)

    def test_render_doctor_checks_java_home_without_executing_gradle(self):
        catalog = self.root / "icon-catalog"
        catalog.mkdir()
        sentinel = self.root / "must-not-be-created"
        wrapper = catalog / "gradlew"
        wrapper.write_text(f'#!/bin/sh\ntouch "{sentinel}"\n')
        wrapper.chmod(0o755)
        home = self.root / "jdk"
        (home / "bin").mkdir(parents=True)
        for tool in ("java", "javac"):
            script = home / "bin" / tool
            value = 'openjdk version "21.0.1"' if tool == "java" else "javac 21.0.1"
            script.write_text(f"#!/bin/sh\nprintf '%s\\n' '{value}'\n")
            script.chmod(0o755)
        result = self.command("doctor", "--repo", str(self.repo), "--render", "--catalog", str(catalog),
            env={**os.environ, "JAVA_HOME": str(home)})
        self.assertEqual(result["status"], "ready")
        self.assertFalse(sentinel.exists())
        cache = next(c for c in result["checks"] if c["name"] == "gradle-cache")
        self.assertEqual(cache["status"], "warning")
        result = self.command("doctor", "--repo", str(self.repo), "--render", "--catalog", str(catalog),
            env={**os.environ, "JAVA_HOME": str(self.root / "missing-jdk")}, ok=False)
        self.assertEqual(next(c["status"] for c in result["checks"] if c["name"] == "jdk"), "error")
        self.assertFalse(sentinel.exists())

    def without_module(self, name, *args):
        script = """
import importlib.abc, pathlib, runpy, sys
missing = sys.argv[1]
class Missing(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == missing or fullname.startswith(missing + '.'):
            raise ModuleNotFoundError('controlled missing ' + fullname)
for loaded in list(sys.modules):
    if loaded == missing or loaded.startswith(missing + '.'):
        del sys.modules[loaded]
sys.meta_path.insert(0, Missing())
del sys.argv[1]
sys.argv = sys.argv[1:]
sys.path.insert(0, str(pathlib.Path(sys.argv[0]).parent))
runpy.run_path(sys.argv[0], run_name='__main__')
"""
        return subprocess.run([sys.executable, "-c", script, name, str(attempts.SCRIPT), *args],
            capture_output=True, text=True)

    def test_missing_fcntl_preserves_help_and_gives_structured_platform_error(self):
        result = self.without_module("fcntl", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("doctor", result.stdout)
        result = self.without_module("fcntl", "task-start", "--task", "update-existing", "--id", "10", "--repo", str(self.repo), "--session", str(self.session))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("WSL", json.loads(result.stdout)["error"])
        self.assertFalse(self.session.exists())
        result = self.without_module("fcntl", "doctor", "--repo", str(self.repo))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "blocked")
        self.assertNotIn("Traceback", result.stderr)

    def test_pillow_is_optional_until_render_and_missing_import_is_actionable(self):
        result = self.without_module("PIL", "doctor", "--repo", str(self.repo))
        self.assertEqual(result.returncode, 0, result.stderr)
        make_session(self)
        result = self.without_module("PIL", "desktop-export", "--session", str(self.session),
            "--id", "10", "--output", "evidence/10/export")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("requirements.txt", json.loads(result.stdout)["error"])
        self.assertFalse((self.session / "evidence/10/export").exists())
        self.assertNotIn("Traceback", result.stderr)

    def test_native_windows_guard_is_explicit(self):
        with patch.object(environment.os, "name", "nt"):
            with self.assertRaisesRegex(ValueError, "Native Windows is not supported"):
                environment.require_posix()


class PublicationPortabilityCLI(unittest.TestCase):
    def test_asset_checkout_is_byte_exact_with_autocrlf_and_session_alias(self):
        import tempfile
        import test_candidates as candidates
        import test_asset_publication as publication
        # Newline-bearing real Git blobs make an accidental CRLF checkout observable.
        with patch.object(candidates, "BASE", candidates.BASE + b"\n"), \
                patch.object(candidates, "FINAL", candidates.FINAL + b"\n"):
            fixture = publication.ApprovedAssetCLI(
                "test_approved_candidate_applies_only_exact_icon_on_target_base")
            self.addCleanup(fixture.doCleanups)
            fixture.setUp()
            candidates.git(fixture.repo, "config", "--file", str(fixture.global_config), "core.autocrlf", "true")
            candidates.git(fixture.repo, "config", "--file", str(fixture.global_config), "core.eol", "crlf")
            aliases = tempfile.TemporaryDirectory()
            self.addCleanup(aliases.cleanup)
            alias = Path(aliases.name) / "session-alias"
            alias.symlink_to(fixture.session, target_is_directory=True)
            fixture.session = alias
            prepared = fixture.prepare()
            worktree = fixture.session / "asset-publication/10/worktree"
            self.assertEqual((worktree / "banks" / fixture.folder / "icon.svg").read_bytes(), candidates.FINAL)
            shown = fixture.cli("asset-show", "--package", prepared["packageHash"])
            self.assertEqual(shown["packageHash"], prepared["packageHash"])
            self.assertIsNone(fixture.remote_ref("icons/10-reviewed"))


if __name__ == "__main__":
    unittest.main()
