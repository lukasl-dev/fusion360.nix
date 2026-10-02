{ wineWow64Packages }:

# Compatibility changes apply only to this package's Wine, not the system Wine.
# Each patch records its source; live validation is documented in the README.
wineWow64Packages.stagingFull.overrideAttrs (old: {
  patches = (old.patches or [ ]) ++ [
    # Preserve ordinary RSA; handle Fusion's legacy public exponent 1.
    ./symcrypt-rsa-exponent1.patch

    # Keep captionless popups under Wine's control instead of exposing them as
    # ordinary managed windows to the Linux compositor. Needs live validation.
    ./captionless-popups.patch
  ];
  passthru = (old.passthru or { }) // {
    fusionRsaWorkaround = true;
    fusionCaptionlessPopupWorkaround = true;
  };
})
