"""Versioned publication preflight; recorded judgments are not a source of authority.

Legacy candidate/evidence packages stay byte-exact. New asset packages bind this
append-only supplement. Validators enforce explicit failures and evidence bindings,
not the truth of a model's interpretation of a logo, source or catalog purpose.
"""
import copy
from datetime import datetime, timezone
from pathlib import Path

import source_evidence as se
from evidence import required, instant

VERSION = 1
CHECKS = ('fidelity', 'textPolicy', 'fit', 'lightTheme', 'darkTheme', 'smallSize')
ROLES = {'symbol', 'monogram', 'lettermark', 'wordmark', 'lockup', 'app-icon',
         'mask', 'decoration', 'interface', 'unknown'}
CHANGES = {'first-addition', 'rebrand', 'identity-correction', 'geometry-color-correction',
           'render-repair', 'presentation-normalization', 'supported-replacement'}
BLOCKING = {'identity', 'record-purpose', 'lifecycle', 'asset-role', 'applicability',
            'variant', 'fidelity', 'legibility', 'contrast', 'clipping'}
INFORMATIONAL = {'baseline-attribution', 'transition-date', 'platform-unavailable'}
NOTE_KEYS = {'record', 'asset', 'change', 'quality', 'concerns', 'assessor'}

# These same choices drive validation and the adjacent blank-template contract.
PURPOSES = {'current', 'historical', 'unresolved'}
BASIS_KINDS = {'current-use', 'catalog-policy', 'human-decision', 'unresolved'}
HISTORICAL_BASES = {'catalog-policy', 'human-decision'}
LIFECYCLES = {'legalLifecycle': {'active', 'merged', 'closed', 'unknown'},
              'brandLifecycle': {'active', 'retired', 'unknown'}}
SCOPES = {'record', 'group', 'regional', 'product', 'historical', 'unknown'}
ATTRIBUTIONS = {'supported', 'unknown', 'absent'}
FINDINGS = {'pass', 'fail', 'unresolved'}
CONCERN_KEYS = {'kind', 'basis', 'priorUncertainty', 'target'}
VISUAL_CONCERNS = {'variant', 'fidelity', 'legibility', 'contrast', 'clipping'}


def baseline_choices(present):
    return {'change.kind': sorted(CHANGES - {'first-addition'}) if present else ['first-addition'],
            'change.baselineAttribution': sorted(ATTRIBUTIONS - {'absent'}) if present else ['absent']}


def schema(bank):
    """Describe allowed inputs, never populate observations or grant admission."""
    enums = {'record.purpose': PURPOSES, 'record.basisKind': BASIS_KINDS,
             'asset.sourceRole': ROLES, 'asset.finalRole': ROLES, 'asset.scope': SCOPES,
             'change.kind': CHANGES, 'change.baselineAttribution': ATTRIBUTIONS,
             'concerns.*.kind': BLOCKING | INFORMATIONAL}
    enums.update({f'record.{name}.status': choices for name, choices in LIFECYCLES.items()})
    enums.update({f'asset.{name}.status': FINDINGS for name in ('originalVector', 'role', 'applicability')})
    enums.update({f'quality.{name}.status': FINDINGS for name in CHECKS})
    required_fields = {}
    def visit(value, path):
        if isinstance(value, dict):
            required_fields[path] = sorted(value)
            for key, item in value.items():
                visit(item, f'{path}.{key}' if path else key)
    visit(template(), '')
    required_fields['concerns.*'] = sorted(CONCERN_KEYS)
    return {'guide': str(Path(__file__).resolve().parents[1] / 'references/preparation-feedback.md'),
            'enums': {key: sorted(choices) for key, choices in enums.items()},
            'requiredFields': required_fields,
            'optionalFields': {'concerns.*.resolution': ['basis', 'sourceRefs', 'evidenceRefs']},
            'baselineChoices': baseline_choices(bank['iconHash'] is not None),
            'conditions': {
                'historical': {'when': {'record.purpose': 'historical'},
                    'basisKind': sorted(HISTORICAL_BASES),
                    'requiredNonempty': ['record.period', 'record.purposeEvidence']},
                'current': {'when': {'record.purpose': 'current'}, 'brandLifecycle.status': 'active'},
                'rebrand': {'when': {'change.kind': 'rebrand'},
                    'baselineAttribution': 'supported', 'requiredNonempty': ['change.transitionSourceRefs']},
                'platform-unavailable': {'when': {'concerns.*.kind': 'platform-unavailable'},
                    'target': 'Exact recorded platform with available=false and status=not-tested; otherwise target is null.'},
                'sourceRefs': 'Distinct zero-based indices in original sources: required for resolved purpose/lifecycle, passing asset findings and change; no invented observations.',
                'uncertainties': 'Classify each prior uncertainty exactly once; additional concerns may have priorUncertainty=null.',
                'resolution': 'Optional structured resolution of a prior blocking uncertainty only: nonempty basis, actual sourceRefs and/or evidenceRefs. Visual concerns require sourceComparison and candidate-light/candidate-dark; other concerns require sourceRefs. Omission means unresolved, never infer resolution from prose.',
                'findings': 'All basis/assessor strings must be nonempty. Required quality/asset findings must pass; unknown roles/scopes and unresolved blocking concerns still block.'},
            'blockingConcernKinds': sorted(BLOCKING), 'informationalConcernKinds': sorted(INFORMATIONAL),
            'warning': 'Allowed inputs are not observed facts. Keep notes blank until assessed; no permission or pass inferred.'}


