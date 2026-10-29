"""Task-scoped observations over existing bank state; never a parent-session seed.

A fresh session owns its own evidence. Review text is an input, not a publication
permission. The canonical attempt/candidate/state writers remain the only writers.
"""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re

import feedback_intake as intake
import github_pr
import rejection_review
import state_publication as state
from evidence import instant, required, require_hash

TASKS = {'update-existing', 'add-missing', 'rework-rejected', 'rework-changes'}
REWORK = {'rework-rejected', 'rework-changes'}
ACTIONS = {'repair', 'verify-applicability', 'keep-current', 'defer', 'unclear'}


def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _bank_fields(bank):
    return {k: bank[k] for k in ('id', 'folder', 'metadataHash', 'iconHash')}


def _messages(target, number):
    rows = rejection_review.read_thread(target, number)
    # This is the normalized task view, not the raw source receipt. Generated
    # comparisons and surrounding whitespace do not manufacture a new request.
    rows = [{**r, 'body': intake.clean_body(r['body'])} for r in rows]
    rows = [r for r in rows if r['body']]
    if len(state.encoded(rows)) > rejection_review.MAX_DISCUSSION_BYTES:
        raise ValueError('review discussion too large; never silently truncate it')
    return rows


def _key(value):
    return state.digest(state.encoded({'task': value['task'], 'bank': value['bank'],
        'mr': (value.get('mr') or {}).get('url'), 'messages': value.get('messages', [])}))


def _proposal(before, number):
    values = (before or {}).get('proposals', [])
    matches = [p for p in values if (p.get('mr') or {}).get('number') == number]
    if len(matches) > 1:
        raise ValueError('multiple proposals claim this MR; reconcile its history first')
    return matches[0] if matches else None


def capture(repo, data, bank, task, number=None):
    """Read only this bank/PR and bind the catalog; old artifact paths are data."""
    if task not in TASKS:
        raise ValueError('unknown bank task')
    if task in REWORK and (type(number) is not int or number < 1):
        raise ValueError('rework requires one explicit MR selected by this task')
    if task not in REWORK and number is not None:
        raise ValueError('ordinary icon tasks do not silently become rework tasks')
    if task == 'add-missing' and bank['iconHash'] is not None:
        raise ValueError('add-missing needs an existing metadata record without an icon')
    if task == 'update-existing' and bank['iconHash'] is None:
        raise ValueError('update-existing needs a present accepted icon')
    target = intake.repository_from_remote(data['stateRemote'])
    before = bank['state']
    opened = github_pr.open_for_path(target, bank['folder'] + '/icon.svg')
    if any(task != 'rework-changes' or mr['number'] != number for mr in opened) or len(opened) > 1:
        raise ValueError('another open proposal already owns this icon; no duplicate task')
    value = {'schemaVersion': 1, 'task': task, 'bank': _bank_fields(bank),
             'target': target, 'baseCommit': data['baseCommit'], 'stateCommit': data['stateCommit'],
             'beforeSha256': state.digest(state.encoded(before)), 'observedAt': now(),
             'mr': None, 'prBody': None, 'messages': [], 'outcomes': []}
    # Resolve only published proposals relevant to this bank, not the entire state ref.
    active = [p for p in (before or {}).get('proposals', []) if p['status'] in {'open', 'pending'}]
    if len(active) > 1:
        raise ValueError('multiple active proposals; no silent abandonment')
    for proposal in active:
        previous = proposal.get('mr')
        if previous is None:
            from task_recovery import audit_pending
            value['orphanAudit'] = audit_pending(repo, target, bank)
            continue
        if previous['target'] != target:
            raise ValueError('published proposal belongs to another state destination')
        observed = github_pr.outcome(target, previous['number'])
        if observed['state'] == 'open':
            if task != 'rework-changes' or number != observed['number']:
                raise ValueError('an open MR already owns this bank; use rework-changes for that MR')
        else:
            proof = None
            if observed['state'] == 'merged':
                from proposal_sync import _merge_proof
                proof = _merge_proof(repo, bank['id'], proposal, observed)
            value['outcomes'].append({'number': previous['number'], 'observed': observed, 'proof': proof})
    if task in REWORK:
        prior = _proposal(before, number)
        if prior and prior['mr'].get('target') != target:
            raise ValueError('rework proposal belongs to a different repository')
        raw = github_pr._api(f'repos/{target}/pulls/{number}')
        mr = github_pr.inspect(target, number)
        expected = 'closed' if task == 'rework-rejected' else 'open'
        if mr['state'] != expected:
            raise ValueError(f'{task} requires an {expected} unmerged MR')
        files = intake._pages(f'repos/{target}/pulls/{number}/files')
        path = bank['folder'] + '/icon.svg'
        if len(files) != 1 or files[0].get('filename') != path or files[0].get('status') not in {'added', 'modified'}:
            raise ValueError('task must bind one added/modified bank icon, not title/alt text or unrelated changes')
        if state.digest(raw.get('body', '').encode()) != mr['bodySha256']:
            raise ValueError('MR description changed while reading task input')
        value.update(mr=mr, prBody=raw['body'], messages=_messages(target, number))
        if task == 'rework-changes':
            from proposal_sync import _fetch_ref
            from feedback_revision import _proof
            base = _fetch_ref(repo, target, f"refs/heads/{mr['base']}")
            source = _fetch_ref(repo, target, f'refs/pull/{number}/head')
            if base != data['baseCommit'] or source != mr['headCommit']:
                raise ValueError('rework needs the current chosen base and exact PR head; no checkout reset')
            branch_base = state.git(repo, 'merge-base', base, source).stdout.decode().strip()
            _proof(repo, base, bank, before, mr, source, branch_base)
            value.update(sourceHead=source, branchBase=branch_base)
    value['key'] = _key(value)
    return value



