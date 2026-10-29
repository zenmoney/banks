# Background shape and emblem fit

**Emblem integrity → legibility → background consistency.** A circle is the standard
catalog backing for an eligible plain field, not a mandatory mask for every logo.
Canvas, official presentation and emblem geometry differ. Apply this to updates and first additions.

## 1. Classify source and actual presentation

Use the full original vector and observed source context. A square file, viewport
or full-bleed fill is not by itself a branded silhouette. Separate the emblem from
the backing by actual structure/context, not SVG tag names or canvas edges alone.

**Plain-field type 1** has a separable, uniform-color field (background) around the complete
emblem, which does not reach the canvas edges. Uniform color describes the background,
not the emblem: a multicolored emblem may qualify. A meaningful enclosing contour,
pattern, gradient or inseparable composition does not qualify merely because an
outer area is flat. Intentional off-center brand composition is not type 1 (the
product calls it type 2). Do not recenter it to force eligibility. Incidental empty
canvas is not proof of a branded offset; do not invent one or require a brandbook.
This rule does not define the other types or silently assign them new treatments.

| Source role | Action |
| --- | --- |
| Eligible plain-field type 1 | Use a same-color circle with transparency outside; center the complete emblem as below, with individually judged scale. |
| Same emblem/color variant observed in a circular official avatar, even with a square source file | Evidence of presentation, not a classification override; no separate round source is needed. |
| Branded silhouette, shield, frame, meaningful outer contour or intentional off-center composition | Preserve significant contours/composition; do not force type 1. Official display does not excuse clipped details. |
| Suitable transparent standalone mark | Keep transparency; do not add a backing merely for consistency. |
| Uncertain or inseparable background; complex pattern/gradient/compositing | Do not automatically reshape it. Use a suitable supported variant or bounded further research. |

Use the existing conflict/unconfirmed route for real gaps, not invented types or endless search.

### Official display of the same variant

An actually observed round avatar on an identified official bank account/channel
confirms circular use of that same emblem variant, even with a platform-applied mask.
A square download does not negate it. Do not request a separate round SVG or routine human
shape approval. This supports the observed presentation, not blanket permission to
redesign logos and not an assertion that the circle is part of the original vector.
Official display does not override type classification or type-1 centering. It can
inform scale and variant choice, not justify clipping or a silent change of type.

Match the displayed emblem, colors and composition to the selected original vector.
A different file posted in the same channel does not inherit the avatar's shape.
A generic platform convention, unobserved avatar, unofficial account or a different
historical/color variant is not confirmation. If context cannot be observed, disclose
that limit and use the plain-field policy or another supported variant, without
claiming official circular use. Do not keep searching just for a round asset.

Retain the source URL, account identity/basis, observed time and actual contextual
screenshot or returned display image with its hash under local evidence. Capture
the actual account/mask, not a square download or locally rounded reconstruction. Explain the
match in existing `sources[].observation` and `assessment.variant.basis`; do not add
schema fields or invent observations. Keep raw source and contextual display evidence separate:
the latter establishes composition, not vector geometry or a Desktop/mobile render pass.
A raster-only avatar still needs an original vector; never trace it. Public prose
keeps a real context URL and observation, not private paths or unpublished image links.

## 2. Adapt the backing, not the emblem

Work on a separate working SVG copy; keep downloads, extraction lineage, full source
raster and selected source comparison immutable. Preserve actual source/clear-space
rules. Conflicting explicit constraints need a supported alternative or the existing
conflict route; a platform display alone does not override them.

**50% rounding** means a circle inscribed in a square display area: its diameter
equals the square side and its radius is half that side. This rounds the backing,
not the emblem, and says nothing about the emblem's scale. A non-square viewport
must not turn this into an ellipse. Prepare the circular geometry in the working SVG;
do not rely on an unverified UI `clipContent` / `overflow hidden` to supply it later.

Replace the eligible field with a same-color circle with transparency outside.
Do not clip the whole SVG blindly: a background-only mask is allowed only when
no emblem pixel, stroke, negative-space feature or satellite detail is removed or
changed. Prefer a separate circular backing when feasible. Both implementations
need the pinned converter and actual final render checks; a mask is not a visual pass.
Only uniform scale and translation of the complete emblem are allowed for placement.
Do not stretch, rotate, redraw, recolor, move individual parts or crop strokes and satellite details.
Preserve internal transparency; a hole is not white paint. Do not fill holes or
change alpha to repair an unsuitable monochrome mask; choose a suitable source instead.

