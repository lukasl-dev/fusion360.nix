{
  writeShellApplication,
  common,
  installer,
  wine,
  xdg-utils,
  desktop-file-utils,
  mesa-demos,
  vulkan-tools,
  nix,
  coreutils,
}:

writeShellApplication {
  name = "fusion360";
  runtimeInputs = common.runtimeInputs ++ [
    xdg-utils
    desktop-file-utils
    mesa-demos
    vulkan-tools
    nix
  ];
  text =
    common.text
    # bash
    + ''
      usage() {
        cat <<'EOF'
      Autodesk Fusion on NixOS

      Usage: fusion360 [COMMAND] [OPTIONS]

      Commands:
        run [FILES...]          Launch Fusion (default); never installs implicitly
        install [OPTIONS...]    Install directly from Autodesk
        update [OPTIONS...]     Update, backing up the stopped prefix first
        login URL               Deliver an adskidmgr: browser sign-in callback
        graphics dxvk|opengl    Change rendering backend while Fusion is stopped
        desktop                 Register launch and login entries for this user
        doctor                  Show Wine, graphics, installation, and login diagnostics

      Installation options:
        --graphics dxvk|opengl  Initial viewport backend (default: dxvk)
        --installer FILE       Use a previously downloaded Autodesk installer
        --no-backup            Skip the update backup

      State defaults:
        ~/.local/share/fusion360   Wine prefix and backups
        ~/.cache/fusion360         Downloaded installers
        ~/.local/state/fusion360   Logs

      XDG_DATA_HOME, XDG_CACHE_HOME, and XDG_STATE_HOME are respected.
      Override individual locations with FUSION360_{DATA,CACHE,STATE}_HOME.
      EOF
      }

      command="''${1:-run}"
      (( $# == 0 )) || shift
      case "$command" in
        --help|-h|help) usage; exit 0 ;;
        install|update) exec ${installer}/bin/fusion360-install "$command" "$@" ;;
        run|login|graphics|desktop|doctor) ;;
        *) fail "Unknown command: $command. Use 'fusion360 --help'." ;;
      esac

      case "$command" in
        desktop)
          (( $# == 0 )) || fail "desktop takes no arguments."
          applications="''${XDG_DATA_HOME:-$HOME/.local/share}/applications"
          mkdir -p "$applications" "''${XDG_CONFIG_HOME:-$HOME/.config}"
          # A GC root keeps these absolute store paths valid after a one-off nix run.
          root="''${XDG_STATE_HOME:-$HOME/.local/state}/fusion360/desktop-root"
          mkdir -p "$(dirname "$root")"
          command -v nix >/dev/null || fail "nix is needed to register persistent desktop entries."
          nix-store --add-root "$root" --indirect --realise ${placeholder "out"} >/dev/null
          # Desktop Entry argument escaping differs from shell escaping. Preserve
          # custom prefix locations without embedding a shell command.
          desktop_environment="$(python3 - "$data_dir" "$cache_dir" "$state_dir" <<'PY'
      import sys
      def quote(value):
          value = value.replace("\\", "\\\\\\\\").replace('"', '\\\\"').replace('`', '\\\\`').replace('$', '\\\\$').replace('%', '%%')
          return '"' + value + '"'
      print(" ".join(quote(key + "=" + value) for key, value in zip(
          ("FUSION360_DATA_HOME", "FUSION360_CACHE_HOME", "FUSION360_STATE_HOME"), sys.argv[1:])))
      PY
          )"
          cat > "$applications/fusion360.desktop" <<EOF
      [Desktop Entry]
      Type=Application
      Name=Autodesk Fusion
      Exec=${coreutils}/bin/env $desktop_environment ${placeholder "out"}/bin/fusion360 run %F
      Icon=applications-engineering
      Categories=Graphics;Engineering;
      Terminal=false
      EOF
          cat > "$applications/fusion360-login.desktop" <<EOF
      [Desktop Entry]
      Type=Application
      Name=Autodesk Fusion sign-in
      Exec=${coreutils}/bin/env $desktop_environment ${placeholder "out"}/bin/fusion360 login %u
      MimeType=x-scheme-handler/adskidmgr;
      NoDisplay=true
      Terminal=false
      EOF
          update-desktop-database "$applications"
          # Home Manager may own an immutable mimeapps.list with this already set.
          if [[ "$(xdg-mime query default x-scheme-handler/adskidmgr)" != fusion360-login.desktop ]]; then
            xdg-mime default fusion360-login.desktop x-scheme-handler/adskidmgr
          fi
          [[ "$(xdg-mime query default x-scheme-handler/adskidmgr)" == fusion360-login.desktop ]] \
            || fail "The desktop files were created, but your MIME configuration did not accept the login handler."
          echo "Registered Fusion and adskidmgr: login for this user."
          ;;
        doctor)
          (( $# == 0 )) || fail "doctor takes no arguments."
          echo "Wine: $($WINE --version)"
          echo "Wine package: ${wine}"
          echo "Prefix: $WINEPREFIX"
          echo "Cache: $cache_dir"
          echo "Logs: $state_dir"
          echo "DISPLAY: ''${DISPLAY:-unset}"
          echo "WAYLAND_DISPLAY: ''${WAYLAND_DISPLAY:-unset} (Xwayland is used)"
          echo "Graphics: $(cat "$data_dir/graphics" 2>/dev/null || echo 'dxvk (default)')"
          echo "64-bit graphics drivers: $(readlink -f /run/opengl-driver 2>/dev/null || echo missing)"
          echo "32-bit graphics drivers: $(readlink -f /run/opengl-driver-32 2>/dev/null || echo missing) (legacy Wine only)"
          echo "Login handler: $(xdg-mime query default x-scheme-handler/adskidmgr || true)"
          echo "Fusion executable: $(resolve_executable Fusion360.exe || true)"
          echo "Identity Manager: $(resolve_executable AdskIdentityManager.exe || true)"
          if [[ -n "''${DISPLAY:-}" ]]; then
            glxinfo -B || true
            vulkaninfo --summary || true
          fi
          if [[ -f "$data_dir/installation.json" ]]; then cat "$data_dir/installation.json"; fi
          ;;
        graphics)
          (( $# == 1 )) || fail "Usage: fusion360 graphics dxvk|opengl"
          [[ -f "$WINEPREFIX/system.reg" ]] || fail "Install Fusion first."
          require_display
          lock_prefix --exclusive
          require_stopped_prefix
          configure_graphics "$1"
          "$WINESERVER" -w
          echo "Viewport backend set to $1; Qt uses OpenGL."
          ;;
        login)
          (( $# == 1 )) || fail "Usage: fusion360 login 'adskidmgr:/login?code=…'"
          # No eval, sh -c, or logging of authentication codes.
          [[ "$1" == adskidmgr:/login\?* || "$1" == adskidmgr://login\?* ]] || fail "Expected an adskidmgr: login callback."
          require_display
          lock_prefix --shared
          executable="$(resolve_executable AdskIdentityManager.exe)"
          cd "$(dirname "$executable")"
          WINEDEBUG=-all "$WINE" "$executable" "$1" >/dev/null 2>&1
          echo "Login callback delivered to Autodesk Identity Manager."
          ;;
        run)
          require_display
          lock_prefix --shared
          executable="$(resolve_executable Fusion360.exe)"
          mkdir -p "$state_dir"
          umask 077
          log="$state_dir/run-$(date +%Y%m%dT%H%M%S)-$$.log"
          echo "Launching Fusion. Log: $log"
          # A working directory beside Fusion is needed by some bundled components.
          # Convert local file arguments before changing directory.
          files=()
          for argument in "$@"; do
            file="$(realpath -e "$argument")"
            files+=("$($WINE winepath -w "$file" | tr -d '\r')")
          done
          cd "$(dirname "$executable")"
          rc=0
          "$WINE" "$executable" "''${files[@]}" >"$log" 2>&1 || rc=$?
          # Keep the shared lock while GUI child processes are still alive. Never
          # kill wineserver on exit: that could abort a sign-in or unsaved document.
          "$WINESERVER" -w
          exit "$rc"
          ;;
      esac
    '';
}
