"""Synchronous canonical CLI calls with complete local outputs and small summaries.

No new state machine, authorization source, retry policy, or remote writer.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import uuid

from source_evidence import link, now, sha, write_json

CLI = Path(__file__).resolve().with_name("session.py")
COMMANDS = {"doctor", "continue", "pause", "desktop-export", "candidate-assess", "candidate-approve",
            "asset-prepare", "asset-show", "asset-publish", "asset-reconcile", "state-prepare",
            "state-show", "state-publish", "state-reconcile", "state-sync", "task-finish"}
TIMEOUT = 900


def _pick(value, names):
    return {key: value[key] for key in names if key in value}


def summarize(value):
    """Keep stops/authority/remote outcomes; never infer success from exit code."""
    if type(value) is not dict:
        raise ValueError("canonical CLI result is not an object")
    result = _pick(value, ("status", "error", "reason", "nextAction", "point", "packageHash", "id", "ids",
                           "target", "ref", "head", "headRef", "assetCommit", "materialCommit", "commit",
                           "baseRef", "baseCommit", "session", "worktree", "selectedIds", "stateHistory",
                           "stateRemote", "stateRead", "localSync", "publicationPolicy", "publicationAuthority",
                           "continuationStops", "stops", "open", "warnings", "mr", "sessionSnapshotUnchanged"))
    if type(value.get("receipt")) is dict:
        result["receipt"] = _pick(value["receipt"], ("status", "commit", "outgoingCommit", "pendingRef",
                                                    "error", "nextAction", "mr", "localSync"))
    if type(value.get("banks")) is list:
        result["banks"] = [_pick(bank, ("id", "title", "point", "reason", "warnings", "report", "evidence"))
                           for bank in value["banks"][:20] if type(bank) is dict]
        if len(value["banks"]) > 20:
            result["remainingBanksInResultFile"] = len(value["banks"]) - 20
    result["fullResultKeys"] = sorted(value)
    return result


def _root(session):
    parent = session / "tool-results"
    if parent.is_symlink():
        raise ValueError("tool-results cannot be a symlink")
    parent.mkdir(mode=0o700, exist_ok=True)
    root = parent / uuid.uuid4().hex
    root.mkdir(mode=0o700)
    return root


@contextmanager
def coordinator(session):
    import fcntl
    path = session / ".workflow-writer.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("another workflow operation is still running; await that job's terminal result") from exc
        yield
    finally:
        os.close(fd)


def validate_arguments(arguments):
    """Reject unsupported entry routes before touching an existing session."""
    if (type(arguments) is not list or not arguments or arguments[0] not in COMMANDS or
            any(type(arg) is not str for arg in arguments) or
            any(arg == "--session" or arg.startswith("--session=") for arg in arguments)):
        raise ValueError("use a supported session command; --session is supplied by the wrapper. "
                         "workflow run requires an existing session; invoke task-start, task-list "
                         "and operation-based commands directly through session.py.")


def execute(session, arguments, *, full=False):
    """One exact session command; caller owns coordination for a sequence."""
    validate_arguments(arguments)
    if not CLI.is_file():
        raise ValueError("the installed session.py is missing")
    root = _root(session)
    command = [sys.executable, str(CLI), *arguments, "--session", str(session)]
    started = now()
    timed_out = False
    with (root / "stdout.json").open("xb") as stdout, (root / "stderr.txt").open("xb") as stderr:
        process = subprocess.Popen(command, stdin=None, stdout=stdout, stderr=stderr,
                                   start_new_session=True)
        try:
            exit_code = process.wait(timeout=TIMEOUT)
        except BaseException as exc:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            if not isinstance(exc, subprocess.TimeoutExpired):
                write_json(root / "execution.json", {"command": command, "startedAt": started,
                           "finishedAt": now(), "status": "interrupted", "nextAction": "Inspect canonical receipts before any retry."})
                raise
            timed_out = True
            exit_code = 1
    content = (root / "stdout.json").read_bytes()
    execution = {"command": command, "startedAt": started, "finishedAt": now(), "exitCode": exit_code,
                 "timedOut": timed_out, "stdoutSha256": sha(content)}
    write_json(root / "execution.json", execution)
    result_file = {"path": str(root / "stdout.json"), "sha256": sha(content)}
    if timed_out:
        result = {"status": "command-timeout", "nextAction": "Inspect canonical receipts; reconcile unknown publication, never blindly retry."}
    else:
        try:
            result = json.loads(content)
            if type(result) is not dict:
                raise ValueError("not an object")
        except (ValueError, UnicodeError):
            result = {"status": "unreadable-command-result", "nextAction": "Inspect complete stdout/stderr files; no success inferred."}
            exit_code = exit_code or 1
    if full:
        return {"exitCode": exit_code, "resultFile": result_file, "result": result}
    return {"exitCode": exit_code, "resultFile": result_file, "execution": str(root / "execution.json"),
            "stderr": str(root / "stderr.txt"), "result": summarize(result),
            "fullPackageRequiredForReview": arguments[0] in {"asset-show", "state-show"}}


def check_state(session):
    """Require fresh canonical reconciliation of every approved local result."""
    from state_results import _session_data
    with coordinator(session):
        try:
            data = _session_data(session, None, {})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return {"status": "state-incomplete", "reason": str(exc), "banks": [],
                    "nextAction": "Resolve session validation before checking state again."}
        banks = []
        for id in data["selectedIds"]:
            checked = execute(session, ["state-prepare", "--ids", str(id)])
            result = checked["result"]
            original = next(b for b in data["banks"] if b["id"] == id)
            inherited = not original.get("report") and original.get("point") == "needs-human"
            complete = checked["exitCode"] == 0 and result.get("status") == "no-state-changes"
            banks.append({"id": id, "complete": complete,
                          "resultKind": "inherited-stop-no-new-attempt" if complete and inherited else "attempt-result",
                          "exitCode": checked["exitCode"],
                          "status": result.get("status", "blocked"),
                          "reason": result.get("reason", result.get("error")) or
                                    (original.get("reason") if complete and inherited else None),
                          "packageHash": result.get("packageHash"),
                          "nextAction": result.get("nextAction") or ("none" if complete else
                              "Inspect the canonical result; resolve stops or review and authorize the exact state package."),
                          "resultFile": checked["resultFile"], "result": result})
        pending = [b['id'] for b in data['banks'] if any(p.get('status') == 'pending' and not p.get('mr')
                   for p in (b.get('state') or {}).get('proposals', []))]
        complete = all(bank["complete"] for bank in banks)
        return {"status": "state-complete" if complete else "state-incomplete",
                "selectedIds": data["selectedIds"], "banks": banks,
                "taskComplete": complete and not pending, "pendingIds": pending,
                "nextAction": "Pending is an interruption checkpoint, not a finished task. Continue the candidate or task-finish its actual stop; then publish/check state." if pending else "none" if complete else "Resolve every incomplete bank before normal run completion."}



def publish_assets(session, packages, *, authorization=None, run_authorization=None):
    """Publish explicitly named, already shown packages serially; stop on any nonterminal outcome."""
    import re
    if (type(packages) is not list or not packages or
            any(type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value) for value in packages) or
            len(set(packages)) != len(packages) or
            (authorization and run_authorization) or (authorization and len(packages) != 1)):
        raise ValueError("use distinct exact packages and one applicable authority; exact grants cover one package")
    results = []
    with coordinator(session):
        for package in packages:
            args = ["asset-publish", "--package", package]
            if authorization:
                args += ["--authorization", authorization]
            if run_authorization:
                args += ["--run-authorization", run_authorization]
            result = execute(session, args)
            results.append({"packageHash": package, **result})
            if result["exitCode"] != 0 or result["result"].get("status") != "mr-confirmed":
                return {"status": "stopped", "results": results, "unattempted": packages[len(results):],
                        "nextAction": "Resolve the reported stop; no automatic retry or duplicate creation."}
    return {"status": "mr-confirmed", "results": results, "unattempted": [],
            "nextAction": "Prepare/show separate service-state publication; do not wait for merge."}
