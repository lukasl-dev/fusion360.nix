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
  pythonSource = ./python;
in
{
  # Ordinary source files stay readable and importable; Nix owns the interpreter
  # and its dependencies. No generated Python or runtime source extraction.
  inherit python pythonSource;

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

      # Fusion bundles Windows Qt. Linux desktop integration must not select its
      # platform plugins, styles, or plugin search paths. Keep rendering/debug
      # overrides such as QT_QUICK_BACKEND available for explicit diagnostics.
      unset QT_QPA_PLATFORM QT_QPA_PLATFORMTHEME QT_STYLE_OVERRIDE
      unset QT_PLUGIN_PATH QT_QPA_PLATFORM_PLUGIN_PATH QML2_IMPORT_PATH QML_IMPORT_PATH
      unset QT_WAYLAND_DISABLE_WINDOWDECORATION

      # Wine 11.16's CreateDesktop security handling breaks Chromium's sandbox,
      # leaving Fusion's Data Panel blank. This reduces browser isolation; users
      # can opt back in to retest with a compatible Wine build. Qt checks whether
      # its variable exists, so setting QTWEBENGINE_DISABLE_SANDBOX=0 is NOT an
      # opt-out; the enabled branch must remove it from the environment.
      # https://codeberg.org/Lolig4/Autodesk-Fusion-360-on-Linux/issues/10
      case "''${FUSION360_WEBENGINE_SANDBOX:-0}" in
        0) export QTWEBENGINE_DISABLE_SANDBOX=1 ;;
        1) unset QTWEBENGINE_DISABLE_SANDBOX ;;
        *)
          echo "FUSION360_WEBENGINE_SANDBOX must be 0 or 1." >&2
          exit 2
          ;;
      esac

      fail() {
        echo "fusion360: $*" >&2
        exit 1
      }

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
        flock "$1" --nonblock 9 || fail "Fusion is running, or another installation is in progress. Close Fusion; use 'fusion360 stop' if background Wine services remain."
      }

      require_stopped_prefix() {
        # Never kill Wine processes, including a pending login, to make an update succeed.
        timeout 5 "$WINESERVER" -w || fail "Wine is still running in this prefix. Close Fusion and run 'fusion360 stop' before changing it."
      }

      resolve_executable() {
        ${python}/bin/python3 ${pythonSource}/deployment.py resolve "$WINEPREFIX" "$1"
      }

      configure_graphics() {
        local backend="$1"
        local chromium="''${2:-}"

        case "$backend" in
          dxvk|opengl) ;;
          *) fail "Graphics must be dxvk or opengl." ;;
        esac
        case "$chromium" in
          ""|vulkan|opengles|gl) ;;
          *) fail "Chromium graphics must be vulkan, opengles, or gl." ;;
        esac

        # DXVK is selectable for the viewport only; the navigation UI keeps
        # builtin D3D9. X11 remains explicit even on a Wayland desktop.
        "$WINE" reg add 'HKCU\Software\Wine\Drivers' /v Graphics /d x11 /f >/dev/null
        "$WINE" reg add 'HKCU\Software\Wine\DllOverrides' /v d3d9 /d builtin /f >/dev/null

        local dll override=builtin
        [[ "$backend" != dxvk ]] || override=native
        for dll in d3d11 dxgi d3d10core; do
          "$WINE" reg add 'HKCU\Software\Wine\DllOverrides' /v "$dll" /d "$override" /f >/dev/null
        done
        ${python}/bin/python3 ${pythonSource}/graphics.py "$WINEPREFIX" "$backend" "$chromium"
        printf '%s\n' "$backend" > "$data_dir/graphics"
      }
    '';
}
