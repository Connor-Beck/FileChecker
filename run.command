#!/bin/bash
set -e

cd "$(dirname "$0")"

if [ -d ".venv" ]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

exec python3 -m filechecker
