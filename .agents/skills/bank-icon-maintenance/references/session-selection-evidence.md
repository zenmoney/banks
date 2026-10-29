# Selection and evidence validation

This engineering guide describes executable checks, not a claim of a live agent run.
The single planner is tasks.plan; task-list discovers a finite plan, task-start
creates a fresh one-bank workspace and checks its relevant live context.

## Selection invariants

For add-missing, metadata records must lack a committed icon; eligible numeric IDs
are considered in descending order before filling the requested limit. The newest
eligible ID takes priority even if a smaller ID has never been attempted. A recent
error/unconfirmed attempt remains excluded by its retry window. Invalid identities,
active proposals, human stops and unavailable state are not silently cleared.

Update-existing keeps its attempt-age/ascending-ID ranking. Explicit task-start
uses exactly the requested bank; it never starts an automatic queue. Current
continuation never recalculates the plan. Rework selection uses the requested MR
state and actual discussion; approved merge uses exact human-reviewed current head.

## Mechanical checks

The test suite covers committed catalog identity, exact bytes, missing-icon null
baselines, no invented unchanged verification for absent icons, finite limits,
state read failures, pinned snapshots and no mutation of unrelated refs/worktrees.
Tests compare selected IDs and side effects rather than incidental prose.

Source fidelity and visual suitability still require actual source research and
model image input. Synthetic fixture observations exercise evidence bindings only.
A passing hash or script cannot establish that the inspected logo is the correct
bank, a faithful vector, or legible on the target themes/platforms.

Run the complete suite from the skill directory as documented in tests/README.md.
Use task entry-point and task selection regressions for CLI behavior; use
source/candidate/publication suites for their own invariants. The removed generic
queue and session-bound permission formats have rejection tests, not adapters.
