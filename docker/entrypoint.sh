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
    launcher=$(dirname "$(readlink -f "$0")")
    runner=/usr/local/share/bank-icon-maintenance/runner.md
    test -r "$runner"
    config=${BANK_ICON_TASKS_FILE:-tasks.yaml}
    case "$config" in
        /*) ;;
        *) config="/run/bank-icon-config/$config" ;;
    esac
    plan=$(mktemp -d /tmp/bank-icon-steps.XXXXXX)
    trap 'rm -rf "$plan"' 0
    python3 "$launcher/prepare-steps.py" "$config" "$plan"

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
    test -r "${OMP_CONFIG_FILE:-$HOME/.omp/agent/config.yml}"
    while IFS= read -r index; do
        task=$(cat "$plan/$index/task")
        if [ -n "$task" ]; then
            test -r "$skill"
            test -r "$recipes/$task.md"
        fi
    done < "$plan/order"
    export BANK_ICON_INTERACTION=noninteractive

    # Only recipe-backed runs need the catalog's installed-only build cache.
    if [ -e "$plan/needs-catalog" ]; then
        prep="$plan/prepare"
        mkdir "$prep"
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
    fi
    cd "$BANK_ICON_REPO"
    status=0
    # Only these fresh task roots can own worktrees eligible for cleanup.
    set -- "$source_repo" "$BANK_ICON_REPO"
    while IFS= read -r index; do
        step="$plan/$index"
        task=$(cat "$step/task")
        label=$(cat "$step/label")
        task_root=$(mktemp -d "/sessions/$label.XXXXXX")
        printf 'Starting step %s (sessions: %s)\n' "$label" "$task_root" >&2
        (
            set -- omp -p --no-title --no-session \
                --config "${OMP_CONFIG_FILE:-$HOME/.omp/agent/config.yml}" \
                "@$runner"
            context=$(printf 'Step directory: %s' "$task_root")
            if [ -n "$task" ]; then
                set -- "$@" "@$recipes/$task.md"
                context=$(printf 'Selected task: %s\n%s' "$task" "$context")
            fi
            if [ -e "$step/prompt.md" ]; then
                set -- "$@" "@$step/prompt.md"
            fi
            set -- "$@" "$context"
            exec timeout --signal=TERM --kill-after=10s "${BANK_ICON_PASS_TIMEOUT_SECONDS:-10800}s" "$@"
        ) </dev/null &
        pass_pid=$!
        trap 'kill -TERM "$pass_pid" 2>/dev/null || :; wait "$pass_pid" || :; exit 143' TERM
        trap 'kill -INT "$pass_pid" 2>/dev/null || :; wait "$pass_pid" || :; exit 130' INT
        if wait "$pass_pid"; then
            task_status=0
        else
            task_status=$?
        fi
        trap - TERM INT
        printf 'Finished step %s (exit: %s)\n' "$label" "$task_status" >&2
        if [ "$task_status" -eq 0 ]; then
            set -- "$@" "$task_root"
        fi
        if [ "$status" -eq 0 ] && [ "$task_status" -ne 0 ]; then
            status=$task_status
        fi
    done < "$plan/order"
    if python3 "$launcher/cleanup-worktrees.py" "$@"; then
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
