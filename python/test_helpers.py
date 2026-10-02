"""Regression tests for deployment discovery, preferences, and desktop quoting."""

import datetime
import hashlib
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import deployment
import desktop
import graphics
import pylnk3


class HelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.prefix = Path(self.directory.name)
        self.user = self.prefix / "drive_c/users/test"
        self.user.mkdir(parents=True)
        self.deployments = (
            self.prefix / "drive_c/Program Files/Autodesk/webdeploy/production"
        )

    def executable(self, deployment_name: str, name: str = "Fusion360.exe") -> Path:
        path = self.deployments / deployment_name / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return path

    def shortcut(self, target: str, name: str = "Autodesk Fusion.lnk") -> None:
        directory = (
            self.user / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Autodesk"
        )
        directory.mkdir(parents=True, exist_ok=True)
        pylnk3.for_file(target, str(directory / name))

    def protocol_registration(self, registry: str, target: str) -> None:
        # Wine registry escaping is not shell escaping; %1 remains data here.
        command = f'"{target}" "%1"'
        encoded = command.replace("\\", "\\\\").replace('"', '\\"')
        (self.prefix / registry).write_text(
            "[Software\\\\Classes\\\\adskidmgr\\\\shell\\\\open\\\\command]\n"
            f'@="{encoded}"\n'
        )

    def test_missing_or_ambiguous_deployments_are_rejected(self) -> None:
        with self.assertRaisesRegex(SystemExit, "not found"):
            deployment.resolve_executable(self.prefix, "Fusion360.exe")

        active = self.executable("active")
        self.assertEqual(
            deployment.resolve_executable(self.prefix, "Fusion360.exe"), active
        )

        self.executable("old")
        with self.assertRaisesRegex(SystemExit, "Multiple"):
            deployment.resolve_executable(self.prefix, "Fusion360.exe")

    def test_shortcuts_choose_active_deployment_case_insensitively(self) -> None:
        active = self.executable("active")
        self.executable("old")
        self.shortcut(
            r"C:\PROGRAM FILES\AUTODESK\webdeploy\production\ACTIVE\fusion360.EXE"
        )
        self.assertEqual(
            deployment.resolve_executable(self.prefix, "Fusion360.exe"), active
        )

        self.shortcut(
            r"C:\Program Files\Autodesk\webdeploy\production\old\Fusion360.exe",
            "Another Fusion.lnk",
        )
        with self.assertRaisesRegex(SystemExit, "Conflicting installer pointers"):
            deployment.resolve_executable(self.prefix, "Fusion360.exe")

    def test_identity_registration_prefers_hkcu_over_hklm(self) -> None:
        name = "Autodesk Identity Manager/AdskIdentityManager.exe"
        active = self.executable("active", name)
        other = self.executable("other", name)
        self.protocol_registration(
            "user.reg",
            r"C:\Program Files\Autodesk\webdeploy\production\active\Autodesk Identity Manager\AdskIdentityManager.exe",
        )
        self.protocol_registration(
            "system.reg",
            r"C:\Program Files\Autodesk\webdeploy\production\other\Autodesk Identity Manager\AdskIdentityManager.exe",
        )
        self.assertEqual(
            deployment.resolve_executable(self.prefix, "AdskIdentityManager.exe"),
            active,
        )

        (self.prefix / "user.reg").unlink()
        self.assertEqual(
            deployment.resolve_executable(self.prefix, "AdskIdentityManager.exe"), other
        )

    def test_broken_and_unrelated_shortcuts_do_not_pick_a_deployment(self) -> None:
        self.executable("active")
        self.executable("old")
        self.shortcut(
            r"C:\Program Files\Autodesk\webdeploy\production\active\Fusion360.exe",
            "Unrelated.lnk",
        )
        (self.user / "Desktop").mkdir()
        (self.user / "Desktop/Broken Fusion.lnk").write_bytes(b"not a shortcut")
        # Simulate the parser's failure without its malformed-file descriptor
        # leak; unrelated shortcut names are filtered before parsing.
        with patch("deployment.pylnk3.parse", side_effect=ValueError("bad shortcut")):
            with self.assertRaisesRegex(SystemExit, "Multiple"):
                deployment.resolve_executable(self.prefix, "Fusion360.exe")

    def test_windows_target_rejects_escape_and_ambiguous_case(self) -> None:
        self.executable("active")
        target = r"C:\Program Files\Autodesk\webdeploy\production\active\Fusion360.exe"
        for invalid in (
            r"Z:\tmp\Fusion360.exe",
            target.replace("active", ".."),
            target.replace("active", "active\\"),
        ):
            with self.subTest(target=invalid):
                self.assertIsNone(
                    deployment.windows_target(
                        self.prefix, self.deployments, invalid, "Fusion360.exe"
                    )
                )

        (self.deployments / "ACTIVE").mkdir()
        self.assertIsNone(
            deployment.windows_target(
                self.prefix, self.deployments, target, "Fusion360.exe"
            )
        )

    def test_symlink_target_must_remain_inside_webdeploy(self) -> None:
        outside = self.prefix / "outside/Fusion360.exe"
        outside.parent.mkdir()
        outside.touch()
        self.deployments.mkdir(parents=True)
        (self.deployments / "escape").symlink_to(outside.parent)
        self.assertIsNone(
            deployment.windows_target(
                self.prefix,
                self.deployments,
                r"C:\Program Files\Autodesk\webdeploy\production\escape\Fusion360.exe",
                "Fusion360.exe",
            )
        )

    def test_programfiles_expansion_and_wrong_executable(self) -> None:
        active = self.executable("active")
        target = r"%PROGRAMFILES%\Autodesk\webdeploy\production\active\Fusion360.exe"
        self.assertEqual(
            deployment.windows_target(
                self.prefix, self.deployments, target, "Fusion360.exe"
            ),
            active,
        )
        self.assertIsNone(
            deployment.windows_target(
                self.prefix, self.deployments, target, "AdskIdentityManager.exe"
            )
        )

    def test_graphics_preserve_unrelated_settings_and_browser_choice(self) -> None:
        path = (
            self.user
            / "AppData/Roaming/Autodesk/Neutron Platform/Options/NMachineSpecificOptions.xml"
        )
        path.parent.mkdir(parents=True)
        path.write_text('<OptionGroups><Unrelated Value="keep"/></OptionGroups>')

        for backend, driver, chromium, expected in (
            ("opengl", "VirtualDeviceGLCore", "", "gl"),
            ("dxvk", "VirtualDeviceDx11", "", "gl"),
            ("dxvk", "VirtualDeviceDx11", "vulkan", "vulkan"),
            ("opengl", "VirtualDeviceGLCore", "", "vulkan"),
            ("opengl", "VirtualDeviceGLCore", "gl", "gl"),
            ("opengl", "VirtualDeviceGLCore", "opengles", "opengles"),
            ("opengl", "VirtualDeviceGLCore", "", "opengles"),
        ):
            with self.subTest(backend=backend, chromium=chromium):
                graphics.configure_graphics(self.prefix, backend, chromium)
                root = ET.parse(path).getroot()
                unrelated = root.find("Unrelated")
                viewport = root.find("BootstrapOptionsGroup/driverOptionId")
                browser = root.find("CompatibilityGroup/ChromiumGraphicsBackend")
                qt = root.find("CompatibilityGroup/graphicsApiOptionId")
                assert unrelated is not None and viewport is not None
                assert browser is not None and qt is not None
                self.assertEqual(unrelated.get("Value"), "keep")
                self.assertEqual(viewport.get("Value"), driver)
                self.assertEqual(browser.get("Value"), expected)
                self.assertEqual(qt.get("Value"), "OpenGL")
                self.assertNotIn("TrustAllServers", path.read_text(encoding="utf-16"))
                self.assertFalse(path.with_suffix(".tmp").exists())

    def test_graphics_create_only_real_user_preferences(self) -> None:
        for name in ("Public", "Default", "Default User", "All Users"):
            (self.prefix / "drive_c/users" / name).mkdir()
        graphics.configure_graphics(self.prefix, "opengl", "")
        files = list(self.prefix.rglob("NMachineSpecificOptions.xml"))
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].is_relative_to(self.user))

    def test_graphics_accept_utf8_bom_and_big_endian_utf16(self) -> None:
        path = (
            self.user
            / "AppData/Roaming/Autodesk/Neutron Platform/Options/NMachineSpecificOptions.xml"
        )
        path.parent.mkdir(parents=True)
        xml = '<OptionGroups><Unrelated Value="keep"/></OptionGroups>'
        inputs = (
            xml.encode("utf-8-sig"),
            b"\xfe\xff" + xml.encode("utf-16-be"),
        )
        for contents in inputs:
            with self.subTest(contents=contents[:2]):
                path.write_bytes(contents)
                graphics.configure_graphics(self.prefix, "opengl", "")
                unrelated = ET.parse(path).getroot().find("Unrelated")
                assert unrelated is not None
                self.assertEqual(unrelated.get("Value"), "keep")
                self.assertTrue(
                    path.read_bytes().startswith((b"\xff\xfe", b"\xfe\xff"))
                )

    def test_invalid_xml_is_not_replaced(self) -> None:
        path = (
            self.user
            / "AppData/Roaming/Autodesk/Neutron Platform/Options/NMachineSpecificOptions.xml"
        )
        path.parent.mkdir(parents=True)
        contents = b"<OptionGroups><unfinished>"
        path.write_bytes(contents)
        with self.assertRaises(ET.ParseError):
            graphics.configure_graphics(self.prefix, "opengl", "")
        self.assertEqual(path.read_bytes(), contents)
        self.assertFalse(path.with_suffix(".tmp").exists())

    def test_desktop_quoting_uses_desktop_entry_rules(self) -> None:
        self.assertEqual(desktop.quote_argument("a b"), '"a b"')
        self.assertEqual(desktop.quote_argument("a%b"), '"a%%b"')
        self.assertEqual(desktop.quote_argument("a\\b"), '"a\\\\\\\\b"')
        for character in ('"', "`", "$"):
            self.assertEqual(
                desktop.quote_argument(character), '"' + "\\\\" + character + '"'
            )
        self.assertEqual(
            desktop.environment_arguments("/data path", "/cache", "/state"),
            '"FUSION360_DATA_HOME=/data path" "FUSION360_CACHE_HOME=/cache" '
            '"FUSION360_STATE_HOME=/state"',
        )

    def test_metadata_records_installer_bytes_and_deployments(self) -> None:
        self.executable("active")
        installer = self.prefix / "installer.exe"
        installer.write_bytes(b"MZ test installer")
        now = datetime.datetime(2026, 10, 2, tzinfo=datetime.timezone.utc)
        with patch("deployment.datetime.datetime") as clock:
            clock.now.return_value = now
            deployment.record_installation(self.prefix, self.prefix, installer, "11.16")

        self.assertEqual(
            json.loads((self.prefix / "installation.json").read_text()),
            {
                "installed_at": now.isoformat(),
                "installer_sha256": hashlib.sha256(installer.read_bytes()).hexdigest(),
                "wine_version": "11.16",
                "deployments": [
                    "drive_c/Program Files/Autodesk/webdeploy/production/active/Fusion360.exe"
                ],
            },
        )
        self.assertFalse((self.prefix / "installation.tmp").exists())

    def test_failed_installation_does_not_publish_metadata(self) -> None:
        installer = self.prefix / "installer.exe"
        installer.write_bytes(b"MZ test installer")
        with self.assertRaisesRegex(SystemExit, "not marked complete"):
            deployment.record_installation(self.prefix, self.prefix, installer, "11.16")
        self.assertFalse((self.prefix / "installation.json").exists())


if __name__ == "__main__":
    unittest.main()
