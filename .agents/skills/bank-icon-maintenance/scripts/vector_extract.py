"""Extract self-contained original SVG subtrees and sprite symbols, never trace.

No CSS computation, external resource resolution, font conversion or drawing.
Unsupported dependencies are a retrieval limitation, not an absent original.
Only called inside source_material's resource-limited decoder subprocess.
"""
import copy
from html.parser import HTMLParser
import re
import xml.etree.ElementTree as ET

SVG = 'http://www.w3.org/2000/svg'
NS = '{' + SVG + '}'
MAX_NODES = 20000
MAX_SVGS = 512


def is_html(content):
    start = content.lstrip(b'\xef\xbb\xbf \r\n\t')[:512].lower()
    return bool(re.match(br'<(?:!doctype\s+html\b|html\b|head\b|body\b|div\b|main\b|section\b|span\b)', start))


class InlineSVG(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=False)
        self.text, self.depth, self.start, self.rows = text, 0, None, []
        self.offsets = [0]
        for match in re.finditer('\n', text):
            self.offsets.append(match.end())
        self.feed(text)
        self.close()
        if self.depth:
            raise ValueError('inline SVG is incomplete')

    def source_offset(self):
        line, column = self.getpos()
        return self.offsets[line - 1] + column

    def handle_starttag(self, tag, attrs):
        if tag == 'svg':
            if not self.depth:
                self.start = self.source_offset()
            self.depth += 1

    def handle_startendtag(self, tag, attrs):
        if tag == 'svg' and not self.depth:
            self.rows.append(self.get_starttag_text())
            self.check_count()

    def handle_endtag(self, tag):
        if tag == 'svg' and self.depth:
            self.depth -= 1
            if not self.depth:
                end = self.text.find('>', self.source_offset())
                if end < 0:
                    raise ValueError('inline SVG closing tag is incomplete')
                self.rows.append(self.text[self.start:end + 1])
                self.check_count()

    def check_count(self):
        if len(self.rows) > MAX_SVGS:
            raise ValueError('HTML contains too many inline vectors')


def _parse(content):
    text = content.decode('utf-8-sig') if isinstance(content, bytes) else content
    if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
        raise ValueError('vector declarations/entities are unsupported')
    root = ET.fromstring(text)
    if root.tag == 'svg':
        # HTML's implicit SVG namespace becomes explicit, without changing paths.
        text = re.sub(r'<svg(?=[\s/>])', '<svg xmlns="' + SVG + '"', text, count=1)
        root = ET.fromstring(text)
    if root.tag != NS + 'svg' or sum(1 for _ in root.iter()) > MAX_NODES:
        raise ValueError('expected a bounded SVG subtree')
    ids = [element.get('id') for element in root.iter() if element.get('id') is not None]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate vector IDs make source selection ambiguous')
    return root, text.encode('utf-8')


def _self_contained(root, content):
    from source_evidence import _svg_safe
    _svg_safe(content)
    text = content.decode('utf-8')
    if re.search(r'\bvar\s*\(|\bcurrentColor\b|\binherit\b', text, re.I):
        raise ValueError('vector depends on external/inherited presentation; obtain a self-contained original')
    ids = {element.get('id') for element in root.iter() if element.get('id')}
    for element in root.iter():
        for key, value in element.attrib.items():
            if key.rsplit('}', 1)[-1] == 'href' and (not value.startswith('#') or value[1:] not in ids):
                raise ValueError('unresolved vector reference')
        for value in [*element.attrib.values(), element.text or '']:
            for target in re.findall(r'url\s*\((.*?)\)', value, re.I | re.S):
                if target.strip(' \t\r\n\"\'').removeprefix('#') not in ids:
                    raise ValueError('unresolved vector paint reference')


