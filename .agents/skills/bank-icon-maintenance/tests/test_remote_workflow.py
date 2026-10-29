"""Remote discovery, fresh selection and authorization with disposable Git remotes."""
from session_fixture import make_session
from argparse import Namespace
import hashlib
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_session as sessions
import test_state_publication as publication
from test_session import NOW, SVG, git, put

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import state_publication as state

REF = state.REF


class RemoteAddressTests(unittest.TestCase):
    def test_git_ssh_syntax_is_preserved_not_converted_to_root_relative_url(self):
        for target in ("git@github.com:b3er/brands-banks.git", "host:relative/repo.git",
                       "user@host:/absolute/repo.git", "user@host:~/repo.git",
                       "git@[2001:db8::1]:repo.git", "ssh://git@host:2222/path/repo.git",
                       "https://host/owner/repo.git"):
            with self.subTest(target=target):
                self.assertEqual(state.remote_target(target), target)

    def test_aliases_helpers_credentials_controls_and_malformed_urls_are_rejected(self):
        for target in ("origin", "../relative", "ext::sh -c touch", "file:///tmp/repo.git",
                       "git://host/repo.git", "https://user:secret@host/repo.git",
                       "https://host/repo.git?token=secret", "https://host/repo.git#ref",
                       "ssh://host:bad/repo.git", "ssh://host:99999/repo.git", "ssh://host/",
                       "git@host:repo.git\n", "git@host:repo\x00.git", "-host:repo.git",
                       "host:-option", "", None):
            with self.subTest(target=target), self.assertRaises(ValueError):
                state.remote_target(target)

    def test_network_timeout_is_structured_and_credentials_are_not_prompted(self):
        with patch.object(state, "git", side_effect=subprocess.TimeoutExpired("git", 60)) as call:
            with self.assertRaisesRegex(ValueError, "timed out"):
                state._remote_read(Path("."), "ls-remote", "ssh://host/repo.git", REF)
        self.assertEqual(call.call_args.kwargs["timeout"], 60)
        self.assertEqual(call.call_args.kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")


class RemoteSelectionCLI(unittest.TestCase):
    bank = sessions.SessionCLI.bank
    commit = sessions.SessionCLI.commit
    state = sessions.SessionCLI.state
    cli = sessions.SessionCLI.cli

    def setUp(self):
        sessions.SessionCLI.setUp(self)
        self.remote = self.root / "remote.git"
        git(self.root, "init", "--bare", "-q", str(self.remote))
        git(self.repo, "remote", "add", "origin", str(self.remote))

    def remote_state(self, record):
        self.state({10: record})
        commit = git(self.repo, "rev-parse", REF)
        git(self.repo, "push", "-q", str(self.remote), REF)
        git(self.repo, "update-ref", "-d", REF)
        return commit

    @staticmethod
    def verified():
        return {"schemaVersion": 1, "lastVerifiedAt": NOW,
                "verifiedIconHash": hashlib.sha256(SVG).hexdigest(),
                "lastAttemptAt": NOW, "lastAttemptResult": "verified"}

    def test_fresh_history_is_loaded_and_refreshes_only_service_refs(self):
        commit = self.remote_state(self.verified())
        head = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "update-ref", REF, head)  # Stale local branch must not mask remote.
        tracked = "refs/remotes/origin/banks-icon-agent-state"
        git(self.repo, "update-ref", tracked, head)
        git(self.repo, "config", "fetch.prune", "true")
        git(self.repo, "config", "fetch.pruneTags", "true")
        git(self.repo, "tag", "local-only", head)
        git(self.remote, "tag", "remote-only", commit)
        fetch_head = self.repo / ".git/FETCH_HEAD"
        fetch_head.write_text("user's previous fetch\n")
        (self.repo / "notes.txt").write_text("keep my uncommitted work")
        before = git(self.repo, "status", "--porcelain")
        result = make_session(self)
        self.assertEqual(result["selectedIds"], [10])
        self.assertEqual(result["stateCommit"], commit)
        self.assertEqual(result["stateHistory"], "present")
        self.assertEqual(result["stateRemote"], str(self.remote))
        self.assertEqual(result["stateRead"]["status"], "fetched")
        self.assertTrue(result["stateRef"].startswith("refs/bank-icon-maintenance/state-snapshots/"))
        self.assertEqual(git(self.repo, "rev-parse", result["stateRef"]), commit)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), head)
        for ref in (REF, tracked):
            self.assertEqual(git(self.repo, "rev-parse", ref), commit)
        self.assertEqual(result["stateRead"]["localSync"]["status"], "synced")
        self.assertEqual(git(self.repo, "status", "--porcelain"), before)
        self.assertEqual(fetch_head.read_text(), "user's previous fetch\n")
        self.assertEqual(git(self.repo, "tag", "--list"), "local-only")
        self.assertEqual(git(self.remote, "rev-parse", REF), commit)

    def test_default_uses_push_destination_not_fetch_destination(self):
        commit = self.remote_state(self.verified())
        fetch_only = self.root / "fetch-only.git"
        git(self.root, "init", "--bare", "-q", str(fetch_only))
        git(self.repo, "remote", "set-url", "origin", str(fetch_only))
        git(self.repo, "remote", "set-url", "--push", "origin", str(self.remote))
        result = make_session(self)
        self.assertEqual(result["stateCommit"], commit)
        self.assertEqual(result["stateRemote"], str(self.remote))
        self.assertEqual(result["selectedIds"], [10])

    def test_absent_server_branch_is_not_replaced_with_stale_local_history(self):
        self.state({10: self.verified()})
        old = git(self.repo, "rev-parse", REF)
        result = make_session(self)
        self.assertEqual(result["stateRead"]["status"], "absent")
        self.assertEqual(result["stateHistory"], "unknown")
        self.assertIsNone(result["stateRef"])
        self.assertEqual(result["selectedIds"], [10])
        self.assertEqual(git(self.repo, "rev-parse", REF), old)

    def test_multiple_push_urls_stop_before_session_creation_but_override_works(self):
        git(self.repo, "remote", "set-url", "--add", "--push", "origin", str(self.remote))
        git(self.repo, "remote", "set-url", "--add", "--push", "origin", str(self.root / "other.git"))
        refused = make_session(self, ok=False)
        self.assertIn("exactly one push URL", refused["error"])
        self.assertFalse(self.session.exists())
        self.assertEqual(git(self.repo, "worktree", "list", "--porcelain").count("worktree "), 1)
        result = make_session(self , remote=str(self.remote))
        self.assertEqual(result["stateRemote"], str(self.remote))


    def test_no_origin_preserves_local_mode_and_reports_unverified_history(self):
        git(self.repo, "remote", "remove", "origin")
        result = make_session(self)
        self.assertEqual(result["stateRead"]["status"], "no-origin")
        self.assertEqual(result["stateHistory"], "unknown")
        self.assertEqual(result["selectedIds"], [10])

    def test_server_failure_never_silently_falls_back_to_local_history(self):
        self.state({10: self.verified()})
        wrapper_dir = self.root / "bin"
        wrapper_dir.mkdir()
        real_git = shutil.which("git")
        wrapper = wrapper_dir / "git"
        wrapper.write_text(f"#!{sys.executable}\nimport os, sys\n"
                           "if 'ls-remote' in sys.argv:\n"
                           "    print('controlled transport failure', file=sys.stderr)\n"
                           "    sys.exit(128)\n"
                           f"os.execv({real_git!r}, [{real_git!r}, *sys.argv[1:]])\n")
        wrapper.chmod(0o755)
        with patch.dict(os.environ, {"PATH": str(wrapper_dir) + os.pathsep + os.environ["PATH"]}):
            result = make_session(self, ok=False)
        self.assertIn("no queue was created", result["error"])
        self.assertIn("Check access", result["error"])
        self.assertFalse(self.session.exists())

    def test_continue_retains_snapshot_and_new_sessions_have_distinct_pins(self):
        self.remote_state({"schemaVersion": 1, "note": "first"})
        first = make_session(self , bank_id=10)
        second = make_session(self , bank_id=10, session_path=self.root / 'second')
        self.assertNotEqual(first["stateRef"], second["stateRef"])
        git(self.remote, "update-ref", "-d", REF)
        resumed = self.cli("continue")
        self.assertEqual(resumed["stateCommit"], first["stateCommit"])
        self.assertEqual(resumed["banks"], first["banks"])
        self.assertEqual(resumed["stateRead"], first["stateRead"])

    def test_remote_race_does_not_select_from_unobserved_commit(self):
        commit = self.remote_state(self.verified())
        actual_read = state._remote_read
        def raced(repo, *args, **kwargs):
            response = actual_read(repo, *args, **kwargs)
            if args[0] == "ls-remote":
                response.stdout = ("0" * len(commit) + "\t" + REF + "\n").encode()
            return response
        with patch.object(state, "_remote_read", side_effect=raced):
            with self.assertRaisesRegex(ValueError, "changed during fetch"):
                state.initial_state(self.repo, Namespace(remote=None, offline=False))
        self.assertEqual(git(self.repo, "for-each-ref", "--format=%(refname)",
                             "refs/bank-icon-maintenance/state-snapshots/"), "")

    def test_git_url_rewrite_is_resolved_before_target_is_saved(self):
        git(self.repo, "config", f"url.{str(self.remote)}.pushInsteadOf", "fixture:catalog")
        git(self.repo, "remote", "set-url", "origin", "fixture:catalog")
        result = make_session(self)
        self.assertEqual(result["stateRemote"], str(self.remote))


