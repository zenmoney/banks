# Candidate preparation and review package

CLI and SESSION in command examples mean the concrete paths resolved by the
[skill bootstrap](bootstrap.md). Assign them within
the same shell invocation or substitute their values; they are not persistent
across agent tool calls.

Continue the specific `waiting-proposal` after a recorded `candidate` attempt.
Source, any accepted base, and final candidate are distinct roles. The helper checks
package consistency, not the truth of sources, visual judgments, or approval.

For final-candidate Desktop evidence use [offscreen export](headless-export.md):
exact base/final pairs for updates, actual final-only frames for first additions.
Keep source judgment, platform gates, assessment, and final human MR review separate.

## 1. Prepare without changing the accepted icon

A first addition uses the same workflow for an existing, valid `info.json` record.
`iconHash`/`baseIconHash` must be explicit null with confirmed pinned-path absence,
not omitted, empty, or unknown. Symlinks, directories, failed tree reads, changed
metadata, or an icon appearing at the frozen checkout/moved base/publication target
block; never overwrite an appeared file. Do not create a new bank or a dummy SVG.
Absence grants no exemption from original-vector provenance, identity, suitability,
optimization, exact final-byte/render checks, assessment, or publication authority.

Use the sources and fetched asset from the original attempt. Preserve the
supported source design of a suitable standalone symbol or app mark; color/monochrome;
internal transparency and meaningful backing. The [background/fit policy](icon-background.md)
first selects whether a backing is needed, then defines a same-color circle and
complete-emblem centering for retained eligible plain-field type 1.
Intentional off-center branding is not type 1; do not forcibly recenter it.
Official avatars support variant choice and observed scale, not a classification
or centering override. Keep scale source- and legibility-driven without fixed fill
percentages. Prepare the circle in the working SVG, not by assuming a later UI mask.
If type 1 cannot preserve full fit and readability, use another supported eligible
variant or stop, not a silent square fallback. Preserve meaningful contours and
follow the guide for other compositions; do not force a replacement to reproduce an
obsolete accepted design. The candidate needs its own confirmed identity,
currency, and catalog suitability; the old SVG need not have an `unchanged` result.
A supported update for the same bank is not a dispute solely because it differs
from the old icon. Unresolved source/identity/design conflicts or a parent/successor
substitution still require an explicit human decision; similarity alone is not proof.

