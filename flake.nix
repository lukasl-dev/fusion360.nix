{
  description = "Autodesk Fusion on NixOS, with a dedicated Wine environment";

  inputs = {
    flake-parts.url = "github:hercules-ci/flake-parts";
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs =
    inputs@{ flake-parts, ... }:
    flake-parts.lib.mkFlake { inherit inputs; } {
      systems = [ "x86_64-linux" ];

      perSystem =
        { pkgs, ... }:
        let
          fusion360 = pkgs.callPackage ./package.nix { };
          wine-fusion = pkgs.callPackage ./wine.nix { };
        in
        {
          packages = {
            inherit fusion360;
            fusion360-patched = pkgs.callPackage ./package.nix { winePackage = wine-fusion; };
            inherit wine-fusion;
            default = fusion360;
          };

          apps.default = {
            type = "app";
            program = "${fusion360}/bin/fusion360";
            meta.description = "Install and run Autodesk Fusion through Wine";
          };

          formatter = pkgs.nixfmt-tree;

          devShells.default = pkgs.mkShell {
            packages = [
              fusion360
              pkgs.nixfmt
              pkgs.shellcheck
            ];
          };

          checks = {
            package = fusion360;
            cli = pkgs.callPackage ./checks.nix { inherit fusion360; };
          };
        };

      flake.overlays.default = final: _prev: {
        fusion360 = final.callPackage ./package.nix { };
      };
    };
}
