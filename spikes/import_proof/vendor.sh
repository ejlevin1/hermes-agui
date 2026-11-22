#!/usr/bin/env bash
# Option 4: vendor hermes_agui into the host plugin at build time (copy), then start the dashboard and read the host report.
# usage: vendor.sh <hermes_agui source dir (default: repo)>. Adds a tiny relative-import probe module to the COPY only.
set -u; export W=${W:-$HOME/hermes-agui-work/proof}; source "$(dirname "$0")/lib.sh"
D=$W/variants/4/agui_host_probe; rm -rf "$W/variants/4"; mkdir -p "$D"; cp -R "$P/host_probe/agui_host_probe/." "$D/"
mkdir -p "$D/vendor"; rsync -a --exclude __pycache__ "${1:-$REPO}/hermes_agui" "$D/vendor/"
printf '"""probe: a relative import inside the vendored package"""\nfrom . import __version__\n\nOK = bool(__version__)\n' > "$D/vendor/hermes_agui/_rel_probe.py"
echo "vendored tree:"; (cd "$D" && find vendor -type f | sort)
variant 4 19440 "$D"
