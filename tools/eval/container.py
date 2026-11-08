"""Run a GitHub task's install and tests in a throwaway container, never on this machine.

Each command runs in its own `docker run --rm` from one pinned image, as a non-root user, with
the project copy as its only mount: no home directory, no SSH agent or git credentials, no
Docker socket, and no host environment (`docker run` passes none it isn't given). Install
steps have the network, to fetch packages; the test run has none. Every run is capped in
memory, CPUs, and processes, and timed out inside the container and again from here. One
container runs at a time, whatever the caller's concurrency.
"""

import os
import subprocess
import threading
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Literal

# uv's image on python:3.12-slim (Debian trixie), pinned by the digest of its multi-platform
# index, so the same image runs on arm64 and amd64. A task on another Python has uv download
# that interpreter while installing.
IMAGE = (
    "ghcr.io/astral-sh/uv:0.13.2-python3.12-trixie-slim"
    "@sha256:28fc862bfa983d170261af530e5cf74835112f886b6dd235966747ad1472c018"
)
MOUNT = "/project"  # where the project copy is mounted, and the working directory
MEMORY = "2g"  # also the swap limit: no swap beyond it
CPUS = "2"
PIDS = "512"
TEST_SECONDS = 120  # a task's tests run in under 2 minutes (docs/eval/README.md)
INSTALL_SECONDS = 600  # each install step: the venv, the setup, the pins
GRACE_SECONDS = 30  # past the container's own timeout, the harness removes it
NOBODY = "65534:65534"  # the container's user when this machine's user is root
TIMED_OUT = 124  # timeout(1)'s exit status
Phase = Literal["install", "test"]
_ONE_AT_A_TIME = threading.Lock()


def seconds(phase: Phase) -> int:
    """Return a phase's time limit inside the container."""
    return TEST_SECONDS if phase == "test" else INSTALL_SECONDS


def user() -> str:
    """Return the container's uid:gid: this machine's user (it owns the mount), never root."""
    uid = os.getuid()
    return f"{uid}:{os.getgid()}" if uid else NOBODY


def docker_command(command: list[str], project: Path, phase: Phase, name: str) -> list[str]:
    """Return the `docker run` that runs command in project, in a container named name."""
    network = ["--network", "none"] if phase == "test" else []
    return [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--user",
        user(),
        "--memory",
        MEMORY,
        "--memory-swap",
        MEMORY,
        "--cpus",
        CPUS,
        "--pids-limit",
        PIDS,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        *network,
        "--mount",
        f"type=bind,source={project.resolve()},target={MOUNT}",
        "--workdir",
        MOUNT,
        "--env",
        "HOME=/tmp",  # uv's cache lives in the container and goes with it
        IMAGE,
        "timeout",
        "--kill-after=5",
        str(seconds(phase)),
        *command,
    ]


def run(command: list[str], project: Path, phase: Phase) -> "subprocess.CompletedProcess[str]":
    """Run command in project inside a fresh container; return its exit status and output.

    The returned process names `command`, not the `docker run` around it. A run past its time
    limit exits TIMED_OUT, whether the container stopped itself or the harness removed it.
    """
    name = f"commons-eval-{uuid.uuid4().hex[:12]}"
    limit = seconds(phase)
    with _ONE_AT_A_TIME:
        try:
            done = subprocess.run(
                docker_command(command, project, phase, name),
                capture_output=True,
                text=True,
                check=False,
                timeout=limit + GRACE_SECONDS,
            )
        except subprocess.TimeoutExpired:
            remove = ["docker", "rm", "--force", name]
            with suppress(subprocess.TimeoutExpired):  # a hung daemon doesn't hang the harness
                subprocess.run(remove, capture_output=True, check=False, timeout=GRACE_SECONDS)
            return subprocess.CompletedProcess(command, TIMED_OUT, "", f"timed out after {limit}s")
    return subprocess.CompletedProcess(command, done.returncode, done.stdout, done.stderr)
