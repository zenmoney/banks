"""Material -> real PNG source reference -> comparison -> reviewer package.

Image pixels are real Pillow images. Desktop/XML observations and workflow
context are controlled fixtures, not an actual Compose render or provider run.
"""
import copy
import importlib.util
import io
import json
import shutil
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import test_source_material as fixtures
from test_source_material import MaterialFixture, SVG, zipped, tarred
import source_evidence as source
import source_material as material
import review_materials as review
import workflow
from PIL import Image

MEMBER = "Белый фон/Лого.png"


def png(color=(22, 130, 55, 255), size=(18, 12)):
    output = io.BytesIO()
    Image.new("RGBA", size, color).save(output, format="PNG")
    return output.getvalue()


class MaterialPipeline(MaterialFixture):
    def file(self, name, content):
        path = self.session / "evidence/10" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return source.link(self.session, path)

    def fixture(self, pack=zipped, *, pin_in_attempt=False, direct=False):
        original = png()
        body = original if direct else pack([(MEMBER, original)])
        result, raw = self.prepared(body, [] if direct else [MEMBER])
        old_raw = copy.deepcopy(raw)
        if pin_in_attempt:
            raw["sourceMaterial"] = result["sourceMaterial"]
        reference_link = source.prepare_reference(self.session, raw, self.data, self.bank,
            material=None if pin_in_attempt else result["sourceMaterial"])
        reference = source.read_json(self.session / reference_link["path"])
        final = self.file("final.svg", SVG)
        xml = self.file("export/composeResources/drawable/bank_10.xml", b'<vector controlled="fixture"/>')
        metadata = {"id": 10, "title": "Fixture", "countryCode": "RU"}
        base = SVG.replace(b'r="4"', b'r="3"')
        self.bank.update(folder="banks/Fixture_10-ru", title="Fixture", metadata=metadata,
                         metadataHash=source.sha(json.dumps(metadata).encode()), iconHash=source.sha(base))
        folder = Path(self.data["worktree"]) / self.bank["folder"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "info.json").write_text(json.dumps(metadata))
        (folder / "icon.svg").write_bytes(base)
        observed = source.now()
        manifest = {"schemaVersion": 1, "sourceRoot": self.data["worktree"],
            "banksRoot": str(Path(self.data["worktree"]) / "banks"),
            "pins": {"converter": "controlled-fixture", "compose": "controlled-fixture"},
            "entries": [{"id": 10, "error": None, "sourceValidated": True,
                "sourcePath": str(self.session / final["path"]), "sourceSha256": final["sha256"],
                "xmlSha256": xml["sha256"], "resourceName": "bank_10"}]}
        manifest_link = self.file("export/manifest.json", json.dumps(manifest).encode())
        desktop = {"renderer": review.RENDERER, "observedAt": observed,
                   "manifest": manifest_link["path"], "manifestSha256": manifest_link["sha256"]}
        comparisons = {}
        for i, theme in enumerate(source.BACKGROUNDS):
            desktop[theme] = self.file(f"export/{theme}.png", png((80+i, 60, 30, 255), (240, 240)))
            comparisons[theme] = {"source": reference["images"][theme],
                "base": self.file(f"compare/{theme}-base.png", png((5, 6+i, 7, 255), (240, 240))),
                "candidate": desktop[theme],
                "beforeAfter": self.file(f"compare/{theme}-pair.png", png((5, 6+i, 7, 255), (480, 240)))}
        bundle = self.file("export/bundle.json", json.dumps({"desktop": desktop, "comparison": comparisons}).encode())
        # The external renderer is not exercised. The source-reference decoder,
        # pair compositor and subsequent pixel/binding validators ARE real.
        with patch("evidence.desktop_observation", return_value=(xml["sha256"], manifest["pins"])):
            view_link = source.prepare_comparison(self.session, raw, self.data, self.bank,
                self.session / final["path"], bundle, reference_link)
        checked = source.now()
        candidate = {**final, "sourceUrl": raw["sourceUrl"], "sourceAssetUrl": raw["sourceAssetUrl"],
            "rationale": "Controlled fixture, no actual bank judgment", "role": "fixture",
            "variant": "fixture", "transforms": ["Fixture vector adaptation; not a visual claim"],
            "detailsJudgement": "Fixture", "proportionsJudgement": "Fixture",
            "faithful": True, "visibleDiff": True, "visibleDiffExplanation": "Fixture"}
        evidence = {"baseCommit": self.data["baseCommit"], "metadataHash": self.bank["metadataHash"],
            "baseIconHash": self.bank["iconHash"], "checkedAt": checked,
            "sourceComparison": view_link, "candidate": candidate,
            "desktop": {**desktop, "visualComparison": "Controlled Desktop observation, not a runtime pass"},
            "comparison": comparisons, "diff": self.file("candidate.diff", b"controlled diff\n"),
            "platforms": [{"target": target, "available": False, "status": "not-tested", "observation": "fixture only"}
                for target in ("android-compose", "android-native", "ios-compose", "ios-native")]}
        source.validate_comparison(self.session, view_link, raw, evidence, self.data, self.bank,
                                   source.instant(checked, "checkedAt"))
        sources = [{"kind": "official", "url": raw["sourceUrl"], "assetUrl": raw["sourceAssetUrl"],
                    "checkedAt": checked, "independenceGroup": "controlled",
                    "observation": "Resource " + raw["path"] + "; selected material " + reference["source"]["path"]}]
        assessment = {key: {"finding": "confirmed", "basis": "Fixture only"} for key in ("identity", "currency", "variant")}
        assessment.update(independenceRationale="Fixture", uncertainties=[])
        prior = {"candidate": raw, "sources": sources, "assessment": assessment}
        saved = {**final, "iconHash": final["sha256"]}
        report = {"id": 10, "outcome": "candidate", "evidence": evidence,
            "before": {"proposals": [prior]}, "after": {"proposals": [{"sourceCandidate": raw, "candidate": saved}]},
            "candidate": final, "renderBinding": {"sourceSha256": final["sha256"], "xmlSha256": xml["sha256"],
                "converter": "controlled-fixture", "compose": "controlled-fixture"},
            "provenance": {"baseCommit": self.data["baseCommit"], "metadataHash": self.bank["metadataHash"]}}
        return result, raw, old_raw, reference_link, report, original

    def test_raster_container_material_remains_research_only(self):
        for pack in (zipped, tarred):
            with self.subTest(container=pack.__name__):
                result, raw, old_raw, reference, report, original = self.fixture(pack)
                saved_report = copy.deepcopy(report)
                with self.assertRaisesRegex(ValueError, "original vector"):
                    review.prepare(self.session, self.bank, report)
                self.assertEqual(report, saved_report)
                self.assertEqual(raw, old_raw)
                self.assertEqual((self.session / result["material"]["path"]).read_bytes(), original)

    

    def test_new_attempt_selection_is_not_replaceable(self):
        result, raw, _, ref, report, _ = self.fixture(pin_in_attempt=True)
        alternative = material.prepare(self.session, raw["retrieval"], 10, self.data["baseCommit"], [MEMBER])
        with self.assertRaisesRegex(ValueError, "original attempt"):
            source.prepare_reference(self.session, raw, self.data, self.bank, material=alternative["sourceMaterial"])

    def test_dropped_selection_binding_is_rejected(self):
        _, raw, _, ref, _, _ = self.fixture(pin_in_attempt=True)
        path = self.session / ref["path"]
        value = source.read_json(path)
        del value["sourceMaterial"]
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "dropped"):
            source.validate_reference(self.session, source.link(self.session, path), raw, self.data, self.bank,
                                      datetime.now(timezone.utc))

    def test_reference_raster_cannot_be_replaced_by_final_pixels(self):
        _, raw, _, ref, report, _ = self.fixture()
        path = self.session / ref["path"]
        value = source.read_json(path)
        value["raster"] = report["evidence"]["desktop"]["light"]
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "pixels differ"):
            source.validate_reference(self.session, source.link(self.session, path), raw, self.data, self.bank,
                                      datetime.now(timezone.utc))

    def test_review_replays_extraction_after_material_file_changes(self):
        result, raw, _, ref, report, _ = self.fixture()
        (self.session / result["material"]["path"]).write_bytes(png((255, 1, 1, 255)))
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            review.prepare(self.session, self.bank, report)

    def test_initial_source_validator_replays_pinned_material(self):
        result, raw, _, ref, report, _ = self.fixture(pin_in_attempt=True)
        source.validate_source(self.session, raw, self.data, self.bank, datetime.now(timezone.utc))
        (self.session / result["material"]["path"]).write_bytes(png((255, 2, 3, 255)))
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            source.validate_source(self.session, raw, self.data, self.bank, datetime.now(timezone.utc))

    def test_direct_raster_reference_is_research_not_publication(self):
        _, raw, _, _, report, original = self.fixture(direct=True)
        reference = source.prepare_reference(self.session, raw, self.data, self.bank)
        source.validate_reference(self.session, reference, raw, self.data, self.bank, datetime.now(timezone.utc))
        with self.assertRaisesRegex(ValueError, "original vector"):
            review.prepare(self.session, self.bank, report)
        self.assertEqual((self.session / raw["path"]).read_bytes(), original)

    def test_new_container_attempt_is_stopped_before_recording_without_material(self):
        receipt, raw = self.fetched(zipped([("Logo.svg", SVG)]))
        self.bank.update(point="identity-evidence", iconHash="b" * 64)
        notes = {"outcome": "candidate", "reason": "fixture", "nextAction": "fixture", "sources": [],
                 "assessment": {}, "platforms": [], "recheckRequired": False, "recheckRationale": "fixture",
                 "candidate": {"sourceUrl": raw["sourceUrl"], "rationale": "fixture"}}
        with patch.object(workflow, "_context", return_value=(self.data, self.bank)), \
                patch.object(workflow, "validate_attempt") as validator, \
                self.assertRaisesRegex(ValueError, "before recording"):
            workflow.assemble_attempt(self.session, 10, notes, retrieval_path=raw["retrieval"]["path"])
        validator.assert_not_called()
        self.assertEqual(list(self.session.glob("evidence/10/attempt-input-*")), [])

    def test_new_attempt_uses_material_binding_and_never_copies_working_bytes(self):
        result, raw = self.prepared()
        self.bank.update(point="identity-evidence", iconHash="b" * 64)
        notes = {"outcome": "candidate", "reason": "fixture", "nextAction": "fixture", "sources": [],
                 "assessment": {}, "platforms": [], "recheckRequired": False, "recheckRationale": "fixture",
                 "candidate": {"sourceUrl": raw["sourceUrl"], "rationale": "fixture"}}
        with patch.object(workflow, "_context", return_value=(self.data, self.bank)), \
                patch.object(workflow, "validate_attempt") as validator:
            built = workflow.assemble_attempt(self.session, 10, notes,
                retrieval_path=raw["retrieval"]["path"], material_path=result["sourceMaterial"]["path"])
        validator.assert_called_once()
        attempt = source.read_json(self.session / built["input"]["path"])
        self.assertEqual(attempt["candidate"]["path"], raw["path"])
        self.assertEqual(attempt["candidate"]["sourceMaterial"], result["sourceMaterial"])
        self.assertFalse((self.session / "session.json").exists())

    def test_pending_proposal_cannot_switch_retrieval(self):
        first, raw = self.fetched(zipped([("Logo.svg", SVG)]))
        second, other = self.fetched(zipped([("Logo.svg", SVG)]))
        self.bank["point"] = "waiting-proposal"
        with patch.object(workflow, "_context", return_value=(self.data, self.bank)), \
                patch.object(workflow, "_raw_candidate", return_value=({}, raw)), \
                self.assertRaisesRegex(ValueError, "original proposal"):
            workflow.source_material(self.session, 10, second["path"], ["Logo.svg"])

    def test_state_binding_enumeration_includes_complete_material_lineage(self):
        result, raw, _, reference, report, _ = self.fixture()
        links = source.evidence_links(self.session, report["evidence"])
        names = {item["path"] for item in links}
        for item in (raw["retrieval"], result["sourceMaterial"], result["material"], reference):
            self.assertIn(item["path"], names)
        self.assertIn(raw["path"], names)
        for item in links:
            source.linked(self.session, item, "bound source evidence")

    def test_legacy_initial_state_binding_shape_is_unchanged(self):
        receipt, raw = self.fetched(png())
        self.assertEqual(source.evidence_links(self.session, {"candidate": raw}), [])

    @unittest.skipUnless(shutil.which("rsvg-convert"), "real librsvg unavailable")
    def test_real_svg_member_reference(self):
        result, raw = self.prepared()
        ref = source.prepare_reference(self.session, raw, self.data, self.bank, material=result["sourceMaterial"])
        value = source.validate_reference(self.session, ref, raw, self.data, self.bank, datetime.now(timezone.utc))
        self.assertEqual(value["renderer"], "librsvg source reference")
        self.assertEqual(value["sourceFormat"], "SVG")


if __name__ == "__main__":
    unittest.main()
