"""Multiline reviewer words through real local publication, not a visual eval."""
import copy

from test_task_recovery import RecoveryFixture, HEAD, state


class TaskReviewMaterials(RecoveryFixture):
    def wording(self, text):
        self.review_text = text
        self.comments[0]['body'] = text + ' \r\n'
        self.nodes['COMMENT1']['body'] = self.comments[0]['body']

    def flow(self, kind):
        self.wording('Исправить обрезание.\r\nСохранить полный знак и обе темы.')
        before = self.prior_rejected() if kind == 'rework-rejected' else self.prior()
        self.start_task(before, kind)
        self.decide()
        self.fresh_candidate()
        resolution = 'Сохранён полный знак.\nОбе темы проверены на тестовых данных.'
        self.payload['taskResponse'][0]['resolution'] = resolution
        self.package_path.write_bytes(state.encoded(self.payload))
        response = copy.deepcopy(self.payload['taskResponse'])
        root, prepared = self.prepare_asset('bank-icon-maintenance/new-10' if self.closed else HEAD)
        self.assertEqual(self.payload['taskResponse'], response)
        material = prepared['package']['materials']['details']['taskResponse'][0]
        self.assertEqual(material['quote'], self.review_text.replace('\r\n', ' '))
        self.assertEqual(material['resolution'], resolution.replace('\n', ' '))
        self.assertNotIn('\r', prepared['package']['body'])
        published = self.publish_asset(root, prepared)
        self.assertEqual(published['status'], 'mr-confirmed', published)
        self.assertEqual(self.publish_state(self.state_package())['status'], 'published')
        self.assertEqual(self.state_package()['status'], 'no-state-changes')
        saved, _ = state.remote_record(self.remote, self.ref(state.REF), 'banks/state/10.json')
        self.assertEqual(saved['taskResults'][-1]['outcome'], 'published')
        self.assertEqual(saved['proposals'][-1]['taskResponse'], response)
        self.assertEqual(saved['lastVerifiedAt'], before['lastVerifiedAt'])
        self.assertEqual(self.create_calls, 1 if kind == 'rework-rejected' else 0)
        self.assertEqual(self.comment_calls, 0 if kind == 'rework-rejected' else 1)

    def test_closed_multiline_repair_preserves_evidence_and_publishes_linked_mr(self):
        self.flow('rework-rejected')

    def test_open_multiline_repair_preserves_evidence_and_updates_same_mr(self):
        self.flow('rework-changes')

    def blocked_public_text(self, text):
        self.wording('Исправить обрезание.\nСохранить полный знак.')
        self.start_task(self.prior_rejected())
        self.decide()
        self.fresh_candidate()
        self.payload['taskResponse'][0]['resolution'] = text
        self.package_path.write_bytes(state.encoded(self.payload))
        with self.assertRaisesRegex(ValueError, 'local path|credential|control'):
            self.prepare_asset('bank-icon-maintenance/blocked-public-text')
        self.assertEqual(self.create_calls, 0)
        self.assertEqual(self.comment_calls, 0)

    def test_local_paths_still_block_publication(self):
        self.blocked_public_text('/tmp/private.txt\nDo not disclose')

    def test_credentials_still_block_publication(self):
        self.blocked_public_text('ghp_' + 'A' * 36 + '\nDo not disclose')

    def test_bare_carriage_return_is_not_a_line_ending(self):
        self.blocked_public_text('first\rsecond')

    def test_escape_controls_still_block_publication(self):
        self.blocked_public_text('first\x1bsecond')

    def test_tab_controls_still_block_publication(self):
        self.blocked_public_text('first\tsecond')
