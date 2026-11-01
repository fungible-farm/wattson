"""Turn the rendered quick-start pod into one that needs no GPU and no model download, for testing.

Reads the rendered kube YAML on stdin and writes the reduced YAML on stdout: the pod gets a new name (so a real ``wattson`` pod is
untouched), the model-pulling init container and the vLLM server are removed, and the models volume becomes an empty directory.
What is left is the harness and the case-file fetch, which is everything that does not depend on a GPU.

    python pod_filter.py NEW_NAME < rendered.yaml > reduced.yaml
"""

from __future__ import annotations

import sys

import yaml

DROPPED_INIT: frozenset[str] = frozenset({"pull-model"})
DROPPED_CONTAINERS: frozenset[str] = frozenset({"vllm"})


def reduce(pod: dict, name: str) -> dict:
    pod["metadata"]["name"] = name
    spec = pod["spec"]
    spec["initContainers"] = [c for c in spec.get("initContainers", []) if c["name"] not in DROPPED_INIT]
    spec["containers"] = [c for c in spec["containers"] if c["name"] not in DROPPED_CONTAINERS]
    for volume in spec["volumes"]:
        if volume["name"] == "models":
            volume.pop("persistentVolumeClaim")
            volume["emptyDir"] = {}
        if volume["name"] == "cases":
            volume["persistentVolumeClaim"]["claimName"] = f"{name}-cases"
    return pod


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    yaml.safe_dump(reduce(yaml.safe_load(sys.stdin), sys.argv[1]), sys.stdout, sort_keys=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
