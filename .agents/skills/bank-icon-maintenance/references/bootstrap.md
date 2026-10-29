# Bootstrap and continuation

Use one task-oriented route. Resolve three independent locations: CLI is the
existing absolute `scripts/session.py` beside the installed SKILL.md; REPO is the
person's serviced checkout or the Git root of the original task working directory;
SESSION is a new per-bank path or the exact current path for continuation.
A repository URL is not a local path. Do not append `brands`, search neighboring
checkouts, create symlinks for guessed paths or use the skill development repository
as the serviced catalog. Bind concrete quoted variables in each tool invocation.

## Preflight before creating anything

```sh
python3 "${CLI:?Resolve the actual installed session.py}" doctor --render
```

Without --repo, doctor discovers the task Git root. An explicit --repo is authoritative;
a typo cannot fall back to another checkout. Use the same chosen committed --base
for doctor, task-list and task-start. It must contain banks/ and
bank-icon-maintenance.json. Uncommitted files are not frozen inputs.

Read status, checks, paths.cli, paths.repo and paths.catalog. A nonzero exit or
status: blocked stops until diagnosed; ready is not renderer/visual certification.
Merge-only needs no --render. --github checks gh authentication read-only and
supplies no permission. Install prerequisites only under the operator's permission;
maintenance never downloads Android tools. See README and headless-runtime.md.

Read publicationPolicy and [publication permission](standing-authorization.md).
A covered policy permits the selected actions without another question. Otherwise
interactive tasks ask once for finite IDs/MRs, target and actions; noninteractive
publishing tasks stop early. An invalid policy is not permission to modify it.
Local-only is a restriction, never a fallback silently chosen by the agent.

## Plan and allocate

Use [task-runs](task-runs.md): task-list for discovery, then one task-start per bank;
explicit IDs/MRs need no initial list. add-missing orders eligible numeric IDs
from largest to smallest before the limit. There is no second automatic selector.
An empty plan finishes without sessions or a publication question. Never refill a
finite plan after failures. No whole-catalog outcome/feedback sweep precedes a task.

Allocate a unique temporary parent outside REPO after preflight. For a single bank,
this command prints a nonexistent session child under that new parent:

```sh
python3 -c 'import tempfile; from pathlib import Path; print(Path(tempfile.mkdtemp(prefix="bank-icon-")).resolve() / "session")'
```

For multiple banks use distinct nonexistent child paths under the same task parent.
When recording dialogue consent, allocate the empty parent first through the
permission guide; record its actual answer there before creating bank children.
Report concrete paths. Shell variables may not persist between tool calls.

Task-start reads origin's sole push URL and fresh service history for each bank.
An explicit --remote pins another destination; contradictory or multiple targets
require resolution. Failed online reads are not permission to use stale history.
Display stateRemote, stateRead, stateRead.localSync and each bank's point/reason.
Local cache refs may fast-forward; divergent/checked-out/symbolic refs stay intact.
The fetched private snapshot remains authoritative even when cache sync fails.

The session owns a detached worktree at baseCommit. Identity/state errors may
leave diagnostic resources; inspect rather than delete/restart blindly. Current
schemaVersion 2 sessions each have exactly one bank and no selection-approval phase.

## Continue the current operation

Resolve CLI from the installed skill and reuse the exact SESSION. Run
`doctor --session "$SESSION" --render`, then `continue --session "$SESSION"`.
Pause preserves the bank's point; continuation does not select another bank or
reopen a completed attempt. Use workflow next to follow the saved stage. No manual
session/report edits, fabricated attestations, or renewed verification timestamps.

A new requested task uses fresh state and evidence, not a parent session. If an
external write may already have happened, reconcile its retained package or compact
operation intent before anything else. Read-only task-recover / merge-reconcile do
not need old render trees. Missing files never authorize replaying an unknown write.
Saved old-version sessions are unsupported; preserve their uncertain-write evidence
for diagnosis, then use a fresh task only after establishing remote facts.

CLI bank stops may use exit 0 with a point/reason; command errors use exit 1. Read
the structured result, not just the exit status. Cleanup only owned temporary
runtime resources; never erase durable bank state or uncertain-write intents.
