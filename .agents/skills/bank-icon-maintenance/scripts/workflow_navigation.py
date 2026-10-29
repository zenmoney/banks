"""Read-only routing and blank judgment templates over the canonical workflow.

No transition logic, validation bypass, visual opinion or publication authority.
Commands with unresolved inputs are explicitly non-runnable templates.
"""
import copy
from pathlib import Path
import shlex
import sys

import source_evidence as source
import feedback_evidence

ROOT = Path(__file__).resolve().parents[1]


def _background_review():
    """Prompt the judgment, never choose a shape or alter evidence/notes schemas."""
    return {'guide': str(ROOT / 'references/icon-background.md'),
            'questions': {
                'backing-role': 'Is this eligible plain-field type 1: a separable uniform-color background, complete emblem not reaching the edges, and no intentional off-center branding or meaningful contour? Foreground colors do not determine background type. Preserve other supported compositions; square download edges alone are not a branded silhouette.',
                'official-display': 'Was this same variant actually observed as a circular avatar of an identified official bank account? Match emblem/colors and retain URL/time/context image/hash; a square download is not a veto. This informs scale but does not override type classification or centering. Do not infer from a different posted asset or a generic platform mask.',
                'whole-emblem-fit': 'Start with the observed scale when confirmed, otherwise supported source composition and useful size: no fixed fill percentage. For type 1, center full visible emblem bounds on both axes; intentional off-center branding is excluded, not forcibly corrected. A 50% round backing is a circle inscribed in a square, not emblem scaling. Include strokes/detached parts; a background-only mask may remove no emblem detail.',
                'small-size-legibility': 'Is it recognizable at target sizes on both themes without excessive shrinking? If type 1 fails, use another supported eligible variant or stop, not a silent square fallback. Other compositions retain the documented supported-shape route.',
                'emblem-fidelity': 'Are contours, colors, relative geometry and internal transparency preserved independently of any permitted backing change?',
                'adaptation-provenance': 'Distinguish an observed official circular presentation from a separately sourced circular asset or catalog-only adaptation. Keep original vector, full source comparison and context immutable; never trace a raster avatar or infer publication authority.'}}


def _command(script, session, id, *arguments, missing=()):
    argv = [sys.executable, str(ROOT/'scripts'/script), *arguments, '--session', str(session)]
    return {'argv': argv, 'shell': shlex.join(argv), 'runnable': not missing,
            'requiredInputs': list(missing)}


