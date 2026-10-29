"""State-package behavior through external session CLI and disposable Git catalogs.

PNG/XML here are schema fixtures, not proof of a graphical renderer observation.
"""
from session_fixture import make_session
import difflib
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "session.py"
NOW = "2026-09-25T12:00:00Z"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 54 54"/>'
XML = b'<vector xmlns:android="http://schemas.android.com/apk/res/android" android:width="54dp"/>'
RAW = b'<svg xmlns="http://www.w3.org/2000/svg"><circle r="19"/></svg>'
FINAL = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 54 54"><circle r="19"/></svg>'


def sha(content):
    return hashlib.sha256(content).hexdigest()


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class StateResultsCLI(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.repo = root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-qb", "main")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        put(self.repo / "bank-icon-maintenance.json", {"schemaVersion": 1, "recheckAfterDays": 90,
            "retryUnconfirmedAfterDays": 30, "retryErrorAfterDays": 7, "selectionLimit": 5})
        self.folder = "Alpha_10-ru"
        put(self.repo / "banks" / self.folder / "info.json",
            {"id": 10, "title": "Alpha", "countryCode": "RU"})
        (self.repo / "banks" / self.folder / "icon.svg").write_bytes(SVG)
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "catalog")
        git(self.repo, "checkout", "-qb", "banks-icon-agent-state")
        self.prior = {"schemaVersion": 1, "lastAttemptAt": "2025-01-01T00:00:00Z",
                      "lastAttemptResult": "error", "customHistory": {"origin": "unknown"},
                      "proposals": [{"status": "accepted", "baseIconHash": sha(SVG),
                                     "mr": {"state": "merged"}, "note": "Historic proposal"}]}
        put(self.repo / "banks" / "state" / "10.json", self.prior)
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "prior service state")
        self.remote = root / "remote.git"
        subprocess.run(["git", "init", "--bare", "-q", str(self.remote)], check=True)
        git(self.repo, "push", str(self.remote), "banks-icon-agent-state")
        git(self.repo, "checkout", "-q", "main")
        self.session = root / "session"
        self.started = make_session(self , bank_id=10, now=NOW)
        self.attempt_path = self.session / "evidence" / "10" / "attempt.json"

    def cli(self, command, *args, ok=True):
        result = subprocess.run([sys.executable, str(SCRIPT), command, "--session",
                                 str(self.session), *args], capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def attempt(self, outcome="unconfirmed"):
        sources = [{"kind": kind, "url": url, "assetUrl": None, "checkedAt": NOW,
                    "observation": "Search performed; no verified current asset found.",
                    "independenceGroup": kind}
                   for kind, url in (("zenplugins-issue", "https://github.com/ZenPlugins/Zen/issues?q=Alpha"),
                                     ("zenplugins-pr", "https://github.com/ZenPlugins/Zen/pulls?q=Alpha"))]
        return {"schemaVersion": 1, "id": 10, "baseCommit": self.started["baseCommit"],
                "iconHash": sha(SVG), "checkedAt": NOW, "outcome": outcome,
                "reason": "Insufficient evidence of currency", "nextAction": "Recheck after new evidence",
                "sources": sources,
                "assessment": {name: {"finding": "uncertain", "basis": "No independent current asset"}
                               for name in ("identity", "currency", "variant")}
                | {"independenceRationale": "Searches do not independently establish current identity.",
                   "uncertainties": ["Current asset unavailable"]},
                "desktop": None, "platforms": [], "recheckRequired": False,
                "recheckRationale": "No current-version confirmation attempted"}

    def observed_attempt(self, outcome="needs-human", checked_at=NOW):
        record = self.attempt(outcome)
        record["checkedAt"] = checked_at
        record["sources"].insert(0, {"kind": "official", "url": "https://alpha.example.invalid/brand",
            "assetUrl": "https://alpha.example.invalid/logo.svg", "checkedAt": NOW,
            "observation": "Observed bank sign and compared the exact current variant.",
            "independenceGroup": "official-alpha"})
        for aspect in ("identity", "currency", "variant"):
            record["assessment"][aspect] = {"finding": "confirmed", "basis": "Current first-party sign"}
        record["assessment"]["uncertainties"] = []
        if outcome == "needs-human":
            record["assessment"]["variant"] = {"finding": "conflicting", "basis": "Variant ambiguity"}
            record["assessment"]["uncertainties"] = ["Confirm variant equivalence"]
        generated = self.session / "evidence/10/generated"
        xml = generated / "composeResources/drawable/bank_10.xml"
        xml.parent.mkdir(parents=True, exist_ok=True)
        xml.write_bytes(XML)
        put(generated / "manifest.json", {"schemaVersion": 1, "sourceRoot": str(self.session / "worktree"),
            "banksRoot": str(self.session / "worktree/banks"),
            "pins": {"converter": "0.9.2", "compose": "1.9.1"},
            "entries": [{"id": 10, "sourcePath": f"banks/{self.folder}/icon.svg",
                         "sourceSha256": sha(SVG), "sourceValidated": True, "xmlSha256": sha(XML),
                         "resourceName": "bank_10", "error": None}]})
        images = {}
        for background in ("light", "dark"):
            image = b"\x89PNG\r\n\x1a\n" + background.encode() + b" schema-only fixture"
            path = self.session / f"evidence/10/{background}.png"
            path.write_bytes(image)
            images[background] = {"path": str(path.relative_to(self.session)), "sha256": sha(image)}
        record["desktop"] = {"renderer": "compose-desktop-vector-xml-painterResource",
                             "manifest": "evidence/10/generated/manifest.json",
                             "observedAt": NOW, "visualComparison": "Fixture describes same sign on both backgrounds",
                             **images}
        record["platforms"] = [{"target": target, "status": "not-tested",
                                "observation": "Unavailable in schema-only fixture"}
                               for target in ("android-compose", "android-native", "ios-compose", "ios-native")]
        record["recheckRationale"] = "Exact SVG/XML/pins unchanged in schema-only fixture"
        return record

    def candidate_package(self):
        raw_path = self.session / "evidence/10/raw.svg"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(RAW)
        search = self.attempt("candidate")
        source = "https://alpha.example.invalid/brand"
        asset = "https://alpha.example.invalid/current.svg"
        search["sources"].insert(0, {"kind": "official", "url": source, "assetUrl": asset,
            "checkedAt": NOW, "observation": "Current bank-list variant", "independenceGroup": "alpha"})
        for aspect in ("identity", "currency", "variant"):
            search["assessment"][aspect] = {"finding": "confirmed", "basis": "Official asset compared"}
        search["assessment"]["uncertainties"] = []
        search["candidate"] = {"path": "evidence/10/raw.svg", "iconHash": sha(RAW),
                               "sourceUrl": source, "sourceAssetUrl": asset,
                               "rationale": "Current same-role bank-list variant"}
        self.record(search)

        def material(name, content):
            path = self.session / "evidence/10" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            return {"path": str(path.relative_to(self.session)), "sha256": sha(content)}

        final = material("final.svg", FINAL)
        material("render/composeResources/drawable/bank_10.xml", XML)
        put(self.session / "evidence/10/render/manifest.json", {
            "schemaVersion": 1, "sourceRoot": str(self.session / "worktree"),
            "banksRoot": str(self.session / "worktree/banks"),
            "pins": {"converter": "controlled-converter", "compose": "controlled-compose"},
            "entries": [{"id": 10, "sourcePath": str(self.session / final["path"]),
                         "sourceSha256": sha(FINAL), "sourceValidated": True,
                         "xmlSha256": sha(XML), "resourceName": "bank_10", "error": None}]})
        manifest_hash = sha((self.session / "evidence/10/render/manifest.json").read_bytes())
        screens = {background: material(f"render/{background}.png",
                   b"\x89PNG\r\n\x1a\n" + background.encode())
                   for background in ("light", "dark")}
        comparison = {background: {role: material(f"compare/{background}-{role}.png",
                      b"\x89PNG\r\n\x1a\n" + background.encode() + role.encode())
                      for role in ("source", "base", "candidate", "beforeAfter")}
                      for background in ("light", "dark")}
        draft = f"Alpha 10 {sha(SVG)} {sha(FINAL)} {source} {asset} comparison"
        diff = ("\n".join(difflib.unified_diff(SVG.decode().splitlines(), FINAL.decode().splitlines(),
                fromfile="base/10/icon.svg", tofile="candidate/10/icon.svg", lineterm="")) + "\n").encode()
        package = {"schemaVersion": 1, "id": 10, "baseCommit": self.started["baseCommit"],
                   "baseIconHash": sha(SVG), "metadataHash": self.started["banks"][0]["metadataHash"],
                   "checkedAt": NOW,
                   "priorReport": {"path": "reports/10.json",
                                   "sha256": sha((self.session / "reports/10.json").read_bytes())},
                   "priorEvidence": {"path": "evidence/10/validated.json",
                                     "sha256": sha((self.session / "evidence/10/validated.json").read_bytes())},
                   "candidate": {**final, "sourceUrl": source, "sourceAssetUrl": asset,
                       "rationale": "Same-role current circle", "role": "bank-list icon",
                       "variant": "current circle", "transforms": ["Normalize dimensions without cropping"],
                       "detailsJudgement": "Details retained", "proportionsJudgement": "Proportions retained",
                       "faithful": True, "visibleDiff": True,
                       "visibleDiffExplanation": "Circle size differs on both backgrounds"},
                   "desktop": {"renderer": "compose-desktop-vector-xml-painterResource",
                       "manifest": "evidence/10/render/manifest.json", "observedAt": NOW,
                       "manifestSha256": manifest_hash,
                       "visualComparison": "Controlled comparison fixture", **screens},
                   "comparison": comparison,
                   "platforms": [{"target": target, "available": False, "status": "not-tested",
                                  "observation": "Unavailable in schema-only fixture"}
                                 for target in ("android-compose", "android-native", "ios-compose", "ios-native")],
                   "mrDraft": material("mr-draft.txt", draft.encode()),
                   "diff": material("candidate.diff", diff)}
        package_path = self.session / "evidence/10/candidate-package.json"
        put(package_path, package)
        return package_path

    def forge_prior_proposal(self, status):
        report_path = self.session / "reports/10.json"
        report = json.loads(report_path.read_text())
        proposal = dict(report["after"]["proposals"][-1])
        proposal["status"] = status
        if status == "rejected":
            proposal["rejectionReason"] = "This exact variant was rejected by a human"
        new_prior = {**self.prior, "proposals": [*self.prior["proposals"], proposal]}
        git(self.repo, "checkout", "-q", "banks-icon-agent-state")
        put(self.repo / "banks/state/10.json", new_prior)
        git(self.repo, "add", "banks/state/10.json")
        git(self.repo, "commit", "-qm", "prior proposal requires reconciliation")
        new_commit = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "push", str(self.remote), "banks-icon-agent-state")
        git(self.repo, "checkout", "-q", "main")
        after = {**new_prior, "lastAttemptAt": NOW, "lastAttemptResult": "candidate",
                 "proposals": [*new_prior["proposals"], report["after"]["proposals"][-1]]}
        report.update(before=new_prior, after=after)
        report["provenance"]["stateCommit"] = new_commit
        put(report_path, report)
        put(self.session / "banks/state/10.json", after)
        session_data = json.loads((self.session / "session.json").read_text())
        session_data["stateCommit"] = new_commit
        session_data["banks"][0]["state"] = after
        put(self.session / "session.json", session_data)

    def record(self, evidence):
        put(self.attempt_path, evidence)
        return self.cli("continue", "--evidence", str(self.attempt_path))

    def prepare(self, *, ok=True, ids=(10,)):
        return self.cli("state-prepare", "--remote", str(self.remote), "--ids",
                        *(str(id) for id in ids), ok=ok)

    def test_unconfirmed_attempt_preserves_unknown_history_and_does_not_invent_verification(self):
        self.record(self.attempt())
        prepared = self.prepare()
        record = prepared["package"]["records"][0]
        self.assertEqual({key: record[key] for key in ("id", "path", "before", "after")}, {
            "id": 10, "path": "banks/state/10.json", "before": self.prior,
            "after": self.prior | {"lastAttemptAt": NOW, "lastAttemptResult": "unconfirmed"}})
        self.assertEqual(len(prepared["packageHash"]), 64)
        self.assertEqual(json.loads((self.session / "banks/state/10.json").read_text()),
                         prepared["package"]["records"][0]["after"])

    def test_separate_bank_sessions_keep_distinct_prior_histories(self):
        beta = "Beta_11-ru"
        put(self.repo / "banks" / beta / "info.json", {"id": 11, "title": "Beta", "countryCode": "RU"})
        (self.repo / "banks" / beta / "icon.svg").write_bytes(FINAL)
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "second bank")
        packages = []
        for id in (10, 11):
            self.session = self.session.parent / f"bank-{id}"
            self.started = make_session(self, bank_id=id, now=NOW)
            self.attempt_path = self.session / f"evidence/{id}/attempt.json"
            value = self.attempt()
            if id == 11:
                value.update(id=11, iconHash=sha(FINAL))
            self.record(value)
            packages.append(self.prepare(ids=(id,))["package"])
        self.assertEqual([p["ids"] for p in packages], [[10], [11]])
        self.assertEqual([p["records"][0]["before"] for p in packages], [self.prior, None])
        self.assertEqual(packages[0]["records"][0]["after"]["proposals"], self.prior["proposals"])
        self.assertEqual(packages[1]["records"][0]["after"],
                         {"schemaVersion": 1, "lastAttemptAt": NOW, "lastAttemptResult": "unconfirmed"})

    def test_absent_service_history_stays_unknown_with_null_before(self):
        root = self.session.parent
        self.repo = root / "new-repo"
        self.repo.mkdir()
        git(self.repo, "init", "-qb", "main")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        put(self.repo / "bank-icon-maintenance.json", {"schemaVersion": 1,
            "recheckAfterDays": 90, "retryUnconfirmedAfterDays": 30,
            "retryErrorAfterDays": 7, "selectionLimit": 5})
        put(self.repo / "banks" / self.folder / "info.json",
            {"id": 10, "title": "Alpha", "countryCode": "RU"})
        (self.repo / "banks" / self.folder / "icon.svg").write_bytes(SVG)
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "catalog without service state")
        self.remote = root / "empty.git"
        subprocess.run(["git", "init", "--bare", "-q", str(self.remote)], check=True)
        self.session = root / "new-session"
        self.started = make_session(self , bank_id=10, now=NOW)
        self.attempt_path = self.session / "evidence/10/attempt.json"
        self.record(self.attempt())
        package = self.prepare()["package"]
        self.assertEqual((package["stateHistory"], package["observedRef"]), ("unknown", None))
        self.assertEqual(package["records"][0]["before"], None)
        self.assertEqual(package["records"][0]["after"],
                         {"schemaVersion": 1, "lastAttemptAt": NOW, "lastAttemptResult": "unconfirmed"})

    def test_per_bank_state_rejects_id_and_icon_updated_at_even_in_original_history(self):
        root = self.session.parent
        for field, value in (("id", 10), ("iconUpdatedAt", NOW)):
            with self.subTest(field=field):
                git(self.repo, "checkout", "-q", "banks-icon-agent-state")
                put(self.repo / "banks/state/10.json", self.prior | {field: value})
                git(self.repo, "add", "banks/state/10.json")
                git(self.repo, "commit", "-qm", f"forbidden {field} state fixture")
                git(self.repo, "push", str(self.remote), "banks-icon-agent-state")
                git(self.repo, "checkout", "-q", "main")
                self.session = root / f"forbidden-{field}"
                self.started = make_session(self , bank_id=10, now=NOW)
                self.attempt_path = self.session / "evidence/10/attempt.json"
                self.record(self.attempt())
                self.assertIn("error", self.prepare(ok=False))

    def test_report_and_session_cannot_self_certify_extra_verification(self):
        self.record(self.attempt())
        report_path = self.session / "reports/10.json"
        report = json.loads(report_path.read_text())
        forged = report["after"] | {"lastVerifiedAt": NOW, "verifiedIconHash": sha(SVG)}
        report["after"] = forged
        put(report_path, report)
        put(self.session / "banks/state/10.json", forged)
        data = json.loads((self.session / "session.json").read_text())
        data["banks"][0]["state"] = forged
        put(self.session / "session.json", data)
        self.assertIn("error", self.prepare(ok=False))
        self.assertEqual(json.loads((self.session / "banks/state/10.json").read_text()), forged)

    def test_saved_evidence_remains_valid_when_mutable_original_attempt_changes(self):
        self.record(self.attempt())
        self.attempt_path.write_text('{"draft":"changed after recording"}', encoding="utf-8")
        prepared = self.prepare()
        self.assertEqual(prepared["package"]["records"][0]["after"]["lastAttemptResult"], "unconfirmed")
        self.assertNotIn(str(self.attempt_path), prepared["package"]["bindings"])
        self.assertIn(str(self.session / "evidence/10/validated.json"), prepared["package"]["bindings"])

    def test_paused_session_cannot_prepare_until_resumed(self):
        self.record(self.attempt())
        self.cli("pause")
        self.assertIn("error", self.prepare(ok=False))
        self.cli("continue")
        self.assertEqual(self.prepare()["package"]["records"][0]["after"]["lastAttemptResult"],
                         "unconfirmed")

    def test_boolean_schema_version_is_not_a_valid_session(self):
        self.record(self.attempt())
        path = self.session / "session.json"
        data = json.loads(path.read_text())
        data["schemaVersion"] = True
        put(path, data)
        self.assertIn("error", self.prepare(ok=False))

    def test_unrelated_base_commit_keeps_exact_selected_bank_result_valid(self):
        self.record(self.attempt())
        (self.repo / "implementation.txt").write_text("unrelated change", encoding="utf-8")
        git(self.repo, "add", "implementation.txt")
        git(self.repo, "commit", "-qm", "unrelated implementation")
        package = self.prepare()["package"]
        self.assertEqual(package["baseCommit"], self.started["baseCommit"])
        self.assertEqual(package["records"][0]["after"]["lastAttemptResult"], "unconfirmed")

    def test_changed_accepted_catalog_base_stops_old_session_result(self):
        self.record(self.attempt())
        (self.repo / "banks" / self.folder / "icon.svg").write_bytes(FINAL)
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "accepted icon changed")
        self.assertIn("error", self.prepare(ok=False))

    def test_new_duplicate_company_id_in_accepted_base_stops_result(self):
        self.record(self.attempt())
        duplicate = self.repo / "banks/Other_10-ru"
        put(duplicate / "info.json", {"id": 10, "title": "Other", "countryCode": "RU"})
        (duplicate / "icon.svg").write_bytes(SVG)
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "ambiguous accepted Company.id")
        self.assertIn("error", self.prepare(ok=False))

    def test_moved_base_metadata_id_duplicate_stops_error_result(self):
        self.record(self.attempt("error"))
        duplicate = self.repo / "banks/Beta_20-ru"
        put(duplicate / "info.json", {"id": 10, "title": "Beta", "countryCode": "RU"})
        (duplicate / "icon.svg").write_bytes(SVG)
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "accepted catalog metadata aliases selected Company.id")
        self.assertIn("error", self.prepare(ok=False))

    def test_moved_base_folder_id_collision_stops_result_despite_other_metadata_id(self):
        self.record(self.attempt())
        collision = self.repo / "banks/Beta_10-ru"
        put(collision / "info.json", {"id": 20, "title": "Beta", "countryCode": "RU"})
        (collision / "icon.svg").write_bytes(SVG)
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "folder still claims selected Company.id")
        self.assertIn("error", self.prepare(ok=False))

    def test_moved_base_malformed_metadata_in_selected_id_folder_stops_result(self):
        self.record(self.attempt())
        collision = self.repo / "banks/Beta_10-ru"
        collision.mkdir()
        (collision / "info.json").write_text('{"id":', encoding="utf-8")
        (collision / "icon.svg").write_bytes(SVG)
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "invalid metadata under selected Company.id folder")
        self.assertIn("error", self.prepare(ok=False))

    def test_replaced_catalog_symlink_returns_safe_json_error(self):
        self.record(self.attempt())
        banks = self.session / "worktree/banks"
        hidden = self.session / "worktree/banks-saved"
        banks.rename(hidden)
        banks.symlink_to(hidden, target_is_directory=True)
        self.assertIn("error", self.prepare(ok=False))

    def test_reviewed_variant_only_advances_freshness_after_bound_human_assessment(self):
        initial = self.observed_attempt()
        self.record(initial)
        reviewed_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        checked_at = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        completed = self.observed_attempt("unchanged", checked_at)
        completed["sources"] = initial["sources"]
        completed["desktop"] = initial["desktop"]
        completed["platforms"] = initial["platforms"]
        completed["assessment"]["variant"]["basis"] = "Reviewer resolved the same variant ambiguity"
        comparison = self.session / "evidence/10/comparison.png"
        comparison.write_bytes(b"\x89PNG\r\n\x1a\ncomparison fixture")
        new_path = self.session / "evidence/10/review-attempt.json"
        put(new_path, completed)
        review = {"schemaVersion": 1, "id": 10, "baseCommit": self.started["baseCommit"],
                  "iconHash": sha(SVG), "decision": "equivalent-current-variant",
                  "reviewer": "Human fixture reviewer", "statement": "Exact current variant was compared.",
                  "reviewedAt": reviewed_at,
                  "priorReport": {"path": "reports/10.json",
                                  "sha256": sha((self.session / "reports/10.json").read_bytes())},
                  "priorEvidence": {"path": "evidence/10/validated.json",
                                    "sha256": sha((self.session / "evidence/10/validated.json").read_bytes())},
                  "evidence": {"path": "evidence/10/review-attempt.json", "sha256": sha(new_path.read_bytes())},
                  "comparison": {"path": "evidence/10/comparison.png",
                                 "sha256": sha(comparison.read_bytes())}}
        review_path = self.session / "evidence/10/review.json"
        put(review_path, review)
        self.cli("continue", "--review", str(review_path))
        prepared = self.prepare()
        record = prepared["package"]["records"][0]
        self.assertEqual(record["before"], self.prior)
        self.assertEqual(record["after"], self.prior | {
            "lastAttemptAt": checked_at, "lastAttemptResult": "verified",
            "lastVerifiedAt": checked_at, "verifiedIconHash": sha(SVG)})
        self.assertEqual(record["path"], "banks/state/10.json")

    def test_historical_report_without_render_binding_uses_pinned_passed_platform(self):
        initial = self.observed_attempt()
        initial["platforms"][0].update(status="passed", sourceSha256=sha(SVG),
            xmlSha256=sha(XML), converter="0.9.2", compose="1.9.1")
        self.record(initial)
        original_path = self.session / "reports/10.json"
        original = json.loads(original_path.read_text())
        original.pop("renderBinding")
        put(original_path, original)  # Historical serialized reports had no top-level renderBinding.
        reviewed_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        checked_at = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        completed = self.observed_attempt("unchanged", checked_at)
        for field in ("sources", "desktop", "platforms", "recheckRequired", "recheckRationale"):
            completed[field] = initial[field]
        completed["assessment"]["variant"]["basis"] = "Reviewer resolved exact variant"
        new_path = self.session / "evidence/10/review-attempt.json"
        put(new_path, completed)
        comparison = self.session / "evidence/10/comparison.png"
        comparison.write_bytes(b"\x89PNG\r\n\x1a\ncomparison fixture")
        review = {"schemaVersion": 1, "id": 10, "baseCommit": self.started["baseCommit"],
                  "iconHash": sha(SVG), "decision": "equivalent-current-variant",
                  "reviewer": "Human fixture reviewer", "statement": "Exact variant compared.",
                  "reviewedAt": reviewed_at,
                  "priorReport": {"path": "reports/10.json", "sha256": sha(original_path.read_bytes())},
                  "priorEvidence": {"path": "evidence/10/validated.json",
                                    "sha256": sha((self.session / "evidence/10/validated.json").read_bytes())},
                  "evidence": {"path": "evidence/10/review-attempt.json", "sha256": sha(new_path.read_bytes())},
                  "comparison": {"path": "evidence/10/comparison.png",
                                 "sha256": sha(comparison.read_bytes())}}
        review_path = self.session / "evidence/10/review.json"
        put(review_path, review)
        self.cli("continue", "--review", str(review_path))
        prepared = self.prepare()
        self.assertEqual(prepared["package"]["records"][0]["after"]["lastVerifiedAt"], checked_at)

    def test_raw_candidate_attempt_is_publishable_without_inventing_render_or_review(self):
        self.candidate_package()  # Records only the search attempt; no --candidate continuation.
        prepared = self.prepare()
        record = prepared["package"]["records"][0]
        state = record["after"]
        proposal = state["proposals"][-1]
        self.assertEqual(record["before"], self.prior)
        self.assertEqual(state["proposals"][0], self.prior["proposals"][0])
        self.assertEqual((state["lastAttemptAt"], state["lastAttemptResult"]), (NOW, "candidate"))
        self.assertEqual((proposal["status"], proposal["candidateIconHash"]), ("pending", sha(RAW)))
        self.assertNotIn("lastVerifiedAt", state)
        self.assertNotIn("verification", proposal)
        self.assertNotIn("reviewPackage", proposal)
        self.assertNotIn("mr", proposal)

    def test_duplicate_pending_candidate_cannot_be_replayed_as_new_search(self):
        self.candidate_package()
        self.forge_prior_proposal("pending")
        self.assertIn("error", self.prepare(ok=False))

    def test_rejected_candidate_cannot_be_replayed_without_human_reconsideration(self):
        self.candidate_package()
        self.forge_prior_proposal("rejected")
        self.assertIn("error", self.prepare(ok=False))

    def test_prepared_candidate_survives_unrelated_implementation_commit(self):
        package_path = self.candidate_package()
        self.cli("continue", "--candidate", str(package_path))
        (self.repo / "implementation.txt").write_text("unrelated change", encoding="utf-8")
        git(self.repo, "add", "implementation.txt")
        git(self.repo, "commit", "-qm", "unrelated implementation")
        record = self.prepare()["package"]["records"][0]
        self.assertEqual(record["after"]["proposals"][-1]["candidateIconHash"], sha(FINAL))

    def test_prepared_candidate_binds_final_svg_without_verifying_current_icon(self):
        package_path = self.candidate_package()
        saved = self.cli("continue", "--candidate", str(package_path))
        prepared = self.prepare()
        record = prepared["package"]["records"][0]
        proposal = record["after"]["proposals"][-1]
        self.assertEqual(record["before"], self.prior)
        self.assertEqual(record["after"]["proposals"][0], self.prior["proposals"][0])
        self.assertEqual(record["after"]["lastAttemptResult"], "candidate")
        self.assertNotIn("lastVerifiedAt", record["after"])
        self.assertEqual(proposal["status"], "pending")
        self.assertEqual(proposal["sourceCandidate"]["iconHash"], sha(RAW))
        self.assertEqual(proposal["candidateIconHash"], sha(FINAL))
        self.assertEqual(proposal["verification"]["sourceSha256"], sha(FINAL))
        self.assertEqual(proposal["reviewPackage"]["sha256"], sha(package_path.read_bytes()))
        self.assertEqual(proposal["humanReview"], "pending")
        self.assertEqual(record["after"], saved["banks"][0]["state"])


    def test_candidate_bindings_cover_consumed_bytes_without_unrelated_evidence(self):
        package_path = self.candidate_package()
        bank = self.cli("continue", "--candidate", str(package_path))["banks"][0]
        unrelated = self.session / "evidence/10/unrelated.txt"
        unrelated.write_text("not read by this result", encoding="utf-8")
        bindings = self.prepare()["package"]["bindings"]
        required = {"session.json", "banks/state/10.json", "reports/10.json", bank["report"],
                    "evidence/10/validated.json", bank["evidence"], "evidence/10/raw.svg",
                    "evidence/10/final.svg", "evidence/10/render/manifest.json",
                    "evidence/10/render/composeResources/drawable/bank_10.xml",
                    "evidence/10/mr-draft.txt", "evidence/10/candidate.diff",
                    f"evidence/10/candidate-{sha(FINAL)}.svg",
                    f"worktree/banks/{self.folder}/info.json",
                    f"worktree/banks/{self.folder}/icon.svg"}
        required |= {f"evidence/10/render/{background}.png" for background in ("light", "dark")}
        required |= {f"evidence/10/compare/{background}-{role}.png"
                     for background in ("light", "dark")
                     for role in ("source", "base", "candidate", "beforeAfter")}
        self.assertTrue({str(self.session / item) for item in required}.issubset(bindings.keys()))
        self.assertNotIn(str(unrelated), bindings)
        self.assertNotIn(str(package_path), bindings)  # Saved content-addressed copy is canonical.
        for absolute, expected in bindings.items():
            self.assertTrue(Path(absolute).is_absolute())
            self.assertEqual(expected, sha(Path(absolute).read_bytes()))

    def test_candidate_source_changes_after_preparation_stop_state_package(self):
        package_path = self.candidate_package()
        saved = self.cli("continue", "--candidate", str(package_path))
        prior_state = (self.session / "banks/state/10.json").read_bytes()
        remote_before = git(self.repo, "ls-remote", str(self.remote), "refs/heads/banks-icon-agent-state")
        (self.session / "evidence/10/final.svg").write_bytes(FINAL + b" changed")
        self.assertIn("error", self.prepare(ok=False))
        self.assertEqual((self.session / "banks/state/10.json").read_bytes(), prior_state)
        self.assertEqual(git(self.repo, "ls-remote", str(self.remote),
                             "refs/heads/banks-icon-agent-state"), remote_before)
        self.assertEqual(saved["banks"][0]["state"]["proposals"][-1]["candidateIconHash"], sha(FINAL))

if __name__ == "__main__":
    unittest.main()
