#!/usr/bin/env python3
"""Opt-in, paired behavioral evaluations using an operator's isolated model adapter.

Preparation is free of model calls. --execute-model-calls invokes real adapters;
unit fixtures test this protocol, never count as measured agent passes. The
operator must sandbox tools/network/provider writes; this runner is not a sandbox.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

SKILL = '.agents/skills/bank-icon-maintenance'
MAX_BYTES = 16 * 1024 * 1024


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def regular(path):
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError('expected a bounded regular non-symlink file')
    return path.read_bytes()


def save(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], timeout=30)


def freeze(repo, revision, output):
    """Read committed tracked source without checkout hooks, index changes or remotes."""
    commit = git(repo, 'rev-parse', '--verify', f'{revision}^{{commit}}').decode().strip()
    rows = git(repo, 'ls-tree', '-rz', commit, '--', SKILL, 'icon-catalog', 'svgo.config.mjs')
    output.mkdir()
    for row in rows.split(b'\0'):
        if not row:
            continue
        header, name = row.split(b'\t', 1)
        mode, kind, oid = header.split()
        name = name.decode()
        if mode not in (b'100644', b'100755') or kind != b'blob':
            raise ValueError('evaluation installation contains unsupported links or objects')
        path = output / name
        if not path.is_relative_to(output) or '..' in Path(name).parts:
            raise ValueError('invalid Git path')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(git(repo, 'cat-file', 'blob', oid.decode()))
        path.chmod(0o755 if mode == b'100755' else 0o644)
    if not (output / SKILL / 'SKILL.md').is_file():
        raise ValueError('revision lacks the skill')
    return commit


def fingerprint(directory):
    files = {}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise ValueError('fixture/artifact symlinks are unsupported')
        if path.is_file():
            files[path.relative_to(directory).as_posix()] = digest(regular(path))
    if len(files) > 2000:
        raise ValueError('fixture is too large')
    return files


def local_artifact(root, value):
    if type(value) is not str or Path(value).is_absolute() or any(p in ('', '.', '..') for p in value.split('/')):
        raise ValueError('trace artifact must be an exact run-relative file')
    path = root / value
    regular(path)
    return path


def observations(root):
    """Check trace bindings; do not convert a model's statement into a passed expectation."""
    raw = regular(root / 'transcript.jsonl')
    if not raw.strip():
        raise ValueError('empty transcript is not an agent run')
    counts, images = {}, []
    for line in raw.splitlines():
        event = json.loads(line)
        if type(event) is not dict or type(event.get('type')) is not str:
            raise ValueError('trace events require a type')
        kind = event['type']
        counts[kind] = counts.get(kind, 0) + 1
        if kind == 'image_input':
            path = local_artifact(root, event.get('path'))
            if digest(regular(path)) != event.get('sha256'):
                raise ValueError('image input hash differs from the actual artifact')
            from PIL import Image
            with Image.open(path) as image:
                image.verify()
            images.append({'path': event['path'], 'sha256': event['sha256']})
    final = regular(root / 'final.txt').decode('utf-8')
    if not final.strip():
        raise ValueError('agent final response is missing')
    return {'traceSha256': digest(raw), 'eventCounts': counts, 'imageInputs': images,
            'visualEvaluation': 'requires-human-review' if images else 'not-tested'}


def call_adapter(command, request, root, timeout):
    start = time.monotonic()
    environment = {k: v for k, v in os.environ.items() if k not in
        {'GH_TOKEN', 'GITHUB_TOKEN', 'SSH_AUTH_SOCK', 'BANK_ICON_PUBLICATION_POLICY', 'BANK_ICON_PUBLICATION_POLICY_SHA256'}}
    # Bounded files/process group; credentials and external tool confinement are
    # still the operator adapter's responsibility, not proven by these limits.
    def limits():
        import resource
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_BYTES, MAX_BYTES))
    result = {'status': 'infrastructure-failed'}
    try:
        with (root / 'adapter.stdout.json').open('xb') as out, (root / 'adapter.stderr.txt').open('xb') as err:
            proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=out, stderr=err,
                                    cwd=root, env=environment, start_new_session=True, preexec_fn=limits)
            try:
                proc.communicate(json.dumps(request).encode(), timeout=timeout)
            except BaseException:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
                raise
        if proc.returncode:
            raise ValueError(f'isolated adapter exited {proc.returncode}')
        response = json.loads(regular(root / 'adapter.stdout.json'))
        if (type(response) is not dict or response.get('status') != 'agent-complete' or
                any(type(response.get(k)) is not str or not response[k].strip() for k in ('model', 'client'))):
            raise ValueError('adapter must report actual model/client and agent-complete')
        result.update(observations(root), status='awaiting-grading', model=response['model'], client=response['client'])
        usage = response.get('usage')
        if usage is not None:
            if type(usage) is not dict or any(type(v) is not int or v < 0 for v in usage.values()):
                raise ValueError('usage must contain actual nonnegative integer client counters')
            result['usage'] = usage
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        result.update(status='infrastructure-failed', reason=str(exc))
    result['elapsedSeconds'] = time.monotonic() - start
    save(root / 'timing.json', {k: result[k] for k in ('elapsedSeconds', 'usage') if k in result})
    return result


