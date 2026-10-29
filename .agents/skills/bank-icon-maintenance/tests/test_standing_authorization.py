"""Standing authority and publication boundaries, not source/render certification.

Context validators and asset/provider boundaries are controlled fixtures. State
transport tests use actual Git objects, local bare remotes, locks and receipts.
"""
import argparse
import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import standing_authorization as sa
import state_publication as sp
import asset_publication as ap
import environment
import github_pr
import run_authorization


def encoded(value):
    return sp.encoded(value)


class PolicyFixture:
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="standing-policy-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.repo = self.root / "checkout"
        self.repo.mkdir()
        self.session = self.root / "session"
        self.session.mkdir()
        self.file = self.root / "owner-policy.json"
        self.context = {"repo": str(self.repo), "baseCommit": "b" * 40,
                        "selectedIds": [4443, 4486, 4509, 4523, 4525]}
        self.grant = {"schemaVersion": 2, "target": "fixture/catalog", "base": "main",
                      "headPrefix": "bank-icon-maintenance/",
                      "actions": {"assetPublish": True, "statePublish": True}}
        self.start_patch(patch.dict(os.environ, dict(os.environ)))
        os.environ.pop(sa.POLICY_ENV, None)
        os.environ.pop(sa.INTERACTION_ENV, None)
        self.context_validator = self.start_patch(patch.object(sa, "_context", return_value=self.context))
        self.write_grant()
        self.state = {"schemaVersion": 1, "scope": "state-publication",
                      "target": "git@github.com:fixture/catalog.git", "ref": sp.REF,
                      "ids": list(self.context["selectedIds"]), "baseCommit": self.context["baseCommit"],
                      "reconciliation": {}}
        self.asset = {"schemaVersion": 1, "scope": "asset-publication", "id": 4443,
                      "target": "fixture/catalog", "base": "main", "baseCommit": "c" * 40,
                      "head": "bank-icon-maintenance/run-4443",
                      "headRef": "refs/heads/bank-icon-maintenance/run-4443",
                      "materials": {"ref": "refs/heads/bank-icon-maintenance/run-4443-review",
                                    "commit": "d" * 40},
                      "assetCommit": "e" * 40, "public": False, "title": "fixture", "body": "fixture"}

    def start_patch(self, patcher):
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def write_grant(self):
        self.file.write_bytes(encoded(self.grant))
        self.file.chmod(0o600)
        os.environ[sa.POLICY_ENV] = str(self.file)

    def authorize(self, package=None, action="statePublish", session=None):
        package = self.state if package is None else package
        return sa.authorize(session or self.session, package, sp.digest(encoded(package)), action)


