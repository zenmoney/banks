"""Closed-PR discussion intake, not a logo checker or a publication grant.

Read text without requiring an attachment. The agent classifies actual comments;
we bind the complete discussion, selected quotations and their real editors. All
state effects still go through proposal_sync and the existing authorized writer.
"""
import copy
import json
import re
from pathlib import Path

import feedback_intake as feedback
import state_publication as state
from evidence import instant, require_hash, required

CATEGORIES = {"keep-current": "not-now", "repair": "technical",
              "verify-applicability": "identity/logo", "defer": "not-now"}
MAX_DISCUSSION_BYTES = 2 * 1024 * 1024
ACTIONS = set(CATEGORIES) | {"unclear", "no-comment"}


def read_thread(target, number):
    """Bounded REST reads; original authors here are context, never editor proof."""
    import github_pr
    messages = [("body", github_pr._api(f"repos/{target}/pulls/{number}"))]
    for kind, endpoint in (("comment", f"issues/{number}/comments"),
                           ("review", f"pulls/{number}/reviews"),
                           ("inline", f"pulls/{number}/comments")):
        messages.extend((kind, row) for row in feedback._pages(f"repos/{target}/{endpoint}"))
    if len(messages) > feedback.MAX_MESSAGES:
        raise ValueError("closed-MR discussion exceeds the bounded message count")
    result = []
    for kind, row in messages:
        node = required(row, "node_id")
        url = required(row, "html_url")
        body = row.get("body") or ""
        feedback.clean_body(body)  # Input size/type guard, not semantic filtering.
        user = row.get("user") or {}
        result.append({"kind": kind, "nodeId": node, "url": url, "body": body,
                       "author": user.get("login"), "userType": user.get("type"),
                       "reviewState": row.get("state") if kind == "review" else None})
    return sorted(result, key=lambda r: (r["kind"], r["nodeId"]))


def _fingerprint(review):
    # Observation time and unrelated target-base advancement are not new feedback.
    return state.digest(state.encoded({
        "mr": {k: v for k, v in review["mr"].items() if k != "baseCommit"},
        "candidateIconHash": review["candidateIconHash"],
        "attemptCheckedAt": review["attemptCheckedAt"], "messages": review["messages"]}))


def capture(proposal, observed):
    if observed["state"] != "closed":
        raise ValueError("rejection discussion requires a closed, unmerged MR")
    messages = read_thread(observed["target"], observed["number"])
    if len(state.encoded(messages)) > MAX_DISCUSSION_BYTES:
        raise ValueError("closed-MR discussion exceeds the byte limit; no truncated interpretation")
    # A body racing with the provider observation is not a coherent discussion.
    for row in messages:
        if row["kind"] == "body" and state.digest(row["body"].encode()) != observed["bodySha256"]:
            raise ValueError("closed-MR description changed during discussion read")
    review = {"schemaVersion": 1, "mr": copy.deepcopy(observed),
              "candidateIconHash": proposal["candidateIconHash"],
              "attemptCheckedAt": proposal["checkedAt"], "messages": messages}
    review["key"] = _fingerprint(review)
    return review


def candidates(review):
    # A bot's text or the original proposal description is not a human rejection.
    # Unknown authors are retained for an explicit unclear decision, not discarded.
    return [r for r in review["messages"] if r["kind"] != "body"
            and r["userType"] != "Bot" and feedback.clean_body(r["body"])
            and r.get("reviewState") != "DISMISSED"]


