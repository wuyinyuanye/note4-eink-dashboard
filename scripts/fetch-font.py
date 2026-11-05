#!/usr/bin/env python3
"""Download the official text font after verifying its SHA-256 digest."""
from hashlib import sha256
from pathlib import Path
from urllib.request import Request, urlopen

URL = 'https://cloud.zectrix.com/zfull_GB.ttf'
DIGEST = '2b74d788dd5bfc63d95b55d98b71842c5c0a925d4822bc90f25a18575125b3b3'
ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'fonts' / 'zfull_GB.ttf'


def main():
    if TARGET.is_file() and sha256(TARGET.read_bytes()).hexdigest() == DIGEST:
        print(f'font already present: {TARGET}')
        return
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(Request(URL, headers={'User-Agent': 'NOTE4-EInk-Dashboard/1.0'}), timeout=45) as response:
        data = response.read(24 * 1024 * 1024 + 1)
    if len(data) > 24 * 1024 * 1024:
        raise SystemExit('font download is unexpectedly large')
    actual = sha256(data).hexdigest()
    if actual != DIGEST:
        raise SystemExit(f'font SHA-256 mismatch: {actual}')
    temporary = TARGET.with_suffix('.tmp')
    temporary.write_bytes(data)
    temporary.replace(TARGET)
    print(f'downloaded official font: {TARGET}')


if __name__ == '__main__':
    main()
