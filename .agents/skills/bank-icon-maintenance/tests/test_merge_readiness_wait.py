"""Bounded UNKNOWN handling through real readiness/dispatch, with a virtual clock.

GitHub, Git transport and write boundaries are controlled fixtures. These are not
live GitHub merge tests. No test sleeps in real time or mutates a hosted PR/state.
"""
import argparse
from contextlib import ExitStack, nullcontext, redirect_stderr, redirect_stdout
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import approved_merge as merge
import operation_budget as budget

TARGET = 'fixture/catalog'
HEAD, BASE = 'a' * 40, 'b' * 40


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class Waiting(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='merge-wait-test-')
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        self.clock = Clock()
        self.out, self.err = io.StringIO(), io.StringIO()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(redirect_stderr(self.err))
        self.stack.enter_context(redirect_stdout(self.out))
        self.stack.enter_context(patch.object(budget.time, 'monotonic', self.clock.monotonic))
        self.stack.enter_context(patch.object(budget.time, 'sleep', self.clock.sleep))
        self.states = ['CLEAN']
        self.reads, self.requests, self.puts = 0, [], []
        self.api_cost = 0
        self.on_inspect = None
        self.on_graphql = None
        self.target_head = BASE
        self.mr = {'number': 97, 'url': f'https://github.com/{TARGET}/pull/97',
                   'state': 'open', 'target': TARGET, 'base': 'main', 'head': 'bank/97',
                   'headCommit': HEAD, 'baseCommit': 'c' * 40,
                   'title': 'Fixture icon', 'bodySha256': 'd' * 64}
        self.pr = {'number': 97, 'isDraft': False, 'reviewDecision': None,
                   'baseRefName': 'main', 'headRefOid': HEAD, 'baseRefOid': 'c' * 40,
                   'mergeStateStatus': 'CLEAN', 'commits': {'nodes': [
                       {'commit': {'oid': HEAD, 'statusCheckRollup': {'state': 'SUCCESS'}}}]}}
        self.reviews = [{'id': 7, 'user': {'login': 'reviewer', 'type': 'User'},
                         'state': 'APPROVED', 'commit_id': HEAD,
                         'submitted_at': '2020-01-01T00:00:00Z'}]
        self.permission = 'write'
        self.inspections = 0
        self.inspect = self.stack.enter_context(patch.object(merge.github_pr, 'inspect', side_effect=self.inspect_pr))
        self.api = self.stack.enter_context(patch.object(merge.github_pr, '_api', side_effect=self.api_response))
        self.pages = self.stack.enter_context(patch.object(merge.intake, '_pages', side_effect=self.page_response))
        self.trust = self.stack.enter_context(patch.object(merge.intake, '_trusted', side_effect=self.trusted))
        self.stack.enter_context(patch.object(merge.intake, 'repository_from_remote', return_value=TARGET))
        self.stack.enter_context(patch.object(merge.state, 'resolve_remote', return_value=f'https://github.com/{TARGET}.git'))

    def inspect_pr(self, *_):
        self.inspections += 1
        budget.check()
        if self.on_inspect:
            self.on_inspect(self.inspections)
        return copy.deepcopy(self.mr)

    def api_response(self, endpoint, *args):
        self.requests.append((endpoint, args, budget.timeout(120)))
        budget.check()
        self.clock.now += self.api_cost
        if endpoint == 'graphql':
            self.reads += 1
            if self.on_graphql:
                self.on_graphql(self.reads)
            value = copy.deepcopy(self.pr)
            value['mergeStateStatus'] = self.states[min(self.reads - 1, len(self.states) - 1)]
            return {'data': {'repository': {'pullRequest': value}}}
        if endpoint == f'repos/{TARGET}/git/ref/heads/main':
            return {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': self.target_head}}
        if endpoint.endswith('/merge') and 'PUT' in args:
            self.puts.append(args)
            return {'merged': True, 'sha': 'e' * 40}
        raise AssertionError(f'unexpected API call: {endpoint} {args}')

    def page_response(self, endpoint, *args):
        budget.check()
        if endpoint.endswith('/reviews'):
            return copy.deepcopy(self.reviews)
        if endpoint.endswith('/files'):
            return [{'filename': 'banks/Fixture_10-ru/icon.svg', 'status': 'modified'}]
        if endpoint == f'repos/{TARGET}/pulls?state=open':
            return [{'number': 97, 'draft': False, 'base': {'ref': 'main'}}]
        raise AssertionError(f'unexpected pages: {endpoint}')

    def trusted(self, target, actor, cache):
        budget.check()
        if self.permission != 'write':
            raise ValueError('reviewer permission revoked')
        return {'login': actor['login'], 'permission': 'write'}

    def ready(self, **kwargs):
        return merge.readiness(TARGET, 97, **kwargs)

    def args(self, command='merge-prepare'):
        return argparse.Namespace(command=command, repo=str(self.repo), mr=97, remote=None,
                                  base='main', method='squash', limit=10,
                                  operation=str(self.repo / 'operation'), authorization=None)

    def test_clean_has_no_sleep_progress_or_extra_target_read(self):
        mr, approvals = self.ready()
        self.assertEqual(mr, self.mr)
        self.assertEqual(approvals[0]['commit'], HEAD)
        self.assertEqual(self.clock.sleeps, [])
        self.assertEqual(self.err.getvalue(), '')
        self.assertEqual(self.out.getvalue(), '')
        self.assertEqual([r[0] for r in self.requests], ['graphql'])

    def test_unknown_unknown_clean_uses_fixed_delays_and_fresh_approvals(self):
        self.states = ['UNKNOWN', 'UNKNOWN', 'CLEAN']
        self.assertEqual(self.ready()[0]['headCommit'], HEAD)
        self.assertEqual(self.clock.sleeps, [2, 4])
        self.assertEqual(self.inspections, 3)
        self.assertEqual(self.trust.call_count, 3)
        events = [json.loads(line) for line in self.err.getvalue().splitlines()]
        self.assertEqual([e['mergeStateStatus'] for e in events], ['UNKNOWN', 'UNKNOWN', 'CLEAN'])
        self.assertEqual([e['attempt'] for e in events], [1, 2, 3])
        self.assertEqual(self.out.getvalue(), '')
        self.assertEqual(self.puts, [])

    def test_fifth_unknown_is_pending_not_conflict_or_success(self):
        self.states = ['UNKNOWN']
        with self.assertRaises(merge.ReadinessPending) as raised:
            self.ready()
        result = raised.exception.result()
        self.assertEqual(result['status'], 'merge-readiness-pending')
        self.assertEqual(result['attempts'], 5)
        self.assertFalse(result['mergeAttempted'])
        self.assertEqual(result['elapsedSeconds'], 22)
        self.assertEqual(self.clock.sleeps, [2, 4, 8, 8])
        self.assertEqual(self.reads, 5)
        self.assertEqual(self.puts, [])

    def test_unknown_then_conflict_stops_without_retrying_the_conflict(self):
        self.states = ['UNKNOWN', 'DIRTY', 'CLEAN']
        with self.assertRaisesRegex(merge.ReadinessError, 'DIRTY'):
            self.ready()
        self.assertEqual(self.clock.sleeps, [2])
        self.assertEqual(self.reads, 2)

    def test_definitive_states_never_sleep(self):
        for status in ('BLOCKED', 'BEHIND', 'DIRTY', 'UNSTABLE', 'HAS_HOOKS'):
            with self.subTest(status=status):
                self.states = [status]
                with self.assertRaises(merge.ReadinessError):
                    self.ready()
                self.assertEqual(self.clock.sleeps, [])

    def test_bad_unknown_response_is_not_retryable(self):
        self.states = ['UNKNOWN']
        del self.pr['baseRefName']
        with self.assertRaisesRegex(merge.ReadinessError, 'incomplete'):
            self.ready()
        self.assertEqual(self.clock.sleeps, [])

    def test_malformed_merge_status_is_not_the_unknown_enum(self):
        for status in (None, '', [], True):
            with self.subTest(status=status):
                self.states = [status]
                with self.assertRaises(merge.ReadinessError):
                    self.ready()
                self.assertEqual(self.clock.sleeps, [])

    def test_partial_graphql_data_with_unknown_is_not_retried(self):
        self.states = ['UNKNOWN']
        original = self.api_response
        def partial(endpoint, *args):
            response = original(endpoint, *args)
            if endpoint == 'graphql':
                response['errors'] = [{'message': 'partial response'}]
            return response
        self.api.side_effect = partial
        with self.assertRaisesRegex(merge.ReadinessError, 'incomplete'):
            self.ready()
        self.assertEqual(self.clock.sleeps, [])

    def test_unknown_does_not_wait_for_approval_or_repair_ci(self):
        self.states = ['UNKNOWN']
        self.reviews = []
        with self.assertRaisesRegex(merge.ReadinessError, 'exact current head'):
            self.ready()
        self.reviews = [{'id': 8, 'user': {'login': 'reviewer', 'type': 'User'},
                         'state': 'CHANGES_REQUESTED', 'commit_id': HEAD,
                         'submitted_at': '2020-01-02T00:00:00Z'}]
        with self.assertRaisesRegex(merge.ReadinessError, 'change request'):
            self.ready()
        self.pr['commits']['nodes'][0]['commit']['statusCheckRollup']['state'] = 'FAILURE'
        with self.assertRaisesRegex(merge.ReadinessError, 'CI'):
            self.ready()
        self.assertEqual(self.clock.sleeps, [])

    def test_unknown_with_review_requirement_never_sleeps(self):
        self.states = ['UNKNOWN']
        for decision in ('REVIEW_REQUIRED', 'CHANGES_REQUESTED'):
            self.pr['reviewDecision'] = decision
            with self.assertRaises(merge.ReadinessError):
                self.ready()
        self.assertEqual(self.clock.sleeps, [])

    def test_permission_revoked_during_wait_remains_a_blocker(self):
        self.states = ['UNKNOWN', 'CLEAN']
        def revoke(number):
            if number == 2:
                self.permission = 'read'
        self.on_inspect = revoke
        with self.assertRaisesRegex(ValueError, 'revoked'):
            self.ready()
        self.assertEqual(self.clock.sleeps, [2])
        self.assertEqual(self.puts, [])

    def test_head_changed_between_consistent_observations_stops(self):
        self.states = ['UNKNOWN', 'CLEAN']
        def advance(number):
            if number == 2:
                self.mr['headCommit'] = self.pr['headRefOid'] = 'e' * 40
                self.pr['commits']['nodes'][0]['commit']['oid'] = 'e' * 40
                self.reviews[0]['commit_id'] = 'e' * 40
        self.on_inspect = advance
        with self.assertRaisesRegex(merge.ReadinessError, 'changed'):
            self.ready()
        self.assertEqual(self.clock.sleeps, [2])

    def test_target_branch_name_changed_during_wait_stops(self):
        self.states = ['UNKNOWN', 'CLEAN']
        def retarget(number):
            if number == 2:
                self.mr['base'] = self.pr['baseRefName'] = 'other'
        self.on_inspect = retarget
        with self.assertRaisesRegex(merge.ReadinessError, 'changed'):
            self.ready()
        self.assertEqual(self.clock.sleeps, [2])

    def test_actual_target_moved_during_wait_stops_without_rebinding(self):
        self.states = ['UNKNOWN', 'CLEAN']
        def advance(number):
            if number == 2:
                self.target_head = 'e' * 40
        self.on_inspect = advance
        with self.assertRaisesRegex(merge.ReadinessError, 'target.*changed'):
            self.ready()
        self.assertEqual(self.clock.sleeps, [2])

    def test_pr_base_observation_can_refresh_without_target_moving(self):
        self.states = ['UNKNOWN', 'CLEAN']
        def refresh(number):
            if number == 2:
                self.mr['baseCommit'] = self.pr['baseRefOid'] = BASE
        self.on_inspect = refresh
        self.assertEqual(self.ready()[0]['baseCommit'], BASE)
        self.assertEqual(self.clock.sleeps, [2])

    def test_expected_package_head_is_checked_before_waiting(self):
        self.states = ['UNKNOWN']
        expected = {**self.mr, 'headCommit': 'e' * 40}
        with self.assertRaisesRegex(merge.ReadinessError, 'changed'):
            self.ready(expected_mr=expected, expected_base=BASE)
        self.assertEqual(self.clock.sleeps, [])

    def test_expected_package_target_is_checked_before_waiting(self):
        self.states = ['UNKNOWN']
        with self.assertRaisesRegex(merge.ReadinessError, 'target.*changed'):
            self.ready(expected_mr=self.mr, expected_base='e' * 40)
        self.assertEqual(self.clock.sleeps, [])

    def test_closed_during_wait_stops_without_merge(self):
        self.states = ['UNKNOWN', 'CLEAN']
        def close(number):
            if number == 2:
                self.mr['state'] = 'closed'
        self.on_inspect = close
        with self.assertRaisesRegex(merge.ReadinessError, 'open'):
            self.ready()
        self.assertEqual(self.puts, [])

    def test_ordinary_api_error_after_unknown_is_not_hidden_or_retried(self):
        self.states = ['UNKNOWN', 'CLEAN']
        def fail(number):
            if number == 2:
                raise ValueError('API forbidden 403')
        self.on_inspect = fail
        with self.assertRaisesRegex(ValueError, '403'):
            self.ready()
        self.assertEqual(self.clock.sleeps, [2])

    def test_request_time_and_sleep_share_the_same_deadline(self):
        self.states = ['UNKNOWN']
        self.api_cost = 3
        with self.assertRaises(merge.ReadinessPending) as raised:
            self.ready()
        self.assertLessEqual(self.clock.now, 30)
        self.assertLessEqual(self.reads, 5)
        self.assertEqual(raised.exception.result()['status'], 'merge-readiness-pending')
        limits = [r[2] for r in self.requests]
        self.assertLessEqual(max(limits), 30)
        self.assertTrue(all(a >= b for a, b in zip(limits, limits[1:])))

    def test_outer_deadline_is_not_extended_or_reset(self):
        self.states = ['UNKNOWN']
        with budget.bounded(3), self.assertRaises(merge.ReadinessPending):
            self.ready()
        self.assertEqual(self.clock.sleeps, [2, 1])
        self.assertEqual(self.clock.now, 3)
        self.assertEqual(self.reads, 2)
        self.assertIsNone(budget.timeout())

    def test_deadline_before_first_unknown_is_not_false_provider_pending(self):
        with budget.bounded(0), self.assertRaises(merge.ReadinessError) as raised:
            self.ready()
        self.assertNotIsInstance(raised.exception, merge.ReadinessPending)
        self.assertEqual(self.api.call_count, 0)
        self.assertEqual(self.clock.sleeps, [])

    def test_late_clean_response_cannot_succeed_after_deadline(self):
        self.states = ['UNKNOWN', 'CLEAN']
        def delayed(number):
            if number == 2:
                self.clock.now += 31
        self.on_graphql = delayed
        with self.assertRaises(merge.ReadinessPending):
            self.ready()
        self.assertEqual(self.puts, [])

    def test_list_exposes_pending_as_pending_not_a_conflict(self):
        self.states = ['UNKNOWN']
        result = merge.dispatch(self.args('merge-list'))
        self.assertEqual(result['selected'], [])
        self.assertEqual(result['skipped'], [])
        self.assertEqual(result['stops'][0]['code'], 'merge-readiness-pending')
        self.assertEqual(result['stops'][0]['status'], 'merge-readiness-pending')
        self.assertEqual(result['stops'][0]['attempts'], 5)
        self.assertEqual(result['examined'], 1)

    def test_prepare_pending_does_not_allocate_a_package_or_write(self):
        self.states = ['UNKNOWN']
        with patch.object(merge, 'operation_root') as root:
            result = merge.dispatch(self.args())
        root.assert_not_called()
        self.assertEqual(result['status'], 'merge-readiness-pending')
        self.assertFalse(result['mergeAttempted'])
        self.assertEqual(list(self.repo.iterdir()), [])
        self.assertEqual(self.puts, [])

    def publisher(self):
        """Keep the actual merge() gate/request order, isolate filesystem/Git writes."""
        import asset_publication as assets
        manifest = {'target': TARGET, 'mr': copy.deepcopy(self.mr), 'method': 'squash',
                    'repo': str(self.repo), 'base': 'main', 'baseCommit': BASE, 'bank': {'id': 10},
                    'approvals': [{'id': 7, 'login': 'reviewer', 'permission': 'write',
                                   'commit': HEAD, 'submittedAt': '2020-01-01T00:00:00Z'}]}
        self.manifest = copy.deepcopy(manifest)
        self.receipt = {'status': 'not-attempted'}
        authority = {'fixture': 'explicit-test-authority'}
        self.stack.enter_context(patch.object(merge, 'load', return_value=(self.repo, manifest, 'f' * 64)))
        self.authorize = self.stack.enter_context(patch.object(merge, 'authorize', return_value=authority))
        self.stack.enter_context(patch.object(merge.state, 'writer', return_value=nullcontext()))
        self.stack.enter_context(patch.object(merge, '_receipt', side_effect=lambda _: copy.deepcopy(self.receipt)))
        def save(directory, value):
            self.receipt = copy.deepcopy(value)
            return value
        self.save = self.stack.enter_context(patch.object(merge, '_save', side_effect=save))
        self.stack.enter_context(patch.object(assets, '_bank_base'))
        self.stack.enter_context(patch.object(assets, '_ref', side_effect=lambda repo, target, ref: HEAD if '/pull/' in ref else BASE))
        self.reconcile = self.stack.enter_context(patch.object(merge, 'reconcile', return_value={'status': 'outcome-unknown'}))
        return manifest

    def test_prewrite_pending_never_marks_a_write_attempt_or_mutates_manifest(self):
        manifest = self.publisher()
        self.states = ['UNKNOWN']
        result = merge.dispatch(self.args('merge-publish'))
        self.assertEqual(result['status'], 'merge-readiness-pending')
        self.assertEqual(manifest, self.manifest)
        self.save.assert_not_called(); self.reconcile.assert_not_called()
        self.assertEqual(self.receipt, {'status': 'not-attempted'})
        self.assertEqual(self.puts, [])

    def test_prewrite_wait_then_clean_sends_exactly_one_expected_head_request(self):
        self.publisher()
        self.states = ['UNKNOWN', 'CLEAN']
        merge.dispatch(self.args('merge-publish'))
        self.assertEqual(self.clock.sleeps, [2])
        self.assertEqual(len(self.puts), 1)
        self.assertIn(f'sha={HEAD}', self.puts[0])
        self.save.assert_called_once()
        self.reconcile.assert_called_once()

    def test_prewrite_target_movement_preserves_old_package_and_does_not_write(self):
        manifest = self.publisher()
        self.states = ['UNKNOWN', 'CLEAN']
        def advance(number):
            if number == 2:
                self.target_head = 'e' * 40
        self.on_inspect = advance
        with self.assertRaisesRegex(merge.ReadinessError, 'target.*changed'):
            merge.dispatch(self.args('merge-publish'))
        self.assertEqual(manifest, self.manifest)
        self.save.assert_not_called()
        self.assertEqual(self.puts, [])

    def test_unknown_write_reconciles_without_any_readiness_wait_or_second_put(self):
        self.publisher()
        self.states = ['UNKNOWN', 'CLEAN']
        self.receipt = {'status': 'outcome-unknown', 'attempted': True}
        result = merge.dispatch(self.args('merge-publish'))
        self.assertEqual(result['status'], 'outcome-unknown')
        self.assertEqual(self.reads, 0)
        self.assertEqual(self.clock.sleeps, [])
        self.assertEqual(self.puts, [])
        self.reconcile.assert_called_once()

    def test_lost_response_after_wait_does_not_reenter_wait_or_resend_merge(self):
        self.publisher()
        self.states = ['UNKNOWN', 'CLEAN']
        original = self.api_response
        def lose(endpoint, *args):
            value = original(endpoint, *args)
            if 'PUT' in args:
                raise ValueError('lost merge response')
            return value
        self.api.side_effect = lose
        merge.dispatch(self.args('merge-publish'))
        reads, sleeps = self.reads, list(self.clock.sleeps)
        merge.dispatch(self.args('merge-publish'))
        self.assertEqual(len(self.puts), 1)
        self.assertEqual(self.reads, reads)
        self.assertEqual(self.clock.sleeps, sleeps)

    def test_ctrl_c_is_not_converted_to_pending_or_retried(self):
        self.states = ['UNKNOWN']
        with patch.object(budget.time, 'sleep', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.ready()
        self.assertEqual(self.puts, [])

    def test_cli_pending_has_nonzero_exit_and_one_json_on_stdout(self):
        import session
        self.states = ['UNKNOWN']
        argv = ['session.py', 'merge-prepare', '--repo', str(self.repo),
                '--mr', '97', '--base', 'main', '--method', 'squash']
        with patch.object(sys, 'argv', argv):
            code = session.main()
        self.assertEqual(code, 1)
        result = json.loads(self.out.getvalue())
        self.assertEqual(result['status'], 'merge-readiness-pending')
        self.assertFalse(result['mergeAttempted'])
        self.assertEqual(result['attempts'], 5)
        self.assertEqual(len(self.err.getvalue().splitlines()), 5)

    def test_cli_real_error_is_not_relabelled_as_provider_pending(self):
        import session
        self.states = ['DIRTY']
        with patch.object(sys, 'argv', ['session.py', 'merge-prepare', '--repo', str(self.repo),
                                       '--mr', '97', '--base', 'main', '--method', 'squash']):
            code = session.main()
        self.assertEqual(code, 1)
        self.assertIn('DIRTY', json.loads(self.out.getvalue())['error'])
        self.assertEqual(self.clock.sleeps, [])

    def test_approval_dismissed_during_wait_is_not_retried(self):
        self.states = ['UNKNOWN', 'CLEAN']
        def dismiss(number):
            if number == 2:
                self.reviews[0]['state'] = 'DISMISSED'
        self.on_inspect = dismiss
        with self.assertRaisesRegex(merge.ReadinessError, 'exact current head'):
            self.ready()
        self.assertEqual(self.clock.sleeps, [2])
        self.assertEqual(self.puts, [])

    def test_ci_becomes_pending_during_wait_is_not_polled_to_completion(self):
        self.states = ['UNKNOWN', 'CLEAN']
        def pending(number):
            if number == 2:
                self.pr['commits']['nodes'][0]['commit']['statusCheckRollup']['state'] = 'PENDING'
        self.on_inspect = pending
        with self.assertRaisesRegex(merge.ReadinessError, 'CI'):
            self.ready()
        self.assertEqual(self.clock.sleeps, [2])

    def test_merge_authority_is_rechecked_after_wait_before_any_write(self):
        self.publisher()
        self.states = ['UNKNOWN', 'CLEAN']
        self.authorize.side_effect = [{'fixture': 'explicit-test-authority'}, ValueError('merge permission revoked')]
        with self.assertRaisesRegex(ValueError, 'permission revoked'):
            merge.dispatch(self.args('merge-publish'))
        self.save.assert_not_called()
        self.assertEqual(self.puts, [])
        self.assertEqual(self.clock.sleeps, [2])

    def test_list_keeps_unexamined_rows_when_enclosing_wait_budget_expires(self):
        self.states = ['UNKNOWN']
        original = self.page_response
        def rows(endpoint, *args):
            value = original(endpoint, *args)
            if endpoint.endswith('/pulls?state=open'):
                value.append({'number': 94, 'draft': False, 'base': {'ref': 'main'}})
            return value
        self.pages.side_effect = rows
        with budget.bounded(3):
            result = merge.dispatch(self.args('merge-list'))
        self.assertEqual(result['examined'], 1)
        self.assertEqual(result['remaining'], 1)
        self.assertEqual(result['stops'][0]['status'], 'merge-readiness-pending')
        self.assertEqual(self.clock.now, 3)


if __name__ == '__main__':
    unittest.main()
