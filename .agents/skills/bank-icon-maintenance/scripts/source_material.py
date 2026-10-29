"""Resolve an exact source material from a downloaded resource, without adapting it.

Container/compression support is private to this module. Consumers use the same
receipt-bound material for direct files and selected members. No extractall,
archive-named filesystem writes, execution, installation, network or state writes.
"""
import importlib
import hashlib
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import zipfile

MAX_INPUT = 16 * 1024 * 1024
MAX_MATERIAL = 16 * 1024 * 1024
MAX_EXPANDED = 64 * 1024 * 1024
MAX_ENTRIES = 512
MAX_LAYERS = 6
MAX_NAME = 1024
TIMEOUT = 30
SCOPE = "bank-icon-source-material"


def _sha(content):
    return hashlib.sha256(content).hexdigest()


def _name(value):
    # Member names identify bytes, never output paths. Permit common ./ prefixes,
    # but detect aliases in each archive before any selection is accepted.
    if (type(value) is not str or not value or len(value.encode("utf-8")) > MAX_NAME or
            "\\" in value or ":" in value or value.startswith("/") or
            any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError("unsafe or oversized source member name")
    normalized = value.rstrip("/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized or any(p in ("", ".", "..") for p in normalized.split("/")):
        raise ValueError("unsafe source member path")
    return normalized


def _read(stream, limit):
    content = stream.read(limit + 1)
    if len(content) > limit:
        raise ValueError("source expansion exceeds the byte limit")
    return content


def _asset_format(content):
    # Detect bytes, not HTTP headers, suffixes, or archive filenames. Full source
    # safety/decoding and model inspection still happen in source_evidence.
    from vector_extract import is_html
    if is_html(content):
        return "html"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if content.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "webp"
    if content.startswith(b"\x00\x00\x01\x00"):
        return "ico"
    if content.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<"):
        import xml.etree.ElementTree as ET
        if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
            raise ValueError("source SVG declarations/entities are not allowed")
        try:
            if ET.fromstring(content).tag == "{http://www.w3.org/2000/svg}svg":
                return "svg"
        except ET.ParseError:
            pass
    return None


def _members(archive, kind):
    rows, names, total = [], set(), 0
    entries = archive.infolist() if kind == "zip" else archive
    for index, entry in enumerate(entries):
        if index >= MAX_ENTRIES:
            raise ValueError("source container exceeds the entry limit")
        name = entry.filename if kind == "zip" else entry.name
        if kind == "zip" and entry.orig_filename != name:
            raise ValueError("source member name was truncated by the container decoder")
        directory = entry.is_dir() if kind == "zip" else entry.isdir()
        # A conventional TAR root directory is harmless and is never selected.
        if directory and name in (".", "./"):
            continue
        normalized = _name(name)
        if normalized in names:
            raise ValueError("source container has duplicate/ambiguous member names")
        names.add(normalized)
        if kind == "zip":
            mode = stat.S_IFMT(entry.external_attr >> 16)
            regular = mode in (0, stat.S_IFREG) and not directory
            encrypted = bool(entry.flag_bits & 1)
            size = entry.file_size
        else:
            regular = entry.isfile() and not entry.issparse()
            encrypted = False
            size = entry.size
        if not directory and not regular:
            raise ValueError("source container contains a link, device or unsupported member type")
        if size < 0:
            raise ValueError("source member has an invalid size")
        total += size
        if total > MAX_EXPANDED:
            raise ValueError("source container exceeds the declared byte limit")
        rows.append((entry, {"name": name, "bytes": size, "directory": directory,
                              "encrypted": encrypted}))
    return rows


def decode(content, members, vector_selection=None):
    """Pure bounded selection. Call via the isolated worker for untrusted inputs."""
    if (not 0 < len(content) <= MAX_INPUT or type(members) is not list or
            len(members) > MAX_LAYERS):
        raise ValueError("invalid source size or selection depth")
    for member in members:
        _name(member)
    steps, selected, expanded = [], 0, 0
    while True:
        format = _asset_format(content)
        if format == "html" or (format == "svg" and vector_selection is not None):
            if selected != len(members):
                raise ValueError("source selection continues beyond vector material")
            from vector_extract import extract
            descriptor, output = extract(content, vector_selection)
            if descriptor["kind"] == "selection-required":
                return {**descriptor, "steps": steps}, None
            if len(steps) >= MAX_LAYERS or len(output) > MAX_MATERIAL or expanded + len(output) > MAX_EXPANDED:
                raise ValueError("vector extraction exceeds source limits")
            steps.append({"operation": "extract-vector", "format": format,
                          "selection": vector_selection, "inputSha256": _sha(content),
                          "outputSha256": _sha(output), "bytes": len(output)})
            expanded += len(output)
            content, vector_selection = output, None
            continue
        if format is not None:
            if vector_selection is not None:
                raise ValueError("vector selection cannot be applied to raster material")
            if selected != len(members):
                raise ValueError("source selection continues beyond an image")
            if len(content) > MAX_MATERIAL:
                raise ValueError("source material exceeds the byte limit")
            return {"kind": "material", "format": format, "sha256": _sha(content),
                    "bytes": len(content), "steps": steps}, content
        if len(steps) >= MAX_LAYERS:
            raise ValueError("source container exceeds the layer limit")
        wrapped = next(((name, module) for magic, name, module in (
            (b"\x1f\x8b", "gzip", "gzip"), (b"BZh", "bzip2", "bz2"),
            (b"\xfd7zXZ\x00", "xz", "lzma")) if content.startswith(magic)), None)
        before = _sha(content)
        if wrapped:
            kind, module = wrapped
            try:
                opener = importlib.import_module(module).open
            except ImportError as exc:
                raise ValueError(f"source compression decoder is unavailable: {kind}") from exc
            with opener(io.BytesIO(content), mode="rb") as stream:
                output = _read(stream, MAX_EXPANDED - expanded)
            operation = {"operation": "decompress", "format": kind}
        else:
            if zipfile.is_zipfile(io.BytesIO(content)):
                kind, archive = "zip", zipfile.ZipFile(io.BytesIO(content))
            else:
                try:
                    kind, archive = "tar", tarfile.open(fileobj=io.BytesIO(content), mode="r:")
                except tarfile.ReadError as exc:
                    raise ValueError("unsupported resource; no supported material/container decoder") from exc
            with archive:
                rows = _members(archive, kind)
                if selected == len(members):
                    return {"kind": "selection-required", "format": kind,
                            "members": [row for _, row in rows], "steps": steps}, None
                name = members[selected]
                matches = [(entry, row) for entry, row in rows if row["name"] == name]
                if len(matches) != 1:
                    raise ValueError("selected source member does not exist exactly once")
                entry, row = matches[0]
                if row["directory"] or row["encrypted"]:
                    raise ValueError("select a regular, unencrypted source member")
                if row["bytes"] > MAX_MATERIAL:
                    raise ValueError("selected source member exceeds the byte limit")
                stream = archive.open(entry) if kind == "zip" else archive.extractfile(entry)
                if stream is None:
                    raise ValueError("selected source member is not readable")
                with stream:
                    output = _read(stream, min(MAX_MATERIAL, MAX_EXPANDED - expanded))
                if len(output) != row["bytes"]:
                    raise ValueError("selected source member size differs from container metadata")
            selected += 1
            operation = {"operation": "extract-member", "format": kind, "member": name}
        if not output:
            raise ValueError("source decoder returned an empty material")
        expanded += len(output)
        steps.append({**operation, "inputSha256": before, "outputSha256": _sha(output),
                      "bytes": len(output)})
        content = output


def _worker():
    # A bounded subprocess keeps parser/decompressor resource limits out of the
    # coordinator. It starts no children and never uses a name from an archive.
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    try:
        request = json.loads(sys.stdin.buffer.read(65537))
        path = Path(request["input"])
        if not path.is_file() or any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError("source worker needs a regular input")
        with path.open("rb") as stream:
            content = _read(stream, MAX_INPUT)
        if _sha(content) != request["sha256"]:
            raise ValueError("download changed before source decoding")
        result, material = decode(content, request["members"], request.get("vectorSelection"))
        if material is not None and request.get("output") is not None:
            destination = Path(request["output"])
            if any(p.is_symlink() for p in (destination, *destination.parents)):
                raise ValueError("source output cannot contain a symlink")
            with destination.open("xb") as stream:
                stream.write(material)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, EOFError,
            MemoryError, tarfile.TarError, zipfile.BadZipFile, NotImplementedError) as exc:
        print(json.dumps({"kind": "blocked", "reason": str(exc) or type(exc).__name__}))
        return 1


def inspect(path, expected_hash, members=None, *, output=None, vector_selection=None):
    """Inspect/select without rendering, network access or publication authority."""
    request = {"input": str(path), "sha256": expected_hash,
               "members": [] if members is None else members, "vectorSelection": vector_selection,
               "output": str(output) if output is not None else None}
    try:
        process = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker"],
            input=json.dumps(request).encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=TIMEOUT, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("source decoder timed out; original resource retained") from exc
    try:
        result = json.loads(process.stdout)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("source decoder failed or exceeded resource limits") from exc
    if process.returncode or result.get("kind") == "blocked":
        raise ValueError(result.get("reason", "source decoder failed"))
    return result


def prepare(session, receipt_link, id, base_commit, members=None, vector_selection=None):
    """Append material evidence or return a finite member listing. Never save state."""
    import source_evidence as source
    checked = source.instant(source.now(), "material command time")
    receipt, downloaded = source.retrieval(session, receipt_link, id, base_commit, checked)
    root = source.fresh(session, id, "source-material")
    output = root / "material.bin"
    chosen = [] if members is None else members
    decoded = inspect(downloaded, receipt["body"]["sha256"], chosen, output=output, vector_selection=vector_selection)
    if decoded["kind"] == "selection-required":
        if "members" in decoded:
            from source_listing import save_listing
            return save_listing(session, root, receipt_link, receipt, downloaded,
                                id, base_commit, chosen, decoded)
        return {"status": "source-selection-required", "resourceSha256": receipt["body"]["sha256"],
                **decoded, "nextAction": ("Choose the evidenced inline vector with --svg-index or --svg-id; optionally --symbol-id."
                               if decoded["format"] == "html" else
                               "Choose the evidenced variant by exact member name; repeat --member for nested containers.")}
    if (source.sha(output.read_bytes()) != decoded["sha256"] or
            source.sha(downloaded.read_bytes()) != receipt["body"]["sha256"]):
        raise ValueError("source decoder input/output changed")
    value = {"schemaVersion": 1, "scope": SCOPE, "id": id, "baseCommit": base_commit,
             "retrieval": receipt_link, "download": source.link(session, downloaded),
             "selection": chosen, "steps": decoded["steps"],
             "material": source.link(session, output), "format": decoded["format"],
             "createdAt": source.now(), "decoder": {"name": "python-stdlib", "version": sys.version.split()[0]}}
    if vector_selection is not None:
        value["vectorSelection"] = vector_selection
    path = root / "material.json"
    source.write_json(path, value)
    return {"status": "source-material-ready", "sourceMaterial": source.link(session, path),
            "material": value["material"], "format": value["format"],
            "nextAction": "Use this material binding, not a fabricated direct URL; inspect the source and final candidate separately."}


def validate(session, material_link, receipt_link, id, base_commit, checked):
    """Replay selection from the original resource; saved commands/hashes alone are insufficient."""
    import source_evidence as source
    path = source.linked(session, material_link, "source material manifest")
    value = source.read_json(path)
    fields = {"schemaVersion", "scope", "id", "baseCommit", "retrieval", "download",
              "selection", "steps", "material", "format", "createdAt", "decoder"}
    if (not fields.issubset(value) or value.keys() - (fields | {"vectorSelection"}) or
            ("vectorSelection" in value and type(value["vectorSelection"]) is not dict) or
            type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1 or
            value["scope"] != SCOPE or type(value["id"]) is not int or value["id"] != id or
            value["baseCommit"] != base_commit or value["retrieval"] != receipt_link or
            not material_link["path"].startswith(f"evidence/{id}/")):
        raise ValueError("source material is not bound to this bank, base and retrieval")
    receipt, downloaded = source.retrieval(session, receipt_link, id, base_commit, checked)
    if (value["download"] != source.link(session, downloaded) or
            not source.instant(receipt["finishedAt"], "retrieval.finishedAt") <=
                source.instant(value["createdAt"], "source material createdAt") <= checked):
        raise ValueError("source material download or chronology differs")
    decoder = value["decoder"]
    if (type(decoder) is not dict or decoder.keys() != {"name", "version"} or
            decoder["name"] != "python-stdlib" or type(decoder["version"]) is not str or
            not decoder["version"].strip() or len(decoder["version"]) > 128):
        raise ValueError("source material decoder provenance is incomplete")
    material = source.linked(session, value["material"], "exact source material")
    if (material != path.parent / "material.bin" or material.stat().st_size > MAX_MATERIAL):
        raise ValueError("source material location or size differs")
    decoded = inspect(downloaded, value["download"]["sha256"], value["selection"], vector_selection=value.get("vectorSelection"))
    if (decoded["kind"] != "material" or decoded["sha256"] != value["material"]["sha256"] or
            decoded["bytes"] != material.stat().st_size or decoded["format"] != value["format"] or
            decoded["steps"] != value["steps"]):
        raise ValueError("source material differs from reproducible extraction of original resource")
    return value, material


def links(session, material_link):
    """All material lineage inputs to freeze in publication bindings, after validation."""
    import source_evidence as source
    value = source.read_json(source.linked(session, material_link, "source material"))
    return [material_link, value["retrieval"], value["download"], value["material"]]


if __name__ == "__main__":
    if sys.argv[1:] != ["--worker"]:
        sys.exit("Use workflow.py source-material --help")
    sys.exit(_worker())
