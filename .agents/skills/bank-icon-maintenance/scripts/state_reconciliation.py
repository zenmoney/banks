"""Rebase validated session results onto observed service state without erasing history.

The caller validates original session results, reads exact remote blobs, and confirms
prior package commits and their content. This helper never writes files or Git refs.
"""
import copy
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re

from evidence import instant
from state_publication import REF, encoded

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_ATTEMPT = ("lastAttemptAt", "lastAttemptResult")
_VERIFIED = ("lastVerifiedAt", "verifiedIconHash")
_ALLOWED_DELTA = {"schemaVersion", *_ATTEMPT, *_VERIFIED, "proposals"}


def _source_records(records, expected, observed=False):
    if type(records) is not list:
        raise ValueError("invalid state reconciliation records")
    by_id = {}
    for row in records:
        if (type(row) is not dict or type(row.get("id")) is not int or row["id"] < 0 or
                row.get("path") != f"banks/state/{row['id']}.json" or
                (not observed and type(row.get("after")) is not dict) or
                (row.get("before") is not None and type(row["before"]) is not dict) or
                row["id"] in by_id):
            raise ValueError("invalid or duplicate state reconciliation record")
        if expected is not None and row["id"] not in expected:
            raise ValueError("unexpected state reconciliation record")
        by_id[row["id"]] = row
    return by_id


def _own_prior(original, current, target, packages):
    """Match exact remote content AND the original source basis, not a later date."""
    for package in sorted(packages, key=lambda item: item["packageHash"]):
        if package.get("target") != target or package.get("ref") != REF:
            continue
        records = _source_records(package.get("records"), None)
        sources = _source_records(package.get("results"), None)
        record = records.get(original["id"])
        source = sources.get(original["id"])
        if (record is not None and source is not None and record["path"] == original["path"] and
                source["path"] == original["path"] and record["after"] == current and
                source["before"] == original["before"]):
            return package["packageHash"]
    return None


def _validate_resolution(resolution, originals, observed, target, conflicts):
    if not conflicts:
        if resolution is not None:
            raise ValueError("resolution has no conflicting state records to reconcile")
        return
    if resolution is None:
        raise ValueError(f"remote state changed for Company.id {conflicts[0]}; human reconciliation required")
    keys = {"schemaVersion", "target", "ref", "records", "reviewer", "statement", "resolvedAt"}
    if (type(resolution) is not dict or resolution.keys() != keys or
            type(resolution["schemaVersion"]) is not int or resolution["schemaVersion"] != 1 or
            resolution["target"] != target or resolution["ref"] != REF or
            type(resolution["records"]) is not list or
            len(resolution["records"]) != len(conflicts)):
        raise ValueError("resolution must bind the exact target, state ref and conflicting records")
    for field in ("reviewer", "statement"):
        if type(resolution[field]) is not str or not resolution[field].strip():
            raise ValueError(f"resolution requires {field}")
    if instant(resolution["resolvedAt"], "resolution resolvedAt") > datetime.now(timezone.utc):
        raise ValueError("resolution resolvedAt cannot be in the future")
    for id, decision in zip(conflicts, resolution["records"]):
        source, remote = originals[id], observed[id]
        if (type(decision) is not dict or
                decision.keys() != {"id", "beforeSha256", "localAfterSha256", "decision"} or
                type(decision["id"]) is not int or decision["id"] != id or
                decision["beforeSha256"] != remote["beforeSha256"] or
                decision["localAfterSha256"] != hashlib.sha256(encoded(source["after"])).hexdigest() or
                decision["decision"] != "apply-local-attempt-preserving-history"):
            raise ValueError(f"resolution does not bind exact remote and local state for Company.id {id}")


def _raw_proposal(proposal):
    """Restore raw pending facts without rebasing the original nullable base icon."""
    raw = proposal.get("sourceCandidate")
    if type(raw) is not dict or type(raw.get("iconHash")) is not str:
        return None
    result = {key: copy.deepcopy(value) for key, value in proposal.items()
              if key not in {"sourceCandidate", "reviewPackage", "verification", "humanReview",
                             "approval", "assetPublication", "mr"}}
    result["candidate"] = copy.deepcopy(raw)
    result["candidateIconHash"] = raw["iconHash"]
    result["status"] = "pending"
    return result

def _prepared_proposal(proposal):
    """Same exact proposal before later human approval and observed MR facts."""
    prepared = copy.deepcopy(proposal)
    for field in ("approval", "assetPublication", "mr"):
        prepared.pop(field, None)
    prepared["humanReview"] = "pending"
    prepared["status"] = "pending"
    return prepared


def _approved_proposal(proposal):
    approved = copy.deepcopy(proposal)
    for field in ("assetPublication", "mr"):
        approved.pop(field, None)
    approved["status"] = "pending"
    return approved


