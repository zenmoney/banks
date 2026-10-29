"""Append-only candidate approval and confirmed MR facts for one local session bank.

Approval is a human-supplied exact decision; a quoted file is provenance, never
an authority to make that decision. MR observations must be supplied by a caller
that has independently confirmed the provider and exact Git commits.
"""
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

from candidate import _link, validate_prepared_proposal, validate_prepared_report
from evidence import artifact, instant, required, require_hash, sha, web_url
from session import save
from state_results import (_accepted_bank_pin, _catalog_pin, _git, _initial_result,
                           _prior_state, _session_data, _state_fields)
from session import catalog
_STOP_ACTION = "MR closed or merged was observed; human resolution required before acceptance, rejection, or freshness update"

_APPROVED_ACTION = "Candidate approved; prepare exact local asset publication separately"
_OPEN_ACTION = "Confirmed MR open; wait for independently observed outcome and human decision"
_ASSESSED_ACTION = "Agent assessed exact candidate; publication requires separately authorized run"


def _json(path, label):
    try:
        value = json.loads(path.read_bytes())
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f"invalid {label} JSON") from exc
    if type(value) is not dict:
        raise ValueError(f"{label} must be an object")
    return value


def _report_link(session, bank):
    return {"path": bank["report"], "sha256": require_hash(bank.get("reportSha256"), "report")}


def _approval_report(id, prepared, approval, link):
    after = copy.deepcopy(prepared["after"])
    agent = approval["scope"] == "candidate-assessment"
    if agent:
        after["proposals"][-1].update(agentVisualAssessment="suitable", agentAssessment=link)
    else:
        after["proposals"][-1].update(humanReview="approved", approval=link)
    return {"schemaVersion": 1, "id": id, "outcome": "candidate",
            "before": prepared["after"], "after": after,
            "priorReport": {"path": f"reports/{id}-candidate-{prepared['reviewPackage']['sha256']}.json",
                            "sha256": sha(_encode(prepared))},
            "approval": link, "reviewPackage": prepared["reviewPackage"],
            "humanReview": "pending" if agent else "approved", "statePath": f"banks/state/{id}.json",
            "nextAction": _ASSESSED_ACTION if agent else _APPROVED_ACTION}


def _mr_report(id, previous, prior_link, package_link, observation_link, observation):
    after = copy.deepcopy(previous["after"])
    after["proposals"][-1].update(status="open" if observation["state"] == "open" else "pending",
                                   assetPublication=package_link, mr=observation)
    from feedback_revision import published
    published(after, observation, after["proposals"][-1]["candidateIconHash"])
    return {"schemaVersion": 1, "id": id, "outcome": "candidate",
            "before": previous["after"], "after": after,
            "priorReport": prior_link,
            "assetPublication": package_link, "observation": observation_link,
            "humanReview": previous["humanReview"], "statePath": f"banks/state/{id}.json",
            "nextAction": _OPEN_ACTION if observation["state"] == "open" else _STOP_ACTION}


def _encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _saved_link(session, value, label, *, report=False):
    path = _link(session, value, label, report=report)
    return _json(path, label)


def _approval(session, data, bank, prepared, value):
    agent = value.get("scope") == "candidate-assessment"
    actor, timestamp = ("assessor", "assessedAt") if agent else ("reviewer", "reviewedAt")
    expected = {"schemaVersion", "scope", "decision", "id", "baseCommit",
                "baseIconHash", "candidateIconHash", "reviewPackage", actor,
                "statement", timestamp, "provenance"}
    proposal = prepared["after"]["proposals"][-1]
    if (value.keys() != expected or type(value["schemaVersion"]) is not int or
            value["schemaVersion"] != 1 or
            value["scope"] != ("candidate-assessment" if agent else "candidate-review") or
            value["decision"] != ("suitable" if agent else "approve") or type(value["id"]) is not int or
            value["id"] != bank["id"] or value["baseCommit"] != data["baseCommit"] or
            value["baseIconHash"] != bank["iconHash"] or
            value["candidateIconHash"] != proposal["candidateIconHash"] or
            value["reviewPackage"] != prepared["reviewPackage"]):
        raise ValueError("assessment does not bind the exact pending candidate and review package")
    required(value, actor)
    required(value, "statement")
    reviewed = instant(value[timestamp], f"assessment.{timestamp}")
    if reviewed < instant(prepared["renderBinding"]["checkedAt"], "candidate.checkedAt") or reviewed > datetime.now(timezone.utc):
        raise ValueError("assessment time is outside candidate-to-command interval")
    quote = _link(session, value["provenance"], "assessment provenance")
    if not quote.read_bytes().strip():
        raise ValueError("assessment provenance is empty")

