"""Start the shell-resolved Identity Manager and wait for its SSO readiness."""

import os
import pathlib
import re
import subprocess
import sys
import time
from enum import Enum
from typing import BinaryIO

READY_MESSAGE = "Identity Manager is ready."
FAILED_MESSAGE = "Identity Manager failed to start. Inspect the launch log; no processes were killed."
TIMEOUT_MESSAGE = "Identity Manager did not report readiness within 30 seconds. Close Fusion and try 'fusion360 stop'; no account data was reset or processes killed."


class Lifecycle(Enum):
    STARTING = "starting"
    READY = "ready"
    STOPPED = "stopped"


class LogReader:
    """Keep an open log's position and an incomplete fresh line across polls."""

    def __init__(self, path: pathlib.Path) -> None:
        self.file: BinaryIO = path.open("rb")
        self.pending: bytes = b""
        self.offset: int = 0

    def read_lines(self) -> list[bytes]:
        if os.fstat(self.file.fileno()).st_size < self.offset:
            self.offset = 0
            self.pending = b""
        self.file.seek(self.offset)
        lines = (self.pending + self.file.read()).split(b"\n")
        self.pending = lines.pop()
        self.offset = self.file.tell()
        return lines


def identity_running(prefix: pathlib.Path) -> bool:
    """Check Linux liveness only for Identity processes in this Wine prefix."""
    for process in pathlib.Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            arguments = (process / "cmdline").read_bytes().split(b"\0")
            environment = (process / "environ").read_bytes().split(b"\0")
            if (
                any(
                    argument.lower().endswith(b"adskidentitymanager.exe")
                    for argument in arguments[:2]
                )
                and ("WINEPREFIX=" + str(prefix)).encode() in environment
            ):
                return True
        except OSError:
            continue
    return False


def observe(
    path: pathlib.Path,
    line: bytes,
    fresh: bool,
    states: dict[tuple[pathlib.Path, bytes], Lifecycle],
    prefix: pathlib.Path,
) -> bool:
    """Advance per-log PID lifecycle state; accept only a fresh startup signal."""
    match = re.search(
        rb"\[AdskIdentityManager:(\d+),\s*\d+\] \[AdskIdentityManager INFO\] (.*)", line
    )
    if not match:
        return False
    pid, message = match.groups()
    key = (path, pid)
    if message.startswith(b"Starting Autodesk IDSDK Server process"):
        states[key] = Lifecycle.STARTING
    elif message.startswith(b"SSO Server is ready"):
        states[key] = Lifecycle.READY
        return fresh
    elif b"Quitting" in message or b"App state set to Quit" in message:
        states[key] = Lifecycle.STOPPED

    # A fresh duplicate report may reuse an incumbent only if its last-known
    # state is ready and an Identity process in this prefix is still alive.
    duplicate = re.search(
        rb"Quitting since another instance \(pid (\d+)\) is already running", message
    )
    return bool(
        fresh
        and duplicate
        and states.get((path, duplicate[1])) == Lifecycle.READY
        and identity_running(prefix)
    )


def start_identity_manager(
    prefix: pathlib.Path,
    wine: str,
    identity: pathlib.Path,
    launch_log: pathlib.Path,
    desktop: str,
    timeout: float = 30,
    poll_interval: float = 0.1,
) -> None:
    users = prefix / "drive_c/users"
    paths: list[pathlib.Path] = [
        user / "AppData/Local/Autodesk/Identity Services/Log/IdServices.log"
        for user in users.iterdir()
        if user.is_dir()
    ]
    readers: dict[pathlib.Path, LogReader] = {}
    states: dict[tuple[pathlib.Path, bytes], Lifecycle] = {}

    try:
        # Historical readiness is not a new startup signal. Retain lifecycle
        # state so a fresh duplicate report can reuse a live, ready SSO.
        for path in paths:
            try:
                reader = LogReader(path)
            except FileNotFoundError:
                continue
            readers[path] = reader
            for line in reader.file:
                observe(path, line, False, states, prefix)
            reader.offset = reader.file.tell()

        # Resolution remains shell-owned. Launch the given executable without
        # changing account state or terminating any existing Wine processes.
        arguments: list[str] = [wine, str(identity)]
        if desktop:
            windows_identity = "C:\\" + str(
                identity.relative_to(prefix / "drive_c")
            ).replace("/", "\\")
            arguments = [
                wine,
                "explorer",
                "/desktop=Fusion360," + desktop,
                windows_identity,
            ]
        with launch_log.open("ab") as output:
            process: subprocess.Popen[bytes] = subprocess.Popen(
                arguments,
                cwd=identity.parent,
                stdout=output,
                stderr=output,
                close_fds=True,
            )

        # Only complete fresh lines count, even if a log appears late.
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for path in paths:
                # Drain the old descriptor before following a rotated log;
                # its last appended readiness line must not be lost.
                if path in readers and any(
                    observe(path, line, True, states, prefix)
                    for line in readers[path].read_lines()
                ):
                    print(READY_MESSAGE)
                    return
                try:
                    stat = path.stat()
                except FileNotFoundError:
                    continue
                if path not in readers:
                    readers[path] = LogReader(path)
                else:
                    old = os.fstat(readers[path].file.fileno())
                    if (old.st_dev, old.st_ino) != (stat.st_dev, stat.st_ino):
                        readers[path].file.close()
                        readers[path] = LogReader(path)
            if process.poll() not in (None, 0):
                raise SystemExit(FAILED_MESSAGE)
            time.sleep(poll_interval)
        raise SystemExit(TIMEOUT_MESSAGE)
    finally:
        for reader in readers.values():
            reader.file.close()


def main() -> None:
    prefix, wine, identity, launch_log, desktop = sys.argv[1:]
    start_identity_manager(
        pathlib.Path(prefix),
        wine,
        pathlib.Path(identity),
        pathlib.Path(launch_log),
        desktop,
    )


if __name__ == "__main__":
    main()
