# Task-oriented runs over existing state

These are the only entry points for new work. No generic start/selection approval
or parent-session outcome workflow is supported. Continue current unfinished
operations and use compact recovery for uncertain writes. Durable bank state remains
`refs/heads/banks-icon-agent-state`, schemaVersion 1; no reset or migration.

## Plan and start

Resolve CLI/REPO through [bootstrap](bootstrap.md). Use the person's count, IDs,
MRs, committed base and remote. For discovery:

```sh
python3 "$CLI" task-list --repo "$REPO" --base "$BASE" --task rework-rejected --limit 20
```

Tasks: `update-existing`, `add-missing`, `rework-rejected`, `rework-changes`.
For add-missing, eligible numeric IDs are ordered descending before the limit.
Update-existing retains attempt-age/ID order. The returned list is finite: no refill after unsuccessful work. Explicit IDs/MRs
need no initial list. Record selection/provenance; task-start reads fresh state for
each object. Invalid records are per-bank diagnostics. Failed remote reads never
mean empty history. Default count uses selectionLimit; an explicit 1..200 overrides
it. Remote pages and command read budgets remain bounded (180 seconds); limits or
no-progress reads are disclosed, not a claim of a complete sweep.

```sh
python3 "$CLI" task-start --repo "$REPO" --base "$BASE" --session "$SESSION" \
  --task rework-rejected --id "$ID" --mr "$MR"
```

Call task-start directly through session.py, not workflow.py run: the wrapper
requires an existing session and only accepts its supported session commands.
Task-list and operation-based merge/recovery commands also use session.py directly.

SESSION is a nonexistent sibling directory per selected object under one unique
system-temp parent. It owns only this object's new evidence. No source/target reset,
metadata changes or history edits. Use workflow next/work-order/attempt/candidate/
assessment/admission commands normally; all exact-byte render and source checks
remain. Addition uses null baseline. A plain task does not override a saved rejection;
explicit rework reads its actual requirements instead of replaying parentSession.

## Parallel work

A finite task is not a serial bank-by-bank loop. Use available subagents for
independent work when the host has capacity; sequential execution remains valid
when delegation is unavailable. Keep one coordinator and the original finite list,
not one queue per worker. Dispatch in returned priority order (`add-missing`: larger
IDs first), or the explicit requested order; completion/publication may be out of
order unless the person explicitly requires sequential completion. A slow or stopped
bank must not hold up unrelated ready banks. Do not refill the list after failures.

**Worker scope.** The coordinator creates each bank's session and hands off its
exact ID/task/MR, frozen base, work order, remaining per-bank budget and owned output
paths. Independent source searches, HTTP/headless-browser retrieval, discussion
analysis, SVG preparation/SVGO on separate copies, and inspection of immutable
source/render images may overlap. Respect each bank's stage dependencies: interpret
reviewer requirements before searching for a repair; check final bytes only after
preparation. A research-only work order stays research-only; delegate preparation
separately with its stage guide and inputs. Workers return receipts, artifacts and
actual observations, not fabricated approvals or canonical state changes. Required
source/visual assessment still applies; a worker's summary or hash is not image input.

Use a separate evidence/work directory and browser context per worker. Multiple
researchers for the same bank share its session and cumulative budget through the
existing adapters, never a new per-worker allowance or second bank attempt. Honor
ledger/writer busy responses and reuse existing tickets/results, rather than
repeating an external action or resetting limits. Never overwrite another worker's
findings. Stop and join writers to a candidate before freezing its bytes for review;
later edits need fresh checks. Clean up only owned resources after their jobs end.

**Coordinator scope.** Keep canonical session transitions (`task-review`, attempt/
candidate recording, assessment and admission), Git/worktree/ref mutations, and
asset/state publication with one coordinator. Serialize these writes; workers can
continue isolated research/preparation while it records or publishes another bank.
A background command owns its write until terminal completion, not until the tool
returns a job ID. Do not overlap canonical writes for the same bank/MR or bypass locks.
Revalidate live head/base, requirements, evidence and authority at the existing
write gates. Unknown/partial writes require reconciliation, not a second writer.

