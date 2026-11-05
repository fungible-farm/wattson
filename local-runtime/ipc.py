"""Real local IPC demonstration, not a power-system solver or an MCP substitute.

The service and client run in separate containers in the deployment proof.
Only the shared directory is needed: neither requires host process access.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import socket
import stat
import sys
import time
import uuid
from typing import TypedDict

# Tutorial protocol strings, intentionally independent of physical study results.
SOCKET_MESSAGE: str = "hello from the packaged service"
FIFO_MESSAGE: str = "message through a named pipe"
# Bound broken peers in a live tutorial; these are operational limits, not physics tolerances.
TIMEOUT_SECONDS: float = 10.0
MAX_MESSAGE_BYTES: int = 4096
POLL_SECONDS: float = 0.1
# Deliberately recognizable failure code for the inspect exercise.
DEMO_FAILURE: int = 17
# One pending connection is enough for the sequential tutorial.
SOCKET_BACKLOG: int = 1


class CheckResult(TypedDict):
    """The two independently verified IPC transports."""

    socket: str
    fifo: str


def shared_directory() -> Path:
    """Return the configured shared volume path; tests use a temporary directory."""
    return Path(os.environ.get("WATTSON_SHARED", "/shared"))


def request(message: str) -> str:
    """Send a bounded request to the service and return its newline-delimited reply.

    Args: message: A protocol command, not executable shell text.
    Raises: OSError or TimeoutError if the service is unavailable.
    """
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(TIMEOUT_SECONDS)
        client.connect(str(shared_directory() / "service.sock"))
        client.sendall((message + "\n").encode())
        with client.makefile("rb") as stream:
            return stream.readline(MAX_MESSAGE_BYTES).decode().strip()


def check() -> CheckResult:
    """Verify real socket and named-pipe bytes against the committed fixture.

    A nonblocking FIFO reader is opened before asking the other process to write,
    so a missing writer is bounded by a timeout rather than an indefinite open().
    Returns: The checked protocol values. Raises: AssertionError on any mismatch.
    """
    directory = shared_directory()
    pipe = directory / ("reply-" + uuid.uuid4().hex + ".fifo")
    os.mkfifo(pipe)
    descriptor = os.open(pipe, os.O_RDONLY | os.O_NONBLOCK)
    try:
        response = request("fifo " + pipe.name)
        if response != "written":
            raise AssertionError(response)
        deadline = time.monotonic() + TIMEOUT_SECONDS
        data = b""
        while not data.endswith(b"\n"):
            if time.monotonic() >= deadline:
                raise TimeoutError("named-pipe reply timed out")
            try:
                data += os.read(descriptor, MAX_MESSAGE_BYTES)
            except BlockingIOError:
                pass
            time.sleep(POLL_SECONDS)
        result: CheckResult = {"socket": request("hello"), "fifo": data.decode().strip()}
        expected = json.loads(Path(__file__).with_name("expected_check.json").read_text())
        if result != expected:
            raise AssertionError(f"IPC mismatch: {result!r} != {expected!r}")
        print(json.dumps(result, sort_keys=True), flush=True)
        print("PASS: socket + FIFO round trips", flush=True)
        return result
    finally:
        os.close(descriptor)
        pipe.unlink(missing_ok=True)


def serve() -> None:
    """Serve the tutorial's two fixed operations until SIGTERM/SIGINT.

    The service logs normal start/stop; the external runtime records termination
    independently, including cases where SIGKILL prevents this cleanup.
    """
    directory = shared_directory()
    directory.mkdir(parents=True, exist_ok=True)
    address = directory / "service.sock"
    address.unlink(missing_ok=True)
    stopping = False

    def stop(signum: int, frame: object) -> None:
        """Ask the bounded accept loop to stop on a runtime termination signal."""
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(address))
        server.listen(SOCKET_BACKLOG)
        server.settimeout(POLL_SECONDS)
        print("service_started", flush=True)
        try:
            while not stopping:
                try:
                    connection, _ = server.accept()
                except TimeoutError:
                    continue
                with connection:
                    connection.settimeout(TIMEOUT_SECONDS)
                    try:
                        with connection.makefile("rb") as stream:
                            message = stream.readline(MAX_MESSAGE_BYTES).decode().strip()
                        reply = "unknown operation"
                        if message == "hello":
                            reply = SOCKET_MESSAGE
                        elif message.startswith("fifo "):
                            name = message.removeprefix("fifo ")
                            pipe = directory / name
                            if Path(name).name != name or not name.startswith("reply-"):
                                raise ValueError("invalid tutorial pipe name")
                            if not stat.S_ISFIFO(pipe.lstat().st_mode):
                                raise ValueError("expected a FIFO")
                            descriptor = os.open(pipe, os.O_WRONLY | os.O_NONBLOCK)
                            try:
                                os.write(descriptor, (FIFO_MESSAGE + "\n").encode())
                            finally:
                                os.close(descriptor)
                            reply = "written"
                        connection.sendall((reply + "\n").encode())
                    except (OSError, ValueError) as error:
                        print(f"request_failed: {error}", flush=True)
        finally:
            address.unlink(missing_ok=True)
            print("service_stopped", flush=True)


def main() -> None:
    """Dispatch a fixed tutorial operation and preserve intentional failure status."""
    operation = sys.argv[1]
    if operation == "serve":
        serve()
    elif operation == "hello":
        print(request("hello"), flush=True)
    elif operation == "check":
        check()
    elif operation == "fail":
        print("Intentional demonstration failure; inspect this invocation.", flush=True)
        raise SystemExit(DEMO_FAILURE)
    else:
        raise SystemExit(f"unknown operation: {operation}")


if __name__ == "__main__":
    main()
