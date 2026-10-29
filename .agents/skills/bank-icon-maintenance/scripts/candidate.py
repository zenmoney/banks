"""Bind a final candidate to the prior attempt and an immutable local review package.

This records preparation, not visual approval, an accepted icon, a commit or publication.
"""
import copy
import difflib
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess

from evidence import (TARGETS, artifact, baseline_bytes, baseline_blob, desktop_observation,
                      instant, require_hash, required, sha, validate_attempt, web_url)


def _link(session, value, label, *, report=False):
    if type(value) is not dict or value.keys() != {"path", "sha256"}:
        raise ValueError(f"{label} requires path and SHA-256")
    relative = required(value, "path")
    if report:
        if (not relative.startswith("reports/") or Path(relative).is_absolute() or
                any(part in ("", ".", "..") for part in relative.split("/"))):
            raise ValueError(f"{label} must be a local report")
        path = session
        for part in relative.split("/"):
            path = path / part
            if path.is_symlink():
                raise ValueError(f"{label} cannot contain a symlink")
        if not path.is_file():
            raise OSError(f"{label} missing: {path}")
    else:
        path = artifact(session, relative, label)
    if sha(path.read_bytes()) != require_hash(value["sha256"], label):
        raise ValueError(f"{label} hash mismatch")
    return path


def _json(path, label):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid {label} JSON") from exc
    if type(value) is not dict:
        raise ValueError(f"{label} must be an object")
    return value


def _current_base(data, bank, *, historical=False):
    repo = Path(data["repo"])
    ref = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify",
                          f'{data["baseRef"]}^{{commit}}'], capture_output=True, text=True)
    if ref.returncode:
        raise ValueError("base ref missing; check accepted icon and reassess proposal")
    current_commit = ref.stdout.strip()
    worktree = Path(data["worktree"])
    head = subprocess.run(["git", "-C", str(worktree), "rev-parse", "HEAD"],
                          capture_output=True, text=True)
    if head.returncode or head.stdout.strip() != data["baseCommit"]:
        raise ValueError("session worktree base commit changed")
    folder = worktree / bank["folder"]
    for path in (worktree / "banks", folder, folder / "icon.svg", folder / "info.json"):
        if path.is_symlink():
            raise ValueError("current catalog contains a symlink")
    icon = folder / "icon.svg"
    metadata = folder / "info.json"
    baseline_bytes(icon, bank["iconHash"])
    baseline_blob(repo, data["baseCommit"], f'{bank["folder"]}/icon.svg', bank["iconHash"])
    if (sha(metadata.read_bytes()) != bank["metadataHash"] or
            _json(metadata, "bank metadata") != bank["metadata"]):
        raise ValueError("current SVG or bank metadata changed; reassess proposal")
    # Share session selection's catalog identity rules, including dirty duplicate IDs.
    from session import catalog
    entries, errors = catalog(worktree)
    selected = entries.get(bank["id"], [])
    if (errors.get(bank["id"]) or len(selected) != 1 or selected[0]["problem"] or
            any(selected[0].get(key) != bank.get(key) for key in
                ("folder", "metadata", "metadataHash", "iconHash", "title"))):
        raise ValueError("catalog entry or current SVG changed; reassess proposal")
    if current_commit != data["baseCommit"] and not historical:
        def accepted_file(relative):
            result = subprocess.run(["git", "-C", str(repo), "show",
                                     f"{current_commit}:{relative}"], capture_output=True)
            mode = subprocess.run(["git", "-C", str(repo), "ls-tree", current_commit,
                                   "--", relative], capture_output=True, text=True)
            if (result.returncode or mode.returncode or
                    not mode.stdout.startswith(("100644 blob ", "100755 blob "))):
                raise ValueError("base ref moved; accepted bank file missing or replaced")
            return result.stdout

        baseline_blob(repo, current_commit, f'{bank["folder"]}/icon.svg', bank["iconHash"])
        if sha(accepted_file(f'{bank["folder"]}/info.json')) != bank["metadataHash"]:
            raise ValueError("base ref moved; accepted bank SVG or metadata changed; reassess proposal")
        changed = subprocess.run(["git", "-C", str(repo), "diff", "--name-only",
                                  data["baseCommit"], current_commit, "--", "banks/"],
                                 capture_output=True, text=True)
        if changed.returncode:
            raise ValueError("cannot inspect moved base ref catalog")
        for name in changed.stdout.splitlines():
            if name == f'{bank["folder"]}/info.json' or not name.startswith("banks/") or not name.endswith("/info.json"):
                continue
            entry = subprocess.run(["git", "-C", str(repo), "show", f"{current_commit}:{name}"],
                                   capture_output=True)
            if entry.returncode:
                continue
            try:
                value = json.loads(entry.stdout)
            except (UnicodeError, json.JSONDecodeError):
                continue
            if type(value) is dict and type(value.get("id")) is int and value["id"] == bank["id"]:
                raise ValueError("base ref moved; duplicate Company.id needs reassessment")
    return icon


