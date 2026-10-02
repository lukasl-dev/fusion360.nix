{
  lib,
  callPackage,
  symlinkJoin,
  makeDesktopItem,
  winePackage ? callPackage ./wine.nix { },
}:

let
  wine = winePackage;
  common = callPackage ./common.nix { inherit wine; };
  installer = callPackage ./install.nix { inherit common wine; };
  launcher = callPackage ./fusion360.nix { inherit common installer wine; };
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
    desktop
    login
  ];
  passthru = { inherit wine installer launcher; };
  meta = {
    description = "Autodesk Fusion installer and launcher with a pinned Wine environment";
    homepage = "https://github.com/lukasl-dev/fusion360.nix";
    license = lib.licenses.mit;
    platforms = [ "x86_64-linux" ];
    mainProgram = "fusion360";
  };
}
