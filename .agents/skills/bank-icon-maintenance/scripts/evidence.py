"""Validate the local attempt package before the session records its outcome.

This checks binding, artifact presence, and evidence completeness. It cannot establish
that a human-observed source or visual comparison is subjectively correct.
"""
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


HASH = re.compile(r"[0-9a-f]{64}\Z")
FINDINGS = {"confirmed", "uncertain", "conflicting", "disappeared"}
OUTCOMES = {"unchanged", "unconfirmed", "candidate", "error", "needs-human"}
TARGETS = {"android-compose", "android-native", "ios-compose", "ios-native"}
RENDERER = "compose-desktop-vector-xml-painterResource"


def required(obj, field, kind=str):
    value = obj.get(field)
    if type(value) is not kind or (kind is str and not value.strip()):
        raise ValueError(f"{field} must be a nonempty {kind.__name__}")
    return value


def sha(value):
    return hashlib.sha256(value).hexdigest()


def require_hash(value, label):
    if type(value) is not str or HASH.fullmatch(value) is None:
        raise ValueError(f"invalid {label} SHA-256")
    return value


def baseline_bytes(path, expected_hash):
    """Read exact pinned bytes, or confirm explicit absence; reject symlinks."""
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("baseline symlink is unsupported")
    if any(p.exists() and not p.is_dir() for p in path.parents):
        raise ValueError("baseline parent is not a directory")
    if expected_hash is None:
        if path.exists():
            raise ValueError("absent baseline appeared")
        return None
    require_hash(expected_hash, "baseline")
    if not path.is_file():
        raise ValueError("baseline is not a regular file")
    content = path.read_bytes()
    if sha(content) != expected_hash:
        raise ValueError("baseline bytes differ from pinned hash")
    return content


def baseline_blob(repo, commit, relative, expected_hash):
    """Successful tree lookup proves absence; failed Git commands never do."""
    if type(relative) is not str or Path(relative).is_absolute() or any(
            part in ("", ".", "..") for part in relative.split("/")):
        raise ValueError("invalid committed baseline path")
    if expected_hash is not None:
        require_hash(expected_hash, "baseline")
    from state_publication import git
    result = git(repo, "ls-tree", "-z", commit, "--", relative, check=False, timeout=60)
    if result.returncode:
        raise ValueError("cannot establish committed baseline")
    entries = [row for row in result.stdout.split(b"\0") if row]
    if not entries:
        if expected_hash is not None:
            raise ValueError("committed baseline is absent")
        return None
    if len(entries) != 1:
        raise ValueError("ambiguous committed baseline")
    header, name = entries[0].split(b"\t", 1)
    mode, kind, object_id = header.split()
    if name.decode() != relative or mode not in (b"100644", b"100755") or kind != b"blob":
        raise ValueError("committed baseline is not a regular file")
    if expected_hash is None:
        raise ValueError("absent committed baseline appeared")
    blob = git(repo, "cat-file", "blob", object_id.decode(), check=False, timeout=60)
    if blob.returncode or sha(blob.stdout) != expected_hash:
        raise ValueError("committed baseline bytes differ from pinned hash")
    return blob.stdout



def instant(value, label):
    if type(value) is not str or not value.endswith("Z"):
        raise ValueError(f"{label} must be UTC with Z suffix")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"invalid {label}") from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"invalid {label} UTC instant")
    return parsed


def web_url(value, label):
    if type(value) is not str:
        raise ValueError(f"{label} must be a URL")
    parsed = urlparse(value)
    if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"{label} must be an HTTP(S) URL")
    return value


def artifact(session, value, label):
    """Resolve a regular file under SESSION/evidence, rejecting every symlink."""
    if type(value) is not str or not value or Path(value).is_absolute():
        raise ValueError(f"{label} must be a relative evidence file")
    rel = Path(value)
    if (not rel.parts or rel.parts[0] != "evidence" or
            any(part in ("", ".", "..") for part in value.split("/"))):
        raise ValueError(f"{label} must remain under session evidence")
    current = session
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label} cannot contain a symlink")
    if not current.is_file():
        raise OSError(f"{label} is missing: {current}")
    return current


