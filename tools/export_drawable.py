#!/usr/bin/env python3
"""Export canonical bank SVGs for zen-mobile-app's Compose drawable resources."""

import argparse
import json
import math
from pathlib import Path
import re
import shutil
import sys
from xml.dom import minidom
from xml.parsers import expat
import zipfile


SVG_NAMESPACE = "http://www.w3.org/2000/svg"
ARCHIVE_NAME = "bank-icons-drawable.zip"
NUMBER = r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
LENGTH = re.compile(rf"({NUMBER})(?:px)?")
SEPARATOR = r"(?:[ \t\r\n]+|[ \t\r\n]*,[ \t\r\n]*)"
VIEWBOX = re.compile(SEPARATOR.join([rf"({NUMBER})"] * 4))


def positive_length(value: str, attribute: str) -> str:
    match = LENGTH.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"{attribute} must be a positive unitless or px length")
    number = match.group(1)
    parsed = float(number)
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError(f"{attribute} must be finite and positive")
    return number


def reject_declaration(*_args) -> None:
    raise ValueError("DTD and entity declarations are not allowed")


def normalize_svg(source: Path) -> bytes:
    data = source.read_bytes()
    # Expat rejects declarations before any entity expansion, including UTF-16
    # input. The DOM pass then preserves prefixes, comments, CDATA and SVG defs.
    parser = expat.ParserCreate()
    parser.StartDoctypeDeclHandler = reject_declaration
    parser.EntityDeclHandler = reject_declaration
    try:
        parser.Parse(data, True)
        document = minidom.parseString(data)
    except expat.ExpatError as error:
        raise ValueError(f"invalid XML: {error}") from error

    with document:
        root = document.documentElement
        if root.localName != "svg" or root.namespaceURI != SVG_NAMESPACE:
            raise ValueError("expected an SVG root in the SVG namespace")

        dimensions = {}
        for attribute in ("width", "height"):
            if root.hasAttribute(attribute):
                dimensions[attribute] = positive_length(root.getAttribute(attribute), attribute)

        if root.hasAttribute("viewBox"):
            viewbox = VIEWBOX.fullmatch(root.getAttribute("viewBox").strip())
            if viewbox is None:
                raise ValueError("viewBox must contain four numbers")
            values = [float(number) for number in viewbox.groups()]
            if not all(math.isfinite(number) for number in values) or values[2] <= 0 or values[3] <= 0:
                raise ValueError("viewBox must be finite with positive width and height")
        else:
            if len(dimensions) != 2:
                raise ValueError("SVG without viewBox requires both width and height")
            root.setAttribute("viewBox", f"0 0 {dimensions['width']} {dimensions['height']}")

        root.setAttribute("width", "54")
        root.setAttribute("height", "54")
        root.setAttribute("preserveAspectRatio", "xMidYMid meet")
        return "".join(node.toxml() for node in document.childNodes).encode("utf-8")


def collect_icons(banks: Path) -> tuple[list[tuple[int, Path]], int]:
    icons = []
    seen_ids = {}
    skipped = 0
    for directory in sorted(banks.iterdir()):
        if not directory.is_dir():
            continue
        info_path = directory / "info.json"
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError(f"{info_path}: {error}") from error
        bank_id = info.get("id") if isinstance(info, dict) else None
        if type(bank_id) is not int:
            raise ValueError(f"{info_path}: id must be an integer")
        if bank_id in seen_ids:
            raise ValueError(f"duplicate bank id {bank_id}: {seen_ids[bank_id]} and {info_path}")
        seen_ids[bank_id] = info_path

        icon = directory / "icon.svg"
        if not icon.exists() and not icon.is_symlink():
            skipped += 1
            continue
        if not icon.is_file():
            raise ValueError(f"{icon}: expected an SVG file")
        if bank_id < 0:
            raise ValueError(f"{info_path}: negative id {bank_id} cannot name a drawable")
        icons.append((bank_id, icon))
    if not icons:
        raise ValueError("no bank icons found")
    return icons, skipped


def export_drawable(banks: Path, output: Path) -> tuple[int, int]:
    banks = banks.resolve()
    if output.exists() or output.is_symlink():
        raise ValueError(f"output already exists: {output}")
    output = output.resolve()
    if banks == output or banks in output.parents or output in banks.parents:
        raise ValueError("input and output directories must not overlap")
    if not banks.is_dir():
        raise ValueError(f"banks directory does not exist: {banks}")

    icons, skipped = collect_icons(banks)
    # Exclusive creation prevents clobbering, including an output created after
    # the existence check. Only this newly created directory is cleaned on error.
    output.mkdir(parents=True, exist_ok=False)
    try:
        drawable = output / "drawable"
        drawable.mkdir()
        for bank_id, source in icons:
            try:
                data = normalize_svg(source)
            except ValueError as error:
                raise ValueError(f"{source}: {error}") from error
            (drawable / f"ic_bank_{bank_id}_54px.svg").write_bytes(data)

        temporary_archive = output / f"{ARCHIVE_NAME}.tmp"
        with zipfile.ZipFile(temporary_archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for icon in sorted(drawable.iterdir()):
                entry = zipfile.ZipInfo(f"drawable/{icon.name}", date_time=(1980, 1, 1, 0, 0, 0))
                entry.create_system = 3
                entry.external_attr = 0o100644 << 16
                archive.writestr(entry, icon.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        temporary_archive.rename(output / ARCHIVE_NAME)
    except BaseException:
        shutil.rmtree(output)
        raise
    return len(icons), skipped


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--banks", type=Path, default=Path("banks"), help="bank metadata and canonical SVG directory")
    parser.add_argument("--output", type=Path, required=True, help="new export directory (must not exist)")
    arguments = parser.parse_args()
    try:
        exported, skipped = export_drawable(arguments.banks, arguments.output)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Export failed: {error}\n")
    print(f"Exported {exported} icons; skipped {skipped} metadata-only banks.")
    print(f"Archive: {arguments.output.resolve() / ARCHIVE_NAME}")


if __name__ == "__main__":
    main()
