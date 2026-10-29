"""State publication through the session CLI and isolated real Git repositories."""
from session_fixture import make_session
import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path

import test_attempts as fixtures
from test_attempts import NOW, git, put

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "session.py"
REF = "refs/heads/banks-icon-agent-state"


class StatePublicationCLI(unittest.TestCase):
    bank = fixtures.AttemptCLI.bank
    commit = fixtures.AttemptCLI.commit
    state = fixtures.AttemptCLI.state
    cli = fixtures.AttemptCLI.cli
    evidence = fixtures.AttemptCLI.evidence

    def setUp(self):
        fixtures.AttemptCLI.setUp(self)
        self.remote = self.root / "remote.git"
        git(self.root, "init", "--bare", "-q", str(self.remote))

    def prepared(self, id=10):
        started = make_session(self, bank_id=id)
        self.cli("continue", "--evidence", str(self.evidence(started, id=id)))
        return self.cli("state-prepare", "--remote", str(self.remote), "--ids", str(id))

    def absent_prepared(self):
        (self.repo / "banks/Alpha_10-ru/icon.svg").unlink()
        self.commit()
        started = make_session(self)
        self.cli("continue", "--evidence", str(self.evidence(started, iconHash=None)))
        return self.cli("state-prepare", "--remote", str(self.remote), "--ids", "10")

    def test_absent_icon_binding_publishes_durable_unverified_attempt(self):
        prepared = self.absent_prepared()
        icon = str(self.session / "worktree/banks/Alpha_10-ru/icon.svg")
        self.assertIn(icon, prepared["package"]["bindings"])
        self.assertIsNone(prepared["package"]["bindings"][icon])
        self.assertEqual(self.cli("state-show", "--package", prepared["packageHash"])["package"],
                         prepared["package"])
        published = self.cli("state-publish", "--package", prepared["packageHash"],
                             "--authorization", str(self.authorization(prepared)))
        self.assertEqual(published["status"], "published")
        record = json.loads(git(self.remote, "show", REF + ":banks/state/10.json"))
        self.assertEqual(record, prepared["package"]["records"][0]["after"])
        self.assertEqual(record["lastAttemptResult"], "unconfirmed")
        self.assertNotIn("verifiedIconHash", record)
        repeated = self.cli("state-prepare", "--remote", str(self.remote), "--ids", "10")
        self.assertEqual(repeated["package"]["records"][0]["after"], record)
        self.assertEqual(self.cli("state-publish", "--package", repeated["packageHash"],
                                 "--authorization", str(self.authorization(repeated)))["status"],
                         "no-state-changes")
        self.assertEqual(git(self.remote, "rev-list", "--count", REF), "1")

    def test_absent_icon_appearing_after_authorization_blocks_state_publication(self):
        prepared = self.absent_prepared()
        authorization = self.authorization(prepared)
        icon = self.session / "worktree/banks/Alpha_10-ru/icon.svg"
        for kind in ("file", "directory", "dangling-symlink"):
            with self.subTest(kind=kind):
                if kind == "file":
                    icon.write_bytes(fixtures.SVG)
                elif kind == "directory":
                    icon.mkdir()
                else:
                    icon.symlink_to(self.root / "missing.svg")
                denied = self.cli("state-publish", "--package", prepared["packageHash"],
                                  "--authorization", str(authorization), ok=False)
                self.assertIn("prepared input changed", denied["error"])
                self.assertEqual(git(self.remote, "for-each-ref", "--format=%(refname)"), "")
                if kind == "directory":
                    icon.rmdir()
                else:
                    icon.unlink()

    def test_null_binding_cannot_hide_missing_evidence_or_unselected_icon(self):
        prepared = self.absent_prepared()
        package = prepared["package"]
        for relative in ("evidence/10/missing.svg", "worktree/banks/Alpha_10-ru/info.json",
                         "worktree/banks/Beta_20-ru/icon.svg",
                         "worktree/banks/Alpha_10-ru/other-icon.svg"):
            with self.subTest(relative=relative):
                name = str(self.session / relative)
                original = package["bindings"].get(name)
                package["bindings"][name] = None
                content = (json.dumps(package, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
                package_hash = hashlib.sha256(content).hexdigest()
                (self.session / "state-publication" / (package_hash + ".json")).write_bytes(content)
                denied = self.cli("state-show", "--package", package_hash, ok=False)
                self.assertIn("invalid state input binding", denied["error"])
                if original is None:
                    package["bindings"].pop(name)
                else:
                    package["bindings"][name] = original

    def test_no_authorization_keeps_exact_package_local_and_remote_unchanged(self):
        prepared = self.prepared()
        shown = self.cli("state-show", "--package", prepared["packageHash"])
        self.assertEqual(shown["package"], prepared["package"])
        result = self.cli("state-publish", "--package", prepared["packageHash"])
        self.assertEqual(result["status"], "awaiting-state-authorization")
        self.assertEqual(git(self.remote, "for-each-ref", "--format=%(refname)"), "")
        self.assertEqual(shown["package"]["records"][0]["after"]["lastAttemptAt"], NOW)
    def authorization(self, prepared, **changes):
        package = prepared["package"]
        value = {"schemaVersion": 1, "scope": "state-publication", "decision": "authorize",
                 "packageHash": prepared["packageHash"], "target": package["target"],
                 "ref": REF, "ids": package["ids"], "reviewer": "Controlled test fixture",
                 "statement": "Authorize this exact isolated test package", "authorizedAt": NOW}
        value.update(changes)
        path = self.session / (prepared["packageHash"] + "-authorization.json")
        put(path, value)
        return path

    def test_exact_authorization_publishes_one_orphan_state_commit(self):
        prepared = self.prepared()
        result = self.cli("state-publish", "--package", prepared["packageHash"],
                          "--authorization", str(self.authorization(prepared)))
        self.assertEqual(result["status"], "published")
        self.assertEqual(git(self.remote, "rev-list", "--count", REF), "1")
        self.assertEqual(git(self.remote, "ls-tree", "-r", "--name-only", REF), "banks/state/10.json")
        record = json.loads(git(self.remote, "show", REF + ":banks/state/10.json"))
        self.assertEqual(record, prepared["package"]["records"][0]["after"])
        self.assertNotIn("lastVerifiedAt", record)
    def test_race_different_banks_retries_nonforce_and_keeps_both_records(self):
        first = self.prepared(10)
        first_session = self.session
        first_auth = self.authorization(first)
        self.session = self.root / "second-session"
        second = self.prepared(20)
        second_auth = self.authorization(second)
        import shlex
        hook = self.session / "state-publication/objects.git/hooks/pre-push"
        command = [sys.executable, str(SCRIPT), "state-publish", "--session", str(first_session),
                   "--package", first["packageHash"], "--authorization", str(first_auth)]
        hook.write_text("#!/bin/sh\nunset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE\n"
                        + shlex.join(command) + " >/dev/null\n", encoding="utf-8")
        hook.chmod(0o700)
        result = self.cli("state-publish", "--package", second["packageHash"],
                          "--authorization", str(second_auth))
        self.assertEqual(result["status"], "published")
        self.assertEqual(git(self.remote, "rev-list", "--count", REF), "2")
        self.assertEqual(git(self.remote, "ls-tree", "-r", "--name-only", REF).splitlines(),
                         ["banks/state/10.json", "banks/state/20.json"])
        for package in (first, second):
            row = package["package"]["records"][0]
            self.assertEqual(json.loads(git(self.remote, "show", REF + ":" + row["path"])), row["after"])
    def test_unknown_push_outcome_reconciles_before_retry_without_duplicate_commit(self):
        import os
        import shutil
        prepared = self.prepared()
        approval = self.authorization(prepared)
        wrapper_dir = self.root / "bin"
        wrapper_dir.mkdir()
        real_git = shutil.which("git")
        wrapper = wrapper_dir / "git"
        hidden = self.root / "temporarily-unreachable.git"
        wrapper.write_text("#!/usr/bin/env python3\nimport os, subprocess, sys\n"
                           + f"code = subprocess.call([{real_git!r}, *sys.argv[1:]])\n"
                           + "if 'push' in sys.argv and code == 0:\n"
                           + f"    os.rename({str(self.remote)!r}, {str(hidden)!r})\n"
                           + "    sys.exit(1)\nsys.exit(code)\n")
        wrapper.chmod(0o700)
        command = [sys.executable, str(SCRIPT), "state-publish", "--session", str(self.session),
                   "--package", prepared["packageHash"], "--authorization", str(approval)]
        response = subprocess.run(command, capture_output=True, text=True,
                                  env={**os.environ, "PATH": str(wrapper_dir) + os.pathsep + os.environ["PATH"]})
        self.assertEqual(response.returncode, 0, response.stdout + response.stderr)
        self.assertEqual(json.loads(response.stdout)["status"], "outcome-unknown")
        unresolved = self.cli("state-publish", "--package", prepared["packageHash"],
                              "--authorization", str(approval))
        self.assertEqual(unresolved["status"], "outcome-unknown")
        hidden.rename(self.remote)
        (self.session / "state-publication" / (prepared["packageHash"] + ".receipt.tmp")).write_text("interrupted write")
        reconciled = self.cli("state-reconcile", "--package", prepared["packageHash"])
        self.assertEqual(reconciled["status"], "published")
        repeated = self.cli("state-publish", "--package", prepared["packageHash"],
                            "--authorization", str(approval))
        self.assertEqual(repeated["status"], "published")
        self.assertEqual(git(self.remote, "rev-list", "--count", REF), "1")
    def test_same_bank_race_stops_even_when_the_newer_attempt_has_later_date(self):
        first = self.prepared(10)
        first_session = self.session
        first_auth = self.authorization(first)
        self.session = self.root / "second-session"
        started = make_session(self)
        self.cli("continue", "--evidence", str(self.evidence(started, id=10, outcome="error",
                 checkedAt="2026-09-25T01:00:00Z", reason="Controlled later transport error")))
        second = self.cli("state-prepare", "--remote", str(self.remote), "--ids", "10")
        approval = self.authorization(second)
        import shlex
        hook = self.session / "state-publication/objects.git/hooks/pre-push"
        command = [sys.executable, str(SCRIPT), "state-publish", "--session", str(first_session),
                   "--package", first["packageHash"], "--authorization", str(first_auth)]
        hook.write_text("#!/bin/sh\nunset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE\n"
                        + shlex.join(command) + " >/dev/null\n", encoding="utf-8")
        hook.chmod(0o700)
        result = self.cli("state-publish", "--package", second["packageHash"], "--authorization", str(approval))
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(git(self.remote, "rev-list", "--count", REF), "1")
        self.assertEqual(json.loads(git(self.remote, "show", REF + ":banks/state/10.json")),
                         first["package"]["records"][0]["after"])
        self.assertEqual(self.cli("state-show", "--package", second["packageHash"])["package"], second["package"])

    def test_asset_permission_wrong_target_and_changed_values_never_authorize_state(self):
        prepared = self.prepared()
        for changes in ({"scope": "asset-publication"}, {"target": str(self.repo)},
                        {"ref": "refs/heads/main"}, {"ids": [20]}, {"packageHash": "0" * 64}):
            with self.subTest(changes=changes):
                result = self.cli("state-publish", "--package", prepared["packageHash"],
                                  "--authorization", str(self.authorization(prepared, **changes)), ok=False)
                self.assertIn("error", result)
        path = self.session / "state-publication" / (prepared["packageHash"] + ".json")
        changed = json.loads(path.read_text())
        changed["records"][0]["after"]["lastAttemptResult"] = "verified"
        put(path, changed)
        result = self.cli("state-publish", "--package", prepared["packageHash"],
                          "--authorization", str(self.authorization(prepared)), ok=False)
        self.assertIn("error", result)
        self.assertEqual(git(self.remote, "for-each-ref", "--format=%(refname)"), "")
    def test_each_bank_publishes_one_atomic_result_without_overwriting_another(self):
        records = {}
        for id in (10, 20):
            self.session = self.root / f"session-{id}"
            prepared = self.prepared(id)
            self.assertEqual(prepared["package"]["ids"], [id])
            published = self.cli("state-publish", "--package", prepared["packageHash"],
                                 "--authorization", str(self.authorization(prepared)))
            self.assertEqual(published["status"], "published")
            records[id] = prepared["package"]["records"][0]["after"]
            for prior_id, expected in records.items():
                self.assertEqual(json.loads(git(self.remote, "show", REF + f":banks/state/{prior_id}.json")), expected)
        self.assertEqual(git(self.remote, "rev-list", "--count", REF), "2")

    def test_changed_evidence_and_concurrent_writer_cannot_publish_prepared_values(self):
        import fcntl
        prepared = self.prepared()
        with (self.session / "state-publication/writer.lock").open("r") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            result = self.cli("state-publish", "--package", prepared["packageHash"],
                              "--authorization", str(self.authorization(prepared)), ok=False)
            self.assertIn("error", result)
        evidence = self.session / "evidence/10/validated.json"
        content = json.loads(evidence.read_text())
        content["reason"] = "Changed after the package was shown"
        put(evidence, content)
        result = self.cli("state-publish", "--package", prepared["packageHash"],
                          "--authorization", str(self.authorization(prepared)), ok=False)
        self.assertIn("error", result)
        self.assertEqual(git(self.remote, "for-each-ref", "--format=%(refname)"), "")
    def test_new_digest_and_permission_cannot_publish_an_unvalidated_state_record(self):
        import hashlib
        prepared = self.prepared()
        package = prepared["package"]
        package["records"][0]["after"]["lastVerifiedAt"] = NOW
        package["records"][0]["after"]["verifiedIconHash"] = fixtures.HASH
        content = (json.dumps(package, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
        replacement = {"package": package, "packageHash": hashlib.sha256(content).hexdigest()}
        (self.session / "state-publication" / (replacement["packageHash"] + ".json")).write_bytes(content)
        result = self.cli("state-publish", "--package", replacement["packageHash"],
                          "--authorization", str(self.authorization(replacement)), ok=False)
        self.assertIn("error", result)
        self.assertEqual(git(self.remote, "for-each-ref", "--format=%(refname)"), "")
    def test_interrupted_package_write_can_prepare_the_same_exact_values_again(self):
        import resource
        import signal
        self.prepared()
        second_remote = self.root / "second-remote.git"
        git(self.root, "init", "--bare", "-q", str(second_remote))
        def small_files():
            signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
            resource.setrlimit(resource.RLIMIT_FSIZE, (512, 512))
        command = [sys.executable, str(SCRIPT), "state-prepare", "--session", str(self.session),
                   "--remote", str(second_remote), "--ids", "10"]
        failed = subprocess.run(command, capture_output=True, text=True, preexec_fn=small_files)
        self.assertNotEqual(failed.returncode, 0)
        prepared = self.cli("state-prepare", "--remote", str(second_remote), "--ids", "10")
        shown = self.cli("state-show", "--package", prepared["packageHash"])
        self.assertEqual(shown["package"]["records"][0]["after"]["lastAttemptResult"], "unconfirmed")

    def test_dead_writer_index_does_not_block_authorized_retry(self):
        prepared = self.prepared()
        (self.session / "state-publication/state.index").write_bytes(b"interrupted Git index")
        result = self.cli("state-publish", "--package", prepared["packageHash"],
                          "--authorization", str(self.authorization(prepared)))
        self.assertEqual(result["status"], "published")
        self.assertEqual(git(self.remote, "rev-list", "--count", REF), "1")
    def test_preparing_an_already_published_result_does_not_add_an_empty_commit(self):
        original = self.prepared()
        self.cli("state-publish", "--package", original["packageHash"],
                 "--authorization", str(self.authorization(original)))
        repeated = self.cli("state-prepare", "--remote", str(self.remote), "--ids", "10")
        result = self.cli("state-publish", "--package", repeated["packageHash"],
                          "--authorization", str(self.authorization(repeated)))
        self.assertEqual(result["status"], "no-state-changes")
        self.assertEqual(git(self.remote, "rev-list", "--count", REF), "1")

if __name__ == "__main__":
    unittest.main()
