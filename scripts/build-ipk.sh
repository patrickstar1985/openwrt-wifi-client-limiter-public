#!/bin/sh
# SPDX-License-Identifier: MIT
set -eu
task_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
output="${1:-$task_root/dist}"
mkdir -p "$output"
output="$(CDPATH= cd -- "$output" && pwd)"
stage="$(mktemp -d "${TMPDIR:-/tmp}/wifi-client-limiter-ipk.XXXXXX")"
trap 'rm -rf "$stage"' EXIT INT TERM
python3 "$task_root/packaging/stage-package.py" "$stage/package" ipk
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1767225600}"
export OPENWRT_PACKAGE_TAR_BIN="$(command -v tar)"
cp "$task_root/packaging/vendor/root-tar" "$stage/root-tar"
chmod 755 "$stage/root-tar"
export TAR="$stage/root-tar"
bash "$task_root/packaging/vendor/ipkg-build" "$stage/package" "$output"
cd "$output"
find . -maxdepth 1 -type f \( -name '*.ipk' -o -name '*.apk' -o -name '*.pub' \) -print | sort | xargs sha256sum > SHA256SUMS