def _object(value, keys, label):
    if type(value) is not dict or value.keys() != set(keys):
        raise ValueError(f'{label}: incomplete or unsupported fields')
    return value


def _choice(value, choices, label):
    if type(value) is not str or value not in choices:
        raise ValueError(f'{label}: choose one of {sorted(choices)}')
    return value


def _refs(value, sources, label, *, needed=True):
    if (type(value) is not list or (needed and not value) or
            any(type(i) is not int or i < 0 or i >= len(sources) for i in value) or
            len(set(value)) != len(value)):
        raise ValueError(f'{label}: use distinct indices of actual prior source observations')
    return value


def _finding(value, label, sources=None):
    keys = {'status', 'basis'} | ({'sourceRefs'} if sources is not None else set())
    _object(value, keys, label)
    _choice(value['status'], FINDINGS, label)
    required(value, 'basis')
    if sources is not None:
        _refs(value['sourceRefs'], sources, label, needed=value['status'] == 'pass')
    return value['status']


def template():
    """Blank agent inputs. No affirmative opinion, scope or policy is inferred."""
    finding = lambda: {'status': None, 'basis': None, 'sourceRefs': []}
    return {'assessor': None,
            'record': {'purpose': None, 'basisKind': None, 'basis': None,
                       'purposeEvidence': None, 'period': None, 'sourceRefs': [],
                       'legalLifecycle': {'status': None, 'basis': None, 'sourceRefs': []},
                       'brandLifecycle': {'status': None, 'basis': None, 'sourceRefs': []}},
            'asset': {'sourceRole': None, 'finalRole': None, 'scope': None,
                      'accompanyingText': None, 'originalVector': finding(),
                      'role': finding(), 'applicability': finding()},
            'change': {'kind': None, 'basis': None, 'sourceRefs': [],
                       'baselineAttribution': None, 'transitionSourceRefs': []},
            'quality': {name: {'status': None, 'basis': None} for name in CHECKS},
            'concerns': []}


def image_links(report):
    result = {}
    for theme, row in report['evidence']['comparison'].items():
        for role in ('source', 'base', 'candidate'):
            if row[role] is not None:
                result[f'{role}-{theme}'] = row[role]
    return result


