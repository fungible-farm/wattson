#!/usr/bin/env bash
# One blob, at most 256 MiB: an intentionally small local staging example.
set -euo pipefail
readonly MAX_BLOB_BYTES=268435456
account=$1
container=$2
blob=$3
# Staging uses a new volume per dataset. Never overwrite a completed prior download.
mkdir /stage/.download-lock
trap 'rm -f /stage/model.xml.partial; rmdir /stage/.download-lock' EXIT
test ! -e /stage/model.xml
metadata=$(az storage blob show --auth-mode login --account-name "$account" \
    --container-name "$container" --name "$blob" --only-show-errors \
    --query '{size:properties.contentLength,etag:properties.etag}' --output json)
readarray -t fields < <(python3 -c 'import json,sys; x=json.load(sys.stdin); print(x["size"]); print(x["etag"])' <<< "$metadata")
size=${fields[0]}
etag=${fields[1]}
[[ "$size" =~ ^[0-9]+$ ]] && (( size <= MAX_BLOB_BYTES )) || {
    echo 'Blob exceeds the 256 MiB example limit or has an invalid size' >&2
    exit 1
}
# Matching the metadata ETag prevents a different/larger revision being downloaded.
az storage blob download --auth-mode login --account-name "$account" \
    --container-name "$container" --name "$blob" --if-match "$etag" \
    --file /stage/model.xml.partial --overwrite false --max-connections 1 \
    --timeout 300 --no-progress --only-show-errors --output none
[[ $(stat -c %s /stage/model.xml.partial) == "$size" ]]
chmod 0444 /stage/model.xml.partial
ln /stage/model.xml.partial /stage/model.xml
rm /stage/model.xml.partial
