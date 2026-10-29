# Fix open review requests

Apply the [common skill](../SKILL.md) and [shared task tools](../references/task-runs.md).

The default list uses current formal CHANGES_REQUESTED. Explicit user-selected open PRs with ordinary remarks can go directly to task-start.

Use `task-list --task rework-changes`, then `task-start --task rework-changes --id ID --mr NUMBER`. Interpret actual sources with task-review, find/prepare a fresh candidate and meet every requirement. Append to the same exact PR head; preserve human title/body, retain prior versions and post one new comparison. Never create a competing PR.

If no new/revised icon is published, leave a [result comment](../references/task-runs.md#rework-result-comments)
on the source MR explaining the recorded reason; do not duplicate the same result.

Use [parallel work](../references/task-runs.md#parallel-work) for independent research,
preparation and checks. The coordinator records/publishes each ready result without
waiting for other banks; session writes and shared resources stay coordinated.
Report skipped/failed objects, state/write outcomes and evidence limits; finish the
finite request. Do not merge unless explicitly running the merge-approved task.
