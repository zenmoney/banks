"""Existing-PR transport/recovery over real Git with controlled provider responses.

The candidate gate is a fixture here, not bypassed in production. Independent
candidate/reference tests exercise evidence bindings; these cases isolate remote
write order, optimistic concurrency and uncertain API results.
"""
import argparse
import copy
import json
from unittest.mock import patch

from test_review_feedback import FeedbackFixture, TARGET, PATH, HEAD, B, C, git
import asset_publication as assets
import asset_results
import feedback_intake as intake
import feedback_revision as revision
import feedback_publication as publication
import github_pr
import state_publication as state


class RevisionTransport(FeedbackFixture):
    def setUp(self):
        super().setUp()
        self.session, self.data = self.start(self.queue(self.prior()))
        self.data = json.loads((self.session / 'session.json').read_text())
        self.selected = next(b for b in self.data['banks'] if b['id'] == 10)
        self.ctx = revision.context(self.session, self.data, self.selected)
        self.output = self.session / 'asset-publication'; self.output.mkdir()
        self.final = assets._commit(self.repo, self.root, [(PATH, C)], 'reviewer-selected revision', self.asset)
        self.material = assets._commit(self.repo, self.root, [('review.txt', b'exact reviewer material')], 'material')
        self.package = {'schemaVersion': 2, 'scope': 'asset-publication', 'id': 10,
            'target': TARGET, 'base': 'main', 'baseCommit': self.base, 'public': False,
            'head': HEAD, 'headRef': f'refs/heads/{HEAD}', 'assetCommit': self.final,
            'title': self.ctx['mr']['title'], 'body': self.body, 'revision': self.ctx,
            'bindings': {self.selected['feedbackContext']['path']: self.selected['feedbackContext']['sha256']},
            'materials': {'ref': publication.material_ref(HEAD, self.ctx['requestKey']), 'commit': self.material},
            'reviewComment': publication.review_comment(self.ctx, 'Exact controlled final comparison')}
        self.hash = state.digest(state.encoded(self.package))
        self.args = argparse.Namespace(package=self.hash)
        self.authority = {'decision': 'authorize', 'scope': 'controlled-test'}
        patch.object(assets, '_validate_local', return_value=(self.repo, self.selected)).start()
        patch.object(assets, 'publication_authorization', return_value=self.authority).start()
        self.record = patch.object(asset_results, 'record_mr').start()

    def publish(self):
        return publication.publish(self.args, self.session, self.output, self.package, self.authority, self.authority)

    def test_exact_revision_is_one_fast_forward_commit_and_one_comment(self):
        before_body = self.body
        result = self.publish()
        self.assertEqual(result['status'], 'mr-confirmed', result)
        self.assertEqual(self.ref(f'refs/heads/{HEAD}'), self.final)
        self.assertEqual(git(self.remote, 'rev-list', '--parents', '-n', '1', self.final).split(), [self.final, self.asset])
        self.assertEqual(git(self.remote, 'diff', '--name-only', self.asset, self.final), PATH)
        self.assertEqual(self.body, before_body)
        self.assertEqual(self.comment_calls, 1)
        self.assertEqual(self.record.call_args.args[3]['number'], 146)
        self.assertEqual(self.publish()['status'], 'mr-confirmed')
        self.assertEqual(self.comment_calls, 1)

    def test_lost_successful_comment_response_is_reconciled_not_repeated(self):
        def api(endpoint, *args):
            result = self.api(endpoint, *args)
            if 'POST' in args:
                raise ValueError('response lost after server creation')
            return result
        with patch.object(github_pr, '_api', side_effect=api):
            result = self.publish()
        self.assertEqual(result['status'], 'mr-confirmed', result)
        self.assertEqual(self.publish()['status'], 'mr-confirmed')
        self.assertEqual(self.comment_calls, 1)

    def test_unobservable_attempted_comment_never_blindly_retries(self):
        writes = []
        def api(endpoint, *args):
            if 'POST' in args:
                writes.append(endpoint)
                raise ValueError('unknown transport outcome')
            return self.api(endpoint, *args)
        with patch.object(github_pr, '_api', side_effect=api):
            self.assertEqual(self.publish()['status'], 'outcome-unknown')
            self.assertEqual(self.publish()['status'], 'outcome-unknown')
        self.assertEqual(len(writes), 1)
        self.assertTrue(assets._receipt(self.output, self.hash)['commentAttempted'])

    def test_concurrent_human_head_is_preserved_without_any_comment(self):
        human = assets._commit(self.repo, self.root, [(PATH, B+b'\n')], 'human change', self.asset)
        git(self.repo, 'push', '-q', 'origin', f'{human}:refs/heads/{HEAD}')
        result = self.publish()
        self.assertEqual(result['status'], 'outcome-unknown', result)
        self.assertEqual(self.ref(f'refs/heads/{HEAD}'), human)
        self.assertEqual(self.comment_calls, 0)
        self.assertEqual(assets._ref(self.repo, TARGET, self.package['materials']['ref']), None)

    def test_atomic_lease_rejects_a_head_race_after_read(self):
        original = assets._transport
        human = assets._commit(self.repo, self.root, [(PATH, B+b'\n')], 'human race', self.asset)
        def race(repo, *args, **kwargs):
            if 'push' in args and f'{self.final}:refs/heads/{HEAD}' in args:
                git(self.repo, 'push', '-q', 'origin', f'{human}:refs/heads/{HEAD}')
            return original(repo, *args, **kwargs)
        with patch.object(assets, '_transport', side_effect=race):
            result = self.publish()
        self.assertEqual(result['status'], 'outcome-unknown', result)
        self.assertEqual(self.ref(f'refs/heads/{HEAD}'), human)
        self.assertEqual(self.comment_calls, 0)

    def test_new_discussion_and_changed_body_stop_existing_package(self):
        self.body += '\nUse a different variant instead.'
        result = self.publish()
        self.assertEqual(result['status'], 'outcome-unknown')
        self.assertEqual(self.ref(f'refs/heads/{HEAD}'), self.asset)
        self.assertEqual(self.comment_calls, 0)

    def test_revoked_reviewer_rights_block_writes(self):
        self.permission = 'read'
        result = self.publish()
        self.assertEqual(result['status'], 'outcome-unknown')
        self.assertEqual(self.ref(f'refs/heads/{HEAD}'), self.asset)
        self.assertEqual(self.comment_calls, 0)

    def test_closed_source_pr_never_creates_a_replacement(self):
        original = self.raw_pr
        def closed():
            return {**original(), 'state': 'closed'}
        with patch.object(self, 'raw_pr', side_effect=closed):
            result = self.publish()
        self.assertEqual(result['status'], 'outcome-unknown')
        self.assertEqual(self.ref(f'refs/heads/{HEAD}'), self.asset)
        self.assertEqual(self.comment_calls, 0)

    def test_already_published_material_is_reused_after_partial_write(self):
        git(self.repo, 'push', '-q', 'origin', f'{self.material}:{self.package["materials"]["ref"]}')
        self.assertEqual(self.publish()['status'], 'mr-confirmed')
        self.assertEqual(self.comment_calls, 1)

    def test_comment_with_wrong_bytes_or_duplicate_marker_stops(self):
        self.comments.append({'id': 9, 'node_id': 'manual', 'body': self.package['reviewComment']+' changed',
            'html_url': f'https://github.com/{TARGET}/pull/146#issuecomment-9'})
        result = self.publish()
        self.assertIn(result['status'], {'outcome-unknown', 'conflict'})
        self.assertEqual(self.ref(f'refs/heads/{HEAD}'), self.asset)
        self.assertEqual(self.comment_calls, 0)

    def test_non_descendant_candidate_cannot_use_a_force_lease(self):
        wrong = assets._commit(self.repo, self.root, [(PATH, C)], 'wrong parent', self.base)
        self.package['assetCommit'] = wrong
        with self.assertRaisesRegex(ValueError, 'append one commit'):
            self.publish()
        self.assertEqual(self.ref(f'refs/heads/{HEAD}'), self.asset)
        self.assertEqual(self.comment_calls, 0)

    def test_package_keeps_exact_old_body_title_and_request_binding(self):
        publication.check_package(self.session, self.data, self.selected, self.package)
        for key, value in (('body', 'overwrite human prose'), ('title', 'invented'), ('head', 'second-pr')):
            bad = copy.deepcopy(self.package); bad[key] = value
            with self.assertRaises(ValueError):
                publication.check_package(self.session, self.data, self.selected, bad)
        bad = copy.deepcopy(self.package); bad['bindings'] = {}
        with self.assertRaises(ValueError):
            publication.check_package(self.session, self.data, self.selected, bad)

    def test_prepare_reuses_existing_pr_and_rejects_duplicate_open_pr(self):
        ctx = publication.prepare(self.session, self.data, self.selected, self.repo, TARGET, 'main', HEAD)
        self.assertEqual(ctx, self.ctx)
        with patch.object(github_pr, 'open_for_path', return_value=[self.ctx['mr'], self.ctx['mr']]):
            with self.assertRaisesRegex(ValueError, 'exactly one'):
                publication.prepare(self.session, self.data, self.selected, self.repo, TARGET, 'main', HEAD)

    def test_merge_baseline_uses_original_accepted_base_not_old_proposal(self):
        proposal = {'revision': {k: self.ctx[k] for k in ('requestKey', 'sourceHead', 'branchBase', 'mr')}}
        parent = revision.baseline_parent(self.repo, proposal, self.final, self.asset, PATH)
        self.assertEqual(parent, self.base)
