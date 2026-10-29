"""Bounded views of a complete, receipt-bound archive listing; no member selection.

The decoder validates the WHOLE archive before this projection. Filtering never
changes what the decoder checks or what exact --member means. Paging is read-only.
"""
import argparse
import json
from pathlib import Path
import sys

import source_evidence as source
from source_material import MAX_ENTRIES, _name

SCOPE = 'bank-icon-source-listing'
DEFAULT_LIMIT = 20
MAX_LIMIT = 100
MAX_RESPONSE = 16 * 1024
MAX_LISTING = 2 * 1024 * 1024


def _size(value):
    return len((json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode())


def save_listing(session, root, receipt_link, receipt, downloaded, id, base, chosen, decoded):
    if source.sha(downloaded.read_bytes()) != receipt['body']['sha256']:
        raise ValueError('source changed after complete archive inspection')
    document = {'schemaVersion': 1, 'scope': SCOPE, 'id': id, 'baseCommit': base,
                'retrieval': receipt_link, 'download': source.link(session, downloaded),
                'selection': chosen, 'decoded': decoded, 'createdAt': source.now()}
    if _size(document) > MAX_LISTING:
        raise ValueError('source listing exceeds the evidence size limit')
    path = root / 'listing.json'
    source.write_json(path, document)
    return page(session, source.link(session, path))


def _page(session, listing, document, *, offset=0, limit=DEFAULT_LIMIT, name_contains=''):
    if (type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= MAX_LIMIT or
            type(name_contains) is not str or len(name_contains) > 256 or
            any(ord(c) < 32 or ord(c) == 127 for c in name_contains)):
        raise ValueError('listing needs offset >= 0, limit 1..100 and a bounded plain-text name filter')
    decoded = document['decoded']
    rows = decoded['members']
    if decoded.get('kind') != 'selection-required' or decoded.get('format') not in {'zip', 'tar'}:
        raise ValueError('not an archive-member listing')
    if type(rows) is not list or len(rows) > MAX_ENTRIES:
        raise ValueError('invalid archive listing length')
    seen = set()
    for row in rows:
        if (type(row) is not dict or row.keys() != {'name', 'bytes', 'directory', 'encrypted'} or
                type(row['bytes']) is not int or row['bytes'] < 0 or
                type(row['directory']) is not bool or type(row['encrypted']) is not bool):
            raise ValueError('invalid archive listing member')
        normalized = _name(row['name'])
        if normalized in seen:
            raise ValueError('ambiguous archive listing members')
        seen.add(normalized)
    matches = [row for row in rows if name_contains.casefold() in row['name'].casefold()]
    if offset > len(matches):
        raise ValueError('listing offset exceeds the matched member count; restart at offset 0 after changing filter')
    command = [sys.executable, str(Path(__file__).resolve()), '--session', str(session),
               '--listing', listing['path'], '--sha256', listing['sha256'], '--limit', str(limit),
               '--name-contains', name_contains]
    result = {'status': 'source-selection-required', 'kind': 'selection-required', 'format': decoded['format'],
              'resourceSha256': document['download']['sha256'], 'listing': listing,
              'guide': str(Path(__file__).resolve().parents[1] / 'references/preparation-feedback.md'),
              'selection': document['selection'], 'steps': decoded['steps'],
              'totalMembers': len(rows), 'matchedMembers': len(matches), 'offset': offset, 'limit': limit,
              'nameContains': name_contains, 'members': [], 'returned': 0, 'nextOffset': None,
              'listingArgv': command, 'nextPageArgv': None,
              'nextAction': 'Use nextPageArgv or listingArgv with --name-contains to inspect the saved full listing. Select only an evidenced exact --member with workflow.py source-material; this listing is not a material/approval.'}
    for row in matches[offset:offset + limit]:
        trial = {**result, 'members': [*result['members'], row], 'returned': result['returned'] + 1}
        following = offset + trial['returned']
        if following < len(matches):
            trial.update(nextOffset=following, nextPageArgv=command + ['--offset', str(following)])
        else:
            trial.update(nextOffset=None, nextPageArgv=None)
        if _size(trial) > MAX_RESPONSE:
            break
        result = trial
    if _size(result) > MAX_RESPONSE or (offset < len(matches) and result['returned'] == 0):
        raise ValueError('listing page metadata exceeds the output budget; full listing retained as evidence')
    return result


def page(session, listing, **options):
    session = Path(session).resolve(strict=True)
    path = source.artifact(session, listing['path'], 'complete source listing')
    if path.stat().st_size > MAX_LISTING:
        raise ValueError('source listing exceeds the evidence size limit')
    source.linked(session, listing, 'complete source listing')
    document = source.read_json(path)
    if (document.get('scope') != SCOPE or type(document.get('schemaVersion')) is not int or
            document['schemaVersion'] != 1 or type(document.get('id')) is not int or
            not listing['path'].startswith(f"evidence/{document['id']}/") or path.name != 'listing.json'):
        raise ValueError('source listing does not bind a bank evidence directory')
    receipt, downloaded = source.retrieval(session, document['retrieval'], document['id'],
                                           document['baseCommit'], source.instant(source.now(), 'listing read'))
    if document['download'] != source.link(session, downloaded):
        raise ValueError('source listing download changed')
    return _page(session, listing, document, **options)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse can echo untrusted arguments; this command returns bounded JSON.
        raise ValueError('invalid listing arguments; use --help for the supported flags')


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument('--session', required=True)
    parser.add_argument('--listing', required=True, help='Exact evidence-relative listing.json path returned by source-material')
    parser.add_argument('--sha256', required=True, help='Returned listing artifact digest; never recompute after editing it')
    parser.add_argument('--offset', type=int, default=0)
    parser.add_argument('--limit', type=int, default=DEFAULT_LIMIT)
    parser.add_argument('--name-contains', default='', help='Case-insensitive literal substring; not regex or glob')
    try:
        args = parser.parse_args(argv)
        result = page(args.session, {'path': args.listing, 'sha256': args.sha256},
                      offset=args.offset, limit=args.limit, name_contains=args.name_contains)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result = {'status': 'blocked', 'reason': str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result['status'] == 'blocked' else 0


if __name__ == '__main__':
    sys.exit(main())
