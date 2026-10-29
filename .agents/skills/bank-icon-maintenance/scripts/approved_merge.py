"""Explicit approved-PR merge, independently recoverable without render sessions.

Operation manifests/receipts live in the serviced repository's Git common dir.
They are compact write intents, not another bank-state database or old renders.
GitHub rules are never bypassed; merge and state authority are separate.
"""
import argparse
import copy
from datetime import datetime, timezone
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

import feedback_intake as intake
import github_pr
import state_publication as state
import operation_budget as budget
from evidence import instant, required, require_hash

KIND = 'approved-merge'
# One readiness check, not each retry, owns this deadline. Nested API reads use
# operation_budget's minimum of this and the caller's remaining command budget.
READINESS_WAIT_SECONDS = 30
READINESS_RETRY_DELAYS = (2, 4, 8, 8)
_QUERY = '''query($owner:String!,$name:String!,$number:Int!) {
 repository(owner:$owner,name:$name) { pullRequest(number:$number) {
   number isDraft reviewDecision mergeStateStatus headRefOid baseRefOid baseRefName
   commits(last:1) { nodes { commit { oid statusCheckRollup { state } } } }
 } }
}'''


def _now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')


def operation_root(repo):
    repo = Path(repo).resolve(strict=True)
    root = state.git(repo,'rev-parse','--show-toplevel').stdout.decode().strip()
    if Path(root).resolve() != repo:
        raise ValueError('--repo must be the checkout root')
    common = Path(state.git(repo,'rev-parse','--git-common-dir').stdout.decode().strip())
    if not common.is_absolute():
        common = repo/common
    destination = common.resolve()/'bank-icon-maintenance'/'operations'
    for path in (destination.parent,destination):
        if path.is_symlink():
            raise ValueError('durable operation directory is a symlink')
        path.mkdir(exist_ok=True)
    return destination


class ReadinessError(ValueError):
    """A readable stop with a stable code for merge-list diagnostics."""
    def __init__(self, code, reason):
        super().__init__(reason)
        self.code = code


class ReadinessPending(ReadinessError):
    """Known temporary uncertainty before any write, never an unknown write result."""
    def __init__(self, target, number, attempts, started, reason):
        super().__init__('merge-readiness-pending', reason)
        self.target, self.number, self.attempts = target, number, attempts
        self.elapsed = round(time.monotonic() - started, 3)

    def result(self):
        return {'status': self.code, 'code': self.code, 'target': self.target,
                'mr': self.number, 'reason': str(self), 'attempts': self.attempts,
                'maxAttempts': len(READINESS_RETRY_DELAYS) + 1,
                'elapsedSeconds': self.elapsed, 'mergeAttempted': False,
                'nextAction': 'Mark this MR pending and continue the finite selection; '
                              'do not publish, invent a conflict or loop over the queue. '
                              'A later attempt requires fresh readiness; unknown writes '
                              'use merge-reconcile instead.'}


def _readiness_progress(target, number, attempt, status, started, delay=None):
    value = {'kind': 'merge-readiness-progress', 'target': target, 'mr': number,
             'attempt': attempt, 'maxAttempts': len(READINESS_RETRY_DELAYS) + 1,
             'mergeStateStatus': status, 'elapsedSeconds': round(time.monotonic() - started, 3)}
    if delay is not None:
        value['retryAfterSeconds'] = delay
    # stdout remains exactly one final command JSON; no untrusted review prose.
    print(json.dumps(value, ensure_ascii=False), file=sys.stderr, flush=True)


