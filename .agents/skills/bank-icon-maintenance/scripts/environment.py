"""Small, read-only preflight and CLI boundary helpers; no package installation."""
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


PLATFORM_MESSAGE = (
    "This workflow requires POSIX file locking and filesystem semantics. "
    "Native Windows is not supported; run the agent, Python, Git and JDK inside WSL "
    "with the repository and sessions on the Linux filesystem."
)


def require_posix():
    if os.name != "posix":
        raise ValueError(PLATFORM_MESSAGE)
    try:
        importlib.import_module("fcntl")
    except ImportError as exc:
        raise ValueError(PLATFORM_MESSAGE) from exc
    if not all(hasattr(os, flag) for flag in ("O_NOFOLLOW", "O_DIRECTORY")):
        raise ValueError(PLATFORM_MESSAGE)


def canonicalize_session_arguments(args):
    """Allow a symlinked external session root, not symlinked evidence within it."""
    # Discovery and durable-operation commands have no working session. Their
    # own handlers validate repo/operation paths; do not invent a session here.
    if getattr(args, "session", None) is None:
        return
    original = Path(args.session).absolute()
    canonical = original.resolve()
    args.session = str(canonical)
    if original == canonical:
        return
    for name in ("evidence", "review", "candidate", "outcome", "approval",
                 "authorization", "resolution"):
        value = getattr(args, name, None)
        if value is None:
            continue
        # Preserve lexical dot segments for the existing stage-specific guards.
        # pathlib would otherwise hide './' before review input validation.
        if any(part in (".", "..") for part in str(value).split(os.sep)):
            continue
        path = Path(value).absolute()
        try:
            relative = path.relative_to(original)
        except ValueError:
            continue
        # Do NOT resolve the suffix: artifact validators must still see symlinks.
        setattr(args, name, str(canonical / relative))


def catalog_path(repo, override=None):
    return (Path(override) if override is not None else Path(repo) / "icon-catalog").resolve()


def require_export_inputs(catalog):
    """Check cheap prerequisites before creating an immutable export directory."""
    try:
        importlib.import_module("PIL.Image")
    except (ImportError, OSError) as exc:
        raise ValueError("Pillow is required for Desktop export; install requirements.txt in this Python environment") from exc
    wrapper = catalog / "gradlew"
    if not catalog.is_dir() or not wrapper.is_file():
        raise ValueError(f"catalog Gradle wrapper is missing: {wrapper}; check the source checkout's icon-catalog directory, or use --catalog only if you know the location of a separate catalog checkout")
    if not os.access(wrapper, os.X_OK):
        raise ValueError(f"catalog Gradle wrapper is not executable: {wrapper}; check its executable bit")


def _probe(command):
    """Bound diagnostic subprocesses; never execute repository build scripts here."""
    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise ValueError(f"{Path(command[0]).name} diagnostic failed (exit {result.returncode})")
    return (result.stdout + result.stderr).strip()


def add_subcommands(commands):
    parser = commands.add_parser("doctor", help="Check prerequisites without changing sessions or publishing",
        description="Read-only environment checks. Does not run Gradle, install packages, fetch or publish. --github checks login through gh auth status.")
    location = parser.add_mutually_exclusive_group()
    location.add_argument("--repo", help="Explicit source checkout root; otherwise discover the Git root from the command working directory")
    location.add_argument("--session", help="Existing session; read its saved source repo and base commit")
    parser.add_argument("--base", default="HEAD", help="Local base to inspect; ignored with --session")
    parser.add_argument("--render", action="store_true", help="Also check Pillow, available JDK and catalog wrapper")
    parser.add_argument("--catalog", help="Override source repo/icon-catalog for --render")
    parser.add_argument("--github", action="store_true", help="Also check GitHub CLI and read-only github.com authentication")


