"""Continuation and human reconciliation through the public CLI and disposable Git remotes."""
from session_fixture import make_session
import hashlib
import json
import subprocess
import unittest

import test_attempts as attempts
import test_candidates as candidates
import test_reviewed_unchanged as reviewed
from test_attempts import NOW, git, put

REF = "refs/heads/banks-icon-agent-state"


class ReviewedContinuationCLI(unittest.TestCase):
    cli = reviewed.ReviewedUnchangedCLI.cli
    prepare_render = reviewed.ReviewedUnchangedCLI.prepare_render
    attempt = reviewed.ReviewedUnchangedCLI.attempt
    review_record = reviewed.ReviewedUnchangedCLI.review_record

    def setUp(self):
        reviewed.ReviewedUnchangedCLI.setUp(self)
        self.remote = self.session.parent / "remote.git"
        git(self.repo, "init", "--bare", "-q", str(self.remote))
        git(self.repo, "push", "-q", str(self.remote), f"{REF}:{REF}")

    def authorization(self, package):
        value = {"schemaVersion": 1, "scope": "state-publication", "decision": "authorize",
                 "packageHash": package["packageHash"], "target": package["package"]["target"],
                 "ref": REF, "ids": package["package"]["ids"], "reviewer": "Controlled reviewer",
                 "statement": "Authorize only this disposable state package", "authorizedAt": NOW}
        path = self.session / (package["packageHash"] + "-authorization.json")
        put(path, value)
        return path

    def test_published_needs_human_then_review_can_publish_new_verified_result(self):
        initial = self.cli("state-prepare", "--remote", str(self.remote), "--ids", "10")
        first_auth = self.authorization(initial)
        self.assertEqual(self.cli("state-publish", "--package", initial["packageHash"],
                                  "--authorization", str(first_auth))["status"], "published")
        self.cli("continue", "--review", str(self.review_path))
        next_package = self.cli("state-prepare", "--remote", str(self.remote), "--ids", "10")
        self.assertNotEqual(initial["packageHash"], next_package["packageHash"])
        self.assertEqual(next_package["package"]["reconciliation"]["priorPackages"],
                         [initial["packageHash"]])
        self.assertEqual(next_package["package"]["results"][0]["before"], self.prior_state)
        next_record = next_package["package"]["records"][0]
        self.assertEqual(next_record["before"], initial["package"]["records"][0]["after"])
        self.assertEqual(next_record["after"]["lastAttemptResult"], "verified")
        self.assertEqual(next_record["after"]["verifiedIconHash"], reviewed.sha(reviewed.SVG))
        self.assertEqual(next_record["after"]["lastVerifiedAt"], self.new_checked_at)
        self.assertEqual(next_record["after"]["customHistory"], self.prior_state["customHistory"])
        denied = self.cli("state-publish", "--package", next_package["packageHash"],
                          "--authorization", str(first_auth), ok=False)
        self.assertIn("error", denied)
        self.assertEqual(self.cli("state-publish", "--package", next_package["packageHash"],
                                  "--authorization", str(self.authorization(next_package)))["status"], "published")
        remote_state = json.loads(git(self.remote, "show", f"{REF}:banks/state/10.json"))
        self.assertEqual(remote_state, next_record["after"])
        self.assertEqual(git(self.remote, "rev-list", "--count", REF), "4")


