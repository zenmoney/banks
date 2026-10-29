# Rework rejected proposals

Apply the [common skill](../SKILL.md) and [shared task tools](../references/task-runs.md).

Consider the requested count of closed unmerged bank PRs whose significant remarks have not been processed. Closing is not by itself a logo reason.

Use `task-list --task rework-rejected`, then `task-start --task rework-rejected --id ID --mr NUMBER`. Read discussion and record `task-review`; preserve each requirement. A repair gets a fresh candidate and a new linked PR, not reopened old history. Keep/defer/unclear records its actual no-search decision. Old sessions are unnecessary.

If no new/revised icon is published, leave a [result comment](../references/task-runs.md#rework-result-comments)
on the source MR explaining the recorded reason; do not duplicate the same result.

Use [parallel work](../references/task-runs.md#parallel-work) for independent research,
preparation and checks. The coordinator records/publishes each ready result without
waiting for other banks; session writes and shared resources stay coordinated.
Report skipped/failed objects, state/write outcomes and evidence limits; finish the
finite request. Do not merge unless explicitly running the merge-approved task.