def resolution_links(session, report, resolution):
    """Resolve named existing evidence, not arbitrary paths or new attestations."""
    _object(resolution, {'basis', 'sourceRefs', 'evidenceRefs'}, 'concern.resolution')
    required(resolution, 'basis')
    _refs(resolution['sourceRefs'], report['before']['proposals'][-1]['sources'],
          'concern.resolution.sourceRefs', needed=False)
    names = resolution['evidenceRefs']
    allowed = {**image_links(report)}
    if report['evidence'].get('sourceComparison'):
        allowed['sourceComparison'] = report['evidence']['sourceComparison']
    if (type(names) is not list or any(type(name) is not str or name not in allowed for name in names)
            or len(set(names)) != len(names)):
        raise ValueError('concern.resolution.evidenceRefs must name existing candidate evidence')
    links = [allowed[name] for name in names]
    if 'sourceComparison' in names:
        view = se.read_json(se.linked(session, allowed['sourceComparison'], 'resolution source comparison'))
        if (view.get('id') != report['id'] or
                view.get('final', {}).get('sha256') != report['candidate']['sha256'] or
                set(view.get('pairs', {})) != {'light', 'dark'}):
            raise ValueError('concern resolution comparison belongs to different candidate bytes')
        reference = se.read_json(se.linked(session, view['sourceReference'], 'resolution full source'))
        # Include the uncropped original raster as well as the theme pairs. The
        # normal candidate validator establishes source/pixel lineage, not this note.
        links += [view['sourceReference'], view['final'], view['desktopBundle'],
                  reference['source'], reference['raster'], *view['pairs'].values()]
    for link in links:
        se.linked(session, link, 'concern resolution evidence')
    return links


def resolved(session, report, item):
    resolution = item.get('resolution')
    if resolution is None:
        return False
    if item['priorUncertainty'] is None or item['kind'] not in BLOCKING:
        raise ValueError('resolution must address an indexed prior blocking uncertainty')
    resolution_links(session, report, resolution)
    if item['kind'] in VISUAL_CONCERNS:
        if not {'sourceComparison', 'candidate-light', 'candidate-dark'}.issubset(resolution['evidenceRefs']):
            raise ValueError('visual resolution requires full source comparison and both candidate themes')
    elif not resolution['sourceRefs']:
        raise ValueError('nonvisual resolution requires actual source observations')
    return True