def next_step(session, id):
    from workflow import _context
    data, bank = _context(session, id)
    point = bank['point']
    result = {'status': 'next-step', 'id': id, 'point': point,
              'baseCommit': data['baseCommit'], 'reason': bank.get('reason'),
              'baseline': {'present': bank['iconHash'] is not None, 'sha256': bank['iconHash']},
              'commands': [], 'images': {}, 'requiredInputs': [],
              'warning': 'Routing is not authorization or clearance of receipt/evidence stops; canonical commands revalidate.'}
    command = lambda script, *args, **kw: _command(script, session, id, *args, **kw)
    if point == 'task-review':
        from task_work import context
        result.update(stage='interpret-review', guide=str(ROOT/'references/task-runs.md'),
                      taskInput=context(session,data,bank),
                      notesTemplate={'action':None,'requirements':[],'rationale':None,'selectedUrl':None},
                      commands=[command('session.py','task-review','--id',str(id),'--notes','<NOTES>',missing=('NOTES',))])
    elif point == 'task-complete':
        result.update(stage='state-publication',guide=str(ROOT/'references/task-runs.md'),
                      commands=[command('session.py','state-prepare','--ids',str(id))])
    elif point == 'identity-evidence':
        result.update(stage='research', guide=str(ROOT/'references/research.md'),
                      commands=[command('workflow.py', 'work-order', '--id', str(id)),
                                command('workflow.py', 'notes-template', '--id', str(id), '--stage', 'attempt')])
    elif point == 'waiting-proposal':
        proposals = (bank.get('state') or {}).get('proposals') or []
        proposal = proposals[-1] if proposals else {}
        if not bank.get('report'):
            result.update(stage='resume-original-session', guide=str(ROOT/'references/session-contract.md'),
                          reason='An active proposal has no original local evidence here. Locate its original session; do not make a second proposal.')
        elif not proposal.get('reviewPackage'):
            result.update(stage='prepare-candidate', guide=str(ROOT/'references/candidate-review.md'),
                          requiredInputs=['workingSvg', 'actual source judgment', 'actual final desktop bundle'],
                          commands=[command('workflow.py', 'notes-template', '--id', str(id), '--stage', 'candidate'),
                                    command('session.py', 'desktop-export', '--id', str(id), '--candidate', '<FINAL_SVG>',
                                            '--output', '<FRESH_EVIDENCE_DIRECTORY>', missing=('FINAL_SVG', 'FRESH_EVIDENCE_DIRECTORY'))])
        else:
            from asset_results import load_candidate
            _, _, prepared, assessment = load_candidate(session, id)
            payload = source.read_json(source.linked(session, prepared['reviewPackage'], 'review package'))
            result['reviewPackage'] = prepared['reviewPackage']
            result['images'] = {
                'finalFrames': {theme: payload['comparison'][theme]['candidate'] for theme in source.BACKGROUNDS},
                'baseFinal': {theme: payload['comparison'][theme]['beforeAfter'] for theme in source.BACKGROUNDS}}
            if payload.get('sourceComparison'):
                comparison = source.read_json(source.linked(session, payload['sourceComparison'], 'source comparison'))
                result['images']['sourceFinal'] = comparison['pairs']
            result['guide'] = str(ROOT/'references/asset-publication.md')
            if assessment is None:
                result.update(stage='assess-candidate', commands=[command('workflow.py', 'notes-template', '--id', str(id), '--stage', 'assessment')])
            else:
                result.update(stage='prepare-asset-package', requiredInputs=['target', 'base', 'head', 'admission'],
                              admissionGuide=str(ROOT/'references/admission.md'),
                              commands=[command('workflow.py', 'notes-template', '--id', str(id), '--stage', 'admission'),
                                        command('session.py', 'asset-prepare', '--id', str(id), '--target', '<TARGET>',
                                                '--base', '<BASE>', '--head', '<HEAD>', '--admission', '<ADMISSION>',
                                                missing=('TARGET', 'BASE', 'HEAD', 'ADMISSION'))])
    elif point in {'attempt-complete', 'awaiting-state-publication', 'waiting-mr'}:
        result.update(stage='state-publication', guide=str(ROOT/'references/state-publication.md'),
                      commands=[command('session.py', 'state-prepare', '--ids', str(id))])
    elif point in {'blocked', 'needs-human'}:
        result.update(status='stopped', stage='resolve-specific-stop',
                      guide=str(ROOT/'references/session-contract.md'))
        if bank.get('report'):
            result['commands'] = [command('session.py', 'state-prepare', '--ids', str(id))]
    else:
        raise ValueError('unknown saved point; diagnose the canonical session rather than choosing a transition')
    if bank.get('taskContext'):
        from task_work import context
        task = context(session,data,bank)
        result['task'] = task['task']
        if result.get('stage') == 'prepare-asset-package':
            head = task['mr']['head'] if task['task']=='rework-changes' else f"bank-icon-maintenance/task-{task['key'][:12]}-{id}"
            base = task['mr']['base'] if task['mr'] else data['baseRef']
            import re
            if re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', base) or base=='HEAD':
                base = '<BASE_BRANCH>'
            result['commands'][-1] = command('session.py','asset-prepare','--id',str(id),'--target',task['target'],
                '--base',base,'--head',head,'--admission','<ADMISSION>',
                missing=('BASE_BRANCH','ADMISSION') if base=='<BASE_BRANCH>' else ('ADMISSION',))
            result['requiredInputs'] = ['baseBranch','admission'] if base=='<BASE_BRANCH>' else ['admission']
    if feedback_evidence.required_for(session,data,bank):
        ctx = feedback_evidence.context(session,data,bank)
        result['reviewerFeedback'] = {'requestKey': ctx['requestKey'], 'selectedUrl': ctx['request']['selectedUrl'],
                                    'mr': ctx['mr'], 'guide': str(ROOT/'references/review-feedback.md')}
        if result.get('stage') == 'prepare-candidate':
            result['requiredInputs'] += ['selected attachment receipt (--feedback-retrieval)',
                                         'reviewer view (--feedback-view)', 'actual feedbackJudgement']
        if result.get('stage') == 'prepare-asset-package' and not bank.get('taskContext'):
            result['commands'][-1] = command('session.py', 'asset-prepare', '--id', str(id),
                '--target', ctx['mr']['target'], '--base', ctx['mr']['base'], '--head', ctx['mr']['head'],
                '--admission', '<ADMISSION>', missing=('ADMISSION',))
            result['requiredInputs'] = ['admission']
        if result.get('reviewPackage') and payload.get('reviewerFeedback'):
            compared = source.read_json(source.linked(session, payload['reviewerFeedback']['comparison'], 'reviewer comparison'))
            result['images']['reviewerFinal'] = compared['pairs']
    if point == 'waiting-proposal' and bank.get('taskContext'):
        result['onCandidateStop'] = {
            'guide': str(ROOT/'references/task-runs.md') + '#finish-a-stopped-candidate',
            'command': command('session.py', 'task-finish', '--id', str(id),
                '--outcome', '<OUTCOME>', '--stage', '<STAGE>', '--reason', '<ACTUAL_REASON>',
                missing=('OUTCOME', 'STAGE', 'ACTUAL_REASON'))}
    if result.get('stage') in {'research', 'prepare-candidate', 'assess-candidate'}:
        result['backgroundReview'] = _background_review()
    return result