Prepare a separate working SVG under `SESSION/evidence/<ID>/`. Choose its
`final.svg` bytes after the optimization step below. Accepted
`worktree/banks/**/icon.svg`, metadata, and folder names remain unchanged. Preserve
source details and proportions; neither crop nor distort the emblem to fit a square
or circle. Only uniform scale/translation of the whole emblem is allowed for placement.
Only an original vector source may supply replacement emblem geometry. The standard
catalog circle or verified background-only mask is the narrow presentation exception,
not a traced logo. A mask may remove only background; no emblem detail may be lost.
Keep contextual avatar evidence separate from full original source/final comparisons.
An observed avatar does not replace final SVG/XML/renderer checks. Apply the
[original-vector rule](current-icon-verification.md#original-vector-source): no
automatic tracing, manual tracing/redrawing of PNG/JPEG/WebP/ICO, or raster
embedding, even when visually accurate. Raster remains research evidence only.
Lossless SVG extraction from bundles and source-preserving optimization are allowed.
Gradients/masks from the original vector are acceptable only when the pinned
converter produces a correct result.
Invisible reserialization, lost detail, or inability to render faithfully stops
the candidate; preserve the accepted icon or absence and record the limitation.
For an update, verify the source-supported reason in exact before/after renders.
For an addition, `visibleDiffExplanation` explains absent-to-real-icon, not an
invented comparison or improvement over a blank. If an update shows no meaningful
improvement, do not manufacture `visibleDiff: true` or an MR merely for byte changes.
Retain stopped reports rather than claiming a verified no-update result.

### Optimize the working SVG

Run [SVGO optimization](svg-optimization.md) as the normal next step for a new
SVG candidate, before final verification. Use the repository's `svgo.config.mjs`
and the installed SVGO on a copy; keep the downloaded source and working
SVG unchanged. Compare the working and optimized versions, then choose the
smaller faithful, converter-compatible result. When optimization fails, regresses,
increases size, or is unavailable, retain the working version and record why.
Continue the normal final-byte checks for that version; optimization alone grants
no verification and creates no new approval pause. Already recorded candidate
packages retain their original bytes and evidence.

## 2. Verify the final bytes

Run [`desktop-export --candidate`](headless-export.md) for the exact final SVG.
It passes the selected session worktree, ID, and final path to the existing catalog.
The generator validates SVG safety before converting unchanged bytes; DTD/entities,
active content, external resources, and embedded raster remain disallowed.

`continue --candidate` does not run the generator: it verifies the saved
manifest/XML binding to final bytes. The actual safety/conversion run above is
mandatory agent work **before** the command, not an assertion made by input JSON.
Never hand-author the manifest or change its source hash to accept another SVG.

Inspect the actual offscreen Compose Desktop generated XML/painterResource PNGs
on both backgrounds. Updates compare source/base/final with the exact helper pair;
additions compare independent source/final plus both actual final frames, with no
blank/dummy panel. Preserve manifest/XML/pins/hashes/result/invocation evidence.
Apply the [catalog icon suitability rule](current-icon-verification.md#catalog-icon-suitability)
to the actual final renders: the standalone mark must fit a square cell and look
appropriate as an application icon on both backgrounds. Reject accompanying bank
names, slogans, or explanatory text, even when the full logo fits inside a square.
Monograms and letters forming the mark itself remain allowed. If unsuitable, stop
the proposal; official provenance or a successful render does not make it acceptable.
Assess emblem fidelity and background adaptation separately. Check complete fit,
clearances, alpha and useful size using the [fit/legibility checks](icon-background.md#3-check-complete-fit-and-useful-size).
Do not normalize the source image to conceal intentional background differences.
Supplemental small-size previews do not replace the exact Desktop/theme/platform evidence.

Follow the [headless execution policy](headless-runtime.md) for mobile checks.
Use a headless Android emulator/AVD, ADB interaction, and device-native screenshots
for available Android targets. Other platforms use available command-line runtimes
and runtime-native capture. Determine availability from the actual environment;
unavailable targets use `available: false`, `status: not-tested`, with the concrete
limitation and risk. Preserve existing exact-byte evidence and its actual dates.
Record `passed` only after the required render observation and evidence bindings;
known failures and unresolved required checks keep the candidate stopped. Changed
final SVG bytes require new safety, conversion, offscreen rendering, and actual
model image comparison.

## 3. Assemble one self-contained package

A local one-bank draft must be understandable without the worktree: unchanged
metadata, exact final version and base version or pinned absence, diff, date, URLs,
identity/currency/role, independence, visible difference, transformations, actual
renderer, and limitations. Save exact before/after images for updates or final-only
first-addition images on both backgrounds. Obtain independent source PNGs
in [headless image assessment](headless-runtime.md), labelled `librsvg source
reference`. Headless-browser captures supply separate source-page context. Pass
saved images directly to the model, preserving their bytes and actual renderer
labels. Historical captures retain their original dates and provenance.
This draft is not the published PR body: [asset publication](asset-publication.md)
**rebuilds** it from validated data with absolute commit-pinned GitHub links; local
paths and relative links are not published. Local reports and continuation points
remain separate from the PR description.

Before recording the package, read the draft as a reviewer without worktree
knowledge: check every requirement above, read image attachments through model
image input and text attachments as files, confirm matching
versions, and name the renderer actually used. The six mandatory identifiers
checked by the CLI do not prove completeness or a self-contained description.
If these conditions are unmet, keep the unfinished local material without calling
it ready for review or recording successful preparation.

Input `preparation.json`, schemaVersion 1, lives under `SESSION/evidence/`.
Every `{path, sha256}` link binds exact bytes; `path` is relative to **SESSION**.
Symlinks/traversal are forbidden. Only `priorReport` points into `reports/`; all
other files live under `evidence/`. Fields:

| Field | Contract |
| --- | --- |
| `schemaVersion`, `id`, `baseCommit`, `baseIconHash`, `metadataHash` | Version 1, existing Company.id, frozen commit, exact metadata hash and base icon hash or explicit null |
| `checkedAt` | UTC with `Z`, completion of candidate verification; not merge or an old attempt date |
| `priorReport`, `priorEvidence` | Hash links to canonical `reports/<ID>.json` and `evidence/<ID>/validated.json`; old documents remain unchanged |
| `candidate` | Object below |
| `desktop` | Observation below |
| `comparison` | Both themes have real PNG links `source`, `candidate`; updates also have real `base`, `beforeAfter`, additions have both explicitly null |
| `platforms` | One declaration each for `android-compose`, `android-native`, `ios-compose`, `ios-native` |
| `mrDraft` | UTF-8 draft with ID, name, final hash, source/asset URLs, and base hash or `No existing icon.svg at frozen base commit` for an addition |
| `diff` | Hash link to the exact UTF-8 unified diff of base and final |

`candidate`: `path`, `sha256`, `sourceUrl`, `sourceAssetUrl`, `rationale`, `role`,
`variant`, `transforms` (description list; explicitly state if no transformation
was needed; include the SVGO version/config hash, size change, and selected or
skipped result from the optimization notes), `detailsJudgement`, `proportionsJudgement`, `faithful: true`,
`visibleDiff: true`, `visibleDiffExplanation`. URLs must match the original
attempt's fetched asset. `faithful`/`visibleDiff` record agent judgments, **not**
human approval. Do not set them merely to pass a gate. For a permitted backing
adaptation, `faithful` requires both source-faithful emblem and policy-compliant
presentation, not a claim of identical source/final composition. Describe the
circle or fallback and all actual placement transforms in existing judgment fields.

`desktop`: the helper-generated offscreen object plus your `visualComparison`;
retain `baseObservation` (explicit null for an addition), `invocation`, `result`,
manifest/hash, and both real final PNG links. Never invent base renderer evidence.
The manifest binds the absolute final SVG path/hash, worktree, selected ID, exact
XML and pins. Historical `compose-desktop-vector-xml-painterResource` window
observations remain readable without retroactive relabeling.

Each platform row: `target`, `status`, `available` (bool), `observation`.
`passed` additionally requires `sourceSha256`, `xmlSha256`, `converter`, `compose`,
and hash links `buildEvidence` and `uiEvidence` (actual screenshot PNG).
Available but untested targets, `failed`, and stale hashes are rejected. Unavailable targets
use `available: false`, `status: not-tested`, with a concrete reason and risk.

Diff format: `difflib.unified_diff(base.decode('utf-8').splitlines(),
final.decode('utf-8').splitlines(), fromfile='base/<ID>/icon.svg',
tofile='candidate/<ID>/icon.svg', lineterm='')`, joined with `\n` and terminated
with `\n`. For an addition, the old lines are absent and `fromfile` is `/dev/null`.
Separate actual SVG hashes bind exact bytes, including trailing newlines.

## 4. Record and assess for MR review

```sh
python3 "$CLI" continue --session "$SESSION" \
  --candidate "$SESSION"/evidence/15362/preparation.json
```

`--candidate`, `--evidence`, and `--review` are mutually
exclusive. Resume a paused session or approve its selection in a separate command
first. Successful recording stays at `waiting-proposal`, `humanReview: pending`.
These values mean the proposal awaits final human MR review, not that the agent
must pause before its own assessment. Continue with candidate assessment and
local preparation; publish only under matching asset/MR authority. Pause only
when actually requested or when a concrete unresolved gate requires it.
Separate content-addressed JSON/SVG files go into `evidence/<ID>/candidate-<hash>.*`;
the report goes into `reports/<ID>-candidate-<packageHash>.json`. Original
attempt/report remain unchanged. Local state preserves the current icon's
`lastVerifiedAt`/`verifiedIconHash` as they were.

The same pending proposal receives `sourceCandidate` (original fetched asset),
`candidate`/`candidateIconHash` (final SVG), `reviewPackage: {path, sha256}`,
`verification: {checkedAt, sourceSha256, xmlSha256, converter, compose}`, and
`humanReview: pending`. The report contains theme comparisons, priorReport/priorEvidence,
reviewPackage, renderBinding, and provenance; `bank.reportSha256` binds its bytes.
This is local preparation, not service-state publication.

Every ordinary `continue` of a prepared proposal rechecks base, metadata, and the
entire linked package. Changed final/comparison/XML/manifest/report stops the bank
at `needs-human`: old verification does not apply to new bytes. If selected baseRef
now contains a different current icon/metadata, missing ID, or duplicate ID, verify
the new base and reassess replacement first. An unchanged bank does not lose
verification merely because of an unrelated implementation commit. Before
publishing an MR for a new version, prepare a new isolated session, package, and
review; do not edit old reports/session.json. Existing-MR changes use a fresh
[rework-changes task](task-runs.md), preserving its exact current head and reviewer
requirements. Historical evidence does not verify new bytes.

Inspect the exact final SVG, source, comparisons, diff, and draft yourself. Record
an agent assessment bound to this exact package via
[candidate assessment](asset-publication.md#1-record-the-agent-assessment-or-a-specific-human-decision).
A suitable assessment permits local asset preparation and an authorized MR before
human visual approval; the person retains final MR review and merge. A failed or
unsupported candidate must not be published. Use [task-finish](task-runs.md#finish-a-stopped-candidate)
with the actual unsuccessful outcome; reserve needs-human for a specific unresolved
human decision. The assessment does not clear another stop.
`continue --candidate` accepts no approval, applies no SVG, and performs no commit,
push, MR, or merge. For an agent assessment and separately authorized asset/MR
publication, use the [asset/MR contract](asset-publication.md). Deferred review
preserves the worktree and all materials without automatic deletion. State
publication has its own [contract and authorization](state-publication.md).
