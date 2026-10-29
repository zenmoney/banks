"""Bounded, read-only GitHub feedback discovery and separately published intake.

Messages are evidence, not commands. The agent supplies the semantic decision;
this module binds the actual editor, attachment, bank and PR, and never grants
asset/merge permission. The service-state queue survives disposable sessions.
"""
import copy
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

import github_pr
import state_publication as state
from evidence import instant, require_hash, required

KIND = "review-feedback"
MAX_PRS = 100
MAX_MESSAGES = 1000
MAX_BODY = 131072
# Exclude only complete generated blocks, never human text around them.
_SECTION = re.compile(r"<!-- bank-icon-feedback:([0-9a-f]{64}):begin -->.*?"
                      r"<!-- bank-icon-feedback:\1:end -->", re.S)
# A result notice identifies an existing task result, not a successful revision.
_RESULT_SECTION = re.compile(
    r"<!-- bank-icon-task-result:([0-9a-f]{64}):"
    r"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z):begin -->.*?"
    r"<!-- bank-icon-task-result:\1:\2:end -->", re.S)
_NODE_QUERY = """query($id: ID!) { node(id: $id) {
  __typename
  ... on PullRequest { id url body createdAt lastEditedAt author { __typename login } editor { __typename login } }
  ... on IssueComment { id url body createdAt lastEditedAt author { __typename login } editor { __typename login } }
  ... on PullRequestReview { id url body createdAt lastEditedAt author { __typename login } editor { __typename login } }
  ... on PullRequestReviewComment { id url body createdAt lastEditedAt author { __typename login } editor { __typename login } }
} }"""
_TYPES = {"body": "PullRequest", "comment": "IssueComment", "review": "PullRequestReview",
          "inline": "PullRequestReviewComment"}
_STATUSES = {"queued", "ignored", "blocked", "candidate", "published", "superseded"}


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def clean_body(body):
    if not isinstance(body, str) or len(body.encode("utf-8")) > MAX_BODY:
        raise ValueError("feedback body is missing or exceeds the bounded input size")
    return _RESULT_SECTION.sub("", _SECTION.sub("", body)).strip()


def _url(value):
    if not isinstance(value, str):
        raise ValueError("attachment URL is missing")
    value = unescape(value.strip())
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.fragment or parsed.port not in (None, 443)
            or any(ord(c) <= 32 or ord(c) == 127 for c in value)):
        raise ValueError("feedback attachments require credential-free HTTPS URLs")
    return value


