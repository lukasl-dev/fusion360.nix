# fusion360.nix

Autodesk Fusion on x86-64 NixOS, using a dedicated, Nix-pinned Wine environment.
The package downloads Fusion directly from Autodesk; it does not run a Linux
distribution installer or require Bottles, Distrobox, `nix-ld`, or an FHS shell.

**Status:** installation, browser sign-in, and launch to the modelling workspace have been tested on
NixOS 26.05 with Hyprland/Xwayland, Radeon 860M, Wine Staging 11.16, and DXVK 2.7.1
(September 2026). Modelling, save/export, and updates are not yet verified.
Autodesk does not support Linux.
You need an Autodesk account and a valid Fusion entitlement, including an
eligible personal-use or educational license.

## Usage

```console
nix run . -- --help
nix run . -- doctor
nix run . -- install
nix run . -- desktop
nix run .
```

`install` creates a dedicated prefix and installs the fonts and Windows runtimes,
WebView2, and Autodesk's self-contained installer. It requires an internet
connection and a desktop session; expect several gigabytes of downloads and
considerably more space for installation and backups. Installation does not
perform Autodesk sign-in. A failed setup can be rerun; completed dependencies
are recorded only after their installer succeeds.

The offline WebView2 installer leaves an updater helper resident. Setup stops
only that helper and requests a graceful Windows-session shutdown after the
Autodesk installer finishes; it never force-kills the whole Wine prefix.

The default command launches Fusion. It never downloads or installs implicitly.
Use `desktop` to register an application-menu entry and the browser login callback
for a one-off `nix run` installation. This also creates a user GC root so its
desktop launchers remain valid. Alternatively, install the package declaratively
as described below.

Desktop entries created by `desktop` preserve custom Fusion state paths. A
declaratively installed desktop entry uses the login session's environment.

### Sign-in

Click **Sign in** in Fusion and authenticate in your Linux browser. Its
`adskidmgr:` callback is handled by `fusion360 login` in the same Wine prefix.
If the browser fails to open the registered handler, obtain the callback URL
from the browser's developer tools and submit it manually:

```console
nix run . -- login 'adskidmgr:/login?code=…'
```

The callback contains a short-lived credential: do not post it in logs or issues.
The login command does not print it. Successful delivery to Identity Manager
does not itself prove that Autodesk accepted the login.

If a local terminal or browser handler lacks `DISPLAY`, the wrapper reads the
local Xwayland display from your systemd user manager. An existing `DISPLAY` is
preserved; SSH sessions never automatically attach to the local desktop. If
discovery fails, use a desktop terminal with `DISPLAY` set.

### Graphics

The default is DX11 through DXVK for the modelling viewport, builtin D3D9 for
legacy UI components, and OpenGL for Qt rendering. Wine uses X11/Xwayland even
inside a Wayland desktop; native Wine Wayland is not the initial compatibility
target. To try the OpenGL viewport fallback, close Fusion first:

```console
nix run . -- graphics opengl
nix run .
```

Use `graphics dxvk` to switch back, or `install --graphics opengl` for a fresh
OpenGL setup. Unrelated preferences and TLS certificate verification are not
changed. No version-coupled Qt DLL replacements or unverified SpaceMouse DLLs
are installed.

### Updates

Close Fusion and its Wine processes, then:

```console
nix run . -- update
```

Updates download the current official admin installer and use Autodesk's update
operation. Before changing the installation, the stopped prefix and installation
metadata are copied into the data directory's `backups/` folder. Copies use
reflinks when available. `--no-backup` skips this; backups are never deleted
automatically. Failed updates are not automatically rolled back.

You may supply a previously downloaded official installer for installation or
updates:

```console
nix run . -- install --installer '/path/to/Fusion Admin Install.exe'
```

`nix flake update` updates the packaged Linux environment, **not** Fusion.
Conversely, `fusion360 update` updates the mutable Autodesk installation, **not**
the lockfile. Nix rollback does not restore the Wine prefix. Wine upgrades may
also migrate the prefix, so keep a stopped-prefix backup before changing Wine.

