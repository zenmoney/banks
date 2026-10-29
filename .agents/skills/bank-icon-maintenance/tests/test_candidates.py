"""Candidate review package through the external session CLI and temporary Git catalogs.

PNG/XML fixtures are controlled bytes; they do not establish an actual renderer observation.
"""
from session_fixture import make_session
import difflib
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "session.py"
OLD = "2026-09-25T00:00:00Z"
BASE = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 54 54"><circle cx="27" cy="27" r="18"/></svg>'
RAW = b'<svg xmlns="http://www.w3.org/2000/svg"><circle r="19"/></svg>'
FINAL = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 54 54"><circle cx="27" cy="27" r="19"/></svg>'
XML = b'<vector xmlns:android="http://schemas.android.com/apk/res/android" android:width="54dp"/>'
PNG = b"\x89PNG\r\n\x1a\ncontrolled fixture image"
TARGETS = ("android-compose", "android-native", "ios-compose", "ios-native")
SOURCE = "https://example.invalid/bank/brand"
ASSET = "https://example.invalid/bank/circle.svg"


def digest(value):
    return hashlib.sha256(value).hexdigest()


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, check=True).stdout.strip()


def put_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class CandidateCLI(unittest.TestCase):
    base_icon = BASE
    raw_source = RAW

    def setUp(self):
        self.base_icon = getattr(self, "base_icon", BASE)
        self.raw_source = getattr(self, "raw_source", RAW)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        put_json(self.repo / "bank-icon-maintenance.json", {"schemaVersion": 1,
            "recheckAfterDays": 90, "retryUnconfirmedAfterDays": 30,
            "retryErrorAfterDays": 7, "selectionLimit": 5})
        self.folder = "Alpha_10-ru"
        put_json(self.repo / "banks" / self.folder / "info.json",
                 {"id": 10, "title": "Alpha", "countryCode": "RU"})
        if self.base_icon is not None:
            (self.repo / "banks" / self.folder / "icon.svg").write_bytes(self.base_icon)
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "fixture")
        branch = git(self.repo, "branch", "--show-current")
        git(self.repo, "checkout", "-q", "-b", "banks-icon-agent-state")
        prior = {"schemaVersion": 1, "customHistory": {"keep": True}}
        if self.base_icon is not None:
            prior.update(lastVerifiedAt="2025-01-01T00:00:00Z", verifiedIconHash=digest(self.base_icon))
        if self._testMethodName == "test_historical_prepared_proposal_does_not_revalidate_new_raw_pending":
            prior["proposals"] = [{"status": "accepted", "mr": {"state": "merged"},
                "reviewPackage": {"path": "evidence/old-review.json", "sha256": "0" * 64}}]
        put_json(self.repo / "banks" / "state" / "10.json", prior)
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "state fixture")
        git(self.repo, "checkout", "-q", branch)
        self.session = self.root / "session"
        self.started = make_session(self , bank_id=10, now=OLD)
        self.evidence = self.session / "evidence" / "10"
        self.evidence.mkdir(parents=True)
        (self.evidence / "raw.svg").write_bytes(self.raw_source)
        attempt = {"schemaVersion": 1, "id": 10, "baseCommit": self.started["baseCommit"],
            "iconHash": self.started["banks"][0]["iconHash"], "checkedAt": OLD, "outcome": "candidate",
            "reason": "Official circle is a different and current bank-list variant",
            "nextAction": "Prepare reviewed final SVG and ask human to compare",
            "sources": [
                {"kind": "official", "url": SOURCE, "assetUrl": ASSET, "checkedAt": OLD,
                 "observation": "Current circular brand asset", "independenceGroup": "official"},
                {"kind": "zenplugins-issue", "url": "https://example.invalid/issues", "assetUrl": None,
                 "checkedAt": OLD, "observation": "No relevant issue", "independenceGroup": "issues"},
                {"kind": "zenplugins-pr", "url": "https://example.invalid/pulls", "assetUrl": None,
                 "checkedAt": OLD, "observation": "No relevant PR", "independenceGroup": "prs"}],
            "assessment": {key: {"finding": "confirmed", "basis": "Controlled official source"}
                           for key in ("identity", "currency", "variant")},
            "desktop": None, "platforms": [], "recheckRequired": False,
            "candidate": {"path": "evidence/10/raw.svg", "iconHash": digest(self.raw_source),
                          "sourceUrl": SOURCE, "sourceAssetUrl": ASSET,
                          "rationale": "Current circle fits the bank-list role"}}
        attempt["assessment"].update(independenceRationale="Original independent source", uncertainties=[])
        put_json(self.evidence / "attempt.json", attempt)
        self.attempted = self.cli("continue", "--evidence", str(self.evidence / "attempt.json"))
        self.assertEqual(self.attempted["banks"][0]["point"], "waiting-proposal")
        self.package = self.create_package()

    def cli(self, command, *arguments, ok=True):
        result = subprocess.run([sys.executable, str(SCRIPT), command, "--session", str(self.session),
                                 *arguments], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0 if ok else 1, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def file(self, name, content):
        path = self.evidence / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return {"path": str(path.relative_to(self.session)), "sha256": digest(content)}

    def create_package(self):
        final = self.file("final.svg", FINAL)
        xml = self.file("render/composeResources/drawable/bank_10.xml", XML)
        manifest = {"schemaVersion": 1, "sourceRoot": self.started["worktree"],
            "banksRoot": str(Path(self.started["worktree"]) / "banks"),
            "pins": {"converter": "controlled-converter", "compose": "controlled-compose"},
            "entries": [{"id": 10, "error": None, "sourcePath": str(self.session / final["path"]),
                         "sourceSha256": final["sha256"], "sourceValidated": True,
                         "xmlSha256": xml["sha256"], "resourceName": "bank_10"}]}
        put_json(self.evidence / "render/manifest.json", manifest)
        screens = {background: self.file(f"render/{background}.png", PNG + background.encode())
                   for background in ("light", "dark")}
        comparison = {}
        for background in ("light", "dark"):
            comparison[background] = {role: self.file(f"compare/{background}-{role}.png",
                                     PNG + background.encode() + role.encode())
                                     for role in (("source", "base", "candidate", "beforeAfter") if self.base_icon is not None else ("source",))}
        if self.base_icon is None:
            comparison = {theme: {"source": row["source"], "base": None,
                                  "candidate": screens[theme], "beforeAfter": None}
                          for theme, row in comparison.items()}
        prior_report = (self.session / "reports/10.json").read_bytes()
        prior_evidence = (self.evidence / "validated.json").read_bytes()
        base_description = digest(self.base_icon) if self.base_icon is not None else "No existing icon.svg at frozen base commit"
        draft = (f"Alpha Company.id 10\nBase {base_description}\nCandidate {digest(FINAL)}\n"
                 f"Source {SOURCE} asset {ASSET}\n{OLD}\n"
                 "Role bank-list circle, vectorization and details intact.\n"
                 "Compose Desktop XML painterResource, light and dark comparison.\n"
                 + "\n".join(f"{t}: not-tested (unavailable)" for t in TARGETS))
        package = {"schemaVersion": 1, "id": 10, "baseCommit": self.started["baseCommit"],
            "baseIconHash": self.started["banks"][0]["iconHash"], "metadataHash": self.started["banks"][0]["metadataHash"],
            "checkedAt": OLD,
            "priorReport": {"path": "reports/10.json", "sha256": digest(prior_report)},
            "priorEvidence": {"path": "evidence/10/validated.json", "sha256": digest(prior_evidence)},
            "candidate": {**final, "sourceUrl": SOURCE, "sourceAssetUrl": ASSET,
                "rationale": "New circle is same bank-list role, newer official variant",
                "role": "bank-list icon", "variant": "red circle with white mark",
                "transforms": ["Normalize original SVG dimensions without crop, preserving geometry"],
                "detailsJudgement": "Compared source strokes and terminal details; preserved",
                "proportionsJudgement": "Preserved source viewBox proportions without fitting",
                "faithful": True, "visibleDiff": True,
                "visibleDiffExplanation": "Red ring visibly thicker on both backgrounds"},
            "desktop": {"renderer": "compose-desktop-vector-xml-painterResource",
                "manifest": "evidence/10/render/manifest.json",
                "manifestSha256": digest((self.evidence / "render/manifest.json").read_bytes()),
                "observedAt": OLD,
                "visualComparison": "Controlled images compare circle on both backgrounds", **screens},
            "comparison": comparison,
            "platforms": [{"target": t, "available": False, "status": "not-tested",
                           "observation": "Unavailable in controlled fixture; residual platform risk"}
                          for t in TARGETS],
            "mrDraft": self.file("mr-draft.txt", draft.encode()),
            "diff": self.file("candidate.diff", ("\n".join(difflib.unified_diff(
                [] if self.base_icon is None else self.base_icon.decode().splitlines(), FINAL.decode().splitlines(),
                fromfile="/dev/null" if self.base_icon is None else "base/10/icon.svg", tofile="candidate/10/icon.svg", lineterm=""))
                + "\n").encode())}
        if self.base_icon is None:
            package["desktop"]["baseObservation"] = None
        self.package_path = self.evidence / "candidate-package.json"
        put_json(self.package_path, package)
        return package

    def update_package(self):
        put_json(self.package_path, self.package)

    def test_preparation_preserves_original_report_and_current_icon_pending_human_review(self):
        old_report = (self.session / "reports/10.json").read_bytes()
        old_evidence = (self.evidence / "validated.json").read_bytes()
        result = self.cli("continue", "--candidate", str(self.package_path))
        bank = result["banks"][0]
        self.assertEqual(bank["point"], "waiting-proposal")
        self.assertEqual((self.session / "reports/10.json").read_bytes(), old_report)
        self.assertEqual((self.evidence / "validated.json").read_bytes(), old_evidence)
        self.assertEqual((self.session / "worktree/banks" / self.folder / "icon.svg").read_bytes(), BASE)
        proposal = bank["state"]["proposals"][-1]
        self.assertEqual(proposal["sourceCandidate"]["iconHash"], digest(RAW))
        self.assertEqual(proposal["candidateIconHash"], digest(FINAL))
        self.assertEqual(proposal["humanReview"], "pending")
        self.assertEqual(proposal["verification"]["sourceSha256"], digest(FINAL))
        self.assertEqual(proposal["verification"]["xmlSha256"], digest(XML))
        self.assertEqual(proposal["reviewPackage"]["sha256"], digest(self.package_path.read_bytes()))
        self.assertEqual(bank["state"]["lastVerifiedAt"], "2025-01-01T00:00:00Z")
        self.assertEqual(self.cli("continue")["banks"][0]["point"], "waiting-proposal")
        report = json.loads((self.session / bank["report"]).read_text())
        self.assertEqual(report["before"], self.attempted["banks"][0]["state"])
        self.assertEqual(report["after"], bank["state"])
        self.assertEqual(report["humanReview"], "pending")
        self.assertEqual(report["priorReport"]["sha256"], digest(old_report))

    def rejected(self):
        prior_state = (self.session / "banks/state/10.json").read_bytes()
        prior_session = (self.session / "session.json").read_bytes()
        prior_report = (self.session / "reports/10.json").read_bytes()
        prior_evidence = (self.evidence / "validated.json").read_bytes()
        result = self.cli("continue", "--candidate", str(self.package_path), ok=False)
        self.assertEqual(set(result), {"error"})
        self.assertEqual((self.session / "banks/state/10.json").read_bytes(), prior_state)
        self.assertEqual((self.session / "session.json").read_bytes(), prior_session)
        self.assertEqual((self.session / "reports/10.json").read_bytes(), prior_report)
        self.assertEqual((self.evidence / "validated.json").read_bytes(), prior_evidence)
        self.assertFalse(any((self.session / "reports").glob("10-candidate-*.json")))
        self.assertEqual(self.cli("continue")["banks"][0]["point"], "waiting-proposal")


    def update_manifest(self, manifest):
        path = self.evidence / "render/manifest.json"
        put_json(path, manifest)
        self.package["desktop"]["manifestSha256"] = digest(path.read_bytes())
        self.update_package()

    def test_invalid_generated_manifest_validation_and_converter_error_refuse_candidate(self):
        manifest_path = self.evidence / "render/manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["entries"][0]["sourceValidated"] = False
        self.update_manifest(manifest)
        self.rejected()
        manifest["entries"][0]["sourceValidated"] = True
        manifest["entries"][0]["error"] = "unsafe SVG/conversion error"
        self.update_manifest(manifest)
        self.rejected()

    def test_failed_or_available_unchecked_renderer_refuses_candidate(self):
        self.package["platforms"][0].update(status="failed", observation="Android render lost mask")
        self.update_package()
        self.rejected()
        self.package["platforms"][0].update(status="not-tested", available=True)
        self.update_package()
        self.rejected()

    def test_loss_of_details_and_invisible_diff_stop_before_recording(self):
        self.package["candidate"]["faithful"] = False
        self.update_package()
        self.rejected()
        self.package["candidate"].update(faithful=True, visibleDiff=False)
        self.update_package()
        self.rejected()
        self.package["candidate"]["visibleDiff"] = True
        self.package["comparison"]["dark"]["candidate"] = self.package["comparison"]["dark"]["base"]
        self.update_package()
        self.rejected()

    def test_candidate_manifest_must_bind_final_bytes_not_prior_or_base(self):
        manifest_path = self.evidence / "render/manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["entries"][0]["sourceSha256"] = digest(RAW)
        self.update_manifest(manifest)
        self.rejected()
        manifest["entries"][0]["sourceSha256"] = digest(FINAL)
        manifest["entries"][0]["sourcePath"] = f"banks/{self.folder}/icon.svg"
        self.update_manifest(manifest)
        self.rejected()

    def test_candidate_edit_and_comparison_tamper_after_preparation_stop_resume(self):
        prepared = self.cli("continue", "--candidate", str(self.package_path))["banks"][0]
        (self.evidence / "final.svg").write_bytes(FINAL + b" ")
        stopped = self.cli("continue")["banks"][0]
        self.assertEqual(stopped["point"], "needs-human")
        self.assertEqual((self.session / "reports" / Path(prepared["report"]).name).is_file(), True)
        self.assertEqual((self.session / "worktree/banks" / self.folder / "icon.svg").read_bytes(), BASE)

    def test_base_svg_and_metadata_change_after_preparation_stop_resume(self):
        self.cli("continue", "--candidate", str(self.package_path))
        (self.session / "worktree/banks" / self.folder / "icon.svg").write_bytes(BASE + b" ")
        stopped = self.cli("continue")["banks"][0]
        self.assertEqual(stopped["point"], "needs-human")

    def test_dirty_worktree_duplicate_bank_stops_prepared_proposal(self):
        self.cli("continue", "--candidate", str(self.package_path))
        twin = self.session / "worktree/banks/Beta_20-ru"
        put_json(twin / "info.json", {"id": 10, "title": "Beta", "countryCode": "RU"})
        (twin / "icon.svg").write_bytes(BASE)
        stopped = self.cli("continue")["banks"][0]
        self.assertEqual(stopped["point"], "needs-human")

    def test_implementation_only_base_ref_move_keeps_exact_proposal_valid(self):
        prepared = self.cli("continue", "--candidate", str(self.package_path))["banks"][0]
        put_json(self.repo / "new-file.json", {"changed": "implementation only"})
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "implementation")
        self.assertEqual(self.cli("continue")["banks"][0], prepared)

    def test_new_accepted_icon_at_moved_base_ref_stops_stale_proposal(self):
        self.cli("continue", "--candidate", str(self.package_path))
        (self.repo / "banks" / self.folder / "icon.svg").write_bytes(BASE + b" ")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "new accepted icon")
        stopped = self.cli("continue")["banks"][0]
        self.assertEqual(stopped["point"], "needs-human")

    def test_new_duplicate_bank_at_moved_base_ref_stops_stale_proposal(self):
        self.cli("continue", "--candidate", str(self.package_path))
        twin = self.repo / "banks/Beta_20-ru"
        put_json(twin / "info.json", {"id": 10, "title": "Beta", "countryCode": "RU"})
        (twin / "icon.svg").write_bytes(BASE)
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "duplicate ID")
        stopped = self.cli("continue")["banks"][0]
        self.assertEqual(stopped["point"], "needs-human")

    def test_deleted_bank_at_moved_base_ref_stops_stale_proposal(self):
        self.cli("continue", "--candidate", str(self.package_path))
        (self.repo / "banks" / self.folder / "info.json").unlink()
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "remove accepted bank metadata")
        stopped = self.cli("continue")["banks"][0]
        self.assertEqual(stopped["point"], "needs-human")

    def test_pause_resume_retains_exact_candidate_review_package(self):
        prepared = self.cli("continue", "--candidate", str(self.package_path))["banks"][0]
        paused = self.cli("pause")["banks"][0]
        self.assertEqual(paused["priorPoint"], "waiting-proposal")
        self.assertEqual(set(self.cli("continue", "--candidate", str(self.package_path), ok=False)), {"error"})
        resumed = self.cli("continue")["banks"][0]
        self.assertEqual(resumed, prepared)
        self.assertEqual(self.cli("continue")["banks"][0], prepared)

    def test_prior_report_change_or_symlinked_artifact_refuses_reuse(self):
        report_path = self.session / "reports/10.json"
        report_path.write_bytes(report_path.read_bytes() + b" ")
        self.rejected()
        report_path.write_bytes(report_path.read_bytes()[:-1])
        alternate = self.root / "outside.svg"
        alternate.write_bytes(FINAL)
        (self.evidence / "final.svg").unlink()
        (self.evidence / "final.svg").symlink_to(alternate)
        self.rejected()

    def test_metadata_changed_before_candidate_recording_is_rejected(self):
        metadata_path = self.session / "worktree/banks" / self.folder / "info.json"
        metadata_path.write_text(metadata_path.read_text() + " ")
        self.rejected()

    def test_invalid_prior_link_and_diff_are_rejected_before_state_replacement(self):
        self.package["priorReport"] = "reports/10.json"
        self.update_package()
        self.rejected()
        self.package["priorReport"] = {"path": "reports/10.json",
            "sha256": digest((self.session / "reports/10.json").read_bytes())}
        self.file("candidate.diff", b"incomplete technical rewrite\n")
        self.package["diff"]["sha256"] = digest(b"incomplete technical rewrite\n")
        self.update_package()
        self.rejected()

    def test_original_input_package_and_saved_review_package_tamper_stop_reuse(self):
        prepared = self.cli("continue", "--candidate", str(self.package_path))["banks"][0]
        saved = self.session / prepared["state"]["proposals"][-1]["reviewPackage"]["path"]
        saved.write_bytes(saved.read_bytes() + b" ")
        stopped = self.cli("continue")["banks"][0]
        self.assertEqual(stopped["point"], "needs-human")

    def test_xml_and_comparison_tamper_stop_deferred_candidate_review(self):
        self.cli("continue", "--candidate", str(self.package_path))
        (self.evidence / "render/composeResources/drawable/bank_10.xml").write_bytes(XML + b" ")
        stopped = self.cli("continue")["banks"][0]
        self.assertEqual(stopped["point"], "needs-human")

    def test_report_tamper_stops_resume_without_replacing_saved_state(self):
        bank = self.cli("continue", "--candidate", str(self.package_path))["banks"][0]
        local_state = (self.session / "banks/state/10.json").read_bytes()
        report = self.session / bank["report"]
        report.write_bytes(report.read_bytes() + b" ")
        stopped = self.cli("continue")["banks"][0]
        self.assertEqual(stopped["point"], "needs-human")
        self.assertEqual((self.session / "banks/state/10.json").read_bytes(), local_state)

    def test_preexisting_content_addressed_output_is_never_overwritten(self):
        destination = self.evidence / f"candidate-{digest(self.package_path.read_bytes())}.json"
        destination.write_bytes(b"reserved unrelated output")
        self.rejected()
        self.assertEqual(destination.read_bytes(), b"reserved unrelated output")

    def test_final_svg_same_as_base_is_not_a_candidate(self):
        self.file("final.svg", BASE)
        self.package["candidate"]["sha256"] = digest(BASE)
        self.update_package()
        self.rejected()

    def test_claimed_platform_pass_needs_build_and_real_ui_receipts(self):
        self.package["platforms"][0].update(available=True, status="passed",
            sourceSha256=digest(FINAL), xmlSha256=digest(XML),
            converter="controlled-converter", compose="controlled-compose",
            observation="Controlled build and UI path")
        self.update_package()
        self.rejected()

    def test_bound_platform_receipts_remain_checked_after_deferred_review(self):
        row = self.package["platforms"][0]
        row.update(available=True, status="passed", sourceSha256=digest(FINAL),
                   xmlSha256=digest(XML), converter="controlled-converter",
                   compose="controlled-compose", observation="Controlled build and UI scenario",
                   buildEvidence=self.file("android-build.txt", b"controlled build successful"),
                   uiEvidence=self.file("android-ui.png", PNG + b"android-ui"))
        self.update_package()
        prepared = self.cli("continue", "--candidate", str(self.package_path))["banks"][0]
        self.assertEqual(prepared["point"], "waiting-proposal")
        self.assertEqual(self.cli("continue")["banks"][0]["point"], "waiting-proposal")
        (self.evidence / "android-ui.png").write_bytes(PNG + b"different")
        stopped = self.cli("continue")["banks"][0]
        self.assertEqual(stopped["point"], "needs-human")

    def test_missing_comparison_image_and_mr_draft_stop_candidate(self):
        (self.session / self.package["comparison"]["light"]["source"]["path"]).unlink()
        self.rejected()
        self.file("compare/light-source.png", PNG + b"lightsource")
        self.package["comparison"]["light"]["source"]["sha256"] = digest(PNG + b"lightsource")
        self.package["mrDraft"]["sha256"] = "0" * 64
        self.update_package()
        self.rejected()

    def test_local_state_tamper_after_preparation_requires_human_reassessment(self):
        self.cli("continue", "--candidate", str(self.package_path))
        state = self.session / "banks/state/10.json"
        state.write_bytes(state.read_bytes() + b" ")
        stopped = self.cli("continue")["banks"][0]
        self.assertEqual(stopped["point"], "needs-human")

    def test_metadata_change_after_preparation_stops_without_new_verification(self):
        self.cli("continue", "--candidate", str(self.package_path))
        metadata = self.session / "worktree/banks" / self.folder / "info.json"
        metadata.write_bytes(metadata.read_bytes() + b" ")
        stopped = self.cli("continue")["banks"][0]
        self.assertEqual(stopped["point"], "needs-human")
        self.assertEqual(stopped["state"]["lastVerifiedAt"], "2025-01-01T00:00:00Z")

    def test_before_after_comparison_tamper_requires_new_visual_review(self):
        self.cli("continue", "--candidate", str(self.package_path))
        image = self.session / self.package["comparison"]["dark"]["beforeAfter"]["path"]
        image.write_bytes(PNG + b"changed comparison")
        stopped = self.cli("continue")["banks"][0]
        self.assertEqual(stopped["point"], "needs-human")

    def test_manifest_byte_tamper_stops_resume_even_when_selected_entry_stays_same(self):
        self.cli("continue", "--candidate", str(self.package_path))
        manifest = self.evidence / "render/manifest.json"
        manifest.write_bytes(manifest.read_bytes() + b" ")
        stopped = self.cli("continue")["banks"][0]
        self.assertEqual(stopped["point"], "needs-human")


    def test_historical_prepared_proposal_does_not_revalidate_new_raw_pending(self):
        bank = self.attempted["banks"][0]
        self.assertEqual(bank["point"], "waiting-proposal")
        self.assertEqual(bank["state"]["proposals"][0]["status"], "accepted")
        self.assertNotIn("reviewPackage", bank["state"]["proposals"][-1])
        self.assertEqual(self.cli("continue")["banks"][0], bank)

    def test_malformed_proposal_history_fails_structured_without_mutation(self):
        session_file = self.session / "session.json"
        record = json.loads(session_file.read_text())
        record["banks"][0]["state"]["proposals"].insert(0, None)
        put_json(session_file, record)
        before = session_file.read_bytes()
        state_before = (self.session / "banks/state/10.json").read_bytes()
        report_before = (self.session / "reports/10.json").read_bytes()
        result = self.cli("continue", ok=False)
        self.assertEqual(set(result), {"error"})
        self.assertEqual(session_file.read_bytes(), before)
        self.assertEqual((self.session / "banks/state/10.json").read_bytes(), state_before)
        self.assertEqual((self.session / "reports/10.json").read_bytes(), report_before)

    def missing_fixture(self, source=RAW):
        fixture = CandidateCLI("test_preparation_preserves_original_report_and_current_icon_pending_human_review")
        fixture.base_icon = None
        fixture.raw_source = source
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_first_icon_preparation_preserves_absence_and_pending_state(self):
        fixture = self.missing_fixture()
        original = (fixture.session / "evidence/10/validated.json").read_bytes()
        result = fixture.cli("continue", "--candidate", str(fixture.package_path))
        bank = result["banks"][0]
        self.assertIsNone(bank["iconHash"])
        self.assertIsNone(bank["state"]["proposals"][-1]["baseIconHash"])
        self.assertEqual(bank["state"]["proposals"][-1]["humanReview"], "pending")
        self.assertNotIn("verifiedIconHash", bank["state"])
        self.assertFalse((Path(fixture.started["worktree"]) / "banks" / fixture.folder / "icon.svg").exists())
        self.assertEqual((fixture.session / "evidence/10/validated.json").read_bytes(), original)
        self.assertTrue((fixture.session / fixture.package["diff"]["path"]).read_bytes().startswith(b"--- /dev/null\n+++ candidate/10/icon.svg\n"))
        self.assertEqual(fixture.cli("continue")["banks"][0], bank)

    def test_first_icon_rejects_fabricated_base_and_unbound_final_comparison(self):
        import copy
        fixture = self.missing_fixture()
        original = copy.deepcopy(fixture.package)
        before = (fixture.session / "session.json").read_bytes()
        for role in ("baseObservation", "base", "beforeAfter", "candidate"):
            with self.subTest(role=role):
                fixture.package = copy.deepcopy(original)
                if role == "baseObservation":
                    fixture.package["desktop"][role] = fixture.package["comparison"]["light"]["source"]
                else:
                    fixture.package["comparison"]["light"][role] = fixture.package["comparison"]["light"]["source"]
                fixture.update_package()
                fixture.cli("continue", "--candidate", str(fixture.package_path), ok=False)
                self.assertEqual((fixture.session / "session.json").read_bytes(), before)

    def test_first_icon_blocks_appeared_checkout_and_moved_baseline(self):
        fixture = self.missing_fixture()
        before = (fixture.session / "session.json").read_bytes()
        icon = Path(fixture.started["worktree"]) / "banks" / fixture.folder / "icon.svg"
        icon.write_bytes(BASE)
        fixture.cli("continue", "--candidate", str(fixture.package_path), ok=False)
        self.assertEqual((fixture.session / "session.json").read_bytes(), before)
        icon.unlink()
        fixture.cli("continue", "--candidate", str(fixture.package_path))
        (fixture.repo / "banks" / fixture.folder / "icon.svg").write_bytes(BASE)
        git(fixture.repo, "add", ".")
        git(fixture.repo, "commit", "-qm", "Appeared accepted icon")
        state = (fixture.session / "banks/state/10.json").read_bytes()
        stopped = fixture.cli("continue")["banks"][0]
        self.assertEqual(stopped["point"], "needs-human")
        self.assertEqual((fixture.session / "banks/state/10.json").read_bytes(), state)

    def test_first_icon_does_not_relax_original_vector_source_policy(self):
        fixture = self.missing_fixture(b'<svg xmlns="http://www.w3.org/2000/svg"><image href="data:image/png;base64,AA=="/></svg>')
        before = (fixture.session / "session.json").read_bytes()
        rejected = fixture.cli("continue", "--candidate", str(fixture.package_path), ok=False)
        self.assertIn("original vector", rejected["error"])
        self.assertEqual((fixture.session / "session.json").read_bytes(), before)