**Shared render resources.** Serialize builds/exports using the same catalog project
or generated-output tree, and tests using the same emulator/device. Distinct sessions
or PNG output directories do not isolate the default `REPO/icon-catalog` build.
Parallel rendering requires genuinely separate catalog/build/output roots and device
resources where applicable; otherwise queue only that stage, not the whole bank.
Independent SVG reference renders into owned directories may run in parallel.

**Merge and completion.** Preliminary read-only review of different MRs may overlap.
For one target branch, fresh merge preparation → merge → state publication stays
sequential; do not prebuild packages against a base another merge will change.
Publish each ready bank's result promptly under its actual scope and run its
state-check. Before finishing the task, account for every selected object and join
or explicitly stop all owned workers; a launched job is not a completed result.

## Interpret requirements without an intermediate state publication

For rework, read `taskInput.messages` / workflow next discussion. `task-review`
accepts an agent-written notes object with `action`, `requirements`, `rationale`
and optional nullable `selectedUrl`. Actions: repair, verify-applicability,
keep-current, defer, unclear. Each requirement has exact `url` and `quote` from an
actual message. Multiple requirements in one message should be kept in one full
quote rather than dropped. No source or comment supplies publication permission.

```json
{
  "action": "repair",
  "requirements": [{"url": "ACTUAL_COMMENT_URL", "quote": "ACTUAL_REVIEWER_WORDS"}],
  "rationale": "Explain the required repair, without declaring it already verified.",
  "selectedUrl": null
}
```

These placeholders must be replaced with actual observations. Read the complete
thread; do not infer keep-current from closure, missing reason, or bot text.
The CLI checks actual editor and write/maintain/admin rights; body edits are not
attributed to the original author. Already saved reasons can inform interpretation,
but changed live messages take precedence. Task comparison uses normalized text;
source receipts retain raw body bytes and both hashes. Trailing whitespace does
not invalidate initial attribution; any edit to a bound source still blocks stale
publication. No-questions means use existing answers,
not ask for them again. Preserve conflict as unclear and continue other objects.

```sh
python3 "$CLI" task-review --session "$SESSION" --id "$ID" --notes "$NOTES"
```

Repair/applicability continues directly to new research; reason publication is not
an entry gate. Keep-current/defer/unclear creates a no-search result for ordinary
state publication, without a fake attempt or lastVerifiedAt. Lifecycle remarks are
reviewer claims, not newly confirmed bank metadata or permission to use a successor.

Candidate notes add `taskResponse`: one exact url/quote plus a nonempty actual
`resolution` for each requirement. Explain what the new candidate changes; the
normal source/fidelity/render/admission checks still establish exact-byte evidence.
The published comparison includes the source MR and these resolutions. Identical
rejected final bytes cannot be passed off as a technical repair.

When the reviewer explicitly selected an attachment, select its actually observed
URL in `selectedUrl`. Retrieve it first. Raster is a reference, never traced/embedded
as a replacement. Use candidate-view `--feedback-retrieval FILE`, inspect the full
reference/final pairs, then candidate-build `--feedback-view FILE` plus actual
`feedbackJudgement`. This additional gate applies to both closed and open rework;
it does not replace original-vector/final comparison or permit another variant.

## Result, idempotency and state

State preserves old records and adds taskResults (input key, source MR/review,
action/result and actual timestamps), plus a proposal taskKey/supersedesMR as needed.
Read-reason is not attempted-repair. Unchanged significant input already published,
kept/deferred/unclear is not processed repeatedly; new reviewer text changes its key.
Generated comparison and result comments do not generate tasks. Unsuccessful searches use the
existing retry intervals; explicitly requested rework may be assessed again but
cannot duplicate a published proposal. Current active MR ownership always wins.

