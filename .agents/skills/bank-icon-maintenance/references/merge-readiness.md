# Approved merge: real reviews and explicit diagnostics

`merge-list`, `merge-prepare` and the immediate pre-write check share one readiness
predicate. A successfully read GraphQL `reviewDecision: null` is not a rejection or
an approval: inspect the actual paginated REST reviews. An omitted field, partial
GraphQL response, failed permission read or incomplete pagination is a stop, not
permission to assume approval. Do not patch scripts during maintenance.

Require at least one human approval of the exact current head with confirmed
write/maintain/admin access. Per reviewer, keep the latest decisive review;
`COMMENTED` and unsubmitted `PENDING` do not replace an approval or change request.
A dismissed/latest stale approval cannot revive an earlier one. Unresolved requests
for changes remain blockers. Explicit `REVIEW_REQUIRED` / `CHANGES_REQUESTED`,
draft, changed head/target branch, non-passing CI and definitive non-CLEAN states still block.
Only a complete `UNKNOWN` observation gets the bounded read-only wait below.
Missing aggregate status alone must not hide a real approval.

The read-only list returns `target`, `base`, `examined`, `remaining`, `limitReached`,
`selected`, `skipped` and `stops`. Every examined PR belongs to one of those three
lists. Drafts, other targets, non-icon diffs and no current approval have explicit
skip codes; API/permission errors and merge blockers are stops. `remaining` means
listed PRs not examined after reaching the finite selection limit, not extra work
that must be processed. Empty `selected` is not a claim that no approvals exist.

Process ONE PR at a time: fresh prepare → separately authorized merge → state.
Only then prepare the next PR against the newly observed target. Do not prepare
all packages in parallel or against the first base; a successful merge advances it. Selection
does not grant merge power or prove future readiness. Use an allowed repository
merge method (for example, squash when required); never change branch protection,
request repeated approval just to satisfy a tool bug, or use administrative bypass.
The existing expected-head SHA, unknown-write receipt and independent state writer
remain in force. Do not invent verification dates from a merge observation.

Sources: GitHub's [reviewDecision schema](https://docs.github.com/en/graphql/reference/objects#pullrequest)
and [paginated review API](https://docs.github.com/en/rest/pulls/reviews#list-reviews-for-a-pull-request).

## Current target versus the PR's base observation

PR REST `base.sha` and GraphQL `baseRefOid` are observations on a PR, not substitutes
for reading the target branch ref. They may differ from each other or from today's
`main`. Preserve the raw PR observation; never rewrite its historical SHA just to
pass a comparison. Readiness checks the exact head, target branch NAME, reviews and
CI. Preparation independently reads `git/ref/heads/BRANCH` and the advertised Git
ref, fetches that exact commit, and binds it as the package's top-level `baseCommit`.
`package.mr.baseCommit` remains the original PR observation, not the chosen target.

The existing one-bank baseline/metadata/identity proof is checked at the common
ancestor and at that actual target. Unrelated target advancements are therefore
allowed when preparing a NEW package; a changed selected bank, retargeted PR,
changed approved head or disagreeing API/Git ref still stops it. There is no rebase,
force push, edited receipt or reconstructed render approval.

Before saving a package, repeat readiness/approval and actual target-ref checks.
Immediately before a merge write, recheck the head, actual target and merge authority.
If the target advanced after preparation, prepare a NEW package; do not mutate the
old manifest or retry a prior unknown write. The REST merge request pins the head
SHA; it has no target-base compare-and-swap argument. These prechecks do not claim
an atomic lock against another person's GitHub write: GitHub still enforces its
current branch/rules/mergeability at the write boundary, without admin bypass.

Merge preparation and recovery reuse proposal sync's bounded snapshot fetch. Each
fetch has its own private ref, with `--no-write-fetch-head` and empty ref mapping;
concurrent local reads cannot consume another command's `FETCH_HEAD`, rewrite user
branches/tracking refs or depend on a shared checkout update. This isolation is not
permission to parallelize merges or skip per-object state completion.

See Git's [fetch options](https://git-scm.com/docs/git-fetch) and GitHub's
[reference endpoint](https://docs.github.com/en/rest/git/refs#get-a-reference) and
[merge endpoint](https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request).

## Bounded UNKNOWN readiness, not a retry of merge writes

GitHub may need to recalculate mergeability after the target advances. The shared
readiness predicate handles a complete `mergeStateStatus: UNKNOWN` inside the
same CLI call. The model must not add `sleep`, a background worker or a polling
loop. There is no unconditional delay after every merge and no new configuration
variables. The source constants in `approved_merge.py` define the policy:

- At most five complete readiness attempts, including the first.
- Delays before retries: 2, 4, 8, 8 seconds.
- One 30-second read deadline per readiness check, including API time and sleeps;
  the existing enclosing command deadline still wins if it expires sooner.

A fresh REST PR read on each attempt triggers/observes provider calculation; then
GraphQL, current-head human approvals and permissions are checked again. Missing
or partial fields, transport errors, CI blockers, draft, unresolved reviews or
other definite merge states are not retried. UNKNOWN without a real current-head
approval does not wait for somebody to approve it. Timeout before any complete
UNKNOWN observation is a read failure, not evidence of GitHub recalculation.

Pin the original PR/head and independently read target ref while waiting. Changed
head, body/identity, target name or target commit stops this check; never adopt
new commits or reset the timer to keep waiting. Refreshes of the PR's observational
base SHA alone do not rebind the actual target. Final preparation and pre-write
readiness also bind the already selected package's head and base. No package,
receipt, policy or bank state is changed by waiting. Existing Git/ref checks still
run before writing; these reads are not a server-side target CAS.

Progress is flushed JSON lines on stderr (`merge-readiness-progress`, MR, attempt,
status, elapsed time and next delay). Stdout contains only the final command JSON.
If CLEAN arrives within the limit, continue the ordinary authorized route; do not
merge merely because the wait ended. If UNKNOWN persists or its read deadline
expires, return `merge-readiness-pending`, `mergeAttempted: false`, attempts and
elapsed time. `merge-prepare`/`merge-publish` exit nonzero for this result; no merge
request or attempted-write receipt was produced. `merge-list` preserves this as a
pending entry in `stops` (not a conflict), and unexamined rows remain in `remaining`
if the enclosing budget expires during readiness. Do not report it as successfully prepared.

Mark the object pending and proceed only to the next object in the finite request.
Report pending items at completion; do not launch another queue or indefinitely
retry the same command. A future attempt rechecks current inputs normally.

If a merge request WAS sent and its response was lost, this readiness wait is NOT
used. Existing receipts go straight to read-only `merge-reconcile`, with no second
PUT and no false `mergeAttempted: false` claim. Authority remains separate.

See GitHub's [asynchronous mergeability calculation](https://docs.github.com/en/rest/pulls/pulls#get-a-pull-request).
