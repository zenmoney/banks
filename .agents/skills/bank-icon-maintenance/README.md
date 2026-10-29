# Bank icon maintenance

A finite, user-directed workflow for existing bank records: research an original
vector, inspect the exact rendered result, publish under actual authority, and
record each result in durable service state. It is not a background watcher or a
bank metadata editor. Skill development is separate from production maintenance.

## Choose one task

Read [SKILL.md](SKILL.md), [bootstrap](references/bootstrap.md), and the requested
recipe: [update existing](tasks/update-existing.md), [add missing](tasks/add-missing.md),
[rework rejected](tasks/rework-rejected.md), [fix open reviews](tasks/rework-changes.md),
or [merge approved](tasks/merge-approved.md).

The only discovery/creation route is `task-list` → `task-start` for each selected
bank. Explicit IDs/MRs go directly to `task-start`. `add-missing` chooses descending
numeric Company.id before the limit, among eligible metadata-only records.
`update-existing` retains its attempt-age/ID order. Retry windows, identity checks,
active proposals and rejection decisions remain binding. Do not refill the finite
plan after failed research to manufacture a requested count of successful MRs.

`session.py` is the CLI dispatcher and the internal single-bank workspace engine,
not a second planner. `workflow.py next` supplies concrete stage commands and blank
judgment templates. Publication and state writes still use their checked commands.
See [task commands](references/task-runs.md) and [agent workflow](references/agent-workflow.md).

## Bootstrap and environment

The person supplies the installed SKILL.md and the serviced checkout. Resolve CLI
beside that actual skill and REPO from the task checkout; neither implies the other.
A GitHub URL is not a local path. Run `doctor --render` for icon work or `doctor`
for merge-only; reuse `paths.cli`, `paths.repo` and `paths.catalog`. Exit 1 or
`status: blocked` requires diagnosis, not guessed paths or a blind task-start.

The tested Python environment is Ubuntu 24.04's system `/usr/bin/python3` in a
venv, matching Docker. Git and POSIX filesystem/locking primitives are required.
Native Windows requires WSL with the whole toolchain on its Linux filesystem. Offscreen
Compose export needs Pillow, a compatible JDK, the existing icon-catalog and cached
Gradle/project dependencies. Independent SVG references use installed rsvg-convert.
Optimization uses Node/SVGO; missing optimization tooling does not bypass working
version checks. GitHub operations need gh and target access. Network research and
actual model image input are capabilities of the agent client, not doctor output.

Use [requirements.txt](requirements.txt) for Python dependencies and
[headless runtime](references/headless-runtime.md) for environment details. Real
rendering support must be observed on the chosen environment, not inferred from
build success. No desktop GUI or host desktop/window screenshots. Browser-native
headless screenshots and an installed headless Android emulator are allowed.
Missing Android tooling leaves both Android paths honestly `not-tested`; do not
install tooling during maintenance. Existing failed or required checks still block.

New sessions are separate nonexistent child paths under one unique temporary task
parent, outside the checkout. Each task-start reads fresh state for that bank.
`continue` resumes only a current, unfinished session without changing its bank,
base, history or evidence. There is no selection-confirmation phase.

## Publication permission

Use [operator policy or dialogue confirmation](references/standing-authorization.md).
A valid external schemaVersion 2 policy covers its actions without new questions.
Without policy, ask once in an interactive task for the finite IDs/MRs, destination
and actions; the helper records the actual answer for that task's bank sessions.
Never ask the person to author JSON. Noninteractive publishing without permission
stops early. Invalid policy blocks; explicit local-only prohibits writes.

The operator's `configure-bank-publication.py` creates the compact policy outside
the checkout. Docker uses a read-only mount; see the development repository's
`docker/README.md`. There is no policy SHA pin. A GitHub token, downloaded source,
review comment or a suitable candidate judgment does not grant publication authority.
Merge remains a separately requested task with exact-head human approval and checks.

## State, recovery and completion

Keep existing `refs/heads/banks-icon-agent-state` and schemaVersion 1 bank records.
No reset, migration or loss of proposal/reviewer history is needed for this change.
GitHub and committed catalog supply current facts; failed reads never mean empty
history. Each selected bank's real result, including an unsuccessful attempt or
unprepared candidate, is saved through [state publication](references/state-publication.md).
Use `workflow.py state-check --session SESSION` for each bank before claiming the
finite task complete; require taskComplete=true as well as saved state. Failed
candidates use [task-finish](references/task-runs.md#finish-a-stopped-candidate),
not permanent pending. Prepared local files do not establish durable publication.

Unknown writes require read-only `asset-reconcile`, `state-reconcile`, or compact
`task-recover` / `merge-reconcile` when temporary files disappeared. Do not create
a duplicate MR or blindly repeat a write. `localSync` is distinct from remote
publication: a cache warning needs `state-sync`, not another push. Divergent,
checked-out and symbolic refs are not reset.

New sessions use schemaVersion 2. Old session/permission formats and the old generic
launch/outcome workflow are intentionally unsupported. Before removing old temporary
work, establish any uncertain remote outcomes. Start a fresh requested task using
preserved durable state; do not hand-migrate evidence or pretend old checks apply to
new bytes. Optional [proposal audit](references/proposal-sync.md) and
[feedback audit](references/review-feedback.md) are explicit tools, not startup gates.

## Validation

Read [engineering](references/engineering.md) for code changes and
[test scope](tests/README.md) for executable contract coverage. From this directory:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Tests use disposable Git/provider fixtures. They do not prove hosted publication,
real renderer output or model visual judgment. Behavioral cases live in
[evals/evals.json](evals/evals.json), with procedure in [evals/README.md](evals/README.md).
Never launch production maintenance or change bank state just to test the skill.