def _approvals(target, number, head):
    # Read submitted reviews even when GitHub has no aggregate reviewDecision.
    # COMMENTED/PENDING are not votes; they must not erase an earlier decision.
    rows = intake._pages(f'repos/{target}/pulls/{number}/reviews')
    latest, seen = {}, set()
    for row in rows:
        if not isinstance(row, dict) or row.get('state') not in {
                'APPROVED', 'CHANGES_REQUESTED', 'DISMISSED', 'COMMENTED', 'PENDING'}:
            raise ReadinessError('reviews-incomplete', 'review response has an unknown or missing state')
        if row['state'] in {'COMMENTED', 'PENDING'}:
            continue
        user = row.get('user')
        if (not isinstance(user, dict) or type(row.get('id')) is not int or row['id'] <= 0):
            raise ReadinessError('reviews-incomplete', 'review identity is incomplete')
        if row['id'] in seen:
            raise ReadinessError('reviews-incomplete', 'duplicate review ID; discussion may have changed during pagination')
        seen.add(row['id'])
        login = required(user, 'login').casefold()
        at = instant(row.get('submitted_at'), 'review.submitted_at')
        if at > datetime.now(timezone.utc):
            raise ReadinessError('reviews-incomplete', 'review submission is in the future')
        if row['state'] in {'APPROVED', 'CHANGES_REQUESTED'} and (
                type(row.get('commit_id')) is not str or
                re.fullmatch(r'[0-9a-f]{40}', row['commit_id']) is None):
            raise ReadinessError('reviews-incomplete', 'review does not identify a commit')
        # IDs order distinct reviews submitted in the same timestamp second.
        order = (at, row['id'])
        if login not in latest or order > latest[login][0]:
            latest[login] = (order, row)
    if any(row['state'] == 'CHANGES_REQUESTED' for _, row in latest.values()):
        raise ReadinessError('changes-requested', 'a current change request remains unresolved')
    approvals = []
    for _, row in latest.values():
        if row['state'] == 'APPROVED' and row['commit_id'] == head and row['user'].get('type') == 'User':
            # Current permission is re-read on preparation AND immediately before
            # merge. Author association or a nonempty aggregate is not authority.
            trust = intake._trusted(target, {'__typename': 'User', 'login': row['user']['login']}, {})
            approvals.append({'id': row['id'], 'login': trust['login'], 'permission': trust['permission'],
                              'commit': row['commit_id'], 'submittedAt': row['submitted_at']})
    if not approvals:
        raise ReadinessError('no-current-approval',
            'no trusted human approval of this exact current head; historical approval is insufficient')
    return sorted(approvals, key=lambda r: r['id'])


def _readiness_once(target, number, expected_mr=None):
    """One complete observation; only the explicit UNKNOWN enum is retryable."""
    mr = github_pr.inspect(target, number)
    if mr['state'] != 'open':
        raise ReadinessError('not-open', 'merge preparation requires an open, unmerged PR')
    if expected_mr is not None and not _same_pr(mr, expected_mr):
        raise ReadinessError('ref-changed',
            'merge readiness: PR/head/target changed while waiting; prepare a fresh package')
    owner, name = target.split('/')
    response = github_pr._api('graphql', '-f', f'query={_QUERY}', '-f', f'owner={owner}',
                              '-f', f'name={name}', '-F', f'number={number}')
    try:
        if response.get('errors'):
            raise ValueError('partial GraphQL response')
        pr = response['data']['repository']['pullRequest']
        commits = pr['commits']['nodes']
        if len(commits) != 1:
            raise ValueError('missing or ambiguous head commit')
        commit = commits[0]['commit']
        status = commit['statusCheckRollup']
        # A successful explicit null is different from an omitted/failed field.
        decision = pr['reviewDecision']
        if decision not in (None, 'APPROVED', 'REVIEW_REQUIRED', 'CHANGES_REQUESTED'):
            raise ValueError('unknown aggregate review decision')
        if status is not None and (not isinstance(status, dict) or 'state' not in status):
            raise ValueError('incomplete commit checks')
        if type(pr['number']) is not int or pr['number'] != number or type(pr['isDraft']) is not bool:
            raise ValueError('incomplete PR identity')
        merge_status, head, base, oid = pr['mergeStateStatus'], pr['headRefOid'], pr['baseRefOid'], commit['oid']
        if not isinstance(merge_status, str) or not merge_status:
            raise ValueError('missing merge state')
        base_name = required(pr, 'baseRefName')
        from proposal_sync import _commit
        _commit(base)
        _commit(mr['baseCommit'])
    except (AttributeError, TypeError, KeyError, IndexError, ValueError) as exc:
        raise ReadinessError('readiness-incomplete', 'merge readiness response incomplete') from exc
    if pr['isDraft']:
        raise ReadinessError('draft', 'merge readiness: PR is a draft')
    if head != mr['headCommit'] or oid != mr['headCommit'] or base_name != mr['base']:
        raise ReadinessError('ref-changed', 'merge readiness: head/target branch changed during inspection')
    if decision in ('REVIEW_REQUIRED', 'CHANGES_REQUESTED'):
        raise ReadinessError('review-required' if decision == 'REVIEW_REQUIRED' else 'changes-requested',
                             f'merge readiness: reviewDecision={decision}; no administrative bypass')
    if status is not None and status['state'] != 'SUCCESS':
        raise ReadinessError('ci-not-passing', f'merge readiness: CI status={status["state"]}')
    if merge_status not in ('CLEAN', 'UNKNOWN'):
        raise ReadinessError('merge-not-ready', f'merge readiness: mergeStateStatus={merge_status}; no administrative bypass')
    # Never wait for a new approval or for a change request/permission to clear.
    return mr, _approvals(target, number, mr['headCommit']), merge_status