Original-vector-only applies to all emblem contours. A circle or verified background-only
mask is the narrow presentation exception, not permission to trace raster or combine unsupported
colors/geometry. Extraction stays lossless; presentation changes belong to the working candidate.

## 3. Check complete fit and useful size

The observed scale and position are evidence, not universal placement requirements.
Start from the observed scale of confirmed same-variant display; otherwise use the supported
source composition, full fit and useful size. No fixed fill percentage is a target or starting point.
Do not add a fixed 10% margin or shrink it merely to meet an arbitrary percentage.

For type 1, center the complete emblem on both axes using its full visible bounds,
not empty `viewBox` space or the background bounds. Include strokes and detached details.
Do not invent optical offsets; a supported intentional offset excludes type 1,
rather than granting an exception to its centering rule. Only move/scale the whole
emblem and explain the transform. Side margins alone do not prove circular fit.
Official use does not excuse clipped details or poor readability at our target size.
An actually clipped emblem needs a suitable source-backed alternative, not lost details.

Inspect real final renders on both themes at the actual target sizes. When unspecified,
use 24, 32 and 48 px diagnostics plus normal full-size source/final and base/final views.
Resized previews of bound Desktop frames may help: keep originals, record their
hash and scale, and label previews as resized diagnostics, not native platform render passes.
Use equal base/final sizes; additions have real final frames, no fake baseline. Local supplemental
context/previews do not change the six/seven reviewer-file contract.

Mathematical fit does not establish useful size. Excessive shrinking can lose detail,
recognition or optical weight. Inspect thin lines, gaps, contrast and effective size.
Image-input limitations leave visual checks incomplete. For type 1, if a faithful,
legible circle cannot be prepared, find another supported eligible variant or stop
the candidate; never silently switch to a square or relabel the type to evade the rule.
Outside type 1, keep a suitable source-backed alternative where the source/composition
requires it. Do not delete a needed field or invent a transparent variant. Record the
concrete constraint/fallback in existing proportions/transform notes; no routine
shape-approval question or endless scale trials. Never fabricate unchanged.

## 4. Judge and describe two separate things

1. **Emblem fidelity:** source contours, colors, internal transparency, details and
   relative geometry survive any disclosed whole-emblem placement transform.
2. **Presentation:** backing classification, observed official display or catalog
   adaptation, complete fit and theme/size legibility meet this policy.

Both must be supported for `faithful: true`; this does not mean identical source/final
composition. Preserve visible differences instead of normalizing the source to hide
them. Official provenance, conversion or pixel matching alone does not prove suitability.
Use existing fields, not new evidence schema keys:

| Existing field | Record |
| --- | --- |
| `assessment.variant.basis` | Source/display variant match and backing role; do not claim fit before rendering. |
| `candidate.variant`, `candidate.transforms` | Observed official presentation or catalog adaptation; exact field/mask and whole-emblem changes or fallback, plus optimization. |
| `candidate.detailsJudgement`, `candidate.proportionsJudgement` | Emblem fidelity separately from fit, useful size, alpha and circle fallback. |
| `sourceJudgement`, `visualComparison` | Actual full source/final and theme/size observations; context or resized previews are not platform passes. |
| `candidate.rationale`, `visibleDiffExplanation` | Observed meaningful benefit; catalog presentation normalization is not a rebrand or proof the accepted design is obsolete. |

When observed, say “circular presentation matches the official channel avatar;
the source file has a square canvas”, not a separate circular asset or a rebrand.
Without such an observation, say “original emblem on a circular catalog backing”,
not an official circular app icon. The original source hash proves byte preservation,
not arbitrary editing authority. Do not invent square derivatives for provenance or create
bulk cosmetic MRs solely for consistency; suitability, update rationale and authority still apply.

## 5. Preserve workflow boundaries

No automatic shape selection, masking or acceptance is added to validators. Prompts
and observed use are not proof of good pixels or publication rights. Known failures
stay blocking. Final-byte changes need fresh checks; historical packages, sources,
evidence, dates and proposals remain immutable; do not reopen them for this policy.
No metadata/state changes, duplicate proposals or automatic merge follow.
