#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ ! -x .venv/bin/python ]]; then
  printf '%s\n' 'Run bash tools/setup-linux.sh first.' >&2
  exit 1
fi
exec .venv/bin/python main.py "$@"
