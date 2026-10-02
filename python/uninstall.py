"""Remove user-owned desktop integration, with separately confirmed state deletion.

The default operation never deletes a Wine prefix. Purging is deliberately more
restrictive than choosing runtime paths: a broad or shared directory is a valid
place to create files, but not a safe directory to delete recursively.
"""

import configparser
import fcntl
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from desktop import environment_arguments


def owned_desktop_launcher(entry: Path, assignments: str, action: str) -> Path | None:
    """Recognize the exact Exec shape written by `fusion360 desktop`, including old builds."""
    if entry.is_symlink() or not entry.is_file():
        return None
    if not stat.S_IMODE(entry.stat().st_mode) & 0o222:
        return None

    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(entry.read_text())
        command = parser.get("Desktop Entry", "Exec")
    except (OSError, UnicodeError, configparser.Error):
        return None

    match = re.fullmatch(
        r"/nix/store/[a-z0-9]{32}-[^/]+/bin/env "
        + re.escape(assignments)
        + r" (/nix/store/[a-z0-9]{32}-fusion360)/bin/fusion360 "
        + re.escape(action),
        command,
    )
    return Path(match[1]) if match else None


def remove_mime_association(config_home: Path, applications: Path) -> None:
    """Remove only this handler from user-writable MIME lists; preserve other handlers."""
    candidates: set[Path] = set()
    for directory in (config_home, applications):
        candidates.add(directory / "mimeapps.list")
        candidates.update(directory.glob("*-mimeapps.list"))

    for path in sorted(candidates):
        if path.is_symlink():
            print(f"Keeping declarative MIME configuration: {path}")
            continue
        if not path.is_file():
            continue
        mode = stat.S_IMODE(path.stat().st_mode)
        if not mode & 0o222 or not os.access(path, os.W_OK):
            print(f"Keeping read-only MIME configuration: {path}")
            continue

        original = path.read_text()
        replacement: list[str] = []
        section = ""
        for line in original.splitlines(keepends=True):
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                section = stripped[1:-1]
            key, separator, value = line.partition("=")
            if (
                section
                in (
                    "Default Applications",
                    "Added Associations",
                    "Removed Associations",
                )
                and separator
                and key.strip() == "x-scheme-handler/adskidmgr"
            ):
                handlers = [
                    handler.strip()
                    for handler in value.strip().split(";")
                    if handler.strip()
                ]
                if "fusion360-login.desktop" in handlers:
                    remaining = [
                        handler
                        for handler in handlers
                        if handler != "fusion360-login.desktop"
                    ]
                    if remaining:
                        ending = "\n" if line.endswith("\n") else ""
                        replacement.append(
                            key + "=" + ";".join(remaining) + ";" + ending
                        )
                    continue
            replacement.append(line)

        updated = "".join(replacement)
        if updated == original:
            continue

        # Preserve comments and other MIME keys rather than reserializing an INI
        # document. Atomic replacement never modifies a Home Manager store file.
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", dir=path.parent, delete=False
            ) as output:
                temporary = Path(output.name)
                output.write(updated)
            temporary.chmod(mode)
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def remove_desktop_integration(
    data_dir: Path,
    cache_dir: Path,
    state_dir: Path,
    applications: Path,
    config_home: Path,
    desktop_root: Path,
) -> None:
    assignments = environment_arguments(str(data_dir), str(cache_dir), str(state_dir))
    launchers: set[Path] = set()
    removed_login = False
    for name, action in (
        ("fusion360.desktop", "run %F"),
        ("fusion360-login.desktop", "login %u"),
    ):
        entry = applications / name
        launcher = owned_desktop_launcher(entry, assignments, action)
        if launcher is None:
            if entry.exists() or entry.is_symlink():
                print(f"Keeping desktop entry not owned by this registration: {entry}")
            continue
        entry.unlink()
        launchers.add(launcher)
        removed_login |= name == "fusion360-login.desktop"
        print(f"Removed desktop entry: {entry}")

    if removed_login:
        remove_mime_association(config_home, applications)
    if desktop_root.is_symlink() and desktop_root.readlink() in launchers:
        desktop_root.unlink()
        print(f"Removed desktop GC root: {desktop_root}")
    elif desktop_root.exists() or desktop_root.is_symlink():
        print(f"Keeping desktop GC root without matching registration: {desktop_root}")

    if launchers:
        try:
            refreshed = subprocess.run(
                ["update-desktop-database", str(applications)],
                check=False,
                capture_output=True,
            )
            refresh_failed = refreshed.returncode != 0
        except OSError:
            refresh_failed = True
        if refresh_failed:
            print(
                "Desktop entries removed, but updating the desktop database failed.",
                file=sys.stderr,
            )


