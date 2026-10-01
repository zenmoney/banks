#!/usr/bin/env python3
"""Package generator-validated original SVGs for the native iOS catalog."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile


HASH = re.compile(r"[0-9a-f]{64}\Z")
ENTRY_FIELDS = {"id", "title", "sourcePath", "sourceSha256", "xmlSha256",
                "sourceWidth", "sourceHeight", "viewBox", "naturalWidth",
                "naturalHeight", "resourceName", "error", "warnings"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def overlaps(first, second):
    return first == second or first in second.parents or second in first.parents


def path_under(path, root):
    if not path.is_relative_to(root):
        raise ValueError(f"Source path escapes --source-root: {path}")
    return path


def parse_candidates(values, source_root):
    overrides = {}
    for value in values:
        identifier, separator, filename = value.partition("=")
        if not separator or not re.fullmatch(r"-?\d+", identifier) or not filename:
            raise ValueError(f"Invalid --candidate (expected ID=PATH): {value}")
        bank_id = int(identifier)
        if bank_id in overrides:
            raise ValueError(f"Duplicate --candidate ID: {bank_id}")
        candidate = Path(filename).expanduser()
        overrides[bank_id] = (candidate if candidate.is_absolute() else path_under(
            (source_root / candidate).resolve(), source_root)).resolve()
    return overrides


def checked_manifest(raw):
    manifest = json.loads(raw)
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 1 or isinstance(manifest.get("schemaVersion"), bool):
        raise ValueError("Unsupported or missing manifest schemaVersion")
    pins = manifest.get("pins")
    if not isinstance(pins, dict) or not all(isinstance(pins.get(key), str) and pins[key] for key in ("converter", "compose")):
        raise ValueError("Missing manifest pins")
    if not isinstance(manifest.get("entries"), list):
        raise ValueError("Missing manifest entries")
    return manifest


def source_for(entry, overrides, source_root):
    source_path = Path(entry["sourcePath"])
    identifier = entry["id"]
    if identifier in overrides:
        return overrides.pop(identifier)
    if source_path.is_absolute():
        raise ValueError(f"Absolute candidate source requires --candidate {identifier}=PATH")
    return path_under((source_root / source_path).resolve(), source_root)


def prepare_entries(manifest, manifest_path, source_root, output, overrides):
    entries = []
    sources = []
    names = set()
    for entry in manifest["entries"]:
        if not isinstance(entry, dict) or not ENTRY_FIELDS.issubset(entry):
            raise ValueError("Malformed manifest entry")
        identifier = entry["id"]
        if identifier is not None and (isinstance(identifier, bool) or not isinstance(identifier, int)):
            raise ValueError("Invalid bank ID")
        if not isinstance(entry["sourcePath"], str) or not entry["sourcePath"]:
            raise ValueError("Missing sourcePath")
        marker = entry.get("sourceValidated")
        if marker is not None and type(marker) is not bool:
            raise ValueError("Invalid sourceValidated marker")
        packaged = entry.copy()
        if entry.get("sourceValidated") is True:
            if identifier is None or not HASH.fullmatch(entry["sourceSha256"] or ""):
                raise ValueError(f"Validated source requires ID and SHA-256: {identifier}")
            source = source_for(entry, overrides, source_root)
            if overlaps(output, source) or overlaps(output, manifest_path):
                raise ValueError(f"Output overlaps source or manifest: {output}")
            data = source.read_bytes()
            if digest(data) != entry["sourceSha256"]:
                raise ValueError(f"Source hash mismatch: {source}")
            name = f"bank_m{-identifier}" if identifier < 0 else f"bank_{identifier}"
            if name in names:
                raise ValueError(f"Duplicate native asset name: {name}")
            names.add(name)
            resource = entry["resourceName"]
            xml_hash = entry["xmlSha256"]
            if resource is None and xml_hash is None:
                if not isinstance(entry["error"], str) or not entry["error"]:
                    raise ValueError(f"Missing XML without converter error: {identifier}")
            elif resource == name and isinstance(xml_hash, str) and HASH.fullmatch(xml_hash) and entry["error"] is None:
                xml = manifest_path.parent / "composeResources" / "drawable" / f"{name}.xml"
                if overlaps(output, xml.resolve()):
                    raise ValueError(f"Output overlaps XML resource: {output}")
                if digest(xml.read_bytes()) != xml_hash:
                    raise ValueError(f"XML hash mismatch: {xml}")
            else:
                raise ValueError(f"Malformed XML resource fields for bank {identifier}")
            packaged["nativeAssetName"] = name
            sources.append((name, data))
        else:
            if identifier in overrides:
                raise ValueError(f"--candidate cannot bypass unvalidated source: {identifier}")
            path = Path(entry["sourcePath"])
            source = path.resolve() if path.is_absolute() else path_under((source_root / path).resolve(), source_root)
            if overlaps(output, source):
                raise ValueError(f"Output overlaps source: {output}")
            packaged["nativeAssetName"] = None
            packaged["nativeError"] = "Source SVG was not validated by the generator"
        entries.append(packaged)
    if overrides:
        raise ValueError(f"--candidate ID not in manifest: {next(iter(overrides))}")
    return entries, sources


def verify_previous(output):
    """Only replace files attributable to a complete previous native package."""
    marker = output / "native-manifest.json"
    assets = output / "Assets.xcassets"
    if (not output.is_dir() or marker.is_symlink() or not marker.is_file()
            or assets.is_symlink() or not assets.is_dir()
            or {child.name for child in output.iterdir()} != {"native-manifest.json", "Assets.xcassets"}):
        raise ValueError(f"Refusing to replace non-generated output: {output}")
    previous = checked_manifest(marker.read_bytes())
    if set(previous) != {"schemaVersion", "pins", "manifestSha256", "entries"} or not isinstance(previous["manifestSha256"], str) or not HASH.fullmatch(previous["manifestSha256"]):
        raise ValueError(f"Invalid previous native manifest: {marker}")
    root_contents = assets / "Contents.json"
    if root_contents.is_symlink() or json.loads(root_contents.read_bytes()) != {"info": {"version": 1, "author": "xcode"}}:
        raise ValueError(f"Invalid previous asset catalog: {assets}")
    owned = {"Contents.json"}
    for row in previous["entries"]:
        if not isinstance(row, dict) or "nativeAssetName" not in row:
            raise ValueError(f"Malformed previous native entry: {marker}")
        name = row["nativeAssetName"]
        if name is None:
            continue
        identifier = row.get("id")
        expected = f"bank_m{-identifier}" if type(identifier) is int and identifier < 0 else f"bank_{identifier}"
        source_hash = row.get("sourceSha256")
        if (type(identifier) is not int or row.get("sourceValidated") is not True
                or name != expected or not isinstance(source_hash, str) or not HASH.fullmatch(source_hash)):
            raise ValueError(f"Invalid previous native entry: {marker}")
        folder = f"{name}.imageset"
        if folder in owned:
            raise ValueError(f"Duplicate previous native asset: {name}")
        owned.add(folder)
        imageset = assets / folder
        if imageset.is_symlink() or not imageset.is_dir() or {file.name for file in imageset.iterdir()} != {"Contents.json", "original.svg"}:
            raise ValueError(f"Unexpected previous imageset content: {imageset}")
        image = imageset / "original.svg"
        contents = imageset / "Contents.json"
        if (image.is_symlink() or contents.is_symlink() or digest(image.read_bytes()) != source_hash
                or json.loads(contents.read_bytes()) != {
                    "images": [{"filename": "original.svg", "idiom": "universal"}],
                    "info": {"version": 1, "author": "xcode"},
                    "properties": {"preserves-vector-representation": True},
                }):
            raise ValueError(f"Altered previous imageset: {imageset}")
    if {child.name for child in assets.iterdir()} != owned:
        raise ValueError(f"Unrelated asset catalog content: {assets}")


def publish(output, result, sources):
    if output.exists() or output.is_symlink():
        verify_previous(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".native-assets-", dir=output.parent))
    backup = temporary / "previous"
    try:
        staged = temporary / "Generated"
        assets = staged / "Assets.xcassets"
        assets.mkdir(parents=True)
        (assets / "Contents.json").write_text(json.dumps({"info": {"version": 1, "author": "xcode"}}, indent=2) + "\n")
        for name, data in sources:
            imageset = assets / f"{name}.imageset"
            imageset.mkdir()
            (imageset / "original.svg").write_bytes(data)
            (imageset / "Contents.json").write_text(json.dumps({
                "images": [{"filename": "original.svg", "idiom": "universal"}],
                "info": {"version": 1, "author": "xcode"},
                "properties": {"preserves-vector-representation": True},
            }, indent=2) + "\n")
        (staged / "native-manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        try:
            if output.exists():
                output.rename(backup)
            staged.rename(output)
        except BaseException:
            if backup.exists():
                try:
                    if output.exists():
                        output.rename(temporary / "interrupted")
                    backup.rename(output)
                except BaseException as restore_error:
                    raise RuntimeError(f"Previous package preserved at {backup}; restore manually") from restore_error
            raise
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        if not backup.exists():
            shutil.rmtree(temporary)



def package(manifest_path, source_root, output, candidates, require_native=None):
    if not source_root.is_dir():
        raise ValueError(f"--source-root is not a directory: {source_root}")
    manifest_path = manifest_path.resolve(strict=True)
    source_root = source_root.resolve(strict=True)
    output = output.absolute()
    if output.is_symlink():
        raise ValueError(f"Output is a symbolic link: {output}")
    output = output.resolve()
    if overlaps(output, manifest_path):
        raise ValueError(f"Output overlaps manifest: {output}")
    original_manifest = manifest_path.read_bytes()
    manifest = checked_manifest(original_manifest)
    overrides = parse_candidates(candidates, source_root)
    if require_native is not None and overrides and set(overrides) != {require_native}:
        raise ValueError("--candidate ID must match --require-native ID")
    entries, sources = prepare_entries(manifest, manifest_path, source_root, output, overrides)
    if require_native is not None:
        selected = [entry for entry in entries if entry["id"] == require_native]
        if len(selected) != 1 or selected[0]["nativeAssetName"] is None:
            raise ValueError(f"Required native bank ID {require_native} is missing, ambiguous, or unvalidated")
    result = {"schemaVersion": manifest["schemaVersion"], "pins": manifest["pins"],
              "manifestSha256": digest(original_manifest), "entries": entries}
    publish(output, result, sources)
    print(f"Packaged {len(sources)} original SVG assets (packaging only; not rendered).")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate", action="append", default=[], metavar="ID=PATH")
    parser.add_argument("--require-native", type=int, metavar="ID")
    args = parser.parse_args()
    try:
        package(args.manifest, args.source_root, args.output, args.candidate, args.require_native)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Failed to package original SVGs: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
