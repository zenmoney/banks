"""One-shot, portable reconciliation of published proposal outcomes before selection.

Published service state is the historical input, not a temporary session or a PR
body pretending to be evidence. Only terminal provider facts are reconciled. A
stored candidate verification can carry over only to the identical accepted SVG.
All writes use the existing state-package authorization, validation and transport.
"""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import uuid

import github_pr
import state_publication as state
from evidence import baseline_blob, instant, require_hash, required

KIND = "proposal-outcomes"
_STABLE_MR = ("number", "url", "target", "head", "base", "title", "bodySha256")


def _commit(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value) is None:
        raise ValueError("expected an exact Git commit")
    return value


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _ancestor(objects, ancestor, descendant):
    return state.git(objects, "merge-base", "--is-ancestor", _commit(ancestor),
                     _commit(descendant), check=False).returncode == 0


def _fetch_ref(objects, target, ref):
    # The same command-local gh credential helper as asset publication; no config
    # writes, GUI, unlimited fetches, or dependency on an undeleted head branch.
    from asset_publication import _remote, _transport
    github_pr.normalize(target)
    if state.git(objects, "check-ref-format", ref, check=False).returncode:
        raise ValueError("invalid proposal Git ref")
    try:
        response = _transport(objects, "ls-remote", "--exit-code", _remote(target),
                              ref, check=False, timeout=60)
        rows = response.stdout.decode().splitlines()
        if response.returncode or len(rows) != 1 or rows[0].split()[1:] != [ref]:
            raise ValueError("proposal Git ref is unavailable or ambiguous")
        expected = _commit(rows[0].split()[0])
        pin = f"refs/bank-icon-maintenance/proposal-sync/{uuid.uuid4().hex}"
        _transport(objects, "fetch", *state.FETCH_OPTIONS, _remote(target),
                   f"{ref}:{pin}", timeout=60)
        actual = state.git(objects, "rev-parse", f"{pin}^{{commit}}").stdout.decode().strip()
    except subprocess.TimeoutExpired as exc:
        raise ValueError("proposal Git read timed out; preserve state and retry the read later") from exc
    if actual != expected:
        raise ValueError("proposal Git ref changed during fetch; prepare a fresh outcome package")
    return actual


def _proposal(before, *, clarification=False):
    proposals = before.get("proposals", [])
    if type(proposals) is not list or not proposals or any(type(p) is not dict for p in proposals):
        raise ValueError("published proposal history is incomplete")
    active = [p for p in proposals if p.get("status") in {"open", "pending"}
              or (p.get("mr") or {}).get("state") == "open"]
    proposal = proposals[-1]
    if clarification:
        if (active or proposal.get("status") != "rejected" or
                proposal.get("rejectionReason") is not None):
            raise ValueError("reason clarification needs the latest rejected proposal with an unknown reason")
    elif len(active) != 1 or active[0] is not proposal or proposal.get("status") != "open":
        raise ValueError("expected one latest published open proposal; reconcile other histories explicitly")
    mr = proposal.get("mr")
    if (type(mr) is not dict or mr.get("state") != ("closed" if clarification else "open") or
            type(mr.get("number")) is not int or mr["number"] < 1):
        raise ValueError("proposal lacks an exact published MR observation")
    target = github_pr.normalize(required(mr, "target"))
    if mr.get("url") != f"https://github.com/{target}/pull/{mr['number']}":
        raise ValueError("proposal MR URL does not bind its repository and number")
    for key in ("head", "base", "title"):
        required(mr, key)
    _commit(mr.get("headCommit"))
    _commit(mr.get("baseCommit"))
    require_hash(mr.get("bodySha256"), "published MR body")
    if "baseIconHash" not in proposal:
        raise ValueError("published proposal lacks an explicit base SVG binding")
    if proposal["baseIconHash"] is not None:
        require_hash(proposal["baseIconHash"], "published base SVG")
    require_hash(proposal.get("candidateIconHash"), "published candidate SVG")
    instant(mr.get("observedAt"), "published MR observedAt")
    instant(proposal.get("checkedAt"), "published attempt checkedAt")
    return proposal