def eligibility(value, before, bank, at, policy, explicit):
    from session import state_decision
    effective = observed_state(value,before)
    if (value.get('orphanAudit') and not effective.get('proposals') and
            effective.get('lastAttemptResult')=='candidate'):
        # Exact no-output audit retired the sole local-only candidate. Preserve its
        # attempt facts; the fresh task, not a rewritten timestamp, permits search.
        return None, True, 'unpublished orphan retained as history; fresh evidence required'
    return state_decision(effective,bank['iconHash'],at,policy,explicit)


def attach(session, data, bank, task, number=None):
    if bank['point'] == 'blocked':
        raise ValueError(bank['reason'])
    value = capture(Path(data['repo']), data, bank, task, number)
    from source_evidence import fresh, link
    root = fresh(session, bank['id'], 'task')
    state.save_new(root / 'context.json', state.encoded(value))
    bank['taskContext'] = link(session, root / 'context.json')
    effective = observed_state(value, bank['state'])
    if task not in REWORK:
        from session import state_decision, policy_at
        point, eligible, reason = eligibility(value,bank['state'],bank,instant(data['now'],'now'),
                                               policy_at(Path(data['repo']),data['baseCommit']),True)
        if point is not None or not eligible:
            raise ValueError(reason or 'bank remains ineligible for this task')
    else:
        previous = next((r for r in (bank['state'] or {}).get('taskResults', []) if r['key'] == value['key']), None)
        if previous and previous['outcome'] in {'published', 'keep-current', 'defer', 'unclear'}:
            raise ValueError('this version of the reviewer task was already processed; no duplicate proposal')
    bank.update(point='task-review' if task in REWORK else 'identity-evidence', reason=None)
    return value