class _Images(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.urls = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "img":
            self.urls.extend(value for key, value in attrs if key.lower() == "src" and value)


def attachment_urls(body):
    """Extract HTML/Markdown images and linked image files, never display dimensions."""
    text = clean_body(body)
    text = re.sub(r"(?ms)^\s*(```|~~~).*?^\s*\1[^\n]*(?:\n|$)", "", text)
    text = re.sub(r"`[^`\n]*`", "", text)
    parser = _Images()
    parser.feed(text)
    urls = list(parser.urls)
    definitions = {m[1].casefold(): m[2] for m in re.finditer(
        r'(?m)^\s*\[([^\]]+)\]:\s*<?(https://[^\s>]+)>?', text)}
    for match in re.finditer(r'!\[([^\]]*)\]\[([^\]]*)\]', text):
        key = (match[2] or match[1]).casefold()
        if key in definitions:
            urls.append(definitions[key])
    # Balanced parentheses commonly occur in raw repository filenames.
    pattern = r'(!?)\[[^\]\n]*\]\(\s*(?:<([^>]+)>|(https://[^\s()]+(?:\([^\s()]*\)[^\s()]*)*))\s*(?:["\'][^\n]*?["\'])?\s*\)'
    for match in re.finditer(pattern, text):
        url = match[2] or match[3]
        if match[1] or re.search(r'\.(?:svg|png|jpe?g|webp)$', urlsplit(url).path, re.I):
            urls.append(url)
    result = []
    for url in urls:
        value = _url(url)
        if value not in result:
            result.append(value)
    return result


def _pages(endpoint, limit=MAX_MESSAGES):
    # Bound requests as well as returned rows. Do not ask gh to traverse an
    # unbounded thread and only discover the limit after its last page.
    rows = []
    for number in range(1, limit // 100 + 2):
        page = github_pr._api(endpoint, "-X", "GET", "-F", "per_page=100", "-F", f"page={number}")
        if not isinstance(page, list) or any(not isinstance(row, dict) for row in page):
            raise ValueError("feedback pagination incomplete")
        rows.extend(page)
        if len(rows) > limit:
            raise ValueError(f"feedback scan exceeds the bounded {limit}-row limit")
        if len(page) < 100:
            return rows
    raise ValueError("feedback pagination did not terminate within its request limit")


def _trusted(target, actor, cache):
    if (not isinstance(actor, dict) or actor.get("__typename") != "User"
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", str(actor.get("login", "")))):
        raise ValueError("feedback author/editor is not an identified human GitHub user")
    login = actor["login"]
    if login not in cache:
        value = github_pr._api(f"repos/{target}/collaborators/{login}/permission")
        if (not isinstance(value, dict) or not isinstance(value.get("user"), dict)
                or str(value["user"].get("login", "")).casefold() != login.casefold()
                or value["user"].get("type") != "User"
                or value.get("permission") not in {"admin", "write", "maintain"}):
            raise ValueError("feedback editor does not have confirmed repository write/maintain/admin access")
        cache[login] = {"login": login, "permission": value["permission"]}
    return copy.deepcopy(cache[login])


def message(target, number, kind, node_id, permissions=None):
    """Bind the current text to its actual editor, not the original PR author."""
    if kind not in _TYPES or not isinstance(node_id, str) or not node_id:
        raise ValueError("invalid feedback node")
    response = github_pr._api("graphql", "-f", f"query={_NODE_QUERY}", "-f", f"id={node_id}")
    value = response.get("data", {}).get("node") if isinstance(response, dict) else None
    if not isinstance(value, dict) or value.get("__typename") != _TYPES[kind] or value.get("id") != node_id:
        raise ValueError("feedback node disappeared or changed kind")
    url = required(value, "url")
    pr_url = f"https://github.com/{target}/pull/{number}"
    if kind == "body":
        valid_url = url == pr_url
    else:
        valid_url = bool(re.fullmatch(re.escape(pr_url) + r"(?:/files)?#(?:issuecomment-|pullrequestreview-|discussion_r)[A-Za-z0-9_-]+", url))
    if not valid_url:
        raise ValueError("feedback belongs to a different PR")
    created = instant(value.get("createdAt"), "feedback.createdAt")
    edited = value.get("lastEditedAt")
    if edited is not None and instant(edited, "feedback.lastEditedAt") < created:
        raise ValueError("feedback edit precedes creation")
    if max(created, instant(edited, "feedback.lastEditedAt") if edited else created) > datetime.now(timezone.utc):
        raise ValueError("feedback observation is in the future")
    actor = value.get("editor") if edited is not None else value.get("author")
    trust = _trusted(target, actor, permissions if permissions is not None else {})
    body = required(value, "body")
    text = clean_body(body)
    return {"kind": kind, "nodeId": node_id, "url": url, "body": body,
            "bodySha256": state.digest(body.encode()), "text": text,
            "textSha256": state.digest(text.encode()), "author": value.get("author"),
            "editor": actor, "createdAt": value["createdAt"], "lastEditedAt": edited,
            "trust": trust, "attachments": attachment_urls(text)}


def event_key(target, number, source):
    # Ref pushes/reactions and our generated materials do not manufacture requests.
    return state.digest(state.encoded({"target": target, "number": number,
        "kind": source["kind"], "nodeId": source["nodeId"], "textSha256": source["textSha256"]}))


def repository_from_remote(remote):
    if remote.startswith("https://github.com/"):
        value = remote[len("https://github.com/"):]
    elif remote.startswith("git@github.com:"):
        value = remote[len("git@github.com:"):]
    elif remote.startswith("ssh://git@github.com/"):
        value = remote[len("ssh://git@github.com/"):]
    else:
        raise ValueError("feedback discovery requires a concrete GitHub state destination")
    return github_pr.normalize(value.removesuffix(".git").rstrip("/"))


def _bank(repo, base, path):
    from asset_publication import _blob, _bank_base
    match = re.fullmatch(r"banks/[^/]+_([0-9]+)-(?:[a-zA-Z]{2}|unknown)/icon\.svg", path)
    if not match:
        raise ValueError("feedback PR does not touch one canonical bank icon")
    folder = path.rsplit("/", 1)[0]
    metadata = _blob(repo, base, folder + "/info.json")
    info = json.loads(metadata)
    id = int(match[1])
    if not isinstance(info, dict) or type(info.get("id")) is not int or info["id"] != id:
        raise ValueError("feedback path and Company.id disagree")
    present = state.git(repo, "ls-tree", "-z", base, "--", path).stdout
    icon_hash = state.digest(_blob(repo, base, path)) if present else None
    bank = {"id": id, "folder": folder, "title": required(info, "title"),
            "metadataHash": state.digest(metadata), "iconHash": icon_hash}
    _bank_base(repo, base, bank)
    if info.get("countryCode") is not None and (not isinstance(info["countryCode"], str) or
            not folder.endswith("-" + info["countryCode"].lower())):
        raise ValueError("feedback country does not match the record")
    return bank


def discover(repo, base, target, state_commit):
    rows = _pages(f"repos/{target}/pulls?state=open", MAX_PRS)
    if len(rows) > MAX_PRS:
        raise ValueError(f"feedback scan exceeds {MAX_PRS} open PRs; no partial queue is claimed complete")
    events, stops, seen, permissions = [], [], set(), {}
    for item in rows:
        number = item.get("number") if isinstance(item, dict) else None
        try:
            if type(number) is not int or number < 1 or item.get("state") != "open":
                raise ValueError("open PR list incomplete")
            files = _pages(f"repos/{target}/pulls/{number}/files")
            paths = [row.get("filename") for row in files if isinstance(row, dict)]
            if len(paths) != len(files):
                raise ValueError("PR file list incomplete")
            bank_paths = [p for p in paths if isinstance(p, str) and p.startswith("banks/") and p.endswith("/icon.svg")]
            if not bank_paths:
                continue
            if len(paths) != 1 or len(bank_paths) != 1 or files[0].get("status") not in {"added", "modified"}:
                raise ValueError("feedback amendment requires a single added/modified bank icon, without other files")
            bank = _bank(repo, base, bank_paths[0])
            before, _ = state.remote_record(repo, state_commit, f"banks/state/{bank['id']}.json")
            known = {row["key"] for row in requests(before)}
            messages = [("body", item)]
            for kind, endpoint in (("comment", f"issues/{number}/comments"),
                                   ("review", f"pulls/{number}/reviews"),
                                   ("inline", f"pulls/{number}/comments")):
                messages.extend((kind, row) for row in _pages(f"repos/{target}/{endpoint}"))
            if len(messages) > MAX_MESSAGES:
                raise ValueError("feedback thread exceeds the bounded message count")
            for kind, row in messages:
                body = row.get("body") if isinstance(row, dict) else None
                if body is None or not body.strip() or not attachment_urls(body):
                    continue
                try:
                    source = message(target, number, kind, row.get("node_id"), permissions)
                    if not source["attachments"]:
                        continue
                    key = event_key(target, number, source)
                    if key in known or key in seen:
                        continue
                    pr = github_pr.inspect(target, number)
                    if pr["state"] != "open":
                        raise ValueError("PR closed during feedback scan")
                    seen.add(key)
                    raw = github_pr._api(f"repos/{target}/pulls/{number}")
                    if state.digest(raw["body"].encode()) != pr["bodySha256"]:
                        raise ValueError("PR description changed during feedback scan")
                    events.append({"key": key, "bank": bank, "mr": pr, "prBody": raw["body"],
                                   "discussionSha256": discussion_digest(messages),
                                   "source": source, "observedAt": now()})
                except (ValueError, KeyError, TypeError) as exc:
                    stops.append({"id": bank["id"], "mr": number, "kind": kind, "reason": str(exc)})
        except (ValueError, KeyError, TypeError) as exc:
            stops.append({"mr": number, "reason": str(exc)})
    return {"events": events, "stops": stops}


def discussion_digest(messages):
    rows = []
    for kind, row in messages:
        if not isinstance(row, dict) or not isinstance(row.get("node_id"), str):
            raise ValueError("feedback discussion identity is incomplete")
        body = row.get("body") or ""
        text = clean_body(body)
        if text:
            rows.append({"kind": kind, "nodeId": row["node_id"], "text": text})
    return state.digest(state.encoded(sorted(rows, key=lambda r: (r["kind"], r["nodeId"]))))


def live_discussion(target, number):
    messages = [("body", github_pr._api(f"repos/{target}/pulls/{number}"))]
    for kind, endpoint in (("comment", f"issues/{number}/comments"),
                           ("review", f"pulls/{number}/reviews"),
                           ("inline", f"pulls/{number}/comments")):
        messages.extend((kind, row) for row in _pages(f"repos/{target}/{endpoint}"))
    if len(messages) > MAX_MESSAGES:
        raise ValueError("feedback discussion exceeds the message limit")
    return discussion_digest(messages)


def validate_event(event):
    if not isinstance(event, dict) or event.keys() != {
            "key", "bank", "mr", "prBody", "discussionSha256", "source", "observedAt"}:
        raise ValueError("feedback event requires its complete immutable observation")
    try:
        source, mr, bank = event["source"], event["mr"], event["bank"]
        if (source["kind"] not in _TYPES or not isinstance(source["nodeId"], str)
                or source["bodySha256"] != state.digest(source["body"].encode())
                or source["text"] != clean_body(source["body"])
                or source["textSha256"] != state.digest(source["text"].encode())
                or source["attachments"] != attachment_urls(source["text"])
                or state.digest(event["prBody"].encode()) != mr["bodySha256"]
                or mr["url"] != f'https://github.com/{github_pr.normalize(mr["target"])}/pull/{mr["number"]}'
                or type(mr["number"]) is not int or mr["number"] < 1 or mr["state"] != "open"
                or type(bank["id"]) is not int or bank["id"] < 0
                or not isinstance(bank["folder"], str)
                or event["key"] != event_key(mr["target"], mr["number"], source)):
            raise ValueError("feedback event hashes, bank or PR provenance changed")
        instant(event["observedAt"], "feedback observation")
        require_hash(event["discussionSha256"], "feedback discussion")
        require_hash(bank["metadataHash"], "feedback bank metadata")
        if bank["iconHash"] is not None:
            require_hash(bank["iconHash"], "feedback bank icon")
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("feedback event is incomplete") from exc


def requests(before):
    values = (before or {}).get("reviewFeedback", [])
    if not isinstance(values, list):
        raise ValueError("reviewFeedback must be a list")
    keys = set()
    for row in values:
        if not isinstance(row, dict) or row.get("status") not in _STATUSES:
            raise ValueError("invalid feedback record")
        key = require_hash(row.get("key"), "feedback.key")
        if key in keys:
            raise ValueError("duplicate feedback record")
        event = row.get("event")
        validate_event(event)
        if event["key"] != key:
            raise ValueError("feedback record is not bound to its source")
        if row["status"] not in {"ignored", "blocked", "superseded"}:
            if row.get("selectedUrl") not in event["source"]["attachments"]:
                raise ValueError("selected feedback attachment was not observed")
        required(row, "reason")
        if required(row, "quote") not in event["source"]["text"]:
            raise ValueError("feedback decision lost its actual quotation")
        if instant(row.get("recordedAt"), "feedback.recordedAt") < instant(event["observedAt"], "feedback observation"):
            raise ValueError("feedback decision predates its observation")
        keys.add(key)
    return values


def verify_source(event):
    validate_event(event)
    mr, source = event["mr"], event["source"]
    current = message(mr["target"], mr["number"], source["kind"], source["nodeId"])
    if current != source:
        raise ValueError("feedback source text, actual editor or permission changed")
    if live_discussion(mr["target"], mr["number"]) != event["discussionSha256"]:
        raise ValueError("PR discussion changed; inspect new human feedback before publication")


def verify_event(event):
    validate_event(event)
    mr = event["mr"]
    current = github_pr.inspect(mr["target"], mr["number"])
    keys = ("number", "url", "target", "head", "headCommit", "base", "title", "bodySha256", "state")
    if any(current.get(key) != mr.get(key) for key in keys) or current["state"] != "open":
        raise ValueError("feedback PR/head/description changed; rescan instead of guessing")
    verify_source(event)


def decision_state(before, event, decision, recorded_at):
    validate_event(event)
    values = requests(before)
    if any(row["key"] == event["key"] for row in values):
        raise ValueError("feedback event is already recorded; never enqueue it again")
    if (not isinstance(decision, dict) or decision.keys() != {"key", "decision", "selectedUrl", "quote", "reason", "supersedes"}
            or decision["key"] != event["key"] or decision["decision"] not in {"use", "ignore", "blocked"}):
        raise ValueError("feedback decision requires exact key/decision/selectedUrl/quote/reason/supersedes fields")
    required(decision, "reason")
    quote = required(decision, "quote")
    if quote not in event["source"]["text"]:
        raise ValueError("feedback decision quote was not in the person's actual message")
    selected = decision["selectedUrl"]
    if decision["decision"] == "use":
        if selected not in event["source"]["attachments"]:
            raise ValueError("select one actually observed attachment; no invented URL")
    elif selected is not None:
        raise ValueError("ignored/blocked feedback does not select a replacement")
    supersedes = decision["supersedes"]
    if (not isinstance(supersedes, list) or any(not isinstance(k, str) for k in supersedes)
            or len(set(supersedes)) != len(supersedes)):
        raise ValueError("supersedes must identify distinct previously queued requests")
    queued = {row["key"]: row for row in values if row["status"] == "queued"}
    if not set(supersedes).issubset(queued):
        raise ValueError("cannot supersede an unknown, processing or completed request")
    for key in supersedes:
        old = queued[key]["event"]
        if old["mr"]["url"] != event["mr"]["url"] or instant(old["observedAt"], "old feedback") > instant(event["observedAt"], "new feedback"):
            raise ValueError("superseding feedback must address an earlier request on the same MR")
    after = copy.deepcopy(before) if before is not None else {"schemaVersion": 1}
    result = copy.deepcopy(values)
    for row in result:
        if row["key"] in supersedes:
            row.update(status="superseded", supersededBy=event["key"])
    result.append({"key": event["key"], "event": copy.deepcopy(event),
        "status": {"use": "queued", "ignore": "ignored", "blocked": "blocked"}[decision["decision"]],
        "selectedUrl": selected, "quote": quote, "reason": decision["reason"],
        "supersedes": supersedes, "recordedAt": recorded_at})
    after["reviewFeedback"] = result
    return after


def scan(args):
    from argparse import Namespace
    repo = Path(args.repo).resolve(strict=True)
    if Path(state.git(repo, "rev-parse", "--show-toplevel").stdout.decode().strip()).resolve() != repo:
        raise ValueError("--repo must be the checkout root")
    session = Path(args.session).resolve()
    if session.exists() or session == repo or repo in session.parents:
        raise ValueError("feedback scan requires a new directory outside the checkout")
    remote = state.resolve_remote(repo, args.remote)
    target = repository_from_remote(remote)
    base = state.git(repo, "rev-parse", f"{args.base}^{{commit}}").stdout.decode().strip()
    _, commit, _, _ = state.initial_state(repo, Namespace(remote=remote, offline=False))
    session.mkdir(parents=True)
    summary = discover(repo, base, target, commit)
    snapshot = {"schemaVersion": 1, "purpose": KIND, "repo": str(repo), "baseCommit": base,
                "stateRemote": remote, "stateCommit": commit, "target": target,
                "selectedIds": sorted({e["bank"]["id"] for e in summary["events"]}),
                "events": summary["events"], "stops": summary["stops"]}
    state.save_new(session / "scan.json", state.encoded(snapshot))
    state.save_new(session / "session.json", state.encoded({key: value for key, value in snapshot.items() if key not in {"events", "stops", "target"}}))
    decisions = [{"key": event["key"], "decision": None, "selectedUrl": None,
                  "quote": None, "reason": None, "supersedes": []} for event in summary["events"]]
    state.save_new(session / "decisions-template.json", state.encoded(decisions))
    return {"status": "feedback-decisions-required" if decisions else "no-new-feedback",
            "session": str(session), **summary, "decisionsTemplate": str(session / "decisions-template.json"),
            "nextAction": "Read actual requests; use/ignore/block each new image-bearing message. Do not treat comparisons as requests. Prepare and publish intake, then start the normal queue."}


def _snapshot(session):
    path = session / "scan.json"
    snapshot = json.loads(state.regular(path))
    data = json.loads(state.regular(session / "session.json"))
    if (snapshot.get("purpose") != KIND or data != {key: value for key, value in snapshot.items() if key not in {"events", "stops", "target"}}):
        raise ValueError("invalid feedback intake context")
    return snapshot, {str(path): state.digest(state.regular(path)),
                      str(session / "session.json"): state.digest(state.regular(session / "session.json"))}


def prepare(args):
    session = Path(args.session).resolve()
    snapshot, bindings = _snapshot(session)
    decisions = json.loads(state.regular(Path(args.decisions).absolute()))
    if not isinstance(decisions, list) or not decisions:
        raise ValueError("feedback decisions must be a nonempty list")
    events = {event["key"]: event for event in snapshot["events"]}
    keys = [row.get("key") for row in decisions if isinstance(row, dict)]
    if len(keys) != len(decisions) or len(set(keys)) != len(keys) or not set(keys).issubset(events):
        raise ValueError("decision keys must be distinct events from this scan")
    at = now()
    results = [{"event": events[row["key"]], "decision": row, "recordedAt": at} for row in decisions]
    with state.writer(session) as output:
        objects = state.object_repo(output)
        head = state.fetch_head(objects, snapshot["stateRemote"])
        records = _records(objects, head, results, live=True)
        package = {"schemaVersion": 1, "scope": "state-publication", "target": snapshot["stateRemote"],
                   "ref": state.REF, "baseCommit": snapshot["baseCommit"],
                   "stateHistory": "present" if head else "unknown", "observedRef": head,
                   "ids": [row["id"] for row in records], "records": records,
                   "results": results, "reconciliation": {"kind": KIND}, "bindings": bindings}
        digest = state.digest(state.encoded(package))
        state.save_new(output / f"{digest}.json", state.encoded(package))
        return {"status": "awaiting-state-authorization", "packageHash": digest, "package": package,
                "nextAction": "Show and publish this intake with state authority; the next normal start prioritizes queued feedback. No asset or merge permission was granted."}


def _records(objects, head, results, *, live):
    grouped = {}
    for row in results:
        if row.keys() != {"event", "decision", "recordedAt"}:
            raise ValueError("invalid feedback decision result")
        event = row["event"]
        if live:
            verify_event(event)
        id = event["bank"]["id"]
        if type(id) is not int or id < 0:
            raise ValueError("invalid feedback bank ID")
        if id not in grouped:
            path = f"banks/state/{id}.json"
            before, digest = state.remote_record(objects, head, path)
            grouped[id] = {"id": id, "path": path, "before": before,
                           "beforeSha256": digest, "after": before}
        record = grouped[id]
        record["after"] = decision_state(record["after"], event, row["decision"], row["recordedAt"])
    return [grouped[id] for id in sorted(grouped)]


def authorization_context(session, package):
    snapshot, bindings = _snapshot(session)
    if (package["reconciliation"] != {"kind": KIND} or package["bindings"] != bindings
            or not set(package["ids"]).issubset(snapshot["selectedIds"])
            or package["baseCommit"] != snapshot["baseCommit"] or package["target"] != snapshot["stateRemote"]):
        raise ValueError("feedback package does not bind this intake context")
    return {key: value for key, value in snapshot.items() if key not in {"events", "stops", "target"}}


def validate_package(session, package, objects, head):
    authorization_context(session, package)
    snapshot, _ = _snapshot(session)
    events = {event["key"]: event for event in snapshot["events"]}
    for row in package["results"]:
        if events.get(row["event"]["key"]) != row["event"]:
            raise ValueError("feedback result differs from the immutable scan")
        if instant(row["recordedAt"], "feedback.recordedAt") > datetime.now(timezone.utc):
            raise ValueError("feedback decision is in the future")
    expected = _records(objects, package["observedRef"], package["results"], live=True)
    if expected != package["records"]:
        raise ValueError("feedback package changes more than the sourced intake decisions")
    # The generic state writer additionally checks exact affected before-bytes,
    # concurrent unrelated commits, authority and unknown-write receipts.


def add_subcommands(commands):
    sub = commands.add_parser("feedback-scan", help="read new reviewer image requests once before selection")
    sub.add_argument("--repo", required=True)
    sub.add_argument("--session", required=True)
    sub.add_argument("--base", default="HEAD")
    sub.add_argument("--remote")
    sub = commands.add_parser("feedback-prepare", help="prepare durable sourced feedback decisions; no push")
    sub.add_argument("--session", required=True)
    sub.add_argument("--decisions", required=True)


def dispatch(args):
    if args.command == "feedback-scan":
        return scan(args)
    from standing_authorization import describe_package
    return describe_package(prepare(args), Path(args.session).resolve(), "statePublish")