def readiness(target, number, *, expected_mr=None, expected_base=None):
    """Shared bounded wait for discovery, preparation and the pre-write gate.

    No writes, retries of merge requests, or package rebinding happen here. Only
    complete UNKNOWN observations are retried; all other errors propagate once.
    """
    started, attempts, saw_unknown = time.monotonic(), 0, False
    pinned_mr = copy.deepcopy(expected_mr)
    pinned_target = expected_base
    try:
        with budget.bounded(READINESS_WAIT_SECONDS):
            for index in range(len(READINESS_RETRY_DELAYS) + 1):
                budget.check()
                attempts += 1
                mr, approvals, status = _readiness_once(target, number, pinned_mr)
                saw_unknown = saw_unknown or status == 'UNKNOWN'
                budget.check()
                if pinned_mr is None:
                    pinned_mr = copy.deepcopy(mr)
                if saw_unknown or pinned_target is not None:
                    actual = _target_commit(target, mr['base'])
                    budget.check()
                    if pinned_target is None:
                        pinned_target = actual
                    elif actual != pinned_target:
                        raise ReadinessError('target-ref-changed',
                            'merge readiness: target branch changed while waiting; '
                            'prepare a fresh package, do not rewrite the original')
                if status == 'CLEAN':
                    if saw_unknown:
                        _readiness_progress(target, number, attempts, status, started)
                    return mr, approvals
                if index == len(READINESS_RETRY_DELAYS):
                    _readiness_progress(target, number, attempts, status, started)
                    raise ReadinessPending(target, number, attempts, started,
                        'GitHub merge readiness remained UNKNOWN after bounded reads; no merge attempted')
                # Include API calls AND sleeps; do not reset the timer per retry.
                delay = budget.timeout(READINESS_RETRY_DELAYS[index])
                _readiness_progress(target, number, attempts, status, started, delay)
                time.sleep(delay)
                budget.check()
    except budget.Exhausted as exc:
        if saw_unknown:
            raise ReadinessPending(target, number, attempts, started,
                'GitHub merge readiness was not established before the read deadline; '
                'no merge attempted') from exc
        raise ReadinessError('readiness-timeout',
            'merge readiness read deadline expired before a complete UNKNOWN observation; '
            'no merge attempted') from exc


def _target_commit(target, branch):
    """Read the branch ref, not a PR's possibly historical base.sha/baseRefOid."""
    github_pr.normalize(target)
    github_pr._branch(branch)
    value = github_pr._api(f'repos/{target}/git/ref/heads/{quote(branch, safe="/")}')
    try:
        from proposal_sync import _commit
        if value['ref'] != f'refs/heads/{branch}' or value['object']['type'] != 'commit':
            raise ValueError('different ref or non-commit target')
        return _commit(value['object']['sha'])
    except (KeyError, TypeError, ValueError) as exc:
        raise ReadinessError('target-ref-incomplete', 'current target branch reference is incomplete') from exc


def _same_pr(current, original):
    # Keep the original observation intact in the manifest. Only its base SHA is
    # observational; the independently checked target is manifest.baseCommit.
    return ({k: v for k, v in current.items() if k != 'baseCommit'} ==
            {k: v for k, v in original.items() if k != 'baseCommit'})


def _check_target(repo, target, branch, expected):
    from asset_publication import _ref
    api = _target_commit(target, branch)
    advertised = _ref(repo, target, f'refs/heads/{branch}')
    if api != expected or advertised != expected:
        raise ReadinessError('target-ref-changed',
            f'target branch changed or API/Git disagree: expected {expected}, '
            f'API {api}, Git {advertised}; prepare a fresh package, do not rewrite it')