def context(session, data, bank, before=None, bindings=None):
    if not bank.get('taskContext'):
        return None
    from source_evidence import linked, read_json
    path = linked(session, bank['taskContext'], 'task context')
    value = read_json(path)
    if before is None:
        from state_results import _prior_state
        before = _prior_state(data, bank['id'])
    if (value.get('schemaVersion') != 1 or value.get('task') not in TASKS or
            value.get('bank') != _bank_fields(bank) or value.get('baseCommit') != data['baseCommit'] or
            value.get('stateCommit') != data['stateCommit'] or
            value.get('beforeSha256') != state.digest(state.encoded(before)) or value.get('key') != _key(value) or
            value.get('target') != intake.repository_from_remote(data['stateRemote'])):
        raise ValueError('task is not bound to this fresh catalog and original state')
    if instant(value['observedAt'], 'task observedAt') > datetime.now(timezone.utc):
        raise ValueError('task observation is in the future')
    if value['task'] not in REWORK:
        from session import state_decision, policy_at
        point, eligible, _ = eligibility(value,before,bank,instant(data['now'],'now'),
            policy_at(Path(data['repo']),data['baseCommit']),True)
        if point is not None or not eligible:
            raise ValueError('ordinary task context cannot release a rejected or blocked proposal')
    if bindings is not None:
        bindings[str(path)] = bank['taskContext']['sha256']
    return value


def verify_live(value, *, published_head=None):
    """Mutable input checks at write boundaries; replays keep their own snapshots."""
    for row in value['outcomes']:
        current = github_pr.outcome(value['target'], row['number'])
        if current != row['observed']:
            raise ValueError('bank proposal outcome changed; reobserve before publication')
    if value['mr'] is None:
        return
    mr = github_pr.inspect(value['target'], value['mr']['number'])
    expected = dict(value['mr'])
    if published_head is not None:
        expected['headCommit'] = published_head
    if any(mr[k] != expected[k] for k in mr if k != 'baseCommit'):
        raise ValueError('task MR/head/description changed; never publish against stale feedback')
    if _messages(value['target'], mr['number']) != value['messages']:
        raise ValueError('review discussion changed; inspect new feedback rather than replaying a stale decision')


def decision(session, data, bank, bindings=None, *, live=False):
    value = context(session, data, bank, bindings=bindings)
    if value is None or value['task'] not in REWORK:
        return None
    from source_evidence import linked, read_json
    if not bank.get('taskDecision'):
        raise ValueError('read the task discussion and record an agent decision; no owner question is implied')
    path = linked(session, bank['taskDecision'], 'task decision')
    choice = read_json(path)
    fields = {'key', 'action', 'requirements', 'rationale', 'sources', 'assessedAt', 'selectedUrl'}
    if (choice.keys() != fields or choice['key'] != value['key'] or choice['action'] not in ACTIONS
            or type(choice['requirements']) is not list or type(choice['sources']) is not list
            or len(choice['requirements']) != len(choice['sources'])):
        raise ValueError('task review action or source requirements changed')
    required(choice, 'rationale')
    checked = instant(choice['assessedAt'], 'assessedAt')
    if checked < instant(value['observedAt'], 'task observedAt') or checked > datetime.now(timezone.utc):
        raise ValueError('task decision dates are not from this new task')
    rows = {r['url']: r for r in value['messages']}
    if choice['action'] != 'unclear' and not choice['requirements']:
        raise ValueError('a substantive task decision requires sourced human words')
    seen = set()
    for requirement, source in zip(choice['requirements'], choice['sources']):
        if type(requirement) is not dict or requirement.keys() != {'url', 'quote'}:
            raise ValueError('task requirement requires an exact source URL and quotation')
        row = rows.get(requirement['url'])
        if not isinstance(source, dict):
            raise ValueError('task decision source must be an observed message')
        # intake.message already separates raw body/hash from task text/hash.
        # Compare like representations; never strip the raw evidence or its hash.
        text = intake.clean_body(source.get('body'))
        if (row is None or requirement['url'] in seen or required(requirement, 'quote') not in row['body'] or
                source.get('url') != row['url'] or text != row['body'] or
                source.get('text') != text or source.get('textSha256') != state.digest(text.encode()) or
                source.get('nodeId') != row['nodeId'] or source.get('kind') != row['kind'] or
                source.get('bodySha256') != state.digest(source['body'].encode()) or
                source.get('editor', {}).get('__typename') != 'User' or
                source.get('trust', {}).get('login') != source.get('editor', {}).get('login') or
                source.get('trust', {}).get('permission') not in {'write', 'maintain', 'admin'}):
            raise ValueError('task decision lost its exact human-editor source')
        if live and intake.message(value['target'], value['mr']['number'], row['kind'], row['nodeId']) != source:
            raise ValueError('review source/editor/permission changed')
        seen.add(requirement['url'])
    if choice['selectedUrl'] is not None and choice['selectedUrl'] not in {url for src in choice['sources'] for url in src['attachments']}:
        raise ValueError('selected task attachment is not in the sourced reviewer requirements')
    if live:
        verify_live(value)
    if bindings is not None:
        bindings[str(path)] = bank['taskDecision']['sha256']
    return choice


