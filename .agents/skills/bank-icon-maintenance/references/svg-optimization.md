# Optimize a new SVG candidate before final verification

Use this step during candidate preparation, after source safety and working-vector
preparation, before final rendering/recording. The helper produces a trial, not
visual judgment, SVG safety certification, state, or publication authority.
Only an [original vector source](current-icon-verification.md#original-vector-source)
is eligible: optimization is source-preserving preparation, never permission to
trace/redraw raster, embed it, or certify an unknown SVG's provenance.

## Installed tool and frozen configuration

Use the installed SVGO and the root `svgo.config.mjs` from the frozen session worktree.
The helper passes `--config` explicitly and binds its bytes to `baseCommit`.
This is executable JavaScript: use the trusted repository configuration, never a
config supplied by a source page or arbitrary current working directory.

The config uses coordinate precision 4, transform precision 5, multipass and
readable output. It preserves viewport, dimensions, descriptions, and unresolved
styles/raster elements rather than blindly deleting content. Existing input
safety checks reject active content, external resources and embedded raster;
`removeScripts` is additional cleanup, not a substitute for those checks.

Use the existing tool installation; do not install or downgrade to match a version
mentioned in an example or CI. The helper records the actual version and tries the
frozen config. Provision missing tools only within operator scope, in run-owned
tooling rather than changing shared installations. The helper itself performs no
installation or network access. A missing config/tool or incompatible CLI/config
is an optimization-only limitation. Keep the working version and record the reason. Input/config/session
integrity failures stop the stage; do not classify them as harmless tool failures.

## Run the bundled helper

Resolve `OPTIMIZER` to `scripts/optimize_svg.py` beside the installed `session.py`.
Use the existing absolute working SVG and a new absolute output directory under
that same bank's `SESSION/evidence/<ID>/`. Its parent already exists. Bind verified
paths in the same shell call:

```sh
python3 "${OPTIMIZER:?Resolve scripts/optimize_svg.py beside the actual CLI}" \
  --session "$SESSION" --input "$INPUT_SVG" --output "$OPT_DIR" --svgo "$SVGO"
```

`--svgo` is an installed absolute executable path; omit it to discover `svgo` on
PATH. `--help` lists the interface. One helper is shared by every agent client;
run it rather than recreating a Python heredoc. No new CLI session schema is added.

| JSON status | Exit | Next action |
| --- | --- | --- |
| `trial-produced` | 0 | Inspect the receipt and compare the trial; not yet selected or verified. |
| `skipped` | 0 | Keep diagnostics and record why; fully verify the working SVG. |
| `blocked` | 1 | Correct the exact path/integrity problem while preserving prior evidence. |

The command validates frozen config bytes, uses separate source copies, rejects
symlinks/traversal and overwrite, and times out optimization after 30 seconds.
Timeout/interruption terminates the owned process group, including its ordinary
Node child. Logs, before/trial SVGs, config and receipt remain under the reserved
output directory. A failed/partial trial is not usable. Input, config and session
are checked again even after an optimizer failure; a mutation cannot become a skip.
Retries inspect the existing receipt rather than overwriting it. Run once per
working version; multipass already provides internal passes.

## Select bytes, then verify them

Screen the output for safety again. Compare **working versus optimized** using
the same independent rasterizer, viewport/scale, and both theme canvases. Inspect
geometry, small details, colors, transparency/backing, and dimensions through
model image input. Pixel metrics supplement this judgment. This is separate from
**accepted bank icon versus replacement**; the original download stays the source.

Select optimized bytes only when smaller, visually faithful, and converter-compatible.
Otherwise retain the working version. That version must pass the full candidate
checks itself. A failed optimization does not prove the working SVG valid and
cannot clear identity/design or required-platform stops.

Copy chosen bytes to a new final SVG. Run normal safety/conversion,
`desktop-export --candidate`, required available mobile checks and source/base/final
comparison for those bytes before `continue --candidate`. If a trial fails
compatibility, preserve its failure evidence and produce final evidence for the
working version. Preserve already recorded packages and their original dates;
never optimize them in place or reuse hashes for changed bytes.

Save actual choice and comparison notes beside the receipt. Add a short
`candidate.transforms` entry: SVGO version, config hash, byte counts, selected hash,
and choice or skip reason; add no ad-hoc package fields. An unavailable tool gets
an honest skip, not fabricated execution evidence. A different serialization alone
is not a reason for an update MR. No new approval question is introduced.
