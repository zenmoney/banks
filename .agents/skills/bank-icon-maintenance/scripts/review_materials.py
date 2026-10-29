"""Prepare strictly bounded reviewer files and a fresh, commit-pinned PR description.

The caller revalidates the canonical candidate report and owns Git commits and publication.
This module reads only frozen worktree and previously validated session evidence.
"""
import copy
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import quote, unquote

from evidence import RENDERER, artifact, baseline_bytes, require_hash, web_url
from headless import RENDERER as OFFSCREEN

SOURCE_TYPES = {"svg": "SVG", "png": "PNG", "jpg": "JPEG", "webp": "WebP"}
OTHER_NAMES = ("base.svg", "final.svg", "candidate.diff", "final.xml",
               "comparison-light.png", "comparison-dark.png")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_SLUG = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?\Z")
# A URL path segment such as /wiki/File:Name is not a file: URI scheme.
_LOCAL = re.compile(
    r"""(?:(?<![\w/])file:|(?:^|[\s(\[{'"`])/(?=[A-Za-z_.])|
    (?:^|[\s(\[{'"`])(?:\.\.?/|(?:evidence|reports|worktree|asset-publication)/|[A-Za-z]:[\\/]))""",
    re.I | re.X)
_AUTH = re.compile(r"(?:github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|"
                   r"authorization\s*[:=]\s*(?:bearer|basic)\s+|"
                   r"https?://[^/@\s]+:[^/@\s]+@)", re.I)


def _hash(content):
    return hashlib.sha256(content).hexdigest()

def _source_format(content):
    from source_evidence import require_vector_source
    require_vector_source(content)
    return "svg"


def _scan_material(name, content, session, *, raster_format=None):
    def check_text(label, text):
        if _LOCAL.search(text) or _AUTH.search(text) or str(session) in text:
            raise ValueError(f"{label} contains a local path or credential-like text; inspect before publication")

    if raster_format is not None:
        from raster_metadata import inspect_metadata
        inspect_metadata(content, raster_format,
                         lambda field, text: check_text(f"{name} metadata {field}", text))
    else:
        check_text(name, content.decode("utf-8", errors="replace"))


def _file(session, link, label, *, expected=None):
    if type(link) is not dict or link.keys() != {"path", "sha256"}:
        raise ValueError(f"{label} requires exact path and SHA-256")
    path = artifact(session, link["path"], label)
    content = path.read_bytes()
    digest = require_hash(link["sha256"], label)
    if _hash(content) != digest or (expected is not None and digest != expected):
        raise ValueError(f"{label} differs from reviewed SHA-256")
    return content


def _base(session, bank, report):
    folder = bank.get("folder")
    if (type(folder) is not str or len(folder.split("/")) != 2 or
            folder.split("/")[0] != "banks" or
            any(part in ("", ".", "..") for part in folder.split("/"))):
        raise ValueError("unsafe bank folder")
    worktree = session / "worktree"
    path = worktree / folder / "icon.svg"
    metadata = worktree / folder / "info.json"
    for item in (worktree, worktree / "banks", worktree / folder, path, metadata):
        if item.is_symlink():
            raise ValueError("catalog path contains a symlink")
    current = baseline_bytes(path, bank["iconHash"])
    meta = metadata.read_bytes()
    if (_hash(meta) != bank["metadataHash"] or
            json.loads(meta) != bank["metadata"] or
            report["evidence"]["baseIconHash"] != bank["iconHash"] or
            report["provenance"]["metadataHash"] != _hash(meta)):
        raise ValueError("frozen bank icon or metadata differs from review")
    return current


def _review_source(session, raw, evidence, bank):
    """Use the same validated material as the source/final comparison, not its container."""
    original = {"path": raw["path"], "sha256": raw["iconHash"]}
    if "sourceComparison" not in evidence:
        if "sourceMaterial" in raw:
            raise ValueError("selected source material requires a source comparison")
        return original, None
    import source_evidence as source
    view = source.read_json(source.linked(session, evidence["sourceComparison"], "source comparison"))
    reference = source.validate_reference(session, view["sourceReference"], raw,
        {"baseCommit": evidence["baseCommit"]}, bank, source.instant(evidence["checkedAt"], "candidate checkedAt"))
    if "sourceMaterial" not in reference:
        return original, None
    material = source.read_json(source.linked(session, reference["sourceMaterial"], "source material"))
    # Member identities are URI-encoded data, not local filesystem links. No
    # reference to the download's local path is projected onto the selected file.
    origin = {"resourceSha256": raw["iconHash"], "materialSha256": reference["source"]["sha256"],
              "members": ["member:" + quote(name, safe="") for name in material["selection"]],
              "operations": [step["operation"] + ":" + step["format"] for step in material["steps"]]}
    if "vectorSelection" in material:
        origin["selectors"] = [key + ":" + quote(str(value), safe="") for key, value in sorted(material["vectorSelection"].items())]
    return reference["source"], origin


