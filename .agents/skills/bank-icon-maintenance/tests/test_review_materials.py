"""Reviewer materials observed through asset-prepare and a disposable Git repository.

Controlled PNG/XML fixture bytes prove packaging, not a real graphical renderer check.
"""
import json
import subprocess
import unittest
from unittest.mock import patch

import test_candidates as candidates
import test_asset_publication as asset_fixture

COMMONS_URL = "https://commons.wikimedia.org/wiki/File:New_Sovcombank_logo_(updated_version).svg"


class ReviewMaterialsCLI(unittest.TestCase):
    def setUp(self):
        if self._testMethodName == "test_commons_file_page_survives_review_publication":
            source_patch = patch.object(candidates, "SOURCE", COMMONS_URL)
            source_patch.start()
            self.addCleanup(source_patch.stop)
        if self._testMethodName.startswith("test_public_text_"):
            self.public_observation = "Homepage displays the current compact bank mark."
            original_cli = candidates.CandidateCLI.cli

            def ingest_observation(candidate, command, *args, **kwargs):
                if command == "continue" and "--evidence" in args:
                    attempt_path = candidate.evidence / "attempt.json"
                    attempt = json.loads(attempt_path.read_text())
                    attempt["sources"][0]["observation"] = 'Homepage contains <img src="/wcms-resources/LOGO_RSHB.svg">.'
                    candidates.put_json(attempt_path, attempt)
                return original_cli(candidate, command, *args, **kwargs)

            source_patch = patch.object(candidates.CandidateCLI, "cli", ingest_observation)
            source_patch.start()
            self.addCleanup(source_patch.stop)
        fixture = asset_fixture.ApprovedAssetCLI("test_approved_candidate_applies_only_exact_icon_on_target_base")
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.fixture = fixture
        if self._testMethodName.startswith("test_public_text_"):
            self.public_text_path = fixture.root / "public-text.json"
            candidates.put_json(self.public_text_path, {"sources.0.observation": self.public_observation})

    def test_committed_material_bytes_and_review_body_match_prepared_evidence(self):
        fixture = self.fixture
        prepared = fixture.prepare()
        package = prepared["package"]
        commit = package["materials"]["commit"]
        git_dir = fixture.session / "asset-publication/objects.git"
        expected = {"base.svg": candidates.BASE, "source.svg": candidates.RAW,
                    "final.svg": candidates.FINAL, "final.xml": candidates.XML,
                    "comparison-light.png": candidates.PNG + b"lightbeforeAfter",
                    "comparison-dark.png": candidates.PNG + b"darkbeforeAfter"}
        for name, content in expected.items():
            actual = subprocess.run(["git", "--git-dir", str(git_dir), "show",
                                     f"{commit}:review-materials/10/{name}"],
                                    check=True, capture_output=True).stdout
            self.assertEqual(actual, content, name)
        body = package["body"]
        self.assertIn(f"/blob/{commit}/review-materials/10/comparison-light.png?raw=true", body)
        self.assertIn(f"/blob/{commit}/review-materials/10/comparison-dark.png?raw=true", body)
        self.assertIn(f"/blob/{commit}/review-materials/10/source.svg", body)
        self.assertIn(f"/blob/{commit}/review-materials/10/candidate.diff", body)
        self.assertIn(candidates.digest(candidates.BASE), body)
        self.assertIn(candidates.digest(candidates.FINAL), body)
        self.assertIn(candidates.digest(candidates.XML), body)
        self.assertIn(candidates.SOURCE, body)
        self.assertIn(candidates.ASSET, body)
        self.assertIn("not-tested", body)
        self.assertIn("Compose Desktop", body)
        self.assertNotIn("raw.githubusercontent.com", body)
        self.assertNotIn(str(fixture.session), body)
        details = package["materials"]["details"]
        self.assertEqual(details["metadata"], {"id": 10, "title": "Alpha", "countryCode": "RU"})
        self.assertNotIn("buildEvidence", json.dumps(details))
        self.assertNotIn("uiEvidence", json.dumps(details))
        self.assertNotIn("manifest", json.dumps(details))
        self.assertNotIn("evidence/10/", json.dumps(details))

    def test_commons_file_page_survives_review_publication(self):
        package = self.fixture.prepare()["package"]
        self.assertEqual(package["materials"]["details"]["sourceUrl"], COMMONS_URL)
        self.assertEqual(package["materials"]["details"]["sources"][0]["url"], COMMONS_URL)
        self.assertIn("https://commons.wikimedia.org/wiki/File:New_Sovcombank_logo_%28updated_version%29.svg", package["body"])

    def test_source_symlink_cannot_escape_review_allowlist(self):
        fixture = self.fixture
        source = fixture.fixture.evidence / "raw.svg"
        outside = fixture.root / "outside.svg"
        outside.write_bytes(candidates.RAW)
        source.unlink()
        source.symlink_to(outside)
        result = fixture.cli("asset-prepare", "--id", "10", "--target", "fixture/catalog",
                             "--base", "main", "--head", "icons/10-reviewed", ok=False)
        self.assertIn("symlink", result["error"])
        self.assertIsNone(fixture.remote_ref("icons/10-reviewed"))
        self.assertIsNone(fixture.remote_ref("icons/10-reviewed-review"))
        self.assertFalse((fixture.session / "asset-publication/objects.git").exists())

    def test_public_text_file_requires_a_json_object(self):
        for value in (None, [], "replacement", 1):
            with self.subTest(value=value):
                candidates.put_json(self.public_text_path, value)
                rejected = self.prepare_public_text(ok=False)
                self.assertIn("JSON object", rejected["error"])
        self.assertIsNone(self.fixture.remote_ref("icons/10-reviewed"))

    def prepare_public_text(self, *, ok=True):
        return self.fixture.cli("asset-prepare", "--id", "10", "--target", "fixture/catalog",
                                "--base", "main", "--head", "icons/10-reviewed",
                                "--public-text", str(self.public_text_path), ok=ok)

    def test_public_text_is_frozen_across_show_retry_and_publish(self):
        fixture = self.fixture
        immutable = {path: path.read_bytes() for directory in ("evidence", "reports")
                     for path in (fixture.session / directory).rglob("*") if path.is_file()}
        rejected = fixture.cli("asset-prepare", "--id", "10", "--target", "fixture/catalog",
                               "--base", "main", "--head", "icons/10-reviewed", ok=False)
        self.assertIn("sources.0.observation", rejected["error"])
        prepared = self.prepare_public_text()
        package = prepared["package"]
        fixture.package_hash = prepared["packageHash"]
        self.assertIn(self.public_observation, package["body"])
        self.assertNotIn("/wcms-resources/", package["body"])
        self.assertEqual(package["materials"]["details"]["sources"][0]["url"], candidates.SOURCE)
        candidates.put_json(self.public_text_path, {"sources.0.observation": "Different observation."})
        rejected = self.prepare_public_text(ok=False)
        self.assertIn("public", rejected["error"].lower())
        self.assertEqual(fixture.cli("asset-show", "--package", fixture.package_hash)["package"], package)
        self.public_text_path.unlink()
        self.assertEqual(fixture.prepare()["package"], package)
        self.assertEqual(fixture.cli("asset-show", "--package", fixture.package_hash)["package"], package)
        published = fixture.cli("asset-publish", "--package", fixture.package_hash,
                                "--authorization", str(fixture.authorization(package)))
        self.assertEqual(published["status"], "mr-confirmed")
        git_dir = fixture.session / "asset-publication/objects.git"
        commit = package["materials"]["commit"]
        expected = {"base.svg": candidates.BASE, "source.svg": candidates.RAW,
                    "final.svg": candidates.FINAL, "final.xml": candidates.XML,
                    "candidate.diff": (fixture.fixture.evidence / "candidate.diff").read_bytes(),
                    "comparison-light.png": candidates.PNG + b"lightbeforeAfter",
                    "comparison-dark.png": candidates.PNG + b"darkbeforeAfter"}
        for name, content in expected.items():
            actual = subprocess.run(["git", "--git-dir", str(git_dir), "show",
                                     f"{commit}:review-materials/10/{name}"],
                                    check=True, capture_output=True).stdout
            self.assertEqual(actual, content, name)
        self.assertEqual({path: path.read_bytes() for path in immutable}, immutable)

    def test_public_text_package_and_evidence_tampering_block_show_and_publish(self):
        fixture = self.fixture
        prepared = self.prepare_public_text()
        fixture.package_hash = prepared["packageHash"]
        package_paths = list((fixture.session / "asset-publication").rglob(f"{fixture.package_hash}.json"))
        self.assertEqual(len(package_paths), 1)
        package_path = package_paths[0]
        original = package_path.read_bytes()
        frozen = json.loads(original)
        frozen["publicText"]["sources.0.observation"] = "Different observation."
        candidates.put_json(package_path, frozen)
        for command in ("asset-show", "asset-publish"):
            with self.subTest(command=command, tamper="package"):
                fixture.cli(command, "--package", fixture.package_hash, ok=False)
        package_path.write_bytes(original)
        (fixture.fixture.evidence / "raw.svg").write_bytes(candidates.RAW + b"changed")
        for command in ("asset-show", "asset-publish"):
            with self.subTest(command=command, tamper="evidence"):
                args = ("--authorization", str(fixture.authorization(prepared["package"]))) if command == "asset-publish" else ()
                fixture.cli(command, "--package", fixture.package_hash, *args, ok=False)
        self.assertIsNone(fixture.remote_ref("icons/10-reviewed"))
        self.assertIsNone(fixture.remote_ref("icons/10-reviewed-review"))
        self.assertEqual(json.loads(fixture.gh_state.read_text())["createCalls"], 0)

