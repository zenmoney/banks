"""External CLI behavior against temporary Git catalogs; evidence is controlled, not source proof."""
from session_fixture import make_session
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "session.py"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
HASH = hashlib.sha256(SVG).hexdigest()
NOW = "2026-09-25T00:00:00Z"


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class AttemptCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        put(self.repo / "bank-icon-maintenance.json", {"schemaVersion": 1,
            "recheckAfterDays": 90, "retryUnconfirmedAfterDays": 30,
            "retryErrorAfterDays": 7, "selectionLimit": 5})
        self.bank(10, "Alpha")
        self.bank(20, "Beta")
        self.commit()
        self.session = self.root / "session"

    def bank(self, id, name):
        folder = self.repo / "banks" / f"{name}_{id}-ru"
        put(folder / "info.json", {"id": id, "title": name, "countryCode": "RU"})
        (folder / "icon.svg").write_bytes(SVG)

    def commit(self):
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "fixture")

    def state(self, record):
        current = git(self.repo, "branch", "--show-current")
        git(self.repo, "checkout", "-q", "-b", "banks-icon-agent-state")
        put(self.repo / "banks" / "state" / "10.json", record)
        self.commit()
        git(self.repo, "checkout", "-q", current)

    def cli(self, command, *extra, ok=True):
        args = [sys.executable, str(SCRIPT), command, "--session", str(self.session)]
        result = subprocess.run(args + list(extra), capture_output=True, text=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def evidence(self, session, id=10, outcome="unconfirmed", **changes):
        searches = ([{"kind": "zenplugins-issue", "url": "https://example.invalid/zenplugins/issues?q=icon",
                     "assetUrl": None, "checkedAt": NOW,
                     "observation": "Simulated issue query, no matching asset", "independenceGroup": "fictional-issues"},
                    {"kind": "zenplugins-pr", "url": "https://example.invalid/zenplugins/pulls?q=icon",
                     "assetUrl": None, "checkedAt": NOW,
                     "observation": "Simulated PR query, no matching asset", "independenceGroup": "fictional-prs"}]
                    if outcome == "unconfirmed" else [])
        payload = {"schemaVersion": 1, "id": id, "baseCommit": session["baseCommit"],
                   "iconHash": HASH, "checkedAt": NOW, "outcome": outcome,
                   "reason": "Search did not establish current logo", "nextAction": "Review more sources",
                   "sources": searches, "assessment": {"identity": {"finding": "uncertain", "basis": "No corroboration"},
                   "currency": {"finding": "uncertain", "basis": "No dated mark"},
                   "variant": {"finding": "uncertain", "basis": "Role unknown"},
                   "independenceRationale": "No independent sources", "uncertainties": ["No corroboration"]},
                   "desktop": None,
                   "platforms": [], "recheckRequired": False}
        payload.update(changes)
        path = self.session / "evidence" / str(id) / "attempt.json"
        put(path, payload)
        return path

    def test_absent_baseline_stopped_attempt_is_durable_and_never_verified(self):
        (self.repo / "banks/Alpha_10-ru/icon.svg").unlink()
        self.commit()
        started = make_session(self)
        path = self.evidence(started, iconHash=None)
        result = self.cli("continue", "--evidence", str(path))
        state = result["banks"][0]["state"]
        self.assertEqual(state["lastAttemptResult"], "unconfirmed")
        self.assertNotIn("verifiedIconHash", state)
        self.assertEqual(self.cli("continue")["banks"][0]["state"], state)

    def test_first_candidate_pins_null_original_baseline(self):
        (self.repo / "banks/Alpha_10-ru/icon.svg").unlink()
        self.commit()
        started = make_session(self)
        candidate_path = self.session / "evidence/10/candidate.svg"
        candidate_path.parent.mkdir(parents=True)
        candidate_path.write_bytes(SVG)
        path = self.positive(started, "candidate", iconHash=None, candidate={
            "path": "evidence/10/candidate.svg", "iconHash": HASH,
            "sourceUrl": "https://example.invalid/bank",
            "sourceAssetUrl": "https://example.invalid/logo.svg", "rationale": "First icon"})
        result = self.cli("continue", "--evidence", str(path))
        self.assertEqual(result["banks"][0]["point"], "waiting-proposal")
        state = result["banks"][0]["state"]
        self.assertIsNone(state["proposals"][-1]["baseIconHash"])
        self.assertNotIn("verifiedIconHash", state)
        self.assertEqual(self.cli("continue")["banks"][0]["state"], state)

    def test_absent_baseline_cannot_confirm_unchanged(self):
        (self.repo / "banks/Alpha_10-ru/icon.svg").unlink()
        self.commit()
        started = make_session(self)
        path = self.positive(started, "unchanged", iconHash=None)
        self.assertIn("absent icon", self.cli("continue", "--evidence", str(path), ok=False)["error"])
        self.assertFalse((self.session / "reports/10.json").exists())

    def test_appeared_baseline_blocks_record_without_mutating_evidence(self):
        (self.repo / "banks/Alpha_10-ru/icon.svg").unlink()
        self.commit()
        started = make_session(self)
        path = self.evidence(started, iconHash=None)
        original = path.read_bytes()
        (Path(started["worktree"]) / "banks/Alpha_10-ru/icon.svg").write_bytes(SVG)
        result = self.cli("continue", "--evidence", str(path))
        self.assertEqual(result["banks"][0]["point"], "needs-human")
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse((self.session / "reports/10.json").exists())


    def test_unconfirmed_retains_prior_verification_and_history_in_local_state(self):
        prior = {"schemaVersion": 1, "lastAttemptAt": "2025-01-01T00:00:00Z",
                 "lastAttemptResult": "verified", "lastVerifiedAt": "2025-01-01T00:00:00Z",
                 "verifiedIconHash": HASH, "customHistory": {"source": "preserve"},
                 "proposals": [{"status": "accepted", "mr": {"state": "merged", "url": "prior"},
                                "note": "do not lose"}]}
        self.state(prior)
        started = make_session(self)
        path = self.evidence(started)
        completed = self.cli("continue", "--evidence", str(path))
        bank = completed["banks"][0]
        self.assertEqual(bank["point"], "attempt-complete")
        state = json.loads((self.session / "banks" / "state" / "10.json").read_text())
        self.assertEqual(state["lastAttemptAt"], NOW)
        self.assertEqual(state["lastAttemptResult"], "unconfirmed")
        for key in ("lastVerifiedAt", "verifiedIconHash", "proposals", "customHistory"):
            self.assertEqual(state[key], prior[key])
        report = json.loads((self.session / "reports" / "10.json").read_text())
        self.assertEqual(report["before"], prior)
        self.assertEqual(report["after"], state)
        self.assertEqual(report["evidence"]["outcome"], "unconfirmed")
        self.assertEqual(report["provenance"]["stateCommit"], started["stateCommit"])
        self.assertEqual(git(self.repo, "rev-parse", "refs/heads/banks-icon-agent-state"), started["stateCommit"])
        put(path, {"id": 10, "outcome": "error"})
        self.assertEqual(json.loads((self.session / bank["evidence"]).read_text())["outcome"], "unconfirmed")
        self.assertEqual(self.cli("continue")["banks"][0], bank)
        self.assertEqual(self.cli("pause")["banks"][0]["priorPoint"], "attempt-complete")
        self.assertEqual(self.cli("continue")["banks"][0], bank)

    def positive(self, started, outcome, **changes):
        sources = [{"kind": "official", "url": "https://example.invalid/bank", "assetUrl": "https://example.invalid/logo.svg",
                    "checkedAt": NOW, "observation": "Controlled mark", "independenceGroup": "first"},
                   {"kind": "zenplugins-issue", "url": "https://github.com/ZenPlugins/brands/issues/1", "assetUrl": None,
                    "checkedAt": NOW, "observation": "Search yielded no result", "independenceGroup": "issue-search"},
                   {"kind": "zenplugins-pr", "url": "https://github.com/ZenPlugins/brands/pulls", "assetUrl": None,
                    "checkedAt": NOW, "observation": "Search yielded no result", "independenceGroup": "pr-search"}]
        assessment = {"identity": {"finding": "confirmed", "basis": "Controlled observation"},
                      "currency": {"finding": "confirmed", "basis": "Controlled observation"},
                      "variant": {"finding": "confirmed", "basis": "Controlled observation"},
                      "independenceRationale": "Controlled independent observations", "uncertainties": []}
        return self.evidence(started, outcome=outcome, sources=sources, assessment=assessment, **changes)

    def test_candidate_is_pending_and_never_verifies_current_icon(self):
        previous = {"schemaVersion": 1, "lastVerifiedAt": "2025-01-01T00:00:00Z",
                    "verifiedIconHash": HASH, "proposals": [{"status": "accepted", "mr": {"state": "merged"},
                                                          "candidateIconHash": "0" * 64, "note": "earlier"}]}
        self.state(previous)
        started = make_session(self)
        candidate_svg = b'<svg xmlns="http://www.w3.org/2000/svg"><circle r="5"/></svg>'
        candidate_path = self.session / "evidence" / "10" / "candidate.svg"
        candidate_path.parent.mkdir(parents=True)
        candidate_path.write_bytes(candidate_svg)
        candidate_hash = hashlib.sha256(candidate_svg).hexdigest()
        path = self.positive(started, "candidate", candidate={"path": "evidence/10/candidate.svg",
                             "iconHash": candidate_hash, "sourceUrl": "https://example.invalid/bank",
                             "sourceAssetUrl": "https://example.invalid/logo.svg", "rationale": "Distinct newer mark"})
        result = self.cli("continue", "--evidence", str(path))
        self.assertEqual(result["banks"][0]["point"], "waiting-proposal")
        record = json.loads((self.session / "banks" / "state" / "10.json").read_text())
        self.assertEqual(record["lastAttemptResult"], "candidate")
        self.assertEqual(record["lastVerifiedAt"], previous["lastVerifiedAt"])
        self.assertEqual(record["verifiedIconHash"], HASH)
        self.assertEqual(record["proposals"][0], previous["proposals"][0])
        proposal = record["proposals"][1]
        self.assertEqual((proposal["baseIconHash"], proposal["candidateIconHash"]), (HASH, candidate_hash))
        self.assertEqual(proposal["candidate"]["sourceAssetUrl"], "https://example.invalid/logo.svg")
        self.assertEqual(proposal["status"], "pending")
        self.assertNotIn("mr", proposal)
        self.assertNotIn("candidateVerifiedAt", proposal)
        self.assertEqual(self.cli("continue")["banks"][0]["point"], "waiting-proposal")

    def test_invalid_evidence_leaves_prior_attempt_and_local_outputs_untouched(self):
        self.state({"schemaVersion": 1, "lastAttemptAt": "2025-01-01T00:00:00Z",
                    "lastAttemptResult": "error"})
        started = make_session(self)
        prior = started["banks"][0]
        bad = self.evidence(started, iconHash="0" * 64)
        self.assertIn("invalid evidence", self.cli("continue", "--evidence", str(bad), ok=False)["error"])
        self.assertIn("evidence must be", self.cli("continue", "--evidence", "", ok=False)["error"])
        self.assertEqual(self.cli("continue")["banks"][0], prior)
        self.assertFalse((self.session / "banks" / "state" / "10.json").exists())
        self.assertFalse((self.session / "reports" / "10.json").exists())
        self.assertFalse((self.session / "evidence" / "10" / "validated.json").exists())
        good = self.evidence(started)
        self.assertEqual(self.cli("continue", "--evidence", str(good))["banks"][0]["point"], "attempt-complete")
        self.assertIn("not at identity-evidence", self.cli("continue", "--evidence", str(good), ok=False)["error"])

    def test_catalog_drift_stops_only_affected_bank_without_attempt(self):
        started = make_session(self)
        worktree = Path(started["worktree"])
        put(worktree / "banks" / "Alpha_10-ru" / "info.json",
            {"id": 10, "title": "Changed", "countryCode": "RU"})
        path = self.evidence(started)
        stopped = self.cli("continue", "--evidence", str(path))
        self.assertEqual(stopped["banks"][0]["point"], "needs-human")
        self.assertIn("catalog", stopped["banks"][0]["reason"])
        self.assertIsNone(stopped["banks"][0]["state"])
        self.assertFalse((self.session / "banks" / "state" / "10.json").exists())
        frozen = (self.session / "session.json").read_bytes()
        first_session = self.session
        self.session = self.root / "neighbor"
        second = make_session(self, bank_id=20)
        neighbor = self.evidence(second, id=20)
        self.assertEqual(self.cli("continue", "--evidence", str(neighbor))["banks"][0]["point"], "attempt-complete")
        self.assertEqual((first_session / "session.json").read_bytes(), frozen)

    def test_changed_svg_stops_before_stale_evidence(self):
        started = make_session(self)
        worktree = Path(started["worktree"])
        (worktree / "banks" / "Alpha_10-ru" / "icon.svg").write_bytes(b"changed")
        result = self.cli("continue", "--evidence", str(self.evidence(started)))
        self.assertEqual(result["banks"][0]["point"], "needs-human")
        self.assertFalse((self.session / "reports" / "10.json").exists())

    def test_waiting_bank_cannot_consume_evidence(self):
        self.state({"schemaVersion": 1, "proposals": [{"status": "pending", "baseIconHash": HASH,
                                                        "candidateIconHash": "0" * 64}]})
        started = make_session(self)
        self.assertEqual(started["banks"][0]["point"], "waiting-proposal")
        path = self.evidence(started)
        self.assertIn("not at identity-evidence", self.cli("continue", "--evidence", str(path), ok=False)["error"])
        self.assertFalse((self.session / "banks" / "state" / "10.json").exists())

    def test_observed_unchanged_records_only_exact_current_version_locally(self):
        started = make_session(self)
        render = self.session / "evidence" / "10" / "render"
        xml = b'<vector xmlns:android="http://schemas.android.com/apk/res/android"/>'
        xml_path = render / "composeResources" / "drawable" / "bank_10.xml"
        xml_path.parent.mkdir(parents=True)
        xml_path.write_bytes(xml)
        xml_hash = hashlib.sha256(xml).hexdigest()
        put(render / "manifest.json", {"schemaVersion": 1, "sourceRoot": started["worktree"],
            "banksRoot": str(Path(started["worktree"]) / "banks"),
            "pins": {"converter": "fixture-converter", "compose": "fixture-compose"},
            "entries": [{"id": 10, "error": None, "sourcePath": "banks/Alpha_10-ru/icon.svg",
                         "sourceSha256": HASH, "sourceValidated": True,
                         "xmlSha256": xml_hash, "resourceName": "bank_10"}]})
        # Controlled PNG artifacts exercise binding only; this test is not graphical proof.
        png = bytes.fromhex("89504e470d0a1a0a") + b"controlled screenshot bytes"
        screenshots = {}
        for background in ("light", "dark"):
            image = render / f"{background}.png"
            image.write_bytes(png)
            screenshots[background] = {"path": f"evidence/10/render/{background}.png",
                                       "sha256": hashlib.sha256(png).hexdigest()}
        desktop = {"renderer": "compose-desktop-vector-xml-painterResource",
                   "manifest": "evidence/10/render/manifest.json", "observedAt": NOW,
                   "visualComparison": "Controlled current variant matches", **screenshots}
        platforms = [{"target": target, "status": "not-tested", "observation": "Unavailable in fixture"}
                     for target in ("android-compose", "android-native", "ios-compose", "ios-native")]
        path = self.positive(started, "unchanged", desktop=desktop, platforms=platforms,
                             recheckRationale="No new converter or exceptional SVG conditions")
        result = self.cli("continue", "--evidence", str(path))
        bank = result["banks"][0]
        self.assertEqual(bank["point"], "awaiting-state-publication")
        self.assertEqual(bank["report"], "reports/10.json")
        state = json.loads((self.session / "banks" / "state" / "10.json").read_text())
        self.assertEqual((state["lastAttemptResult"], state["lastVerifiedAt"], state["verifiedIconHash"]),
                         ("verified", NOW, HASH))
        self.assertEqual((Path(started["worktree"]) / "banks" / "Alpha_10-ru" / "icon.svg").read_bytes(), SVG)
        self.assertEqual(self.cli("continue")["banks"][0], bank)
        report = json.loads((self.session / bank["report"]).read_text())
        self.assertEqual(report["humanReview"], "pending")
        self.assertEqual(report["evidence"]["desktop"]["manifest"], "evidence/10/render/manifest.json")

    def test_error_and_human_question_record_attempt_but_no_current_verification(self):
        for id, outcome, point in ((10, "error", "attempt-complete"), (20, "needs-human", "needs-human")):
            self.session = self.root / f"session-{id}"
            started = make_session(self, bank_id=id)
            path = self.evidence(started, id=id, outcome=outcome)
            result = self.cli("continue", "--evidence", str(path))
            self.assertEqual(result["banks"][0]["point"], point)
            record = json.loads((self.session / "banks/state" / f"{id}.json").read_text())
            self.assertEqual(record["lastAttemptResult"], outcome)
            self.assertNotIn("lastVerifiedAt", record)
            self.assertNotIn("verifiedIconHash", record)

    def test_metadata_file_change_stops_stale_evidence_even_if_fields_match(self):
        started = make_session(self)
        metadata = Path(started["worktree"]) / "banks" / "Alpha_10-ru" / "info.json"
        metadata.write_text(json.dumps(json.loads(metadata.read_text()), indent=4), encoding="utf-8")
        result = self.cli("continue", "--evidence", str(self.evidence(started)))
        self.assertEqual(result["banks"][0]["point"], "needs-human")
        self.assertFalse((self.session / "banks" / "state" / "10.json").exists())

    def test_new_duplicate_company_id_stops_original_bank_only(self):
        started = make_session(self)
        worktree = Path(started["worktree"])
        folder = worktree / "banks" / "Twin_10-ru"
        put(folder / "info.json", {"id": 10, "title": "Twin", "countryCode": "RU"})
        (folder / "icon.svg").write_bytes(SVG)
        result = self.cli("continue", "--evidence", str(self.evidence(started)))
        self.assertEqual(result["banks"][0]["point"], "needs-human")

    def test_local_state_output_refuses_symlinked_ancestor(self):
        started = make_session(self)
        external = self.root / "external"
        (external / "state").mkdir(parents=True)
        (self.session / "banks").symlink_to(external, target_is_directory=True)
        path = self.evidence(started)
        result = self.cli("continue", "--evidence", str(path), ok=False)
        self.assertIn("symlink", result["error"])
        self.assertFalse((external / "state" / "10.json").exists())
        self.assertEqual(self.cli("continue")["banks"][0]["point"], "identity-evidence")

    def test_preexisting_local_output_is_preserved_on_failed_attempt(self):
        started = make_session(self)
        destination = self.session / "banks" / "state" / "10.json"
        destination.parent.mkdir(parents=True)
        destination.write_text("do not replace this file", encoding="utf-8")
        path = self.evidence(started)
        self.assertIn("already exists", self.cli("continue", "--evidence", str(path), ok=False)["error"])
        self.assertEqual(destination.read_text(), "do not replace this file")
        self.assertFalse((self.session / "reports" / "10.json").exists())
        self.assertFalse((self.session / "evidence" / "10" / "validated.json").exists())

    def test_paused_session_cannot_record_until_resumed(self):
        started = make_session(self)
        path = self.evidence(started)
        paused = self.cli("pause")
        self.assertEqual(paused["banks"][0]["priorPoint"], "identity-evidence")
        self.assertIn("resume", self.cli("continue", "--evidence", str(path), ok=False)["error"])
        self.assertFalse((self.session / "banks" / "state" / "10.json").exists())
        self.assertEqual(self.cli("continue")["banks"][0]["point"], "identity-evidence")
        self.assertEqual(self.cli("continue", "--evidence", str(path))["banks"][0]["point"], "attempt-complete")
