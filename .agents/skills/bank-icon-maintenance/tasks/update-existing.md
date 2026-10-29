# Update existing icons

Apply the [common skill](../SKILL.md) and [shared task tools](../references/task-runs.md).

Select up to the requested count of due existing icons, using current bank state and committed catalog. Research justified updates; never force a visual change or N successful PRs.

Use `task-list --task update-existing`, then `task-start --task update-existing --id ID` per selected bank. Existing open proposals are not duplicated. Publish a checked improvement or record the actual unchanged/unsuccessful result.

Use [parallel work](../references/task-runs.md#parallel-work) for independent research,
preparation and checks. The coordinator records/publishes each ready result without
waiting for other banks; session writes and shared resources stay coordinated.
Report skipped/failed objects, state/write outcomes and evidence limits; finish the
finite request. Do not merge unless explicitly running the merge-approved task.
