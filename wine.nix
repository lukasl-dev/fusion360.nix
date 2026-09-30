{ wineWow64Packages, writeText }:

# Reported Fusion black-canvas workaround for Wine >= 11.11. This preserves
# ordinary RSA's calculation and handles only the legacy public exponent 1.
# https://bugs.winehq.org/show_bug.cgi?id=60190
# Adapted from Lolig4's symcrypt-rsa-exponent1.patch at commit
# 7da442d6af5ac2619e5151475d2be54efe6170bc (MIT-licensed upstream project).
wineWow64Packages.stagingFull.overrideAttrs (old: {
  patches = (old.patches or [ ]) ++ [
    (writeText "fusion360-symcrypt-rsa-exponent1.patch" ''
      diff --git a/libs/symcrypt/lib/rsakey.c b/libs/symcrypt/lib/rsakey.c
      --- a/libs/symcrypt/lib/rsakey.c
      +++ b/libs/symcrypt/lib/rsakey.c
      @@ -423,6 +423,12 @@ SymCryptRsakeyCalculatePrivateFields(
                   goto cleanup;
               }

      +        /* The multiplicative inverse of public exponent 1 is 1. */
      +        if( pkRsakey->au64PubExp[i] == 1 )
      +        {
      +            SymCryptIntSetValueUint32( 1, pkRsakey->piPrivExps[i] );
      +            continue;
      +        }
               // Calculate D
               SymCryptIntSetValueUint64( pkRsakey->au64PubExp[i], piScr );

    '')
  ];
  passthru = (old.passthru or { }) // {
    fusionRsaWorkaround = true;
  };
})
