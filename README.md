# fusion360.nix

Autodesk Fusion on x86-64 NixOS, using a dedicated, Nix-pinned Wine environment.
The package downloads Fusion directly from Autodesk; it does not run a Linux
distribution installer or require Bottles, Distrobox, `nix-ld`, or an FHS shell.

**Status:** installation, browser sign-in, sketch interaction, a shaded solid body,
and the populated Data Panel have been observed on NixOS 26.05 with
Hyprland/Xwayland, Radeon 860M, patched Wine Staging 11.16, and OpenGL Core
(October 2026). Save/reopen, STEP export, and updates are not yet verified;
some UI positioning/text artifacts remain. This is not a claim that every
Fusion feature or GPU works. The default launcher has also been cold-started
successfully with authentication and Data Panel loading confirmed in its logs.
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

Before launching Fusion, the wrapper starts the installer-selected Identity
Manager and waits up to 30 seconds for a fresh readiness report. This avoids a
startup race observed as `IDSDK_E_SERVER_PROCESS_NOT_READY` / "Unable to sign in".
An already-running ready Identity Manager is reused when its new
duplicate-instance report confirms it. A timeout reports an error instead of killing processes,
clearing credentials, or launching into a known-unready sign-in service.

If a local terminal or browser handler lacks `DISPLAY`, the wrapper reads the
local Xwayland display from your systemd user manager. An existing `DISPLAY` is
preserved; SSH sessions never automatically attach to the local desktop. If
discovery fails, use a desktop terminal with `DISPLAY` set.

### Graphics

Fresh installations default to **OpenGL Core for the modelling viewport,
OpenGL for Qt, and `gl` for Chromium**, with builtin D3D9 for legacy UI components.
Wine uses X11/Xwayland even inside a Wayland desktop; native Wine Wayland is not
the initial compatibility target. Existing prefixes keep their selected graphics
backend; to apply the tested combination to an older installation, close Fusion
and its Wine session first:

```console
nix run . -- graphics opengl gl
nix run .
```

If the launcher remains running after you close Fusion, background Windows
services may still own its prefix lock. Use `nix run . -- stop` to request a
graceful Windows-session shutdown, then retry changing graphics or updating.
Save and close documents first. This command never uses force/kill flags; if
Wine does not stop within 15 seconds, it reports an error rather than killing it.

Use `graphics dxvk` to test DX11 through DXVK, or `install --graphics dxvk` for
a fresh DXVK setup. DXVK remains installed but is not the default viewport path.
Unrelated preferences and TLS certificate verification are not changed.
No version-coupled Qt DLL replacements or unverified SpaceMouse DLLs
are installed.

The viewport, Qt UI, and embedded Chromium browser use separate rendering
settings. An optional second argument selects Chromium's backend without
changing Qt's OpenGL setting:

```console
nix run . -- graphics dxvk vulkan
```

This combination has AMD success reports upstream, but is not a universal fix.
Use `opengles` or `gl` instead of `vulkan` to test alternatives. Omitting the
second argument preserves your existing Chromium setting (initially `gl`).
The wrapper removes inherited Linux Qt platform/theme/plugin paths so Fusion
uses its bundled Windows Qt.

### Wine compatibility fixes