def _provider(proposal):
    mr = proposal["mr"]
    observed = github_pr.outcome(mr["target"], mr["number"])
    if any(observed.get(key) != mr.get(key) for key in _STABLE_MR):
        raise ValueError("provider MR identity/title/body differs from the published proposal")
    if mr["state"] != "open":
        previous = proposal.get("mrOutcome", mr)
        keys = ("state", "headCommit", "mergeCommit", "mergedAt", "closedAt")
        if any(key in previous and observed.get(key) != previous[key] for key in keys):
            raise ValueError("terminal MR changed or reopened; no automatic state rewrite")
    return observed


def _bank(objects, commit, path, id):
    from asset_publication import _blob, _bank_base
    metadata = _blob(objects, commit, path.removesuffix("icon.svg") + "info.json")
    info = json.loads(metadata)
    if (type(info) is not dict or type(info.get("id")) is not int or info["id"] != id or
            not isinstance(info.get("title"), str)):
        raise ValueError("proposal bank identity changed")
    present = state.git(objects, "ls-tree", "-z", commit, "--", path).stdout
    icon_hash = state.digest(_blob(objects, commit, path)) if present else None
    metadata_hash = state.digest(metadata)
    _bank_base(objects, commit, {"id": id, "folder": path.rsplit("/", 1)[0],
                                "iconHash": icon_hash, "metadataHash": metadata_hash})
    return {"commit": commit, "iconHash": icon_hash, "metadataHash": metadata_hash}


def _merge_proof(objects, id, proposal, observed):
    from asset_publication import _blob
    mr = proposal["mr"]
    current = _fetch_ref(objects, mr["target"], f"refs/heads/{mr['base']}")
    head = _fetch_ref(objects, mr["target"], f"refs/pull/{mr['number']}/head")
    reviewed = _commit(mr["headCommit"])
    merge = _commit(observed["mergeCommit"])
    if head != observed["headCommit"] or not _ancestor(objects, reviewed, head):
        raise ValueError("PR head changed non-descendently or raced with its provider observation")
    if not _ancestor(objects, merge, current):
        raise ValueError("merge commit is not in current target history")
    parents = state.git(objects, "rev-list", "--parents", "-n", "1", reviewed).stdout.split()
    if len(parents) != 2:
        raise ValueError("reviewed asset commit must have exactly one parent")
    parent = parents[1].decode()
    paths = state.git(objects, "diff", "--no-renames", "--name-only", "-z", parent, reviewed).stdout.split(b"\0")[:-1]
    if len(paths) != 1:
        raise ValueError("reviewed asset commit does not contain exactly one bank SVG change")
    path = paths[0].decode("utf-8")
    match = re.fullmatch(r"banks/[^/]+_([0-9]+)-(?:[a-zA-Z]{2}|unknown)/icon\.svg", path)
    if match is None or int(match[1]) != id:
        raise ValueError("reviewed change does not bind selected Company.id")
    from feedback_revision import baseline_parent
    parent = baseline_parent(objects, proposal, reviewed, parent, path)
    original = _bank(objects, parent, path, id)
    final = _bank(objects, reviewed, path, id)
    baseline_blob(objects, parent, path, proposal["baseIconHash"])
    baseline_blob(objects, reviewed, path, proposal["candidateIconHash"])
    if (original["iconHash"] != proposal["baseIconHash"] or
            final["iconHash"] != proposal["candidateIconHash"] or
            original["metadataHash"] != final["metadataHash"]):
        raise ValueError("published base/candidate hashes differ from the reviewed Git commit")
    if head != reviewed:
        changed = state.git(objects, "diff", "--no-renames", "--name-only", "-z", reviewed, head).stdout.split(b"\0")[:-1]
        if changed != [paths[0]]:
            raise ValueError("amended head changes more than the reviewed bank SVG")
    accepted = _bank(objects, merge, path, id)
    require_hash(accepted["iconHash"], "accepted SVG")
    live = _bank(objects, current, path, id)
    if state.digest(_blob(objects, head, path)) != accepted["iconHash"]:
        raise ValueError("merge result differs from the PR head SVG")
    if accepted["metadataHash"] != final["metadataHash"]:
        raise ValueError("merged bank metadata differs from the reviewed identity")
    return {"path": path, "reviewedCommit": reviewed, "accepted": accepted,
            "current": live, "target": mr["target"], "ref": f"refs/heads/{mr['base']}"}