def record_decision(session, data, bank, notes):
    value = context(session, data, bank)
    if value is None or value['task'] not in REWORK or bank['point'] != 'task-review':
        raise ValueError('review assessment belongs to an unstarted rework task')
    if type(notes) is not dict or notes.keys() not in ({'action', 'requirements', 'rationale'}, {'action', 'requirements', 'rationale', 'selectedUrl'}):
        raise ValueError('task-review notes need action, requirements and rationale')
    verify_live(value)
    rows = {r['url']: r for r in value['messages']}
    sources = []
    for item in notes['requirements']:
        row = rows.get(item.get('url'))
        if row is None:
            raise ValueError('review source not observed in this MR')
        sources.append(intake.message(value['target'], value['mr']['number'], row['kind'], row['nodeId']))
    from source_evidence import fresh, link
    root = fresh(session, bank['id'], 'task-review')
    payload = {'key': value['key'], 'selectedUrl': None, **notes, 'sources': sources, 'assessedAt': now()}
    state.save_new(root / 'decision.json', state.encoded(payload))
    bank['taskDecision'] = link(session, root / 'decision.json')
    decision(session, data, bank)
    bank.update(point='identity-evidence' if notes['action'] in {'repair', 'verify-applicability'} else 'task-complete',
                reason=notes['rationale'])
    return payload


def _reason(value, choice, proposal):
    if not choice or choice['action'] == 'unclear' or not choice['sources']:
        return proposal.get('rejectionReason')
    src = choice['sources'][0]
    return {'comment': {'url': src['url'], 'body': src['body'], 'author': src['editor']['login'],
                'createdAt': src['createdAt'], 'updatedAt': src['lastEditedAt'] or src['createdAt'], 'kind': src['kind']},
            'mr': {k: value['mr'][k] for k in ('number', 'url')},
            'candidateIconHash': proposal['candidateIconHash'],
            'category': {'repair':'technical', 'verify-applicability':'identity/logo', 'keep-current':'not-now', 'defer':'not-now'}[choice['action']],
            'action': choice['action'], 'explanation': choice['requirements'][0]['quote'],
            'requirements': choice['requirements'], 'rationale': choice['rationale']}


def observed_state(value, before, choice=None):
    from proposal_sync import _transition
    after = copy.deepcopy(before) if before is not None else {'schemaVersion': 1}
    for row in value['outcomes']:
        p = _proposal(after, row['number'])
        if not p or p is not after['proposals'][-1]:
            raise ValueError('task observation conflicts with latest proposal history')
        after = _transition(after, row['observed'], row['proof'], value['stateCommit'],
                            value['observedAt'], p.get('rejectionReason'))
    if value.get('orphanAudit'):
        previous = after['proposals'].pop()
        after.setdefault('retiredLocalCandidates', []).append({'proposal':previous,'audit':value['orphanAudit']})
    if value['task'] == 'rework-rejected':
        p = _proposal(after, value['mr']['number'])
        if p:
            if p is not after['proposals'][-1]:
                raise ValueError('a newer proposal supersedes this rejected task')
            if p.get('rejectionReview'):
                # A later task assessment must not leave the old assessment claiming
                # a now different reason. Retain it as history, not as current proof.
                p.setdefault('rejectionReviewHistory', []).append(p.pop('rejectionReview'))
            if (p.get('mr') or {}).get('state') == 'open':
                p.setdefault('mrHistory', [copy.deepcopy(p['mr'])])
            p.update(status='rejected', mr={**value['mr'], 'observedAt': value['observedAt']},
                     rejectionReason=_reason(value, choice, p))
    return after


