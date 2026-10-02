"""Exercise packaged CLI boundaries without Wine, a display, or a live prefix."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

LAUNCHER = Path(os.environ["FUSION360_TEST_LAUNCHER"])
COMMON_SHELL = Path(os.environ["FUSION360_TEST_COMMON"])


class RuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        self.environment = dict(os.environ)
        for key in (
            "DISPLAY",
            "WAYLAND_DISPLAY",
            "SSH_TTY",
            "XDG_DATA_HOME",
            "XDG_CACHE_HOME",
            "XDG_STATE_HOME",
            "XDG_CONFIG_HOME",
            "FUSION360_DATA_HOME",
            "FUSION360_CACHE_HOME",
            "FUSION360_STATE_HOME",
            "FUSION360_WEBENGINE_SANDBOX",
        ):
            self.environment.pop(key, None)

        # SSH markers ensure a live systemd desktop cannot hide missing-display
        # cases. Every test uses its own HOME rather than the developer's prefix.
        self.environment["HOME"] = str(self.home)
        self.environment["SSH_CONNECTION"] = "test"

    def launch(
        self, *arguments: str, environment: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(LAUNCHER), *arguments],
            env=self.environment | (environment or {}),
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )

    def test_help_does_not_create_state(self) -> None:
        result = self.launch("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for description in (
            "login URL",
            "doctor",
            "stop ",
            "uninstall",
            "virtual-desktop",
            "default: opengl",
            "FUSION360_WEBENGINE_SANDBOX=1",
        ):
            self.assertIn(description, result.stdout)
        self.assertFalse((self.home / ".local/share/fusion360").exists())

    def test_invalid_commands_and_options_do_not_create_state(self) -> None:
        cases = (
            (("nonsense",), "Unknown command"),
            (("run", "--virtual-desktop"), "Usage:"),
            (("run", "--virtual-desktop", "invalid"), "Virtual desktop size must be"),
            (("stop", "unexpected"), "stop takes no arguments"),
            (("login", "https://example.com"), "Expected an adskidmgr"),
            (("install", "--graphics", "invalid"), "Graphics must be"),
            (("graphics", "dxvk", "invalid"), "Chromium graphics must be"),
            (("run",), "No X11 display"),
            (("uninstall", "--invalid"), "Unknown uninstall option"),
            (("uninstall", "--yes"), "--yes requires --purge"),
        )
        for arguments, message in cases:
            with self.subTest(arguments=arguments):
                result = self.launch(*arguments)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertFalse((self.home / ".local/share/fusion360").exists())

    def test_relative_state_path_is_rejected(self) -> None:
        result = self.launch("--help", environment={"FUSION360_DATA_HOME": "relative"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("paths must be absolute", result.stderr)

    def test_login_callback_is_not_evaluated(self) -> None:
        injected = self.home / "injected"
        callback = f"adskidmgr:/login?code=$(touch {injected})"
        result = self.launch("login", callback)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No X11 display", result.stderr)
        self.assertNotIn(callback, result.stdout + result.stderr)
        self.assertFalse(injected.exists())

    def test_uninstall_help_and_default_keep_state(self) -> None:
        result = self.launch("uninstall", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--purge", result.stdout)
        self.assertFalse((self.home / ".local/share/fusion360").exists())

        # Desktop-only removal must not initialize or query a Wine prefix.
        data = self.home / ".local/share/fusion360"
        data.mkdir(parents=True)
        sentinel = data / "local-document"
        sentinel.write_text("keep")
        for _ in range(2):
            result = self.launch("uninstall")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("state was kept", result.stdout)
            self.assertEqual(sentinel.read_text(), "keep")
            self.assertFalse((data / "prefix.lock").exists())

    def test_purge_requires_confirmation_without_a_terminal(self) -> None:
        result = self.launch("uninstall", "--purge")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("confirmation", result.stderr)
        self.assertFalse((self.home / ".local/share/fusion360").exists())

    def test_confirmed_empty_purge_does_not_start_wine(self) -> None:
        result = self.launch("uninstall", "--purge", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = self.home / ".local/share/fusion360"
        self.assertEqual([path.name for path in data.iterdir()], ["prefix.lock"])

    def shell_environment(
        self, values: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        # Source the actual shared shell text. Tests no longer scrape Python or
        # function bodies out of a generated launcher to re-execute approximations.
        return subprocess.run(
            ["bash", "-eu", "-c", 'source "$1"; env', "test", str(COMMON_SHELL)],
            env=self.environment | values,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )

    def test_linux_qt_paths_are_removed_but_diagnostics_remain(self) -> None:
        linux_qt = (
            "QT_QPA_PLATFORM",
            "QT_QPA_PLATFORMTHEME",
            "QT_STYLE_OVERRIDE",
            "QT_PLUGIN_PATH",
            "QT_QPA_PLATFORM_PLUGIN_PATH",
            "QML2_IMPORT_PATH",
            "QML_IMPORT_PATH",
            "QT_WAYLAND_DISABLE_WINDOWDECORATION",
        )
        result = self.shell_environment(
            dict.fromkeys(linux_qt, "linux-only") | {"QT_QUICK_BACKEND": "software"}
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        cleaned = dict(
            line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
        )
        self.assertTrue(all(key not in cleaned for key in linux_qt))
        self.assertEqual(cleaned["QT_QUICK_BACKEND"], "software")
        self.assertEqual(cleaned["QTWEBENGINE_DISABLE_SANDBOX"], "1")

    def test_sandbox_opt_in_removes_qt_variable(self) -> None:
        result = self.shell_environment(
            {"FUSION360_WEBENGINE_SANDBOX": "1", "QTWEBENGINE_DISABLE_SANDBOX": "1"}
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("QTWEBENGINE_DISABLE_SANDBOX=", result.stdout)

        result = self.shell_environment({"FUSION360_WEBENGINE_SANDBOX": "invalid"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be 0 or 1", result.stderr)

    def test_local_display_discovery(self) -> None:
        environment = dict(self.environment)
        environment.pop("SSH_CONNECTION")
        # Only the session-manager query is mocked. Values are output as data,
        # not evaluated, even if they contain shell-looking text.
        harness = """
        source "$1"
        systemctl() { printf '%s\\n' "$SESSION_ENV"; }
        timeout() { shift; "$@"; }
        require_display
        printf '%s' "$DISPLAY"
        """
        cases = (
            ({"SESSION_ENV": "OTHER=ignore\nDISPLAY=:7.0"}, ":7.0"),
            ({"DISPLAY": ":9", "SESSION_ENV": "DISPLAY=:7"}, ":9"),
            ({"SESSION_ENV": ""}, None),
            ({"SESSION_ENV": "DISPLAY=remote.example:0"}, None),
            ({"SESSION_ENV": "DISPLAY=$(exit 42)"}, None),
            ({"SESSION_ENV": "DISPLAY=:7", "SSH_CONNECTION": "remote"}, None),
            ({"SESSION_ENV": "DISPLAY=:7", "SSH_TTY": "/dev/pts/1"}, None),
        )
        for values, display in cases:
            with self.subTest(values=values):
                result = subprocess.run(
                    ["bash", "-eu", "-c", harness, "test", str(COMMON_SHELL)],
                    env=environment | values,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=10,
                )
                if display is None:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("No X11 display", result.stderr)
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, display)


if __name__ == "__main__":
    unittest.main()