class RemotePublicationCLI(unittest.TestCase):
    setUp = publication.StatePublicationCLI.setUp
    bank = publication.StatePublicationCLI.bank
    commit = publication.StatePublicationCLI.commit
    state = publication.StatePublicationCLI.state
    cli = publication.StatePublicationCLI.cli
    evidence = publication.StatePublicationCLI.evidence
    authorization = publication.StatePublicationCLI.authorization

    def attempt(self):
        started = make_session(self)
        self.cli("continue", "--evidence", str(self.evidence(started)))
        return started

    def test_legacy_session_can_prepare_from_origin_without_rewriting_its_history(self):
        self.attempt()
        saved = self.session / "session.json"
        data = json.loads(saved.read_text())
        data.pop("stateRemote")
        data.pop("stateRead")
        put(saved, data)  # Simulate the previous release, before any package exists.
        before = saved.read_bytes()
        git(self.repo, "remote", "add", "origin", str(self.remote))
        prepared = self.cli("state-prepare", "--ids", "10")
        self.assertEqual(prepared["package"]["target"], str(self.remote))
        self.assertEqual(prepared["package"]["stateHistory"], "unknown")
        self.assertEqual(saved.read_bytes(), before)
        result = self.cli("state-publish", "--package", prepared["packageHash"])
        self.assertEqual(result["status"], "awaiting-state-authorization")
        self.assertEqual(git(self.remote, "for-each-ref", "--format=%(refname)"), "")

    def test_pinned_target_survives_origin_change_and_new_target_requires_new_authorization(self):
        git(self.repo, "remote", "add", "origin", str(self.remote))
        self.attempt()
        other = self.root / "other.git"
        git(self.root, "init", "--bare", "-q", str(other))
        git(self.repo, "remote", "set-url", "origin", str(other))
        original = self.cli("state-prepare", "--ids", "10")
        self.assertEqual(original["package"]["target"], str(self.remote))
        redirected = self.cli("state-prepare", "--remote", str(other), "--ids", "10")
        self.assertEqual(redirected["package"]["target"], str(other))
        self.assertNotEqual(original["packageHash"], redirected["packageHash"])
        denied = self.cli("state-publish", "--package", redirected["packageHash"],
                          "--authorization", str(self.authorization(original)), ok=False)
        self.assertIn("authorization", denied["error"])
        for target in (self.remote, other):
            self.assertEqual(git(target, "for-each-ref", "--format=%(refname)"), "")

    def test_published_result_is_seen_by_next_task_workspace_without_manual_fetch(self):
        git(self.repo, "remote", "add", "origin", str(self.remote))
        self.attempt()
        prepared = self.cli("state-prepare", "--ids", "10")
        result = self.cli("state-publish", "--package", prepared["packageHash"],
                          "--authorization", str(self.authorization(prepared)))
        self.assertEqual(result["status"], "published")
        data = make_session(self, session_path=self.root / "next", now=NOW)
        self.assertEqual(data["stateCommit"], result["commit"])
        self.assertEqual(data["banks"][0]["state"], result["package"]["records"][0]["after"])
        self.assertEqual(data["stateRead"]["status"], "fetched")

    def test_scp_destination_works_through_git_transport_without_url_conversion(self):
        target = "git@fixture.example:catalog.git"
        wrapper = self.root / "controlled ssh"
        calls = self.root / "ssh-calls.jsonl"
        real_git = shutil.which("git")
        wrapper.write_text(f"#!{sys.executable}\nimport json, os, shlex, sys\n"
                           f"with open({str(calls)!r}, 'a') as f: f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                           "parts = shlex.split(sys.argv[-1])\n"
                           "if ('git@fixture.example' not in sys.argv or len(parts) != 2 or\n"
                           "    parts[0] not in ('git-upload-pack', 'git-receive-pack') or\n"
                           "    parts[1] != 'catalog.git'): sys.exit(1)\n"
                           f"os.execv({real_git!r}, [{real_git!r}, parts[0][4:], {str(self.remote)!r}])\n")
        wrapper.chmod(0o755)
        git(self.repo, "remote", "add", "origin", target)
        with patch.dict(os.environ, {"GIT_SSH_COMMAND": shlex.quote(str(wrapper)), "GIT_SSH_VARIANT": "ssh"}):
            self.attempt()
            prepared = self.cli("state-prepare", "--ids", "10")
            self.assertEqual(prepared["package"]["target"], target)
            result = self.cli("state-publish", "--package", prepared["packageHash"],
                              "--authorization", str(self.authorization(prepared)))
            self.assertEqual(result["status"], "published")
            self.session = self.root / "next-ssh-session"
            started = make_session(self)
            self.assertEqual(started["stateCommit"], result["commit"])
            self.assertEqual(started["stateRemote"], target)
        operations = [shlex.split(json.loads(line)[-1])[0] for line in calls.read_text().splitlines()]
        self.assertIn("git-upload-pack", operations)
        self.assertIn("git-receive-pack", operations)

    def test_fetched_state_validates_and_can_be_prepared_for_next_authorized_update(self):
        self.state({"schemaVersion": 1, "lastAttemptAt": "2025-01-01T00:00:00Z",
                    "lastAttemptResult": "unconfirmed", "history": "preserve"})
        git(self.repo, "push", "-q", str(self.remote), REF)
        git(self.repo, "update-ref", "-d", REF)
        git(self.repo, "remote", "add", "origin", str(self.remote))
        started = self.attempt()
        self.assertEqual(started["stateRead"]["status"], "fetched")
        prepared = self.cli("state-prepare", "--ids", "10")
        self.assertEqual(prepared["package"]["stateHistory"], "present")
        self.assertEqual(prepared["package"]["records"][0]["after"]["history"], "preserve")
        result = self.cli("state-publish", "--package", prepared["packageHash"],
                          "--authorization", str(self.authorization(prepared)))
        self.assertEqual(result["status"], "published")

    def test_no_origin_or_ambiguous_origin_requires_explicit_destination_only_at_prepare(self):
        self.attempt()
        missing = self.cli("state-prepare", "--ids", "10", ok=False)
        self.assertIn("origin is not configured", missing["error"])
        git(self.repo, "remote", "add", "origin", str(self.remote))
        git(self.repo, "remote", "set-url", "--add", "--push", "origin", str(self.remote))
        git(self.repo, "remote", "set-url", "--add", "--push", "origin", str(self.root / "other.git"))
        ambiguous = self.cli("state-prepare", "--ids", "10", ok=False)
        self.assertIn("exactly one push URL", ambiguous["error"])
        prepared = self.cli("state-prepare", "--remote", str(self.remote), "--ids", "10")
        self.assertEqual(prepared["package"]["target"], str(self.remote))


if __name__ == "__main__":
    unittest.main()
