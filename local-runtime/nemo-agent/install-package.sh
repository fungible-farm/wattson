#!/bin/sh
# Run inside an unpacked package, on the same CPU architecture as its images.
set -eu
cd "$(dirname "$0")"
# Restore into a fresh directory so existing source or model state is never overwritten.
[ ! -e source ] || { echo 'source already exists; unpack the package into a fresh directory.' >&2; exit 1; }
sha256sum -c SHA256SUMS
for IMAGE in images/*.tar; do podman load --input "$IMAGE"; done
tar -xzf source.tar.gz
cd source
WATTSON_OFFLINE=1 sh ./wattson install
