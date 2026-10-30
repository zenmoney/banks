# Bank icon maintenance — Docker

The service runs one noninteractive YAML sequence, each step in a separate agent
process, and exits. The default `docker/tasks.yaml` retains the five maintenance
tasks; steps can also contain standalone prompts. Compose's restart policy
defaults to `no`.
Run every Compose command from the checkout root.
The main file is `compose.yaml`; Compose reads the local `.env` automatically.
For initial setup, copy `.env.example` to `.env` only if `.env` does not already
exist, then fill in the required credentials and host settings. Shell exports
take precedence over `.env`. Neither Python helpers nor native build scripts
load `.env` themselves. Do not commit the populated file or share unredacted
`docker compose config` output: it contains resolved credentials.
Prepare the mounted omp configuration at
`./docker/omp-config.yml` with a broker reachable from the container and a
broker-supported model with direct image input. Set preauthorized tool permissions
for unattended execution: the launcher does not autoapprove tools, bypass prompts, or
grant publication authority. Each write requires the matching owner-provisioned
standing or exact run/package permission; the launcher never fabricates it.

The image contains tools, SDK/browser/Python runtimes, and the Docker launcher,
not bank, skill, or catalog sources. By default, `INSTALL_ANDROID_EMULATOR=no`
omits the Android emulator and system image. The Android command-line tools,
platform SDK, build-tools, and platform-tools remain installed for Android builds.
SDK licenses are still required without the emulator. Build it separately, only
after accepting the Android SDK licenses:

```sh
export BANK_UID="$(id -u)" BANK_GID="$(id -g)"
ACCEPT_ANDROID_SDK_LICENSES=yes docker compose build maintenance
```

GitHub CLI is installed from the official `cli/cli` release package, pinned to
`GH_VERSION=2.102.0` and verified with `GH_SHA256` (linux/amd64). Update both
Dockerfile arguments together when upgrading. This provides `gh api --paginate
--slurp`; no runtime download or PATH override is needed. Rebuild the image and
recreate the container to replace the old Ubuntu-provided CLI.

Then launch the existing image from the same checkout root:

```sh
# Optional alternative to ./docker/omp-config.yml:
# export OMP_CONFIG_FILE=/absolute/path/to/config.yml
docker compose up -d --no-build maintenance
docker compose logs -f maintenance
# Stop the current pass and prevent another pass from starting:
docker compose stop maintenance
```

For Android emulator runtime validation, use the optional override on a Linux host
with accessible `/dev/kvm`:

```sh
export BANK_UID="$(id -u)" BANK_GID="$(id -g)"
export KVM_GID="$(stat -c %g /dev/kvm)"
ACCEPT_ANDROID_SDK_LICENSES=yes docker compose -f compose.yaml -f compose.emulator.yaml build maintenance
docker compose -f compose.yaml -f compose.emulator.yaml up -d --no-build --force-recreate maintenance
docker compose -f compose.yaml -f compose.emulator.yaml logs -f maintenance
docker compose -f compose.yaml -f compose.emulator.yaml stop maintenance
```

The override forces `INSTALL_ANDROID_EMULATOR=yes` and adds the KVM device and
its host group to the same service; the image name and volumes are unchanged.
Use both `-f` arguments for subsequent Compose commands in emulator mode.
Switching either direction requires rebuilding the shared image and recreating
the container; changing an environment variable or restarting alone does not
change installed packages. To return to the default:

```sh
INSTALL_ANDROID_EMULATOR=no ACCEPT_ANDROID_SDK_LICENSES=yes docker compose build maintenance
docker compose up -d --no-build --force-recreate maintenance
```

Accept the SDK licenses before either build. Without an emulator, Android
compilation remains available, but actual Android runtime validation is
unavailable in the container.

Repetition is optional and controlled only by Compose's restart policy (default
`no`). To repeat successful passes as well as failures:

```sh
BANK_ICON_RESTART_POLICY=always docker compose up -d --no-build maintenance
```

