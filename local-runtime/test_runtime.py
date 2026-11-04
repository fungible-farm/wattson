"""Protocol and packaging checks; proof.sh additionally exercises real containers."""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import ipc
import kubernetes

# Bounded subprocess startup wait for the two-process IPC regression.
STARTUP_SECONDS: float = 5.0
POLL_SECONDS: float = 0.02
# unittest exercises exact protocol bytes; no floating-point tolerance applies.
EXPECTED_FIXTURE: Path = Path(__file__).with_name("expected_check.json")


class RuntimeTest(unittest.TestCase):
    """Check real IPC and restricted pod behavior before a deployment proof."""

    def test_real_socket_fifo_and_shutdown(self) -> None:
        """An independent service process must return fixture bytes and stop cleanly."""
        with tempfile.TemporaryDirectory(prefix="wattson-") as directory:
            environment = dict(os.environ, WATTSON_SHARED=directory)
            server = subprocess.Popen(
                [sys.executable, str(Path(ipc.__file__)), "serve"],
                env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            try:
                deadline = time.monotonic() + STARTUP_SECONDS
                while not (Path(directory) / "service.sock").exists():
                    if server.poll() is not None or time.monotonic() >= deadline:
                        self.fail("service failed to start")
                    time.sleep(POLL_SECONDS)
                with patch.dict(os.environ, environment), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(ipc.check(), json.loads(EXPECTED_FIXTURE.read_text()))
                    self.assertEqual(ipc.request("unknown"), "unknown operation")
            finally:
                server.terminate()
                output, _ = server.communicate(timeout=STARTUP_SECONDS)
            self.assertEqual(server.returncode, 0, output)
            self.assertIn("service_started", output)
            self.assertIn("service_stopped", output)
            self.assertFalse((Path(directory) / "service.sock").exists())

    def test_deliberate_failure_is_nonzero(self) -> None:
        """The diagnostic failure must never be mistaken for a successful command."""
        process = subprocess.run(
            [sys.executable, str(Path(ipc.__file__)), "fail"], capture_output=True,
        )
        self.assertEqual(process.returncode, ipc.DEMO_FAILURE)

    def test_job_has_bounded_no_retry_lifecycle(self) -> None:
        """Native sidecar startup and Job policy bound command execution."""
        manifest = kubernetes.resources("registry.example/command@sha256:example", "kubernetes")
        job = manifest["items"][-1]["spec"]
        self.assertEqual(job["backoffLimit"], 0)
        self.assertGreater(job["activeDeadlineSeconds"], 0)
        pod = job["template"]["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertEqual(pod["initContainers"][0]["restartPolicy"], "Always")
        self.assertIn("startupProbe", pod["initContainers"][0])
        self.assertNotIn("hostPID", pod)
        self.assertNotIn("hostNetwork", pod)
        self.assertEqual(pod["volumes"], [{"name": "ipc", "emptyDir": {}}])

    def test_openshift_does_not_force_a_uid_or_group(self) -> None:
        """SCC must be able to supply the namespace's permitted group and UID."""
        job = kubernetes.resources("registry.example/command:test", "openshift")["items"][-1]
        pod = job["spec"]["template"]["spec"]
        self.assertNotIn("fsGroup", pod["securityContext"])
        self.assertNotIn("runAsUser", pod["securityContext"])
        for container in pod["containers"] + pod["initContainers"]:
            self.assertFalse(container["securityContext"]["allowPrivilegeEscalation"])


if __name__ == "__main__":
    unittest.main()
