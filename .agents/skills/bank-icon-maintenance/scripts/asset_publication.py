"""One-bank local application and separately authorized GitHub PR publication.

The frozen evidence worktree is never changed. An isolated object repository owns
both the one-icon worktree and the orphan reviewer-materials commit.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile

from evidence import baseline_blob, instant
from state_publication import (digest, encoded, git, object_repo, regular,
                               save_new, save_receipt, writer)


_SCOPE = "asset-publication"
_HASH = re.compile(r"[0-9a-f]{64}\Z")


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _branch(repo, value):
    if (not isinstance(value, str) or value.startswith("-") or
            git(repo, "check-ref-format", f"refs/heads/{value}", check=False).returncode):
        raise ValueError("invalid publication branch")
    return f"refs/heads/{value}"


def _remote(target):
    return f"https://github.com/{target}.git"


def _transport(repo, *args, **kwargs):
    # Use the existing gh login for this command only, without writing Git config.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1"}
    return git(repo, "-c", "credential.helper=",
               "-c", "credential.https://github.com.helper=!gh auth git-credential",
               *args, env=env, **kwargs)


def _ref(repo, target, ref):
    result = _transport(repo, "ls-remote", "--exit-code", _remote(target), ref, check=False)
    if result.returncode == 2:
        return None
    if result.returncode:
        raise ValueError("cannot establish remote ref; publication outcome remains unknown")
    rows = result.stdout.decode().splitlines()
    if len(rows) != 1 or rows[0].split()[1] != ref:
        raise ValueError("ambiguous remote ref")
    return rows[0].split()[0]


def _fetch(repo, target, ref):
    commit = _ref(repo, target, ref)
    if commit is None:
        raise ValueError("target base branch does not exist")
    _transport(repo, "fetch", "--no-tags", _remote(target), ref)
    observed = git(repo, "rev-parse", "FETCH_HEAD").stdout.decode().strip()
    if observed != commit:
        raise ValueError("target base changed during fetch; reassess before application")
    return commit


def _blob(repo, commit, path):
    mode = git(repo, "ls-tree", commit, "--", path).stdout
    if not mode.startswith((b"100644 blob ", b"100755 blob ")):
        raise ValueError("reviewed target file is missing or not regular")
    return git(repo, "show", f"{commit}:{path}").stdout


def _bank_base(repo, commit, bank):
    folder = bank["folder"]
    baseline_blob(repo, commit, f"{folder}/icon.svg", bank["iconHash"])
    if digest(_blob(repo, commit, f"{folder}/info.json")) != bank["metadataHash"]:
        raise ValueError("target base bank changed; verify current icon and review a new proposal")
    from git_catalog import identity_paths
    matches = identity_paths(repo, commit, bank["id"])
    if matches != [f"{folder}/info.json"]:
        raise ValueError("target catalog has missing or duplicate Company.id; reassess proposal")


def _commit(repo, root, files, message, parent=None):
    with tempfile.TemporaryDirectory(prefix="asset-index-", dir=root) as temporary:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(temporary) / "index"),
               "GIT_AUTHOR_NAME": "Bank icon maintenance", "GIT_COMMITTER_NAME": "Bank icon maintenance",
               "GIT_AUTHOR_EMAIL": "bank-icon-agent@localhost", "GIT_COMMITTER_EMAIL": "bank-icon-agent@localhost"}
        git(repo, "read-tree", parent if parent else "--empty", env=env)
        for path, content in files:
            blob = git(repo, "hash-object", "-w", "--stdin", input=content).stdout.decode().strip()
            git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=env)
        tree = git(repo, "write-tree", env=env).stdout.decode().strip()
        args = ["commit-tree", tree]
        if parent:
            args += ["-p", parent]
        return git(repo, *args, input=(message + "\n").encode(), env=env).stdout.decode().strip()


def _load(root, package_hash):
    if type(package_hash) is not str or _HASH.fullmatch(package_hash) is None:
        raise ValueError("publication package must be a SHA-256 digest")
    content = regular(root / f"{package_hash}.json")
    if digest(content) != package_hash:
        raise ValueError("publication package bytes changed")
    package = json.loads(content)
    if package.get("schemaVersion") not in (1, 2) or package.get("scope") != _SCOPE:
        raise ValueError("invalid publication package")
    return package


def _validate_local(session, root, package):
    from asset_results import load_candidate
    import review_materials
    data, bank, prepared, approval = load_candidate(session, package["id"])
    if approval is None:
        raise ValueError("exact candidate assessment is required")
    if (bank["iconHash"] != package["baseIconHash"] or bank["folder"] != package["folder"] or
            prepared["candidate"]["sha256"] != package["candidateIconHash"] or
            prepared["reviewPackage"] != package["reviewPackage"]):
        raise ValueError("approved proposal differs from publication package")
    for path, expected in package["bindings"].items():
        if digest(regular(session / path)) != expected:
            raise ValueError("publication input changed; new review/package required")
    preflight = None
    if package["schemaVersion"] == 2:
        import admission
        preflight = admission.validate(session, prepared, package.get("admission"))
        expected = admission.bindings(session, prepared, package["admission"])
        if any(package["bindings"].get(path) != digest for path, digest in expected.items()):
            raise ValueError("asset package does not bind its admission evidence")
    materials = review_materials.prepare(session, bank, prepared, public_text=package.get("publicText"), admission=preflight)
    rows = [{key: row[key] for key in ("name", "sha256", "size")} for row in materials["files"]]
    if rows != package["materials"]["files"] or materials["details"] != package["materials"]["details"]:
        raise ValueError("reviewer materials changed")
    owner, repo_name = package["target"].split("/")
    import feedback_publication
    if bank.get("taskContext"):
        from task_work import context as task_context
        if package.get("taskKey") != task_context(session,data,bank)["key"]:
            raise ValueError("asset package lost its task input binding")
    elif package.get("taskKey"):
        raise ValueError("unsourced task publication")
    revision = feedback_publication.check_package(session, data, bank, package)
    rendered = review_materials.render(materials, owner, repo_name, package["materials"]["commit"])
    if revision:
        if feedback_publication.review_comment(revision, rendered) != package.get("reviewComment"):
            raise ValueError("revision review comment changed")
    elif materials["title"] != package["title"] or rendered != package["body"]:
        raise ValueError("PR description changed")
    objects = object_repo(root)
    worktree = root / str(bank["id"]) / "worktree"
    if (worktree.is_symlink() or git(worktree, "rev-parse", "--show-toplevel").stdout.decode().strip() != str(worktree) or
            git(worktree, "rev-parse", "--absolute-git-dir").stdout.decode().strip() != package["worktreeGitDir"] or
            git(worktree, "rev-parse", "HEAD").stdout.decode().strip() != package["assetCommit"] or
            git(worktree, "status", "--porcelain").stdout):
        raise ValueError("asset worktree registration, commit or content changed")
    parents = git(objects, "rev-list", "--parents", "-n", "1", package["assetCommit"]).stdout.decode().split()
    parent = revision["sourceHead"] if revision else package["baseCommit"]
    if parents != [package["assetCommit"], parent]:
        raise ValueError("asset commit is not one commit atop the reviewed target base")
    material_commit = package["materials"]["commit"]
    if len(git(objects, "rev-list", "--parents", "-n", "1", material_commit).stdout.split()) != 1:
        raise ValueError("reviewer material commit is not orphaned")
    material_paths = git(objects, "ls-tree", "-r", "--name-only", material_commit).stdout.decode().splitlines()
    expected_paths = sorted(materials["details"]["materialPath"] + row["name"] for row in materials["files"])
    if material_paths != expected_paths:
        raise ValueError("material commit contains files outside the exact upload allowlist")
    changed = git(objects, "diff", "--name-only", "-z", parent, package["assetCommit"]).stdout.decode().split("\0")[:-1]
    if changed != [f'{bank["folder"]}/icon.svg']:
        raise ValueError("asset commit contains changes outside the selected bank icon")
    change = git(objects, "diff", "--name-status", "-z", parent, package["assetCommit"]).stdout
    expected_status = b"A" if bank["iconHash"] is None and revision is None else b"M"
    if change.split(b"\0") != [expected_status, changed[0].encode(), b""]:
        raise ValueError("asset commit is not the reviewed icon addition or update")
    if digest(_blob(objects, package["assetCommit"], changed[0])) != package["candidateIconHash"]:
        raise ValueError("asset commit no longer matches final candidate")
    _bank_base(objects, package["baseCommit"], bank)
    for row in materials["files"]:
        path = materials["details"]["materialPath"] + row["name"]
        if _blob(objects, package["materials"]["commit"], path) != row["content"]:
            raise ValueError("material commit differs from exact review package")
    return objects, bank


def prepare(args, session, root):
    from asset_results import load_candidate
    import github_pr
    import review_materials
    data, bank, report, approval = load_candidate(session, args.id)
    if approval is None:
        raise ValueError("exact candidate assessment is required before asset application")
    if bank["state"]["proposals"][-1].get("mr"):
        raise ValueError("proposal already has an MR; reconcile and await its outcome")
    target = github_pr.normalize(args.target)
    public_text = json.loads(regular(Path(args.public_text).absolute())) if args.public_text else None
    if args.public_text and type(public_text) is not dict:
        raise ValueError("public text must be a JSON object mapping narrative field paths to strings")
    objects = object_repo(root)
    head_ref = _branch(objects, args.head)
    base_ref = _branch(objects, args.base)
    import feedback_revision
    import feedback_publication
    revision = feedback_revision.context(session, data, bank)
    material_ref = (feedback_publication.material_ref(args.head, revision["requestKey"]) if revision
                    else _branch(objects, args.head + "-review"))
    if len({head_ref, base_ref, material_ref, "refs/heads/banks-icon-agent-state"}) != 4:
        raise ValueError("asset, material, base and state refs must be distinct")
    idroot = root / str(args.id)
    if idroot.is_symlink():
        raise ValueError("asset worktree parent cannot be a symlink")
    marker = idroot / "prepared.json"
    if marker.exists():
        existing = json.loads(regular(marker))
        package = _load(root, existing["packageHash"])
        if (package["target"], package["base"], package["head"]) != (target, args.base, args.head):
            raise ValueError("existing local asset package has another target; preserve it for review")
        if public_text is not None and public_text != package.get("publicText", {}):
            raise ValueError("existing local asset package has frozen public text; preserve it for review")
        if args.admission:
            import source_evidence as source
            requested = Path(args.admission)
            if not requested.is_absolute():
                requested = session / requested
            if source.link(session, requested) != package.get("admission"):
                raise ValueError("existing package has frozen admission; preserve it")
        _validate_local(session, root, package)
        return {"status": "prepared", "packageHash": existing["packageHash"], "package": package}
    # No new local application or remote read before the current admission gate.
    import admission
    import source_evidence as source
    if not args.admission:
        raise ValueError("new asset preparation requires workflow admission and --admission FILE")
    path = Path(args.admission)
    if not path.is_absolute():
        path = session / path
    admission_link = source.link(session, path)
    preflight = admission.validate(session, report, admission_link)
    repository = github_pr.repository(target)
    if not revision:
        if github_pr.lookup(target, args.head):
            raise ValueError("repo/head already has a PR in provider history; reconcile, never duplicate")
        if github_pr.open_for_path(target, f'{bank["folder"]}/icon.svg'):
            raise ValueError("bank already has an open PR; wait for its outcome instead of another head")
        if _ref(objects, target, head_ref) is not None or _ref(objects, target, material_ref) is not None:
            raise ValueError("publication head or reviewer-materials ref already exists; inspect before reuse")
    base_commit = _fetch(objects, target, base_ref)
    _bank_base(objects, base_commit, bank)
    if revision:
        if base_commit != data["baseCommit"]:
            raise ValueError("revision target base changed since its source was pinned")
        revision = feedback_publication.prepare(session, data, bank, objects, target, args.base, args.head)
    if bank.get("taskContext"):
        import task_work
        task_work.state_live(session, data, bank, bank["state"])
    materials = review_materials.prepare(session, bank, report, public_text=public_text, admission=preflight)
    idroot.mkdir(exist_ok=True)
    worktree = idroot / "worktree"
    if worktree.exists() or worktree.is_symlink():
        raise ValueError("preserved asset worktree already exists; inspect interrupted local preparation")
    # Revalidate the evidence chain immediately before constructing the one-icon commit.
    load_candidate(session, args.id)
    final = regular(session / report["candidate"]["path"])
    if digest(final) != report["candidate"]["sha256"]:
        raise ValueError("candidate changed immediately before application")
    icon_path = f'{bank["folder"]}/icon.svg'
    asset_commit = _commit(objects, root, [(icon_path, final)],
                           f'update bank icon {bank["id"]}', revision["sourceHead"] if revision else base_commit)
    git(objects, "-c", "core.autocrlf=false", "-c", "core.eol=lf",
        "worktree", "add", "-b", args.head, str(worktree), asset_commit)
    material_commit = _commit(objects, root,
        [(materials["details"]["materialPath"] + row["name"], row["content"]) for row in materials["files"]],
        f'review bank icon {bank["id"]}')
    owner, repo_name = target.split("/")
    proposal = bank["state"]["proposals"][-1]
    approval_link = proposal.get("approval") or proposal.get("agentAssessment")
    if type(approval_link) is not dict:
        raise ValueError("assessed proposal lacks immutable visual assessment link")
    bindings = {approval_link["path"]: approval_link["sha256"]}
    bindings.update(admission.bindings(session, report, admission_link))
    if bank.get("taskContext"):
        for field in ("taskContext", "taskDecision"):
            if bank.get(field):
                bindings[bank[field]["path"]] = bank[field]["sha256"]
    package = {"schemaVersion": 2, "scope": _SCOPE, "id": args.id, "admission": admission_link,
        "target": target, "public": not repository["isPrivate"], "base": args.base,
        "baseCommit": base_commit, "head": args.head, "headRef": head_ref,
        "assetCommit": asset_commit, "folder": bank["folder"],
        "worktreeGitDir": git(worktree, "rev-parse", "--absolute-git-dir").stdout.decode().strip(),
        "baseIconHash": bank["iconHash"], "candidateIconHash": report["candidate"]["sha256"],
        "reviewPackage": report["reviewPackage"], "approval": approval_link,
        "materials": {"ref": material_ref, "commit": material_commit,
            "files": [{key: row[key] for key in ("name", "sha256", "size")} for row in materials["files"]],
            "details": materials["details"]}, "title": materials["title"],
        "body": review_materials.render(materials, owner, repo_name, material_commit),
        "diff": git(objects, "diff", "--no-ext-diff", base_commit, asset_commit, "--", icon_path).stdout.decode(),
        "bindings": bindings}
    if bank.get("taskContext"):
        import task_work
        package["taskKey"] = task_work.context(session,data,bank)["key"]
    if revision:
        package["revision"] = revision
        package["reviewComment"] = feedback_publication.review_comment(revision, package["body"])
        package["title"] = revision["mr"]["title"]
        package["body"] = revision["request"]["event"]["prBody"]
        link = bank.get("taskContext") or bank["feedbackContext"]
        package["bindings"][link["path"]] = link["sha256"]
        package["diff"] = git(objects, "diff", "--no-ext-diff", revision["sourceHead"], asset_commit,
                              "--", icon_path).stdout.decode()
    if public_text:
        package["publicText"] = public_text
    content = encoded(package)
    package_hash = digest(content)
    save_new(root / f"{package_hash}.json", content)
    save_new(marker, encoded({"packageHash": package_hash}))
    return {"status": "prepared", "packageHash": package_hash, "package": package}


def _authorize(path, package, package_hash):
    authorization = json.loads(regular(Path(path).absolute()))
    exact = {"schemaVersion": 1, "scope": _SCOPE, "decision": "authorize",
             "packageHash": package_hash, "target": package["target"], "id": package["id"],
             "base": package["base"], "baseCommit": package["baseCommit"],
             "head": package["head"], "assetCommit": package["assetCommit"],
             "materialRef": package["materials"]["ref"], "materialCommit": package["materials"]["commit"]}
    if any(authorization.get(key) != value for key, value in exact.items()):
        raise ValueError("authorization does not bind exact asset/PR and reviewer-material publication")
    if any(type(authorization.get(key)) is not str or not authorization[key].strip()
           for key in ("reviewer", "statement")):
        raise ValueError("authorization requires actual human reviewer and statement")
    if instant(authorization.get("authorizedAt"), "authorizedAt") > datetime.now(timezone.utc):
        raise ValueError("publication authorization cannot be in the future")
    return authorization


def _receipt(root, package_hash):
    path = root / f"{package_hash}.receipt.json"
    return json.loads(regular(path)) if path.exists() else {"status": "not-published"}


def _save(root, package_hash, status, **facts):
    previous = _receipt(root, package_hash)
    durable = {key: previous[key] for key in
               ("creationAttempted", "commentAttempted", "authorization", "materialPublished", "assetPublished")
               if key in previous}
    receipt = {**durable, **facts, "status": status, "observedAt": _now()}
    save_receipt(root, package_hash, receipt)
    return receipt


def validate_remote_observation(session, package_link, observation):
    """Require unchanged live PR facts before a separately authorized state write."""
    import github_pr
    session = Path(session).resolve()
    package_hash = package_link["sha256"]
    if package_link.get("path") != f"asset-publication/{package_hash}.json":
        raise ValueError("invalid MR publication package link")
    root = session / "asset-publication"
    package = _load(root, package_hash)
    objects, bank = _validate_local(session, root, package)
    matches = github_pr.lookup(package["target"], package["head"])
    if len(matches) != 1:
        raise ValueError("current MR outcome is unknown or duplicated; reconcile before state publication")
    current = matches[0]
    keys = ("number", "url", "target", "head", "headCommit", "base", "state", "title", "bodySha256")
    if any(current.get(key) != observation.get(key) for key in keys):
        raise ValueError("MR changed; reconcile and authorize a new state package")
    for ref, expected in ((package["headRef"], package["assetCommit"]),
                          (package["materials"]["ref"], package["materials"]["commit"])):
        if _ref(objects, package["target"], ref) != expected:
            raise ValueError("published asset or reviewer material ref changed; reconcile before state publication")
    base = _fetch(objects, package["target"], f'refs/heads/{package["base"]}')
    if current["baseCommit"] != base:
        raise ValueError("provider base is not current; reconcile before state publication")
    if current["state"] == "open":
        _bank_base(objects, base, bank)
        if package.get("revision"):
            from feedback_publication import _comments
            if _comments(package) is None:
                raise ValueError("revision reviewer materials comment is no longer observable")


def reconcile(session, root, package, package_hash):
    if package.get("revision"):
        from feedback_publication import reconcile as reconcile_revision
        return reconcile_revision(session, root, package, package_hash)
    import github_pr
    from asset_results import record_mr
    objects, bank = _validate_local(session, root, package)
    try:
        observations = github_pr.lookup(package["target"], package["head"])
        material = _ref(objects, package["target"], package["materials"]["ref"])
        head = _ref(objects, package["target"], package["headRef"])
    except ValueError as exc:
        return _save(root, package_hash, "outcome-unknown", reason=str(exc), nextAction="read-only asset-reconcile before any retry")
    if material not in (None, package["materials"]["commit"]) or head not in (None, package["assetCommit"]):
        return _save(root, package_hash, "conflict", reason="published ref differs; preserve package and obtain human review")
    facts = {"materialPublished": material is not None, "assetPublished": head is not None}
    if len(observations) > 1:
        return _save(root, package_hash, "conflict", **facts, reason="multiple PRs for repository/head; human review required")
    if observations:
        observation = observations[0]
        expected = {"target": package["target"], "head": package["head"],
                    "headCommit": package["assetCommit"], "base": package["base"],
                    "title": package["title"], "bodySha256": digest(package["body"].encode())}
        if any(observation.get(key) != value for key, value in expected.items()):
            return _save(root, package_hash, "conflict", **facts, reason="existing PR differs from reviewed proposal")
        if not all(facts.values()):
            return _save(root, package_hash, "conflict", **facts, reason="PR exists but exact asset/material refs are missing")
        # Fetch provider-observed base and verify selected bank, not merely branch spelling.
        try:
            observed_base = _fetch(objects, package["target"], f'refs/heads/{package["base"]}')
            if observation["baseCommit"] != observed_base:
                raise ValueError("provider base observation differs from the current target ref")
            if observation["state"] == "open":
                _bank_base(objects, observed_base, bank)
        except ValueError as exc:
            return _save(root, package_hash, "conflict", **facts, reason=str(exc),
                         nextAction="establish current target base and reassess proposal")
        observation = {**observation, "observedAt": _now()}
        record_mr(session, package["id"], {"path": f"asset-publication/{package_hash}.json", "sha256": package_hash}, observation)
        return _save(root, package_hash, "mr-confirmed", **facts, mr=observation,
                     nextAction="prepare a new state package; obtain independent state authorization")
    previous = _receipt(root, package_hash)
    if previous.get("creationAttempted"):
        return _save(root, package_hash, "outcome-unknown", **facts,
                     reason="PR create was attempted but no matching PR is observable; do not duplicate",
                     nextAction="establish provider facts; retained package requires human recovery")
    return _save(root, package_hash, "not-published", **facts, nextAction="show exact package and obtain asset publication authorization")


def publication_authorization(args, session, package):
    if args.authorization:
        return _authorize(args.authorization, package, args.package)
    if args.run_authorization:
        from run_authorization import authorize
        return authorize(args.run_authorization, session, package, "assetPublish")
    from standing_authorization import authorize as authorize_standing
    return authorize_standing(session, package, args.package, "assetPublish")


def publish(args, session, root, package):
    result = _publish(args,session,root,package)
    pointer = session / f"task-operation-{args.package}.json"
    if package.get('taskKey') and pointer.exists():
        result = {**result, 'operation': json.loads(regular(pointer))['operation']}
    return result


def _publish(args, session, root, package):
    import github_pr
    from standing_authorization import configured, USE_SCOPE
    if not (args.authorization or args.run_authorization or configured()):
        return {"status": "awaiting-asset-authorization", "packageHash": args.package, "package": package}
    authorization = publication_authorization(args, session, package)
    actor_key = "reviewer" if args.authorization else "authorizer"
    receipt_authority = (authorization if authorization.get("scope") == USE_SCOPE else
                         {actor_key: authorization[actor_key], "authorizedAt": authorization["authorizedAt"]})
    if not args.authorization and _receipt(root, args.package).get("status") == "publication-failed":
        raise ValueError("provider rejected publication; obtain interactive provider approval and new exact-package authorization")
    from task_recovery import retain
    retain(session, package)
    if package.get("revision"):
        from feedback_publication import publish as publish_revision
        return publish_revision(args, session, root, package, authorization, receipt_authority)
    previous = reconcile(session, root, package, args.package)
    if previous["status"] != "not-published":
        return previous
    if package["schemaVersion"] != 2:
        raise ValueError("legacy package is read/reconcile-only under this skill; new publication requires admission")
    objects, bank = _validate_local(session, root, package)
    repository = github_pr.repository(package["target"])
    if (not repository["isPrivate"]) != package["public"]:
        raise ValueError("repository visibility changed; show and authorize a new package")
    current = _fetch(objects, package["target"], f'refs/heads/{package["base"]}')
    _bank_base(objects, current, bank)
    if current != package["baseCommit"]:
        raise ValueError("target base commit changed; review a new publication package")
    for ref, commit, flag in ((package["materials"]["ref"], package["materials"]["commit"], "materialPublished"),
                              (package["headRef"], package["assetCommit"], "assetPublished")):
        if previous.get(flag):
            continue
        _validate_local(session, root, package)
        if github_pr.open_for_path(package["target"], f'{bank["folder"]}/icon.svg'):
            raise ValueError("bank already has an open PR; reconcile it instead of publishing another")
        if _ref(objects, package["target"], f'refs/heads/{package["base"]}') != package["baseCommit"]:
            raise ValueError("target base changed immediately before push; stop publication")
        if publication_authorization(args, session, package) != authorization:
            raise ValueError("publication authorization changed immediately before asset push")
        from task_work import publication_live
        if package.get("taskKey"):
            publication_live(session, package["id"])
        _save(root, args.package, "outcome-unknown", pendingRef=ref,
              authorization=receipt_authority)
        pushed = _transport(objects, "push", "--porcelain", _remote(package["target"]), f"{commit}:{ref}", check=False)
        previous = reconcile(session, root, package, args.package)
        if previous["status"] == "not-published" and not previous.get(flag) and pushed.returncode:
            return _save(root, args.package, "publication-failed", pendingRef=ref,
                         exitCode=pushed.returncode, error=pushed.stderr.decode(errors="replace"),
                         nextAction="inspect rejection and obtain any required provider approval before explicit retry")
        if previous["status"] != "not-published" or not previous.get(flag):
            return previous
    _validate_local(session, root, package)
    if _ref(objects, package["target"], f'refs/heads/{package["base"]}') != package["baseCommit"]:
        raise ValueError("target base changed before PR creation; preserve pushed refs")
    # A final all-state lookup closes ordinary retry duplication; one session writer
    # holds the lock, and an indeterminate create is never sent a second time.
    if github_pr.lookup(package["target"], package["head"]):
        return reconcile(session, root, package, args.package)
    if github_pr.open_for_path(package["target"], f'{bank["folder"]}/icon.svg'):
        raise ValueError("bank already has an open PR; preserve local package and await its outcome")
    if publication_authorization(args, session, package) != authorization:
        raise ValueError("publication authorization changed immediately before PR creation")
    from task_work import publication_live
    if package.get("taskKey"):
        publication_live(session, package["id"])
    _save(root, args.package, "outcome-unknown", creationAttempted=True,
          authorization=receipt_authority, nextAction="verify repository/head before retry")
    try:
        github_pr.create(package["target"], package["head"], package["base"], package["title"], package["body"])
    except ValueError:
        return reconcile(session, root, package, args.package)
    return reconcile(session, root, package, args.package)


def add_subcommands(commands):
    approval = commands.add_parser("candidate-approve")
    approval.add_argument("--session", required=True)
    approval.add_argument("--id", type=int, required=True)
    approval.add_argument("--approval", required=True)
    assessment = commands.add_parser("candidate-assess")
    assessment.add_argument("--session", required=True)
    assessment.add_argument("--id", type=int, required=True)
    assessment.add_argument("--assessment", required=True)
    for name in ("asset-prepare", "asset-show", "asset-publish", "asset-reconcile"):
        command = commands.add_parser(name)
        command.add_argument("--session", required=True)
        if name == "asset-prepare":
            command.add_argument("--id", type=int, required=True)
            command.add_argument("--target", required=True)
            command.add_argument("--base", required=True)
            command.add_argument("--head", required=True)
            command.add_argument("--admission", help="Exact append-only preflight returned by workflow admission")
            command.add_argument("--public-text",
                                 help="JSON map of narrative field paths to public wording; evidence stays unchanged")
        else:
            command.add_argument("--package", required=True)
        if name == "asset-publish":
            permission = command.add_mutually_exclusive_group()
            permission.add_argument("--authorization")
            permission.add_argument("--run-authorization")


def dispatch(args):
    session = Path(args.session).resolve()
    with writer(session):
        from standing_authorization import describe_package
        root = session / "asset-publication"
        if root.is_symlink():
            raise ValueError("asset publication directory cannot be a symlink")
        root.mkdir(exist_ok=True)
        if args.command == "candidate-approve":
            from asset_results import approve
            return approve(session, args.id, Path(args.approval).absolute())
        if args.command == "candidate-assess":
            from asset_results import approve
            return approve(session, args.id, Path(args.assessment).absolute(), agent=True)
        if args.command == "asset-prepare":
            return describe_package(prepare(args, session, root), session, "assetPublish")
        package = _load(root, args.package)
        if args.command == "asset-show":
            _validate_local(session, root, package)
            shown = {"status": "prepared", "packageHash": args.package, "package": package,
                     "receipt": _receipt(root, args.package)}
            return describe_package(shown, session, "assetPublish")
        if args.command == "asset-reconcile":
            return reconcile(session, root, package, args.package)
        return publish(args, session, root, package)
