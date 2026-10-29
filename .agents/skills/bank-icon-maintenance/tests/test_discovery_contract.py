"""Discovery evidence gates: a replacement does not certify the accepted icon.

These are local validation tests with synthetic sources, not live research,
Compose rendering, state publication, or a full maintenance-session run.
"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from evidence import sha, validate_attempt


class DiscoveryContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="bank-discovery-test-")
        self.addCleanup(temporary.cleanup)
        self.session = Path(temporary.name).resolve()
        self.worktree = self.session / "worktree"
        self.folder = "banks/7-Test"
        self.base_svg = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0h2v2z"/></svg>'
        self.new_svg = b'<svg xmlns="http://www.w3.org/2000/svg"><circle cx="2" cy="2" r="2"/></svg>'
        self.icon = self.worktree / self.folder / "icon.svg"
        self.icon.parent.mkdir(parents=True)
        self.icon.write_bytes(self.base_svg)
        self.download = self.session / "evidence/7/download.svg"
        self.download.parent.mkdir(parents=True)
        self.download.write_bytes(self.new_svg)
        self.attempt = self.download.parent / "attempt.json"
        self.data = {"baseCommit": "a" * 40, "worktree": str(self.worktree)}
        self.bank = {"id": 7, "folder": self.folder, "iconHash": sha(self.base_svg),
                     "state": {"lastVerifiedAt": None, "verifiedIconHash": None}}
        observed = "2026-09-25T10:00:00Z"
        source_url = "https://example.invalid/bank/brand"
        asset_url = "https://example.invalid/bank/symbol.svg"
        self.payload = {
            "schemaVersion": 1, "id": 7, "baseCommit": self.data["baseCommit"],
            "iconHash": sha(self.base_svg), "checkedAt": "2026-09-25T10:01:00Z",
            "outcome": "candidate",
            "reason": "Fixture: supported new bank symbol; old variant attribution is unknown.",
            "nextAction": "Prepare final SVG, source/base/final renders, and assess the update.",
            "sources": [
                {"kind": "official", "url": source_url, "assetUrl": asset_url,
                 "checkedAt": observed, "observation": "Fixture: current symbol for the same bank.",
                 "independenceGroup": "Synthetic primary publication"},
                {"kind": "zenplugins-issue", "url": "https://example.invalid/issues",
                 "assetUrl": None, "checkedAt": observed,
                 "observation": "Fixture: no matching issue in searched results.",
                 "independenceGroup": "Synthetic issue search"},
                {"kind": "zenplugins-pr", "url": "https://example.invalid/pulls",
                 "assetUrl": None, "checkedAt": observed,
                 "observation": "Fixture: no matching open or closed PR in searched results.",
                 "independenceGroup": "Synthetic PR search"},
            ],
            "assessment": {
                "identity": {"finding": "confirmed", "basis": "Fixture: replacement belongs to bank 7."},
                "currency": {"finding": "confirmed", "basis": "Fixture: source identifies its current design."},
                "variant": {"finding": "confirmed", "basis": "Fixture: replacement symbol suits catalog use."},
                "independenceRationale": "Synthetic primary asset; issue/PR searches are not corroboration.",
                "uncertainties": ["Accepted old-icon attribution remains unknown; not a candidate ambiguity."],
            },
            "desktop": None,
            "platforms": [{"target": target, "status": "not-tested",
                           "observation": "Fixture: terminal-only run; no mobile observation."}
                          for target in ("android-compose", "android-native", "ios-compose", "ios-native")],
            "recheckRequired": False,
            "candidate": {"path": "evidence/7/download.svg", "iconHash": sha(self.new_svg),
                          "sourceUrl": source_url, "sourceAssetUrl": asset_url,
                          "rationale": "Fixture: evidenced new design differs visibly from the old symbol."},
        }

    def validate(self):
        self.attempt.write_text(json.dumps(self.payload), encoding="utf-8")
        return validate_attempt(self.attempt, self.data, self.bank)

    def make_unconfirmed(self):
        self.payload.update(outcome="unconfirmed", candidate=None,
                            reason="Fixture: no supported replacement; current variant remains uncertain.",
                            nextAction="Prepare and show the unsuccessful-attempt status.")
        self.payload["sources"] = self.payload["sources"][1:]
        self.payload["assessment"]["variant"] = {
            "finding": "uncertain", "basis": "Fixture: no asset confirms the accepted variant."}

    def test_candidate_does_not_require_current_verification_or_render(self):
        before = copy.deepcopy((self.data, self.bank))
        result = self.validate()
        self.assertEqual(result, self.payload)
        self.assertIsNone(result["desktop"])
        self.assertEqual(result["iconHash"], sha(self.base_svg))
        self.assertEqual(result["candidate"]["iconHash"], sha(self.new_svg))
        self.assertTrue(result["assessment"]["uncertainties"])
        self.assertEqual((self.data, self.bank), before)
        self.assertEqual(self.icon.read_bytes(), self.base_svg)
        self.assertFalse((self.session / "reports").exists())
        self.assertFalse((self.session / "banks/state").exists())

    def test_candidate_accepts_recorded_indirect_sources_without_official_gate(self):
        first = self.payload["sources"][0]
        first.update(kind="third-party", independenceGroup="Synthetic independent publisher A")
        second = dict(first, url="https://example.invalid/independent-publication",
                      assetUrl=None, independenceGroup="Synthetic independent publisher B",
                      observation="Fixture: independently corroborates identity and design.")
        self.payload["sources"].append(second)
        self.payload["assessment"]["independenceRationale"] = "Fixture: separate publishers, not mirrors."
        self.assertEqual(self.validate()["outcome"], "candidate")

    def test_candidate_still_requires_its_own_confirmed_assessment(self):
        for aspect in ("identity", "currency", "variant"):
            with self.subTest(aspect=aspect):
                self.payload["assessment"][aspect]["finding"] = "uncertain"
                with self.assertRaisesRegex(ValueError, "positive outcome cannot have ambiguous"):
                    self.validate()
                self.payload["assessment"][aspect]["finding"] = "confirmed"

    def test_candidate_keeps_exact_unverified_baseline_binding(self):
        with self.subTest(change="wrong baseline hash"):
            self.payload["iconHash"] = sha(self.new_svg)
            with self.assertRaisesRegex(ValueError, "exact current SVG bytes"):
                self.validate()
        with self.subTest(change="changed baseline file"):
            self.payload["iconHash"] = sha(self.base_svg)
            self.icon.write_bytes(self.new_svg)
            with self.assertRaisesRegex(ValueError, "exact current SVG bytes"):
                self.validate()

    def test_identical_bytes_are_not_a_replacement(self):
        self.download.write_bytes(self.base_svg)
        self.payload["candidate"]["iconHash"] = sha(self.base_svg)
        with self.assertRaisesRegex(ValueError, "same as current SVG"):
            self.validate()

    def test_candidate_download_hash_is_checked(self):
        self.download.write_bytes(self.new_svg + b"\n")
        with self.assertRaisesRegex(ValueError, "candidate file differs"):
            self.validate()

    def test_candidate_requires_observed_source_lineage(self):
        for field in ("sourceUrl", "sourceAssetUrl"):
            with self.subTest(field=field):
                previous = self.payload["candidate"][field]
                self.payload["candidate"][field] = "https://example.invalid/unobserved"
                with self.assertRaisesRegex(ValueError, "must be observed in sources"):
                    self.validate()
                self.payload["candidate"][field] = previous

    def test_completed_search_needs_observations_not_vendor_specific_checks(self):
        original = copy.deepcopy(self.payload)
        for outcome in ("candidate", "unconfirmed"):
            with self.subTest(outcome=outcome):
                self.payload = copy.deepcopy(original)
                if outcome == "unconfirmed":
                    self.make_unconfirmed()
                    observation = copy.deepcopy(original["sources"][0])
                    observation.update(assetUrl=None, observation="Fixture: official source did not establish a usable current variant.")
                    self.payload["sources"] = [observation]
                self.payload["sources"] = [r for r in self.payload["sources"] if r["kind"] in ("official", "third-party")]
                self.assertEqual(self.validate()["outcome"], outcome)
                self.payload["sources"] = []
                with self.assertRaisesRegex(ValueError, "recorded source observations"):
                    self.validate()

    def test_initial_candidate_does_not_relabel_render_evidence(self):
        self.payload["desktop"] = {"renderer": "not-an-actual-render"}
        with self.assertRaisesRegex(ValueError, "candidate rendering belongs to later review"):
            self.validate()

    def test_initial_candidate_cannot_invent_mobile_passes(self):
        self.payload["platforms"][0]["status"] = "passed"
        with self.assertRaisesRegex(ValueError, "passed platform result needs a bound Desktop manifest"):
            self.validate()

    def test_unconfirmed_can_record_no_asset_without_claiming_a_render(self):
        self.make_unconfirmed()
        before = copy.deepcopy(self.bank)
        self.assertEqual(self.validate()["outcome"], "unconfirmed")
        self.assertEqual(self.bank, before)
        self.assertEqual(self.icon.read_bytes(), self.base_svg)

    def test_unconfirmed_must_identify_what_remains_uncertain(self):
        self.make_unconfirmed()
        self.payload["assessment"]["variant"]["finding"] = "confirmed"
        with self.assertRaisesRegex(ValueError, "must identify what remains unconfirmed"):
            self.validate()

    def test_unchanged_cannot_inherit_candidate_confirmation_with_old_uncertainty(self):
        self.payload.update(outcome="unchanged", candidate=None)
        with self.assertRaisesRegex(ValueError, "ambiguous current variant cannot be unchanged"):
            self.validate()

    def test_unchanged_cannot_skip_required_recheck(self):
        self.payload.update(outcome="unchanged", candidate=None, recheckRequired=True)
        self.payload["assessment"]["uncertainties"] = []
        with self.assertRaisesRegex(ValueError, "recheck required before current SVG can be confirmed"):
            self.validate()

    def test_candidate_evidence_cannot_escape_session(self):
        self.payload["candidate"]["path"] = "evidence/7/../../outside.svg"
        with self.assertRaisesRegex(ValueError, "must remain under session evidence"):
            self.validate()


if __name__ == "__main__":
    unittest.main()