def notes_template(session, id, stage):
    from workflow import _context
    data, bank = _context(session, id)
    expected_point = 'identity-evidence' if stage == 'attempt' else 'waiting-proposal'
    if bank['point'] != expected_point:
        raise ValueError('judgment template does not match the current bank stage')
    platforms = [{'target': target, 'status': None, 'observation': None}
                 for target in ('android-compose', 'android-native', 'ios-compose', 'ios-native')]
    import admission
    templates = {'admission': admission.template(),
        'attempt': {'outcome': None, 'reason': None, 'nextAction': None, 'sources': [],
                    'assessment': {**{aspect: {'finding': None, 'basis': None} for aspect in ('identity', 'currency', 'variant')},
                                   'independenceRationale': None, 'uncertainties': []},
                    'platforms': platforms, 'recheckRequired': None, 'recheckRationale': None},
        'candidate': {'candidate': {**{field: None for field in ('rationale', 'role', 'variant', 'detailsJudgement',
                      'proportionsJudgement', 'faithful', 'visibleDiff', 'visibleDiffExplanation')}, 'transforms': []},
                      'visualComparison': None, 'sourceJudgement': None, 'platforms': platforms},
        'assessment': {'assessor': None, 'decision': None, 'statement': None}}
    if feedback_evidence.required_for(session,data,bank):
        templates['candidate']['feedbackJudgement'] = {'sameVariant': None, 'statement': None}
    if bank.get('taskContext') and stage == 'candidate':
        from task_work import decision
        choice = decision(session,data,bank)
        if choice:
            templates['candidate']['taskResponse'] = [{**row,'resolution':None} for row in choice['requirements']]
    if stage not in templates:
        raise ValueError('unknown notes stage')
    if stage in {'candidate', 'assessment', 'admission'}:
        proposals = (bank.get('state') or {}).get('proposals') or []
        prepared = bool(proposals and proposals[-1].get('reviewPackage'))
        if prepared != (stage in {'assessment', 'admission'}) or not bank.get('report'):
            raise ValueError('template requires the original raw/prepared local proposal, not another attempt')
    root = source.fresh(session, id, 'notes-template')
    path = root/'notes.json'
    source.write_json(path, copy.deepcopy(templates[stage]))
    return {'status': 'judgments-required', 'stage': stage, 'notes': source.link(session, path),
            'backgroundReview': _background_review(),
            **({'admissionSchema': admission.schema(bank)} if stage == 'admission' else {}),
            'nextAction': 'Fill actual observations; null fields intentionally fail validation. Add candidate:{sourceUrl,rationale} only for a candidate attempt, or visualComparison for unchanged. No opinion or permission was supplied.'}