Use `BANK_ICON_RESTART_POLICY=on-failure` to restart only after errors, not
successful completion. `docker compose stop maintenance` suppresses automatic
restart. Each restarted container reuses the installed client, rereads the YAML
and prompt files, and restarts the entire sequence, not just the failed step.
Gradle preparation runs once if any step selects a maintenance task. There is no
configurable delay between sequences.

A single configuration file, `${OMP_CONFIG_FILE:-./docker/omp-config.yml}`, is mounted
read-only at `/home/bank/.omp/agent/config.yml` for both the explicit omp
`--config` option and global broker configuration. The file must already exist
and be readable by the configured bank user; Compose does not create it. No host
agent directory, credentials database, or provider-token store is mounted.
Referenced configuration paths must resolve inside the container. Broker
configuration supplies agent authentication; optionally export
`OMP_AUTH_BROKER_URL` and `OMP_AUTH_BROKER_TOKEN` as runtime overrides, not
image build arguments. Broker health proves reachability, not authenticated agent
access. Direct broker clients receive access tokens; refresh tokens stay at the
broker. The separate `omp auth-gateway` avoids exposing provider access tokens to
clients.

Compose mounts the checkout, including `.git`, read-write at `/workspace`. The
agent works at `/workspace/.worktree`, independent of the host directory name.
On startup, an absent worktree is created from HEAD; an existing one is
reconnected using `git worktree repair`, without reset or deletion of changes.
Before checking the skill, each `run` copies `/workspace/.agents/.` into
`/workspace/.worktree/.agents/`, including uncommitted files. Matching files are
overwritten; destination-only files are retained. Other worktree files are unchanged.
Worktree Git metadata uses container paths: operate Git in `.worktree` from the
container, not from the host. The main checkout remains usable on the host.
Host SSH keys and credential stores are not mounted; model broker authentication
does not provide Git push or PR authentication.

The entrypoint configures Git in the container's home to rewrite
`git@github.com:zenmoney/banks.git` to `https://github.com/zenmoney/banks.git` and
authenticate GitHub HTTPS requests with `gh auth git-credential`. The shared
checkout's `origin` is unchanged, so host Git can continue using SSH. State stays
in `banks-icon-agent-state`; no data migration is needed. State reads/pushes and
asset publication use HTTPS. Compose does not forward an SSH-agent socket or
mount `known_hosts`; `run-docker.sh` needs neither an SSH agent nor Fish.

`GH_TOKEN` is passed only at runtime, not stored in Git configuration or baked
into the image. A fine-grained token needs access to `zenmoney/banks` with
**Contents: read/write** for Git publication and **Pull requests: read/write**
for PR operations. A separately provisioned container gh login also works.
Transport authentication does not grant publication permission.

