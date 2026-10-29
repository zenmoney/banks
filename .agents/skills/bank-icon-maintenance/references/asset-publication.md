# Candidate assessment and asset/MR publication

CLI and SESSION in command examples mean the concrete paths resolved by the
[skill bootstrap](bootstrap.md). Assign them within
the same shell invocation or substitute their values; they are not persistent
across agent tool calls.

Start from the immutable [candidate review package](candidate-review.md) for one
Company.id. The agent directly inspects the sources and exact rendered comparisons and may
assess a clear candidate as suitable for MR review. The person retains the final
MR/merge decision. Local preparation, publication authority, and visual judgment
are separate facts; JSON, a file quote, or a website cannot supply a missing
decision. Before calling the provider, check actual sources, comparisons,
and available renderers; unavailable Android/iOS/native checks remain `not-tested`.

New packages also require the append-only [admission preflight](admission.md).
Pass `--admission FILE` returned by `workflow.py admission` to `asset-prepare`.
Asset schema 2 freezes the preflight and its evidence bindings. Existing schema 1
packages remain readable/reconcilable; they are not newly publishable without the gate.
A semantic or usability failure cannot be waived by suitable/approve or public prose.

## 1. Record the agent assessment or a specific human decision

Actually inspect the source/final SVG, any base SVG, exact diff, both theme images,
sources, uncertainties, Desktop XML observation, and platform limitations. For a
clear faithful candidate, apply the [background/fit policy](icon-background.md):
check emblem fidelity separately from the disclosed circle/fallback presentation.
A circle cannot justify clipped details or an unreadably small mark. Record your
own visual/source assessment under
`SESSION/evidence/` and continue:

```sh
python3 "$CLI" candidate-assess --session "$SESSION" --id ID \
  --assessment "$SESSION"/evidence/ID/assessment.json
```

The schemaVersion 1 assessment has `scope: candidate-assessment`,
`decision: suitable`, `id`, exact `baseCommit`, `baseIconHash` (null for an addition),
`candidateIconHash`, `reviewPackage: {path, sha256}`, `assessor`, `statement`,
`assessedAt` (UTC Z), and `provenance: {path, sha256}` linking the actual agent
visual notes under SESSION/evidence. The command binds the prepared version and
records `agentVisualAssessment: suitable` with an `agentAssessment` link;
`humanReview` remains pending. A record is not evidence of an image you did not
open or a source you did not inspect. Ambiguity, source conflict, distorted detail,
or a prior rejection needs the human decision specified by its stop/outcome
contract; self-assessment never clears it.

Alternatively, before any agent assessment, the existing per-candidate human
approval path remains available when the person directly approves the exact
candidate. These are mutually exclusive pre-MR paths: after `candidate-assess`,
`candidate-approve` does not record a later human decision. The person's final
decision is through MR review/merge, not a second candidate approval receipt.
This is distinct from run publication authority:

```sh
python3 "$CLI" candidate-approve --session "$SESSION" --id ID \
  --approval "$SESSION"/evidence/ID/approval.json
```

The receipt has `schemaVersion: 1`, `scope: candidate-review`, `decision: approve`,
`id`, original `baseCommit`/`baseIconHash`, exact `candidateIconHash`,
`reviewPackage: {path, sha256}` from the prepared proposal, actual `reviewer`,
`statement`, `reviewedAt` (UTC `Z`, after candidate verification), and
`provenance: {path, sha256}` linking a nonempty record of the person's words under
SESSION/evidence. The quote is provenance, not authority by itself; do not invent
the statement, time, or decision. The command validates the chain, writes
content-addressed approval/report records, and sets `humanReview: approved`;
`status: pending`, the accepted icon, and `lastVerifiedAt` remain unchanged.
A requested rejection rework reads its actual discussion through [task commands](task-runs.md).

## 2. Prepare a local package without publication

```sh
python3 "$CLI" asset-prepare --session "$SESSION" --id ID \
  --target OWNER/REPO --base MAIN_BRANCH --head ONE_BANK_BRANCH
python3 "$CLI" asset-show --session "$SESSION" --package PACKAGE_SHA256
```

Only GitHub.com is supported: `OWNER/REPO` or exact
`https://github.com/OWNER/REPO`, with the same repository for base/head and MR.
`gh` needs access to the repository and all PR states. Each call checks the
provider and Git; the helper neither writes persistent Git config nor embeds
credentials in links. Provider safety/permission requests require separate
explicit human approval; automation must not bypass them.

