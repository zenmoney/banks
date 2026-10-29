# Mechanical workflow inputs

`FLOW` is the absolute `scripts/workflow.py` beside the opened skill. `SESSION`
is the canonical existing session. Bootstrap/selection still use `session.py`.
This helper assembles inputs and calls the existing validators/writers; it does
not research, judge images, approve publication, change the queue, or merge.

## Research and initial attempt

Create one concrete work order per selected bank, then give the returned file to
its researcher. It contains metadata, exact paths, an owned evidence directory,
fetch arguments and the result filename. No repository search is mandatory:

```sh
python3 "$FLOW" work-order --session "$SESSION" --id ID
python3 "$FLOW" fetch --session "$SESSION" --id ID --work-order ORDER_JSON --url HTTPS_URL
```

Use the computed per-bank limits and remainders in `researchBudget`; never assign
new caps or edit env/journals. Save receipts in the order's `findings.json`. Consume
ready results while other researchers continue; serialize shared-catalog exports
and session writes. The helper does not launch workers or create a second queue.

Write actual research judgments under `SESSION/evidence/`: `outcome`, `reason`,
`nextAction`, `sources`, `assessment`, `platforms` (a list), `recheckRequired`, and
`recheckRationale`, using the [source-check contract](current-icon-verification.md).
For a candidate, first establish an [original vector source](current-icon-verification.md#original-vector-source),
then add only `candidate: {sourceUrl, rationale}`. Do not add copied
ID/base/hash/path fields. Keep each actual source observation's `checkedAt`;
retrieval time is not a replacement for the date of a different observation.

```sh
python3 "$FLOW" attempt --session "$SESSION" --id ID --notes NOTES_JSON \
  --retrieval RECEIPT_JSON --record
```

The receipt binds the original downloaded resource, not a working SVG. For a
container or compressed resource, resolve the selected material before `attempt`:

```sh
python3 "$FLOW" source-material --session "$SESSION" --id ID --retrieval RECEIPT_JSON
python3 "$FLOW" source-material --session "$SESSION" --id ID --retrieval RECEIPT_JSON --member 'EXACT_LISTED_MEMBER'
```

The first command lists container members without choosing a logo. Repeat
`--member` for nested containers; compression layers are detected from bytes.
Pass the returned `material.json` as `attempt --material MATERIAL_JSON`; the
helper binds the resource and selected material without inventing a direct URL.
For a direct image the existing attempt command remains sufficient.

Comparison and MR packaging use the exact selected material, with its resource
hash and member lineage. Validation replays extraction; a saved command or hash
alone is not proof. Adaptation/optimization uses a separate working SVG. A transfer
or extraction receipt proves neither identity/currentness nor visual fidelity.
Unsupported decoding is a technical limitation, not a request for new permission.

This backend handles ZIP/TAR and gzip/bzip2/xz layers with installed Python's
standard library. It enforces input/expansion, entry-count, nesting, memory and
execution bounds; member names never become filesystem destinations. It does not
install tools, unpack all files, follow links, or run container contents. These
are decoding capabilities, not a promise of support for every archive or image.
Raster decoding, including ICO, is visual research support only, not permission
to trace/redraw a replacement or publish raster-backed candidate material.
Lossless extraction of an original SVG remains a valid replacement source.

For `unchanged` (present icon only), omit `--retrieval` and supply `--desktop-bundle BUNDLE_JSON` plus
`visualComparison` in the notes. Negative outcomes omit an unused bundle; no
render or successful check is invented. Without `--record`, assembly saves a new
input and returns the exact canonical command without changing state. `--record`
runs it once. Existing completed attempts and human-review transitions are unchanged.

## Candidate inspection, then assembly

Prepare only from an original vector and optimize a separate working SVG using
[SVGO](svg-optimization.md). Raster-backed saved attempts stay stopped with
immutable evidence and eligible durable state, not an invented vector or asset MR.
Export the chosen final bytes with [desktop-export](headless-export.md), then:

```sh
python3 "$FLOW" candidate-view --session "$SESSION" --id ID \
  --final FINAL_SVG --desktop-bundle BUNDLE_JSON
```

Open the full source raster and source/final pairs on both themes. Updates also
need base/final pairs; first additions use exact final-only frames labelled as such.
Pillow PNG/JPEG/WebP/ICO decoding remains research evidence only, not candidate
eligibility. The source is never rendered from `final.svg`. The source uses the same
240px canvas and 162px icon area as the catalog comparison, without stretching.
For a wide source lockup, an explicit `--crop CROP_JSON` may name a pixel `box`
`[left, top, right, bottom]` and source-supported `basis`. Coordinates refer to the
preserved full source raster. Inspect the full raster and selected region; a crop
does not justify removing a brand backing, changing geometry or inventing colors.
The [background/fit policy](icon-background.md) applies to the working candidate,
not to source extraction or the comparison crop. Keep the original backing visible
in source evidence; label any eligible circular catalog adaptation or fit fallback.

Supply `candidate` judgments from the [candidate contract](candidate-review.md):
`rationale`, `role`, `variant`, `transforms`, `detailsJudgement`,
`proportionsJudgement`, `faithful`, `visibleDiff`, `visibleDiffExplanation`.
Also supply `sourceJudgement` (original versus final), `visualComparison` (base
versus final for updates, explicit absence and final render quality for additions),
and actual `platforms`. No flag is automatic: conversion cannot approve a wrong gradient.
`next` and `notes-template` return separate `backgroundReview` prompts and a policy
link; these are guidance, not new notes-schema fields or completed assessments.
Use the existing fields for backing role, whole-emblem fit, small-size/theme
legibility, emblem fidelity and adaptation provenance. No blanket circular clipping.

```sh
python3 "$FLOW" candidate-build --session "$SESSION" --id ID \
  --view COMPARISON_JSON --notes CANDIDATE_NOTES_JSON --record
python3 "$FLOW" assessment --session "$SESSION" --id ID \
  --notes ASSESSMENT_NOTES_JSON --record
```

Assessment notes contain only `assessor`, `decision: suitable`, and the actual
`statement`. The helper binds the canonical review package; it creates no human
approval or publication authority. Candidate assembly derives exact metadata,
previous-report links, diff, local draft, hashes and the source/final binding.
It invokes the existing complete validator before any recording transition.

New receipt-backed candidates require their original-source comparison at later
revalidation too. Only current task inputs are supported; never rewrite immutable evidence. The helper will not relabel an old working SVG
as a downloaded source. An existing unprepared `waiting-proposal` bound to a
container can resolve its same saved receipt and pass `candidate-view --material
MATERIAL_JSON`. This appends material/reference evidence without rewriting the
attempt, choosing another resource, reopening completed attempts or publishing.
Other proposal stops and existing recovery contracts remain unchanged.

## Compact results and sequential publication

Wrap existing session commands to retain full stdout/stderr on disk and return
small summaries with status, stops, authority, references and output-file hashes:

```sh
python3 "$FLOW" run --session "$SESSION" -- asset-prepare --id ID --target OWNER/REPO --base MAIN --head HEAD
python3 "$FLOW" run --session "$SESSION" --full -- asset-show --package HASH
```

For agent-reworded public prose, append `--public-text "$SESSION/public-text.json"`
to `asset-prepare`; see the [allowed fields and example](asset-publication.md#local-observations-and-public-prose).
Keep this editable pre-prepare draft separate from immutable reports/evidence.
Use prose observations, with full URLs in structured URL fields, not raw HTML.
The scanner does not judge meaning: preserve all facts, uncertainties and untested
platforms. Correct a reported narrative field and retry in the same session only
before a package exists, without re-review solely for wording. Normal guards still
apply. Prepared `publicText` is frozen; show/publish never reread the draft, and
preparing a different map cannot replace the package.

Use `--full` for package review, or read the exact `resultFile`; a summary is not
the displayed package. `--session` is supplied by the wrapper. It performs one
synchronous canonical call with bounded execution and no automatic retry. A shell
reporting a background job is not completion: wait for that job's terminal result
before another write. The canonical writer lock and unknown-outcome reconciliation
remain in force; no force push, policy creation, or interactive-prompt bypass.

After showing the exact packages and checking [authority](standing-authorization.md#agent-behavior):

```sh
python3 "$FLOW" publish-assets --session "$SESSION" --package HASH1 --package HASH2
```

This invokes canonical asset publication in order, stopping at the first result
other than confirmed MR publication. Only named packages are attempted. Standing
policy is checked by each existing writer; a real `--run-authorization FILE` can
be supplied instead. An exact `--authorization FILE` covers one named package.
Failed/unknown/denied results are not retried or moved to another worker. Prepare
and publish the separate state package through its normal authority, then check
all selected results below, including banks without an MR.

## Check state before completion

```sh
python3 "$FLOW" state-check --session "$SESSION"
```

Required before normal online completion. The helper checks every selected bank
through canonical `state-prepare`, which validates the saved result and reads
fresh remote state. It can create local packages/result files, but never pushes,
grants authority, changes attempt outcomes, or retries publication. `state-complete`
(exit 0) requires `no-state-changes` for every selected ID; an empty approved queue
also completes. An older published package cannot cover a newer local result.
`taskComplete: false` with nonempty `pendingIds` means the saved result is only an
unfinished candidate checkpoint. Continue it or use [task-finish](task-runs.md#finish-a-stopped-candidate)
for its actual terminal stop, then publish/check again. State-complete is not an
assertion that an icon changed or a candidate passed admission.

`state-incomplete` (exit 1) retains per-bank results and full command output.
Publish outstanding valid packages only after showing them and checking state
authority, then repeat the check. Missing results must be recorded first;
validation, conflict, permission, and transport stops require their normal
resolution. Unknown writes still require `state-reconcile`, never a blind push.
A confirmed cache-sync warning alone does not require republishing.

Do not omit stopped/no-MR banks or supply a local-only override to make the check
pass. If the person explicitly requested local-only work, report it as such, not
`state-complete`. A genuine blocker ends as blocked with affected IDs and the risk
that a new queue sees old state. This check proves state durability, not candidate
quality or completion of the asset/MR path.

## Android readiness

Use the exact installed SDK requirements and the explicitly selected permitted
path; do not auto-select a connected personal device:

```sh
python3 ANDROID_READINESS_PY --sdk-root SDK --platform android-N --build-tools VERSION --serial SERIAL
# Alternative: preparing a new AVD from an already installed compatible image.
python3 ANDROID_READINESS_PY --sdk-root SDK --platform android-N --build-tools VERSION --android-cli /absolute/path/to/android-cli --system-image 'system-images;android-N;default;ABI'
```

`ANDROID_READINESS_PY` is `scripts/android_readiness.py` beside `FLOW`. Existing
serials do not require Android CLI/emulator/image files for a new AVD. The new-AVD
path resolves `android` from `PATH` by default; `--android-cli` selects the actual
installed binary rather than an SDK bootstrap launcher. Use `tools.androidCli`
for lifecycle commands. File presence does not prove bootstrap-free execution or
offline creation; follow the [CLI constraints](headless-runtime.md#disposable-android-avd-lifecycle).
`tools-ready`
is not build-cache readiness, device-use permission or a render pass: use the
required offline build with SDK auto-download disabled and actual UI capture.
Missing prerequisites skip both Android targets as not-tested; preserve known
failures/rechecks. No installation or installation-approval question is introduced.

The helper's HTTPS checks do not intercept unrelated OMP/browser tool calls.
The operator's adapter must reject TLS/sandbox-bypass options. Headless browsers
remain available under the existing [runtime contract](headless-runtime.md).

## Next action and blank notes

`workflow.py next --session SESSION --id ID` reads the existing canonical point,
validates prepared candidates through the normal reader, and returns `commands`,
`guides`, `requiredInputs` and available `images`. Commands with `runnable: false`
contain explicitly unresolved inputs; do not execute their placeholders. This
read-only projection does not replace the state machine or infer publication
permission. Stops retain their exact reason. An old remote pending proposal without
its original local evidence requires the original session, not a new proposal.

`workflow.py notes-template --session SESSION --id ID --stage attempt|candidate|assessment`
creates a fresh notes file for the current stage. Mechanical fields are excluded;
judgments remain null/empty until actually observed. The resulting template alone
cannot pass validation. Do not manufacture missing source or visual assessments.

`candidate-view` returns `baseline.present`, `baseline.sha256`, and `finalFrames`
for both themes. First additions have `baseline.present:false`, null base hash and
null `baseFinal` entries, but actual final frames and independent `sourceFinal`
images. Open the full source plus all relevant images, not only a path list.

Research now uses the [per-bank budget and provider-neutral plan](research.md).
Known-URL fetches are receipt-cached; native tools need the documented adapter hooks.

## Mandatory admission before new asset preparation

Read [admission](admission.md) during research. After preparing the exact candidate,
use `notes-template --stage admission`, fill actual purpose/role/applicability,
change and separate quality findings, then run `workflow.py admission --notes FILE`.
The helper binds hashes and images; use the returned `admission.path` with
`asset-prepare --admission FILE`. A blocked result is preserved, not rewritten to
suitable or unchanged. Admission is not a new human-approval question.

## Reviewer-request inputs

When `workflow next` or `work-order` returns `reviewerFeedback`, follow its
[stage guide](review-feedback.md). Fetch the selected URL first. Add
`--feedback-retrieval FILE` to `candidate-view`, inspect its full reviewer/final
pairs, then add the returned `--feedback-view FILE` to `candidate-build` and fill
the blank candidate `feedbackJudgement`. The concrete asset command retains the
original MR target/base/head. These inputs supplement, never replace, the normal
original-vector/final, accepted-base/final, renderer and admission evidence.

## Inherited rejection stops are not new attempts

[Closed-MR decisions](rejection-review.md#queue-and-completion) defines separate
blocked-bank diagnostics and exact no-op state validation for inherited rejection
stops. `inherited-stop-no-new-attempt` is durability, not research or verification.
