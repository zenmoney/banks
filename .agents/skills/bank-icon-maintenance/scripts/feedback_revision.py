"""A new, evidence-bearing revision of ONE existing icon PR, never a duplicate.

Only the queued, sourced human request changes automatic-selection eligibility.
Original proposal/state/receipts stay historical; candidate validation and all
publication authority gates are reused. Source messages cannot authorize writes.
"""
import copy
import json
from pathlib import Path

from evidence import instant, required
import feedback_intake as intake
import state_publication as state


def queued(before):
    rows = [row for row in intake.requests(before) if row["status"] == "queued"]
    if len(rows) > 1:
        raise ValueError("conflicting queued feedback requires an explicit sourced superseding decision")
    return rows[0] if rows else None


def selection(before, icon_hash):
    """Return an override only for queued feedback; never bypass identity stops."""
    request = queued(before)
    if request is None:
        return None
    event = request["event"]
    if event["bank"]["iconHash"] != icon_hash:
        return "needs-human", False, "feedback baseline changed; reassess the request against current bank bytes"
    proposals = (before or {}).get("proposals", [])
    active = [p for p in proposals if p["status"] in {"pending", "open"} or (p.get("mr") or {}).get("state") == "open"]
    if len(active) > 1:
        return "blocked", False, "multiple active proposals require human reconciliation"
    if active:
        proposal = active[0]
        mr = proposal.get("mr") or {}
        if proposal is not proposals[-1] or proposal["status"] != "open" or mr.get("state") != "open":
            return "waiting-proposal", False, "unfinished candidate must be resumed; feedback cannot discard it"
        if any(mr.get(key) != event["mr"].get(key) for key in ("number", "url", "target", "head", "base")):
            return "blocked", False, "feedback refers to another MR than the active proposal"
        if proposal.get("baseIconHash") != icon_hash:
            return "needs-human", False, "current accepted base changed since the active proposal"
    elif proposals and proposals[-1]["status"] == "rejected":
        return "needs-human", False, "feedback does not release a rejected proposal's identity/design stop"
    return None, True, "queued reviewer-selected icon revision; preserve the same open MR"


def _changed(repo, base, head):
    return state.git(repo, "diff", "--no-renames", "--name-only", "-z", base, head).stdout.split(b"\0")[:-1]


def _proof(repo, base, bank, before, mr, source_head, branch_base):
    from asset_publication import _bank_base
    from proposal_sync import _ancestor, _commit
    _commit(source_head)
    _commit(branch_base)
    path = f'{bank["folder"]}/icon.svg'.encode()
    if not _ancestor(repo, branch_base, source_head) or not _ancestor(repo, branch_base, base):
        raise ValueError("feedback PR branch base is not in both source and target histories")
    if _changed(repo, branch_base, source_head) != [path]:
        raise ValueError("feedback PR must change exactly this bank icon, without hidden unrelated changes")
    for commit in (base, branch_base):
        _bank_base(repo, commit, bank)
    # Identity/metadata must not have been modified on the PR branch either.
    from asset_publication import _blob
    if state.digest(_blob(repo, source_head, f'{bank["folder"]}/info.json')) != bank["metadataHash"]:
        raise ValueError("feedback PR changes bank identity")
    active = [p for p in (before or {}).get("proposals", []) if p["status"] == "open"]
    if active:
        previous = active[-1]
        old_head = previous["mr"]["headCommit"]
        if not _ancestor(repo, old_head, source_head):
            raise ValueError("feedback head no longer descends from the published proposal")
        if old_head != source_head and _changed(repo, old_head, source_head) not in ([], [path]):
            raise ValueError("feedback head contains changes outside the selected icon")
        if state.digest(_blob(repo, old_head, path.decode())) != previous["candidateIconHash"]:
            raise ValueError("prior proposal differs from its immutable published commit")
    if source_head != mr["headCommit"]:
        raise ValueError("feedback source commit differs from its provider observation")