Preparation reads fresh `TARGET/base`, comparing the **bank** with the frozen
session: exact SVG bytes or confirmed absence, exact info.json, unique ID, and
unchanged selected-bank base. Unrelated target commits are allowed; an appeared
icon or any bank change blocks. One asset commit adds/replaces only `banks/<bank>/icon.svg`.
No writes go to the original checkout/index, service branch, or detached
`SESSION/worktree`: its HEAD/base/icon stay unchanged. The local asset worktree
is under `SESSION/asset-publication/ID/worktree`.

A separate orphan reviewer-material commit lives at `refs/heads/<head>-review`,
not on the asset branch. Updates retain exactly seven files under `review-materials/ID/`:
`base.svg`, `source.svg`, `final.svg`, `candidate.diff`, `final.xml`,
`comparison-light.png`, and `comparison-dark.png`. Additions have exactly the six
other files, omit `base.svg`, and set `details.baseSha256: null`. Their public body
states pinned-base absence and labels exact final-theme images first addition, not
before/after. No invented base hash/link/panel is uploaded. Both modes require
genuine original vector SVG, fetched or losslessly extracted; traced/redrawn,
renamed or embedded raster is forbidden even if accurate. Base (when present),
source/final/diff/XML/images remain exact and hash-checked. Source is byte-preserved with
its original hash and extraction lineage where applicable. The separately prepared
`final.svg` must remain genuine vector artwork derived from that source through
source-preserving preparation/optimization, with only the documented plain-field
catalog-circle exception. The emblem must still come from the original vector;
this does not permit raster redraw, recoloring or inventing an official round variant.
Scripts check bytes and SVG structure,
not whether a vector was previously traced; the agent must judge provenance.
Historical packages/evidence stay immutable; readability is not new publication
eligibility. Full original screenshots,
logs, manifests, session/auth/
provenance JSON, and local paths are not published. The bounded text scan for local
paths/credential-like strings does not prove absence of every secret. PNGs are
not recropped or redrawn; hashes alone do not establish their content or renderer.
Before publication, the agent must inspect every exact commit file, including
SVG comments, XML/diff, PNG pixels, metadata, and private-data exposure. Preserve
the human's opportunity to inspect the MR before merge; local checks cannot prove
private-hosted image visibility.

The new PR body is rebuilt from the structured report: name/ID/metadata, exact
versions, sources/asset URLs and dates, evidence independence,
identity/currency/variant, rationale, visible difference, transforms, actual
renderer, and honest limitations of all platforms. The old local `mrDraft` is
not copied. File and image links are pinned to the **material commit**, without
relative/file/local URLs; images use GitHub `blob/<commit>/...?raw=true` for an
authorized reviewer's private-repository access. Actual image visibility in a
private MR must be checked separately after publication: local packages and test
PNGs do not prove it. The body identifies and links the actual source filename
and original-vector provenance, distinguishing the preserved source SVG from the
prepared final vector SVG and describing actual transformations. Describe an eligible
round field as a catalog adaptation, not an official app icon or rebrand unless
those separate claims have evidence. A preserved noncircular field needs only its
actual fit/readability rationale, not new shape permission. Keep public file counts
unchanged; any resized diagnostic previews remain linked local evidence only.

### Local observations and public prose

Local evidence may name files under `evidence/`, `reports/`, `worktree/`, or
`asset-publication/`. Do not rewrite those immutable observations for the MR.
The exporter derives a separate public representation of narrative fields only:
exact references to validated public files become material-commit
GitHub links; other session-local references are labelled **local artifact not
published; reference omitted**. An independent source preview is not replaced
with a Compose image, nor a different asset with a matching basename. Unpublished
references are not opened or added to the upload set. Relative paths, `./` paths,
absolute paths beneath the session, and its `file:` URIs are supported; quote
paths containing spaces. No source finding, uncertainty, hash or date is renewed.
An ordinary HTTPS URL with `/wiki/File:...` in its path is not a local reference;
actual `file:` URIs must be projected or rejected before publication.

Metadata, identity fields, source URLs and artifact bytes are not sanitized.
Credential-like text and control characters are rejected before projection;
external paths, traversal, symlinks and changed hashes remain errors. Inspect the
resulting public body as well as all public files. Projection is deterministic;
path-free descriptions retain their previous bytes, so existing valid packages
do not change merely because the helper was updated.