def purge_targets(
    data_dir: Path,
    cache_dir: Path,
    state_dir: Path,
    applications: Path,
    config_home: Path,
    desktop_root: Path,
) -> list[Path]:
    """Preflight every deletion before removing anything, including desktop entries."""
    directories = (data_dir, cache_dir, state_dir)
    resolved = [directory.resolve() for directory in directories]
    home = Path.home().resolve()
    protected = [
        home,
        Path.cwd().resolve(),
        Path("/nix/store"),
        Path("/usr"),
        Path("/etc"),
        Path("/var"),
        Path("/tmp"),
        Path("/run"),
        Path("/proc"),
        Path("/sys"),
        Path("/dev"),
        config_home.resolve(),
        applications.resolve(),
        applications.parent.resolve(),
        desktop_root.parent.parent.resolve(),
        Path(os.environ.get("XDG_CACHE_HOME", str(home / ".cache"))).resolve(),
    ]
    for directory, canonical in zip(directories, resolved, strict=True):
        if not directory.is_absolute() or directory.is_symlink():
            raise SystemExit(
                f"Refusing to purge a relative or symlinked state directory: {directory}"
            )
        if any(
            path.is_relative_to(canonical) for path in protected
        ) or canonical.is_relative_to(Path("/nix/store")):
            raise SystemExit(
                f"Refusing to purge a broad or protected directory: {directory}"
            )
        if directory.exists() and not directory.is_dir():
            raise SystemExit(f"Expected a dedicated state directory: {directory}")
    for index, directory in enumerate(resolved):
        if any(
            other.is_relative_to(directory) or directory.is_relative_to(other)
            for other in resolved[index + 1 :]
        ):
            raise SystemExit("Refusing to purge overlapping Fusion state directories.")

    data_names = {
        "prefix",
        "prefix.lock",
        "setup",
        "installation.json",
        "graphics",
        "backups",
    }
    cache_names = {
        "winetricks",
        "Fusion Admin Install.exe",
        "Fusion Admin Install.exe.part",
        "MicrosoftEdgeWebView2RuntimeInstallerX64.exe",
        "MicrosoftEdgeWebView2RuntimeInstallerX64.exe.part",
    }
    for directory, allowed in ((data_dir, data_names), (cache_dir, cache_names)):
        if directory.exists():
            for child in directory.iterdir():
                if child.name not in allowed:
                    raise SystemExit(
                        f"Refusing to purge a directory containing unrelated files: {child}"
                    )
    if state_dir.exists():
        for child in state_dir.iterdir():
            if child == desktop_root:
                assignments = environment_arguments(
                    str(data_dir), str(cache_dir), str(state_dir)
                )
                launchers = {
                    owned_desktop_launcher(applications / name, assignments, action)
                    for name, action in (
                        ("fusion360.desktop", "run %F"),
                        ("fusion360-login.desktop", "login %u"),
                    )
                }
                if not child.is_symlink() or child.readlink() not in launchers:
                    raise SystemExit(
                        f"Refusing to purge an unrecognized desktop GC root: {child}"
                    )
                continue
            if child.is_dir() or not re.fullmatch(
                r"(?:run|install|update)-\d{8}T\d{6}-\d+\.log", child.name
            ):
                raise SystemExit(
                    f"Refusing to purge a directory containing unrelated files: {child}"
                )

    prefix = data_dir / "prefix"
    if prefix.is_symlink():
        raise SystemExit("Refusing to purge a symlinked Wine prefix.")
    if prefix.exists() and not (
        (prefix / "system.reg").is_file() and (data_dir / "setup/prefix").is_file()
    ):
        raise SystemExit("Refusing to purge a prefix without Fusion setup markers.")
    metadata = data_dir / "installation.json"
    if metadata.exists():
        try:
            installed: object = json.loads(metadata.read_text())
        except (OSError, ValueError) as error:
            raise SystemExit(
                f"Cannot validate Fusion installation metadata: {metadata}"
            ) from error
        if not isinstance(installed, dict) or "installer_sha256" not in installed:
            raise SystemExit(
                f"Cannot validate Fusion installation metadata: {metadata}"
            )

    # Keep the data directory and its lock inode. Unlinking an acquired lock
    # would let a concurrent launcher create a new inode and bypass that lock.
    targets = (
        [child for child in data_dir.iterdir() if child.name != "prefix.lock"]
        if data_dir.exists()
        else []
    )
    targets.extend(
        directory for directory in (cache_dir, state_dir) if directory.exists()
    )
    return targets


