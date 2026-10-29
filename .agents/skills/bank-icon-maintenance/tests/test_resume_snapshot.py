"""Continuation validates one bank, not other banks' in-flight transitions."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import asset_results


class ResumeSnapshot(unittest.TestCase):
    def setUp(self):
        self.saved = {"repo": "/fixture/repo", "baseCommit": "a" * 40,
                      "selectedIds": [10], "paused": False,
                      "banks": [{"id": 10, "point": "waiting-proposal", "report": "bound-report",
                                 "state": {"candidate": "unchanged"}}]}
        self.data = copy.deepcopy(self.saved)

    def validate(self):
        with patch.object(asset_results, "load_candidate", return_value=(
                self.saved, self.saved["banks"][0], None, None)) as loader:
            asset_results.validate_resume(None, self.data, self.data["banks"][0])
            loader.assert_called_once_with(None, 10, _resume=True)


    def test_recomputed_bookkeeping_is_not_a_candidate_change(self):
        self.data["continuationStops"] = []
        self.validate()

    def test_exact_candidate_changes_still_fail(self):
        for key, value in (("point", "needs-human"), ("report", "different"), ("state", {})):
            with self.subTest(key=key):
                self.data = copy.deepcopy(self.saved)
                self.data["banks"][0][key] = value
                with self.assertRaisesRegex(ValueError, "candidate changed"):
                    self.validate()

    def test_context_changes_including_unknown_fields_still_fail(self):
        for key, value in (("repo", "/different"), ("baseCommit", "b" * 40),
                           ("selectedIds", [20]), ("paused", True), ("futureContext", "changed")):
            with self.subTest(key=key):
                self.data = copy.deepcopy(self.saved)
                self.data[key] = value
                with self.assertRaisesRegex(ValueError, "candidate changed"):
                    self.validate()

    def test_full_chain_validation_errors_are_not_suppressed(self):
        with patch.object(asset_results, "load_candidate", side_effect=ValueError("source hash mismatch")):
            with self.assertRaisesRegex(ValueError, "source hash mismatch"):
                asset_results.validate_resume(None, self.data, self.data["banks"][0])


if __name__ == "__main__":
    unittest.main()