def inspect(session, report, notes):
    """Validate the declared facts and return blockers, including non-visual ones."""
    _object(notes, NOTE_KEYS, 'admission notes')
    required(notes, 'assessor')
    prior = report['before']['proposals'][-1]
    sources = prior['sources']
    blockers = []
    record = _object(notes['record'], {'purpose', 'basisKind', 'basis', 'purposeEvidence',
                                      'period', 'sourceRefs', 'legalLifecycle', 'brandLifecycle'}, 'record')
    purpose = _choice(record['purpose'], PURPOSES, 'record.purpose')
    basis_kind = _choice(record['basisKind'], BASIS_KINDS, 'record.basisKind')
    required(record, 'basis')
    _refs(record['sourceRefs'], sources, 'record.sourceRefs', needed=purpose != 'unresolved')
    if purpose == 'historical':
        required(record, 'period')
        if basis_kind not in HISTORICAL_BASES or record['purposeEvidence'] is None:
            blockers.append('historical purpose requires catalog policy or an actual human decision, not a rename/merger/title')
    elif record['period'] is not None:
        required(record, 'period')
    if purpose == 'unresolved' or basis_kind == 'unresolved':
        blockers.append('record purpose is unresolved')
    if record['purposeEvidence'] is not None:
        path = se.linked(session, record['purposeEvidence'], 'catalog purpose provenance')
        if not path.read_bytes().strip():
            raise ValueError('catalog purpose provenance is empty')
    for name, choices in LIFECYCLES.items():
        row = _object(record[name], {'status', 'basis', 'sourceRefs'}, name)
        _choice(row['status'], choices, name)
        required(row, 'basis')
        _refs(row['sourceRefs'], sources, name, needed=row['status'] != 'unknown')
    if record['brandLifecycle']['status'] == 'unknown':
        blockers.append('brand lifecycle is unresolved; legal status does not determine brand use')
    if purpose == 'current' and record['brandLifecycle']['status'] != 'active':
        blockers.append('current record has no confirmed active brand')
    asset = _object(notes['asset'], {'sourceRole', 'finalRole', 'scope', 'accompanyingText',
                                    'originalVector', 'role', 'applicability'}, 'asset')
    for name in ('sourceRole', 'finalRole'):
        _choice(asset[name], ROLES, f'asset.{name}')
    _choice(asset['scope'], SCOPES, 'asset.scope')
    if type(asset['accompanyingText']) is not bool:
        raise ValueError('asset.accompanyingText must be an observed boolean')
    if asset['sourceRole'] in {'mask', 'decoration', 'interface', 'unknown'}:
        blockers.append('source role is not an original vector logo variant suitable for this replacement')
    if asset['finalRole'] in {'lockup', 'mask', 'decoration', 'interface', 'unknown'} or asset['accompanyingText']:
        blockers.append('final has an unsuitable role or accompanying name/tagline; calling a lockup a monogram is not an exception')
    if asset['scope'] == 'unknown':
        blockers.append('source applicability scope is unknown')
    for name in ('originalVector', 'role', 'applicability'):
        if _finding(asset[name], 'asset.' + name, sources) != 'pass':
            blockers.append('asset.' + name)
    change = _object(notes['change'], {'kind', 'basis', 'sourceRefs', 'baselineAttribution', 'transitionSourceRefs'}, 'change')
    _choice(change['kind'], CHANGES, 'change.kind')
    _choice(change['baselineAttribution'], ATTRIBUTIONS, 'change.baselineAttribution')
    required(change, 'basis')
    _refs(change['sourceRefs'], sources, 'change.sourceRefs')
    _refs(change['transitionSourceRefs'], sources, 'change.transitionSourceRefs', needed=False)
    absent = report['evidence']['baseIconHash'] is None
    if any(change[field.split('.')[1]] not in choices for field, choices in baseline_choices(not absent).items()):
        blockers.append('change kind / baseline attribution contradict the pinned baseline presence')
    if change['kind'] == 'rebrand' and (change['baselineAttribution'] != 'supported' or not change['transitionSourceRefs']):
        blockers.append('rebrand needs evidence of the old attribution and the transition; use supported-replacement instead of inventing history')
    _object(notes['quality'], CHECKS, 'quality')
    for name in CHECKS:
        if _finding(notes['quality'][name], 'quality.' + name) != 'pass':
            blockers.append('quality.' + name)
    if type(notes['concerns']) is not list:
        raise ValueError('concerns must be a list')
    uncertainties = prior['assessment']['uncertainties']
    covered = []
    platforms = {row['target']: row for row in report['evidence']['platforms']}
    for item in notes['concerns']:
        _object(item, CONCERN_KEYS | ({'resolution'} if type(item) is dict and 'resolution' in item else set()), 'concern')
        kind = _choice(item['kind'], BLOCKING | INFORMATIONAL, 'concern.kind')
        required(item, 'basis')
        index = item['priorUncertainty']
        if index is not None:
            _refs([index], uncertainties, 'concern.priorUncertainty')
            covered.append(index)
        closed = resolved(session, report, item)
        if kind in BLOCKING and not closed:
            blockers.append('concern.' + kind)
        if kind == 'baseline-attribution' and change['kind'] == 'rebrand':
            blockers.append('unknown baseline attribution contradicts rebrand')
        if kind == 'platform-unavailable':
            target = item['target']
            if type(target) is not str or target not in platforms or platforms[target].get('available') is not False or platforms[target]['status'] != 'not-tested':
                raise ValueError('platform-unavailable concern requires the exact unavailable, not-tested target')
        elif item['target'] is not None:
            raise ValueError('only unavailable platform concerns have a target; a theme defect is not platform unavailability')
    if sorted(covered) != list(range(len(uncertainties))):
        raise ValueError('classify every prior uncertainty exactly once; do not silently discard it')
    # Agent flags and previous render failures cannot be overturned by this supplement.
    candidate = report['evidence']['candidate']
    if candidate['faithful'] is not True or candidate['visibleDiff'] is not True:
        blockers.append('candidate fidelity or meaningful change failed')
    return blockers


