"""Small task facade: choose work, then use the shared evidence/publication tools.

No global outcome sweep and no model-generated default approvals. A finite plan
is fixed once; each object starts from fresh service state and committed catalog.
"""
import argparse
import json
from pathlib import Path
import re
import tempfile
from datetime import datetime, timezone

import github_pr
import feedback_intake as intake
import operation_budget
import session as sessions
import state_publication as state
import task_work as work

_REVIEW_QUERY = '''query($owner:String!,$name:String!,$number:Int!) {
 repository(owner:$owner,name:$name) { pullRequest(number:$number) { reviewDecision } }
}'''


def review_decision(target, number):
    owner, name = target.split('/')
    result = github_pr._api('graphql', '-f', f'query={_REVIEW_QUERY}', '-f', f'owner={owner}',
                            '-f', f'name={name}', '-F', f'number={number}')
    try:
        value = result['data']['repository']['pullRequest']['reviewDecision']
    except (KeyError, TypeError) as exc:
        raise ValueError('current review decision unavailable') from exc
    if value not in (None, 'APPROVED', 'CHANGES_REQUESTED', 'REVIEW_REQUIRED'):
        raise ValueError('unsupported review decision')
    return value


def catalog_at(repo, base):
    """An owned temporary committed worktree; never read incidental checkout edits."""
    with tempfile.TemporaryDirectory(prefix='bank-task-catalog-') as name:
        root = Path(name) / 'catalog'
        state.git(repo, 'worktree', 'add', '--detach', str(root), base, timeout=60)
        try:
            return sessions.catalog(root)
        finally:
            state.git(repo, 'worktree', 'remove', '--force', str(root), timeout=60)


def plan(args):
    with github_pr.open_path_snapshot():
        return _plan(args)


def _plan(args):
    repo = Path(args.repo).resolve(strict=True)
    base = state.git(repo, 'rev-parse', '--verify', f'{args.base}^{{commit}}').stdout.decode().strip()
    policy = sessions.policy_at(repo, base)
    limit = args.limit if args.limit is not None else policy['selectionLimit']
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError('task limit must be 1..200 objects, not successful MRs')
    source = argparse.Namespace(remote=args.remote, offline=False)
    ref, commit, remote, read = state.initial_state(repo, source)
    target = intake.repository_from_remote(remote)
    entries, errors = catalog_at(repo, base)
    paths = set(state.git(repo, 'ls-tree', '-r', '--name-only', commit, '--', 'banks/state/').stdout.decode().splitlines()) if commit else set()
    cache, selected, stops = {}, [], []
    def bank_state(id):
        if id not in cache:
            value, error = sessions.read_state(repo, commit, paths, id)
            if error:
                raise ValueError(error)
            cache[id] = value
        return cache[id]
    timestamp = datetime.now(timezone.utc)
    data = {'repo':str(repo), 'baseCommit':base, 'stateCommit':commit, 'stateRemote':remote, 'now':work.now()}
    if args.task in {'update-existing','add-missing'}:
        def rank(id):
            before = bank_state(id)
            return ((before or {}).get('lastAttemptAt') or '', id)
        # Invalid state remains a per-bank stop rather than breaking sort.
        candidates = []
        for id, matches in entries.items():
            try:
                if len(matches)!=1 or errors.get(id) or matches[0]['problem']:
                    raise ValueError('invalid or duplicate catalog identity')
                if (matches[0]['iconHash'] is None) != (args.task=='add-missing'):
                    continue
                if sessions.check_due(bank_state(id) or {}, matches[0]['iconHash'], timestamp, policy):
                    candidates.append(id)
            except ValueError as exc:
                stops.append({'id':id,'reason':str(exc)})
        # Missing icons are filled newest-ID first, before applying the finite limit.
        ordered = sorted(candidates, reverse=True) if args.task == 'add-missing' else sorted(candidates, key=rank)
        objects = [(id, None) for id in ordered]
    else:
        state_filter = 'closed' if args.task=='rework-rejected' else 'open'
        rows = intake._pages(f'repos/{target}/pulls?state={state_filter}&sort=updated&direction=desc', 500)
        def mr_objects():
            for row in rows:
                if row.get('merged_at') is not None:
                    continue
                number = row.get('number')
                try:
                    if args.task=='rework-changes' and review_decision(target, number)!='CHANGES_REQUESTED':
                        continue
                    files = intake._pages(f'repos/{target}/pulls/{number}/files')
                    if len(files)!=1:
                        continue
                    match = re.fullmatch(r'banks/[^/]+_([0-9]+)-(?:[a-zA-Z]{2}|unknown)/icon\.svg', files[0].get('filename',''))
                    if match:
                        yield int(match[1]), number
                except ValueError as exc:
                    stops.append({'mr':number,'reason':str(exc)})
        objects = mr_objects()
    chosen = set()
    objects = iter(objects)
    while len(selected)<limit:
        try:
            id, number = next(objects)
        except StopIteration:
            break
        if id in chosen:
            continue
        try:
            matches = entries.get(id, [])
            if len(matches)!=1 or errors.get(id) or matches[0]['problem']:
                raise ValueError('invalid catalog record for MR')
            bank = {**matches[0], 'state':bank_state(id)}
            context = work.capture(repo, data, bank, args.task, number)
            if args.task not in work.REWORK:
                point, eligible, reason = work.eligibility(context,bank['state'],bank,timestamp,policy,False)
                if point is not None or not eligible:
                    if point:
                        stops.append({'id':id,'reason':reason})
                    continue
            else:
                prior = next((r for r in (bank['state'] or {}).get('taskResults',[]) if r['key']==context['key']), None)
                if prior:
                    if prior['outcome'] in {'published','keep-current','defer','unclear','needs-human'}:
                        continue
                    delay = policy['retryErrorAfterDays'] if prior['outcome']=='error' else policy['retryUnconfirmedAfterDays']
                    if (timestamp-sessions.parse_time(prior['processedAt'])).total_seconds()<delay*86400:
                        continue
            selected.append({'id':id,'title':bank['title'],'mr':number,'taskKey':context['key']})
            chosen.add(id)
        except (ValueError, KeyError, OSError) as exc:
            stops.append({'id':id,'mr':number,'reason':str(exc)})
    return {'status':'task-plan', 'task':args.task, 'baseCommit':base, 'stateCommit':commit,
            'stateRead':read, 'limit':limit, 'selected':selected, 'stops':stops,
            'nextAction':'Dispatch selected objects in returned priority order with one fresh task-start session each. Independent research/preparation may overlap; the coordinator records and publishes ready results under references/task-runs.md#parallel-work. Do not refill this finite plan.'}


