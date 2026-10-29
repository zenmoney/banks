# Preparation inputs and complete diagnostics

These changes help the agent use existing gates. They do not change publication
permissions, quality requirements, durable bank state or prior immutable evidence.

## Public text preflight

Asset preparation assembles the whole public representation (including admission,
reviewer responses and title) before its final text preflight. It reports all
unsafe field paths and reason codes together, without quoting rejected values.
For example, `platforms.0.observation` and
`admission.judgments.concerns.0.basis` can appear in the same error. Credential
patterns, control characters and local-path rejection remain enforced. Material
byte/binding failures still stop immediately; this is not a permissive publication
or a guarantee to aggregate every unrelated source/structure/integrity error.

Use `--public-text` only for its existing editable narrative fields. Preserve facts,
uncertainties and not-tested platforms. Admission findings and sourced reviewer
requirements are not added to that override allowlist. A correction to admission
uses a new append-only admission input through the existing helper BEFORE preparing
a package; never modify a saved admission, source report, hash or prepared package
in place. Read the full diagnostic before retrying. The scanner is not a semantic
review and does not certify the truth of prose.

## Admission template contract

`workflow.py notes-template --stage admission` returns `admissionSchema` beside its
`notes` link. Choices come from the same constants used by the validator. It lists
required object fields, enums (including `current-use` and `absent`), baseline-
conditioned choices, source-reference and historical/rebrand/platform conditions.
`baselineChoices` are constraints, not observations: absent accepted bytes require
`first-addition`/`absent`, but the actual quality and applicability findings stay
null until assessed. The notes file still contains exactly the original blank
input shape; do not copy the schema into it. No affirmative judgment is generated.

## Archive member pages

`workflow.py source-material --retrieval RECEIPT` still validates the complete
archive. If member choice is required, it retains a hashed `listing.json` in that
bank's evidence, with its receipt, source hash and nested selection prefix. The
response contains at most 20 entries by default and at most 16 KiB of JSON.
Long names can reduce the returned count; `nextOffset` reflects the actual page.
Use `nextPageArgv` exactly, or `listingArgv` with `--name-contains TEXT`, `--offset N`
and `--limit N` (1..100). Name filtering is a case-insensitive literal substring,
not a glob or regular expression. Reset offset to 0 when changing the filter.

The reader is `scripts/source_listing.py`, beside `workflow.py`; it accepts only
the returned evidence-relative `--listing` and `--sha256` in the same `--session`.
Paging rechecks saved listing/download bindings and does not fetch, select,
extract, authorize or change state. The full artifact remains available. HTML
inline-vector selection is unchanged; these pages concern ZIP/TAR member lists.

Filtering happens AFTER complete archive safety validation. Links, ambiguous names,
expansion/entry/depth limits and exact-member checks are not relaxed. A match is not
an approval: use its exact original name with `workflow.py source-material --member`;
repeat the existing prefix for nested containers. Actual material validation still
replays extraction from the original resource. Never select the first SVG merely
because it is the first entry or hide an unsafe member with a display filter.

## Bare website hints

A syntactically valid bare host such as `zen.com` now supplies `officialHost` and
`site:zen.com` search hints. Metadata is untouched. `officialUrl` remains null for
a bare host: no HTTPS address is silently invented or claimed visited. Qualified
HTTP(S) URLs retain their exact original value. Credentials, malformed hosts,
local paths and unsupported schemes do not become official queries. Host syntax
alone proves neither website ownership nor applicability of a logo. Follow the
existing strict HTTPS retrieval and source-evidence validation for any download.

Android provisioning/readiness is unchanged. This change cannot turn historical
not-tested Android checks into passes; compatibility still needs a separate
offline build and actual platform renders.
