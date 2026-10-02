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
        in
        {
          packages = {
            inherit fusion360;
            # Keep the earlier experimental name usable without a second runtime.
            fusion360-patched = fusion360;
            fusion360-unpatched = pkgs.callPackage ./package.nix {
              winePackage = pkgs.wineWow64Packages.stagingFull;
            };
            wine-fusion = fusion360.wine;
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
              fusion360.common.python
              pkgs.mypy
              pkgs.ruff
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