def _package(session, data, bank, prepared, approved, link):
    if type(link) is not dict or link.keys() != {"path", "sha256"}:
        raise ValueError("asset publication package requires an exact path and digest")
    digest = require_hash(link["sha256"], "asset publication package")
    if link["path"] != f"asset-publication/{digest}.json":
        raise ValueError("asset publication package must be content-addressed")
    path = session / link["path"]
    if any(part.is_symlink() for part in (path, *path.parents)) or not path.is_file():
        raise ValueError("asset publication package must be a regular non-symlink file")
    if sha(path.read_bytes()) != digest:
        raise ValueError("asset publication package hash mismatch")
    package = _json(path, "asset publication package")
    proposal = prepared["after"]["proposals"][-1]
    if (package.get("schemaVersion") not in (1, 2) or package.get("scope") != "asset-publication" or
            type(package.get("id")) is not int or package["id"] != bank["id"] or
            "baseIconHash" not in package or package["baseIconHash"] != bank["iconHash"] or
            package.get("candidateIconHash") != proposal["candidateIconHash"] or
            package.get("reviewPackage") != prepared["reviewPackage"] or
            package.get("approval") != approved["approval"] or
            package.get("folder") != bank["folder"] or
            type(package.get("assetCommit")) is not str or not package["assetCommit"]):
        raise ValueError("asset publication package differs from exact approved proposal")
    bindings = package.get("bindings")
    if (type(bindings) is not dict or
            bindings.get(approved["approval"]["path"]) != approved["approval"]["sha256"] or
            sha(_link(session, approved["approval"], "package approved candidate").read_bytes()) != approved["approval"]["sha256"]):
        raise ValueError("asset package does not bind approved review evidence")
    if package["schemaVersion"] == 2:
        import admission
        admission.validate(session, prepared, package.get("admission"))
        expected = admission.bindings(session, prepared, package["admission"])
        if any(bindings.get(path) != digest for path, digest in expected.items()):
            raise ValueError("publication lost admission bindings")
    from feedback_publication import check_package
    check_package(session, data, bank, package)
    return package


def _observation(value, package, approved_at):
    keys = {"number", "url", "state", "head", "headCommit", "base", "baseCommit",
            "target", "title", "bodySha256", "observedAt"}
    if type(value) is not dict or value.keys() != keys or type(value["number"]) is not int or value["number"] <= 0:
        raise ValueError("MR observation must contain exact provider facts")
    if package.get("revision") and any(value.get(k) != package["revision"]["mr"][k] for k in ("number", "url")):
        raise ValueError("revision observation belongs to another MR")
    web_url(value["url"], "MR URL")
    if value["state"] not in ("open", "closed", "merged"):
        raise ValueError("unsupported provider MR state")
    for field in ("head", "headCommit", "base", "baseCommit", "target"):
        required(value, field)
    required(value, "title")
    if (value["title"] != package.get("title") or
            require_hash(value["bodySha256"], "MR body") != sha(required(package, "body").encode("utf-8"))):
        raise ValueError("provider PR title or body differs from self-contained reviewed package")
    if any(value[field] != package[bound] for field, bound in
           (("head", "head"), ("headCommit", "assetCommit"), ("base", "base"),
            ("target", "target"))):
        raise ValueError("MR observation does not bind the exact asset commit and target")
    commit = value["baseCommit"]
    if type(commit) is not str or len(commit) not in (40, 64) or any(c not in "0123456789abcdef" for c in commit):
        raise ValueError("MR observed base commit must be a Git SHA")
    observed = instant(value["observedAt"], "MR.observedAt")
    if observed < instant(approved_at, "approval.reviewedAt") or observed > datetime.now(timezone.utc):
        raise ValueError("MR observation must follow approval receipt and precede command time")


def _snapshot(session, id, *, resume=False, historical=False, historical_report=None):
    session = Path(session).resolve()
    bindings = {}
    data = _session_data(session, [id], bindings, allow_paused=resume)
    if resume and data["paused"]:
        data["paused"] = False
        for item in data["banks"]:
            item["point"] = item.pop("priorPoint")
    bank = next(item for item in data["banks"] if item["id"] == id)
    entries, errors = catalog(Path(data["worktree"]))
    _catalog_pin(data, bank, entries, errors, bindings)
    if not historical:
        _accepted_bank_pin(data, bank, _git(data["repo"], "rev-parse", "--verify",
                                                  f"{data['baseRef']}^{{commit}}"))
    if historical_report is not None:
        report = _saved_link(session, historical_report, "historical MR report", report=True)
        bank.update(state=report["after"], report=historical_report["path"],
                    reportSha256=historical_report["sha256"], reason=report["nextAction"],
                    point=("waiting-proposal" if not report["after"]["proposals"][-1].get("mr") else
                           "waiting-mr" if report["after"]["proposals"][-1]["mr"]["state"] == "open" else "needs-human"))
    before = _prior_state(data, id)
    initial, payload, after = _initial_result(session, data, bank, before, bindings)
    if payload["outcome"] != "candidate":
        raise ValueError("selected bank has no candidate attempt")
    return session, data, bank, after


