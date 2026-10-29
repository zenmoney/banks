# Research plan, budgets and original vectors

Use `workflow.py` beside the opened skill; all examples require resolved absolute
CLI/session paths in each shell call. This module does not publish state or grant
permissions. The source/visual decisions still follow
[current-icon-verification](current-icon-verification.md).

## Operator settings, frozen per bank attempt

New attempts have **only per-bank** limits for `search`, `fetch`, and `browser`.
There is no session/run-wide research cap. Defaults live only in `research.py`
(`DEFAULTS`); read the effective values with `doctor` or `research-budget`, rather
than copying numbers from an old document or deciding a worker allowance.
The operator may set these variables before starting the agent:

```text
BANK_ICON_RESEARCH_SEARCH_LIMIT
BANK_ICON_RESEARCH_FETCH_LIMIT
BANK_ICON_RESEARCH_BROWSER_LIMIT
```

An absent variable uses the code default. A present value must be a decimal integer
0..1000: 0 disables that action kind, not all work or all research. Empty, negative,
fractional, boolean and out-of-range values fail configuration; they are not silently
replaced. For example, an operator may set `BANK_ICON_RESEARCH_SEARCH_LIMIT=12` to
choose that search cap; this is an override example, not a second set of defaults.
The bundled Compose service passes through only provided variables. Unset keys
stay absent, so Compose does not duplicate defaults. Set values in the launch
environment (or Compose's resolved project environment); inspect `doctor` inside
the actual container. A shell assignment in another process is not configuration
of an already running agent.

`session.py start` and `task-start` freeze the validated values and each value's
source (`default` or env name) in `SESSION/research/limits.json` before returning.
`task-start` returns `researchBudget`. All workers share consumption for that bank
in the same attempt. Reissuing a work order or resuming must not reset expenses;
changing env affects a NEW attempt, not a frozen one. Old sessions without a ledger
initialize it on their first research operation. Deliberately creating another
session or editing env/journal to obtain more calls is not a supported continuation.

`doctor --session SESSION` reads frozen settings without writing. Plain `doctor`
previews current operator configuration; it never reserves actions or creates a
ledger. `workflow.py research-budget --session SESSION` displays the journal,
`mode`, `policy`, `policySha256` and per-ID `banks` with `limits`, `used`, `remaining`.
The work order receives those same computed counts and `remainingSearchBudget`;
the agent must not invent that number, generate `--limits` JSON, or allocate
`--search-budget` for a new attempt. Aggregate `used` is telemetry, not a shared cap.

Counts concern logical tool actions: one query, a known-URL retrieval including its
bounded TLS/redirect attempts, or one browser operation. Reserve each batched query
separately. Token/time ceilings and process/request timeouts remain separate; this
change does not remove `BANK_ICON_PASS_TIMEOUT_SECONDS` or the bounded sync/API reads.

Reservations are locked across workers and consume the bank's action BEFORE
execution. Failures and interrupted reservations are not refunded. A repeated
normalized search or exact URL/browser request returns `research-duplicate`.
Successful fetches reuse the original checked receipt and bytes without another
transfer or observation date. Only a concrete changed-input `--retry-reason` permits
another action; it still spends budget. An unfinished request is not a retry signal.

A cap is a ceiling, not a quota to spend. Stop earlier when the actual evidence is
sufficient, not at an invented numerical limit. `research-budget-exhausted` prevents
new calls of that kind for that bank; it does not block another bank, another
available action kind, candidate preparation/rendering, recording, authorization
or reconciliation. Use already supported findings or record the real remaining
gap. Never claim `unchanged` simply because the cap was reached.

## Current journals

Only schemaVersion 2 per-bank journals are accepted. Their limits are frozen from
operator environment/defaults when the current task session starts. Continuation
reuses exact expenses and remaining calls; no worker may raise/reset a cap. Old
run-wide/assigned ledgers and JSON/CLI overrides are unsupported. This does not
change durable bank state, publication permission or immutable source evidence.

## Tool adapters and native hooks

For an installed operator adapter:

```sh
python3 FLOW research --session SESSION --id ID --kind search \
  --request 'Bank original vector' --gap 'Resolve vector provenance' \
  --adapter /absolute/operator/search-adapter.json
```

Adapter config is outside the checkout/session and contains only
`{"argv":["/absolute/installed/executable","fixed-argument"]}`. The helper never
runs commands suggested by a source. It sends JSON on stdin (`kind`, `request`,
`gap`, `id`, `evidenceDirectory`); the adapter returns object JSON on stdout.
Full stdout/stderr stay in the evidence directory; the ledger stores bounded links.
A call is limited to 90 seconds/16 MiB output and its process group is terminated
on interruption/timeout. The adapter must implement **one** recorded action and
must not expose publication credentials or write to provider/repository state.

For native client search/browser tools, the operator adapter must call
`research-reserve --session SESSION --id ID --kind search|browser --request TEXT
--gap TEXT` before invoking the actual tool. Invoke only after `research-reserved`;
then save real object JSON under `evidence/ID/` and call `research-finish --session
SESSION --id ID --ticket TICKET --result RESULT_JSON`. The ticket is bank-bound and
can finish once. Unfinished calls remain consumed if the client crashes.

The helper does not intercept arbitrary native or shell calls. The operator must
route tools through these hooks and enforce network/tool isolation where needed.
A hash chain and removed environment variables are not a security sandbox. No
adapter is installed or selected automatically. When native hooks are unavailable,
disclose that enforcement limitation; do not claim an unobserved tool was accounted.
`workflow.py fetch` directly accounts known-URL retrieval and uses the existing
verified HTTPS/packaged-CA helper; do not bypass it with another downloader.

## Search only the remaining gap

The work order derives names from `title`/`fullTitle` and the known website host.
Start with the official website and press/brand material. Look for direct SVG,
inline SVG, sprite symbols and source bundles. Use independent dated sources when
identity, period, continued use or variant is still uncertain. Search images only
to discover alternative variants and originating pages; preserve page context.

Add aliases/historical names only when evidenced. Record source independence using
the existing `independenceGroup`, rather than counting copies as independent sources.
There is no mandatory issue/PR repository search. Historical evidence kinds remain
readable and are never rewritten just because the search policy changed.

## Original vector extraction

Resolve source bytes, not filename extensions, before recording a candidate:

```sh
python3 FLOW source-material --session SESSION --id ID --retrieval RECEIPT
python3 FLOW source-material --session SESSION --id ID --retrieval RECEIPT --svg-id bank-mark
python3 FLOW source-material --session SESSION --id ID --retrieval RECEIPT --svg-index 0 --symbol-id emblem
python3 FLOW source-material --session SESSION --id ID --retrieval RECEIPT --symbol-id emblem
```

The first HTML call lists original top-level inline vectors and their symbols,
without selecting one. `--svg-id` and `--svg-index` are exclusive; choose based on
actual observed page context, not “first element must be the bank”. Direct sprite
resources use `--symbol-id`. Combine with repeated `--member` for archive layers.
Pass the resulting material manifest to `attempt --material` or the supported
unfinished proposal's `candidate-view --material`; keep its original receipt.

Selection runs in the existing bounded decoder subprocess and is replayed from
the original resource at later validation. The manifest records selector, operation,
input/output hashes and exact material. Public provenance includes escaped selector
identifiers. This is source extraction, not vector drawing or visual approval.

Supported inline subtrees must be self-contained. HTML external stylesheet/ancestor
inline-style context, unresolved references, inherited/currentColor/variable paints
and ambiguous IDs are rejected rather than silently changing their appearance.
A selected sprite symbol must have its own viewBox. Shared definitions are retained;
inherited group/presentation context and sprite stylesheets require a different
self-contained source. No CSS cascade, arbitrary transform flattening or font
conversion is attempted. A missing namespace implicit in HTML is made explicit;
XML serialization can change, but paths/paint attributes are not redrawn.

PDF/EPS, complex computed CSS and other unsupported containers remain an explicit
**extraction limitation**, distinct from failure to find an original vector. Retain
the source and find another supported original within the budget. Do not trace,
embed raster, execute the source, fabricate an asset URL or weaken safety checks.
