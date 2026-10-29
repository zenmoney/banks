"""Fast-forward revision publication with exact-ref leases and one review comment.

No PR creation, body replacement, merge, or non-fast-forward update is supported.
The parent is independently proved before using a lease: the lease adds an atomic
expected-old check; it is never permission to replace non-descendant history.
"""
import subprocess

import feedback_intake as intake
import feedback_revision as revision
import github_pr
import state_publication as state


def material_ref(head, key):
    return f"refs/heads/{head}-review-{key[:16]}"


def review_comment(context, rendered):
    key = context["requestKey"]
    url = context["request"]["event"]["source"]["url"]
    return (f"<!-- bank-icon-feedback:{key}:begin -->\n"
            f"Icon revision for reviewer request: {url}\n\n{rendered}\n"
            f"<!-- bank-icon-feedback:{key}:end -->")


def check_package(session, data, bank, package, bindings=None):
    ctx = revision.context(session, data, bank)
    if ctx is None:
        if "revision" in package or "reviewComment" in package:
            raise ValueError("revision package lacks a sourced queued request")
        return None
    if (package.get("revision") != ctx or package["body"] != ctx["request"]["event"]["prBody"]
            or package["title"] != ctx["mr"]["title"]
            or any(package[k] != ctx["mr"][k] for k in ("target", "head", "base"))
            or package["materials"]["ref"] != material_ref(package["head"], ctx["requestKey"])):
        raise ValueError("revision package differs from the original request or existing MR identity")
    link = bank.get("taskContext") or bank["feedbackContext"]
    if package["bindings"].get(link["path"]) != link["sha256"]:
        raise ValueError("revision publication lost immutable feedback context binding")
    return ctx


def prepare(session, data, bank, objects, target, base, head):
    """Prepare-time gate; the normal writer still validates all candidate evidence."""
    from asset_publication import _fetch, _ref
    ctx = revision.context(session, data, bank)
    if ctx is None:
        return None
    mr = ctx["mr"]
    if (target, base, head) != (mr["target"], mr["base"], mr["head"]):
        raise ValueError("feedback revision must reuse its exact existing MR target/base/head")
    verify_context(ctx)
    matches = github_pr.open_for_path(target, f'{bank["folder"]}/icon.svg')
    if len(matches) != 1 or matches[0] != mr:
        raise ValueError("feedback revision needs exactly one matching open PR for this bank")
    if _fetch(objects, target, f"refs/heads/{head}") != ctx["sourceHead"]:
        raise ValueError("feedback PR branch moved or is unavailable")
    if _ref(objects, target, material_ref(head, ctx["requestKey"])) is not None:
        raise ValueError("revision reviewer-material branch already exists; inspect its history")
    from state_results import _prior_state
    revision._proof(objects, data["baseCommit"], bank, _prior_state(data, bank["id"]),
                    mr, ctx["sourceHead"], ctx["branchBase"])
    return ctx


def _comments(package):
    ctx = package["revision"]
    rows = intake._pages(f'repos/{package["target"]}/issues/{ctx["mr"]["number"]}/comments')
    if len(rows) > intake.MAX_MESSAGES:
        raise ValueError("revision comment lookup exceeds the bounded message count")
    marker = f'<!-- bank-icon-feedback:{ctx["requestKey"]}:begin -->'
    matches = [r for r in rows if isinstance(r, dict) and marker in (r.get("body") or "")]
    if len(matches) > 1:
        raise ValueError("multiple comments claim this revision; do not post again")
    if matches:
        row = matches[0]
        if (row.get("body") != package["reviewComment"] or type(row.get("id")) is not int
                or row.get("html_url") != f'https://github.com/{package["target"]}/pull/{ctx["mr"]["number"]}#issuecomment-{row["id"]}'):
            raise ValueError("revision comment differs from its exact reviewed material")
        return {"id": row["id"], "url": row["html_url"], "bodySha256": state.digest(row["body"].encode())}
    return None


def _live(package, objects, bank):
    from asset_publication import _ref, _fetch, _bank_base
    ctx = package["revision"]
    current = github_pr.inspect(package["target"], ctx["mr"]["number"])
    if any(current[k] != ctx["mr"][k] for k in ("number", "url", "target", "head", "base", "title", "bodySha256")):
        raise ValueError("human PR description or identity changed; preserve the package and rescan")
    if current["state"] != "open":
        raise ValueError("revision source PR closed or merged; no automatic reopening or replacement PR")
    head = _ref(objects, package["target"], package["headRef"])
    if head not in {ctx["sourceHead"], package["assetCommit"]} or current["headCommit"] != head:
        raise ValueError("PR head changed concurrently; never overwrite another writer")
    material = _ref(objects, package["target"], package["materials"]["ref"])
    if material not in (None, package["materials"]["commit"]):
        raise ValueError("revision reviewer-material ref changed")
    base = _fetch(objects, package["target"], f'refs/heads/{package["base"]}')
    _bank_base(objects, base, bank)
    if base != package["baseCommit"] or current["baseCommit"] != base:
        raise ValueError("revision target base changed; recheck rather than replaying the package")
    verify_context(ctx, published_head=head)
    return current, {"materialPublished": material is not None,
                     "assetPublished": head == package["assetCommit"]}


