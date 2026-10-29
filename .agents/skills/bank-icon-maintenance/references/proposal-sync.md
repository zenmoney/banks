# Bounded proposal outcome synchronization before a new queue

**Optional explicit audit**, not a maintenance startup step. New tasks inspect only
the relevant bank/PR through [task-runs](task-runs.md). Use this command only when
the person requests a broader synchronization of already-published proposal facts.
It does not start icon research, create MRs or merge anything. Pending/unknown writes
still require reconciliation; historical evidence and dates stay immutable.

## Input and lifetime

Resolve `CLI`, `REPO` and the new maintenance `SESSION` through the
[skill bootstrap](bootstrap.md). Derive a different,
still-nonexistent `SYNC` sibling, normally `${SESSION}-outcomes`, under the same
unique temporary parent. Do not create `SESSION` early. Rebind concrete paths in
each shell call; an existing sync directory is not overwritten. Use `--resume`
for a saved scan; do not allocate another directory to bypass incomplete work.

```sh
python3 "$CLI" state-prepare-outcomes --repo "$REPO" --session "$SYNC"
```

Use the same explicitly chosen `--remote TARGET` for this command and subsequent
`start`; otherwise both use origin's sole push URL. Pass the same chosen `--base`
when applicable; the default HEAD is frozen at initialization. No destination guessing.
Run `doctor --github` when needed for the read-only GitHub login preflight.
No source research, rendering, installation, or OS image viewer is used here.

The command reads `banks/state/<ID>.json` from the actual service branch. It checks
the latest active published proposals and latest rejected proposals with unknown
reasons, plus explicit rejection clarifications. Malformed/local-only histories
are reported separately. It reads
the exact PR number, repository, head/base, title and body digest. An open MR is
reported and left unchanged by outcome sync; there is no poll/sleep/retry-until-closed loop.
After this phase, [reviewer intake](review-feedback.md) can separately queue an
evidence-bearing revision without changing the original outcome observation.
Git reads are limited to 60 seconds per command; GitHub retains its API timeout.
Both are additionally clamped to the remaining shared read budget. A transport
failure is not a closed MR or proof of absent history.

No old `/tmp` directory is needed: persisted service state supplies the prior
proposal/verification facts, and Git supplies exact asset/merge/current objects.
The old `assetPublication` and evidence paths remain historical strings, not
local files to reconstruct or trust on the new host. The sync context is a new
non-maintenance `session.json` with `purpose: proposal-outcomes`; it has no bank
worktree and cannot be passed to `continue`, `desktop-export`, or `asset-publish`.
A state read failure retains diagnostics and raises; never silently select offline.
Explicitly offline/local-only work, preview-only requests and resumed maintenance
sessions skip this phase with a disclosure, rather than recalculating a saved queue.

## Batches, progress and continuation

Defaults are `--max-proposals 5` and `--max-seconds 180`. A call processes at most
five active/stopped proposal records and prepares at most one small state package.
Inactive state rows are skipped under the same time budget. The time budget is for
reads; atomic local checkpoint/package finalization still runs afterwards. Keep the
launcher command timeout above this budget (for example, its existing 300 seconds),
not equal to or shorter than it. Configurable ranges are 1..25 proposals and 1..240
seconds. These are synchronization limits, separate from icon research limits.

`progress.json` and flushed JSON progress events on stderr identify the bank/MR,
last operation, processed/total paths and elapsed seconds. Stdout remains the final
command JSON. Progress is diagnostic, not authority or proof of a completed check.
Completed per-bank observations are saved atomically and content-addressed under
`outcome-scan/rows/`; interruption only leaves the unfinished read to redo.

Inspect `packageHash`, `scanComplete`, `readyForSelection`, `remainingPaths` and
`unpackagedPaths` (already observed work not yet assigned to a batch). For a package,
show/publish/reconcile it through the normal state workflow with the SAME SYNC path.
Then invoke the returned `resumeCommand` argv, or equivalently:

