"""Nullable aggregate through the actual fixture Git/CLI/write/state chain.

Only GitHub responses and authority are synthetic. No hosted merge is performed.
"""
import argparse
from contextlib import redirect_stdout
import io
import json
import sys
from unittest.mock import patch

import test_approved_merge as fixture
import approved_merge as merge
import session
import state_publication as state


class NullableMerge(fixture.MergeFixture):
    def test_cli_discovery_prepare_merge_and_state_share_nullable_readiness(self):
        self.review_status = None
        output = io.StringIO()
        argv = ['session.py', 'merge-list', '--repo', str(self.repo), '--base', 'main', '--limit', '1']
        with patch.object(sys, 'argv', argv), redirect_stdout(output):
            code = session.main()
        self.assertEqual(code, 0)
        listing = json.loads(output.getvalue())
        self.assertEqual([r['mr'] for r in listing['selected']], [146])
        self.assertEqual(listing['skipped'], [])
        self.assertEqual(listing['stops'], [])
        self.assertEqual(self.merge_calls, 0)
        self.prepare_merge()
        result = self.execute()
        self.assertEqual(result['mergeStatus'], 'merged')
        self.assertEqual(self.publish_state(result)['status'], 'published')
        saved, _ = state.remote_record(self.remote, self.ref(state.REF), 'banks/state/10.json')
        self.assertEqual(saved['proposals'][-1]['status'], 'accepted')
        self.assertEqual(saved['lastVerifiedAt'], self.before['lastVerifiedAt'])
        self.assertEqual(saved['lastAttemptAt'], self.before['lastAttemptAt'])
        self.assertEqual(self.merge_calls, 1)

    def test_comment_after_approval_does_not_erase_the_vote_or_duplicate_merge(self):
        self.review_status = None
        self.reviews.append({**self.reviews[0], 'id': 8, 'node_id': 'R8', 'state': 'COMMENTED',
                             'submitted_at': '2020-01-04T00:00:00Z', 'body': 'Informational note'})
        self.prepare_merge()
        self.lose_reply = True
        self.assertEqual(self.execute()['mergeStatus'], 'merged')
        self.assertEqual(self.execute()['mergeStatus'], 'merged')
        self.assertEqual(self.merge_calls, 1)

    def test_approval_dismissed_after_prepare_stops_before_any_merge_write(self):
        self.review_status = None
        self.prepare_merge()
        self.reviews[0]['state'] = 'DISMISSED'
        before = self.ref(state.REF)
        with self.assertRaisesRegex(ValueError, 'exact current head'):
            self.execute()
        self.assertEqual(self.merge_calls, 0)
        self.assertEqual(self.ref(state.REF), before)
        self.assertFalse((self.operation / 'merge.receipt.json').exists())

    def test_empty_nullable_list_explains_missing_approval_and_never_grants_authority(self):
        self.review_status = None
        self.reviews = []
        before = self.ref(state.REF)
        listing = merge.list_approved(argparse.Namespace(repo=str(self.repo), remote=None, base='main', limit=20))
        self.assertEqual(listing['selected'], [])
        self.assertEqual(listing['skipped'][0]['code'], 'no-current-approval')
        self.assertEqual(listing['examined'], 1)
        self.assertEqual(self.ref(state.REF), before)
        self.assertEqual(self.merge_calls, 0)
        self.assertFalse((self.repo / '.git/bank-icon-maintenance/operations').exists())

    def test_null_does_not_waive_merge_permission(self):
        self.review_status = None
        self.prepare_merge()
        with self.assertRaisesRegex(ValueError, 'not configured'):
            merge.merge(argparse.Namespace(operation=str(self.operation), authorization=None))
        self.assertEqual(self.merge_calls, 0)