class FirstIconMaterialsCLI(unittest.TestCase):
    def setUp(self):
        fixture = asset_fixture.FirstIconPublicationCLI(
            "test_exact_one_path_addition_preserves_metadata_and_source_checkout")
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.fixture = fixture

    def test_six_committed_real_materials_preserve_exact_review_integrity(self):
        fixture = self.fixture
        prepared = fixture.prepare()
        package = prepared["package"]
        objects = fixture.session / "asset-publication/objects.git"
        commit = package["materials"]["commit"]
        names = candidates.git(objects, "ls-tree", "-r", "--name-only", commit).splitlines()
        expected = {"source.svg": candidates.RAW, "final.svg": candidates.FINAL,
                    "final.xml": candidates.XML,
                    "comparison-light.png": candidates.PNG + b"light",
                    "comparison-dark.png": candidates.PNG + b"dark"}
        expected["candidate.diff"] = (fixture.fixture.evidence / "candidate.diff").read_bytes()
        self.assertEqual(names, ["review-materials/10/" + name for name in sorted(expected)])
        for name, content in expected.items():
            actual = subprocess.run(["git", "--git-dir", str(objects), "show",
                                     f"{commit}:review-materials/10/{name}"],
                                    check=True, capture_output=True).stdout
            self.assertEqual(actual, content, name)
        self.assertIsNone(package["materials"]["details"]["baseSha256"])
        self.assertNotIn("base.svg", package["body"])
        self.assertNotIn("Before and after", package["body"])
        self.assertEqual(fixture.cli("asset-show", "--package", prepared["packageHash"])["package"], package)
        frame = fixture.fixture.evidence / "render/light.png"
        frame.write_bytes(candidates.PNG + b"substituted final frame")
        fixture.cli("asset-show", "--package", prepared["packageHash"], ok=False)
        self.assertIsNone(fixture.remote_ref("icons/10-reviewed"))

if __name__ == "__main__":
    unittest.main()
