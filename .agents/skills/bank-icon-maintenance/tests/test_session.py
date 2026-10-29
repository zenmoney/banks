"""Behavior tests at the external session CLI against real temporary Git catalogs."""
from session_fixture import make_session
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "session.py"
POLICY = {"schemaVersion": 1, "recheckAfterDays": 90,
          "retryUnconfirmedAfterDays": 30, "retryErrorAfterDays": 7,
          "selectionLimit": 5}
NOW = "2026-09-25T00:00:00Z"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"/>'


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class SessionCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        put(self.repo / "bank-icon-maintenance.json", POLICY)
        self.bank(10, "Alpha", "ru")
        self.commit()
        self.session = self.root / "session"

    def bank(self, id, title, country="ru", *, icon=True, metadata=None, folder=None):
        path = self.repo / "banks" / (folder or f"{title}_{id}-{country}")
        put(path / "info.json", metadata if metadata is not None else
            {"id": id, "title": title, "countryCode": country.upper(),
             "url": None, "fullTitle": None})
        if icon:
            (path / "icon.svg").write_bytes(SVG)
        return path

    def commit(self):
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "fixture")
        return git(self.repo, "rev-parse", "HEAD")

    def state(self, records):
        main = git(self.repo, "branch", "--show-current")
        git(self.repo, "checkout", "-q", "-b", "banks-icon-agent-state")
        for id, record in records.items():
            put(self.repo / "banks" / "state" / f"{id}.json", record)
        self.commit()
        git(self.repo, "checkout", "-q", main)

    def cli(self, command="continue", *extra, session=None, ok=True):
        args = [sys.executable, str(SCRIPT), command, "--session",
                str(session or self.session)]
        args += list(extra)
        result = subprocess.run(args, capture_output=True, text=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)


    def test_repo_argument_must_be_checkout_root_not_nested_directory(self):
        inside_original = self.repo / "session-under-source"
        result = subprocess.run([sys.executable, str(SCRIPT), "task-start", "--task", "update-existing", "--id", "10", "--repo",
                                 str(self.repo / "banks"), "--session", str(inside_original)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checkout root", json.loads(result.stdout)["error"])
        self.assertFalse(inside_original.exists())


    def test_corrupt_continuation_stops_cleanly_without_touching_candidate(self):
        started = make_session(self , bank_id=10)
        candidate = Path(started["worktree"]) / "candidate.svg"
        candidate.write_text("preserve this work")
        (self.session / "session.json").write_text("[]", encoding="utf-8")
        result = self.cli("continue", ok=False)
        self.assertIn("invalid session", result["error"])
        self.assertEqual(candidate.read_text(), "preserve this work")


    def test_invalid_explicit_id_has_no_worktree_side_effect(self):
        result = make_session(self , bank_id='not-an-id', ok=False)
        self.assertIn("Company.id", result["error"])
        self.assertFalse(self.session.exists())


    def test_continue_rejects_replaced_worktree_even_when_head_matches(self):
        started = make_session(self , bank_id=10)
        worktree = Path(started["worktree"])
        (worktree / "candidate.svg").write_text("keep")
        moved = self.root / "moved-worktree"
        worktree.rename(moved)
        worktree.symlink_to(self.repo, target_is_directory=True)
        result = self.cli("continue", ok=False)
        self.assertIn("worktree", result["error"])
        self.assertEqual((moved / "candidate.svg").read_text(), "keep")


    def test_continue_rejects_different_registered_worktree_at_same_path_and_commit(self):
        started = make_session(self , bank_id=10)
        worktree = Path(started["worktree"])
        candidate = worktree / "candidate.svg"
        candidate.write_text("original candidate")
        self.cli("pause")
        moved = self.root / "moved-worktree"
        git(self.repo, "worktree", "move", str(worktree), str(moved))
        git(self.repo, "worktree", "add", "--detach", str(worktree), started["baseCommit"])
        self.assertEqual(git(worktree, "rev-parse", "HEAD"), started["baseCommit"])
        result = self.cli("continue", ok=False)
        self.assertIn("registration changed", result["error"])
        self.assertEqual((moved / "candidate.svg").read_text(), "original candidate")
        self.assertTrue(json.loads((self.session / "session.json").read_text())["paused"])

    def test_invalid_cli_arguments_return_json_error(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "task-start", "--task", "update-existing", "--id", "10", "--repo", str(self.repo)],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--session", json.loads(result.stdout)["error"])


if __name__ == "__main__":
    unittest.main()