def source_rows(rows, checked, outcome):
    if type(rows) is not list:
        raise ValueError("sources must be a list")
    kinds = set()
    assets = set()
    for source in rows:
        if type(source) is not dict:
            raise ValueError("source observation must be an object")
        kind = required(source, "kind")
        if kind not in {"official", "third-party", "zenplugins-issue", "zenplugins-pr"}:
            raise ValueError("unknown source kind")
        kinds.add(kind)
        web_url(required(source, "url"), "source.url")
        asset_url = source.get("assetUrl")
        if asset_url is not None:
            assets.add(web_url(asset_url, "source.assetUrl"))
        if instant(source.get("checkedAt"), "source.checkedAt") > checked:
            raise ValueError("source observed after attempt")
        required(source, "observation")
        required(source, "independenceGroup")
    # Legacy kinds remain readable; no provider-specific search is mandatory.
    if outcome in {"unchanged", "candidate", "unconfirmed"} and not rows:
        raise ValueError("completed search needs recorded source observations")
    if outcome in {"unchanged", "candidate"} and not assets:
        raise ValueError("positive outcome needs source asset")
    return assets


def assessment_of(value, outcome):
    if type(value) is not dict:
        raise ValueError("assessment must be an object")
    for aspect in ("identity", "currency", "variant"):
        item = value.get(aspect)
        if type(item) is not dict or item.get("finding") not in FINDINGS:
            raise ValueError(f"assessment.{aspect} needs a finding")
        required(item, "basis")
    required(value, "independenceRationale")
    uncertainties = value.get("uncertainties")
    if type(uncertainties) is not list or any(type(item) is not str or not item.strip() for item in uncertainties):
        raise ValueError("assessment.uncertainties must be a list of descriptions")
    if outcome in ("unchanged", "candidate") and any(value[aspect]["finding"] != "confirmed"
                                                    for aspect in ("identity", "currency", "variant")):
        raise ValueError("positive outcome cannot have ambiguous identity, currency or variant")
    if outcome == "unchanged" and uncertainties:
        raise ValueError("ambiguous current variant cannot be unchanged")
    if outcome == "unconfirmed" and all(value[aspect]["finding"] == "confirmed"
                                         for aspect in ("identity", "currency", "variant")):
        raise ValueError("unconfirmed outcome must identify what remains unconfirmed")


def platform_rows(rows, icon_hash, xml_hash, pins, unchanged):
    if type(rows) is not list:
        raise ValueError("platforms must be a list")
    seen = set()
    for row in rows:
        if type(row) is not dict:
            raise ValueError("platform declaration must be an object")
        target = required(row, "target")
        if target not in TARGETS or target in seen:
            raise ValueError("unknown or duplicate platform target")
        seen.add(target)
        status = required(row, "status")
        if status not in ("passed", "failed", "not-tested"):
            raise ValueError("invalid platform status")
        required(row, "observation")
        if status == "passed":
            require_hash(icon_hash, "passed platform source")
            if pins is None:
                raise ValueError("passed platform result needs a bound Desktop manifest")
            if (row.get("sourceSha256") != icon_hash or row.get("xmlSha256") != xml_hash or
                row.get("converter") != pins["converter"] or row.get("compose") != pins["compose"]):
                raise ValueError("passed platform result is not bound to current SVG/XML/pins")
    if unchanged and seen != TARGETS:
        raise ValueError("unchanged must declare all platform targets, including unavailable targets")
    if unchanged and any(row["status"] == "failed" for row in rows):
        raise ValueError("failed platform check cannot confirm unchanged")


