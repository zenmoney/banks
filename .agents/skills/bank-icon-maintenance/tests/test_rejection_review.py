"""Real state/Git transitions; controlled GitHub HTTP responses, no production writes."""
import argparse
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_outcome_scan as scan_fixture
import test_proposal_sync as fixture
import github_pr
import operation_budget as budget
import rejection_review as review
import state_publication as state

REAL_READER = review.read_thread


class RejectionReview(unittest.TestCase):
    write = fixture.ProposalSync.write
    commit = fixture.ProposalSync.commit
    push_catalog = fixture.ProposalSync.push_catalog
    state_head = fixture.ProposalSync.state_head
    write_state = fixture.ProposalSync.write_state
    publish = fixture.ProposalSync.publish
    resave = fixture.ProposalSync.resave
    scan = scan_fixture.OutcomeScan.scan

    def setUp(self):
        fixture.ProposalSync.setUp(self)
        self.body = 'Proposed replacement, not a rejection decision'
        digest = state.digest(self.body.encode())
        self.original_mr['bodySha256'] = digest
        self.observed.update(state='closed', mergeCommit=None, mergedAt=None,
                             closedAt=fixture.CLOSED, bodySha256=digest)
        self.write_state({10: self.before})
        self.text = 'Оставляем как было'
        self.kind = 'comment'
        self.permission = 'write'
        self.editor = 'reviewer'
        self.threads = None
        self.failure = None
        self.actual_reader = patch.object(review, 'read_thread', side_effect=REAL_READER).start()
        self.api_mock = patch.object(github_pr, '_api', side_effect=self.api).start()

    def message(self, text=None, *, kind=None, suffix='42'):
        kind = kind or self.kind
        anchor = {'comment':'issuecomment-', 'review':'pullrequestreview-', 'inline':'discussion_r'}[kind]
        return {'node_id': kind + suffix, 'html_url': self.original_mr['url'] + '#' + anchor + suffix,
                'body': text or self.text, 'user': {'login':'author', 'type':'User'},
                'state': 'CHANGES_REQUESTED' if kind == 'review' else None}

    def api(self, endpoint, *args):
        if self.failure is not None:
            raise ValueError(self.failure)
        if endpoint == 'repos/test/banks/pulls/1':
            return {'node_id':'PR1', 'html_url':self.original_mr['url'], 'body':self.body,
                    'user':{'login':'author', 'type':'User'}}
        routes = {'repos/test/banks/issues/1/comments':'comment',
                  'repos/test/banks/pulls/1/reviews':'review',
                  'repos/test/banks/pulls/1/comments':'inline'}
        messages = self.threads if self.threads is not None else [self.message()]
        if endpoint in routes:
            return [m for m in messages if m['node_id'].startswith(routes[endpoint])]
        if endpoint == 'graphql':
            node = next(a[3:] for a in args if a.startswith('id='))
            m = next(m for m in messages if m['node_id'] == node)
            kind = next(k for k in ('comment','review','inline') if node.startswith(k))
            return {'data':{'node':{'__typename':review.feedback._TYPES[kind], 'id':node,
                'url':m['html_url'], 'body':m['body'], 'createdAt':'2020-01-01T04:00:00Z',
                'lastEditedAt':'2020-01-01T05:00:00Z',
                'author':{'__typename':'User', 'login':'author'},
                'editor':{'__typename':'User', 'login':self.editor}}}}
        if endpoint == f'repos/test/banks/collaborators/{self.editor}/permission':
            return {'user':{'login':self.editor, 'type':'User'}, 'permission':self.permission}
        raise AssertionError(f'unexpected GitHub operation: {endpoint} {args}')

    def decision(self, result, action='keep-current', *, quotes=None):
        data = json.loads(Path(result['reviewInput']).read_text())
        row = data['reviews'][0]
        sources = [m for m in row['discussion'] if m['kind'] != 'body']
        return {'key':row['key'], 'action':action,
                'sources':[{'url':m['url'], 'quote':m['body']} for m in sources] if quotes is None else quotes,
                'rationale':'Preserve all reviewer requirements; this is not SVG verification or merge authority.'}

    def resolve(self, decision):
        file = self.root / 'review-decisions.json'
        fixture.put(file, [decision])
        return self.scan(resume=True, review_decisions=str(file))

    def test_plain_comment_without_attachment_requires_assessment_before_publication(self):
        first = self.scan()
        self.assertEqual(first['status'], 'awaiting-review-decisions')
        self.assertFalse(first['readyForSelection'])
        self.assertIsNone(first['packageHash'])
        self.assertEqual(first['pendingReviews'][0]['id'], 10)
        self.assertIn('Оставляем как было', Path(first['reviewInput']).read_text())
        self.assertNotIn('lastVerifiedAt', self.before)

    def test_keep_current_saves_editor_quote_action_and_no_freshness(self):
        first = self.scan()
        result = self.resolve(self.decision(first))
        proposal = result['package']['records'][0]['after']['proposals'][-1]
        self.assertEqual(proposal['rejectionReason']['action'], 'keep-current')
        self.assertEqual(proposal['rejectionReason']['category'], 'not-now')
        self.assertEqual(proposal['rejectionReason']['comment']['author'], 'reviewer')
        self.assertEqual(proposal['rejectionReason']['explanation'], self.text)
        self.assertEqual(self.publish()['status'], 'published')
        saved, _ = state.remote_record(self.remote, self.state_head(), 'banks/state/10.json')
        self.assertEqual(saved['lastAttemptAt'], fixture.ATTEMPT)
        self.assertNotIn('lastVerifiedAt', saved)
        self.assertEqual(saved['proposals'][-1]['mrHistory'], [self.original_mr])
        self.assertTrue(self.scan(resume=True)['readyForSelection'])

    def test_existing_null_reason_is_backfilled_without_old_session(self):
        self.before = review_state = copy.deepcopy(self.before)
        p = review_state['proposals'][-1]
        p.update(status='rejected', rejectionReason=None, mrHistory=[copy.deepcopy(self.original_mr)],
                 mrOutcome=copy.deepcopy(self.observed))
        p['mr'] = {**self.observed, 'observedAt':'2020-01-03T00:00:00Z'}
        self.write_state({10: self.before})
        first = self.scan()
        result = self.resolve(self.decision(first))
        self.assertTrue(result['package']['results'][0]['clarification'])
        self.assertEqual(self.publish()['status'], 'published')

    def test_repair_preserves_both_clipping_and_variant_requirements(self):
        self.text = 'лого обрезан и есть еще вариант со словом банк'
        first = self.scan()
        after = self.resolve(self.decision(first, 'repair'))['package']['records'][0]['after']
        reason = after['proposals'][-1]['rejectionReason']
        self.assertEqual(reason['category'], 'technical')
        self.assertEqual(reason['requirements'][0]['quote'], self.text)
        self.assertNotIn('reconsideration', after['proposals'][-1])

    def test_review_submission_and_inline_comments_are_valid_reason_sources(self):
        for kind in ('review', 'inline'):
            with self.subTest(kind=kind):
                self.kind = kind
                snapshot = review.capture(self.before['proposals'][-1], self.observed)
                row = review.candidates(snapshot)[0]
                choice = {'key':snapshot['key'], 'action':'repair',
                          'sources':[{'url':row['url'], 'quote':row['body']}], 'rationale':'Exact repair request'}
                decision = review.bind(10, self.before['proposals'][-1], snapshot, choice, '2026-01-01T00:00:00Z')
                self.assertEqual(review.validate(decision, self.before['proposals'][-1], decision['evaluatedAt'])['action'], 'repair')

    def test_lifecycle_comment_records_reviewer_position_not_verified_bank_fact(self):
        self.text = 'Банк «Открытие» прекратил самостоятельную деятельность 1 января 2025 года'
        first = self.scan()
        after = self.resolve(self.decision(first, 'verify-applicability'))['package']['records'][0]['after']
        self.assertEqual(after['proposals'][-1]['rejectionReason']['category'], 'identity/logo')
        self.assertNotIn('lastVerifiedAt', after)
        self.assertNotIn('reconsideration', after['proposals'][-1])

    def test_multiple_sources_and_conflicting_words_remain_explicit_unclear(self):
        self.threads = [self.message('Оставляем как было',suffix='42'), self.message('Нужен другой вариант',suffix='43')]
        first = self.scan()
        after = self.resolve(self.decision(first, 'unclear'))['package']['records'][0]['after']
        self.assertIsNone(after['proposals'][-1]['rejectionReason'])
        self.assertEqual(len(after['proposals'][-1]['rejectionReview']['sources']), 2)
        self.assertNotIn('lastVerifiedAt', after)

    def test_no_comments_records_checked_absence_not_an_unread_null(self):
        self.threads = []
        result = self.scan()
        after = result['package']['records'][0]['after']
        self.assertIsNone(after['proposals'][-1]['rejectionReason'])
        self.assertEqual(after['proposals'][-1]['rejectionReview']['judgement']['action'], 'no-comment')
        self.assertEqual(self.publish()['status'], 'published')
        # A fresh scan sees the same discussion but produces no repeated state write.
        self.sync_dir = self.root / 'fresh'
        args = argparse.Namespace(command='state-prepare-outcomes', repo=str(self.repo), session=str(self.sync_dir),
                                  remote=None, decisions=None, resume=False, max_proposals=5, max_seconds=180)
        result = state.dispatch(args)
        self.assertEqual(result['status'], 'no-state-changes')
        self.assertTrue(result['readyForSelection'])

    def test_new_comment_after_checked_absence_becomes_a_new_review(self):
        self.threads = []
        self.scan(); self.publish()
        self.threads = None
        args = argparse.Namespace(command='state-prepare-outcomes', repo=str(self.repo), session=str(self.root / 'fresh'),
                                  remote=None, decisions=None, resume=False, max_proposals=5, max_seconds=180)
        self.assertEqual(state.dispatch(args)['status'], 'awaiting-review-decisions')

    def test_failed_discussion_read_is_a_stop_not_missing_comments(self):
        self.failure = 'GitHub transport failed'
        result = self.scan()
        self.assertIsNone(result['packageHash'])
        self.assertIn('transport failed', result['stops'][0]['reason'])

    def test_untrusted_editor_cannot_supply_a_rejection_action(self):
        first = self.scan()
        self.permission = 'read'
        with self.assertRaisesRegex(ValueError, 'write/maintain/admin'):
            self.resolve(self.decision(first))
        self.assertFalse(list((self.sync_dir / 'state-publication').glob('*.receipt.json')))

    def test_fabricated_quote_and_false_absence_are_rejected(self):
        first = self.scan()
        decision = self.decision(first)
        decision['sources'][0]['quote'] = 'invented'
        with self.assertRaisesRegex(ValueError, 'quotation'):
            self.resolve(decision)
        decision = self.decision(first, 'no-comment', quotes=[])
        with self.assertRaisesRegex(ValueError, 'no-comment'):
            self.resolve(decision)

    def test_changed_thread_before_assessment_does_not_use_stale_text(self):
        first = self.scan()
        decision = self.decision(first)
        self.text = 'Новый комментарий'
        with self.assertRaisesRegex(ValueError, 'discussion changed'):
            self.resolve(decision)

    def test_changed_comment_or_editor_rights_before_push_never_writes(self):
        first = self.scan()
        self.resolve(self.decision(first))
        previous = self.state_head()
        self.permission = 'read'
        with self.assertRaisesRegex(ValueError, 'write/maintain/admin'):
            self.publish()
        self.assertEqual(self.state_head(), previous)

    def test_pending_assessment_survives_resume_without_re_read_or_questions(self):
        first = self.scan()
        self.api_mock.reset_mock()
        second = self.scan(resume=True)
        self.api_mock.assert_not_called()
        self.assertEqual(first['reviewInput'], second['reviewInput'])
        self.assertFalse(second['readyForSelection'])

    def test_missing_resolution_is_not_reconstructed_from_published_receipt(self):
        first = self.scan(); self.resolve(self.decision(first)); self.publish()
        for file in (self.sync_dir / 'outcome-scan/resolutions').glob('*.json'):
            file.unlink()
        with self.assertRaisesRegex(ValueError, 'resolution'):
            self.scan(resume=True)

    def test_reopened_mr_cannot_be_classified_as_closed(self):
        first = self.scan(); decision = self.decision(first)
        self.observed.update(state='open', closedAt=None)
        with self.assertRaisesRegex(ValueError, 'outcome changed'):
            self.resolve(decision)

    def test_theme_inversion_is_a_recorded_repair_not_a_new_svg_check(self):
        self.text = 'Сделать инверсию для темной и светлой темы'
        first = self.scan()
        after = self.resolve(self.decision(first, 'repair'))['package']['records'][0]['after']
        self.assertEqual(after['proposals'][-1]['rejectionReason']['requirements'][0]['quote'], self.text)
        self.assertEqual(after['lastAttemptAt'], fixture.ATTEMPT)
        self.assertNotIn('verifiedIconHash', after)

    def test_discussion_deadline_keeps_the_unfinished_bank_resumable(self):
        def timed(endpoint, *args):
            if endpoint.endswith('/comments'):
                raise budget.Exhausted('test deadline during discussion')
            return self.api(endpoint, *args)
        self.api_mock.side_effect = timed
        result = self.scan()
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['processedPaths'], 0)
        self.assertEqual(result['stops'], [])
        self.assertFalse(result['readyForSelection'])
        self.api_mock.side_effect = self.api
        self.assertEqual(self.scan(resume=True)['status'], 'awaiting-review-decisions')

    def test_unknown_discussion_key_is_not_bound_to_another_bank(self):
        first = self.scan()
        choice = self.decision(first)
        choice['key'] = 'a' * 64
        with self.assertRaisesRegex(ValueError, 'unobserved'):
            self.resolve(choice)

    def test_oversized_discussion_is_not_truncated_into_an_empty_reason(self):
        with patch.object(review, 'MAX_DISCUSSION_BYTES', 10):
            result = self.scan()
        self.assertIsNone(result['packageHash'])
        self.assertIn('byte limit', result['stops'][0]['reason'])