def prepare(args):
    if args.method not in {'merge','squash','rebase'}:
        raise ValueError('explicit supported merge method required')
    repo=Path(args.repo).resolve(strict=True)
    remote=state.resolve_remote(repo,args.remote)
    target=intake.repository_from_remote(remote)
    mr, approvals=readiness(target,args.mr)
    if mr['base']!=args.base:
        raise ValueError('approved MR targets another branch')
    from asset_publication import _blob, _bank_base
    from proposal_sync import _fetch_ref
    expected_base = _target_commit(target, args.base)
    # Existing snapshot transport owns a unique ref and never writes FETCH_HEAD.
    base=_fetch_ref(repo,target,f'refs/heads/{args.base}')
    head=_fetch_ref(repo,target,f'refs/pull/{args.mr}/head')
    if base != expected_base:
        raise ReadinessError('target-ref-changed', 'target branch changed during merge preparation')
    if head != mr['headCommit']:
        raise ReadinessError('head-changed', 'approved PR head changed during merge preparation')
    files=intake._pages(f'repos/{target}/pulls/{args.mr}/files')
    if len(files)!=1 or files[0].get('status') not in {'added','modified'}:
        raise ValueError('merge task only accepts one added/modified bank icon')
    bank=intake._bank(repo,base,files[0]['filename'])
    branch_base=state.git(repo,'merge-base',base,head).stdout.decode().strip()
    _bank_base(repo,branch_base,bank)
    from feedback_revision import _proof
    _proof(repo,base,bank,None,mr,head,branch_base)
    final_hash=state.digest(_blob(repo,head,files[0]['filename']))
    _,source_commit,_,_=state.initial_state(repo,argparse.Namespace(remote=remote,offline=False))
    before,_=state.remote_record(repo,source_commit,f"banks/state/{bank['id']}.json")
    current, latest_approvals = readiness(target, args.mr, expected_mr=mr, expected_base=base)
    if not _same_pr(current, mr) or latest_approvals != approvals:
        raise ValueError('approval/head/target changed during prepare; create a fresh merge package')
    _check_target(repo, target, args.base, base)
    manifest={'schemaVersion':1,'scope':KIND,'repo':str(repo),'target':target,'stateRemote':remote,
              'base':args.base,'baseCommit':base,'mr':mr,'id':bank['id'],'bank':bank,'branchBase':branch_base,
              'candidateIconHash':final_hash,'method':args.method,'approvals':approvals,
              'stateCommit':source_commit,'before':before}
    key=state.digest(state.encoded(manifest))
    directory=operation_root(repo)/('merge-'+key)
    directory.mkdir(exist_ok=True)
    state.save_new(directory/'manifest.json',state.encoded(manifest))
    data={'schemaVersion':1,'purpose':KIND,'repo':str(repo),'baseCommit':base,'stateRemote':remote,
          'stateCommit':source_commit,'selectedIds':[bank['id']]}
    state.save_new(directory/'session.json',state.encoded(data))
    result={'status':'awaiting-merge-authorization','operation':str(directory),'packageHash':key,
            'package':manifest,'nextAction':'Show the exact head/method; merge-publish requires separate merge authority. No renders or old session are needed.'}
    import standing_authorization
    if standing_authorization.configured():
        try:
            authorize(directory,manifest,key,None)
            result.update(status='ready-to-merge',publicationAuthority={'status':'covered','action':'mergeApproved'},
                          nextAction='Show the exact merge package and use merge-publish; do not ask again for covered authority.')
        except (ValueError,OSError) as exc:
            result['publicationAuthority']={'status':'blocked','reason':str(exc)}
    return result


def load(directory):
    directory=Path(directory).resolve(strict=True)
    value=json.loads(state.regular(directory/'manifest.json'))
    key=state.digest(state.encoded(value))
    if directory!=operation_root(value['repo'])/('merge-'+key) or value.get('scope')!=KIND:
        raise ValueError('merge operation binding changed')
    return directory,value,key


