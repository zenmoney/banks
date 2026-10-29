"""Completion checks against disposable real Git remotes and recorded CLI results."""
from session_fixture import make_session
import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path

import test_attempts as fixtures
from test_attempts import NOW, git, put
import test_state_publication as publication
import test_state_reconciliation as reconciliation

REF = publication.REF

WORKFLOW = Path(__file__).resolve().parents[1] / "scripts/workflow.py"
SESSION = WORKFLOW.with_name("session.py")


class StateCheckCLI(unittest.TestCase):
    bank = fixtures.AttemptCLI.bank
    commit = fixtures.AttemptCLI.commit
    cli = fixtures.AttemptCLI.cli
    evidence = fixtures.AttemptCLI.evidence
    positive = fixtures.AttemptCLI.positive
    authorization = publication.StatePublicationCLI.authorization

    def setUp(self):
        fixtures.AttemptCLI.setUp(self)
        self.remote = self.root / "remote.git"
        git(self.root, "init", "--bare", "-q", str(self.remote))
        git(self.repo, "remote", "add", "origin", str(self.remote))

    def start(self, id=10):
        return make_session(self, bank_id=id)

    def check(self, complete=False):
        process = subprocess.run([sys.executable, str(WORKFLOW), "state-check", "--session", str(self.session)],
                                 capture_output=True, text=True)
        result = json.loads(process.stdout)
        self.assertEqual(process.returncode, 0 if complete else 1, result)
        self.assertEqual(result["status"], "state-complete" if complete else "state-incomplete", result)
        for bank in result["banks"]:
            full = json.loads(Path(bank["resultFile"]["path"]).read_text())
            self.assertEqual(full.get("status", "blocked"), bank["status"])
        return result

    def publish(self, id=10):
        prepared = self.cli("state-prepare", "--ids", str(id))
        self.assertEqual(self.cli("state-publish", "--package", prepared["packageHash"],
                                 "--authorization", str(self.authorization(prepared)))["status"], "published")
        return prepared

    def candidate(self, started):
        raw = b'<svg xmlns="http://www.w3.org/2000/svg"><circle r="5"/></svg>'
        path = self.session / "evidence/10/candidate.svg"
        path.parent.mkdir(parents=True)
        path.write_bytes(raw)
        evidence = self.positive(started, "candidate", candidate={"path": "evidence/10/candidate.svg",
            "iconHash": hashlib.sha256(raw).hexdigest(), "sourceUrl": "https://example.invalid/bank",
            "sourceAssetUrl": "https://example.invalid/logo.svg", "rationale": "Distinct newer mark"})
        self.cli("continue", "--evidence", str(evidence))

    def test_raw_candidate_requires_publication_and_new_session_preserves_pending(self):
        self.candidate(self.start())
        self.assertEqual(self.check()["banks"][0]["status"], "awaiting-state-authorization")
        self.assertEqual(git(self.remote, "for-each-ref", "--format=%(refname)"), "")
        self.publish()
        checked = self.check(True)
        self.assertEqual(checked["banks"][0]["status"], "no-state-changes")
        self.assertFalse(checked['taskComplete'])
        self.assertEqual(checked['pendingIds'], [10])
        self.assertEqual(git(self.remote, "rev-list", "--count", REF), "1")
        self.session = self.root / "next"
        self.assertEqual(self.start()["banks"][0]["point"], "waiting-proposal")

    def test_completed_bank_does_not_hide_missing_result_in_next_session(self):
        started = self.start()
        self.cli("continue", "--evidence", str(self.evidence(started)))
        self.publish()
        self.assertTrue(self.check(True)["taskComplete"])
        completed = (self.session / "session.json").read_bytes()
        first = self.session
        self.session = self.root / "second"
        self.start(20)
        result = self.check()
        self.assertEqual([(b["id"], b["complete"]) for b in result["banks"]], [(20, False)])
        self.assertEqual((first / "session.json").read_bytes(), completed)

    def test_newer_local_result_is_not_covered_by_published_earlier_package(self):
        started = self.start()
        self.cli("continue", "--evidence", str(self.evidence(started)))
        self.publish()
        self.check(True)
        # Record a fresh valid result in a new explicit session against the same remote.
        self.session = self.root / "newer"
        started = self.start()
        self.cli("continue", "--evidence", str(self.evidence(started, checkedAt="2026-09-26T00:00:00Z")))
        self.assertFalse(self.check()["banks"][0]["complete"])
        self.assertEqual(git(self.remote, "rev-list", "--count", REF), "1")

    def test_invalid_result_and_unavailable_remote_are_incomplete(self):
        started = self.start()
        self.cli("continue", "--evidence", str(self.evidence(started)))
        report = self.session / "reports/10.json"
        original = report.read_text()
        put(report, {"id": 10})
        self.assertEqual(self.check()["banks"][0]["status"], "blocked")
        report.write_text(original)
        # A concrete target that disappears is an error, not remote absence.
        self.remote.rename(self.root / "unavailable.git")
        self.assertEqual(self.check()["banks"][0]["status"], "blocked")

    def test_empty_or_mismatched_workspace_cannot_skip_results(self):
        self.start()
        path = self.session / "session.json"
        original = json.loads(path.read_text())
        for changes in ({"banks": []}, {"selectedIds": []}, {"selectedIds": [], "banks": []},
                        {"schemaVersion": 1}):
            with self.subTest(changes=changes):
                put(path, {**original, **changes})
                self.check()

    def test_conflicting_remote_state_is_not_complete(self):
        started = self.start()
        self.cli("continue", "--evidence", str(self.evidence(started)))
        self.publish()
        other = self.root / "other"
        git(self.root, "clone", "-q", "-b", "banks-icon-agent-state", str(self.remote), str(other))
        git(other, "config", "user.name", "Other")
        git(other, "config", "user.email", "other@example.invalid")
        put(other / "banks/state/10.json", {"schemaVersion": 1,
            "lastAttemptAt": "2026-09-26T00:00:00Z", "lastAttemptResult": "error"})
        git(other, "add", ".")
        git(other, "commit", "-qm", "Competing record")
        git(other, "push", "-q", "origin", REF)
        result = self.check()
        self.assertFalse(result["banks"][0]["complete"])
        self.assertEqual(git(self.remote, "rev-list", "--count", REF), "2")


    def test_coordinator_lock_prevents_overlapping_check(self):
        import fcntl
        self.start()
        with (self.session / ".workflow-writer.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            process = subprocess.run([sys.executable, str(WORKFLOW), "state-check", "--session", str(self.session)],
                                     capture_output=True, text=True)
            self.assertEqual(process.returncode, 1)
            self.assertEqual(json.loads(process.stdout)["status"], "state-incomplete")


class StateCheckContinuationCLI(unittest.TestCase):
    cli = reconciliation.ReviewedContinuationCLI.cli
    prepare_render = reconciliation.ReviewedContinuationCLI.prepare_render
    attempt = reconciliation.ReviewedContinuationCLI.attempt
    review_record = reconciliation.ReviewedContinuationCLI.review_record
    authorization = reconciliation.ReviewedContinuationCLI.authorization
    check = StateCheckCLI.check

    def setUp(self):
        reconciliation.ReviewedContinuationCLI.setUp(self)
        git(self.repo, "remote", "add", "origin", str(self.remote))

    def test_published_version_does_not_cover_new_review_in_same_session(self):
        first = self.cli("state-prepare", "--ids", "10")
        self.assertEqual(self.cli("state-publish", "--package", first["packageHash"],
            "--authorization", str(self.authorization(first)))["status"], "published")
        self.check(True)
        self.cli("continue", "--review", str(self.review_path))
        checked = self.check()
        self.assertEqual(checked["banks"][0]["status"], "awaiting-state-authorization")
        self.assertNotEqual(checked["banks"][0]["packageHash"], first["packageHash"])


if __name__ == "__main__":
    unittest.main()
