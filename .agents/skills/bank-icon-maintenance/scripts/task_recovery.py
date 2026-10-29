"""Compact publication intents in the repo's Git dir, not retained render sessions.

Only a validated candidate can create an intent. Recovery reads provider/Git facts
and prepares ordinary state publication; it never repeats an asset write.
"""
import argparse
import copy
import json
from pathlib import Path

import approved_merge
import github_pr
import state_publication as state
from evidence import required

KIND='task-recovery'


def retain(session, package):
    if not package.get('taskKey'):
        return None
    pointer = Path(session) / f"task-operation-{state.digest(state.encoded(package))}.json"
    if pointer.exists():
        directory,value=load(json.loads(state.regular(pointer))['operation'])
        if value['package'] != package:
            raise ValueError('asset operation points to another package')
        return str(directory)
    from asset_results import load_candidate
    from state_results import validate_results
    snapshot=json.loads(state.regular(Path(session)/'session.json'))
    bank=next((b for b in snapshot.get('banks',[]) if b['id']==package['id']),{})
    if not bank.get('taskContext'):
        return None
    data,bank,_,_=load_candidate(session,package['id'])
    _,rows,_=validate_results(session,[package['id']])
    row=rows[0]
    value={'schemaVersion':1,'kind':KIND,'repo':data['repo'],'target':package['target'],
           'stateRemote':data['stateRemote'],'stateCommit':data['stateCommit'],
           'id':bank['id'],'baseCommit':package['baseCommit'],'bank':{k:bank[k] for k in ('id','folder','metadataHash','iconHash')},
           'before':row['before'],'prepared':row['after'],'package':package}
    key=state.digest(state.encoded(value))
    directory=approved_merge.operation_root(data['repo'])/('asset-'+key)
    directory.mkdir(exist_ok=True)
    state.save_new(directory/'manifest.json',state.encoded(value))
    context={'schemaVersion':1,'purpose':KIND,'repo':data['repo'],'baseCommit':package['baseCommit'],
             'stateRemote':data['stateRemote'],'stateCommit':data['stateCommit'],'selectedIds':[bank['id']]}
    state.save_new(directory/'session.json',state.encoded(context))
    state.save_new(pointer,state.encoded({'operation':str(directory)}))
    return str(directory)


def load(path):
    path=Path(path).resolve(strict=True)
    value=json.loads(state.regular(path/'manifest.json'))
    if value.get('kind')!=KIND or path!=approved_merge.operation_root(value['repo'])/('asset-'+state.digest(state.encoded(value))):
        raise ValueError('asset recovery manifest/path changed')
    return path,value


def observed(value):
    from asset_publication import _ref,_fetch,_blob
    package=value['package'];repo=Path(value['repo'])
    prs=github_pr.lookup(value['target'],package['head'])
    head=_ref(repo,value['target'],package['headRef'])
    material=_ref(repo,value['target'],package['materials']['ref'])
    if not prs and head is None and material is None:
        return None
    if len(prs)!=1:
        raise ValueError('partial/ambiguous asset publication; do not restart or repeat writes')
    mr=prs[0]
    if (any(mr[k]!=package[k] for k in ('target','head','base','title')) or
            mr['headCommit']!=package['assetCommit'] or mr['bodySha256']!=state.digest(package['body'].encode()) or
            material!=package['materials']['commit']):
        raise ValueError('observed publication differs from retained exact manifest')
    if mr['state']=='open' and head!=package['assetCommit']:
        raise ValueError('published head moved; a new human revision must be inspected')
    commit=_fetch(repo,value['target'],f"refs/pull/{mr['number']}/head")
    if state.digest(_blob(repo,commit,value['bank']['folder']+'/icon.svg'))!=package['candidateIconHash']:
        raise ValueError('published SVG differs from verified candidate')
    if package.get('revision'):
        from feedback_publication import _comments
        if _comments(package) is None:
            raise ValueError('revision comment was not confirmed; do not post it again without original operation reconciliation')
    return mr


def result_state(value,mr,at=None):
    after=copy.deepcopy(value['prepared'])
    p=after['proposals'][-1]
    at = at or approved_merge._now()
    p.update(status='open',mr={**mr,'state':'open','observedAt':at},
             assetPublication={'path':f"asset-publication/{state.digest(state.encoded(value['package']))}.json",
                               'sha256':state.digest(state.encoded(value['package']))})
    # Recovery claims the observed publication, not acceptance or new verification.
    from task_work import published
    published(after,p['mr'],value['package']['candidateIconHash'])
    if mr['state']!='open':
        from proposal_sync import _merge_proof, _transition
        outcome=github_pr.outcome(value['target'],mr['number'])
        proof=_merge_proof(Path(value['repo']),value['id'],p,outcome) if outcome['state']=='merged' else None
        after=_transition(after,outcome,proof,value['stateCommit'],at)
    return after


