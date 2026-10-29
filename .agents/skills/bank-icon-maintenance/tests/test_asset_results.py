"""Candidate approval and MR facts through the external session CLI and disposable Git catalogs.

The PNG/XML bytes are controlled schema fixtures, not graphical verification.
"""
import json
import unittest
import test_candidates as candidates

FINAL, BASE = candidates.FINAL, candidates.BASE
digest, put_json = candidates.digest, candidates.put_json



class AssetResultsCLI(unittest.TestCase):
    def setUp(self):
        fixture = candidates.CandidateCLI("test_preparation_preserves_original_report_and_current_icon_pending_human_review")
        self.addCleanup(fixture.doCleanups)
        fixture.base_icon = getattr(self, "base_icon", candidates.BASE)
        fixture.setUp()
        self.fixture = fixture
        self.session = fixture.session
        self.evidence = fixture.evidence
        self.prepared = fixture.cli("continue", "--candidate", str(fixture.package_path))["banks"][0]

    def cli(self, command, *args, ok=True):
        return self.fixture.cli(command, *args, ok=ok)

    def approval(self):
        package = self.prepared["state"]["proposals"][-1]["reviewPackage"]
        provenance = self.fixture.file("candidate-review-quote.txt",
            b"Reviewer approved this exact bank candidate after visual review.")
        value = {"schemaVersion": 1, "scope": "candidate-review", "decision": "approve", "id": 10,
                 "baseCommit": self.fixture.started["baseCommit"],
                 "baseIconHash": self.fixture.started["banks"][0]["iconHash"],
                 "candidateIconHash": digest(FINAL), "reviewPackage": package,
                 "reviewer": "Human reviewer", "statement": "Approved exact before/after candidate",
                 "reviewedAt": "2026-09-25T12:00:00Z", "provenance": provenance}
        path = self.evidence / "candidate-approval.json"
        put_json(path, value)
        return path

    def test_exact_approval_preserves_original_package_and_catalog(self):
        original = {name: (self.session / name).read_bytes() for name in
                    ("reports/10.json", self.prepared["report"],
                     self.prepared["state"]["proposals"][-1]["reviewPackage"]["path"])}
        approved = self.cli("candidate-approve", "--id", "10", "--approval", str(self.approval()))
        bank = approved["banks"][0]
        self.assertEqual(bank["point"], "waiting-proposal")
        self.assertEqual(bank["state"]["proposals"][-1]["humanReview"], "approved")
        self.assertEqual(bank["state"]["proposals"][-1]["status"], "pending")
        self.assertEqual(bank["state"]["lastVerifiedAt"], "2025-01-01T00:00:00Z")
        self.assertEqual((self.session / "worktree/banks" / self.fixture.folder / "icon.svg").read_bytes(), BASE)
        for name, content in original.items():
            self.assertEqual((self.session / name).read_bytes(), content)
        self.assertEqual(self.cli("continue")["banks"][0]["point"], "waiting-proposal")

    def test_pause_then_continue_approved_candidate_revalidates_without_losing_review(self):
        self.cli("candidate-approve", "--id", "10", "--approval", str(self.approval()))
        self.assertEqual(self.cli("pause")["banks"][0]["point"], "paused")
        bank = self.cli("continue")["banks"][0]
        self.assertEqual(bank["point"], "waiting-proposal")
        self.assertEqual(bank["state"]["proposals"][-1]["humanReview"], "approved")

    def test_wrong_candidate_or_review_package_approval_cannot_advance_state(self):
        source = self.approval()
        value = json.loads(source.read_text())
        before = (self.session / "session.json").read_bytes()
        for field in ("candidateIconHash", "reviewPackage"):
            invalid = dict(value)
            invalid[field] = "0" * 64 if field == "candidateIconHash" else {**value[field], "sha256": "0" * 64}
            put_json(source, invalid)
            result = self.cli("candidate-approve", "--id", "10", "--approval", str(source), ok=False)
            self.assertIn("error", result)
            self.assertEqual((self.session / "session.json").read_bytes(), before)
            self.assertEqual(self.cli("continue")["banks"][0]["state"]["proposals"][-1]["humanReview"], "pending")


    def test_changed_review_quote_stops_approved_continuation_without_reapproval(self):
        source = self.approval()
        approved = self.cli("candidate-approve", "--id", "10", "--approval", str(source))
        quote = json.loads(source.read_text())["provenance"]["path"]
        (self.session / quote).write_text("Different later words do not authorize this candidate")
        result = self.cli("continue")["banks"][0]
        self.assertEqual(result["point"], "needs-human")
        self.assertEqual(result["state"], approved["banks"][0]["state"])

class FirstIconAssessmentCLI(unittest.TestCase):
    base_icon = None
    setUp = AssetResultsCLI.setUp
    cli = AssetResultsCLI.cli
    approval = AssetResultsCLI.approval

    def test_approval_does_not_invent_current_verification_or_materialize_baseline(self):
        before = json.loads((self.session / "session.json").read_bytes())["banks"][0]["state"]
        approved = self.cli("candidate-approve", "--id", "10", "--approval", str(self.approval()))
        bank = approved["banks"][0]
        self.assertIsNone(bank["iconHash"])
        self.assertIsNone(bank["state"]["proposals"][-1]["baseIconHash"])
        self.assertEqual(bank["state"].get("lastVerifiedAt"), before.get("lastVerifiedAt"))
        self.assertEqual(bank["state"].get("verifiedIconHash"), before.get("verifiedIconHash"))
        self.assertFalse((self.session / "worktree/banks" / self.fixture.folder / "icon.svg").exists())
        self.assertEqual(self.cli("continue")["banks"][0]["state"], bank["state"])

    def test_appeared_icon_blocks_approval_and_preserves_report(self):
        approval = self.approval()
        report = (self.session / "reports/10.json").read_bytes()
        icon = self.session / "worktree/banks" / self.fixture.folder / "icon.svg"
        icon.write_bytes(BASE)
        self.cli("candidate-approve", "--id", "10", "--approval", str(approval), ok=False)
        self.assertEqual((self.session / "reports/10.json").read_bytes(), report)
        self.assertEqual(icon.read_bytes(), BASE)