def prepare(session: Path, bank: dict, prepared_report: dict, *, public_text=None, admission=None) -> dict:
    """Return exact public files plus renderable, path-free details."""
    session = Path(session)
    report = prepared_report
    if (type(bank) is not dict or type(report) is not dict or
            report.get("outcome") != "candidate" or report.get("id") != bank.get("id") or
            type(bank.get("id")) is not int):
        raise ValueError("prepared candidate report does not match selected bank")
    evidence = report["evidence"]
    prior = report["before"]["proposals"][-1]
    proposal = report["after"]["proposals"][-1]
    raw = proposal["sourceCandidate"]
    candidate = evidence["candidate"]
    final = proposal["candidate"]
    if (raw != prior.get("candidate") or final.get("sha256") != candidate.get("sha256") or
            final.get("iconHash") != candidate.get("sha256") or
            report["candidate"] != {key: final[key] for key in ("path", "sha256")} or
            report["renderBinding"]["sourceSha256"] != candidate["sha256"] or
            evidence["baseCommit"] != report["provenance"]["baseCommit"] or
            evidence["metadataHash"] != bank["metadataHash"]):
        raise ValueError("source, final, XML or frozen base binding changed")
    base_bytes = _base(session, bank, report)
    source_link, source_origin = _review_source(session, raw, evidence, bank)
    source_bytes = _file(session, source_link, "source material")
    source_format = _source_format(source_bytes)
    source_name = f"source.{source_format}"
    final_bytes = _file(session, {key: final[key] for key in ("path", "sha256")},
                        "saved final SVG", expected=candidate["sha256"])
    _file(session, {key: candidate[key] for key in ("path", "sha256")},
          "reviewed final SVG", expected=final["sha256"])
    diff_bytes = _file(session, evidence["diff"], "base-to-final diff")
    desktop = evidence["desktop"]
    if desktop["renderer"] not in (RENDERER, OFFSCREEN) or report["renderBinding"]["xmlSha256"] != (
            require_hash(report["renderBinding"]["xmlSha256"], "generated XML")):
        raise ValueError("missing generated XML/Compose Desktop render binding")
    manifest_bytes = _file(session, {"path": desktop["manifest"],
                                     "sha256": desktop["manifestSha256"]}, "render manifest")
    manifest = json.loads(manifest_bytes)
    entries = [row for row in manifest["entries"] if row.get("id") == bank["id"]]
    pins = manifest.get("pins", {})
    if (len(entries) != 1 or entries[0].get("error") is not None or
            entries[0].get("sourceValidated") is not True or
            entries[0].get("sourcePath") != str(session / candidate["path"]) or
            entries[0].get("sourceSha256") != candidate["sha256"] or
            pins.get("converter") != report["renderBinding"]["converter"] or
            pins.get("compose") != report["renderBinding"]["compose"]):
        raise ValueError("render manifest is not bound to exact final candidate and tools")
    resource = f"bank_{bank['id']}" if bank["id"] >= 0 else f"bank_m{-bank['id']}"
    if entries[0].get("resourceName") != resource:
        raise ValueError("generated resource differs from selected bank")
    manifest_path = Path(desktop["manifest"])
    xml_path = (manifest_path.parent / "composeResources" / "drawable" / f"{resource}.xml").as_posix()
    xml_bytes = _file(session, {"path": xml_path, "sha256": entries[0]["xmlSha256"]},
                      "generated final XML", expected=report["renderBinding"]["xmlSha256"])
    addition = base_bytes is None
    if addition:
        from candidate import _canonical_diff
        if diff_bytes != _canonical_diff(None, final_bytes, bank["id"]).encode():
            raise ValueError("first icon diff differs from exact addition")
    comparison_role = "candidate" if addition else "beforeAfter"
    comparisons = {}
    for background in ("light", "dark"):
        comparison = evidence["comparison"][background]
        if addition and (comparison["base"] is not None or comparison["beforeAfter"] is not None):
            raise ValueError("first icon review cannot contain fabricated baseline images")
        image = _file(session, comparison[comparison_role],
                      f"{background} reviewed {comparison_role}")
        if not image.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError(f"{background} reviewed comparison is not PNG")
        comparisons[f"comparison-{background}.png"] = image
    blobs = {} if addition else {"base.svg": base_bytes}
    blobs.update({source_name: source_bytes, "final.svg": final_bytes,
                  "candidate.diff": diff_bytes, "final.xml": xml_bytes})
    for name, content in blobs.items():
        # /dev/null is the canonical absent Git baseline, not a disclosed local path.
        scanned = content.split(b"\n", 1)[1] if addition and name == "candidate.diff" else content
        _scan_material(name, scanned, session)
    blobs.update(comparisons)
    files = [{"name": name, "content": content, "sha256": _hash(content), "size": len(content)}
             for name, content in blobs.items()]
    by_name = {file["name"]: file for file in files}
    source = evidence["candidate"]
    if (source["sourceUrl"] != raw["sourceUrl"] or
            source["sourceAssetUrl"] != raw["sourceAssetUrl"]):
        raise ValueError("source links changed from fetched candidate")
    details = {"materialPath": f"review-materials/{bank['id']}/", "id": bank["id"],
               "name": bank["title"], "metadata": bank["metadata"],
               "baseCommit": evidence["baseCommit"], "baseSha256": bank["iconHash"],
               "sourceSha256": by_name[source_name]["sha256"], "finalSha256": by_name["final.svg"]["sha256"],
               "xmlSha256": by_name["final.xml"]["sha256"], "checkedAt": evidence["checkedAt"],
               "sourceUrl": web_url(source["sourceUrl"], "source URL"),
               "sourceAssetUrl": web_url(source["sourceAssetUrl"], "source asset URL"),
               "sources": prior["sources"], "assessment": prior["assessment"],
               "candidate": {key: candidate[key] for key in (
                   "rationale", "role", "variant", "transforms", "detailsJudgement",
                   "proportionsJudgement", "visibleDiffExplanation")},
               "renderer": desktop["renderer"], "observedAt": desktop["observedAt"],
               "visualComparison": desktop["visualComparison"],
               "platforms": [{key: row[key] for key in
                             ("target", "status", "available", "observation", "sourceSha256",
                              "xmlSha256", "converter", "compose") if key in row}
                             for row in evidence["platforms"]]}
    if source_origin is not None:
        details["sourceOrigin"] = source_origin
    # Map only paths whose exact bytes were read and validated above. Never look
    # up arbitrary files mentioned in prose, nor guess a role from a basename.
    locations = [(source_link["path"], by_name[source_name]),
                 (final["path"], by_name["final.svg"]),
                 (candidate["path"], by_name["final.svg"]),
                 (evidence["diff"]["path"], by_name["candidate.diff"]),
                 (xml_path, by_name["final.xml"])]
    if not addition:
        locations.insert(0, (f"worktree/{bank['folder']}/icon.svg", by_name["base.svg"]))
    locations.extend((evidence["comparison"][theme][comparison_role]["path"],
                      by_name[f"comparison-{theme}.png"]) for theme in ("light", "dark"))
    if admission is not None:
        from admission import public_summary
        details["admission"] = public_summary(admission)
    if proposal.get("taskResponse"):
        # Requirements stay byte-exact in the source-bound proposal. In the
        # public Markdown list only, ordinary line endings are layout, not
        # control data. Keep all words and let the unchanged public scanner
        # reject paths, credentials and every remaining control character.
        details["taskResponse"] = [
            {**row, **{key: row[key].replace("\r\n", "\n").replace("\n", " ")
                      for key in ("quote", "resolution")}}
            for row in proposal["taskResponse"]]
        details["sourceMR"] = proposal.get("supersedesMR") or (proposal.get("revision") or {}).get("mr", {}).get("url")
    title = f"Bank icon: {bank['title']} (Company.id {bank['id']})"
    details = _project_narratives(details, session, locations, public_text, title=title)
    return {"files": files, "title": title, "details": details}