def authorize(directory,manifest,key,path):
    if path:
        grant=json.loads(state.regular(Path(path).absolute()))
        expected={'schemaVersion':1,'scope':KIND,'decision':'authorize','packageHash':key,
                  'target':manifest['target'],'base':manifest['base'],'id':manifest['id'],
                  'number':manifest['mr']['number'],'headCommit':manifest['mr']['headCommit'],'method':manifest['method']}
        if any(grant.get(k)!=v for k,v in expected.items()):
            raise ValueError('merge authority must bind exact MR/head/method')
        required(grant,'authorizer');required(grant,'statement')
        if instant(grant.get('authorizedAt'),'authorizedAt')>datetime.now(timezone.utc):
            raise ValueError('future merge authority')
        return grant
    import standing_authorization as standing
    grant,pin=standing.load(manifest['repo'],directory)
    permission=grant.get('merge')
    if (grant['actions'].get('mergeApproved') is not True or not permission or
            permission['target']!=manifest['target'] or permission['base']!=manifest['base'] or
            manifest['method'] not in permission['methods'] or
            (grant['ids']!='selected' and manifest['id'] not in grant['ids'])):
        raise ValueError('standing authority does not cover approved merge; asset/state permissions do not imply merge')
    return {'policy':pin,'action':'mergeApproved','authorizer':grant['authorizer'],'statement':grant['statement'],
            'headCommit':manifest['mr']['headCommit'],'packageHash':key}


def _receipt(directory):
    path=directory/'merge.receipt.json'
    return json.loads(state.regular(path)) if path.exists() else {'status':'not-attempted'}


def _save(directory,value):
    state.save_receipt(directory,'merge',{**value,'observedAt':_now()})
    return value


def merge(args):
    directory,manifest,key=load(args.operation)
    authority=authorize(directory,manifest,key,args.authorization)
    with state.writer(directory):
        prior=_receipt(directory)
        if prior['status']!='not-attempted':
            return reconcile(directory,manifest)
        current,approvals=readiness(manifest['target'],manifest['mr']['number'],
                                    expected_mr=manifest['mr'], expected_base=manifest['baseCommit'])
        if not _same_pr(current, manifest['mr']) or approvals!=manifest['approvals']:
            raise ValueError('approval/head/base changed; prepare a new exact merge package')
        from asset_publication import _bank_base, _ref
        repo = Path(manifest['repo'])
        _bank_base(repo, manifest['baseCommit'], manifest['bank'])
        if _ref(repo, manifest['target'], f"refs/pull/{manifest['mr']['number']}/head") != manifest['mr']['headCommit']:
            raise ReadinessError('head-changed', 'approved PR head changed before merge; no write attempted')
        _check_target(repo, manifest['target'], manifest['base'], manifest['baseCommit'])
        if authorize(directory,manifest,key,args.authorization)!=authority:
            raise ValueError('merge permission changed before write')
        _save(directory,{'status':'outcome-unknown','attempted':True,'authority':authority})
        try:
            github_pr._api(f"repos/{manifest['target']}/pulls/{manifest['mr']['number']}/merge",
                           '-X','PUT','-f',f"sha={manifest['mr']['headCommit']}",'-f',f"merge_method={manifest['method']}")
        except ValueError:
            pass  # A lost response is not permission to send another merge request.
        return reconcile(directory,manifest)


def observe(manifest):
    from asset_publication import _blob
    from proposal_sync import _commit, _ancestor, _fetch_ref
    repo=Path(manifest['repo'])
    mr=github_pr.outcome(manifest['target'],manifest['mr']['number'])
    if mr['state']!='merged':
        return None
    if any(mr[k]!=manifest['mr'][k] for k in ('number','url','target','base','headCommit')):
        raise ValueError('different MR/head was merged; preserve operation for investigation')
    merged=_commit(mr['mergeCommit'])
    current=_fetch_ref(repo,manifest['target'],f"refs/heads/{manifest['base']}")
    path=manifest['bank']['folder']+'/icon.svg'
    from git_catalog import identity_paths
    if (not _ancestor(repo,merged,current) or state.digest(_blob(repo,merged,path))!=manifest['candidateIconHash'] or
            state.digest(_blob(repo,merged,manifest['bank']['folder']+'/info.json'))!=manifest['bank']['metadataHash'] or
            identity_paths(repo,merged,manifest['id'])!=[manifest['bank']['folder']+'/info.json']):
        raise ValueError('merged bank bytes or ancestry do not match approved head')
    return mr


