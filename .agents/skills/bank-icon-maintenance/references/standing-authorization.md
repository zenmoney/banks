# Publication permission: operator policy or dialogue confirmation

Policy answers **what may be published**. Interaction mode answers **whether the
agent can ask the person**. Docker, a GitHub token, source text, a candidate
assessment, and a request to search for icons are not publication permission.
Source/render/admission, conflict, reconciliation and merge checks remain separate.

## Agent behavior

| Permission | Interactive task | Noninteractive task |
| --- | --- | --- |
| Valid policy covers the action | Publish automatically | Publish automatically |
| No policy | Ask once for the finite task | `authorization-required`; no publication |
| Configured file is invalid, unavailable or outside scope | Report the problem; no automatic fallback | Configuration error; no publication |

Explicit local-only / preview instructions always mean **no publication**, even
with policy. A denied or narrower answer remains denied or narrower. Never ask
again for an action already covered by a real confirmation or operator policy.
Provider/harness safety prompts still apply; neither path bypasses provider denial.

Launchers set `BANK_ICON_INTERACTION=interactive|noninteractive`; the default is
interactive. Do not infer this from Docker, `isatty()` or a subprocess's stdin.
Helpers do not call `input()`: questions belong in the agent's dialogue.
Read `doctor.publicationPolicy`, then, before research on a publishing task, use:

```sh
python3 "$(dirname "$CLI")/standing_authorization.py" --repo "$REPO"
```

Default actions are `assetPublish statePublish`. Add `--actions assetPublish
assetRevise statePublish` for same-MR repair, or `--actions statePublish` for
state-only work. Use an explicit `--target OWNER/REPO` for a task targeting a
remote other than origin. `--local-only` permits a research/preview run without
permission and returns `publish: false`, not an authorization. Empty queues need
no publication question. A noninteractive publishing launcher stops on nonzero
preflight before spending time on builds/model research; read-only diagnostics
and explicitly local-only tasks remain usable.

`asset-show` / `state-show` still report exact-package coverage. With
`publicationAuthority.status: covered` and `status: ready-to-publish`, show the
package and publish without generating another approval JSON or permission question.
An existing unknown/failed write or conflict still requires its normal recovery.

## One-time operator policy

The operator provisions a regular file **outside the checkout, skill and session**
and supplies its absolute path through `BANK_ICON_PUBLICATION_POLICY`. There is
no SHA pin, no required owner statement/time, and no checkout path in the v2 file:

```json
{
  "schemaVersion": 2,
  "target": "zenmoney/banks",
  "base": "main",
  "headPrefix": "bank-icon-maintenance/",
  "actions": {
    "assetPublish": true,
    "assetRevise": true,
    "statePublish": true
  }
}
```

