# Single-bank session contract

New work uses [task-list/task-start](task-runs.md); paths and prerequisites follow
[bootstrap](bootstrap.md). session.py owns the frozen workspace and evidence
transitions, not automatic selection. The internal create_session primitive is
called by task-start, which binds the requested task and fresh GitHub observations.

## Workspace

A current session.json has schemaVersion 2, repo, baseRef/baseCommit, worktree and
worktreeGitDir, stateRef/stateCommit/stateRemote/stateRead/stateHistory, now,
selectedIds with exactly one integer Company.id, banks with that same one record,
paused, and frozen researchBudget. The bank binds folder, metadata/metadataHash,
iconHash (null only for a confirmed missing icon), original state, taskContext,
point/reason/warnings, and any report/evidence links as transitions occur.

The detached worktree's registration, HEAD, exact icon/metadata bytes and unique
identity are revalidated. The source checkout/index and original frozen worktree
are not modified by publication. Explicit IDs and publication permission cannot
clear identity, source, byte-integrity or conflicting-state stops. State history
is a pinned observation; missing remote history remains unknown, not invented.

One bank per session prevents another bank's partial result from sharing mutable
session bookkeeping. The task's finite plan and any dialogue grant may cover several
such sessions. Neither list approval nor a nested parent/child outcome chain exists.
Existing service-state records remain schemaVersion 1 and are preserved separately.

## Stages and continuation

Relevant points: task-review, identity-evidence, waiting-proposal, waiting-mr,
awaiting-state-publication, attempt-complete, task-complete, needs-human, blocked,
and paused (with priorPoint). workflow next reads the canonical point and returns
stage guides, commands and required evidence. Its blank templates grant no opinion.

`continue --evidence FILE` records a validated actual source decision only at
identity-evidence. `continue --candidate FILE` prepares the exact final version at
waiting-proposal. `continue --review FILE` records a genuine narrow human resolution
of an applicable needs-human source decision, not general publication permission.
These inputs are mutually exclusive; resume a paused session before recording them.
`pause` saves the point, ordinary `continue` resumes and revalidates linked prepared
candidate evidence without changing bank, base, queue, dates or publication rights.
An already completed attempt is not reopened by continue.
`task-finish` appends a validated completion of a known-unpublished pending candidate;
it preserves all prior evidence and enables ordinary retry timing for failures.
See [candidate completion](task-runs.md#finish-a-stopped-candidate).

Original retrievals, reports, images and judgments remain immutable. Changed bytes
need a new prepared version with actual checks; no editing hashes to make evidence
fit. Different catalog bytes stop the bank. Unrelated changes elsewhere in the
catalog do not invalidate a byte-identical selected bank.

Use [candidate review](candidate-review.md), [asset publication](asset-publication.md)
and [state publication](state-publication.md) for their current contracts. A new
rework task reads published review words and makes fresh evidence; it does not
resume a previous generation of sessions. Merge-approved is separate, using exact
current human approval and checks, never side-effect merge after opening an MR.

## Stops and recovery

Before repeating an uncertain remote operation, use its read-only asset-reconcile
or state-reconcile. If temporary files disappeared, inspect the compact task-recover
or merge-reconcile intent. An absent response does not establish absent output.
Provider denial, conflicting heads or multiple proposals cannot be cleared by a
new session or broad standing permission. Preserve diagnostics and tell the person
exactly what remains unknown.

After each real result, publish state under applicable policy/task confirmation and
require workflow state-check before normal completion. An inherited unchanged stop
may validate as no-state-changes; it is not a fresh attempt or icon verification.
No MR is not permission to omit an otherwise valid stopped result. Only explicit
local-only work or a real blocker permits ending with unpublished results.

Old launch commands, selection approvals, session schemaVersion 1, and old
permission formats are not supported. Do not migrate them by hand or erase durable
history. Determine uncertain remote outcomes and start a fresh requested task.