def after_state(manifest,observed):
    after=copy.deepcopy(manifest['before']) if manifest['before'] is not None else {'schemaVersion':1}
    proposals=after.setdefault('proposals',[])
    matches=[p for p in proposals if (p.get('mr') or {}).get('url')==observed['url']]
    if len(matches)>1:
        raise ValueError('ambiguous merge state history')
    if matches:
        p=matches[0]
        if p.get('mr',{}).get('state')=='open':
            p.setdefault('mrHistory',[copy.deepcopy(p['mr'])])
    else:
        p={'status':'accepted','baseIconHash':manifest['bank']['iconHash'],
           'candidateIconHash':manifest['candidateIconHash']}
        proposals.append(p)
    p.update(status='accepted',mr={**observed,'observedAt':observed['mergedAt']},mrOutcome=observed,
             acceptedIconHash=manifest['candidateIconHash'],acceptedHeadCommit=observed['headCommit'])
    p['mergeReceipt']={'headCommit':observed['headCommit'],'mergeCommit':observed['mergeCommit'],
                      'mergedAt':observed['mergedAt'],'method':manifest['method']}
    # No search, renderer invocation or verification time is invented by merging.
    return after


def reconcile(directory,manifest):
    try:
        observed=observe(manifest)
    except (ValueError,OSError) as exc:
        return _save(directory,{**_receipt(directory),'status':'outcome-unknown','reason':str(exc)})
    if observed is None:
        return _save(directory,{**_receipt(directory),'status':'outcome-unknown',
            'nextAction':'Read-only merge-reconcile; an attempted merge is never blindly retried or reported as merged.'})
    result=_save(directory,{**_receipt(directory),'status':'merged','mr':observed})
    root=directory/'state-publication';root.mkdir(exist_ok=True)
    objects=state.object_repo(root)
    head=state.fetch_head(objects,manifest['stateRemote'])
    before,before_hash=state.remote_record(objects,head,f"banks/state/{manifest['id']}.json")
    after=after_state(manifest,observed)
    if before==after:
        return {**result,'stateStatus':'synced','stateCommit':head,'operation':str(directory)}
    if before!=manifest['before']:
        return {**result,'stateStatus':'conflict','reason':'bank state changed; merge succeeded but no newer history was overwritten','operation':str(directory)}
    bindings={str(directory/name):state.digest(state.regular(directory/name)) for name in ('session.json','manifest.json')}
    record={'id':manifest['id'],'path':f"banks/state/{manifest['id']}.json",'before':before,'beforeSha256':before_hash,'after':after}
    package={'schemaVersion':1,'scope':'state-publication','target':manifest['stateRemote'],'ref':state.REF,
             'baseCommit':manifest['baseCommit'],'stateHistory':'present' if head else 'unknown','observedRef':head,
             'ids':[manifest['id']],'records':[record],'results':[{'observed':observed}],
             'reconciliation':{'kind':KIND},'bindings':bindings}
    key=state.digest(state.encoded(package));state.save_new(root/f'{key}.json',state.encoded(package))
    from standing_authorization import describe_package
    return describe_package({**result,'status':'awaiting-state-authorization','mergeStatus':'merged',
        'operation':str(directory),'packageHash':key,'package':package,
        'nextAction':'Publish this state package under separate state authority using --session OPERATION. Do not merge again.'},directory,'statePublish')


def authorization_context(directory,package):
    directory,manifest,_=load(directory)
    data=json.loads(state.regular(directory/'session.json'))
    expected={str(directory/name):state.digest(state.regular(directory/name)) for name in ('session.json','manifest.json')}
    if (package['reconciliation']!={'kind':KIND} or package['ids']!=[manifest['id']] or
            package['target']!=manifest['stateRemote'] or package['baseCommit']!=manifest['baseCommit'] or
            package['bindings']!=expected):
        raise ValueError('merge-state package lost its exact operation binding')
    return data


def validate_package(directory,package,objects,head):
    authorization_context(directory,package)
    _,manifest,_=load(directory)
    current=observe(manifest)
    if current is None or package['results']!=[{'observed':current}]:
        raise ValueError('merge observation changed')
    before,sha=state.remote_record(objects,package['observedRef'],f"banks/state/{manifest['id']}.json")
    if before!=manifest['before'] or package['records']!=[{'id':manifest['id'],'path':f"banks/state/{manifest['id']}.json",
            'before':before,'beforeSha256':sha,'after':after_state(manifest,current)}]:
        raise ValueError('merge-state package modifies more than observed acceptance')


