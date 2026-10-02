# Read-only bank icon catalog

A standalone read-only catalog of existing SVGs from `banks/` and final candidates: Compose Desktop, Compose Android, and an independent native Android `ImageView` using VectorDrawable in `:androidApp`. The catalog does not edit source files, metadata, `viewBox`, or padding. Derived VectorDrawable XML does not replace the SVG.

## Running the catalog

JDK 21 is required to run the build and the Desktop application; the Java/Kotlin bytecode target is 17. The Android application runs in the Android runtime.

```sh
cd icon-catalog
./gradlew --no-daemon :generator:test :catalog:desktopTest
./gradlew --no-daemon :catalog:run
```

The grid shows only entries with an existing `icon.svg`; search matches a case-insensitive substring of the ID or name. Opening the detail view and returning preserves the query. The card area is 54×54dp. The detail view displays the derived XML without fitting it to the card; large images can be scrolled. A toggle switches between light and dark backgrounds. The raw SVG width/height, `viewBox`, SHA-256 hashes of the exact SVG/XML bytes, and renderer are listed separately. If the source dimensions are ambiguous, the UI says so: the viewport size is not presented as the SVG's natural size.

To use a different working dataset and a candidate (absolute paths):

```sh
./gradlew --no-daemon :catalog:run -PcatalogBanks=/path/to/worktree/banks
./gradlew --no-daemon :catalog:run -PcatalogBanks=/path/to/worktree/banks -PcatalogId=3 -PcatalogCandidate=/path/to/final.svg
```

The candidate replaces only the render/validation input for the selected existing metadata ID, including a metadata-only bank without `icon.svg`; it never creates a bank or writes an accepted icon. Effective source is chosen before missing-source validation. Invalid metadata, duplicate IDs and folder ID/country disagreement still block; missing/unsafe candidates never fall back to the base. Without a candidate, a selected metadata-only bank fails missing-source validation (bulk still omits it). This is not variant selection. Selected errors stop generation before rendering; bulk errors stay local to their cards.

### Desktop: headless export

The same Desktop entrypoint supports a single export in a fresh JVM without a window or display server. A normal run without arguments still opens the interactive catalog. Example from `icon-catalog/` (the output directory must not yet exist, and its parent must already exist):

```sh
env -u DISPLAY -u WAYLAND_DISPLAY -u XAUTHORITY JAVA_TOOL_OPTIONS=-Djava.awt.headless=true \
  ./gradlew --offline --no-daemon :catalog:run \
  --args="export --id 3 --manifest $PWD/catalog/build/generated/catalog/manifest.json --output /tmp/catalog-export-3"
```

For a final candidate, add the same `-PcatalogId=15362 -PcatalogCandidate=/absolute/final.svg` options and specify `--id 15362`. `-PcatalogBanks` also retains its existing meaning: it is generator input, not an additional renderer. Do not run old compiled resources after generation fails and treat them as the new candidate's result. The CLI requires absolute `--manifest`/`--output` paths, exactly one known ID, and a manifest that matches the compiled entry, source SVG bytes, and XML bytes actually read.

A successful package contains `light.png`, `dark.png`, and `result.json`. Both PNGs are 240×240px at a density of 3px/dp; the icon is fitted proportionally into a centered 54×54dp area without changing the source or `viewBox`. The backgrounds are `#FFFFFF` and `#121212`. This is an **offscreen Compose Desktop CPU/Skia render through the shared `CatalogIcon`/`painterResource`**, not a screenshot of the `CatalogApp` window, a GPU render, or Android/iOS/native verification.

`result.json` (`schemaVersion: 1`, `status: "success"`) contains `id`, `source` (path/SHA-256), `xml` (resourceName, expected `sha256`, and observed `loadedSha256`), `manifest` (path/SHA-256), `renderer`, `pins`, `runtime`, `density`, the square image size `sizePx`, `iconSizeDp`, `observedAt`, and `images` (theme/path/SHA-256/background). Hashes refer to the exact bytes; PNG filenames are relative to the package. Only exit code 0 together with a complete package and matching hashes means the export is complete.

The shared composable's readiness is checked before the completed frame is saved; blocking loading/rendering is limited to 30 seconds. `LocalResourceReader` observes the exact bytes passed to `painterResource`; a preliminary decode alone does not provide this evidence. The second background may reuse the already verified resource within the same invocation. Repeated in-process calls are outside the contract; each CLI export runs in a fresh JVM, so no cache reset is needed.

An unknown ID or a conversion/resource/hash/render/write failure produces a nonzero exit code and a diagnostic message. Existing output is neither modified nor deleted. New output is reserved with `createDirectory`; `result.json` is published last as the marker of complete success. The directory as a whole does not appear atomically: if a write fails, partial files may remain in the directory created by this invocation, **without** `result.json`. Use a new path for the next run, and do not treat old PNGs as the current result. Human review and separate Android/iOS checks remain mandatory in their respective workflows; the headless package does not automatically replace existing maintenance evidence.

