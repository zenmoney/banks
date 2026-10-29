"""Human review of a current-variant ambiguity through the public session CLI."""
from session_fixture import make_session
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "session.py"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 54 54"><circle cx="27" cy="27" r="18"/></svg>'
XML = b'<vector xmlns:android="http://schemas.android.com/apk/res/android" android:width="54dp" android:height="54dp"/>'
PNG = b"\x89PNG\r\n\x1a\nobserved comparison"
OLD = "2026-09-25T12:00:00Z"


def sha(content):
    return hashlib.sha256(content).hexdigest()


def put(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content), encoding="utf-8")


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


class ReviewedUnchangedCLI(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name) / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        put(self.repo / "bank-icon-maintenance.json", {"schemaVersion": 1, "recheckAfterDays": 90,
            "retryUnconfirmedAfterDays": 30, "retryErrorAfterDays": 7, "selectionLimit": 5})
        self.folder = "Alpha_10-ru"
        put(self.repo / "banks" / self.folder / "info.json", {"id": 10, "title": "Alpha", "countryCode": "RU"})
        (self.repo / "banks" / self.folder / "icon.svg").write_bytes(SVG)
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "fixture")
        branch = git(self.repo, "branch", "--show-current")
        git(self.repo, "checkout", "-q", "-b", "banks-icon-agent-state")
        self.prior_state = {"schemaVersion": 1, "lastAttemptAt": "2025-01-01T00:00:00Z",
                            "lastAttemptResult": "verified", "lastVerifiedAt": "2025-01-01T00:00:00Z",
                            "verifiedIconHash": sha(SVG), "customHistory": {"status": "unknown"},
                            "proposals": [{"status": "accepted", "mr": {"state": "merged"}, "note": "retain"}]}
        put(self.repo / "banks" / "state" / "10.json", self.prior_state)
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "prior state")
        git(self.repo, "checkout", "-q", branch)
        self.session = Path(temp.name) / "session"
        self.started = make_session(self , bank_id=10, now=OLD)
        self.prepare_render()
        self.prior = self.attempt("needs-human", OLD)
        self.prior["assessment"]["variant"] = {"finding": "conflicting", "basis": "Variants need visual review"}
        self.prior["assessment"]["uncertainties"] = ["Which current variant is appropriate?"]
        self.prior["reason"] = "Ambiguous current variant"
        self.prior["nextAction"] = "Human to compare current variant with evidence"
        path = self.session / "evidence" / "10" / "attempt.json"
        put(path, self.prior)
        recorded = self.cli("continue", "--evidence", str(path))
        self.assertEqual(recorded["banks"][0]["point"], "needs-human")
        self.reviewed_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        self.new_checked_at = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        self.new = self.attempt("unchanged", self.new_checked_at)
        self.new["sources"] = self.prior["sources"]
        self.new["desktop"] = self.prior["desktop"]
        self.new["platforms"] = self.prior["platforms"]
        self.new["recheckRequired"] = self.prior["recheckRequired"]
        self.new["recheckRationale"] = self.prior["recheckRationale"]
        self.new["assessment"]["variant"]["basis"] = "Human compared the same current variant and confirmed visual equivalence"
        self.comparison = self.session / "evidence" / "10" / "comparison.png"
        self.comparison.write_bytes(PNG)
        self.new_path = self.session / "evidence" / "10" / "review-attempt.json"
        put(self.new_path, self.new)
        self.review_path = self.session / "evidence" / "10" / "review.json"
        self.review = self.review_record()
        put(self.review_path, self.review)

    def cli(self, command, *args, ok=True):
        call = subprocess.run([sys.executable, str(SCRIPT), command, "--session", str(self.session), *args],
                              capture_output=True, text=True)
        if ok:
            self.assertEqual(call.returncode, 0, call.stdout + call.stderr)
        else:
            self.assertEqual(call.returncode, 1, call.stdout + call.stderr)
        return json.loads(call.stdout)

    def prepare_render(self):
        generated = self.session / "evidence" / "10" / "generated"
        xml = generated / "composeResources" / "drawable" / "bank_10.xml"
        xml.parent.mkdir(parents=True)
        xml.write_bytes(XML)
        put(generated / "manifest.json", {"schemaVersion": 1, "sourceRoot": str(self.session / "worktree"),
             "banksRoot": str(self.session / "worktree" / "banks"), "pins": {"converter": "0.9.2", "compose": "1.9.1"},
             "entries": [{"id": 10, "sourcePath": f"banks/{self.folder}/icon.svg", "sourceSha256": sha(SVG),
                          "sourceValidated": True, "xmlSha256": sha(XML), "resourceName": "bank_10", "error": None}]})
        for background in ("light", "dark"):
            (self.session / "evidence" / "10" / f"{background}.png").write_bytes(b"\x89PNG\r\n\x1a\n" + background.encode())

    def attempt(self, outcome, checked_at):
        sources = [{"kind": "official", "url": "https://alpha.example.invalid/brand",
                    "assetUrl": "https://alpha.example.invalid/brand/logo.svg", "checkedAt": OLD,
                    "observation": "Same bank sign observed", "independenceGroup": "official"}]
        sources.extend({"kind": kind, "url": url, "assetUrl": None, "checkedAt": OLD,
                        "observation": "No relevant issue or PR found", "independenceGroup": kind}
                       for kind, url in (("zenplugins-issue", "https://github.com/ZenPlugins/Zen/issues?q=Alpha"),
                                         ("zenplugins-pr", "https://github.com/ZenPlugins/Zen/pulls?q=Alpha")))
        screens = {}
        for background in ("light", "dark"):
            path = self.session / "evidence" / "10" / f"{background}.png"
            screens[background] = {"path": str(path.relative_to(self.session)), "sha256": sha(path.read_bytes())}
        desktop = {"renderer": "compose-desktop-vector-xml-painterResource",
                   "manifest": "evidence/10/generated/manifest.json", "observedAt": OLD,
                   "visualComparison": "Both backgrounds preserve details and color against current SVG", **screens}
        assessment = {key: {"finding": "confirmed", "basis": "Official sign compared to same exact current bank icon"}
                      for key in ("identity", "currency", "variant")}
        assessment.update(independenceRationale="First-party asset; search results are not independent support", uncertainties=[])
        return {"schemaVersion": 1, "id": 10, "baseCommit": self.started["baseCommit"], "iconHash": sha(SVG),
                "checkedAt": checked_at, "outcome": outcome, "reason": "Current variant confirmed after human review",
                "nextAction": "Review local state package before publication", "sources": sources,
                "assessment": assessment, "desktop": desktop,
                "platforms": [{"target": target, "status": "not-tested", "observation": "Not available in controlled example"}
                              for target in ("android-compose", "android-native", "ios-compose", "ios-native")],
                "recheckRequired": False, "recheckRationale": "Same SVG, XML and renderer pins as previous observation"}

    def review_record(self):
        return {"schemaVersion": 1, "id": 10, "baseCommit": self.started["baseCommit"], "iconHash": sha(SVG),
                "decision": "equivalent-current-variant", "reviewer": "Human reviewer",
                "statement": "I compared the exact current variant; it is visually equivalent to the source.",
                "reviewedAt": self.reviewed_at,
                "priorReport": {"path": "reports/10.json", "sha256": sha((self.session / "reports" / "10.json").read_bytes())},
                "priorEvidence": {"path": "evidence/10/validated.json", "sha256": sha((self.session / "evidence" / "10" / "validated.json").read_bytes())},
                "evidence": {"path": str(self.new_path.relative_to(self.session)), "sha256": sha(self.new_path.read_bytes())},
                "comparison": {"path": str(self.comparison.relative_to(self.session)), "sha256": sha(PNG)}}

    def test_reviewed_current_variant_verifies_only_new_assessment_and_preserves_original_record(self):
        old_report = (self.session / "reports" / "10.json").read_bytes()
        old_evidence = (self.session / "evidence" / "10" / "validated.json").read_bytes()
        completed = self.cli("continue", "--review", str(self.review_path))
        bank = completed["banks"][0]
        self.assertEqual(bank["point"], "awaiting-state-publication")
        self.assertEqual((self.session / "reports" / "10.json").read_bytes(), old_report)
        self.assertEqual((self.session / "evidence" / "10" / "validated.json").read_bytes(), old_evidence)
        state = json.loads((self.session / "banks" / "state" / "10.json").read_text())
        self.assertEqual(state, self.prior_state | {"lastAttemptAt": self.new_checked_at, "lastAttemptResult": "verified",
                                                     "lastVerifiedAt": self.new_checked_at, "verifiedIconHash": sha(SVG)})
        report = json.loads((self.session / "reports" / "10-reviewed-unchanged.json").read_text())
        self.assertEqual(report["before"], json.loads(old_report)["after"])
        self.assertEqual(report["after"], state)
        self.assertEqual(report["evidence"]["desktop"]["observedAt"], OLD)
        self.assertEqual(report["humanReview"], "confirmed")
        self.assertEqual(report["reviewPath"], "evidence/10/human-review.json")
        self.assertEqual(report["priorReport"], {"path": "reports/10.json", "sha256": sha(old_report)})
        self.assertEqual(report["priorEvidence"], {"path": "evidence/10/validated.json", "sha256": sha(old_evidence)})
        validated = json.loads((self.session / report["evidencePath"]).read_text())
        self.assertEqual((validated["outcome"], validated["checkedAt"]), ("unchanged", self.new_checked_at))
        human_review = json.loads((self.session / report["reviewPath"]).read_text())
        self.assertEqual(human_review["statement"],
                         "I compared the exact current variant; it is visually equivalent to the source.")
        self.assertEqual(bank["report"], "reports/10-reviewed-unchanged.json")
        self.assertEqual(bank["evidence"], "evidence/10/reviewed-unchanged.json")
        self.assertEqual((self.session / "worktree" / "banks" / self.folder / "icon.svg").read_bytes(), SVG)

    def rejected(self):
        state = (self.session / "banks" / "state" / "10.json").read_bytes()
        session = (self.session / "session.json").read_bytes()
        report = (self.session / "reports" / "10.json").read_bytes()
        evidence = (self.session / "evidence" / "10" / "validated.json").read_bytes()
        outputs = ("reports/10-reviewed-unchanged.json", "evidence/10/reviewed-unchanged.json",
                   "evidence/10/human-review.json")
        before = {name: (self.session / name).read_bytes() if (self.session / name).exists() else None
                  for name in outputs}
        result = self.cli("continue", "--review", str(self.review_path), ok=False)
        self.assertIn("error", result)
        self.assertEqual((self.session / "banks" / "state" / "10.json").read_bytes(), state)
        self.assertEqual((self.session / "session.json").read_bytes(), session)
        self.assertEqual((self.session / "reports" / "10.json").read_bytes(), report)
        self.assertEqual((self.session / "evidence" / "10" / "validated.json").read_bytes(), evidence)
        for name in outputs:
            path = self.session / name
            self.assertEqual(path.read_bytes() if path.exists() else None, before[name])
        return result["error"]

    def test_canonical_prior_report_and_evidence_cannot_be_rebound(self):
        self.review["priorReport"]["path"] = "reports/other.json"
        put(self.review_path, self.review)
        self.rejected()
        self.review["priorReport"]["path"] = "reports/10.json"
        self.review["priorEvidence"]["path"] = "evidence/10/attempt.json"
        put(self.review_path, self.review)
        self.rejected()

    def test_prior_report_cannot_misstate_its_evidence_outcome(self):
        evidence_path = self.session / "evidence" / "10" / "validated.json"
        old = json.loads(evidence_path.read_text())
        old["outcome"] = "unconfirmed"
        put(evidence_path, old)
        report_path = self.session / "reports" / "10.json"
        report = json.loads(report_path.read_text())
        report["evidence"] = old
        put(report_path, report)
        self.review["priorEvidence"]["sha256"] = sha(evidence_path.read_bytes())
        self.review["priorReport"]["sha256"] = sha(report_path.read_bytes())
        put(self.review_path, self.review)
        self.rejected()

    def test_review_identity_decision_and_chronology_are_bound(self):
        for field, value in (("id", 99), ("baseCommit", "other"), ("iconHash", "0" * 64),
                             ("decision", "approve-candidate"), ("reviewer", " "), ("statement", ""),
                             ("reviewedAt", "2026-09-25T11:59:59Z"),
                             ("reviewedAt", "2099-01-01T00:00:00Z")):
            with self.subTest(field=field, value=value):
                self.review = self.review_record()
                self.review[field] = value
                put(self.review_path, self.review)
                self.rejected()

    def test_incomplete_review_does_not_record_verification(self):
        for field in ("statement", "priorReport", "evidence", "comparison"):
            with self.subTest(field=field):
                review = self.review_record()
                review.pop(field)
                put(self.review_path, review)
                self.rejected()


    def test_new_assessment_must_finish_after_review_and_not_in_future(self):
        for timestamp in (OLD, "2099-01-01T00:00:00Z"):
            with self.subTest(timestamp=timestamp):
                self.new["checkedAt"] = timestamp
                put(self.new_path, self.new)
                self.review["evidence"]["sha256"] = sha(self.new_path.read_bytes())
                put(self.review_path, self.review)
                self.rejected()

    def test_new_package_cannot_change_sources_render_or_recheck_status(self):
        changes = (("sources", []), ("desktop", None), ("platforms", []),
                   ("recheckRequired", True), ("recheckRationale", "different pins"))
        for field, value in changes:
            with self.subTest(field=field):
                updated = dict(self.new)
                updated[field] = value
                put(self.new_path, updated)
                self.review["evidence"]["sha256"] = sha(self.new_path.read_bytes())
                put(self.review_path, self.review)
                self.rejected()

    def test_review_requires_complete_confirmed_new_assessment(self):
        for field, value in (("outcome", "unconfirmed"), ("desktop", None),
                             ("assessment", self.prior["assessment"])):
            with self.subTest(field=field):
                updated = dict(self.new)
                updated[field] = value
                put(self.new_path, updated)
                self.review["evidence"]["sha256"] = sha(self.new_path.read_bytes())
                put(self.review_path, self.review)
                self.rejected()

    def test_all_linked_artifacts_must_match_bytes_and_be_safe_files(self):
        self.comparison.write_bytes(PNG + b"tampered")
        self.rejected()
        self.comparison.write_bytes(PNG)
        self.review["comparison"]["path"] = "evidence/10/../comparison.png"
        put(self.review_path, self.review)
        self.rejected()
        self.review["comparison"]["path"] = "evidence/10/comparison.png"
        self.review["evidence"]["sha256"] = "0" * 64
        put(self.review_path, self.review)
        self.rejected()
        self.review["evidence"]["sha256"] = sha(self.new_path.read_bytes())
        self.comparison.unlink()
        self.comparison.symlink_to(self.new_path)
        put(self.review_path, self.review)
        self.rejected()

    def test_prior_render_artifact_or_current_catalog_change_cannot_verify(self):
        xml = self.session / "evidence/10/generated/composeResources/drawable/bank_10.xml"
        xml.write_bytes(XML + b"edited")
        self.rejected()
        xml.write_bytes(XML)
        (self.session / "worktree/banks" / self.folder / "info.json").write_text('{"id":10,"title":"Changed"}')
        self.rejected()

    def test_paused_duplicate_and_exclusive_review_controls(self):
        self.cli("pause")
        self.rejected()
        self.cli("continue")
        for args in (("--evidence", str(self.new_path)), ("--approve-selection",)):
            with self.subTest(args=args):
                state = (self.session / "banks/state/10.json").read_bytes()
                self.cli("continue", "--review", str(self.review_path), *args, ok=False)
                self.assertEqual((self.session / "banks/state/10.json").read_bytes(), state)
        self.cli("continue", "--review", str(self.review_path))
        state = (self.session / "banks/state/10.json").read_bytes()
        self.cli("continue", "--review", str(self.review_path), ok=False)
        self.assertEqual((self.session / "banks/state/10.json").read_bytes(), state)

    def test_failed_session_save_restores_previous_state_and_discards_new_files(self):
        temporary = self.session / "session.json.tmp"
        temporary.mkdir()
        self.rejected()
        self.assertTrue(temporary.is_dir())


    def test_review_needs_newly_confirmed_assessment_not_unchanged_prior_opinion(self):
        prior_path = self.session / "evidence/10/validated.json"
        prior = json.loads(prior_path.read_text())
        prior["assessment"] = self.new["assessment"]
        put(prior_path, prior)
        report_path = self.session / "reports/10.json"
        report = json.loads(report_path.read_text())
        report["evidence"] = prior
        put(report_path, report)
        self.review["priorReport"]["sha256"] = sha(report_path.read_bytes())
        self.review["priorEvidence"]["sha256"] = sha(prior_path.read_bytes())
        put(self.review_path, self.review)
        self.rejected()

    def test_forged_prior_links_or_local_state_cannot_supply_freshness(self):
        report_path = self.session / "reports/10.json"
        report = json.loads(report_path.read_text())
        report["after"]["lastAttemptResult"] = "verified"
        put(report_path, report)
        self.review["priorReport"]["sha256"] = sha(report_path.read_bytes())
        put(self.review_path, self.review)
        self.rejected()
        report["after"]["lastAttemptResult"] = "needs-human"
        put(report_path, report)
        self.review["priorReport"]["sha256"] = sha(report_path.read_bytes())
        put(self.review_path, self.review)
        local = self.session / "banks/state/10.json"
        state = json.loads(local.read_text())
        state["customHistory"] = {"status": "forged"}
        put(local, state)
        self.rejected()

    def test_valid_but_different_sources_or_platform_evidence_cannot_be_reused(self):
        for field in ("sources", "desktop", "platforms"):
            with self.subTest(field=field):
                updated = json.loads(json.dumps(self.new))
                if field == "sources":
                    updated[field][0]["observation"] = "Different asset observed"
                elif field == "desktop":
                    updated[field]["visualComparison"] = "Different render conclusion"
                else:
                    updated[field][0]["observation"] = "Different Android check"
                put(self.new_path, updated)
                self.review["evidence"]["sha256"] = sha(self.new_path.read_bytes())
                put(self.review_path, self.review)
                self.rejected()

    def test_new_attempt_wrong_bank_base_or_missing_full_schema_is_rejected(self):
        for field, value in (("id", 20), ("baseCommit", "other"), ("iconHash", "0" * 64),
                             ("assessment", None)):
            with self.subTest(field=field):
                updated = dict(self.new)
                updated[field] = value
                put(self.new_path, updated)
                self.review["evidence"]["sha256"] = sha(self.new_path.read_bytes())
                put(self.review_path, self.review)
                self.rejected()

    def test_missing_prior_desktop_cannot_be_promoted_by_a_later_review(self):
        prior_path = self.session / "evidence/10/validated.json"
        prior = json.loads(prior_path.read_text())
        prior["desktop"] = None
        put(prior_path, prior)
        report_path = self.session / "reports/10.json"
        report = json.loads(report_path.read_text())
        report["evidence"] = prior
        put(report_path, report)
        self.review["priorReport"]["sha256"] = sha(report_path.read_bytes())
        self.review["priorEvidence"]["sha256"] = sha(prior_path.read_bytes())
        self.new["desktop"] = None
        put(self.new_path, self.new)
        self.review["evidence"]["sha256"] = sha(self.new_path.read_bytes())
        put(self.review_path, self.review)
        self.rejected()

    def test_changed_current_svg_is_not_approved_even_when_hashes_are_self_consistent(self):
        icon = self.session / "worktree/banks" / self.folder / "icon.svg"
        icon.write_bytes(SVG + b"changed")
        self.rejected()

    def test_review_file_must_be_under_evidence_without_symlink(self):
        other = self.session / "review.json"
        put(other, self.review)
        self.cli("continue", "--review", str(other), ok=False)
        self.cli("continue", "--review", str(self.session) + "/evidence/10/./review.json", ok=False)
        self.review_path.unlink()
        self.review_path.symlink_to(other)
        self.cli("continue", "--review", str(self.review_path), ok=False)
        self.assertEqual(json.loads((self.session / "banks/state/10.json").read_text())["lastAttemptResult"], "needs-human")

    def test_waiting_proposal_or_mr_cannot_be_rebranded_as_review(self):
        current = git(self.repo, "branch", "--show-current")
        git(self.repo, "checkout", "-q", "banks-icon-agent-state")
        state_path = self.repo / "banks/state/10.json"
        state = json.loads(state_path.read_text())
        for status, point in (("pending", "waiting-proposal"), ("open", "waiting-mr")):
            proposal = {"status": status, "baseIconHash": sha(SVG)}
            if status == "open":
                proposal["mr"] = {"state": "open", "url": "https://example.invalid/mr"}
            state["proposals"] = [proposal]
            put(state_path, state)
            git(self.repo, "add", ".")
            git(self.repo, "commit", "-qm", f"{status} proposal")
            self.session = self.session.parent / f"session-{status}"
            started = make_session(self , bank_id=10, now=OLD)
            self.assertEqual(started["banks"][0]["point"], point)
            review = self.session / "evidence/10/review.json"
            put(review, {"id": 10})
            self.cli("continue", "--review", str(review), ok=False)
            self.assertFalse((self.session / "banks/state/10.json").exists())
            self.assertEqual(json.loads((self.session / "session.json").read_text())["banks"][0]["point"], point)
        git(self.repo, "checkout", "-q", current)

    def test_prior_report_and_evidence_hashes_detect_edits(self):
        report_path = self.session / "reports/10.json"
        report_path.write_bytes(report_path.read_bytes() + b" ")
        self.rejected()
        report_path.write_bytes(report_path.read_bytes()[:-1])
        prior_path = self.session / "evidence/10/validated.json"
        prior_path.write_bytes(prior_path.read_bytes() + b" ")
        self.rejected()

    def test_review_output_collision_leaves_original_records_and_state_intact(self):
        existing = self.session / "evidence/10/human-review.json"
        existing.write_bytes(b"not a review")
        self.rejected()
        self.assertEqual(existing.read_bytes(), b"not a review")

    def test_unknown_state_history_remains_unknown_after_confirmation(self):
        root = self.session.parent
        self.repo = root / "no-state-repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        put(self.repo / "bank-icon-maintenance.json", {"schemaVersion": 1, "recheckAfterDays": 90,
            "retryUnconfirmedAfterDays": 30, "retryErrorAfterDays": 7, "selectionLimit": 5})
        put(self.repo / "banks" / self.folder / "info.json", {"id": 10, "title": "Alpha", "countryCode": "RU"})
        (self.repo / "banks" / self.folder / "icon.svg").write_bytes(SVG)
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "fixture without state history")
        self.session = root / "no-state-session"
        self.started = make_session(self , bank_id=10, now=OLD)
        self.assertEqual(self.started["stateHistory"], "unknown")
        self.prepare_render()
        self.prior = self.attempt("needs-human", OLD)
        self.prior["assessment"]["variant"]["finding"] = "conflicting"
        self.prior["assessment"]["uncertainties"] = ["Variant choice needs review"]
        put(self.session / "evidence/10/attempt.json", self.prior)
        self.cli("continue", "--evidence", str(self.session / "evidence/10/attempt.json"))
        self.reviewed_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        self.new = self.attempt("unchanged", self.reviewed_at)
        self.new["assessment"]["variant"]["basis"] = "Human matched the visual current variant"
        self.new_path = self.session / "evidence/10/review-attempt.json"
        put(self.new_path, self.new)
        self.comparison = self.session / "evidence/10/comparison.png"
        self.comparison.write_bytes(PNG)
        self.review_path = self.session / "evidence/10/review.json"
        put(self.review_path, self.review_record())
        completed = self.cli("continue", "--review", str(self.review_path))
        self.assertEqual(completed["stateHistory"], "unknown")
        report = json.loads((self.session / "reports/10-reviewed-unchanged.json").read_text())
        self.assertEqual(report["provenance"]["stateHistory"], "unknown")
        self.assertEqual(report["before"]["lastAttemptResult"], "needs-human")
        self.assertIsNone(json.loads((self.session / "reports/10.json").read_text())["before"])
        self.assertEqual(report["after"]["lastAttemptResult"], "verified")

    def test_review_rejects_dot_segments_even_when_path_resolves_under_evidence(self):
        self.review["comparison"]["path"] = "evidence/10/./comparison.png"
        put(self.review_path, self.review)
        self.rejected()


    def test_webp_comparison_image_can_confirm_same_current_variant(self):
        webp = b"RIFF" + (14).to_bytes(4, "little") + b"WEBPVP8 " + (2).to_bytes(4, "little") + b"\x00\x00"
        self.comparison.write_bytes(webp)
        self.review["comparison"]["sha256"] = sha(webp)
        put(self.review_path, self.review)
        completed = self.cli("continue", "--review", str(self.review_path))
        self.assertEqual(completed["banks"][0]["state"]["lastAttemptResult"], "verified")

    def test_state_parent_symlink_cannot_write_outside_session(self):
        state_dir = self.session / "banks/state"
        outside = self.session.parent / "outside-state"
        state_dir.rename(outside)
        state_dir.symlink_to(outside, target_is_directory=True)
        self.rejected()
        self.assertEqual(json.loads((outside / "10.json").read_text())["lastAttemptResult"], "needs-human")

    def test_prior_optional_rationale_absence_fails_closed_without_traceback(self):
        prior_path = self.session / "evidence/10/validated.json"
        prior = json.loads(prior_path.read_text())
        prior.pop("recheckRationale")
        put(prior_path, prior)
        report_path = self.session / "reports/10.json"
        report = json.loads(report_path.read_text())
        report["evidence"] = prior
        put(report_path, report)
        self.review["priorReport"]["sha256"] = sha(report_path.read_bytes())
        self.review["priorEvidence"]["sha256"] = sha(prior_path.read_bytes())
        put(self.review_path, self.review)
        self.rejected()

    def test_attempt_save_failure_rolls_back_all_outputs_and_can_retry(self):
        self.session = self.session.parent / "attempt-save-failure"
        self.started = make_session(self , bank_id=10, now=OLD)
        self.prepare_render()
        attempt_path = self.session / "evidence/10/attempt.json"
        put(attempt_path, self.attempt("needs-human", OLD))
        temporary = self.session / "session.json.tmp"
        temporary.mkdir()
        self.cli("continue", "--evidence", str(attempt_path), ok=False)
        self.assertTrue(temporary.is_dir())
        self.assertEqual(json.loads((self.session / "session.json").read_text())["banks"][0]["point"], "identity-evidence")
        for relative in ("banks/state/10.json", "reports/10.json", "evidence/10/validated.json"):
            self.assertFalse((self.session / relative).exists())
        temporary.rmdir()
        resumed = self.cli("continue", "--evidence", str(attempt_path))
        self.assertEqual(resumed["banks"][0]["point"], "needs-human")

    def test_review_cannot_rebind_mutated_xml_and_pins_as_original_render(self):
        manifest_path = self.session / "evidence/10/generated/manifest.json"
        xml_path = self.session / "evidence/10/generated/composeResources/drawable/bank_10.xml"
        xml_path.write_bytes(XML + b"different")
        manifest = json.loads(manifest_path.read_text())
        manifest["pins"]["converter"] = "unreviewed-converter"
        manifest["entries"][0]["xmlSha256"] = sha(xml_path.read_bytes())
        put(manifest_path, manifest)
        self.rejected()

    def test_legacy_prior_passed_platform_row_pins_render_without_report_binding(self):
        old_path = self.session / "evidence/10/validated.json"
        old = json.loads(old_path.read_text())
        passed = {"target": "android-compose", "status": "passed", "observation": "Reused exact XML/pins",
                  "sourceSha256": sha(SVG), "xmlSha256": sha(XML), "converter": "0.9.2", "compose": "1.9.1"}
        old["platforms"][0] = passed
        put(old_path, old)
        report_path = self.session / "reports/10.json"
        report = json.loads(report_path.read_text())
        report["evidence"] = old
        report.pop("renderBinding")
        put(report_path, report)
        self.new["platforms"][0] = passed
        put(self.new_path, self.new)
        self.review["priorReport"]["sha256"] = sha(report_path.read_bytes())
        self.review["priorEvidence"]["sha256"] = sha(old_path.read_bytes())
        self.review["evidence"]["sha256"] = sha(self.new_path.read_bytes())
        put(self.review_path, self.review)
        completed = self.cli("continue", "--review", str(self.review_path))
        self.assertEqual(completed["banks"][0]["state"]["lastAttemptResult"], "verified")

    def test_legacy_all_not_tested_without_render_binding_cannot_verify(self):
        report_path = self.session / "reports/10.json"
        report = json.loads(report_path.read_text())
        report.pop("renderBinding")
        put(report_path, report)
        self.review["priorReport"]["sha256"] = sha(report_path.read_bytes())
        put(self.review_path, self.review)
        self.rejected()

    def test_future_completed_attempt_cannot_create_verification_freshness(self):
        self.session = self.session.parent / "future-attempt"
        self.started = make_session(self , bank_id=10, now=OLD)
        self.prepare_render()
        path = self.session / "evidence/10/attempt.json"
        put(path, self.attempt("unchanged", "2099-01-01T00:00:00Z"))
        self.cli("continue", "--evidence", str(path), ok=False)
        self.assertFalse((self.session / "banks/state/10.json").exists())
        self.assertEqual(json.loads((self.session / "session.json").read_text())["banks"][0]["point"], "identity-evidence")

    def test_new_review_attempt_cannot_overwrite_original_input_provenance(self):
        original = self.session / "evidence/10/attempt.json"
        put(original, self.new)
        self.review["evidence"] = {"path": "evidence/10/attempt.json", "sha256": sha(original.read_bytes())}
        put(self.review_path, self.review)
        self.rejected()

    def test_session_save_temp_symlink_cannot_overwrite_external_file(self):
        outside = self.session.parent / "outside-session"
        outside.write_bytes(b"private original bytes")
        temporary = self.session / "session.json.tmp"
        temporary.symlink_to(outside)
        self.rejected()
        self.assertEqual(outside.read_bytes(), b"private original bytes")
        self.assertTrue(temporary.is_symlink())

    def test_preexisting_session_temp_file_is_not_deleted_on_review_failure(self):
        temporary = self.session / "session.json.tmp"
        temporary.write_bytes(b"unfinished other operation")
        self.rejected()
        self.assertEqual(temporary.read_bytes(), b"unfinished other operation")
