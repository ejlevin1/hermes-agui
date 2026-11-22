# Shared helpers for the import proof. Source it; needs W (work dir) set by the caller.
W=${W:-$HOME/hermes-agui-work/proof}
V=$W/venv/bin
REPO=${REPO:-$HOME/Development/hermes-agui.wt/agui-010-proof}
P=$REPO/spikes/import_proof

fresh_home() {  # fresh_home <name> -> exports HERMES_HOME, empty dir
  export HERMES_HOME=$W/homes/$1; rm -rf "$HERMES_HOME"; mkdir -p "$HERMES_HOME/plugins"
}
managed_venv() {  # the pm-built environment of this HERMES_HOME (what a managed install really runs)
  ls -dt "$HERMES_HOME"/installs/*/environments/*/venv 2>/dev/null | head -1   # newest = current (older generations are kept)
}
start_dash() {  # start_dash <port>; PID in $DASH_PID, log $HERMES_HOME/dash.log. Runs the MANAGED env's hermes when it exists.
  local H="$V/hermes"; [ -n "$(managed_venv)" ] && H="$(managed_venv)/bin/hermes"; echo "dashboard binary: $H"
  (cd "$W" && exec "$H" dashboard --no-open --skip-build --port "$1" >"$HERMES_HOME/dash.log" 2>&1 & echo $! >"$HERMES_HOME/dash.pid")
  DASH_PID=$(cat "$HERMES_HOME/dash.pid")
  for _ in $(seq 1 60); do curl -fs "127.0.0.1:$1/api/status" >/dev/null 2>&1 && return 0; sleep 1; done
  echo "dashboard did not come up; tail of log:"; tail -20 "$HERMES_HOME/dash.log"; return 1
}
stop_dash() { [ -n "$DASH_PID" ] && kill "$DASH_PID" 2>/dev/null; sleep 1; }
probe() {  # probe: print the report the host plugin wrote at dashboard start (none = plugin not imported)
  if [ -f "$HERMES_HOME/agui_probe_report.json" ]; then "$V/python" -m json.tool "$HERMES_HOME/agui_probe_report.json"; else echo "NO REPORT (host plugin_api.py never imported)"; fi
}
variant() {  # variant <name> <port> <plugin-dir-to-copy>: fresh home, install host plugin dir, enable, start dashboard, print report
  fresh_home "$1"; cp -R "$3" "$HERMES_HOME/plugins/agui_host_probe"
  echo "--- enable"; "$V/hermes" plugins enable agui_host_probe 2>&1 | tail -${ENABLE_TAIL:-15}
  start_dash "$2" && sleep 2; echo "--- report ($1)"; probe; stop_dash
}
# Safety: every hermes call goes through hx, which refuses to run unless HERMES_HOME is inside the work dir
# (an unset HERMES_HOME silently means the real ~/.hermes: that bit us once).
hx() {
  case "$HERMES_HOME" in "$W"/homes/*) ;; *) echo "REFUSING: HERMES_HOME='$HERMES_HOME' is not under $W/homes" >&2; return 99;; esac
  "$V/hermes" "$@"
}
