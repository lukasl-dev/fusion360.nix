{
  lib,
  callPackage,
  symlinkJoin,
  makeDesktopItem,
  winePackage ? callPackage ./wine { },
}:

let
  wine = winePackage;
  common = callPackage ./common.nix { inherit wine; };
  installer = callPackage ./install.nix { inherit common wine; };
  uninstaller = callPackage ./uninstall.nix { inherit common; };
  launcher = callPackage ./fusion360.nix {
    inherit
      common
      installer
      uninstaller
      wine
      ;
  };
  desktop = makeDesktopItem {
    name = "fusion360";
    desktopName = "Autodesk Fusion";
    comment = "CAD, CAM, and CAE through Wine";
    exec = "${launcher}/bin/fusion360 run %F";
    icon = "applications-engineering";
    categories = [
      "Graphics"
      "Engineering"
    ];
    terminal = false;
  };
  login = makeDesktopItem {
    name = "fusion360-login";
    desktopName = "Autodesk Fusion sign-in";
    exec = "${launcher}/bin/fusion360 login %u";
    mimeTypes = [ "x-scheme-handler/adskidmgr" ];
    noDisplay = true;
    terminal = false;
  };
in
symlinkJoin {
  name = "fusion360-0.1.0";
  paths = [
    launcher
    installer
    uninstaller
    desktop
    login
  ];
  passthru = {
    inherit wine installer launcher;
    inherit uninstaller;
    inherit common;
  };
  meta = {
    description = "Autodesk Fusion installer and launcher with a pinned Wine environment";
    homepage = "https://github.com/lukasl-dev/fusion360.nix";
    license = lib.licenses.mit;
    platforms = [ "x86_64-linux" ];
    mainProgram = "fusion360";
  };
}
