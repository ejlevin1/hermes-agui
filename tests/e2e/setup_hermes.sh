#!/usr/bin/env bash
# Idempotent: copy the live-sha Hermes source into the cache and build a throwaway py3.14 venv (S7 README setup).
set -euo pipefail
SHA="${HERMES_SHA:-84e3e72bbb}"
SRC="${HERMES_SRC:-$HOME/Development/_ref/hermes-agent-$SHA}"
CACHE="${E2E_CACHE:-$HOME/.cache/hermes-agui-e2e}/hermes-$SHA"
mkdir -p "$CACHE"
if [ -x "$CACHE/venv/bin/python" ] && [ -f "$CACHE/.ready" ]; then echo "hermes venv cached: $CACHE"; exit 0; fi
[ -d "$SRC" ] || { echo "Hermes source not found: $SRC" >&2; exit 2; }
# NB: anchored excludes only: an unanchored 'venv*' drops hermes_cli/venv_sync.py (S7 README)
rsync -a --delete --exclude __pycache__ --exclude .git --exclude node_modules --exclude '/venv*' --exclude '/.venv' "$SRC/" "$CACHE/src/"
rm -rf "$CACHE/venv"
uv venv -q -p 3.14 "$CACHE/venv"
echo "pillow==12.3.0" > "$CACHE/ov.txt"   # live sha pins pillow 13 vs a conflicting transitive pin (S7)
uv pip install -q -p "$CACHE/venv/bin/python" --override "$CACHE/ov.txt" -e "$CACHE/src[mcp,web]" aiohttp
touch "$CACHE/.ready"; echo "hermes venv built: $CACHE"