def doctor(args):
    from session import SessionError, git, policy_at
    checks = []

    def check(name, operation):
        try:
            detail = operation()
        except (SessionError, ValueError, OSError, ImportError, subprocess.SubprocessError) as exc:
            checks.append({"name": name, "status": "error", "detail": str(exc)})
            return None
        checks.append({"name": name, "status": "ok", "detail": detail})
        return detail

    def python_version():
        if sys.version_info < (3, 10):
            raise ValueError("Python 3.10 or newer is required")
        return sys.version.split()[0]

    check("python", python_version)
    check("platform", lambda: require_posix() or "POSIX primitives available; not an OS certification")
    check("git", lambda: _probe(["git", "--version"]))

    def source():
        if args.session:
            session = Path(args.session).resolve()
            # Preserve the same no-symlink rule for the saved document itself.
            from state_publication import regular
            data = json.loads(regular(session / "session.json"))
            if (not isinstance(data, dict) or type(data.get("schemaVersion")) is not int or
                    data["schemaVersion"] != 2 or not isinstance(data.get("repo"), str) or
                    not Path(data["repo"]).is_absolute() or
                    not isinstance(data.get("baseCommit"), str)):
                raise ValueError("session lacks a valid saved repository/base")
            supplied, base, option = data["repo"], data["baseCommit"], "saved repo"
        elif args.repo is not None:
            if not args.repo:
                raise ValueError("--repo cannot be empty")
            # An explicit path is authoritative: never fall back after a typo.
            supplied, base, option = args.repo, args.base, "--repo"
        else:
            result = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                                    capture_output=True, text=True, timeout=15)
            if result.returncode or not result.stdout.strip():
                raise ValueError(
                    "Cannot discover a Git checkout from the command working directory. "
                    "Run doctor from the task checkout or pass its verified root with --repo. "
                    "The skill installation directory and neighboring repositories are not searched.")
            supplied, base, option = result.stdout.rstrip("\r\n"), args.base, "discovered repo"
        repo = Path(supplied).resolve(strict=True)
        root = git(repo, "rev-parse", "--show-toplevel", check=False)
        if root.returncode:
            raise ValueError(f"{option} {supplied!r} is not inside a Git checkout: {repo}")
        actual = Path(root.stdout.strip()).resolve()
        if actual != repo:
            raise ValueError(
                f"{option} {supplied!r} resolves to {repo}, but the Git checkout root is {actual}; "
                "use the checkout root as --repo")
        return repo, base

    location = check("source", source)
    repo = location[0] if location is not None else None
    if location is not None:
        # Path objects are kept internal; all CLI output remains JSON serializable.
        checks[-1]["detail"] = {"repo": str(repo), "base": location[1]}

        def base_policy():
            commit = git(repo, "rev-parse", "--verify", f"{location[1]}^{{commit}}").stdout.strip()
            policy_at(repo, commit)
            tree = git(repo, "ls-tree", commit, "--", "banks").stdout
            if not tree.startswith("040000 tree "):
                raise ValueError("selected base has no committed banks/ catalog")
            return {"baseCommit": commit, "policy": "committed schemaVersion 1", "catalog": "banks/"}

        check("base-policy", base_policy)

    from research import describe as describe_research
    def research_budget():
        if args.session:
            if location is None:
                raise ValueError("research budget requires a valid saved session/repository")
            from state_publication import regular
            saved = json.loads(regular(Path(args.session).resolve() / "session.json"))
            return describe_research(Path(args.session).resolve(), saved["baseCommit"], saved.get("selectedIds", []))
        return describe_research()
    research_policy = check("research-budget", research_budget)

    from standing_authorization import configured, describe
    publication_policy = {"status": "not-configured"}
    if configured():
        publication_policy = (check("publication-policy", lambda: describe(repo))
                              if repo is not None else None)
        if publication_policy is None:
            publication_policy = {"status": "blocked"}

    if args.render:
        check("pillow", lambda: importlib.import_module("PIL").__version__)
        if repo is not None:
            catalog = catalog_path(repo, args.catalog)
            check("catalog-wrapper", lambda: require_export_inputs(catalog) or str(catalog))

        def java():
            # Gradle's wrapper uses JAVA_HOME before PATH; check the same JDK.
            home = os.environ.get("JAVA_HOME")
            versions = {}
            for tool in ("java", "javac"):
                executable = str(Path(home) / "bin" / tool) if home else shutil.which(tool)
                if executable is None:
                    raise ValueError(f"JDK {tool} is missing; set JAVA_HOME or PATH")
                version = _probe([executable, "-version"])
                if not version:
                    raise ValueError(f"{tool} diagnostic returned no version information")
                versions[tool] = version.splitlines()[0]
            return versions

        check("jdk", java)
        checks.append({"name": "gradle-cache", "status": "warning", "detail":
            "Not verified: doctor never runs the project wrapper. Desktop export uses --offline; "
            "the wrapper distribution and project dependencies must already be cached. "
            "The catalog build owns JDK/toolchain compatibility; a real export is the render/dependency check."})
    if args.github:
        check("github-cli", lambda: _probe(["gh", "--version"]).splitlines()[0])
        def github_auth():
            _probe(["gh", "auth", "status", "--hostname", "github.com"])
            return "github.com login reported by gh; target-specific permissions are checked during preparation"
        check("github-auth", github_auth)
    return {"status": "blocked" if any(c["status"] == "error" for c in checks) else "ready",
            "paths": {"cli": str(Path(__file__).resolve().with_name("session.py")),
                      "repo": str(repo) if repo is not None else None,
                      "catalog": str(catalog_path(repo, args.catalog))
                          if repo is not None or args.catalog else None},
            "publicationPolicy": publication_policy, "researchBudget": research_policy,
            "checks": checks, "scope": {"render": args.render, "github": args.github},
            "limitations": ["No visual judgment, renderer certification or publication authorization is inferred.",
                "Network research and image-viewing capabilities must be checked in the agent itself.",
                "Existing sessions are not modified or fully revalidated; continue performs session validation."]}