def reconcile(session, root, package, package_hash):
    from asset_publication import _validate_local, _save, _receipt, _now
    from asset_results import record_mr
    objects, bank = _validate_local(session, root, package)
    try:
        current, facts = _live(package, objects, bank)
        comment = _comments(package)
    except ValueError as exc:
        # An unavailable provider is not evidence that the earlier write failed.
        return _save(root, package_hash, "outcome-unknown", reason=str(exc),
                     nextAction="read-only asset-reconcile; inspect changed head/text or provider before any retry")
    facts["commentPublished"] = comment is not None
    if comment is not None and not all(facts.values()):
        return _save(root, package_hash, "conflict", **facts, reason="revision comment exists without exact asset/material refs")
    if all(facts.values()):
        observation = {**current, "observedAt": _now()}
        record_mr(session, package["id"], {"path": f"asset-publication/{package_hash}.json", "sha256": package_hash}, observation)
        return _save(root, package_hash, "mr-confirmed", **facts, mr=observation, reviewComment=comment,
                     nextAction="publish the new exact revision state under separate state authority; no merge")
    if _receipt(root, package_hash).get("commentAttempted") and comment is None:
        return _save(root, package_hash, "outcome-unknown", **facts,
                     reason="comment was attempted but is not observable; do not post a duplicate")
    return _save(root, package_hash, "not-published", **facts,
                 nextAction="show and authorize the exact existing-MR revision, material ref and comment")


def publish(args, session, root, package, authorization, receipt_authority):
    from asset_publication import (_validate_local, _ref, _save, _transport, _remote,
                                   publication_authorization)
    previous = reconcile(session, root, package, args.package)
    if previous["status"] != "not-published":
        return previous
    if package["schemaVersion"] != 2:
        raise ValueError("new revisions require complete admission")
    objects, bank = _validate_local(session, root, package)
    repo = github_pr.repository(package["target"])
    if (not repo["isPrivate"]) != package["public"]:
        raise ValueError("repository visibility changed; show and authorize a new package")
    ctx = package["revision"]
    steps = [(package["materials"]["ref"], package["materials"]["commit"], None, "materialPublished"),
             (package["headRef"], package["assetCommit"], ctx["sourceHead"], "assetPublished")]
    for ref, commit, expected, flag in steps:
        if previous[flag]:
            continue
        _validate_local(session, root, package)
        _live(package, objects, bank)
        if expected is not None:
            parents = state.git(objects, "rev-list", "--parents", "-n", "1", commit).stdout.decode().split()
            if parents != [commit, expected]:
                raise ValueError("revision may only append one commit to the exact expected head")
        if _ref(objects, package["target"], ref) != expected:
            raise ValueError("revision ref lease changed immediately before push")
        if publication_authorization(args, session, package) != authorization:
            raise ValueError("publication authorization changed immediately before revision push")
        _save(root, args.package, "outcome-unknown", pendingRef=ref, authorization=receipt_authority)
        # A proved direct child + atomic expected-old lease is a fast-forward only.
        # No '+' refspec, unrestricted --force, or replacement of another head.
        try:
            pushed = _transport(objects, "push", "--porcelain",
                f"--force-with-lease={ref}:{expected or ''}", _remote(package["target"]),
                f"{commit}:{ref}", check=False, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return _save(root, args.package, "outcome-unknown", reason=str(exc))
        previous = reconcile(session, root, package, args.package)
        if previous["status"] == "not-published" and not previous[flag] and pushed.returncode:
            return _save(root, args.package, "publication-failed", pendingRef=ref,
                         exitCode=pushed.returncode, error=pushed.stderr.decode(errors="replace"))
        if previous["status"] != "not-published" or not previous[flag]:
            return previous
    _validate_local(session, root, package)
    _live(package, objects, bank)
    if _comments(package) is not None:
        return reconcile(session, root, package, args.package)
    if publication_authorization(args, session, package) != authorization:
        raise ValueError("publication authorization changed before revision comment")
    _save(root, args.package, "outcome-unknown", commentAttempted=True, authorization=receipt_authority)
    try:
        github_pr._api(f'repos/{package["target"]}/issues/{ctx["mr"]["number"]}/comments',
                       "-X", "POST", "-f", f'body={package["reviewComment"]}')
    except ValueError:
        pass  # Creation outcome is established by a read, never a blind retry.
    return reconcile(session, root, package, args.package)


def verify_context(ctx, published_head=None):
    if ctx.get("task"):
        import task_work
        task_work.verify_live(ctx["task"], published_head=published_head)
        for src in ctx["decision"]["sources"]:
            if intake.message(ctx["mr"]["target"], ctx["mr"]["number"], src["kind"], src["nodeId"]) != src:
                raise ValueError("task reviewer source/editor rights changed")
    elif published_head is None:
        intake.verify_event(ctx["request"]["event"])
    else:
        intake.verify_source(ctx["request"]["event"])
