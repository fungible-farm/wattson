#!/usr/bin/env bash
# Evidence for the admission gates of an evaluated candidate: image architecture, rootless and offline execution, licence text, pinned dependencies.
# usage: scripts/ontology_eval/gates.sh IMAGE LICENSE_PATH_IN_IMAGE "DEPENDENCY_LISTING_COMMAND"   e.g. ... localhost/x:local /opt/x/LICENSE "pip freeze"
# Every line is a command's output, to be pasted verbatim into docs/ontology-eval-<name>.md. Nothing here infers.
set -u
image=$1; lic=$2; pin=${3:-pip freeze}
echo "host_arch=$(uname -m)  podman_rootless=$(podman info --format '{{.Host.Security.Rootless}}')"
echo "image=$image id=$(podman image inspect "$image" --format '{{.Id}}') arch=$(podman image inspect "$image" --format '{{.Architecture}}/{{.Os}}') size_bytes=$(podman image inspect "$image" --format '{{.Size}}')"
echo "--- rootless, offline run (--network=none, uid/gid mapped to the invoking user)"
podman run --rm --network=none --userns=keep-id:uid="$(id -u)",gid="$(id -g)" --entrypoint sh "$image" -c 'echo "uid=$(id -u) machine=$(uname -m) interfaces=$(ls /sys/class/net | tr "\n" " ")"'
echo "--- licence file $lic"
podman run --rm --network=none --entrypoint sh "$image" -c "sha256sum '$lic' && head -12 '$lic'"
echo "--- pinned dependencies: $pin"
podman run --rm --network=none --entrypoint sh "$image" -c "$pin"
