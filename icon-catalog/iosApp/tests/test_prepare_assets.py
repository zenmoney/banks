"""Black-box tests of the original-SVG iOS asset packaging CLI."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


CLI = Path(__file__).resolve().parents[1] / "prepare_assets.py"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="20"><path d="M0 0h24v20z"/></svg>\n'
XML = b'<vector xmlns:android="http://schemas.android.com/apk/res/android" />\n'


def sha(data):
    return hashlib.sha256(data).hexdigest()


class PrepareAssetsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source_root = self.root / "sources"
        self.source_root.mkdir()
        self.generated = self.root / "generated"
        self.generated.mkdir()
        self.manifest = self.generated / "manifest.json"
        self.output = self.root / "ios" / "Generated"
        self.source = self.source_root / "banks" / "Example_42-ru" / "icon.svg"
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(SVG)
        self.xml = self.generated / "composeResources" / "drawable" / "bank_42.xml"
        self.xml.parent.mkdir(parents=True)
        self.xml.write_bytes(XML)
        self.entry = {
            "id": 42, "title": "Example", "sourcePath": "banks/Example_42-ru/icon.svg",
            "sourceSha256": sha(SVG), "xmlSha256": sha(XML), "sourceWidth": "24",
            "sourceHeight": "20", "viewBox": None, "naturalWidth": 24.0,
            "naturalHeight": 20.0, "resourceName": "bank_42",
            "error": None, "warnings": [], "sourceValidated": True,
        }
        self.write_manifest()

    def write_manifest(self, entries=None):
        self.manifest.write_text(json.dumps({"schemaVersion": 1, "pins": {"converter": "32.4.0", "compose": "1.12.0"}, "entries": entries if entries is not None else [self.entry]}))

    def run_cli(self, *args, **kwargs):
        command = [sys.executable, str(CLI), "--manifest", str(self.manifest),
                   "--source-root", str(self.source_root), "--output", str(self.output), *args]
        return subprocess.run(command, capture_output=True, text=True, **kwargs)

    def packaged(self):
        return json.loads((self.output / "native-manifest.json").read_text())

    def test_original_bytes_and_manifest_provenance_are_packaged(self):
        result = self.run_cli()
        self.assertEqual(0, result.returncode, result.stderr)
        image = self.output / "Assets.xcassets" / "bank_42.imageset"
        self.assertEqual(SVG, (image / "original.svg").read_bytes())
        contents = json.loads((image / "Contents.json").read_text())
        self.assertEqual("original.svg", contents["images"][0]["filename"])
        self.assertEqual("universal", contents["images"][0]["idiom"])
        self.assertTrue(contents["properties"]["preserves-vector-representation"])
        root_contents = json.loads((self.output / "Assets.xcassets/Contents.json").read_text())
        self.assertEqual({"version": 1, "author": "xcode"}, root_contents["info"])
        manifest = self.packaged()
        self.assertEqual(sha(self.manifest.read_bytes()), manifest["manifestSha256"])
        row = manifest["entries"][0]
        self.assertEqual("bank_42", row["nativeAssetName"])
        self.assertEqual(self.entry["sourceSha256"], row["sourceSha256"])
        self.assertEqual(self.entry["sourcePath"], row["sourcePath"])

    def test_safe_converter_failure_is_native_but_unsafe_source_is_not(self):
        converted = {**self.entry, "resourceName": None, "xmlSha256": None,
                     "error": "Svg2Vector: unsupported feature"}
        unsafe = {**self.entry, "id": 8, "sourceValidated": False,
                  "sourcePath": "banks/Unsafe_8-ru/icon.svg", "sourceSha256": None,
                  "xmlSha256": None, "resourceName": None,
                  "error": "Unsafe SVG element <script>"}
        self.write_manifest([converted, unsafe])
        result = self.run_cli()
        self.assertEqual(0, result.returncode, result.stderr)
        rows = self.packaged()["entries"]
        self.assertEqual("bank_42", rows[0]["nativeAssetName"])
        self.assertEqual(SVG, (self.output / "Assets.xcassets/bank_42.imageset/original.svg").read_bytes())
        self.assertIsNone(rows[1]["nativeAssetName"])
        self.assertIsInstance(rows[1]["nativeError"], str)
        self.assertTrue(rows[1]["nativeError"])
        self.assertFalse((self.output / "Assets.xcassets/bank_8.imageset").exists())

    def test_relative_source_escape_is_rejected_without_publishing(self):
        self.entry["sourcePath"] = "../elsewhere.svg"
        (self.root / "elsewhere.svg").write_bytes(SVG)
        self.write_manifest()
        result = self.run_cli()
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(self.output.exists())
        self.assertEqual(SVG, (self.root / "elsewhere.svg").read_bytes())

    def test_hash_failures_preserve_last_complete_package(self):
        self.assertEqual(0, self.run_cli().returncode)
        previous = (self.output / "native-manifest.json").read_bytes()
        original = (self.output / "Assets.xcassets/bank_42.imageset/original.svg").read_bytes()
        self.source.write_bytes(SVG + b"tampered")
        result = self.run_cli()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Source hash mismatch", result.stderr)
        self.assertEqual(previous, (self.output / "native-manifest.json").read_bytes())
        self.assertEqual(original, (self.output / "Assets.xcassets/bank_42.imageset/original.svg").read_bytes())
        self.source.write_bytes(SVG)
        self.xml.write_bytes(XML + b"tampered")
        result = self.run_cli()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("XML hash mismatch", result.stderr)
        self.assertEqual(previous, (self.output / "native-manifest.json").read_bytes())

    def test_absolute_candidate_requires_explicit_override_and_hash_matches(self):
        candidate = self.root / "candidate.svg"
        candidate.write_bytes(SVG)
        self.entry["sourcePath"] = str(candidate)
        self.write_manifest()
        self.assertNotEqual(0, self.run_cli().returncode)
        self.assertFalse(self.output.exists())
        result = self.run_cli("--candidate", f"42={candidate}")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(str(candidate), self.packaged()["entries"][0]["sourcePath"])
        self.assertEqual(SVG, (self.output / "Assets.xcassets/bank_42.imageset/original.svg").read_bytes())

    def test_repackage_removes_stale_assets_and_preserves_untouched_source(self):
        self.assertEqual(0, self.run_cli().returncode)
        old = self.source.read_bytes()
        second = {**self.entry, "id": -7, "resourceName": None, "xmlSha256": None,
                  "error": "Svg2Vector: unsupported feature"}
        self.write_manifest([second])
        result = self.run_cli()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse((self.output / "Assets.xcassets/bank_42.imageset").exists())
        self.assertEqual(old, (self.output / "Assets.xcassets/bank_m7.imageset/original.svg").read_bytes())
        self.assertEqual(old, self.source.read_bytes())
        self.assertEqual("bank_m7", self.packaged()["entries"][0]["nativeAssetName"])

    def test_output_containing_input_is_refused_before_copy(self):
        output = self.source_root / "banks"
        result = self.run_cli("--output", str(output))
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(SVG, self.source.read_bytes())
        self.assertFalse((output / "native-manifest.json").exists())
        result = self.run_cli("--output", str(self.generated))
        self.assertNotEqual(0, result.returncode)
        self.assertTrue(self.manifest.exists())

    def test_missing_xml_and_malformed_manifest_do_not_publish(self):
        self.xml.unlink()
        result = self.run_cli()
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(self.output.exists())
        self.entry.pop("id")
        self.write_manifest()
        result = self.run_cli()
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(self.output.exists())

    def test_unvalidated_source_cannot_be_forced_in_with_candidate(self):
        self.entry["sourceValidated"] = False
        self.entry["error"] = "Unsafe SVG element <script>"
        self.entry["resourceName"] = None
        self.entry["xmlSha256"] = None
        self.write_manifest()
        result = self.run_cli("--candidate", f"42={self.source}")
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(self.output.exists())

    def test_missing_source_root_is_a_cli_error(self):
        result = subprocess.run([sys.executable, str(CLI), "--manifest", str(self.manifest),
                                 "--output", str(self.output)], capture_output=True, text=True)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("--source-root", result.stderr)
        self.assertFalse(self.output.exists())

    def test_output_cannot_replace_unvalidated_source_directory(self):
        self.entry["sourceValidated"] = False
        self.entry["error"] = "Unsafe SVG element <script>"
        self.entry["resourceName"] = None
        self.entry["xmlSha256"] = None
        self.write_manifest()
        (self.source_root / "banks/Assets.xcassets").mkdir()
        (self.source_root / "banks/native-manifest.json").write_text("{}")
        result = self.run_cli("--output", str(self.source_root / "banks"))
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(SVG, self.source.read_bytes())

    def test_non_boolean_safety_marker_never_packages_original(self):
        self.entry["sourceValidated"] = 1
        self.write_manifest()
        result = self.run_cli()
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(self.output.exists())

    def test_late_entry_failure_does_not_publish_partial_assets(self):
        second = {**self.entry, "id": 43, "sourcePath": "banks/Missing_43-ru/icon.svg",
                  "resourceName": None, "xmlSha256": None,
                  "error": "Svg2Vector: unsupported feature"}
        self.write_manifest([self.entry, second])
        result = self.run_cli()
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(self.output.exists())

    def test_source_symlink_escaping_root_is_rejected(self):
        target = self.root / "outside.svg"
        target.write_bytes(SVG)
        self.source.unlink()
        self.source.symlink_to(target)
        result = self.run_cli()
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(self.output.exists())
        self.assertEqual(SVG, target.read_bytes())

    def test_candidate_hash_mismatch_and_unused_override_are_errors(self):
        candidate = self.root / "candidate.svg"
        candidate.write_bytes(SVG + b"different")
        self.entry["sourcePath"] = str(candidate)
        self.write_manifest()
        result = self.run_cli("--candidate", f"42={candidate}")
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(self.output.exists())
        result = self.run_cli("--candidate", f"99={candidate}")
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(self.output.exists())

    def test_unrelated_output_file_is_never_deleted_during_repackage(self):
        self.assertEqual(0, self.run_cli().returncode)
        unrelated = self.output / "review-notes.txt"
        unrelated.write_text("keep this")
        previous = (self.output / "native-manifest.json").read_bytes()
        result = self.run_cli()
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("keep this", unrelated.read_text())
        self.assertEqual(previous, (self.output / "native-manifest.json").read_bytes())

    def test_unrelated_nested_asset_is_not_deleted(self):
        self.assertEqual(0, self.run_cli().returncode)
        extra = self.output / "Assets.xcassets/user.imageset"
        extra.mkdir()
        (extra / "user.svg").write_bytes(SVG)
        previous = (self.output / "native-manifest.json").read_bytes()
        result = self.run_cli()
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(SVG, (extra / "user.svg").read_bytes())
        self.assertEqual(previous, (self.output / "native-manifest.json").read_bytes())

    def test_malformed_previous_package_marker_is_not_trusted(self):
        self.assertEqual(0, self.run_cli().returncode)
        prior = self.output / "native-manifest.json"
        prior.write_text("{}")
        result = self.run_cli()
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("{}", prior.read_text())
        self.assertEqual(SVG, (self.output / "Assets.xcassets/bank_42.imageset/original.svg").read_bytes())

    def test_require_native_accepts_safe_converter_error_only(self):
        converted = {**self.entry, "resourceName": None, "xmlSha256": None,
                     "error": "Svg2Vector: unsupported feature"}
        self.write_manifest([converted])
        result = self.run_cli("--require-native", "42")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("bank_42", self.packaged()["entries"][0]["nativeAssetName"])
        self.assertEqual(SVG, (self.output / "Assets.xcassets/bank_42.imageset/original.svg").read_bytes())

    def test_require_native_rejects_unvalidated_absent_and_duplicate_selected_ids(self):
        self.entry["sourceValidated"] = False
        self.entry["resourceName"] = None
        self.entry["xmlSha256"] = None
        self.entry["error"] = "Unsafe SVG element <script>"
        self.write_manifest()
        self.assertNotEqual(0, self.run_cli("--require-native", "42").returncode)
        self.assertFalse(self.output.exists())
        self.write_manifest([{**self.entry, "sourceValidated": True, "resourceName": "bank_42",
                              "xmlSha256": sha(XML), "error": None}])
        self.assertNotEqual(0, self.run_cli("--require-native", "99").returncode)
        self.assertFalse(self.output.exists())
        self.write_manifest([self.entry, dict(self.entry)])
        self.assertNotEqual(0, self.run_cli("--require-native", "42").returncode)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
