#!/usr/bin/env python3
"""Fetch a known HTTPS source with one narrowly qualified packaged-CA retry.

A successful transfer is saved source material, never an icon verification. No
browser, trust-store edit, source execution, state transition or publication.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import ssl
import subprocess
import sys
import tempfile
from urllib.parse import urljoin, urlsplit

CERT_DIR = Path(__file__).resolve().parents[1] / "assets/certs"
CERT_NAMES = ("russian_trusted_root_ca_pem.crt", "russian_trusted_sub_ca_pem.crt",
              "russian_trusted_sub_ca_2024_pem.crt")
MAX_BYTES = 16 * 1024 * 1024
MAX_REDIRECTS = 3
TIMEOUT = 35


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha(content):
    return hashlib.sha256(content).hexdigest()


def regular(path):
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise ValueError("expected a regular non-symlink file")
    return path.read_bytes()


def https_url(value):
    if (not isinstance(value, str) or len(value) > 8192 or
            any(ord(c) <= 32 or ord(c) == 127 for c in value)):
        raise ValueError("source must be an exact HTTPS URL without control characters")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or
            parsed.password is not None or parsed.fragment or "\\" in value):
        raise ValueError("source must be HTTPS without credentials, fragment or backslashes")
    _ = parsed.port
    return value


def issuer_failure(code, diagnostic):
    """curl 60 is not sufficient: expiry and hostname errors never widen trust."""
    message = diagnostic.casefold()
    excluded = ("expired", "not yet valid", "revoked", "hostname", "does not match",
                "no alternative certificate", "subject name", "weak", "insecure algorithm")
    issuers = ("unable to get local issuer certificate", "unable to verify the first certificate",
               "unable to verify first certificate", "self-signed certificate", "self signed certificate",
               "issuer is not recognized", "untrusted root", "cert_trust_is_untrusted_root")
    return (code == 60 and not any(term in message for term in excluded) and
            any(term in message for term in issuers))


def _session_context(session, id):
    # Reuse the canonical reader; source retrieval does not maintain a second
    # selection/state machine. This also rejects paused/unapproved/moved sessions.
    from state_results import _session_data
    return _session_data(session, [id], {})


def fresh_output(session, id, relative):
    parts = relative.split("/")
    if (len(parts) < 3 or parts[:2] != ["evidence", str(id)] or
            any(part in ("", ".", "..") for part in parts) or "\\" in relative):
        raise ValueError("output must be a fresh relative evidence/<ID>/directory")
    path = session
    for part in parts:
        path /= part
        if path.is_symlink():
            raise ValueError("output contains a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir(mode=0o700)  # Existing output, including a previous failed run, stays immutable.
    return path


@contextmanager
def ca_bundle(session):
    """Only the listed files shipped with the opened skill; no CLI CA override."""
    identities, chunks = [], []
    for name in CERT_NAMES:
        content = regular(CERT_DIR / name)
        text = content.decode("ascii")
        der = ssl.PEM_cert_to_DER_cert(text)
        identities.append({"name": name, "fileSha256": sha(content), "certificateSha256": sha(der)})
        chunks.append(content.rstrip() + b"\n")
    bundle = b"".join(chunks)
    # Validate the PEM bundle without altering any OS/user trust store.
    ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(cadata=bundle.decode("ascii"))
    with tempfile.TemporaryDirectory(prefix=".https-ca-", dir=session) as temporary:
        path = Path(temporary) / "ca-bundle.pem"
        with path.open("xb") as stream:
            stream.write(bundle)
        yield path, {"certificates": identities, "bundleSha256": sha(bundle)}


def _run(command, directory):
    with (directory / "stderr.txt").open("xb") as errors:
        with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=errors, start_new_session=True,
                              env={**os.environ, "LC_ALL": "C"}) as process:
            try:
                stdout, _ = process.communicate(timeout=TIMEOUT)
            except BaseException:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.communicate()
                raise
    (directory / "transfer.txt").write_bytes(stdout)
    return process.returncode, stdout.decode("utf-8", errors="replace")


def transfer(curl, url, output, attempts, bundle=None):
    directory = output / f"attempt-{len(attempts) + 1}"
    directory.mkdir()
    command = [curl, "--disable", "--silent", "--show-error", "--proto", "=https",
               "--connect-timeout", "10", "--max-time", "30", "--max-filesize", str(MAX_BYTES),
               "--dump-header", str(directory / "headers.txt"),
               "--output", str(directory / "body.bin"),
               "--write-out", "%{http_code}\n%{content_type}\n"]
    if bundle is not None:
        command += ["--cacert", str(bundle)]
    # Redirects are handled here, not by curl: added trust is used only for the
    # exact failing URL. Every new redirect destination starts with normal trust.
    command += ["--url", url]
    row = {"url": url, "trust": "packaged-ca" if bundle else "default",
           "startedAt": now(), "command": command, "directory": directory.name,
           "exitCode": None, "httpStatus": None}
    attempts.append(row)
    try:
        code, summary = _run(command, directory)
        fields = summary.splitlines()
        row.update(exitCode=code, httpStatus=int(fields[0]) if fields and fields[0].isdigit() else None,
                   contentType=fields[1] if len(fields) > 1 else None)
    except subprocess.TimeoutExpired:
        row["failure"] = "process-timeout"
    except (KeyboardInterrupt, InterruptedError):
        row["failure"] = "interrupted"
        raise
    finally:
        row["finishedAt"] = now()
        error_file = directory / "stderr.txt"
        row["diagnostic"] = regular(error_file).decode("utf-8", errors="replace") if error_file.exists() else ""
    body = directory / "body.bin"
    if body.exists():
        if body.stat().st_size > MAX_BYTES:
            raise ValueError("response exceeds the source size limit")
        content = regular(body)
        row["body"] = {"path": str(body.relative_to(output)), "sha256": sha(content), "bytes": len(content)}
    return row


def redirect_location(output, row):
    text = regular(output / row["directory"] / "headers.txt").decode("iso-8859-1")
    # Ignore proxy CONNECT and interim response blocks; only the final response
    # supplies Location. Reject ambiguous multiple Location fields.
    blocks = re.split(r"\r?\n\r?\n", text)
    responses = [block for block in blocks if block.startswith("HTTP/")]
    locations = re.findall(r"(?im)^location:[ \t]*([^\r\n]*)", responses[-1] if responses else "")
    if len(locations) != 1:
        raise ValueError("redirect needs exactly one Location header")
    return https_url(urljoin(row["url"], locations[0].strip()))


def fetch(args):
    if os.name != "posix":
        raise ValueError("source helper requires the supported POSIX environment")
    session = Path(args.session).resolve(strict=True)
    url = https_url(args.url)
    data = _session_context(session, args.id)
    curl = shutil.which(args.curl or "curl")
    if not curl:
        return {"status": "unavailable", "reason": "curl is not installed", "caRetry": "not-attempted"}
    version = subprocess.check_output([curl, "--disable", "--version"], timeout=10).decode().splitlines()[0]
    output = fresh_output(session, args.id, args.output)
    receipt = {"schemaVersion": 1, "id": args.id, "baseCommit": data["baseCommit"],
               "requestedUrl": url, "tool": version, "status": "failed", "caRetry": "not-needed",
               "attempts": [], "startedAt": now()}
    try:
        visited = set()
        for _ in range(MAX_REDIRECTS + 1):
            if url in visited:
                raise ValueError("redirect loop")
            visited.add(url)
            row = transfer(curl, url, output, receipt["attempts"])
            if issuer_failure(row["exitCode"], row["diagnostic"]) and receipt["caRetry"] == "not-needed":
                receipt["caRetry"] = "unavailable"
                with ca_bundle(session) as (bundle, identity):
                    receipt["ca"] = identity
                    receipt["caRetry"] = "used"
                    row = transfer(curl, url, output, receipt["attempts"], bundle)
            if row["exitCode"] != 0:
                receipt["reason"] = row.get("failure") or "HTTPS transfer failed; inspect the saved diagnostic"
                break
            status = row["httpStatus"] or 0
            if 200 <= status < 300 and row.get("body", {}).get("bytes", 0) > 0:
                receipt.update(status="fetched", finalUrl=url,
                               body={**row["body"], "path": str((output / row["body"]["path"]).relative_to(session))},
                               nextAction="Inspect bytes/page context, then assess identity, period and variant; transfer is not verification.")
                break
            if status in (301, 302, 303, 307, 308):
                url = redirect_location(output, row)
                continue
            receipt["reason"] = f"HTTP {status} or empty response; no icon verification"
            break
        else:
            receipt["reason"] = "redirect limit reached"
    except (OSError, ValueError, ssl.SSLError) as exc:
        receipt["reason"] = str(exc)
    except (KeyboardInterrupt, InterruptedError):
        receipt["reason"] = "interrupted; evidence retained"
    finally:
        receipt["finishedAt"] = now()
        with (output / "receipt.json").open("x", encoding="utf-8") as stream:
            json.dump(receipt, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    return {**receipt, "output": str(output), "receipt": str(output / "receipt.json")}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument("--session", required=True)
    parser.add_argument("--id", type=int, required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", required=True, help="new relative evidence/<ID>/directory")
    parser.add_argument("--curl", help="existing curl executable; defaults to PATH")
    try:
        result = fetch(parser.parse_args(argv))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        result = {"status": "blocked", "reason": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "fetched" else 1


if __name__ == "__main__":
    def interrupted(signum, frame):
        raise InterruptedError(f"signal {signum}")
    for signum in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, interrupted)
    sys.exit(main())
