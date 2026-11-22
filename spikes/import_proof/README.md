# import_proof — can a host dashboard plugin import `hermes_agui`?

Evidence for `docs/packaging.md`. Throwaway probes; never imported by product code. Everything runs under
`~/hermes-agui-work/proof/` (`W`) with a throwaway `HERMES_HOME` per variant (`$W/homes/<name>`). **Never run a
`hermes` command with `HERMES_HOME` unset: it means the real `~/.hermes`** (it happened once; `lib.sh` now routes
every call through `hx`, which refuses outside `$W/homes/`).

## Setup (once)
```sh
W=~/hermes-agui-work/proof; mkdir -p $W && cd $W
rsync -a --exclude __pycache__ --exclude .git --exclude node_modules --exclude '/venv*' --exclude '/.venv' \
  <hermes-src>/ hermes-src/
uv venv -q -p 3.14 venv; echo "pillow==12.3.0" > ov.txt
uv pip install -q -p venv/bin/python --override ov.txt -e './hermes-src[mcp,web]'
# stand-in "repo with a plugin/ subdir" (the real plugin/ is built by another lane): clone + commit the stub
REPO=<this worktree>; git clone -q $REPO repo-standin
cp -R $REPO/spikes/import_proof/stub_plugin/agui_frontend repo-standin/plugin
(cd repo-standin && git checkout -q -b standin && git add -A && git -c user.email=t@t -c user.name=t commit -qm stub \
  && git rev-parse HEAD > ../standin.sha)
```

## Re-run
```sh
export REPO=<this worktree>                       # lib.sh default is the agui-010-proof worktree
spikes/import_proof/variants.sh                   # baseline, 1a, 1c, 1d, 1e, 1f  -> evidence/<name>.txt
spikes/import_proof/profiles.sh                   # per-profile gating A/B/C       -> evidence/profiles.txt
spikes/import_proof/option3.sh                    # uv pip install into managed env (run from /tmp) -> evidence/3.txt
spikes/import_proof/vendor.sh                     # vendoring                      -> evidence/4.txt
spikes/import_proof/update.sh                     # rev bump + real deps           -> evidence/update.txt
# option 2 (gateway half via `plugins install file://…#plugin`): commands in evidence/2.txt header
```
Layout: `host_probe/agui_host_probe/` = stand-in host plugin (`plugin.yaml`, `dashboard/manifest.json`,
`dashboard/plugin_api.py` which writes `$HERMES_HOME/agui_probe_report.json` at import: the dashboard API is behind
auth, so the probe reports to a file). `stub_plugin/agui_frontend/` = stand-in gateway plugin. `tools_probe.py` lists the
tools Hermes offers for a home. The dashboard is started with the MANAGED env's `hermes` (`installs/*/environments/*/venv`),
the way a managed install runs; check imports with `python -I` from a neutral cwd (from the repo cwd `import hermes_agui`
succeeds by accident: my first option-3 run was invalid for that reason).
