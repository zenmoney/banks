"""Isolated CLI and controlled provider proof; fixture pixels are not visual proof."""
import json
import unittest

import test_asset_publication as provider
import test_candidates as candidates

class AutonomousFlow(unittest.TestCase):
    agent_assessment = True
    head = "bank-icons/10"
    setUp = provider.ApprovedAssetCLI.setUp
    cli = provider.ApprovedAssetCLI.cli
    prepare = provider.ApprovedAssetCLI.prepare
    remote_ref = provider.ApprovedAssetCLI.remote_ref

    def run_permission(self, **changes):
        permissions = {"schemaVersion": 2, "target": "fixture/catalog", "base": "main",
                       "headPrefix": "bank-icons/",
                       "actions": {"assetPublish": True, "statePublish": True}}
        value = {"schemaVersion": 2, "scope": "bank-icon-maintenance-run", "decision": "authorize",
                 "repo": str(self.repo), "runRoot": str(self.root), "ids": [10],
                 "permissions": permissions, "authorizer": "Controlled human",
                 "statement": "Authorize bounded disposable run", "authorizedAt": "2026-09-25T12:01:00Z"}
        for key, replacement in changes.items():
            if key in permissions:
                permissions[key] = replacement
            else:
                value[key] = replacement
        path = self.root / "publication-permission.json"
        candidates.put_json(path, value)
        return path

    def test_assessed_candidate_publishes_one_reviewable_pr_without_human_visual_approval(self):
        bank = self.cli("continue")["banks"][0]
        proposal = bank["state"]["proposals"][-1]
        self.assertEqual(proposal["humanReview"], "pending")
        self.assertEqual(proposal["agentVisualAssessment"], "suitable")
        self.assertNotIn("approval", proposal)
        self.assertEqual(bank["state"]["verifiedIconHash"], candidates.digest(candidates.BASE))
        package = self.prepare()
        self.assertIsNone(self.remote_ref(self.head))
        published = self.cli("asset-publish", "--package", package["packageHash"],
                             "--run-authorization", str(self.run_permission()))
        self.assertEqual(published["status"], "mr-confirmed")
        self.assertEqual(self.remote_ref(self.head), package["package"]["assetCommit"])
        self.assertEqual(json.loads(self.gh_state.read_text())["createCalls"], 1)
        bank = self.cli("continue")["banks"][0]
        self.assertEqual(bank["state"]["proposals"][-1]["humanReview"], "pending")
        self.assertEqual(bank["state"]["verifiedIconHash"], candidates.digest(candidates.BASE))
        state = self.cli("state-prepare", "--ids", "10", "--remote", "https://github.com/fixture/catalog.git")
        saved = self.cli("state-publish", "--package", state["packageHash"],
                         "--run-authorization", str(self.run_permission()))
        self.assertEqual(saved["status"], "published")
        self.assertEqual(saved["package"]["records"][0]["after"]["proposals"][-1]["status"], "open")

    def test_out_of_scope_or_disabled_permission_cannot_publish(self):
        package = self.prepare()
        for changed in ({"ids": [11]}, {"actions": {"assetPublish": False, "statePublish": True}},
                        {"target": "fixture/other"},
                        {"runRoot": str(self.session)}):
            denied = self.cli("asset-publish", "--package", package["packageHash"],
                              "--run-authorization", str(self.run_permission(**changed)), ok=False)
            self.assertIn("error", denied)
            self.assertIsNone(self.remote_ref(self.head))
        self.assertEqual(json.loads(self.gh_state.read_text())["createCalls"], 0)

    def test_state_scope_cannot_retarget_or_enable_unauthorized_write(self):
        original = self.remote_ref("banks-icon-agent-state")
        package = self.cli("state-prepare", "--ids", "10", "--remote", "https://github.com/fixture/catalog.git")
        for changed in ({"actions": {"assetPublish": True, "statePublish": False}},
                        {"target": "fixture/other"}):
            denied = self.cli("state-publish", "--package", package["packageHash"],
                              "--run-authorization", str(self.run_permission(**changed)), ok=False)
            self.assertIn("error", denied)
        self.assertEqual(self.remote_ref("banks-icon-agent-state"), original)

    def test_provider_rejection_does_not_allow_automatic_run_retry(self):
        package = self.prepare()
        hook = self.remote / "hooks/pre-receive"
        hook.write_text("#!/bin/sh\nwhile read old new ref; do\n"
                        "  if [ \"$ref\" = refs/heads/bank-icons/10 ]; then exit 1; fi\n"
                        "done\nexit 0\n")
        hook.chmod(0o700)
        permission = self.run_permission()
        first = self.cli("asset-publish", "--package", package["packageHash"],
                         "--run-authorization", str(permission))
        self.assertEqual(first["status"], "publication-failed")
        hook.unlink()
        denied = self.cli("asset-publish", "--package", package["packageHash"],
                          "--run-authorization", str(permission), ok=False)
        self.assertIn("error", denied)
        self.assertIsNone(self.remote_ref(self.head))
        self.assertEqual(json.loads(self.gh_state.read_text())["createCalls"], 0)
