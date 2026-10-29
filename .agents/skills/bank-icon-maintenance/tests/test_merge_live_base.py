"""Current target ref versus historical PR bases, using disposable real Git.

Provider snapshots and merge requests are controlled fixtures, not hosted merges.
The existing complete merge/state writers are exercised without old render files.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

import test_approved_merge as fixture
import test_review_feedback as git_fixture
import approved_merge as merge
import asset_publication as assets
import proposal_sync as sync
import state_publication as state

TARGET, HEAD, PATH = fixture.TARGET, fixture.HEAD, fixture.PATH
git = git_fixture.git


class LiveBase(fixture.MergeFixture):
    def setUp(self):
        super().setUp()
        self.reported_base = self.base
        self.graphql_base = None
        self.second = None

    def raw_pr(self):
        value = super().raw_pr()
        value['base']['sha'] = getattr(self, 'reported_base', value['base']['sha'])
        return value

    def api(self, endpoint, *args):
        second = getattr(self, 'second', None)
        if second is not None:
            if endpoint == f'repos/{TARGET}/pulls/147':
                return copy.deepcopy(second['pr'])
            if endpoint == f'repos/{TARGET}/pulls/147/files':
                return [{'filename': second['path'], 'status': 'modified'}]
            if endpoint == f'repos/{TARGET}/pulls/147/reviews':
                return [{**self.reviews[0], 'id': 8, 'commit_id': second['head']}]
            if endpoint == f'repos/{TARGET}/pulls/147/merge':
                self.assertIn(f"sha={second['head']}", args)
                self.merge_calls += 1
                git(self.repo, 'merge', '--no-ff', '--no-edit', second['head'])
                commit = git(self.repo, 'rev-parse', 'HEAD')
                git(self.repo, 'push', '-q', 'origin', 'HEAD:refs/heads/main')
                second['pr'].update(state='closed', merged=True, merged_at=merge._now(),
                                    closed_at=merge._now(), merge_commit_sha=commit)
                return {'merged': True, 'sha': commit}
            if endpoint == 'graphql' and 'number=147' in args:
                return {'data': {'repository': {'pullRequest': {
                    'number': 147, 'isDraft': False, 'reviewDecision': None,
                    'mergeStateStatus': 'CLEAN', 'headRefOid': second['head'],
                    'baseRefOid': self.reported_base, 'baseRefName': 'main',
                    'commits': {'nodes': [{'commit': {'oid': second['head'],
                        'statusCheckRollup': {'state': 'SUCCESS'}}}]}}}}}
        value = super().api(endpoint, *args)
        if (endpoint == 'graphql' and any('mergeStateStatus' in a for a in args)
                and getattr(self, 'graphql_base', None) is not None):
            value['data']['repository']['pullRequest']['baseRefOid'] = self.graphql_base
        return value

    def advance(self, path='unrelated.txt', content=b'Unrelated target advancement'):
        self.put(path, content)
        commit = self.commit()
        git(self.repo, 'push', '-q', 'origin', 'HEAD:refs/heads/main')
        return commit

    def assert_no_merge(self):
        self.assertEqual(self.merge_calls, 0)
        if hasattr(self, 'operation'):
            self.assertFalse((self.operation / 'merge.receipt.json').exists())

    def test_stale_rest_and_graphql_bases_prepare_merge_and_state_against_live_main(self):
        self.review_status = None
        self.graphql_base = self.base
        live = self.advance()
        result = self.prepare_merge()
        self.assertEqual(result['package']['mr']['baseCommit'], self.base)
        self.assertEqual(result['package']['baseCommit'], live)
        self.assertEqual(result['package']['branchBase'], self.base)
        merged = self.execute()
        self.assertEqual(merged['mergeStatus'], 'merged')
        self.assertEqual(self.publish_state(merged)['status'], 'published')
        saved, _ = state.remote_record(self.remote, self.ref(state.REF), 'banks/state/10.json')
        self.assertEqual(saved['lastVerifiedAt'], self.before['lastVerifiedAt'])
        self.assertEqual(saved['proposals'][-1]['status'], 'accepted')

    def test_rest_and_graphql_can_report_different_valid_base_oids(self):
        live = self.advance()
        self.assertNotEqual(self.reported_base, live)
        # GraphQL fixture reports the live target, REST the old PR snapshot.
        self.assertEqual(self.prepare_merge()['package']['baseCommit'], live)

    def test_reported_pr_base_refresh_does_not_invalidate_unchanged_target_or_approval(self):
        live = self.advance()
        self.prepare_merge()
        self.reported_base = live
        self.assertEqual(self.execute()['mergeStatus'], 'merged')
        self.assertEqual(self.merge_calls, 1)

    def test_real_target_movement_after_prepare_requires_new_package_before_write(self):
        self.prepare_merge()
        self.advance()
        with self.assertRaisesRegex(ValueError, 'target branch changed'):
            self.execute()
        self.assert_no_merge()
        fresh = self.prepare_merge()
        self.assertEqual(fresh['package']['baseCommit'], self.ref('refs/heads/main'))
        self.assertEqual(self.execute()['mergeStatus'], 'merged')

    def test_target_movement_during_prepare_is_not_mislabeled_as_historical_pr_base(self):
        real = sync._fetch_ref
        def moved(repo, target, ref):
            value = real(repo, target, ref)
            if ref == 'refs/heads/main':
                self.advance()
            return value
        with patch.object(sync, '_fetch_ref', side_effect=moved):
            with self.assertRaisesRegex(ValueError, 'target branch changed'):
                self.prepare_merge()
        self.assert_no_merge()
        self.assertFalse((self.repo / '.git/bank-icon-maintenance/operations').exists())

    def test_api_and_git_target_disagreement_remains_a_blocker(self):
        live = self.advance()
        original = self.api
        def stale(endpoint, *args):
            value = original(endpoint, *args)
            if endpoint.endswith('/git/ref/heads/main'):
                value['object']['sha'] = self.base
            return value
        self.api_patch.side_effect = stale
        with self.assertRaisesRegex(ValueError, 'target branch changed'):
            self.prepare_merge()
        self.assertEqual(self.ref('refs/heads/main'), live)
        self.assert_no_merge()

    def test_target_same_bank_changes_still_fail_exact_baseline_check(self):
        for path, content in ((PATH, git_fixture.C),
                              (PATH.replace('icon.svg', 'info.json'),
                               state.encoded({'id': 10, 'title': 'Different identity', 'countryCode': 'RU'}))):
            with self.subTest(path=path):
                self.advance(path, content)
                with self.assertRaises(ValueError):
                    self.prepare_merge()
                self.assert_no_merge()
                # Restore the accepted file for the next independent mutation.
                self.advance(path, state.git(self.repo, 'show', f'{self.base}:{path}').stdout)

    def test_head_push_after_prepare_blocks_without_consuming_merge_receipt(self):
        self.prepare_merge()
        new_head = assets._commit(self.repo, self.root, [(PATH, git_fixture.C)], 'changed proposal', self.asset)
        git(self.repo, 'push', '-q', 'origin', f'{new_head}:refs/heads/{HEAD}', f'{new_head}:refs/pull/146/head')
        with self.assertRaises(merge.ReadinessError) as stopped:
            self.execute()
        self.assertEqual(stopped.exception.code, 'ref-changed')
        self.assert_no_merge()

    def test_prepare_and_recovery_never_read_or_overwrite_shared_fetch_head(self):
        sentinel = self.repo / '.git/FETCH_HEAD'
        sentinel.write_bytes(b'unrelated operation owns this FETCH_HEAD\n')
        self.advance()
        self.prepare_merge()
        self.assertEqual(sentinel.read_bytes(), b'unrelated operation owns this FETCH_HEAD\n')
        self.lose_reply = True
        result = self.execute()
        self.assertEqual(result['mergeStatus'], 'merged')
        self.assertEqual(self.execute()['mergeStatus'], 'merged')
        self.assertEqual(self.merge_calls, 1)
        self.assertEqual(sentinel.read_bytes(), b'unrelated operation owns this FETCH_HEAD\n')

    def test_two_bank_merges_prepare_and_persist_serially_after_main_advances(self):
        path = 'banks/Example_20-ru/icon.svg'
        second_head = assets._commit(self.repo, self.root, [(path, git_fixture.C)], 'second bank', self.base)
        branch = HEAD + '-20'
        git(self.repo, 'push', '-q', 'origin', f'{second_head}:refs/heads/{branch}', f'{second_head}:refs/pull/147/head')
        raw = self.raw_pr()
        raw.update(number=147, node_id='PR147', html_url=f'https://github.com/{TARGET}/pull/147')
        raw['head'] = {'ref': branch, 'sha': second_head, 'repo': {'full_name': TARGET}}
        self.second = {'pr': raw, 'path': path, 'head': second_head}
        self.prepare_merge()
        first = self.execute()
        self.assertEqual(self.publish_state(first)['status'], 'published')
        current = self.ref('refs/heads/main')
        result = merge.prepare(argparse.Namespace(repo=str(self.repo), remote=None, mr=147, base='main', method='merge'))
        manifest = result['package']
        self.assertEqual(manifest['baseCommit'], current)
        self.assertEqual(manifest['mr']['baseCommit'], self.base)
        self.assertEqual(manifest['id'], 20)
        self.operation = Path(result['operation'])
        grant = {'schemaVersion': 1, 'scope': 'approved-merge', 'decision': 'authorize',
            'packageHash': result['packageHash'], 'target': TARGET, 'base': 'main', 'id': 20,
            'number': 147, 'headCommit': second_head, 'method': 'merge',
            'authorizer': 'Fixture owner', 'statement': 'Second separately authorized fixture merge',
            'authorizedAt': merge._now()}
        authority = self.root / 'second-authority.json'; authority.write_bytes(state.encoded(grant))
        second = merge.merge(argparse.Namespace(operation=str(self.operation), authorization=str(authority)))
        self.assertEqual(second['mergeStatus'], 'merged')
        state_grant = {'schemaVersion': 1, 'scope': 'state-publication', 'decision': 'authorize',
            'packageHash': second['packageHash'], 'target': str(self.remote), 'ref': state.REF, 'ids': [20],
            'reviewer': 'Fixture owner', 'statement': 'Record second actual fixture merge', 'authorizedAt': merge._now()}
        state_path = self.root / 'second-state.json'; state_path.write_bytes(state.encoded(state_grant))
        published = state.dispatch(argparse.Namespace(command='state-publish', session=str(self.operation),
            package=second['packageHash'], authorization=str(state_path), run_authorization=None))
        self.assertEqual(published['status'], 'published')
        self.assertEqual(self.merge_calls, 2)
        for id in (10, 20):
            saved, _ = state.remote_record(self.remote, self.ref(state.REF), f'banks/state/{id}.json')
            self.assertEqual(saved['proposals'][-1]['status'], 'accepted')
        saved, _ = state.remote_record(self.remote, self.ref(state.REF), 'banks/state/20.json')
        self.assertNotIn('lastVerifiedAt', saved)


class SnapshotFetch(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='merge-snapshot-fetch-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / 'repo'; self.repo.mkdir()
        git(self.repo, 'init', '-qb', 'main')
        git(self.repo, 'config', 'user.name', 'Fixture')
        git(self.repo, 'config', 'user.email', 'fixture@localhost')
        (self.repo / 'one').write_text('one')
        git(self.repo, 'add', '.'); git(self.repo, 'commit', '-qm', 'first')
        self.first = git(self.repo, 'rev-parse', 'HEAD')
        (self.repo / 'two').write_text('two')
        git(self.repo, 'add', '.'); git(self.repo, 'commit', '-qm', 'second')
        self.second = git(self.repo, 'rev-parse', 'HEAD')
        self.remote = self.root / 'remote.git'
        git(self.root, 'init', '--bare', '-q', str(self.remote))
        git(self.repo, 'push', '-q', str(self.remote), f'{self.first}:refs/heads/main', f'{self.second}:refs/pull/62/head')
        self.fetch_head = self.repo / '.git/FETCH_HEAD'
        self.fetch_head.write_bytes(b'owned by another operation\n')
        patched = patch.object(assets, '_remote', return_value=str(self.remote))
        patched.start(); self.addCleanup(patched.stop)

    def test_interleaved_fetches_return_their_own_commits_and_leave_fetch_head_alone(self):
        barrier = threading.Barrier(2)
        original = assets._transport
        def interleaved(repo, *args, **kwargs):
            result = original(repo, *args, **kwargs)
            if args[0] == 'fetch':
                self.assertIn('--no-write-fetch-head', args)
                self.assertIn('--refmap=', args)
                barrier.wait(timeout=15)
            return result
        with patch.object(assets, '_transport', side_effect=interleaved), ThreadPoolExecutor(max_workers=2) as pool:
            base = pool.submit(sync._fetch_ref, self.repo, TARGET, 'refs/heads/main')
            head = pool.submit(sync._fetch_ref, self.repo, TARGET, 'refs/pull/62/head')
            self.assertEqual(base.result(timeout=20), self.first)
            self.assertEqual(head.result(timeout=20), self.second)
        self.assertEqual(self.fetch_head.read_bytes(), b'owned by another operation\n')
        self.assertEqual(git(self.repo, 'rev-parse', 'HEAD'), self.second)
        pins = git(self.repo, 'for-each-ref', '--format=%(objectname)', 'refs/bank-icon-maintenance/proposal-sync/').splitlines()
        self.assertEqual(sorted(pins), sorted([self.first, self.second]))

    def test_a_ref_moving_during_its_own_fetch_is_still_rejected(self):
        original = assets._transport
        def raced(repo, *args, **kwargs):
            result = original(repo, *args, **kwargs)
            if args[0] == 'ls-remote':
                git(self.remote, 'update-ref', 'refs/heads/main', self.second, self.first)
            return result
        with patch.object(assets, '_transport', side_effect=raced):
            with self.assertRaisesRegex(ValueError, 'changed during fetch'):
                sync._fetch_ref(self.repo, TARGET, 'refs/heads/main')
        self.assertEqual(self.fetch_head.read_bytes(), b'owned by another operation\n')

    def test_missing_ref_is_not_resolved_from_somebody_elses_fetch_head(self):
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            sync._fetch_ref(self.repo, TARGET, 'refs/pull/999/head')
        self.assertEqual(self.fetch_head.read_bytes(), b'owned by another operation\n')
