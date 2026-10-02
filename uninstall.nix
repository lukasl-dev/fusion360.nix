{
  writeShellApplication,
  common,
  desktop-file-utils,
}:

writeShellApplication {
  name = "fusion360-uninstall";
  runtimeInputs = common.runtimeInputs ++ [ desktop-file-utils ];
  text =
    common.text
    # bash
    + ''
      usage() {
        cat <<'EOF'
      Usage: fusion360 uninstall [--purge [--yes]]

      Removes manually registered desktop entries and their login association.
      By default, the Wine prefix, account, documents, backups, and logs remain.

      --purge  Also delete the dedicated prefix, backups, installers, and logs.
               Requires a stopped Wine session and explicit confirmation.
      --yes    Confirm --purge non-interactively.

      Declaratively installed packages and desktop entries must be removed from
      NixOS/Home Manager configuration separately. Nix store paths are not deleted.
      EOF
      }

      purge=0
      confirmed=0
      for argument in "$@"; do
        case "$argument" in
          --help|-h)
            usage
            exit 0
            ;;
          --purge) purge=1 ;;
          --yes) confirmed=1 ;;
          *) fail "Unknown uninstall option: $argument" ;;
        esac
      done
      [[ "$confirmed" == 0 || "$purge" == 1 ]] || fail "--yes requires --purge."

      umask 077
      ${common.python}/bin/python3 ${common.pythonSource}/uninstall.py \
        "$data_dir" "$cache_dir" "$state_dir" \
        "''${XDG_DATA_HOME:-$HOME/.local/share}/applications" \
        "''${XDG_CONFIG_HOME:-$HOME/.config}" \
        "''${XDG_STATE_HOME:-$HOME/.local/state}/fusion360/desktop-root" \
        "$WINESERVER" "$purge" "$confirmed"
    '';
}