class StandingAuthority(PolicyFixture, unittest.TestCase):
    def test_persistent_policy_covers_new_sessions_without_inventing_human_approval(self):
        first = self.authorize()
        other = self.root / "second-session"
        other.mkdir()
        second = self.authorize(session=other)
        self.assertEqual(first["policy"], second["policy"])
        self.assertNotEqual(first["session"], second["session"])
        self.assertIsNone(first["authorizedAt"])
        self.assertNotIn("humanReview", first)
        self.assertEqual(first["packageHash"], sp.digest(encoded(self.state)))

    def test_policy_actions_revocation_and_package_ids_are_checked(self):
        self.grant["actions"]["assetPublish"] = False
        self.write_grant()
        self.authorize()
        with self.assertRaisesRegex(ValueError, "does not permit"):
            self.authorize(self.asset, "assetPublish")
        for ids in ([9999], [4443, 4443], [True], [], [-1]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                self.authorize({**self.state, "ids": ids})
        self.file.unlink()
        with self.assertRaises(OSError):
            self.authorize()

    def test_only_current_schema_is_accepted_and_duplicate_keys_are_rejected(self):
        original = copy.deepcopy(self.grant)
        for changes in ({"schemaVersion": 1}, {"schemaVersion": True}, {"statement": "old format"},
                        {"actions": {"statePublish": 1}}, {"target": "*"}):
            with self.subTest(changes=changes):
                self.grant = {**original, **changes}
                self.write_grant()
                with self.assertRaises(ValueError):
                    self.authorize()
        self.grant = original
        self.write_grant()
        self.file.write_bytes(self.file.read_bytes().replace(b'"schemaVersion": 2', b'"schemaVersion": 2, "schemaVersion": 2'))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.authorize()

    def test_target_base_refs_and_numeric_bank_namespace_cannot_drift(self):
        for changes in ({"target": "git@github.com:fixture/other.git"},
                        {"ref": "refs/heads/main"}, {"baseCommit": "f" * 40}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.authorize({**self.state, **changes})
        for changes in ({"target": "fixture/other"}, {"base": "develop"},
                        {"head": "main"}, {"head": "bank-icon-maintenance-evil/run-4443"},
                        {"head": "bank-icon-maintenance/run-4486"},
                        {"head": "bank-icon-maintenance/run-4443/extra"},
                        {"headRef": "refs/heads/main"}, {"materials": {"ref": "refs/heads/main"}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.authorize({**self.asset, **changes}, "assetPublish")
        for prefix in ("", "main", "../", "bad name/", "refs/../", "-x/", "bank.lock/"):
            with self.subTest(prefix=prefix):
                self.grant["headPrefix"] = prefix
                self.write_grant()
                with self.assertRaises(ValueError):
                    self.authorize()
        self.grant.update(headPrefix="main/", base="main/nested")
        self.write_grant()
        with self.assertRaisesRegex(ValueError, "overlaps"):
            self.authorize()

    def test_policy_file_boundary_and_size_are_preserved(self):
        for mode in (0o622, 0o666):
            self.file.chmod(mode)
            with self.assertRaisesRegex(ValueError, "owner-controlled"):
                self.authorize()
        self.write_grant()
        alias = self.root / "alias.json"
        alias.symlink_to(self.file)
        os.environ[sa.POLICY_ENV] = str(alias)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.authorize()
        os.environ[sa.POLICY_ENV] = "owner-policy.json"
        with self.assertRaisesRegex(ValueError, "absolute"):
            self.authorize()
        os.environ[sa.POLICY_ENV] = str(self.file)
        self.file.write_bytes(b" " * 65537)
        with self.assertRaisesRegex(ValueError, "small"):
            self.authorize()
        for parent in (self.repo, self.session):
            self.file = parent / "owner-policy.json"
            self.write_grant()
            with self.assertRaisesRegex(ValueError, "outside the checkout"):
                self.authorize()

    def test_package_and_evidence_checks_cannot_be_overruled(self):
        with self.assertRaisesRegex(ValueError, "package hash mismatch"):
            sa.authorize(self.session, self.state, "0" * 64, "statePublish")
        self.context_validator.side_effect = ValueError("altered session")
        with self.assertRaisesRegex(ValueError, "altered session"):
            self.authorize()

    def test_coverage_is_read_only_and_does_not_clear_transport_stops(self):
        key = sp.digest(encoded(self.state))
        for status in ("outcome-unknown", "publication-failed", "conflict", "ref-race"):
            for nested in (True, False):
                with self.subTest(status=status, nested=nested):
                    result = {"status": "prepared" if nested else status,
                              "packageHash": key, "package": self.state, "nextAction": "Existing recovery"}
                    if nested:
                        result["receipt"] = {"status": status}
                    original = copy.deepcopy(result)
                    shown = sa.describe_package(result, self.session, "statePublish")
                    self.assertEqual(shown["publicationAuthority"]["status"], "covered")
                    self.assertEqual(shown["status"], result["status"])
                    self.assertEqual(shown["nextAction"], result["nextAction"])
                    self.assertEqual(result, original)
        result = {"status": "awaiting-state-authorization", "package": self.state, "packageHash": key}
        self.assertEqual(sa.describe_package(result, self.session, "statePublish")["status"], "ready-to-publish")
        self.file.unlink()
        self.assertEqual(sa.describe_package(result, self.session, "statePublish")["publicationAuthority"]["status"], "blocked")

    def test_exact_recovery_authority_is_explicit_and_never_silently_falls_back(self):
        path = self.root / "exact.json"
        key = sp.digest(encoded(self.state))
        receipt = {"schemaVersion": 1, "scope": "state-publication", "decision": "authorize",
                   "packageHash": key, "target": self.state["target"], "ref": sp.REF,
                   "ids": self.state["ids"], "reviewer": "fixture-owner", "statement": "Specific recovery grant.",
                   "authorizedAt": "2020-01-01T00:00:00Z"}
        path.write_bytes(encoded(receipt))
        self.file.unlink()
        args = argparse.Namespace(authorization=str(path), run_authorization=None,
                                  session=str(self.session), package=key)
        self.assertEqual(sp.publication_authorization(args, self.state), receipt)
        receipt["ids"] = [9999]
        path.write_bytes(encoded(receipt))
        self.write_grant()
        with self.assertRaises(ValueError):
            sp.publication_authorization(args, self.state)

    def test_old_session_grant_is_rejected_not_migrated(self):
        path = self.root / "old-grant.json"
        path.write_bytes(encoded({"schemaVersion": 1, "scope": "bank-icon-maintenance-run"}))
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "schemaVersion 2"):
            run_authorization.authorize(path, self.session, self.state, "statePublish")
        self.assertEqual(path.read_bytes(), before)


class ContextRouting(unittest.TestCase):
    def test_normal_and_outcome_contexts_keep_existing_validators(self):
        normal, sync = types.ModuleType("state_results"), types.ModuleType("proposal_sync")
        normal._session_data = Mock(return_value={"fixture": "normal"})
        sync.authorization_context = Mock(return_value={"fixture": "outcomes"})
        with patch.dict(sys.modules, {"state_results": normal, "proposal_sync": sync}):
            self.assertEqual(sa._context(Path("/fixture"), {"ids": [10]}, "statePublish"), {"fixture": "normal"})
            normal._session_data.assert_called_once_with(Path("/fixture"), [10], {})
            package = {"reconciliation": {"kind": "proposal-outcomes"}, "ids": [10]}
            self.assertEqual(sa._context(Path("/fixture"), package, "statePublish"), {"fixture": "outcomes"})
            sync.authorization_context.assert_called_once()
            with self.assertRaises(ValueError):
                sa._context(Path("/fixture"), package, "assetPublish")


class StateTransport(PolicyFixture, unittest.TestCase):
    def state_fixture(self):
        self.remote = self.root / "remote.git"
        subprocess.run(["git", "init", "--bare", "-q", str(self.remote)], check=True)
        # Map only the controlled local transport to the same canonical test destination.
        normalize = sa.repository_target
        self.start_patch(patch.object(sa, "repository_target", side_effect=lambda target:
            "fixture/catalog" if target == str(self.remote) else normalize(target)))
        (self.session / "session.json").write_bytes(encoded(self.context))
        evidence = self.session / "bound-result.json"
        evidence.write_bytes(b"immutable fixture input")
        records = [{"id": id, "path": f"banks/state/{id}.json", "before": None,
                    "beforeSha256": None, "after": {"schemaVersion": 1, "lastAttemptAt": "2020-01-02T00:00:00Z",
                    "lastAttemptResult": "error" if index < 3 else "needs-human"}}
                   for index, id in enumerate(self.context["selectedIds"])]
        self.state.update(target=str(self.remote), stateHistory="unknown", observedRef=None,
                          records=records, results=[], bindings={str(evidence): sp.digest(evidence.read_bytes())})
        self.args = argparse.Namespace(command="state-publish", session=str(self.session),
                                       package=sp.digest(encoded(self.state)), authorization=None, run_authorization=None)
        self.publication_root = self.session / "state-publication"
        self.publication_root.mkdir()
        (self.publication_root / f"{self.args.package}.json").write_bytes(encoded(self.state))
        self.validation = self.start_patch(patch.object(sp, "validate_package"))
        def remote_record(repo, commit, path):
            if commit is None:
                return None, None
            response = sp.git(repo, "show", f"{commit}:{path}", check=False)
            if response.returncode:
                return None, None
            return json.loads(response.stdout), sp.digest(response.stdout)
        self.start_patch(patch.object(sp, "remote_record", side_effect=remote_record))
        self.start_patch(patch.object(sp, "sync_published", side_effect=lambda session, root, package, hash, receipt: receipt))

    def test_plain_publish_writes_error_and_needs_human_once_without_prompt(self):
        self.state_fixture()
        result = sp.dispatch(self.args)
        self.assertEqual(result["status"], "published")
        self.assertEqual(result["authorization"]["scope"], sa.USE_SCOPE)
        self.validation.assert_called_once()
        tip = sp.git(self.remote, "rev-parse", sp.REF).stdout
        for row in self.state["records"]:
            self.assertEqual(sp.git(self.remote, "show", f"{sp.REF}:{row['path']}").stdout, encoded(row["after"]))
            self.assertNotIn("lastVerifiedAt", row["after"])
        self.assertEqual(sp.dispatch(self.args)["status"], "published")
        self.assertEqual(sp.git(self.remote, "rev-parse", sp.REF).stdout, tip)

    def test_publish_without_any_authority_still_waits_and_show_never_pushes(self):
        self.state_fixture()
        os.environ.pop(sa.POLICY_ENV)
        self.assertEqual(sp.dispatch(self.args)["status"], "awaiting-state-authorization")
        self.write_grant()
        self.args.command = "state-show"
        self.assertEqual(sp.dispatch(self.args)["publicationAuthority"]["status"], "covered")
        self.assertNotEqual(sp.git(self.remote, "rev-parse", "--verify", sp.REF, check=False).returncode, 0)
        self.validation.assert_not_called()

    def test_policy_revoked_after_commit_prevents_the_state_push(self):
        self.state_fixture()
        original = sp.commit_package
        def revoke(*args, **kwargs):
            commit = original(*args, **kwargs)
            self.file.unlink()
            return commit
        with patch.object(sp, "commit_package", side_effect=revoke):
            with self.assertRaises(OSError):
                sp.dispatch(self.args)
        self.assertNotEqual(sp.git(self.remote, "rev-parse", "--verify", sp.REF, check=False).returncode, 0)

    def test_provider_denial_cannot_be_blindly_retried_under_standing_grant(self):
        self.state_fixture()
        sp.save_receipt(self.publication_root, self.args.package, {"status": "publication-failed"})
        with self.assertRaisesRegex(ValueError, "exact-package authorization"):
            sp.dispatch(self.args)
        self.validation.assert_not_called()

    def test_existing_evidence_gate_and_conflict_still_stop_publication(self):
        self.state_fixture()
        self.validation.side_effect = ValueError("fixture evidence changed")
        with self.assertRaisesRegex(ValueError, "evidence changed"):
            sp.dispatch(self.args)
        self.validation.side_effect = None
        with patch.object(sp, "first_conflict", return_value=4443):
            self.assertEqual(sp.dispatch(self.args)["status"], "conflict")
        self.assertNotEqual(sp.git(self.remote, "rev-parse", "--verify", sp.REF, check=False).returncode, 0)

    def test_lost_push_response_is_reconciled_read_only_even_after_grant_revocation(self):
        self.state_fixture()
        real_git = sp.git
        pushes = []
        def transport(repo, *args, **kwargs):
            result = real_git(repo, *args, **kwargs)
            if args and args[0] == "push":
                pushes.append(args)
                raise subprocess.TimeoutExpired("fixture push", 120)
            return result
        with patch.object(sp, "git", side_effect=transport):
            self.assertEqual(sp.dispatch(self.args)["status"], "outcome-unknown")
            self.file.unlink()
            self.args.command = "state-reconcile"
            self.assertEqual(sp.dispatch(self.args)["status"], "published")
        self.assertEqual(len(pushes), 1)


class AssetBoundary(PolicyFixture, unittest.TestCase):
    def asset_fixture(self):
        self.asset["schemaVersion"] = 2
        self.asset_root = self.session / "asset-publication"
        self.asset_root.mkdir()
        self.args = argparse.Namespace(session=str(self.session), authorization=None, run_authorization=None,
                                       package=sp.digest(encoded(self.asset)))
        self.refs, self.writes = {}, []
        self.created = False
        self.asset_validation = self.start_patch(patch.object(ap, "_validate_local", return_value=(self.root, {"folder": "banks/Fixture_4443-ru"})))
        self.start_patch(patch.object(ap, "_bank_base"))
        self.start_patch(patch.object(ap, "_fetch", return_value=self.asset["baseCommit"]))
        self.start_patch(patch.object(ap, "_ref", return_value=self.asset["baseCommit"]))
        self.start_patch(patch.object(github_pr, "repository", return_value={"isPrivate": True}))
        self.start_patch(patch.object(github_pr, "lookup", return_value=[]))
        self.start_patch(patch.object(github_pr, "open_for_path", return_value=[]))
        def reconcile(*args):
            return {"status": "mr-confirmed" if self.created else "not-published",
                    "materialPublished": self.asset["materials"]["ref"] in self.refs,
                    "assetPublished": self.asset["headRef"] in self.refs}
        self.start_patch(patch.object(ap, "reconcile", side_effect=reconcile))
        def push(repo, *args, **kwargs):
            self.writes.append(args)
            commit, ref = args[-1].split(":", 1)
            self.refs[ref] = commit
            return subprocess.CompletedProcess(args, 0, b"", b"")
        self.transport = self.start_patch(patch.object(ap, "_transport", side_effect=push))
        def create(*args):
            self.created = True
        self.create = self.start_patch(patch.object(github_pr, "create", side_effect=create))

    def test_plain_asset_publish_pushes_both_refs_and_creates_one_mr(self):
        self.asset_fixture()
        self.assertEqual(ap.publish(self.args, self.session, self.asset_root, self.asset)["status"], "mr-confirmed")
        self.assertEqual(len(self.writes), 2)
        self.create.assert_called_once()
        self.assertEqual(self.context_validator.call_count, 4)
        receipt = ap._receipt(self.asset_root, self.args.package)
        self.assertEqual(receipt["authorization"]["policy"], {"path": str(self.file)})
        ap.publish(self.args, self.session, self.asset_root, self.asset)
        self.assertEqual(len(self.writes), 2)
        self.create.assert_called_once()

    def test_revocation_between_refs_or_before_create_stops_next_write(self):
        self.asset_fixture()
        original_push = self.transport.side_effect
        def push(*args, **kwargs):
            result = original_push(*args, **kwargs)
            if len(self.writes) == 1:
                self.file.unlink()
            return result
        self.transport.side_effect = push
        with self.assertRaises(OSError):
            ap.publish(self.args, self.session, self.asset_root, self.asset)
        self.assertEqual(len(self.writes), 1)
        self.create.assert_not_called()
        self.write_grant()
        def second_push(*args, **kwargs):
            result = original_push(*args, **kwargs)
            self.file.unlink()
            return result
        self.transport.side_effect = second_push
        with self.assertRaises(OSError):
            ap.publish(self.args, self.session, self.asset_root, self.asset)
        self.assertEqual(len(self.writes), 2)
        self.create.assert_not_called()

    def test_unknown_result_and_provider_denial_never_duplicate_a_write(self):
        self.asset_fixture()
        with patch.object(ap, "reconcile", return_value={"status": "outcome-unknown"}):
            self.assertEqual(ap.publish(self.args, self.session, self.asset_root, self.asset)["status"], "outcome-unknown")
        self.transport.assert_not_called()
        self.create.assert_not_called()
        ap._save(self.asset_root, self.args.package, "publication-failed")
        with self.assertRaisesRegex(ValueError, "provider approval"):
            ap.publish(self.args, self.session, self.asset_root, self.asset)

    def test_state_only_policy_and_changed_evidence_do_not_allow_asset_writes(self):
        self.asset_fixture()
        self.grant["actions"]["assetPublish"] = False
        self.write_grant()
        with self.assertRaises(ValueError):
            ap.publish(self.args, self.session, self.asset_root, self.asset)
        self.transport.assert_not_called()
        self.grant["actions"]["assetPublish"] = True
        self.write_grant()
        self.asset_validation.side_effect = ValueError("fixture candidate changed")
        with self.assertRaisesRegex(ValueError, "candidate changed"):
            ap.publish(self.args, self.session, self.asset_root, self.asset)
        self.transport.assert_not_called()
        self.create.assert_not_called()


class PreflightAndOrchestration(PolicyFixture, unittest.TestCase):
    def test_operator_setup_creates_unpinned_policy_once_without_remote_writes(self):
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        home = self.root / "operator-home"
        home.mkdir()
        command = [sys.executable, str(ROOT / "scripts/configure-bank-publication.py"),
                   "--target", "fixture/catalog", "--output", str(home / "policy.json")]
        env = {**os.environ, "HOME": str(home)}
        result = subprocess.run(command, cwd=self.repo, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        import shlex
        settings = dict(shlex.split(line)[1].split("=", 1) for line in result.stdout.splitlines())
        self.assertEqual(set(settings), {sa.POLICY_ENV})
        with patch.dict(os.environ, settings):
            grant, source = sa.load(self.repo)
            self.assertEqual(grant["state"]["ref"], sp.REF)
            self.assertEqual(grant["asset"]["target"], "fixture/catalog")
            saved = Path(source["path"]).read_bytes()
        second = subprocess.run(command, cwd=self.repo, env=env, capture_output=True, text=True)
        self.assertNotEqual(second.returncode, 0)
        self.assertEqual(Path(settings[sa.POLICY_ENV]).read_bytes(), saved)
        self.assertEqual(sp.git(self.repo, "for-each-ref").stdout, b"")
        self.assertEqual(sp.git(self.repo, "status", "--porcelain").stdout, b"")

    def test_doctor_reports_policy_without_writes_and_invalid_file_blocks(self):
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "Fixture"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "fixture@example.invalid"], check=True)
        (self.repo / "banks").mkdir()
        (self.repo / "banks/fixture").write_text("not an icon fixture", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "fixture"], check=True)
        args = argparse.Namespace(repo=str(self.repo), session=None, base="HEAD",
                                  render=False, github=False, catalog=None)
        import session
        before = sp.git(self.repo, "for-each-ref", "--format=%(refname) %(objectname)").stdout
        with patch.object(session, "policy_at", return_value={}):
            result = environment.doctor(args)
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["publicationPolicy"]["status"], "configured")
            self.assertEqual(result["publicationPolicy"]["actions"], {**self.grant["actions"], "mergeApproved": False})
            self.file.write_text("{invalid", encoding="utf-8")
            result = environment.doctor(args)
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["publicationPolicy"]["status"], "blocked")
            os.environ.pop(sa.POLICY_ENV)
            self.assertEqual(environment.doctor(args)["publicationPolicy"], {"status": "not-configured"})
        self.assertEqual(sp.git(self.repo, "for-each-ref", "--format=%(refname) %(objectname)").stdout, before)
        self.assertEqual(sp.git(self.repo, "status", "--porcelain").stdout, b"")
        self.assertEqual(list(self.session.iterdir()), [])

    

    


if __name__ == "__main__":
    unittest.main()