def _canonical_diff(base, final, id):
    try:
        old = [] if base is None else base.decode("utf-8").splitlines()
        new = final.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise ValueError("candidate and current SVG must be UTF-8 for exact diff") from exc
    return "\n".join(difflib.unified_diff(old, new, fromfile="/dev/null" if base is None else f"base/{id}/icon.svg",
                                          tofile=f"candidate/{id}/icon.svg", lineterm="")) + "\n"


def _validate_package(source, data, bank, *, historical=False):
    session = Path(data["worktree"]).parent
    source = artifact(session, str(Path(source).relative_to(session)), "candidate package")
    payload = _json(source, "candidate package")
    if payload.keys() - {"sourceComparison", "reviewerFeedback", "taskResponse"} != {"schemaVersion", "id", "baseCommit", "baseIconHash", "metadataHash",
                          "checkedAt", "priorReport", "priorEvidence", "candidate", "desktop",
                          "comparison", "platforms", "mrDraft", "diff"}:
        raise ValueError("incomplete or unsupported candidate package fields")
    if (type(payload["schemaVersion"]) is not int or payload["schemaVersion"] != 1 or
            type(payload["id"]) is not int or payload["id"] != bank["id"] or
            payload["baseCommit"] != data["baseCommit"] or
            payload["baseIconHash"] != bank["iconHash"] or
            payload["metadataHash"] != bank["metadataHash"]):
        raise ValueError("candidate bank, base commit, SVG or metadata binding differs")
    now = datetime.now(timezone.utc)
    checked = instant(payload["checkedAt"], "candidate.checkedAt")
    if checked > now:
        raise ValueError("candidate checkedAt is after command time")
    base = _current_base(data, bank, historical=historical)
    id = bank["id"]
    if (bank.get("report") != f"reports/{id}.json" or
            bank.get("evidence") != f"evidence/{id}/validated.json"):
        raise ValueError("candidate requires original recorded attempt")
    if (type(payload["priorReport"]) is not dict or
            type(payload["priorEvidence"]) is not dict or
            payload["priorReport"].get("path") != bank["report"] or
            payload["priorEvidence"].get("path") != bank["evidence"]):
        raise ValueError("candidate must link canonical prior report and evidence")
    report = _json(_link(session, payload["priorReport"], "priorReport", report=True), "prior report")
    prior_path = _link(session, payload["priorEvidence"], "priorEvidence")
    prior = _json(prior_path, "prior evidence")
    provenance = report.get("provenance")
    if (report.get("outcome") != "candidate" or report.get("id") != id or
            report.get("after") != bank["state"] or report.get("evidence") != prior or
            report.get("evidencePath") != bank["evidence"] or
            type(provenance) is not dict or provenance.get("baseCommit") != data["baseCommit"] or
            validate_attempt(prior_path, data, bank) != prior):
        raise ValueError("prior candidate attempt, report or local state changed")
    if (type(bank["state"].get("proposals")) is not list or
            not bank["state"]["proposals"]):
        raise ValueError("candidate requires a pending proposal")
    proposal = bank["state"]["proposals"][-1]
    raw = prior.get("candidate")
    if (type(proposal) is not dict or proposal.get("status") != "pending" or
            "reviewPackage" in proposal or proposal.get("candidate") != raw or
            proposal.get("candidateIconHash") != raw["iconHash"] or
            proposal.get("baseIconHash") != bank["iconHash"] or
            any(proposal.get(key) != prior[key] for key in
                ("checkedAt", "sources", "assessment", "reason"))):
        raise ValueError("pending source candidate differs from original attempt")
    if checked < instant(prior["checkedAt"], "prior.checkedAt"):
        raise ValueError("candidate checkedAt precedes search")
    candidate = payload["candidate"]
    if type(candidate) is not dict or candidate.keys() != {
            "path", "sha256", "sourceUrl", "sourceAssetUrl", "rationale", "role", "variant",
            "transforms", "detailsJudgement", "proportionsJudgement", "faithful",
            "visibleDiff", "visibleDiffExplanation"}:
        raise ValueError("candidate requires complete source, transform and visual judgement")
    final_path = _link(session, {key: candidate[key] for key in ("path", "sha256")}, "final SVG")
    final_hash = candidate["sha256"]
    if final_path.suffix.lower() != ".svg" or final_hash == bank["iconHash"]:
        raise ValueError("final SVG must visibly replace the exact current SVG")
    for key in ("sourceUrl", "sourceAssetUrl"):
        web_url(required(candidate, key), f"candidate.{key}")
        if candidate[key] != raw[key]:
            raise ValueError(f"candidate.{key} differs from source candidate")
    for key in ("rationale", "role", "variant", "detailsJudgement",
                "proportionsJudgement", "visibleDiffExplanation"):
        if not required(candidate, key).strip():
            raise ValueError(f"candidate.{key} needs an actual judgement")
    transforms = candidate["transforms"]
    if (type(transforms) is not list or not transforms or
            any(type(item) is not str or not item.strip() for item in transforms)):
        raise ValueError("candidate needs described source-to-final transformations")
    if candidate["faithful"] is not True or candidate["visibleDiff"] is not True:
        raise ValueError("lost source detail or no visible replacement: preserve current icon")
    desktop = payload["desktop"]
    if type(desktop) is not dict:
        raise ValueError("candidate Desktop observation must be an object")
    manifest_hash = require_hash(desktop.get("manifestSha256"), "desktop manifest")
    _link(session, {"path": desktop.get("manifest"), "sha256": manifest_hash},
          "desktop manifest")
    xml_hash, pins = desktop_observation(session, desktop, data, bank, checked,
                                         source_path=str(final_path), source_hash=final_hash)
    if instant(desktop["observedAt"], "desktop.observedAt") < instant(prior["checkedAt"], "prior.checkedAt"):
        raise ValueError("candidate Desktop observation predates prior search")
    # Compare only after validating the generated XML against the exact final SVG.
    # SVG serialization can change the byte hash while leaving the rendered candidate intact.
    for rejected in bank["state"].get("proposals", [])[:-1]:
        if rejected.get("status") != "rejected":
            continue
        if bank.get("taskContext"):
            # Task-scoped reconsideration authorizes investigation, not reuse of
            # old verification; the new candidate must resolve exact remarks.
            from task_work import validate_response
            validate_response(session, data, bank, payload)
            if final_hash == rejected.get("candidateIconHash"):
                raise ValueError("task cannot silently re-propose rejected final bytes")
            continue
        reason, release = rejected.get("rejectionReason"), rejected.get("reconsideration")
        same_final = (final_hash == rejected.get("candidateIconHash") or
                      xml_hash == (rejected.get("verification") or {}).get("xmlSha256"))
        if type(reason) is dict and reason.get("category") == "technical":
            if (type(release) is not dict or release.get("category") != "technical" or
                    release.get("status") != "search-eligible"):
                raise ValueError("technical retry requires a reason-linked reconsideration release")
            if same_final:
                raise ValueError("technical rejection requires a corrected and verified final candidate, not the old rendered candidate")
            if instant(prior["checkedAt"], "prior.checkedAt") <= instant(
                    release.get("reviewedAt"), "reconsideration.reviewedAt"):
                raise ValueError("technical retry requires a new search and final verification after reconsideration")
            continue
        if not same_final:
            continue
        if type(reason) is not dict or type(release) is not dict or release.get("status") != "search-eligible":
            raise ValueError("rejected final candidate cannot be proposed without relevant new evidence")
        if reason.get("category") == "identity/logo" and not set(release.get("sourceUrls", [])).intersection(
                row["url"] for row in prior["sources"]):
            raise ValueError("rejected identity/logo candidate lacks newly relevant search evidence")
    comparison = payload["comparison"]
    if type(comparison) is not dict or comparison.keys() != {"light", "dark"}:
        raise ValueError("comparison must cover light and dark backgrounds")
    absent = bank["iconHash"] is None
    if absent and ("baseObservation" not in desktop or desktop["baseObservation"] is not None):
        raise ValueError("first addition requires null baseObservation; no baseline exists")
    for background in ("light", "dark"):
        row = comparison[background]
        if type(row) is not dict or row.keys() != {"source", "base", "candidate", "beforeAfter"}:
            raise ValueError(f"{background} comparison needs source/base/candidate/before-after")
        for role, link in row.items():
            if absent and role in {"base", "beforeAfter"}:
                if link is not None:
                    raise ValueError("first addition cannot contain fabricated baseline comparison evidence")
                continue
            image = _link(session, link, f"comparison.{background}.{role}").read_bytes()
            if not image.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError(f"comparison.{background}.{role} must be PNG")
        if not absent and row["base"]["sha256"] == row["candidate"]["sha256"]:
            raise ValueError("no visible difference in base/candidate comparison")
    from headless import RENDERER as OFFSCREEN, validate_comparison
    if desktop["renderer"] == OFFSCREEN:
        validate_comparison(session, desktop, comparison, data, bank, checked)
    elif absent and any(comparison[theme]["candidate"] != desktop[theme] for theme in ("light", "dark")):
        raise ValueError("first addition comparison differs from exact final export images")
    if "retrieval" in raw or "sourceComparison" in payload:
        from source_evidence import validate_comparison as validate_source_comparison
        if "retrieval" not in raw or "sourceComparison" not in payload:
            raise ValueError("retrieval-bound candidates require the original-source/final comparison")
        view = validate_source_comparison(session, payload["sourceComparison"], raw, payload, data, bank, checked)
        if not historical:
            from source_evidence import linked, read_json, require_vector_source
            reference = read_json(linked(session, view["sourceReference"], "source reference"))
            require_vector_source(linked(session, reference["source"], "replacement source").read_bytes())
    elif not historical:
        from source_evidence import require_vector_source
        require_vector_source(artifact(session, raw["path"], "replacement source").read_bytes())
    rows = payload["platforms"]
    if type(rows) is not list or len(rows) != len(TARGETS):
        raise ValueError("candidate requires all platform targets")
    seen = set()
    for row in rows:
        if type(row) is not dict or row.get("target") not in TARGETS or row["target"] in seen:
            raise ValueError("unknown or duplicate platform target")
        seen.add(row["target"])
        if type(row.get("available")) is not bool or not required(row, "observation").strip():
            raise ValueError("platform requires availability and observation")
        status = row.get("status")
        if status == "failed" or (row["available"] and status != "passed"):
            raise ValueError(f"{row['target']} renderer failed or available target not tested")
        if status == "passed":
            if not row["available"] or any(row.get(key) != value for key, value in {
                    "sourceSha256": final_hash, "xmlSha256": xml_hash,
                    "converter": pins["converter"], "compose": pins["compose"]}.items()):
                raise ValueError(f"{row['target']} result not bound to final SVG/XML/pins")
            build = _link(session, row.get("buildEvidence"), f"{row['target']}.buildEvidence")
            if not build.read_bytes().strip():
                raise ValueError(f"{row['target']} buildEvidence is empty")
            image = _link(session, row.get("uiEvidence"), f"{row['target']}.uiEvidence")
            if not image.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError(f"{row['target']} uiEvidence must be a screenshot PNG")
        elif status != "not-tested" or row["available"]:
            raise ValueError(f"{row['target']} must disclose unavailable not-tested target")
    if seen != TARGETS:
        raise ValueError("missing candidate platform target")
    draft = _link(session, payload["mrDraft"], "MR draft").read_bytes()
    try:
        text = draft.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError("MR draft must be human-readable UTF-8") from exc
    if not text.strip() or any(token not in text for token in
                               (str(id), bank["title"], bank["iconHash"] if not absent else
                                "No existing icon.svg at frozen base commit", final_hash,
                                candidate["sourceUrl"], candidate["sourceAssetUrl"])):
        raise ValueError("MR draft needs bank, precise versions and source links")
    expected_diff = _canonical_diff(baseline_bytes(base, bank["iconHash"]), final_path.read_bytes(), id)
    if _link(session, payload["diff"], "base-to-final diff").read_bytes() != expected_diff.encode("utf-8"):
        raise ValueError("base-to-final diff does not match exact SVG bytes")
    from task_work import validate_response
    validate_response(session, data, bank, payload)
    import feedback_evidence
    feedback_evidence.validate(session, data, bank, payload)
    verification = {"checkedAt": payload["checkedAt"], "sourceSha256": final_hash,
                    "xmlSha256": xml_hash, "converter": pins["converter"], "compose": pins["compose"]}
    return payload, final_path, verification


