"""Compact policy, finite dialogue permission, and noninteractive authorization.

No provider writes or model runs. Package context is a controlled fixture; the
dialogue-recording CLI is exercised as a subprocess.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from test_standing_authorization import PolicyFixture, ROOT
import standing_authorization as sa
import run_authorization as run


class CompactPermissions(PolicyFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.grant = {"schemaVersion": 2, "target": "fixture/catalog", "base": "main",
                      "headPrefix": "bank-icon-maintenance/",
                      "actions": {"assetPublish": True, "assetRevise": True, "statePublish": True}}
        self.write_grant()

    def test_valid_policy_is_automatic_in_both_modes_and_across_checkout_paths(self):
        for mode in ("interactive", "noninteractive"):
            with self.subTest(mode=mode), patch.dict(os.environ, {sa.INTERACTION_ENV: mode}):
                result = sa.preflight(self.repo, ["assetPublish", "assetRevise", "statePublish"], "fixture/catalog")
                self.assertEqual(result["status"], "ready-to-publish")
                self.authorize()
                self.authorize(self.asset, "assetPublish")
        other = self.root / "another-clone"
        other.mkdir()
        self.assertEqual(sa.load(other)[0]["asset"]["target"], "fixture/catalog")
        for target in ("https://github.com/fixture/catalog.git", "git@github.com:fixture/catalog.git"):
            self.authorize({**self.state, "target": target})

    def test_absent_policy_asks_only_when_interactive_and_local_only_never_grants_publication(self):
        os.environ[sa.POLICY_ENV] = ""
        for mode, can_ask in (("interactive", True), ("noninteractive", False)):
            with self.subTest(mode=mode), patch.dict(os.environ, {sa.INTERACTION_ENV: mode}):
                result = sa.preflight(self.repo, ["assetPublish"], "fixture/catalog")
                self.assertEqual(result["status"], "authorization-required")
                self.assertEqual(result["canAsk"], can_ask)
        self.write_grant()
        self.assertEqual(sa.preflight(self.repo, ["assetPublish"], local_only=True),
                         {"status": "local-only", "publish": False})
        self.file.write_text("not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            sa.preflight(self.repo, ["assetPublish"], "fixture/catalog")
        os.environ[sa.POLICY_ENV] = str(self.root / "missing")
        with self.assertRaises(OSError):
            sa.preflight(self.repo, ["assetPublish"], "fixture/catalog")

    def test_target_actions_namespace_and_merge_stay_bounded(self):
        with self.assertRaises(ValueError):
            sa.preflight(self.repo, ["assetPublish"], "fixture/other")
        with self.assertRaises(ValueError):
            sa.preflight(self.repo, ["mergeApproved"], "fixture/catalog")
        with self.assertRaises(ValueError):
            self.authorize({**self.state, "target": "https://github.com/fixture/other.git"})
        self.grant["actions"] = {"statePublish": True}
        self.write_grant()
        self.authorize()
        with self.assertRaises(ValueError):
            self.authorize(self.asset, "assetPublish")
        with self.assertRaises(ValueError):
            sa.preflight(self.repo, ["assetRevise"], "fixture/catalog")
        for actions in ({"statePublish": "true"}, {"forcePush": True}, {"assetRevise": True}):
            self.grant["actions"] = actions
            self.write_grant()
            with self.assertRaises(ValueError):
                sa.load(self.repo)

    def test_dialogue_confirmation_covers_two_fresh_bank_sessions_not_another_run(self):
        os.environ.pop(sa.POLICY_ENV)
        root = self.root / "task"
        root.mkdir()
        command = [sys.executable, str(ROOT / "scripts/run_authorization.py"),
                   "--repo", str(self.repo), "--run-root", str(root), "--ids", "4443", "4486",
                   "--target", "fixture/catalog", "--authorizer", "fixture-user",
                   "--statement", "Yes, publish these two banks and state."]
        response = subprocess.run(command, env=os.environ.copy(), capture_output=True, text=True)
        self.assertEqual(response.returncode, 0, response.stderr + response.stdout)
        path = Path(json.loads(response.stdout)["authorization"])
        original = path.read_bytes()
        for id in (4443, 4486):
            session = root / str(id)
            session.mkdir()
            package = {**self.state, "ids": [id]}
            context = {**self.context, "selectedIds": [id]}
            with patch.object(sa, "_context", return_value=context):
                self.assertEqual(run.authorize(path, session, package, "statePublish")["ids"], [4443, 4486])
        with self.assertRaises(ValueError):
            run.authorize(path, self.session, {**self.state, "ids": [4443]}, "statePublish")
        with self.assertRaises(ValueError):
            run.authorize(path, root / "4443", self.state, "statePublish")
        with patch.object(sa, "_context", return_value={**self.context, "repo": str(self.root)}):
            with self.assertRaises(ValueError):
                run.authorize(path, root / "4443", {**self.state, "ids": [4443]}, "statePublish")
        second = subprocess.run(command, env=os.environ.copy(), capture_output=True, text=True)
        self.assertNotEqual(second.returncode, 0)
        self.assertEqual(path.read_bytes(), original)

    def test_dialogue_state_only_and_revision_opt_in_use_the_same_scope_checker(self):
        root = self.root / "limited-task"
        root.mkdir()
        args = argparse.Namespace(repo=str(self.repo), run_root=str(root), ids=[4443],
                                  target="fixture/catalog", base="main", head_prefix="bank-icon-maintenance/",
                                  actions=["statePublish"], authorizer="fixture-user", statement="Only state.")
        result = run.record(args)
        session = root / "4443"
        session.mkdir()
        run.authorize(result["authorization"], session, {**self.state, "ids": [4443]}, "statePublish")
        with self.assertRaises(ValueError):
            run.authorize(result["authorization"], session, self.asset, "assetPublish")
        import feedback_publication
        self.asset["revision"] = {"requestKey": "a" * 64}
        self.asset["materials"]["ref"] = feedback_publication.material_ref(self.asset["head"], "a" * 64)
        for allow in (False, True):
            task = self.root / f"revision-task-{allow}"
            task.mkdir()
            args.run_root = str(task)
            args.actions = ["assetPublish", "statePublish"] + (["assetRevise"] if allow else [])
            args.statement = "Publish the selected task with the stated actions."
            permission = run.record(args)["authorization"]
            bank = task / "4443"
            bank.mkdir()
            if allow:
                run.authorize(permission, bank, self.asset, "assetPublish")
            else:
                with self.assertRaisesRegex(ValueError, "does not allow"):
                    run.authorize(permission, bank, self.asset, "assetPublish")

    def test_noninteractive_cannot_manufacture_a_dialogue_confirmation(self):
        root = self.root / "unattended"
        root.mkdir()
        with patch.dict(os.environ, {sa.INTERACTION_ENV: "noninteractive"}):
            with self.assertRaisesRegex(ValueError, "noninteractive"):
                run.record(argparse.Namespace(repo=str(self.repo), run_root=str(root)))
        self.assertEqual(list(root.iterdir()), [])

if __name__ == "__main__":
    unittest.main()
