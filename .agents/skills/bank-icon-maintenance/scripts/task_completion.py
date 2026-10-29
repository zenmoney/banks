"""Finish a known-unpublished candidate without rewriting its immutable evidence.

This is a local task transition, not publication, a new queue or permission to
retry an uncertain remote write. Ordinary state publication persists its result.
"""
import copy
from datetime import datetime, timezone
from pathlib import Path
import re

from evidence import instant, required
import source_evidence as source
import state_publication as state
import task_work as work

OUTCOMES = {'error', 'unconfirmed', 'needs-human'}
STAGES = {'preparation', 'render', 'assessment', 'admission', 'publication'}


def unpublished(session, data, bank):
    """Check known publication destinations; unknown/partial writes stay recovery."""
    import asset_publication as assets
    import github_pr
    ctx = work.context(session, data, bank)
    work.verify_live(ctx)
    allowed = ctx['mr']['number'] if ctx['task'] == 'rework-changes' else None
    opened = github_pr.open_for_path(ctx['target'], bank['folder'] + '/icon.svg')
    if any(mr['number'] != allowed for mr in opened) or len(opened) > 1:
        raise ValueError('an open proposal needs reconciliation, not candidate completion')
    root = session / 'asset-publication'
    if root.is_symlink():
        raise ValueError('publication directory cannot be a symlink')
    for receipt in root.glob('*.receipt.json'):
        if not (root / receipt.name.replace('.receipt.json', '.json')).is_file():
            raise ValueError('publication receipt lost its package; recover before task-finish')
    for pointer in session.glob('task-operation-*.json'):
        key = pointer.stem.removeprefix('task-operation-')
        if not (root / f'{key}.json').is_file():
            raise ValueError('retained operation lost its package; recover before task-finish')
    for path in sorted(root.glob('*.json')):
        if not re.fullmatch(r'[0-9a-f]{64}\.json', path.name):
            continue
        package = assets._load(root, path.stem)
        if package['id'] != bank['id'] or package.get('taskKey') != ctx['key'] or package['target'] != ctx['target']:
            raise ValueError('another bank publication package in this single-bank session')
        receipt = assets._receipt(root, path.stem)
        if (receipt.get('creationAttempted') or receipt.get('commentAttempted') or
                receipt.get('status') not in {'not-attempted', 'not-published', 'publication-failed'}):
            raise ValueError('publication outcome needs asset-reconcile before task-finish')
        # A failed push is not proof of absence. Read the exact refs/provider now.
        head = assets._ref(Path(data['repo']), package['target'], package['headRef'])
        material = assets._ref(Path(data['repo']), package['target'], package['materials']['ref'])
        if package.get('revision'):
            expected = package['revision']['mr']
            current = github_pr.inspect(package['target'], expected['number'])
            if head != expected['headCommit'] or any(current[k] != expected[k] for k in
                    ('state', 'headCommit', 'head', 'base', 'title', 'bodySha256')):
                raise ValueError('revision head changed; reconcile before task-finish')
            from feedback_publication import _comments
            if _comments(package) is not None:
                raise ValueError('revision comment already exists; reconcile before task-finish')
        elif head is not None or github_pr.lookup(package['target'], package['head']):
            raise ValueError('asset output exists; reconcile before task-finish')
        if material is not None:
            raise ValueError('partial material output exists; reconcile before task-finish')


def previous_revision(proposal):
    history = proposal['revisionHistory']
    previous = copy.deepcopy(history[-1])
    if history[:-1]:
        previous['revisionHistory'] = copy.deepcopy(history[:-1])
    return previous


def transition(before, note):
    """Retire only this unfinished local candidate; retain an existing source MR."""
    after = copy.deepcopy(before)
    proposals = after.get('proposals', [])
    if not proposals:
        raise ValueError('task-finish requires an owned pending candidate')
    p = proposals[-1]
    if p.get('status') != 'pending' or p.get('mr') or p.get('taskKey') != note['taskKey']:
        raise ValueError('only the owned unpublished candidate can be finished')
    proposals.pop()
    if p.get('revisionHistory'):
        # An unsuccessful same-MR repair must not remove the still-open proposal.
        proposals.append(previous_revision(p))
    completion = {k: copy.deepcopy(note[k]) for k in ('outcome', 'stage', 'reason', 'finishedAt', 'evidence')}
    after.setdefault('retiredLocalCandidates', []).append({'proposal': p, 'completion': completion})
    previous = next(r for r in before['taskResults'] if r['key'] == note['taskKey'])
    if previous['outcome'] != 'candidate':
        raise ValueError('task result is not an unfinished candidate')
    result = {k: copy.deepcopy(previous[k]) for k in ('key', 'task', 'sourceMR', 'review')}
    result.update(outcome=note['outcome'], processedAt=note['finishedAt'], reason=note['reason'],
                  completion={**completion, 'candidateIconHash': p['candidateIconHash']})
    work._set_result(after, result)
    after.update(lastAttemptAt=note['finishedAt'], lastAttemptResult=note['outcome'])
    return after


def before_completion(after):
    """Recover the exact last local candidate stage for own-stage reconciliation."""
    prior = copy.deepcopy(after)
    retired = prior['retiredLocalCandidates'].pop()
    if not prior['retiredLocalCandidates']:
        prior.pop('retiredLocalCandidates')
    p = retired['proposal']
    if p.get('revisionHistory'):
        if prior['proposals'][-1] != previous_revision(p):
            raise ValueError('completion changed the source MR history')
        prior['proposals'].pop()
    prior['proposals'].append(p)
    row = next(r for r in prior['taskResults'] if r['key'] == p['taskKey'])
    history = row['history']
    previous = copy.deepcopy(history[-1])
    if history[:-1]:
        previous['history'] = history[:-1]
    prior['taskResults'][prior['taskResults'].index(row)] = previous
    prior.update(lastAttemptAt=previous['processedAt'], lastAttemptResult='candidate')
    return prior


