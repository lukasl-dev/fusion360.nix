"""Exercise Identity readiness using temporary logs and stub processes, not Wine."""

import contextlib
import io
import pathlib
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable, Iterable, Iterator
from functools import partial
from typing import BinaryIO
from unittest import mock

import identity


def marker(message: str = "SSO Server is ready", pid: int = 123) -> bytes:
    return f"[AdskIdentityManager:{pid}, 456] [AdskIdentityManager INFO] {message}\n".encode()


READY = marker()
DUPLICATE = marker("Quitting since another instance (pid 123) is already running", 789)
TIMEOUT = "Identity Manager did not report readiness within 30 seconds. Close Fusion and try 'fusion360 stop'; no account data was reset or processes killed."
FAILED = "Identity Manager failed to start. Inspect the launch log; no processes were killed."


class StubProcess:
    """Advance synthetic log writes between polls, without running Wine."""

    def __init__(
        self, actions: Iterable[Callable[[], None]] = (), returncode: int | None = 0
    ) -> None:
        self.actions: Iterator[Callable[[], None]] = iter(actions)
        self.returncode = returncode

    def poll(self) -> int | None:
        action = next(self.actions, None)
        if action is not None:
            action()
        return self.returncode


class StubLauncher:
    """Capture launch options through a typed substitute for Popen."""

    def __init__(
        self, process: StubProcess, on_launch: Callable[[BinaryIO], None] | None = None
    ) -> None:
        self.process = process
        self.on_launch = on_launch
        self.arguments: list[str] = []
        self.cwd: pathlib.Path | None = None
        self.stdout: BinaryIO | None = None
        self.stderr: BinaryIO | None = None
        self.close_fds = False
        self.calls = 0

    def __call__(
        self,
        arguments: list[str],
        *,
        cwd: pathlib.Path,
        stdout: BinaryIO,
        stderr: BinaryIO,
        close_fds: bool,
    ) -> StubProcess:
        self.calls += 1
        self.arguments = arguments
        self.cwd = cwd
        self.stdout = stdout
        self.stderr = stderr
        self.close_fds = close_fds
        if self.on_launch is not None:
            self.on_launch(stdout)
        return self.process


class IdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.prefix = pathlib.Path(directory.name)
        self.log = (
            self.prefix
            / "drive_c/users/test/AppData/Local/Autodesk/Identity Services/Log/IdServices.log"
        )
        self.log.parent.mkdir(parents=True)
        self.executable = (
            self.prefix
            / "drive_c/Program Files/Autodesk/Autodesk Identity Manager/AdskIdentityManager.exe"
        )
        self.executable.parent.mkdir(parents=True)
        self.executable.write_text(
            "Synthetic Identity fixture; startup uses a stub process.\n"
        )
        self.launch_log = self.prefix / "launch.log"

    def append(self, data: bytes = READY) -> None:
        with self.log.open("ab") as output:
            output.write(data)

    def run_startup(
        self,
        actions: Iterable[Callable[[], None]] = (),
        returncode: int | None = 0,
        desktop: str = "",
        expected_error: str | None = None,
    ) -> list[str]:
        output = io.StringIO()
        launcher = StubLauncher(StubProcess(actions, returncode))
        with (
            mock.patch.object(subprocess, "Popen", side_effect=launcher),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(output),
        ):
            if expected_error is None:
                identity.start_identity_manager(
                    self.prefix,
                    "fake-wine",
                    self.executable,
                    self.launch_log,
                    desktop,
                    timeout=1.5,
                    poll_interval=0.005,
                )
            else:
                with self.assertRaises(SystemExit) as error:
                    identity.start_identity_manager(
                        self.prefix,
                        "fake-wine",
                        self.executable,
                        self.launch_log,
                        desktop,
                        timeout=0.15,
                        poll_interval=0.005,
                    )
                self.assertEqual(error.exception.code, expected_error)
        self.assertEqual(
            output.getvalue(),
            "Identity Manager is ready.\n" if expected_error is None else "",
        )
        self.assertNotIn(READY.decode(), output.getvalue())
        self.assertEqual(launcher.calls, 1)
        self.assertEqual(launcher.cwd, self.executable.parent)
        self.assertTrue(launcher.close_fds)
        self.assertIs(launcher.stdout, launcher.stderr)
        assert launcher.stdout is not None
        self.assertTrue(launcher.stdout.closed)
        return launcher.arguments

    def test_append_create_empty_delayed_split_rotate_truncate(self) -> None:
        for behavior in (
            "append",
            "create",
            "empty",
            "delayed",
            "split",
            "rotate",
            "truncate",
        ):
            with self.subTest(behavior=behavior):
                self.log.unlink(missing_ok=True)
                if behavior not in ("create", "delayed"):
                    self.log.write_bytes(
                        b"" if behavior == "empty" else b"old log data\n" * 20
                    )

                def write_ready(behavior: str = behavior) -> None:
                    if behavior == "rotate":
                        self.log.rename(self.log.with_suffix(".old"))
                    if behavior in ("create", "delayed", "rotate", "truncate"):
                        self.log.write_bytes(b"")
                    self.append()

                actions: list[Callable[[], None]]
                if behavior == "split":
                    actions = [
                        lambda: self.append(READY[:35]),
                        lambda: None,
                        lambda: self.append(READY[35:]),
                    ]
                elif behavior == "delayed":
                    actions = [lambda: None, lambda: None, write_ready]
                else:
                    actions = [write_ready]
                self.run_startup(actions)

    def test_historical_ready_alone_times_out_even_after_clean_exit(self) -> None:
        self.log.write_bytes(READY)
        self.run_startup(expected_error=TIMEOUT)

    def test_missing_empty_and_incomplete_logs_time_out(self) -> None:
        for data in (None, b"", READY.rstrip(b"\n")):
            with self.subTest(data=data):
                self.log.unlink(missing_ok=True)
                if data is not None:
                    self.log.write_bytes(data)
                self.run_startup(expected_error=TIMEOUT)

    def test_failed_process(self) -> None:
        self.run_startup(returncode=7, expected_error=FAILED)

    def test_ready_is_checked_before_process_failure(self) -> None:
        self.log.write_bytes(b"")

        def launch(output: BinaryIO) -> None:
            self.append()

        launcher = StubLauncher(StubProcess(returncode=7), launch)
        with (
            mock.patch.object(subprocess, "Popen", side_effect=launcher),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            identity.start_identity_manager(
                self.prefix,
                "fake-wine",
                self.executable,
                self.launch_log,
                "",
                timeout=1.5,
            )
        self.assertEqual(output.getvalue(), "Identity Manager is ready.\n")

    def test_snapshot_precedes_spawn_and_launch_log_is_append_only(self) -> None:
        self.log.write_bytes(READY)
        account = self.prefix / "user.reg"
        account.write_bytes(b"untouched account state")
        self.launch_log.write_bytes(b"existing launch output\n")

        def launch(output: BinaryIO) -> None:
            observed.assert_called_once_with(
                self.log, READY, False, mock.ANY, self.prefix
            )
            # Preserve the offset-based behavior: a same-size rewrite is not
            # detected as truncation and does not become fresh readiness.
            self.log.write_bytes(READY)
            output.write(b"synthetic launch output\n")

        launcher = StubLauncher(StubProcess(), launch)
        with (
            mock.patch.object(identity, "observe", wraps=identity.observe) as observed,
            mock.patch.object(subprocess, "Popen", side_effect=launcher),
            self.assertRaises(SystemExit) as error,
        ):
            identity.start_identity_manager(
                self.prefix,
                "fake-wine",
                self.executable,
                self.launch_log,
                "",
                timeout=0.03,
                poll_interval=0.005,
            )
        self.assertEqual(error.exception.code, TIMEOUT)
        self.assertEqual(account.read_bytes(), b"untouched account state")
        self.assertEqual(
            self.launch_log.read_bytes(),
            b"existing launch output\nsynthetic launch output\n",
        )

    def test_wine_and_virtual_desktop_arguments(self) -> None:
        for desktop in ("", "1920x1080"):
            self.log.write_bytes(b"")
            arguments = self.run_startup([self.append], desktop=desktop)
            windows_path = "C:\\Program Files\\Autodesk\\Autodesk Identity Manager\\AdskIdentityManager.exe"
            expected = (
                ["fake-wine", "explorer", "/desktop=Fusion360,1920x1080", windows_path]
                if desktop
                else ["fake-wine", str(self.executable)]
            )
            self.assertEqual(arguments, expected)

    def test_duplicate_lifecycle_semantics(self) -> None:
        cases = (
            ("ready", READY, True, DUPLICATE, None),
            (
                "restarted ready",
                READY + marker("Starting Autodesk IDSDK Server process") + READY,
                True,
                DUPLICATE,
                None,
            ),
            ("dead", READY, False, DUPLICATE, TIMEOUT),
            (
                "starting",
                READY + marker("Starting Autodesk IDSDK Server process"),
                True,
                DUPLICATE,
                TIMEOUT,
            ),
            (
                "stopped",
                READY + marker("App state set to Quitting"),
                True,
                DUPLICATE,
                TIMEOUT,
            ),
            ("quitting", READY + marker("Quitting now"), True, DUPLICATE, TIMEOUT),
            ("unknown", b"", True, DUPLICATE, TIMEOUT),
            ("historical duplicate", READY + DUPLICATE, True, b"", TIMEOUT),
        )
        for name, history, alive, fresh, error in cases:
            with self.subTest(name=name):
                self.log.write_bytes(history)
                with mock.patch.object(
                    identity, "identity_running", return_value=alive
                ) as running:
                    self.run_startup(
                        [partial(self.append, fresh)], expected_error=error
                    )
                if name in ("ready", "restarted ready", "dead"):
                    running.assert_called_with(self.prefix)
                else:
                    running.assert_not_called()

    def test_ready_state_is_scoped_to_log_path_and_pid(self) -> None:
        states: dict[tuple[pathlib.Path, bytes], identity.Lifecycle] = {}
        self.assertFalse(identity.observe(self.log, READY, False, states, self.prefix))
        with mock.patch.object(
            identity, "identity_running", return_value=True
        ) as running:
            other = self.log.with_name("other.log")
            self.assertFalse(
                identity.observe(other, DUPLICATE, True, states, self.prefix)
            )
            self.assertFalse(
                identity.observe(
                    self.log,
                    marker(
                        "Quitting since another instance (pid 124) is already running",
                        789,
                    ),
                    True,
                    states,
                    self.prefix,
                )
            )
            self.assertTrue(
                identity.observe(self.log, DUPLICATE, True, states, self.prefix)
            )
            self.assertFalse(
                identity.observe(self.log, DUPLICATE, False, states, self.prefix)
            )
        running.assert_called_once_with(self.prefix)

    def test_multiple_existing_users_and_non_directory_entries(self) -> None:
        users = self.prefix / "drive_c/users"
        (users / "not-a-user").write_bytes(b"")
        other = (
            users / "other/AppData/Local/Autodesk/Identity Services/Log/IdServices.log"
        )
        other.parent.mkdir(parents=True)

        def write_other() -> None:
            other.write_bytes(READY)

        self.run_startup([write_other])

    def test_main_converts_only_path_arguments(self) -> None:
        arguments = [
            str(self.prefix),
            "fake-wine",
            str(self.executable),
            str(self.launch_log),
            "",
        ]
        with (
            mock.patch.object(sys, "argv", ["identity.py"] + arguments),
            mock.patch.object(identity, "start_identity_manager") as start,
        ):
            identity.main()
        start.assert_called_once_with(
            self.prefix, "fake-wine", self.executable, self.launch_log, ""
        )

    def test_rotation_drains_readiness_from_old_descriptor_before_replacement(
        self,
    ) -> None:
        self.log.write_bytes(b"")

        def rotate() -> None:
            old = self.log.with_suffix(".old")
            self.log.rename(old)
            with old.open("ab") as output:
                output.write(READY)
            self.log.write_bytes(b"replacement without readiness\n")

        self.run_startup([rotate])

    def test_log_opened_after_spawn_is_not_read_before_failure_poll(self) -> None:
        # A missing log is opened this iteration but read only on the next;
        # preserve failure ordering even if the new file already holds READY.
        def launch(output: BinaryIO) -> None:
            self.log.write_bytes(READY)

        launcher = StubLauncher(StubProcess(returncode=7), launch)
        with (
            mock.patch.object(subprocess, "Popen", side_effect=launcher),
            self.assertRaises(SystemExit) as error,
        ):
            identity.start_identity_manager(
                self.prefix,
                "fake-wine",
                self.executable,
                self.launch_log,
                "",
                timeout=1.5,
            )
        self.assertEqual(error.exception.code, FAILED)


class ReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = pathlib.Path(directory.name) / "IdServices.log"
        self.path.write_bytes(b"")
        self.reader = identity.LogReader(self.path)
        self.addCleanup(self.reader.file.close)

    def test_split_line_is_pending_until_newline(self) -> None:
        self.path.write_bytes(READY[:35])
        self.assertEqual(self.reader.read_lines(), [])
        self.assertEqual(self.reader.pending, READY[:35])
        with self.path.open("ab") as output:
            output.write(READY[35:] + b"next line\npartial")
        self.assertEqual(self.reader.read_lines(), [READY.rstrip(b"\n"), b"next line"])
        self.assertEqual(self.reader.pending, b"partial")

    def test_truncation_discards_old_pending_line(self) -> None:
        self.path.write_bytes(b"old data\n" * 30 + b"partial")
        self.reader.read_lines()
        self.path.write_bytes(READY)
        self.assertEqual(self.reader.read_lines(), [READY.rstrip(b"\n")])
        self.assertEqual(self.reader.pending, b"")

    def test_rotation_drains_old_descriptor_even_when_path_disappears(self) -> None:
        rotated = self.path.with_suffix(".old")
        self.path.rename(rotated)
        with rotated.open("ab") as output:
            output.write(READY)
        self.assertEqual(self.reader.read_lines(), [READY.rstrip(b"\n")])


class ProcessLivenessTests(unittest.TestCase):
    def test_first_two_arguments_case_and_exact_prefix_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            process = root / "123"
            process.mkdir()
            non_process = root / "self"
            non_process.mkdir()
            inaccessible = root / "456"
            inaccessible.mkdir()
            prefix = root / "prefix with spaces"
            cases = (
                ([b"AdskIdentityManager.EXE"], str(prefix), True),
                (
                    [b"wine", b"C:\\Program Files\\ADSKIDENTITYMANAGER.EXE"],
                    str(prefix),
                    True,
                ),
                (
                    [b"wine", b"explorer", b"AdskIdentityManager.exe"],
                    str(prefix),
                    False,
                ),
                ([b"AdskIdentityManager.exe.bak"], str(prefix), False),
                ([b"AdskIdentityManager.exe"], str(prefix) + "/other", False),
                ([b"AdskIdentityManager.exe"], str(prefix) + "/", False),
            )
            for arguments, environment_prefix, expected in cases:
                with self.subTest(
                    arguments=arguments, environment_prefix=environment_prefix
                ):
                    (process / "cmdline").write_bytes(b"\0".join(arguments) + b"\0")
                    (process / "environ").write_bytes(
                        b"OTHER=value\0WINEPREFIX="
                        + environment_prefix.encode()
                        + b"\0"
                    )
                    with mock.patch.object(
                        pathlib.Path,
                        "iterdir",
                        autospec=True,
                        return_value=iter([non_process, inaccessible, process]),
                    ) as entries:
                        self.assertEqual(identity.identity_running(prefix), expected)
                    entries.assert_called_once_with(pathlib.Path("/proc"))
            # A missing environment cannot prove prefix-scoped liveness.
            (process / "environ").unlink()
            with mock.patch.object(
                pathlib.Path, "iterdir", return_value=iter([process])
            ):
                self.assertFalse(identity.identity_running(prefix))


if __name__ == "__main__":
    unittest.main()
