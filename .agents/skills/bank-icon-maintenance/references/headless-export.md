# Offscreen Desktop export

CLI and SESSION in command examples mean the concrete paths resolved by the
[skill bootstrap](bootstrap.md). Assign them within
the same shell invocation or substitute their values; they are not persistent
across agent tool calls.

Use this path for a selected session current icon, exact final candidate, or a pinned changed-current snapshot. It runs the existing catalog generator and one fresh Desktop JVM per export; it does not open a window or rewrite SVG/manifest bindings.

Prerequisites: compatible Python with Pillow, Git, a JDK compatible with the catalog build, and the existing `icon-catalog` Gradle dependencies cached locally. Run one export command at a time against a catalog build directory. The helper removes `DISPLAY`, `WAYLAND_DISPLAY`, and `XAUTHORITY`, and sets `JAVA_TOOL_OPTIONS=-Djava.awt.headless=true`. It invokes `./gradlew --offline --no-daemon :catalog:run` with exact `catalogBanks`, `catalogId`, and optional `catalogCandidate` inputs. `--catalog` with the real path to an alternate catalog selects an existing project; the default is `<session.repo>/icon-catalog`, independent of the skill's installation path or current working directory.

Run `doctor --session "$SESSION" --render` first; pass the same `--catalog`
override to both commands when needed. This checks the selected JDK (respecting
`JAVA_HOME`), Pillow and the executable wrapper without running the project or
claiming its Gradle cache is complete. Missing Pillow/wrapper errors stop export
before reserving `--output`; a failed actual build still retains its diagnostics.

```sh
# Current icon in the frozen session worktree:
python3 "$CLI" desktop-export --session "$SESSION" --id 4387 \
  --output evidence/4387/desktop-current
# Final candidate: base/final JVMs for updates; candidate-only JVM for additions:
python3 "$CLI" desktop-export --session "$SESSION" --id 15362 \
  --candidate "$SESSION/evidence/15362/final.svg" \
  --output evidence/15362/desktop-candidate
```


## Headless execution boundary

Follow the [headless execution policy](headless-runtime.md). Use `desktop-export`
for new Compose Desktop evidence and keep its `export` arguments. The interactive
catalog is a prohibited desktop GUI application. Headless browsers serve source
research; headless Android emulators serve Android checks. Capture those images
through the browser/device API and label each observation by its actual renderer.
For independent source-SVG references use `rsvg-convert`; pass saved PNG bytes
directly to the model's image-input tool.

## Consume the result

The JSON response and `bundle.json` contain `desktop`, `desktopPath`, and, for a candidate, `comparison`. Each invocation retains its process log, command/exit/time/session/base receipt, unmodified exporter `result.json`, unmodified generated manifest/XML, and light/dark PNGs. Build-directory paths in the raw result are historical provenance, not continuing dependencies: consumers validate the immutable evidence copies.

1. Pass both final PNGs directly to the model as images and assess them; do not launch a viewer or screenshot them. If the harness cannot deliver image input, stop visual verification and preserve the export without claiming a judgment. Add your actual `visualComparison` to the returned `desktop` object in the existing attempt or candidate package. The helper deliberately supplies no judgment, `faithful`, source assessment, outcome, approval, or state transition.
2. Current: use that object in the existing attempt, preserve original source-observation dates and reasons, and run `continue --evidence FILE`. New `checkedAt` must not precede export `observedAt`; a new render is not a new source search. Explain any reuse of historical sources and its applicability.
3. Candidate: use returned `desktop` and `comparison` in the existing package, plus independent source comparisons, judgment, lineage, platforms, diff and draft. Updates retain exact 480×240 before/after pairs of the 240×240 base/final frames without rescaling. First additions use `desktop.baseObservation: null` and both themes `{base: null, candidate: <real PNG link>, beforeAfter: null}`; source links are added normally. Inspect actual 240×240 final frames, never blank/dummy panels or invented baseline exports, and label them first addition. `continue --candidate FILE` still leaves human review pending.
4. Changed-current: use the returned `desktop` in an attempt bound to the pinned `currentBase.commit/iconHash`, then submit the existing `verify-current` outcome request with its exact report/snapshot/evidence links. Do not substitute the original session base.

Downstream assessment, optional human approval, asset preparation, materials, and state preserve renderer/evidence bindings and explicit absence. Addition public comparison images are exact final theme frames; update images remain exact reviewed pairs. No current/base-only export or unchanged verification exists for an absent icon. Public materials exclude private logs and paths.

## What this proves

The label is **`offscreen CPU Skia/painterResource`**, an offscreen Compose Desktop CPU/Skia render through the shared catalog component and generated XML. This is not a window screenshot, GPU observation, or Android/iOS Compose/native verification. Both frames are 240×240 px, density 3, a centered 54 dp icon area, on `#FFFFFF` and `#121212`.

Consumers reject unsuccessful/missing/partial results, unsupported schema/status, foreign session/base/ID/source/manifest, changed SVG/XML/result/PNG, loaded XML hash mismatch, wrong themes/backgrounds/dimensions/pins/runtime, and mismatched invocation times. A failed attempt retains diagnostics and partial artifacts but no successful `desktop.json`; use a new output only after resolving the cause. There is no automatic retry. The accepted icon, reports/history, human approval, and other banks are not changed by export.

Identity, currency, visual fidelity, and human review remain separate decisions. Preserve all Android/iOS Compose/native requirements and explicit `not-tested` limitations. Historical window observations retain their original label and dates; do not relabel them as offscreen. Re-render when XML loading, scaling, clipping, background, or readiness behavior changes, not merely because a renderer filename/hash changed. No implementation hash or resource-cache reset framework is required; every export gets a fresh JVM.