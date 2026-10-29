"""Downloaded-source lineage and reproducible source/final inspection materials.

Receipts bind files and invocations; they do not certify source identity or the
truth of a model's visual judgment. No catalog/state mutation or remote publication.
"""
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import uuid
from urllib.parse import urlsplit

from evidence import artifact, instant, require_hash, sha

MAX_BYTES = 16 * 1024 * 1024
MAX_PIXELS = 16 * 1024 * 1024
SIZE = 240
ICON_SIZE = 162
BACKGROUNDS = {"light": "#FFFFFF", "dark": "#121212"}


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result
    value = json.loads(Path(path).read_bytes(), object_pairs_hook=pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite JSON value")))
    if type(value) is not dict:
        raise ValueError("expected a JSON object")
    return value


def link(session, path):
    path = Path(path)
    relative = path.relative_to(session).as_posix()
    checked = artifact(session, relative, "workflow artifact")
    return {"path": relative, "sha256": sha(checked.read_bytes())}


def linked(session, value, label):
    if type(value) is not dict or value.keys() != {"path", "sha256"}:
        raise ValueError(f"{label} requires exact path and SHA-256")
    path = artifact(session, value["path"], label)
    if sha(path.read_bytes()) != require_hash(value["sha256"], label):
        raise ValueError(f"{label} hash mismatch")
    return path


def fresh(session, id, prefix):
    # Only the coordinator's own new directory; never replace a previous attempt.
    if type(id) is not int or not re.fullmatch(r"[a-z-]+", prefix):
        raise ValueError("invalid workflow output identity")
    parent = session / "evidence" / str(id)
    for path in (session / "evidence", parent):
        if path.is_symlink():
            raise ValueError("workflow output contains a symlink")
        path.mkdir(exist_ok=True, mode=0o700)
    result = parent / (prefix + "-" + uuid.uuid4().hex)
    result.mkdir(mode=0o700)
    return result


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _https(value):
    if (type(value) is not str or "\\" in value or
            any(ord(c) <= 32 or ord(c) == 127 for c in value)):
        raise ValueError("retrieval URL must be exact HTTPS")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or
            parsed.password is not None or parsed.fragment):
        raise ValueError("retrieval URL must be HTTPS without credentials or fragment")
    _ = parsed.port
    return value