class SameBankResolutionCLI(unittest.TestCase):
    bank = attempts.AttemptCLI.bank
    commit = attempts.AttemptCLI.commit
    state = attempts.AttemptCLI.state
    cli = attempts.AttemptCLI.cli
    evidence = attempts.AttemptCLI.evidence

    def setUp(self):
        attempts.AttemptCLI.setUp(self)
        self.remote = self.root / "remote.git"
        git(self.root, "init", "--bare", "-q", str(self.remote))

    def authorization(self, package):
        value = {"schemaVersion": 1, "scope": "state-publication", "decision": "authorize",
                 "packageHash": package["packageHash"], "target": package["package"]["target"],
                 "ref": REF, "ids": package["package"]["ids"], "reviewer": "Controlled reviewer",
                 "statement": "Authorize only this disposable state package", "authorizedAt": NOW}
        path = self.session / (package["packageHash"] + "-authorization.json")
        put(path, value)
        return path

    def test_other_writer_same_bank_requires_exact_resolution_and_new_authorization(self):
        prior = {"schemaVersion": 1, "lastVerifiedAt": "2025-01-01T00:00:00Z",
                 "verifiedIconHash": attempts.HASH,
                 "otherBankHistory": {"neverOverwrite": True}}
        self.state(prior)
        git(self.repo, "push", "-q", str(self.remote), f"{REF}:{REF}")
        started = make_session(self)
        self.cli("continue", "--evidence", str(self.evidence(started, id=10,
                 outcome="error", checkedAt="2026-09-25T01:00:00Z",
                 reason="Controlled later error")))
        original = self.cli("state-prepare", "--remote", str(self.remote), "--ids", "10")
        old_auth = self.authorization(original)

        other = self.root / "other"
        git(self.root, "clone", "-q", "-b", "banks-icon-agent-state", str(self.remote), str(other))
        git(other, "config", "user.name", "Other writer")
        git(other, "config", "user.email", "other@example.invalid")
        competing = {**prior, "lastAttemptAt": "2026-09-25T00:00:01Z",
                     "lastAttemptResult": "unconfirmed", "lastVerifiedAt": "2026-01-01T00:00:00Z",
                     "verifiedIconHash": "b" * 64, "otherWriterField": {"keep": [1, 2]}}
        put(other / "banks/state/10.json", competing)
        put(other / "banks/state/20.json", {"schemaVersion": 1,
            "lastVerifiedAt": "2025-12-01T00:00:00Z", "verifiedIconHash": "a" * 64,
            "otherBankHistory": ["retained"]})
        git(other, "add", ".")
        git(other, "commit", "-qm", "other writer records")
        git(other, "push", "-q", "origin", f"HEAD:{REF}")

        refused = self.cli("state-prepare", "--remote", str(self.remote), "--ids", "10", ok=False)
        self.assertIn("error", refused)
        old_result = self.cli("state-publish", "--package", original["packageHash"],
                              "--authorization", str(old_auth))
        self.assertEqual(old_result["status"], "conflict")
        self.assertEqual(self.cli("state-reconcile", "--package", original["packageHash"])["status"],
                         "conflict")

        local = original["package"]["records"][0]["after"]
        remote_bytes = subprocess.run(["git", "-C", str(self.remote), "show", f"{REF}:banks/state/10.json"],
                                      capture_output=True, check=True).stdout
        local_bytes = (json.dumps(local, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
        resolution = {"schemaVersion": 1, "target": str(self.remote), "ref": REF,
                      "records": [{"id": 10,
                                   "beforeSha256": hashlib.sha256(remote_bytes).hexdigest(),
                                   "localAfterSha256": hashlib.sha256(local_bytes).hexdigest(),
                                   "decision": "apply-local-attempt-preserving-history"}],
                      "reviewer": "Controlled reviewer", "statement": "Preserve concurrent history; apply this attempt",
                      "resolvedAt": NOW}
        decision_file = self.session / "evidence/10/reconciliation.json"
        for invalid, reason in (
            ({**resolution, "target": str(self.repo)}, "target"),
            ({**resolution, "records": [{**resolution["records"][0], "beforeSha256": "0" * 64}]}, "exact remote"),
            ({**resolution, "records": [{**resolution["records"][0], "localAfterSha256": "0" * 64}]}, "exact remote"),
            ({**resolution, "resolvedAt": "2099-01-01T00:00:00Z"}, "future"),
        ):
            with self.subTest(invalid=invalid):
                put(decision_file, invalid)
                refusal = self.cli("state-prepare", "--remote", str(self.remote), "--ids", "10",
                                   "--resolution", str(decision_file), ok=False)
                self.assertIn("error", refusal)
        put(decision_file, resolution)
        package = self.cli("state-prepare", "--remote", str(self.remote), "--ids", "10",
                           "--resolution", str(decision_file))
        self.assertEqual(package["package"]["reconciliation"]["resolution"], resolution)
        self.assertNotEqual(package["packageHash"], original["packageHash"])
        record = package["package"]["records"][0]
        self.assertEqual(record["before"], competing)
        self.assertEqual(record["after"]["lastAttemptResult"], "error")
        self.assertEqual(record["after"]["otherWriterField"], competing["otherWriterField"])
        self.assertEqual(record["after"]["verifiedIconHash"], competing["verifiedIconHash"])
        denied = self.cli("state-publish", "--package", package["packageHash"],
                          "--authorization", str(old_auth), ok=False)
        self.assertIn("error", denied)
        self.assertEqual(self.cli("state-publish", "--package", package["packageHash"],
                                  "--authorization", str(self.authorization(package)))["status"], "published")
        self.assertEqual(json.loads(git(self.remote, "show", f"{REF}:banks/state/20.json")),
                         {"schemaVersion": 1, "lastVerifiedAt": "2025-12-01T00:00:00Z",
                          "verifiedIconHash": "a" * 64, "otherBankHistory": ["retained"]})
        self.assertEqual(json.loads(git(self.remote, "show", f"{REF}:banks/state/10.json")), record["after"])


class CandidateContinuationCLI(unittest.TestCase):
    cli = candidates.CandidateCLI.cli
    file = candidates.CandidateCLI.file
    create_package = candidates.CandidateCLI.create_package
    authorization = ReviewedContinuationCLI.authorization

    def setUp(self):
        candidates.CandidateCLI.setUp(self)
        self.remote = self.root / "remote.git"
        git(self.repo, "init", "--bare", "-q", str(self.remote))
        git(self.repo, "push", "-q", str(self.remote), f"{REF}:{REF}")

    def test_published_raw_candidate_continues_as_same_prepared_pending_proposal(self):
        raw = self.cli("state-prepare", "--remote", str(self.remote), "--ids", "10")
        self.assertEqual(self.cli("state-publish", "--package", raw["packageHash"],
                                  "--authorization", str(self.authorization(raw)))["status"], "published")
        self.cli("continue", "--candidate", str(self.package_path))
        prepared = self.cli("state-prepare", "--remote", str(self.remote), "--ids", "10")
        record = prepared["package"]["records"][0]
        self.assertEqual(prepared["package"]["reconciliation"]["priorPackages"], [raw["packageHash"]])
        self.assertEqual(record["before"], raw["package"]["records"][0]["after"])
        self.assertEqual(record["after"]["lastVerifiedAt"], "2025-01-01T00:00:00Z")
        self.assertEqual(record["after"]["verifiedIconHash"], candidates.digest(candidates.BASE))
        self.assertEqual(len(record["after"]["proposals"]), 1)
        proposal = record["after"]["proposals"][0]
        self.assertEqual(proposal["sourceCandidate"]["iconHash"], candidates.digest(candidates.RAW))
        self.assertEqual(proposal["candidateIconHash"], candidates.digest(candidates.FINAL))
        self.assertEqual(proposal["status"], "pending")
        self.assertNotIn("mr", proposal)
        self.assertEqual(self.cli("state-publish", "--package", prepared["packageHash"],
                                  "--authorization", str(self.authorization(prepared)))["status"], "published")
        self.assertEqual(json.loads(git(self.remote, "show", f"{REF}:banks/state/10.json")), record["after"])

    def test_published_reconciled_raw_prepares_without_new_resolution_and_keeps_other_history(self):
        decision, other = self.conflicting_proposal({"status": "rejected",
            "baseIconHash": candidates.digest(candidates.BASE), "candidateIconHash": "a" * 64,
            "rejectionReason": "Different earlier mark was rejected"})
        raw = self.cli("state-prepare", "--remote", str(self.remote), "--ids", "10",
                       "--resolution", str(decision))
        self.assertEqual(self.cli("state-publish", "--package", raw["packageHash"],
                                  "--authorization", str(self.authorization(raw)))["status"], "published")
        raw_after = raw["package"]["records"][0]["after"]
        self.assertEqual(raw_after["proposals"][0], other["proposals"][0])
        self.cli("continue", "--candidate", str(self.package_path))

        prepared = self.cli("state-prepare", "--remote", str(self.remote), "--ids", "10")
        self.assertIsNone(prepared["package"]["reconciliation"]["resolution"])
        self.assertEqual(prepared["package"]["reconciliation"]["priorPackages"], [raw["packageHash"]])
        record = prepared["package"]["records"][0]
        self.assertEqual(record["before"], raw_after)
        self.assertEqual(record["after"]["proposals"][:-1], other["proposals"])
        self.assertEqual(len(record["after"]["proposals"]), 2)
        self.assertEqual(record["after"]["proposals"][-1]["sourceCandidate"]["iconHash"],
                         candidates.digest(candidates.RAW))
        self.assertEqual(record["after"]["proposals"][-1]["candidateIconHash"],
                         candidates.digest(candidates.FINAL))
        self.assertEqual(self.cli("state-publish", "--package", prepared["packageHash"],
                                  "--authorization", str(self.authorization(prepared)))["status"], "published")
        self.assertEqual(json.loads(git(self.remote, "show", f"{REF}:banks/state/10.json")), record["after"])
        repeated = self.cli("state-prepare", "--remote", str(self.remote), "--ids", "10")
        self.assertIsNone(repeated["package"]["reconciliation"]["resolution"])
        self.assertEqual(repeated["package"]["reconciliation"]["priorPackages"],
                         [prepared["packageHash"]])
        self.assertEqual(repeated["package"]["records"][0]["after"], record["after"])

    def conflicting_proposal(self, proposal):
        other = self.root / "other"
        git(self.root, "clone", "-q", "-b", "banks-icon-agent-state", str(self.remote), str(other))
        git(other, "config", "user.name", "Other writer")
        git(other, "config", "user.email", "other@example.invalid")
        before = json.loads(git(self.remote, "show", f"{REF}:banks/state/10.json"))
        before["proposals"] = [proposal]
        put(other / "banks/state/10.json", before)
        git(other, "add", ".")
        git(other, "commit", "-qm", "concurrent proposal")
        git(other, "push", "-q", "origin", f"HEAD:{REF}")
        remote_bytes = subprocess.run(["git", "-C", str(self.remote), "show", f"{REF}:banks/state/10.json"],
                                      capture_output=True, check=True).stdout
        local = self.attempted["banks"][0]["state"]
        local_bytes = (json.dumps(local, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
        resolution = {"schemaVersion": 1, "target": str(self.remote), "ref": REF,
                      "records": [{"id": 10, "beforeSha256": hashlib.sha256(remote_bytes).hexdigest(),
                                   "localAfterSha256": hashlib.sha256(local_bytes).hexdigest(),
                                   "decision": "apply-local-attempt-preserving-history"}],
                      "reviewer": "Controlled reviewer", "statement": "Attempt to reconcile candidate history",
                      "resolvedAt": NOW}
        path = self.evidence / "resolution.json"
        put(path, resolution)
        return path, before

    def test_active_unrelated_proposal_requires_dedicated_recovery(self):
        decision, before = self.conflicting_proposal({"status": "pending",
            "baseIconHash": candidates.digest(candidates.BASE), "candidateIconHash": "a" * 64})
        refused = self.cli("state-prepare", "--remote", str(self.remote), "--ids", "10",
                           "--resolution", str(decision), ok=False)
        self.assertIn("error", refused)
        self.assertEqual(json.loads(git(self.remote, "show", f"{REF}:banks/state/10.json")), before)

    def test_rejected_same_candidate_cannot_be_appended_under_resolution(self):
        decision, before = self.conflicting_proposal({"status": "rejected",
            "baseIconHash": candidates.digest(candidates.BASE),
            "candidateIconHash": candidates.digest(candidates.RAW),
            "rejectionReason": "Prior review rejected this mark"})
        refused = self.cli("state-prepare", "--remote", str(self.remote), "--ids", "10",
                           "--resolution", str(decision), ok=False)
        self.assertIn("error", refused)
        self.assertEqual(json.loads(git(self.remote, "show", f"{REF}:banks/state/10.json")), before)

    def test_rejected_prepared_candidate_refuses_same_raw_source_under_resolution(self):
        decision, before = self.conflicting_proposal({"status": "rejected",
            "baseIconHash": candidates.digest(candidates.BASE),
            "candidateIconHash": candidates.digest(candidates.FINAL),
            "sourceCandidate": {"iconHash": candidates.digest(candidates.RAW)},
            "rejectionReason": "Prior review rejected this source mark"})
        refused = self.cli("state-prepare", "--remote", str(self.remote), "--ids", "10",
                           "--resolution", str(decision), ok=False)
        self.assertIn("error", refused)
        self.assertEqual(json.loads(git(self.remote, "show", f"{REF}:banks/state/10.json")), before)

    def test_distinct_new_candidate_appends_without_erasing_rejected_history(self):
        decision, before = self.conflicting_proposal({"status": "rejected",
            "baseIconHash": candidates.digest(candidates.BASE), "candidateIconHash": "a" * 64,
            "rejectionReason": "Different earlier mark was rejected"})
        package = self.cli("state-prepare", "--remote", str(self.remote), "--ids", "10",
                           "--resolution", str(decision))
        after = package["package"]["records"][0]["after"]
        self.assertEqual(after["proposals"][0], before["proposals"][0])
        self.assertEqual(after["proposals"][1]["candidateIconHash"], candidates.digest(candidates.RAW))
        self.assertEqual(after["proposals"][1]["status"], "pending")
        self.assertNotIn("mr", after["proposals"][1])
        self.assertEqual(after["verifiedIconHash"], before["verifiedIconHash"])
        self.assertEqual(self.cli("state-publish", "--package", package["packageHash"],
                                  "--authorization", str(self.authorization(package)))["status"], "published")
        self.assertEqual(json.loads(git(self.remote, "show", f"{REF}:banks/state/10.json")), after)


class FirstIconContinuationCLI(unittest.TestCase):
    base_icon = None
    cli = candidates.CandidateCLI.cli
    file = candidates.CandidateCLI.file
    create_package = candidates.CandidateCLI.create_package
    setUp = CandidateContinuationCLI.setUp
    authorization = ReviewedContinuationCLI.authorization
    conflicting_proposal = CandidateContinuationCLI.conflicting_proposal

    def test_null_original_base_survives_reconciled_raw_and_prepared_publication(self):
        historical = {"status": "rejected", "baseIconHash": None,
                      "candidateIconHash": "a" * 64,
                      "rejectionReason": "A different first-icon mark was rejected"}
        decision, competing = self.conflicting_proposal(historical)
        raw = self.cli("state-prepare", "--remote", str(self.remote), "--ids", "10",
                       "--resolution", str(decision))
        raw_after = raw["package"]["records"][0]["after"]
        self.assertEqual(raw_after["proposals"][0], historical)
        self.assertIsNone(raw_after["proposals"][-1]["baseIconHash"])
        self.assertEqual(raw_after["proposals"][-1]["candidateIconHash"], candidates.digest(candidates.RAW))
        self.assertEqual(self.cli("state-publish", "--package", raw["packageHash"],
                                  "--authorization", str(self.authorization(raw)))["status"], "published")
        self.assertEqual(json.loads(git(self.remote, "show", f"{REF}:banks/state/10.json")), raw_after)

        self.cli("continue", "--candidate", str(self.package_path))
        prepared = self.cli("state-prepare", "--remote", str(self.remote), "--ids", "10")
        record = prepared["package"]["records"][0]
        after = record["after"]
        self.assertEqual(record["before"], raw_after)
        self.assertEqual(prepared["package"]["reconciliation"]["priorPackages"], [raw["packageHash"]])
        self.assertIsNone(prepared["package"]["reconciliation"]["resolution"])
        self.assertEqual(after["proposals"][:-1], competing["proposals"])
        proposal = after["proposals"][-1]
        self.assertIsNone(proposal["baseIconHash"])
        self.assertEqual(proposal["sourceCandidate"]["iconHash"], candidates.digest(candidates.RAW))
        self.assertEqual(proposal["candidateIconHash"], candidates.digest(candidates.FINAL))
        self.assertEqual(proposal["status"], "pending")
        self.assertNotIn("verifiedIconHash", after)
        self.assertNotIn("lastVerifiedAt", after)
        self.assertEqual(after["customHistory"], {"keep": True})
        icon = str(self.session / "worktree/banks/Alpha_10-ru/icon.svg")
        self.assertIsNone(prepared["package"]["bindings"][icon])
        self.assertEqual(self.cli("state-publish", "--package", prepared["packageHash"],
                                  "--authorization", str(self.authorization(prepared)))["status"], "published")
        self.assertEqual(json.loads(git(self.remote, "show", f"{REF}:banks/state/10.json")), after)
        repeated = self.cli("state-prepare", "--remote", str(self.remote), "--ids", "10")
        self.assertEqual(repeated["package"]["records"][0]["after"], after)
        self.assertEqual(repeated["package"]["reconciliation"]["priorPackages"], [prepared["packageHash"]])
