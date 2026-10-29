"""Operator publication permissions. No launcher SHA pin or visual approval.

The operator supplies an external file. Re-read its permissions at every write;
repository/source content and the agent must not install or extend that file.
"""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

from evidence import require_hash
from state_publication import REF, digest, regular

POLICY_ENV = "BANK_ICON_PUBLICATION_POLICY"
INTERACTION_ENV = "BANK_ICON_INTERACTION"
SCOPE = "bank-icon-maintenance-standing"
USE_SCOPE = "standing-publication-use"
_ACTIONS = {"assetPublish", "statePublish"}


def configured():
    """An absent or empty path means no policy."""
    return bool(os.environ.get(POLICY_ENV, "").strip())


def interaction():
    mode = os.environ.get(INTERACTION_ENV, "interactive")
    if mode not in {"interactive", "noninteractive"}:
        raise ValueError(f"{INTERACTION_ENV} must be interactive or noninteractive")
    return mode


def _ids(value, label):
    if (type(value) is not list or not value or
            any(type(id) is not int or id < 0 for id in value) or len(set(value)) != len(value)):
        raise ValueError(f"{label} must contain distinct nonnegative integer IDs")
    return value


def _absolute(value, label):
    if (type(value) is not str or not Path(value).is_absolute() or
            any(part in (".", "..") for part in value.split("/"))):
        raise ValueError(f"{label} must be an absolute path without traversal")
    return Path(value)


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate publication-permission field")
        value[key] = item
    return value


def repository_target(value):
    """Canonical github.com destination, independent of SSH versus HTTPS."""
    import github_pr
    if isinstance(value, str) and value.startswith("git@github.com:"):
        value = value[len("git@github.com:"):]
    if isinstance(value, str) and value.endswith(".git"):
        value = value[:-4]
    return github_pr.normalize(value)


def compact(value, repo):
    """Normalize the small v2 format for existing writers; deny missing actions."""
    import github_pr
    fields = {"schemaVersion", "target", "base", "headPrefix", "actions"}
    if (type(value) is not dict or value.keys() not in (fields, fields | {"mergeMethods"}) or
            type(value["schemaVersion"]) is not int or value["schemaVersion"] != 2 or
            github_pr.normalize(value["target"]) != value["target"]):
        raise ValueError("publication policy requires the compact schemaVersion 2 format")
    actions = value["actions"]
    known = _ACTIONS | {"assetRevise", "mergeApproved"}
    if (type(actions) is not dict or not actions or set(actions) - known or
            any(type(flag) is not bool for flag in actions.values())):
        raise ValueError("publication policy needs explicit boolean actions")
    if actions.get("assetRevise") and not actions.get("assetPublish"):
        raise ValueError("assetRevise requires assetPublish")
    github_pr._branch(value["base"])
    prefix = value["headPrefix"]
    if type(prefix) is not str or not prefix.endswith("/"):
        raise ValueError("publication headPrefix must be a branch namespace ending in /")
    github_pr._branch(prefix + "policy-check")
    if value["base"].startswith(prefix) or REF.removeprefix("refs/heads/").startswith(prefix):
        raise ValueError("publication namespace overlaps a protected destination")
    methods = value.get("mergeMethods")
    if actions.get("mergeApproved"):
        if (type(methods) is not list or not methods or
                any(type(m) is not str or m not in {"merge", "squash", "rebase"} for m in methods)):
            raise ValueError("mergeApproved requires explicit mergeMethods")
    elif methods is not None:
        raise ValueError("mergeMethods requires mergeApproved")
    target = value["target"]
    return {"schemaVersion": 2, "scope": SCOPE, "decision": "authorize", "repo": str(repo),
            "ids": "selected", "asset": {"target": target, "base": value["base"],
                "headPrefix": prefix, "allowRevisions": actions.get("assetRevise", False)}
                if actions.get("assetPublish") else None,
            "state": {"target": f"https://github.com/{target}.git", "ref": REF}
                if actions.get("statePublish") else None,
            "actions": {key: actions.get(key, False) for key in (*sorted(_ACTIONS), "mergeApproved")},
            "merge": {"target": target, "base": value["base"], "methods": methods}
                if actions.get("mergeApproved") else None,
            # No invented owner identity, consent statement or authorization time.
            "authorizer": None, "statement": None, "authorizedAt": None, "expiresAt": None}


