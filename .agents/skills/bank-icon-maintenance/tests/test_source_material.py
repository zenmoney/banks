"""Actual container bytes and receipt lineage, not bank identity or renderer certification."""
import bz2
import gzip
import io
import json
import lzma
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
import warnings
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import source_material as material
import source_evidence as source

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12"><circle cx="6" cy="6" r="4"/></svg>'
MEMBER = "White background/Logo.svg"


def zipped(items):
    stream = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, content in items:
                archive.writestr(name, content)
    return stream.getvalue()


def tarred(items):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, content in items:
            entry = name if isinstance(name, tarfile.TarInfo) else tarfile.TarInfo(name)
            entry.size = len(content)
            archive.addfile(entry, io.BytesIO(content))
    return stream.getvalue()


class MaterialFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="material-test-")
        self.addCleanup(temporary.cleanup)
        self.session = Path(temporary.name).resolve() / "session"
        self.session.mkdir()
        self.data = {"baseCommit": "a" * 40, "worktree": str(self.session / "worktree")}
        self.bank = {"id": 10}
        self.counter = 0

    def fetched(self, content):
        self.counter += 1
        root = self.session / f"evidence/10/fetch-{self.counter}"
        attempt = root / "attempt-1"
        attempt.mkdir(parents=True)
        body = attempt / "body.bin"
        body.write_bytes(content)
        started = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat().replace("+00:00", "Z")
        finished = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        url = "https://example.invalid/resource"
        command = ["/usr/bin/curl", "--disable", "--silent", "--show-error", "--proto", "=https",
            "--connect-timeout", "10", "--max-time", "30", "--max-filesize", str(source.MAX_BYTES),
            "--dump-header", str(attempt / "headers.txt"), "--output", str(body),
            "--write-out", "%{http_code}\n%{content_type}\n", "--url", url]
        link = {**source.link(self.session, body), "bytes": len(content)}
        value = {"schemaVersion": 1, "id": 10, "baseCommit": self.data["baseCommit"], "status": "fetched",
                 "requestedUrl": url, "finalUrl": url, "startedAt": started, "finishedAt": finished,
                 "caRetry": "not-needed", "body": link,
                 "attempts": [{"directory": "attempt-1", "url": url, "startedAt": started,
                    "finishedAt": finished, "trust": "default", "command": command, "exitCode": 0,
                    "httpStatus": 200, "body": {**link, "path": "attempt-1/body.bin"}}]}
        path = root / "receipt.json"
        source.write_json(path, value)
        receipt = source.link(self.session, path)
        raw = {"path": link["path"], "iconHash": link["sha256"], "retrieval": receipt,
               "sourceUrl": "https://example.invalid/brand", "sourceAssetUrl": url,
               "rationale": "Controlled source material"}
        return receipt, raw

    def prepared(self, content=None, members=None):
        receipt, raw = self.fetched(zipped([(MEMBER, SVG)]) if content is None else content)
        result = material.prepare(self.session, receipt, 10, self.data["baseCommit"],
                                  [MEMBER] if members is None else members)
        return result, raw

    def validate(self, binding, raw):
        return material.validate(self.session, binding, raw["retrieval"], 10,
                                 self.data["baseCommit"], datetime.now(timezone.utc))

    def change_manifest(self, binding, mutate):
        path = self.session / binding["path"]
        value = source.read_json(path)
        mutate(value)
        path.write_text(json.dumps(value))  # Deliberate tampering in disposable fixture only.
        return source.link(self.session, path)