def extract(content, selection):
    if selection is not None and (type(selection) is not dict or not selection or
            selection.keys() - {'svgIndex', 'svgId', 'symbolId'} or
            {'svgIndex', 'svgId'}.issubset(selection)):
        raise ValueError('select an exact inline SVG index/id and/or a symbol id')
    chosen = selection or {}
    if 'svgIndex' in chosen and (type(chosen['svgIndex']) is not int or chosen['svgIndex'] < 0):
        raise ValueError('inline SVG index must be a nonnegative integer')
    for key in ('svgId', 'symbolId'):
        if key in chosen and (type(chosen[key]) is not str or not chosen[key] or len(chosen[key]) > 1024):
            raise ValueError('vector id must be exact nonempty text')
    if is_html(content):
        html = content.decode('utf-8-sig')
        rows = InlineSVG(html).rows
        if not rows:
            raise ValueError("HTML contains no original inline SVG")
        parsed = [_parse(row) for row in rows]
        listing = [{'index': i, 'id': root.get('id'), 'viewBox': root.get('viewBox'),
                    'symbols': [el.get('id') for el in root.iter(NS + 'symbol') if el.get('id')]}
                   for i, (root, _) in enumerate(parsed)]
        if 'svgIndex' not in chosen and 'svgId' not in chosen:
            return {'kind': 'selection-required', 'format': 'html', 'vectors': listing}, None
        indices = ([chosen['svgIndex']] if 'svgIndex' in chosen else
                   [row['index'] for row in listing if row['id'] == chosen['svgId']])
        if len(indices) != 1 or indices[0] >= len(parsed):
            raise ValueError('inline SVG selection is missing or ambiguous')
        root, material = parsed[indices[0]]
        # A subtree is not computed browser CSS. Refuse external stylesheet
        # dependencies instead of silently changing an inherited colour/shape.
        without_svgs = html
        for row in rows:
            without_svgs = without_svgs.replace(row, '', 1)
        if re.search(r'<style\b|<link\b[^>]*\bstylesheet\b|\bstyle\s*=', without_svgs, re.I):
            raise ValueError('HTML has external SVG style context; use a self-contained original asset')
    else:
        if 'svgIndex' in chosen or 'svgId' in chosen:
            raise ValueError('inline selection requires an HTML resource')
        root, material = _parse(content)
    if 'symbolId' in chosen:
        matches = [el for el in root.iter(NS + 'symbol') if el.get('id') == chosen['symbolId']]
        if len(matches) != 1:
            raise ValueError('sprite symbol does not exist exactly once')
        symbol = matches[0]
        if not symbol.get('viewBox'):
            raise ValueError('sprite symbol lacks its own viewport; do not invent geometry')
        parents = {child: parent for parent in root.iter() for child in parent}
        ancestor = parents.get(symbol)
        while ancestor is not None:
            if ancestor.tag not in {NS + 'svg', NS + 'defs'}:
                raise ValueError('sprite symbol has inherited group context; no flattening')
            for key, value in ancestor.attrib.items():
                if key == 'style' and re.fullmatch(r'\s*display\s*:\s*none\s*;?\s*', value):
                    continue
                if key not in {'id', 'version', 'width', 'height', 'viewBox', 'role', 'focusable'} and not key.startswith('aria-'):
                    raise ValueError('sprite symbol has inherited presentation; obtain a self-contained original')
            ancestor = parents.get(ancestor)
        if list(root.iter(NS + 'style')):
            raise ValueError('sprite stylesheet needs CSS-aware extraction; no automatic style rewrite')
        # Preserve the selected symbol and shared definitions. Do not apply a
        # sprite container's display:none or invent a new colour/background.
        result = ET.Element(NS + 'svg', dict(symbol.attrib))
        for defs in root.findall(NS + 'defs'):
            preserved = copy.deepcopy(defs)
            for parent in preserved.iter():
                for child in list(parent):
                    if child.tag == NS + 'symbol' and child.get('id') == chosen['symbolId']:
                        parent.remove(child)
            result.append(preserved)
        for child in symbol:
            result.append(copy.deepcopy(child))
        ET.register_namespace('', SVG)
        ET.register_namespace('xlink', 'http://www.w3.org/1999/xlink')
        material = ET.tostring(result, encoding='utf-8')
        root = result
    _self_contained(root, material)
    return {'kind': 'material', 'format': 'svg'}, material