Before a package exists, an optional `asset-prepare --public-text FILE` supplies
an editable public draft, separate from immutable evidence. FILE is a JSON object
mapping dotted field paths to nonempty replacement strings. Only existing narrative
leaves are editable; `N` is a zero-based array index:

- `sources.N.observation`, `sources.N.independenceGroup`
- `assessment.identity.basis`, `assessment.currency.basis`, `assessment.variant.basis`
- `assessment.independenceRationale`, `assessment.uncertainties.N`
- `candidate.rationale`, `candidate.role`, `candidate.variant`,
  `candidate.detailsJudgement`, `candidate.proportionsJudgement`,
  `candidate.visibleDiffExplanation`, `candidate.transforms.N`
- `visualComparison`, `platforms.N.observation`

For example, if this accurately preserves the inspected evidence, save as
`$SESSION/public-text.json`:

```json
{"sources.0.observation":"The official homepage header uses the selected SVG; site configuration references the same asset."}
```

```sh
python3 "$CLI" asset-prepare --session "$SESSION" --id ID \
  --target OWNER/REPO --base MAIN_BRANCH --head ONE_BANK_BRANCH \
  --public-text "$SESSION/public-text.json"
```

The agent rewrites wording only, preserving every fact, uncertainty and limitation;
never imply an unperformed check passed. IDs, URLs, dates, hashes, findings, statuses
and array membership cannot be changed or deleted through this map. Prefer prose
observations and full URLs in structured URL fields over raw HTML. The existing
scanner/projection still applies; there is no HTML parser or automatic semantic
judgment that a replacement means the same thing. Inspect the resulting body.

A scanner error names the offending field: use its dotted narrative path when
allowed, not the error message itself, as the map key. Correct only that public
wording without altering saved evidence. A structured URL/identity field or artifact
error is not repairable with public prose; resolve its actual cause. Do not erase
limitations or disguise credentials to make a scanner pass.

For an existing suitable candidate rejected **only** for public wording before any
package exists, reuse the same session, ID, target/base and intended head. After
successful preflight/resumption and checking that no package, refs or MR were
created, correct the draft and retry `asset-prepare`, then `asset-show`. No new
search/assessment or review is required solely for wording; do not edit saved
reports, SVGs, hashes or dates. Normal fresh-base, evidence and authority guards
still apply. Unknown remote outcomes require reconciliation, not this retry.
After a confirmed MR, prepare/publish the new service state under its separate
authority; do not repush the old pending-state package.

Optional edits are frozen as `publicText` inside the content-addressed publication
package. `asset-show` and `asset-publish` replay that map without rereading FILE;
changing the draft afterward cannot change the package. Preparing with a different
map when the package already exists is rejected. Do not replace a prepared package.


`asset-prepare` saves exact `SESSION/asset-publication/<packageHash>.json` and
returns `packageHash` and `package`. `asset-show` revalidates local
bindings and shows target/base/head, asset commit, material ref/commit, file list,
PR title/body, and diff. Show the **entire** package with its exact six/seven files;
local `worktreeGitDir` and bindings are for validation only, not the PR.
Previously prepared immutable packages are not rewritten to correct a misleading
source name: reuse must reject them, and a truthful package must be prepared
separately. Preparing the same session/ID again with another target/base/head is
forbidden. Neither preparation nor agent assessment or candidate visual approval
authorizes push or PR creation.

## 3. Publish under applicable authority

