import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


CLEANUP = Path(__file__).resolve().parents[1] / "cleanup-worktrees.py"


class CleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="worktree-cleanup-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        self.git(self.repo, "init", "-q")
        self.git(self.repo, "config", "user.name", "Fixture")
        self.git(self.repo, "config", "user.email", "fixture@example.invalid")
        (self.repo / "tracked").write_text("original\n")
        (self.repo / ".gitignore").write_text("cache/\n")
        self.git(self.repo, "add", ".")
        self.git(self.repo, "commit", "-qm", "base")
        self.base = self.git(self.repo, "rev-parse", "HEAD").strip()
        self.main = self.repo / ".worktree"
        self.git(self.repo, "worktree", "add", "--detach", str(self.main))
        self.task = self.root / "successful task"
        self.task.mkdir()

    def git(self, repo, *args):
        return subprocess.check_output(
            ["git", "-C", str(repo), *args], env=self.env,
            stderr=subprocess.PIPE, text=True, timeout=30,
        )

    def session(self, name="completed", *, parent=None, outcome="unchanged", receipt="published"):
        session = (parent or self.task) / name
        session.mkdir(parents=True)
        tree = session / "worktree"
        self.git(self.repo, "worktree", "add", "--detach", str(tree), self.base)
        data = {"schemaVersion": 2, "repo": str(self.main), "baseCommit": self.base,
                "worktree": str(tree), "worktreeGitDir": self.git(tree, "rev-parse", "--absolute-git-dir").strip(),
                "selectedIds": [3], "paused": False,
                "banks": [{"id": 3, "point": "awaiting-state-publication"}]}
        snapshot = json.dumps(data).encode()
        (session / "session.json").write_bytes(snapshot)
        package = {"scope": "state-publication", "ids": [3],
                   "bindings": {str(session / "session.json"): hashlib.sha256(snapshot).hexdigest()},
                   "results": [{"id": 3, "before": None,
                                "after": {"taskResults": [{"key": "task", "outcome": outcome}]}}]}
        raw = json.dumps(package).encode()
        key = hashlib.sha256(raw).hexdigest()
        publication = session / "state-publication"
        publication.mkdir()
        (publication / f"{key}.json").write_bytes(raw)
        (publication / f"{key}.receipt.json").write_text(json.dumps({"status": receipt}))
        return tree

    def cleanup(self, *roots):
        return subprocess.run(
            ["python3", str(CLEANUP), str(self.repo), str(self.main),
             *map(str, roots or [self.task])], env=self.env,
            capture_output=True, text=True, check=True, timeout=30,
        ).stderr

    def test_completed_tree_removed_but_evidence_main_and_foreign_trees_survive(self):
        tree = self.session()
        foreign = self.session(parent=self.root / "other run")
        (self.main / "cache").mkdir()
        (self.main / "cache" / "build").write_text("keep")
        self.cleanup()
        self.assertFalse(tree.exists())
        self.assertTrue((tree.parent / "session.json").is_file())
        self.assertEqual("keep", (self.main / "cache" / "build").read_text())
        registered = self.git(self.repo, "worktree", "list", "--porcelain")
        self.assertNotIn(f"worktree {tree}\n", registered)
        self.assertIn(f"worktree {foreign}\n", registered)
        self.assertIn(f"worktree {self.main}\n", registered)
        self.cleanup()  # Repeated cleanup must leave the remaining registrations alone.
        self.assertEqual(registered, self.git(self.repo, "worktree", "list", "--porcelain"))

    def test_failed_uncertain_and_unfinished_operations_survive(self):
        trees = [self.session("failed", outcome="error"),
                 self.session("unclear", outcome="unclear"),
                 self.session("candidate", outcome="candidate"),
                 self.session("unknown", receipt="outcome-unknown")]
        missing = self.session("missing")
        next((missing.parent / "state-publication").glob("*.receipt.json")).unlink()
        trees.append(missing)
        mixed = self.session("mixed")
        (mixed.parent / "state-publication" / "another.receipt.json").write_text(
            json.dumps({"status": "outcome-unknown"})
        )
        trees.append(mixed)
        self.cleanup()
        for tree in trees:
            self.assertTrue(tree.is_dir(), tree)

    def test_tracked_untracked_ignored_changes_and_local_commits_survive(self):
        dirty = self.session("dirty")
        (dirty / "tracked").write_text("unsaved")
        untracked = self.session("untracked")
        (untracked / "new").write_text("unsaved")
        ignored = self.session("ignored")
        (ignored / "cache").mkdir()
        (ignored / "cache" / "new").write_text("unsaved")
        committed = self.session("committed")
        self.git(committed, "commit", "--allow-empty", "-qm", "unpublished")
        self.cleanup()
        self.assertEqual("unsaved", (dirty / "tracked").read_text())
        self.assertEqual("unsaved", (untracked / "new").read_text())
        self.assertEqual("unsaved", (ignored / "cache" / "new").read_text())
        self.assertEqual("unpublished", self.git(committed, "log", "-1", "--format=%s").strip())

    def test_stale_binding_malformed_session_and_symlink_are_retained(self):
        stale = self.session("stale")
        snapshot = stale.parent / "session.json"
        snapshot.write_text(snapshot.read_text() + "\n")
        malformed = self.session("malformed")
        (malformed.parent / "session.json").write_text("null")
        linked = self.session("linked")
        snapshot = linked.parent / "session.json"
        moved = linked.parent / "original.json"
        snapshot.rename(moved)
        snapshot.symlink_to(moved)
        self.cleanup()
        for tree in (stale, malformed, linked):
            self.assertTrue(tree.is_dir())

    def test_locked_tree_and_unlisted_task_root_survive(self):
        locked = self.session("locked")
        self.git(self.repo, "worktree", "lock", str(locked))
        failed_task = self.root / "failed task"
        failed = self.session(parent=failed_task)
        self.cleanup()
        self.assertTrue(locked.is_dir())
        self.assertTrue(failed.is_dir())


if __name__ == "__main__":
    unittest.main()
