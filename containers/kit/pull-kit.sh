#!/bin/sh
# pull-kit: the init container's one job. Fetch a ModelKit by DIGEST, check its cosign signature, unpack it, record the digest.
#
#   pull-kit pull REGISTRY/REPO@sha256:<64 hex> --dest DIR
#
# Fails closed: a tag instead of a digest (exit 2), a missing public key (exit 2), a signature that does not verify (exit 1,
# nothing is unpacked). Environment: WATTSON_COSIGN_PUBKEY = path to the cosign public key (required);
# WATTSON_KIT_INSECURE=1 = the registry speaks plain HTTP (local development registry only).
set -eu
usage() { echo "usage: pull-kit pull REF@sha256:<digest> --dest DIR   (env: WATTSON_COSIGN_PUBKEY required, WATTSON_KIT_INSECURE=1 for a plain-HTTP dev registry)"; }
[ "${1:-}" = "--help" ] && { usage; exit 0; }
[ "${1:-}" = "pull" ] || { usage >&2; exit 2; }
ref="${2:-}"; [ "${3:-}" = "--dest" ] && dest="${4:-}" || { usage >&2; exit 2; }
case "$ref" in *@sha256:*) ;; *) echo "pull-kit: refusing '$ref': a tag is mutable; pin the kit by digest (REF@sha256:...)" >&2; exit 2 ;; esac
digest="${ref##*@}"
echo "$digest" | grep -Eq '^sha256:[0-9a-f]{64}$' || { echo "pull-kit: '$digest' is not a sha256 digest" >&2; exit 2; }
pub="${WATTSON_COSIGN_PUBKEY:-}"
[ -n "$pub" ] && [ -r "$pub" ] || { echo "pull-kit: WATTSON_COSIGN_PUBKEY must name a readable cosign public key; unsigned supply is not allowed" >&2; exit 2; }
insecure_cosign=""; insecure_kit=""
[ "${WATTSON_KIT_INSECURE:-0}" = "1" ] && { insecure_cosign="--allow-insecure-registry"; insecure_kit="--plain-http"; }
echo "pull-kit: verifying the signature of $ref"
# No transparency log: the trust anchor is the public key (see docs/registries and #47).
cosign verify --key "$pub" $insecure_cosign --insecure-ignore-tlog=true "$ref" >/dev/null 2>/tmp/cosign.err \
  || { echo "pull-kit: signature verification FAILED for $ref; nothing unpacked" >&2; tail -n 3 /tmp/cosign.err >&2; exit 1; }
echo "pull-kit: signature verified; unpacking"
export HOME=/tmp/kit-home; mkdir -p "$HOME"
kit unpack $insecure_kit -d "$dest" "$ref"
echo "$digest" > "$dest/.model-digest"
echo "pull-kit: $digest unpacked to $dest"
