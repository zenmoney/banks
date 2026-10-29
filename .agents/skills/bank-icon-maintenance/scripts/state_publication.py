"""One session writer for immutable state packages and separately authorized Git publication."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import uuid
from urllib.parse import urlparse

REF = "refs/heads/banks-icon-agent-state"
TRACKING_REF = "refs/remotes/origin/banks-icon-agent-state"
FETCH_OPTIONS = ("--no-tags", "--no-prune", "--no-prune-tags", "--no-write-fetch-head",
                 "--no-recurse-submodules", "--refmap=", "--no-auto-gc")


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def regular(path):
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError(f"symlink not allowed: {parent}")
    if not path.is_file():
        raise ValueError(f"regular file required: {path}")
    return path.read_bytes()


def git(repo, *args, check=True, input=None, env=None, timeout=None):
    from operation_budget import timeout as budget_timeout, check as budget_check
    timeout = budget_timeout(timeout)
    command = ["git", "-C", str(repo), "-c", "protocol.ext.allow=never", *args]
    if timeout is None:
        result = subprocess.run(command, input=input, capture_output=True, env=env)
    else:
        # Kill the transport/helper process group too: an orphan SSH process can
        # otherwise keep captured pipes open after the Git parent has timed out.
        with subprocess.Popen(command, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=env, start_new_session=True) as process:
            try:
                stdout, stderr = process.communicate(input, timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.communicate()
                budget_check()
                raise
            result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    if check and result.returncode:
        raise ValueError(f"git {args[0]} failed: {result.stderr.decode(errors='replace').strip()}")
    return result


def remote_target(value):
    """Validate a concrete Git destination without rewriting SSH path semantics."""
    if (not isinstance(value, str) or not value or value != value.strip() or
            any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError("remote must be a nonempty concrete Git destination")
    if Path(value).is_absolute():
        path = Path(value).resolve(strict=True)
        if git(path, "rev-parse", "--is-bare-repository").stdout.strip() != b"true":
            raise ValueError("local publication target must be an explicit bare repository")
        return str(path)
    if "://" in value:
        parsed = urlparse(value)
        # Accessing port also rejects malformed/non-numeric/out-of-range ports.
        _ = parsed.port
        if (parsed.scheme in {"https", "ssh"} and parsed.hostname and
                not parsed.hostname.startswith("-") and parsed.password is None and
                parsed.path not in ("", "/") and not parsed.query and not parsed.fragment and
                not any(char.isspace() for char in value)):
            return value
    else:
        # SCP-like relative paths are home-relative; ssh://host/path is root-relative.
        # Preserve it verbatim rather than performing a lossy URL conversion.
        match = re.fullmatch(
            r"(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?"
            r"(?:[A-Za-z0-9][A-Za-z0-9.-]*|\[[0-9A-Fa-f:.]+\]):([^\s]+)", value)
        if match and not match[1].startswith((":", "-", "//")):
            return value
    raise ValueError("remote must be a concrete HTTPS/SSH URL, SCP-like SSH address, "
                     "or absolute bare repository path; omit --remote to use origin's sole push URL")


def resolve_remote(repo, explicit=None, *, optional=False):
    """Read configuration only; target discovery is not publication permission."""
    if explicit is not None:
        return remote_target(explicit)
    remotes = git(repo, "remote").stdout.decode().splitlines()
    if "origin" not in remotes:
        if optional:
            return None
        raise ValueError("origin is not configured; specify a concrete --remote destination")
    response = git(repo, "remote", "get-url", "--push", "--all", "origin")
    targets = response.stdout.decode().splitlines()
    if len(targets) != 1 or not targets[0]:
        raise ValueError("origin must have exactly one push URL; specify --remote to select a destination")
    return remote_target(targets[0])


def _remote_read(repo, *args, check=True):
    """Bound network reads; do not turn authentication/network failure into absence."""
    try:
        return git(repo, *args, check=check, timeout=60,
                   env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    except subprocess.TimeoutExpired as exc:
        raise ValueError("remote state read timed out after 60 seconds; check access and retry the read. "
                         "No automatic local-history fallback is permitted.") from exc


def cache_state(repo, target, pin, commit):
    """Expose a fetched snapshot without resetting local work or another target's refs."""
    result = {"status": "synced", "repo": str(repo), "target": target,
              "commit": commit, "refs": []}
    try:
        configured = resolve_remote(repo, optional=True)
        if configured is not None and configured != target:
            return {**result, "status": "skipped-other-target",
                    "warning": "Target differs from origin; only the private snapshot was updated."}
        refs = [REF]
        fetch_urls = git(repo, "remote", "get-url", "--all", "origin", check=False)
        if fetch_urls.returncode == 0 and fetch_urls.stdout.decode().splitlines() == [target]:
            refs.append(TRACKING_REF)
        elif configured is not None:
            result["warning"] = "Origin fetch URL differs; its remote-tracking ref was not changed."
        for ref in refs:
            before = git(repo, "rev-parse", "--verify", "--quiet", ref, check=False).stdout.decode().strip() or None
            row = {"ref": ref, "before": before}
            if git(repo, "symbolic-ref", "-q", ref, check=False).returncode == 0:
                row.update(status="skipped", reason="symbolic ref; resolve it manually, do not reset it")
            elif before == commit:
                row.update(status="up-to-date", after=commit)
            else:
                if ref == REF:
                    # A local-only fetch enforces FF-only updates and Git's checked-out
                    # worktree protection. Never pass '+' or --update-head-ok here.
                    response = git(repo, "-c", "protocol.file.allow=always", "fetch", *FETCH_OPTIONS,
                                   ".", f"{pin}:{ref}", check=False, timeout=60)
                else:
                    # Remote-tracking refs are observations, not unpublished local work.
                    response = git(repo, "update-ref", "--no-deref", ref, commit,
                                   before or "0" * len(commit), check=False)
                if response.returncode:
                    row.update(status="skipped", reason=response.stderr.decode(errors="replace").strip())
                else:
                    row.update(status="updated" if before else "created", after=commit)
            result["refs"].append(row)
        if any(row["status"] == "skipped" for row in result["refs"]):
            result.update(status="partial", nextAction="Inspect skipped refs; do not reset or force-update them. "
                          "Online queue selection still uses the fetched snapshot.")
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        result.update(status="sync-failed", warning=str(exc), nextAction="Retry state-sync after fixing local access; "
                      "do not repeat a confirmed publication.")
    return result


