# Merge approved proposals

Apply the [common skill](../SKILL.md) and [shared task tools](../references/task-runs.md).

Only run on an explicit merge instruction and matching merge authority, not as a side effect of icon research.

Use `merge-list --repo REPO --base main --limit N` or explicit requested MR numbers.
[Parallel work](../references/task-runs.md#parallel-work) may cover preliminary read-only
inspection of independent MRs; those observations are not prepared merge packages.
Keep fresh `merge-prepare` → `merge-publish` → state publication sequential for MRs
sharing a target branch. A merge changes that base; never prepare all packages ahead.
For each MR run `merge-prepare --repo REPO --mr NUMBER --base main --method METHOD`,
show its exact head and use `merge-publish --operation DIR` under merge authority.
Reconcile uncertain results read-only with `merge-reconcile`. Publish the returned
state package with `--session DIR` under state authority before preparing the next
MR against its fresh base. Do not search, rerender, bypass rules or invent dates.

Read [approval checks and skip diagnostics](../references/merge-readiness.md).
Save each object's result; report skipped/failed objects, state/write outcomes and
evidence limits; finish the finite request.
