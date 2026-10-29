"""Per-bank research accounting with frozen operator settings.

This is not an authorization or sandbox boundary. Native client tools must use
reserve/finish in their adapter; calls outside an adapter cannot be intercepted.
No session/report/state transition or publication is performed here.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import uuid
from urllib.parse import urlsplit

from source_evidence import now, sha, write_json, read_json, fresh, link

KINDS = ("search", "fetch", "browser")
# Operational defaults, not measured optimal research counts. Operator overrides
# are frozen before the first action; exhaustion never stops durable state work.
DEFAULTS = {"search": 20, "fetch": 100, "browser": 20}
ENVIRONMENT = {kind: f"BANK_ICON_RESEARCH_{kind.upper()}_LIMIT" for kind in KINDS}
MAX_EVENTS = 10000
MAX_OUTPUT = 16 * 1024 * 1024


def _website_hint(value):
    """Syntax-only discovery hint. Never upgrade metadata to retrieval evidence."""
    if (not isinstance(value, str) or not value or value != value.strip() or
            len(value) > 2048 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value) or
            "\\" in value):
        return None, None
    qualified = "://" in value
    # Bare hosts only: not a path, email, port, protocol-relative URL or query.
    if not qualified and any(c in value for c in "/:@?#%"):
        return None, None
    try:
        parsed = urlsplit(value if qualified else "https://" + value)
        if (parsed.scheme not in {"https", "http"} or parsed.username is not None or
                parsed.password is not None or parsed.hostname is None or
                (parsed.port is not None and not 1 <= parsed.port <= 65535)):
            return None, None
        host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except (ValueError, UnicodeError):
        return None, None
    labels = host.split(".")
    if (len(host) > 253 or len(labels) < 2 or labels[-1].isdigit() or
            labels[-1] in {"local", "localhost", "internal"} or
            any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part) for part in labels)):
        return None, None
    return (value if qualified else None), host


def plan(bank):
    metadata = bank["metadata"]
    names = list(dict.fromkeys(value.strip() for value in
                 (metadata.get("title"), metadata.get("fullTitle"))
                 if isinstance(value, str) and value.strip()))
    url = metadata.get("url")
    official_url, host = _website_hint(url)
    queries = [f'"{name}" bank logo original svg' for name in names]
    return {"names": names, "officialUrl": official_url, "officialHost": host,
            "officialQueries": [f'site:{host} logo brand press media' ] if host else [],
            "independentQueries": queries,
            "imageDiscoveryQueries": [f'"{names[0]}" bank symbol logo'] if names else [],
            "stages": [
                {"stage": "official", "action": "Open the known bank website and brand/press materials; establish the represented entity and period."},
                {"stage": "vector", "action": "Inspect direct SVG, inline SVG, sprite symbols and downloadable source bundles. Keep original bytes and context."},
                {"stage": "independent", "action": "Resolve only the remaining identity, currency, variant or provenance gap. Add historical/alternative names only when evidenced."},
                {"stage": "image-discovery", "action": "Use image search only to find variants and originating pages; thumbnails are not replacement sources."}],
            "stop": "Stop once the decision is supported or the assigned budget is exhausted; record the exact remaining gap. Never trace raster artwork."}


def _text(value, label, limit=8192):
    if (type(value) is not str or not value.strip() or len(value) > limit or
            any(ord(c) < 32 and c not in '\n\t' for c in value)):
        raise ValueError(f"{label} must be nonempty bounded text")
    return value.strip()


def _regular(path):
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise ValueError("research input must be a regular non-symlink file")
    if path.stat().st_size > MAX_OUTPUT:
        raise ValueError("research input is too large")
    return path.read_bytes()


def _counts(group):
    if (type(group) is not dict or group.keys() != set(KINDS) or
            any(type(n) is not int or not 0 <= n <= 1000 for n in group.values())):
        raise ValueError("each limit must be an integer from 0 to 1000")
    return group


def _limits(value):
    """Only the current per-bank limits format is accepted."""
    if type(value) is not dict or value.keys() != {"bank"}:
        raise ValueError("limits require exactly one bank object")
    for group in value.values():
        _counts(group)
    return value


def settings():
    """Read once at attempt start. Absence uses defaults; an explicit typo fails."""
    limits, sources = {}, {}
    for kind, name in ENVIRONMENT.items():
        raw = os.environ.get(name)
        if raw is None:
            limits[kind], sources[kind] = DEFAULTS[kind], "default"
        else:
            if re.fullmatch(r"[0-9]{1,4}", raw) is None or int(raw) > 1000:
                raise ValueError(f"{name} must be a decimal integer from 0 to 1000; unset it to use the default")
            limits[kind], sources[kind] = int(raw), name
    return {"limits": {"bank": limits}, "sources": sources}


@contextmanager
def _lock(session):
    import fcntl
    root = session / "research"
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError("research workspace cannot contain symlinks")
    root.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(root / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield root
    except BlockingIOError as exc:
        raise ValueError("another research ledger operation is in progress") from exc
    finally:
        os.close(fd)


def _load(root, base):
    _regular(root / "limits.json")
    policy = read_json(root / "limits.json")
    if (type(policy) is not dict or type(policy.get("schemaVersion")) is not int or
            policy.get("baseCommit") != base):
        raise ValueError("research budget differs from the frozen base")
    if policy["schemaVersion"] != 2:
        raise ValueError("unsupported research budget version; expected schemaVersion 2")
    if (policy.keys() != {"schemaVersion", "baseCommit", "limits", "sources"} or
            set(_limits(policy["limits"])) != {"bank"} or type(policy["sources"]) is not dict or
            policy["sources"].keys() != set(KINDS) or
            any(policy["sources"][kind] not in ("default", ENVIRONMENT[kind]) for kind in KINDS)):
        raise ValueError("invalid per-bank research budget")
    digest = sha(_regular(root / "limits.json"))
    files = sorted(root.glob("event-*.json"))
    if len(files) > MAX_EVENTS:
        raise ValueError("research journal is too large")
    previous, events = digest, []
    for index, path in enumerate(files):
        if path.name != f"event-{index:05d}.json":
            raise ValueError("research journal is incomplete")
        raw = _regular(path)
        event = json.loads(raw)
        if event.get("previousSha256") != previous:
            raise ValueError("research journal or budget was changed")
        if event.get("operation") not in {"reserve", "finish"}:
            raise ValueError("unknown research journal operation")
        events.append(event)
        previous = sha(raw)
    return policy, events, previous


def _append(root, events, previous, value):
    if len(events) >= MAX_EVENTS:
        raise ValueError("research journal is full; retain it and finish the run")
    value = {**value, "at": now(), "previousSha256": previous}
    write_json(root / f"event-{len(events):05d}.json", value)
    return value


def _init(root, base):
    path = root / "limits.json"
    if path.exists() or path.is_symlink():
        _load(root, base)
        return
    if list(root.glob("event-*.json")):
        raise ValueError("research budget was removed; cannot reset the journal")
    write_json(path, {"schemaVersion": 2, "baseCommit": base, **settings()})


def _summary(policy, events, digest, ids=()):
    reservations = [e for e in events if e["operation"] == "reserve"]
    used = {kind: sum(e.get("kind") == kind for e in reservations) for kind in KINDS}
    ids = sorted(set(ids) | {e["id"] for e in events if e["operation"] == "reserve"})
    banks = {}
    for id in ids:
        counts = {kind: sum(e.get("id") == id and e.get("kind") == kind for e in reservations) for kind in KINDS}
        caps = dict(policy["limits"]["bank"])
        remaining = {k: max(0, caps[k] - counts[k]) for k in KINDS}
        banks[str(id)] = {"limits": caps, "used": counts, "remaining": remaining}
    return {"status": "research-budget", "mode": "per-bank",
            "policy": policy, "policySha256": digest, "used": used, "banks": banks}


def describe(session=None, base=None, ids=()):
    """Read-only preflight: no directory, lock, snapshot or event is created."""
    if session is not None:
        root = Path(session) / "research"
        if root.exists() or root.is_symlink():
            policy, events, _ = _load(root, base)
            return {**_summary(policy, events, sha(_regular(root / "limits.json")), ids), "frozen": True}
    return {"status": "research-budget", "mode": "per-bank", "frozen": False, **settings()}


def configure(session, base, *, ids=()):
    with _lock(session) as root:
        _init(root, base)
        policy, events, _ = _load(root, base)
        return {**_summary(policy, events, sha(_regular(root / "limits.json")), ids), "frozen": True}


def reserve(session, base, id, kind, request, gap, *, retry_reason=None):
    if kind not in KINDS or type(id) is not int:
        raise ValueError("research action needs a bank ID and known kind")
    request, gap = _text(request, "request"), _text(gap, "remaining evidence gap", 2000)
    if retry_reason is not None:
        retry_reason = _text(retry_reason, "changed-input retry reason", 2000)
    # Preserve URL case/query/encoding: different URLs may identify different bytes.
    normalized = " ".join(request.split()).casefold() if kind == "search" else request
    key = sha(json.dumps([id, kind, normalized], ensure_ascii=False).encode())
    with _lock(session) as root:
        _init(root, base)
        policy, events, previous = _load(root, base)
        reservations = [e for e in events if e["operation"] == "reserve"]
        prior = next((e for e in reversed(reservations) if e["key"] == key), None)
        if prior and retry_reason is None:
            finished = next((e for e in reversed(events) if e["operation"] == "finish" and e["ticket"] == prior["ticket"]), None)
            return {"status": "research-duplicate", "ticket": prior["ticket"],
                    "result": finished.get("result") if finished else None,
                    "nextAction": "Reuse the recorded result; unfinished/failed actions are not retried without a concrete changed-input reason."}
        bank_used = sum(e["kind"] == kind and e["id"] == id for e in reservations)
        bank_limit = policy["limits"]["bank"][kind]
        if bank_used >= bank_limit:
            return {"status": "research-budget-exhausted", "kind": kind, "id": id,
                    "used": {"bank": bank_used}, "limit": bank_limit,
                    "nextAction": "Finish the source decision with its remaining gap; recording, rendering and authorized state publication remain available."}
        ticket = uuid.uuid4().hex
        _append(root, events, previous, {"operation": "reserve", "ticket": ticket, "id": id,
                "kind": kind, "key": key, "request": request, "gap": gap, "retryReason": retry_reason})
        return {"status": "research-reserved", "ticket": ticket, "id": id, "kind": kind,
                "remaining": {"bank": bank_limit-bank_used-1}}


def finish(session, base, ticket, result, *, expected_id=None):
    if not isinstance(ticket, str) or re.fullmatch(r"[0-9a-f]{32}", ticket) is None or type(result) is not dict:
        raise ValueError("completion needs the exact ticket and structured result")
    if len(json.dumps(result).encode()) > 65536:
        raise ValueError("save large results in evidence, not the research journal")
    with _lock(session) as root:
        _, events, previous = _load(root, base)
        reservation = next((e for e in events if e["operation"] == "reserve" and e["ticket"] == ticket), None)
        if reservation is None:
            raise ValueError("research ticket was not reserved")
        if expected_id is not None and reservation["id"] != expected_id:
            raise ValueError("research ticket belongs to another bank")
        if any(e["operation"] == "finish" and e["ticket"] == ticket for e in events):
            raise ValueError("research ticket is already complete")
        _append(root, events, previous, {"operation": "finish", "ticket": ticket, "result": result})
    return result


def run_adapter(session, data, id, kind, request, gap, adapter_path, *, retry_reason=None):
    """Run a configured search/browser adapter once; never execute source commands."""
    if kind not in {"search", "browser"}:
        raise ValueError("known HTTPS assets use workflow fetch")
    adapter = Path(adapter_path)
    if not adapter.is_absolute():
        raise ValueError("adapter configuration must be an absolute operator-provisioned path")
    for workspace in (session, Path(data["repo"])):
        if adapter.is_relative_to(workspace):
            raise ValueError("operator adapter configuration must be outside checkout and session")
    config = json.loads(_regular(adapter))
    command = config.get("argv") if type(config) is dict else None
    if (type(config) is not dict or config.keys() != {"argv"} or type(command) is not list or not command or
            any(type(arg) is not str or not arg for arg in command) or not Path(command[0]).is_absolute()):
        raise ValueError("adapter requires an explicit argv array with an installed absolute executable; no shell")
    admitted = reserve(session, data["baseCommit"], id, kind, request, gap, retry_reason=retry_reason)
    if admitted["status"] != "research-reserved":
        return admitted
    root = fresh(session, id, "research-tool")
    payload = {"schemaVersion": 1, "kind": kind, "request": request, "gap": gap,
               "id": id, "evidenceDirectory": str(root)}
    # This reduces accidental credential inheritance, not an OS security sandbox.
    environment = {k: v for k, v in os.environ.items() if k not in
                   {"BANK_ICON_PUBLICATION_POLICY", "BANK_ICON_PUBLICATION_POLICY_SHA256", "GH_TOKEN", "GITHUB_TOKEN", "SSH_AUTH_SOCK"}}
    def limits():
        import resource
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT, MAX_OUTPUT))
    result = {"status": "research-tool-failed", "ticket": admitted["ticket"]}
    try:
        with (root / "stdout.json").open("xb") as out, (root / "stderr.txt").open("xb") as err:
            proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=out, stderr=err,
                                    cwd=root, env=environment, start_new_session=True, preexec_fn=limits)
            try:
                proc.communicate(json.dumps(payload).encode(), timeout=90)
            except BaseException:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
                raise
        raw = _regular(root / "stdout.json")
        output = json.loads(raw)
        if proc.returncode != 0 or type(output) is not dict:
            raise ValueError("adapter failed or returned non-object JSON; inspect exact output")
        result.update(status="research-tool-complete", output=link(session, root / "stdout.json"),
                      stderr=link(session, root / "stderr.txt"), exitCode=proc.returncode)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        result["reason"] = str(exc)
        for name in ("stdout.json", "stderr.txt"):
            if (root / name).is_file():
                result[name] = link(session, root / name)
    finally:
        finish(session, data["baseCommit"], admitted["ticket"], result, expected_id=id)
    return result
