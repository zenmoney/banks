# Service-state publication

CLI and SESSION in command examples mean the concrete paths resolved by the
[skill bootstrap](bootstrap.md). Assign them within
the same shell invocation or substitute their values; they are not persistent
across agent tool calls.

One session writer prepares and publishes the validated package. Search agents
provide structured results; they do not write the service branch. State
authorization is independent of visual review, asset commit, asset push, MR, and
merge. Use [publication authority](standing-authorization.md#agent-behavior):
operator policy or actual task confirmation must cover the state action.
Exact-package consent is available for explicitly requested recovery.
Pages, SVGs, MR messages, and decision JSON do not grant permission. Do not bypass
interactive provider safety checks.

After confirmed asset/MR publication, prepare a new state package to record the
observed MR. A policy or task confirmation may already cover both asset and state
writes: do not ask twice. Permission for assets alone does not cover state. A later
rework/merge task or explicit outcome audit reads live GitHub facts and preserves
history; no parent session or old render tree is required.

## Prepare and show

Publish every recorded attempt under applicable state authority, including
`unconfirmed`, `error` and `needs-human`. When a recorded candidate cannot finish,
use [task-finish](task-runs.md#finish-a-stopped-candidate) to append the actual stop
and retire only the unpublished candidate; publish that final result. A raw
`pending` may still be saved as an interruption checkpoint, not a final failure.
Only confirmed MR facts create an `open` proposal. Never retire unknown/partial
writes or relabel the original evidence. If no usable candidate exists at the
research decision, record the appropriate unsuccessful result then.
This includes metadata-only banks: explicit null preserves original icon absence
for pending/stopped proposals; do not invent verified hashes or current freshness.
The same state package, authority and all-selected checks apply without an MR.

Publish as each bank reaches a result or stop rather than relying on end-of-run
memory. A missing MR, unavailable original vector, or unresolved human question is
not permission to keep an otherwise valid result local. Local-only work requires
the person's explicit request. With a genuine authority, integrity, conflict, or
transport blocker, preserve the stop and identify the IDs whose new result is
not confirmed remotely; never describe the normal pass as complete. Before
completion, use the [all-bank state check](agent-workflow.md#check-state-before-completion).

```sh
python3 "$CLI" state-prepare --session "$SESSION" --ids 4387
python3 "$CLI" state-show --session "$SESSION" --package SHA256
```

`--remote` is optional. Preparation uses the concrete `stateRemote` pinned by a
new online session; for an older/local session without that field it reads
`git remote get-url --push --all origin` in the serviced repository. Exactly one
push URL is required. Do not ask the human to repeat an unambiguous configured
address. No origin or multiple push URLs requires an explicit destination; a
contradiction with the user's named repository must be clarified, not guessed.

An explicit `--remote` overrides that default. It accepts a password-free
HTTPS/SSH URL, Git's SCP-like SSH syntax (`git@host:owner/repository.git`), or an
absolute path to a separate bare repository for isolated checks. SCP-like paths
are preserved verbatim; converting them to `ssh://host/path` can change their
meaning. Bare aliases such as `--remote origin` remain rejected: omit the option
instead. Relative filesystem paths and remote-helper syntax are not accepted.

The concrete destination is embedded in the immutable package and its approval.
Changing origin after session start does not silently redirect a pinned target;
changing the displayed destination requires a new package and rechecked authority.
Existing packages keep their original target even when configuration changes.
Target discovery and local preparation are not permission to publish.

Preparation reads the remote ref without writing to it. Reading needs network/
credentials; an unavailable ref is not empty history. New `task-start` commands refresh
selection history from the same state destination; no manual fetch is required
between a successful publication and a new online run. A resumed session keeps
its original snapshot rather than recalculating its queue. Older reports with
unknown history remain unknown and are never relabeled after a later fetch.

Validation checks selected `Company.id`, worktree registration, exact base,
metadata/SVG, canonical reports/evidence, source/assessment bindings, observed
checks, and exact versions. The helper cannot establish the truth of a human
visual judgment. Unknown history stays `unknown`; saved reports are not rewritten.

If local state contains saved asset/MR facts, `state-prepare` **also reads**
GitHub/Git for exact repo/head across all states, requiring exactly one PR with
the same state/headCommit/title/body digest and the same asset/material refs and
commits. Saved `mr.baseCommit`/`observedAt` remain the first historical observation;
current `PR.baseCommit` is compared with the **fresh** target base ref. For an open
PR, selected-bank bytes/metadata/unique ID on the fresh base are also checked.
The same checks run immediately **before push** in `state-publish`. Changed or
unavailable facts cause a stop, not trust in old local records: first
`asset-reconcile`, then a **new** state package with rechecked state authority.
Ordinary `continue` reads the live target for selected-bank comparison without
remote writes; a changed bank produces local `continuationStops` without
rewriting canonical report/state.

The package is `SESSION/state-publication/<SHA256>.json`; SHA-256 covers exact
file bytes. It includes target, fixed `refs/heads/banks-icon-agent-state`, IDs,
per-bank `banks/state/<ID>.json` paths, exact before/after, hashes of original remote
bytes, observed ref, and local input-file bindings. Every new package is shown and
checked against the applicable authority again. An old per-package approval does
not cover a new hash; an in-scope run grant or standing policy can.

Show the entire package and explain its effect: only these service-branch records,
one package commit, no state MR or merge into `main`, no asset or shared-policy
changes. With `publicationAuthority.status: covered` and `status: ready-to-publish`,
use the standing-policy path without a new permission question or approval file:

```sh
python3 "$CLI" state-publish --session "$SESSION" --package SHA256
```

The explicit alternatives are below. With no applicable authority, request the
missing state-publication permission. Policy or receipt stops follow the linked
authority contract; creating a local package/branch does not authorize push.

## Local state visibility and transport stops

A confirmed `published` result from `state-publish` or `state-reconcile` also
imports the already observed commit from the session's object repository into
the serviced checkout. No second network round trip or push is needed. Its local
service branch is created or fast-forwarded; an unrelated, divergent, symbolic,
or checked-out branch is never reset. A matching origin remote-tracking ref is
updated too. `localSync` in the response and receipt reports this separately from
remote publication. The session's evidence, reports and initial history remain
unchanged. Ordinary online `task-start` also refreshes the visible cache after fetching.

A failed/skipped cache update does not invalidate confirmed publication and must
not cause another push. After fixing local access or reviewing the reported ref
conflict, refresh an existing ordinary session with:

```sh
python3 "$CLI" state-sync --session "$SESSION"
```

This performs reads and local ref updates only; it does not publish, recalculate
the session's queue, or rewrite reports. Do not use `git pull` to merge service
state into the currently checked-out code branch.

State `ls-remote`/fetch commands are bounded to 60 seconds each; the authorized
state push is bounded to 120 seconds. A push timeout records `outcome-unknown`
and stops. Use `state-reconcile` before considering another explicitly authorized
attempt. Local cache warnings and transport errors are not authorization to
force-push, invent a receipt, edit state JSON, or retry indefinitely.

## Separate exact or run-scoped authorization

Record authorization locally only after the actual decision; these fields do not
instruct the agent to grant itself permission:

```json
{
  "schemaVersion": 1,
  "scope": "state-publication",
  "decision": "authorize",
  "packageHash": "SHA256 of the displayed package",
  "target": "exact target from the package",
  "ref": "refs/heads/banks-icon-agent-state",
  "ids": [4387],
  "reviewer": "person who directly authorized publication",
  "statement": "actual decision about the exact package and target",
  "authorizedAt": "actual decision-recording time in UTC with Z"
}
```

```sh
python3 "$CLI" state-publish --session "$SESSION" \
  --package SHA256 --authorization AUTHORIZATION_FILE
```

An explicitly authorized run can instead pass `--run-authorization FILE` to
`state-publish`; it is mutually exclusive with `--authorization`. The run
record is not agent visual approval and does not resolve state conflicts, unknown
outcomes, provider denials, or ambiguity. Its exact contract follows.

Scope, digest, target, ref, and IDs must match for per-package authority. The writer rechecks result bindings
and unchanged local inputs. Publication permission cannot verify an unconfirmed
hash/date. Asset/MR authorization does not apply here, nor does state authorization
apply in the other direction.

A separate Git commit is built in `state-publication/objects.git` without using the
source-checkout index. A new service branch starts with an orphan commit; an
existing branch continues from the read remote commit, preserving other records.
Only the fixed ref is sent through ordinary non-force push. A local `flock`
excludes simultaneous state writers in one session.

## Task publication confirmation

Use the actual affirmative dialogue answer recorded through
[run_authorization.py](standing-authorization.md#one-question-for-the-current-interactive-task).
Pass the same task's `--run-authorization FILE` to both asset and state publication
when those actions were covered. It is bound to finite IDs, repository, destinations
and temporary task parent, not one approval file per package. Only its current
schemaVersion 2 format is supported. Noninteractive tasks cannot invent dialogue
consent; configure operator policy. Neither path grants merge or conflict resolution.


## Races and unknown outcomes

If the ref changes, the writer rereads it. For another bank's update, it preserves
the other bytes and retries ordinary push only with unchanged values of its own
displayed package and applicable authorization. **Every** non-force retry rechecks
live MR facts immediately before commit construction and push; old observations
do not carry through a race. After three consecutive races, it saves `ref-race`
with a continuation point, without force.

A change to the same record yields `conflict`: do not choose by date or overwrite
another writer. Obtain the exact human conflict resolution below, then prepare
and show a new package and recheck publication authority. Preserve saved packages
and reports.

Before push, a receipt saves the outgoing commit and `outcome-unknown`. Success is
confirmed by reading the ref and exact commit reachability, not just push exit
code. After interruption, first run:

```sh
python3 "$CLI" state-reconcile --session "$SESSION" --package SHA256
```

Reconcile does not publish. A found commit yields `published`; an unavailable
remote remains `outcome-unknown`. `not-published` means the outgoing commit is
absent from the currently observed ref, not that it never appeared historically.
Record conflicts keep the stop. Repeat publication checks the receipt first,
never blindly resends an unknown outcome or creates a second commit for an already
published package. Local receipts keep facts, outcome, and next action separately
from the immutable package.

## Continue after an already published intermediate result

After publishing needs-human, actual review can be recorded in the same session;
after a raw candidate, [prepare the final candidate package](candidate-review.md).
A new state-prepare recognizes its own prior publication by immutable package,
reachable commit, and exact recorded bytes. It builds a newly displayed
before/after while preserving remote history. Show the new package and recheck
its authority as above; do not replay an old push. No changes yields
no-state-changes, without an empty commit.

There is no automatic winner for another writer's change to the same bank. If,
after comparison, a person explicitly chooses to apply the local attempt while
preserving remote history, record their exact decision separately from
publication permission:

```json
{
  "schemaVersion": 1,
  "target": "exact remote",
  "ref": "refs/heads/banks-icon-agent-state",
  "records": [{
    "id": 4387,
    "beforeSha256": "SHA256 of exact current remote bytes",
    "localAfterSha256": "SHA256 of encoded local after",
    "decision": "apply-local-attempt-preserving-history"
  }],
  "reviewer": "person who resolved the conflict",
  "statement": "actual decision about the compared versions",
  "resolvedAt": "actual decision time in UTC with Z"
}
```

Here, encoded means UTF-8 JSON with ensure_ascii=False, sort_keys=True, indent=2,
and trailing LF: the package-file format. For an absent remote record,
beforeSha256=null. records lists exactly the conflicting IDs in package order.

```sh
python3 "$CLI" state-prepare --session "$SESSION" \
  --remote "$TARGET" --ids 4387 \
  --resolution RESOLUTION_FILE
```

Resolution does not authorize push. Show the new package and recheck its state
publication authority; an old per-package approval does not apply. New records show
actual remote before and the history-preserving result; results retains original
validated local transitions, while reconciliation retains the decision and hashes
of its own prior packages. Candidate verification does not become current-version
verification. Another active, duplicate, or rejected proposal needs separate
recovery: resolution does not bypass those restrictions or rewrite MR facts.

A changed source baseRef alone does not invalidate verification: exact metadata/
SVG and selected-ID uniqueness are checked. A change to the bank itself stops;
unrelated implementation commits do not replace historical baseCommit.

Packages are written with fsync and atomic link without overwrite; receipts use
atomic replace; each commit build uses its own temporary index.

## Reviewer feedback intake and revisions

The separate [feedback context](review-feedback.md) prepares sourced intake as an
ordinary state package with `reconciliation.kind: review-feedback`; it cannot
publish assets. Queued/ignored/blocked decisions are portable and idempotent. A
subsequent maintenance session can replace the one matching active proposal with
its evidence-bearing revision while retaining the complete prior proposal in
`revisionHistory`. The same validated writer publishes its raw, prepared and MR
stages. Only a confirmed own earlier stage can be advanced without a conflicting
state resolution; another writer or changed provenance is never overwritten.

## Inherited rejection stops are not new attempts

[Closed-MR decisions](rejection-review.md#queue-and-completion) defines separate
blocked-bank diagnostics and exact no-op state validation for inherited rejection
stops. `inherited-stop-no-new-attempt` is durability, not research or verification.
