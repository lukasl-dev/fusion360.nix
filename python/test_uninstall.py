"""Uninstall safety regressions using temporary state and no live Wine processes."""

import contextlib
import fcntl
import io
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest import mock

import desktop
import uninstall

LAUNCHER = Path("/nix/store/" + "a" * 32 + "-fusion360")
OLD_LAUNCHER = Path("/nix/store/" + "b" * 32 + "-fusion360")
ENV = "/nix/store/" + "c" * 32 + "-coreutils/bin/env"
WINESERVER = "fixture-wineserver"
MIME = (
    "# Keep this comment and the unrelated sections verbatim.\n"
    "[Default Applications]\n"
    "x-scheme-handler/adskidmgr=fusion360-login.desktop;other-login.desktop;\n"
    "text/plain=editor.desktop;\n"
    "\n"
    "[Added Associations]\n"
    "x-scheme-handler/adskidmgr=other-login.desktop;fusion360-login.desktop;\n"
    "[Removed Associations]\n"
    "x-scheme-handler/adskidmgr=fusion360-login.desktop;\n"
    "[Unrelated Section]\n"
    "x-scheme-handler/adskidmgr=fusion360-login.desktop;\n"
    "# Final comment\n"
)
CLEAN_MIME = (
    "# Keep this comment and the unrelated sections verbatim.\n"
    "[Default Applications]\n"
    "x-scheme-handler/adskidmgr=other-login.desktop;\n"
    "text/plain=editor.desktop;\n"
    "\n"
    "[Added Associations]\n"
    "x-scheme-handler/adskidmgr=other-login.desktop;\n"
    "[Removed Associations]\n"
    "[Unrelated Section]\n"
    "x-scheme-handler/adskidmgr=fusion360-login.desktop;\n"
    "# Final comment\n"
)