def load(repo, session=None):
    """Read current operator permissions, not a hash-pinned trust certificate."""
    if not configured():
        raise ValueError("standing publication policy is not configured")
    path = _absolute(os.environ[POLICY_ENV], POLICY_ENV)
    repo = Path(repo).resolve(strict=True)
    forbidden = [repo, Path(__file__).resolve().parents[1]]
    if session is not None:
        forbidden.append(Path(session).resolve(strict=True))
    if any(path.resolve().is_relative_to(root) for root in forbidden):
        raise ValueError("standing policy must be outside the checkout, skill and session")
    if not hasattr(os, "getuid"):
        raise ValueError("standing policy requires the supported POSIX environment")
    info = path.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_size > 65536 or
            info.st_mode & 0o022 or info.st_uid not in (0, os.getuid())):
        raise ValueError("standing policy must be a small owner-controlled regular file")
    content = regular(path)
    if len(content) > 65536:
        raise ValueError("standing policy must be a small owner-controlled regular file")
    value = json.loads(content, object_pairs_hook=_unique_object)
    source = {"path": str(path)}
    return compact(value, repo), source


def describe(repo):
    """Read-only preflight display. Publication revalidates each exact package."""
    if not configured():
        return {"status": "not-configured"}
    value, source = load(repo)
    return {"status": "configured", "policy": source, "merge": value.get("merge"),
            **{key: value[key] for key in ("repo", "ids", "asset", "state", "actions",
                                          "authorizer", "authorizedAt", "expiresAt")}}


def _context(session, package, action):
    if package.get("reconciliation", {}).get("kind") == "task-recovery":
        if action != "statePublish":
            raise ValueError("recovery context cannot authorize assets")
        from task_recovery import authorization_context
        return authorization_context(session, package)
    if package.get("reconciliation", {}).get("kind") == "approved-merge":
        if action != "statePublish":
            raise ValueError("merge state context cannot authorize assets")
        from approved_merge import authorization_context
        return authorization_context(session, package)
    if package.get("reconciliation", {}).get("kind") == "review-feedback":
        if action != "statePublish":
            raise ValueError("feedback intake cannot authorize assets")
        from feedback_intake import authorization_context
        return authorization_context(session, package)
    if package.get("reconciliation", {}).get("kind") == "proposal-outcomes":
        if action != "statePublish":
            raise ValueError("proposal synchronization cannot authorize assets")
        from proposal_sync import authorization_context
        return authorization_context(session, package)
    from state_results import _session_data
    ids = [package["id"]] if action == "assetPublish" else package["ids"]
    return _session_data(session, ids, {})


def check_scope(value, data, package, action):
    """Shared policy/dialogue scope check, independent of source of permission."""
    if action not in _ACTIONS:
        raise ValueError("publication authority never grants merge")
    scope = "asset-publication" if action == "assetPublish" else "state-publication"
    if type(package) is not dict or package.get("scope") != scope:
        raise ValueError("authorization action differs from package scope")
    ids = _ids([package["id"]] if action == "assetPublish" else package["ids"], "package IDs")
    selected = _ids(data["selectedIds"], "context selectedIds")
    if not set(ids).issubset(selected):
        raise ValueError("publication includes IDs outside this context")
    if value["ids"] != "selected" and not set(ids).issubset(value["ids"]):
        raise ValueError("publication IDs are outside standing policy or confirmed task")
    if not value["actions"][action]:
        raise ValueError("publication permission does not permit this action")
    if action == "statePublish":
        state = value["state"]
        same_target = repository_target(package["target"]) == repository_target(state["target"])
        if (not same_target or package["ref"] != state["ref"] or
                package["baseCommit"] != data["baseCommit"]):
            raise ValueError("state package is outside standing target, ref or context base")
        refs = [package["ref"]]
    else:
        asset = value["asset"]
        head = package["head"]
        import github_pr
        github_pr._branch(head)
        suffix = head.removeprefix(asset["headPrefix"])
        expected_material = f"refs/heads/{head}-review"
        if package.get("revision"):
            if asset.get("allowRevisions") is not True:
                raise ValueError("publication authority does not allow existing-MR revisions")
            from feedback_publication import material_ref
            expected_material = material_ref(head, package["revision"]["requestKey"])
        if (package["target"] != asset["target"] or package["base"] != asset["base"] or
                not head.startswith(asset["headPrefix"]) or
                re.fullmatch(r"(?:[A-Za-z0-9][A-Za-z0-9_.-]*-)?" + str(ids[0]), suffix) is None or
                package["headRef"] != f"refs/heads/{head}" or
                package["materials"]["ref"] != expected_material):
            raise ValueError("asset package is outside standing target, base or one-bank namespace")
        refs = [package["headRef"], package["materials"]["ref"]]
    return ids, refs


