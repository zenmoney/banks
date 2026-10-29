# Add missing bank icons

Apply the [common skill](../SKILL.md) and [shared task tools](../references/task-runs.md).

Select existing valid metadata records with no icon. This adds an icon, not a bank entity.

Use `task-list --task add-missing`, then `task-start --task add-missing --id ID`. Keep null baseline and final-only frames; missing is never unchanged. Preserve metadata and publish the first icon only after all normal checks.

Use [parallel work](../references/task-runs.md#parallel-work) for independent research,
preparation and checks. The coordinator records/publishes each ready result without
waiting for other banks; session writes and shared resources stay coordinated.
Report skipped/failed objects, state/write outcomes and evidence limits; finish the
finite request. Do not merge unless explicitly running the merge-approved task.

Select eligible numeric Company.id values descending before the limit; dispatch in
that priority order, not completion order. Explicit IDs retain their dispatch order.
