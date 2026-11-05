"""Emit the same command/service example as Kubernetes objects, without a YAML dependency.

Run via ./wattson manifest [kubernetes|openshift] and apply to an existing cluster.
This is an actual Job with a native sidecar (Kubernetes >=1.29); Podman kube play
does not stand in for a Kubernetes Job controller in this example.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

# Names are deliberately isolated from existing Wattson application namespaces.
NAMESPACE: str = "wattson-local-example"
JOB_NAME: str = "wattson-ipc-check"
# Kubernetes tutorial resource budgets, not measured Wattson solver requirements.
MEMORY_REQUEST: str = "32Mi"
MEMORY_LIMIT: str = "128Mi"
CPU_REQUEST: str = "100m"
CPU_LIMIT: str = "1"
# Never retry this demonstration automatically: later recipes may have side effects.
NO_RETRIES: int = 0
# Bound the whole invocation including startup; retain evidence for one day.
JOB_DEADLINE_SECONDS: int = 120
JOB_RETENTION_SECONDS: int = 86400
# Startup allowance: thirty 1-second checks. This is not a completion hook.
STARTUP_ATTEMPTS: int = 30
STARTUP_PERIOD_SECONDS: int = 1
# Vanilla K8s needs a shared writable group; OpenShift SCC assigns its own.
EXAMPLE_GROUP: int = 65532


def resources(image: str, platform: str) -> dict[str, Any]:
    """Return a Namespace, deny-network policy, and one bounded Job.

    Args: image: Registry reference accessible by the target nodes.
        platform: 'kubernetes' or 'openshift'; the latter delegates fsGroup to SCC.
    Returns: A Kubernetes List, serialized as JSON (also valid YAML).
    Raises: ValueError for an unknown platform.
    """
    if platform not in {"kubernetes", "openshift"}:
        raise ValueError("platform must be kubernetes or openshift")
    security: dict[str, Any] = {
        "runAsNonRoot": True,
        "seccompProfile": {"type": "RuntimeDefault"},
    }
    if platform == "kubernetes":
        security["fsGroup"] = EXAMPLE_GROUP

    def container(name: str, recipe: str) -> dict[str, Any]:
        """Build a least-capability container spec using the shared command image."""
        return {
            "name": name,
            "image": image,
            "imagePullPolicy": "IfNotPresent",
            "args": [recipe],
            "securityContext": {
                "allowPrivilegeEscalation": False,
                "readOnlyRootFilesystem": True,
                "capabilities": {"drop": ["ALL"]},
            },
            "resources": {
                "requests": {"cpu": CPU_REQUEST, "memory": MEMORY_REQUEST},
                "limits": {"cpu": CPU_LIMIT, "memory": MEMORY_LIMIT},
            },
            "volumeMounts": [{"name": "ipc", "mountPath": "/shared"}],
        }

    service = container("service", "serve")
    # Native sidecar stops automatically when the main command completes.
    service["restartPolicy"] = "Always"
    service["startupProbe"] = {
        "exec": {"command": ["python", "/opt/wattson/ipc.py", "hello"]},
        "periodSeconds": STARTUP_PERIOD_SECONDS,
        "failureThreshold": STARTUP_ATTEMPTS,
    }
    labels = {"app.kubernetes.io/name": "wattson-example"}
    return {
        "apiVersion": "v1",
        "kind": "List",
        "items": [
            {
                "apiVersion": "v1", "kind": "Namespace",
                "metadata": {"name": NAMESPACE, "labels": {
                    "pod-security.kubernetes.io/enforce": "restricted",
                }},
            },
            {
                "apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
                "metadata": {"name": "deny-network", "namespace": NAMESPACE},
                "spec": {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]},
            },
            {
                "apiVersion": "batch/v1", "kind": "Job",
                "metadata": {"name": JOB_NAME, "namespace": NAMESPACE},
                "spec": {
                    "backoffLimit": NO_RETRIES,
                    "activeDeadlineSeconds": JOB_DEADLINE_SECONDS,
                    "ttlSecondsAfterFinished": JOB_RETENTION_SECONDS,
                    "template": {
                        "metadata": {"labels": labels},
                        "spec": {
                            "automountServiceAccountToken": False,
                            "restartPolicy": "Never",
                            "securityContext": security,
                            "initContainers": [service],
                            "containers": [container("command", "check")],
                            "volumes": [{"name": "ipc", "emptyDir": {}}],
                        },
                    },
                },
            },
        ],
    }


if __name__ == "__main__":
    # localhost tags are intentional for Podman; this override MUST name the
    # approved registry when deploying to another node or OpenShift Local VM.
    reference = os.environ.get("WATTSON_KUBE_IMAGE", "localhost/wattson-command:example")
    target = sys.argv[1] if len(sys.argv) > 1 else "kubernetes"
    print(json.dumps(resources(reference, target), indent=2))
