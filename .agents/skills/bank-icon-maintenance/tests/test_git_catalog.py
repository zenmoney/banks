"""Scale is asserted by process count, not a machine-specific timing threshold."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import asset_publication as asset
import git_catalog as catalog
import state_publication as state


class GitCatalog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        state.git(self.repo, "init", "-q")
        state.git(self.repo, "config", "user.name", "Fixture")
        state.git(self.repo, "config", "user.email", "fixture@localhost")

    def put(self, path, value):
        file = self.repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(json.dumps(value).encode())

    def commit(self):
        state.git(self.repo, "add", ".")
        state.git(self.repo, "commit", "-qm", "fixture")
        return state.git(self.repo, "rev-parse", "HEAD").stdout.decode().strip()

    def test_thousand_banks_use_constant_process_count_and_preserve_exact_bindings(self):
        for id in range(1000):
            self.put(f"banks/Test_{id}-ru/info.json", {"id": id, "title": "Test"})
        icon = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
        path = "banks/Test_10-ru"
        (self.repo / path / "icon.svg").write_bytes(icon)
        commit = self.commit()
        bank = {"id": 10, "folder": path, "iconHash": state.digest(icon),
                "metadataHash": state.digest((self.repo / path / "info.json").read_bytes())}
        with patch.object(subprocess, "Popen", wraps=subprocess.Popen) as processes:
            asset._bank_base(self.repo, commit, bank)
        self.assertLessEqual(processes.call_count, 8)
        bank["iconHash"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "baseline bytes"):
            asset._bank_base(self.repo, commit, bank)

    def test_changed_metadata_builds_new_index_and_duplicate_is_not_cached_away(self):
        self.put("banks/A_10-ru/info.json", {"id": 10})
        first = self.commit()
        with catalog.scope(), patch.object(catalog, "git", wraps=catalog.git) as calls:
            self.assertEqual(catalog.identity_paths(self.repo, first, 10), ["banks/A_10-ru/info.json"])
            initial = calls.call_count
            catalog.identity_paths(self.repo, first, 10)
            self.assertEqual(calls.call_count - initial, 1)
            self.put("banks/B_20-ru/info.json", {"id": 10})
            second = self.commit()
            self.assertEqual(catalog.identity_paths(self.repo, second, 10),
                             ["banks/A_10-ru/info.json", "banks/B_20-ru/info.json"])
            self.assertEqual(catalog.identity_paths(self.repo, first, 10), ["banks/A_10-ru/info.json"])

    def test_paths_with_spaces_unicode_and_newlines_are_not_batch_expressions(self):
        path = "banks/Банк\n с пробелом_10-ru/info.json"
        self.put(path, {"id": 10})
        self.assertEqual(catalog.identity_paths(self.repo, self.commit(), 10), [path])

    def test_unrelated_malformed_noninteger_or_symlink_metadata_does_not_count_as_id(self):
        self.put("banks/A_1-ru/info.json", {"id": 1})
        self.put("banks/B_2-ru/info.json", {"id": True})
        self.put("banks/C_3-ru/info.json", {"id": "1"})
        self.put("banks/D_4-ru/info.json", "bad structure")
        link = self.repo / "banks/E_5-ru/info.json"
        link.parent.mkdir()
        link.symlink_to("../A_1-ru/info.json")
        self.assertEqual(catalog.identity_paths(self.repo, self.commit(), 1), ["banks/A_1-ru/info.json"])

    def test_truncated_batch_cannot_prove_unique_identity(self):
        self.put("banks/A_1-ru/info.json", {"id": 1})
        commit = self.commit()
        real = catalog.git
        def truncated(repo, *args, **kwargs):
            result = real(repo, *args, **kwargs)
            if "--batch" in args:
                result.stdout = result.stdout[:-1]
            return result
        with patch.object(catalog, "git", side_effect=truncated), self.assertRaisesRegex(ValueError, "truncated"):
            catalog.identity_paths(self.repo, commit, 1)
        # A failed batch was not cached.
        self.assertEqual(catalog.identity_paths(self.repo, commit, 1), ["banks/A_1-ru/info.json"])

    def test_mutable_ref_is_not_a_cache_key(self):
        self.put("banks/A_1-ru/info.json", {"id": 1})
        self.commit()
        with self.assertRaisesRegex(ValueError, "exact commit"):
            catalog.identity_paths(self.repo, "HEAD", 1)

    def test_missing_object_is_not_silently_skipped(self):
        self.put("banks/A_1-ru/info.json", {"id": 1})
        commit = self.commit()
        oid = state.git(self.repo, "rev-parse", f"{commit}:banks/A_1-ru/info.json").stdout.decode().strip()
        (self.repo / ".git/objects" / oid[:2] / oid[2:]).unlink()
        with self.assertRaisesRegex(ValueError, "missing or non-blob"):
            catalog.identity_paths(self.repo, commit, 1)
