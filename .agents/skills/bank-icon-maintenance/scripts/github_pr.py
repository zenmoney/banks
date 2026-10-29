"""Small, fail-closed GitHub API boundary for asset pull requests."""

import hashlib
import copy
from contextlib import contextmanager
from contextvars import ContextVar
import json
import re
import subprocess
from datetime import datetime, timezone
from urllib.parse import urlsplit


_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+\Z")
_QUERY = """query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    nameWithOwner url isPrivate viewerPermission defaultBranchRef { name }
  }
}"""

_REVIEW_QUERY = """query($id: ID!) {
  node(id: $id) {
    ... on PullRequestReview {
      __typename id url body createdAt updatedAt
      author { __typename login }
      pullRequest { number repository { nameWithOwner } }
    }
  }
}"""



def normalize(target):
    if not isinstance(target, str):
        raise ValueError("GitHub target must be owner/repo or its https://github.com URL")
    if target.startswith("https://"):
        parsed = urlsplit(target)
        if (parsed.netloc.lower() != "github.com" or parsed.username or parsed.password
                or parsed.port or parsed.query or parsed.fragment):
            raise ValueError("GitHub target must be an unqualified github.com repository URL")
        target = parsed.path.strip("/")
    if not _REPOSITORY.fullmatch(target):
        raise ValueError("GitHub target must be owner/repo or its https://github.com URL")
    return target


def _branch(value):
    if (not isinstance(value, str) or not value or value.startswith("-") or value.endswith("/")
            or value.endswith(".") or value == "@" or ".." in value or "@{" in value
            or "//" in value or any(component.startswith(".") or component.endswith(".lock")
                                     for component in value.split("/"))
            or re.search(r"[\x00-\x20\x7f~^:?*\[\\]", value)):
        raise ValueError("invalid Git branch name")
    return value


