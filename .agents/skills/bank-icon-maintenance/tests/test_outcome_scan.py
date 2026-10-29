"""Resumption, bounded batches and crash recovery on disposable Git/provider fixtures."""
import argparse
import copy
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import subprocess
import sys
import time
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import github_pr
import operation_budget as budget
import outcome_scan
import proposal_sync
import state_publication as state
import test_proposal_sync as fixture


class OutcomeScan(unittest.TestCase):
    # Reuse setup/helpers only, not the original suite's test methods.
    setUp = fixture.ProposalSync.setUp
    write = fixture.ProposalSync.write
    commit = fixture.ProposalSync.commit
    push_catalog = fixture.ProposalSync.push_catalog
    state_head = fixture.ProposalSync.state_head
    write_state = fixture.ProposalSync.write_state
    publish = fixture.ProposalSync.publish
    resave = fixture.ProposalSync.resave

    def many(self, count=3, *, opened=False):
        values = {}
        for i in range(1, count + 1):
            record = copy.deepcopy(self.before)
            record["proposals"][-1]["mr"].update(number=i, url=f"https://github.com/test/banks/pull/{i}")
            values[i * 10] = record
        self.write_state(values)
        def observed(target, number):
            value = copy.deepcopy(self.observed)
            value.update(number=number, url=f"https://github.com/test/banks/pull/{number}",
                         state="open" if opened else "closed", mergeCommit=None, mergedAt=None,
                         closedAt=None if opened else fixture.CLOSED)
            return value
        self.lookup.side_effect = observed
        return observed

    def scan(self, *, resume=False, limit=1, seconds=180, **extra):
        self.sync_dir = self.root / "resumable"
        args = argparse.Namespace(command="state-prepare-outcomes", repo=str(self.repo),
                                  session=str(self.sync_dir), remote=None, decisions=None,
                                  resume=resume, max_proposals=limit, max_seconds=seconds, **extra)
        with redirect_stderr(io.StringIO()):
            self.result = state.dispatch(args)
        return self.result

    def test_batches_publish_separately_and_finish_without_reprocessing(self):
        self.many()
        first = self.scan()
        self.assertEqual(first["package"]["ids"], [10])
        self.assertFalse(first["readyForSelection"])
        source = first["sourceStateCommit"]
        root_context = (self.sync_dir / "session.json").read_bytes()
        self.assertEqual(self.publish()["status"], "published")
        second = self.scan(resume=True)
        self.assertEqual(second["package"]["ids"], [20])
        self.assertEqual(second["sourceStateCommit"], source)
        self.assertEqual((self.sync_dir / "session.json").read_bytes(), root_context)
        self.assertEqual(self.publish()["status"], "published")
        third = self.scan(resume=True)
        self.assertEqual(third["package"]["ids"], [30])
        self.assertTrue(third["scanComplete"])
        self.assertFalse(third["readyForSelection"])
        self.publish()
        self.lookup.reset_mock()
        final = self.scan(resume=True)
        self.assertTrue(final["readyForSelection"])
        self.assertIsNone(final["packageHash"])
        self.lookup.assert_not_called()
        for id in (10, 20, 30):
            saved, _ = state.remote_record(self.remote, self.state_head(), f"banks/state/{id}.json")
            self.assertEqual(saved["proposals"][-1]["status"], "rejected")
            self.assertNotIn("lastVerifiedAt", saved)

    def test_unpublished_package_stops_resume_without_any_provider_read(self):
        self.many()
        first = self.scan()
        self.lookup.reset_mock()
        second = self.scan(resume=True)
        self.assertEqual(second["packageHash"], first["packageHash"])
        self.assertEqual(second["processedPaths"], 1)
        self.lookup.assert_not_called()
        self.assertEqual(second["status"], "awaiting-state-authorization")

    def test_unknown_publication_is_not_bypassed_or_retried(self):
        self.many()
        first = self.scan()
        state.save_receipt(self.sync_dir / "state-publication", first["packageHash"],
                           {"status": "outcome-unknown", "error": "response lost"})
        self.lookup.reset_mock()
        self.assertEqual(self.scan(resume=True)["status"], "outcome-unknown")
        self.lookup.assert_not_called()

    def test_hard_interruption_keeps_completed_bank_and_resume_does_not_read_it_again(self):
        observed = self.many(2)
        def interrupt(target, number):
            if number == 2:
                raise KeyboardInterrupt()
            return observed(target, number)
        self.lookup.side_effect = interrupt
        with self.assertRaises(KeyboardInterrupt):
            self.scan(limit=5)
        rows = list((self.sync_dir / "outcome-scan/rows").glob("*.json"))
        self.assertEqual(len(rows), 1)
        original = rows[0].read_bytes()
        self.lookup.side_effect = observed
        self.lookup.reset_mock()
        result = self.scan(resume=True, limit=5)
        self.lookup.assert_called_once_with("test/banks", 2)
        self.assertEqual(result["package"]["ids"], [10, 20])
        self.assertEqual(rows[0].read_bytes(), original)
        self.assertEqual(self.publish()["status"], "published")

    def test_crash_after_package_creation_reuses_exact_observations_and_package(self):
        self.many(1)
        original = outcome_scan._save_hashed
        def crash(directory, prefix, value):
            if directory.name == "batches":
                raise KeyboardInterrupt()
            return original(directory, prefix, value)
        with patch.object(outcome_scan, "_save_hashed", side_effect=crash), self.assertRaises(KeyboardInterrupt):
            self.scan()
        files = list((self.sync_dir / "state-publication").glob("*.json"))
        self.assertEqual(len(files), 1)
        content = files[0].read_bytes()
        self.lookup.reset_mock()
        resumed = self.scan(resume=True)
        self.lookup.assert_not_called()
        self.assertEqual(resumed["packageHash"], files[0].stem)
        self.assertEqual(files[0].read_bytes(), content)
        self.assertEqual(self.publish()["status"], "published")

    def test_lower_limit_after_crash_still_produces_small_packages(self):
        observed = self.many(3)
        def interrupt(target, number):
            if number == 3:
                raise KeyboardInterrupt()
            return observed(target, number)
        self.lookup.side_effect = interrupt
        with self.assertRaises(KeyboardInterrupt):
            self.scan(limit=5)
        self.lookup.side_effect = observed
        first = self.scan(resume=True, limit=1)
        self.assertEqual(first["package"]["ids"], [10])
        self.assertEqual(first["unpackagedPaths"], 1)
        self.publish()
        second = self.scan(resume=True, limit=1)
        self.assertEqual(second["package"]["ids"], [20])
        self.publish()
        self.assertEqual(self.scan(resume=True, limit=1)["package"]["ids"], [30])

    def test_no_origin_or_absent_state_resume_does_not_fabricate_source_pin(self):
        fixture.git(self.remote, "update-ref", "-d", state.REF)
        first = self.scan()
        self.assertEqual(first["status"], "absent")
        self.assertEqual(self.scan(resume=True)["status"], "absent")

    def test_changed_context_file_is_rejected_before_publication(self):
        self.scan()
        link = self.result["package"]["reconciliation"]["context"]
        (self.sync_dir / link["path"]).write_text("{}")
        before = self.state_head()
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(self.state_head(), before)

    def test_deadline_preserves_completed_results_without_recording_unfinished_bank_as_error(self):
        observed = self.many(2)
        self.lookup.side_effect = lambda target, number: (observed(target, number) if number == 1 else
                                                         (_ for _ in ()).throw(budget.Exhausted()))
        result = self.scan(limit=5)
        self.assertEqual(result["package"]["ids"], [10])
        self.assertEqual(result["stops"], [])
        self.assertEqual(result["remainingPaths"], 1)
        progress = json.loads((self.sync_dir / "progress.json").read_text())
        self.assertEqual(progress["stage"], "budget-exhausted")
        self.lookup.side_effect = observed
        self.publish()
        self.assertEqual(self.scan(resume=True)["package"]["ids"], [20])

    def test_deadline_during_initial_fetch_is_resumable(self):
        self.many(1, opened=True)
        with patch.object(state, "fetch_head", side_effect=budget.Exhausted):
            first = self.scan()
        self.assertEqual(first["status"], "incomplete")
        self.assertFalse(first["readyForSelection"])
        self.assertTrue(self.scan(resume=True)["readyForSelection"])

    def test_open_batches_do_not_require_publication_and_never_claim_partial_scan_complete(self):
        self.many(3, opened=True)
        for i in range(3):
            result = self.scan(resume=i != 0)
            self.assertIsNone(result["packageHash"])
            self.assertEqual(result["readyForSelection"], i == 2)
            self.assertEqual(len(result["open"]), i + 1)
        self.assertEqual(self.lookup.call_count, 3)

    def test_stale_completed_observation_is_rechecked_before_push(self):
        self.many(1)
        self.scan()
        before = self.state_head()
        self.lookup.side_effect = self.many(1, opened=True)
        before = self.state_head()
        with self.assertRaisesRegex(ValueError, "MR outcome changed"):
            self.publish()
        self.assertEqual(self.state_head(), before)

    def test_legacy_package_without_batch_context_remains_publishable(self):
        self.scan()
        package = copy.deepcopy(self.result["package"])
        package["reconciliation"] = {"kind": "proposal-outcomes"}
        package["bindings"] = {str(self.sync_dir / "session.json"):
                               state.digest((self.sync_dir / "session.json").read_bytes())}
        self.assertEqual(self.publish(result=self.resave(package))["status"], "published")

    def test_checkpoint_tampering_or_deletion_is_not_used_as_progress(self):
        self.many(2, opened=True)
        self.scan()
        path = next((self.sync_dir / "outcome-scan/rows").glob("*.json"))
        path.write_text("{}")
        with self.assertRaisesRegex(ValueError, "checkpoint order/hash"):
            self.scan(resume=True)

    def test_resume_rejects_changed_base_and_does_not_modify_owner_authority(self):
        self.many(2, opened=True)
        self.scan()
        self.write("unrelated.txt", b"advance")
        new_base = self.commit()
        with self.assertRaisesRegex(ValueError, "frozen base"):
            self.scan(resume=True, base=new_base)
        # No explicit new base means keep the original frozen context.
        self.assertTrue(self.scan(resume=True)["readyForSelection"])

    def test_interrupted_old_version_without_checkpoint_needs_new_sibling_not_fake_resume(self):
        path = self.root / "resumable"
        path.mkdir()
        (path / "diagnostics.txt").write_text("old timeout")
        with self.assertRaisesRegex(ValueError, "new sibling"):
            self.scan(resume=True)
        self.assertEqual((path / "diagnostics.txt").read_text(), "old timeout")

    def test_limits_are_validated_before_creating_session(self):
        for limit, seconds in ((0, 180), (26, 180), (1, 0), (1, 241)):
            with self.subTest(limit=limit, seconds=seconds), self.assertRaisesRegex(ValueError, "limits"):
                self.scan(limit=limit, seconds=seconds)
        self.assertFalse((self.root / "resumable").exists())

    def test_prepublication_budget_exhaustion_never_writes_state(self):
        self.scan()
        before = self.state_head()
        with patch.object(proposal_sync, "_provider", side_effect=budget.Exhausted):
            with self.assertRaisesRegex(ValueError, "no push attempted"):
                self.publish()
        self.assertEqual(before, self.state_head())


class SharedReadBudget(unittest.TestCase):
    def test_git_timeout_is_clamped_and_process_group_is_reaped(self):
        with tempfile.TemporaryDirectory() as root:
            started = time.monotonic()
            with budget.bounded(0.1), self.assertRaises(budget.Exhausted):
                state.git(root, "-c", "alias.slow=!sleep 5", "slow", timeout=60)
            self.assertLess(time.monotonic() - started, 3)
        self.assertIsNone(budget.timeout())

    def test_github_api_uses_remaining_not_full_per_call_timeout(self):
        response = subprocess.CompletedProcess([], 0, '{}', '')
        with budget.bounded(10), patch.object(subprocess, "run", return_value=response) as run:
            github_pr._api("repos/test/banks/pulls/1")
            self.assertGreater(run.call_args.kwargs["timeout"], 0)
            self.assertLessEqual(run.call_args.kwargs["timeout"], 10)

    def test_exhausted_budget_does_not_start_another_process(self):
        with budget.bounded(0), patch.object(subprocess, "run") as run:
            with self.assertRaises(budget.Exhausted):
                github_pr._api("repos/test/banks/pulls/1")
            run.assert_not_called()