def _result(value, choice, outcome, at, reason):
    return {'key': value['key'], 'task': value['task'], 'sourceMR': value['mr'],
            'review': copy.deepcopy(choice), 'outcome': outcome, 'processedAt': at, 'reason': reason}


def _set_result(after, result):
    rows = after.setdefault('taskResults', [])
    index = next((i for i, row in enumerate(rows) if row['key'] == result['key']), None)
    if index is None:
        rows.append(result)
    else:
        prior = rows[index]
        if prior.get('processedAt') != result['processedAt']:
            result['history'] = [*prior.get('history', []), {k:v for k,v in prior.items() if k!='history'}]
        rows[index] = result


def no_attempt(session, data, bank, before, bindings=None):
    value = context(session, data, bank, before, bindings)
    choice = decision(session, data, bank, bindings)
    if not choice or choice['action'] in {'repair', 'verify-applicability'} or bank['point'] != 'task-complete':
        raise ValueError('task has not completed a no-research decision')
    if bank.get('report') or bank.get('evidence'):
        raise ValueError('actual research cannot be hidden by a no-attempt decision')
    after = observed_state(value, before, choice)
    _set_result(after, _result(value, choice, choice['action'], choice['assessedAt'], choice['rationale']))
    return after


def attempt_state(session, data, bank, before, payload, bindings=None):
    value = context(session, data, bank, before, bindings)
    choice = decision(session, data, bank, bindings)
    if choice and choice['action'] not in {'repair', 'verify-applicability'}:
        raise ValueError('this reviewer decision does not call for new icon research')
    if instant(payload['checkedAt'], 'checkedAt') < instant(value['observedAt'], 'task observedAt'):
        raise ValueError('new task cannot reuse historical search/check dates')
    if value['task'] in REWORK and payload['outcome'] == 'unchanged':
        raise ValueError('ordinary unchanged does not prove reviewer requirements satisfied')
    after = observed_state(value, before, choice)
    after.update(lastAttemptAt=payload['checkedAt'], lastAttemptResult='verified' if payload['outcome']=='unchanged' else payload['outcome'])
    if payload['outcome'] == 'unchanged':
        after.update(lastVerifiedAt=payload['checkedAt'], verifiedIconHash=payload['iconHash'])
    if payload['outcome'] == 'candidate':
        old = after.get('proposals', [])
        candidate = payload['candidate']
        proposal = {'status':'pending', 'baseIconHash':payload['iconHash'], 'candidateIconHash':candidate['iconHash'],
                    'candidate':candidate, 'checkedAt':payload['checkedAt'], 'sources':payload['sources'],
                    'assessment':payload['assessment'], 'reason':payload['reason'], 'taskKey':value['key']}
        if value['task'] == 'rework-changes':
            previous = _proposal(after, value['mr']['number'])
            if previous:
                if previous is not old[-1]:
                    raise ValueError('rework cannot discard another proposal')
                proposal['revisionHistory'] = [*previous.get('revisionHistory', []),
                    {k:v for k,v in previous.items() if k != 'revisionHistory'}]
                old = old[:-1]
            proposal['revision'] = {'requestKey':value['key'], 'context':bank['taskContext'],
                'mr':value['mr'], 'sourceHead':value['sourceHead'], 'branchBase':value['branchBase']}
        if value['task'] == 'rework-rejected':
            proposal['supersedesMR'] = value['mr']['url']
        after['proposals'] = [*old, proposal]
    _set_result(after, _result(value, choice, payload['outcome'], payload['checkedAt'], payload['reason']))
    return after