def _api(*args):
    from operation_budget import timeout as budget_timeout, check as budget_check
    limit = budget_timeout(120)
    try:
        result = subprocess.run(
            ["gh", "api", "--hostname", "github.com", *args],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=limit,
            env=_noninteractive_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        budget_check()
        raise ValueError("GitHub API transport unavailable") from exc
    if result.returncode != 0:
        raise ValueError(f"GitHub API request failed: {result.stderr.strip() or 'unknown error'}")
    try:
        value = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise ValueError("GitHub API returned invalid JSON") from exc
    if isinstance(value, dict) and ("errors" in value or "message" in value and "data" not in value and not (type(value.get("merged")) is bool and ("sha" in value or not value["merged"]))):
        raise ValueError("GitHub API returned an error")
    return value


def _noninteractive_environment():
    import os

    return {**os.environ, "GH_PROMPT_DISABLED": "1", "GIT_TERMINAL_PROMPT": "0",
            "GH_NO_UPDATE_NOTIFIER": "1"}


def repository(target):
    target = normalize(target)
    owner, name = target.split("/")
    response = _api("graphql", "-f", f"query={_QUERY}", "-f", f"owner={owner}",
                    "-f", f"name={name}")
    try:
        repo = response["data"]["repository"]
        canonical = repo["nameWithOwner"]
        url = repo["url"]
        private = repo["isPrivate"]
        branch = repo["defaultBranchRef"]["name"]
        permission = repo["viewerPermission"]
    except (KeyError, TypeError) as exc:
        raise ValueError("GitHub repository response incomplete") from exc
    if (not isinstance(canonical, str) or canonical.lower() != target.lower()
            or url != f"https://github.com/{canonical}" or type(private) is not bool
            or not isinstance(branch, str) or not branch
            or permission not in {"ADMIN", "MAINTAIN", "WRITE", "TRIAGE", "READ", "NONE"}):
        raise ValueError("GitHub repository response inconsistent")
    return {"nameWithOwner": canonical, "url": url, "isPrivate": private,
            "defaultBranch": branch, "viewerPermission": permission}


def _observation(value, target):
    try:
        head = value["head"]
        base = value["base"]
        head_repo = head["repo"]["full_name"]
        base_repo = base["repo"]["full_name"]
        number = value["number"]
        url = value["html_url"]
        state = value["state"]
        merged_at = value["merged_at"]
        title, body = value["title"], value["body"]
        head_ref, head_commit = head["ref"], head["sha"]
        base_ref, base_commit = base["ref"], base["sha"]
    except (KeyError, TypeError) as exc:
        raise ValueError("GitHub pull request response incomplete") from exc
    if (not isinstance(head_repo, str) or not isinstance(base_repo, str)
            or base_repo.lower() != target.lower() or type(number) is not int or number < 1
            or url != f"https://github.com/{base_repo}/pull/{number}"
            or state not in {"open", "closed"}
            or (merged_at is not None and (state != "closed" or not isinstance(merged_at, str)))
            or not isinstance(title, str) or not title.strip()
            or not isinstance(body, str) or not body.strip()
            or any(not isinstance(field, str) or not field for field in
                   (head_ref, head_commit, base_ref, base_commit))):
        raise ValueError("GitHub pull request response inconsistent")
    return {"number": number, "url": url,
            "state": "merged" if merged_at is not None else state,
            "head": head_ref, "headCommit": head_commit, "base": base_ref,
            "baseCommit": base_commit, "target": base_repo, "headRepository": head_repo,
            "title": title, "bodySha256": hashlib.sha256(body.encode("utf-8")).hexdigest()}


def lookup(target, head):
    target = normalize(target)
    head = _branch(head)
    owner = target.split("/")[0]
    pages = _api(f"repos/{target}/pulls", "-X", "GET", "-f", "state=all",
                 "-f", f"head={owner}:{head}", "-F", "per_page=100", "--paginate", "--slurp")
    if not isinstance(pages, list) or not pages or any(not isinstance(page, list) for page in pages):
        raise ValueError("GitHub pull request pagination incomplete")
    matches = []
    for page in pages:
        for item in page:
            observation = _observation(item, target)
            if observation["head"] != head:
                raise ValueError("GitHub pull request lookup returned a different head")
            if observation["headRepository"].lower() == target.lower():
                del observation["headRepository"]
                matches.append(observation)
    return matches

_OPEN_CACHE = ContextVar("task_open_pr_snapshot", default=None)


@contextmanager
def open_path_snapshot():
    """One read-only planning call only; publication never reuses this snapshot."""
    token = _OPEN_CACHE.set({})
    try:
        yield
    finally:
        _OPEN_CACHE.reset(token)


def open_for_path(target, path):
    """Observe every open PR that touches an exact catalog path, across all heads."""
    target = normalize(target)
    if (not isinstance(path, str) or not path.startswith("banks/") or
            not path.endswith("/icon.svg") or ".." in path.split("/") or
            "" in path.split("/")):
        raise ValueError("invalid bank icon path")
    cache = _OPEN_CACHE.get()
    if cache is not None and target in cache:
        return copy.deepcopy(cache[target].get(path, []))
    index = {}
    pages = _api(f"repos/{target}/pulls", "-X", "GET", "-f", "state=open",
                 "-F", "per_page=100", "--paginate", "--slurp")
    if not isinstance(pages, list) or not pages or any(not isinstance(page, list) for page in pages):
        raise ValueError("GitHub open pull request pagination incomplete")
    matches = []
    for page in pages:
        for item in page:
            try:
                number = item["number"]
                state = item["state"]
            except (KeyError, TypeError) as exc:
                raise ValueError("GitHub open pull request list incomplete") from exc
            if type(number) is not int or number < 1 or state != "open":
                raise ValueError("GitHub open pull request list inconsistent")
            file_pages = _api(f"repos/{target}/pulls/{number}/files", "-X", "GET",
                              "-F", "per_page=100", "--paginate", "--slurp")
            if (not isinstance(file_pages, list) or not file_pages or
                    any(not isinstance(files, list) for files in file_pages) or
                    sum(map(len, file_pages)) >= 3000):
                raise ValueError("GitHub pull request file pagination incomplete")
            touches = False
            bank_paths = set()
            for files in file_pages:
                for file in files:
                    if not isinstance(file, dict) or not isinstance(file.get("filename"), str):
                        raise ValueError("GitHub pull request file response incomplete")
                    touches |= path in (file["filename"], file.get("previous_filename"))
                    bank_paths.update(p for p in (file["filename"], file.get("previous_filename"))
                                      if isinstance(p, str) and p.startswith("banks/") and p.endswith("/icon.svg"))
            if touches or bank_paths and cache is not None:
                observation = _observation(item, target)
                del observation["headRepository"]
                if touches:
                    matches.append(observation)
                for p in bank_paths:
                    index.setdefault(p, []).append(observation)
    if cache is not None:
        cache[target] = index
    return matches



def inspect(target, number):
    target = normalize(target)
    if type(number) is not int or number < 1:
        raise ValueError("pull request number must be a positive integer")
    observation = _observation(_api(f"repos/{target}/pulls/{number}"), target)
    if observation["number"] != number or observation["headRepository"].lower() != target.lower():
        raise ValueError("pull request does not belong to this repository head")
    del observation["headRepository"]
    return observation


def _utc_timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", value):
        raise ValueError("GitHub timestamp must be UTC")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("GitHub timestamp invalid") from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("GitHub timestamp must be UTC")
    return parsed


def outcome(target, number):
    """Observe the exact PR outcome without changing the existing inspect contract."""
    target = normalize(target)
    if type(number) is not int or number < 1:
        raise ValueError("pull request number must be a positive integer")
    value = _api(f"repos/{target}/pulls/{number}")
    observation = _observation(value, target)
    if observation["number"] != number or observation["headRepository"].lower() != target.lower():
        raise ValueError("pull request does not belong to this repository head")
    try:
        merged, merge_commit, closed_at = (value[key] for key in
                                            ("merged", "merge_commit_sha", "closed_at"))
    except (KeyError, TypeError) as exc:
        raise ValueError("GitHub pull request outcome incomplete") from exc
    merged_at = value["merged_at"]
    state = value["state"]
    if (type(merged) is not bool or merged != (merged_at is not None)
            or (state == "open") != (closed_at is None)):
        raise ValueError("GitHub pull request outcome inconsistent")
    closed_time = _utc_timestamp(closed_at) if closed_at is not None else None
    if (merge_commit is not None and (not isinstance(merge_commit, str)
            or not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", merge_commit))):
        raise ValueError("GitHub pull request merge commit inconsistent")
    if merged:
        merged_time = _utc_timestamp(merged_at)
        if merge_commit is None or merged_time > closed_time:
            raise ValueError("GitHub pull request merge inconsistent")
    del observation["headRepository"]
    return {**observation, "mergeCommit": merge_commit if merged else None,
            "mergedAt": merged_at, "closedAt": closed_at}


def comment(target, number, url):
    """Fetch a specific human comment linked to the exact PR, without inferring decisions."""
    target = normalize(target)
    if type(number) is not int or number < 1:
        raise ValueError("pull request number must be a positive integer")
    pr = _api(f"repos/{target}/pulls/{number}")
    try:
        base_repo = pr["base"]["repo"]["full_name"]
        pr_number, pr_url = pr["number"], pr["html_url"]
    except (KeyError, TypeError) as exc:
        raise ValueError("GitHub comment pull request identity incomplete") from exc
    if (not isinstance(base_repo, str) or base_repo.lower() != target.lower()
            or type(pr_number) is not int or pr_number != number
            or pr_url != f"https://github.com/{base_repo}/pull/{number}"):
        raise ValueError("GitHub comment pull request identity inconsistent")
    observation = {"url": pr_url, "target": base_repo}
    if not isinstance(url, str):
        raise ValueError("comment URL must identify a known GitHub PR comment")
    match = re.fullmatch(re.escape(observation["url"]) +
                         r"#(issuecomment-|discussion_r|pullrequestreview-)([1-9]\d*)", url)
    if match is None:
        issue_url = observation["url"].replace(f"/pull/{number}", f"/issues/{number}")
        match = re.fullmatch(re.escape(issue_url) + r"#(issuecomment-)([1-9]\d*)", url)
    if match is None:
        raise ValueError("comment URL must identify a known GitHub PR comment")
    prefix, comment_id = match.group(1), int(match.group(2))
    kind = {"issuecomment-": "issue-comment", "discussion_r": "review-comment",
            "pullrequestreview-": "review"}[prefix]
    endpoint = (f"issues/comments/{comment_id}" if kind == "issue-comment" else
                f"pulls/comments/{comment_id}" if kind == "review-comment" else
                f"pulls/{number}/reviews/{comment_id}")
    value = _api(f"repos/{observation['target']}/{endpoint}")
    try:
        user = value["user"]
        author = user["login"]
        body = value["body"]
        created_at = updated_at = None
        if kind != "review":
            created_at, updated_at = value["created_at"], value["updated_at"]
        actual_id, actual_url = value["id"], value["html_url"]
        parent_url = value["issue_url" if kind == "issue-comment" else "pull_request_url"]
    except (KeyError, TypeError) as exc:
        raise ValueError("GitHub comment response incomplete") from exc
    if (type(actual_id) is not int or actual_id != comment_id or actual_url != url
            or parent_url != (f"https://api.github.com/repos/{observation['target']}/"
                              f"{'issues' if kind == 'issue-comment' else 'pulls'}/{number}")
            or not isinstance(body, str) or not body.strip()
            or user.get("type") != "User" or not isinstance(author, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", author)
            or author.lower() == "ghost" or author.lower().endswith("[bot]")):
        raise ValueError("GitHub comment provenance inconsistent")
    if kind == "review":
        try:
            node_id = value["node_id"]
            if not isinstance(node_id, str) or not node_id:
                raise ValueError("GitHub review node identifier invalid")
            node = _api("graphql", "-f", f"query={_REVIEW_QUERY}", "-f", f"id={node_id}")["data"]["node"]
            parent = node["pullRequest"]
            graph_author = node["author"]
            created_at, updated_at = node["createdAt"], node["updatedAt"]
        except (KeyError, TypeError) as exc:
            raise ValueError("GitHub review provenance incomplete") from exc
        try:
            consistent = (node["__typename"] == "PullRequestReview"
                          and node["id"] == node_id
                          and node["url"] == url and node["body"] == body
                          and type(parent["number"]) is int and parent["number"] == number
                          and parent["repository"]["nameWithOwner"] == observation["target"]
                          and graph_author["__typename"] == "User"
                          and graph_author["login"] == author)
        except (KeyError, TypeError) as exc:
            raise ValueError("GitHub review provenance incomplete") from exc
        if not consistent:
            raise ValueError("GitHub review provenance inconsistent")
    created_time = _utc_timestamp(created_at)
    updated_time = _utc_timestamp(updated_at)
    if updated_time < created_time:
        raise ValueError("GitHub comment timestamps inconsistent")
    return {"url": url, "body": body, "author": author,
            "createdAt": created_at, "updatedAt": updated_at, "kind": kind}


def create(target, head, base, title, body):
    target = normalize(target)
    head, base = _branch(head), _branch(base)
    if (not isinstance(title, str) or not title.strip() or not isinstance(body, str)
            or not body.strip()):
        raise ValueError("pull request title and body must be nonempty")
    observation = _observation(
        _api(f"repos/{target}/pulls", "-X", "POST", "-f", f"head={head}",
             "-f", f"base={base}", "-f", f"title={title}", "-f", f"body={body}"), target)
    if (observation["head"] != head or observation["base"] != base
            or observation["headRepository"].lower() != target.lower()
            or observation["title"] != title
            or observation["bodySha256"] != hashlib.sha256(body.encode("utf-8")).hexdigest()):
        raise ValueError("created pull request does not match the requested repository/branches")
    del observation["headRepository"]
    return observation
