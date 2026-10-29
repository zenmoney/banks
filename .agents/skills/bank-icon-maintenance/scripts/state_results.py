"""Validate a session's saved bank results before preparing service-state publication.

This module reads the canonical reports and their evidence; it never searches sources,
renders an icon, or writes a Git ref. File digests bind the validated inputs to the
later publication decision.
"""
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import re
import subprocess

from evidence import artifact, instant, render_binding, validate_attempt, validate_review
from evidence import baseline_bytes, baseline_blob
from session import SessionError as SessionInputError, catalog, policy_at, read_state, state_decision


POINTS = {"unchanged": "awaiting-state-publication", "unconfirmed": "attempt-complete",
          "error": "attempt-complete", "candidate": "waiting-proposal", "needs-human": "needs-human"}


def _sha(content):
    return hashlib.sha256(content).hexdigest()


def _git_bytes(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    if result.returncode:
        raise ValueError(f"session Git {args[0]} failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def _git(repo, *args):
    return _git_bytes(repo, *args).decode("utf-8").strip()


def _file(path, bindings):
    """Read and bind an exact regular file, without following symlinks in its path."""
    path = Path(path).absolute()
    if any(part.is_symlink() for part in (path, *path.parents)) or not path.is_file():
        raise ValueError(f"expected regular non-symlink file: {path}")
    content = path.read_bytes()
    digest = _sha(content)
    old = bindings.setdefault(str(path), digest)
    if old != digest:
        raise ValueError(f"file changed during validation: {path}")
    return content


def _json(path, bindings):
    try:
        return json.loads(_file(path, bindings))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON: {path}") from exc


def _artifact(session, relative, bindings, label, expected_hash=None):
    path = artifact(session, relative, label)
    _file(path, bindings)
    if expected_hash is not None and bindings[str(path)] != expected_hash:
        raise ValueError(f"{label} hash mismatch")
    return path


def _session_data(session, ids, bindings, *, allow_paused=False):
    if not session.is_dir() or session.is_symlink():
        raise ValueError("session must be an existing regular directory")
    data = _json(session / "session.json", bindings)
    if (type(data) is not dict or type(data.get("schemaVersion")) is not int or
            data["schemaVersion"] != 2 or
            (type(data.get("paused")) is not bool if allow_paused else data.get("paused") is not False) or
            type(data.get("selectedIds")) is not list or type(data.get("banks")) is not list or
            len(data["selectedIds"]) != 1 or len(data["banks"]) != 1 or
            any(type(id) is not int for id in data["selectedIds"]) or
            len(set(data["selectedIds"])) != len(data["selectedIds"]) or
            any(type(bank) is not dict or type(bank.get("id")) is not int or bank["id"] != id
                for id, bank in zip(data["selectedIds"], data["banks"]))):
        raise ValueError("session is not a valid single-bank task workspace")
    if ids is None:
        ids = data["selectedIds"]
    elif type(ids) is not list or not ids or any(type(id) is not int for id in ids) or len(set(ids)) != len(ids):
        raise ValueError("state package needs distinct Company.id values")
    if not set(ids).issubset(data["selectedIds"]):
        raise ValueError("state package includes a bank outside the approved selection")
    repo_value, worktree_value = data.get("repo"), data.get("worktree")
    if (type(repo_value) is not str or type(worktree_value) is not str or
            not Path(repo_value).is_absolute() or worktree_value != str(session / "worktree") or
            not (session / "worktree").is_dir() or (session / "worktree").is_symlink()):
        raise ValueError("session worktree is missing, moved or replaced")
    repo, worktree = Path(repo_value), session / "worktree"
    if _git(repo, "rev-parse", "--show-toplevel") != str(repo.resolve()):
        raise ValueError("session source repository registration changed")
    if _git(worktree, "rev-parse", "--show-toplevel") != str(worktree):
        raise ValueError("session worktree registration changed")
    common = Path(_git(repo, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = repo / common
    git_dir = Path(_git(worktree, "rev-parse", "--absolute-git-dir")).resolve()
    if git_dir != Path(data.get("worktreeGitDir", "")) or git_dir.parent != common.resolve() / "worktrees":
        raise ValueError("session worktree Git directory changed")
    base = data.get("baseCommit")
    if type(base) is not str or _git(worktree, "rev-parse", "HEAD") != base:
        raise ValueError("session worktree base commit changed")
    base_ref = data.get("baseRef")
    if type(base_ref) is not str or not base_ref.strip():
        raise ValueError("session base ref is invalid")
    history = data.get("stateHistory")
    state_ref, state_commit = data.get("stateRef"), data.get("stateCommit")
    if ((history == "unknown" and (state_ref is not None or state_commit is not None)) or
            (history == "present" and (type(state_ref) is not str or type(state_commit) is not str)) or
            history not in ("unknown", "present")):
        raise ValueError("session state-history pin is invalid")
    if state_commit is not None and _git(repo, "rev-parse", "--verify", f"{state_commit}^{{commit}}") != state_commit:
        raise ValueError("session original service-state commit is unavailable")
    return data


def _prior_state(data, id):
    if data["stateCommit"] is None:
        return None
    repo, commit = Path(data["repo"]), data["stateCommit"]
    path = f"banks/state/{id}.json"
    row = _git(repo, "ls-tree", commit, "--", path).split()
    if not row:
        return None
    if row[0] not in ("100644", "100755"):
        raise ValueError(f"original state is not a regular file: {path}")
    try:
        state, error = read_state(repo, commit, {path}, id)
    except SessionInputError as exc:
        raise ValueError(str(exc)) from exc
    if error:
        raise ValueError(error)
    _state_fields(state)
    return state


def _state_fields(state):
    if type(state) is not dict or "id" in state or "iconUpdatedAt" in state:
        raise ValueError("per-bank state must be an object without id or iconUpdatedAt")


def _catalog_pin(data, bank, entries, errors, bindings):
    worktree = Path(data["worktree"])
    matches = entries.get(bank["id"], [])
    if (errors.get(bank["id"]) or len(matches) != 1 or matches[0]["problem"] or
            any(matches[0].get(key) != bank.get(key) for key in
                ("folder", "metadata", "metadataHash", "iconHash", "title"))):
        raise ValueError(f"catalog entry or current SVG changed for Company.id {bank['id']}")
    folder = worktree / bank["folder"]
    metadata_path, icon_path = folder / "info.json", folder / "icon.svg"
    _file(metadata_path, bindings)
    baseline_bytes(icon_path, bank["iconHash"])
    bindings[str(icon_path)] = bank["iconHash"]
    if (bindings[str(metadata_path)] != bank["metadataHash"] or
            bindings[str(icon_path)] != bank["iconHash"]):
        raise ValueError(f"catalog bytes changed for Company.id {bank['id']}")


def _accepted_bank_pin(data, bank, current_base):
    """Allow later unrelated commits, never a changed selected accepted bank."""
    if current_base == data["baseCommit"]:
        return
    repo = Path(data["repo"])
    for filename, expected in (("info.json", bank["metadataHash"]),
                               ("icon.svg", bank["iconHash"])):
        relative = f"{bank['folder']}/{filename}"
        baseline_blob(repo, current_base, relative, expected)
    names = _git_bytes(repo, "ls-tree", "-r", "-z", "--name-only", current_base,
                       "--", "banks/").split(b"\0")
    for name in names:
        if not name.endswith(b"/info.json"):
            continue
        folder = name[:-len(b"/info.json")].decode("utf-8")
        match = re.fullmatch(r"(.+)_([0-9]+)-([a-zA-Z]{2}|unknown)", Path(folder).name)
        if folder != bank["folder"] and match and int(match.group(2)) == bank["id"]:
            raise ValueError(f"base ref moved and Company.id {bank['id']} is duplicate")
        if folder == bank["folder"]:
            continue
        try:
            metadata = json.loads(_git_bytes(repo, "show", f"{current_base}:{name.decode('utf-8')}"))
        except (UnicodeError, json.JSONDecodeError):
            continue
        if type(metadata) is dict and type(metadata.get("id")) is int and metadata["id"] == bank["id"]:
            raise ValueError(f"base ref moved and Company.id {bank['id']} is duplicate")


def _report(session, relative, bindings):
    path = Path(relative)
    if (path.is_absolute() or len(path.parts) != 2 or path.parts[0] != "reports" or
            any(segment in ("", ".", "..") for segment in relative.split("/"))):
        raise ValueError("report must be under session/reports")
    report = _json(session / path, bindings)
    if type(report) is not dict:
        raise ValueError("report must be a JSON object")
    return report


def _evidence_files(session, bank, payload, bindings):
    desktop = payload.get("desktop")
    if desktop is not None:
        manifest_path = _artifact(session, desktop["manifest"], bindings, "Desktop manifest",
                                  desktop.get("manifestSha256"))
        resource = f"bank_{bank['id']}" if bank["id"] >= 0 else f"bank_m{-bank['id']}"
        _artifact(session, str((manifest_path.parent / "composeResources" / "drawable" /
                                f"{resource}.xml").relative_to(session)), bindings, "generated XML")
        for background in ("light", "dark"):
            image = desktop[background]
            _artifact(session, image["path"], bindings, f"Desktop {background}", image["sha256"])
        from headless import RENDERER as OFFSCREEN
        if desktop["renderer"] == OFFSCREEN:
            for key in ("invocation", "result"):
                link = desktop[key]
                _artifact(session, link["path"], bindings, f"offscreen {key}", link["sha256"])
            receipt = json.loads((session / desktop["invocation"]["path"]).read_bytes())
            log = receipt["log"]
            _artifact(session, log["path"], bindings, "offscreen process log", log["sha256"])
            if desktop.get("baseObservation") is not None:
                link = desktop["baseObservation"]
                path = _artifact(session, link["path"], bindings, "base observation", link["sha256"])
                _evidence_files(session, bank, {"desktop": json.loads(path.read_bytes())}, bindings)
    if payload.get("candidate") is not None:
        candidate = payload["candidate"]
        _artifact(session, candidate["path"], bindings, "candidate input",
                  candidate.get("iconHash", candidate.get("sha256")))
    from source_evidence import evidence_links
    for item in evidence_links(session, payload):
        _artifact(session, item["path"], bindings, "source material lineage", item["sha256"])


def _initial_result(session, data, bank, before, bindings):
    id = bank["id"]
    report = _report(session, f"reports/{id}.json", bindings)
    evidence_rel = f"evidence/{id}/validated.json"
    evidence = _json(_artifact(session, evidence_rel, bindings, "validated attempt"), bindings)
    provenance = report.get("provenance")
    if type(provenance) is not dict:
        raise ValueError("attempt report lacks provenance")
    source = provenance.get("sourceEvidence")
    if type(source) is not str or not Path(source).is_absolute():
        raise ValueError("attempt source path is not absolute")
    try:
        source_rel = Path(source).relative_to(session)
    except ValueError as exc:
        raise ValueError("attempt source is outside session") from exc
    if (not source_rel.parts or source_rel.parts[0] != "evidence" or
            any(part in ("", ".", "..") for part in source.split("/")[1:])):
        raise ValueError("attempt source provenance is not a session evidence path")
    # The original search input is mutable; the saved validated.json is canonical.
    evidence_path = session / evidence_rel
    payload = validate_attempt(evidence_path, data, bank)
    _file(evidence_path, bindings)
    if instant(payload["checkedAt"], "checkedAt") > datetime.now(timezone.utc):
        raise ValueError("attempt checkedAt is after command time")
    expected_provenance = {"baseRef": data["baseRef"], "baseCommit": data["baseCommit"],
                           "stateRef": data["stateRef"], "stateCommit": data["stateCommit"],
                           "stateHistory": data["stateHistory"], "sourceEvidence": source}
    binding = render_binding(data, bank, payload)
    if "renderBinding" in report:
        bound_render = report["renderBinding"] == binding and binding is not None
    elif binding is None:
        bound_render = True
    else:
        # Immutable older reports pinned the same identity in a passed platform row.
        bound_render = any({key: row.get(key) for key in binding} == binding
                           for row in payload["platforms"] if row["status"] == "passed")
    if (report.get("schemaVersion") != 1 or type(report.get("id")) is not int or report["id"] != id or
            report.get("outcome") != payload["outcome"] or report.get("before") != before or
            report.get("evidence") != payload or evidence != payload or
            report.get("evidencePath") != evidence_rel or report.get("statePath") != f"banks/state/{id}.json" or
            report.get("provenance") != expected_provenance or report.get("humanReview") != "pending" or
            report.get("nextAction") != payload["nextAction"] or
            not bound_render):
        raise ValueError(f"initial report/evidence mismatch for Company.id {id}")
    from feedback_revision import attempt_state
    after = attempt_state(session, data, bank, before, payload, bindings)
    _state_fields(after)
    if report.get("after") != after:
        raise ValueError(f"attempt report changes unsupported state for Company.id {id}")
    _evidence_files(session, bank, payload, bindings)
    return report, payload, after


def _reviewed_result(session, data, bank, initial, prior_state, bindings):
    id = bank["id"]
    prior_bank = {**bank, "state": prior_state, "report": f"reports/{id}.json",
                  "evidence": f"evidence/{id}/validated.json"}
    review_rel = f"evidence/{id}/human-review.json"
    review_path = _artifact(session, review_rel, bindings, "saved human review")
    review, payload, prior_report = validate_review(review_path, data, prior_bank,
                                                    datetime.now(timezone.utc))
    _file(review_path, bindings)
    _file(session / f"reports/{id}.json", bindings)
    original = Path(initial["provenance"]["sourceEvidence"])
    _artifact(session, str(original.relative_to(session)), bindings, "review prior source")
    if prior_report != initial:
        raise ValueError(f"review prior report differs from initial result for Company.id {id}")
    _artifact(session, review["priorEvidence"]["path"], bindings, "review prior evidence",
              review["priorEvidence"]["sha256"])
    _artifact(session, review["evidence"]["path"], bindings, "review assessment",
              review["evidence"]["sha256"])
    _artifact(session, review["comparison"]["path"], bindings, "review comparison",
              review["comparison"]["sha256"])
    evidence_rel = f"evidence/{id}/reviewed-unchanged.json"
    stored = _json(_artifact(session, evidence_rel, bindings, "reviewed evidence"), bindings)
    after = dict(prior_state)
    after.update(lastAttemptAt=payload["checkedAt"], lastAttemptResult="verified",
                 lastVerifiedAt=payload["checkedAt"], verifiedIconHash=payload["iconHash"])
    report_rel = f"reports/{id}-reviewed-unchanged.json"
    report = _report(session, report_rel, bindings)
    expected = {"schemaVersion": 1, "id": id, "outcome": "unchanged",
                "before": prior_state, "after": after, "evidence": payload,
                "evidencePath": evidence_rel, "statePath": f"banks/state/{id}.json",
                "priorReport": review["priorReport"], "priorEvidence": review["priorEvidence"],
                "reviewPath": review_rel, "humanReview": "confirmed",
                "provenance": {**initial["provenance"], "sourceEvidence": review["evidence"]["path"]},
                "nextAction": payload["nextAction"]}
    if (stored != payload or report != expected or bank.get("report") != report_rel or
            bank.get("evidence") != evidence_rel or bank.get("point") != "awaiting-state-publication"):
        raise ValueError(f"reviewed report/evidence mismatch for Company.id {id}")
    _evidence_files(session, bank, payload, bindings)
    return after


def _candidate_result(session, data, bank, prior_state, bindings):
    from candidate import validate_prepared_proposal

    id = bank["id"]
    report = validate_prepared_proposal(session, data, bank)
    package_link = report["reviewPackage"]
    package_path = _artifact(session, package_link["path"], bindings, "saved candidate package",
                             package_link["sha256"])
    payload = _json(package_path, bindings)
    if report["before"] != prior_state or report["evidence"] != payload:
        raise ValueError(f"candidate prior state or package differs for Company.id {id}")
    after = copy.deepcopy(prior_state)
    proposal = after["proposals"][-1]
    raw = proposal["candidate"]
    final = report["candidate"]
    proposal.update(sourceCandidate=raw, candidate={**payload["candidate"], **final,
                                                   "iconHash": final["sha256"]},
                    candidateIconHash=final["sha256"], reviewPackage=package_link,
                    verification=report["renderBinding"], humanReview="pending")
    from feedback_evidence import record as record_feedback
    record_feedback(proposal, payload)
    from task_work import record_response
    record_response(proposal, payload)
    if (report["after"] != after or bank.get("point") != "waiting-proposal" or
            bank.get("evidence") != package_link["path"]):
        raise ValueError(f"prepared candidate changes unsupported state for Company.id {id}")
    if _report(session, bank["report"], bindings) != report:
        raise ValueError(f"candidate report changed during validation for Company.id {id}")
    _artifact(session, payload["priorEvidence"]["path"], bindings, "candidate prior evidence",
              payload["priorEvidence"]["sha256"])
    _artifact(session, payload["candidate"]["path"], bindings, "candidate input SVG",
              payload["candidate"]["sha256"])
    _artifact(session, final["path"], bindings, "saved final SVG", final["sha256"])
    _evidence_files(session, bank, payload, bindings)
    for background in ("light", "dark"):
        for role in ("source", "base", "candidate", "beforeAfter"):
            image = payload["comparison"][background][role]
            if image is None and bank["iconHash"] is None and role in ("base", "beforeAfter"):
                continue
            _artifact(session, image["path"], bindings,
                      f"candidate comparison {background} {role}", image["sha256"])
    for row in payload["platforms"]:
        if row["status"] == "passed":
            for name in ("buildEvidence", "uiEvidence"):
                link = row[name]
                _artifact(session, link["path"], bindings,
                          f"candidate {row['target']} {name}", link["sha256"])
    for name in ("mrDraft", "diff"):
        link = payload[name]
        _artifact(session, link["path"], bindings, f"candidate {name}", link["sha256"])
    import admission
    for relative, digest in admission.retained(session, report).items():
        _artifact(session, relative, bindings, "admission evidence", digest)
    return after


def _inherited_rejection(session, bank, before, point, eligible):
    """A saved rejection with no new work needs no fabricated attempt or write.

    Catalog and remote-before checks remain with the ordinary caller/writer. This
    narrow no-op is also usable by an old explicit/frozen session. Any local work,
    queued feedback, altered state or released reconsideration retains its gates.
    """
    proposals = (before or {}).get("proposals", [])
    if (point != "needs-human" or eligible or bank.get("point") != point or
            not proposals or proposals[-1].get("status") != "rejected" or
            bank.get("state") != before or bank.get("report") or bank.get("evidence") or
            any(p.get("status") in {"open", "pending"} for p in proposals) or
            any(r.get("status") in {"queued", "candidate"} for r in (before or {}).get("reviewFeedback", []))):
        return False
    id = bank["id"]
    paths = [session / f"banks/state/{id}.json", session / f"reports/{id}.json",
             session / f"evidence/{id}"] + list((session / "reports").glob(f"{id}-*.json"))
    return not any(p.exists() or p.is_symlink() for p in paths)


def validate_results(session: Path, ids: list[int]) -> tuple[dict, list[dict], dict[str, str | None]]:
    """Return pinned session data, exact per-ID transitions, and input-file SHA-256 bindings."""
    session = Path(session).resolve()
    bindings = {}
    data = _session_data(session, ids, bindings)
    banks = {bank["id"]: bank for bank in data["banks"]}
    try:
        entries, errors = catalog(Path(data["worktree"]))
        policy = policy_at(Path(data["repo"]), data["baseCommit"])
    except SessionInputError as exc:
        raise ValueError(str(exc)) from exc
    current_base = _git(data["repo"], "rev-parse", "--verify",
                        f"{data['baseRef']}^{{commit}}")
    session_time = instant(data.get("now"), "session.now")
    records = []
    for id in ids:
        bank = banks[id]
        before = _prior_state(data, id)
        _catalog_pin(data, bank, entries, errors, bindings)
        _accepted_bank_pin(data, bank, current_base)
        if bank.get("taskContext"):
            from task_work import context, decision, no_attempt
            task = context(session, data, bank, before, bindings)
            choice = decision(session, data, bank, bindings)
            if bank.get("point") == "task-complete":
                after = no_attempt(session, data, bank, before, bindings)
                if after != bank["state"]:
                    raise ValueError("completed task state changed")
                records.append({"id":id, "path":f"banks/state/{id}.json", "before":before, "after":after})
                continue
        point, eligible, _ = state_decision(before, bank["iconHash"],
                                            session_time, policy, True)
        if bank.get("taskContext"):
            # The validated task explicitly owns a new attempt; it does not grant
            # permission to republish rejected bytes or bypass quality checks.
            if choice and choice["action"] not in {"repair", "verify-applicability"}:
                raise ValueError("recorded reviewer action does not call for research")
            point, eligible = None, True
        if point is not None or not eligible:
            if _inherited_rejection(session, bank, before, point, eligible):
                records.append({"id": id, "path": f"banks/state/{id}.json", "before": before, "after": before})
                continue
            raise ValueError(f"Company.id {id} prior state was not eligible for this new attempt")
        from feedback_revision import queued, context as feedback_context
        if not bank.get("taskContext") and queued(before) is not None and feedback_context(session, data, bank, before, bindings) is None:
            raise ValueError("queued feedback requires an immutable verified revision context")
        initial, payload, after = _initial_result(session, data, bank, before, bindings)
        if bank.get("report", "").startswith(f"reports/{id}-finished-"):
            if payload["outcome"] != "candidate":
                raise ValueError("completion requires an actual candidate attempt")
            from task_completion import validate_result
            after = validate_result(session, data, bank, after, bindings)
        elif bank.get("report") == f"reports/{id}-reviewed-unchanged.json":
            if payload["outcome"] != "needs-human":
                raise ValueError("reviewed result requires prior needs-human attempt")
            after = _reviewed_result(session, data, bank, initial, after, bindings)
        elif payload["outcome"] == "candidate" and bank.get("report") != f"reports/{id}.json":
            if bank["report"].startswith(f"reports/{id}-candidate-"):
                after = _candidate_result(session, data, bank, after, bindings)
            else:
                from asset_results import validate_transition
                after = validate_transition(session, data, bank, bindings)
        elif (bank.get("report") != f"reports/{id}.json" or
              bank.get("evidence") != f"evidence/{id}/validated.json" or
              bank.get("point") != POINTS[payload["outcome"]]):
            raise ValueError(f"Company.id {id} is not at a completed publishable result")
        _state_fields(bank.get("state"))
        state = _json(session / f"banks/state/{id}.json", bindings)
        if state != after or bank["state"] != after:
            raise ValueError(f"session state differs from validated result for Company.id {id}")
        records.append({"id": id, "path": f"banks/state/{id}.json", "before": before, "after": after})
    return data, records, bindings
