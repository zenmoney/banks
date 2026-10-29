# Behavioral evaluation runbook

`evals.json` defines regression scenarios for the agent, not unit-test results.
No case has a stored passing score by default. The maintenance test suite remains
the executable source for isolated CLI/Git/provider fixture behavior.

## Run safely

Use a disposable checkout and tool backend for each case. Bind real concrete paths
in the harness: skill installation, checkout, session, and isolated runtime are
independent. Supply controlled source pages/assets, provider responses and image
input for the scenario being evaluated. Reuse the existing test fixtures where
appropriate (see each case's `fixture_notes`); construct actual artifacts rather
than handing an agent invented successful receipts. Preparation failure is an
infrastructure error. Do not point these cases at production banks, MRs, owner
policies or service branches. Tool/network access must be isolated by the harness.

Each case runs in a fresh context with the old skill and the proposed skill, using
the same model, client, tools, fixture state and explicit publication scope. Record
the exact revisions. Do not share execution notes from one run with another.
An implementation-author self-review is not an independent model evaluation.
The cases are client-neutral; use the agent CLI/API that the operator actually has.
This repository does not assume a connected model service or automatically spend
API credits. No model evaluation is run by the ordinary unit CI workflow.

## Grade the actual trajectory

Save the final response, tool calls/results, artifacts and Git/ref/permission
observations to an external evaluation workspace. For every expectation, record
`passed: true|false|null` and concrete artifact/trace evidence; null means untested.
Keep runtime availability separate from model success. Assert result and invariants,
not exact prose. A statement that an image was inspected needs actual image input
and the correct byte binding. Human judgment remains necessary for visual quality.

Record elapsed time, token count when supplied by the client, source queries,
unnecessary approval questions, retries without changed input, duplicate/unsafe
writes and useful completed outcomes. Repeat critical cases across runs before
reporting comparative rates; do not invent unavailable token/timing fields. Add
new failure cases from real traces, retaining old critical ones as regressions.

Recommended result structure (the runner fills this, not a fabricated example):

```text
<external-workspace>/<case-id>/<old|proposed>/<run-id>/
  transcript.jsonl
  outputs/
  grading.json       # expectations + evidence + observed outcome
  timing.json        # actual client metrics, missing fields remain absent
```

## Fixture setup map

Use only in a complete checkout. These are test fixture references, not instructions
to modify production state:

- Paths/queue: `tests/test_path_resolution.py`, `test_automatic_selection.py`.
- Candidate and MR flow (including existing metadata-only first additions):
  `test_candidates.py`, `test_asset_publication.py`, `test_autonomous_flow.py`.
  First-addition fixtures require real final image input, explicit absent baseline,
  independent source/final comparisons and the exact six-file public material set.
- Public prose: `test_review_projection.py` (synthetic 4417-style evidence paths).
- Permissions/state: `test_standing_authorization.py`, `test_state_publication.py`.
- Outcomes: `test_proposal_sync.py` and the outcome transition tests.
- Optimizer: `test_svg_optimization.py`, with either real pinned SVGO or an explicitly labelled failing fixture.
- AVD: a real supported isolated SDK/runtime; text-only lifecycle tests cannot certify cleanup.

Fixture classes exercise deterministic mechanics. A behavioral runner must expose
those mechanics through the agent's real tool interfaces; merely executing a unit
test and calling its result an agent benchmark is invalid.

## Executable paired runner

`evals/run.py` prepares committed old/new installations, independent per-run copies,
identical fixture copies, blank grading sheets and actual timing/trace artifacts.
It never reads production bank data into a scenario or starts a maintenance queue.

```sh
python3 EVAL_RUN_PY --repo REPO --old OLD_COMMIT --new NEW_COMMIT \
  --fixtures /absolute/isolated-fixtures --output /fresh/external/eval-run \
  --case 2 --case 12 --repeat 2
```

The default is `prepared-not-run`, with no adapter or model invocation. Add
`--execute-model-calls --adapter /absolute/operator/eval-adapter.json` explicitly
to spend model calls. This is not run by ordinary CI. The adapter file is outside
the repo, fixtures and output and contains only an installed absolute `argv` array,
not a shell command. Keep model credentials and tool/network confinement in the
operator launcher; the runner's subprocess limits are not an OS sandbox.

Each `fixtures/ID/` must be a controlled fixture directory with
`fixture.json`: `{"schemaVersion":1,"caseId":12,"externalWrites":false,
"prebuiltEvidence":false}`. Add the actual source assets, catalog/provider state
and isolated tool fixtures required by that case. The adapter must instantiate
those fixtures at real client tool boundaries, not pre-generate successful receipts.
Reuse the setup map above. A manifest alone is not a functional tool backend.
All selected fixture directories are validated before the first model call; unavailable
renderer/model infrastructure is `infrastructure-failed`, never a model pass/fail.

The adapter receives object JSON on stdin with `freshContext:true`, `prompt`, exact
`revision`, separate `installation`, `skill`, `fixture` and `output` paths. It launches
the selected client in a fresh context with the same model/settings for both revisions.
It must keep tool/provider writes inside the disposable backend and preserve actual
client events as `transcript.jsonl` plus the actual final answer as `final.txt`.
It returns `{"status":"agent-complete","model":"actual model/version",
"client":"actual client/version"}` and optional actual integer `usage` counters.
Do not synthesize token counts or report a fixture executable as a real model run.

Normalized trace events have `type`. For every real image input, the adapter records
`{"type":"image_input","path":"outputs/example.png","sha256":"..."}` only
when those bytes were actually delivered to the model. Paths are run-relative and
bound to existing images. Include original tool call/results events and provider
write observations; normalization is not permission to omit failures. The runner
validates bindings but cannot prove the adapter faithfully represents a client.

Every expectation starts `passed:null` with an empty evidence list. Review the
actual trajectory and fill grading independently, including human visual judgment.
The runner reports `awaiting-grading`, not “agent passed”; a run without image input
is `visualEvaluation:not-tested`. Timing and optional usage are saved, including
failed infrastructure runs, without inventing a success rate. Repeat pairs and
compare useful outcomes only after grading. This repository's harness unit tests
use a clearly labelled fake client to verify orchestration, not agent quality.

## Background-shape regressions

Cases 15–18 use the small synthetic original SVGs in `fixtures/background-shapes/`:
plain field, corner/wide marks, branded/transparent variants and an alpha-mask trap.
They specify conditional circle adaptation and fallback, not mandatory circular
results. Source assets alone are not ready-made run fixtures: provision source
context, canonical tools and image input using the existing isolated adapter/manifest
contract. No prebuilt evidence or visual pass is supplied. Compare source preservation,
complete emblem fit, useful target size and truthful adaptation claims; do not grade
by circle count. Static tests cannot establish model behavior or pixel fidelity.