def _reconcile_proposals(before, after, current, own):
    prior = before.get("proposals", []) if before is not None else []
    local = after.get("proposals", [])
    remote = current.get("proposals", [])
    if not all(type(rows) is list for rows in (prior, local, remote)) or not all(
            type(proposal) is dict for rows in (prior, local, remote) for proposal in rows):
        raise ValueError("invalid proposal history; dedicated proposal recovery required")
    if local == prior:
        return copy.deepcopy(remote) if "proposals" in current else None
    if len(local) != len(prior) + 1 or local[:len(prior)] != prior or local[-1].get("status") not in {"pending", "open", "accepted", "rejected"}:
        raise ValueError("unsupported local proposal change; dedicated proposal recovery required")
    candidate = local[-1]
    if own and remote:
        if remote[-1] == candidate:
            return copy.deepcopy(remote)
        previous = [_raw_proposal(candidate), _prepared_proposal(candidate)]
        if type(candidate.get("mr")) is dict:
            previous.append(_approved_proposal(candidate))
            observed_mr = remote[-1].get("mr")
            if (candidate["mr"].get("state") in {"closed", "merged"} and
                    type(observed_mr) is dict and observed_mr.get("state") == "open"):
                identity = ("number", "url", "head", "headCommit", "base", "target",
                            "title", "bodySha256")
                if all(candidate["mr"].get(key) == observed_mr.get(key) for key in identity):
                    opened = copy.deepcopy(candidate)
                    opened["status"] = "open"
                    opened["mr"] = copy.deepcopy(observed_mr)
                    previous.append(opened)
        if candidate["status"] in {"accepted", "rejected"}:
            history = candidate.get("mrHistory")
            if type(history) is not list or len(history) != 1 or type(history[0]) is not dict or history[0].get("state") != "open":
                raise ValueError("terminal proposal lacks exact prior open MR observation")
            observed_open = copy.deepcopy(candidate)
            for field in ("mrOutcome", "mrHistory", "acceptedIconHash", "acceptedHeadCommit",
                          "rejectionReason", "currentVerification", "reassessment", "reconsideration"):
                observed_open.pop(field, None)
            observed_open["status"] = "open"
            observed_open["mr"] = copy.deepcopy(history[0])
            previous.append(observed_open)
        if remote[-1] in previous:
            return [*copy.deepcopy(remote[:-1]), copy.deepcopy(candidate)]
    if any(p.get("status") in {"pending", "open"} or
           (type(p.get("mr")) is dict and p["mr"].get("state") == "open") for p in remote):
        raise ValueError("active unrelated proposal requires dedicated proposal recovery")
    hashes = {candidate.get("candidateIconHash")}
    source = candidate.get("sourceCandidate")
    if type(source) is dict:
        hashes.add(source.get("iconHash"))
    if any(p.get("candidateIconHash") in hashes or
           (type(p.get("sourceCandidate")) is dict and p["sourceCandidate"].get("iconHash") in hashes)
           for p in remote):
        raise ValueError("duplicate or rejected candidate requires dedicated proposal recovery")
    return [*copy.deepcopy(remote), copy.deepcopy(candidate)]


def _overlay(original, current, own):
    before, local = original["before"], original["after"]
    basis = before if before is not None else {}
    if basis.get('taskResults') != local.get('taskResults'):
        from task_work import overlay
        return overlay(original, current, own)
    if basis.get("reviewFeedback") != local.get("reviewFeedback"):
        from feedback_revision import overlay_own
        return overlay_own(original, current, own)
    if current is not None and (type(current) is not dict or current.get("schemaVersion") != 1):
        raise ValueError("invalid remote state; human reconciliation required")
    if local.get("schemaVersion") != 1 or any(
            basis.get(key) != local.get(key) for key in set(basis) | set(local) if key not in _ALLOWED_DELTA):
        raise ValueError("unsupported local state change; human reconciliation required")
    if any(field not in local for field in _ATTEMPT):
        raise ValueError("local result lacks an attempt; human reconciliation required")
    merged = copy.deepcopy(current) if current is not None else {"schemaVersion": 1}
    for field in _ATTEMPT:
        merged[field] = copy.deepcopy(local[field])
    if tuple(basis.get(key) for key in _VERIFIED) != tuple(local.get(key) for key in _VERIFIED):
        if any(key not in local for key in _VERIFIED):
            raise ValueError("partial local verification; human reconciliation required")
        for field in _VERIFIED:
            merged[field] = copy.deepcopy(local[field])
    proposals = _reconcile_proposals(before, local, merged, own)
    if proposals is not None:
        merged["proposals"] = proposals
    return merged


def reconcile_records(session: Path, originals: list[dict], observed: list[dict], target: str,
                      resolution: dict | None, prior: list[dict]) -> tuple[list[dict], dict]:
    """Return effective transitions and exact decisions for content-addressed publication.

    ``prior`` contains only own packages whose commits and exact state blobs the caller
    has confirmed on the observed ref. Their ``results`` retain the original source
    basis; their ``records`` reflect what actually landed. No time-based winner exists.
    """
    session = Path(session)
    if not session.is_dir() or session.is_symlink():
        raise ValueError("existing regular session directory required")
    source = _source_records(originals, None)
    remote = _source_records(observed, set(source), observed=True)
    if len(source) != len(remote) or not source:
        raise ValueError("observed state records do not match session results")
    if type(prior) is not list or any(type(item) is not dict or
            type(item.get("packageHash")) is not str or
            _HASH.fullmatch(item["packageHash"]) is None for item in prior):
        raise ValueError("invalid confirmed prior state packages")
    for row in observed:
        sha = row.get("beforeSha256")
        if ((row["before"] is None) != (sha is None) or
                (sha is not None and (type(sha) is not str or _HASH.fullmatch(sha) is None))):
            raise ValueError("observed state lacks exact remote byte hash")
    own = {}
    conflicts = []
    for original in originals:
        id = original["id"]
        if remote[id]["before"] != original["before"]:
            own[id] = _own_prior(original, remote[id]["before"], target, prior)
            if own[id] is None:
                conflicts.append(id)
    _validate_resolution(resolution, source, remote, target, conflicts)
    records = []
    for original in originals:
        id = original["id"]
        observed_record = remote[id]
        if observed_record["before"] == original["before"]:
            after = copy.deepcopy(original["after"])
        else:
            after = _overlay(original, observed_record["before"], own.get(id) is not None)
        records.append({"id": id, "path": original["path"],
                        "before": copy.deepcopy(observed_record["before"]),
                        "beforeSha256": observed_record["beforeSha256"], "after": after})
    return records, {"resolution": copy.deepcopy(resolution), "priorPackages": sorted(set(own.values()) - {None})}
