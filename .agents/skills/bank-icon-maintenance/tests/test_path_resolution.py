"""Path bootstrap through real CLI calls, without renderers or network access."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

import test_attempts as fixtures


class PathResolutionCLI(unittest.TestCase):
    setUp = fixtures.AttemptCLI.setUp
    bank = fixtures.AttemptCLI.bank
    commit = fixtures.AttemptCLI.commit

    def invoke(self, *args, cwd=None, script=None, ok=True):
        result = subprocess.run(
            [sys.executable, str(script or fixtures.SCRIPT), *args],
            cwd=cwd or self.repo, capture_output=True, text=True, timeout=30,
            env={**os.environ, **getattr(self, "task_env", {}), "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(result.returncode, 0 if ok else 1,
                         result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        return json.loads(result.stdout)

    def check_detail(self, result, name):
        return next(c["detail"] for c in result["checks"] if c["name"] == name)

    def install_skill(self, directory):
        shutil.copytree(fixtures.SCRIPT.parent, directory / "scripts")
        return directory / "scripts/session.py"

    def prepare_task_provider(self):
        from test_asset_publication import GH
        remote = self.root / "remote.git"
        fixtures.git(self.root, "init", "--bare", "-q", str(remote))
        fixtures.git(self.repo, "remote", "add", "origin", "https://github.com/fixture/catalog.git")
        config = self.root / "gitconfig"
        fixtures.git(self.repo, "config", "--file", str(config),
                     f"url.file://{remote}.insteadOf", "https://github.com/fixture/catalog.git")
        binary = self.root / "bin"
        binary.mkdir()
        gh = binary / "gh"
        gh.write_text(GH.replace("#!PYTHON", "#!" + sys.executable))
        gh.chmod(0o755)
        data = self.root / "gh.json"
        data.write_text(json.dumps({"prs": [], "createCalls": 0}))
        self.task_env = {"PATH": str(binary) + os.pathsep + os.environ["PATH"],
                         "FIXTURE_GH_STATE": str(data), "FIXTURE_GIT_REMOTE": str(remote),
                         "GIT_CONFIG_GLOBAL": str(config), "GIT_CONFIG_NOSYSTEM": "1"}

    def test_default_from_renamed_checkout_does_not_append_brands(self):
        renamed = self.root / "brands-test"
        self.repo.rename(renamed)
        self.repo = renamed
        (self.repo / "brands").mkdir()
        result = self.invoke("doctor")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["paths"], {
            "cli": str(fixtures.SCRIPT.resolve()),
            "repo": str(self.repo.resolve()),
            "catalog": str(self.repo.resolve() / "icon-catalog")})
        self.assertEqual(self.check_detail(result, "source")["repo"], str(self.repo))

    def test_default_from_subdirectory_with_external_skill_and_spaces(self):
        renamed = self.root / "каталог с пробелами"
        self.repo.rename(renamed)
        self.repo = renamed
        nested = self.repo / "nested/work"
        nested.mkdir(parents=True)
        installed = self.install_skill(self.root / "global skills/maintenance")
        # A different Git repository around the installed skill must not be used.
        fixtures.git(installed.parent.parent, "init", "-q")
        relative_cli = os.path.relpath(installed, nested)
        result = self.invoke("doctor", cwd=nested, script=relative_cli)
        self.assertEqual(result["paths"]["repo"], str(self.repo))
        self.assertEqual(result["paths"]["cli"], str(installed.resolve()))
        self.assertEqual(result["paths"]["catalog"], str(self.repo / "icon-catalog"))

    def test_explicit_repo_wins_over_command_working_directory(self):
        other = self.root / "unrelated-repo"
        other.mkdir()
        fixtures.git(other, "init", "-q")
        result = self.invoke("doctor", "--repo", str(self.repo), cwd=other)
        self.assertEqual(result["paths"]["repo"], str(self.repo))
        self.assertEqual(result["status"], "ready")

    def test_incorrect_explicit_repo_never_falls_back(self):
        for value in (str(self.repo / "brands"), str(self.repo / "banks"), ""):
            with self.subTest(repo=value):
                result = self.invoke("doctor", "--repo", value, ok=False)
                self.assertEqual(result["status"], "blocked")
                self.assertTrue(any(c["status"] == "error" for c in result["checks"]))
                self.assertFalse(self.session.exists())

    def test_outside_git_does_not_search_neighbors_or_skill_installation(self):
        installed = self.install_skill(self.repo / ".agents/skills/bank-icon-maintenance")
        result = self.invoke("doctor", cwd=self.root, script=installed, ok=False)
        self.assertIsNone(result["paths"]["repo"])
        self.assertIn("command working directory", self.check_detail(result, "source"))
        self.assertIn("--repo", self.check_detail(result, "source"))
        self.assertFalse(self.session.exists())

    def test_bare_repository_is_not_a_serviced_checkout(self):
        bare = self.root / "bare.git"
        bare.mkdir()
        fixtures.git(bare, "init", "--bare", "-q")
        result = self.invoke("doctor", cwd=bare, ok=False)
        self.assertIsNone(result["paths"]["repo"])
        self.assertIn("Cannot discover a Git checkout", self.check_detail(result, "source"))

    def test_missing_policy_is_a_base_error_not_a_request_to_guess_paths(self):
        base = fixtures.git(self.repo, "rev-parse", "HEAD")
        (self.repo / "bank-icon-maintenance.json").unlink()
        self.commit()
        result = self.invoke("doctor", ok=False)
        self.assertEqual(result["paths"]["repo"], str(self.repo))
        self.assertIn("policy", self.check_detail(result, "base-policy"))
        corrected = self.invoke("doctor", "--base", base)
        self.assertEqual(corrected["status"], "ready")
        self.assertEqual(self.check_detail(corrected, "base-policy")["baseCommit"], base)
        self.assertFalse(self.session.exists())

    def test_linked_worktree_and_symlink_are_canonicalized_by_git(self):
        worktree = self.root / "linked checkout"
        fixtures.git(self.repo, "worktree", "add", "--detach", str(worktree), "HEAD")
        nested = worktree / "banks"
        alias = self.root / "alias"
        alias.symlink_to(nested, target_is_directory=True)
        result = self.invoke("doctor", cwd=alias)
        self.assertEqual(result["paths"]["repo"], str(worktree.resolve()))
        self.assertEqual(result["paths"]["catalog"], str(worktree / "icon-catalog"))

    def test_returned_paths_start_from_another_fresh_shell_without_guessing(self):
        # Reproduce the doc's separate-tool-call boundary: no shell variables survive.
        self.prepare_task_provider()
        installed = self.install_skill(self.root / "agent skills/maintenance")
        result = self.invoke("doctor", script=installed)
        started = self.invoke("task-start", "--task", "update-existing", "--id", "10", "--remote", "https://github.com/fixture/catalog.git", "--repo", result["paths"]["repo"],
                              "--session", str(self.session),
                              cwd=self.root, script=result["paths"]["cli"])
        self.assertEqual(started["repo"], str(self.repo))
        self.assertEqual(started["selectedIds"], [10])
        self.assertEqual(started["banks"][0]["point"], "identity-evidence")

    def test_existing_session_paths_override_cwd_and_preserve_pinned_base(self):
        self.prepare_task_provider()
        started = self.invoke("task-start", "--task", "update-existing", "--id", "10", "--remote", "https://github.com/fixture/catalog.git", "--repo", str(self.repo),
                              "--session", str(self.session))
        saved = (self.session / "session.json").read_bytes()
        (self.repo / "bank-icon-maintenance.json").unlink()
        self.commit()
        result = self.invoke("doctor", "--session", str(self.session), cwd=self.root)
        self.assertEqual(result["paths"]["repo"], str(self.repo))
        self.assertEqual(self.check_detail(result, "base-policy")["baseCommit"], started["baseCommit"])
        self.assertEqual((self.session / "session.json").read_bytes(), saved)

    def test_auto_discovery_is_read_only_even_with_an_unreachable_origin(self):
        fixtures.git(self.repo, "remote", "add", "origin", str(self.root / "absent-remote.git"))
        before = {p.relative_to(self.repo): p.read_bytes()
                  for p in self.repo.rglob("*") if p.is_file()}
        self.invoke("doctor")
        after = {p.relative_to(self.repo): p.read_bytes()
                 for p in self.repo.rglob("*") if p.is_file()}
        self.assertEqual(after, before)
        self.assertFalse(self.session.exists())

    def test_temp_command_in_skill_creates_unique_parent_not_session(self):
        skill = fixtures.SCRIPT.parent.parent / "SKILL.md"
        # Bootstrap is the canonical recipe linked by the compact skill entry.
        self.assertIn("references/bootstrap.md", skill.read_text(encoding="utf-8"))
        bootstrap = skill.parent / "references/bootstrap.md"
        # Execute the documented allocation command, not a parallel implementation.
        command = next(line for line in bootstrap.read_text(encoding="utf-8").splitlines()
                       if line.startswith("python3 -c 'import tempfile;"))
        paths = []
        for _ in range(2):
            result = subprocess.run(["sh", "-c", command], capture_output=True,
                text=True, check=True, cwd=self.repo,
                env={**os.environ, "TMPDIR": str(self.root)}, timeout=15)
            path = Path(result.stdout.strip())
            self.assertTrue(path.is_absolute())
            self.assertTrue(path.parent.is_dir())
            self.assertFalse(path.exists())
            self.assertNotIn(self.repo, path.parents)
            paths.append(path)
        self.assertNotEqual(paths[0], paths[1])


if __name__ == "__main__":
    unittest.main()