def load_candidate(session: Path, id: int, *, _resume=False, historical=False,
                   historical_report=None, _repair_point=False):
    """Validate full original attempt, prepared report, approval, and MR chain."""
    session, data, bank, initial = _snapshot(session, id, resume=_resume,
                                            historical=historical, historical_report=historical_report)
    if bank.get("point") not in ("waiting-proposal", "waiting-mr", "needs-human"):
        raise ValueError("candidate is not awaiting review or MR outcome")
    current_link = _report_link(session, bank)
    current = _saved_link(session, current_link, "current candidate report", report=True)
    if _repair_point:
        # Only the known continuation bookkeeping defect, never a human decision
        # or a general integrity override. Everything below still validates.
        if (_resume or historical or historical_report is not None or
                bank["point"] != "needs-human" or bank.get("reason") !=
                "prepared proposal needs revalidation: candidate changed during continuation"):
            raise ValueError("not the known continuation-snapshot stop")
        mr = current.get("after", {}).get("proposals", [{}])[-1].get("mr")
        if mr is not None and mr.get("state") != "open":
            raise ValueError("terminal MR decisions cannot be repaired by revalidation")
        bank.update(point="waiting-mr" if mr else "waiting-proposal",
                    reason=current["nextAction"])
    if current_link["path"].startswith(f"reports/{id}-candidate-"):
        prepared = (validate_prepared_report(session, data, bank, current_link, historical=True)
                    if historical else validate_prepared_proposal(session, data, bank))
        approval = None
        expected_state = prepared["after"]
        expected_point = "waiting-proposal"
    else:
        stages = []
        link, report = current_link, current
        while link["path"].startswith(f"reports/{id}-mr-"):
            stages.append((link, report))
            if len(stages) > 2:
                raise ValueError("MR report chain has unsupported extra transitions")
            link = report.get("priorReport")
            report = _saved_link(session, link, "prior MR report", report=True)
        approved_link, approved = link, report
        prepared_link = approved.get("priorReport")
        prepared = validate_prepared_report(session, data, bank, prepared_link, historical=historical)
        approval_link = approved.get("approval")
        approval = _saved_link(session, approval_link, "saved candidate approval")
        _approval(session, data, bank, prepared, approval)
        expected = _approval_report(id, prepared, approval, approval_link)
        if (approved_link["path"] != f"reports/{id}-{'assessed' if approval['scope'] == 'candidate-assessment' else 'approved'}-{approval_link['sha256']}.json" or
                approved != expected or approved_link["sha256"] != sha(_encode(approved))):
            raise ValueError("approval report does not extend exact prepared proposal")
        expected_state = approved["after"]
        expected_point = "waiting-proposal"
        prior_link, prior_report = approved_link, approved
        prior_observation = None
        for stage_link, stage in reversed(stages):
            package_link = stage.get("assetPublication")
            package = _package(session, data, bank, prepared, approved, package_link)
            observation_link = stage.get("observation")
            if (type(observation_link) is not dict or
                    observation_link.get("path") != f"evidence/{id}/mr-observation-{observation_link.get('sha256')}.json"):
                raise ValueError("MR observation path is not content-addressed")
            observation = _saved_link(session, observation_link, "saved MR observation")
            _observation(observation, package, approval["assessedAt" if approval["scope"] == "candidate-assessment" else "reviewedAt"])
            if prior_observation is not None:
                same_mr = ("number", "url", "head", "headCommit", "base", "target")
                if (prior_observation["state"] != "open" or observation["state"] == "open" or
                        any(prior_observation[key] != observation[key] for key in same_mr)):
                    raise ValueError("MR outcome is not the same previously observed open MR")
            expected_stage = _mr_report(id, prior_report, prior_link, package_link, observation_link, observation)
            name = (f"reports/{id}-mr-{observation_link['sha256']}.json" if observation["state"] == "open"
                    else f"reports/{id}-mr-outcome-{observation_link['sha256']}.json")
            if stage_link["path"] != name or stage != expected_stage or stage_link["sha256"] != sha(_encode(stage)):
                raise ValueError("MR report does not extend exact approved proposal")
            expected_state = stage["after"]
            expected_point = "waiting-mr" if observation["state"] == "open" else "needs-human"
            prior_link, prior_report, prior_observation = stage_link, stage, observation
    if prepared["before"] != initial or bank["state"] != expected_state or bank["point"] != expected_point:
        raise ValueError("candidate report chain differs from original attempt or session state")
    _state_fields(expected_state)
    state_path = session / f"banks/state/{id}.json"
    if historical_report is None and (any(path.is_symlink() for path in (session / "banks", session / "banks/state", state_path)) or state_path.read_bytes() != _encode(expected_state)):
        raise ValueError("canonical candidate state differs from validated report chain")
    return data, bank, prepared, approval


