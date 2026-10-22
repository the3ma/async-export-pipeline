#!/usr/bin/env bash
# Build a deployable Lambda zip (linux / arm64 / CPython 3.12) without Docker.
#
#   infra/scripts/build-lambda-artifact.sh worker [out.zip]   # default: infra/.build/worker.zip
#   infra/scripts/build-lambda-artifact.sh api    [out.zip]
#
# Then deploy exactly that artifact:
#   npx cdk deploy -c stage=prod -c workerArtifact=.build/worker.zip -c apiArtifact=.build/api.zip
#
# Third-party dependencies are taken from the uv lock file (exact pins) as binary wheels for the
# Lambda platform; the workspace packages (exportkit + the service) are installed without deps.
# Mirrors the Docker bundling command in lib/constructs/python-code.ts - keep them in sync.
# Requires: uv >= 0.11 on PATH.
set -euo pipefail

component="${1:-}"
case "$component" in
  worker) package="export-worker" ;;
  api) package="export-api" ;;
  *) echo "usage: $0 worker|api [out.zip]" >&2; exit 2 ;;
esac

infra_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_root="$(cd "$infra_dir/.." && pwd)"
out_zip="${2:-$infra_dir/.build/$component.zip}"
mkdir -p "$(dirname "$out_zip")"
out_zip="$(cd "$(dirname "$out_zip")" && pwd)/$(basename "$out_zip")"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
target="$work/package"
# Lambda python3.12 runs on Amazon Linux 2023 (glibc 2.34), so manylinux_2_28 wheels are compatible.
platform=(--python-platform aarch64-manylinux_2_28 --python-version 3.12)

cd "$repo_root"
uv export --frozen --no-dev --no-emit-workspace --no-hashes --package "$package" -o "$work/requirements.txt" >/dev/null
uv pip install --quiet --target "$target" "${platform[@]}" --only-binary :all: -r "$work/requirements.txt"
uv pip install --quiet --no-deps --target "$target" "${platform[@]}" services/shared "services/$component"
find "$target" -name '__pycache__' -prune -exec rm -rf {} +

rm -f "$out_zip"
# Zip with the Python that uv manages, so the script does not depend on a `zip` binary.
(cd "$target" && uv run --no-project --quiet --python 3.12 python -m zipfile -c "$out_zip" ./*)
echo "built $out_zip ($(du -h "$out_zip" | cut -f1))"
