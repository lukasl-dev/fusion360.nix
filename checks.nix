{
  runCommand,
  fusion360,
  gnugrep,
  coreutils,
  python3,
  lib,
}:

runCommand "fusion360-cli-checks"
  {
    nativeBuildInputs = [
      fusion360
      gnugrep
      coreutils
      (python3.withPackages (ps: [ ps.pylnk3 ]))
    ];
  }
  ''
    export HOME="$TMPDIR/home"
    mkdir -p "$HOME"
    unset DISPLAY WAYLAND_DISPLAY
    # Do not let a developer's live desktop hide the no-display error cases.
    export SSH_CONNECTION=test

    fusion360 --help > help.txt
    grep -q 'login URL' help.txt
    grep -q 'doctor' help.txt
    test ! -e "$HOME/.local/share/fusion360"

    if fusion360 nonsense >error.txt 2>&1; then exit 1; fi
    grep -q 'Unknown command' error.txt

    if fusion360 login 'https://example.com' >error.txt 2>&1; then exit 1; fi
    grep -q 'Expected an adskidmgr' error.txt
    test ! -e "$HOME/.local/share/fusion360"

    if fusion360 install --graphics invalid >error.txt 2>&1; then exit 1; fi
    grep -q 'Graphics must be' error.txt

    FUSION360_DATA_HOME=relative fusion360 --help >error.txt 2>&1 && exit 1
    grep -q 'paths must be absolute' error.txt

    if fusion360 run >error.txt 2>&1; then exit 1; fi
    grep -q 'No X11 display' error.txt
    test ! -e "$HOME/.local/share/fusion360"

    # The packaged callback command must handle a hostile-looking URL as one
    # argument, and must not evaluate it before detecting the missing display.
    if fusion360 login 'adskidmgr:/login?code=$(touch /tmp/fusion360-injected)' >error.txt 2>&1; then exit 1; fi
    grep -q 'No X11 display' error.txt
    test ! -e /tmp/fusion360-injected

    # callPackage must not accidentally inject pkgs.wine (stable Wine 11.0)
    # instead of the staging package selected by package.nix.
    test '${
      if lib.versionAtLeast fusion360.wine.version "11.1" then "supported" else "too-old"
    }' = supported

    python3 - ${fusion360.launcher}/bin/fusion360 <<'PY'
    import pathlib
    import subprocess
    import sys
    import tempfile
    import xml.etree.ElementTree as ET
    import pylnk3

    script = pathlib.Path(sys.argv[1]).read_text()
    graphics = script.split('python3 - "$WINEPREFIX" "$backend" <<\'PY\'\n', 1)[1].split('\nPY', 1)[0]
    resolver = script.split(' - "$WINEPREFIX" "$1" <<\'PY\'\n', 1)[1].split('\nPY', 1)[0]
    display = 'require_display() {' + script.split('require_display() {', 1)[1].split('\n}', 1)[0] + '\n}'

    # Mock the session manager, without calling Wine or touching a desktop.
    import os
    environment = dict(os.environ)
    for key in ('DISPLAY', 'SSH_CONNECTION', 'SSH_TTY'):
        environment.pop(key, None)
    harness = """
    fail() { echo "$*" >&2; exit 1; }
    systemctl() { printf '%s\\n' "$SESSION_ENV"; }
    timeout() { shift; "$@"; }
    """ + display + '\nrequire_display; printf "%s" "$DISPLAY"'
    def get_display(**values):
        return subprocess.run(['bash', '-eu', '-c', harness], env=environment | values,
                              text=True, capture_output=True)
    result = get_display(SESSION_ENV='OTHER=ignore\nDISPLAY=:7.0')
    assert result.returncode == 0 and result.stdout == ':7.0', result.stderr
    result = get_display(DISPLAY=':9', SESSION_ENV='DISPLAY=:7')
    assert result.returncode == 0 and result.stdout == ':9'
    for values in (
        {'SESSION_ENV': ""},
        {'SESSION_ENV': 'DISPLAY=remote.example:0'},
        {'SESSION_ENV': 'DISPLAY=$(exit 42)'},
        {'SESSION_ENV': 'DISPLAY=:7', 'SSH_CONNECTION': 'remote'},
        {'SESSION_ENV': 'DISPLAY=:7', 'SSH_TTY': '/dev/pts/1'},
    ):
        result = get_display(**values)
        assert result.returncode != 0 and 'No X11 display' in result.stderr

    with tempfile.TemporaryDirectory() as directory:
        prefix = pathlib.Path(directory)
        user = prefix / 'drive_c/users/test'
        options = user / 'AppData/Roaming/Autodesk/Neutron Platform/Options/NMachineSpecificOptions.xml'
        options.parent.mkdir(parents=True)
        options.write_text('<OptionGroups><Unrelated Value="keep"/></OptionGroups>')
        for backend, driver in [('dxvk', 'VirtualDeviceDx11'), ('opengl', 'VirtualDeviceGLCore')]:
            subprocess.run([sys.executable, '-', directory, backend], input=graphics, text=True, check=True)
            tree = ET.parse(options)
            assert tree.find('Unrelated').get('Value') == 'keep'
            assert tree.find('BootstrapOptionsGroup/driverOptionId').get('Value') == driver
            assert 'TrustAllServers' not in options.read_text(encoding='utf-16')

        root = prefix / 'drive_c/Program Files/Autodesk/webdeploy/production'
        old, active = root / 'old/Fusion360.exe', root / 'active/Fusion360.exe'
        for path in (old, active):
            path.parent.mkdir(parents=True)
            path.touch()
        def resolve(name):
            return subprocess.run([sys.executable, '-', directory, name], input=resolver, text=True, capture_output=True)
        assert resolve('Fusion360.exe').returncode != 0

        shortcuts = user / 'AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Autodesk'
        shortcuts.mkdir(parents=True)
        pylnk3.for_file(r'C:\PROGRAM FILES\AUTODESK\webdeploy\production\ACTIVE\fusion360.EXE',
                       str(shortcuts / 'Autodesk Fusion.lnk'))
        result = resolve('Fusion360.exe')
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == str(active)

        identity = root / 'identity/Autodesk Identity Manager/AdskIdentityManager.exe'
        other = root / 'other/Autodesk Identity Manager/AdskIdentityManager.exe'
        for path in (identity, other):
            path.parent.mkdir(parents=True)
            path.touch()
        (prefix / 'user.reg').write_text(
            '[Software\\\\Classes\\\\adskidmgr\\\\shell\\\\open\\\\command]\n'
            '@="\\"C:\\\\Program Files\\\\Autodesk\\\\webdeploy\\\\production\\\\identity\\\\Autodesk Identity Manager\\\\AdskIdentityManager.exe\\" \\"%1\\""\n')
        result = resolve('AdskIdentityManager.exe')
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == str(identity)
    PY

    touch "$out"
  ''