Observation on 2026-09-26: 29 tests, headless probes for ID3/15362/5044, converter rejection for 15685, final candidate 15362, loaded XML/write failures, and an actual 30s timeout. Eight PNGs were visually inspected; a new window/Android/iOS were not checked. Commands, exact manifests/hashes, comparison with the historical window, and limitations: [local evidence #12](../.scratch/bank-icon-maintenance-implementation/evidence/12-headless/2026-09-26/README.md), if available. `.scratch` is not tracked in Git; a fresh clone does not guarantee the evidence or candidate is available.

### Android

First, successfully build the APK with the required inputs; after a failed build, a previously built APK does **not** prove the result for the current input. For the device, specify only an explicitly selected and authorized emulator serial — connected user devices are never selected automatically. Example from `icon-catalog/` (replace the placeholder with the exact serial before running):

```sh
EMULATOR_SERIAL='<explicit emulator serial>' # Replace with an explicitly selected and authorized serial
ANDROID_HOME=/opt/android/sdk ./gradlew --no-daemon :androidApp:assembleDebug &&
  adb -s "$EMULATOR_SERIAL" install -r androidApp/build/outputs/apk/debug/androidApp-debug.apk &&
  adb -s "$EMULATOR_SERIAL" shell am start -n catalog.android/.MainActivity
```

After a successful installation, an optional direct entry into the native detail view is available:

```sh
adb -s "$EMULATOR_SERIAL" shell am start -n catalog.android/.NativeCatalogActivity --ei bankId 3
```

The same `-PcatalogBanks=/absolute/path/to/banks`, `-PcatalogId=3`, and `-PcatalogCandidate=/absolute/path/to/final.svg` options can be passed to the build command. A button in Compose opens the native catalog; the native detail view displays the same XML through the platform `ImageView`, not the Compose renderer. Synchronization copies the exact XML bytes only into `:androidApp`'s own generated output, removing stale copies; AAPT then compiles them. APK contents must be correlated with the exact `manifest.json` and hashes from the corresponding build.

## Conversion and resource contract

`:catalog:generateCatalogResources` invokes the JVM CLI `catalog.generator.MainKt`:

```sh
./gradlew --no-daemon :generator:installDist
generator/build/install/generator/bin/generator --banks /path/to/banks --output /path/to/generated
# Optional: --id 3 --candidate /path/to/final.svg
```

Exit codes: `0` — bulk catalog completed (see the manifest for individual errors); `1` — error for the selected ID; `2` — invalid arguments, input/output, or an infrastructure error. Output must not overlap source files.

Default output relative to the repository root: `icon-catalog/catalog/build/generated/catalog/`:

- `composeResources/drawable/bank_<id>.xml` — successfully derived resources; negative IDs use `bank_m<abs(id)>`; get the exact name from `resourceName`;
- `kotlin/catalog/GeneratedCatalog.kt` — `generatedCatalog(): List<CatalogEntry>`;
- `manifest.json` — `schemaVersion: 1`, `banksRoot`, `sourceRoot`, `pins`, `entries`.

An original entry's `sourcePath` is resolved relative to `sourceRoot`; a candidate path is absolute. `sourceSha256` and `xmlSha256` refer to the exact bytes. Each entry contains `id`, `title`, `sourcePath`, hashes, raw `sourceWidth`/`sourceHeight`/`viewBox`, unambiguous `naturalWidth`/`naturalHeight`, `resourceName`, `error`, and `warnings`. `sourceValidated: true` means the SVG security checks and width/height validation succeeded for the exact bytes identified by `sourceSha256`. This allows a native consumer to consider the original SVG even if conversion fails, but does not imply a successful render. An absent or `false` flag does not permit packaging an unvalidated source. Missing XML/hash is not a successful check. The exact manifest is included in the Android APK as an asset.

The SVG first undergoes a separate secure XML check (no DTD/entities, scripts, events, external resources, embedded raster images, or active content). Safe local references, gradients, and masks are not prohibited in themselves. The converter receives an unchanged snapshot of the validated bytes; converter diagnostics conservatively reject that resource rather than allow potential loss of detail. The generated XML is parsed in advance by the Compose parser; the image is still displayed by `painterResource`, not a separate SVG renderer.

The initial XML load uses an additional secure parse before the composable `painterResource`, because Compose does not allow its exception to be caught with a normal `try/catch` around a composable. This is not an automatic visual fidelity check. A local marker of an observed distortion is tied to the SVG/XML hash pair, is not saved in metadata, and does not constitute approval.

## Versions and platforms

Versions of the catalog's external dependencies and Gradle plugins are defined in [`gradle/libs.versions.toml`](gradle/libs.versions.toml); build scripts use `libs` aliases. The platform-specific `compose.desktop.currentOs` dependency remains managed by the Compose plugin, whose version is defined in the same file.

Pinned to match the consumer at `/home/b3er/Projects/zen/mobile/zenmoney`:

| Component | Version | Source in the consumer |
|---|---|---|
| Kotlin / Compose compiler plugin | 2.4.20 | `mobile/gradle/libs.versions.toml` |
| Compose Multiplatform / resources | 1.12.0 | the same version catalog, `mobile-ui/build.gradle.kts` |
| Svg2Vector | `com.android.tools:sdk-common:32.4.0` | version catalog, `mobile-ui/buildSrc` |
| Gradle | 9.7.1 | `mobile-ui/gradle/wrapper/gradle-wrapper.properties` |
| Android Gradle Plugin | 9.4.0 | `mobile/gradle/libs.versions.toml` |
| Android SDK | compileSdk 37, minSdk 26, targetSdk 36 | `mobile/gradle/libs.versions.toml` |
| activity-compose | 1.10.1 | `mobile/gradle/libs.versions.toml` |

Material3 is pinned separately to 1.9.0. The Desktop test environment runs on OpenJDK 21, not the consumer's toolchain 17; this limitation on comparability is explicitly retained.

Shared Compose UI: `:catalog`, `CatalogApp(entries)` on Desktop and Android. Native Android in `:androidApp` uses the same XML through an independent renderer. For iOS, there are `iosArm64`/`iosSimulatorArm64` static `IconCatalog` targets and a native project for the original SVG; see [iosApp/README.md](iosApp/README.md), `iosApp/project.yml`, and `iosApp/native-project.yml`. The native-only original SVG asset can independently package a safe candidate even if the VectorDrawable converter fails; iOS compilation/runtime/rendering on Linux are **unverified**.

## Historical Desktop observation and limitations

2026-09-25: an actual full-catalog generation produced 415 entries, 413 XML files, and 2 converter errors. IDs 15685 (Ozon Bank) and 16060 (Ozon Bank Business): `Semitransparent mask cannot be represented by a vector drawable` on lines 3, 7, 13, 25, and 29. No XML is published for these entries; the current SVGs are preserved. Successful conversion of the other resources does not imply successful rendering.

The final command `./gradlew --no-daemon --offline :generator:test :catalog:desktopTest :catalog:desktopJar :generator:installDist --console=plain` succeeded: 17 generator tests and 2 Desktop state tests, 0 failures/errors/skips. Both entrypoint classes have bytecode major version 61 (Java 17).

An external smoke run of the final version used `./gradlew --no-daemon --offline :catalog:run --console=plain` on Linux KDE Wayland with a real XWayland window, `DISPLAY=:0`. After computer capture failed and Java AX was unavailable, XGetImage/XTest were used only on the catalog window. Neither Xvfb nor a browser was used. An earlier attempt with the desktop locked was not counted; the final images were captured after the user unlocked it.

ID/name search and query preservation were observed; ID3 (54×54), ID15362 (63.8×51.39), ID5165 (width/height54 and a separate viewBox55), and gradients ID5044/4387 — each on light and dark backgrounds. Errors for both Ozon entries were displayed without stale XML; returning to ID15362 displayed the image again. A controlled SVG with `onload` was stopped by the public CLI before rendering: exit code 1, no XML.

18 screenshots, exact SVG/XML hashes, renderer, and limitations: `.scratch/bank-icon-maintenance-implementation/evidence/02-desktop/2026-09-25/`; scenarios and commands are in ticket #02. All 2135 files in `banks/` have the same before/after tree digest: `c3a29fceb2ab1e0fb4bec49310f89f3cd3d74f3b27c59135ca67103aed2f2e16`.

This is a historical observation of selected Desktop samples, not an audit of all 413 XML files or confirmation that the logos are current. Android/iOS were not checked at this stage; a subsequent Android observation is below. Human review is not replaced. A separate scenario involving artificial corruption of already packaged XML when changing the filter was not reproduced; the transition from a real conversion error to a working icon was verified.

## Android observation (2026-09-25)

The actual emulator ran Android 16 / API 36 with hardware GPU gfxstream/HWUI skiagl, 1080×2400, density 420; the image folder's name does not make it API 36.1. IDs 3, 4387, 5044, 5165, and 15362 were visually observed in Compose and native views on light and dark backgrounds; comparison with the Chromium reference and Desktop for the exact SVG/XML hashes showed consistent shapes, gradients, and transparency. Expected raster rounding: native 142 px ≈ 54.095dp; the non-square sample was 167×135 px for source dimensions of 63.8×51.39dp. This is a spot check, not a successful result for the entire catalog.

IDs 15685/16060 (Ozon) explicitly **were not rendered** because conversion failed. For IDs 15358/15687/15878, AAPT successfully built the resources, but actual native inflation failed: native rendering is unconfirmed; returning to ID 4387 was observed. Compose rendered ID 15358. SVGs must not be simplified to bypass an error without a separate fidelity review. On API 36, hardware Back through the dispatcher was verified from the detail view to the native list, preserving the search query. A candidate for ID 3 with a harmless XML comment changed the exact SVG hash while leaving the XML unchanged; after a new build, both Android surfaces displayed the candidate path/hash without a stale local distortion marker. When narrowing 413→1, synchronization removed 412 stale drawables.

Scenarios and evidence: [results.json](../.scratch/bank-icon-maintenance-implementation/evidence/03-android/2026-09-25/results.json). New exact SVG/XML bytes, exceptional resources, or changes to the converter/renderer require another Android check: an earlier successful render is not inherited. This is not human approval or a check of the bank's current identity; iOS compilation/runtime/rendering remain **unverified**.
