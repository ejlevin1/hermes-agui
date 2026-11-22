#!/usr/bin/env bash
# Options 1a-1f + baseline: how a host plugin can declare hermes_agui as a dependency. Writes evidence/<name>.txt.
# Needs: $W/venv (Hermes live-sha copy), $W/repo-standin (git clone of this repo + a stand-in plugin/ dir; see README "setup").
set -u; export W=${W:-$HOME/hermes-agui-work/proof}; source "$(dirname "$0")/lib.sh"
G=$W/repo-standin; SHA=$(git -C "$G" rev-list --max-parents=0 HEAD >/dev/null; cat "$W/standin.sha")
mk() { rm -rf "$W/variants/$1"; mkdir -p "$W/variants/$1"; cp -R "$P/host_probe/agui_host_probe" "$W/variants/$1/"; }
proj() { printf '[project]\nname = "agui-host-probe"\nversion = "0.0.1"\nrequires-python = ">=3.12"\ndependencies = [%s]\n%s\n' "$2" "$3" > "$W/variants/$1/agui_host_probe/pyproject.toml"; }
mk base
mk 1a; printf 'python_dependencies:\n  - "hermes-agui @ file://%s"\n' "$REPO" >> "$W/variants/1a/agui_host_probe/plugin.yaml"
mk 1c; proj 1c '"hermes-agui"' "$(printf '[tool.uv.sources]\nhermes-agui = { path = "%s" }' "$REPO")"
mk 1d; proj 1d "\"hermes-agui @ file://$REPO\"" ""
mk 1e; proj 1e '"hermes-agui==0.0.1"' ""
mk 1f; proj 1f '"hermes-agui"' "$(printf '[tool.uv.sources]\nhermes-agui = { git = "file://%s", rev = "%s" }' "$G" "$SHA")"
port=19460
for v in ${@:-base 1a 1c 1d 1e 1f}; do port=$((port+1)); ENABLE_TAIL=30 variant $v $port "$W/variants/$v/agui_host_probe" > "$P/evidence/$v.txt" 2>&1; echo "== $v: $(grep -c '"ok": true' "$P/evidence/$v.txt") import(s) ok; $(grep -o 'DependencyConflict' "$P/evidence/$v.txt" | head -1)"; done