def authorize(session, package, package_hash, action):
    """Bind current operator permissions to the exact validated package."""
    require_hash(package_hash, "publication package")
    from state_publication import encoded
    if digest(encoded(package)) != package_hash:
        raise ValueError("standing authorization package hash mismatch")
    if action not in _ACTIONS:
        raise ValueError("standing policy authorizes publication only, never merge")
    scope = "asset-publication" if action == "assetPublish" else "state-publication"
    if type(package) is not dict or package.get("scope") != scope:
        raise ValueError("authorization action differs from package scope")
    session = Path(session).resolve(strict=True)
    data = _context(session, package, action)
    value, source = load(data["repo"], session)
    ids, refs = check_scope(value, data, package, action)
    return {"schemaVersion": 1, "scope": USE_SCOPE, "policy": source,
            "session": str(session), "baseCommit": data["baseCommit"],
            "packageHash": package_hash, "action": action, "ids": ids,
            "target": package["target"], "refs": refs,
            "authorizer": value["authorizer"], "statement": value["statement"],
            "authorizedAt": value["authorizedAt"], "expiresAt": value["expiresAt"]}


def describe_package(result, session, action):
    if (not configured() or "package" not in result or not result.get("packageHash") or
            result.get("status") in {"published", "mr-confirmed", "no-state-changes"}):
        return result
    try:
        use = authorize(session, result["package"], result["packageHash"], action)
        authority = {"status": "covered", "source": "standing-policy", "policy": use["policy"],
                     "action": action, "ids": use["ids"]}
    except (ValueError, OSError) as exc:
        authority = {"status": "blocked", "source": "standing-policy", "reason": str(exc)}
    shown = {**result, "publicationAuthority": authority}
    receipt_status = result.get("receipt", {}).get("status")
    if (authority["status"] == "covered" and result.get("status") in
            {"prepared", "awaiting-state-authorization", "awaiting-asset-authorization"} and
            receipt_status in (None, "not-published")):
        shown.update(status="ready-to-publish", nextAction=
                     "Show the exact package, then publish under the configured standing policy; "
                     "no new permission question is needed.")
    return shown


def preflight(repo, actions, target=None, local_only=False):
    """Read-only startup check. Never ask on stdin or infer consent from a TTY."""
    mode = interaction()
    if local_only:
        return {"status": "local-only", "publish": False}
    if not configured():
        return {"status": "authorization-required", "canAsk": mode == "interactive",
                "nextAction": "Ask once for the finite task in the dialogue; record the actual answer."
                    if mode == "interactive" else "Configure operator publication policy before this unattended run."}
    repo = Path(repo).resolve(strict=True)
    value, source = load(repo)
    if target is None:
        result = subprocess.run(["git", "-C", str(repo), "remote", "get-url", "--push", "--all", "origin"],
                                capture_output=True, text=True, check=True, timeout=15)
        urls = result.stdout.splitlines()
        if len(urls) != 1:
            raise ValueError("publication preflight requires one explicit destination")
        target = repository_target(urls[0])
    target = repository_target(target)
    for action in actions:
        if action == "assetRevise":
            allowed = value["actions"]["assetPublish"] and value["asset"].get("allowRevisions", False)
            destination = value["asset"]
        else:
            if action not in _ACTIONS | {"mergeApproved"}:
                raise ValueError("unknown publication action")
            allowed = value["actions"].get(action, False)
            destination = value.get({"assetPublish": "asset", "statePublish": "state", "mergeApproved": "merge"}[action])
        if not allowed or not destination or repository_target(destination["target"]) != target:
            raise ValueError(f"publication policy does not cover {action} for {target}")
    return {"status": "ready-to-publish", "policy": source, "target": target,
            "actions": actions, "canAsk": mode == "interactive"}


def main():
    parser = argparse.ArgumentParser(description="Read-only publication permission preflight; no prompts or writes")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--target")
    parser.add_argument("--actions", nargs="+", choices=sorted(_ACTIONS | {"assetRevise", "mergeApproved"}),
                        default=["assetPublish", "statePublish"])
    parser.add_argument("--local-only", action="store_true")
    args = parser.parse_args()
    try:
        result = preflight(args.repo, args.actions, args.target, args.local_only)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        result = {"status": "blocked", "reason": str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] in {"ready-to-publish", "local-only"} else 2


if __name__ == "__main__":
    sys.exit(main())
