# iOS catalog calibration

Two surfaces in the combined application and a separate **native-only application**, providing checks that are not interchangeable:

- **Compose XML**: `catalog.MainViewController()` → shared `CatalogApp(generatedCatalog())` → Compose iOS / Skia `painterResource` using derived VectorDrawable XML.
- **Original SVG**: SwiftUI interface → `UIImage` → compiled Xcode asset catalog. The imageset receives the exact bytes of the **original SVG**, not XML, PNG, or a rewritten SVG. Apple performs its own asset processing; compatibility and rendering fidelity require observation.

KMP targets: `iosArm64`, `iosSimulatorArm64`; static framework `IconCatalog`; deployment target iOS 16.0. Intel Simulator (`iosX64`) is not included. Shared pipeline pins: Kotlin 2.4.20, Compose 1.12.0, `sdk-common` 32.4.0, Gradle 9.7.1; Material3 1.9.0. The native renderer consists of the Apple asset compiler and UIKit from the specific Xcode/iOS versions; record their versions when running, rather than substituting the Compose version.

## Status as of 2026-09-25

The controller, two XcodeGen projects, combined host, independent native-only host, and actual packaging are implemented. **The iOS build, runtime, and visual calibration of both surfaces HAVE NOT BEEN VERIFIED.** The execution environment is Linux without Apple SDK/Xcode/runtime; the user confirmed that no suitable ARM host is available. ARM alone is insufficient: supported macOS/Xcode/SDK/runtime versions are required. Parsing Swift syntax on Linux is not iOS type checking or a build. Desktop results are not iOS results.

[Evidence №04](../../.scratch/bank-icon-maintenance-implementation/evidence/04-ios/2026-09-25/) separately contains an imported prerequisite observation, checks that could be performed, and a version comparison. An unavailable environment is not reported as successful calibration.

## Running on a suitable Mac

An Apple Silicon Mac, Xcode and an iOS Simulator runtime compatible with the pinned Kotlin/Compose versions, selected Xcode command-line tools, JDK 21, Python 3.9+, and XcodeGen are required. CocoaPods is not used. SDK installation, license acceptance, signing configuration, and credentials are outside the harness's automated actions.

From `icon-catalog/`:

```sh
# Record the actual environment versions before calibration.
xcodebuild -version
xcodegen --version
xcrun simctl list runtimes
java -version
python3 --version

# Host regeneration: resources first, then the project with actual resource references.
bash iosApp/build_framework.sh prepare
cd iosApp
xcodegen generate --spec project.yml
xcrun simctl list devices available
```

Choose an actual available arm64 Simulator and substitute its UDID:

```sh
UDID='<selected UDID>'
xcodebuild -project IconCatalogIOS.xcodeproj -scheme IconCatalogIOS \
  -configuration Debug -destination "id=$UDID" \
  -derivedDataPath build/DerivedData CODE_SIGNING_ALLOWED=NO build
# boot is needed only if the selected Simulator is still shut down.
xcrun simctl boot "$UDID"
xcrun simctl bootstatus "$UDID" -b
xcrun simctl install "$UDID" build/DerivedData/Build/Products/Debug-iphonesimulator/IconCatalogIOS.app
xcrun simctl launch "$UDID" org.zen.iconcatalog
open -a Simulator
```

Open both tabs, find the bank by ID/name, open the detail view, and check both backgrounds. Save a separate screenshot for each screen actually displayed, for example:

```sh
xcrun simctl io "$UDID" screenshot /path/to/evidence/ios-native-3-light.png
```

A screenshot does not automatically establish a visual pass: the observer describes the image and discrepancies. When finished, stop the application: `xcrun simctl terminate "$UDID" org.zen.iconcatalog`.

`build_framework.sh build` is called by the Xcode pre-build phase. It prepares resources again and runs the standard `:catalog:embedAndSignAppleFrameworkForXcode`, including Compose resource integration. Do not run this phase manually in place of Xcode with fabricated environment variables.

## Portable baseline and candidate

By default, resources are generated on the host from `banks/` in this checkout. Absolute Linux paths from another manifest are not used as a fallback.

```sh
CATALOG_BANKS=/available/worktree/banks bash iosApp/build_framework.sh prepare
CATALOG_BANKS=/available/worktree/banks CATALOG_ID=3 \
  CATALOG_CANDIDATE=/available/candidates/final.svg bash iosApp/build_framework.sh prepare
```

Pass the same variables to the Xcode build (`xcodebuild` inherits the environment); otherwise, pre-build will select the default baseline again. When explicitly provided, `CATALOG_SOURCE_ROOT` must resolve the manifest's relative `sourcePath` values; for the generator, this is the parent of `CATALOG_BANKS`. Sources and metadata are not modified. The candidate replaces only the input for the selected existing ID.

To transfer a **ready-made** manifest together with XML and an accessible source tree, packaging can be run independently, without presenting it as an application run:

```sh
python3 iosApp/prepare_assets.py \
  --manifest /available/generated/manifest.json \
  --source-root /available/worktree \
  --output iosApp/Generated
# An absolute candidate sourcePath requires an explicit, current mapping:
# add --candidate 3=/available/candidates/final.svg
```