def prepare(path):
    path,value=load(path)
    mr=observed(value)
    if mr is None:
        return {'status':'not-published','operation':str(path),'id':value['id'],
                'nextAction':'No output refs/PR exist; a fresh attempt may replace the unavailable local candidate, retaining its history.'}
    root=path/'state-publication';root.mkdir(exist_ok=True)
    objects=state.object_repo(root);head=state.fetch_head(objects,value['stateRemote'])
    before,sha=state.remote_record(objects,head,f"banks/state/{value['id']}.json")
    # Repeated recovery uses the historical observedAt, not a new date each time.
    saved=root/'recovered.json'
    if saved.exists():
        previous=json.loads(state.regular(saved))
        if previous['mr']!=mr:
            raise ValueError('recovered MR changed')
        after=previous['after']
    else:
        at=approved_merge._now();after=result_state(value,mr,at)
        state.save_new(saved,state.encoded({'mr':mr,'observedAt':at,'after':after}))
    if before==after:
        return {'status':'state-synced','stateCommit':head,'operation':str(path),'id':value['id']}
    if before not in (value['before'],value['prepared']):
        raise ValueError('newer bank state conflicts with recovered operation; it was not overwritten')
    bindings={str(path/name):state.digest(state.regular(path/name)) for name in ('manifest.json','session.json')}
    bindings[str(saved)]=state.digest(state.regular(saved))
    package={'schemaVersion':1,'scope':'state-publication','target':value['stateRemote'],'ref':state.REF,
             'baseCommit':value['baseCommit'],'stateHistory':'present' if head else 'unknown','observedRef':head,
             'ids':[value['id']],'records':[{'id':value['id'],'path':f"banks/state/{value['id']}.json",
                 'before':before,'beforeSha256':sha,'after':after}], 'results':[{'mr':mr}],
             'reconciliation':{'kind':KIND},'bindings':bindings}
    key=state.digest(state.encoded(package));state.save_new(root/f'{key}.json',state.encoded(package))
    from standing_authorization import describe_package
    return describe_package({'status':'awaiting-state-authorization','operation':str(path),'packageHash':key,
                             'package':package,'nextAction':'Publish recovered state with --session OPERATION; never recreate the MR.'},path,'statePublish')


def authorization_context(path,package):
    path,value=load(path)
    data=json.loads(state.regular(path/'session.json'))
    expected={str(path/name):state.digest(state.regular(path/name)) for name in ('manifest.json','session.json','state-publication/recovered.json')}
    if (package['reconciliation']!={'kind':KIND} or package['bindings']!=expected or package['ids']!=[value['id']]
            or package['target']!=value['stateRemote'] or package['baseCommit']!=value['baseCommit']):
        raise ValueError('recovery package/context mismatch')
    return data


def validate_package(path,package,objects,head):
    authorization_context(path,package)
    path,value=load(path);mr=observed(value)
    if package['results']!=[{'mr':mr}]:
        raise ValueError('recovered provider observation changed')
    recovered=json.loads(state.regular(path/'state-publication/recovered.json'))
    after=result_state(value,mr,recovered['observedAt'])
    if after!=recovered['after']:
        raise ValueError('recovery manufactured a different candidate result')
    before,sha=state.remote_record(objects,package['observedRef'],f"banks/state/{value['id']}.json")
    if before not in (value['before'],value['prepared']) or package['records']!=[{'id':value['id'],
            'path':f"banks/state/{value['id']}.json",'before':before,'beforeSha256':sha,'after':after}]:
        raise ValueError('recovery package changes unsupported state')


def audit_pending(repo,target,bank):
    """Legacy orphan: prove no matching remote icon before retiring local-only work."""
    from asset_publication import _remote,_transport,_fetch,_blob
    p=bank['state']['proposals'][-1]
    for file in approved_merge.operation_root(repo).glob('asset-*/manifest.json'):
        _,v=load(file.parent)
        if v['id']==bank['id']:
            output=observed(v)
            if output is not None:
                raise ValueError(f'published output needs task-recover --operation {file.parent}; no old render session required')
    if github_pr.open_for_path(target,bank['folder']+'/icon.svg'):
        raise ValueError('an open bank PR already exists; recover/rework it, not a duplicate')
    rows=_transport(repo,'ls-remote','--refs',_remote(target),'refs/heads/*',timeout=60).stdout.decode().splitlines()
    if len(rows)>100:
        raise ValueError('orphan audit exceeds 100 heads; narrow the recovery instead of assuming no publication')
    refs=[]
    for row in rows:
        sha,ref=row.split('\t')
        commit=_fetch(repo,target,ref)
        if commit!=sha:
            raise ValueError('remote head changed during orphan audit')
        tree=state.git(repo,'ls-tree','-z',commit,'--',bank['folder']+'/icon.svg').stdout
        if tree and state.digest(_blob(repo,commit,bank['folder']+'/icon.svg'))==p['candidateIconHash']:
            raise ValueError(f'pending candidate bytes exist at {ref}; inspect/reconcile that output before new work')
        refs.append({'ref':ref,'sha':sha})
    return {'headSnapshot':refs,'candidateIconHash':p['candidateIconHash'],'observedAt':approved_merge._now()}