def list_approved(args):
    repo = Path(args.repo).resolve(strict=True)
    target = intake.repository_from_remote(state.resolve_remote(repo, args.remote))
    if type(args.limit) is not int or not 1 <= args.limit <= 200:
        raise ValueError('merge limit must be 1..200')
    selected, skipped, stops = [], [], []
    rows = intake._pages(f'repos/{target}/pulls?state=open', 500)
    examined = 0
    for row in rows:
        if len(selected) >= args.limit:
            break
        examined += 1
        number = row.get('number') if isinstance(row, dict) else None
        try:
            if type(number) is not int or number <= 0:
                raise ValueError('open PR list has no valid PR number')
            if row.get('draft'):
                skipped.append({'mr': number, 'code': 'draft', 'reason': 'PR is a draft'})
                continue
            base = row.get('base')
            if not isinstance(base, dict) or not isinstance(base.get('ref'), str):
                raise ValueError('open PR list has no valid target branch')
            if base['ref'] != args.base:
                skipped.append({'mr': number, 'code': 'different-base', 'reason': 'PR targets another branch'})
                continue
            # Skip unrelated PRs cheaply, with an explanation, before review reads.
            files = intake._pages(f'repos/{target}/pulls/{number}/files')
            if (len(files) != 1 or not isinstance(files[0], dict) or
                    files[0].get('status') not in {'added', 'modified'} or
                    not isinstance(files[0].get('filename'), str) or not re.fullmatch(r'banks/[^/]+_[0-9]+-(?:[A-Za-z]{2}|unknown)/icon\.svg',
                                     files[0].get('filename', ''))):
                skipped.append({'mr': number, 'code': 'not-single-bank-icon',
                                'reason': 'PR must change exactly one added/modified bank icon'})
                continue
            mr, _ = readiness(target, number)
            if mr['base'] != args.base:
                raise ReadinessError('base-changed', 'PR target changed during merge-list')
            selected.append({'mr': mr['number'], 'url': mr['url'], 'headCommit': mr['headCommit']})
        except ReadinessPending as exc:
            stops.append(exc.result())
        except ValueError as exc:
            code = getattr(exc, 'code', 'read-failed')
            destination = skipped if code in {'draft', 'not-open', 'no-current-approval'} else stops
            destination.append({'mr': number, 'code': code, 'reason': str(exc)})
        try:
            budget.check()
        except budget.Exhausted:
            break  # Keep unexamined rows in remaining; no fresh budget per MR.
    return {'status': 'merge-plan', 'target': target, 'base': args.base,
            'selected': selected, 'skipped': skipped, 'stops': stops, 'limit': args.limit,
            'examined': examined, 'remaining': len(rows) - examined,
            'limitReached': len(selected) >= args.limit,
            'nextAction': 'Read skipped/stops; an empty selection does not mean no reviews exist. Process one MR at a time: fresh prepare, separately authorized merge, then state. Do not prepare a batch against one base or refill after failures. UNKNOWN is retried only inside readiness; retain pending results without another queue loop.'}


def add_subcommands(commands):
    sub=commands.add_parser('merge-list',help='read a finite list of approved, merge-ready bank PRs')
    sub.add_argument('--repo',required=True);sub.add_argument('--remote')
    sub.add_argument('--base',default='main');sub.add_argument('--limit',type=int,default=20)
    sub=commands.add_parser('merge-prepare',help='prepare an exact approved-head merge; no new icon research')
    sub.add_argument('--repo',required=True);sub.add_argument('--remote');sub.add_argument('--mr',type=int,required=True)
    sub.add_argument('--base',default='main');sub.add_argument('--method',choices=('merge','squash','rebase'),required=True)
    for name in ('merge-publish','merge-reconcile'):
        sub=commands.add_parser(name)
        sub.add_argument('--operation',required=True)
        if name=='merge-publish':sub.add_argument('--authorization')


def dispatch(args):
    try:
        with budget.bounded(180):
            return _dispatch(args)
    except ReadinessPending as exc:
        return exc.result()


def _dispatch(args):
    if args.command=='merge-list':return list_approved(args)
    if args.command=='merge-prepare':return prepare(args)
    if args.command=='merge-publish':return merge(args)
    directory,manifest,_=load(args.operation)
    with state.writer(directory):return reconcile(directory,manifest)