def desktop_observation(session, value, data, bank, checked, *, source_path=None, source_hash=None):
    from headless import RENDERER as OFFSCREEN, validate_export
    if source_path is None and bank["iconHash"] is None:
        raise ValueError("absent baseline cannot have Desktop observation")
    if type(value) is not dict or value.get("renderer") not in (RENDERER, OFFSCREEN):
        raise ValueError("Desktop observation requires Compose Desktop generated XML via painterResource")
    observed = instant(value.get("observedAt"), "desktop.observedAt")
    if observed > checked:
        raise ValueError("Desktop observation is after attempt")
    required(value, "visualComparison")
    manifest_path = artifact(session, value.get("manifest"), "desktop.manifest")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid generated manifest") from exc
    if type(manifest) is not dict or type(manifest.get("schemaVersion")) is not int or manifest["schemaVersion"] != 1:
        raise ValueError("invalid generated manifest schema")
    worktree = Path(data["worktree"])
    if (manifest.get("sourceRoot") != str(worktree) or
        manifest.get("banksRoot") != str(worktree / "banks")):
        raise ValueError("manifest is not generated from session worktree")
    pins = manifest.get("pins")
    if type(pins) is not dict:
        raise ValueError("manifest lacks converter/Compose pins")
    required(pins, "converter")
    required(pins, "compose")
    entries = manifest.get("entries")
    if type(entries) is not list or any(type(row) is not dict for row in entries):
        raise ValueError("invalid generated manifest entries")
    selected = [row for row in entries if type(row.get("id")) is int and row["id"] == bank["id"]]
    if len(selected) != 1:
        raise ValueError("manifest must contain one selected bank entry")
    entry = selected[0]
    if entry.get("error") is not None:
        raise ValueError("selected bank has a generated manifest error")
    expected_source = source_path if source_path is not None else f'{bank["folder"]}/icon.svg'
    expected_hash = source_hash if source_hash is not None else bank["iconHash"]
    if entry.get("sourcePath") != expected_source or entry.get("sourceSha256") != expected_hash:
        raise ValueError("manifest is not for exact selected SVG")
    if entry.get("sourceValidated") is not True:
        raise ValueError("manifest source SVG was not validated")
    resource = entry.get("resourceName")
    expected_resource = f'bank_{bank["id"]}' if bank['id'] >= 0 else f'bank_m{-bank["id"]}'
    if resource != expected_resource:
        raise ValueError("generated resource name differs from selected Company.id")
    xml = manifest_path.parent / "composeResources" / "drawable" / f"{resource}.xml"
    xml_path = artifact(session, str(xml.relative_to(session)), "generated XML")
    xml_hash = require_hash(entry.get("xmlSha256"), "manifest XML")
    if sha(xml_path.read_bytes()) != xml_hash:
        raise ValueError("generated XML hash differs from manifest")
    for background in ("light", "dark"):
        image = value.get(background)
        if type(image) is not dict:
            raise ValueError(f"missing {background} Desktop screenshot")
        path = artifact(session, image.get("path"), f"desktop.{background}")
        content = path.read_bytes()
        if not content.startswith(b"\x89PNG\r\n\x1a\n") or sha(content) != require_hash(image.get("sha256"), background):
            raise ValueError(f"{background} screenshot missing or hash mismatch")
    if value["renderer"] == OFFSCREEN:
        validate_export(session, value, data, bank, expected_source, expected_hash, xml_hash, pins)
    return xml_hash, pins


