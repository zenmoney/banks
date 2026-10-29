"""Explicit synthetic judgments for disposable protocol fixtures, never model results."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import admission


def notes_for(report):
    notes = admission.template()
    notes['assessor'] = 'Synthetic unit-test assessor (not a model or human review)'
    notes['record'].update(purpose='current', basisKind='current-use',
                          basis='Controlled current bank fixture', sourceRefs=[0])
    for field in ('legalLifecycle', 'brandLifecycle'):
        notes['record'][field].update(status='active', basis='Controlled lifecycle fixture', sourceRefs=[0])
    notes['asset'].update(sourceRole='symbol', finalRole='symbol', scope='record', accompanyingText=False)
    for field in ('originalVector', 'role', 'applicability'):
        notes['asset'][field].update(status='pass', basis='Controlled original vector and context fixture', sourceRefs=[0])
    absent = report['evidence']['baseIconHash'] is None
    notes['change'].update(kind='first-addition' if absent else 'supported-replacement',
                           basis='Synthetic visible replacement fixture; no rebrand claimed', sourceRefs=[0],
                           baselineAttribution='absent' if absent else 'unknown')
    for row in notes['quality'].values():
        row.update(status='pass', basis='Explicit synthetic judgment for protocol tests, not real image review')
    notes['concerns'] = [{'kind': 'baseline-attribution', 'basis': text,
                         'priorUncertainty': i, 'target': None}
                        for i, text in enumerate(report['before']['proposals'][-1]['assessment']['uncertainties'])]
    return notes


def save(session, report):
    result = admission.build(session, report, notes_for(report))
    assert result['status'] == 'admitted', result
    return str(session / result['admission']['path'])
