"""Complete-checkout CLI/Git regression paths; no live banks, MRs or remote pushes."""
import copy
import gzip
import json
from pathlib import Path
import subprocess
import sys
import unittest

from test_source_material import zipped, tarred

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import source_evidence as source
import review_materials


@unittest.skipUnless((ROOT / "scripts/session.py").is_file() and
                     (ROOT / "tests/test_workflow_integration.py").is_file(),
                     "complete checkout required for canonical CLI/Git integration")
class MaterialCLI(unittest.TestCase):
    def setUp(self):
        from test_workflow_integration import WorkflowIntegration
        fixture = WorkflowIntegration("runTest")
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.fixture = fixture
        self.session = fixture.session
        self.raw_bytes = (self.session / fixture.fixture.body["path"]).read_bytes()
        self.member = "White background/Logo.svg"

    def resource(self, pack):
        self.fixture.fixture.replace_source(pack([(self.member, self.raw_bytes)]))
        return self.session / self.fixture.fixture.receipt_link["path"]

    def complete(self, pack, *, legacy=False):
        f = self.fixture
        receipt = self.resource(pack)
        if legacy:
            # This is the old writer-supported resource attempt, not a rewrite of
            # already saved evidence and not a new maintenance session.
            notes = json.loads(f.notes.read_text())
            payload = {"schemaVersion": 1, "id": 10, "baseCommit": f.started["baseCommit"],
                       "iconHash": f.started["banks"][0]["iconHash"], "checkedAt": source.now(),
                       **notes, "desktop": None, "candidate": f.fixture.raw}
            f.session_call("continue", "--evidence", str(f.write("legacy-attempt.json", payload)))
        resolved = f.helper("source-material", "--id", "10", "--retrieval", str(receipt),
                            "--member", self.member)
        material_file = self.session / resolved["sourceMaterial"]["path"]
        if not legacy:
            f.helper("attempt", "--id", "10", "--notes", str(f.notes), "--retrieval", str(receipt),
                     "--material", str(material_file), "--record")
        old_report = (self.session / "reports/10.json").read_bytes()
        old_evidence = (self.session / "evidence/10/validated.json").read_bytes()
        bundle_link, bundle = f.fixture.desktop_bundle()
        bundle["desktop"]["observedAt"] = source.now()
        f.fixture.put(bundle_link["path"], json.dumps(bundle).encode())
        arguments = ["candidate-view", "--id", "10", "--final", str(f.fixture.final),
                     "--desktop-bundle", str(self.session / bundle_link["path"])]
        if legacy:
            arguments += ["--material", str(material_file)]
        view = f.helper(*arguments)
        f.helper("candidate-build", "--id", "10", "--view", str(self.session / view["view"]["path"]),
                 "--notes", str(f.write("candidate-notes.json", f.candidate_notes())), "--record")
        f.helper("assessment", "--id", "10", "--notes", str(f.write("assessment-notes.json",
                 {"assessor": "fixture", "decision": "suitable", "statement": "Controlled fixture, not a visual certification"})),
                 "--record")
        self.assertEqual(old_report, (self.session / "reports/10.json").read_bytes())
        self.assertEqual(old_evidence, (self.session / "evidence/10/validated.json").read_bytes())
        from asset_results import load_candidate
        data, bank, report, assessment = load_candidate(self.session, 10)
        public = review_materials.prepare(self.session, bank, report)
        self.assertEqual(next(x["content"] for x in public["files"] if x["name"] == "source.svg"), self.raw_bytes)
        self.assertEqual(len(public["files"]), 7)
        body = review_materials.render(public, "fixture", "catalog", "c" * 40)
        self.assertIn("Source material origin", body)
        self.assertNotIn(str(self.session), body)
        remote = f.root / "state.git"
        subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
        prepared = f.session_call("state-prepare", "--remote", str(remote), "--ids", "10")
        self.assertIn("packageHash", prepared)
        # Every selected source dependency must participate in the state package.
        bindings = prepared["package"]["bindings"]
        for link in (resolved["sourceMaterial"], resolved["material"], f.fixture.receipt_link, f.fixture.body):
            self.assertIn(str(self.session / link["path"]), bindings)
        self.assertEqual(subprocess.run(["git", "--git-dir", str(remote), "show-ref"], capture_output=True).returncode, 1)
        return resolved, remote

    def test_zip_through_canonical_candidate_assessment_state_and_reviewer_materials(self):
        self.complete(zipped)

    def test_compressed_tar_through_same_canonical_pipeline(self):
        self.complete(lambda items: gzip.compress(tarred(items)))

    def test_legacy_pending_container_attempt_is_append_only(self):
        self.complete(zipped, legacy=True)
        state = json.loads((self.session / "session.json").read_bytes())
        self.assertNotIn("sourceMaterial", state["banks"][0]["state"]["proposals"][-1]["sourceCandidate"])

    def test_canonical_writer_rejects_material_without_its_retrieval(self):
        f = self.fixture
        receipt = self.resource(zipped)
        resolved = f.helper("source-material", "--id", "10", "--retrieval", str(receipt), "--member", self.member)
        built = f.helper("attempt", "--id", "10", "--notes", str(f.notes), "--retrieval", str(receipt),
                         "--material", str(self.session / resolved["sourceMaterial"]["path"]))
        payload = source.read_json(self.session / built["input"]["path"])
        del payload["candidate"]["retrieval"]
        before = (self.session / "session.json").read_bytes()
        result = f.session_call("continue", "--evidence", str(f.write("tampered-input.json", payload)), ok=False)
        self.assertIn("original retrieval binding", result["error"])
        self.assertEqual(before, (self.session / "session.json").read_bytes())

    def test_later_state_preparation_rejects_tampered_member(self):
        resolved, remote = self.complete(zipped)
        state = (self.session / "banks/state/10.json").read_bytes()
        (self.session / resolved["material"]["path"]).write_bytes(b"changed member")
        result = self.fixture.session_call("state-prepare", "--remote", str(remote), "--ids", "10", ok=False)
        self.assertIn("hash mismatch", result["error"])
        self.assertEqual(state, (self.session / "banks/state/10.json").read_bytes())
        self.assertEqual(subprocess.run(["git", "--git-dir", str(remote), "show-ref"], capture_output=True).returncode, 1)


if __name__ == "__main__":
    unittest.main()