def _persist(session, data, bank, report, report_rel, evidence_rel, evidence_bytes, point):
    id = bank["id"]
    session = Path(session).resolve()
    state_path = session / f"banks/state/{id}.json"
    temp = state_path.with_suffix(".json.tmp")
    original_state = state_path.read_bytes()
    old_bank = copy.deepcopy(bank)
    outputs = [(session / evidence_rel, evidence_bytes),
               (session / report_rel, _encode(report))]
    for path, _ in outputs + [(temp, b"")]:
        if any(parent.is_symlink() for parent in (path, *path.parents)) or path.exists():
            raise ValueError(f"local candidate output exists or is symlinked: {path}")
    written = []
    try:
        for path, content in outputs:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                written.append(path)
                stream.write(content)
        with temp.open("xb") as stream:
            stream.write(_encode(report["after"]))
        temp.replace(state_path)
        bank.update(state=report["after"], point=point, reason=report["nextAction"],
                    report=report_rel, reportSha256=sha(outputs[-1][1]))
        save(session, data)
    except (ValueError, OSError):
        if state_path.read_bytes() != original_state:
            with temp.open("xb") as stream:
                stream.write(original_state)
            temp.replace(state_path)
        temp.unlink(missing_ok=True)
        for path in written:
            path.unlink(missing_ok=True)
        bank.clear()
        bank.update(old_bank)
        raise


def approve(session: Path, id: int, approval_path: Path, *, agent=False) -> dict:
    data, bank, prepared, existing = load_candidate(session, id)
    if existing is not None:
        raise ValueError("candidate already has a visual assessment")
    session = Path(session).resolve()
    source = Path(approval_path).absolute()
    artifact(session, str(source.relative_to(session)), "assessment input")
    approval_bytes = source.read_bytes()
    approval = _json(source, "candidate assessment")
    if approval.get("scope") != ("candidate-assessment" if agent else "candidate-review"):
        raise ValueError("assessment scope does not match requested command")
    _approval(session, data, bank, prepared, approval)
    digest = sha(approval_bytes)
    kind = "assessed" if agent else "approved"
    link = {"path": f"evidence/{id}/{kind}-{digest}.json", "sha256": digest}
    report = _approval_report(id, prepared, approval, link)
    _persist(session, data, bank, report, f"reports/{id}-{kind}-{digest}.json",
             link["path"], approval_bytes, "waiting-proposal")
    return data


def record_mr(session: Path, id: int, package_link: dict, observation: dict) -> dict:
    data, bank, prepared, approval = load_candidate(session, id)
    if approval is None:
        raise ValueError("MR requires approved pending candidate")
    session = Path(session).resolve()
    current_link = _report_link(session, bank)
    previous = _saved_link(session, current_link, "latest candidate report", report=True)
    link, approved = current_link, previous
    while link["path"].startswith(f"reports/{id}-mr-"):
        link = approved["priorReport"]
        approved = _saved_link(session, link, "approved report", report=True)
    package = _package(session, data, bank, prepared, approved, package_link)
    _observation(observation, package, approval["assessedAt" if approval["scope"] == "candidate-assessment" else "reviewedAt"])
    point = bank["point"]
    if point in ("waiting-mr", "needs-human"):
        earlier = bank["state"]["proposals"][-1]["mr"]
        identity = ("number", "url", "head", "headCommit", "base", "target",
                    "title", "bodySha256")
        if (bank["state"]["proposals"][-1]["assetPublication"] != package_link or
                any(earlier[key] != observation[key] for key in identity)):
            raise ValueError("MR observation differs from the confirmed proposal")
        if earlier["state"] == observation["state"]:
            return data
        if point == "needs-human" or earlier["state"] != "open" or observation["state"] == "open":
            raise ValueError("MR outcome is awaiting human resolution")
    elif point != "waiting-proposal":
        raise ValueError("MR requires approved pending candidate")
    observation_bytes = _encode(observation)
    digest = sha(observation_bytes)
    saved = {"path": f"evidence/{id}/mr-observation-{digest}.json", "sha256": digest}
    report = _mr_report(id, previous, current_link, package_link, saved, observation)
    path = (f"reports/{id}-mr-{digest}.json" if observation["state"] == "open"
            else f"reports/{id}-mr-outcome-{digest}.json")
    _persist(session, data, bank, report, path, saved["path"], observation_bytes,
             "waiting-mr" if observation["state"] == "open" else "needs-human")
    return data