def _reason(proposal, decision, at):
    if isinstance(decision, dict) and "review" in decision:
        from rejection_review import validate
        return validate(decision, proposal, at)
    if type(decision) is not dict or decision.keys() != {"id", "mr", "commentUrl", "category", "explanation"}:
        raise ValueError("rejection decision needs exact id, mr, commentUrl, category and explanation")
    mr = proposal["mr"]
    if (decision["mr"] != {key: mr[key] for key in ("number", "url")} or
            decision["category"] not in {"identity/logo", "technical", "not-now"}):
        raise ValueError("rejection decision is for another MR or category")
    explanation = required(decision, "explanation")
    comment = github_pr.comment(mr["target"], mr["number"], decision["commentUrl"])
    if (explanation.casefold() not in comment["body"].casefold() or
            instant(comment["createdAt"], "comment.createdAt") <
            instant(proposal["checkedAt"], "proposal.checkedAt") or
            instant(comment["updatedAt"], "comment.updatedAt") > instant(at, "observedAt")):
        raise ValueError("human comment does not support this proposal's rejection reason")
    return {"comment": comment, "mr": decision["mr"],
            "candidateIconHash": proposal["candidateIconHash"],
            "category": decision["category"], "explanation": explanation}


def _transition(before, observed, proof, source_commit, at, reason=None, *, clarification=False, reviewed=False):
    """Derive the entire after-state; never accept an agent-authored replacement state."""
    proposal = _proposal(before, clarification=clarification)
    mr = proposal["mr"]
    when = instant(at, "sync observedAt")
    if when < instant(mr["observedAt"], "prior observedAt") or when > datetime.now(timezone.utc):
        raise ValueError("outcome observation is outside the prior-to-command interval")
    if observed["state"] not in {"merged", "closed"}:
        raise ValueError("only terminal MRs can produce a synchronized state change")
    closed = instant(observed["closedAt"], "MR.closedAt")
    if closed > when or (not clarification and closed < instant(mr["observedAt"], "prior observedAt")):
        raise ValueError("MR closure is outside the open-to-outcome observation interval")
    after = copy.deepcopy(before)
    result = after["proposals"][-1]
    if not clarification:
        if "mrHistory" in proposal or "mrOutcome" in proposal:
            raise ValueError("open proposal already contains a terminal outcome; reconcile explicitly")
        result["mrHistory"] = [copy.deepcopy(mr)]
        result["mr"] = {**{key: observed[key] for key in mr if key != "observedAt"}, "observedAt": at}
        result["mrOutcome"] = copy.deepcopy(observed)
    # This commit-addressed receipt is portable. Relative paths inside the old
    # proposal are historical references, not paths to resolve on this machine.
    receipt = {"schemaVersion": 1, "sourceStateCommit": source_commit, "observedAt": at,
               "provider": copy.deepcopy(observed), "proof": copy.deepcopy(proof)}
    if clarification:
        if observed["state"] != "closed" or (reason is None and not reviewed):
            raise ValueError("clarification needs a closed MR and a sourced human reason")
        if "outcomeSync" in result:
            result.setdefault("outcomeSyncHistory", []).append(copy.deepcopy(result["outcomeSync"]))
    if observed["state"] == "closed":
        result.update(status="rejected", rejectionReason=copy.deepcopy(reason))
        receipt["currentVerification"] = "not-renewed"
    else:
        if reason is not None or clarification or type(proof) is not dict:
            raise ValueError("merged MR requires exact Git proof, not a rejection decision")
        accepted, current = proof["accepted"], proof["current"]
        require_hash(accepted["iconHash"], "accepted SVG")
        result.update(status="accepted", acceptedIconHash=accepted["iconHash"],
                      acceptedHeadCommit=observed["headCommit"])
        verification = proposal.get("verification")
        reusable = (accepted["iconHash"] == current["iconHash"] == proposal["candidateIconHash"] and
                    accepted["metadataHash"] == current["metadataHash"] and
                    type(verification) is dict and verification.get("sourceSha256") == accepted["iconHash"])
        if reusable:
            require_hash(verification.get("xmlSha256"), "published verification XML")
            required(verification, "converter")
            required(verification, "compose")
            checked = instant(verification.get("checkedAt"), "candidate verification.checkedAt")
            if not (instant(proposal["checkedAt"], "proposal.checkedAt") <= checked <=
                    instant(mr["observedAt"], "MR.observedAt") <= when):
                raise ValueError("published candidate verification times are inconsistent")
            # Never roll a newer current verification backwards.
            if not after.get("lastVerifiedAt") or instant(after["lastVerifiedAt"], "lastVerifiedAt") <= checked:
                after.update(lastVerifiedAt=verification["checkedAt"], verifiedIconHash=accepted["iconHash"])
            receipt["currentVerification"] = "retained-candidate-verification"
        else:
            receipt["currentVerification"] = "requires-current-check"
    result["outcomeSync"] = receipt
    return after


