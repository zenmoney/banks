"""A dialogue confirmation for one finite task and its single-bank sessions."""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sys

from evidence import instant
from state_publication import regular

SCOPE = "bank-icon-maintenance-run"
FILENAME = "publication-permission.json"


def authorize(path, session, package, action):
    import standing_authorization as permissions
    session = Path(session).resolve(strict=True)
    value = json.loads(regular(Path(path).absolute()), object_pairs_hook=permissions._unique_object)
    data = permissions._context(session, package, action)
    if type(value) is dict and type(value.get("schemaVersion")) is int and value["schemaVersion"] == 2:
        fields = {"schemaVersion", "scope", "decision", "repo", "runRoot", "ids",
                  "permissions", "authorizer", "statement", "authorizedAt"}
        if value.keys() != fields or value["scope"] != SCOPE or value["decision"] != "authorize":
            raise ValueError("task confirmation requires the bounded schema")
        root = permissions._absolute(value["runRoot"], "confirmation.runRoot").resolve(strict=True)
        repo = permissions._absolute(value["repo"], "confirmation.repo").resolve(strict=True)
        if (Path(path).absolute() != root / FILENAME or session == root or
                not session.is_relative_to(root) or repo != Path(data["repo"]).resolve(strict=True) or
                root.is_relative_to(repo) or root.is_relative_to(Path(__file__).resolve().parents[1])):
            raise ValueError("confirmation belongs to another task root or repository")
        grant = permissions.compact(value["permissions"], repo)
        if grant["actions"].get("mergeApproved"):
            raise ValueError("task publication confirmation never authorizes merge")
        grant["ids"] = permissions._ids(value["ids"], "confirmation.ids")
        _human_decision(value)
        permissions.check_scope(grant, data, package, action)
        return value
    raise ValueError("task confirmation requires schemaVersion 2; old session grants are not supported")


def _human_decision(value):
    if any(type(value[field]) is not str or not value[field].strip()
           for field in ("authorizer", "statement")):
        raise ValueError("run authorization needs a human authorizer and statement")
    if instant(value["authorizedAt"], "authorizedAt") > datetime.now(timezone.utc):
        raise ValueError("run authorization cannot be in the future")


def record(args):
    """Record an actual chat answer, never solicit input or manufacture consent.

    The agent supplies the user's real words only after asking the scoped question.
    This record is an audit of that interaction, not proof that a person spoke.
    """
    import standing_authorization as permissions
    if permissions.interaction() != "interactive":
        raise ValueError("noninteractive runs cannot record a dialogue confirmation; configure operator policy")
    repo = Path(args.repo).resolve(strict=True)
    root = permissions._absolute(args.run_root, "run root").resolve(strict=True)
    if (not root.is_dir() or root.is_relative_to(repo) or
            root.is_relative_to(Path(__file__).resolve().parents[1]) or any(root.iterdir())):
        raise ValueError("confirmation requires a new empty task directory outside the checkout and skill")
    ids = permissions._ids(args.ids, "selected IDs")
    actions = {name: name in args.actions for name in ("assetPublish", "assetRevise", "statePublish")}
    config = {"schemaVersion": 2, "target": args.target, "base": args.base,
              "headPrefix": args.head_prefix, "actions": actions}
    permissions.compact(config, repo)
    value = {"schemaVersion": 2, "scope": SCOPE, "decision": "authorize", "repo": str(repo),
             "runRoot": str(root), "ids": ids, "permissions": config,
             "authorizer": args.authorizer, "statement": args.statement,
             "authorizedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
    _human_decision(value)
    path = root / FILENAME
    content = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)
    return {"status": "recorded", "authorization": str(path), "runRoot": str(root), "ids": ids,
            "nextAction": "Create each bank session beneath runRoot; pass --run-authorization to each publication. No further permission question within this task."}


def main():
    parser = argparse.ArgumentParser(description="Record a real dialogue permission for one finite task, after the person answers")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--ids", type=int, nargs="+", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--base", default="main")
    parser.add_argument("--head-prefix", default="bank-icon-maintenance/")
    parser.add_argument("--actions", nargs="+", choices=["assetPublish", "assetRevise", "statePublish"],
                        default=["assetPublish", "statePublish"])
    parser.add_argument("--authorizer", required=True)
    parser.add_argument("--statement", required=True, help="Actual affirmative response, not an invented approval")
    try:
        result = record(parser.parse_args())
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