def validate_transition(session, data, bank, bindings: dict, *, historical_report=None) -> dict:
    """Reconstruct approved/open/terminal candidate and pin every review input."""
    from state_results import _artifact, _evidence_files, _file, _report

    actual, validated_bank, prepared, approval = load_candidate(
        session, bank["id"], historical=historical_report is not None,
        historical_report=historical_report)
    if validated_bank != bank:
        raise ValueError(f"candidate changed during service-state validation: {sorted(k for k in set(validated_bank) | set(bank) if validated_bank.get(k) != bank.get(k))}")
    if ((historical_report is None and actual != data) or
            (historical_report is not None and any(
                actual[key] != data[key] for key in ("baseCommit", "worktree", "repo", "selectedIds")))):
        raise ValueError("candidate session changed during service-state validation")
    session = Path(session).resolve()
    id = bank["id"]
    package_link = prepared["reviewPackage"]
    payload = _saved_link(session, package_link, "prepared review package")
    _artifact(session, package_link["path"], bindings, "prepared review package", package_link["sha256"])
    _artifact(session, prepared["candidate"]["path"], bindings, "saved final SVG",
              prepared["candidate"]["sha256"])
    _artifact(session, payload["priorEvidence"]["path"], bindings, "original search evidence",
              payload["priorEvidence"]["sha256"])
    _file(session / payload["priorReport"]["path"], bindings)
    _evidence_files(session, bank, payload, bindings)
    for background in ("light", "dark"):
        for role in ("source", "base", "candidate", "beforeAfter"):
            link = payload["comparison"][background][role]
            if link is None and bank["iconHash"] is None and role in ("base", "beforeAfter"):
                continue
            _artifact(session, link["path"], bindings, "review comparison", link["sha256"])
    for row in payload["platforms"]:
        if row["status"] == "passed":
            for key in ("buildEvidence", "uiEvidence"):
                link = row[key]
                _artifact(session, link["path"], bindings, "candidate platform evidence", link["sha256"])
    for key in ("mrDraft", "diff"):
        link = payload[key]
        _artifact(session, link["path"], bindings, "candidate review material", link["sha256"])
    link = _report_link(session, bank)
    while link["path"].startswith(f"reports/{id}-mr-"):
        report = _report(session, link["path"], bindings)
        observation = report["observation"]
        _artifact(session, observation["path"], bindings, "MR observation", observation["sha256"])
        publication = report["assetPublication"]
        _file(session / publication["path"], bindings)
        link = report["priorReport"]
    if approval is not None:
        approved_report = _report(session, link["path"], bindings)
        evidence_link = approved_report["approval"]
        _artifact(session, evidence_link["path"], bindings, "exact candidate assessment",
                  evidence_link["sha256"])
        provenance = approval["provenance"]
        _artifact(session, provenance["path"], bindings, "attributed visual assessment provenance",
                  provenance["sha256"])
        link = approved_report["priorReport"]
    _report(session, link["path"], bindings)
    import admission
    for relative, digest in admission.retained(session, prepared).items():
        _artifact(session, relative, bindings, "admission evidence", digest)
    proposal = bank["state"]["proposals"][-1]
    if proposal.get("mr") is not None and historical_report is None:
        from asset_publication import validate_remote_observation
        validate_remote_observation(session, proposal["assetPublication"], proposal["mr"])
    return copy.deepcopy(bank["state"])


def validate_resume(session, data, bank) -> None:
    actual, validated, _, _ = load_candidate(session, bank["id"], _resume=True)
    # continue may have just recorded another bank, resumed paused points, or
    # recomputed continuationStops without saving session.json yet. Those are
    # not changes to this candidate. Compare its complete validated bank plus
    # every session-context field; never certify the other banks here.
    def context(value):
        return {key: item for key, item in value.items()
                if key not in {"banks", "continuationStops"}}
    if context(actual) != context(data) or validated != bank:
        raise ValueError("candidate changed during continuation")
