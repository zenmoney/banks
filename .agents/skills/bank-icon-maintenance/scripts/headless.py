"""One-shot catalog export and immutable session evidence; never a visual judgment."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess

from evidence import artifact, instant, require_hash, required, sha

RENDERER = "offscreen CPU Skia/painterResource"
PINS = {"converter": "com.android.tools:sdk-common:32.4.0", "compose": "1.12.0"}
BACKGROUNDS = {"light": "#FFFFFF", "dark": "#121212"}


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json(path):
    value = json.loads(path.read_bytes())
    if type(value) is not dict:
        raise ValueError("headless evidence must be a JSON object")
    return value


def _write(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _link(session, path):
    return {"path": str(path.relative_to(session)), "sha256": sha(path.read_bytes())}


def _linked(session, link, label):
    if type(link) is not dict or link.keys() != {"path", "sha256"}:
        raise ValueError(f"{label} needs path and SHA-256")
    path = artifact(session, link["path"], label)
    if sha(path.read_bytes()) != require_hash(link["sha256"], label):
        raise ValueError(f"{label} hash mismatch")
    return path


def _png(path, size):
    from PIL import Image
    with Image.open(path) as image:
        if image.format != "PNG" or image.size != size:
            raise ValueError("offscreen PNG dimensions or format changed")
        image.load()
        return image.convert("RGBA")


def validate_export(session, desktop, data, bank, source_path, source_hash, xml_hash, pins):
    """Validate saved export, not the shared mutable Gradle build directory."""
    receipt_path = _linked(session, desktop.get("invocation"), "export invocation")
    receipt = _json(receipt_path)
    if (type(receipt.get("schemaVersion")) is not int or receipt["schemaVersion"] != 1 or
            receipt.get("session") != str(session) or receipt.get("baseCommit") != data["baseCommit"] or
            receipt.get("worktree") != data["worktree"] or receipt.get("id") != bank["id"] or
            receipt.get("baseIconHash") != bank["iconHash"] or
            receipt.get("metadataHash") != sha((Path(data["worktree"]) / bank["folder"] / "info.json").read_bytes()) or
            type(receipt.get("exitCode")) is not int or receipt["exitCode"] != 0 or
            receipt.get("environment") != {"JAVA_TOOL_OPTIONS": "-Djava.awt.headless=true",
                                           "unset": ["DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY"]}):
        raise ValueError("export invocation differs from session/base or was unsuccessful")
    expected_source = str(Path(data["worktree"]) / source_path)
    if receipt.get("source") != {"path": expected_source, "sha256": source_hash}:
        raise ValueError("export invocation source differs from exact SVG")
    source = Path(expected_source)
    if source.is_symlink() or sha(source.read_bytes()) != source_hash:
        raise ValueError("export SVG changed after invocation")
    catalog = Path(required(receipt, "cwd"))
    generated_manifest = catalog / "catalog/build/generated/catalog/manifest.json"
    command = _command(catalog, data["worktree"], bank["id"], receipt_path.parent / "render",
                       source_path if Path(source_path).is_absolute() else None)
    if (not catalog.is_absolute() or receipt.get("command") != command or
            receipt.get("generatedManifest") != str(generated_manifest)):
        raise ValueError("export invocation command differs from exact catalog inputs/output")
    _linked(session, receipt.get("log"), "export process log")
    result_path = _linked(session, desktop.get("result"), "export result")
    if receipt.get("result") != desktop["result"] or receipt.get("manifest") != {
            "path": desktop["manifest"], "sha256": desktop["manifestSha256"]}:
        raise ValueError("export receipt does not bind result and manifest")
    _linked(session, receipt["manifest"], "export manifest")
    result = _json(result_path)
    if (type(result.get("schemaVersion")) is not int or result["schemaVersion"] != 1 or
            type(result.get("id")) is not int or result["id"] != bank["id"] or
            result.get("status") != "success" or result.get("renderer") != RENDERER or
            result.get("source") != receipt["source"] or result.get("pins") != pins or pins != PINS or
            result.get("xml") != {"resourceName": f"bank_{bank['id']}" if bank['id'] >= 0 else f"bank_m{-bank['id']}",
                                   "sha256": xml_hash, "loadedSha256": xml_hash} or
            result.get("manifest") != {"path": receipt.get("generatedManifest"),
                                       "sha256": desktop["manifestSha256"]} or
            any(type(result.get(key)) is not int or result[key] != expected
                for key, expected in (("density", 3), ("sizePx", 240), ("iconSizeDp", 54)))):
        raise ValueError("unsupported or mismatched offscreen export result")
    runtime = result.get("runtime")
    if type(runtime) is not dict or runtime.get("compose") != pins["compose"]:
        raise ValueError("offscreen runtime differs from Compose pin")
    for key in ("skiko", "skia", "javaVersion", "osName", "osArch"):
        required(runtime, key)
    observed = instant(result.get("observedAt"), "export observedAt")
    if (desktop.get("observedAt") != result["observedAt"] or
            not instant(receipt.get("startedAt"), "export startedAt") <= observed <=
                instant(receipt.get("finishedAt"), "export finishedAt") <= datetime.now(timezone.utc)):
        raise ValueError("offscreen observation is outside actual invocation")
    if result_path != receipt_path.parent / "render" / "result.json":
        raise ValueError("export result belongs to another invocation directory")
    images = result.get("images")
    if type(images) is not list or len(images) != 2 or any(type(row) is not dict for row in images):
        raise ValueError("offscreen export requires both images")
    for theme, background in BACKGROUNDS.items():
        rows = [row for row in images if row.get("theme") == theme]
        if len(rows) != 1 or rows[0].get("path") != f"{theme}.png" or rows[0].get("background") != background:
            raise ValueError("offscreen export theme/background mismatch")
        path = result_path.parent / f"{theme}.png"
        if desktop.get(theme) != {"path": str(path.relative_to(session)), "sha256": rows[0].get("sha256")}:
            raise ValueError("offscreen image differs from export result")
        _png(_linked(session, desktop[theme], f"offscreen {theme}"), (240, 240))
    return receipt


def _fresh(session, relative):
    if (not isinstance(relative, str) or not relative.startswith("evidence/") or
            any(part in ("", ".", "..") for part in relative.split("/"))):
        raise ValueError("export output must be a fresh relative directory under session evidence")
    path = session
    for part in relative.split("/"):
        path /= part
        if path.is_symlink():
            raise ValueError("export output cannot contain a symlink")
    if path.exists():
        raise ValueError("export output already exists; no files overwritten")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()
    return path

def _command(catalog, worktree, id, output, candidate):
    manifest = catalog / "catalog/build/generated/catalog/manifest.json"
    args = [str(catalog / "gradlew"), "--offline", "--no-daemon", "--console=plain", ":catalog:run",
            f"-PcatalogBanks={worktree}/banks", f"-PcatalogId={id}"]
    if candidate:
        args.append(f"-PcatalogCandidate={candidate}")
    args.append("--args=" + shlex.join(["export", "--id", str(id), "--manifest", str(manifest),
                                       "--output", str(output)]))
    return args


def _run(session, data, bank, catalog, root, candidate=None):
    manifest = catalog / "catalog/build/generated/catalog/manifest.json"
    output = root / "render"
    source = candidate or Path(data["worktree"]) / bank["folder"] / "icon.svg"
    source_hash = sha(source.read_bytes())
    args = _command(catalog, data["worktree"], bank["id"], output, candidate)
    environment = {"JAVA_TOOL_OPTIONS": "-Djava.awt.headless=true",
                   "unset": ["DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY"]}
    env = dict(os.environ)
    for key in environment["unset"]:
        env.pop(key, None)
    env["JAVA_TOOL_OPTIONS"] = environment["JAVA_TOOL_OPTIONS"]
    started = _now()
    with (root / "process.log").open("xb") as log:
        process = subprocess.run(args, cwd=catalog, env=env, stdout=log, stderr=subprocess.STDOUT)
    receipt = {"schemaVersion": 1, "session": str(session), "id": bank["id"],
        "baseCommit": data["baseCommit"], "worktree": data["worktree"],
        "baseIconHash": bank["iconHash"], "metadataHash": bank["metadataHash"],
        "source": {"path": str(source), "sha256": source_hash}, "command": args,
        "cwd": str(catalog), "environment": environment, "startedAt": started,
        "finishedAt": _now(), "exitCode": process.returncode,
        "log": _link(session, root / "process.log"), "generatedManifest": str(manifest)}
    if process.returncode:
        _write(root / "invocation.json", receipt)
        raise ValueError(f"catalog export failed (exit {process.returncode}); see {root / 'process.log'}")
    try:
        generated = root / "generated"
        generated.mkdir()
        shutil.copyfile(manifest, generated / "manifest.json")
        result = _json(output / "result.json")
        resource = f"bank_{bank['id']}" if bank['id'] >= 0 else f"bank_m{-bank['id']}"
        xml = generated / "composeResources/drawable" / f"{resource}.xml"
        xml.parent.mkdir(parents=True)
        shutil.copyfile(manifest.parent / "composeResources/drawable" / xml.name, xml)
        receipt.update(result=_link(session, output / "result.json"), manifest=_link(session, generated / "manifest.json"))
    except (OSError, ValueError):
        _write(root / "invocation.json", receipt)
        raise
    _write(root / "invocation.json", receipt)
    desktop = {"renderer": RENDERER, "observedAt": result.get("observedAt"),
        "manifest": receipt["manifest"]["path"], "manifestSha256": receipt["manifest"]["sha256"],
        "invocation": _link(session, root / "invocation.json"), "result": receipt["result"],
        **{theme: _link(session, output / f"{theme}.png") for theme in BACKGROUNDS}}
    from evidence import desktop_observation
    desktop_observation(session, {**desktop, "visualComparison": "Export validation only; judgment not supplied"},
        data, bank, datetime.now(timezone.utc), source_path=str(candidate) if candidate else None,
        source_hash=source_hash)
    _write(root / "desktop.json", desktop)
    return desktop

def validate_comparison(session, desktop, comparison, data, bank, checked):
    from evidence import desktop_observation
    if bank["iconHash"] is None:
        if "baseObservation" not in desktop or desktop["baseObservation"] is not None:
            raise ValueError("first addition requires null base export observation")
        for theme in BACKGROUNDS:
            row = comparison[theme]
            if row["base"] is not None or row["beforeAfter"] is not None or row["candidate"] != desktop[theme]:
                raise ValueError("first addition comparison requires exact final images without baseline evidence")
            _png(_linked(session, row["candidate"], "final comparison image"), (240, 240))
        return
    base = _json(_linked(session, desktop.get("baseObservation"), "base export observation"))
    if base.get("renderer") != RENDERER or "baseObservation" in base:
        raise ValueError("candidate comparison requires an exact offscreen base export")
    desktop_observation(session, {**base, "visualComparison": desktop["visualComparison"]}, data, bank, checked)
    for theme in BACKGROUNDS:
        row = comparison[theme]
        if row["base"] != base[theme] or row["candidate"] != desktop[theme]:
            raise ValueError("comparison differs from exact base/final export images")
        pair = _png(_linked(session, row["beforeAfter"], "before/after"), (480, 240))
        for offset, image in ((0, base[theme]), (240, desktop[theme])):
            expected = _png(_linked(session, image, "comparison input"), (240, 240))
            if pair.crop((offset, 0, offset + 240, 240)).tobytes() != expected.tobytes():
                raise ValueError("before/after pixels differ from exact base/final images")


def add_subcommands(commands):
    parser = commands.add_parser("desktop-export",
        help="Export exact session SVG through offscreen Compose Desktop CPU/Skia",
        description="Run real catalog export in fresh JVMs without a window. No visual judgment, verification, approval or publication is inferred.")
    parser.add_argument("--session", required=True)
    parser.add_argument("--id", required=True, type=int)
    parser.add_argument("--catalog",
                        help="Existing icon-catalog project; default: SESSION's source repo/icon-catalog")
    parser.add_argument("--output", required=True, help="Fresh relative SESSION/evidence directory; never overwritten")
    parser.add_argument("--candidate", help="Exact final SVG under session evidence; export candidate and, when present, base plus before/after")

def dispatch(args):
    from state_results import _session_data, _catalog_pin
    from session import catalog as read_catalog
    session = Path(args.session).resolve()
    data = _session_data(session, [args.id], {})
    bank = next(bank for bank in data["banks"] if bank["id"] == args.id)
    entries, errors = read_catalog(Path(data["worktree"]))
    _catalog_pin(data, bank, entries, errors, {})
    candidate = None
    if args.candidate:
        candidate = Path(args.candidate).absolute()
        artifact(session, str(candidate.relative_to(session)), "final candidate")
        if candidate.suffix.lower() != ".svg" or sha(candidate.read_bytes()) == bank["iconHash"]:
            raise ValueError("candidate must be a different final SVG under session evidence")
    if candidate is None and bank["iconHash"] is None:
        raise ValueError("No current icon.svg exists at the pinned base; provide a candidate for first-icon export")
    from environment import catalog_path, require_export_inputs
    catalog = catalog_path(data["repo"], args.catalog)
    require_export_inputs(catalog)
    root = _fresh(session, args.output)
    if candidate is None:
        desktop = _run(session, data, bank, catalog, root)
        result = {"desktop": desktop}
    else:
        from PIL import Image
        base_root, final_root = root / "base", root / "candidate"
        final_root.mkdir()
        base = None
        if bank["iconHash"] is not None:
            base_root.mkdir()
            base = _run(session, data, bank, catalog, base_root)
        desktop = _run(session, data, bank, catalog, final_root, candidate)
        desktop["baseObservation"] = _link(session, base_root / "desktop.json") if base is not None else None
        comparisons = {}
        for theme in BACKGROUNDS:
            if base is None:
                comparisons[theme] = {"base": None, "candidate": desktop[theme], "beforeAfter": None}
                continue
            pair = Image.new("RGBA", (480, 240))
            pair.paste(_png(session / base[theme]["path"], (240, 240)), (0, 0))
            pair.paste(_png(session / desktop[theme]["path"], (240, 240)), (240, 0))
            image = root / f"before-after-{theme}.png"
            with image.open("xb") as stream:
                pair.save(stream, format="PNG")
            comparisons[theme] = {"base": base[theme], "candidate": desktop[theme],
                                  "beforeAfter": _link(session, image)}
        _write(root / "desktop.json", desktop)
        result = {"desktop": desktop, "comparison": comparisons}
    result.update(desktopPath=str((root / "desktop.json").relative_to(session)),
        nextAction="Inspect both PNGs; supply source assessment and visual judgment separately. No verification or approval recorded.")
    _write(root / "bundle.json", result)
    return result
