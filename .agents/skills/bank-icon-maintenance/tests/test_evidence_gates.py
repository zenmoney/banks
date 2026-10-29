"""Evidence acceptance through the public start/continue session CLI."""
from session_fixture import make_session
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "session.py"
POLICY = {"schemaVersion": 1, "recheckAfterDays": 90,
          "retryUnconfirmedAfterDays": 30, "retryErrorAfterDays": 7,
          "selectionLimit": 5}
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 54 54"><circle cx="27" cy="27" r="18"/></svg>'
XML = b'<vector xmlns:android="http://schemas.android.com/apk/res/android" android:width="54dp" android:height="54dp"/>'
NOW = "2026-09-25T12:00:00Z"


def digest(value):
    return hashlib.sha256(value).hexdigest()


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class EvidenceCLI(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        put(self.repo / "bank-icon-maintenance.json", POLICY)
        self.folder = "Alpha_10-ru"
        bank = self.repo / "banks" / self.folder
        put(bank / "info.json", {"id": 10, "title": "Alpha", "countryCode": "RU"})
        (bank / "icon.svg").write_bytes(SVG)
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "fixture")
        self.session = self.root / "session"
        self.start = make_session(self , bank_id=10, now=NOW)
        self.evidence = self.session / "evidence" / "10" / "attempt.json"

    def cli(self, command, *args, ok=True):
        result = subprocess.run([sys.executable, str(SCRIPT), command, "--session",
                                 str(self.session), *args], capture_output=True, text=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def record(self, outcome="unchanged"):
        source = {"kind": "official", "url": "https://alpha.example.invalid/brand",
                  "assetUrl": "https://alpha.example.invalid/brand/logo.svg", "checkedAt": NOW,
                  "observation": "Current bank sign appears with same colors and geometry.",
                  "independenceGroup": "official-alpha"}
        attempts = [{"kind": kind, "url": url, "assetUrl": None, "checkedAt": NOW,
                     "observation": "Searched for newer relevant icon; no matching result.",
                     "independenceGroup": kind}
                    for kind, url in (("zenplugins-issue", "https://github.com/ZenPlugins/Zen/issues?q=Alpha"),
                                      ("zenplugins-pr", "https://github.com/ZenPlugins/Zen/pulls?q=Alpha"))]
        return {"schemaVersion": 1, "id": 10, "baseCommit": self.start["baseCommit"],
                "iconHash": digest(SVG), "checkedAt": NOW, "outcome": outcome,
                "reason": "The current bank sign matches the observed asset and current branding.",
                "nextAction": "Record current version and wait for its next check.",
                "sources": [source, *attempts],
                "assessment": {key: {"finding": "confirmed", "basis": "Compared bank identity, date and exact sign variant against the official asset."}
                               for key in ("identity", "currency", "variant")}
                | {"independenceRationale": "Official first-party asset; search rows are not treated as independent support.",
                   "uncertainties": []},
                "desktop": None, "platforms": [
                    {"target": target, "status": "not-tested", "observation": "Unavailable in this controlled schema test."}
                    for target in ("android-compose", "android-native", "ios-compose", "ios-native")],
                "recheckRequired": False,
                "recheckRationale": "No exceptional SVG features or changes to converter/renderer pins in this controlled example."}

    def rendered(self, record):
        generated = self.session / "evidence" / "10" / "generated"
        resource = generated / "composeResources" / "drawable" / "bank_10.xml"
        resource.parent.mkdir(parents=True, exist_ok=True)
        resource.write_bytes(XML)
        put(generated / "manifest.json", {"schemaVersion": 1,
            "banksRoot": str(self.session / "worktree" / "banks"),
            "sourceRoot": str(self.session / "worktree"),
            "pins": {"converter": "0.9.2", "compose": "1.9.1"},
            "entries": [{"id": 10, "title": "Alpha", "sourcePath": f"banks/{self.folder}/icon.svg",
                         "sourceSha256": digest(SVG), "sourceValidated": True,
                         "xmlSha256": digest(XML), "resourceName": "bank_10", "error": None}]})
        screens = {}
        for background in ("light", "dark"):
            image = b"\x89PNG\r\n\x1a\n" + background.encode() + b" simulated observation"
            path = self.session / "evidence" / "10" / f"{background}.png"
            path.write_bytes(image)
            screens[background] = {"path": str(path.relative_to(self.session)), "sha256": digest(image)}
        record["desktop"] = {"renderer": "compose-desktop-vector-xml-painterResource",
                             "manifest": str((generated / "manifest.json").relative_to(self.session)),
                             "observedAt": NOW,
                             "visualComparison": "On both backgrounds the XML matches source SVG shape and color; no detail lost.",
                             **screens}
        return record

    def submit(self, record, ok=True):
        put(self.evidence, record)
        return self.cli("continue", "--evidence", str(self.evidence), ok=ok)

    def test_exact_current_version_with_observed_desktop_updates_verification(self):
        result = self.submit(self.rendered(self.record()))
        bank = result["banks"][0]
        self.assertEqual(bank["state"]["lastVerifiedAt"], NOW)
        self.assertEqual(bank["state"]["verifiedIconHash"], digest(SVG))
        self.assertEqual((self.session / "worktree" / "banks" / self.folder / "icon.svg").read_bytes(), SVG)
        self.assertTrue(self.evidence.exists())

    def test_valid_utc_milliseconds_in_source_desktop_and_attempt_are_preserved(self):
        observed_at = "2026-09-25T12:00:00.006Z"
        record = self.rendered(self.record())
        record["checkedAt"] = observed_at
        for source in record["sources"]:
            source["checkedAt"] = observed_at
        record["desktop"]["observedAt"] = observed_at
        result = self.submit(record)
        self.assertEqual(result["banks"][0]["state"]["lastVerifiedAt"], observed_at)
        self.assertEqual(json.loads((self.session / result["banks"][0]["evidence"]).read_text())["desktop"]["observedAt"], observed_at)

    def test_invalid_or_non_utc_attempt_timestamp_still_rejected(self):
        for value in ("2026-09-25T12:00:00", "2026-02-30T12:00:00Z",
                      "2026-09-25T12:00:00+02:00Z"):
            with self.subTest(value=value):
                record = self.record("error")
                record["checkedAt"] = value
                record["sources"] = []
                record["platforms"] = []
                self.assertIn("checkedAt", self.submit(record, ok=False)["error"])

    def test_no_verified_shortcut_or_missing_screenshot(self):
        record = self.record()
        record["verified"] = True
        self.assertIn("unsupported attempt fields", self.submit(record, ok=False)["error"])
        record.pop("verified")
        self.assertIn("Desktop", self.submit(record, ok=False)["error"])
        record = self.rendered(record)
        (self.session / record["desktop"]["dark"]["path"]).unlink()
        self.assertIn("desktop.dark", self.submit(record, ok=False)["error"])
        self.assertNotIn("lastVerifiedAt", json.loads((self.session / "session.json").read_text())["banks"][0]["state"] or {})

    def test_manifest_must_bind_exact_svg_and_generated_xml(self):
        before = (self.session / "session.json").read_bytes()
        record = self.rendered(self.record())
        manifest = self.session / record["desktop"]["manifest"]
        content = json.loads(manifest.read_text())
        content["entries"][0]["sourcePath"] = "banks/Other_10-ru/icon.svg"
        put(manifest, content)
        self.submit(record, ok=False)
        content["entries"][0]["sourcePath"] = f"banks/{self.folder}/icon.svg"
        put(manifest, content)
        xml = manifest.parent / "composeResources" / "drawable" / "bank_10.xml"
        xml.write_bytes(XML + b" changed")
        self.submit(record, ok=False)
        xml.write_bytes(XML)
        content["entries"][0]["resourceName"] = "bank_99"
        put(manifest, content)
        (xml.parent / "bank_99.xml").write_bytes(XML)
        self.submit(record, ok=False)
        self.assertEqual((self.session / "session.json").read_bytes(), before)
        self.assertFalse((self.session / "reports/10.json").exists())

    def test_unrelated_catalog_conversion_failure_does_not_block_selected_icon(self):
        record = self.rendered(self.record())
        manifest = self.session / record["desktop"]["manifest"]
        content = json.loads(manifest.read_text())
        content["entries"].append({"id": 20, "title": "Other", "sourcePath": "banks/Other_20-ru/icon.svg",
                                   "sourceSha256": None, "xmlSha256": None, "error": "unsupported gradient"})
        put(manifest, content)
        result = self.submit(record)
        self.assertEqual(result["banks"][0]["state"]["verifiedIconHash"], digest(SVG))

    def test_ambiguous_or_recheck_required_cannot_confirm_current_variant(self):
        record = self.rendered(self.record())
        record["assessment"]["variant"] = {"finding": "conflicting", "basis": "Source shows another mark."}
        self.assertIn("ambiguous", self.submit(record, ok=False)["error"])
        record["assessment"]["variant"]["finding"] = "confirmed"
        record["recheckRequired"] = True
        self.assertIn("recheck", self.submit(record, ok=False)["error"])
        record["recheckRequired"] = False
        record["platforms"][0].update({"status": "passed", "observation": "Old converter version was observed."})
        self.assertIn("not bound", self.submit(record, ok=False)["error"])

    def test_ios_unavailable_does_not_hide_bound_android_result(self):
        record = self.rendered(self.record())
        record["platforms"][0].update({"status": "passed", "observation": "Observed Android Compose XML on device.",
                                       "sourceSha256": digest(SVG), "xmlSha256": digest(XML),
                                       "converter": "0.9.2", "compose": "1.9.1"})
        result = self.submit(record)
        stored = json.loads((self.session / result["banks"][0]["evidence"]).read_text())
        self.assertEqual(stored["platforms"][0]["status"], "passed")
        self.assertEqual(stored["platforms"][2]["status"], "not-tested")
        self.assertEqual(result["banks"][0]["state"]["verifiedIconHash"], digest(SVG))

    def test_positive_outcome_requires_actual_asset_and_search_attempts(self):
        record = self.rendered(self.record())
        record["sources"] = []
        self.assertIn("source observations", self.submit(record, ok=False)["error"])
        record["sources"] = self.record()["sources"]
        record["sources"][0]["assetUrl"] = None
        self.assertIn("source asset", self.submit(record, ok=False)["error"])

    def test_unconfirmed_requires_recorded_completed_search_but_not_positive_asset(self):
        record = self.record("unconfirmed")
        record["assessment"]["currency"] = {"finding": "uncertain", "basis": "No current matching source was found."}
        record["assessment"]["uncertainties"] = ["Search did not confirm current variant."]
        record["reason"] = "Search found no usable current variant."
        record["nextAction"] = "Retry after more evidence becomes available."
        record["sources"] = []
        self.assertIn("source observations", self.submit(record, ok=False)["error"])
        record["sources"] = self.record()["sources"][1:]
        result = self.submit(record)
        self.assertEqual(result["banks"][0]["state"]["lastAttemptResult"], "unconfirmed")
        self.assertNotIn("lastVerifiedAt", result["banks"][0]["state"])

    def test_passed_platform_without_desktop_manifest_is_not_recorded_as_success(self):
        record = self.record("error")
        record["assessment"]["variant"] = {"finding": "uncertain", "basis": "Desktop render was unavailable."}
        record["sources"] = []
        record["platforms"] = [{"target": "android-compose", "status": "passed",
                                "observation": "An Android run was claimed without a bound XML manifest."}]
        self.assertIn("Desktop manifest", self.submit(record, ok=False)["error"])
        record = self.rendered(record)
        record["platforms"][0].update({"sourceSha256": digest(SVG), "xmlSha256": digest(XML),
                                       "converter": "0.9.2", "compose": "1.9.1"})
        result = self.submit(record)
        self.assertEqual(result["banks"][0]["state"]["lastAttemptResult"], "error")
        self.assertNotIn("lastVerifiedAt", result["banks"][0]["state"])

    def test_negative_outcome_records_attempt_without_fabricated_search(self):
        record = self.record("error")
        record["sources"] = []
        record["platforms"] = []
        record["assessment"]["currency"] = {"finding": "uncertain", "basis": "Source fetch failed before search."}
        record["assessment"]["uncertainties"] = ["Source unavailable; no search completed."]
        record["reason"] = "Source retrieval failed before any search."
        record["nextAction"] = "Retry source retrieval."
        result = self.submit(record)
        state = result["banks"][0]["state"]
        self.assertEqual(state["lastAttemptResult"], "error")
        self.assertNotIn("lastVerifiedAt", state)
        stored = json.loads((self.session / result["banks"][0]["evidence"]).read_text())
        self.assertEqual(stored["sources"], [])

    def test_observed_desktop_error_preserves_previous_version_verification(self):
        original_branch = git(self.repo, "branch", "--show-current")
        git(self.repo, "checkout", "-q", "-b", "banks-icon-agent-state")
        put(self.repo / "banks" / "state" / "10.json",
            {"schemaVersion": 1, "lastVerifiedAt": "2026-01-01T00:00:00Z", "verifiedIconHash": digest(SVG)})
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "prior verification")
        git(self.repo, "checkout", "-q", original_branch)
        self.session = self.root / "with-history"
        self.start = make_session(self , bank_id=10, now=NOW)
        self.evidence = self.session / "evidence" / "10" / "attempt.json"
        record = self.rendered(self.record("error"))
        record["assessment"]["variant"] = {"finding": "conflicting", "basis": "Observed XML loses a detail visible in the source."}
        record["assessment"]["uncertainties"] = ["Render differs despite successful conversion."]
        record["reason"] = "Observed Desktop XML differs visibly from source SVG."
        record["nextAction"] = "Investigate conversion before confirming this variant."
        result = self.submit(record)
        state = result["banks"][0]["state"]
        self.assertEqual(state["lastAttemptResult"], "error")
        self.assertEqual(state["lastVerifiedAt"], "2026-01-01T00:00:00Z")
        self.assertEqual(state["verifiedIconHash"], digest(SVG))
        stored = json.loads((self.session / result["banks"][0]["evidence"]).read_text())
        self.assertIn("visibly", stored["reason"])
        self.assertEqual(stored["desktop"]["renderer"], "compose-desktop-vector-xml-painterResource")

    def test_candidate_requires_fetched_file_and_observed_asset(self):
        record = self.record("candidate")
        candidate = self.session / "evidence" / "10" / "fetched.svg"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(SVG + b" candidate")
        record["candidate"] = {"path": str(candidate.relative_to(self.session)),
                               "iconHash": digest(candidate.read_bytes()),
                               "sourceUrl": "https://alpha.example.invalid/brand",
                               "sourceAssetUrl": "https://alpha.example.invalid/brand/logo.svg",
                               "rationale": "A newer matching bank sign was observed."}
        candidate.unlink()
        self.assertIn("candidate.path", self.submit(record, ok=False)["error"])
        candidate.write_bytes(SVG + b" candidate")
        record["candidate"]["sourceAssetUrl"] = "https://alpha.example.invalid/other.svg"
        self.assertIn("observed in sources", self.submit(record, ok=False)["error"])
        record["candidate"]["sourceAssetUrl"] = record["sources"][0]["assetUrl"]
        candidate.write_bytes(SVG)
        record["candidate"]["iconHash"] = digest(SVG)
        self.assertIn("same as current", self.submit(record, ok=False)["error"])
        candidate.write_bytes(SVG + b" candidate")
        record["candidate"]["iconHash"] = digest(candidate.read_bytes())
        result = self.submit(record)
        self.assertEqual(result["banks"][0]["state"]["lastAttemptResult"], "candidate")
        self.assertNotIn("lastVerifiedAt", result["banks"][0]["state"])

    def test_nullable_baseline_helpers_reject_entries_and_failed_tree_lookup(self):
        sys.path.insert(0, str(SCRIPT.parent))
        from evidence import baseline_bytes, baseline_blob
        missing = self.root / "missing.svg"
        self.assertIsNone(baseline_bytes(missing, None))
        missing.symlink_to(self.root / "not-created.svg")
        with self.assertRaisesRegex(ValueError, "symlink"):
            baseline_bytes(missing, None)
        missing.unlink()
        missing.mkdir()
        with self.assertRaisesRegex(ValueError, "appeared"):
            baseline_bytes(missing, None)
        missing.rmdir()
        missing.write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "appeared"):
            baseline_bytes(missing, None)
        with self.assertRaisesRegex(ValueError, "cannot establish"):
            baseline_blob(self.repo, "invalid-commit", "banks/Alpha_10-ru/icon.svg", None)
        with self.assertRaisesRegex(ValueError, "appeared"):
            baseline_blob(self.repo, self.start["baseCommit"], "banks/Alpha_10-ru/icon.svg", None)


    def test_evidence_symlink_escape_is_rejected(self):
        record = self.rendered(self.record())
        screenshot = self.session / record["desktop"]["light"]["path"]
        outside = self.root / "outside.png"
        outside.write_bytes(screenshot.read_bytes())
        screenshot.unlink()
        screenshot.symlink_to(outside)
        self.assertIn("symlink", self.submit(record, ok=False)["error"])

if __name__ == "__main__":
    unittest.main()