def start(args):
    data = sessions.create_session(args.repo, args.session, args.id,
                                   base=args.base, remote=args.remote)
    path = Path(args.session).resolve()
    bank = data['banks'][0]
    try:
        value = work.attach(path, data, bank, args.task, args.mr)
    except (ValueError, OSError) as exc:
        bank.update(point="blocked", reason=f"task initialization failed: {exc}")
        sessions.save(path, data)
        raise
    sessions.save(path,data)
    return {**data, 'task':args.task, 'taskInput':value, 'nextAction':
        ('Read the discussion, then task-review --notes FILE. This is agent interpretation, not an owner-approval pause.'
         if args.task in work.REWORK else 'Use workflow work-order and the ordinary attempt/candidate checks. Independent banks may research/prepare in parallel; the coordinator saves this result when ready under references/task-runs.md#parallel-work.')}


def review(args):
    path = Path(args.session).resolve()
    data = json.loads(state.regular(path/'session.json'))
    bank = next(b for b in data['banks'] if b['id']==args.id)
    notes = json.loads(state.regular(Path(args.notes).absolute()))
    result = work.record_decision(path,data,bank,notes)
    if bank['point']=='task-complete':
        from state_results import _prior_state
        bank['state'] = work.no_attempt(path,data,bank,_prior_state(data,bank['id']))
    sessions.save(path,data)
    return {'status':bank['point'],'id':args.id,'decision':result,
            'nextAction':'Use workflow next; publish result through existing authority, not a new permission inferred from review text.'}


def add_subcommands(commands):
    sub=commands.add_parser('task-list',help='read a finite task-scoped plan, without global state synchronization')
    sub.add_argument('--task',choices=sorted(work.TASKS),required=True)
    sub.add_argument('--repo',required=True); sub.add_argument('--base',default='HEAD')
    sub.add_argument('--remote'); sub.add_argument('--limit',type=int)
    sub=commands.add_parser('task-start',help='start one independent object of a task using existing remote state')
    sub.add_argument('--task',choices=sorted(work.TASKS),required=True)
    sub.add_argument('--repo',required=True); sub.add_argument('--session',required=True)
    sub.add_argument('--base',default='HEAD'); sub.add_argument('--remote')
    sub.add_argument('--id',type=int,required=True); sub.add_argument('--mr',type=int)
    sub=commands.add_parser('task-review',help='record actual agent interpretation of the task discussion, without publishing state first')
    sub.add_argument('--session',required=True); sub.add_argument('--id',type=int,required=True)
    sub.add_argument('--notes',required=True)
    sub=commands.add_parser('task-finish', help='finish a known-unpublished candidate with a durable failure reason')
    sub.add_argument('--session', required=True); sub.add_argument('--id', type=int, required=True)
    from task_completion import OUTCOMES, STAGES
    sub.add_argument('--outcome', choices=sorted(OUTCOMES), required=True)
    sub.add_argument('--stage', choices=sorted(STAGES), required=True)
    sub.add_argument('--reason', required=True)
    sub.add_argument('--evidence', action='append', help='existing bank evidence; repeat for additional artifacts')
    sub=commands.add_parser('task-recover',help='recover an observed publication from compact durable intent, without old session files')
    sub.add_argument('--operation',required=True)
    import approved_merge
    approved_merge.add_subcommands(commands)


def dispatch(args):
    try:
        return _dispatch(args)
    except operation_budget.Exhausted as exc:
        return {'status':'incomplete','reason':str(exc),'operation':getattr(args,'operation',None),
                'session':getattr(args,'session',None),'nextAction':'Retain current evidence; inspect the bounded read. Unknown writes require read-only recovery.'}


def _dispatch(args):
    if args.command=='task-finish':
        from task_completion import finish
        with operation_budget.bounded(180):
            return finish(args)
    if args.command=='task-recover':
        from task_recovery import prepare
        with operation_budget.bounded(180):
            return prepare(args.operation)
    if args.command.startswith('merge-'):
        import approved_merge
        return approved_merge.dispatch(args)
    with operation_budget.bounded(180):
        return {'task-list':plan,'task-start':start,'task-review':review}[args.command](args)
