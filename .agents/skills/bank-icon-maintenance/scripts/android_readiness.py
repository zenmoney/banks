"""Read-only, installed-only checks for the selected Android path.

Tool availability is not build-cache readiness, device-use permission or render
success. No SDK installation, license acceptance, AVD creation or device selection.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def _component(value, name):
    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9_.;-]+", value) or ".." in value:
        raise ValueError(f"invalid {name}")
    return value


def probe(sdk, platform, build_tools, *, serial=None, system_image=None, android_cli="android"):
    if bool(serial) == bool(system_image):
        raise ValueError("select one explicit existing-device or new-AVD path")
    sdk = Path(sdk).resolve(strict=True)
    platform = _component(platform, "Android platform")
    build_tools = _component(build_tools, "Android build-tools")
    files = {"adb": sdk / "platform-tools/adb", "platform": sdk / "platforms" / platform / "android.jar",
             "aapt2": sdk / "build-tools" / build_tools / "aapt2",
             "d8": sdk / "build-tools" / build_tools / "d8",
             "zipalign": sdk / "build-tools" / build_tools / "zipalign"}
    if system_image:
        parts = _component(system_image, "system image").split(";")
        if len(parts) != 4 or parts[0] != "system-images":
            raise ValueError("system image must be its exact installed SDK package identifier")
        cli = shutil.which(android_cli)
        files.update(emulator=sdk / "emulator/emulator", androidCli=Path(cli) if cli else None,
                     systemImage=sdk.joinpath(*parts) / "package.xml")
    missing = [name for name, path in files.items() if path is None or not path.is_file() or
               (name not in {"platform", "systemImage"} and not os.access(path, os.X_OK))]
    result = {"status": "unavailable" if missing else "tools-ready", "path": "existing-device" if serial else "new-avd",
              "missing": missing, "tools": {name: str(path) if path is not None else None for name, path in files.items()},
              "buildCache": "not-verified", "render": "not-tested",
              "nextAction": "Use the chosen permitted path and an offline build with SDK auto-download disabled; no render pass inferred."}
    if missing:
        result["nextAction"] = "Skip both Android targets as not-tested; preserve earlier failures/rechecks. No provisioning or installation question."
    if serial and not missing:
        if type(serial) is not str or not re.fullmatch(r"[A-Za-z0-9_.:-]+", serial) or serial.startswith("-"):
            raise ValueError("invalid explicit device serial")
        try:
            process = subprocess.run([str(files["adb"]), "-s", serial, "get-state"],
                                     capture_output=True, text=True, timeout=10)
            result["serial"] = serial
            if process.returncode or process.stdout.strip() != "device":
                result.update(status="unavailable", missing=["selected-device-ready"],
                              nextAction="Selected device is not ready; do not substitute another phone/emulator or claim a pass.")
        except (OSError, subprocess.TimeoutExpired):
            result.update(status="unavailable", missing=["selected-device-query"],
                          nextAction="Device readiness could not be observed; do not infer absence or select another device.")
    return result


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument("--sdk-root", required=True)
    parser.add_argument("--platform", required=True, help="Exact required installed platform, e.g. android-35")
    parser.add_argument("--build-tools", required=True, help="Exact required build-tools version")
    parser.add_argument("--android-cli", default="android", help="Installed Google Android CLI name or explicit executable path; required only for new AVDs")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--serial", help="Explicitly selected permitted existing device/emulator; never auto-selected")
    group.add_argument("--system-image", help="Exact installed package for the new-AVD path")
    try:
        args = parser.parse_args(argv)
        result = probe(args.sdk_root, args.platform, args.build_tools,
                       serial=args.serial, system_image=args.system_image, android_cli=args.android_cli)
    except (OSError, ValueError) as exc:
        result = {"status": "blocked", "reason": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["status"] == "blocked" else 0


if __name__ == "__main__":
    sys.exit(main())