def retrieval(session, receipt_link, id, base_commit, checked, asset_url=None):
    """Validate the saved helper result and original response, not a working copy."""
    path = linked(session, receipt_link, "source retrieval receipt")
    if not receipt_link["path"].startswith(f"evidence/{id}/") or path.name != "receipt.json":
        raise ValueError("source retrieval receipt belongs to another bank/output")
    receipt = read_json(path)
    if (type(receipt.get("schemaVersion")) is not int or receipt["schemaVersion"] != 1 or
            type(receipt.get("id")) is not int or receipt["id"] != id or
            receipt.get("baseCommit") != base_commit or receipt.get("status") != "fetched"):
        raise ValueError("source retrieval was unsuccessful or belongs to another bank/base")
    requested, final = _https(receipt.get("requestedUrl")), _https(receipt.get("finalUrl"))
    if asset_url is not None and asset_url not in (requested, final):
        raise ValueError("source asset URL differs from retrieval")
    started = instant(receipt.get("startedAt"), "retrieval.startedAt")
    finished = instant(receipt.get("finishedAt"), "retrieval.finishedAt")
    if not started <= finished <= checked <= datetime.now(timezone.utc):
        raise ValueError("retrieval is outside the recorded observation chronology")
    attempts = receipt.get("attempts")
    if type(attempts) is not list or not 1 <= len(attempts) <= 5:
        raise ValueError("retrieval needs its finite actual transfer attempts")
    if type(attempts[0]) is not dict or attempts[0].get("url") != requested:
        raise ValueError("retrieval does not start at the requested URL")
    previous = started
    ca_count = 0
    for index, attempt in enumerate(attempts):
        if type(attempt) is not dict or attempt.get("directory") != f"attempt-{index + 1}":
            raise ValueError("invalid retrieval attempt directory")
        url = _https(attempt.get("url"))
        begin = instant(attempt.get("startedAt"), "transfer.startedAt")
        end = instant(attempt.get("finishedAt"), "transfer.finishedAt")
        if not previous <= begin <= end <= finished:
            raise ValueError("retrieval attempts have inconsistent times")
        previous = end
        command = attempt.get("command")
        if type(command) is not list or not command or not all(type(arg) is str for arg in command):
            raise ValueError("retrieval command must be the verified HTTPS helper argument list")
        directory = path.parent / attempt["directory"]
        expected = [command[0], "--disable", "--silent", "--show-error", "--proto", "=https",
                    "--connect-timeout", "10", "--max-time", "30", "--max-filesize", str(MAX_BYTES),
                    "--dump-header", str(directory / "headers.txt"), "--output", str(directory / "body.bin"),
                    "--write-out", "%{http_code}\n%{content_type}\n"]
        if attempt.get("trust") == "packaged-ca":
            if len(command) != len(expected) + 4 or command[-4] != "--cacert":
                raise ValueError("retrieval command has an invalid CA argument")
            expected += ["--cacert", command[-3]]
        expected += ["--url", url]
        if command != expected:
            raise ValueError("retrieval command differs from the verified HTTPS helper")
        trust = attempt.get("trust")
        if trust == "packaged-ca":
            from fetch_source import issuer_failure
            ca_count += 1
            if (index == 0 or ca_count != 1 or attempts[index - 1].get("url") != url or
                    not issuer_failure(attempts[index - 1].get("exitCode"),
                                       attempts[index - 1].get("diagnostic", "")) or
                    "--cacert" not in command or type(receipt.get("ca")) is not dict):
                raise ValueError("retrieval has an unsupported CA retry")
        elif trust != "default" or "--cacert" in command:
            raise ValueError("retrieval has an unsupported trust mode")
        elif index and (attempts[index - 1].get("exitCode") != 0 or
                        attempts[index - 1].get("httpStatus") not in (301, 302, 303, 307, 308)):
            raise ValueError("ordinary retrieval continuation requires an observed redirect")
    if receipt.get("caRetry") != ("used" if ca_count else "not-needed"):
        raise ValueError("retrieval CA summary differs from its attempts")
    last = attempts[-1]
    body = receipt.get("body")
    if (last.get("url") != final or type(last.get("exitCode")) is not int or last["exitCode"] != 0 or
            type(last.get("httpStatus")) is not int or not 200 <= last["httpStatus"] < 300 or
            type(body) is not dict or body.keys() != {"path", "sha256", "bytes"} or
            type(body["bytes"]) is not int or not 0 < body["bytes"] <= MAX_BYTES):
        raise ValueError("retrieval does not have a successful nonempty response")
    raw = linked(session, {key: body[key] for key in ("path", "sha256")}, "downloaded source")
    observed = last.get("body")
    if (type(observed) is not dict or observed.keys() != {"path", "sha256", "bytes"} or
            observed.get("path") != f"{last['directory']}/body.bin" or
            raw != path.parent / observed["path"] or
            body["sha256"] != observed["sha256"] or body["bytes"] != observed["bytes"] or
            raw.stat().st_size != body["bytes"]):
        raise ValueError("source must be the original downloaded bytes, not a working SVG")
    return receipt, raw


def validate_source(session, candidate, data, bank, checked, *, _material=True):
    receipt, raw = retrieval(session, candidate["retrieval"], bank["id"], data["baseCommit"],
                             checked, candidate["sourceAssetUrl"])
    if candidate["path"] != receipt["body"]["path"] or candidate["iconHash"] != receipt["body"]["sha256"]:
        raise ValueError("candidate source is not the original retrieval body")
    if _material and "sourceMaterial" in candidate:
        from source_material import validate
        validate(session, candidate["sourceMaterial"], candidate["retrieval"],
                 bank["id"], data["baseCommit"], checked)
    return receipt, raw


def reference_source(session, raw_candidate, data, bank, checked, material=None):
    """Resolve the immutable visual source without replacing the downloaded resource."""
    receipt, downloaded = validate_source(session, raw_candidate, data, bank, checked, _material=False)
    pinned = raw_candidate.get("sourceMaterial")
    if material is not None and pinned is not None and material != pinned:
        raise ValueError("source material differs from the one selected in the original attempt")
    selected = pinned if material is None else material
    if selected is None:
        return receipt, downloaded, None
    from source_material import validate
    _, source = validate(session, selected, raw_candidate["retrieval"], bank["id"], data["baseCommit"], checked)
    return receipt, source, selected


def _image(path):
    from PIL import Image
    with Image.open(path) as image:
        if image.width * image.height > MAX_PIXELS:
            raise ValueError("source image exceeds the pixel limit")
        image.load()
        return image.convert("RGBA"), image.format


