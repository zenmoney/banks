"""Visible service refs, frozen sessions, and bounded state publication transport."""
from session_fixture import make_session
from argparse import Namespace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import test_remote_workflow as remote
import test_state_publication as publication
from test_session import git

state = remote.state
REF, TRACKING = state.REF, state.TRACKING_REF


class LocalStateSyncCLI(unittest.TestCase):
    setUp = remote.RemoteSelectionCLI.setUp
    bank = remote.RemoteSelectionCLI.bank
    commit = remote.RemoteSelectionCLI.commit
    state = remote.RemoteSelectionCLI.state
    cli = remote.RemoteSelectionCLI.cli
    remote_state = remote.RemoteSelectionCLI.remote_state
    verified = staticmethod(remote.RemoteSelectionCLI.verified)

    def advance(self, repo, parent, message):
        git(repo, "config", "user.name", "Sync test")
        git(repo, "config", "user.email", "sync@example.invalid")
        tree = git(repo, "rev-parse", f"{parent}^{{tree}}")
        return git(repo, "commit-tree", tree, "-p", parent, "-m", message)

    def test_sync_existing_session_refreshes_visible_refs_without_rewriting_history(self):
        started = make_session(self , bank_id=10)
        saved = self.session / "session.json"
        before = saved.read_bytes()
        commit = self.remote_state(self.verified())
        remote_before = git(self.remote, "show-ref")
        result = self.cli("state-sync")
        self.assertTrue(result["sessionSnapshotUnchanged"])
        self.assertEqual(result["stateRead"]["localSync"]["status"], "synced")
        self.assertEqual(saved.read_bytes(), before)
        self.assertIsNone(started["stateCommit"])
        for ref in (REF, TRACKING):
            self.assertEqual(git(self.repo, "rev-parse", ref), commit)
        self.assertEqual(git(self.remote, "show-ref"), remote_before)
        resumed = self.cli("continue")
        self.assertEqual(resumed["stateHistory"], "unknown")
        self.assertEqual(resumed["banks"], started["banks"])

    def test_local_branch_fast_forwards_to_new_server_commit(self):
        first = self.remote_state(self.verified())
        started = make_session(self , bank_id=10)
        second = self.advance(self.remote, first, "remote advance")
        git(self.remote, "update-ref", REF, second, first)
        result = self.cli("state-sync")
        self.assertEqual(result["stateCommit"], second)
        for ref in (REF, TRACKING):
            self.assertEqual(git(self.repo, "rev-parse", ref), second)
        self.assertEqual(git(self.repo, "rev-parse", started["stateRef"]), first)
        self.assertEqual(result["stateRead"]["localSync"]["status"], "synced")

    def test_divergent_local_branch_is_preserved_but_online_queue_uses_remote(self):
        first = self.remote_state(self.verified())
        make_session(self , bank_id=10)
        local = self.advance(self.repo, first, "unpublished local state")
        git(self.repo, "update-ref", REF, local, first)
        server = self.advance(self.remote, first, "independent remote state")
        git(self.remote, "update-ref", REF, server, first)
        fresh = make_session(self , session_path=self.root / 'fresh')
        self.assertEqual(fresh["selectedIds"], [10])
        self.assertEqual(fresh["stateCommit"], server)
        self.assertEqual(git(self.repo, "rev-parse", REF), local)
        self.assertEqual(git(self.repo, "rev-parse", TRACKING), server)
        sync = fresh["stateRead"]["localSync"]
        self.assertEqual(sync["status"], "partial")
        self.assertEqual(sync["refs"][0]["status"], "skipped")

    def test_checked_out_service_worktree_and_its_files_are_not_modified(self):
        first = self.remote_state(self.verified())
        make_session(self , bank_id=10)
        worktree = self.root / "state-worktree"
        git(self.repo, "worktree", "add", str(worktree), "banks-icon-agent-state")
        note = worktree / "local-note.txt"
        note.write_text("uncommitted work")
        before = git(worktree, "status", "--porcelain")
        second = self.advance(self.remote, first, "next remote state")
        git(self.remote, "update-ref", REF, second, first)
        result = self.cli("state-sync")
        self.assertEqual(result["stateRead"]["localSync"]["status"], "partial")
        self.assertEqual(git(self.repo, "rev-parse", REF), first)
        self.assertEqual(git(self.repo, "rev-parse", TRACKING), second)
        self.assertEqual(git(worktree, "status", "--porcelain"), before)
        self.assertEqual(note.read_text(), "uncommitted work")

    def test_separate_fetch_destination_does_not_receive_false_tracking_history(self):
        commit = self.remote_state(self.verified())
        other = self.root / "fetch.git"
        git(self.root, "init", "--bare", "-q", str(other))
        git(self.repo, "remote", "set-url", "origin", str(other))
        git(self.repo, "remote", "set-url", "--push", "origin", str(self.remote))
        head = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "update-ref", TRACKING, head)
        result = make_session(self)
        self.assertEqual(git(self.repo, "rev-parse", REF), commit)
        self.assertEqual(git(self.repo, "rev-parse", TRACKING), head)
        self.assertIn("fetch URL differs", result["stateRead"]["localSync"]["warning"])

    def test_override_of_other_origin_keeps_its_visible_refs_intact(self):
        commit = self.remote_state(self.verified())
        other = self.root / "other-origin.git"
        git(self.root, "init", "--bare", "-q", str(other))
        git(self.repo, "remote", "set-url", "origin", str(other))
        head = git(self.repo, "rev-parse", "HEAD")
        for ref in (REF, TRACKING):
            git(self.repo, "update-ref", ref, head)
        result = make_session(self , remote=str(self.remote))
        self.assertEqual(result["stateCommit"], commit)
        self.assertEqual(result["stateRead"]["localSync"]["status"], "skipped-other-target")
        for ref in (REF, TRACKING):
            self.assertEqual(git(self.repo, "rev-parse", ref), head)

    def test_symbolic_service_ref_does_not_redirect_an_update_to_main(self):
        commit = self.remote_state(self.verified())
        head_ref = git(self.repo, "symbolic-ref", "HEAD")
        head = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "symbolic-ref", REF, head_ref)
        result = make_session(self)
        self.assertEqual(result["stateCommit"], commit)
        self.assertEqual(result["stateRead"]["localSync"]["status"], "partial")
        self.assertEqual(git(self.repo, "symbolic-ref", REF), head_ref)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), head)

    def test_missing_remote_branch_does_not_delete_local_history_or_claim_sync(self):
        commit = self.remote_state(self.verified())
        make_session(self , bank_id=10)
        git(self.remote, "update-ref", "-d", REF)
        result = self.cli("state-sync")
        self.assertEqual(result["status"], "absent")
        self.assertNotIn("localSync", result["stateRead"])
        for ref in (REF, TRACKING):
            self.assertEqual(git(self.repo, "rev-parse", ref), commit)


