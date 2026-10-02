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
    grep -q 'stop ' help.txt
    grep -q 'virtual-desktop' help.txt
    grep -q 'default: opengl' help.txt
    grep -q 'FUSION360_WEBENGINE_SANDBOX=1' help.txt
    test ! -e "$HOME/.local/share/fusion360"

    if fusion360 nonsense >error.txt 2>&1; then exit 1; fi
    grep -q 'Unknown command' error.txt

    if fusion360 run --virtual-desktop >error.txt 2>&1; then exit 1; fi
    grep -q 'Usage:' error.txt
    if fusion360 run --virtual-desktop invalid >error.txt 2>&1; then exit 1; fi
    grep -q 'Virtual desktop size must be' error.txt
    test ! -e "$HOME/.local/share/fusion360"

    if fusion360 stop unexpected >error.txt 2>&1; then exit 1; fi
    grep -q 'stop takes no arguments' error.txt
    test ! -e "$HOME/.local/share/fusion360"

    if fusion360 login 'https://example.com' >error.txt 2>&1; then exit 1; fi
    grep -q 'Expected an adskidmgr' error.txt
    test ! -e "$HOME/.local/share/fusion360"

    if fusion360 install --graphics invalid >error.txt 2>&1; then exit 1; fi
    grep -q 'Graphics must be' error.txt

    if fusion360 graphics dxvk invalid >error.txt 2>&1; then exit 1; fi
    grep -q 'Chromium graphics must be' error.txt
    test ! -e "$HOME/.local/share/fusion360"

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
    test '${if fusion360.wine.fusionRsaWorkaround or false then "patched" else "unpatched"}' = patched

    python3 - ${fusion360.launcher}/bin/fusion360 <<'PY'
    import pathlib
    import subprocess
    import sys
    import tempfile
    import xml.etree.ElementTree as ET
    import pylnk3

    script = pathlib.Path(sys.argv[1]).read_text()
    # Only Linux integration settings are removed; explicit graphics diagnostics
    # remain available. Test the exact packaged environment setup without Wine.
    import os
    environment = dict(os.environ)
    linux_qt = ('QT_QPA_PLATFORM', 'QT_QPA_PLATFORMTHEME', 'QT_STYLE_OVERRIDE',
                'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'QML2_IMPORT_PATH',
                'QML_IMPORT_PATH', 'QT_WAYLAND_DISABLE_WINDOWDECORATION')
    environment.update(dict.fromkeys(linux_qt, 'linux-only'))
    environment['QT_QUICK_BACKEND'] = 'software'
    setup = script.split('\nfail()', 1)[0]
    result = subprocess.run(['bash', '-eu', '-c', setup + '\nenv'], env=environment,
                            text=True, capture_output=True, check=True)
    cleaned = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    assert all(key not in cleaned for key in linux_qt)
    assert cleaned['QT_QUICK_BACKEND'] == 'software'
    assert cleaned['QTWEBENGINE_DISABLE_SANDBOX'] == '1'
    environment['QTWEBENGINE_DISABLE_SANDBOX'] = '1'
    environment['FUSION360_WEBENGINE_SANDBOX'] = '1'
    result = subprocess.run(['bash', '-eu', '-c', setup + '\nenv'], env=environment,
                            text=True, capture_output=True, check=True)
    opted_out = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    assert 'QTWEBENGINE_DISABLE_SANDBOX' not in opted_out
    environment['FUSION360_WEBENGINE_SANDBOX'] = 'invalid'
    result = subprocess.run(['bash', '-eu', '-c', setup], env=environment,
                            text=True, capture_output=True)
    assert result.returncode != 0 and 'must be 0 or 1' in result.stderr

    graphics = script.split('python3 - "$WINEPREFIX" "$backend" "$chromium" <<\'PY\'\n', 1)[1].split('\nPY', 1)[0]
    resolver = script.split(' - "$WINEPREFIX" "$1" <<\'PY\'\n', 1)[1].split('\nPY', 1)[0]
    display = 'require_display() {' + script.split('require_display() {', 1)[1].split('\n}', 1)[0] + '\n}'
    identity_startup = script.split('python3 - "$WINEPREFIX" "$WINE" "$identity" "$log" "$desktop_size" <<\'PY\'\n', 1)[1].split('\nPY', 1)[0]

    # A synthetic Identity executable tests the actual packaged readiness code,
    # without starting Wine, reading credentials, or using the real prefix.
    marker = '[AdskIdentityManager:123, 456] [AdskIdentityManager INFO] SSO Server is ready\n'
    for behavior in ('append', 'create', 'split', 'rotate', 'truncate', 'stale', 'failed'):
        with tempfile.TemporaryDirectory() as directory:
            prefix = pathlib.Path(directory)
            identity_log = prefix / 'drive_c/users/test/AppData/Local/Autodesk/Identity Services/Log/IdServices.log'
            identity_log.parent.mkdir(parents=True)
            if behavior != 'create':
                identity_log.write_text(marker if behavior == 'stale' else 'old log data\n' * 20)
            identity = prefix / 'drive_c/Program Files/Autodesk/Autodesk Identity Manager/AdskIdentityManager.exe'
            identity.parent.mkdir(parents=True)
            identity.write_text("""
    from pathlib import Path
    import os, time
    path = Path(PATH)
    time.sleep(.1)
    if BEHAVIOR == 'failed':
        raise SystemExit(7)
    if BEHAVIOR == 'stale':
        raise SystemExit(0)
    if BEHAVIOR == 'rotate':
        path.rename(path.with_suffix('.old'))
    if BEHAVIOR in ('create', 'rotate', 'truncate'):
        path.write_text("")
    with path.open('a') as file:
        if BEHAVIOR == 'split':
            file.write(MARKER[:35]); file.flush()
            time.sleep(.1)
            file.write(MARKER[35:])
        else:
            file.write(MARKER)
    """.replace('PATH', repr(str(identity_log))).replace('BEHAVIOR', repr(behavior)).replace('MARKER', repr(marker)))
            program = identity_startup.replace('time.monotonic() + 30', 'time.monotonic() + 1.5')
            result = subprocess.run([sys.executable, '-', directory, sys.executable, str(identity),
                                     str(prefix / 'launch.log'), ""], input=program,
                                    text=True, capture_output=True, timeout=5)
            assert (result.returncode == 0) == (behavior not in ('stale', 'failed')), (behavior, result.stderr)
            assert marker not in result.stdout + result.stderr  # No Autodesk log dumping.

    # Only a fresh duplicate report can reuse historical readiness, and only
    # while an Identity process exists in this prefix. Lifecycle changes win.
    startup_definitions = identity_startup.split('# Historical readiness', 1)[0]
    with tempfile.TemporaryDirectory() as directory:
        prefix = pathlib.Path(directory)
        (prefix / 'drive_c/users').mkdir(parents=True)
        saved_argv = sys.argv
        try:
            sys.argv = ['-', directory, sys.executable, str(prefix / 'identity.exe'), str(prefix / 'launch.log'), ""]
            namespace = {}
            exec(startup_definitions, namespace)
        finally:
            sys.argv = saved_argv
    observe = namespace['observe']
    namespace['identity_running'] = lambda: True
    path = pathlib.Path('synthetic.log')
    assert not observe(path, marker.encode(), False)
    duplicate = b'[AdskIdentityManager:789, 1] [AdskIdentityManager INFO] Quitting since another instance (pid 123) is already running'
    assert observe(path, duplicate, True)
    assert not observe(path, duplicate, False)
    namespace['identity_running'] = lambda: False
    assert not observe(path, duplicate, True)
    namespace['identity_running'] = lambda: True
    observe(path, b'[AdskIdentityManager:123, 1] [AdskIdentityManager INFO] Starting Autodesk IDSDK Server process', False)
    assert not observe(path, duplicate, True)
    observe(path, marker.encode(), False)
    observe(path, b'[AdskIdentityManager:123, 1] [AdskIdentityManager INFO] App state set to Quitting', False)
    assert not observe(path, duplicate, True)

    # Mock the session manager, without calling Wine or touching a desktop.
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
        for backend, driver, chromium, expected in (
            ('opengl', 'VirtualDeviceGLCore', "", 'gl'),
            ('dxvk', 'VirtualDeviceDx11', "", 'gl'),
            ('dxvk', 'VirtualDeviceDx11', 'vulkan', 'vulkan'),
            ('opengl', 'VirtualDeviceGLCore', "", 'vulkan'),
            ('opengl', 'VirtualDeviceGLCore', 'gl', 'gl'),
        ):
            subprocess.run([sys.executable, '-', directory, backend, chromium], input=graphics, text=True, check=True)
            tree = ET.parse(options)
            assert tree.find('Unrelated').get('Value') == 'keep'
            assert tree.find('BootstrapOptionsGroup/driverOptionId').get('Value') == driver
            assert tree.find('CompatibilityGroup/ChromiumGraphicsBackend').get('Value') == expected
            assert tree.find('CompatibilityGroup/graphicsApiOptionId').get('Value') == 'OpenGL'
            assert 'TrustAllServers' not in options.read_text(encoding='utf-16')

        # Updating the default does not silently replace an existing browser choice.
        tree.find('CompatibilityGroup/ChromiumGraphicsBackend').set('Value', 'opengles')
        tree.write(options, encoding='utf-16', xml_declaration=True)
        subprocess.run([sys.executable, '-', directory, 'opengl', ""], input=graphics, text=True, check=True)
        assert ET.parse(options).find('CompatibilityGroup/ChromiumGraphicsBackend').get('Value') == 'opengles'

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
