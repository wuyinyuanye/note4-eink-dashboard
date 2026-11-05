#!/usr/bin/env python3
"""Fail when known private identifiers or local runtime data are staged."""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
BLOCKED_NAMES = {'config.json', 'library.json', 'device-status.json', 'display-history.jsonl'}
BLOCKED_PARTS = {'.claude-runs', '__pycache__', 'target', 'data', '.venv'}
PRIVATE_PATTERNS = {
    'home directory': re.compile(r'/Users/carl(?:/|\\)', re.I),
    'private IPv4': re.compile(r'\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b'),
    'known device identifier': re.compile('80456b36' + 'c4c8|b81f3fb7' + '5d00', re.I),
    'private key': re.compile(r'BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY'),
}


def main():
    errors = []
    for path in ROOT.rglob('*'):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if any(part in BLOCKED_PARTS for part in relative.parts) or relative.parts[:1] == ('vendor',):
            continue
        if path.name in BLOCKED_NAMES:
            errors.append(f'{relative}: runtime file must not be published')
            continue
        if path.stat().st_size > 5 * 1024 * 1024:
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except (UnicodeError, OSError):
            continue
        for label, pattern in PRIVATE_PATTERNS.items():
            for match in pattern.finditer(text):
                line = text.count('\n', 0, match.start()) + 1
                errors.append(f'{relative}:{line}: {label}: {match.group(0)}')
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        raise SystemExit(1)
    print('public-content scan passed')


if __name__ == '__main__':
    main()
