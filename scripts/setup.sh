#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
npm --prefix frontend ci --cache /tmp/modbus-npm-cache
npm --prefix frontend run build
.venv/bin/python -m pip install --no-build-isolation -e .