def _report(session, data, bank, payload, package_link, candidate_link, verification):
    before = bank["state"]
    after = copy.deepcopy(before)
    proposal = after["proposals"][-1]
    raw = proposal["candidate"]
    proposal["sourceCandidate"] = raw
    proposal["candidate"] = {**payload["candidate"], **candidate_link,
                             "iconHash": candidate_link["sha256"]}
    proposal["candidateIconHash"] = candidate_link["sha256"]
    proposal["reviewPackage"] = package_link
    proposal["verification"] = verification
    proposal["humanReview"] = "pending"
    from feedback_evidence import record as record_feedback
    record_feedback(proposal, payload)
    from task_work import record_response
    record_response(proposal, payload)
    id = bank["id"]
    return {"schemaVersion": 1, "id": id, "outcome": "candidate", "before": before,
            "after": after, "evidence": payload, "evidencePath": package_link["path"],
            "statePath": f"banks/state/{id}.json", "priorReport": payload["priorReport"],
            "priorEvidence": payload["priorEvidence"], "reviewPackage": package_link,
            "candidate": candidate_link, "renderBinding": verification,
            "provenance": {"baseRef": data["baseRef"], "baseCommit": data["baseCommit"],
                           "metadataHash": bank["metadataHash"],
                           "sourceEvidence": payload["priorEvidence"]["path"]},
            "humanReview": "pending", "nextAction": "Explicit human candidate visual review; no asset or state publication"}


