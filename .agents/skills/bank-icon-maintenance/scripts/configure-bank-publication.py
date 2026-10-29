#!/usr/bin/env python3
"""One-time OPERATOR setup. No maintenance, Git writes, PRs or merge.

Run with an explicit target; the file is reusable across clones and containers.
The maintenance agent must not run this command to authorize itself.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

from standing_authorization import compact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, help="Serviced repository, for example zenmoney/banks")
    parser.add_argument("--output", help="New operator-controlled policy outside the checkout/skill/session")
    parser.add_argument("--base", default="main")
    parser.add_argument("--head-prefix", default="bank-icon-maintenance/")
    parser.add_argument("--state-only", action="store_true")
    parser.add_argument("--allow-revisions", action="store_true")
    args = parser.parse_args()
    if args.state_only and args.allow_revisions:
        raise ValueError("state-only permission cannot authorize MR revisions")
    value = {"schemaVersion": 2, "target": args.target, "base": args.base,
             "headPrefix": args.head_prefix,
             "actions": {"assetPublish": not args.state_only,
                         "assetRevise": args.allow_revisions, "statePublish": True}}
    compact(value, Path.cwd().resolve())
    default = Path.home() / ".config/bank-icon-maintenance" / (args.target.replace("/", "-") + ".json")
    path = Path(args.output).absolute() if args.output else default.absolute()
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("policy path must not contain symlinks")
    checkout = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                              capture_output=True, text=True, timeout=15)
    if checkout.returncode == 0 and path.is_relative_to(Path(checkout.stdout.strip()).resolve()):
        raise ValueError("policy must be outside the source checkout")
    if path.is_relative_to(Path(__file__).resolve().parents[1]):
        raise ValueError("policy must be outside the skill")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    content = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)
    print("export BANK_ICON_PUBLICATION_POLICY=" + shlex.quote(str(path)))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FileExistsError as exc:
        print(f"Existing policy was NOT overwritten: {exc.filename}. Review it as the operator.", file=sys.stderr)
        raise SystemExit(1)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
