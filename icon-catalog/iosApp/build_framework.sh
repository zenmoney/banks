#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'Usage: bash iosApp/build_framework.sh prepare|build|prepare-native|build-native\n' >&2
  printf '  prepare: generate combined Compose/native resources before xcodegen generate\n' >&2
  printf '  build: prepare combined resources and run the Xcode framework integration task\n' >&2
  printf '  prepare-native: generate original-SVG assets before xcodegen --spec native-project.yml\n' >&2
  printf '  build-native: refresh original-SVG assets from the native-only Xcode build phase\n' >&2
  printf 'Environment: CATALOG_SOURCE_ROOT, CATALOG_BANKS, CATALOG_ID, CATALOG_CANDIDATE\n' >&2
}

if [[ $# != 1 ]]; then
  usage
  exit 2
fi
case $1 in
  prepare|build|prepare-native|build-native) ;;
  *) usage; exit 2 ;;
esac
mode=$1
if [[ $mode == build ]]; then
  : "${CONFIGURATION:?Run build from Xcode with CONFIGURATION set}"
  : "${SDK_NAME:?Run build from Xcode with SDK_NAME set}"
  : "${TARGET_BUILD_DIR:?Run build from Xcode with TARGET_BUILD_DIR set}"
fi
app_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
catalog_dir=$(cd "$app_dir/.." && pwd)
default_source_root=$(cd "$catalog_dir/.." && pwd)
banks=${CATALOG_BANKS:-${CATALOG_SOURCE_ROOT:-$default_source_root}/banks}
banks=$(cd "$banks" && pwd)
source_root=${CATALOG_SOURCE_ROOT:-$(cd "$banks/.." && pwd)}
source_root=$(cd "$source_root" && pwd)

gradle_options=("-PcatalogBanks=$banks")
package_options=()
if [[ -n ${CATALOG_ID:-} ]]; then
  gradle_options+=("-PcatalogId=$CATALOG_ID")
fi
if [[ -n ${CATALOG_CANDIDATE:-} ]]; then
  if [[ -z ${CATALOG_ID:-} ]]; then
    printf 'CATALOG_CANDIDATE requires CATALOG_ID\n' >&2
    exit 2
  fi
  candidate=$(cd "$(dirname "$CATALOG_CANDIDATE")" && pwd)/$(basename "$CATALOG_CANDIDATE")
  gradle_options+=("-PcatalogCandidate=$candidate")
  package_options+=(--candidate "$CATALOG_ID=$candidate")
fi

cd "$catalog_dir"
if [[ $mode == prepare-native || $mode == build-native ]]; then
  # A fresh generator output makes exit 1 usable only for this run's selected
  # converter error, never for an old manifest left from an earlier build.
  mkdir -p "$app_dir/build"
  native_output=$(mktemp -d "$app_dir/build/native-catalog.XXXXXXXX")
  trap 'rm -rf -- "$native_output"' EXIT
  ./gradlew :generator:installDist
  generator_args=(--banks "$banks" --output "$native_output")
  if [[ -n ${CATALOG_ID:-} ]]; then generator_args+=(--id "$CATALOG_ID"); fi
  if [[ -n ${CATALOG_CANDIDATE:-} ]]; then generator_args+=(--candidate "$candidate"); fi
  if "$catalog_dir/generator/build/install/generator/bin/generator" "${generator_args[@]}"; then
    :
  else
    status=$?
    if [[ $status != 1 || -z ${CATALOG_ID:-} ]]; then exit "$status"; fi
  fi
  if [[ ! -f $native_output/manifest.json ]]; then
    printf 'Generator did not produce a fresh native manifest\n' >&2
    exit 2
  fi
  if [[ -n ${CATALOG_ID:-} ]]; then package_options+=(--require-native "$CATALOG_ID"); fi
  python3 "$app_dir/prepare_assets.py" \
    --manifest "$native_output/manifest.json" \
    --source-root "$source_root" \
    --output "$app_dir/NativeGenerated" "${package_options[@]}"
else
  ./gradlew "${gradle_options[@]}" :catalog:generateCatalogResources
  python3 "$app_dir/prepare_assets.py" \
    --manifest "$catalog_dir/catalog/build/generated/catalog/manifest.json" \
    --source-root "$source_root" \
    --output "$app_dir/Generated" "${package_options[@]}"

  if [[ $mode == build ]]; then
    # Direct Xcode integration builds/signs the appropriate iOS Kotlin framework.
    ./gradlew "${gradle_options[@]}" :catalog:embedAndSignAppleFrameworkForXcode
  fi
fi