def _decisions(path):
    if path is None:
        return {}
    rows = json.loads(state.regular(Path(path).absolute()))
    if type(rows) is not list or any(type(row) is not dict or type(row.get("id")) is not int
                                   or row["id"] < 0 for row in rows):
        raise ValueError("decisions must be an array with integer bank IDs")
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("duplicate rejection decision IDs")
    return {row["id"]: row for row in rows}


def _context(session):
    path = session / "session.json"
    data = json.loads(state.regular(path))
    if (type(data) is not dict or data.keys() != {"schemaVersion", "purpose", "repo", "baseCommit",
            "stateRemote", "stateCommit", "selectedIds"} or type(data["schemaVersion"]) is not int or
            data["schemaVersion"] != 1 or
            data["purpose"] != KIND or type(data["selectedIds"]) is not list or
            any(type(id) is not int or id < 0 for id in data["selectedIds"]) or
            len(set(data["selectedIds"])) != len(data["selectedIds"])):
        raise ValueError("not an immutable proposal-sync context")
    _commit(data["baseCommit"])
    _commit(data["stateCommit"])
    if not Path(data["repo"]).is_absolute() or state.remote_target(data["stateRemote"]) != data["stateRemote"]:
        raise ValueError("invalid proposal-sync checkout or destination")
    return data, {str(path): state.digest(state.regular(path))}


def authorization_context(session, package):
    reconciliation = package["reconciliation"]
    data, bindings = _context(session)
    if reconciliation != {"kind": KIND}:
        if type(reconciliation) is not dict or reconciliation.keys() != {"kind", "context"}:
            raise ValueError("invalid outcome batch context")
        link = reconciliation["context"]
        if (reconciliation["kind"] != KIND or type(link) is not dict or
                link.keys() != {"path", "sha256"} or
                link.get("path") != f"outcome-contexts/batch-{link.get('sha256')}.json"):
            raise ValueError("invalid outcome context link")
        require_hash(link["sha256"], "outcome context")
        content = state.regular(session / link["path"])
        if state.digest(content) != link["sha256"]:
            raise ValueError("outcome context bytes changed")
        context = json.loads(content)
        if (type(context) is not dict or context.keys() != data.keys() or
                any(context[k] != data[k] for k in data if k != "selectedIds") or
                type(context["selectedIds"]) is not list or not context["selectedIds"] or
                any(type(id) is not int or id < 0 for id in context["selectedIds"]) or
                len(set(context["selectedIds"])) != len(context["selectedIds"])):
            raise ValueError("outcome batch does not bind the original scan")
        data = context
        bindings[str(session / link["path"])] = link["sha256"]
    if (package["bindings"] != bindings or data["selectedIds"] != package["ids"] or
            data["baseCommit"] != package["baseCommit"] or data["stateRemote"] != package["target"] or
            data["stateCommit"] != package["observedRef"]):
        raise ValueError("proposal-sync authority does not bind the prepared context")
    return data


