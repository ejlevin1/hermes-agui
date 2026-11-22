#!/usr/bin/env bash
# Per-profile gating proof: same stand-in gateway plugin, three homes: A installed+enabled, B installed but not enabled, C absent.
set -u; export W=${W:-$HOME/hermes-agui-work/proof}; source "$(dirname "$0")/lib.sh"
G=$W/repo-standin; SHA=$(cat "$W/standin.sha")
for h in A B C; do
  fresh_home "prof-$h"
  case $h in
    A) hx plugins install "file://$G#plugin" --ref "$SHA" --enable >/dev/null 2>&1 ;;
    B) hx plugins install "file://$G#plugin" --ref "$SHA" --no-enable >/dev/null 2>&1 ;;
    C) hx plugins list >/dev/null 2>&1 ;;   # still builds the managed env
  esac
  echo "profile $h: plugins dir = $(ls "$HERMES_HOME/plugins" | tr '\n' ' ')| $(hx plugins list --plain --no-bundled 2>&1 | grep agui_frontend || echo 'agui_frontend: not listed')"
  echo -n "profile $h tools: "; "$(m=$(managed_venv); echo "${m:-$W/venv}")/bin/python" "$P/tools_probe.py" 2>&1 | tail -1
done