def revision(session, data, bank, before=None, bindings=None):
    value = context(session, data, bank, before, bindings)
    if not value or value['task'] != 'rework-changes':
        return None
    choice = decision(session, data, bank, bindings)
    if not choice or choice['action'] not in {'repair', 'verify-applicability'}:
        raise ValueError('open-MR task has no sourced repair decision')
    return {'schemaVersion':1, 'task':value, 'decision':choice, 'requestKey':value['key'],
            'request':{'event':{'source':{'url':value['mr']['url']}, 'prBody':value['prBody']}},
            **{k:value[k] for k in ('baseCommit','sourceHead','branchBase','mr','bank')}}


def validate_response(session, data, bank, payload):
    value = context(session, data, bank)
    if not value or value['task'] not in REWORK:
        if 'taskResponse' in payload:
            raise ValueError('unsolicited task response')
        return
    choice = decision(session, data, bank)
    response = payload.get('taskResponse')
    if type(response) is not list or len(response) != len(choice['requirements']):
        raise ValueError('each reviewer requirement needs an actual candidate resolution')
    for requirement, row in zip(choice['requirements'], response):
        if type(row) is not dict or row.keys() != {'url','quote','resolution'} or any(row[k] != requirement[k] for k in requirement):
            raise ValueError('candidate response dropped or changed a reviewer requirement')
        required(row, 'resolution')
    # Fresh checks remain mandatory. A technical repair cannot republish identical
    # rejected bytes by treating old render files as a reconsideration release.
    from state_results import _prior_state
    previous = _proposal(_prior_state(data, bank['id']), value['mr']['number'])
    if previous and choice['action'] == 'repair' and payload['candidate']['sha256'] == previous.get('candidateIconHash'):
        raise ValueError('repair must change the rejected final bytes and be newly verified')


def record_response(proposal, payload):
    if 'taskResponse' in payload:
        proposal['taskResponse'] = copy.deepcopy(payload['taskResponse'])


def published(after, observation, final_hash):
    proposal = after['proposals'][-1]
    if not proposal.get('taskKey'):
        return
    row = next(r for r in after['taskResults'] if r['key'] == proposal['taskKey'])
    row.update(outcome='published', publication={'mr':observation, 'candidateIconHash':final_hash})


def state_live(session, data, bank, after):
    value = context(session, data, bank)
    if not value:
        return
    p = next((p for p in reversed(after.get('proposals', [])) if p.get('taskKey') == value['key']), {})
    observed = p.get('mr')
    verify_live(value, published_head=observed['headCommit'] if observed and value['task']=='rework-changes' else None)
    # Don't ask the original head check to reject our own exact append.
    if bank.get('taskDecision'):
        choice = decision(session, data, bank)
        for src in choice['sources']:
            if intake.message(value['target'], value['mr']['number'], src['kind'], src['nodeId']) != src:
                raise ValueError('task source permissions/text changed before state publication')


def validate_history(before):
    values = (before or {}).get('taskResults', [])
    if type(values) is not list:
        raise ValueError('taskResults must be a list')
    seen = set()
    for row in values:
        if not isinstance(row,dict) or row.get('task') not in TASKS or row.get('outcome') not in {
                'candidate','published','unchanged','unconfirmed','error','needs-human','keep-current','defer','unclear'}:
            raise ValueError('invalid task result')
        key = require_hash(row.get('key'), 'task result key')
        if key in seen:
            raise ValueError('duplicate task result')
        instant(row.get('processedAt'), 'task processedAt')
        seen.add(key)
    return values


