# Weather artwork v1

This directory contains the continuous-tone silver-grey weather artwork used
by the 400×300 weather renderer. `manifest.json` is the authoritative map from
WMO weather codes to day/night assets and fixes each file's SHA-256.

Only the assets referenced by the current manifest are required at runtime.
The repository intentionally excludes superseded drafts, local generation
metadata, and test contact sheets.

The renderer submits a continuous-tone image. The ZECTRIX SDK maps it to the
device palette; the application does not pre-dither the source image.
