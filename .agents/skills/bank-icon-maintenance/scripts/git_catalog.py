"""Exact-object, batched catalog identity reads (never checkout/filter output).

Caches live for one caller-owned operation only. A changed metadata object list
builds a new index; selected-bank SVG and metadata bindings are checked separately.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType

from state_publication import git

_CACHE = ContextVar("bank_catalog_cache", default=None)
_OID = re.compile(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_MAX_BYTES = 64 * 1024 * 1024


@contextmanager
def scope():
    token = _CACHE.set({"blobs": {}, "indexes": {}})
    try:
        yield
    finally:
        _CACHE.reset(token)


def _read_blobs(repo, oids):
    if not oids:
        return {}
    request = b"\n".join(oids) + b"\n"
    headers = git(repo, "--no-replace-objects", "cat-file", "--batch-check",
                  input=request, timeout=60).stdout.splitlines()
    sizes = []
    if len(headers) != len(oids):
        raise ValueError("incomplete catalog batch headers")
    for oid, header in zip(oids, headers):
        fields = header.split()
        if (len(fields) != 3 or fields[:2] != [oid, b"blob"] or
                not fields[2].isdigit()):
            raise ValueError("missing or non-blob catalog metadata object")
        sizes.append(int(fields[2]))
    if sum(sizes) > _MAX_BYTES:
        raise ValueError("catalog metadata batch exceeds 64 MiB; no identity proof")
    content = git(repo, "--no-replace-objects", "cat-file", "--batch",
                  input=request, timeout=60).stdout
    offset, result = 0, {}
    for oid, size in zip(oids, sizes):
        end = content.find(b"\n", offset)
        header = oid + b" blob " + str(size).encode()
        if end < 0 or content[offset:end] != header:
            raise ValueError("catalog batch header or object order changed")
        start, stop = end + 1, end + 1 + size
        if stop >= len(content) or content[stop:stop + 1] != b"\n":
            raise ValueError("truncated catalog metadata object")
        body = content[start:stop]
        algorithm = "sha1" if len(oid) == 40 else "sha256"
        actual = hashlib.new(algorithm, b"blob " + str(size).encode() + b"\0" + body,
                             usedforsecurity=False).hexdigest().encode()
        if actual != oid:
            raise ValueError("catalog metadata object hash mismatch")
        result[oid] = body
        offset = stop + 1
    if offset != len(content):
        raise ValueError("unexpected trailing catalog batch bytes")
    return result


def identity_paths(repo, commit, bank_id):
    """Find all regular info.json blobs carrying an integer Company.id.

    Preserve the old treatment of unrelated malformed/nonregular metadata, but
    never confuse a transport or truncated object read with an absent duplicate.
    """
    if not isinstance(commit, str) or not _OID.fullmatch(commit.encode()):
        raise ValueError("catalog index requires an exact commit, not a mutable ref")
    rows = git(repo, "--no-replace-objects", "ls-tree", "-r", "-z", commit,
               "--", "banks/", timeout=60).stdout
    entries = []
    for raw in rows.split(b"\0"):
        if not raw:
            continue
        header, path = raw.split(b"\t", 1)
        mode, kind, oid = header.split()
        if not _OID.fullmatch(oid):
            raise ValueError("invalid catalog tree object ID")
        if path.endswith(b"/info.json") and mode in (b"100644", b"100755") and kind == b"blob":
            entries.append((path.decode("utf-8"), oid))
    cache = _CACHE.get()
    if cache is None:
        cache = {"blobs": {}, "indexes": {}}
    root = str(Path(repo).resolve())
    key = (root, tuple(entries))
    index = cache["indexes"].get(key)
    if index is None:
        missing = sorted({oid for _, oid in entries if (root, oid) not in cache["blobs"]})
        # Do not cache partial batches, including a read interrupted by a deadline.
        parsed = {}
        for oid, body in _read_blobs(repo, missing).items():
            try:
                info = json.loads(body)
            except (ValueError, UnicodeError):
                info = None
            parsed[(root, oid)] = (info["id"] if type(info) is dict and
                                   type(info.get("id")) is int else None)
        cache["blobs"].update(parsed)
        values = {}
        for path, oid in entries:
            id = cache["blobs"][(root, oid)]
            if id is not None:
                values.setdefault(id, []).append(path)
        index = MappingProxyType({id: tuple(paths) for id, paths in values.items()})
        if len(cache["indexes"]) >= 32:
            cache["indexes"].pop(next(iter(cache["indexes"])))
        cache["indexes"][key] = index
    return list(index.get(bank_id, ()))