def _validate_review(review, proposal):
    if (type(review) is not dict or review.keys() != {"schemaVersion", "mr", "candidateIconHash",
            "attemptCheckedAt", "messages", "key"} or type(review["schemaVersion"]) is not int
            or review["schemaVersion"] != 1 or type(review["messages"]) is not list
            or len(review["messages"]) > feedback.MAX_MESSAGES
            or len(state.encoded(review["messages"])) > MAX_DISCUSSION_BYTES):
        raise ValueError("invalid closed-MR discussion snapshot")
    mr = review["mr"]
    if (not isinstance(mr, dict) or mr.get("state") != "closed" or
            any(mr.get(k) != proposal["mr"].get(k) for k in
                ("number", "url", "target", "head", "base", "title", "bodySha256")) or
            review["candidateIconHash"] != proposal["candidateIconHash"] or
            review["attemptCheckedAt"] != proposal["checkedAt"]):
        raise ValueError("closed-MR discussion does not bind this proposal")
    seen = set()
    for row in review["messages"]:
        if type(row) is not dict or row.keys() != {
                "kind", "nodeId", "url", "body", "author", "userType", "reviewState"}:
            raise ValueError("closed-MR discussion message incomplete")
        url = required(row, "url")
        if (row["kind"] not in feedback._TYPES or
                (url != mr["url"] if row["kind"] == "body" else not re.fullmatch(
                    re.escape(mr["url"]) + r"(?:/files)?#(?:issuecomment-|pullrequestreview-|discussion_r)[A-Za-z0-9_-]+", url))):
            raise ValueError("closed-MR message is for a different PR")
        identity = (row["kind"], required(row, "nodeId"))
        if identity in seen:
            raise ValueError("duplicate closed-MR discussion message")
        seen.add(identity)
        feedback.clean_body(row["body"])
    require_hash(review["key"], "closed review key")
    if review["key"] != _fingerprint(review):
        raise ValueError("closed-MR discussion snapshot changed")


def _judgement(review, value):
    if type(value) is not dict or value.keys() != {"key", "action", "sources", "rationale"}:
        raise ValueError("review decision needs key, action, sources and rationale")
    if value["key"] != review["key"] or value["action"] not in ACTIONS:
        raise ValueError("review decision does not name an observed request/action")
    required(value, "rationale")
    choices = value["sources"]
    if type(choices) is not list or len(choices) > feedback.MAX_MESSAGES:
        raise ValueError("review sources must be a bounded array of exact quotations")
    available = {r["url"]: r for r in candidates(review)}
    seen = set()
    for choice in choices:
        if type(choice) is not dict or choice.keys() != {"url", "quote"}:
            raise ValueError("selected reason requires a comment URL and its exact quote")
        if (choice["url"] not in available or choice["url"] in seen or
                required(choice, "quote") not in available[choice["url"]]["body"]):
            raise ValueError("rejection quotation does not occur in an observed human comment")
        seen.add(choice["url"])
    if value["action"] in CATEGORIES and not choices:
        raise ValueError("a known rejection action requires at least one sourced human comment")
    if value["action"] == "no-comment" and (available or choices):
        raise ValueError("no-comment requires a completely read thread without candidate comments")
    return [available[c["url"]] for c in choices]


def bind(id, proposal, review, judgement, at, *, refresh=True):
    """Validate a fresh semantic choice; never manufacture a human approval."""
    _validate_review(review, proposal)
    chosen = _judgement(review, judgement)
    if refresh:
        import proposal_sync as sync
        observed = sync._provider(proposal)
        if {k: v for k, v in observed.items() if k != "baseCommit"} != {
                k: v for k, v in review["mr"].items() if k != "baseCommit"}:
            raise ValueError("closed-MR outcome changed; retain evidence and prepare a fresh review context")
        current = capture(proposal, observed)
        if current["key"] != review["key"]:
            raise ValueError("closed-MR discussion changed; retain evidence and prepare a fresh review context")
    sources = []
    for row in chosen:
        source = feedback.message(review["mr"]["target"], review["mr"]["number"],
                                  row["kind"], row["nodeId"])
        if source["body"] != row["body"] or source["url"] != row["url"]:
            raise ValueError("selected rejection comment changed during editor verification")
        if (instant(source["createdAt"], "comment.createdAt") < instant(proposal["checkedAt"], "attempt.checkedAt")
                or instant(source["lastEditedAt"] or source["createdAt"], "comment.updatedAt") > instant(at, "reviewedAt")):
            raise ValueError("rejection comment is outside the proposal-to-review interval")
        sources.append(source)
    return {"id": id, "review": copy.deepcopy(review), "judgement": copy.deepcopy(judgement),
            "sources": sources, "evaluatedAt": at}