```sh
python3 "$CLI" state-prepare-outcomes --repo "$REPO" --session "$SYNC" --resume
```

Retain explicit remote overrides. Resume uses the saved base and decisions; new
base/decision inputs cannot silently alter them. Each batch has an immutable
`outcome-contexts/batch-<hash>.json` with its own IDs. Earlier packages, contexts and
receipts are not rewritten. Existing old-format prepared packages remain valid.
Standing state coverage is checked for each exact batch; an exact/run grant does
not silently authorize different IDs or another package.

An unsettled or unknown publication is returned unchanged by resume: settle that
package first, never skip it. After a confirmed publication, resume verifies that
it is still provable in state history and advances the same pinned scan. Cached
terminal observations are revalidated against live MR, Git and bank facts before
any push; pre-push outcome validation has its own 180-second read bound. A changed
MR/bank still stops publication. A cached open observation grants no freshness and
keeps the existing exclusion; the next ordinary run can observe its later outcome.

`scanComplete` means all source paths were observed; pending agent assessments
or packages may still exist. `awaiting-review-decisions` requires assessment of
`reviewInput`, not another identical resume or a question to the owner. **Only `readyForSelection: true` permits feedback intake and the new queue.**
`status: incomplete` without a package is not `no-state-changes`. Follow continuation
while advancing the finite pinned work, never poll for an MR to change. With no
progress on a resumed call, retain diagnostics and report the actual slow read
rather than repeating until it happens to succeed. Final completion starts no new
scan, queue or background worker.

For an interrupted older skill without `outcome-scan/request.json`, `--resume`
refuses to invent a checkpoint. Preserve that old diagnostics directory and use
one new sibling sync path after updating the skill. Do not treat the unfinished
maintenance SESSION as an established bank queue. Partial synchronization never
creates verified dates, resets bank history, or permits selecting offline.

`publicationPolicy: not-configured` is independent of read performance. It does
not forbid read-only preparation, but autonomous publication still needs existing
state authority. The operator must pass the external policy path and reviewed hash
into the actual runtime, bound to the serviced checkout and destination (for a
production run, zenmoney/banks, not the skill-development repository). The agent
must not generate/re-pin its own authority; see [standing authority](standing-authorization.md).

## Outcomes and verification

| Observed fact | Saved effect |
| --- | --- |
| MR still open | No outcome state change; excluded from ordinary automatic research unless a separately sourced reviewer request queues a same-MR revision. |
| Merged reviewed SVG B, current SVG still B, same bank metadata | `accepted`; retain the stored candidate verification date/hash, not the merge or sync time. |
| Descendant amended head D changes only the selected SVG, or current SVG/metadata changed later | Record acceptance only after Git proof; do not transfer B's verification to different bytes/identity context. Normal current-version checks remain separate. |
| Closed without merge | `rejected`; no successful-check date is created. With no sourced reason, `rejectionReason: null` keeps the existing human-resolution stop. |
| Missing/contradictory provider facts, changed PR identity, unsafe Git history, duplicate bank ID | Preserve state, report the bank's stop; continue checking unrelated proposals. |

For merged MRs, the reviewed commit must have one parent and change only the exact
selected bank's `icon.svg`. Its base/final hashes must match published state.
Current PR head must descend from that reviewed commit; an amended head may
change only this SVG. The merge must be in target history, the merged SVG must
match that PR head, and merged bank metadata/ID must still match the reviewed
identity. The current snapshot and unique ID are checked independently. GitHub's
`refs/pull/<number>/head` is used, so deletion of the ordinary head branch does not
by itself require the old maintenance session. An unavailable PR ref still stops
verification; no bytes are invented.

A missing candidate verification records acceptance without adding a verified
date. Invalid verification hashes/times stop rather than producing freshness.
Existing verification and attempt dates are preserved unless a valid exact-byte
candidate check is carried over; a newer verification is never rolled backwards.

