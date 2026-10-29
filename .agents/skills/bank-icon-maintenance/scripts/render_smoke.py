#!/usr/bin/env python3
"""Engineering-only real Compose smoke on synthetic banks, never a maintenance run.

Builds use the repository's pinned catalog toolchain. --allow-build-downloads is
only for engineering CI; normal maintenance still requires its installed/cache
readiness policy. Pixel checks are not model visual approval or source research.
"""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import time

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path fill="#D53442" d="M3 3h18v18H3z"/></svg>\n'
CANDIDATE = b'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path fill="#2478CA" d="M12 2 22 12 12 22 2 12z"/></svg>\n'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def validate_export(path, id, expected):
    from PIL import Image
    result = json.loads((path / 'result.json').read_bytes())
    if (result.get('status') != 'success' or result.get('id') != id or
            result.get('source', {}).get('sha256') != sha(expected) or
            result.get('renderer') != 'offscreen CPU Skia/painterResource' or
            not result.get('xml', {}).get('sha256') or result['xml']['sha256'] != result['xml'].get('loadedSha256')):
        raise ValueError('real export does not bind exact source and loaded generated XML')
    if re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', result['xml'].get('resourceName', '')) is None:
        raise ValueError('invalid drawable name')
    rows = result.get('images', [])
    if len(rows) != 2 or {r.get('theme') for r in rows} != {'light', 'dark'}:
        raise ValueError('both real theme frames are required')
    for row in rows:
        if row.get('path') != row['theme'] + '.png':
            raise ValueError('unexpected export image path')
        image_path = path / row['path']
        if sha(image_path.read_bytes()) != row['sha256']:
            raise ValueError('image hash differs')
        with Image.open(image_path) as image:
            image.load()
            if image.size != (240, 240) or max(hi-lo for lo, hi in image.convert('RGB').getextrema()) == 0:
                raise ValueError('blank or incorrectly sized exported frame')
    return result


def run(args):
    repo = Path(args.repo).resolve(strict=True)
    output = Path(args.output).resolve()
    if output.exists() or output.is_relative_to(repo):
        raise ValueError('smoke output must be fresh and outside the checkout')
    output.mkdir(parents=True)
    # Independent build directory: no source checkout/catalog build outputs are touched.
    catalog = output / 'icon-catalog'
    shutil.copytree(repo / 'icon-catalog', catalog, ignore=shutil.ignore_patterns('build', '.gradle', 'local.properties'))
    banks = output / 'fixture/banks'
    before = {}
    for id in (10, 11):
        folder = banks / f'Synthetic_{id}-unknown'
        folder.mkdir(parents=True)
        (folder / 'info.json').write_text(json.dumps({'id': id, 'title': 'Synthetic', 'countryCode': None, 'url': None}))
        if id == 10:
            (folder / 'icon.svg').write_bytes(SVG)
        before[str(folder / 'info.json')] = (folder / 'info.json').read_bytes()
    candidate = output / 'candidate.svg'
    candidate.write_bytes(CANDIDATE)
    before[str(banks / 'Synthetic_10-unknown/icon.svg')] = SVG
    before[str(candidate)] = CANDIDATE
    env = {k: v for k, v in os.environ.items() if k not in ('DISPLAY', 'WAYLAND_DISPLAY', 'BANK_ICON_PUBLICATION_POLICY',
           'BANK_ICON_PUBLICATION_POLICY_SHA256', 'GH_TOKEN', 'GITHUB_TOKEN')}
    env['JAVA_TOOL_OPTIONS'] = '-Djava.awt.headless=true'
    wrapper = str(catalog / 'gradlew')
    flags = ['--no-daemon', '--console=plain']
    if not args.allow_build_downloads:
        flags += ['--offline']
    results = []

    def invoke(command, label):
        started = time.monotonic()
        with (output / (label+'.log')).open('xb') as log:
            process = subprocess.Popen(command, cwd=catalog, env=env, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                code = process.wait(timeout=args.timeout)
            except BaseException:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                raise
        results.append({'command': command, 'exitCode': code, 'elapsedSeconds': time.monotonic()-started, 'log': label+'.log'})
        if code:
            raise ValueError('catalog build/export failed; inspect '+label+'.log')

    try:
        invoke([wrapper, *flags, ':generator:test'], 'generator-tests')
        for label, id, asset in (('update-base',10,None), ('update-final',10,candidate), ('addition-final',11,candidate)):
            destination = output / label
            command = [wrapper, *flags, ':catalog:run', f'-PcatalogBanks={banks}', f'-PcatalogId={id}']
            if asset:
                command.append(f'-PcatalogCandidate={asset}')
            command.append('--args='+shlex.join(['export','--id',str(id),'--manifest',
                str(catalog/'catalog/build/generated/catalog/manifest.json'),'--output',str(destination)]))
            invoke(command, label)
            export = validate_export(destination, id, CANDIDATE if asset else SVG)
            generated = catalog/'catalog/build/generated/catalog'
            manifest = (generated/'manifest.json').read_bytes()
            xml = (generated/'composeResources/drawable'/ (export['xml']['resourceName']+'.xml')).read_bytes()
            if sha(manifest) != export['manifest']['sha256'] or sha(xml) != export['xml']['sha256']:
                raise ValueError('generated artifacts differ before retention')
            (destination/'manifest.json').write_bytes(manifest)
            (destination/'drawable.xml').write_bytes(xml)
            (destination/'retained-bindings.json').write_text(json.dumps({'manifest': {'path':'manifest.json','sha256':sha(manifest)},
                'xml':{'path':'drawable.xml','sha256':sha(xml)}})+'\n')
        for path, raw in before.items():
            if Path(path).read_bytes() != raw:
                raise ValueError('smoke changed immutable fixture source')
        if (banks / 'Synthetic_11-unknown/icon.svg').exists():
            raise ValueError('first-addition smoke materialized a fake baseline')
        result = {'status':'passed','checks':['real generator tests','update base/final','first addition','both themes','exact source/XML/images','unchanged fixture sources'], 'runs':results}
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        result = {'status':'failed','reason':str(exc),'runs':results}
    (output/'smoke.json').write_text(json.dumps(result, indent=2)+'\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--allow-build-downloads', action='store_true')
    parser.add_argument('--timeout', type=int, default=600)
    try:
        args = parser.parse_args()
        if not 1 <= args.timeout <= 1200:
            raise ValueError('timeout must be 1..1200 seconds')
        result = run(args)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        result = {'status':'failed','reason':str(exc)}
    print(json.dumps(result, indent=2))
    return 0 if result['status']=='passed' else 1


if __name__=='__main__':
    sys.exit(main())
