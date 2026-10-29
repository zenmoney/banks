"""Real merge predicates with controlled read-only GitHub/provider boundaries.

#62/#137 shapes reproduce the report: real per-review approvals, with a simulated
nullable aggregate. They do not assert the live value of GitHub reviewDecision.
No hosted PR, policy, ref or service state is mutated by these tests.
"""
import argparse
from contextlib import nullcontext
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import approved_merge as merge

TARGET = 'zenmoney/banks'
HEAD = '25b43c8c3dcf05204c4d9ce3c9c1033efc4864c4'
BASE = 'bdf044fea570e6d9b4e543da9db069ad9dd11ae4'


class Readiness(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='merge-readiness-')
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)
        self.mr = {'number': 62, 'url': f'https://github.com/{TARGET}/pull/62',
                   'state': 'open', 'headCommit': HEAD, 'baseCommit': BASE,
                   'base': 'main', 'head': 'bank-icon-maintenance/15540',
                   'target': TARGET, 'title': 'Bank icon', 'bodySha256': 'c' * 64}
        self.pr = {'number': 62, 'isDraft': False, 'reviewDecision': None,
                   'mergeStateStatus': 'CLEAN', 'headRefOid': HEAD, 'baseRefOid': BASE, 'baseRefName': 'main',
                   'commits': {'nodes': [{'commit': {'oid': HEAD, 'statusCheckRollup': {'state': 'SUCCESS'}}}]}}
        self.reviews = [self.review(5545962740, 'VictoryKod')]
        self.pulls = [{'number': 62, 'draft': False, 'base': {'ref': 'main'}}]
        self.files = [{'filename': 'banks/ABN Amro_15540-nl/icon.svg', 'status': 'modified'}]
        self.permissions = {}
        self.requests = []
        self.errors = {}
        def patched(obj, name, **kwargs):
            item = patch.object(obj, name, **kwargs)
            result = item.start(); self.addCleanup(item.stop)
            return result
        self.inspect = patched(merge.github_pr, 'inspect', side_effect=lambda *_: copy.deepcopy(self.mr))
        self.api = patched(merge.github_pr, '_api', side_effect=self.api_response)
        self.pages = patched(merge.intake, '_pages', side_effect=self.page_response)
        self.trust = patched(merge.intake, '_trusted', side_effect=self.trusted)
        patched(merge.intake, 'repository_from_remote', return_value=TARGET)
        patched(merge.state, 'resolve_remote', return_value=f'https://github.com/{TARGET}.git')

    def review(self, id, login, state='APPROVED', when='2026-10-16T09:48:19Z', head=HEAD):
        return {'id': id, 'user': {'login': login, 'type': 'User'}, 'state': state,
                'commit_id': head, 'submitted_at': when, 'body': ''}

    def api_response(self, endpoint, *args):
        self.requests.append((endpoint, args))
        if endpoint in self.errors:
            raise ValueError(self.errors[endpoint])
        self.assertEqual(endpoint, 'graphql')
        self.assertNotIn('-X', args)  # No merge/write is allowed by this fixture.
        return {'data': {'repository': {'pullRequest': copy.deepcopy(self.pr)}}}

    def page_response(self, endpoint, *args):
        self.requests.append((endpoint, args))
        if endpoint in self.errors:
            raise ValueError(self.errors[endpoint])
        if endpoint.endswith('/reviews'):
            return copy.deepcopy(self.reviews)
        if endpoint.endswith('/files'):
            return copy.deepcopy(self.files)
        self.assertEqual(endpoint, f'repos/{TARGET}/pulls?state=open')
        return copy.deepcopy(self.pulls)

    def trusted(self, target, actor, cache):
        self.assertEqual(target, TARGET)
        login = actor['login']
        permission = self.permissions.get(login.casefold(), 'write')
        if permission not in {'write', 'maintain', 'admin'}:
            raise ValueError('reviewer write permission is not confirmed')
        return {'login': login, 'permission': permission}

    def ready(self):
        return merge.readiness(TARGET, self.mr['number'])

    def listing(self, limit=20):
        return merge.list_approved(argparse.Namespace(repo=str(self.repo), remote=None, base='main', limit=limit))

    def test_62_nullable_aggregate_still_requires_and_returns_exact_human_approval(self):
        mr, approvals = self.ready()
        self.assertEqual(mr['number'], 62)
        self.assertEqual(approvals, [{'id': 5545962740, 'login': 'VictoryKod', 'permission': 'write',
                                    'commit': HEAD, 'submittedAt': '2026-10-16T09:48:19Z'}])
        self.trust.assert_called_once()

    def test_137_informational_review_does_not_erase_previous_approval(self):
        head = '0683962aa7c255a640862ef7e8b9a33c4e7dc3b9'
        self.mr.update(number=137, url=f'https://github.com/{TARGET}/pull/137', headCommit=head)
        self.pr.update(number=137, headRefOid=head)
        self.pr['commits']['nodes'][0]['commit']['oid'] = head
        self.reviews = [self.review(5526308520, 'pereperchili', when='2026-10-15T00:05:04Z', head=head),
                        self.review(5526323302, 'pereperchili', 'COMMENTED', '2026-10-15T00:07:20Z', head=head),
                        self.review(5544453900, 'VictoryKod', when='2026-10-16T07:37:28Z', head=head)]
        self.reviews[1]['body'] = '@VictoryKod Светлая тема — проверить контраст (Nest Bank)'
        self.assertEqual([r['login'] for r in self.ready()[1]], ['pereperchili', 'VictoryKod'])

    def test_null_without_actual_reviews_is_not_approval(self):
        self.reviews = []
        with self.assertRaisesRegex(ValueError, 'exact current head'):
            self.ready()

    def test_aggregate_approved_without_actual_reviews_is_not_approval(self):
        self.pr['reviewDecision'] = 'APPROVED'
        self.reviews = []
        with self.assertRaisesRegex(ValueError, 'exact current head'):
            self.ready()

    def test_aggregate_approved_path_is_unchanged(self):
        self.pr['reviewDecision'] = 'APPROVED'
        self.assertEqual(self.ready()[1][0]['commit'], HEAD)

    def test_explicit_review_requirements_remain_blockers(self):
        for decision in ('REVIEW_REQUIRED', 'CHANGES_REQUESTED'):
            with self.subTest(decision=decision):
                self.pr['reviewDecision'] = decision
                with self.assertRaisesRegex(ValueError, 'readiness'):
                    self.ready()

    def test_missing_aggregate_field_is_not_successful_null_response(self):
        del self.pr['reviewDecision']
        with self.assertRaisesRegex(ValueError, 'readiness response incomplete'):
            self.ready()

    def test_unknown_or_wrong_typed_aggregate_is_not_an_approval_fallback(self):
        for value in ('', 'UNKNOWN', [], True):
            with self.subTest(value=value):
                self.pr['reviewDecision'] = value
                with self.assertRaises(ValueError):
                    self.ready()

    def test_pending_comment_and_dismissed_reviews_alone_never_approve(self):
        for status in ('COMMENTED', 'DISMISSED', 'PENDING'):
            with self.subTest(status=status):
                self.reviews[0]['state'] = status
                if status == 'PENDING': self.reviews[0]['submitted_at'] = None
                with self.assertRaisesRegex(ValueError, 'exact current head'):
                    self.ready()

    def test_later_comment_preserves_a_change_request(self):
        self.reviews += [self.review(5545962741, 'another', 'CHANGES_REQUESTED'),
                         self.review(5545962742, 'another', 'COMMENTED', '2026-10-16T10:00:00Z')]
        with self.assertRaisesRegex(ValueError, 'change request'):
            self.ready()

    def test_latest_change_request_wins_even_for_older_head(self):
        self.reviews += [self.review(5545962741, 'VictoryKod', 'CHANGES_REQUESTED',
                                     '2026-10-16T10:00:00Z', 'f' * 40)]
        with self.assertRaisesRegex(ValueError, 'change request'):
            self.ready()

    def test_new_approval_resolves_same_reviewers_earlier_change_request(self):
        self.reviews.insert(0, self.review(1, 'VictoryKod', 'CHANGES_REQUESTED', '2026-10-15T00:00:00Z'))
        self.assertEqual(len(self.ready()[1]), 1)

    def test_dismissal_does_not_resurrect_that_reviewers_old_approval(self):
        self.reviews.append(self.review(5545962741, 'VictoryKod', 'DISMISSED', '2026-10-16T10:00:00Z'))
        with self.assertRaisesRegex(ValueError, 'exact current head'):
            self.ready()

    def test_timestamp_tie_and_login_case_use_latest_decisive_review(self):
        self.reviews.append(self.review(5545962741, 'victorykod', 'CHANGES_REQUESTED'))
        for rows in (self.reviews, list(reversed(self.reviews))):
            self.reviews = rows
            with self.assertRaisesRegex(ValueError, 'change request'):
                self.ready()

    def test_historical_or_bot_approval_does_not_count(self):
        self.reviews[0]['commit_id'] = 'e' * 40
        with self.assertRaisesRegex(ValueError, 'exact current head'):
            self.ready()
        self.reviews[0]['commit_id'] = HEAD
        self.reviews[0]['user']['type'] = 'Bot'
        with self.assertRaisesRegex(ValueError, 'exact current head'):
            self.ready()

    def test_approval_requires_current_reviewer_permission(self):
        self.permissions['victorykod'] = 'read'
        with self.assertRaisesRegex(ValueError, 'permission'):
            self.ready()

    def test_malformed_reviews_and_unknown_state_fail_closed(self):
        good = copy.deepcopy(self.reviews[0])
        for change in ({'state': 'mystery'}, {'submitted_at': None}, {'id': True},
                       {'id': None}, {'user': None}, {'commit_id': None},
                       {'submitted_at': '2999-01-01T00:00:00Z'}):
            with self.subTest(change=change):
                self.reviews = [{**copy.deepcopy(good), **change}]
                with self.assertRaises(ValueError):
                    self.ready()

    def test_duplicate_review_ids_are_not_silently_collapsed(self):
        self.reviews += [copy.deepcopy(self.reviews[0])]
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.ready()

    def test_ci_and_merge_state_blockers_are_not_relaxed_by_null(self):
        # UNKNOWN is covered by bounded-wait tests with a virtual clock.
        for field, values in (('mergeStateStatus', ('BEHIND', 'BLOCKED', 'DIRTY', 'UNSTABLE')),
                              ('isDraft', (True,))):
            old = self.pr[field]
            for value in values:
                with self.subTest(field=field, value=value):
                    self.pr[field] = value
                    with self.assertRaisesRegex(ValueError, 'readiness'):
                        self.ready()
            self.pr[field] = old
        for status in ('PENDING', 'FAILURE', 'ERROR', 'EXPECTED'):
            with self.subTest(ci=status):
                self.pr['commits']['nodes'][0]['commit']['statusCheckRollup'] = {'state': status}
                with self.assertRaisesRegex(ValueError, 'CI'):
                    self.ready()

    def test_absent_rollup_keeps_existing_no_checks_behavior(self):
        self.pr['commits']['nodes'][0]['commit']['statusCheckRollup'] = None
        self.assertEqual(len(self.ready()[1]), 1)

    def test_head_or_target_branch_race_is_not_silently_accepted(self):
        for field in ('headRefOid', 'baseRefName'):
            old = self.pr[field]
            self.pr[field] = 'f' * 40
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'changed'):
                self.ready()
            self.pr[field] = old

    def test_base_commit_observations_are_not_the_live_target_ref(self):
        old = 'fe29fbc1291b8842ed201261408a3bc690875b55'
        self.mr['baseCommit'] = old
        self.assertEqual(self.ready()[0]['baseCommit'], old)
        self.pr['baseRefOid'] = old
        self.assertEqual(self.ready()[0]['baseCommit'], old)
        # Readiness is still only eligibility; prepare/write separately prove main.
        self.assertEqual(len(self.ready()[1]), 1)

    def test_missing_target_name_or_invalid_base_object_is_not_accepted(self):
        for field, value in (('baseRefName', None), ('baseRefOid', 'main')):
            old = self.pr[field]
            self.pr[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'incomplete'):
                self.ready()
            self.pr[field] = old

    def test_target_ref_is_read_by_exact_branch_not_from_pr_base(self):
        value = {'ref': 'refs/heads/release/next', 'object': {'type': 'commit', 'sha': BASE}}
        self.api.side_effect = None
        self.api.return_value = value
        self.assertEqual(merge._target_commit(TARGET, 'release/next'), BASE)
        self.api.assert_called_once_with(f'repos/{TARGET}/git/ref/heads/release/next')

    def test_missing_ambiguous_or_noncommit_target_ref_is_not_a_fallback(self):
        self.api.side_effect = None
        for value in (None, [], {}, {'ref': 'refs/heads/main', 'object': None},
                      {'ref': 'refs/heads/other', 'object': {'type': 'commit', 'sha': BASE}},
                      {'ref': 'refs/heads/main', 'object': {'type': 'tag', 'sha': BASE}},
                      {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': 'not-a-sha'}}):
            with self.subTest(value=value):
                self.api.return_value = value
                with self.assertRaisesRegex(ValueError, 'target branch reference is incomplete'):
                    merge._target_commit(TARGET, 'main')

    def test_pr_comparison_ignores_only_reported_base_oid(self):
        original = copy.deepcopy(self.mr)
        self.assertTrue(merge._same_pr({**self.mr, 'baseCommit': 'f' * 40}, original))
        for field in ('base', 'target', 'headCommit', 'bodySha256', 'title'):
            with self.subTest(field=field):
                self.assertFalse(merge._same_pr({**self.mr, field: 'changed'}, original))
        self.assertEqual(original, self.mr)

    def test_provider_failures_do_not_become_empty_reviews(self):
        self.errors['graphql'] = 'API unavailable'
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            self.ready()
        del self.errors['graphql']
        self.errors[f'repos/{TARGET}/pulls/62/reviews'] = 'pagination incomplete'
        with self.assertRaisesRegex(ValueError, 'pagination'):
            self.ready()

    def test_partial_graphql_data_with_errors_is_not_trusted(self):
        original = self.api_response
        def partial(endpoint, *args):
            return {**original(endpoint, *args), 'errors': [{'message': 'permission or timeout'}]}
        self.api.side_effect = partial
        with self.assertRaisesRegex(ValueError, 'readiness response incomplete'):
            self.ready()

    def test_listing_uses_shared_readiness_not_a_separate_aggregate_filter(self):
        result = self.listing()
        self.assertEqual([r['mr'] for r in result['selected']], [62])
        self.assertEqual(result['stops'], [])
        self.assertEqual(result['skipped'], [])
        self.assertEqual(result['examined'], 1)
        self.assertEqual(result['target'], TARGET)
        self.assertFalse((self.repo / '.git').exists())
        graphql = [args for endpoint, args in self.requests if endpoint == 'graphql']
        self.assertEqual(len(graphql), 1)
        self.assertTrue(any('mergeStateStatus' in arg for arg in graphql[0]))

    def test_listing_no_approval_has_an_explicit_skip_reason(self):
        self.reviews = []
        result = self.listing()
        self.assertEqual(result['selected'], [])
        self.assertEqual(result['skipped'][0]['code'], 'no-current-approval')
        self.assertEqual(result['skipped'][0]['mr'], 62)
        self.assertEqual(result['examined'], 1)

    def test_listing_draft_and_wrong_base_are_explained_without_provider_reads(self):
        self.pulls = [{'number': 61, 'draft': True, 'base': {'ref': 'main'}},
                      {'number': 62, 'draft': False, 'base': {'ref': 'other'}}]
        result = self.listing()
        self.assertEqual([r['code'] for r in result['skipped']], ['draft', 'different-base'])
        self.assertEqual(result['examined'], 2)
        self.api.assert_not_called(); self.inspect.assert_not_called()

    def test_listing_non_icon_diff_is_explained(self):
        for files in ([{'filename': 'README.md', 'status': 'modified'}], [],
                      [*self.files, {'filename': 'extra.txt', 'status': 'added'}]):
            with self.subTest(files=files):
                self.files = files
                result = self.listing()
                self.assertEqual(result['selected'], [])
                self.assertEqual(result['skipped'][0]['code'], 'not-single-bank-icon')

    def test_listing_malformed_branch_is_a_diagnostic_not_a_crash(self):
        self.pulls[0]['base'] = None
        result = self.listing()
        self.assertEqual(result['stops'][0]['code'], 'read-failed')
        self.assertEqual(result['selected'], [])

    def test_listing_malformed_filename_cannot_become_a_bank_candidate(self):
        self.files[0]['filename'] = None
        result = self.listing()
        self.assertEqual(result['skipped'][0]['code'], 'not-single-bank-icon')
        self.assertEqual(result['selected'], [])

    def test_listing_api_failure_is_a_stop_not_an_empty_success(self):
        self.errors[f'repos/{TARGET}/pulls/62/reviews'] = 'API forbidden 403'
        result = self.listing()
        self.assertEqual(result['selected'], [])
        self.assertEqual(result['skipped'], [])
        self.assertIn('403', result['stops'][0]['reason'])
        self.assertEqual(result['examined'], 1)

    def test_listing_explicit_review_blocker_is_reported(self):
        self.pr['reviewDecision'] = 'REVIEW_REQUIRED'
        result = self.listing()
        self.assertEqual(result['stops'][0]['code'], 'review-required')
        self.assertIn('REVIEW_REQUIRED', result['stops'][0]['reason'])

    def test_listing_limit_reports_remaining_without_claiming_full_scan(self):
        self.pulls += [{'number': 63, 'draft': False, 'base': {'ref': 'main'}}]
        result = self.listing(1)
        self.assertEqual(len(result['selected']), 1)
        self.assertEqual(result['examined'], 1)
        self.assertEqual(result['remaining'], 1)
        self.assertTrue(result['limitReached'])

    def test_listing_empty_repository_and_invalid_limit_are_distinct(self):
        self.pulls = []
        result = self.listing()
        self.assertEqual(result['examined'], 0)
        self.assertEqual(result['remaining'], 0)
        self.assertFalse(result['limitReached'])
        for limit in (0, 201, True):
            with self.subTest(limit=limit), self.assertRaisesRegex(ValueError, 'limit'):
                self.listing(limit)

    def test_same_readiness_blocks_revoked_approval_before_mutation(self):
        mr, approvals = self.ready()
        manifest = {'target': TARGET, 'mr': mr, 'approvals': approvals, 'method': 'squash', 'baseCommit': BASE}
        self.reviews[0]['state'] = 'DISMISSED'
        with patch.object(merge, 'load', return_value=(self.repo, manifest, 'a' * 64)), \
             patch.object(merge, 'authorize', return_value={'scope': 'approved-merge'}), \
             patch.object(merge.state, 'writer', return_value=nullcontext()), \
             patch.object(merge, '_receipt', return_value={'status': 'not-attempted'}), \
             patch.object(merge, '_save') as save, patch.object(merge, 'reconcile') as reconcile:
            with self.assertRaisesRegex(ValueError, 'exact current head'):
                merge.merge(argparse.Namespace(operation=str(self.repo), authorization=None))
            save.assert_not_called(); reconcile.assert_not_called()
        self.assertFalse(any('/merge' in endpoint for endpoint, _ in self.requests))


if __name__ == '__main__':
    unittest.main()
