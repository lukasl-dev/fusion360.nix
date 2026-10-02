"""Resolve installer-owned launch targets and record installation provenance.

An Autodesk update can leave several webdeploy directories behind. Their names
and modification times do not identify the active version: shortcuts and the
Identity Manager protocol registration do. Never execute a registry command to
discover its target, and never pick an arbitrary directory when pointers disagree.
"""

import datetime
import hashlib
import json
import re
import sys
from pathlib import Path

import pylnk3


def windows_target(
    prefix: Path,
    deployment_root: Path,
    windows_path: str | None,
    executable_name: str,
) -> Path | None:
    """Map a Windows C: target to one unambiguous file inside webdeploy."""
    if not windows_path:
        return None

    windows_path = re.sub(
        r"%programfiles%",
        lambda _: r"C:\Program Files",
        windows_path,
        flags=re.IGNORECASE,
    )
    if not re.match(r"^[cC]:[\\/]", windows_path):
        return None

    # Windows names are case-insensitive; Linux names are not. Walk components
    # rather than lowercasing a path or accepting two Linux files as one target.
    target = prefix / "drive_c"
    try:
        for component in windows_path[3:].replace("\\", "/").split("/"):
            if component in (".", "..", ""):
                return None

            matches = [
                entry
                for entry in target.iterdir()
                if entry.name.casefold() == component.casefold()
            ]
            if len(matches) != 1:
                return None
            target = matches[0]

        resolved = target.resolve(strict=True)
        resolved.relative_to(deployment_root.resolve())
    except (OSError, ValueError):
        return None

    # The containment check above also rejects symlinks escaping webdeploy.
    if resolved.is_file() and resolved.name.lower() == executable_name.lower():
        return resolved
    return None


def fusion_shortcuts(prefix: Path) -> list[Path]:
    shortcuts = list(
        (prefix / "drive_c/ProgramData/Microsoft/Windows/Start Menu").rglob("*.lnk")
    )
    for user in (prefix / "drive_c/users").glob("*"):
        shortcuts.extend(
            (user / "AppData/Roaming/Microsoft/Windows/Start Menu").rglob("*.lnk")
        )
        shortcuts.extend((user / "Desktop").glob("*.lnk"))
    return shortcuts


def identity_protocol_targets(
    prefix: Path, deployment_root: Path, executable_name: str
) -> set[Path]:
    """Read HKCU first, falling back to HKLM only if no valid HKCU target exists."""
    pointers: set[Path] = set()
    for registry in (prefix / "user.reg", prefix / "system.reg"):
        if not registry.exists():
            continue

        sections = re.split(r"(?m)^\[", registry.read_text(errors="replace"))
        for section in sections:
            header, _, body = section.partition("\n")
            key = header.split("]", 1)[0].replace("\\\\", "\\").lower()
            if not key.endswith(r"classes\adskidmgr\shell\open\command"):
                continue

            value = re.search(r'^@="(.*)"$', body, re.MULTILINE)
            if not value:
                continue

            command = value[1].replace(r"\"", '"').replace("\\\\", "\\")
            executable = re.match(r'^"([^"]+)"', command)
            target = windows_target(
                prefix,
                deployment_root,
                executable[1] if executable else None,
                executable_name,
            )
            if target:
                pointers.add(target)

        if pointers:
            break

    return pointers


def resolve_executable(prefix: Path, executable_name: str) -> Path:
    deployment_root = prefix / "drive_c/Program Files/Autodesk/webdeploy/production"

    if executable_name == "Fusion360.exe":
        candidates = list(deployment_root.glob("*/Fusion360.exe"))
        pointers: set[Path] = set()
        for shortcut in fusion_shortcuts(prefix):
            if "fusion" not in shortcut.name.lower():
                continue

            # Unrelated or broken shortcuts must not prevent checking the rest.
            try:
                target = windows_target(
                    prefix,
                    deployment_root,
                    pylnk3.parse(str(shortcut)).path,
                    executable_name,
                )
            except Exception:
                continue

            if target:
                pointers.add(target)
    else:
        candidates = list(
            deployment_root.glob("*/Autodesk Identity Manager/AdskIdentityManager.exe")
        )
        pointers = identity_protocol_targets(prefix, deployment_root, executable_name)

    if len(pointers) == 1:
        return pointers.pop()
    if len(pointers) > 1:
        raise SystemExit(
            f"Conflicting installer pointers for {executable_name}; refusing to guess: "
            f"{sorted(map(str, pointers))}"
        )

    # A single deployment is safe when an installer has not created pointers.
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise SystemExit(f"{executable_name} not found. Run 'fusion360 install' first.")
    raise SystemExit(
        f"Multiple {executable_name} deployments found; refusing to launch an "
        "arbitrary old version. Run 'fusion360 doctor'."
    )


def record_installation(
    prefix: Path, data_directory: Path, installer: Path, wine_version: str
) -> None:
    """Record the downloaded bytes, not a fictitiously pinned Fusion release."""
    executables = list(
        prefix.glob(
            "drive_c/Program Files/Autodesk/webdeploy/production/*/Fusion360.exe"
        )
    )
    if not executables:
        raise SystemExit(
            "Installer exited without producing Fusion360.exe; installation not marked complete."
        )

    with installer.open("rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()

    metadata: dict[str, str | list[str]] = {
        "installed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "installer_sha256": digest,
        "wine_version": wine_version,
        "deployments": [str(path.relative_to(prefix)) for path in executables],
    }

    # Do not publish partially written success metadata if the process stops.
    destination = data_directory / "installation.json"
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(metadata, indent=2) + "\n")
    temporary.replace(destination)


def main() -> None:
    command, *arguments = sys.argv[1:]
    if command == "resolve":
        prefix, executable_name = arguments
        print(resolve_executable(Path(prefix), executable_name))
    elif command == "record":
        prefix, data_directory, installer, wine_version = arguments
        record_installation(
            Path(prefix), Path(data_directory), Path(installer), wine_version
        )
    else:
        raise SystemExit(f"Unknown deployment operation: {command}")


if __name__ == "__main__":
    main()