def _output_path(session, relative):
    path = session
    for part in relative.split("/")[:-1]:
        path = path / part
        if path.is_symlink():
            raise ValueError(f"candidate output parent is a symlink: {path}")
    path = session / relative
    if path.exists() or path.is_symlink():
        raise ValueError(f"candidate output already exists: {path}")
    return path


def record_candidate(session, data, bank, source):
    """Validate fully, write immutable evidence and replace local state atomically.

    Return rollback for the caller's session.json save failure. Accepted catalog stays unchanged.
    """
    session = Path(session)
    payload, final_path, verification = _validate_package(source, data, bank)
    id = bank["id"]
    package_bytes = Path(source).read_bytes()
    package_hash = sha(package_bytes)
    package_link = {"path": f"evidence/{id}/candidate-{package_hash}.json", "sha256": package_hash}
    final_hash = payload["candidate"]["sha256"]
    candidate_link = {"path": f"evidence/{id}/candidate-{final_hash}.svg", "sha256": final_hash}
    report = _report(session, data, bank, payload, package_link, candidate_link, verification)
    report_rel = f"reports/{id}-candidate-{package_hash}.json"
    state_path = session / f"banks/state/{id}.json"
    for part in (session / "banks", session / "banks/state", state_path):
        if part.is_symlink():
            raise ValueError("local state output cannot contain a symlink")
    if not state_path.is_file():
        raise OSError("canonical prior local state is missing")
    old_state = state_path.read_bytes()
    if _json(state_path, "local state") != bank["state"]:
        raise ValueError("local state differs from prior report")
    outputs = [(package_link["path"], package_bytes),
               (candidate_link["path"], final_path.read_bytes()),
               (report_rel, (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))]
    for relative, _ in outputs:
        _output_path(session, relative)
    temporary = state_path.with_suffix(".json.tmp")
    _output_path(session, str(temporary.relative_to(session)))
    written = []
    previous_bank = copy.deepcopy(bank)

    def rollback():
        if temporary.exists() and not temporary.is_symlink():
            temporary.unlink()
        if state_path.read_bytes() != old_state:
            with temporary.open("xb") as stream:
                stream.write(old_state)
            temporary.replace(state_path)
        for path in written:
            path.unlink(missing_ok=True)
        bank.clear()
        bank.update(previous_bank)

    try:
        for relative, content in outputs:
            path = session / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                written.append(path)
                stream.write(content)
        with temporary.open("xb") as stream:
            stream.write((json.dumps(report["after"], ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        temporary.replace(state_path)
    except (OSError, ValueError):
        rollback()
        raise
    bank.update(state=report["after"], point="waiting-proposal", reason=report["nextAction"],
                report=report_rel, reportSha256=sha(outputs[-1][1]), evidence=package_link["path"])
    return rollback


def validate_prepared_proposal(session, data, bank):
    """Read-only full chain and current-base revalidation; return canonical candidate report."""
    return _validated_prepared_proposal(session, data, bank, current_state=True)


def validate_prepared_report(session, data, bank, link, *, historical=False):
    """Reconstruct an immutable prepared report underneath later linked review reports."""
    session = Path(session)
    path = _link(session, link, "prepared candidate report", report=True)
    report = _json(path, "prepared candidate report")
    prepared = {**bank, "state": report.get("after"), "report": link["path"],
                "reportSha256": link["sha256"],
                "evidence": (report.get("reviewPackage") or {}).get("path")}
    return _validated_prepared_proposal(session, data, prepared, current_state=False,
                                        historical=historical)


def _validated_prepared_proposal(session, data, bank, *, current_state, historical=False):
    session = Path(session)
    proposals = (bank.get("state") or {}).get("proposals")
    if type(proposals) is not list or not proposals or type(proposals[-1]) is not dict:
        raise ValueError("no prepared proposal")
    proposal = proposals[-1]
    package_link = proposal.get("reviewPackage")
    package_path = _link(session, package_link, "reviewPackage")
    payload, final, verification = _validate_prepared_input(package_path, data, bank,
                                                           historical=historical)
    package_hash = package_link["sha256"]
    if package_link["path"] != f'evidence/{bank["id"]}/candidate-{package_hash}.json':
        raise ValueError("review package path is not content-addressed")
    final_hash = payload["candidate"]["sha256"]
    candidate_link = {"path": f'evidence/{bank["id"]}/candidate-{final_hash}.svg', "sha256": final_hash}
    _link(session, candidate_link, "saved final SVG")
    if final.read_bytes() != (session / candidate_link["path"]).read_bytes():
        raise ValueError("saved final SVG differs from reviewed input")
    report_rel = f'reports/{bank["id"]}-candidate-{package_hash}.json'
    if bank.get("report") != report_rel or bank.get("evidence") != package_link["path"]:
        raise ValueError("prepared report or evidence link changed")
    path = _link(session, {"path": report_rel, "sha256": bank.get("reportSha256")},
                 "candidate report", report=True)
    report = _json(path, "candidate report")
    prior_state = report.get("before")
    if type(prior_state) is not dict:
        raise ValueError("candidate report missing prior state")
    prior_bank = {**bank, "state": prior_state,
                  "report": f'reports/{bank["id"]}.json',
                  "evidence": f'evidence/{bank["id"]}/validated.json'}
    expected = _report(session, data, prior_bank, payload, package_link, candidate_link, verification)
    if report != expected or bank["state"] != expected["after"]:
        raise ValueError("saved candidate report or state differs from validated package")
    if current_state:
        state_path = session / f"banks/state/{bank['id']}.json"
        if any(path.is_symlink() for path in (session / "banks", session / "banks/state", state_path)):
            raise ValueError("local state cannot contain a symlink")
        state_bytes = (json.dumps(bank["state"], ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if state_path.read_bytes() != state_bytes:
            raise ValueError("saved local state bytes differ from prepared proposal")
    return report


def _validate_prepared_input(package_path, data, bank, *, historical=False):
    """Revalidate original chain using its before-state, not the extended proposal."""
    session = Path(data["worktree"]).parent
    payload = _json(package_path, "review package")
    prior_path = _link(session, payload.get("priorReport"), "priorReport", report=True)
    prior = _json(prior_path, "prior report")
    prior_bank = {**bank, "state": prior.get("after"),
                  "report": f'reports/{bank["id"]}.json',
                  "evidence": f'evidence/{bank["id"]}/validated.json'}
    return _validate_package(package_path, data, prior_bank, historical=historical)
