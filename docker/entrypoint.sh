#!/bin/sh
set -eu

# Keep the shared checkout's origin unchanged; use HTTPS only in this container.
git config --global url."https://github.com/zenmoney/banks.git".insteadOf \
    "git@github.com:zenmoney/banks.git"
git config --global --replace-all credential.https://github.com.helper ''
git config --global --add credential.https://github.com.helper '!gh auth git-credential'

mkdir -p "$HOME/.local/omp-releases"
exec 9>"$HOME/.local/omp-install.lock"
flock 9
current="$HOME/.local/omp-current"
if [ ! -x "$current/bin/omp" ] || [ ! -x "$current/bin/bun" ] || [ "${OMP_UPDATE:-0}" = 1 ]; then
    runtime=$(mktemp -d "$HOME/.local/omp-releases/runtime.XXXXXX")
    pointer="$HOME/.local/omp-current.$$"
    trap 'rm -rf "$runtime"; rm -f "$pointer"' 0
    printf '%s\n' 'Installing Bun and omp into a new persistent runtime...' >&2
    npm install --global --prefix "$runtime" \
        --cache "$HOME/.local/npm-cache" --no-audit --no-fund \
        bun@latest @oh-my-pi/pi-coding-agent@latest >&2
    PATH="$runtime/bin:$PATH" "$runtime/bin/omp" --version >&2
    ln -s "$runtime" "$pointer"
    mv -Tf "$pointer" "$current"
    trap - 0
fi
runtime=$(readlink -f "$current")
flock -u 9
exec 9>&-
export PATH="$runtime/bin:$PATH"

# Diagnostic commands remain usable without creating a maintenance worktree.
if [ "${1:-}" = run ]; then
    source_repo=${BANK_ICON_SOURCE_REPO:-/workspace}
    export BANK_ICON_REPO="${BANK_ICON_REPO:-$source_repo/.worktree}"
    if [ ! -e "$BANK_ICON_REPO/.git" ]; then
        git -C "$source_repo" worktree add --detach "$BANK_ICON_REPO" HEAD
    else
        git -C "$source_repo" worktree repair "$BANK_ICON_REPO"
    fi
    test "$(git -C "$BANK_ICON_REPO" rev-parse --show-toplevel)" = "$BANK_ICON_REPO"
    mkdir -p "$BANK_ICON_REPO/.agents"
    cp -a "$source_repo/.agents/." "$BANK_ICON_REPO/.agents/"
    catalog=${BANK_ICON_CATALOG:-$BANK_ICON_REPO/icon-catalog}
    skill=${BANK_ICON_SKILL:-$BANK_ICON_REPO/.agents/skills/bank-icon-maintenance/SKILL.md}
    recipes=$(dirname "$skill")/tasks
    tasks='update-existing add-missing rework-rejected rework-changes merge-approved'
    test -r "${OMP_CONFIG_FILE:-$HOME/.omp/agent/config.yml}"
    test -r "$skill"
    for task in $tasks; do
        test -r "$recipes/$task.md"
    done
    export BANK_ICON_INTERACTION=noninteractive

    # Provision the current checkout's build cache before the installed-only skill.
    prep=$(mktemp -d /tmp/bank-icon-prepare.XXXXXX)
    trap 'rm -rf "$prep"' 0
    printf '%s\n' 'Preparing Gradle cache from the Git worktree...' >&2
    env -u DISPLAY -u WAYLAND_DISPLAY -u XAUTHORITY \
        JAVA_TOOL_OPTIONS=-Djava.awt.headless=true \
        JDK_JAVA_OPTIONS="${JDK_JAVA_OPTIONS:-} -Djava.io.tmpdir=$prep" \
        "$catalog/gradlew" -p "$catalog" --no-daemon --max-workers=2 \
        -Pandroid.builder.sdkDownload=false -Pkotlin.compiler.execution.strategy=in-process \
        "-PcatalogBanks=$BANK_ICON_REPO/banks" -PcatalogId=3 \
        :androidApp:assembleDebug :catalog:run \
        --args="export --id 3 --manifest \"$catalog/catalog/build/generated/catalog/manifest.json\" --output \"$prep/render\"" >&2
    rm -rf "$prep"
    trap - 0
    cd "$BANK_ICON_REPO"
    status=0
    # Only these fresh task roots can own worktrees eligible for cleanup.
    set -- "$source_repo" "$BANK_ICON_REPO"
    for task in $tasks; do
        task_root=$(mktemp -d "/sessions/$task.XXXXXX")
        printf 'Starting task %s (sessions: %s)\n' "$task" "$task_root" >&2
        timeout --signal=TERM --kill-after=10s "${BANK_ICON_PASS_TIMEOUT_SECONDS:-10800}s" \
            omp -p --no-title --no-session \
            --config "${OMP_CONFIG_FILE:-$HOME/.omp/agent/config.yml}" \
            "@$recipes/$task.md" \
            "Execute exactly one finite noninteractive task: $task. Follow the attached task recipe and common skill, not the other task recipes or a global startup audit. Use the configured selection limit and one finite plan; do not refill it after failures. Use $task_root as this task's temporary parent, creating a fresh child session or operation directory for each selected object. Keep temporary files in /tmp. Never wait for stdin or an owner reply. Check the configured publication authority before research or writes; missing or insufficient authority is a blocker, not permission to ask or manufacture a grant. Only the merge-approved task requests merges, and still requires matching merge authority and exact-head approval checks. Follow the recipe's state publication and verification before claiming completion. Report an empty eligible queue as no work; otherwise report each processed, skipped, failed or blocked object and any unconfirmed writes. Sessions and evidence are ephemeral and disappear when this container stops." </dev/null &
        pass_pid=$!
        trap 'kill -TERM "$pass_pid" 2>/dev/null || :; wait "$pass_pid" || :; exit 143' TERM
        trap 'kill -INT "$pass_pid" 2>/dev/null || :; wait "$pass_pid" || :; exit 130' INT
        if wait "$pass_pid"; then
            task_status=0
        else
            task_status=$?
        fi
        trap - TERM INT
        printf 'Finished task %s (exit: %s)\n' "$task" "$task_status" >&2
        if [ "$task_status" -eq 0 ]; then
            set -- "$@" "$task_root"
        fi
        if [ "$status" -eq 0 ] && [ "$task_status" -ne 0 ]; then
            status=$task_status
        fi
    done
    if python3 "$(dirname "$0")/cleanup-worktrees.py" "$@"; then
        :
    else
        cleanup_status=$?
        printf 'Worktree cleanup failed (exit: %s)\n' "$cleanup_status" >&2
        if [ "$status" -eq 0 ]; then
            status=$cleanup_status
        fi
    fi
    exit "$status"
fi

exec "$@"