The current upstream tracker reports a black modelling canvas with Wine 11.11+
that briefly renders when resizing or opening panels. Both DXVK and OpenGL can
be affected. Wine issue [60190](https://bugs.winehq.org/show_bug.cgi?id=60190)
proposes an RSA/SymCrypt compatibility fix, reported to restore Fusion in the
Lolig4 fork. The default package applies this small source patch to the pinned
Wine. Locally, patched Wine restored sketch content with DXVK, while switching
to OpenGL Core also restored the background and shaded solid rendering. The
upstream diagnosis is not yet confirmed by Wine maintainers.

Building patched Wine from source can take substantial time and disk space;
without a binary cache, expect a full Wine build on the first `nix build` or
`nix run`. `fusion360-patched` remains an alias for the default package. For
controlled comparisons, `fusion360-unpatched` uses unmodified Wine:

```console
nix build .#fusion360-unpatched --out-link result-unpatched
```

Both packages use the same mutable prefix. Back up the stopped prefix before
switching runtimes, and never run patched and unpatched Wine simultaneously.

#### Blank Data Panel and browser sandbox

Wine 11.16 also has a Chromium sandbox compatibility issue reported in
[Lolig4 issue #10](https://codeberg.org/Lolig4/Autodesk-Fusion-360-on-Linux/issues/10).
The wrapper defaults to `QTWEBENGINE_DISABLE_SANDBOX=1`. This restored the Data
Panel locally without clearing caches, signing out, disabling TLS verification,
or retaining the unsuccessful `DataPanel.UseNewWebImplementation` registry tweak.

**Security trade-off:** Qt WebEngine's sandbox is disabled, reducing isolation
of web content. A dedicated Wine prefix is not an operating-system sandbox. Only
use this with a trusted Fusion installation and account content. To opt out and
retest sandbox support with a compatible runtime:

```console
FUSION360_WEBENGINE_SANDBOX=1 nix run .
```

Stop the existing Wine session before changing this environment setting; running
browser processes cannot inherit a new launch environment. `doctor` reports the
effective value. The workaround applies to bundled Qt WebEngine, not a blanket
disabling of security for the Linux browser or WebView2.

Qt tests whether `QTWEBENGINE_DISABLE_SANDBOX` is present, not its numeric value;
setting it to `0` still disables the sandbox. The wrapper's explicit opt-in above
removes that Qt variable instead.

#### Virtual desktop (experimental)

To isolate Wine's child-window presentation from your Wayland window manager,
close Fusion and try a virtual desktop:

```console
nix run . -- run --virtual-desktop 1280x800
```

This affects only that launch, not saved preferences. Do not mix virtual-desktop
and normal launches in a running prefix; stop the old Wine session first.

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
nix build .#checks.x86_64-linux.cli --no-link
nix fmt
nix develop
```

The Nix files stay flat, with dependency declarations beside each command.
Substantial Python lives in ordinary modules under `python/`:

- `flake.nix`: flake-parts outputs, lockfile inputs, development shell, checks.
- `package.nix`: package composition and desktop entries.
- `install.nix`: `writeShellApplication` for installation and updates.
- `fusion360.nix`: `writeShellApplication` for launch, sign-in, and diagnostics.
- `common.nix`: shared environment, prefix ownership, locks, and graphics policy.
- `wine.nix`: Wine RSA compatibility patch for the black-canvas regression.
- `checks.nix`: Python quality checks and non-interactive regression tests.
- `python/deployment.py`: installer-owned launch pointers and installation provenance.
- `python/graphics.py`: targeted, atomic rendering-preference updates.
- `python/desktop.py`: Desktop Entry argument quoting.
- `python/identity.py`: bounded Identity Manager startup and log lifecycle tracking.
- `python/test_*.py`: helper, startup, and packaged-command regression tests.
- `python/pyproject.toml`: strict mypy and Ruff configuration.
- `python/pylnk3.pyi`: the small typed boundary for the untyped shortcut library.

Shell scripts are syntax-checked and ShellChecked during the Nix build; desktop
entries are validated too. The CLI check runs strict mypy, Ruff lint/format checks,
and unittest against ordinary Python modules, without starting Wine or touching
the real Fusion prefix. `nix develop` provides the interpreter, mypy, and Ruff;
run `cd python && mypy .` to check types locally.

Automated checks do not claim graphical compatibility.
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
- [Current black-workspace report (Codeberg #694)](https://codeberg.org/cryinkfly/Autodesk-Fusion-360-on-Linux/issues/694)
- [Lolig4 compatibility fork](https://codeberg.org/Lolig4/Autodesk-Fusion-360-on-Linux)
- [Wine RSA/SymCrypt regression report (#60190)](https://bugs.winehq.org/show_bug.cgi?id=60190)
- [Wine 11.16 blank Data Panel / sandbox report (Lolig4 #10)](https://codeberg.org/Lolig4/Autodesk-Fusion-360-on-Linux/issues/10)

This wrapper is MIT-licensed. Fusion and Microsoft components retain their own
licenses and are downloaded from their vendors, not redistributed by this flake.