def validate_attempt(path: Path, data: dict, bank: dict, *, session_root: Path | None = None) -> dict:
    """Return JSON-ready evidence, or reject a malformed/stale attempt package."""
    if type(data) is not dict or type(bank) is not dict:
        raise ValueError("session and bank must be objects")
    session = Path(session_root) if session_root is not None else Path(data["worktree"]).parent
    if session.is_symlink() or not session.is_dir():
        raise ValueError("session path is not a directory")
    evidence_path = Path(path)
    try:
        relative = str(evidence_path.relative_to(session))
    except ValueError as exc:
        raise ValueError("attempt must live under session evidence") from exc
    artifact(session, relative, "attempt")
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    if type(payload) is not dict:
        raise ValueError("attempt must be an object")
    allowed = {"schemaVersion", "id", "baseCommit", "iconHash", "checkedAt", "outcome",
               "reason", "nextAction", "sources", "assessment", "desktop", "platforms",
               "candidate", "recheckRequired", "recheckRationale"}
    if payload.keys() - allowed:
        raise ValueError(f"unsupported attempt fields: {sorted(payload.keys() - allowed)}")
    if (type(payload.get("schemaVersion")) is not int or payload["schemaVersion"] != 1 or
        type(payload.get("id")) is not int or payload["id"] != bank["id"] or
        payload.get("baseCommit") != data["baseCommit"]):
        raise ValueError("attempt schema, bank ID or base commit differs from session")
    if "iconHash" not in payload or "iconHash" not in bank:
        raise ValueError("attempt baseline must be explicit")
    icon_hash = payload["iconHash"]
    source = Path(data["worktree"]) / bank["folder"] / "icon.svg"
    try:
        baseline_bytes(source, icon_hash)
        if icon_hash is None:
            baseline_blob(data["worktree"], data["baseCommit"], f"{bank['folder']}/icon.svg", icon_hash)
    except ValueError as exc:
        raise ValueError(f"attempt does not bind exact current SVG bytes: {exc}") from exc
    if icon_hash != bank["iconHash"]:
        raise ValueError("attempt does not bind exact current SVG bytes")
    checked = instant(payload.get("checkedAt"), "checkedAt")
    outcome = required(payload, "outcome")
    if outcome not in OUTCOMES:
        raise ValueError("invalid attempt outcome")
    required(payload, "reason")
    required(payload, "nextAction")
    assets = source_rows(payload.get("sources"), checked, outcome)
    assessment_of(payload.get("assessment"), outcome)
    recheck = payload.get("recheckRequired")
    if type(recheck) is not bool:
        raise ValueError("recheckRequired must be explicit")
    desktop = payload.get("desktop")
    if outcome == "unchanged":
        if icon_hash is None:
            raise ValueError("absent icon cannot be unchanged")
        if recheck:
            raise ValueError("recheck required before current SVG can be confirmed")
        required(payload, "recheckRationale")
        xml_hash, pins = desktop_observation(session, desktop, data, bank, checked)
    elif outcome == "candidate":
        if desktop is not None:
            raise ValueError("candidate rendering belongs to later review; desktop must be null")
        xml_hash, pins = None, None
    elif desktop is not None:
        xml_hash, pins = desktop_observation(session, desktop, data, bank, checked)
    else:
        xml_hash, pins = None, None
    platform_rows(payload.get("platforms"), icon_hash, xml_hash, pins, outcome == "unchanged")
    candidate = payload.get("candidate")
    if outcome == "candidate":
        if type(candidate) is not dict:
            raise ValueError("candidate needs fetched asset")
        file = artifact(session, candidate.get("path"), "candidate.path")
        if sha(file.read_bytes()) != require_hash(candidate.get("iconHash"), "candidate icon"):
            raise ValueError("candidate file differs from recorded hash")
        if candidate["iconHash"] == icon_hash:
            raise ValueError("candidate SVG is same as current SVG")
        web_url(required(candidate, "sourceUrl"), "candidate.sourceUrl")
        asset_url = web_url(required(candidate, "sourceAssetUrl"), "candidate.sourceAssetUrl")
        if asset_url not in assets or candidate["sourceUrl"] not in {row["url"] for row in payload["sources"]}:
            raise ValueError("candidate source and asset must be observed in sources")
        required(candidate, "rationale")
        if "sourceMaterial" in candidate and "retrieval" not in candidate:
            raise ValueError("source material requires its original retrieval binding")
        if "retrieval" in candidate:
            from source_evidence import validate_source
            validate_source(session, candidate, data, bank, checked)
    elif candidate is not None:
        raise ValueError("candidate is only valid for candidate outcome")
    return payload


def render_binding(data: dict, bank: dict, payload: dict, *, session_root: Path | None = None) -> dict | None:
    """Identity of the exact generated package consumed by Desktop observation."""
    desktop = payload.get("desktop")
    if desktop is None:
        return None
    session = Path(session_root) if session_root is not None else Path(data["worktree"]).parent
    xml_hash, pins = desktop_observation(session, desktop, data, bank,
                                         instant(payload["checkedAt"], "checkedAt"))
    return {"sourceSha256": payload["iconHash"], "xmlSha256": xml_hash,
            "converter": pins["converter"], "compose": pins["compose"]}



