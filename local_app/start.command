#!/bin/zsh
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --disable-pip-version-check -q -r local_app/requirements.txt
python3 scripts/fetch-font.py
CMAKE_POLICY_VERSION_MINIMUM=3.5 cargo build --locked --manifest-path local_app/bridge/Cargo.toml

mkdir -p "$ROOT/data"
(
  sleep 1.5
  open "http://127.0.0.1:18772/"
) &

exec .venv/bin/python local_app/app.py --data-dir "$ROOT/data"
