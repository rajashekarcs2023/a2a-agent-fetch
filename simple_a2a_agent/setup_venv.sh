#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -U pip
pip install -e .
echo ""
echo "Virtualenv ready. Activate with:"
echo "  source $ROOT/.venv/bin/activate"
echo "Then (from this directory, with .env or env vars set):"
echo "  python -m simple_a2a_agent --port 10000"
echo "Requires Node.js for npx (Airbnb MCP)."
