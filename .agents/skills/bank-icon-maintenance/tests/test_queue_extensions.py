"""Nullable verification, selection modes and frozen-tree efficiency regressions."""
import copy
import unittest
from test_session import NOW, POLICY
from session_fixture import session as cli_module

def load_session():
    return cli_module


class NullableVerification(unittest.TestCase):
    def test_accepted_without_verification_is_due_for_present_or_absent_icon(self):
        cli = load_session()
        for include_null in (False, True):
            for icon_hash in (None, "a" * 64):
                state = {"schemaVersion": 1, "proposals": [{"status": "accepted", "mr": {"state": "merged"}}]}
                if include_null:
                    state.update(lastVerifiedAt=None, verifiedIconHash=None)
                with self.subTest(include_null=include_null, icon_hash=icon_hash):
                    point, eligible, _ = cli.state_decision(state, icon_hash, cli.parse_time(NOW), POLICY, False)
                    self.assertIsNone(point)
                    self.assertTrue(eligible)

    def test_hash_matching_case_and_mismatch_keep_freshness_semantics(self):
        cli = load_session()
        for value, expected in (("A" * 64, False), ("b" * 64, True), (None, True)):
            state = {"lastVerifiedAt": NOW if value else None, "verifiedIconHash": value,
                     "proposals": [{"status": "accepted", "mr": {"state": "merged"}}]}
            self.assertEqual(cli.state_decision(state, "a" * 64, cli.parse_time(NOW), POLICY, False)[1], expected)

    def test_invalid_optional_hash_still_fails_closed(self):
        cli = load_session()
        for value in (False, 42, "bad", ""):
            with self.subTest(value=value), self.assertRaises(cli.SessionError):
                cli.verified_hash({"verifiedIconHash": value})


class QueueExtensions(unittest.TestCase):
    def setUp(self):
        import test_session as fixtures
        self.fixture = fixtures.SessionCLI('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        missing = self.fixture.bank(11, "Bank11")
        (missing / 'icon.svg').unlink()
        self.fixture.commit()
        self.fixture.cli_module = cli_module


    def test_one_tree_read_for_many_missing_icons_and_worktree_is_rechecked(self):
        from unittest.mock import patch
        import json
        f = self.fixture
        for id in range(12, 32):
            (f.bank(id, f'Bank{id}')/'icon.svg').unlink()
        f.commit()
        with patch.object(f.cli_module, 'git', wraps=f.cli_module.git) as calls:
            entries, errors = f.cli_module.catalog(f.repo)
            self.assertFalse(errors)
            self.assertEqual(sum(e[0]['iconHash'] is None for e in entries.values()), 21)
            f.cli_module.catalog(f.repo)
            tree_reads = [c for c in calls.call_args_list if c.args[1] == 'ls-tree']
            self.assertEqual(len(tree_reads), 1)
        metadata = f.repo/'banks/Bank11_11-ru/info.json'
        value = json.loads(metadata.read_bytes()); value['title'] = 'Changed'
        metadata.write_text(json.dumps(value))
        entries, _ = f.cli_module.catalog(f.repo)
        self.assertIn('differ from the committed blob', entries[11][0]['problem'])

    def test_cached_tree_does_not_hide_a_new_duplicate_or_appeared_icon(self):
        f = self.fixture
        f.cli_module.catalog(f.repo)
        (f.repo/'banks/Bank11_11-ru/icon.svg').write_text('<svg/>')
        entries, _ = f.cli_module.catalog(f.repo)
        self.assertIn('differ from the committed blob', entries[11][0]['problem'])
        f.bank(11, 'Other')
        entries, _ = f.cli_module.catalog(f.repo)
        self.assertEqual(len(entries[11]), 2)
