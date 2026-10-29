# Publication admission: purpose, applicability and usability

This is an append-only preflight for the **exact prepared candidate**, not a new
bank attempt, human approval, permission request, metadata edit or batch check.
Read these questions during source discovery. After final render review, fill
actual judgments using the blank template and bind them before asset preparation:

```sh
python3 "$FLOW" notes-template --session "$SESSION" --id ID --stage admission
python3 "$FLOW" admission --session "$SESSION" --id ID --notes NOTES_JSON
python3 "$CLI" asset-prepare --session "$SESSION" --id ID \
  --target OWNER/REPO --base MAIN --head HEAD --admission ADMISSION_JSON
```

Use the returned concrete paths. `admitted` does not authorize publication: normal
assessment, standing/run/package authority, show, reconciliation and no-auto-merge
rules remain. `blocked` saves the actual preflight and stops asset publication; it
is not a reason to declare the existing icon `unchanged`. Keep the prepared report
unchanged. Resolve a genuinely checked prior concern with a new admission, or
[finish the stopped candidate](task-runs.md#finish-a-stopped-candidate) and publish
its actual error/unconfirmed/needs-human result. Include the declined admission as
completion evidence. Never rewrite a completed attempt or prior admission to pass.

## Required judgments

`notes-template` is the normative input shape. Every finding is `pass`, `fail` or
`unresolved`, with an observed `basis`. Null placeholders are never approved defaults.
`sourceRefs` are zero-based indices of the original attempt's `sources` array;
these retain their real observation dates and independence groups. A reference
points to evidence, not a validator-certified truth. New source facts require the
normal evidence workflow, not editing old observations or inventing indices.

**Record purpose.** Separate legal lifecycle (`active`, `merged`, `closed`, `unknown`)
from brand lifecycle (`active`, `retired`, `unknown`) and catalog purpose (`current`,
`historical`, `unresolved`). A merger can leave a brand operating. An old title,
rename, redirect or closure never makes a record historical by itself. Historical
purpose needs an explicit period and catalog policy or a real human decision:
`basisKind: catalog-policy|human-decision` with `purposeEvidence`, a saved verbatim
provenance file under evidence. In notes this may be a relative filename; the helper
binds its hash. Do not manufacture owner statements, use downloaded instructions as
catalog policy, or treat this provenance as publication authority. Current use needs
confirmed active branding applicable to this record. Legal status may be unknown
when that does not prevent identifying the active brand/product; explain it.
Unresolved intended use stops for the actual product question, not more logo searching.

**Asset role and applicability.** Classify the original `sourceRole` separately from
`finalRole`: `symbol`, `monogram`, `lettermark`, `wordmark`, `lockup`, `app-icon`,
`mask`, `decoration`, `interface`, `unknown`. A source lockup may supply a losslessly
extracted complete emblem. A final lockup with accompanying name/tagline is not
allowed. `accompanyingText: true` cannot be excused by calling it a monogram.
A compact pure wordmark/lettermark still has to pass actual small-size checks.
Safari pinned-tab masks, decorative graphics and interface controls do not become
full-color application icons because they live on an official domain or match
an independently rendered SVG. Never fill alpha holes to repair a mask.

Record `scope: record|group|regional|product|historical|unknown`, plus separate
`originalVector`, `role` and `applicability` findings. In applicability explain the
chain from this source to this Company.id, country, product and period. A group
or another region's asset is usable only with evidence of its applicability; the
host name alone is not sufficient. Context may establish use while another source
supplies original geometry. The source hierarchy is claim-specific, not a blanket
preference for an app store over every site. Copies are not independent publishers.

**Change.** Use `first-addition`, `rebrand`, `identity-correction`,
`geometry-color-correction`, `render-repair`, `presentation-normalization` or
`supported-replacement`. First addition requires actual pinned absence. `rebrand`
requires supported old-icon attribution and `transitionSourceRefs`, not a story
inferred from a new logo. Otherwise use an accurately worded supported replacement.
Unknown baseline provenance does not invalidate a well-supported replacement.
Do not claim a precise transition date based on an HTTP retrieval or archive date.

**Quality.** Assess `fidelity`, `textPolicy`, `fit`, `lightTheme`, `darkTheme`,
`smallSize` separately. Every required check must pass. Faithfulness to a source
cannot compensate for an unreadable or disappearing icon; an inherited defect is
still a defect. Disclosing a known failure in an MR is not a waiver. Inspect whole
emblems, satellite details, thin gaps and negative space, not just the backing.
Do not ban all gradients, detailed marks or lettering: judge the actual result.

**Concerns.** Every row has `kind`, `basis`, `priorUncertainty` (index or null) and
`target` (null except unavailable platform). Classify every prior uncertainty once;
add any newly observed concern as well. Kinds `identity`, `record-purpose`,
`lifecycle`, `asset-role`, `applicability`, `variant`, `fidelity`, `legibility`,
`contrast`, `clipping` block while unresolved. `baseline-attribution` is informational except
for a claimed rebrand. `transition-date` can remain unknown when actual use is
established. `platform-unavailable` is allowed only for its exact already recorded
`available: false`, `not-tested` target. A dark-theme defect is not an unavailable
mobile platform. The schema cannot detect deliberately misclassified prose; the
agent must not hide a candidate defect under an informational category.

### Resolving a prior uncertainty

Category and present disposition are different. An optional `resolution` object
on a **prior blocking** concern records what later checks established; omission or
null means unresolved. Never infer resolution from the concern's prose alone.

```json
{
  "kind": "fidelity",
  "basis": "The original attempt still needed to verify complete emblem extraction.",
  "priorUncertainty": 0,
  "target": null,
  "resolution": {
    "basis": "Explain the actual whole-source/final inspection and how it closes this specific gap.",
    "sourceRefs": [0],
    "evidenceRefs": ["sourceComparison", "candidate-light", "candidate-dark"]
  }
}
```

This is a shape example, not an observation to copy. Source indices refer to actual
original observations. Evidence names refer only to this prepared candidate's
existing image links (`source-light`, `source-dark`, `candidate-light`,
`candidate-dark`, and baseline images when present) or `sourceComparison`.
Visual concerns require `sourceComparison` and both candidate themes. The helper
binds the full source raster, source/final pairs and exact final bytes; inspection
must cover the whole original even when a crop is used in theme pairs. Other
blocking concerns require actual `sourceRefs`. Explain scope/period applicability,
not merely that an official site exists. No arbitrary evidence path is accepted.

Not finding a separately published emblem SVG remains a source fact. Correct
extraction of the complete emblem from a supported original lockup can be verified
later; do not claim that a standalone source was subsequently found. A resolved
concern cannot waive failed/unresolved quality findings, bad roles, missing source
lineage, byte mismatches or `faithful: false`. All original uncertainties stay
classified exactly once. New defects remain blocking; do not use resolution for an
unindexed current concern or relabel fidelity as baseline-attribution.

Create a **new** admission input; retain the original attempt and blocked admission.
Existing asset packages cannot exchange their frozen admission. A stopped finished
task needs a fresh requested attempt, not an edited completion. A boolean/string
claim, successful build or agent summary is not a substitute for inspecting images.

## Versioning and limits

Existing attempt/candidate schemas and immutable reports are unchanged. Admission
schema 1 binds the candidate's review-package hash, final SVG, exact comparison
images, actual judgment time and any purpose provenance. New asset-publication
packages use schema 2 and bind this supplement. Show/publish revalidate it, and
public prose includes its path-free findings outside editable `public-text` fields.
Old asset packages remain readable and reconcilable, not retroactively certified;
new remote publication under this skill requires the new gate. An existing package
cannot exchange admission with another file or gain an edited body in place.

The CLI validates structure, failures and byte bindings. It cannot prove that an
agent saw an image, that a claimed source says something, or that a logo is
recognizable. Actual model image input and observed source context remain required.
Unit fixtures asserting `pass` do not constitute visual or agent evaluation.