def run(args):
    repo, output = Path(args.repo).resolve(strict=True), Path(args.output).resolve()
    if output.exists() or output.is_relative_to(repo):
        raise ValueError('use a fresh evaluation directory outside the checkout')
    output.mkdir(parents=True)
    revisions = {}
    for label, revision in (('old', args.old), ('new', args.new)):
        revisions[label] = freeze(repo, revision, output / label)
    cases = json.loads(regular(output / 'new' / SKILL / 'evals/evals.json'))['evals']
    chosen = [case for case in cases if case['id'] in args.case]
    if len(chosen) != len(set(args.case)) or not chosen:
        raise ValueError('select existing unique case IDs')
    if not 1 <= args.repeat <= 10 or not 1 <= args.timeout <= 3600:
        raise ValueError('repeat must be 1..10 and timeout 1..3600 seconds')
    fixtures = Path(args.fixtures).resolve(strict=True)
    plan = {'revisions': revisions, 'cases': [c['id'] for c in chosen], 'repeats': args.repeat,
            'mode': 'execute' if args.execute_model_calls else 'prepare-only'}
    save(output / 'plan.json', plan)
    command = None
    if args.execute_model_calls:
        if not args.adapter:
            raise ValueError('model execution requires an operator-provisioned isolated adapter')
        config_path = Path(args.adapter).absolute()
        if config_path.is_relative_to(repo) or config_path.is_relative_to(output) or config_path.is_relative_to(fixtures):
            raise ValueError('adapter config must be outside checkout, fixtures and evaluation output')
        config = json.loads(regular(config_path))
        command = config.get('argv') if type(config) is dict else None
        if (type(config) is not dict or config.keys() != {'argv'} or type(command) is not list or
                not command or any(type(a) is not str or not a for a in command) or not Path(command[0]).is_absolute()):
            raise ValueError('adapter requires installed absolute executable argv; no shell')
    # Validate every fixture before the first paid call. A fixture manifest is a
    # scenario specification, never a pre-generated successful render receipt.
    prepared = {}
    for case in chosen:
        fixture = fixtures / str(case['id'])
        manifest = json.loads(regular(fixture / 'fixture.json'))
        if (manifest.get('schemaVersion') != 1 or manifest.get('caseId') != case['id'] or
                manifest.get('externalWrites') is not False or manifest.get('prebuiltEvidence') is not False):
            raise ValueError('fixture needs caseId, externalWrites:false, prebuiltEvidence:false')
        prepared[case['id']] = fingerprint(fixture)
    save(output / 'fixture-fingerprints.json', prepared)
    results = []
    for case in chosen:
        for iteration in range(args.repeat):
            # Alternate ordering to avoid always running the proposed revision second.
            for label in (('old', 'new') if iteration % 2 == 0 else ('new', 'old')):
                root = output / 'runs' / str(case['id']) / str(iteration) / label
                root.mkdir(parents=True)
                shutil.copytree(fixtures / str(case['id']), root / 'fixture')
                if fingerprint(root / 'fixture') != prepared[case['id']]:
                    raise ValueError('fixture changed while preparing the paired run')
                shutil.copytree(output / label, root / 'installation')
                request = {'schemaVersion': 1, 'caseId': case['id'], 'freshContext': True,
                           'prompt': case['prompt'], 'fixture': str(root / 'fixture'),
                           'installation': str(root / 'installation'), 'skill': str(root / 'installation' / SKILL / 'SKILL.md'),
                           'output': str(root), 'revision': revisions[label],
                           'constraint': 'Isolate network/tools/provider writes. Use actual image input; never fabricate successful receipts.'}
                save(root / 'request.json', request)
                result = call_adapter(command, request, root, args.timeout) if command else {'status': 'prepared-not-run'}
                save(root / 'grading.json', {'result': result, 'expectations': [
                    {'expectation': text, 'passed': None, 'evidence': []} for text in case['expectations']]})
                results.append({'caseId': case['id'], 'iteration': iteration, 'revision': label, **result})
    identities = {(r['model'], r['client']) for r in results if 'model' in r and 'client' in r}
    comparable = len(identities) <= 1
    summary = {'status': 'infrastructure-failed' if not comparable or any(r['status'] == 'infrastructure-failed' for r in results) else
               ('awaiting-grading' if command else 'prepared-not-run'), 'sameModelClient': comparable, 'plan': plan, 'runs': results,
               'note': 'Null expectations are untested. Image events require trusted client traces and human review; no inferred pass rate.'}
    save(output / 'summary.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('repo', 'old', 'new', 'fixtures', 'output'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--case', action='append', type=int, required=True)
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--timeout', type=int, default=900)
    parser.add_argument('--adapter')
    parser.add_argument('--execute-model-calls', action='store_true')
    try:
        result = run(parser.parse_args())
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        result = {'status': 'infrastructure-failed', 'reason': str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result['status'] == 'infrastructure-failed' else 0


if __name__ == '__main__':
    sys.exit(main())