def _project_narratives(details, session, locations, public_text=None, *, title=None):
    """Derive public prose without changing immutable evidence or URL/ID fields.

    Strings without local references retain their old representation. Linked
    prose uses typed segments so untrusted text cannot inject a link placeholder.
    Only the seven validated publication files can become material links.
    """
    if public_text is None:
        public_text = {}
    if type(public_text) is not dict or any(type(key) is not str for key in public_text):
        raise ValueError("public text must map narrative field paths to nonempty strings")
    unused = set(public_text)
    issues = []
    session = session.absolute()
    references, aliases = {}, set()
    for relative, file in locations:
        reference = {"material": file["name"], "sha256": file["sha256"]}
        previous = references.setdefault(relative, reference)
        if previous["sha256"] != reference["sha256"]:
            raise ValueError("one local reference names different reviewed bytes")
        path = session / relative
        aliases.update((relative, "./" + relative, str(path), path.as_uri()))
    roots = r"(?:evidence|reports|worktree|asset-publication)/"
    prefix = (r"(?:(?:\./)?" + roots + "|" + re.escape(str(session) + "/") +
              "|" + re.escape(session.as_uri() + "/") + ")")
    known = "|".join(re.escape(alias) for alias in sorted(aliases, key=lambda v: (-len(v), v)))
    # Keep HTTP URLs intact; the same token in a source URL is not a local file.
    # Quoted paths may contain spaces. Exact allowlisted unquoted paths may too.
    boundary = r"(?=$|[\s\"'`()<>{}\[\],;]|[.!?:](?=$|\s))"
    tokens = re.compile(
        r"(?P<url>https?://[^\s\"'`<>]+)|(?<![\w/\\:.-])(?:"
        r"(?P<quote>[\"'`])(?P<quoted>" + prefix + r".*?)(?P=quote)|"
        r"(?P<known>" + known + r")" + boundary + "|"
        r"(?P<bare>" + prefix + r"[^\s\"'`()<>{}\[\],;]+))")

    def project(value):
        if (type(value) is not str or _AUTH.search(value) or
                any(ord(char) < 32 for char in value)):
            raise ValueError("review narrative contains credential-like text or control character")
        parts, end = [], 0
        for match in tokens.finditer(value):
            if match.group("url") is not None:
                continue
            token = match.group("quoted") or match.group("known") or match.group("bare")
            suffix = ""
            if match.group("bare"):
                token = token.rstrip(".!?:")
                suffix = match.group("bare")[len(token):]
            if token.startswith(session.as_uri() + "/"):
                token = unquote(token[len(session.as_uri()) + 1:])
            elif token.startswith(str(session) + "/"):
                token = token[len(str(session)) + 1:]
            elif token.startswith("./"):
                token = token[2:]
            if (not re.match(roots, token) or "\\" in token or
                    any(part in ("", ".", "..") for part in token.split("/")) or
                    _AUTH.search(token) or any(ord(char) < 32 for char in token)):
                raise ValueError("unsafe local reference in review narrative")
            parts.append(value[end:match.start()])
            parts.append(references.get(token, "[local artifact not published; reference omitted]"))
            parts.append(suffix)
            end = match.end()
        if not parts:
            return value
        parts.append(value[end:])
        # Plain text is still escaped normally, never interpreted as Markdown.
        return [part for part in parts if part != ""]

    def text(value, field):
        if field in public_text:
            value = public_text[field]
            unused.remove(field)
            if type(value) is not str or not value.strip():
                issues.append({'field': field, 'code': 'nonempty-text-required'})
                return value
        field = _safe_field(field, 0)
        try:
            projected = project(value)
        except ValueError:
            issues.extend(_public_issues(value, field) or [{'field': field, 'code': 'unsafe-local-reference'}])
            return value
        return projected

    public = copy.deepcopy(details)
    for index, row in enumerate(public["sources"]):
        for field in ("observation", "independenceGroup"):
            row[field] = text(row[field], f"sources.{index}.{field}")
    assessment = public["assessment"]
    for aspect in ("identity", "currency", "variant"):
        assessment[aspect]["basis"] = text(assessment[aspect]["basis"], f"assessment.{aspect}.basis")
    assessment["independenceRationale"] = text(assessment["independenceRationale"],
                                               "assessment.independenceRationale")
    assessment["uncertainties"] = [text(item, f"assessment.uncertainties.{index}")
                                   for index, item in enumerate(assessment["uncertainties"])]
    candidate = public["candidate"]
    for field in candidate:
        candidate[field] = ([text(item, f"candidate.transforms.{index}")
                             for index, item in enumerate(candidate[field])] if field == "transforms"
                            else text(candidate[field], f"candidate.{field}"))
    public["visualComparison"] = text(public["visualComparison"], "visualComparison")
    for index, row in enumerate(public["platforms"]):
        row["observation"] = text(row["observation"], f"platforms.{index}.observation")
    for index, field in enumerate(sorted(unused)):
        issues.append({'field': 'publicText.' + _safe_field(field, index), 'code': 'not-editable-narrative'})
    # Admission and task responses are already present. Do not stop at the first
    # narrative error and hide a later, non-editable gate finding from the agent.
    issues.extend(_public_issues(public))
    if title is not None:
        issues.extend(_public_issues(title, 'title'))
    if issues:
        raise PublicPreflightError(issues)
    return public