@contextmanager
def stopped_prefix(data_dir: Path, wineserver: str) -> Iterator[None]:
    """Hold the same exclusive lock as setup; never terminate Wine to get it."""
    # Even a cache-only purge participates in locking, so an installation
    # cannot start concurrently simply because its prefix does not exist yet.
    data_dir.mkdir(parents=True, exist_ok=True)
    lock = data_dir / "prefix.lock"
    if lock.is_symlink():
        raise SystemExit("Refusing to use a symlinked prefix lock.")
    with lock.open("a") as file:
        try:
            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise SystemExit(
                "Fusion is running, or another installation is in progress. Close Fusion and run 'fusion360 stop' first."
            ) from error
        if (data_dir / "prefix").exists():
            try:
                subprocess.run(
                    [wineserver, "-w"],
                    env=os.environ | {"WINEPREFIX": str(data_dir / "prefix")},
                    check=True,
                    timeout=5,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except (subprocess.SubprocessError, OSError) as error:
                raise SystemExit(
                    "Wine is still running or could not be checked. Run 'fusion360 stop' before purging; no processes were killed."
                ) from error
        yield


def uninstall(
    data_dir: Path,
    cache_dir: Path,
    state_dir: Path,
    applications: Path,
    config_home: Path,
    desktop_root: Path,
    wineserver: str,
    purge: bool = False,
    confirmed: bool = False,
) -> None:
    if not purge:
        remove_desktop_integration(
            data_dir, cache_dir, state_dir, applications, config_home, desktop_root
        )
        print("Desktop integration removed where user-owned. Fusion state was kept.")
        return

    targets = purge_targets(
        data_dir, cache_dir, state_dir, applications, config_home, desktop_root
    )
    print(
        "Purge deletes the local Wine prefix (including account data and local documents),"
    )
    print("backups, downloaded installers, and logs. Cloud projects are not deleted.")
    for path in targets:
        print(f"  {path}")
    if not confirmed:
        if not sys.stdin.isatty():
            raise SystemExit(
                "Purge needs interactive confirmation, or explicit --purge --yes. Nothing was removed."
            )
        try:
            answer = input("Type PURGE to confirm: ")
        except EOFError:
            answer = ""
        if answer != "PURGE":
            raise SystemExit("Uninstall cancelled. Nothing was removed.")

    with stopped_prefix(data_dir, wineserver):
        # Revalidate after taking the lock: setup may have changed state between
        # the confirmation and lock acquisition, but cannot do so while we hold it.
        targets = purge_targets(
            data_dir, cache_dir, state_dir, applications, config_home, desktop_root
        )
        remove_desktop_integration(
            data_dir, cache_dir, state_dir, applications, config_home, desktop_root
        )
        for path in targets:
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.exists():
                shutil.rmtree(path)
        print(
            "Fusion state purged. The data directory's prefix.lock was kept for safe locking."
        )


def main() -> None:
    data, cache, state, applications, config, root, wineserver, purge, confirmed = (
        sys.argv[1:]
    )
    uninstall(
        Path(data),
        Path(cache),
        Path(state),
        Path(applications),
        Path(config),
        Path(root),
        wineserver,
        purge == "1",
        confirmed == "1",
    )


if __name__ == "__main__":
    main()