def prepare_context(session, data, bank):
    from proposal_sync import _fetch_ref
    import github_pr
    before = bank["state"]
    request = queued(before)
    if request is None:
        return
    result = selection(before, bank["iconHash"])
    if result[0] is not None or not result[1]:
        raise ValueError(result[2])
    event, mr = request["event"], request["event"]["mr"]
    if intake.repository_from_remote(data["stateRemote"]) != mr["target"]:
        raise ValueError("feedback MR and selected service-state destination differ")
    if any(event["bank"][key] != bank[key] for key in ("id", "folder", "metadataHash", "iconHash")):
        raise ValueError("feedback bank identity differs from the committed catalog")
    intake.verify_event(event)
    repo = Path(data["repo"])
    base = _fetch_ref(repo, mr["target"], f'refs/heads/{mr["base"]}')
    source_head = _fetch_ref(repo, mr["target"], f'refs/pull/{mr["number"]}/head')
    if base != data["baseCommit"]:
        raise ValueError("feedback revision needs the explicitly selected up-to-date target base; do not reset the user's checkout")
    branch_base = state.git(repo, "merge-base", base, source_head).stdout.decode().strip()
    _proof(repo, base, bank, before, mr, source_head, branch_base)
    if github_pr.inspect(mr["target"], mr["number"]) != mr:
        raise ValueError("feedback PR changed while pinning its revision context")
    context = {"schemaVersion": 1, "requestKey": request["key"], "request": request,
               "baseCommit": base, "sourceHead": source_head, "branchBase": branch_base,
               "mr": mr, "bank": event["bank"]}
    content = state.encoded(context)
    from source_evidence import fresh
    root = fresh(session, bank["id"], "reviewer-context")
    path = (root / "context.json").relative_to(session).as_posix()
    state.save_new(session / path, content)
    bank["feedbackContext"] = {"path": path, "sha256": state.digest(content)}


def context(session, data, bank, before=None, bindings=None):
    """Revalidate immutable local proof, not live PR text during historical replay."""
    from source_evidence import linked, read_json
    if bank.get("taskContext"):
        from task_work import revision
        return revision(session, data, bank, before, bindings)
    link = bank.get("feedbackContext")
    if link is None:
        if before is not None and queued(before) is not None:
            raise ValueError("queued feedback requires its immutable revision context")
        return None
    path = linked(session, link, "feedback context")
    value = read_json(path)
    if before is None:
        from state_results import _prior_state
        before = _prior_state(data, bank["id"])
    request = queued(before)
    if (request is None or value.get("schemaVersion") != 1 or value.get("request") != request
            or value.get("requestKey") != request["key"] or value.get("mr") != request["event"]["mr"]
            or value.get("bank") != request["event"]["bank"]
            or value.get("baseCommit") != data["baseCommit"]):
        raise ValueError("feedback context differs from the pinned durable request")
    if any(value["bank"][k] != bank[k] for k in ("id", "folder", "metadataHash", "iconHash")):
        raise ValueError("feedback context bank identity changed")
    decision = selection(before, bank["iconHash"])
    if decision[0] is not None or not decision[1]:
        raise ValueError(decision[2])
    _proof(Path(data["repo"]), data["baseCommit"], bank, before, value["mr"],
           value["sourceHead"], value["branchBase"])
    if bindings is not None:
        bindings[str(path)] = link["sha256"]
    return value


def attempt_state(session, data, bank, before, payload, bindings=None):
    """Single deterministic transition shared by recording and publication replay."""
    if bank.get("taskContext"):
        from task_work import attempt_state as task_attempt
        return task_attempt(session, data, bank, before, payload, bindings)
    revision = context(session, data, bank, before, bindings)
    after = copy.deepcopy(before) if before is not None else {"schemaVersion": 1}
    outcome = payload["outcome"]
    after.update(lastAttemptAt=payload["checkedAt"],
                 lastAttemptResult="verified" if outcome == "unchanged" else outcome)
    request = None
    if revision:
        request = next(row for row in after["reviewFeedback"] if row["key"] == revision["requestKey"])
        if instant(payload["checkedAt"], "checkedAt") < instant(request["recordedAt"], "feedback.recordedAt"):
            raise ValueError("feedback attempt predates the sourced request")
        if outcome == "unchanged":
            # Existing unchanged evidence proves currency, not that a supplied
            # illustration matches it. Never manufacture a satisfied request.
            raise ValueError("reviewer feedback needs a selected-variant candidate or a specific unsuccessful result; ordinary unchanged does not prove attachment equivalence")
        request.update(status="candidate" if outcome == "candidate" else "blocked",
                       result={"outcome": outcome, "checkedAt": payload["checkedAt"], "reason": payload["reason"]})
    if outcome == "unchanged":
        after.update(lastVerifiedAt=payload["checkedAt"], verifiedIconHash=payload["iconHash"])
    elif outcome == "candidate":
        candidate = payload["candidate"]
        proposal = {"status": "pending", "baseIconHash": payload["iconHash"],
                    "candidateIconHash": candidate["iconHash"], "candidate": candidate,
                    "checkedAt": payload["checkedAt"], "sources": payload["sources"],
                    "assessment": payload["assessment"], "reason": payload["reason"]}
        proposals = after.get("proposals", [])
        if revision:
            proposal["revision"] = {"requestKey": request["key"], "context": bank["feedbackContext"],
                                    "mr": revision["mr"], "sourceHead": revision["sourceHead"],
                                    "branchBase": revision["branchBase"]}
            if proposals and proposals[-1]["status"] == "open":
                old = proposals[-1]
                proposal["revisionHistory"] = [*old.get("revisionHistory", []),
                    {k: v for k, v in old.items() if k != "revisionHistory"}]
                proposals = proposals[:-1]
        after["proposals"] = [*proposals, proposal]
    return after