def validate(decision, proposal, at, *, live=True):
    if type(decision) is not dict or decision.keys() != {"id", "review", "judgement", "sources", "evaluatedAt"}:
        raise ValueError("closed-review decision lacks its immutable provenance")
    if type(decision["id"]) is not int or decision["id"] < 0 or decision["evaluatedAt"] != at:
        raise ValueError("closed-review decision ID/time changed")
    _validate_review(decision["review"], proposal)
    chosen = _judgement(decision["review"], decision["judgement"])
    if type(decision["sources"]) is not list or len(decision["sources"]) != len(chosen):
        raise ValueError("closed-review source bindings incomplete")
    instant(at, "closed review evaluatedAt")
    if live:
        expected = bind(decision["id"], proposal, decision["review"], decision["judgement"], at)
        if expected != decision:
            raise ValueError("rejection source editor, rights or text changed")
    for row, source in zip(chosen, decision["sources"]):
        if not isinstance(source, dict):
            raise ValueError("invalid rejection source")
        actor, trust = source.get("editor"), source.get("trust")
        if (not isinstance(actor, dict) or actor.get("__typename") != "User" or
                not isinstance(trust, dict) or trust.get("login") != actor.get("login") or
                trust.get("permission") not in {"write", "maintain", "admin"}):
            raise ValueError("rejection source lacks actual-editor permission provenance")
        if (source.get("body") != row["body"] or source.get("url") != row["url"]
                or source.get("nodeId") != row["nodeId"] or source.get("kind") != row["kind"]):
            raise ValueError("closed-review decision is not bound to its selected source")
        if (source.get("bodySha256") != state.digest(source["body"].encode()) or
                instant(source.get("createdAt"), "comment.createdAt") < instant(proposal["checkedAt"], "attempt.checkedAt") or
                instant(source.get("lastEditedAt") or source["createdAt"], "comment.updatedAt") > instant(at, "evaluatedAt")):
            raise ValueError("rejection source bytes or dates changed")
    action = decision["judgement"]["action"]
    if action not in CATEGORIES:
        return None
    source = decision["sources"][0]
    comment = {"url": source["url"], "body": source["body"], "author": source["editor"]["login"],
               "createdAt": source["createdAt"], "updatedAt": source["lastEditedAt"] or source["createdAt"],
               "kind": {"comment": "issue", "review": "review", "inline": "review-comment"}[source["kind"]]}
    return {"comment": comment, "mr": {k: proposal["mr"][k] for k in ("number", "url")},
            "candidateIconHash": proposal["candidateIconHash"], "category": CATEGORIES[action],
            "explanation": decision["judgement"]["sources"][0]["quote"], "action": action,
            "requirements": copy.deepcopy(decision["judgement"]["sources"]),
            "rationale": decision["judgement"]["rationale"]}


def annotate(after, decision):
    if decision is None or "review" not in decision:
        return after
    proposal = after["proposals"][-1]
    expected = validate(decision, proposal, decision["evaluatedAt"], live=False)
    if proposal["rejectionReason"] != expected:
        raise ValueError("closed-review decision differs from saved rejection reason")
    previous = proposal.get("rejectionReview")
    if previous is not None:
        proposal.setdefault("rejectionReviewHistory", []).append(copy.deepcopy(previous))
    proposal["rejectionReview"] = copy.deepcopy(decision)
    return after


def inputs(path):
    if path is None:
        return {}
    value = json.loads(state.regular(Path(path).absolute()))
    if type(value) is not list or any(type(v) is not dict for v in value):
        raise ValueError("review decisions must be an array")
    keys = [require_hash(v.get("key"), "review decision key") for v in value]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate review decision keys")
    return dict(zip(keys, value))