`sourceSha256` is rechecked against the exact bytes. The XML hash is also checked if XML is present in the manifest contract. An inaccessible source, changed bytes, an incorrect path, or invalid XML causes a nonzero exit; an invalid package is not published. The previous complete package remains unchanged, and the Xcode build stops. An already installed old application is not updated: check the displayed hashes and manifest hash before drawing conclusions about the new version.

`sourceValidated: true` means only that the hash-bound bytes passed the **generator's current safety gate**. It does not promise Apple compatibility, image quality, or human approval. If the marker is false or absent, the entry receives a native error and **does not receive an asset**; regenerate an old manifest rather than inferring safety from error text.

A safe SVG with an XML converter error can enter the native asset catalog: the full bulk catalog retains such an entry regardless of missing XML (for example, IDs 15685/16060). Under the existing generator contract, a selected `CATALOG_ID`/candidate with a conversion error **does not start the combined Xcode build**; use the independent native-only path below instead. An `actool` error is likewise not hidden or replaced with PNG/XML.

## Independent native probe for an exceptional candidate

`native-project.yml` creates a separate `NativeIconCatalogIOS` without a Compose framework, XML resource dependency, or second SVG validator. It uses the same `NativeCatalogView` and packaging CLI, with output in `NativeGenerated/`. From `icon-catalog/`:

```sh
export CATALOG_BANKS=/available/worktree/banks
export CATALOG_ID=3
export CATALOG_CANDIDATE=/available/candidates/final.svg
bash iosApp/build_framework.sh prepare-native
cd iosApp
xcodegen generate --spec native-project.yml
xcodebuild -project NativeIconCatalogIOS.xcodeproj -scheme NativeIconCatalogIOS \
  -configuration Debug -destination "id=$UDID" \
  -derivedDataPath build/NativeDerivedData CODE_SIGNING_ALLOWED=NO build
xcrun simctl install "$UDID" build/NativeDerivedData/Build/Products/Debug-iphonesimulator/NativeIconCatalogIOS.app
xcrun simctl launch "$UDID" org.zen.iconcatalog.native
```

Select and boot the Simulator as in the previous section; the selection variables also remain set for the Xcode pre-build phase. For an existing icon, leave `CATALOG_CANDIDATE` unset. Run native-only bulk mode without an ID/candidate. Save screenshots and visual observations separately from Compose. Stop with: `xcrun simctl terminate "$UDID" org.zen.iconcatalog.native`.

Each native prepare creates **its own fresh temporary output** and runs the installed JVM generator. Exit 1 is allowed only for the selected entry and does not itself authorize anything: a new manifest is required, then `--require-native ID` requires exactly the selected validated entry with an asset, the exact source hash, and an explicit candidate mapping. An unsafe source, a different/missing ID, inaccessible/changed bytes, a crash, or the absence of a new manifest stops the build; an old manifest is not used. Exit 2 remains an error. Temporary output is removed after preparation; the native manifest retains its SHA-256 and provenance. Successful native packaging does not establish successful Compose conversion, an iOS build, or calibration.

## Calibration protocol

The card area is 54×54pt; the native detail view displays the intrinsic `UIImage` size without fitting/cropping, with scrolling, and separately displays raw SVG width/height/viewBox and unambiguous source dimensions. Apple's intrinsic size is not presented as the original SVG dimensions. The detail view has no second scaled-down preview. Switching backgrounds and searching do not change the SVG.

The initial representative set is aligned with Desktop evidence №02: 3 (ordinary geometry), 4387/5044 (graphic variants), 5165 (`viewBox` 55 with dimensions 54), 15362 (63.8×51.39), 15685/16060 (safe SVG, unsupported semitransparent mask in XML). Before applying a Desktop conclusion, check **both** SVG/XML versions, pins, and renderer configuration. Different versions require a separate check.

For each version, record: ID/title, SVG/XML hashes, source path, pins, Xcode/SDK/iOS/device/scale, renderer, size, background, setup/expected/observed, screenshot path, discrepancies/loss of detail, and limitations. Compare the source, Desktop XML, Compose iOS XML, and native original SVG. A native error does not imply Compose success, or vice versa; an error/no image is not a visual pass.

Recalibration is mandatory for an exceptional candidate and after changes to SVG capabilities, the converter, renderer configuration, or the bytes being checked. Do not carry a previous result over to a new version. A full iOS run is not required for every ordinary candidate; disclose the residual platform risk to the human reviewer. The harness is read-only: it does not accept an icon, confirm that a logo is current, or publish an MR/state.

## Automated checks that can be performed

```sh
python3 -m unittest discover -s icon-catalog/iosApp/tests -p test_prepare_assets.py -v
```

Run this command from the repository root. These are subprocess checks of the public packaging CLI: path/hash errors, portability, source safety, and preservation of the original bytes and the previous complete package. They do not replace `actool`, an iOS build, runtime execution, or visual observation.