class StubCommands:
    """Only the expected Wine wait and desktop refresh may be requested."""

    def __init__(self, applications: Path, prefix: Path) -> None:
        self.applications = applications
        self.prefix = prefix
        self.calls: list[list[str]] = []
        self.wait_error: subprocess.SubprocessError | OSError | None = None
        self.on_wait: Callable[[], None] | None = None
        self.refresh_returncode = 0

    def __call__(
        self,
        arguments: list[str],
        *,
        check: bool,
        capture_output: bool = False,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
        stdout: int | None = None,
        stderr: int | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(arguments.copy())
        if arguments == [WINESERVER, "-w"]:
            assert check and not capture_output
            assert timeout == 5
            assert stdout == stderr == subprocess.DEVNULL
            assert env == os.environ | {"WINEPREFIX": str(self.prefix)}
            if self.on_wait is not None:
                self.on_wait()
            if self.wait_error is not None:
                raise self.wait_error
            return subprocess.CompletedProcess(arguments, 0)
        if arguments == ["update-desktop-database", str(self.applications)]:
            assert not check and capture_output
            assert env is None and timeout is None
            assert stdout is None and stderr is None
            return subprocess.CompletedProcess(arguments, self.refresh_returncode)
        raise AssertionError(f"Unexpected external command: {arguments}")


class UninstallTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.data = self.root / "data"
        self.cache = self.root / "cache"
        self.state = self.root / "state"
        self.applications = self.root / "xdg-data/applications"
        self.config = self.root / "config"
        self.desktop_root = self.root / "xdg-state/fusion360/desktop-root"
        self.paths = (
            self.data,
            self.cache,
            self.state,
            self.applications,
            self.config,
            self.desktop_root,
        )
        for path in (
            self.data,
            self.cache,
            self.state,
            self.applications,
            self.config,
            self.desktop_root.parent,
        ):
            path.mkdir(parents=True)
        self.prefix = self.data / "prefix"
        self.prefix.mkdir()
        (self.prefix / "system.reg").write_text("synthetic registry\n")
        (self.prefix / "local-document.txt").write_text("valuable local document\n")
        self.lock = self.data / "prefix.lock"
        self.lock.touch()
        for name in ("setup", "backups"):
            child = self.data / name
            child.mkdir()
            (child / "prefix").write_text("synthetic setup marker\n")
        (self.data / "graphics").write_text("opengl\n")
        (self.data / "installation.json").write_text(
            '{"installer_sha256": "' + "d" * 64 + '"}\n'
        )
        (self.cache / "winetricks").mkdir()
        (self.cache / "winetricks/cached-installer").write_text("cached bytes")
        for name in (
            "Fusion Admin Install.exe",
            "Fusion Admin Install.exe.part",
            "MicrosoftEdgeWebView2RuntimeInstallerX64.exe",
            "MicrosoftEdgeWebView2RuntimeInstallerX64.exe.part",
        ):
            (self.cache / name).write_text("synthetic download")
        for action in ("run", "install", "update"):
            (self.state / f"{action}-20261002T123456-42.log").write_text("old log\n")
        self.run_entry = self.applications / "fusion360.desktop"
        self.login_entry = self.applications / "fusion360-login.desktop"
        self.run_entry.write_text(self.entry("run %F"))
        self.login_entry.write_text(self.entry("login %u"))
        self.unrelated_entry = self.applications / "unrelated.desktop"
        self.unrelated_entry.write_text("[Desktop Entry]\nExec=unrelated\n")
        self.desktop_root.symlink_to(LAUNCHER)
        self.mime_paths = (
            self.config / "mimeapps.list",
            self.config / "gnome-mimeapps.list",
            self.applications / "mimeapps.list",
            self.applications / "kde-mimeapps.list",
        )
        for path in self.mime_paths:
            path.write_text(MIME)
        self.commands = StubCommands(self.applications, self.prefix)
        commands_patch = mock.patch.object(subprocess, "run", side_effect=self.commands)
        commands_patch.start()
        self.addCleanup(commands_patch.stop)
        environment = mock.patch.dict(
            os.environ,
            {"HOME": str(self.root), "XDG_CACHE_HOME": str(self.root / "xdg-cache")},
        )
        environment.start()
        self.addCleanup(environment.stop)
        output = contextlib.redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)

    def entry(self, action: str, launcher: Path = LAUNCHER) -> str:
        assignments = desktop.environment_arguments(
            str(self.data), str(self.cache), str(self.state)
        )
        return (
            "[Desktop Entry]\nType=Application\nName=Fusion\n"
            f"Exec={ENV} {assignments} {launcher}/bin/fusion360 {action}\n"
        )

    def snapshot(
        self, root: Path | None = None
    ) -> dict[Path, tuple[str, bytes, int, int]]:
        """Include contents, modes and inodes, without following symlinks."""
        directory = self.root if root is None else root
        result: dict[Path, tuple[str, bytes, int, int]] = {}
        for path in [directory, *directory.rglob("*")]:
            info = path.lstat()
            mode = stat.S_IMODE(info.st_mode)
            if path.is_symlink():
                result[path] = (
                    "symlink",
                    os.fsencode(path.readlink()),
                    mode,
                    info.st_ino,
                )
            elif path.is_file():
                result[path] = ("file", path.read_bytes(), mode, info.st_ino)
            else:
                result[path] = ("directory", b"", mode, info.st_ino)
        return result

    def test_default_only_removes_owned_desktop_integration(self) -> None:
        state_before = [
            self.snapshot(path) for path in (self.data, self.cache, self.state)
        ]
        unrelated = self.unrelated_entry.read_bytes()
        uninstall.uninstall(*self.paths, WINESERVER)
        self.assertFalse(self.run_entry.exists())
        self.assertFalse(self.login_entry.exists())
        self.assertFalse(self.desktop_root.is_symlink())
        self.assertEqual(self.unrelated_entry.read_bytes(), unrelated)
        for path, before in zip(
            (self.data, self.cache, self.state), state_before, strict=True
        ):
            self.assertEqual(self.snapshot(path), before)
        for path in self.mime_paths:
            self.assertEqual(path.read_text(), CLEAN_MIME)
        self.assertEqual(
            self.commands.calls, [["update-desktop-database", str(self.applications)]]
        )

    def test_only_matching_gc_root_target_is_removed(self) -> None:
        self.desktop_root.unlink()
        self.desktop_root.symlink_to(OLD_LAUNCHER)
        uninstall.remove_desktop_integration(*self.paths)
        self.assertEqual(self.desktop_root.readlink(), OLD_LAUNCHER)

    def test_gc_root_matches_either_recognized_old_or_current_launcher(self) -> None:
        self.login_entry.write_text(self.entry("login %u", OLD_LAUNCHER))
        self.desktop_root.unlink()
        self.desktop_root.symlink_to(OLD_LAUNCHER)
        uninstall.remove_desktop_integration(*self.paths)
        self.assertFalse(self.desktop_root.is_symlink())
        self.assertFalse(self.login_entry.exists())

    def test_regular_gc_root_is_not_unlinked(self) -> None:
        self.desktop_root.unlink()
        self.desktop_root.write_text("not a GC root")
        uninstall.remove_desktop_integration(*self.paths)
        self.assertEqual(self.desktop_root.read_text(), "not a GC root")

    def test_declarative_desktop_entries_and_mime_are_untouched(self) -> None:
        for index, path in enumerate(
            (self.run_entry, self.login_entry, *self.mime_paths)
        ):
            target = self.root / f"declarative-{index}"
            target.write_bytes(path.read_bytes())
            path.unlink()
            path.symlink_to(target)
        before = self.snapshot()
        uninstall.uninstall(*self.paths, WINESERVER)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.commands.calls, [])

    def test_read_only_desktop_entries_are_untouched(self) -> None:
        self.run_entry.chmod(0o444)
        self.login_entry.chmod(0o444)
        before = self.snapshot()
        uninstall.uninstall(*self.paths, WINESERVER)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.commands.calls, [])

    def test_symlink_and_read_only_mime_survive_owned_login_removal(self) -> None:
        declarative = self.root / "declarative-mime"
        declarative.write_text(MIME)
        symlink = self.mime_paths[0]
        symlink.unlink()
        symlink.symlink_to(declarative)
        read_only = self.mime_paths[1]
        read_only.chmod(0o444)
        uninstall.uninstall(*self.paths, WINESERVER)
        self.assertTrue(symlink.is_symlink())
        self.assertEqual(declarative.read_text(), MIME)
        self.assertEqual(read_only.read_text(), MIME)
        for path in self.mime_paths[2:]:
            self.assertEqual(path.read_text(), CLEAN_MIME)

    def test_mime_requires_removal_of_owned_login_entry(self) -> None:
        self.login_entry.write_text("[Desktop Entry]\nExec=custom-login %u\n")
        uninstall.uninstall(*self.paths, WINESERVER)
        self.assertFalse(self.run_entry.exists())
        self.assertTrue(self.login_entry.exists())
        for path in self.mime_paths:
            self.assertEqual(path.read_text(), MIME)

    def test_custom_registration_requires_all_exact_assignments_and_store_shape(
        self,
    ) -> None:
        original_run = self.entry("run %F")
        original_login = self.entry("login %u")
        changes = (
            (str(self.data), str(self.data / "other")),
            (str(self.cache), str(self.cache / "other")),
            (str(self.state), str(self.state / "other")),
            ("FUSION360_STATE_HOME", "OTHER_STATE_HOME"),
            (str(LAUNCHER), "/opt/fusion360"),
            (str(LAUNCHER), "/nix/store/" + "a" * 31 + "-fusion360"),
            (str(LAUNCHER), str(LAUNCHER) + "-custom"),
            ("/bin/fusion360", "/bin/another-launcher"),
            ("Exec=", "Exec=extra-command "),
        )
        for old, new in changes:
            with self.subTest(replacement=new):
                self.run_entry.write_text(original_run.replace(old, new))
                self.login_entry.write_text(original_login.replace(old, new))
                before = self.snapshot()
                uninstall.uninstall(*self.paths, WINESERVER)
                self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.commands.calls, [])

    def test_wrong_actions_and_malformed_entries_are_not_owned(self) -> None:
        for contents in (
            "not an INI document",
            "[Desktop Entry]\nName=Missing Exec\n",
            self.entry("run %u"),
            self.entry("run %F --extra"),
        ):
            with self.subTest(contents=contents):
                self.run_entry.write_text(contents)
                self.login_entry.write_text(contents)
                before = self.snapshot()
                uninstall.remove_desktop_integration(*self.paths)
                self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.commands.calls, [])

    def test_mime_rewrite_preserves_mode_comments_and_missing_final_newline(
        self,
    ) -> None:
        path = self.config / "mimeapps.list"
        path.chmod(0o640)
        path.write_text(
            "# comment\n[Default Applications]\n"
            " x-scheme-handler/adskidmgr = other.desktop; fusion360-login.desktop;"
        )
        uninstall.remove_mime_association(self.config, self.applications)
        self.assertEqual(
            path.read_text(),
            "# comment\n[Default Applications]\n"
            " x-scheme-handler/adskidmgr =other.desktop;",
        )
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
        self.assertEqual(
            set(self.config.iterdir()), {self.mime_paths[0], self.mime_paths[1]}
        )

    def test_purge_targets_keep_data_directory_and_lock(self) -> None:
        before = self.snapshot()
        targets = uninstall.purge_targets(*self.paths)
        self.assertEqual(
            set(targets),
            {
                self.prefix,
                self.data / "setup",
                self.data / "graphics",
                self.data / "backups",
                self.data / "installation.json",
                self.cache,
                self.state,
            },
        )
        self.assertNotIn(self.data, targets)
        self.assertNotIn(self.lock, targets)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.commands.calls, [])

    def test_purge_waits_with_exclusive_lock_before_mutating_and_preserves_inode(
        self,
    ) -> None:
        before = self.snapshot()
        lock_inode = self.lock.stat().st_ino
        data_inode = self.data.stat().st_ino

        def check_wait() -> None:
            self.assertEqual(self.snapshot(), before)
            with self.lock.open("a") as contender:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        self.commands.on_wait = check_wait
        uninstall.uninstall(*self.paths, WINESERVER, purge=True, confirmed=True)
        self.assertEqual(set(self.data.iterdir()), {self.lock})
        self.assertEqual(self.lock.stat().st_ino, lock_inode)
        self.assertEqual(self.data.stat().st_ino, data_inode)
        self.assertFalse(self.cache.exists())
        self.assertFalse(self.state.exists())
        self.assertFalse(self.run_entry.exists())
        self.assertFalse(self.login_entry.exists())
        self.assertFalse(self.desktop_root.is_symlink())
        self.assertTrue(self.unrelated_entry.exists())
        self.assertEqual(
            self.commands.calls,
            [[WINESERVER, "-w"], ["update-desktop-database", str(self.applications)]],
        )
        with self.lock.open("a") as contender:
            fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(contender.fileno(), fcntl.LOCK_UN)

    def test_held_prefix_lock_refuses_purge_without_mutation(self) -> None:
        before = self.snapshot()
        with self.lock.open("a") as holder:
            fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(SystemExit, "running|installation"):
                uninstall.uninstall(*self.paths, WINESERVER, purge=True, confirmed=True)
            fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.commands.calls, [])

    def test_wineserver_timeout_failure_or_missing_command_refuses_before_mutation(
        self,
    ) -> None:
        failures = (
            subprocess.TimeoutExpired([WINESERVER, "-w"], 5),
            subprocess.CalledProcessError(1, [WINESERVER, "-w"]),
            FileNotFoundError("missing fixture wineserver"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                self.commands.wait_error = failure
                self.commands.calls.clear()
                before = self.snapshot()
                with self.assertRaisesRegex(
                    SystemExit, "Wine is still running|checked"
                ):
                    uninstall.uninstall(
                        *self.paths, WINESERVER, purge=True, confirmed=True
                    )
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.commands.calls, [[WINESERVER, "-w"]])
                with self.lock.open("a") as contender:
                    fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(contender.fileno(), fcntl.LOCK_UN)

    def test_protected_parent_home_and_xdg_directories_are_rejected(self) -> None:
        for broad in (
            self.root,
            self.root.parent,
            Path("/"),
            Path("/nix/store"),
            Path("/usr"),
            Path("/etc"),
            Path("/var"),
            Path("/run"),
            Path("/proc"),
            Path("/sys"),
            Path("/dev"),
            Path("/nix/store") / ("e" * 32 + "-unrelated"),
            self.config,
            self.applications,
            self.applications.parent,
            self.desktop_root.parent.parent,
            self.root / "xdg-cache",
        ):
            with self.subTest(directory=broad):
                before = self.snapshot()
                with self.assertRaisesRegex(SystemExit, "broad or protected"):
                    uninstall.uninstall(
                        broad, *self.paths[1:], WINESERVER, purge=True, confirmed=True
                    )
                self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.commands.calls, [])

    def test_symlinked_or_relative_state_roots_are_rejected(self) -> None:
        link = self.root / "linked-data"
        link.symlink_to(self.data, target_is_directory=True)
        for path in (link, Path("relative-data")):
            before = self.snapshot()
            with self.assertRaisesRegex(SystemExit, "relative or symlinked"):
                uninstall.uninstall(
                    path, *self.paths[1:], WINESERVER, purge=True, confirmed=True
                )
            self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.commands.calls, [])

    def test_overlapping_state_roots_are_rejected(self) -> None:
        for cache in (self.data, self.data / "nested-cache"):
            with self.subTest(cache=cache):
                before = self.snapshot()
                with self.assertRaisesRegex(SystemExit, "overlapping"):
                    uninstall.uninstall(
                        self.data,
                        cache,
                        *self.paths[2:],
                        WINESERVER,
                        purge=True,
                        confirmed=True,
                    )
                self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.commands.calls, [])

    def test_unrelated_children_in_each_state_root_are_rejected(self) -> None:
        for directory in (self.data, self.cache, self.state):
            with self.subTest(directory=directory):
                unrelated = directory / "unrelated.txt"
                unrelated.write_text("must not delete")
                before = self.snapshot()
                with self.assertRaisesRegex(SystemExit, "unrelated files"):
                    uninstall.uninstall(
                        *self.paths, WINESERVER, purge=True, confirmed=True
                    )
                self.assertEqual(self.snapshot(), before)
                unrelated.unlink()
        self.assertEqual(self.commands.calls, [])

    def test_log_named_directory_is_not_treated_as_an_owned_log(self) -> None:
        directory = self.state / "run-20261002T123456-99.log"
        directory.mkdir()
        (directory / "unrelated.txt").write_text("must not delete")
        before = self.snapshot()
        with self.assertRaisesRegex(SystemExit, "unrelated files"):
            uninstall.uninstall(*self.paths, WINESERVER, purge=True, confirmed=True)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.commands.calls, [])

    def test_prefix_without_setup_marker_is_rejected(self) -> None:
        for marker in (self.prefix / "system.reg", self.data / "setup/prefix"):
            with self.subTest(marker=marker):
                contents = marker.read_bytes()
                marker.unlink()
                before = self.snapshot()
                with self.assertRaisesRegex(SystemExit, "setup markers"):
                    uninstall.uninstall(
                        *self.paths, WINESERVER, purge=True, confirmed=True
                    )
                self.assertEqual(self.snapshot(), before)
                marker.write_bytes(contents)
        self.assertEqual(self.commands.calls, [])

    def test_invalid_installation_metadata_is_rejected(self) -> None:
        metadata = self.data / "installation.json"
        for contents in ("not JSON", "[]", "null", '{"unrelated": true}'):
            with self.subTest(contents=contents):
                metadata.write_text(contents)
                before = self.snapshot()
                with self.assertRaisesRegex(SystemExit, "metadata"):
                    uninstall.uninstall(
                        *self.paths, WINESERVER, purge=True, confirmed=True
                    )
                self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.commands.calls, [])

    def test_state_directory_accepts_only_owned_desktop_gc_root(self) -> None:
        root = self.state / "desktop-root"
        paths = (*self.paths[:5], root)
        for target in (OLD_LAUNCHER, self.root / "unrelated-target"):
            with self.subTest(target=target):
                root.symlink_to(target)
                before = self.snapshot()
                with self.assertRaisesRegex(SystemExit, "unrecognized desktop GC root"):
                    uninstall.uninstall(*paths, WINESERVER, purge=True, confirmed=True)
                self.assertEqual(self.snapshot(), before)
                root.unlink()
        root.write_text("unrelated regular file")
        before = self.snapshot()
        with self.assertRaisesRegex(SystemExit, "unrecognized desktop GC root"):
            uninstall.uninstall(*paths, WINESERVER, purge=True, confirmed=True)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.commands.calls, [])
        root.unlink()
        root.symlink_to(LAUNCHER)
        uninstall.uninstall(*paths, WINESERVER, purge=True, confirmed=True)
        self.assertFalse(self.state.exists())
        self.assertEqual(set(self.data.iterdir()), {self.lock})

    def test_declarative_entries_do_not_authorize_state_gc_root_purge(self) -> None:
        root = self.state / "desktop-root"
        root.symlink_to(LAUNCHER)
        for path in (self.run_entry, self.login_entry):
            target = self.root / path.name
            target.write_bytes(path.read_bytes())
            path.unlink()
            path.symlink_to(target)
        before = self.snapshot()
        with self.assertRaisesRegex(SystemExit, "unrecognized desktop GC root"):
            uninstall.uninstall(
                *self.paths[:5], root, WINESERVER, purge=True, confirmed=True
            )
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.commands.calls, [])

    def test_cache_only_purge_creates_data_lock_without_waiting_for_wine(self) -> None:
        data = self.root / "not-yet-installed-data"
        uninstall.uninstall(
            data, *self.paths[1:], WINESERVER, purge=True, confirmed=True
        )
        self.assertEqual(set(data.iterdir()), {data / "prefix.lock"})
        self.assertFalse(self.cache.exists())
        self.assertFalse(self.state.exists())
        # These entries belong to a different data root, so they remain intact.
        self.assertTrue(self.run_entry.exists())
        self.assertTrue(self.login_entry.exists())
        self.assertEqual(self.commands.calls, [])
        with uninstall.stopped_prefix(data, WINESERVER):
            with (data / "prefix.lock").open("a") as contender:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_symlinked_prefix_is_rejected(self) -> None:
        real_prefix = self.root / "external-prefix"
        self.prefix.rename(real_prefix)
        self.prefix.symlink_to(real_prefix, target_is_directory=True)
        before = self.snapshot()
        with self.assertRaisesRegex(SystemExit, "symlinked Wine prefix"):
            uninstall.uninstall(*self.paths, WINESERVER, purge=True, confirmed=True)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.commands.calls, [])

    def test_symlinked_lock_is_rejected(self) -> None:
        external = self.root / "external-lock"
        external.write_text("not ours")
        self.lock.unlink()
        self.lock.symlink_to(external)
        before = self.snapshot()
        with self.assertRaisesRegex(SystemExit, "symlinked prefix lock"):
            uninstall.uninstall(*self.paths, WINESERVER, purge=True, confirmed=True)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.commands.calls, [])

    def test_no_tty_or_cancelled_confirmation_never_mutates(self) -> None:
        for tty, answer, message in (
            (False, "PURGE", "interactive confirmation"),
            (True, "", "cancelled"),
            (True, "purge", "cancelled"),
            (True, "no", "cancelled"),
        ):
            with self.subTest(tty=tty, answer=answer):
                before = self.snapshot()
                with (
                    mock.patch.object(sys.stdin, "isatty", return_value=tty),
                    mock.patch("builtins.input", return_value=answer) as prompt,
                    self.assertRaisesRegex(SystemExit, message),
                ):
                    uninstall.uninstall(*self.paths, WINESERVER, purge=True)
                self.assertEqual(self.snapshot(), before)
                if tty:
                    prompt.assert_called_once_with("Type PURGE to confirm: ")
                else:
                    prompt.assert_not_called()
        self.assertEqual(self.commands.calls, [])

    def test_interactive_exact_confirmation_allows_purge(self) -> None:
        with (
            mock.patch.object(sys.stdin, "isatty", return_value=True),
            mock.patch("builtins.input", return_value="PURGE") as prompt,
        ):
            uninstall.uninstall(*self.paths, WINESERVER, purge=True)
        prompt.assert_called_once_with("Type PURGE to confirm: ")
        self.assertEqual(set(self.data.iterdir()), {self.lock})
        self.assertFalse(self.cache.exists())
        self.assertFalse(self.state.exists())

    def test_confirmation_eof_cancels_without_mutation(self) -> None:
        before = self.snapshot()
        with (
            mock.patch.object(sys.stdin, "isatty", return_value=True),
            mock.patch("builtins.input", side_effect=EOFError),
            self.assertRaisesRegex(SystemExit, "cancelled"),
        ):
            uninstall.uninstall(*self.paths, WINESERVER, purge=True)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.commands.calls, [])

    def test_explicit_confirmation_does_not_prompt(self) -> None:
        with (
            mock.patch.object(sys.stdin, "isatty", return_value=False),
            mock.patch(
                "builtins.input", side_effect=AssertionError("must not prompt")
            ) as prompt,
        ):
            uninstall.uninstall(*self.paths, WINESERVER, purge=True, confirmed=True)
        prompt.assert_not_called()
        self.assertEqual(set(self.data.iterdir()), {self.lock})

    def test_revalidation_after_wait_detects_new_unrelated_child(self) -> None:
        unrelated = self.cache / "created-during-wait"

        def concurrent_change() -> None:
            unrelated.write_text("must preserve")

        self.commands.on_wait = concurrent_change
        entries = (self.run_entry.read_bytes(), self.login_entry.read_bytes())
        with self.assertRaisesRegex(SystemExit, "unrelated files"):
            uninstall.uninstall(*self.paths, WINESERVER, purge=True, confirmed=True)
        self.assertTrue(self.prefix.exists())
        self.assertTrue(unrelated.exists())
        self.assertEqual(
            (self.run_entry.read_bytes(), self.login_entry.read_bytes()), entries
        )
        self.assertEqual(self.desktop_root.readlink(), LAUNCHER)
        for path in self.mime_paths:
            self.assertEqual(path.read_text(), MIME)
        self.assertEqual(self.commands.calls, [[WINESERVER, "-w"]])

    def test_default_and_purge_repeated_invocations_are_idempotent(self) -> None:
        uninstall.uninstall(*self.paths, WINESERVER)
        after_default = self.snapshot()
        self.commands.calls.clear()
        uninstall.uninstall(*self.paths, WINESERVER)
        self.assertEqual(self.snapshot(), after_default)
        self.assertEqual(self.commands.calls, [])
        uninstall.uninstall(*self.paths, WINESERVER, purge=True, confirmed=True)
        after_purge = self.snapshot()
        self.commands.calls.clear()
        uninstall.uninstall(*self.paths, WINESERVER, purge=True, confirmed=True)
        self.assertEqual(self.snapshot(), after_purge)
        self.assertEqual(self.commands.calls, [])

    def test_missing_state_is_safe_for_default_and_purge(self) -> None:
        missing = (
            self.root / "missing-data",
            self.root / "missing-cache",
            self.root / "missing-state",
        )
        paths = (*missing, *self.paths[3:])
        before = self.snapshot()
        uninstall.uninstall(*paths, WINESERVER)
        self.assertEqual(self.snapshot(), before)
        uninstall.uninstall(*paths, WINESERVER, purge=True, confirmed=True)
        missing_lock = missing[0] / "prefix.lock"
        self.assertEqual(set(missing[0].iterdir()), {missing_lock})
        self.assertFalse(missing[1].exists())
        self.assertFalse(missing[2].exists())
        after_purge = self.snapshot()
        uninstall.uninstall(*paths, WINESERVER, purge=True, confirmed=True)
        self.assertEqual(self.snapshot(), after_purge)
        self.assertEqual(self.commands.calls, [])
        with uninstall.stopped_prefix(missing[0], WINESERVER):
            with missing_lock.open("a") as contender:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_desktop_database_failure_does_not_undo_removal(self) -> None:
        self.commands.refresh_returncode = 1
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            uninstall.uninstall(*self.paths, WINESERVER)
        self.assertIn("updating the desktop database failed", errors.getvalue())
        self.assertFalse(self.run_entry.exists())
        self.assertFalse(self.login_entry.exists())
        self.assertTrue(self.prefix.exists())

    def test_missing_desktop_database_command_warns_after_removal(self) -> None:
        with (
            mock.patch.object(subprocess, "run", side_effect=FileNotFoundError),
            contextlib.redirect_stderr(io.StringIO()) as errors,
        ):
            uninstall.uninstall(*self.paths, WINESERVER)
        self.assertIn("updating the desktop database failed", errors.getvalue())
        self.assertFalse(self.run_entry.exists())
        self.assertFalse(self.login_entry.exists())
        self.assertTrue(self.prefix.exists())


if __name__ == "__main__":
    unittest.main()