class PublishedStateSyncCLI(unittest.TestCase):
    setUp = publication.StatePublicationCLI.setUp
    bank = publication.StatePublicationCLI.bank
    commit = publication.StatePublicationCLI.commit
    state = publication.StatePublicationCLI.state
    cli = publication.StatePublicationCLI.cli
    evidence = publication.StatePublicationCLI.evidence
    authorization = publication.StatePublicationCLI.authorization
    prepared = publication.StatePublicationCLI.prepared

    def publish_args(self, prepared):
        return Namespace(command="state-publish", session=str(self.session),
                         package=prepared["packageHash"], authorization=str(self.authorization(prepared)),
                         run_authorization=None)

    def test_confirmed_publication_updates_local_refs_immediately_and_freezes_session(self):
        git(self.repo, "remote", "add", "origin", str(self.remote))
        prepared = self.prepared()
        saved = self.session / "session.json"
        before = saved.read_bytes()
        args = self.publish_args(prepared)
        result = self.cli("state-publish", "--package", args.package, "--authorization", args.authorization)
        self.assertEqual(result["status"], "published")
        self.assertEqual(result["localSync"]["status"], "synced")
        for ref in (REF, TRACKING):
            self.assertEqual(git(self.repo, "rev-parse", ref), result["observedRef"])
        self.assertEqual(saved.read_bytes(), before)
        shown = self.cli("state-show", "--package", args.package)
        self.assertEqual(shown["localSync"], result["localSync"])
        repeated = self.cli("state-reconcile", "--package", args.package)
        self.assertEqual(repeated["status"], "published")
        self.assertEqual(git(self.remote, "rev-list", "--count", REF), "1")

    def test_local_sync_failure_does_not_erase_remote_success_or_trigger_another_push(self):
        git(self.repo, "remote", "add", "origin", str(self.remote))
        prepared = self.prepared()
        with patch.object(state, "cache_state", side_effect=ValueError("controlled local failure")):
            result = state.dispatch(self.publish_args(prepared))
        self.assertEqual(result["status"], "published")
        self.assertEqual(result["localSync"]["status"], "sync-failed")
        self.assertEqual(git(self.remote, "rev-list", "--count", REF), "1")
        refreshed = self.cli("state-sync")
        self.assertEqual(refreshed["stateRead"]["localSync"]["status"], "synced")
        self.assertEqual(git(self.repo, "rev-parse", REF), result["commit"])
        self.assertEqual(git(self.remote, "rev-list", "--count", REF), "1")

    def test_unauthorized_publication_does_not_create_visible_service_refs(self):
        git(self.repo, "remote", "add", "origin", str(self.remote))
        prepared = self.prepared()
        result = self.cli("state-publish", "--package", prepared["packageHash"])
        self.assertEqual(result["status"], "awaiting-state-authorization")
        self.assertNotIn("localSync", result)
        for repo in (self.repo, self.remote):
            self.assertEqual(git(repo, "for-each-ref", "--format=%(refname)", REF), "")

    def test_push_timeout_after_server_accepts_requires_reconcile_not_blind_retry(self):
        git(self.repo, "remote", "add", "origin", str(self.remote))
        prepared = self.prepared()
        real_git = state.git
        pushes = []
        def timed_out(repo, *args, **kwargs):
            result = real_git(repo, *args, **kwargs)
            if args[0] == "push":
                pushes.append(kwargs)
                raise subprocess.TimeoutExpired("git push", kwargs["timeout"])
            return result
        with patch.object(state, "git", side_effect=timed_out):
            result = state.dispatch(self.publish_args(prepared))
        self.assertEqual(result["status"], "outcome-unknown")
        self.assertIn("state-reconcile", result["nextAction"])
        self.assertEqual(len(pushes), 1)
        self.assertEqual(pushes[0]["timeout"], 120)
        self.assertEqual(pushes[0]["env"]["GIT_TERMINAL_PROMPT"], "0")
        reconciled = self.cli("state-reconcile", "--package", prepared["packageHash"])
        self.assertEqual(reconciled["status"], "published")
        self.assertEqual(git(self.repo, "rev-parse", REF), result["commit"])
        self.assertEqual(git(self.remote, "rev-list", "--count", REF), "1")

    def test_publication_read_timeout_does_not_prepare_a_package_or_push(self):
        prepared = self.prepared()
        package_before = sorted((self.session / "state-publication").glob("*.json"))
        real_git = state.git
        def timed_out(repo, *args, **kwargs):
            if args[0] == "ls-remote":
                self.assertEqual(kwargs["timeout"], 60)
                raise subprocess.TimeoutExpired("git ls-remote", 60)
            return real_git(repo, *args, **kwargs)
        args = Namespace(command="state-prepare", session=str(self.session),
                         remote=str(self.remote), ids=[10], resolution=None)
        with patch.object(state, "git", side_effect=timed_out):
            with self.assertRaisesRegex(ValueError, "timed out"):
                state.dispatch(args)
        self.assertEqual(sorted((self.session / "state-publication").glob("*.json")), package_before)
        self.assertEqual(git(self.remote, "for-each-ref", "--format=%(refname)"), "")


class TransportTimeoutTests(unittest.TestCase):
    def test_timeout_terminates_git_child_transport_instead_of_waiting_for_its_pipe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "orphan-finished"
            child = "import time; from pathlib import Path; time.sleep(1); Path(%r).touch()" % str(marker)
            wrapper = root / "git"
            wrapper.write_text(f"#!{sys.executable}\nimport subprocess, sys, time\n"
                               f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
                               "time.sleep(20)\n")
            wrapper.chmod(0o755)
            started = time.monotonic()
            with patch.dict(os.environ, {"PATH": str(root) + os.pathsep + os.environ["PATH"]}):
                with self.assertRaises(subprocess.TimeoutExpired):
                    state.git(root, "ls-remote", "fixture", timeout=0.2)
            self.assertLess(time.monotonic() - started, 3)
            time.sleep(1.1)
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
