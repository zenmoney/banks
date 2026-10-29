#!/usr/bin/env python3
"""Create one SVGO trial without editing source, final assets, or session state.

JSON stdout: trial-produced/skipped (exit 0), blocked (exit 1). --help is text.
This command performs no installation, network operation, visual judgment, SVG
safety certification, or publication. The existing candidate gates still apply.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys

PROCESS_TIMEOUT = 30
PROBE_TIMEOUT = 10


class ToolUnavailable(RuntimeError):
    """Ordinary optimization-only limitation; full working-SVG checks may continue."""


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(f"invalid arguments: {message}; use --help")


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha(content):
    return hashlib.sha256(content).hexdigest()


def _regular(path):
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise ValueError(f"expected regular non-symlink file: {path}")
    return path.read_bytes()


def _path(value):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("use verified absolute paths without traversal")
    return path


def _run(command, *, input=None, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout):
    """Bound the process group too: a killed wrapper must not leave its Node child."""
    with subprocess.Popen(command, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                          stdout=stdout, stderr=stderr, start_new_session=True) as process:
        try:
            out, err = process.communicate(input, timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
            raise
        result = subprocess.CompletedProcess(command, process.returncode, out, err)
        if result.returncode:
            raise subprocess.CalledProcessError(result.returncode, command, out, err)
        return result


def optimize(session, executable, source, output):
    """Library entry point. Failures preserve any reserved output and diagnostics.

    Tool errors raise ToolUnavailable/CalledProcessError/TimeoutExpired. Integrity
    and path errors raise ValueError/OSError, including mutation during a failed run.
    """
    if os.name != "posix":
        raise ValueError("use the supported POSIX environment (WSL on Windows)")
    session_input = _path(session)
    session = session_input.resolve(strict=True)
    source, output = _path(source), _path(output)
    # Canonicalize only the external session alias; evidence suffixes keep symlinks visible.
    for name, path in (("source", source), ("output", output)):
        try:
            suffix = path.relative_to(session_input)
        except ValueError:
            suffix = path.relative_to(session)
        candidate = session / suffix
        if len(suffix.parts) < 3 or suffix.parts[0] != "evidence":
            raise ValueError(f"{name} must live under SESSION/evidence/<ID>/")
        if any(p.is_symlink() for p in (candidate, *candidate.parents)):
            raise ValueError(f"{name} contains an evidence symlink")
        if name == "source":
            source = candidate
        else:
            output = candidate
    if source.relative_to(session).parts[1] != output.relative_to(session).parts[1]:
        raise ValueError("input and trial directory must belong to the same bank")
    if output.exists():
        raise ValueError("trial output already exists; inspect its receipt instead of overwriting")
    if not output.parent.is_dir():
        raise ValueError("trial parent must already exist under this bank's evidence")
    session_bytes = _regular(session / "session.json")
    data = json.loads(session_bytes)
    worktree = session / "worktree"
    if (type(data) is not dict or data.get("worktree") != str(worktree) or
            worktree.is_symlink() or not worktree.is_dir()):
        raise ValueError("session worktree is missing, moved or replaced")

    def git(*args):
        try:
            return _run(["git", "-C", str(worktree), *args], timeout=PROBE_TIMEOUT).stdout
        except subprocess.SubprocessError as exc:
            raise ValueError("local Git validation failed; inspect the frozen session base") from exc

    if git("rev-parse", "HEAD").decode().strip() != data.get("baseCommit"):
        raise ValueError("worktree differs from the frozen session base")
    config = worktree / "svgo.config.mjs"
    before = _regular(source)
    if not before.strip():
        raise ValueError("working SVG is empty")
    tree = git("ls-tree", "HEAD", "--", "svgo.config.mjs")
    if not tree:
        raise ToolUnavailable("frozen base has no svgo.config.mjs; keep the working version")
    if not tree.startswith((b"100644 blob ", b"100755 blob ")):
        raise ValueError("committed config is not a regular file")
    settings = _regular(config)
    if settings != git("show", "HEAD:svgo.config.mjs"):
        raise ValueError("configuration differs from the frozen session base")
    tool = str(_path(executable)) if executable is not None else shutil.which("svgo")
    if tool is None or not Path(tool).is_file() or not os.access(tool, os.X_OK):
        raise ToolUnavailable("SVGO is unavailable; keep the working version")
    try:
        version = _run([tool, "--version"], timeout=PROBE_TIMEOUT).stdout.decode().strip()
    except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
        raise ToolUnavailable(f"SVGO version probe failed: {type(exc).__name__}") from exc
    # Record the installed version; compatibility is established by the bounded
    # invocation with the frozen config and subsequent final-byte checks.

    def unchanged():
        if (_regular(source) != before or _regular(config) != settings or
                _regular(session / "session.json") != session_bytes or
                git("rev-parse", "HEAD").decode().strip() != data["baseCommit"]):
            raise ValueError("input/config/session changed during optimization; preserve diagnostics")

    unchanged()
    output.mkdir()  # Exclusive reservation: retries cannot overwrite an earlier trial.
    for name, content in (("before.svg", before), ("svgo.config.mjs", settings)):
        with (output / name).open("xb") as stream:
            stream.write(content)
    command = [tool, "--config", str(config), "--input", "-", "--output", "-", "--quiet"]
    receipt = {"schemaVersion": 1, "tool": "svgo", "version": version,
               "baseCommit": data["baseCommit"], "configSha256": _sha(settings),
               "inputSha256": _sha(before), "inputBytes": len(before), "command": command,
               "startedAt": _now(), "status": "tool-failed"}
    try:
        try:
            with (output / "optimized.svg").open("xb") as svg, (output / "svgo.log").open("xb") as log:
                try:
                    _run(command, input=before, stdout=svg, stderr=log, timeout=PROCESS_TIMEOUT)
                except OSError as exc:
                    raise ToolUnavailable(f"optimizer could not start: {type(exc).__name__}") from exc
        finally:
            unchanged()  # Also detect mutation when the optimizer exits nonzero or times out.
        after = _regular(output / "optimized.svg")
        if not after.strip():
            raise ToolUnavailable("SVGO produced empty output; keep the working version")
        receipt.update(status="trial-produced", outputSha256=_sha(after), outputBytes=len(after))
    except (ValueError, OSError, ToolUnavailable, subprocess.SubprocessError) as exc:
        receipt["error"] = str(exc)
        if isinstance(exc, ValueError):
            receipt["status"] = "integrity-failed"
        raise
    finally:
        receipt["finishedAt"] = _now()
        with (output / "svgo-receipt.json").open("x", encoding="utf-8") as stream:
            json.dump(receipt, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    return {**receipt, "receipt": str(output / "svgo-receipt.json"),
            "nextAction": "Compare working versus optimized, select bytes, then run all final candidate checks."}


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument("--session", required=True, help="existing saved session; external alias is allowed")
    parser.add_argument("--input", required=True, help="absolute working SVG under session evidence")
    parser.add_argument("--output", required=True, help="new absolute trial directory under the same bank's evidence")
    parser.add_argument("--svgo", help="absolute installed SVGO CLI; default: PATH")
    try:
        args = parser.parse_args(argv)
        result = optimize(args.session, args.svgo, args.input, args.output)
    except (ToolUnavailable, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        result = {"status": "skipped", "reason": str(exc),
                  "nextAction": "Retain diagnostics, record the optimization-only limitation, and fully verify the working SVG."}
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc),
                          "nextAction": "Correct the concrete input/integrity problem; preserve earlier evidence."},
                         ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