def validate_note(session, note, bank, before, bindings):
    fields = {'schemaVersion', 'id', 'taskKey', 'outcome', 'stage', 'reason', 'finishedAt', 'evidence'}
    if (type(note) is not dict or note.keys() != fields or type(note['schemaVersion']) is not int or
            note['schemaVersion'] != 1 or type(note['id']) is not int or note['id'] != bank['id'] or
            note['outcome'] not in OUTCOMES or note['stage'] not in STAGES):
        raise ValueError('invalid candidate completion')
    required(note, 'reason')
    finished = instant(note['finishedAt'], 'candidate completion')
    if not instant(before['lastAttemptAt'], 'candidate attempt') < finished <= datetime.now(timezone.utc):
        raise ValueError('candidate completion must follow the actual attempt')
    if type(note['evidence']) is not list:
        raise ValueError('completion evidence must be a list of existing artifact links')
    from state_results import _artifact
    for link in note['evidence']:
        path = source.linked(session, link, 'completion evidence')
        if not path.relative_to(session).as_posix().startswith(f"evidence/{bank['id']}/"):
            raise ValueError('completion evidence belongs to another bank')
        _artifact(session, link['path'], bindings, 'completion evidence', link['sha256'])


def finish(args):
    from state_results import validate_results
    from asset_results import _persist, _encode
    session = Path(args.session).resolve()
    with state.writer(session):
        data, rows, _ = validate_results(session, [args.id])
        bank = data['banks'][0]
        if bank['point'] != 'waiting-proposal' or not bank.get('taskContext'):
            raise ValueError('task-finish requires a current pending task candidate')
        ctx = work.context(session, data, bank)
        unpublished(session, data, bank)
        links = []
        for value in args.evidence or []:
            path = Path(value)
            links.append(source.link(session, path if path.is_absolute() else session / path))
        note = {'schemaVersion': 1, 'id': args.id, 'taskKey': ctx['key'], 'outcome': args.outcome,
                'stage': args.stage, 'reason': args.reason, 'finishedAt': work.now(), 'evidence': links}
        before = rows[0]['after']
        validate_note(session, note, bank, before, {})
        after = transition(before, note)
        content = _encode(note)
        key = state.digest(content)
        link = {'path': f'evidence/{args.id}/finished-{key}.json', 'sha256': key}
        report = {'schemaVersion': 1, 'id': args.id, 'outcome': note['outcome'],
                  'before': before, 'after': after, 'completion': link,
                  'priorReport': {'path': bank['report'], 'sha256': state.digest(state.regular(session / bank['report']))},
                  'nextAction': note['reason']}
        point = 'needs-human' if note['outcome'] == 'needs-human' else 'attempt-complete'
        _persist(session, data, bank, report, f'reports/{args.id}-finished-{key}.json',
                 link['path'], content, point)
        return {'status': 'task-finished', 'id': args.id, 'outcome': note['outcome'],
                'stage': note['stage'], 'reason': note['reason'], 'completion': link,
                'nextAction': 'Publish/check the final state result; no active pending candidate remains. Rework also reports the reason in its source MR.'}


def validate_result(session, data, bank, initial_after, bindings):
    from state_results import _report, _artifact
    from asset_results import validate_transition
    report = _report(session, bank['report'], bindings)
    from candidate import _link
    _link(session, {'path': bank['report'], 'sha256': bank['reportSha256']}, 'completion report', report=True)
    link = report['completion']
    _artifact(session, link['path'], bindings, 'completion', link['sha256'])
    note = source.read_json(source.linked(session, link, 'completion'))
    key = link['sha256']
    if (bank['report'] != f"reports/{bank['id']}-finished-{key}.json" or
            link['path'] != f"evidence/{bank['id']}/finished-{key}.json"):
        raise ValueError('completion paths are not bound to this bank')
    previous_link = report['priorReport']
    _link(session, previous_link, 'completion prior report', report=True)
    previous = _report(session, previous_link['path'], bindings)
    prior_bank = {**bank, 'state': previous['after'], 'report': previous_link['path'],
                  'reportSha256': previous_link['sha256'], 'point': 'waiting-proposal',
                  'reason': previous['nextAction']}
    if previous_link['path'] == f"reports/{bank['id']}.json":
        before = initial_after
        if previous.get('outcome') != 'candidate' or previous['after'] != before:
            raise ValueError('completion does not follow the original candidate')
    else:
        before = validate_transition(session, data, prior_bank, bindings, historical_report=previous_link)
    validate_note(session, note, bank, before, bindings)
    ctx = work.context(session, data, bank)
    if note['taskKey'] != ctx['key']:
        raise ValueError('completion belongs to another task')
    unpublished(session, data, bank)
    after = transition(before, note)
    expected = {'schemaVersion': 1, 'id': bank['id'], 'outcome': note['outcome'],
                'before': before, 'after': after, 'completion': link, 'priorReport': previous_link,
                'nextAction': note['reason']}
    if (report != expected or bank['point'] != ('needs-human' if note['outcome'] == 'needs-human' else 'attempt-complete')
            or bank['reason'] != note['reason']):
        raise ValueError('completion report differs from the validated candidate transition')
    return after