def build(session, report, notes):
    """Save even a declined preflight, without editing the candidate or state."""
    notes = copy.deepcopy(notes)
    # The helper, not the agent, binds a quoted purpose decision to exact bytes.
    value = notes.get('record', {}).get('purposeEvidence')
    if type(value) is str:
        notes['record']['purposeEvidence'] = se.link(session, se.artifact(session, value, 'purpose provenance'))
    blockers = inspect(session, report, notes)
    root = se.fresh(session, report['id'], 'admission-input')
    document = {'schemaVersion': VERSION, 'id': report['id'], 'reviewPackage': report['reviewPackage'],
                'candidateSha256': report['candidate']['sha256'], 'checkedAt': se.now(),
                'images': image_links(report), 'notes': notes,
                'decision': 'blocked' if blockers else 'admitted', 'blockers': blockers}
    path = root / 'admission.json'
    se.write_json(path, document)
    return {'status': document['decision'], 'admission': se.link(session, path), 'blockers': blockers,
            'nextAction': 'Preserve this preflight. Resolve supported prior concerns with new admission, or task-finish the stopped candidate before final state publication; no asset MR.' if blockers else
                          'Use this exact admission path with asset-prepare --admission; existing publication authority is still required.'}


def validate(session, report, link, *, positive=True):
    document = se.read_json(se.linked(session, link, 'admission'))
    _object(document, {'schemaVersion', 'id', 'reviewPackage', 'candidateSha256', 'checkedAt',
                       'images', 'notes', 'decision', 'blockers'}, 'admission')
    if (type(document['schemaVersion']) is not int or document['schemaVersion'] != VERSION or
            type(document['id']) is not int or document['id'] != report['id'] or
            document['reviewPackage'] != report['reviewPackage'] or
            document['candidateSha256'] != report['candidate']['sha256'] or
            document['images'] != image_links(report)):
        raise ValueError('admission differs from the exact prepared candidate / images')
    checked = instant(document['checkedAt'], 'admission.checkedAt')
    if not instant(report['renderBinding']['checkedAt'], 'candidate.checkedAt') <= checked <= datetime.now(timezone.utc):
        raise ValueError('admission time is outside candidate-to-command interval')
    for role, image in document['images'].items():
        se.linked(session, image, role)
    blockers = inspect(session, report, document['notes'])
    if document['blockers'] != blockers or document['decision'] != ('blocked' if blockers else 'admitted'):
        raise ValueError('admission decision contradicts declared checks')
    if positive and blockers:
        raise ValueError('publication blocked by admission: ' + '; '.join(blockers))
    return document


def bindings(session, report, link):
    document = validate(session, report, link, positive=False)
    links = [link, *document['images'].values()]
    provenance = document['notes']['record']['purposeEvidence']
    if provenance is not None:
        links.append(provenance)
    for concern in document['notes']['concerns']:
        if concern.get('resolution') is not None:
            links.extend(resolution_links(session, report, concern['resolution']))
    return {item['path']: item['sha256'] for item in links}


def retained(session, report):
    """Include matching declined preflights in durable state without changing history."""
    result = {}
    root = Path(session) / 'evidence' / str(report['id'])
    for path in sorted(root.glob('admission-input-*/admission.json')):
        link = se.link(session, path)
        document = se.read_json(path)
        if document.get('reviewPackage') == report['reviewPackage']:
            result.update(bindings(session, report, link))
    return result


def public_summary(document):
    """Path-free gate findings; public-text cannot override these facts."""
    notes = copy.deepcopy(document['notes'])
    notes['record']['purposeEvidence'] = None  # Private quoted instructions stay local.
    return {'contractVersion': document['schemaVersion'], 'decision': document['decision'],
            'candidateSha256': document['candidateSha256'], 'checkedAt': document['checkedAt'],
            'judgments': notes}