For unattended publication, the operator must provision a schemaVersion 2 policy
**outside the checkout**, owned by root or `BANK_UID`, not group/world-writable,
non-symlink and readable by the container user. Follow the
[operator policy instructions](../.agents/skills/bank-icon-maintenance/references/standing-authorization.md#one-time-operator-policy).
Set `BANK_ICON_PUBLICATION_POLICY` in `.env` to its absolute **host** path, then use:

Keep the policy at an operator-owned location such as
`$HOME/.config/bank-icon-maintenance/zenmoney-banks.json`, with mode `0600`
and an owner matching the container's `BANK_UID`. If migrating an existing
policy, preserve its permissions exactly; do not add actions during migration.
Use the absolute expanded path in the environment file passed to Compose
(`.env` by default, or the file selected with `--env-file`).

```sh
docker compose -f compose.yaml -f compose.publication.yaml up -d --no-build maintenance
```

The override mounts that existing file read-only at
`/run/bank-icon-publication/policy.json` and sets the container env to that path.
It refuses a missing/empty source rather than creating a directory. Do not put
the source under the checkout or expose it through another writable mount.
Use both `-f` arguments for subsequent commands; for emulator mode append
`-f compose.emulator.yaml`. Without policy, diagnostics remain available but
noninteractive publication is not authorized. The launcher does not create or
extend grants.
The launcher does not require or copy `banks-policy.json` into the checkout or
worktree. Remove obsolete in-checkout policy copies after migrating to the
external file. A valid token or a policy-shaped file in the checkout cannot
replace the operator-controlled external grant.

Compose sets `BANK_ICON_SOURCE_REPO=/workspace` and `BANK_ICON_REPO=/workspace/.worktree`.
The skill defaults to
`${BANK_ICON_REPO}/.agents/skills/bank-icon-maintenance/SKILL.md`, and the catalog
to `${BANK_ICON_REPO}/icon-catalog`. All maintenance uses that worktree, not a
copied source tree in the image. Project build outputs and caches live under the
ignored `.worktree/icon-catalog`, not the source checkout's `icon-catalog`.
The single `agent-home` volume at `/home/bank` retains installed client binaries,
npm cache, omp state, container Git configuration, and the Gradle user cache at
`/home/bank/.gradle`.
New home volumes initialize from bank-owned image directories. `/sessions` is
ephemeral tmpfs, not a persistent volume.
Protect broker credentials, cached access tokens, configuration, runtime
environment, and state volumes. No provider keys are baked into the image. The
container runs as `bank` with standard Docker isolation: no privileged mode,
custom seccomp profile, `cap_drop`, or `no-new-privileges` override.
`agent-browser` detects Docker and launches Chromium without its own sandbox;
Docker isolation remains, but browser compromise can reach mounted checkout
files and runtime credentials. KVM is exposed only by the emulator override.
Recreate existing containers to apply these Compose settings.

## Environment reference

`.env.example` lists the operator inputs. Compose passes only explicitly declared
runtime keys, not every host variable. Build arguments and host mount paths are
not container environment overrides. After changing runtime settings, recreate
the container (`docker compose up -d --no-build --force-recreate maintenance`);
`docker compose restart` does not reload `.env`. Use the same override files as
at startup. Build settings additionally require an image rebuild.

| Variable | Default / purpose |
| --- | --- |
| `OMP_CONFIG_FILE` | Host path `./docker/omp-config.yml`; must exist. Mounted read-only; inside the container this variable is `/home/bank/.omp/agent/config.yml`. |
| `OMP_AUTH_BROKER_URL` | Empty; optional container-reachable broker URL override. Configure authentication in the mounted file or with these broker variables. |
| `OMP_AUTH_BROKER_TOKEN` | Empty; optional broker credential override. Not a GitHub token or a build argument. |
| `OMP_UPDATE` | `0`; `1` reinstalls Bun/omp at startup. Prefer the one-off update command below; do not leave `1` with automatic restarts. |
| `BANK_ICON_CONFIG_DIR` | Host directory `./docker`, mounted read-only at `/run/bank-icon-config`. Must exist; contains the YAML and any relative prompt files. |
| `BANK_ICON_TASKS_FILE` | `tasks.yaml`, relative to `/run/bank-icon-config`; alternatively an absolute **container** path. Selects the ordered YAML `steps` list, not a host mount path. |
| `BANK_ICON_PASS_TIMEOUT_SECONDS` | `10800`; positive duration in seconds for **each step's agent invocation**, excluding Gradle preparation. Total duration depends on the configured number of invocations plus preparation and kill grace periods. |
| `BANK_ICON_RESTART_POLICY` | `no`; Compose policy, not a runtime env. Alternatives: `on-failure[:N]`, `always`, `unless-stopped`. |
| `BANK_ICON_INTERACTION` | `noninteractive` in Compose; `run` forces this mode and closes stdin for every agent. `interactive` is only for a separately invoked human-driven task. Standalone helpers otherwise default to `interactive`. |
| `BANK_ICON_PUBLICATION_POLICY` | Empty: no standing permission. With `compose.publication.yaml`, an absolute **host** policy-file path; see the mount setup below. Without the override, Compose only forwards the value and does not mount the file. |
| `BANK_ICON_RESEARCH_SEARCH_LIMIT` | Unset: skill default `20` searches per bank. |
| `BANK_ICON_RESEARCH_FETCH_LIMIT` | Unset: skill default `100` fetches per bank. |
| `BANK_ICON_RESEARCH_BROWSER_LIMIT` | Unset: skill default `20` browser operations per bank. |
| `GH_TOKEN` | Empty; GitHub CLI/API and HTTPS Git credential, including state reads/pushes. Required for authenticated GitHub operations unless the container has a separately provisioned gh login. Fine-grained publication permissions: Contents and Pull requests, both read/write. |
| `BANK_UID`, `BANK_GID` | Build arguments, both `1000`. Match host `id -u` / `id -g` for checkout writes and policy ownership checks. Changing them does not chown existing volumes. |
| `ACCEPT_ANDROID_SDK_LICENSES` | Build argument `no`; set `yes` only after accepting the Android SDK licenses. Required even without an emulator. |
| `INSTALL_ANDROID_EMULATOR` | Build argument `no`; installs emulator/system image when `yes`. The emulator override forces `yes`. |
| `KVM_GID` | Emulator override only; fallback `0`. Set the actual host `/dev/kvm` group (`stat -c %g /dev/kvm`). |

Research limits must be decimal integers from `0` to `1000`; `0` disables that
operation, not the budget. Unset/commented keys use skill defaults; explicit empty
values are rejected. Limits are frozen per bank attempt, so new env values affect
new attempts, not resumed ones. `.env.example` spells out the default counts.

### Fixed and non-Compose variables

`BANK_ICON_SOURCE_REPO=/workspace` and `BANK_ICON_REPO=/workspace/.worktree` are
fixed by Compose, not configurable through `.env`. The image/Compose also own
`HOME`, `GRADLE_USER_HOME`, `JAVA_HOME`, `ANDROID_HOME`, `ANDROID_SDK_ROOT`,
`PLAYWRIGHT_BROWSERS_PATH`, `AGENT_BROWSER_EXECUTABLE_PATH`, `VIRTUAL_ENV`, `PATH`,
`TMPDIR`, `LANG`, `PYTHONDONTWRITEBYTECODE` and `PIP_DISABLE_PIP_VERSION_CHECK`.
Host Java/SDK/cache paths must not replace these container paths.

Low-level entrypoint inputs `BANK_ICON_CATALOG`, `BANK_ICON_SKILL` and
`JDK_JAVA_OPTIONS` are not `.env` options in this service. The first changes startup
Gradle preparation; the second selects the common skill location and its sibling
`tasks/` recipes. Only selected recipes must exist. A standalone-prompt-only
sequence requires neither the maintenance skill nor the Gradle catalog.
Normal maintenance uses the worktree's catalog and installed skill.
Headless execution controls `JAVA_TOOL_OPTIONS` and unsets `DISPLAY`,
`WAYLAND_DISPLAY`, `XAUTHORITY` itself. `AGENT_BROWSER_SESSION` is a per-task browser
session, not shared service configuration.

Native macOS builds use `CATALOG_SOURCE_ROOT`, `CATALOG_BANKS`, `CATALOG_ID` and
`CATALOG_CANDIDATE`, documented in [the iOS README](../icon-catalog/iosApp/README.md).
Export them for those scripts/Xcode; they are not Linux container inputs.
`CONFIGURATION`, `SDK_NAME` and `TARGET_BUILD_DIR` come from Xcode. CI supplies
`SVGO_BINARY`, `GH_REPO`, `EXPORT_SHA` and its runner variables; these also do not
belong in the service `.env`.

Dependency pins (`PLAYWRIGHT_VERSION`, `SVGO_VERSION`, `AGENT_BROWSER_VERSION`,
`GH_VERSION`/`GH_SHA256`, `ANDROID_TOOLS_ARCHIVE`/`ANDROID_TOOLS_SHA256`,
`ANDROID_PLATFORM`, `ANDROID_BUILD_TOOLS`, `ANDROID_SYSTEM_IMAGE`) are image-build
arguments in `Dockerfile`, not runtime env. Leave pinned defaults there; deliberate
upgrades use `docker compose build --build-arg NAME=VALUE maintenance` (update
archive/version and checksum together). Docker supplies `TARGETARCH`.

## Installed tools and runtime

The image includes pinned `agent-browser` 0.38.1 and Node 24.
`AGENT_BROWSER_EXECUTABLE_PATH` points to a build-time symlink to the existing
Playwright Chromium; no second browser download or runtime installation is
needed. The browser is headless by default. Inside the container:

```sh
agent-browser skills get core
export AGENT_BROWSER_SESSION="$(agent-browser session id --scope worktree --prefix task)"
agent-browser open https://example.com
agent-browser snapshot -i
agent-browser screenshot /sessions/browser.png
agent-browser close
```

The CLI ships version-matched browser skills; project skill files still come
from the mounted checkout/worktree. Export screenshots before stopping the
container because `/sessions` is ephemeral.

The first start installs `bun@latest` and `@oh-my-pi/pi-coding-agent@latest` into
a fresh release directory in the `agent-home` volume. Later starts reuse it
without invoking npm or accessing its registry. Installation is serialized with
`flock`, and a verified release is published by atomically switching
`~/.local/omp-current`. Interrupted/stale npm directories never become the active
runtime. Running containers keep their resolved release path during an update.
To explicitly update the shared client installation once:

```sh
docker compose run --rm -e OMP_UPDATE=1 maintenance omp --version
```

A failed explicit update stops that invocation and leaves the previous release
available for normal starts. Old releases are retained so active containers are
not broken; legacy `~/.local/lib/node_modules` installations are no longer used.
When the YAML includes at least one `task`, startup prepares the
current worktree's Gradle cache online with `:androidApp:assembleDebug` and a real
Desktop export of bank ID 3. This happens once before the sequence, not before
each step; a standalone-prompt-only sequence skips it. Android SDK auto-download
is disabled. Maintenance tasks then use offline catalog export and installed
tools only: no dependency installation occurs during a task.
Build-source changes in the reused worktree
are prepared on the next container startup, without baking sources into the
image; source-checkout changes do not refresh that worktree except for `.agents`.
Restarting repeats startup. For a shell, use
`docker compose run --rm maintenance bash`; client installation is reused if
present, while worktree creation, `.agents` copying, and Gradle preparation are skipped.

### One agent invocation per YAML step

The default [tasks.yaml](tasks.yaml) runs `update-existing`, `add-missing`,
`rework-rejected`, `rework-changes`, then `merge-approved`. These are the five
supported `task` names. Edit the YAML to select, reorder or repeat steps:

```yaml
steps:
  - task: rework-changes
    prompt: |
      Pay particular attention to comments about emblem size.
  - prompt: |
      Inspect the catalog and report inconsistencies. Do not change anything.
  - prompt_file: ./prompts/catalog-audit.md
  - task: add-missing
```

Every step must contain `task`, `prompt`, or `prompt_file`. `task` is optional;
`prompt` and `prompt_file` are mutually exclusive, and either can supplement a
task. With neither prompt field, a task runs its normal recipe. Without `task`,
the launcher attaches the supplied prompt and shared runner instructions, not a
maintenance recipe.
The normal omp configuration and project instructions still apply.
Prompt files are UTF-8 text instructions, **not executable scripts**.

The YAML must have exactly one top-level `steps` key containing a nonempty list.
Empty steps, unknown fields/tasks, duplicate mapping keys, non-string/blank
instructions, conflicting prompt sources, and unreadable or non-UTF-8 prompt
files fail with exit code `2`. The entire list and all prompt files are validated
before creating the worktree or starting Gradle/agents. Selected recipes are
checked after the worktree's `.agents` copy, before Gradle or any agent.

`prompt_file` paths are relative to the YAML's directory, not the checkout or
agent working directory. Absolute paths must be accessible **inside the
container**. Create the referenced files, for example
`docker/prompts/catalog-audit.md`; the launcher does not generate them.
The directory mount preserves relative paths, including for external configs:

```dotenv
BANK_ICON_CONFIG_DIR=/absolute/host/path/to/agent-config
BANK_ICON_TASKS_FILE=tasks.yaml
```

Here `/absolute/host/path/to/agent-config/tasks.yaml` and its sibling `prompts/`
appear under `/run/bank-icon-config`. Mount a dedicated directory, not a home
directory containing unrelated secrets. Configs under the checkout are still
writable through the separate `/workspace` mount; read-only config mounting is
not a new authorization boundary.

This replaces the old `BANK_ICON_TASKS` space-separated variable; remove it from
local settings and express its order in YAML. There is no fallback task list
when the selected YAML is missing or invalid. Recreate the container after
changing `.env` paths. YAML and user prompt files are read on each start and
snapshotted before the first agent; edits during a sequence affect only the next start.
Applying this launcher change first requires rebuilding and recreating the worker.

Each step runs a fresh `omp -p --no-title --no-session` from `.worktree` with the
mounted omp config and shared [runner instructions](prompts/runner.md).
`runner.md` contains only the general finite/noninteractive execution rules,
temporary-data guidance and reporting requirements. It is bundled in the image
at `/usr/local/share/bank-icon-maintenance/runner.md`; changing it requires
rebuilding and recreating the worker, not just editing a mounted config.
The entrypoint passes `Step directory` as runtime context and includes
`Selected task` only for recipe-backed steps. Standalone prompts get no task
field or `none` placeholder.

Task steps also attach `@tasks/<task>.md`, whose instructions load the common
skill, plus any additional prompt snapshot. Standalone steps attach their prompt
snapshot without a maintenance recipe. Bank selection, publication and merge
checks stay in the recipes/skill, not in `runner.md` or embedded shell text.
Arguments are passed without evaluating prompt text as shell syntax or CLI options.
Each step gets a fresh `/sessions/<index>-<task-or-prompt>.XXXXXX` parent.
Agent context is not reused; installed tools, the worktree, Gradle cache and
durable bank state are shared. Each maintenance recipe uses its finite selection
limit, not an endless/refilled queue. Empty eligible queues are reported as no
work. No mandatory global audit precedes a step.

Every invocation is noninteractive with closed stdin. Missing publication authority
is a blocker, never a request to fabricate a grant. Among maintenance recipes,
only `merge-approved` requests merges, regardless of its position, still
requiring the configured `mergeApproved`/state permissions and exact-head human
approval/checks. Selecting/reordering steps or supplying a standalone/additional
prompt does not extend policy or waive required checks.

`BANK_ICON_PASS_TIMEOUT_SECONDS` applies independently to each invocation through
`timeout --signal=TERM --kill-after=10s` (default `10800s`). A failed or timed-out
agent is logged and the next step still starts; the sequence exits with its first
nonzero agent status, or `0` if all processes exit successfully. Timeout returns
124, or 137 after forced termination. SIGTERM/SIGINT instead terminate the active
agent and stop the sequence immediately (143/130), without starting later steps.
There are no in-process retries or restarts.

After all configured invocations finish, the launcher removes eligible session worktrees
only under the fresh step roots whose agents exited successfully. A zero agent
exit alone is insufficient: cleanup requires a state-publication receipt
(`published` or `no-state-changes`) bound to the unchanged session snapshot and a
new terminal per-object result (`published`, `unchanged`, `keep-current`, `defer`,
`unconfirmed`, `error`, `needs-human`, or `unclear`). A completed investigation
need not produce an icon: a durably recorded negative result also permits cleanup.
An inherited stop without a new task result does not. Pending unpublished
candidates and uncertain or failed state-publication receipts retain the worktree.
Failed/timed-out step roots are excluded entirely; SIGTERM/SIGINT during a step
skips cleanup. Standalone steps do not bypass these receipt/ownership requirements.

Cleanup checks the session's repository, registered worktree identity and original
detached HEAD. Local commits, tracked edits, untracked/ignored files, symlinks,
locked worktrees, malformed sessions and incomplete results are retained with a
diagnostic. Removal uses `git worktree remove` without `--force`; no global prune
or scan of old task directories occurs. The main `.worktree`, build caches,
unrelated worktrees, and session evidence outside the removed checkout are kept.
Retained sessions still disappear when the container's tmpfs is torn down; export
diagnostics before stopping it. A cleanup infrastructure failure makes an otherwise
successful sequence nonzero, without replacing an earlier task failure status.

The cleanup script is copied into the image. Rebuild and recreate the worker to
apply changes to these rules; restarting the existing container is insufficient.

Run the isolated YAML preflight and Git cleanup regression cases with:

```sh
/usr/bin/python3 -m venv /tmp/docker-tests-venv
/tmp/docker-tests-venv/bin/pip install -r docker/requirements.txt
/tmp/docker-tests-venv/bin/python -B -m unittest discover -s docker/tests -p 'test_*.py' -v
```

The [Docker tooling tests workflow](../.github/workflows/docker-tests.yml) runs
all `docker/tests/test_*.py` tests on Ubuntu 24.04 with system Python in a venv,
the pinned launcher dependencies and Git. It runs for pull requests and pushes
to `main` affecting `docker/**`, Compose files, `.env.example`, `Dockerfile`,
`.dockerignore` or the workflow itself, and supports manual dispatch. It does
not build a Docker image or run the catalog render smoke checks.

The retired `workflow.py finish` command is not used. Recipe-backed steps follow
their canonical per-object publication and state checks and report blockers
and uncertain writes. A zero process exit is not independent proof that all
selected objects were published; inspect those task results. Agent output goes
directly to stdout; step start/end markers and diagnostics go to stderr:

```sh
docker compose logs -f maintenance
```

`--no-session` disables omp transcript files; output is retained only by the Docker
logging driver. Export logs separately if they must survive container removal.
Per-step sessions and evidence remain under `/sessions` for this container run
only; the skill still controls maintenance workflows and publication permissions. Launcher
changes require rebuilding the image and recreating the container; restarting an
old image does not update its copied entrypoint.

Both `/tmp` and `/sessions` are tmpfs (`exec`, `nosuid`, `nodev`, `mode=1777`):
their contents disappear when the container stops or restarts, including abnormal
exits. Current-run reviewer contexts may keep ephemeral native tool/image traces
under `/sessions`; they are not old-session dependencies. Publish the compact
durable question context and immutable public evidence before exiting. The next
run reads the exact external answer and rechecks the selected bank/candidate in
a new session; it never needs the old worktree or report. Unknown external writes
are reconciled, not recreated. Expired worktree registrations may remain in shared
`.git`; the launcher does not prune unrelated worktrees. The old
`maintenance-sessions` volume is only disconnected, not deleted. Recreate
containers to apply mount changes.
Docker terminates remaining processes. `.worktree` and agent caches stay persistent.
No startup deletion script is needed.
Compose allows 45 seconds for shutdown; `docker compose stop maintenance`
suppresses automatic restart.

## Publication scope

The current policy explicitly controls `assetPublish`, `assetRevise`,
`statePublish` and, separately, `mergeApproved` with permitted `mergeMethods`.
Missing or false actions are denied. Environment configuration does not extend
the policy or bypass source/render checks, exact-head merge approval or provider
safety prompts. See the skill's
[publication permission contract](../.agents/skills/bank-icon-maintenance/references/standing-authorization.md)
for action coverage and interactive task confirmation.