def prepare(args):
    from outcome_scan import prepare as prepare_scan
    return prepare_scan(args)


def validate_package(session, package, objects, head):
    # Bound the live revalidation too; a prepared batch must not reintroduce an
    # unbounded pre-push sweep. No write has occurred when this deadline expires.
    import git_catalog
    import operation_budget as budget
    try:
        with git_catalog.scope(), budget.bounded(180):
            return _validate_package(session, package, objects, head)
    except budget.Exhausted as exc:
        raise ValueError("outcome package live revalidation exceeded its read budget; "
                         "no push attempted, retain package and diagnose the slow read") from exc


def _validate_package(session, package, objects, head):
    """Re-derive all effects from the pinned remote before-state immediately before push."""
    authorization_context(session, package)
    source = _commit(package["observedRef"])
    if (package["stateHistory"] != "present" or not _ancestor(objects, source, head) or
            type(package["results"]) is not list or len(package["results"]) != len(package["records"])):
        raise ValueError("outcome package source history or result bindings changed")
    for row, result in zip(package["records"], package["results"]):
        if (type(result) is not dict or result.keys() != {"id", "observed", "proof", "observedAt", "decision", "clarification"}
                or result["id"] != row["id"] or type(result["clarification"]) is not bool):
            raise ValueError("outcome package has invalid per-bank bindings")
        before, digest = state.remote_record(objects, source, row["path"])
        if before != row["before"] or digest != row["beforeSha256"]:
            raise ValueError("outcome package rewrites the published source state")
        proposal = _proposal(before, clarification=result["clarification"])
        observed = _provider(proposal)
        # Unrelated target commits may advance the provider's base SHA. Every
        # other provider fact, including the terminal state/head, stays exact.
        if {k: v for k, v in observed.items() if k != "baseCommit"} != {
                k: v for k, v in result["observed"].items() if k != "baseCommit"}:
            raise ValueError("MR outcome changed after preparation; show a new package")
        proof = result["proof"]
        if observed["state"] == "merged":
            live = _merge_proof(objects, row["id"], proposal, observed)
            if (type(proof) is not dict or
                    {k: v for k, v in live.items() if k != "current"} != {k: v for k, v in proof.items() if k != "current"} or
                    {k: v for k, v in live["current"].items() if k != "commit"} !=
                    {k: v for k, v in proof["current"].items() if k != "commit"} or
                    not _ancestor(objects, proof["current"]["commit"], live["current"]["commit"])):
                raise ValueError("current bank changed since outcome preparation; prepare a new package")
        elif proof is not None:
            raise ValueError("closed MR must not carry merge proof")
        decision = result["decision"]
        if decision is not None and decision.get("id") != row["id"]:
            raise ValueError("rejection decision is for another bank")
        reason = _reason(proposal, decision, result["observedAt"]) if decision is not None else None
        after = _transition(before, result["observed"], proof, source, result["observedAt"],
                            reason, clarification=result["clarification"],
                            reviewed=isinstance(decision, dict) and "review" in decision)
        from rejection_review import annotate
        annotate(after, decision)
        if row["after"] != after:
            raise ValueError("outcome package differs from the validated terminal transition")
