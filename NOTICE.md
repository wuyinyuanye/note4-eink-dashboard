# Third-party notices

## ZECTRIX local push SDK

`vendor/zectrix-local-push-sdk/` is derived from ZECTRIX's local push SDK
and is distributed under its included MIT license. The original license and
Opus license notice are retained in that directory.

## Zfull-GB font

`scripts/fetch-font.py` downloads the official Zfull-GB font from
`https://cloud.zectrix.com/zfull_GB.ttf` after verifying SHA-256
`2b74d788dd5bfc63d95b55d98b71842c5c0a925d4822bc90f25a18575125b3b3`.
The font is not committed to this repository. Confirm that your use complies
with the font provider's terms.

## Weather artwork

The weather artwork under `local_app/assets/weather-realistic-v1/` was created
for this project. The repository only retains the assets referenced by the
current manifest; superseded drafts and local generation metadata are excluded.

## Data services

Weather and geocoding are provided by Open-Meteo. Market quotes are requested
from public Sina endpoints. News is requested from public China News Service
pages. Gold references use public sources documented in `local_app/gold*.py`.
Each service remains subject to its own terms and availability.