def _narrative(value, files, root):
    """Escape source prose; only bound material segments become GitHub links."""
    if type(value) is str:
        return _text(value)
    if type(value) is not list or not value:
        raise ValueError("invalid projected review narrative")
    parts = []
    for part in value:
        if type(part) is str:
            parts.append(_text(part))
        elif (type(part) is dict and part.keys() == {"material", "sha256"} and
              type(part["material"]) is str and part["material"] in files and
              part["sha256"] == files[part["material"]]["sha256"]):
            name = part["material"]
            parts.append(f"[{name}]({root}{name})")
        else:
            raise ValueError("review narrative link is outside the exact material allowlist")
    return "".join(parts)


def _text(value):
    if type(value) is not str or _LOCAL.search(value) or _AUTH.search(value) or any(ord(c) < 32 for c in value):
        raise ValueError("review text contains a local path, credential-like text or control character")
    return re.sub(r"([\\`*_{}\[\]()<>!|])", r"\\\1", value)


class PublicPreflightError(ValueError):
    """Safe field paths/codes only; never include offending values in diagnostics."""
    def __init__(self, issues):
        self.issues = [dict(field=field, code=code) for field, code in
                       dict.fromkeys((row['field'], row['code']) for row in issues)]
        super().__init__('public review rejected (local path, credential-like text, control character or invalid field): ' + '; '.join(
            f"{row['field']} ({row['code']})" for row in self.issues) +
            '. Use --public-text only for editable narratives; preserve immutable evidence and admission facts.')