def published(after, observation, final_hash):
    """Called by the existing exact-MR result constructor, never by the model."""
    proposal = after["proposals"][-1]
    if proposal.get("taskKey"):
        from task_work import published as task_published
        return task_published(after, observation, final_hash)
    revision = proposal.get("revision")
    if revision is None:
        return
    if any(observation.get(k) != revision["mr"].get(k) for k in ("number", "url", "target", "head", "base")):
        raise ValueError("revision cannot create or switch to another MR")
    row = next((row for row in after.get("reviewFeedback", []) if row["key"] == revision["requestKey"]), None)
    if row is not None and row["status"] == "published":
        prior = row.get("publication", {})
        if (prior.get("candidateIconHash") == final_hash and
                all(prior.get("mr", {}).get(k) == observation.get(k) for k in ("number", "url", "head", "target"))):
            return  # The original successful publication remains a historical fact.
    if row is None or row["status"] != "candidate":
        raise ValueError("published revision is not bound to its in-progress request")
    row.update(status="published", publication={"mr": observation, "candidateIconHash": final_hash})


def baseline_parent(repo, proposal, reviewed, parent, path):
    """For terminal sync an amendment's accepted baseline is its branch base."""
    revision = proposal.get("revision")
    if revision is None:
        return parent
    from proposal_sync import _ancestor, _commit
    branch_base = _commit(revision["branchBase"])
    if (parent != revision["sourceHead"] or not _ancestor(repo, branch_base, parent)
            or _changed(repo, branch_base, parent) != [path.encode()]
            or _changed(repo, parent, reviewed) != [path.encode()]):
        raise ValueError("revision history does not prove a single-icon amendment")
    return branch_base


def overlay_own(original, current, own):
    """Extend only a confirmed earlier stage of this exact revision/state writer.

    Unrelated affected-bank writes need manual recovery, not a generic overwrite
    resolution. The caller has already revalidated the entire local report chain.
    """
    before, after = original["before"] or {}, original["after"]
    if not own or current is None:
        raise ValueError("feedback revision requires exact prior state or a confirmed own earlier package")
    before_rows, after_rows, current_rows = map(intake.requests, (before, after, current))
    if not len(before_rows) == len(after_rows) == len(current_rows):
        raise ValueError("feedback history changed concurrently")
    changed = [i for i, (a, b) in enumerate(zip(before_rows, after_rows)) if a != b]
    if len(changed) != 1:
        raise ValueError("revision must advance exactly one sourced feedback request")
    i = changed[0]
    for index, (old, final, prior) in enumerate(zip(before_rows, after_rows, current_rows)):
        if index != i and not old == final == prior:
            raise ValueError("unrelated feedback history cannot be replaced")
        if index == i:
            immutable = lambda row: {k: v for k, v in row.items() if k not in {"status", "result", "publication"}}
            if not immutable(old) == immutable(final) == immutable(prior):
                raise ValueError("feedback request provenance changed")
            if old["status"] != "queued" or prior["status"] not in {"candidate", "published"} or final["status"] not in {"candidate", "published"}:
                raise ValueError("feedback result is not a forward candidate/publication transition")
            if prior["status"] == "published" and final["status"] != "published":
                raise ValueError("cannot roll a published revision back to candidate")
    a, b = after.get("proposals", []), current.get("proposals", [])
    if not a or len(a) != len(b) or a[:-1] != b[:-1] or not a[-1].get("revision"):
        raise ValueError("revision proposal lineage changed")
    final, prior = a[-1], b[-1]
    if any(final.get(k) != prior.get(k) for k in ("revision", "revisionHistory", "baseIconHash", "checkedAt")):
        raise ValueError("revision candidate belongs to another history")
    def stage(p):
        if p["status"] in {"accepted", "rejected"}:
            return 4
        if p.get("mr"):
            return 3
        if p.get("approval") or p.get("agentAssessment"):
            return 2
        return 1 if p.get("reviewPackage") else 0
    if stage(final) < stage(prior):
        raise ValueError("cannot roll back an already published revision stage")
    if stage(prior) >= 1 and final["candidateIconHash"] != prior["candidateIconHash"]:
        raise ValueError("revision final bytes changed after prior preparation")
    raw = final.get("sourceCandidate", final["candidate"])
    if raw != prior.get("sourceCandidate", prior["candidate"]):
        raise ValueError("revision raw source changed")
    allowed = {"lastAttemptAt", "lastAttemptResult", "lastVerifiedAt", "verifiedIconHash", "proposals", "reviewFeedback"}
    for key in set(before) | set(after) | set(current):
        if key not in allowed and not before.get(key) == after.get(key) == current.get(key):
            raise ValueError("revision cannot change unrelated per-bank state")
    return copy.deepcopy(after)