def resolve_rows(journal, rows, supplied, *, allow_new=True):
    """Immutable resolution files project review checkpoints into normal outcomes."""
    import proposal_sync as sync
    directory = journal / "resolutions"
    if directory.is_symlink():
        raise ValueError("review resolutions cannot be a symlink")
    directory.mkdir(exist_ok=True)
    reviews = {row["review"]["key"]: row for _, row in rows if row["kind"] == "review"}
    if set(supplied) - set(reviews):
        raise ValueError("review decision refers to an unobserved discussion")
    result = []
    for name, row in rows:
        if row["kind"] != "review":
            result.append((name, row))
            continue
        key = row["review"]["key"]
        proposal = row["before"]["proposals"][-1]
        matches = list(directory.glob(f"{key}-*.json"))
        if len(matches) > 1:
            raise ValueError("multiple resolutions for one immutable review")
        decision = None
        if matches:
            content = state.regular(matches[0])
            if matches[0].name != f"{key}-{state.digest(content)}.json":
                raise ValueError("review resolution hash changed")
            decision = json.loads(content)
            if decision["review"] != row["review"] or (key in supplied and decision["judgement"] != supplied[key]):
                raise ValueError("saved review cannot be replaced by another decision")
        elif allow_new or not candidates(row["review"]):
            judgement = supplied.get(key) if allow_new else None
            if judgement is None and not candidates(row["review"]):
                judgement = {"key": key, "action": "no-comment", "sources": [],
                             "rationale": "Full discussion read; no candidate human rejection comment was found."}
            if judgement is not None:
                decision = bind(row["id"], proposal, row["review"], judgement, sync._now(),
                                refresh=judgement["action"] != "no-comment")
                state.save_new(directory / f"{key}-{state.digest(state.encoded(decision))}.json", state.encoded(decision))
        if decision is None:
            result.append((name, row))
            continue
        at = decision["evaluatedAt"]
        reason = validate(decision, proposal, at, live=False)
        after = sync._transition(row["before"], row["observed"], None, row["stateCommit"], at,
                                 reason, clarification=row["clarification"], reviewed=True)
        annotate(after, decision)
        result.append((name, {"path": row["path"], "id": row["id"], "scanHash": row["scanHash"],
            "kind": "transition", "record": {"id": row["id"], "path": row["path"],
                "before": row["before"], "beforeSha256": row["beforeSha256"], "after": after},
            "result": {"id": row["id"], "observed": row["observed"], "proof": None,
                "observedAt": at, "decision": decision, "clarification": row["clarification"]}}))
    return result


def pending(session, rows):
    values = [{"id": r["id"], "key": r["review"]["key"], "mr": r["observed"]["url"],
               "discussion": r["review"]["messages"]} for _, r in rows if r["kind"] == "review"]
    if not values:
        return {}
    directory = session / "outcome-scan"
    data = {"reviews": values, "decisions": [
        {"key": r["key"], "action": None, "sources": [], "rationale": ""} for r in values]}
    name = f"review-input-{state.digest(state.encoded(data))}.json"
    state.save_new(directory / name, state.encoded(data))
    return {"pendingReviews": [{k: r[k] for k in ("id", "key", "mr")} for r in values],
            "reviewInput": str(directory / name),
            "reviewActions": sorted(ACTIONS - {"no-comment"}),
            "reviewNextAction": "Read the full discussions; save a COPY of decisions with actual action, source URLs/quotes and rationale; resume with --review-decisions FILE. No owner question is required to read an existing answer."}


def check_batch(journal, rows, package):
    """A processed review must retain the exact local resolution that made it."""
    results = {r["id"]: r for r in package["results"]}
    for _, row in rows:
        if row["kind"] != "review":
            continue
        decision = results.get(row["id"], {}).get("decision")
        if not isinstance(decision, dict) or decision.get("review") != row["review"]:
            raise ValueError("batched closed review lost its exact resolution")
        content = state.encoded(decision)
        path = journal / "resolutions" / f"{row['review']['key']}-{state.digest(content)}.json"
        try:
            if state.regular(path) != content:
                raise ValueError("batched closed-review resolution changed")
        except OSError as exc:
            raise ValueError("batched closed-review resolution is missing; retain diagnostics") from exc
