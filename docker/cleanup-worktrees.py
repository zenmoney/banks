"""Remove only completed, unchanged session worktrees owned by this launcher run."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def git(repo, *args):
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], stderr=subprocess.PIPE, timeout=60
    )


def regular(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError(f"symlink in {path}")
    return path.read_bytes()


def completed(session, data, snapshot):
    if data.get("paused") is not False or len(data.get("banks", [])) != 1:
        return False
    if data["banks"][0].get("point") not in {
        "task-complete", "awaiting-state-publication", "waiting-mr",
        "attempt-complete", "needs-human"
    }:
        return False
    # A successful agent exit alone says nothing about publication. Require a
    # receipt bound to the current session and a terminal per-object result.
    # Research can finish with an error or uncertainty; an unknown write cannot.
    receipts = list((session / "state-publication").glob("*.receipt.json"))
    if any(json.loads(regular(path)).get("status") not in {"published", "no-state-changes"}
           for path in receipts):
        return False
    for receipt_path in receipts:
        key = receipt_path.name.removesuffix(".receipt.json")
        raw = regular(receipt_path.with_name(key + ".json"))
        package = json.loads(raw)
        if (hashlib.sha256(raw).hexdigest() != key
                or package.get("scope") != "state-publication"
                or package.get("ids") != data.get("selectedIds")
                or package.get("bindings", {}).get(str(session / "session.json"))
                != hashlib.sha256(snapshot).hexdigest()):
            continue
        results = package.get("results", [])
        if len(results) != 1 or results[0].get("id") != data["banks"][0].get("id"):
            continue
        before = (results[0].get("before") or {}).get("taskResults", [])
        state = results[0]["after"]
        if any(p.get("status") == "pending" and not p.get("mr")
               for p in state.get("proposals", [])):
            continue
        after = state.get("taskResults", [])
        changed = [row for row in after if row not in before]
        if changed and all(row.get("outcome") in {
            "published", "unchanged", "keep-current", "defer",
            "unconfirmed", "error", "needs-human", "unclear"
        } for row in changed):
            return True
    return False


def cleanup(repo, main, roots):
    repo, main = Path(repo).resolve(), Path(main).resolve()
    roots = [Path(root).absolute() for root in roots]
    common = Path(os.fsdecode(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")).strip())
    records = git(repo, "worktree", "list", "--porcelain", "-z").split(b"\0\0")
    for record in records:
        fields = record.split(b"\0")
        if not fields[0].startswith(b"worktree "):
            continue
        worktree = Path(os.fsdecode(fields[0][len(b"worktree "):]))
        if (worktree in {repo, main} or worktree.name != "worktree"
                or not any(root in worktree.parents for root in roots)):
            continue
        try:
            if any(p.is_symlink() for p in (worktree, *worktree.parents)):
                raise ValueError("symlinked worktree path")
            session = worktree.parent
            snapshot = regular(session / "session.json")
            data = json.loads(snapshot)
            git_dir = os.fsdecode(git(worktree, "rev-parse", "--absolute-git-dir")).strip()
            if (data.get("schemaVersion") != 2 or data.get("repo") != str(main)
                    or data.get("worktree") != str(worktree)
                    or data.get("worktreeGitDir") != git_dir
                    or Path(git_dir).parent != common / "worktrees"
                    or b"detached" not in fields
                    or os.fsdecode(git(worktree, "rev-parse", "HEAD")).strip() != data.get("baseCommit")):
                raise ValueError("session ownership or base commit changed")
            if not completed(session, data, snapshot):
                raise ValueError("no confirmed terminal task result for the current session")
            if git(worktree, "status", "--porcelain", "--untracked-files=all", "--ignored=matching"):
                raise ValueError("local changes or untracked/ignored files")
            git(repo, "worktree", "remove", str(worktree))
            print(f"Removed completed session worktree: {worktree}", file=sys.stderr)
        except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as exc:
            print(f"Retained session worktree: {worktree}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    cleanup(sys.argv[1], sys.argv[2], sys.argv[3:])