def publication_live(session, id):
    """Task checks immediately before each new asset/PR write, not at plan time."""
    from asset_results import load_candidate
    snapshot = json.loads(state.regular(Path(session)/'session.json'))
    if not any(b['id']==id and b.get('taskContext') for b in snapshot.get('banks', [])):
        return
    data, bank, _, _ = load_candidate(session, id)
    if bank.get('taskContext'):
        state_live(session, data, bank, bank['state'])


def overlay(original, current, own):
    """Advance only the proven earlier stage of this exact task in this session."""
    if not own or current is None:
        raise ValueError('task state changed by another writer; do not overwrite it')
    before, after = original['before'] or {}, original['after']
    # The caller verified this session's landed package and full current result.
    # Exact no-ops include no-search/failed attempts without a task candidate.
    if current == after:
        return copy.deepcopy(after)
    if (after.get('retiredLocalCandidates') != before.get('retiredLocalCandidates') and
            after.get('retiredLocalCandidates', [{}])[-1].get('completion')):
        from task_completion import before_completion
        candidate = before_completion(after)
        # Recursively apply the EXISTING stage/lineage guards to its prior candidate.
        overlay({'before': original['before'], 'after': candidate}, current, own)
        return copy.deepcopy(after)
    final_tasks, remote_tasks = after.get('taskResults', []), current.get('taskResults', [])
    old_tasks = {r['key']:r for r in before.get('taskResults', [])}
    changed = [r for r in final_tasks if old_tasks.get(r['key']) != r]
    if len(changed) != 1:
        raise ValueError('a task update must advance exactly one task result')
    key = changed[0]['key']
    remote = next((r for r in remote_tasks if r['key']==key), None)
    if remote is None or any(remote.get(k)!=changed[0].get(k) for k in
                            ('task','sourceMR','review','processedAt','reason','history')):
        raise ValueError('task provenance changed between stages')
    if remote['outcome']=='published' and changed[0]['outcome']!='published':
        raise ValueError('cannot roll back a published task')
    if [r for r in final_tasks if r['key']!=key] != [r for r in remote_tasks if r['key']!=key]:
        raise ValueError('unrelated task history changed')
    final, prior = after.get('proposals', []), current.get('proposals', [])
    if not final or len(final)!=len(prior) or final[:-1]!=prior[:-1] or final[-1].get('taskKey')!=key:
        raise ValueError('task proposal lineage changed')
    f,p=final[-1],prior[-1]
    if any(f.get(k)!=p.get(k) for k in ('taskKey','revision','revisionHistory','supersedesMR','baseIconHash','checkedAt')):
        raise ValueError('task proposal source identity changed')
    def stage(v):
        return 4 if v['status'] in {'accepted','rejected'} else 3 if v.get('mr') else 2 if v.get('approval') or v.get('agentAssessment') else 1 if v.get('reviewPackage') else 0
    if stage(f)<stage(p) or (stage(p)>=1 and f['candidateIconHash']!=p['candidateIconHash']):
        raise ValueError('task candidate cannot be rolled back or changed after preparation')
    if f.get('sourceCandidate',f['candidate'])!=p.get('sourceCandidate',p['candidate']):
        raise ValueError('task original source changed')
    allowed={'proposals','taskResults','lastAttemptAt','lastAttemptResult','lastVerifiedAt','verifiedIconHash'}
    if any(after.get(k)!=current.get(k) for k in (set(after)|set(current))-allowed):
        raise ValueError('task update changes unrelated bank state')
    return copy.deepcopy(after)


def attachment_context(session, data, bank):
    value = context(session,data,bank)
    if not value or value['task'] not in REWORK or not bank.get('taskDecision'):
        return None
    choice = decision(session,data,bank)
    if choice['selectedUrl'] is None:
        return None
    return {'requestKey':value['key'], 'mr':value['mr'], 'request':{'selectedUrl':choice['selectedUrl'],
            'recordedAt':choice['assessedAt'], 'event':{'source':{'url':value['mr']['url']}}}}