## NixOS integration

Add this flake as an input, then install its package through NixOS or Home Manager:

```nix
environment.systemPackages = [
  inputs.fusion360.packages.x86_64-linux.default
];

hardware.graphics.enable = true;
```

The host provides its GPU drivers, a working X11/Xwayland display, and a browser
opener. Hyprland must have Xwayland enabled. Modern WoW64 Wine uses 64-bit Linux
libraries; `hardware.graphics.enable32Bit = true` is additionally needed if you
choose a legacy Wine build. NVIDIA systems must use their appropriate host
drivers; this package does not change drivers or Secure Boot settings.

For Home Manager:

```nix
home.packages = [ inputs.fusion360.packages.x86_64-linux.default ];
xdg.mimeApps = {
  enable = true;
  defaultApplications."x-scheme-handler/adskidmgr" = "fusion360-login.desktop";
};
```

The package also exports an overlay. Its `winePackage` argument can be overridden through
`pkgs.callPackage ./package.nix { winePackage = ...; }`, but all commands must use the
same Wine build, and changing it requires renewed compatibility testing.

## State and reproducibility

| Location | Contents |
| --- | --- |
| `$XDG_DATA_HOME/fusion360` | Wine prefix, setup markers, installation metadata, backups |
| `$XDG_CACHE_HOME/fusion360` | Official installers |
| `$XDG_STATE_HOME/fusion360` | Installation and runtime logs |

The standard XDG defaults are used when those variables are unset. Override a
single location with `FUSION360_DATA_HOME`, `FUSION360_CACHE_HOME`, or
`FUSION360_STATE_HOME`; these must be absolute, non-root paths.

The lockfile pins Wine, Winetricks, DXVK, scripts, and Linux dependencies. Fusion,
Microsoft runtime downloads, account state, and cloud-service availability are
external mutable inputs. Installation records the actual Autodesk installer's
SHA-256 and the Wine version; this is provenance, not a promise of bit-for-bit
reproducible Fusion installation.

A dedicated Wine prefix is **not a security sandbox**. Windows applications can
access files with your user's permissions. Certificates are verified normally;
upstream's `TrustAllServers` workaround is deliberately not used.

## Development

```console
nix build
nix flake check
nix fmt
nix develop
```

The flat layout keeps runtime dependency declarations beside each command:

- `flake.nix`: flake-parts outputs, lockfile inputs, development shell, checks.
- `package.nix`: package composition and desktop entries.
- `install.nix`: `writeShellApplication` for installation and updates.
- `fusion360.nix`: `writeShellApplication` for launch, sign-in, and diagnostics.
- `common.nix`: shared prefix ownership, locks, active deployment lookup, and rendering settings.
- `checks.nix`: non-interactive command-line regression tests.

Shell scripts are syntax-checked and ShellChecked during the Nix build; desktop
entries are validated too. Automated checks do not claim graphical compatibility.
The acceptance test is: sign in; sketch and extrude; save and reopen; export STEP;
restart; update without losing account state or preferences.

## References and attribution

The initial compatibility recipe is informed by
[cryinkfly's Autodesk Fusion on Linux](https://codeberg.org/cryinkfly/Autodesk-Fusion-360-on-Linux)
at commit `53b02038548f01a6e0a1fa2def0a9b545f1561c1` (June 2026).
The scripts here are a separate Nix-native implementation.

- [Official Autodesk admin installer](https://dl.appstreaming.autodesk.com/production/installers/Fusion%20Admin%20Install.exe)
- [Autodesk deployment documentation](https://www.autodesk.com/support/technical/article/caas/sfdcarticles/sfdcarticles/How-to-deploy-Fusion-360.html)
- [Existing Fusion Nix wrapper](https://github.com/NullString1/fusion-360-flake)

This wrapper is MIT-licensed. Fusion and Microsoft components retain their own
licenses and are downloaded from their vendors, not redistributed by this flake.