class ContainerBytes(unittest.TestCase):
    def test_same_material_across_containers_and_compression(self):
        tar = tarred([(MEMBER, SVG)])
        cases = [(SVG, []), (zipped([(MEMBER, SVG)]), [MEMBER]), (tar, [MEMBER]),
                 (gzip.compress(tar), [MEMBER]), (bz2.compress(tar), [MEMBER]),
                 (lzma.compress(tar), [MEMBER]), (gzip.compress(SVG), []),
                 (bz2.compress(SVG), []), (lzma.compress(SVG), [])]
        for body, selection in cases:
            with self.subTest(signature=body[:8]):
                info, result = material.decode(body, selection)
                self.assertEqual(result, SVG)
                self.assertEqual(info["sha256"], source.sha(SVG))
                self.assertEqual(info["format"], "svg")

    def test_selection_is_explicit_even_for_one_file(self):
        info, content = material.decode(zipped([(MEMBER, SVG)]), [])
        self.assertIsNone(content)
        self.assertEqual(info["kind"], "selection-required")
        self.assertEqual(info["members"][0]["name"], MEMBER)

    def test_nested_selection_uses_the_same_interface(self):
        body = zipped([("inner.data", gzip.compress(tarred([(MEMBER, SVG)])))])
        info, result = material.decode(body, ["inner.data", MEMBER])
        self.assertEqual(result, SVG)
        self.assertEqual([x["format"] for x in info["steps"]], ["zip", "gzip", "tar"])

    def test_unknown_member_and_surplus_selection_fail(self):
        for body, members in [(zipped([(MEMBER, SVG)]), ["missing"]), (SVG, [MEMBER])]:
            with self.subTest(members=members), self.assertRaises(ValueError):
                material.decode(body, members)

    def test_unsafe_member_names_are_rejected(self):
        for name in ("../escape.svg", "/absolute.svg", "C:/disk.svg", "a\\b.svg", "a/../b.svg", "a\nb.svg"):
            for pack in (zipped, tarred):
                with self.subTest(name=name, pack=pack.__name__), self.assertRaises(ValueError):
                    material.decode(pack([(name, SVG)]), [])

    def test_duplicate_and_normalized_aliases_are_rejected(self):
        for names in ((MEMBER, MEMBER), (MEMBER, "./" + MEMBER)):
            for pack in (zipped, tarred):
                with self.subTest(names=names, pack=pack.__name__), self.assertRaisesRegex(ValueError, "ambiguous"):
                    material.decode(pack([(name, SVG) for name in names]), [])

    def test_tar_links_devices_and_sparse_entries_are_rejected(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.FIFOTYPE, tarfile.GNUTYPE_SPARSE):
            entry = tarfile.TarInfo("link")
            entry.type, entry.linkname = kind, MEMBER
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "member type"):
                material.decode(tarred([(entry, b""), (MEMBER, SVG)]), [])

    def test_zip_symlink_is_rejected(self):
        entry = zipfile.ZipInfo("link")
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.assertRaisesRegex(ValueError, "member type"):
            material.decode(zipped([(entry, b"target"), (MEMBER, SVG)]), [])

    def test_directory_is_not_a_material(self):
        with self.assertRaisesRegex(ValueError, "regular"):
            material.decode(zipped([("folder/", b"")]), ["folder/"])

    def test_expansion_limit_is_enforced_before_large_output(self):
        with patch.object(material, "MAX_EXPANDED", 128), self.assertRaisesRegex(ValueError, "byte limit"):
            material.decode(gzip.compress(b"x" * 1024), [])

    def test_declared_size_and_entry_limits(self):
        body = zipped([(MEMBER, SVG), ("other.svg", SVG)])
        with patch.object(material, "MAX_ENTRIES", 1), self.assertRaisesRegex(ValueError, "entry limit"):
            material.decode(body, [])
        with patch.object(material, "MAX_MATERIAL", 1), self.assertRaisesRegex(ValueError, "byte limit"):
            material.decode(body, [MEMBER])

    def test_layer_limit(self):
        body = SVG
        for _ in range(material.MAX_LAYERS + 1):
            body = gzip.compress(body)
        with self.assertRaisesRegex(ValueError, "layer limit"):
            material.decode(body, [])

    def test_unsupported_and_empty_resources(self):
        for body in (b"", b"not a resource", b"<html>not an icon</html>"):
            with self.subTest(body=body), self.assertRaises(ValueError):
                material.decode(body, [])

    def test_name_traversal_is_never_written_to_disk(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "body.bin"
            body = zipped([("../escape", SVG)])
            path.write_bytes(body)
            with self.assertRaises(ValueError):
                material.inspect(path, source.sha(body), ["../escape"], output=Path(root) / "out.bin")
            self.assertEqual(sorted(p.name for p in Path(root).iterdir()), ["body.bin"])

    def test_missing_optional_decoder_does_not_break_other_formats(self):
        packed = bz2.compress(SVG)
        with patch.object(material.importlib, "import_module", side_effect=ImportError("missing optional decoder")):
            with self.assertRaisesRegex(ValueError, "unavailable: bzip2"):
                material.decode(packed, [])
            self.assertEqual(material.decode(zipped([(MEMBER, SVG)]), [MEMBER])[1], SVG)

    def test_encrypted_member_is_not_prompted_or_extracted(self):
        body = bytearray(zipped([(MEMBER, SVG)]))
        local = body.index(b"PK\x03\x04")
        central = body.index(b"PK\x01\x02")
        body[local + 6] |= 1
        body[central + 8] |= 1
        with self.assertRaisesRegex(ValueError, "unencrypted"):
            material.decode(bytes(body), [MEMBER])

    def test_worker_timeout_is_not_reported_as_success(self):
        with patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("worker", 30)):
            with self.assertRaisesRegex(ValueError, "timed out"):
                material.inspect(Path("unused"), "0" * 64)


