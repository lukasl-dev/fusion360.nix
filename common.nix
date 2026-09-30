{
  wine,
  cacert,
  python3,
  coreutils,
  findutils,
  gnugrep,
  gnused,
  util-linux,
  systemd,
}:

let
  python = python3.withPackages (ps: [ ps.pylnk3 ]);
in
{
  runtimeInputs = [
    wine
    python
    coreutils
    findutils
    gnugrep
    gnused
    util-linux
    systemd
  ];

  # Shared prefix ownership and graphics policy, not a general Wine framework.
  text = # bash
    ''
      data_dir="''${FUSION360_DATA_HOME:-''${XDG_DATA_HOME:-$HOME/.local/share}/fusion360}"
      cache_dir="''${FUSION360_CACHE_HOME:-''${XDG_CACHE_HOME:-$HOME/.cache}/fusion360}"
      state_dir="''${FUSION360_STATE_HOME:-''${XDG_STATE_HOME:-$HOME/.local/state}/fusion360}"
      for directory in "$data_dir" "$cache_dir" "$state_dir"; do
        if [[ "$directory" != /* || "$directory" == / ]]; then
          echo "Fusion paths must be absolute, non-root directories: $directory" >&2
          exit 2
        fi
      done

      export WINEPREFIX="$data_dir/prefix"
      export WINE=${wine}/bin/wine
      export WINESERVER=${wine}/bin/wineserver
      export WINEARCH=win64
      export WINEDEBUG="''${WINEDEBUG:--all,+err}"
      export W_CACHE="$cache_dir/winetricks"
      export NIX_SSL_CERT_FILE="''${NIX_SSL_CERT_FILE:-${cacert}/etc/ssl/certs/ca-bundle.crt}"
      # Let the packaged desktop entries own Linux integration.
      export WINEDLLOVERRIDES="winemenubuilder.exe=d''${WINEDLLOVERRIDES:+;$WINEDLLOVERRIDES}"

      fail() { echo "fusion360: $*" >&2; exit 1; }

      require_display() {
        # Local terminals and browser handlers may lack the desktop environment.
        # Read only the local X11 display from this user's session manager; never
        # evaluate its shell-escaped output or redirect an SSH session silently.
        if [[ -z "''${DISPLAY:-}" && -z "''${SSH_CONNECTION:-}" && -z "''${SSH_TTY:-}" ]]; then
          local key value
          while IFS='=' read -r key value; do
            if [[ "$key" == DISPLAY && "$value" =~ ^:[0-9]+(\.[0-9]+)?$ ]]; then
              export DISPLAY="$value"
              break
            fi
          done < <(timeout 2 systemctl --user show-environment 2>/dev/null || true)
        fi
        [[ -n "''${DISPLAY:-}" ]] || fail "No X11 display. Run from your desktop terminal (Xwayland on Wayland)."
      }

      lock_prefix() {
        umask 077
        mkdir -p "$data_dir" "$state_dir"
        exec 9>"$data_dir/prefix.lock"
        flock "$1" --nonblock 9 || fail "Fusion is running, or another installation is in progress."
      }

      require_stopped_prefix() {
        # Never kill Wine processes, including a pending login, to make an update succeed.
        timeout 5 "$WINESERVER" -w || fail "Wine is still running in this prefix. Close Fusion before changing it."
      }

      resolve_executable() {
        ${python}/bin/python3 - "$WINEPREFIX" "$1" <<'PY'
      import pathlib
      import re
      import sys
      import pylnk3

      prefix, name = pathlib.Path(sys.argv[1]), sys.argv[2]
      root = prefix / "drive_c/Program Files/Autodesk/webdeploy/production"
      candidates = list(root.glob("*/Fusion360.exe")) if name == "Fusion360.exe" else list(root.glob("*/Autodesk Identity Manager/AdskIdentityManager.exe"))

      def unix_target(windows_path):
          if not windows_path:
              return None
          windows_path = re.sub(r"%programfiles%", lambda _: r"C:\Program Files", windows_path, flags=re.I)
          if not re.match(r"^[cC]:[\\/]", windows_path):
              return None
          target = prefix / "drive_c"
          try:
              for part in windows_path[3:].replace("\\", "/").split("/"):
                  if part in (".", "..", ""):
                      return None
                  matches = [p for p in target.iterdir() if p.name.casefold() == part.casefold()]
                  if len(matches) != 1:
                      return None
                  target = matches[0]
              resolved = target.resolve(strict=True)
              resolved.relative_to(root.resolve())
          except (OSError, ValueError):
              return None
          return resolved if resolved.is_file() and resolved.name.lower() == name.lower() else None

      pointers = set()
      if name == "Fusion360.exe":
          shortcuts = list((prefix / "drive_c/ProgramData/Microsoft/Windows/Start Menu").rglob("*.lnk"))
          for user in (prefix / "drive_c/users").glob("*"):
              shortcuts.extend((user / "AppData/Roaming/Microsoft/Windows/Start Menu").rglob("*.lnk"))
              shortcuts.extend((user / "Desktop").glob("*.lnk"))
          for shortcut in shortcuts:
              if "fusion" not in shortcut.name.lower():
                  continue
              try:
                  target = unix_target(pylnk3.parse(str(shortcut)).path)
              except Exception:
                  continue
              if target:
                  pointers.add(target)
      else:
          # Parse the installer-owned Windows protocol command; never execute it.
          for registry in (prefix / "user.reg", prefix / "system.reg"):
              if not registry.exists():
                  continue
              sections = re.split(r"(?m)^\[", registry.read_text(errors="replace"))
              for section in sections:
                  header, _, body = section.partition("\n")
                  key = header.split("]", 1)[0].replace("\\\\", "\\").lower()
                  if not key.endswith(r"classes\adskidmgr\shell\open\command"):
                      continue
                  match = re.search(r'^@="(.*)"$', body, re.MULTILINE)
                  if not match:
                      continue
                  command = match[1].replace(r'\"', '"').replace("\\\\", "\\")
                  executable = re.match(r'^"([^"]+)"', command)
                  target = unix_target(executable[1]) if executable else None
                  if target:
                      pointers.add(target)
              if pointers:
                  break  # HKCU overrides HKLM.
      if len(pointers) == 1:
          print(pointers.pop())
      elif len(pointers) > 1:
          raise SystemExit(f"Conflicting installer pointers for {name}; refusing to guess: {sorted(map(str, pointers))}")
      elif len(candidates) == 1:
          print(candidates[0])
      elif not candidates:
          raise SystemExit(f"{name} not found. Run 'fusion360 install' first.")
      else:
          raise SystemExit(f"Multiple {name} deployments found; refusing to launch an arbitrary old version. Run 'fusion360 doctor'.")
      PY
      }

      configure_graphics() {
        local backend="$1"
        case "$backend" in dxvk|opengl) ;; *) fail "Graphics must be dxvk or opengl." ;; esac
        "$WINE" reg add 'HKCU\Software\Wine\Drivers' /v Graphics /d x11 /f >/dev/null
        "$WINE" reg add 'HKCU\Software\Wine\DllOverrides' /v d3d9 /d builtin /f >/dev/null
        local dll override=builtin
        [[ "$backend" != dxvk ]] || override=native
        for dll in d3d11 dxgi d3d10core; do
          "$WINE" reg add 'HKCU\Software\Wine\DllOverrides' /v "$dll" /d "$override" /f >/dev/null
        done
        python3 - "$WINEPREFIX" "$backend" <<'PY'
      import pathlib
      import sys
      import xml.etree.ElementTree as ET

      prefix, backend = pathlib.Path(sys.argv[1]), sys.argv[2]
      users = [p for p in (prefix / "drive_c/users").iterdir()
               if p.is_dir() and p.name.lower() not in ("public", "default", "default user", "all users")]
      for user in users:
          path = user / "AppData/Roaming/Autodesk/Neutron Platform/Options/NMachineSpecificOptions.xml"
          if path.exists():
              raw = path.read_bytes()
              encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
              tree = ET.ElementTree(ET.fromstring(raw.decode(encoding)))
          else:
              tree = ET.ElementTree(ET.Element("OptionGroups"))
          root = tree.getroot()
          for group_name, key, value in (
              ("BootstrapOptionsGroup", "driverOptionId", "VirtualDeviceDx11" if backend == "dxvk" else "VirtualDeviceGLCore"),
              ("CompatibilityGroup", "graphicsApiOptionId", "OpenGL"),
              ("CompatibilityGroup", "ChromiumGraphicsBackend", "opengles"),
          ):
              group = root.find(group_name)
              if group is None:
                  group = ET.SubElement(root, group_name, SchemaVersion="2")
              option = group.find(key)
              if option is None:
                  option = ET.SubElement(group, key)
              option.set("Value", value)
          # Never relax TLS verification or replace unrelated user preferences.
          path.parent.mkdir(parents=True, exist_ok=True)
          temporary = path.with_suffix(".tmp")
          tree.write(temporary, encoding="utf-16", xml_declaration=True)
          temporary.replace(path)
      PY
        printf '%s\n' "$backend" > "$data_dir/graphics"
      }
    '';
}
