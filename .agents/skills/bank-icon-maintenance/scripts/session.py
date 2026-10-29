#!/usr/bin/env python3
"""Start, resume and record current-icon evidence in an isolated local session.

Command results are JSON; --help is text. Remote writes require explicit authorization.
"""
import argparse
from datetime import datetime, timezone
import hashlib
from functools import lru_cache
from types import MappingProxyType
import json
from pathlib import Path
import re
import subprocess
import sys


def emit_json(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


if sys.version_info < (3, 10):
    emit_json({"error": "Python 3.10 or newer is required"})
    raise SystemExit(1)

from evidence import artifact, instant, render_binding, sha, validate_attempt, validate_review
from evidence import baseline_blob

class SessionError(ValueError):
    pass


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise SessionError(f"invalid arguments: {message}")


def git(repo, *args, check=True, text=True):
    from operation_budget import timeout as budget_timeout, check as budget_check
    try:
        result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                                text=text, timeout=budget_timeout())
    except subprocess.TimeoutExpired:
        budget_check()
        raise
    if check and result.returncode:
        raise SessionError(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result


def integer(value):
    return type(value) is int


def policy_at(repo, base):
    path = "bank-icon-maintenance.json"
    mode = git(repo, "ls-tree", base, "--", path).stdout.strip().split()
    if not mode or mode[0] not in ("100644", "100755"):
        raise SessionError(f"Missing or invalid policy {path} at {base}: expected committed regular file")
    response = git(repo, "show", f"{base}:{path}")
    try:
        policy = json.loads(response.stdout)
    except ValueError as exc:
        raise SessionError(f"Invalid policy {path} at {base}: {exc}") from exc
    keys = ("recheckAfterDays", "retryUnconfirmedAfterDays", "retryErrorAfterDays",
            "selectionLimit")
    if (not isinstance(policy, dict) or not integer(policy.get("schemaVersion"))
            or policy["schemaVersion"] != 1 or any(
                not integer(policy.get(key)) or policy[key] <= 0 for key in keys)):
        raise SessionError(f"Invalid policy {path} at {base}: expected schemaVersion 1 and positive integer intervals/limit")
    return policy


@lru_cache(maxsize=16)
def _catalog_tree(worktree, commit):
    """Cache only immutable Git entries, including trees and nonregular files.

    Worktree contents and current HEAD are always checked again by catalog().
    Failed tree reads never produce a cache entry or establish absence.
    """
    rows = git(worktree, "ls-tree", "-r", "-t", "-z", commit, "--", "banks/", text=False).stdout
    result = {}
    for row in rows.split(b"\0"):
        if not row:
            continue
        header, path = row.split(b"\t", 1)
        mode, kind, object_id = header.split()
        result[path.decode("utf-8", errors="surrogateescape")] = (mode, kind, object_id.decode("ascii"))
    return MappingProxyType(result)


def _matches_blob(content, object_id):
    if object_id is None or len(object_id) not in (40, 64):
        return False
    # Git object framing is separate from the SHA-256 evidence/review digest.
    framed = b"blob " + str(len(content)).encode("ascii") + b"\0" + content
    algorithm = "sha1" if len(object_id) == 40 else "sha256"
    return hashlib.new(algorithm, framed, usedforsecurity=False).hexdigest() == object_id


def catalog(worktree):
    """Read every committed-base catalog entry; errors only taint affected IDs."""
    entries = {}
    errors = {}
    commit = git(worktree, "rev-parse", "--verify", "HEAD^{commit}").stdout.strip()
    tree = _catalog_tree(str(worktree.resolve()), commit)
    committed = {path: object_id for path, (mode, kind, object_id) in tree.items()
                 if mode in (b"100644", b"100755") and kind == b"blob"}
    root = worktree / "banks"
    if root.is_symlink():
        raise SessionError("catalog banks/ symlink cannot be read outside the selected base")
    for folder in sorted(root.iterdir()):
        match = re.fullmatch(r"(.+)_([0-9]+)-([a-zA-Z]{2}|unknown)", folder.name)
        folder_id = int(match.group(2)) if match else None
        if folder.is_symlink():
            if folder_id is not None:
                errors.setdefault(folder_id, []).append("catalog folder symlink is unsupported")
            continue
        if not folder.is_dir():
            continue
        metadata_id = None
        try:
            if (folder / "info.json").is_symlink() or (folder / "icon.svg").is_symlink():
                raise ValueError("catalog info.json or icon.svg symlink is unsupported")
            metadata_bytes = (folder / "info.json").read_bytes()
            data = json.loads(metadata_bytes)
            if isinstance(data, dict) and integer(data.get("id")):
                metadata_id = data["id"]
            if not isinstance(data, dict) or metadata_id is None or not isinstance(data.get("title"), str):
                raise ValueError("id must be integer and title string")
            for field in ("url", "fullTitle", "countryCode"):
                if field in data and data[field] is not None and not isinstance(data[field], str):
                    raise ValueError(f"{field} must be string, null or absent")
            id = data["id"]
            problem = None
            if not match or folder_id != id:
                problem = "folder ID does not match Company.id"
            elif data.get("countryCode") is not None and data["countryCode"].lower() != match.group(3).lower():
                problem = "folder country does not match countryCode"
            icon = folder / "icon.svg"
            if icon.exists() and not icon.is_file():
                raise ValueError("catalog icon.svg is not a regular file")
            icon_bytes = icon.read_bytes() if icon.is_file() else None
            if icon_bytes is None and committed.get(f"{folder.relative_to(worktree).as_posix()}/icon.svg") is not None:
                problem = problem or "tracked current icon.svg was deleted from checkout"
            relative = folder.relative_to(worktree).as_posix()
            if icon_bytes is None and f"{relative}/icon.svg" in tree and f"{relative}/icon.svg" not in committed:
                raise ValueError("committed icon.svg is not a regular file")
            for name, content in (("info.json", metadata_bytes), ("icon.svg", icon_bytes)):
                if content is not None and not _matches_blob(content, committed.get(f"{relative}/{name}")):
                    problem = problem or (
                        f"catalog {name} checkout bytes differ from the committed blob; "
                        "check local edits and Git attributes/filters, then start a fresh session. "
                        "Do not rewrite existing evidence or approve normalized bytes")
            warnings = []
            if match and match.group(1) != data["title"]:
                warnings.append("folder title differs from metadata title")
            entry = {"id": id, "title": data["title"], "folder": str(folder.relative_to(worktree)),
                     "metadata": data, "metadataHash": hashlib.sha256(metadata_bytes).hexdigest(),
                     "iconHash": hashlib.sha256(icon_bytes).hexdigest() if icon_bytes is not None else None,
                     "warnings": warnings, "problem": problem}
            entries.setdefault(id, []).append(entry)
            if folder_id is not None and folder_id != id:
                errors.setdefault(folder_id, []).append("folder ID does not match Company.id")
        except (OSError, ValueError) as exc:
            for id in {folder_id, metadata_id} - {None}:
                errors.setdefault(id, []).append(f"invalid catalog metadata: {exc}")
    return entries, errors


def read_state(repo, commit, paths, id):
    path = f"banks/state/{id}.json"
    if commit is None or path not in paths:
        return None, None
    response = git(repo, "show", f"{commit}:{path}", check=False)
    if response.returncode:
        return None, f"state file {path} cannot be read: {response.stderr.strip()}"
    try:
        state = json.loads(response.stdout)
        if not isinstance(state, dict) or type(state.get("schemaVersion")) is not int or state["schemaVersion"] != 1:
            raise ValueError("expected schemaVersion 1 object")
        attempt = state.get("lastAttemptAt")
        outcome = state.get("lastAttemptResult")
        if (attempt is None) != (outcome is None):
            raise ValueError("lastAttemptAt and lastAttemptResult must occur together")
        if outcome is not None and outcome not in ("verified", "unconfirmed", "candidate", "error", "needs-human"):
            raise ValueError("unsupported lastAttemptResult")
        verified = state.get("lastVerifiedAt")
        verified_hash = state.get("verifiedIconHash")
        if (verified is None) != (verified_hash is None):
            raise ValueError("lastVerifiedAt and verifiedIconHash must occur together")
        for name, value in (("lastAttemptAt", attempt), ("lastVerifiedAt", verified)):
            if value is not None:
                parse_time(value)
        if verified_hash is not None and (not isinstance(verified_hash, str) or
                                           not re.fullmatch(r"[a-fA-F0-9]{64}", verified_hash)):
            raise ValueError("verifiedIconHash must be SHA-256 hex")
        proposals = state.get("proposals", [])
        if not isinstance(proposals, list):
            raise ValueError("proposals must be an array")
        for proposal in proposals:
            if not isinstance(proposal, dict) or proposal.get("status") not in ("pending", "open", "accepted", "rejected"):
                raise ValueError("unsupported proposal status")
            for hash_field in ("baseIconHash", "candidateIconHash"):
                if hash_field == "baseIconHash" and proposal.get(hash_field) is None:
                    continue
                if hash_field in proposal and (not isinstance(proposal[hash_field], str) or
                                                not re.fullmatch(r"[a-fA-F0-9]{64}", proposal[hash_field])):
                    raise ValueError(f"invalid proposal {hash_field}")
            mr = proposal.get("mr")
            if mr is not None and (not isinstance(mr, dict) or mr.get("state") not in ("open", "merged", "closed")):
                raise ValueError("unsupported proposal MR state")
            reason = proposal.get("rejectionReason")
            if reason is not None and not isinstance(reason, (str, dict)):
                raise ValueError("rejectionReason must be a string, structured source or null")
            if type(reason) is dict:
                comment = reason.get("comment")
                mr_binding = reason.get("mr")
                if (reason.get("category") not in ("identity/logo", "technical", "not-now") or
                    not isinstance(reason.get("explanation"), str) or not reason["explanation"].strip() or
                    not isinstance(comment, dict) or not isinstance(comment.get("url"), str) or
                    not isinstance(comment.get("body"), str) or not isinstance(comment.get("author"), str) or
                    not isinstance(mr_binding, dict) or mr_binding.get("number") != (mr or {}).get("number") or
                    mr_binding.get("url") != (mr or {}).get("url") or
                    reason.get("candidateIconHash") != proposal.get("candidateIconHash")):
                    raise ValueError("structured rejection reason is not bound to exact proposal")
                parse_time(comment.get("createdAt"))
                parse_time(comment.get("updatedAt"))
            if "rejectionReview" in proposal:
                from rejection_review import validate as validate_rejection_review
                review = proposal["rejectionReview"]
                if not isinstance(review, dict):
                    raise ValueError("invalid closed-review assessment")
                if (proposal["status"] != "rejected" or review.get("id") != id or
                        validate_rejection_review(review, proposal, review["evaluatedAt"], live=False) != reason):
                    raise ValueError("closed-review assessment is not bound to this rejected proposal")
            history = proposal.get("mrHistory")
            if history is not None and (not isinstance(history, list) or len(history) != 1 or
                    not isinstance(history[0], dict) or history[0].get("state") != "open" or
                    (mr or {}).get("state") not in ("merged", "closed")):
                raise ValueError("terminal MR history is incomplete")
            if "reconsideration" in proposal:
                reconsider = proposal["reconsideration"]
                if (not isinstance(reconsider, dict) or reconsider.get("status") != "search-eligible" or
                    not isinstance(reason, dict) or reconsider.get("category") != reason["category"] or
                    not isinstance(reconsider.get("sourceUrls"), list) or
                    (reason["category"] == "identity/logo" and not reconsider["sourceUrls"]) or
                    (attempt is None or parse_time(reconsider.get("checkedAt")) > parse_time(attempt)) or
                    not isinstance(reconsider.get("currentBase"), dict)):
                    raise ValueError("reconsideration is not bound to known rejection and later attempt")
                parse_time(reconsider["checkedAt"])
        from feedback_intake import requests
        requests(state)
        from task_work import validate_history
        validate_history(state)
        return state, None
    except (ValueError, TypeError, KeyError, AttributeError, SessionError) as exc:
        return None, f"invalid state {path}: {exc}"


def verified_hash(state):
    """Normalize an optional verified hash; explicit null is not verification."""
    value = state.get("verifiedIconHash")
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", value):
        raise SessionError("verifiedIconHash must be SHA-256 hex or null")
    return value.lower()


def check_due(state, icon_hash, now, policy):
    attempt = state.get("lastAttemptAt")
    outcome = state.get("lastAttemptResult")
    pending = outcome == "candidate" and any(p.get("status") == "pending" and not p.get("mr")
                                            for p in state.get("proposals", []))
    if attempt and (outcome in ("unconfirmed", "error") or pending):
        delay = policy["retryErrorAfterDays"] if outcome == "error" or pending else policy["retryUnconfirmedAfterDays"]
        if (now - parse_time(attempt)).total_seconds() < delay * 86400:
            return False
        if pending or any(row.get("completion") and row.get("processedAt") == attempt
                                         for row in state.get("taskResults", [])):
            return True  # A later candidate stop uses its retry interval, not older verification freshness.
    if state.get("lastVerifiedAt") and verified_hash(state) is not None and verified_hash(state) == icon_hash:
        if (now - parse_time(state["lastVerifiedAt"])).total_seconds() < policy["recheckAfterDays"] * 86400:
            return False
    return True


def state_decision(state, icon_hash, now, policy, explicit):
    if state is None:
        return None, True, None
    proposals = state.get("proposals", [])
    for proposal in proposals:
        mr = proposal.get("mr")
        if proposal["status"] == "accepted" and (not mr or mr["state"] != "merged"):
            return "blocked", False, "accepted proposal lacks merged MR; reconcile before searching"
        if proposal["status"] == "rejected" and mr and mr["state"] == "merged":
            return "blocked", False, "rejected proposal conflicts with merged MR"
    from feedback_revision import selection as feedback_selection
    try:
        feedback = feedback_selection(state, icon_hash)
    except ValueError as exc:
        return "needs-human", False, str(exc)
    if feedback is not None:
        return feedback
    active = [p for p in proposals if p["status"] in ("pending", "open") or
              (p.get("mr") and p["mr"]["state"] == "open")]
    if len(active) > 1:
        return "blocked", False, "multiple active proposals require human reconciliation"
    if active:
        proposal = active[0]
        if "baseIconHash" in proposal and (proposal["baseIconHash"].lower() if isinstance(proposal["baseIconHash"], str) else proposal["baseIconHash"]) != icon_hash:
            return "needs-human", False, "current base icon changed; reassess proposal before reuse"
        if proposal["status"] in ("accepted", "rejected"):
            return "blocked", False, "proposal status conflicts with open MR"
        if proposal["status"] == "open" and not (proposal.get("mr") and proposal["mr"]["state"] == "open"):
            return "blocked", False, "open proposal has no open MR"
        if proposal["status"] == "pending" and proposal.get("mr"):
            return "blocked", False, "pending proposal has MR; transition is unsupported"
        return ("waiting-mr" if proposal["status"] == "open" else "waiting-proposal"), False, "active proposal awaits its dedicated continuation; no new automatic search (manual search may be requested)"
    if state.get("lastAttemptResult") == "needs-human" and any(
            row.get("completion") and row.get("processedAt") == state.get("lastAttemptAt")
            for row in state.get("taskResults", [])):
        return "needs-human", False, "completed candidate requires the recorded human decision; no timed retry"
    latest = proposals[-1] if proposals else None
    if (latest and latest["status"] == "accepted" and
            (verified_hash(state) is None or verified_hash(state) != icon_hash)):
        # Acceptance is historical; a failed or missing check of the live SVG
        # must not inherit the candidate's verification or block a fresh check.
        if not explicit and state.get("lastAttemptResult") in ("error", "unconfirmed", "needs-human"):
            delay = (policy["retryErrorAfterDays"] if state["lastAttemptResult"] == "error"
                     else policy["retryUnconfirmedAfterDays"])
            if (now - parse_time(state["lastAttemptAt"])).total_seconds() < delay * 86400:
                return None, False, None
        return None, True, "accepted proposal remains historical; check exact current SVG before any replacement"
    rejected = latest is not None and latest["status"] == "rejected"
    released = (rejected and type(latest.get("rejectionReason")) is dict and
                type(latest.get("reconsideration")) is dict and
                latest["reconsideration"].get("status") == "search-eligible" and
                latest["reconsideration"].get("currentBase", {}).get("iconHash") == icon_hash)
    if released:
        if explicit:
            return None, True, "new relevant evidence permits a fresh current-base search; rejected proposal remains historical"
        if (now - parse_time(state["lastAttemptAt"])).total_seconds() < policy["retryUnconfirmedAfterDays"] * 86400:
            return None, False, None
        return None, True, "new relevant evidence permits selection; do not reuse rejected proposal"
    if rejected:
        reason = latest.get("rejectionReason")
        review = latest.get("rejectionReview") or {}
        action = (reason.get("action") if isinstance(reason, dict) else
                  review.get("judgement", {}).get("action"))
        messages = {
            "keep-current": "previous proposal rejected: keep the current icon; no automatic replacement retry",
            "repair": "previous proposal rejected: recorded repair requirements need relevant evidence and reconsideration",
            "verify-applicability": "previous proposal rejected: recorded applicability concern requires verification before reconsideration",
            "defer": "previous proposal deferred: respect the recorded release condition before reconsideration",
            "unclear": "previous rejection discussion was read but remains ambiguous; retain its sources and unresolved question",
            "no-comment": "previous rejection discussion was checked; no usable human reason comment was found",
        }
        return "needs-human", False, messages.get(action, (
            "previous rejection reason not extracted; inspect closed-MR discussion before reconsideration"
            if not isinstance(reason, dict) else
            "previous proposal was rejected; respect the saved decision and require relevant new evidence"))
    if state.get("lastAttemptResult") == "needs-human":
        return "needs-human", False, "previous attempt needs human decision"
    if state.get("lastAttemptResult") == "candidate" and not (latest and latest["status"] == "accepted"):
        return "needs-human", False, "candidate attempt without accepted proposal needs reconciliation"
    if explicit:
        return None, True, None
    return None, check_due(state, icon_hash, now, policy), None


def save(session, data):
    destination = session / "session.json"
    temporary = session / "session.json.tmp"
    created = False
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            created = True
            stream.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(destination)
    finally:
        if created and temporary.exists() and not temporary.is_symlink():
            temporary.unlink()


def create_session(repo, session, bank_id, *, base="HEAD", remote=None, now=None):
    """Create one bank's frozen workspace; selection belongs exclusively to tasks.plan.

    This is an internal primitive, not another maintenance entry point. task-start
    attaches the requested task and checks live eligibility before work begins.
    """
    from research import settings, configure
    from state_publication import initial_state
    settings()
    if type(bank_id) is not int or bank_id < 0:
        raise SessionError("Company.id must be a nonnegative integer")
    repo = Path(repo).resolve(strict=True)
    root = git(repo, "rev-parse", "--show-toplevel", check=False)
    if root.returncode or Path(root.stdout.strip()).resolve() != repo:
        raise SessionError("--repo must be a Git checkout root")
    session = Path(session).resolve()
    if session == repo or repo in session.parents:
        raise SessionError("session directory must be outside the source checkout")
    if session.exists():
        raise SessionError("session directory already exists; use continue")
    timestamp = parse_time(now) if now else datetime.now(timezone.utc)
    base_commit = git(repo, "rev-parse", "--verify", f"{base}^{{commit}}").stdout.strip()
    policy = policy_at(repo, base_commit)
    source = argparse.Namespace(remote=remote, offline=False)
    state_ref, state_commit, state_remote, state_read = initial_state(repo, source)
    session.mkdir(parents=True)
    worktree = session / "worktree"
    git(repo, "-c", "core.autocrlf=false", "-c", "core.eol=lf",
        "worktree", "add", "--detach", str(worktree), base_commit)
    worktree_git_dir = Path(git(worktree, "rev-parse", "--absolute-git-dir").stdout.strip()).resolve()
    entries, errors = catalog(worktree)
    matches = entries.get(bank_id, [])
    entry = matches[0] if matches else None
    problems = list(errors.get(bank_id, []))
    if len(matches) > 1:
        problems.append("duplicate Company.id")
    if entry is None:
        problems.append("Company.id not found in catalog")
    elif entry["problem"]:
        problems.append(entry["problem"])
    state_paths = (set(git(repo, "ls-tree", "-r", "--name-only", state_commit,
                           "--", "banks/state/").stdout.splitlines()) if state_commit else set())
    bank_state, state_error = read_state(repo, state_commit, state_paths, bank_id)
    if state_error:
        problems.append(state_error)
    point, _, reason = state_decision(bank_state, entry["iconHash"] if entry else None,
                                     timestamp, policy, True)
    if problems:
        point, reason = "blocked", "; ".join(problems)
    bank = {"id": bank_id, "title": entry["title"] if entry else None,
            "folder": entry["folder"] if entry else None,
            "iconHash": entry["iconHash"] if entry else None,
            "metadata": entry["metadata"] if entry else None,
            "metadataHash": entry["metadataHash"] if entry else None,
            "state": bank_state, "warnings": entry["warnings"] if entry else [],
            "point": point or "identity-evidence", "reason": reason}
    data = {"schemaVersion": 2, "repo": str(repo), "baseRef": base,
            "baseCommit": base_commit, "worktree": str(worktree), "worktreeGitDir": str(worktree_git_dir),
            "stateRef": state_ref, "stateRemote": state_remote, "stateRead": state_read,
            "stateCommit": state_commit, "stateHistory": "present" if state_ref else "unknown",
            "selectedIds": [bank_id], "banks": [bank], "paused": False,
            "now": timestamp.isoformat().replace("+00:00", "Z")}
    data["researchBudget"] = configure(session, base_commit, ids=[bank_id])
    save(session, data)
    return data


def parse_time(value):
    if not isinstance(value, str):
        raise SessionError(f"invalid UTC timestamp: {value!r}")
    try:
        result = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise SessionError(f"invalid UTC timestamp: {value}") from exc
    if result.tzinfo is None or result.utcoffset().total_seconds() != 0:
        raise SessionError("timestamp must have UTC timezone")
    return result


def record_attempt(session, data, bank, source):
    """Record one validated attempt locally without modifying accepted assets or refs."""
    worktree = Path(data["worktree"])
    entries, errors = catalog(worktree)
    matches = entries.get(bank["id"], [])
    if (errors.get(bank["id"]) or len(matches) != 1 or matches[0]["problem"] or
            any(matches[0].get(key) != bank.get(key) for key in
                ("folder", "metadata", "metadataHash", "iconHash", "title"))):
        bank["point"] = "needs-human"
        bank["reason"] = "catalog entry or current SVG changed since session start; reassess this bank"
        return

    try:
        payload = validate_attempt(source, data, bank)
        if instant(payload["checkedAt"], "checkedAt") > datetime.now(timezone.utc):
            raise ValueError("attempt checkedAt is after command time")
        binding = render_binding(data, bank, payload)
    except (ValueError, OSError) as exc:
        raise SessionError(f"invalid evidence for Company.id {bank['id']}: {exc}") from exc
    id = bank["id"]
    before = bank["state"]
    from feedback_revision import attempt_state
    after = attempt_state(session, data, bank, before, payload)
    outcome = payload["outcome"]
    points = {"unchanged": "awaiting-state-publication", "unconfirmed": "attempt-complete",
              "error": "attempt-complete", "candidate": "waiting-proposal", "needs-human": "needs-human"}

    evidence_rel = f"evidence/{id}/validated.json"
    state_rel = f"banks/state/{id}.json"
    report_rel = f"reports/{id}.json"
    report = {"schemaVersion": 1, "id": id, "outcome": outcome,
              "before": before, "after": after, "evidence": payload,
              "evidencePath": evidence_rel, "statePath": state_rel,
              "provenance": {"baseRef": data["baseRef"], "baseCommit": data["baseCommit"],
                             "stateRef": data["stateRef"], "stateCommit": data["stateCommit"],
                             "stateHistory": data["stateHistory"],
                             "sourceEvidence": str(source)},
              "humanReview": "pending", "nextAction": payload["nextAction"]}
    if binding is not None:
        report["renderBinding"] = binding
    documents = [(evidence_rel, payload), (state_rel, after), (report_rel, report)]
    for relative, _ in documents:
        path = session / relative
        parent = session
        for segment in Path(relative).parts[:-1]:
            parent = parent / segment
            if parent.is_symlink():
                raise SessionError(f"local attempt output parent is a symlink: {parent}; no files overwritten")
        if path.exists() or path.is_symlink():
            raise SessionError(f"local attempt output already exists: {path}; no files overwritten")
    written = []
    try:
        for relative, content in documents:
            destination = session / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("x", encoding="utf-8") as stream:
                written.append(destination)
                json.dump(content, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
    except (OSError, ValueError) as exc:
        for path in written:
            path.unlink()
        raise SessionError(f"cannot save local attempt: {exc}") from exc
    bank["state"] = after
    bank["point"] = points[outcome]
    bank["reason"] = payload["reason"]
    bank["report"] = report_rel
    bank["evidence"] = evidence_rel
    def rollback():
        for path in written:
            path.unlink(missing_ok=True)
    return rollback


def record_review(session, data, bank, source):
    """Save a confirmed human review without replacing the original attempt record."""
    worktree = Path(data["worktree"])
    entries, errors = catalog(worktree)
    matches = entries.get(bank["id"], [])
    if (errors.get(bank["id"]) or len(matches) != 1 or matches[0]["problem"] or
            any(matches[0].get(key) != bank.get(key) for key in
                ("folder", "metadata", "metadataHash", "iconHash", "title"))):
        raise SessionError("catalog entry or current SVG changed since session start")
    id = bank["id"]
    state_path = session / f"banks/state/{id}.json"
    if (bank.get("report") != f"reports/{id}.json" or
            bank.get("evidence") != f"evidence/{id}/validated.json" or
            (session / "banks").is_symlink() or (session / "banks/state").is_symlink() or
            state_path.is_symlink() or not state_path.is_file()):
        raise SessionError("canonical prior attempt or state is missing")
    old_state = state_path.read_bytes()
    try:
        if json.loads(old_state) != bank["state"]:
            raise SessionError("local state differs from canonical prior attempt")
        review, payload, prior_report = validate_review(source, data, bank, datetime.now(timezone.utc))
    except (ValueError, OSError, UnicodeError) as exc:
        raise SessionError(f"invalid review for Company.id {id}: {exc}") from exc
    after = dict(bank["state"])
    after.update(lastAttemptAt=payload["checkedAt"], lastAttemptResult="verified",
                 lastVerifiedAt=payload["checkedAt"], verifiedIconHash=payload["iconHash"])
    evidence_rel = f"evidence/{id}/reviewed-unchanged.json"
    review_rel = f"evidence/{id}/human-review.json"
    report_rel = f"reports/{id}-reviewed-unchanged.json"
    report = {"schemaVersion": 1, "id": id, "outcome": "unchanged",
              "before": bank["state"], "after": after, "evidence": payload,
              "evidencePath": evidence_rel, "statePath": f"banks/state/{id}.json",
              "priorReport": review["priorReport"], "priorEvidence": review["priorEvidence"],
              "reviewPath": review_rel, "humanReview": "confirmed",
              "provenance": {**prior_report["provenance"], "sourceEvidence": review["evidence"]["path"]},
              "nextAction": payload["nextAction"]}
    documents = [(evidence_rel, payload), (review_rel, review), (report_rel, report)]
    for relative, _ in documents:
        path = session / relative
        parent = session
        for segment in Path(relative).parts[:-1]:
            parent = parent / segment
            if parent.is_symlink():
                raise SessionError(f"review output parent is a symlink: {parent}")
        if path.exists() or path.is_symlink():
            raise SessionError(f"review output already exists: {path}")
    written = []
    state_temporary = state_path.with_suffix(".json.tmp")
    if state_temporary.exists() or state_temporary.is_symlink():
        raise SessionError("local state temporary file already exists")

    def rollback():
        if state_path.read_bytes() != old_state:
            state_temporary.write_bytes(old_state)
            state_temporary.replace(state_path)
        state_temporary.unlink(missing_ok=True)
        for path in written:
            path.unlink(missing_ok=True)

    try:
        for relative, content in documents:
            destination = session / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("x", encoding="utf-8") as stream:
                written.append(destination)
                json.dump(content, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
        state_temporary.write_text(json.dumps(after, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        state_temporary.replace(state_path)
    except (OSError, ValueError) as exc:
        rollback()
        raise SessionError(f"cannot save local review: {exc}") from exc
    bank.update(state=after, point="awaiting-state-publication", reason=payload["reason"],
                report=report_rel, evidence=evidence_rel)
    return rollback


def resume(args):
    session = Path(args.session).resolve()
    try:
        data = json.loads((session / "session.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SessionError(f"No valid session at {session}: {exc}") from exc
    points = {"identity-evidence", "paused", "blocked", "task-review", "task-complete",
              "needs-human", "waiting-proposal", "waiting-mr",
              "awaiting-state-publication", "attempt-complete"}
    if (not isinstance(data, dict) or type(data.get("schemaVersion")) is not int or
        data["schemaVersion"] != 2 or type(data.get("paused")) is not bool or
        not isinstance(data.get("repo"), str) or not isinstance(data.get("baseRef"), str) or
        not isinstance(data.get("baseCommit"), str) or not isinstance(data.get("worktree"), str) or
        not isinstance(data.get("worktreeGitDir"), str) or
        not isinstance(data.get("selectedIds"), list) or not isinstance(data.get("banks"), list) or
        len(data["selectedIds"]) != 1 or len(data["banks"]) != 1 or
        any(type(id) is not int for id in data["selectedIds"]) or
        any(not isinstance(bank, dict) or bank.get("id") != id or bank.get("point") not in points or
            (data["paused"] and bank.get("priorPoint") not in points)
            for id, bank in zip(data["selectedIds"], data["banks"]))):
        raise SessionError(f"invalid session continuation at {session}; no files overwritten")
    worktree = session / "worktree"
    if data["worktree"] != str(worktree) or worktree.is_symlink() or not worktree.is_dir():
        raise SessionError("session worktree is missing, moved or replaced")
    reported_root = Path(git(worktree, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    common_path = Path(git(data["repo"], "rev-parse", "--git-common-dir").stdout.strip())
    if not common_path.is_absolute():
        common_path = Path(data["repo"]) / common_path
    git_dir = Path(git(worktree, "rev-parse", "--absolute-git-dir").stdout.strip()).resolve()
    if (reported_root != worktree or git_dir.parent != common_path.resolve() / "worktrees" or
            git_dir != Path(data["worktreeGitDir"])):
        raise SessionError("session worktree registration changed; no files overwritten")
    head = git(worktree, "rev-parse", "HEAD").stdout.strip()
    if head != data.get("baseCommit"):
        raise SessionError("session worktree base commit changed; no files overwritten")
    rollback = None
    if args.command == "continue" and args.evidence is not None:
        if data["paused"]:
            raise SessionError("resume before recording evidence")
        source = Path(args.evidence).absolute()
        if (source.is_symlink() or not source.is_file() or
                (session / "evidence").resolve() not in source.resolve().parents):
            raise SessionError("evidence must be a regular file under session/evidence")
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SessionError(f"invalid evidence JSON: {exc}") from exc
        id = raw.get("id") if isinstance(raw, dict) else None
        if not integer(id):
            raise SessionError("evidence Company.id must be an integer")
        bank = next((item for item in data["banks"] if item["id"] == id), None)
        if bank is None or bank["point"] != "identity-evidence":
            raise SessionError(f"Company.id {id} is not at identity-evidence; no attempt recorded")
        rollback = record_attempt(session, data, bank, source)
    if args.command == "continue" and args.review is not None:
        if data["paused"]:
            raise SessionError("resume before recording review")
        if any(part in (".", "..") for part in args.review.split("/")):
            raise SessionError("review path cannot contain traversal")
        source = Path(args.review).absolute()
        try:
            relative = str(source.relative_to(session))
            artifact(session, relative, "review")
            raw = json.loads(source.read_text(encoding="utf-8"))
        except (ValueError, OSError, UnicodeError) as exc:
            raise SessionError(f"invalid review JSON or path: {exc}") from exc
        id = raw.get("id") if isinstance(raw, dict) else None
        if not integer(id):
            raise SessionError("review Company.id must be an integer")
        bank = next((item for item in data["banks"] if item["id"] == id), None)
        if bank is None or bank["point"] != "needs-human":
            raise SessionError(f"Company.id {id} is not at needs-human; no review recorded")
        rollback = record_review(session, data, bank, source)
    if args.command == "continue" and args.candidate is not None:
        if data["paused"]:
            raise SessionError("resume before preparing candidate")
        source = Path(args.candidate).absolute()
        artifact(session, str(source.relative_to(session)), "candidate package")
        raw = json.loads(source.read_text(encoding="utf-8"))
        id = raw.get("id") if isinstance(raw, dict) else None
        if not integer(id):
            raise SessionError("candidate Company.id must be an integer")
        bank = next((item for item in data["banks"] if item["id"] == id), None)
        if bank is None or bank["point"] != "waiting-proposal":
            raise SessionError(f"Company.id {id} is not at waiting-proposal")
        from candidate import record_candidate
        rollback = record_candidate(session, data, bank, source)
    if args.command == "pause":
        if not data["paused"]:
            for bank in data["banks"]:
                bank["priorPoint"] = bank["point"]
                bank["point"] = "paused"
            data["paused"] = True
    else:
        if data["paused"]:
            for bank in data["banks"]:
                bank["point"] = bank.pop("priorPoint")
            data["paused"] = False
    if args.command == "continue":
        for bank in data["banks"]:
            if bank["point"] not in ("waiting-proposal", "waiting-mr"):
                continue
            state = bank.get("state")
            if not isinstance(state, dict):
                raise SessionError("waiting proposal has no valid local state")
            proposals = state.get("proposals", [])
            if not isinstance(proposals, list) or any(not isinstance(item, dict) for item in proposals):
                raise SessionError("invalid local proposal history")
            if proposals and proposals[-1].get("reviewPackage"):
                try:
                    if proposals[-1].get("approval") or proposals[-1].get("agentAssessment"):
                        from asset_results import validate_resume
                        validate_resume(session, data, bank)
                    else:
                        from candidate import validate_prepared_proposal
                        validate_prepared_proposal(session, data, bank)
                except (ValueError, OSError) as exc:
                    bank["point"] = "needs-human"
                    bank["reason"] = f"prepared proposal needs revalidation: {exc}"
    try:
        save(session, data)
    except OSError:
        if rollback is not None:
            rollback()
        raise
    return data


def main():
    parser = JsonArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("continue", "pause"):
        sub = commands.add_parser(command)
        sub.add_argument("--session", required=True)
        if command == "continue":
            choice = sub.add_mutually_exclusive_group()
            choice.add_argument("--evidence")
            choice.add_argument("--review")
            choice.add_argument("--candidate")
    import environment
    environment.add_subcommands(commands)
    import state_publication
    state_publication.add_subcommands(commands)
    import asset_publication
    asset_publication.add_subcommands(commands)
    import headless
    headless.add_subcommands(commands)
    import feedback_intake
    feedback_intake.add_subcommands(commands)
    import tasks
    tasks.add_subcommands(commands)
    try:
        args = parser.parse_args()
        if args.command == "doctor":
            result = environment.doctor(args)
            emit_json(result)
            return 1 if result["status"] == "blocked" else 0
        environment.require_posix()
        environment.canonicalize_session_arguments(args)
        if args.command.startswith("task-") or args.command.startswith("merge-"):
            result = tasks.dispatch(args)
        elif args.command.startswith("feedback-"):
            result = feedback_intake.dispatch(args)
        elif args.command == "desktop-export":
            result = headless.dispatch(args)
        elif args.command.startswith("asset-") or args.command in ("candidate-approve", "candidate-assess"):
            result = asset_publication.dispatch(args)
        elif args.command.startswith("state-"):
            result = state_publication.dispatch(args)
        else:
            result = resume(args)
    except (SessionError, OSError, ValueError) as exc:
        emit_json({"error": str(exc)})
        return 1
    emit_json(result)
    return 1 if result.get("status") == "merge-readiness-pending" else 0


if __name__ == "__main__":
    sys.exit(main())