`mrHistory` preserves the original open observation. Terminal `mr` and `mrOutcome`
record provider facts. An additive `outcomeSync` receipt binds the prior
`sourceStateCommit`, observation time, provider outcome, and commit-addressed Git
proof. This is a portable outcome receipt, **not** new source/render evidence.
Existing schemaVersion 1 bank records remain readable. No migration of original
sessions/reports is performed; their historical pointers are not rewritten.

## Rejection reasons

Follow [closed-MR decision intake](rejection-review.md). Newly closed proposals and
persisted `rejected`/null reasons get a bounded discussion read, including plain
text comments, review submissions and inline comments. If there is candidate human
text, `awaiting-review-decisions` returns saved discussions and a blank assessment
array. The agent supplies action, exact source URLs/quotations and rationale via
`--resume --review-decisions FILE`; this is not an owner-approval pause. Preserve
all relevant requirements and do not infer intent from a close button alone.

`keep-current`, `repair`, `verify-applicability`, `defer` and `unclear` remain distinct.
A completed empty read records `no-comment`; a failed read is a stop, not absence.
Unknown reasons with unchanged assessed discussions do not create repeat writes.
Known reasons retain separate reconsideration/evidence gates, not automatic new MRs.
No new successful-check or attempt date is created by recording a decision.

Legacy callers may still provide `--decisions FILE` as an array of exact
`{id, mr: {number, url}, commentUrl, category, explanation}` objects, with
`category: identity/logo|technical|not-now` and an actual supported quotation.
The existing comment/proposal validation remains. Old packages are not rewritten.
A legacy reason provided after a scan was pinned requires a new context with that
specific new input; normal discovered review assessments use the saved context.

## Publish before selecting, then finish normally

The response includes `open`, `stops`, and, when there are actual transitions,
`packageHash` and the complete immutable state package. Show this summary even when
no package is needed. `absent` means history remains unknown, not known empty.
`no-origin` allows only the existing local behavior with a warning. Malformed or
unreachable proposals remain excluded/stopped; they are not silently discarded.

Use [publication authority](standing-authorization.md#agent-behavior) for the
state action, with **SYNC**, not SESSION. For `publicationAuthority.status: covered`
and `status: ready-to-publish`, show and publish without a new approval file:

```sh
python3 "$CLI" state-show --session "$SYNC" --package "$PACKAGE"
python3 "$CLI" state-publish --session "$SYNC" --package "$PACKAGE"
```

For an actual explicit grant, add `--authorization` or `--run-authorization`
as specified in the [state contract](state-publication.md). A run grant must bind
this sync context's path, baseCommit, IDs and state target/ref; another session's
run grant does not carry over. Standing coverage is checked for this context.
This state action grants no asset/merge permission and clears no receipt stop.

Before push the package is fully re-derived from the pinned remote before-state,
current provider facts and Git proof. Unrelated target commits are allowed only
as descendants with identical bank bytes/metadata. Changed affected state, MR,
comment, or current bank stops publication; never force-push or overwrite it.
Existing state transport handles concurrent unrelated writes, receipts, push
timeouts, `state-reconcile`, and safe local-ref synchronization. A confirmed push
with a localSync warning must not be repeated. Unknown outcomes need reconcile.

After `published` is confirmed, resume the same scan for its next batch. Only
`readyForSelection: true` after all batches settle permits feedback intake and
the new maintenance session, so selection fetches updated service state. A prepared but unpublished
package does not count as synchronized state. Do not proceed to a normal online
queue while that package's publication is pending/unknown. Report per-bank stops
and continue unrelated work once the valid package is settled.

Show the remote current snapshot from merged outcomes. Do not mistake an old local
`HEAD` for that accepted version: use the intended up-to-date committed base for
the new queue and re-run preflight if changing `--base`. Do not switch/reset the
source branch or override an explicitly requested historical base silently.

At the end of this new run, publish its applicable MR/status results and stop.
Do not wait for those new MRs. Their eventual outcomes belong to the next normal
online run. This phase does not recover **unpublished** MR facts, unfinished
candidate packages, or the full old renderer evidence needed for reconsideration;
keep unfinished original sessions and never delete them as part of synchronization.