def initial_state(repo, args):
    """Freeze selection input and safely refresh visible service refs, not the checkout."""
    from datetime import datetime, timezone
    explicit = getattr(args, "remote", None)
    target = resolve_remote(repo, explicit, optional=True)
    if target is None:
        ref = next((ref for ref in (REF, "refs/remotes/origin/banks-icon-agent-state")
                    if git(repo, "show-ref", "--verify", "--quiet", ref, check=False).returncode == 0), None)
        commit = git(repo, "rev-parse", f"{ref}^{{commit}}").stdout.decode().strip() if ref else None
        read = {"status": "no-origin", "target": target, "remoteRef": REF,
                "warning": "Local snapshot only; server history was not refreshed."}
        return ref, commit, target, read
    response = _remote_read(repo, "ls-remote", "--exit-code", target, REF, check=False)
    read = {"status": "absent", "target": target, "remoteRef": REF,
            "observedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
    if response.returncode == 2:
        # No advertised ref is an observation, not proof of no historical state.
        return None, None, target, read
    if response.returncode:
        raise ValueError("remote state could not be read; no queue was created. "
                         "Check access before starting the task: " +
                         response.stderr.decode(errors="replace").strip())
    rows = [row.split() for row in response.stdout.decode().splitlines()]
    if (len(rows) != 1 or len(rows[0]) != 2 or rows[0][1] != REF or
            re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", rows[0][0]) is None):
        raise ValueError("remote state ref is ambiguous or invalid; no queue was created")
    # Each start has a private pin, so concurrent fetches cannot change its input
    # and future garbage collection cannot lose a paused session's snapshot.
    pin = f"refs/bank-icon-maintenance/state-snapshots/{uuid.uuid4().hex}"
    _remote_read(repo, "fetch", *FETCH_OPTIONS, target, f"{REF}:{pin}")
    commit = git(repo, "rev-parse", f"{pin}^{{commit}}").stdout.decode().strip()
    if commit != rows[0][0]:
        git(repo, "update-ref", "-d", pin, commit)
        raise ValueError("remote state changed during fetch; retry the read before selecting banks")
    read["status"] = "fetched"
    read["localSync"] = cache_state(repo, target, pin, commit)
    return pin, commit, target, read


@contextmanager
def writer(session):
    from environment import require_posix
    require_posix()
    import fcntl
    if not session.is_dir() or session.is_symlink():
        raise ValueError("existing regular session directory required")
    root = session / "state-publication"
    if root.is_symlink():
        raise ValueError("publication directory cannot be a symlink")
    root.mkdir(exist_ok=True)
    lock = root / "writer.lock"
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("another state writer owns this session") from exc
        yield root
    finally:
        os.close(descriptor)


def object_repo(root):
    repo = root / "objects.git"
    if repo.is_symlink():
        raise ValueError("publication object repository cannot be a symlink")
    if not repo.exists():
        git(root, "init", "--bare", "-q", str(repo))
    return repo


def fetch_head(repo, target):
    response = _remote_read(repo, "ls-remote", "--exit-code", target, REF, check=False)
    if response.returncode == 2:
        return None
    if response.returncode:
        raise ValueError("remote state ref could not be established: " + response.stderr.decode(errors="replace"))
    _remote_read(repo, "fetch", "--no-tags", "--no-prune", "--no-prune-tags",
                 "--no-recurse-submodules", "--refmap=", "--no-auto-gc", target, REF)
    return git(repo, "rev-parse", "FETCH_HEAD^{commit}").stdout.decode().strip()


def remote_record(repo, commit, path):
    if commit is None:
        return None, None
    tree = git(repo, "ls-tree", commit, "--", path).stdout.decode().strip()
    if not tree:
        return None, None
    if tree.split()[0] not in {"100644", "100755"}:
        raise ValueError(f"remote state is not a regular file: {path}")
    from session import read_state
    state, error = read_state(repo, commit, {path}, int(Path(path).stem))
    if error or state is None or "id" in state or "iconUpdatedAt" in state:
        raise ValueError(error or f"unsupported remote per-bank state: {path}")
    content = git(repo, "show", f"{commit}:{path}").stdout
    return state, digest(content)


def save_new(path, content):
    if path.exists():
        if regular(path) != content:
            raise ValueError("immutable publication artifact already differs")
        return
    descriptor, name = tempfile.mkstemp(prefix="package-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def confirmed_prior(root, repo, target, head, requested=None):
    hashes = (requested if requested is not None else
              [path.name.removesuffix(".receipt.json") for path in root.glob("*.receipt.json")])
    prior = []
    for package_hash in sorted(hashes):
        package = load_package(root, package_hash)
        receipt = json.loads(regular(root / f"{package_hash}.receipt.json"))
        if package["target"] == target and landed(repo, head, receipt.get("commit"), package):
            prior.append({**package, "packageHash": package_hash})
        elif requested is not None:
            raise ValueError("prior publication is no longer confirmed; resolve and show a new package")
    return prior


def prepare(args, session, root):
    from state_results import validate_results
    from state_reconciliation import reconcile_records
    data, originals, bindings = validate_results(session, args.ids)
    target = resolve_remote(Path(data["repo"]),
                            args.remote if args.remote is not None else data.get("stateRemote"))
    repo = object_repo(root)
    head = fetch_head(repo, target)
    observed = []
    for record in originals:
        before, before_hash = remote_record(repo, head, record["path"])
        observed.append({"id": record["id"], "path": record["path"], "before": before,
                         "beforeSha256": before_hash})
    resolution = json.loads(regular(Path(args.resolution).absolute())) if args.resolution else None
    records, provenance = reconcile_records(session, originals, observed, target, resolution,
                                            confirmed_prior(root, repo, target, head))
    package = {"schemaVersion": 1, "scope": "state-publication", "target": target,
               "ref": REF, "baseCommit": data["baseCommit"],
               "stateHistory": data["stateHistory"],
               "observedRef": head, "ids": [row["id"] for row in records],
               "records": records, "results": originals, "reconciliation": provenance, "bindings": bindings}
    content = encoded(package)
    package_hash = digest(content)
    save_new(root / f"{package_hash}.json", content)
    status = "no-state-changes" if all(row["before"] == row["after"] for row in records) else "awaiting-state-authorization"
    return {"status": status, "packageHash": package_hash, "package": package}


def _null_base_paths(session, ids, bindings):
    """Null binds only the selected bank's absent canonical icon."""
    snapshot = session / "session.json"
    if type(bindings.get(str(snapshot))) is not str:
        raise ValueError("invalid state input binding: unbound session baseline")
    data = json.loads(regular(snapshot))
    if (type(data) is not dict or data.get("worktree") != str(session / "worktree") or
            type(data.get("selectedIds")) is not list or type(data.get("banks")) is not list):
        raise ValueError("invalid state input binding: no canonical selected baseline")
    allowed = set()
    for bank in data["banks"]:
        if (type(bank) is not dict or type(bank.get("id")) is not int or
                bank["id"] not in ids or bank["id"] not in data["selectedIds"] or
                "iconHash" not in bank or bank["iconHash"] is not None):
            continue
        folder = bank.get("folder")
        if (type(folder) is not str or len(Path(folder).parts) != 2 or
                Path(folder).parts[0] != "banks" or Path(folder).parts[1] == ".." or
                str(Path(folder)) != folder):
            raise ValueError("invalid state input binding: noncanonical bank folder")
        allowed.add(str(session / "worktree" / folder / "icon.svg"))
    return allowed


def load_package(root, package_hash):
    if re.fullmatch(r"[0-9a-f]{64}", package_hash) is None:
        raise ValueError("package must be a SHA-256 digest")
    content = regular(root / f"{package_hash}.json")
    if digest(content) != package_hash:
        raise ValueError("immutable package hash mismatch; show and authorize a new package")
    package = json.loads(content)
    fields = {"schemaVersion", "scope", "target", "ref", "baseCommit", "stateHistory",
              "observedRef", "ids", "records", "results", "reconciliation", "bindings"}
    if (type(package) is not dict or package.keys() != fields or
            type(package["schemaVersion"]) is not int or package["schemaVersion"] != 1 or
            package["scope"] != "state-publication" or package["ref"] != REF):
        raise ValueError("invalid state publication scope, schema or ref")
    ids, records, bindings = package["ids"], package["records"], package["bindings"]
    if (type(ids) is not list or not ids or any(type(id) is not int or id < 0 for id in ids) or
            len(set(ids)) != len(ids) or type(records) is not list or len(ids) != len(records) or
            type(bindings) is not dict or not bindings):
        raise ValueError("invalid state package records or bindings")
    for id, row in zip(ids, records):
        if (type(row) is not dict or row.keys() != {"id", "path", "before", "after", "beforeSha256"} or
                type(row["id"]) is not int or row["id"] != id or row["path"] != f"banks/state/{id}.json" or
                type(row["after"]) is not dict or (row["before"] is not None and type(row["before"]) is not dict) or
                (row["before"] is None) != (row["beforeSha256"] is None) or
                (row["beforeSha256"] is not None and not re.fullmatch(r"[0-9a-f]{64}", str(row["beforeSha256"])))):
            raise ValueError("invalid per-bank state transition")
    if any(type(name) is not str or not Path(name).is_absolute() or
           (value is not None and (type(value) is not str or
                                   re.fullmatch(r"[0-9a-f]{64}", value) is None))
           for name, value in bindings.items()):
        raise ValueError("invalid state input binding")
    absent = {name for name, value in bindings.items() if value is None}
    if absent and not absent.issubset(_null_base_paths(root.parent, ids, bindings)):
        raise ValueError("invalid state input binding: null is only a selected base icon")
    return package


def authorize(path, package, package_hash):
    from datetime import datetime, timezone
    from evidence import instant
    approval = json.loads(regular(Path(path).absolute()))
    if type(approval) is not dict:
        raise ValueError("authorization must be a structured object")
    expected = {"schemaVersion": 1, "scope": "state-publication", "decision": "authorize",
                "packageHash": package_hash, "target": package["target"],
                "ref": REF, "ids": package["ids"]}
    if any(approval.get(key) != value for key, value in expected.items()):
        raise ValueError("authorization does not cover this exact state package, target, ref and IDs")
    for field in ("reviewer", "statement"):
        if type(approval.get(field)) is not str or not approval[field].strip():
            raise ValueError(f"authorization requires {field}")
    if instant(approval.get("authorizedAt"), "authorizedAt") > datetime.now(timezone.utc):
        raise ValueError("authorization time cannot be in the future")
    return approval


def check_bindings(package):
    from evidence import baseline_bytes
    for name, expected in package["bindings"].items():
        try:
            baseline_bytes(Path(name), expected)
        except (ValueError, OSError) as exc:
            raise ValueError(f"prepared input changed: {name}; show and authorize a new package") from exc

def validate_package(session, package, root, repo, head):
    if package["reconciliation"].get("kind") == "task-recovery":
        from task_recovery import validate_package as validate_recovery
        return validate_recovery(session, package, repo, head)
    if package["reconciliation"].get("kind") == "approved-merge":
        from approved_merge import validate_package as validate_merge
        return validate_merge(session, package, repo, head)
    if package["reconciliation"].get("kind") == "review-feedback":
        from feedback_intake import validate_package as validate_feedback
        return validate_feedback(session, package, repo, head)
    if package["reconciliation"].get("kind") == "proposal-outcomes":
        from proposal_sync import validate_package as validate_outcomes
        return validate_outcomes(session, package, repo, head)
    from state_results import validate_results
    from state_reconciliation import reconcile_records
    data, originals, bindings = validate_results(session, package["ids"])
    from task_work import state_live
    for row in originals:
        bank = next(b for b in data["banks"] if b["id"] == row["id"])
        if bank.get("taskContext"):
            state_live(session, data, bank, row["after"])
    expected_history = data["stateHistory"]
    if (originals != package["results"] or bindings != package["bindings"] or
            data["baseCommit"] != package["baseCommit"] or expected_history != package["stateHistory"]):
        raise ValueError("package does not match validated session results; show and authorize a new package")
    if remote_target(package["target"]) != package["target"]:
        raise ValueError("publication target changed")
    provenance = package["reconciliation"]
    prior = confirmed_prior(root, repo, package["target"], head, provenance["priorPackages"])
    observed = [{key: row[key] for key in ("id", "path", "before", "beforeSha256")} for row in package["records"]]
    records, expected = reconcile_records(session, originals, observed, package["target"],
                                          provenance["resolution"], prior)
    if records != package["records"] or expected != provenance:
        raise ValueError("reconciled package does not match validated session results")

def save_receipt(root, package_hash, receipt):
    destination = root / f"{package_hash}.receipt.json"
    descriptor, name = tempfile.mkstemp(prefix=f"{package_hash}.receipt-", suffix=".tmp", dir=root)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded(receipt))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return receipt


def commit_package(repo, root, package, package_hash, parent):
    with tempfile.TemporaryDirectory(prefix="state-index-", dir=root) as temporary:
        index = Path(temporary) / "index"
        env = {**os.environ, "GIT_INDEX_FILE": str(index),
               "GIT_AUTHOR_NAME": "Bank icon state writer", "GIT_AUTHOR_EMAIL": "state@localhost",
               "GIT_COMMITTER_NAME": "Bank icon state writer", "GIT_COMMITTER_EMAIL": "state@localhost"}
        git(repo, "read-tree", parent if parent else "--empty", env=env)
        for record in package["records"]:
            blob = git(repo, "hash-object", "-w", "--stdin", input=encoded(record["after"])).stdout.decode().strip()
            git(repo, "update-index", "--add", "--cacheinfo", "100644", blob, record["path"], env=env)
        tree = git(repo, "write-tree", env=env).stdout.decode().strip()
        parents = ("-p", parent) if parent else ()
        return git(repo, "commit-tree", tree, *parents, env=env,
                   input=f"Publish bank state package {package_hash}\n".encode()).stdout.decode().strip()


def landed(repo, head, commit, package):
    if (head is None or type(commit) is not str or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit) is None or
            git(repo, "merge-base", "--is-ancestor", commit, head, check=False).returncode != 0):
        return False
    return all(git(repo, "show", f"{commit}:{row['path']}", check=False).stdout == encoded(row["after"])
               for row in package["records"])


def first_conflict(repo, head, records):
    for row in records:
        before, current = remote_record(repo, head, row["path"])
        if current != row["beforeSha256"] or before != row["before"]:
            return row["id"]
    return None


def reconcile(root, package, package_hash):
    path = root / f"{package_hash}.receipt.json"
    if not path.exists():
        return {"status": "not-published", "nextAction": "show package and obtain state authorization"}
    receipt = json.loads(regular(path))
    if not receipt.get("commit"):
        return receipt
    repo = object_repo(root)
    try:
        observed = fetch_head(repo, package["target"])
    except ValueError as exc:
        return save_receipt(root, package_hash, {**receipt, "status": "outcome-unknown",
                            "error": str(exc), "nextAction": "state-reconcile before any retry"})
    if landed(repo, observed, receipt["commit"], package):
        return save_receipt(root, package_hash, {**receipt, "status": "published", "observedRef": observed, "nextAction": "none"})
    conflict = first_conflict(repo, observed, package["records"])
    if conflict is not None:
        return save_receipt(root, package_hash, {**receipt, "status": "conflict", "id": conflict,
                            "observedRef": observed, "nextAction": "human reconciliation and new package authorization"})
    return save_receipt(root, package_hash, {**receipt, "status": "not-published", "observedRef": observed,
                        "nextAction": "explicit authorized retry; no publication observed on current remote ref"})


def publication_authorization(args, package):
    if args.authorization:
        return authorize(args.authorization, package, args.package)
    if args.run_authorization:
        from run_authorization import authorize as authorize_run
        return authorize_run(args.run_authorization, args.session, package, "statePublish")
    from standing_authorization import authorize as authorize_standing
    return authorize_standing(args.session, package, args.package, "statePublish")


def publish(args, root, package):
    approval = publication_authorization(args, package)
    if not args.authorization and (root / f"{args.package}.receipt.json").exists():
        previous_receipt = json.loads(regular(root / f"{args.package}.receipt.json"))
        if previous_receipt.get("status") == "publication-failed":
            raise ValueError("state publication failed; inspect provider rejection and obtain new exact-package authorization")
    previous = reconcile(root, package, args.package)
    if previous["status"] in {"published", "outcome-unknown", "conflict"}:
        return previous
    check_bindings(package)
    repo = object_repo(root)
    parent = fetch_head(repo, package["target"])
    for attempt in range(3):
        conflict = first_conflict(repo, parent, package["records"])
        if conflict is not None:
            return save_receipt(root, args.package, {"status": "conflict", "id": conflict,
                                "observedRef": parent, "nextAction": "human reconciliation and new package authorization"})
        check_bindings(package)
        validate_package(Path(args.session).resolve(), package, root, repo, parent)
        if publication_authorization(args, package) != approval:
            raise ValueError("publication authorization changed; re-evaluate the package")
        if all(row["before"] == row["after"] for row in package["records"]):
            return save_receipt(root, args.package, {"status": "no-state-changes", "observedRef": parent, "nextAction": "none"})
        commit = commit_package(repo, root, package, args.package, parent)
        receipt = {"status": "outcome-unknown", "commit": commit, "parent": parent,
                   "authorization": approval, "nextAction": "state-reconcile before any retry"}
        save_receipt(root, args.package, receipt)
        if publication_authorization(args, package) != approval:
            raise ValueError("publication authorization changed immediately before state push")
        try:
            pushed = git(repo, "push", "--porcelain", package["target"], f"{commit}:{REF}", check=False,
                         timeout=120, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        except subprocess.TimeoutExpired:
            return save_receipt(root, args.package, {**receipt,
                                "error": "State push timed out after 120 seconds; remote outcome is unknown."})
        try:
            observed = fetch_head(repo, package["target"])
        except ValueError as exc:
            return save_receipt(root, args.package, {**receipt, "error": str(exc)})
        if landed(repo, observed, commit, package):
            return save_receipt(root, args.package, {**receipt, "status": "published", "observedRef": observed, "nextAction": "none"})
        if observed == parent:
            return save_receipt(root, args.package, {**receipt, "status": "publication-failed",
                                "error": pushed.stderr.decode(errors="replace"), "nextAction": "inspect failure before explicit retry"})
        parent = observed
    conflict = first_conflict(repo, parent, package["records"])
    if conflict is not None:
        return save_receipt(root, args.package, {**receipt, "status": "conflict", "id": conflict,
                            "observedRef": parent, "nextAction": "human reconciliation and new package authorization"})
    return save_receipt(root, args.package, {**receipt, "status": "ref-race",
                        "observedRef": parent, "nextAction": "retry unchanged package after reviewing ref race"})


def sync_session(args, session):
    """Refresh visible state refs without changing a session's evidence or history."""
    from argparse import Namespace
    data = json.loads(regular(session / "session.json"))
    repo = Path(data["repo"]).resolve(strict=True)
    target = resolve_remote(repo, args.remote if args.remote is not None else data.get("stateRemote"))
    ref, commit, target, read = initial_state(repo, Namespace(remote=target, offline=False))
    return {"status": read["status"], "stateRemote": target, "stateRef": ref,
            "stateCommit": commit, "stateRead": read, "sessionSnapshotUnchanged": True}


def sync_published(session, root, package, package_hash, receipt):
    """A cache failure must never turn a confirmed push into a retryable failure."""
    if receipt["status"] != "published":
        return receipt
    try:
        data = json.loads(regular(session / "session.json"))
        repo = Path(data["repo"]).resolve(strict=True)
        # This commit was just confirmed against the server. Transfer the objects
        # locally, not via a second network round trip or a new publication.
        commit = receipt["observedRef"]
        pin = f"refs/bank-icon-maintenance/state-snapshots/{uuid.uuid4().hex}"
        git(repo, "-c", "protocol.file.allow=always", "fetch", *FETCH_OPTIONS,
            str(object_repo(root)), f"{commit}:{pin}", timeout=60)
        sync = cache_state(repo, package["target"], pin, commit)
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        sync = {"status": "sync-failed", "warning": str(exc),
                "nextAction": "Fix local access and run state-sync; do not repeat the confirmed push."}
    return save_receipt(root, package_hash=package_hash, receipt={**receipt, "localSync": sync})


def add_subcommands(commands):
    outcomes = commands.add_parser("state-prepare-outcomes",
                                   help="reconcile published MR outcomes once, before a new queue; no push")
    outcomes.add_argument("--repo", required=True)
    outcomes.add_argument("--session", required=True, help="synchronization directory outside the checkout; existing requires --resume")
    outcomes.add_argument("--remote", help="state destination; defaults to origin's sole push URL")
    outcomes.add_argument("--decisions", help="optional legacy exact rejection-comment decisions; never inferred")
    outcomes.add_argument("--review-decisions", help="agent assessment of saved closed-MR discussions; use with --resume")
    outcomes.add_argument("--base", help="committed catalog base; defaults to HEAD, frozen on resume")
    outcomes.add_argument("--resume", action="store_true", help="continue saved outcome checkpoints; never bypass pending publication")
    outcomes.add_argument("--max-proposals", type=int, default=5, help="maximum active proposals per batch (1..25)")
    outcomes.add_argument("--max-seconds", type=int, default=180, help="shared Git/API read budget per call (1..240)")
    sync = commands.add_parser("state-sync", help="refresh local state refs without changing session history or publishing")
    sync.add_argument("--session", required=True)
    sync.add_argument("--remote", help="concrete target; defaults to the session target or origin push URL")
    for name in ("state-prepare", "state-show", "state-publish", "state-reconcile"):
        command = commands.add_parser(name)
        command.add_argument("--session", required=True)
        if name == "state-prepare":
            command.add_argument("--remote", help="concrete target; defaults to the pinned session target or origin push URL")
            command.add_argument("--ids", required=True, type=int, nargs="+")
            command.add_argument("--resolution")
        else:
            command.add_argument("--package", required=True)
        if name == "state-publish":
            permission = command.add_mutually_exclusive_group()
            permission.add_argument("--authorization")
            permission.add_argument("--run-authorization")


def dispatch(args):
    if args.command == "state-prepare-outcomes":
        from proposal_sync import prepare as prepare_outcomes
        from standing_authorization import describe_package
        return describe_package(prepare_outcomes(args), Path(args.session).resolve(), "statePublish")
    session = Path(args.session).resolve()
    with writer(session) as root:
        from standing_authorization import configured, describe_package
        if args.command == "state-sync":
            return sync_session(args, session)
        if args.command == "state-prepare":
            return describe_package(prepare(args, session, root), session, "statePublish")
        package = load_package(root, args.package)
        status = "no-state-changes" if all(row["before"] == row["after"] for row in package["records"]) else "awaiting-state-authorization"
        result = {"packageHash": args.package, "package": package, "status": status}
        if args.command == "state-show":
            receipt = root / f"{args.package}.receipt.json"
            shown = {**result, **(json.loads(regular(receipt)) if receipt.exists() else {})}
            return describe_package(shown, session, "statePublish")
        if args.command == "state-reconcile":
            receipt = reconcile(root, package, args.package)
            return {**result, **sync_published(session, root, package, args.package, receipt)}
        if args.command == "state-publish" and not (args.authorization or args.run_authorization or configured()):
            return result
        receipt = publish(args, root, package)
        return {**result, **sync_published(session, root, package, args.package, receipt)}
