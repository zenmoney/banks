"""Bounded, resumable intake for proposal_sync's existing outcome validator.

Checkpoints are observations, never publication receipts. Each batch has its own
immutable context/package; the existing writer rechecks live facts before push.
"""
import json
import os
from pathlib import Path
import re
import sys
import subprocess
import tempfile
import time

import git_catalog
import operation_budget as budget
import rejection_review as reviews
import state_publication as state


def _json(path):
    return json.loads(state.regular(path))


def _save_hashed(directory, prefix, value):
    content = state.encoded(value)
    name = f"{prefix}-{state.digest(content)}.json"
    state.save_new(directory / name, content)
    return name


def _load_series(directory):
    values = []
    for path in sorted(directory.glob("*.json")):
        match = re.fullmatch(r"([0-9]{6})-([0-9a-f]{64})\.json", path.name)
        content = state.regular(path)
        if (match is None or int(match[1]) != len(values) or
                state.digest(content) != match[2]):
            raise ValueError("outcome checkpoint order/hash changed; preserve diagnostics")
        values.append((path.name, json.loads(content)))
    return values


def _progress(session, started, stage, **fields):
    value = {"kind": "proposal-outcomes-progress", "stage": stage,
             "elapsedSeconds": round(time.monotonic() - started, 3), **fields}
    # Diagnostic projection only; never read it to infer completed work.
    fd, name = tempfile.mkstemp(prefix="progress-", suffix=".tmp", dir=session)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(state.encoded(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, session / "progress.json")
    finally:
        Path(name).unlink(missing_ok=True)
    print(json.dumps(value, ensure_ascii=False), file=sys.stderr, flush=True)


def _one(objects, manifest, path, progress):
    import proposal_sync as sync
    row = {"path": path, "scanHash": state.digest(state.encoded(manifest)), "kind": "skip"}
    match = re.fullmatch(r"banks/state/(0|[1-9][0-9]*)\.json", path)
    if match is None:
        return {**row, "kind": "stop", "stop": {"path": path, "reason": "non-canonical service-state path"}}
    id = int(match[1])
    row["id"] = id
    try:
        before, before_hash = state.remote_record(objects, manifest["stateCommit"], path)
        proposals = before.get("proposals", [])
        decision = next((d for d in manifest["request"]["decisions"] if d["id"] == id), None)
        unknown_rejection = bool(proposals and proposals[-1].get("status") == "rejected"
                                 and proposals[-1].get("rejectionReason") is None)
        if not any(p.get("status") in {"open", "pending"} or
                   (p.get("mr") or {}).get("state") == "open" for p in proposals) and decision is None and not unknown_rejection:
            return row
        clarification = bool(proposals and proposals[-1].get("status") == "rejected")
        proposal = sync._proposal(before, clarification=clarification)
        progress("provider", id=id, mr=proposal["mr"]["url"])
        observed = sync._provider(proposal)
        if observed["state"] == "open":
            if decision is not None:
                raise ValueError("a rejection decision cannot resolve an open MR")
            return {**row, "kind": "open", "open": {"id": id, "mr": observed["url"]}}
        if observed["state"] == "closed" and decision is None:
            progress("closed-discussion", id=id, mr=observed["url"])
            review = reviews.capture(proposal, observed)
            saved = proposal.get("rejectionReview")
            if saved is not None:
                reason = reviews.validate(saved, proposal, saved["evaluatedAt"], live=False)
                if reason != proposal.get("rejectionReason"):
                    raise ValueError("saved closed-review reason differs from the proposal")
                if saved["review"]["key"] == review["key"]:
                    return {**row, "kind": "review-unchanged", "id": id}
            return {**row, "kind": "review", "before": before, "beforeSha256": before_hash,
                    "observed": observed, "stateCommit": manifest["stateCommit"],
                    "clarification": clarification, "review": review}
        progress("git-proof" if observed["state"] == "merged" else "closed-outcome", id=id, mr=observed["url"])
        proof = sync._merge_proof(objects, id, proposal, observed) if observed["state"] == "merged" else None
        at = sync._now()
        reason = sync._reason(proposal, decision, at) if decision is not None else None
        after = sync._transition(before, observed, proof, manifest["stateCommit"], at, reason,
                                 clarification=clarification)
        return {**row, "kind": "transition",
                "record": {"id": id, "path": path, "before": before, "beforeSha256": before_hash, "after": after},
                "result": {"id": id, "observed": observed, "proof": proof, "observedAt": at,
                           "decision": decision, "clarification": clarification}}
    except (ValueError, OSError, KeyError, TypeError, AttributeError, subprocess.TimeoutExpired) as exc:
        # A shared deadline is a separate Exception, never a completed bank stop.
        return {**row, "kind": "stop", "stop": {"id": id, "reason": str(exc)}}


def _package(session, output, manifest, rows):
    import proposal_sync as sync
    records = [r["record"] for r in rows if r["kind"] == "transition"]
    if not records:
        return None
    request = manifest["request"]
    context = {"schemaVersion": 1, "purpose": sync.KIND, "repo": request["repo"],
               "baseCommit": request["baseCommit"], "stateRemote": request["stateRemote"],
               "stateCommit": manifest["stateCommit"], "selectedIds": [r["id"] for r in records]}
    # Keep the original session root for state transport/local cache sync. Later
    # batches bind a different immutable context, not edits to session.json.
    if not (session / "session.json").exists():
        state.save_new(session / "session.json", state.encoded(context))
    name = _save_hashed(session / "outcome-contexts", "batch", context)
    link = {"path": f"outcome-contexts/{name}", "sha256": state.digest(state.encoded(context))}
    bindings = {str(session / "session.json"): state.digest(state.regular(session / "session.json")),
                str(session / link["path"]): link["sha256"]}
    package = {"schemaVersion": 1, "scope": "state-publication", "target": context["stateRemote"],
               "ref": state.REF, "baseCommit": context["baseCommit"], "stateHistory": "present",
               "observedRef": manifest["stateCommit"], "ids": context["selectedIds"], "records": records,
               "results": [r["result"] for r in rows if r["kind"] == "transition"],
               "reconciliation": {"kind": sync.KIND, "context": link}, "bindings": bindings}
    content = state.encoded(package)
    package_hash = state.digest(content)
    state.save_new(output / f"{package_hash}.json", content)
    return package_hash


def _summary(manifest, rows):
    values = [r for _, r in rows]
    complete = len(rows) == len(manifest["paths"])
    stops = [r["stop"] for r in values if r["kind"] == "stop"]
    used = {r["id"] for r in values if r["kind"] == "transition" and r["result"]["decision"] is not None}
    unused = {d["id"] for d in manifest["request"]["decisions"]} - used
    if complete and unused:
        stops.append({"ids": sorted(unused), "reason": "rejection decisions not applied"})
    return {"stateRemote": manifest["request"]["stateRemote"], "sourceStateCommit": manifest["stateCommit"],
            "open": [r["open"] for r in values if r["kind"] == "open"], "stops": stops,
            "scanComplete": complete, "processedPaths": len(rows), "totalPaths": len(manifest["paths"]),
            "remainingPaths": len(manifest["paths"]) - len(rows)}


def _pending(session, output, package_hash, summary):
    import proposal_sync as sync
    package = state.load_package(output, package_hash)
    state.check_bindings(package)
    sync.authorization_context(session, package)
    receipt_path = output / f"{package_hash}.receipt.json"
    receipt = _json(receipt_path) if receipt_path.exists() else {}
    return {**summary, "readyForSelection": False, "packageHash": package_hash, "package": package,
            "status": receipt.get("status", "awaiting-state-authorization"),
            "nextAction": "Settle this exact package via state-show/state-publish or read-only state-reconcile, then --resume."}


def prepare(args):
    import proposal_sync as sync
    started = time.monotonic()
    seconds = getattr(args, "max_seconds", 180)
    limit = getattr(args, "max_proposals", 5)
    if type(seconds) is not int or not 1 <= seconds <= 240 or type(limit) is not int or not 1 <= limit <= 25:
        raise ValueError("outcome limits require --max-seconds 1..240 and --max-proposals 1..25")
    repo = Path(args.repo).resolve(strict=True)
    if Path(state.git(repo, "rev-parse", "--show-toplevel").stdout.decode().strip()).resolve() != repo:
        raise ValueError("--repo must be a Git checkout root")
    raw_session = Path(args.session).absolute()
    if any(p.is_symlink() for p in (raw_session, *raw_session.parents)):
        raise ValueError("outcome session path cannot contain symlinks")
    session = raw_session.resolve()
    resume = getattr(args, "resume", False)
    review_input = getattr(args, "review_decisions", None)
    if review_input is not None and not resume:
        raise ValueError("--review-decisions resolves a saved scan; use --resume")
    supplied = reviews.inputs(review_input)
    if session == repo or repo in session.parents or (session.exists() and not resume):
        raise ValueError("proposal-sync --session must be a new path outside the checkout; use --resume for a saved scan")
    if resume and not (session / "outcome-scan" / "request.json").is_file():
        raise ValueError("no resumable outcome checkpoint; retain old diagnostics and use a new sibling sync path")
    target = state.resolve_remote(repo, args.remote, optional=True)
    if target is None:
        if resume:
            raise ValueError("outcome resume destination unavailable; keep the pinned target")
        return {"status": "no-origin", "stateHistory": "unknown", "packageHash": None,
                "nextAction": "No online outcome synchronization; only local maintenance is possible."}
    session.mkdir(parents=True, exist_ok=resume)
    with state.writer(session) as output, git_catalog.scope():
        journal = session / "outcome-scan"
        for directory in (journal, journal / "rows", journal / "batches", session / "outcome-contexts"):
            if directory.is_symlink():
                raise ValueError("outcome checkpoint directory cannot be a symlink")
            directory.mkdir(exist_ok=True)
        request_path = journal / "request.json"
        if resume:
            request = _json(request_path)
            if (request.get("schemaVersion") != 1 or request.get("repo") != str(repo) or
                    request.get("stateRemote") != target):
                raise ValueError("outcome resume checkout/destination differs from pinned input")
            if getattr(args, "decisions", None) and list(sync._decisions(args.decisions).values()) != request["decisions"]:
                raise ValueError("outcome resume cannot replace pinned decisions")
            if getattr(args, "base", None):
                base = state.git(repo, "rev-parse", "--verify", f"{args.base}^{{commit}}").stdout.decode().strip()
                if base != request["baseCommit"]:
                    raise ValueError("outcome resume cannot change the frozen base")
        else:
            base = state.git(repo, "rev-parse", "--verify", f"{getattr(args, 'base', None) or 'HEAD'}^{{commit}}").stdout.decode().strip()
            request = {"schemaVersion": 1, "repo": str(repo), "baseCommit": sync._commit(base),
                       "stateRemote": target, "decisions": list(sync._decisions(getattr(args, "decisions", None)).values())}
            state.save_new(request_path, state.encoded(request))
        next_command = [sys.executable, str(Path(__file__).with_name("session.py")), "state-prepare-outcomes",
                        "--repo", str(repo), "--session", str(session), "--remote", target, "--resume",
                        "--max-seconds", str(seconds), "--max-proposals", str(limit)]
        manifest_path = journal / "manifest.json"
        rows, batches, manifest = [], [], None
        exhausted = False
        start = 0
        initialized = False
        cursor = {}
        def progress(stage, **fields):
            cursor.update(fields)
            if stage not in {"budget-exhausted", "batch-complete"}:
                cursor["lastOperation"] = stage
            _progress(session, started, stage, processedPaths=len(rows),
                      totalPaths=len(manifest["paths"]) if manifest else None, **cursor)
        try:
            with budget.bounded(seconds):
                objects = state.object_repo(output)
                if manifest_path.exists():
                    manifest = _json(manifest_path)
                    if manifest.get("request") != request or type(manifest.get("paths")) is not list:
                        raise ValueError("outcome manifest differs from pinned request")
                    pin = (state.git(objects, "rev-parse", "refs/proposal-sync/source-state^{commit}").stdout.decode().strip()
                           if manifest["stateCommit"] is not None else None)
                    if pin != manifest["stateCommit"]:
                        raise ValueError("outcome source-state pin changed")
                else:
                    progress("fetch-state")
                    head = state.fetch_head(objects, target)
                    if head is None:
                        # An absent read is a completed observation, not empty history.
                        manifest = {"request": request, "stateCommit": None, "paths": []}
                    else:
                        state.git(objects, "update-ref", "refs/proposal-sync/source-state", head)
                        paths = state.git(objects, "ls-tree", "-r", "-z", "--name-only", head,
                                          "--", "banks/state/").stdout.split(b"\0")
                        manifest = {"request": request, "stateCommit": head,
                                    "paths": [p.decode("utf-8") for p in paths if p]}
                    state.save_new(manifest_path, state.encoded(manifest))
                if manifest["stateCommit"] is None:
                    return {"status": "absent", "stateHistory": "unknown", "packageHash": None,
                            "scanComplete": True, "readyForSelection": True,
                            "nextAction": "Start the new queue; no published proposal state was found."}
                rows = _load_series(journal / "rows")
                scan_hash = state.digest(state.encoded(manifest))
                for index, (_, row) in enumerate(rows):
                    if (index >= len(manifest["paths"]) or row.get("path") != manifest["paths"][index] or
                            row.get("scanHash") != scan_hash or row.get("kind") not in {"skip", "open", "stop", "transition", "review", "review-unchanged"}):
                        raise ValueError("outcome checkpoint source/path changed")
                batches = _load_series(journal / "batches")
                completed_packages = []
                for _, batch in batches:
                    end = batch.get("end")
                    if (batch.get("start") != start or type(end) is not int or not start < end <= len(rows) or
                            batch.get("rows") != [name for name, _ in rows[start:end]]):
                        raise ValueError("outcome batch range/bindings changed")
                    if batch["packageHash"] is not None:
                        pending = _pending(session, output, batch["packageHash"], _summary(manifest, rows))
                        reviews.check_batch(journal, rows[start:end], pending["package"])
                        if pending["status"] != "published":
                            return {**pending, "resumeCommand": next_command}
                        completed_packages.append(pending)
                    start = end
                if completed_packages:
                    progress("confirm-prior-publication")
                    current = state.fetch_head(objects, target)
                    for pending in completed_packages:
                        receipt = _json(output / f"{pending['packageHash']}.receipt.json")
                        if not state.landed(objects, current, receipt.get("commit"), pending["package"]):
                            raise ValueError("prior outcome publication is no longer provable; state-reconcile before continuing")
                initialized = True
                effective = reviews.resolve_rows(journal, rows, supplied)
                consumed = sum(r["kind"] != "skip" for _, r in rows[start:])
                while (len(rows) < len(manifest["paths"]) and consumed < limit
                       and not any(r["kind"] == "review" for _, r in effective)):
                    budget.check()
                    path = manifest["paths"][len(rows)]
                    row = _one(objects, manifest, path, progress)
                    name = _save_hashed(journal / "rows", f"{len(rows):06d}", row)
                    rows.append((name, row))
                    consumed += row["kind"] != "skip"
                    if row["kind"] != "skip" or len(rows) % 100 == 0:
                        progress("checkpoint", id=row.get("id"), result=row["kind"])
                reviews.resolve_rows(journal, rows, supplied)
        except budget.Exhausted:
            exhausted = True
        # Package/checkpoint finalization is local and atomic, outside the read
        # deadline. It never sends a write or promotes an unfinished observation.
        if not initialized:
            progress("budget-exhausted")
            return {"status": "incomplete", "packageHash": None, "scanComplete": False,
                    "readyForSelection": False, "resumeCommand": next_command,
                    "nextAction": "Resume the saved read; do not select a queue or restart in a fresh directory."}
        effective = reviews.resolve_rows(journal, rows, supplied, allow_new=False)
        summary = {**_summary(manifest, effective), **reviews.pending(session, effective)}
        end, active = start, 0
        while end < len(effective) and active < limit and effective[end][1]["kind"] != "review":
            active += effective[end][1]["kind"] != "skip"
            end += 1
        if end > start:
            package_hash = _package(session, output, manifest, [r for _, r in effective[start:end]])
            batch = {"start": start, "end": end, "rows": [name for name, _ in rows[start:end]],
                     "packageHash": package_hash}
            _save_hashed(journal / "batches", f"{len(batches):06d}", batch)
        else:
            package_hash = None
        settled = summary["scanComplete"] and end == len(rows) and not summary.get("pendingReviews")
        summary["unpackagedPaths"] = len(rows) - end
        if summary["scanComplete"] and not summary.get("pendingReviews"):
            state.save_new(session / "scan.json", state.encoded(_summary(manifest, effective)))
        progress("budget-exhausted" if exhausted else "batch-complete", scanComplete=summary["scanComplete"])
        if package_hash:
            return {**_pending(session, output, package_hash, summary), "resumeCommand": next_command}
        return {**summary, "status": ("awaiting-review-decisions" if summary.get("pendingReviews") else
                                      "no-state-changes" if settled else "incomplete"),
                "packageHash": None, "readyForSelection": settled, "resumeCommand": next_command,
                "nextAction": (summary["reviewNextAction"] if summary.get("pendingReviews") else
                               "Outcome scan settled; proceed to feedback intake and queue selection." if settled else
                               "Resume remaining pinned work; do not select a queue from an incomplete scan.")}