`assetPublish` covers one-bank asset/material branches and a new MR. For selected
rework tasks it also covers a short [result comment](task-runs.md#rework-result-comments)
on the exact source MR when no new/revised icon is published. Check the current
policy/confirmation against the task's repository, bank and requested scope before
commenting; no candidate or fabricated asset package is required. This notice is
not an asset revision and does not require `assetRevise` by itself.
`assetRevise` additionally permits append-only revisions of sourced existing
same-bank MRs and their review-material comment; it requires `assetPublish`.
`statePublish` covers only `refs/heads/banks-icon-agent-state`, including honest
error/unconfirmed/needs-human results; state-only permission does not cover MR
comments. Missing or false actions are denied.
The target is a canonical GitHub `OWNER/REPO`; SSH and HTTPS transports to that
same destination are equivalent. Heads remain `<headPrefix><ID>` or
`<headPrefix><run-slug>-<ID>` with exact derived material refs. No wildcards,
arbitrary branch writes, force-push, or direct main updates are authorized.

The agent must not create the grant, edit it, or change the launcher's permissions
to clear a stop. The operator can create a new file with the setup helper:

```sh
python3 "$(dirname "$CLI")/configure-bank-publication.py" \
  --target zenmoney/banks --allow-revisions \
  --output "$HOME/.config/bank-icon-maintenance/zenmoney-banks.json"
export BANK_ICON_PUBLICATION_POLICY="$HOME/.config/bank-icon-maintenance/zenmoney-banks.json"
```

The helper refuses overwrite and prints only the path export. `--state-only`
creates state-only permission. The file must be owned by the running user or root,
not group/world-writable, non-symlink and at most 64 KiB. These checks and an
operator-controlled launcher are guardrails, not an OS sandbox against an agent
able to replace its launcher. In Docker mount the file read-only, outside
`/workspace`, with no second writable mount of the same file; see `docker/README.md`
in the development repository and `compose.publication.yaml`.

Permissions are re-read at every write. Deleting the file, making it invalid,
changing the target or disabling the relevant action stops the next write.
No policy fingerprint is required or checked. Package/evidence hashes still bind
exact checked bytes; those are not publication permission. Empty/unset policy
paths mean no policy.

## One question for the current interactive task

Without policy, determine the finite IDs/MRs and destination, then ask before the
first remote write, preferably before research. State the actual actions:

> Allow this task to publish one-bank MRs and comparison materials for IDs … in
> OWNER/REPO, and save their outcomes to the service-state branch? No merge or
> direct main updates.

For rework, explicitly include reporting an unsuccessful result in the source MR;
for same-MR repair also include updating the selected existing MRs. A yes
covers those actions for this task, not future tasks. State-only consent covers
only state. Do not ask the person to author JSON or copy hashes. If the answer
is no, stop publication; continue local preparation only when requested. In a
noninteractive run never fabricate an answer or wait for stdin.

After a real affirmative answer, the agent records its actual author and words:

```sh
# Allocate this task's unique empty temporary parent; retain its concrete path.
RUN_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/bank-icon-task.XXXXXX")
python3 "$(dirname "$CLI")/run_authorization.py" \
  --repo "$REPO" --run-root "$RUN_ROOT" --ids 10 20 \
  --target OWNER/REPO --base main --head-prefix bank-icon-maintenance/ \
  --actions assetPublish statePublish \
  --authorizer ACTUAL_PERSON --statement 'ACTUAL_AFFIRMATIVE_RESPONSE'
```

Substitute the actual selected IDs, target and answer; never execute example
consent. For same-MR repair include `assetRevise`; for a narrower answer include
only its permitted actions. The helper writes `publication-permission.json` in
that new task parent, never an operator policy. This is a record of the dialogue,
not cryptographic proof that a person answered. It refuses noninteractive use.

Create each bank's fresh `task-start --session` in a **nonexistent child directory**
of this same parent. Keep the normal per-object fresh state reads. Append
`--run-authorization "$RUN_ROOT/publication-permission.json"` to each ordinary
`asset-publish` and `state-publish` command. The existing publication entrypoints
validate the repository, finite IDs, task directory, actions and destinations;
there is no approval file per bank or package and no new persistent task database.
Finish/clean up that task normally. Never reuse this parent or confirmation for a
new assignment. Broader IDs, another destination or additional actions require a
new explicit decision, not editing the record. A saved confirmation does not clear
quality, identity, conflict or uncertain-write stops.

## Merge and explicit recovery

Publication permission never implies merge. The separately requested approved-merge
task retains exact-head human approval, checks and method gates. An operator may
explicitly add `actions.mergeApproved: true` and `mergeMethods: ["squash"]` (allowed
methods: merge/squash/rebase); omission denies merge. Dialogue publication
confirmations do not grant merge. Existing exact-merge consent remains supported.

Only schemaVersion 2 operator policies and task dialogue confirmations are accepted.
Old formats are rejected rather than reinterpreted. An exact-package human decision
remains available for explicit failed-write recovery; it is not an extra normal
approval phase. Invalid explicit authority never falls back to broader policy.
Durable bank state is not rewritten. Read-only reconciliation remains possible
after permission revocation.