Prefer a single final `state-prepare` / authorized `state-publish` per object. A
needed interruption may publish raw pending or prepared stages too; only confirmed
own earlier stages can advance, never another writer's state. Existing outcome
facts relevant to this object and its reason are included with the actual result.
Run workflow state-check for each finished object. Other planned banks may already
be researching or preparing in parallel; do not wait for them to save a ready result.
No-op/inherited stops do not claim research. Creation, acceptance and current-byte
verification remain separate facts; no migrated timestamp becomes a fresh check.

Use [existing state/asset authority](standing-authorization.md#agent-behavior).
Existing grants do not expand to revisions or merge. Closed rework creates a new
linked PR; open rework appends to the same head with its existing explicit revision
scope. The original title/body remain human-owned. Recheck mutable requirements,
head, target and authority immediately before writes, not only when planning.

## Rework result comments

For `rework-rejected` and `rework-changes`, when actual work ends without a
published new/revised icon, the coordinator posts one short **conversation comment
on the source MR** using the available GitHub comment tool. This includes failed
search, an unsuitable/unverifiable candidate, a preparation/validation error, and
keep-current/defer/unclear decisions. For a no-search decision say that no search
was performed. Skipped/already-processed objects need no new comment. Do not post
an interim failure while workers or publication are still running; reconcile an
unknown asset write before claiming that no change was published.

Use the recorded result: what was checked, the concrete blocker, which reviewer
requirement remains unresolved, and what would enable another attempt if known.
Distinguish "not found", "could not verify/access", and "found but failed checks".
Use the discussion's language. No local paths, credentials, raw logs/state or
unnecessary mentions; no reopened PR, review submission, title/body edits or merge.
Commenting requires [rework publication scope](standing-authorization.md#one-time-operator-policy);
state-only/local-only permission does not cover it. Do not ask again when covered.

Keep the normal state publication/check; publish state first when possible. A
comment is a notification, not durable state or a new approval stage. If state is
blocked, disclose that rather than claiming it was saved remotely. A comment error
must not undo state, repeat research or invalidate a confirmed icon publication.
Report its URL, or its specific failure/permission blocker, in the final response.

Wrap the entire generated notice in this distinct block. Copy `KEY` and
`PROCESSED_AT` literally from this result's `taskResults` entry (`key`, `processedAt`),
not the current clock, a new hash or a successful-revision marker:

```html
<!-- bank-icon-task-result:KEY:PROCESSED_AT:begin -->
Recorded rework result: no new/revised icon published.
Brief actual findings, blocker, unresolved requirement and useful next step.
<!-- bank-icon-task-result:KEY:PROCESSED_AT:end -->
```

Before sending, read the source MR's conversation comments, including needed pages.
The same key/time already present means reuse its URL, not post again or overwrite
it. A new actual attempt has its own recorded time; resuming one does not. If the
lookup is incomplete or ambiguous, report that instead of guessing absence. After
an uncertain send, check read-only for this exact marker; never blindly resend.
Only complete matching blocks are excluded from reviewer input; raw source text
and human remarks outside the block remain intact. No extra state schema or
publication package is needed for this notification.

## Finish a stopped candidate

`pending` means unfinished candidate work, not a terminal failure or human approval
request. It may be published as an interruption checkpoint. On normal task completion,
either publish the checked MR/revision or close the **known-unpublished** candidate:

```sh
python3 "$CLI" task-finish --session "$SESSION" --id "$ID" \
  --outcome error --stage admission --reason 'Actual observed technical blocker' \
  --evidence "$ADMISSION_JSON"
```

Choose the real outcome: `error` for a tool/access/build/validator problem;
`unconfirmed` when no acceptable supported candidate could be prepared; `needs-human`
only for an actual identity/design/product question requiring a person. A known
bad image or missing vector does not by itself require a human choice. Stages are
preparation, render, assessment, admission, publication. `--evidence` may be repeated
for existing files beneath this bank's evidence directory; include the blocked
admission or actual failure output, not invented diagnostics. The reason describes
the final stop, not the earlier reason for proposing a replacement.

The command validates the existing candidate chain, saves an append-only completion,
retains the candidate under `retiredLocalCandidates`, updates taskResults and the
last-attempt outcome/time, and leaves verification dates unchanged. It creates no
MR, grants no permission and does not publish state. An unsuccessful same-MR repair
restores its original open proposal; it never closes/rejects the human MR. Then use
ordinary state-prepare/show/publish and state-check. Rework also emits its normal
result comment. Do not hand-edit state or rewrite evidence.

Confirmed outputs, partial refs, changed heads, missing receipts/packages and unknown
create/comment results cannot be retired as "nothing published". Use existing
read-only reconciliation first. Permission or integrity stops remain stops; retain
an interruption checkpoint when finalization itself cannot be proved safely.

Normal update/add selection retries finished `error` after retryErrorAfterDays and
`unconfirmed` after retryUnconfirmedAfterDays; no new pending interval exists.
Unchanged rework `needs-human` is not reselected until reviewer input changes.
An abandoned pending checkpoint uses the error interval before the existing no-output
audit; no immediate re-search loop. Explicit requested work can bypass timing only,
never evidence, MR or unknown-write guards. Selection order/finite limits are unchanged.

While the same unfinished session survives, continue at the blocked stage rather
than repeating research; an admission concern may be closed with new evidence-bound
admission. A new attempt uses recorded stop/source hints from the work order. If
local files disappeared, retrieve sources and recheck; old hashes/paths do not prove
that bytes are available. No permanent archive of renderer sessions is required.

State-check still reports whether the recorded result is remotely saved. It also
returns `taskComplete` and `pendingIds`: state-complete with taskComplete=false means
an unfinished checkpoint, not a completed finite task. Finish/recover it or explicitly
report the remaining blocker before ending; never claim publication succeeded.

## Independent recovery, not archiving sessions

Before task asset writes and approved merges, a compact immutable manifest is saved
under the serviced repository's Git common directory:
`bank-icon-maintenance/operations/{asset|merge}-HASH/`. Keep this small directory
with the checkout/cache; it contains no renderer trees and is not another bank
state database. Old path strings are historical data, not dependencies.

```sh
python3 "$CLI" task-recover --operation "$OPERATION"
python3 "$CLI" merge-reconcile --operation "$OPERATION"
```

These commands read exact provider/Git facts, never repeat asset or merge writes.
Confirmed asset publication reconstructs an ordinary state package from its compact
manifest even after deleting SESSION. Partial refs, missing/uncertain comparison
comments or changed output stop; do not create another MR. Terminal asset recovery
uses a task-scoped provider/Git outcome check rather than fabricated open state.
A confirmed merge with failed state publication remains merged; retry only the
state recovery. Newer bank-state changes are preserved as conflicts, never overwritten.

For truly old local-only pending candidates without an intent, a bounded audit checks
open proposals and remote heads for those candidate bytes. Only proven no-output
candidates may be retired under retained history and replaced by fresh evidence.
A found/ambiguous/too-large output set needs explicit recovery, not guessed absence.
Existing original-session reconcile is still useful when its exact files survive.

## Approved merge

Use `merge-list` or explicit MR numbers, then `merge-prepare` with the requested
method (merge/squash/rebase). Preparation checks exact one-bank diff and metadata,
current trusted human approval of this head, no current change requests, draft,
CI rollup and clean merge status. Historical approval alone is insufficient. Missing
checks are not a claim that render tests ran; repository rules still govern writes.
No admin bypass, automatic conflict edits, or another icon search is performed.

`merge-publish` requires an exact package grant (scope approved-merge, packageHash,
target, base, id, number, headCommit, method, authorizer, statement, authorizedAt),
or an external owner policy with actions.mergeApproved true and a merge object
{target, base, methods}. State authority remains separate. The agent must not edit
its policy to gain this scope. Explicit matching permission is not asked twice.

The API request binds the expected head. A saved pre-write receipt makes retries
read-only. Only an observed merge with exact accepted bytes in main ancestry is
recorded accepted; queued/unknown/blocked is not merged. State records merge facts,
not new search/render dates, and does not require old renderer files. Publish the
returned state package with `--session OPERATION`. Preserve successful merge even
when state sync fails, and report both outcomes separately.
