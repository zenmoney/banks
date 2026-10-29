"""Whole canonical revision chain on disposable Git/provider and pixel fixtures.

All production evidence, candidate, admission, state and asset validators run.
Sources, rendered frames and visual judgments are explicit synthetic fixtures;
this is not a real renderer or model evaluation and never contacts a bank/repo.
"""
import argparse
import copy
import io
import json
from unittest.mock import patch

from PIL import Image
from test_review_feedback import FeedbackFixture, TARGET, PATH, HEAD, URL, A, git
import test_candidates as candidates
import test_workflow_source as image_fixtures
import admission_fixture
import asset_publication as assets
import asset_results
import candidate
import feedback_evidence
import feedback_intake as intake
import feedback_revision as revision
import session as sessions
import source_evidence as source
import state_publication as state
import state_results


class WholeRevision(FeedbackFixture):
    file = candidates.CandidateCLI.file

    def setUp(self):
        super().setUp()
        self.before = self.queue(self.prior())
        self.session, self.started = self.start(self.before)
        self.data = json.loads((self.session/'session.json').read_text())
        self.selected = self.data['banks'][0]
        self.evidence = self.session/'evidence/10'
        self.base_icon = A
        self.clock = intake.now()
        self.file('raw.svg', candidates.RAW)
        attempt = {'schemaVersion': 1, 'id': 10, 'baseCommit': self.base,
            'iconHash': state.digest(A), 'checkedAt': self.clock, 'outcome': 'candidate',
            'reason': 'Explicit synthetic same-variant replacement for protocol tests',
            'nextAction': 'Prepare candidate and inspect exact images',
            'sources': [{'kind': 'official', 'url': candidates.SOURCE, 'assetUrl': candidates.ASSET,
                'checkedAt': self.clock, 'observation': 'Controlled original vector source', 'independenceGroup': 'official-fixture'}],
            'assessment': {**{k: {'finding': 'confirmed', 'basis': 'Explicit controlled evidence judgment'}
                            for k in ('identity', 'currency', 'variant')},
                'independenceRationale': 'Controlled fixture source, not a live factual claim', 'uncertainties': []},
            'desktop': None, 'platforms': [], 'recheckRequired': False,
            'candidate': {'path': 'evidence/10/raw.svg', 'iconHash': state.digest(candidates.RAW),
                'sourceUrl': candidates.SOURCE, 'sourceAssetUrl': candidates.ASSET,
                'rationale': 'Synthetic original SVG matches requested fixture variant'}}
        self.attempt_file = self.evidence/'attempt.json'; self.attempt_file.write_bytes(state.encoded(attempt))
        sessions.record_attempt(self.session, self.data, self.selected, self.attempt_file)
        sessions.save(self.session, self.data)
        state_results.validate_results(self.session, [10])
        self.raw_state = copy.deepcopy(self.selected['state'])
        with patch.object(candidates, 'OLD', self.clock):
            self.candidate = candidates.CandidateCLI.create_package(self)
        # Actual PNG files for the comparison composer, not claimed real UI output.
        for theme in ('light', 'dark'):
            final = self.file(f'render/{theme}.png', image_fixtures.png('#44bb22', (240, 240)))
            base = self.file(f'compare/{theme}-base.png', image_fixtures.png('#2244bb', (240, 240)))
            src = self.file(f'compare/{theme}-source.png', image_fixtures.png('#dd2244', (240, 240)))
            pair = Image.new('RGBA', (480, 240))
            pair.paste(Image.open(self.session/base['path']), (0, 0))
            pair.paste(Image.open(self.session/final['path']), (240, 0))
            stream = io.BytesIO(); pair.save(stream, format='PNG')
            before_after = self.file(f'compare/{theme}-pair.png', stream.getvalue())
            self.candidate['desktop'][theme] = final
            self.candidate['comparison'][theme] = {'base': base, 'candidate': final,
                'source': src, 'beforeAfter': before_after}
        draft = (self.session/self.candidate['mrDraft']['path']).read_text().replace('Alpha', 'Example')
        self.candidate['mrDraft'] = self.file('mr-draft.txt', draft.encode())
        receipt = self.requested_attachment()
        bundle = self.file('desktop-bundle.json', state.encoded({
            'desktop': {k: v for k, v in self.candidate['desktop'].items() if k != 'visualComparison'}, 'comparison': self.candidate['comparison']}))
        view = feedback_evidence.view(self.session, self.data, self.selected, receipt,
                                       self.session/self.candidate['candidate']['path'], bundle)
        self.candidate['reviewerFeedback'] = feedback_evidence.assemble(self.session, self.data, self.selected,
            view['view'], {'sameVariant': True, 'statement': 'Synthetic protocol-test visual judgment; not a model result'})
        self.candidate['checkedAt'] = intake.now()
        self.package_path.write_bytes(state.encoded(self.candidate))

    def requested_attachment(self):
        # Reuse the receipt schema fixture but bind every byte/path/time to THIS
        # request/session and exact selected URL. No installed renderer is faked.
        fixture = image_fixtures.SourceFixture('test_receipt_binds_original_body_and_original_date')
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        receipt = copy.deepcopy(fixture.receipt)
        receipt['requestedUrl'] = receipt['finalUrl'] = URL
        receipt['baseCommit'] = self.base
        receipt['startedAt'] = receipt['finishedAt'] = self.clock
        for attempt in receipt['attempts']:
            attempt['url'] = URL
            attempt['startedAt'] = attempt['finishedAt'] = self.clock
            attempt['command'] = [x.replace(str(fixture.session), str(self.session)).replace(image_fixtures.ASSET, URL)
                                  for x in attempt['command']]
        self.file('fetch/attempt-1/body.bin', (fixture.session/fixture.body['path']).read_bytes())
        return self.file('fetch/receipt.json', state.encoded(receipt))

    def prepare(self):
        candidate.record_candidate(self.session, self.data, self.selected, self.package_path)
        sessions.save(self.session, self.data)
        _, prepared_records, _ = state_results.validate_results(self.session, [10])
        self.prepared_state = prepared_records[0]['after']
        proposal = self.prepared_state['proposals'][-1]
        assessment = {'schemaVersion': 1, 'scope': 'candidate-assessment', 'decision': 'suitable',
            'id': 10, 'baseCommit': self.base, 'baseIconHash': state.digest(A),
            'candidateIconHash': state.digest(candidates.FINAL), 'reviewPackage': proposal['reviewPackage'],
            'assessor': 'Synthetic test assessor', 'statement': 'Fixture-only exact source/base/final judgment',
            'assessedAt': intake.now(), 'provenance': self.file('assessor.txt', b'Explicit synthetic fixture assessment')}
        approval_path = self.evidence/'assessment-input.json'; approval_path.write_bytes(state.encoded(assessment))
        self.data = asset_results.approve(self.session, 10, approval_path, agent=True)
        self.selected = self.data['banks'][0]
        _, _, report, _ = asset_results.load_candidate(self.session, 10)
        admission = admission_fixture.save(self.session, report)
        args = argparse.Namespace(id=10, target=TARGET, base='main', head=HEAD,
                                  public_text=None, admission=admission)
        output = self.session/'asset-publication'; output.mkdir(exist_ok=True)
        prepared = assets.prepare(args, self.session, output)
        assets._validate_local(self.session, output, prepared['package'])
        return output, prepared

    def test_complete_revision_preserves_history_and_publishes_to_same_pr(self):
        output, prepared = self.prepare()
        package, key = prepared['package'], prepared['packageHash']
        self.assertEqual(package['revision']['sourceHead'], self.asset)
        authority = {'schemaVersion': 1, 'scope': 'asset-publication', 'decision': 'authorize',
            'packageHash': key, 'id': 10, 'target': TARGET, 'base': 'main', 'baseCommit': self.base,
            'head': HEAD, 'assetCommit': package['assetCommit'], 'materialRef': package['materials']['ref'],
            'materialCommit': package['materials']['commit'], 'reviewer': 'Synthetic test owner',
            'statement': 'Fixture authority only: exact amendment and one comment to disposable test PR',
            'authorizedAt': intake.now()}
        path = self.root/'asset-authority.json'; path.write_bytes(state.encoded(authority))
        args = argparse.Namespace(package=key, authorization=str(path), run_authorization=None)
        result = assets.publish(args, self.session, output, package)
        self.assertEqual(result['status'], 'mr-confirmed', result)
        _, records, _ = state_results.validate_results(self.session, [10])
        after = records[0]['after']
        self.assertEqual(after['reviewFeedback'][0]['status'], 'published')
        self.assertEqual(len(after['proposals']), 1)
        self.assertEqual(after['proposals'][0]['revisionHistory'], self.before['proposals'])
        self.assertEqual(after['proposals'][0]['mr']['number'], 146)
        self.assertEqual(after['lastVerifiedAt'], self.before['lastVerifiedAt'])
        self.assertEqual(self.comment_calls, 1)
        self.assertEqual((self.session/'worktree'/PATH).read_bytes(), A)
        self.assertEqual(assets.publish(args, self.session, output, package)['status'], 'mr-confirmed')
        self.assertEqual(self.comment_calls, 1)

    def test_missing_selected_reference_cannot_reach_asset_prepare(self):
        self.candidate.pop('reviewerFeedback')
        self.package_path.write_bytes(state.encoded(self.candidate))
        with self.assertRaisesRegex(ValueError, 'reviewer-attachment'):
            self.prepare()
        self.assertEqual(self.ref(f'refs/heads/{HEAD}'), self.asset)
        self.assertEqual(self.comment_calls, 0)

    def test_confirmed_own_raw_stage_can_advance_but_not_unknown_writer(self):
        output, prepared = self.prepare()
        _, records, _ = state_results.validate_results(self.session, [10])
        original = records[0]
        final = revision.overlay_own(original, self.raw_state, own=True)
        self.assertEqual(final, original['after'])
        with self.assertRaises(ValueError): revision.overlay_own(original, self.raw_state, own=False)
        bad = copy.deepcopy(self.raw_state); bad['customHistory'] = {'changed by somebody else': True}
        with self.assertRaises(ValueError): revision.overlay_own(original, bad, own=True)