def validate_review(path: Path, data: dict, bank: dict, now: datetime) -> tuple[dict, dict, dict]:
    """Bind human judgment and completed assessment to canonical prior evidence."""
    if bank["iconHash"] is None:
        raise ValueError("absent icon cannot be equivalent to a current variant")
    session = Path(data["worktree"]).parent
    review_path = artifact(session, str(path.relative_to(session)), "review")
    try:
        review = json.loads(review_path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid review JSON") from exc
    if type(review) is not dict or review.keys() != {
        "schemaVersion", "id", "baseCommit", "iconHash", "decision", "reviewer",
        "statement", "reviewedAt", "priorReport", "priorEvidence", "evidence", "comparison",
    }:
        raise ValueError("incomplete or unsupported human review fields")
    if (type(review["schemaVersion"]) is not int or review["schemaVersion"] != 1 or
            type(review["id"]) is not int or review["id"] != bank["id"] or
            review["baseCommit"] != data["baseCommit"] or
            review["iconHash"] != bank["iconHash"] or
            review["decision"] != "equivalent-current-variant"):
        raise ValueError("review decision, bank, base or icon differs from session")
    required(review, "reviewer")
    required(review, "statement")
    reviewed_at = instant(review["reviewedAt"], "reviewedAt")
    if reviewed_at > now:
        raise ValueError("reviewedAt is after command time")

    def link(name, expected=None):
        item = review[name]
        if type(item) is not dict or item.keys() != {"path", "sha256"}:
            raise ValueError(f"{name} needs path and SHA-256")
        relative = required(item, "path")
        if expected is not None and relative != expected:
            raise ValueError(f"{name} must refer to canonical prior artifact")
        if name == "priorReport":
            linked = session / relative
            if linked.is_symlink() or not linked.is_file():
                raise ValueError("priorReport is not a regular file")
        else:
            linked = artifact(session, relative, name)
        if sha(linked.read_bytes()) != require_hash(item["sha256"], name):
            raise ValueError(f"{name} hash mismatch")
        return linked

    id = bank["id"]
    report_path = link("priorReport", f"reports/{id}.json")
    prior_path = link("priorEvidence", f"evidence/{id}/validated.json")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        prior = json.loads(prior_path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid prior report or evidence") from exc
    if (type(report) is not dict or report.get("id") != id or
            report.get("outcome") != "needs-human" or type(prior) is not dict or
            prior.get("outcome") != "needs-human" or
            report.get("evidence") != prior or
            report.get("evidencePath") != f"evidence/{id}/validated.json" or
            report.get("after") != bank["state"] or
            type(report.get("provenance")) is not dict or
            report["provenance"].get("baseCommit") != data["baseCommit"] or
            validate_attempt(prior_path, data, bank) != prior):
        raise ValueError("prior needs-human report, evidence or state does not match session")
    prior_at = instant(prior.get("checkedAt"), "prior.checkedAt")
    if reviewed_at < prior_at:
        raise ValueError("reviewedAt precedes prior assessment")
    if "renderBinding" in report:
        historical_binding = report["renderBinding"]
    else:
        passed = next((row for row in prior["platforms"] if row["status"] == "passed"), None)
        historical_binding = ({key: passed.get(key) for key in
                               ("sourceSha256", "xmlSha256", "converter", "compose")}
                              if passed is not None else None)
    if historical_binding is None or historical_binding != render_binding(data, bank, prior):
        raise ValueError("prior Desktop SVG/XML/converter/Compose identity is not pinned or changed")

    new_path = link("evidence")
    if new_path == prior_path or new_path == review_path:
        raise ValueError("review requires a new assessment file")
    original = report["provenance"].get("sourceEvidence")
    if type(original) is not str or not Path(original).is_absolute() or any(
            segment in (".", "..") for segment in original.split("/")):
        raise ValueError("prior source evidence path is invalid")
    try:
        original_relative = str(Path(original).relative_to(session))
    except ValueError as exc:
        raise ValueError("prior source evidence path is outside session") from exc
    original_path = artifact(session, original_relative, "prior source evidence")
    if new_path == original_path:
        raise ValueError("review requires a new attempt, not the original input")

    payload = validate_attempt(new_path, data, bank)
    checked_at = instant(payload["checkedAt"], "checkedAt")
    if (checked_at < reviewed_at or checked_at > now or payload["outcome"] != "unchanged" or
            payload["assessment"] == prior["assessment"] or
            any(payload.get(key) != prior.get(key) for key in
                ("sources", "desktop", "platforms", "recheckRequired", "recheckRationale"))):
        raise ValueError("reviewed unchanged assessment is stale or changes source/render package")
    comparison = link("comparison")
    if comparison in (prior_path, review_path, new_path):
        raise ValueError("comparison needs its own image")
    image = comparison.read_bytes()
    if not (image.startswith(b"\x89PNG\r\n\x1a\n") or image.startswith(b"\xff\xd8\xff") or
            (image.startswith(b"RIFF") and image[8:12] == b"WEBP")):
        raise ValueError("comparison must be an image")
    return review, payload, report
