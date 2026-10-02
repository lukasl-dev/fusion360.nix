"""Change only Fusion's viewport, Qt, and Chromium rendering preferences.

These are three separate rendering paths. Never replace the entire options file
to fix one of them: it also contains account data and unrelated user preferences.
"""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def configure_graphics(prefix: Path, backend: str, chromium: str) -> None:
    users = prefix / "drive_c/users"
    default_profiles = {"public", "default", "default user", "all users"}

    for user in users.iterdir():
        if not user.is_dir() or user.name.lower() in default_profiles:
            continue

        path = (
            user
            / "AppData/Roaming/Autodesk/Neutron Platform/Options/NMachineSpecificOptions.xml"
        )
        if path.exists():
            # Autodesk writes UTF-16, but accept UTF-8 files from older recipes.
            raw = path.read_bytes()
            encoding = (
                "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
            )
            root = ET.fromstring(raw.decode(encoding))
        else:
            root = ET.Element("OptionGroups")

        viewport = "VirtualDeviceDx11" if backend == "dxvk" else "VirtualDeviceGLCore"
        for group_name, key, value in (
            ("BootstrapOptionsGroup", "driverOptionId", viewport),
            ("CompatibilityGroup", "graphicsApiOptionId", "OpenGL"),
        ):
            group = root.find(group_name)
            if group is None:
                group = ET.SubElement(root, group_name, SchemaVersion="2")

            option = group.find(key)
            if option is None:
                option = ET.SubElement(group, key)
            option.set("Value", value)

        # The previous phase always creates CompatibilityGroup. An omitted
        # browser argument preserves its existing selection; the tested default
        # applies only when that option does not exist yet.
        compatibility = root.find("CompatibilityGroup")
        assert compatibility is not None
        browser = compatibility.find("ChromiumGraphicsBackend")
        if browser is None:
            browser = ET.SubElement(
                compatibility, "ChromiumGraphicsBackend", Value="gl"
            )
        if chromium:
            browser.set("Value", chromium)

        # Atomic replacement preserves a parseable file. In particular, do not
        # introduce upstream's unrelated TrustAllServers/TLS bypass preference.
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        ET.ElementTree(root).write(temporary, encoding="utf-16", xml_declaration=True)
        temporary.replace(path)


if __name__ == "__main__":
    prefix, backend, chromium = sys.argv[1:]
    configure_graphics(Path(prefix), backend, chromium)
