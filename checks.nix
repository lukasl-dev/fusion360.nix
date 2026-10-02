{
  runCommand,
  writeText,
  fusion360,
  mypy,
  ruff,
  lib,
}:

let
  inherit (fusion360.common) pythonSource python;
  commonShell = writeText "fusion360-common.sh" fusion360.common.text;
in
runCommand "fusion360-checks"
  {
    nativeBuildInputs = [
      fusion360
      python
      mypy
      ruff
    ];
  }
  ''
    export HOME="$TMPDIR/home"
    mkdir -p "$HOME"
    export PYTHONDONTWRITEBYTECODE=1
    export FUSION360_TEST_LAUNCHER=${fusion360.launcher}/bin/fusion360
    export FUSION360_TEST_COMMON=${commonShell}

    # callPackage must keep the patched, supported Wine selection; it must not
    # silently inject pkgs.wine (stable 11.0) through an argument named "wine".
    test '${
      if lib.versionAtLeast fusion360.wine.version "11.1" then "supported" else "too-old"
    }' = supported
    test '${if fusion360.wine.fusionRsaWorkaround or false then "patched" else "unpatched"}' = patched
    test '${
      if fusion360.wine.fusionCaptionlessPopupWorkaround or false then "patched" else "unpatched"
    }' = patched

    # Type-check the same sources used by the packaged commands, including tests.
    # The narrow local pylnk3 stub describes only the library boundary we use.
    cd ${pythonSource}
    for module in deployment desktop graphics identity uninstall; do
      test -f "$module.py"
    done
    mypy --cache-dir "$TMPDIR/mypy" .
    ruff check --no-cache .
    ruff format --check --no-cache .
    python3 -m unittest discover -v

    touch "$out"
  ''
