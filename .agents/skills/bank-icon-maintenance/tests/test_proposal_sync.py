"""Portable outcome sync: real Git graphs/state publication, synthetic GitHub facts.

No source search, renderer, GUI, hosted GitHub write, or historical session is used.
"""
import argparse
import copy
from contextlib import redirect_stderr
import io
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import asset_publication
import github_pr
import proposal_sync as sync
import rejection_review
import run_authorization
import session as sessions
import state_publication as state

ATTEMPT = "2020-01-01T01:00:00Z"
CHECKED = "2020-01-01T02:00:00Z"
OPENED = "2020-01-01T03:00:00Z"
CLOSED = "2020-01-02T00:00:00Z"
PATH = "banks/Example_10-ru/icon.svg"
A, B, C = [f'<svg xmlns="http://www.w3.org/2000/svg"><path fill="{color}"/></svg>\n'.encode()
           for color in ("red", "green", "blue")]
POLICY = {"recheckAfterDays": 90, "retryErrorAfterDays": 7, "retryUnconfirmedAfterDays": 30}


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(state.encoded(value))


class ProposalSync(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="proposal-sync-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "checkout"
        self.repo.mkdir()
        git(self.repo, "init", "-qb", "main")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        put(self.repo / PATH.replace("icon.svg", "info.json"), {"id": 10, "title": "Example", "countryCode": "RU"})
        missing_base = self._testMethodName.startswith("test_first_icon_")
        if not missing_base:
            self.write(PATH, A)
        self.base = self.commit()
        git(self.repo, "checkout", "-qb", "proposal")
        self.write(PATH, B)
        self.asset = self.commit()
        git(self.repo, "checkout", "-q", "main")
        git(self.repo, "merge", "--no-ff", "-qm", "merge fixture", "proposal")
        self.merge = git(self.repo, "rev-parse", "HEAD")
        self.remote = self.root / "remote.git"
        git(self.root, "init", "--bare", "-q", str(self.remote))
        git(self.repo, "remote", "add", "origin", str(self.remote))
        self.push_catalog(self.asset)
        self.original_mr = {"number": 1, "url": "https://github.com/test/banks/pull/1",
                            "state": "open", "head": "proposal", "headCommit": self.asset,
                            "base": "main", "baseCommit": self.base, "target": "test/banks",
                            "title": "Example icon", "bodySha256": "4" * 64, "observedAt": OPENED}
        self.before = {"schemaVersion": 1, "lastAttemptAt": ATTEMPT, "lastAttemptResult": "candidate",
                       "retainedCustomField": {"keep": True}, "proposals": [{
                           "status": "open", "baseIconHash": None if missing_base else state.digest(A),
                           "candidateIconHash": state.digest(B),
                           "checkedAt": ATTEMPT, "verification": {
                               "checkedAt": CHECKED, "sourceSha256": state.digest(B), "xmlSha256": "3" * 64,
                               "converter": "fixture-converter", "compose": "fixture-compose"},
                           "assetPublication": {"path": "asset-publication/not-on-this-host.json", "sha256": "2" * 64},
                           "mr": self.original_mr}]}
        self.write_state({10: self.before})
        self.observed = {key: value for key, value in self.original_mr.items() if key != "observedAt"}
        self.observed.update(state="merged", mergeCommit=self.merge, mergedAt=CLOSED, closedAt=CLOSED,
                             baseCommit=self.merge)
        self.lookup = patch.object(github_pr, "outcome", side_effect=lambda *_: copy.deepcopy(self.observed)).start()
        self.addCleanup(patch.stopall)
        # Redirect only the network destination. Real credential-wrapper Git calls,
        # ls-remote/fetch, object reads, ancestry and publication still execute.
        patch.object(asset_publication, "_remote", return_value=str(self.remote)).start()
        # Existing outcome tests have no discussion unless they explicitly bind
        # a legacy comment. New closed-review tests exercise the real reader.
        patch.object(rejection_review, "read_thread", return_value=[]).start()
        self.counter = 0

    def write(self, path, content):
        file = self.repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content)

    def commit(self):
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "fixture")
        return git(self.repo, "rev-parse", "HEAD")

    def push_catalog(self, head=None):
        # Force updates are fixture setup in our disposable bare repository only.
        git(self.repo, "push", "-q", "--force", "origin", "HEAD:refs/heads/main",
            f"{head or self.asset}:refs/pull/1/head")

    def state_head(self):
        return git(self.remote, "rev-parse", state.REF)

    def write_state(self, values):
        check = subprocess.run(["git", "-C", str(self.remote), "rev-parse", "--verify", state.REF],
                               capture_output=True, text=True)
        parent = check.stdout.strip() if check.returncode == 0 else None
        if parent:
            git(self.repo, "fetch", "-q", "origin", state.REF)
        records = [{"path": f"banks/state/{id}.json", "after": value} for id, value in values.items()]
        sha = state.commit_package(self.repo, self.root, {"records": records}, "fixture", parent)
        git(self.repo, "push", "-q", "origin", f"{sha}:{state.REF}")
        return sha

    def prepare(self, *, decisions=None, remote=None):
        self.counter += 1
        self.sync_dir = self.root / f"sync-{self.counter}"
        args = argparse.Namespace(command="state-prepare-outcomes", repo=str(self.repo),
                                  session=str(self.sync_dir), remote=remote, decisions=decisions)
        self.result = state.dispatch(args)
        return self.result

    def publish(self, *, authorization=True, run=False, result=None):
        result = result or self.result
        args = argparse.Namespace(command="state-publish", session=str(self.sync_dir),
                                  package=result["packageHash"], authorization=None, run_authorization=None)
        if authorization:
            package = result["package"]
            self.authority = self.root / "authority.json"
            if run:
                self.authority = self.root / "publication-permission.json"
                permission = {"schemaVersion": 2, "scope": "bank-icon-maintenance-run", "decision": "authorize",
                    "repo": str(self.repo), "runRoot": str(self.root), "ids": package["ids"],
                    "permissions": {"schemaVersion": 2, "target": "test/banks", "base": "main",
                        "headPrefix": "test/", "actions": {"assetPublish": False, "statePublish": True}},
                    "authorizer": "Test", "statement": "Publish these test outcome facts only.", "authorizedAt": OPENED}
                args.run_authorization = str(self.authority)
            else:
                permission = {"schemaVersion": 1, "scope": "state-publication", "decision": "authorize",
                    "packageHash": result["packageHash"], "target": str(self.remote), "ref": state.REF,
                    "ids": package["ids"], "reviewer": "Test", "statement": "Publish this test package.", "authorizedAt": OPENED}
                args.authorization = str(self.authority)
            put(self.authority, permission)
        if run:
            import standing_authorization as standing
            original = standing.repository_target
            # Only this disposable transport maps to the test's canonical destination.
            with patch.object(standing, 'repository_target', side_effect=lambda value:
                              'test/banks' if value == str(self.remote) else original(value)):
                return state.dispatch(args)
        return state.dispatch(args)

    def row(self):
        return self.result["package"]["records"][0]

    def resave(self, package):
        content = state.encoded(package)
        digest = state.digest(content)
        state.save_new(self.sync_dir / "state-publication" / f"{digest}.json", content)
        return {"package": package, "packageHash": digest}

    def reject(self):
        self.observed.update(state="closed", mergeCommit=None, mergedAt=None)

    def decision(self, **changes):
        return {"id": 10, "mr": {key: self.original_mr[key] for key in ("number", "url")},
                "commentUrl": self.original_mr["url"] + "#issuecomment-7", "category": "technical",
                "explanation": "The edge is clipped", **changes}

    def reason_fixture(self):
        self.comment = {"url": self.decision()["commentUrl"], "body": "The edge is clipped; please fix it.",
                        "author": "reviewer", "createdAt": CLOSED, "updatedAt": CLOSED, "kind": "issue-comment"}
        patch.object(github_pr, "comment", side_effect=lambda *_: copy.deepcopy(self.comment)).start()
        file = self.root / "decisions.json"
        put(file, [self.decision()])
        return str(file)

    def test_merged_without_original_session_publishes_exact_verification_and_preserves_history(self):
        source = self.state_head()
        self.prepare()
        self.assertEqual(self.state_head(), source)
        after = self.row()["after"]
        self.assertEqual(after["lastVerifiedAt"], CHECKED)
        self.assertEqual(after["verifiedIconHash"], state.digest(B))
        self.assertEqual(after["lastAttemptAt"], ATTEMPT)
        self.assertEqual(after["proposals"][-1]["mrHistory"], [self.original_mr])
        self.assertEqual(after["retainedCustomField"], {"keep": True})
        catalog = git(self.repo, "rev-parse", "HEAD")
        response = self.publish()
        self.assertEqual(response["status"], "published")
        self.assertEqual(response["localSync"]["status"], "synced")
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), catalog)
        saved, _ = state.remote_record(self.remote, self.state_head(), "banks/state/10.json")
        self.assertEqual(saved, after)
        _, eligible, _ = sessions.state_decision(saved, state.digest(B),
            sessions.parse_time("2020-01-10T00:00:00Z"), POLICY, False)
        self.assertFalse(eligible)

    def test_repeat_publication_and_next_scan_are_idempotent(self):
        self.prepare()
        self.publish()
        head = self.state_head()
        self.assertEqual(self.publish()["status"], "published")
        self.assertEqual(self.state_head(), head)
        self.lookup.reset_mock()
        self.assertEqual(self.prepare()["status"], "no-state-changes")
        self.lookup.assert_not_called()
        self.assertEqual(self.state_head(), head)

    def test_open_mr_is_read_once_without_new_state_or_polling(self):
        self.observed.update(state="open", mergeCommit=None, mergedAt=None, closedAt=None)
        head = self.state_head()
        result = self.prepare()
        self.assertIsNone(result["packageHash"])
        self.assertEqual(result["open"], [{"id": 10, "mr": self.original_mr["url"]}])
        self.lookup.assert_called_once_with("test/banks", 1)
        self.assertEqual(head, self.state_head())

    def test_closed_without_reason_records_rejection_but_no_freshness(self):
        self.reject()
        self.prepare()
        after = self.row()["after"]
        self.assertEqual(after["proposals"][-1]["status"], "rejected")
        self.assertIsNone(after["proposals"][-1]["rejectionReason"])
        self.assertNotIn("lastVerifiedAt", after)
        self.assertEqual(after["lastAttemptAt"], ATTEMPT)
        self.publish()
        point, eligible, reason = sessions.state_decision(after, state.digest(A),
            sessions.parse_time("2020-01-10T00:00:00Z"), POLICY, False)
        self.assertEqual(point, "needs-human")
        self.assertFalse(eligible)
        self.assertEqual(after["proposals"][-1]["rejectionReview"]["judgement"]["action"], "no-comment")
        self.assertIn("discussion was checked", reason)

    def test_closed_with_exact_comment_preserves_source_reason(self):
        self.reject()
        self.prepare(decisions=self.reason_fixture())
        reason = self.row()["after"]["proposals"][-1]["rejectionReason"]
        self.assertEqual(reason["comment"], self.comment)
        self.assertEqual(reason["candidateIconHash"], state.digest(B))
        self.assertEqual(self.publish()["status"], "published")

    def test_unknown_rejection_can_be_clarified_on_new_host_without_old_session(self):
        self.reject()
        self.prepare()
        self.publish()
        first = self.row()["after"]
        old_dir = self.sync_dir
        self.prepare(decisions=self.reason_fixture())
        self.assertNotEqual(old_dir, self.sync_dir)
        self.assertEqual(self.row()["before"], first)
        self.assertEqual(self.row()["after"]["proposals"][-1]["mrHistory"], [self.original_mr])
        self.assertEqual(self.publish()["status"], "published")

    def test_wrong_comment_binding_or_unsupported_explanation_is_not_a_reason(self):
        self.reject()
        decisions = self.reason_fixture()
        put(Path(decisions), [self.decision(explanation="An invented reason")])
        result = self.prepare(decisions=decisions)
        self.assertIsNone(result["packageHash"])
        self.assertTrue(result["stops"])

    def test_changed_comment_after_prepare_requires_new_package(self):
        self.reject()
        self.prepare(decisions=self.reason_fixture())
        head = self.state_head()
        self.comment["body"] += " Changed advice."
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(self.state_head(), head)

    def test_mr_reopened_after_prepare_does_not_publish_terminal_state(self):
        self.prepare()
        head = self.state_head()
        self.observed.update(state="open", closedAt=None, mergedAt=None, mergeCommit=None)
        with self.assertRaisesRegex(ValueError, "outcome changed"):
            self.publish()
        self.assertEqual(self.state_head(), head)

    def test_provider_identity_changes_are_isolated_not_inferred(self):
        self.observed["bodySha256"] = "f" * 64
        result = self.prepare()
        self.assertIsNone(result["packageHash"])
        self.assertIn("identity", result["stops"][0]["reason"])

    def test_github_failure_is_not_closed_or_absent(self):
        self.lookup.side_effect = ValueError("provider unavailable")
        result = self.prepare()
        self.assertIsNone(result["packageHash"])
        self.assertIn("provider unavailable", result["stops"][0]["reason"])
        self.lookup.assert_called_once()

    def test_state_read_failure_raises_instead_of_offline_fallback(self):
        with patch.object(state, "fetch_head", side_effect=ValueError("transport failure")):
            with self.assertRaisesRegex(ValueError, "transport failure"):
                self.prepare()
        self.lookup.assert_not_called()

    def test_absent_state_is_unknown_not_a_new_empty_history(self):
        git(self.remote, "update-ref", "-d", state.REF)
        result = self.prepare()
        self.assertEqual(result["status"], "absent")
        self.assertEqual(result["stateHistory"], "unknown")
        self.lookup.assert_not_called()

    def test_no_origin_requires_no_extra_user_input_for_local_mode(self):
        git(self.repo, "remote", "remove", "origin")
        self.assertEqual(self.prepare()["status"], "no-origin")
        self.assertFalse(self.sync_dir.exists())

    def test_multiple_origins_push_urls_do_not_pick_a_target(self):
        git(self.repo, "config", "--add", "remote.origin.pushurl", str(self.remote))
        git(self.repo, "config", "--add", "remote.origin.pushurl", str(self.remote))
        with self.assertRaisesRegex(ValueError, "exactly one"):
            self.prepare()

    def test_pending_local_proposal_is_not_fabricated_as_a_published_mr(self):
        self.before["proposals"][-1].update(status="pending")
        self.before["proposals"][-1].pop("mr")
        self.write_state({10: self.before})
        result = self.prepare()
        self.assertIsNone(result["packageHash"])
        self.assertTrue(result["stops"])
        self.lookup.assert_not_called()

    def test_invalid_other_bank_does_not_prevent_valid_outcome(self):
        self.write_state({20: {"schemaVersion": True}})
        self.prepare()
        self.assertEqual(self.result["package"]["ids"], [10])
        self.assertEqual(self.result["stops"][0]["id"], 20)
        self.assertEqual(self.publish()["status"], "published")
        self.assertEqual(json.loads(git(self.remote, "show", f"{state.REF}:banks/state/20.json")), {"schemaVersion": True})

    def test_candidate_hash_mismatch_cannot_transfer_verification(self):
        self.before["proposals"][-1]["candidateIconHash"] = "f" * 64
        self.write_state({10: self.before})
        result = self.prepare()
        self.assertIsNone(result["packageHash"])

    def test_missing_verification_records_acceptance_without_inventing_a_check(self):
        self.before["proposals"][-1].pop("verification")
        self.write_state({10: self.before})
        self.prepare()
        after = self.row()["after"]
        self.assertEqual(after["proposals"][-1]["status"], "accepted")
        self.assertNotIn("lastVerifiedAt", after)
        self.assertEqual(self.publish()["status"], "published")

    def test_changed_current_icon_records_acceptance_and_keeps_it_due_for_check(self):
        self.write(PATH, C)
        self.commit()
        self.push_catalog()
        self.observed["baseCommit"] = git(self.repo, "rev-parse", "HEAD")
        self.prepare()
        after = self.row()["after"]
        self.assertEqual(after["proposals"][-1]["acceptedIconHash"], state.digest(B))
        self.assertNotIn("lastVerifiedAt", after)
        self.assertTrue(sessions.state_decision(after, state.digest(C),
            sessions.parse_time("2020-01-10T00:00:00Z"), POLICY, False)[1])
        self.publish()

    def test_changed_current_metadata_does_not_inherit_candidate_freshness(self):
        put(self.repo / PATH.replace("icon.svg", "info.json"), {"id": 10, "title": "Changed name"})
        self.commit()
        self.push_catalog()
        self.prepare()
        self.assertNotIn("lastVerifiedAt", self.row()["after"])
        self.publish()

    def test_duplicate_current_id_stops_only_this_outcome(self):
        put(self.repo / "banks/Duplicate_11-ru/info.json", {"id": 10, "title": "Duplicate"})
        self.commit()
        self.push_catalog()
        result = self.prepare()
        self.assertIsNone(result["packageHash"])
        self.assertIn("duplicate", result["stops"][0]["reason"])

    def test_amended_descendant_svg_is_accepted_without_old_byte_verification(self):
        git(self.repo, "checkout", "-q", "proposal")
        self.write(PATH, C)
        new_head = self.commit()
        git(self.repo, "checkout", "-qb", "amended-main", self.base)
        git(self.repo, "merge", "--no-ff", "-qm", "amended fixture", new_head)
        merge = git(self.repo, "rev-parse", "HEAD")
        self.push_catalog(new_head)
        self.observed.update(headCommit=new_head, mergeCommit=merge, baseCommit=merge)
        self.prepare()
        self.assertEqual(self.row()["after"]["proposals"][-1]["acceptedIconHash"], state.digest(C))
        self.assertNotIn("lastVerifiedAt", self.row()["after"])
        self.publish()

    def test_amended_head_with_unrelated_file_is_rejected(self):
        git(self.repo, "checkout", "-q", "proposal")
        self.write("unrelated.txt", b"not a bank icon")
        new_head = self.commit()
        git(self.repo, "checkout", "-qb", "amended-main", self.base)
        git(self.repo, "merge", "--no-ff", "-qm", "amended fixture", new_head)
        merge = git(self.repo, "rev-parse", "HEAD")
        self.push_catalog(new_head)
        self.observed.update(headCommit=new_head, mergeCommit=merge, baseCommit=merge)
        result = self.prepare()
        self.assertIsNone(result["packageHash"])
        self.assertIn("more than", result["stops"][0]["reason"])

    def test_merge_not_in_target_history_is_not_accepted(self):
        git(self.repo, "checkout", "--detach", "-q", self.base)
        self.push_catalog()
        result = self.prepare()
        self.assertIsNone(result["packageHash"])
        self.assertIn("not in current", result["stops"][0]["reason"])

    def test_changed_current_after_prepare_does_not_publish_stale_verification(self):
        self.prepare()
        before = self.state_head()
        self.write(PATH, C)
        self.commit()
        self.push_catalog()
        with self.assertRaisesRegex(ValueError, "current bank changed"):
            self.publish()
        self.assertEqual(self.state_head(), before)

    def test_unrelated_target_commit_can_advance_without_repeating_a_check(self):
        self.prepare()
        checked = self.row()["after"]["lastVerifiedAt"]
        self.write("unrelated.txt", b"new implementation")
        self.commit()
        self.push_catalog()
        self.observed["baseCommit"] = git(self.repo, "rev-parse", "HEAD")
        response = self.publish()
        self.assertEqual(response["status"], "published")
        self.assertEqual(response["package"]["records"][0]["after"]["lastVerifiedAt"], checked)

    def test_affected_remote_state_conflict_is_not_overwritten(self):
        self.prepare()
        changed = {**self.before, "concurrent": True}
        before = self.write_state({10: changed})
        response = self.publish()
        self.assertEqual(response["status"], "conflict")
        self.assertEqual(self.state_head(), before)

    def test_unrelated_state_commit_is_preserved_by_publication(self):
        self.prepare()
        self.write_state({20: {"schemaVersion": 1, "retained": True}})
        self.assertEqual(self.publish()["status"], "published")
        saved, _ = state.remote_record(self.remote, self.state_head(), "banks/state/20.json")
        self.assertEqual(saved, {"schemaVersion": 1, "retained": True})

    def test_rewritten_after_or_source_before_cannot_be_published(self):
        self.prepare()
        remote = self.state_head()
        for field in ("before", "after"):
            with self.subTest(field=field):
                package = copy.deepcopy(self.result["package"])
                package["records"][0][field]["invented"] = True
                result = self.resave(package)
                response = None
                try:
                    response = self.publish(result=result)
                except ValueError:
                    pass
                else:
                    self.assertEqual(response["status"], "conflict")
                self.assertEqual(remote, self.state_head())

    def test_missing_authority_only_shows_package(self):
        self.prepare()
        remote = self.state_head()
        self.assertEqual(self.publish(authorization=False)["status"], "awaiting-state-authorization")
        self.assertEqual(remote, self.state_head())

    def test_scoped_authority_works_without_invented_icon_or_merge_approval(self):
        self.prepare()
        self.assertEqual(self.publish(run=True)["status"], "published")
        permission = json.loads(self.authority.read_text())
        self.assertFalse(permission["permissions"]["actions"]["assetPublish"])
        with self.assertRaisesRegex(ValueError, "cannot authorize asset"):
            run_authorization.authorize(self.authority, self.sync_dir, self.result["package"], "assetPublish")

    def test_previous_run_permission_is_not_silently_reused(self):
        self.prepare()
        self.publish(run=True)
        value = json.loads(self.authority.read_text())
        other = self.root / "previous-task"
        other.mkdir()
        value["runRoot"] = str(other)
        put(self.authority, value)
        with self.assertRaisesRegex(ValueError, "another task root"):
            run_authorization.authorize(self.authority, self.sync_dir, self.result["package"], "statePublish")

    def test_landed_push_timeout_is_reconciled_without_another_write(self):
        self.prepare()
        original = state.git
        writes = []
        def lost_response(repo, *args, **kwargs):
            response = original(repo, *args, **kwargs)
            if args and args[0] == "push":
                writes.append(args)
                raise subprocess.TimeoutExpired(args, 120)
            return response
        with patch.object(state, "git", side_effect=lost_response):
            self.assertEqual(self.publish()["status"], "outcome-unknown")
            args = argparse.Namespace(command="state-reconcile", session=str(self.sync_dir), package=self.result["packageHash"])
            self.assertEqual(state.dispatch(args)["status"], "published")
        self.assertEqual(len(writes), 1)

    def test_symlinked_context_or_existing_session_cannot_be_replaced(self):
        self.prepare()
        context = self.sync_dir / "session.json"
        saved = self.root / "saved.json"
        context.rename(saved)
        context.symlink_to(saved)
        head = self.state_head()
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(self.state_head(), head)
        with self.assertRaises(ValueError):
            sync.prepare(argparse.Namespace(repo=str(self.repo), session=str(self.sync_dir), remote=None, decisions=None))

    def test_command_registered_with_explicit_nonpublishing_arguments(self):
        parser = argparse.ArgumentParser()
        commands = parser.add_subparsers(dest="command", required=True)
        state.add_subcommands(commands)
        args = parser.parse_args(["state-prepare-outcomes", "--repo", str(self.repo), "--session", str(self.root / "parser")])
        self.assertEqual(state.dispatch(args)["status"], "awaiting-state-authorization")
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["state-prepare-outcomes", "--repo", str(self.repo), "--session", "x", "--offline"])


    

    def test_reclosed_mr_cannot_silently_clarify_the_old_closure(self):
        self.reject()
        self.prepare()
        self.publish()
        self.observed["closedAt"] = "2020-01-03T00:00:00Z"
        result = self.prepare(decisions=self.reason_fixture())
        self.assertIsNone(result["packageHash"])
        self.assertIn("terminal MR changed", result["stops"][0]["reason"])

    def test_verification_times_must_be_real_historical_checks(self):
        self.before["proposals"][-1]["verification"]["checkedAt"] = "2021-01-01T00:00:00Z"
        self.write_state({10: self.before})
        result = self.prepare()
        self.assertIsNone(result["packageHash"])
        self.assertIn("verification times", result["stops"][0]["reason"])

    def test_later_existing_verification_is_not_rolled_back(self):
        self.before.update(lastVerifiedAt="2020-01-01T04:00:00Z", verifiedIconHash=state.digest(B))
        self.write_state({10: self.before})
        self.prepare()
        self.assertEqual(self.row()["after"]["lastVerifiedAt"], "2020-01-01T04:00:00Z")
        self.publish()

    def test_git_read_timeout_retains_a_stop_without_retry(self):
        calls = []
        def unavailable(*args, **kwargs):
            calls.append(args)
            raise subprocess.TimeoutExpired(args, 60)
        with patch.object(asset_publication, "_transport", side_effect=unavailable):
            result = self.prepare()
        self.assertIsNone(result["packageHash"])
        self.assertIn("timed out", result["stops"][0]["reason"])
        self.assertEqual(len(calls), 1)

    def test_new_sync_directory_cannot_be_inside_source_checkout(self):
        args = argparse.Namespace(repo=str(self.repo), session=str(self.repo / "unsafe"), remote=None, decisions=None)
        with self.assertRaisesRegex(ValueError, "outside the checkout"):
            sync.prepare(args)
        self.assertFalse((self.repo / "unsafe").exists())

    def test_duplicate_or_boolean_decision_ids_are_not_accepted(self):
        file = self.root / "bad-decisions.json"
        for rows in ([self.decision(), self.decision()], [self.decision(id=True)]):
            with self.subTest(rows=rows):
                put(file, rows)
                with self.assertRaises(ValueError):
                    self.prepare(decisions=str(file))

    def test_tampered_sync_context_invalidates_the_package(self):
        self.prepare()
        file = self.sync_dir / "session.json"
        value = json.loads(file.read_text())
        value["selectedIds"] = [10, 20]
        put(file, value)
        head = self.state_head()
        with self.assertRaisesRegex(ValueError, "input changed"):
            self.publish()
        self.assertEqual(self.state_head(), head)


    def test_first_icon_merged_proves_absent_parent_and_publishes_real_verification(self):
        self.prepare()
        self.assertEqual(self.result["stops"], [])
        after = self.row()["after"]
        self.assertIsNone(after["proposals"][-1]["baseIconHash"])
        self.assertEqual(after["proposals"][-1]["acceptedIconHash"], state.digest(B))
        self.assertEqual(after["verifiedIconHash"], state.digest(B))
        self.assertEqual(self.publish()["status"], "published")

    def test_first_icon_closed_preserves_absence_without_verification(self):
        self.reject()
        self.prepare()
        after = self.row()["after"]
        self.assertEqual(after["proposals"][-1]["status"], "rejected")
        self.assertIsNone(after["proposals"][-1]["baseIconHash"])
        self.assertNotIn("lastVerifiedAt", after)
        self.assertNotIn("verifiedIconHash", after)
        self.assertEqual(self.publish()["status"], "published")

    def test_first_icon_deleted_after_merge_does_not_retain_candidate_verification(self):
        (self.repo / PATH).unlink()
        self.commit()
        self.push_catalog()
        self.prepare()
        after = self.row()["after"]
        self.assertIsNone(after["proposals"][-1]["outcomeSync"]["proof"]["current"]["iconHash"])
        self.assertNotIn("lastVerifiedAt", after)
        self.assertEqual(after["proposals"][-1]["outcomeSync"]["currentVerification"], "requires-current-check")
        self.assertEqual(self.publish()["status"], "published")

    def test_null_base_requires_actual_absent_parent(self):
        self.before["proposals"][-1]["baseIconHash"] = None
        self.write_state({10: self.before})
        self.prepare()
        self.assertIsNone(self.result["packageHash"])
        self.assertEqual([row["id"] for row in self.result["stops"]], [10])

    def test_missing_or_malformed_original_hash_is_not_explicit_absence(self):
        for value in ("", False, 0):
            with self.subTest(value=value):
                self.before["proposals"][-1]["baseIconHash"] = value
                self.write_state({10: self.before})
                self.prepare()
                self.assertIsNone(self.result["packageHash"])
                self.assertEqual([row["id"] for row in self.result["stops"]], [10])
        del self.before["proposals"][-1]["baseIconHash"]
        self.write_state({10: self.before})
        self.prepare()
        self.assertIsNone(self.result["packageHash"])
        self.assertEqual([row["id"] for row in self.result["stops"]], [10])

if __name__ == "__main__":
    unittest.main()