def _svg_safe(content):
    import xml.etree.ElementTree as ET
    text = content.decode("utf-8")
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("source SVG declarations/entities are not allowed")
    root = ET.fromstring(text)
    if root.tag != "{http://www.w3.org/2000/svg}svg":
        raise ValueError("source is not an SVG")
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1].lower()
        if tag in {"script", "foreignobject", "image", "animate", "set", "animatetransform"}:
            raise ValueError("source SVG contains unsupported active or raster content")
        for key, value in element.attrib.items():
            name = key.rsplit("}", 1)[-1].lower()
            if name.startswith("on") or (name in {"href", "src", "base"} and not value.startswith("#")):
                raise ValueError("source SVG contains active/external references")
        css = " ".join(element.attrib.values()) + " " + (element.text or "")
        if "@import" in css.lower() or "\\" in css:
            raise ValueError("source SVG contains unsupported CSS escapes/imports")
        for target in re.findall(r"url\s*\((.*?)\)", css, re.I | re.S):
            if not target.strip(" \t\r\n\"'").startswith("#"):
                raise ValueError("source SVG references an external resource")


def require_vector_source(content):
    """Check source bytes, not filenames; provenance still needs agent judgment."""
    import xml.etree.ElementTree as ET
    try:
        _svg_safe(content)
    except (ValueError, UnicodeError, ET.ParseError) as error:
        raise ValueError("replacement requires an original vector SVG source; raster is research evidence only") from error


def _run(command, log, timeout=35):
    with Path(log).open("xb") as stream:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stream,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise
    if code:
        raise ValueError(f"source renderer failed (exit {code}); inspect its saved log")


def _save_png(path, image):
    with path.open("xb") as stream:
        image.save(stream, format="PNG")


def _crop(image, crop):
    if crop is None:
        return image
    if (type(crop) is not dict or crop.keys() != {"box", "basis"} or
            type(crop["basis"]) is not str or not crop["basis"].strip() or
            type(crop["box"]) is not list or len(crop["box"]) != 4 or
            any(type(v) is not int for v in crop["box"])):
        raise ValueError("source crop needs a pixel box and source-supported basis")
    x0, y0, x1, y1 = crop["box"]
    if not 0 <= x0 < x1 <= image.width or not 0 <= y0 < y1 <= image.height:
        raise ValueError("source crop is outside the preserved full raster")
    return image.crop(tuple(crop["box"]))


