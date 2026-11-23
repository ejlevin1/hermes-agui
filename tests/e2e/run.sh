#!/usr/bin/env bash
# e2e contract test: real CopilotKit 1.90.0 -> real hermes_agui bridge -> real Hermes gateway (live sha) + mock LLM.
# Idempotent. Caches under ~/.cache/hermes-agui-e2e/ (Hermes venv keyed by sha, node_modules keyed by lockfile hash).
# Usage: tests/e2e/run.sh [playwright args, e.g. -g "text streams"]
# Phase 1: single-profile gateway, all scenarios. Phase 2: MULTIPLEXED gateway (gateway.multiplex_profiles, default
# profile + agui-test, broker token only in agui-test's .env), scenario "multiplexed gateway".
# E2E_MUX=0 skips phase 2; E2E_MUX=only runs phase 2 alone.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
CACHE="${E2E_CACHE:-$HOME/.cache/hermes-agui-e2e}"; export E2E_CACHE="$CACHE"
APP="$HERE/app"; STATE="$(mktemp -t agui-e2e-state).json"; export E2E_STATE="$STATE"
cleanup() { python3 "$HERE/stack.py" stop "$STATE" >/dev/null 2>&1; rm -f "$STATE"; }
trap cleanup EXIT INT TERM

"$HERE/setup_hermes.sh" || exit 2

# node_modules cached by lockfile hash (a symlink into the cache keeps the tree clean)
LOCKH="$(shasum -a 256 "$APP/package-lock.json" | cut -c1-12)"
NM="$CACHE/node_modules-$LOCKH"
if [ ! -d "$NM/node_modules" ]; then
  mkdir -p "$NM" && cp "$APP/package.json" "$APP/package-lock.json" "$NM/" && (cd "$NM" && npm ci --no-audit --no-fund -q) || exit 2
fi
rm -rf "$APP/node_modules"; ln -s "$NM/node_modules" "$APP/node_modules"
(cd "$APP" && npx playwright install chromium >/dev/null 2>&1; npx vite build >/dev/null 2>&1) || { echo "app build failed"; exit 2; }

summary() {
python3 - "$1" <<'PY'
import json, sys
try: r = json.load(open(sys.argv[1]))
except Exception: print("no results.json"); sys.exit()
rows = []
def walk(s):
    for sp in s.get("specs", []):
        for t in sp["tests"]:
            res = t["results"][-1]; rows.append((sp["title"], t["status"], res["duration"]))
    for c in s.get("suites", []): walk(c)
for s in r["suites"]: walk(s)
print(f"\n{'scenario':<95} {'result':<10} {'ms':>7}")
for n, st, d in rows: print(f"{n[:94]:<95} {st:<10} {d:>7}")
sk = sum(1 for _,s,_ in rows if s=='skipped')
print(f"{sum(1 for _,s,_ in rows if s=='expected')}/{len(rows)-sk} passed" + (f" ({sk} skipped)" if sk else ""))
PY
}

RC=0
if [ "${E2E_MUX:-1}" != "only" ]; then
  python3 "$HERE/stack.py" start "$STATE" --static "$APP/dist" || { echo "stack failed to start; logs: ${E2E_WORK:-$HOME/hermes-agui-work/e2e}/logs"; exit 2; }
  echo "stack: $(python3 -c "import json;s=json.load(open('$STATE'));print('host',s['host_port'],'hermes',s['hermes_port'],s.get('plugin_install',''))")"
  (cd "$APP" && rm -f results.json && npx playwright test "$@"); RC=$?
  summary "$APP/results.json"
  python3 "$HERE/stack.py" stop "$STATE" >/dev/null 2>&1
fi
if [ "${E2E_MUX:-1}" != "0" ]; then
  echo; echo "== multiplexed gateway (gateway.multiplex_profiles: true; /p/agui-test) =="
  MUXW="${E2E_WORK:-$HOME/hermes-agui-work/e2e}-mux"   # own work dir: phase 1 logs survive
  E2E_WORK="$MUXW" python3 "$HERE/stack.py" start "$STATE" --static "$APP/dist" --multiplex || { echo "multiplexed stack failed to start; logs: $MUXW/logs"; exit 2; }
  (cd "$APP" && rm -f results.json && npx playwright test -g "multiplexed gateway"); MRC=$?
  summary "$APP/results.json"
  [ $MRC -ne 0 ] && RC=$MRC
fi
exit $RC