def _safe_field(key, index):
    # An unknown JSON key is untrusted text too; never echo a credential/path as
    # diagnostic ancestry. Static schema field names and indices remain useful.
    return key if (isinstance(key, str) and len(key) <= 120 and
                   re.fullmatch(r'[A-Za-z0-9_.-]+', key) and not _AUTH.search(key)) else f'<key-{index}>'


def _public_issues(value, field=''):
    issues = []
    if isinstance(value, dict):
        for index, (key, item) in enumerate(value.items()):
            name = _safe_field(key, index)
            child = f'{field}.{name}' if field else name
            if isinstance(key, str):
                issues.extend(_public_issues(key, child + '.<key>'))
            issues.extend(_public_issues(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            issues.extend(_public_issues(item, f'{field}.{index}'))
    elif isinstance(value, str):
        for code, failed in (('local-path', bool(_LOCAL.search(value))),
                             ('credential-like-text', bool(_AUTH.search(value))),
                             ('control-character', any(ord(c) < 32 for c in value))):
            if failed:
                issues.append({'field': field or '<value>', 'code': code})
    return issues


def _public_values(value):
    issues = _public_issues(value)
    if issues:
        raise PublicPreflightError(issues)


def _url(value):
    web_url(value, "review source")
    if _LOCAL.search(value) or _AUTH.search(value):
        raise ValueError("local or credential-bearing source URL")
    return quote(value, safe=":/?#@!$&'*+,;=%-._~")


def render(package: dict, owner: str, repo: str, material_commit: str) -> str:
    """Render a self-contained PR body; immutable raw/blob links only."""
    if (any(type(slug) is not str or not _SLUG.fullmatch(slug) for slug in (owner, repo)) or
            type(material_commit) is not str or not re.fullmatch(r"[0-9a-f]{40}", material_commit)):
        raise ValueError("invalid GitHub repository or material commit")
    details = package["details"]
    _public_values(details)
    files = package["files"]
    source_names = {f"source.{extension}" for extension in SOURCE_TYPES}
    addition = details["baseSha256"] is None
    other_names = set(OTHER_NAMES) - ({"base.svg"} if addition else set())
    count = len(other_names) + 1
    if (type(files) is not list or len(files) != count or
            len({file["name"] for file in files}) != count or
            len({file["name"] for file in files if file["name"] in source_names}) != 1 or
            {file["name"] for file in files if file["name"] not in source_names} != other_names or
            any(type(file.get("size")) is not int or file["size"] < 0 or
                not _SHA.fullmatch(file.get("sha256", "")) for file in files)):
        raise ValueError("review material allowlist or hashes differ")
    folder = f"review-materials/{details['id']}/"
    if details.get("materialPath") != folder:
        raise ValueError("unexpected review material path")
    by_name = {file["name"]: file for file in files}
    source_name = next(name for name in by_name if name in source_names)
    bindings = [(source_name, "sourceSha256"), ("final.svg", "finalSha256"),
                ("final.xml", "xmlSha256")]
    if not addition:
        bindings.insert(0, ("base.svg", "baseSha256"))
    for name, key in bindings:
        if by_name[name]["sha256"] != details[key]:
            raise ValueError(f"{name} differs from rendered report binding")
    root = f"https://github.com/{owner}/{repo}/blob/{material_commit}/{folder}"
    def link(name, label):
        return f"[{label}]({root}{name})"
    def prose(value):
        return _narrative(value, by_name, root)
    def image(name, label):
        return f"![{label}]({root}{name}?raw=true)"
    candidate = details["candidate"]
    assessment = details["assessment"]
    metadata = json.dumps(details["metadata"], ensure_ascii=True, sort_keys=True, indent=2)
    if _LOCAL.search(metadata) or _AUTH.search(metadata):
        raise ValueError("metadata contains a local path or credential-like text")
    fence = chr(96) * max(3, max((len(match.group()) for match in
                                  re.finditer(chr(96) + "+", metadata)), default=0) + 1)
    source_label = "Selected source material" if "sourceOrigin" in details else "Fetched source"
    lines = [f"## {_text(details['name'])} — Company.id {details['id']}", "",
             "Catalog metadata (unchanged):", "", f"{fence}json", metadata, fence, "",
             f"Frozen base commit: `{_text(details['baseCommit'])}`. Checked: `{_text(details['checkedAt'])}`.",
             "", "### Exact versions and source", "",
             ("- No existing icon.svg at frozen base commit; this is the first icon addition."
              if addition else
              f"- Current/base SVG: {link('base.svg', 'base.svg')} — SHA-256 `{details['baseSha256']}`."),
             f"- {source_label} {SOURCE_TYPES[source_name.split('.')[1]]}: {link(source_name, source_name)} — SHA-256 `{details['sourceSha256']}`.",
             f"- Proposed final SVG: {link('final.svg', 'final.svg')} — SHA-256 `{details['finalSha256']}`.",
             f"- Generated VectorDrawable XML: {link('final.xml', 'final.xml')} — SHA-256 `{details['xmlSha256']}`.",
             f"- Exact base-to-final changes: {link('candidate.diff', 'candidate.diff')}.",
             f"- Source page: [{_text(details['sourceUrl'])}]({_url(details['sourceUrl'])}).",
             f"- Source asset: [{_text(details['sourceAssetUrl'])}]({_url(details['sourceAssetUrl'])}).",
             "", "### Evidence and judgment", ""]
    if "sourceOrigin" in details:
        origin = details["sourceOrigin"]
        if origin["materialSha256"] != details["sourceSha256"]:
            raise ValueError("source material origin differs from public file")
        require_hash(origin["resourceSha256"], "downloaded resource")
        _public_values(origin)
        lines[-2:] = ["", "### Source material origin", "",
            f"Downloaded resource: [{_text(details['sourceAssetUrl'])}]({_url(details['sourceAssetUrl'])}); "
            f"SHA-256 `{origin['resourceSha256']}`.",
            "Member selection (URI-encoded identifiers): " +
            (", ".join(_text(item) for item in origin["members"]) or "direct resource") + ".",
            *(["Vector selection (URI-encoded identifiers): " + ", ".join(_text(item) for item in origin["selectors"]) + "."]
              if origin.get("selectors") else []),
            "Replayed operations: " + (", ".join(_text(item) for item in origin["operations"]) or "none") + ".",
            "The source file is the exact selected material, not the whole container or an adapted SVG.",
            "", "### Evidence and judgment", ""]
    if "admission" in details:
        preflight = details["admission"]
        judged = preflight["judgments"]
        lines += ["### Publication admission (separate from source fidelity)", "",
                  f"Contract {preflight['contractVersion']}: {_text(preflight['decision'])}; "
                  f"checked {_text(preflight['checkedAt'])}.",
                  f"Record purpose: {_text(judged['record']['purpose'])}. {_text(judged['record']['basis'])}.",
                  f"Source role: {_text(judged['asset']['sourceRole'])}; final role: {_text(judged['asset']['finalRole'])}; "
                  f"scope: {_text(judged['asset']['scope'])}.",
                  f"Applicability: {_text(judged['asset']['applicability']['basis'])}.",
                  f"Change: {_text(judged['change']['kind'])}. {_text(judged['change']['basis'])}."]
        for name, check in judged['quality'].items():
            lines.append(f"- {_text(name)}: {_text(check['status'])} — {_text(check['basis'])}.")
        for concern in judged['concerns']:
            lines.append(f"- Retained concern ({_text(concern['kind'])}): {_text(concern['basis'])}.")
        lines += ["", "The gate binds declared observations, not proof of their semantic truth or human approval.", ""]
    for row in details["sources"]:
        asset = (f"; asset [{_text(row['assetUrl'])}]({_url(row['assetUrl'])})"
                 if row.get("assetUrl") else "")
        lines.append(f"- {_text(row['kind'])} checked {_text(row['checkedAt'])}: "
                     f"[{_text(row['url'])}]({_url(row['url'])}){asset}. "
                     f"{prose(row['observation'])} (independence group: {prose(row['independenceGroup'])}).")
    lines.append(f"- Independent evidence: {prose(assessment['independenceRationale'])}.")
    for aspect in ("identity", "currency", "variant"):
        judgment = assessment[aspect]
        lines.append(f"- {aspect.capitalize()}: {_text(judgment['finding'])} — {prose(judgment['basis'])}.")
    lines.extend(f"- Uncertainty: {prose(item)}." for item in assessment["uncertainties"])
    lines += ["", f"Rationale: {prose(candidate['rationale'])}.",
              f"Role: {prose(candidate['role'])}; variant: {prose(candidate['variant'])}.",
              f"Visible difference: {prose(candidate['visibleDiffExplanation'])}.",
              f"Details: {prose(candidate['detailsJudgement'])}. "
              f"Proportions: {prose(candidate['proportionsJudgement'])}.",
              "", "Source-to-final transformations:"]
    lines.extend(f"- {prose(item)}" for item in candidate["transforms"])
    lines += ["", "### Light and dark first icon addition" if addition else "### Light and dark before/after", "",
              image("comparison-light.png", "First icon addition — light background" if addition else "Before and after — light background"), "",
              image("comparison-dark.png", "First icon addition — dark background" if addition else "Before and after — dark background"), "",
              "### Render checks and limits", "",
              f"Desktop renderer recorded: `{_text(details['renderer'])}` at "
              f"`{_text(details['observedAt'])}`. {prose(details['visualComparison'])}", ""]
    for row in details["platforms"]:
        lines.append(f"- {_text(row['target'])}: {_text(row['status'])} "
                     f"(available: {str(row['available']).lower()}) — {prose(row['observation'])}.")
    lines += ["", "Desktop observation is for the generated XML through Compose Desktop "
              "`painterResource`, not a claim of successful Android/iOS or native-widget rendering. "
              "Unavailable targets remain untested; reviewer visual approval and merge remain separate decisions."]
    if details.get("taskResponse"):
        lines += ["", "## Reviewer requirements addressed", ""]
        if details.get("sourceMR"):
            lines.append("Previous proposal: " + _url(details["sourceMR"]))
        for row in details["taskResponse"]:
            lines.append(f"- [{_text(row['quote'])}]({_url(row['url'])}): {_text(row['resolution'])}")
    return "\n".join(lines) + "\n"