def _canvas(image, theme):
    from PIL import Image, ImageOps
    canvas = Image.new("RGBA", (SIZE, SIZE), BACKGROUNDS[theme])
    icon = ImageOps.contain(image, (ICON_SIZE, ICON_SIZE), Image.Resampling.LANCZOS)
    canvas.alpha_composite(icon, ((SIZE - icon.width) // 2, (SIZE - icon.height) // 2))
    return canvas


def prepare_reference(session, raw_candidate, data, bank, *, crop=None, rasterizer=None, material=None):
    receipt, source, material = reference_source(
        session, raw_candidate, data, bank, datetime.now(timezone.utc), material)
    expected_hash = sha(source.read_bytes())
    root = fresh(session, bank["id"], "source-reference")
    content = source.read_bytes()
    try:
        image, format = _image(io.BytesIO(content))
    except (OSError, ValueError):
        _svg_safe(content)
        tool = shutil.which(rasterizer or "rsvg-convert")
        if tool is None:
            raise ValueError("rsvg-convert is unavailable; source comparison is incomplete")
        command = [tool, "--keep-aspect-ratio", "--width", "960", "--height", "960",
                   "--output", str(root / "raster.png"), str(source)]
        _run(command, root / "process.log")
        image, format = _image(root / "raster.png")
        renderer = "librsvg source reference"
        version = subprocess.check_output([tool, "--version"], stdin=subprocess.DEVNULL, timeout=10).decode().strip()
        if not version or len(version) > 512:
            raise ValueError("source renderer version unavailable")
        invocation = {"command": command, "version": version, "log": link(session, root / "process.log")}
        format = "SVG"
    else:
        if format not in {"PNG", "JPEG", "WEBP", "ICO"}:
            raise ValueError("unsupported source image format")
        _save_png(root / "raster.png", image)
        from PIL import __version__
        renderer = "Pillow source decode"
        invocation = {"pillow": __version__, "format": format}
    if sha(source.read_bytes()) != expected_hash:
        raise ValueError("source changed during reference generation")
    selected = _crop(image, crop)
    images = {}
    for theme in BACKGROUNDS:
        _save_png(root / f"source-{theme}.png", _canvas(selected, theme))
        images[theme] = link(session, root / f"source-{theme}.png")
    result = {"schemaVersion": 1, "id": bank["id"], "baseCommit": data["baseCommit"],
              "source": link(session, source), "retrieval": raw_candidate["retrieval"],
              "retrievedAt": receipt["finishedAt"], "createdAt": now(), "renderer": renderer,
              "sourceFormat": format, "raster": link(session, root / "raster.png"),
              "crop": crop, "invocation": invocation, "images": images}
    if material is not None:
        result["sourceMaterial"] = material
    write_json(root / "reference.json", result)
    return link(session, root / "reference.json")


def validate_reference(session, reference_link, raw_candidate, data, bank, checked):
    path = linked(session, reference_link, "source reference")
    value = read_json(path)
    created = instant(value.get("createdAt"), "source reference createdAt")
    if created > checked:
        raise ValueError("source reference postdates the candidate check")
    if "sourceMaterial" in raw_candidate and "sourceMaterial" not in value:
        raise ValueError("source reference dropped the selected material binding")
    receipt, source, _ = reference_source(
        session, raw_candidate, data, bank, created, value.get("sourceMaterial"))
    if (value.get("schemaVersion") != 1 or type(value.get("schemaVersion")) is not int or
            value.get("id") != bank["id"] or type(value.get("id")) is not int or
            value.get("baseCommit") != data["baseCommit"] or
            value.get("source") != link(session, source) or
            value.get("retrieval") != raw_candidate["retrieval"] or
            value.get("retrievedAt") != receipt["finishedAt"] or
            not instant(receipt["finishedAt"], "retrievedAt") <=
                instant(value.get("createdAt"), "source reference createdAt") <= checked):
        raise ValueError("source reference is not bound to original retrieval and chronology")
    raster = linked(session, value.get("raster"), "source raster")
    image, format = _image(raster)
    if format != "PNG":
        raise ValueError("source reference raster must be PNG")
    invocation = value.get("invocation")
    if type(invocation) is not dict:
        raise ValueError("source reference needs its actual decoder invocation")
    if value.get("renderer") == "Pillow source decode":
        original, format = _image(source)
        if (value.get("sourceFormat") != format or invocation.get("format") != format or
                format not in {"PNG", "JPEG", "WEBP", "ICO"} or
                original.size != image.size or original.tobytes() != image.tobytes()):
            raise ValueError("source reference pixels differ from the downloaded image")
    elif value.get("renderer") == "librsvg source reference":
        _svg_safe(source.read_bytes())
        command = invocation.get("command")
        if (value.get("sourceFormat") != "SVG" or type(command) is not list or
                not all(type(arg) is str for arg in command) or
                type(invocation.get("version")) is not str or not invocation["version"].strip() or
                len(command) != 9 or not Path(command[0]).is_absolute() or
                command[1:] != ["--keep-aspect-ratio", "--width", "960", "--height", "960",
                                "--output", str(raster), str(source)]):
            raise ValueError("source raster invocation does not use the original downloaded SVG")
        linked(session, invocation.get("log"), "source renderer log")
    else:
        raise ValueError("unsupported source reference renderer")
    selected = _crop(image, value.get("crop"))
    if type(value.get("images")) is not dict or value["images"].keys() != BACKGROUNDS.keys():
        raise ValueError("source reference needs both themes")
    for theme in BACKGROUNDS:
        actual, format = _image(linked(session, value["images"][theme], "source theme"))
        expected = _canvas(selected, theme)
        if format != "PNG" or actual.size != expected.size or actual.tobytes() != expected.tobytes():
            raise ValueError("source reference theme pixels differ from the bound source raster")
    return value


def prepare_comparison(session, raw_candidate, data, bank, final, bundle_link, reference_link):
    from PIL import Image
    checked = datetime.now(timezone.utc)
    reference = validate_reference(session, reference_link, raw_candidate, data, bank, checked)
    bundle = read_json(linked(session, bundle_link, "Desktop bundle"))
    desktop = bundle["desktop"]
    from evidence import desktop_observation
    desktop_observation(session, {**desktop, "visualComparison": "Assembly only; no visual judgment"},
                        data, bank, checked, source_path=str(final), source_hash=sha(final.read_bytes()))
    root = fresh(session, bank["id"], "source-comparison")
    pairs = {}
    for theme in BACKGROUNDS:
        left, _ = _image(linked(session, reference["images"][theme], "source theme"))
        right, _ = _image(linked(session, desktop[theme], "candidate theme"))
        if right.size != (SIZE, SIZE):
            raise ValueError("candidate theme has unsupported dimensions")
        pair = Image.new("RGBA", (SIZE * 2, SIZE))
        pair.paste(left, (0, 0))
        pair.paste(right, (SIZE, 0))
        _save_png(root / f"source-final-{theme}.png", pair)
        pairs[theme] = link(session, root / f"source-final-{theme}.png")
    result = {"schemaVersion": 1, "id": bank["id"], "baseCommit": data["baseCommit"],
              "sourceReference": reference_link, "final": link(session, final),
              "desktopBundle": bundle_link, "pairs": pairs, "createdAt": now()}
    write_json(root / "comparison.json", result)
    return link(session, root / "comparison.json")


def validate_comparison(session, comparison_link, raw_candidate, payload, data, bank, checked):
    path = linked(session, comparison_link, "source/final comparison")
    value = read_json(path)
    if (type(value.get("schemaVersion")) is not int or value["schemaVersion"] != 1 or
            type(value.get("id")) is not int or value["id"] != bank["id"] or
            value.get("baseCommit") != data["baseCommit"] or
            value.get("final") != {key: payload["candidate"][key] for key in ("path", "sha256")} or
            instant(value.get("createdAt"), "source comparison createdAt") > checked):
        raise ValueError("source/final comparison belongs to different bytes, bank or time")
    linked(session, value["final"], "source comparison final SVG")
    reference = validate_reference(session, value.get("sourceReference"), raw_candidate, data, bank, checked)
    bundle = read_json(linked(session, value.get("desktopBundle"), "source comparison Desktop bundle"))
    desktop = {k: v for k, v in payload["desktop"].items() if k != "visualComparison"}
    if bundle.get("desktop") != desktop:
        raise ValueError("source comparison differs from exact Desktop observation")
    created = instant(value["createdAt"], "source comparison createdAt")
    if (created < instant(reference["createdAt"], "source reference createdAt") or
            created < instant(desktop.get("observedAt"), "desktop observedAt")):
        raise ValueError("source comparison predates its inputs")
    if type(value.get("pairs")) is not dict or value["pairs"].keys() != BACKGROUNDS.keys():
        raise ValueError("source/final comparison needs both themes")
    for theme in BACKGROUNDS:
        if payload["comparison"][theme]["source"] != reference["images"][theme]:
            raise ValueError("candidate source image was replaced by a derived/final render")
        pair, format = _image(linked(session, value["pairs"][theme], "source/final pair"))
        if format != "PNG" or pair.size != (SIZE * 2, SIZE):
            raise ValueError("source/final pair dimensions or format changed")
        for offset, image_link in ((0, reference["images"][theme]), (SIZE, desktop[theme])):
            image, _ = _image(linked(session, image_link, "source/final input"))
            if pair.crop((offset, 0, offset + SIZE, SIZE)).tobytes() != image.tobytes():
                raise ValueError("source/final pair pixels differ from the bound inputs")
    return value


def evidence_links(session, payload):
    """Freeze source lineage and comparison artifacts alongside existing state bindings.

    Called after canonical package validation; this enumerates exact known links,
    not filenames mentioned in narratives or archive metadata.
    """
    from feedback_evidence import links as feedback_links
    result = feedback_links(session, payload)
    candidate = payload.get("candidate") or {}
    if "sourceMaterial" in candidate:
        from source_material import links
        result.extend(links(session, candidate["sourceMaterial"]))
    if "sourceComparison" in payload:
        comparison_link = payload["sourceComparison"]
        value = read_json(linked(session, comparison_link, "source comparison"))
        reference_link = value["sourceReference"]
        reference = read_json(linked(session, reference_link, "source reference"))
        if "sourceMaterial" not in reference:
            return result
        result.extend([comparison_link, reference_link, value["desktopBundle"], value["final"],
                       reference["retrieval"], reference["source"], reference["raster"],
                       *value["pairs"].values(), *reference["images"].values()])
        if "log" in reference["invocation"]:
            result.append(reference["invocation"]["log"])
        if "sourceMaterial" in reference:
            from source_material import links
            result.extend(links(session, reference["sourceMaterial"]))
    return result
