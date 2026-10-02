{
  lib,
  writeShellApplication,
  common,
  wine,
  winetricks,
  curl,
  dxvk,
}:

writeShellApplication {
  name = "fusion360-install";
  runtimeInputs = common.runtimeInputs ++ [
    winetricks
    curl
  ];
  text =
    common.text
    # bash
    + ''
      usage() {
        cat <<'EOF'
      Usage: fusion360 install [--graphics dxvk|opengl] [--installer /path/to/installer.exe]
             fusion360 update [--installer /path/to/installer.exe] [--no-backup]

      Installs directly from Autodesk into a dedicated Wine prefix.
      Fresh installs default to OpenGL Core, Qt OpenGL, and Chromium gl.
      Update backs up the stopped prefix first. Sign-in is interactive.
      EOF
      }

      operation="''${1:-install}"
      shift || true

      # Nixpkgs' multimedia Wine is a shell wrapper. Winetricks otherwise mistakes
      # it for an unknown architecture and tries a nonexistent wine64 command.
      export WINE64="$WINE"
      if [[ -x "${wine}/bin/.wine" ]]; then
        export WINE_BIN="${wine}/bin/.wine"
      fi

      backend="''${FUSION360_GRAPHICS:-}"
      installer=""
      backup=true

      while (( $# )); do
        case "$1" in
          --help|-h)
            usage
            exit 0
            ;;
          --graphics)
            (( $# >= 2 )) || fail "--graphics requires a backend."
            backend="$2"
            shift 2
            ;;
          --installer)
            (( $# >= 2 )) || fail "--installer requires a file."
            installer="$(realpath -e "$2")"
            shift 2
            ;;
          --no-backup)
            backup=false
            shift
            ;;
          *) fail "Unknown install option: $1" ;;
        esac
      done

      case "$operation" in
        install|update) ;;
        *) fail "Unknown operation: $operation" ;;
      esac

      # An update keeps the prefix's selection unless explicitly overridden.
      if [[ -z "$backend" && -f "$data_dir/graphics" ]]; then
        backend="$(cat "$data_dir/graphics")"
      fi
      backend="''${backend:-opengl}"
      case "$backend" in
        dxvk|opengl) ;;
        *) fail "Graphics must be dxvk or opengl." ;;
      esac

      require_display
      lock_prefix --exclusive
      if [[ -f "$data_dir/setup/prefix" && ! -f "$WINEPREFIX/system.reg" ]]; then
        fail "Setup markers exist but the Wine prefix is missing. Restore the prefix or use a fresh FUSION360_DATA_HOME."
      fi

      if [[ -f "$data_dir/installation.json" && "$operation" == install ]]; then
        if [[ ! -f "$data_dir/setup/dependencies-v1" ]]; then
          fail "Installation metadata exists without its setup state. Use doctor to inspect the prefix."
        fi
        compgen -G "$WINEPREFIX/drive_c/Program Files/Autodesk/webdeploy/production/*/Fusion360.exe" >/dev/null \
          || fail "Installation metadata exists but Fusion is missing. Use 'fusion360 update' to repair it."
        echo "Fusion is already installed. Use 'fusion360 update' to update it."
        exit 0
      fi

      if [[ "$operation" == update && ! -f "$data_dir/installation.json" ]]; then
        fail "Fusion is not installed. Use 'fusion360 install' first."
      fi

      mkdir -p "$cache_dir" "$state_dir" "$data_dir/setup"
      umask 077
      log="$state_dir/$operation-$(date +%Y%m%dT%H%M%S)-$$.log"
      echo "Installation log: $log"
      exec > >(tee -a "$log") 2>&1

      report_failure() {
        local rc=$?
        if (( rc != 0 )); then
          echo "Installation stopped (exit $rc). See $log; rerun to resume." >&2
        fi
      }
      trap report_failure EXIT

      if [[ -f "$WINEPREFIX/system.reg" ]]; then
        require_stopped_prefix
      fi

      if [[ "$operation" == update && "$backup" == true ]]; then
        snapshot="$data_dir/backups/$(date +%Y%m%dT%H%M%S)-$$"
        echo "Backing up the stopped prefix to $snapshot"
        mkdir -p "$snapshot"
        cp --archive --reflink=auto "$WINEPREFIX" "$snapshot/prefix"
        cp "$data_dir/installation.json" "$snapshot/installation.json"
        cp "$data_dir/graphics" "$snapshot/graphics"
      fi

      download() {
        local url="$1" destination="$2"
        curl --fail --location --retry 3 --connect-timeout 30 \
          --proto '=https' --proto-redir '=https' "$url" --output "$destination.part"
        mv "$destination.part" "$destination"
      }

      if [[ ! -f "$data_dir/setup/prefix" ]]; then
        ${wine}/bin/wineboot --init
        "$WINESERVER" -w
        touch "$data_dir/setup/prefix"
      fi

      # This initial recipe follows cryinkfly's June 2026 installer. Each completed
      # dependency is recorded only after success, so a failed download can resume.
      if [[ ! -f "$data_dir/setup/dependencies-v1" ]]; then
        winetricks -q win10
        for verb in atmlib gdiplus corefonts cjkfonts dotnet20 dotnet48 msxml4 msxml6 vcrun2022 fontsmooth=rgb winhttp; do
          if [[ ! -f "$data_dir/setup/$verb" ]]; then
            echo "Installing Wine dependency: $verb"
            winetricks -q "$verb"
            "$WINESERVER" -w
            touch "$data_dir/setup/$verb"
          fi
        done
        touch "$data_dir/setup/dependencies-v1"
      fi

      winetricks -q win11
      for dll in msvcp140 mfc140u; do
        "$WINE" reg add 'HKCU\Software\Wine\DllOverrides' /v "$dll" /d native /f
      done
      "$WINE" reg add 'HKCU\Software\Wine\DllOverrides' /v AdCefWebBrowser.exe /d builtin /f
      "$WINE" reg add 'HKCU\Software\Wine\X11 Driver' /v Managed /d Y /f
      "$WINE" reg add 'HKCU\Software\Wine\X11 Driver' /v Decorated /d Y /f

      if [[ ! -f "$data_dir/setup/webview2" ]]; then
        webview="$cache_dir/MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
        [[ -f "$webview" ]] || download 'https://go.microsoft.com/fwlink/?linkid=2124701' "$webview"
        "$WINE" "$webview" /silent /install
        # The offline installer leaves its updater resident even after successful
        # installation. Stop only that helper, never the whole prefix/wineserver.
        "$WINE" taskkill /IM MicrosoftEdgeUpdate.exe /F >/dev/null 2>&1 || true
        "$WINESERVER" -w
        touch "$data_dir/setup/webview2"
      fi

      # Copy only the D3D11/DXGI pair. Nixpkgs' setup script also replaces D3D9,
      # which Fusion's navigation UI needs to keep builtin.
      for arch in x64 x32; do
        target=system32
        [[ "$arch" != x32 ]] || target=syswow64
        for dll in d3d10core d3d11 dxgi; do
          install -m 644 "${lib.getBin dxvk}/$arch/$dll.dll" "$WINEPREFIX/drive_c/windows/$target/$dll.dll"
        done
      done
      configure_graphics "$backend"
      "$WINESERVER" -w

      if [[ -z "$installer" ]]; then
        installer="$cache_dir/Fusion Admin Install.exe"
        # This URL is mutable. Record the actual bytes, not a fictitious pinned Fusion version.
        download 'https://dl.appstreaming.autodesk.com/production/installers/Fusion%20Admin%20Install.exe' "$installer"
      fi
      [[ -f "$installer" ]] || fail "Installer not found: $installer"
      [[ "$(head -c 2 "$installer")" == MZ ]] || fail "The downloaded file is not a Windows executable: $installer"

      echo "Running Autodesk's installer ($operation)..."
      arguments=(--quiet)
      [[ "$operation" != update ]] || arguments+=(--process update)
      "$WINE" "$installer" "''${arguments[@]}"
      "$WINE" taskkill /IM MicrosoftEdgeUpdate.exe /F >/dev/null 2>&1 || true

      # Autodesk is now finished; request a graceful shutdown of its dedicated
      # Windows session so service-only processes cannot hold the installer open.
      # No force/kill flags: an unexpected application may veto shutdown.
      "$WINE" wineboot --end-session --shutdown
      "$WINESERVER" -w
      configure_graphics "$backend"
      "$WINESERVER" -w

      # Use the same installer-owned launch pointers as the CLI, not timestamps.
      echo "Autodesk installer completed. Checking installed files..."
      executable="$(resolve_executable Fusion360.exe)"
      identity="$(resolve_executable AdskIdentityManager.exe)"
      echo "Active Fusion: $executable"
      echo "Identity Manager: $identity"

      ${common.python}/bin/python3 ${common.pythonSource}/deployment.py record \
        "$WINEPREFIX" "$data_dir" "$installer" "${wine.version}"
      echo "Installation completed. Run 'fusion360' and sign in with your Autodesk account."
    '';
}
