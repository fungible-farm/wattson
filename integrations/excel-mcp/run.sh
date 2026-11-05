#!/usr/bin/env bash
# stdout belongs to MCP in stdio mode. All paths resolve independently of the caller's cwd.
set -euo pipefail
integration_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "$integration_dir/../.." && pwd)"
image="${EXCEL_MCP_IMAGE:-localhost/wattson-excel-mcp:2.2.6}"
action="${1:-}"
shift || true
case "$action" in
  build)
    exec podman build --cpu-period=100000 --cpu-quota=200000 --memory=2g --ulimit nproc=256:256 -t "$image" -f "$integration_dir/Containerfile" "$integration_dir" ;;
  check|proof|stdio|serve|diagnose|test) ;;
  *) echo "usage: $0 build | test | stdio WORKBOOK | check WORKBOOK [OUTPUT_DIR] | diagnose WORKBOOK" >&2; exit 2 ;;
esac
args=(run --rm -i --pull=never --network=none --cpus=2 --memory=2g --pids-limit=128
      --read-only --cap-drop=all --security-opt=no-new-privileges --user=65532:65532
      --tmpfs /tmp:rw,nosuid,nodev,size=256m)
if [[ "$action" == test ]]; then
  exec podman "${args[@]}" -v "$repo_dir/tests/test_excel_mcp.py:/opt/excel/test_excel_mcp.py:ro" --entrypoint python "$image" /opt/excel/test_excel_mcp.py
fi
workbook="${1:?pass an absolute workbook file path}"
# Canonicalize the parent without requiring host Python or GNU realpath.
workbook="$(cd -- "$(dirname -- "$workbook")" && pwd)/$(basename -- "$workbook")"
[[ -f "$workbook" && -r "$workbook" ]] || { echo "workbook is not a readable file: $workbook" >&2; exit 2; }
name="$(basename -- "$workbook")"
args+=(-v "$workbook:/input/$name:ro" -e "EXCEL_WORKBOOK_NAME=$name")
case "$action" in
  stdio|serve) exec podman "${args[@]}" "$image" ;;
  diagnose) exec podman "${args[@]}" --entrypoint python "$image" /opt/excel/probe.py ;;
  check|proof)
    output="${2:-$repo_dir/out/excel-mcp-check}"
    mkdir -p -- "$output"
    rm -f -- "$output/excel-proof.json" "$output/excel-proof.json.partial"
    trap 'rm -f -- "$output/excel-proof.json.partial"' EXIT
    podman "${args[@]}" --entrypoint python "$image" /opt/excel/smoke.py > "$output/excel-proof.json.partial"
    mv -- "$output/excel-proof.json.partial" "$output/excel-proof.json"
    cat -- "$output/excel-proof.json" ;;
esac