class ConfirmedMRStateCLI(unittest.TestCase):
    """Confirmed controlled-provider observations through session CLI, never hosted proof."""

    def setUp(self):
        import test_asset_publication as publication
        fixture = publication.ApprovedAssetCLI("test_approved_candidate_applies_only_exact_icon_on_target_base")
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.fixture = fixture
        self.session, self.repo, self.remote = fixture.session, fixture.repo, fixture.remote

    def cli(self, command, *args, ok=True):
        return self.fixture.cli(command, *args, ok=ok)

    def state_package(self):
        return self.cli("state-prepare", "--remote", str(self.remote), "--ids", "10")

    def publish_state(self, package):
        value = {"schemaVersion": 1, "scope": "state-publication", "decision": "authorize",
                 "packageHash": package["packageHash"], "target": str(self.remote),
                 "ref": "refs/heads/banks-icon-agent-state", "ids": [10],
                 "reviewer": "Controlled reviewer", "statement": "Authorize this disposable state package",
                 "authorizedAt": "2026-09-25T12:02:00Z"}
        path = self.session / "state-authorization.json"
        put_json(path, value)
        published = self.cli("state-publish", "--package", package["packageHash"],
                             "--authorization", str(path))
        self.assertEqual(published["status"], "published")

    def test_own_approved_then_open_then_closed_mr_preserves_undecided_proposal(self):
        approved = self.state_package()
        self.publish_state(approved)
        prepared = self.fixture.prepare()
        self.fixture.package_hash = prepared["packageHash"]
        confirmed = self.cli("asset-publish", "--package", prepared["packageHash"],
                             "--authorization", str(self.fixture.authorization(prepared["package"])))
        self.assertEqual(confirmed["status"], "mr-confirmed")
        opened = self.state_package()
        self.assertEqual(opened["package"]["records"][0]["before"],
                         approved["package"]["records"][0]["after"])
        self.assertEqual(opened["package"]["records"][0]["after"]["proposals"][-1]["status"], "open")
        self.publish_state(opened)
        provider = json.loads(self.fixture.gh_state.read_text())
        provider["prs"][0]["state"] = "closed"
        self.fixture.gh_state.write_text(json.dumps(provider))
        closed = self.cli("asset-reconcile", "--package", prepared["packageHash"])
        self.assertEqual(closed["status"], "mr-confirmed")
        stopped = self.cli("continue")["banks"][0]
        self.assertEqual(stopped["point"], "needs-human")
        proposal = stopped["state"]["proposals"][-1]
        self.assertEqual(proposal["status"], "pending")
        self.assertEqual(proposal["mr"]["state"], "closed")
        self.assertEqual(stopped["state"]["lastVerifiedAt"], "2025-01-01T00:00:00Z")
        terminal = self.state_package()
        self.assertEqual(terminal["package"]["records"][0]["before"],
                         opened["package"]["records"][0]["after"])
        self.assertEqual(terminal["package"]["records"][0]["after"]["proposals"][-1], proposal)

    def test_unrelated_target_base_move_keeps_first_mr_observation_and_receipts_fresh_base(self):
        prepared = self.fixture.prepare()
        self.fixture.package_hash = prepared["packageHash"]
        published = self.cli("asset-publish", "--package", prepared["packageHash"],
                             "--authorization", str(self.fixture.authorization(prepared["package"])))
        self.assertEqual(published["status"], "mr-confirmed")
        initial = self.cli("continue")["banks"][0]
        original_report = (self.session / initial["report"]).read_bytes()
        original_observation = initial["state"]["proposals"][-1]["mr"]
        target = self.fixture.target
        (target / "unrelated-again.txt").write_text("target base moved without changing selected bank")
        candidates.git(target, "add", "unrelated-again.txt")
        candidates.git(target, "commit", "-qm", "unrelated target base move")
        moved_base = candidates.git(target, "rev-parse", "HEAD")
        candidates.git(target, "push", "-q", str(self.remote), "HEAD:refs/heads/main")
        provider = json.loads(self.fixture.gh_state.read_text())
        provider["prs"][0]["base"]["sha"] = moved_base
        self.fixture.gh_state.write_text(json.dumps(provider))
        current = self.cli("asset-reconcile", "--package", prepared["packageHash"])
        self.assertEqual(current["status"], "mr-confirmed")
        self.assertEqual(current["mr"]["baseCommit"], moved_base)
        retained = self.cli("continue")["banks"][0]
        self.assertEqual(retained["state"]["proposals"][-1]["mr"], original_observation)
        self.assertEqual((self.session / retained["report"]).read_bytes(), original_report)
        self.assertEqual(json.loads(self.fixture.gh_state.read_text())["createCalls"], 1)
        state = self.state_package()
        self.assertEqual(state["package"]["records"][0]["after"]["proposals"][-1]["mr"],
                         original_observation)