class MaterialLineage(MaterialFixture):
    def test_real_worker_material_binding_preserves_download_and_state(self):
        result, raw = self.prepared()
        original = (self.session / raw["path"]).read_bytes()
        value, path = self.validate(result["sourceMaterial"], raw)
        self.assertEqual(path.read_bytes(), SVG)
        self.assertEqual(value["download"]["sha256"], source.sha(original))
        self.assertEqual((self.session / raw["path"]).read_bytes(), original)
        self.assertFalse((self.session / "session.json").exists())
        self.assertFalse((self.session / "banks/state").exists())

    def test_member_tamper_with_rehashed_manifest_is_rejected(self):
        result, raw = self.prepared()
        path = self.session / result["material"]["path"]
        path.write_bytes(SVG.replace(b'r="4"', b'r="5"'))
        binding = self.change_manifest(result["sourceMaterial"],
            lambda value: value.update(material=source.link(self.session, path)))
        with self.assertRaisesRegex(ValueError, "reproducible extraction"):
            self.validate(binding, raw)

    def test_trace_tamper_is_rejected(self):
        result, raw = self.prepared()
        binding = self.change_manifest(result["sourceMaterial"], lambda v: v["steps"][0].update(format="tar"))
        with self.assertRaisesRegex(ValueError, "reproducible extraction"):
            self.validate(binding, raw)

    def test_resource_tamper_is_rejected(self):
        result, raw = self.prepared()
        (self.session / raw["path"]).write_bytes(b"different resource")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            self.validate(result["sourceMaterial"], raw)

    def test_other_receipt_id_base_and_future_chronology_are_rejected(self):
        for field, wrong in (("id", 20), ("baseCommit", "b" * 40), ("createdAt", "2999-01-01T00:00:00Z")):
            with self.subTest(field=field):
                result, raw = self.prepared()
                binding = self.change_manifest(result["sourceMaterial"], lambda v: v.update({field: wrong}))
                with self.assertRaises(ValueError):
                    self.validate(binding, raw)

    def test_material_symlink_is_rejected(self):
        result, raw = self.prepared()
        path = self.session / result["material"]["path"]
        target = path.with_name("replacement.bin")
        target.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.validate(result["sourceMaterial"], raw)

    def test_decoder_version_is_provenance_not_an_allowlist(self):
        result, raw = self.prepared()
        binding = self.change_manifest(result["sourceMaterial"], lambda v: v["decoder"].update(version="3.999.1"))
        self.assertEqual(self.validate(binding, raw)[1].read_bytes(), SVG)

    def test_binding_files_cover_resource_receipt_manifest_and_material(self):
        result, raw = self.prepared()
        names = {row["path"] for row in material.links(self.session, result["sourceMaterial"])}
        self.assertEqual(names, {result["sourceMaterial"]["path"], result["material"]["path"],
                                 raw["path"], raw["retrieval"]["path"]})

    def test_listing_does_not_select_or_write_material(self):
        receipt, raw = self.fetched(zipped([(MEMBER, SVG)]))
        result = material.prepare(self.session, receipt, 10, self.data["baseCommit"])
        self.assertEqual(result["status"], "source-selection-required")
        self.assertNotIn("sourceMaterial", result)
        self.assertEqual(list(self.session.glob("evidence/10/source-material-*/material.bin")), [])


if __name__ == "__main__":
    unittest.main()