Use [publication authority](standing-authorization.md#agent-behavior) for both
refs and the PR. With `publicationAuthority.status: covered` and
`status: ready-to-publish`, use ordinary `asset-publish` without a new permission
question or approval file. Exact-package and run grants below are alternatives,
not prerequisites for covered standing publication. Existing receipt stops apply.
With no applicable authority, request the missing asset/MR-publication permission.

For a direct decision on this exact package, record the actual authorization:

```json
{
  "schemaVersion": 1,
  "scope": "asset-publication",
  "decision": "authorize",
  "packageHash": "SHA256 of exact displayed package bytes",
  "target": "OWNER/REPO",
  "id": 10,
  "base": "MAIN_BRANCH",
  "baseCommit": "exact target base SHA",
  "head": "ONE_BANK_BRANCH",
  "assetCommit": "exact local asset commit SHA",
  "materialRef": "refs/heads/ONE_BANK_BRANCH-review",
  "materialCommit": "exact orphan reviewer-material commit SHA",
  "reviewer": "person who directly authorized publication",
  "statement": "their actual decision about both refs and the PR",
  "authorizedAt": "actual UTC time with Z"
}
```

```sh
# Covered standing policy and ready-to-publish:
python3 "$CLI" asset-publish --session "$SESSION" --package PACKAGE_SHA256
# Alternative: actual authorization for this exact package:
python3 "$CLI" asset-publish --session "$SESSION" --package PACKAGE_SHA256 \
  --authorization AUTHORIZATION_FILE
```

Alternatively, use `--run-authorization RUN_AUTHORIZATION_FILE` instead
of `--authorization` on `asset-publish`. Its current scope and recording command are in
[task confirmation](standing-authorization.md#one-question-for-the-current-interactive-task). The asset target/base, selected ID and branch namespace must match the
recorded task permissions. This does not constitute candidate approval or permit
merge. A provider's interactive safety prompt still needs explicit approval; a
permission denial or unknown outcome is a stop, not a reason to bypass it.

For per-package authority, scope, package hash, repository, ID, base/head, and both
commits/refs must match. A new base or changed candidate needs a new package and
rechecked authority: an old per-package approval does not cover its new hash;
an in-scope run grant or standing policy can. After revalidation, the helper
performs ordinary non-force pushes of material ref,
then asset ref, then creates one bank's PR. Never merge automatically. GitHub.com
API requests use `gh`; provider rejection or an uncertain response is not consent.
Inspect the PR as a reviewer, including actual image visibility in a private
repository; do not claim successful display before observing it.

## 4. Partial publication and PR outcome

```sh
python3 "$CLI" asset-reconcile --session "$SESSION" --package PACKAGE_SHA256
```

Reconcile reads remote refs and PRs **in all states**, performs no remote writes,
and may update local receipts and confirmed MR facts. It compares repository/head,
exact commits, base, title, and body digest, not just the PR number. After partial
push, preserve material ref, asset ref, and local package. If push exits nonzero
and a subsequent read **confirms the exact ref is absent**, the receipt becomes
`publication-failed` with `pendingRef`, `exitCode`, and `error`: inspect Git/provider
failure and the safety/permission request; obtain direct interactive human approval
before an explicit retry. The helper does not bypass denial. If the remote outcome
remains unclear, retain `outcome-unknown` and establish facts through read-only
reconcile. If the exact ref reached the target but its response was lost, reconcile
confirms it rather than pushing again. Stop on conflict. Once a PR-creation attempt
is recorded, absence of an observable PR does **not** authorize a blind second
create. Complete confirmation yields `mr-confirmed`; `not-published` describes only
the current observation. Never force push or create a second PR for the same
repo/head while the outcome is unresolved.

A confirmed open PR sets `status: open` and `waiting-mr`, not current-icon freshness.
Observed closed/merged PR facts stop the bank at `needs-human`: acceptance,
rejection, reasons, and freshness require a separate
[task outcome/recovery](task-runs.md), not inference from PR status. Even
`merged` does not update `lastVerifiedAt`/`verifiedIconHash`. Confirmed MR facts
require a new [service-state package](state-publication.md) and separate
`state-publish` authorization. Asset/MR permission never authorizes state or vice versa.

Later `state-prepare`, and again immediately before `state-publish`, recheck saved
MR facts **now**, not merely against a local receipt: an all-state GitHub lookup
for exact repo/head must yield one PR with the same state, headCommit, title, and
body digest; asset/material refs must point to the same commits. Saved
`mr.baseCommit`/`observedAt` are the first confirmed historical observation:
unrelated target commits do not rewrite them. Current `PR.baseCommit` must equal
the **fresh** target base ref; for an open PR, selected-bank bytes/metadata/unique
ID are checked again. A new receipt may observe a newer base. Before the first
PR, asset apply/publish still require the **exact package base commit**. Mismatch
or unavailability stops state writes: run read-only `asset-reconcile`, show changed
facts, and prepare/show a new state package with rechecked state authority.

This local contract does not prove actual remote GitHub PR publication or private
image display. Isolated Git/controlled-provider checks prove mechanics; do not
label them full remote acceptance.

## Reviewer-selected amendments

A sourced queued request follows [reviewer feedback](review-feedback.md), reusing
all candidate and admission checks. Its commit appends to the exact existing head;
body/title stay human-owned, and current materials are posted once as a comment.
The unique material ref, expected-old lease, immutable revision context and
separate owner opt-in/exact package authority are checked by the same asset writer.
Ordinary new proposals still reject existing heads and duplicate bank PRs.
