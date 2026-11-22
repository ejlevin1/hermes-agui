# Packaging: how a host dashboard plugin imports `hermes_agui` (decision D3)

All results below were run on the live Hermes sha `84e3e72bbb` (copied source, py3.14 venv, throwaway `HERMES_HOME` per
variant, dashboard started with the pm-**managed** environment's `hermes`). Probe code, re-run commands and raw output:
`spikes/import_proof/` (`README.md`, `evidence/*.txt`). Stand-in host plugin `agui_host_probe` (manifest + `plugin_api.py`
that reports what it can import); stand-in gateway plugin `agui_frontend` (the real `plugin/` is another lane's).

## Recommendation

**Declare `hermes_agui` in the host plugin's own `pyproject.toml` as an index dependency with a `[tool.uv.sources]` git
source pinned to a commit SHA.** Hermes' package manager (pm) makes the host plugin a uv workspace member and installs
the package into the managed environment on `plugins enable` / `pm install`; it survives plugin-set changes and
`pm repair` because pm owns the environment. Fall back to **vendoring** (option 4) if the host must run where pm
resolution cannot reach the git remote. Do **not** use a bare `plugin.yaml` `python_dependencies` URL (silently dropped),
`uv pip install` into the Hermes env (lost on the next env generation), or an unpublished index pin (not admitted).

Host `plugin/pyproject.toml` (next to its `plugin.yaml`):
```toml
[project]
name = "<host>-plugin"            # any unique name
version = "0.2.1"
requires-python = ">=3.12"
dependencies = ["hermes-agui"]

[tool.uv.sources]
hermes-agui = { git = "ssh://git@github.com/ejlevin1/hermes-agui.git", rev = "<40-char sha>" }   # ssh URL: UNVERIFIED, see below
```
`hermes_agui` is imported by the host's `dashboard/plugin_api.py` in the dashboard process. The gateway half
(`plugin/` of this repo, toolset `agui_frontend`) is a separate Hermes plugin installed per profile (see below); it must
not import `hermes_agui` (its install dir contains only `plugin/`, option 2).

## Options tried (real output in `spikes/import_proof/evidence/`)

| # | Mechanism | Result |
|---|---|---|
| 0 | baseline, nothing declared | `ModuleNotFoundError: No module named 'hermes_agui'` (`base.txt`) |
| 1a | `plugin.yaml` `python_dependencies: ["hermes-agui @ file://<repo>"]` | enable succeeds, **import fails**: direct URLs are dropped. Source: `pm/plugin_declarations.py:98-109` (`applicable_requirements` skips any `Requirement.url`), `hermes_cli/plugin_validate.py:651-654` warns "direct URL requirement(s) are not managed by PM" (`1a.txt`) |
| 1b | `plugin.yaml` index spec | supported keys `pip_dependencies` / `python_dependencies` (`pm/plugin_declarations.py:166`), index requirements only; not run separately: same pm path as 1e |
| 1c | plugin `pyproject.toml` `dependencies=["hermes-agui"]` + `[tool.uv.sources] path = <repo>` | **works**: `hermes_agui` imported from `<managed env>/venv/lib/python3.14/site-packages/hermes_agui/` in the dashboard (`1c.txt`). Absolute path: dev only |
| 1d | `pyproject.toml` `dependencies=["hermes-agui @ file://<repo>"]` | enable succeeds, **import fails** (URL dropped, as 1a) (`1d.txt`) |
| 1e | `pyproject.toml` `dependencies=["hermes-agui==0.0.1"]`, nothing publishes it | enable **refused**: `DependencyConflict … hermes-agui was not found in the package registry` (`1e.txt`). Safe failure, plugin not admitted. Would work if the package were on an index |
| **1f** | `pyproject.toml` `dependencies=["hermes-agui"]` + `[tool.uv.sources] git = "file://<clone>", rev = "<sha>"` | **works**: imported from managed-env site-packages (`1f.txt`). This is the recommendation (with the real `ssh://`/`https://` remote) |
| 2 | `hermes plugins install "file://<repo>#plugin" --ref <sha> --enable` (gateway half) | installs to `$HERMES_HOME/plugins/agui_frontend`, provenance `git pinned@<sha>`, enabled (`2.txt`). Loaded by Hermes as `hermes_plugins.agui_frontend`. The `#plugin` subdir install contains only `plugin/`: `import hermes_agui` and `import agui_frontend` both fail from the host's dashboard code. So the gateway plugin must be self-contained |
| 3 | `uv pip install <repo>` into the managed env | imports fine until the plugin set changes: after enabling another plugin that declares a dependency, pm built a new env generation (newest) with **no** `hermes_agui`; `hermes pm repair` produced another env, also without it (`3.txt`). Operationally unacceptable: every enable/disable/update drops it |
| 4 | vendor: copy `hermes_agui/` into the host plugin (`<plugin>/vendor/hermes_agui`) at build time | **works, no extra mechanism** (`4.txt`), two ways: (a) `sys.path.insert(0, <plugin>/vendor)` then `import hermes_agui` (relative imports inside the package work); (b) `importlib.import_module("hermes_plugins.<host>.vendor.hermes_agui")` (the plugin package is in `sys.modules`; works only if the package uses **relative** imports internally). Costs: a build-step copy per host release, drift risk, and (a) mutates `sys.path` |

### Update path (`update.sh`, `update.txt`)
Bump `rev` in the host `pyproject.toml` (the stand-in commit adds real deps `ag-ui-protocol>=0.1,<1`, `httpx>=0.28.1,<1`),
then `hermes pm install` (creates a new env generation) and re-enable / restart: the current env then has
`hermes_agui 0.0.2` and `ag-ui-protocol 0.1.22`. Real dependencies resolved against Hermes core's pins without conflict.
The dashboard and gateway need a restart to pick up the new env (`hermes gateway restart` hint printed).

### Per-profile gating of the gateway tool (`profiles.sh`, `profiles.txt`)
Same stand-in plugin, three homes: **A** installed+enabled -> `call_frontend_tool` offered; **B** installed, not enabled
-> not offered; **C** absent -> not offered. (Plugins are opt-in per `HERMES_HOME`.) This confirms S7 Q1 for
*enabled vs not enabled*. Not re-proven here: S7's default-on plugin toolsets (a plugin enabled on a profile is offered on every
platform of that profile unless gated by `known_plugin_toolsets` / `agent.disabled_toolsets` / `check_fn`), and the S9-A
multiplexed `/p/<profile>/` case.

## Fresh instance (commands)
```sh
# gateway half, on every profile that serves agentic surfaces (HERMES_HOME of that profile, or `hermes -p <profile>`)
hermes plugins install "https://github.com/ejlevin1/hermes-agui.git#plugin" --ref <sha> --enable   # URL form: UNVERIFIED for https/ssh; file:// verified
# host plugin (brings hermes_agui through its pyproject, option 1f)
hermes plugins install <host plugin source> --enable            # host repo's own command; pm resolves + installs hermes-agui at <sha>
hermes gateway restart                                          # and restart the dashboard
```
## Updates
Bump `<sha>` in the host `pyproject.toml` (host release) and `hermes plugins install … --ref <newsha> --force` for the
gateway half (`--force` reinstalls over an existing plugin from the same source; flag exists, update flow UNVERIFIED);
then `hermes pm install`, restart. Keep the gateway-half `--ref` and the host's `rev` on the same commit.

## What the host plugin's doctor must check
1. `import hermes_agui` works **in the dashboard process** and `hermes_agui.__version__` equals the expected one (pin). Not from the CLI's python: use the dashboard's own interpreter.
2. The gateway plugin is enabled on every profile serving a surface (`plugins list`), its provenance revision equals the pinned sha, and `call_frontend_tool` is offered there (tools probe) and **not** on profiles without agentic surfaces.
3. Both halves are on the same commit (version + sha).
4. The pm environment is current (`hermes pm doctor`), no `DependencyConflict` on enable.
5. For the vendoring fallback: vendored `__version__` matches the pin; only one of the two import styles is used (otherwise two copies load).

## Gotchas found
- **An unset `HERMES_HOME` means the real `~/.hermes`.** One probe command ran that way and installed+enabled a stub plugin in the live home (and signalled the live gateway). It was undone with `hermes plugins remove agui_frontend` (plugin dir, `plugins.enabled` entry and install metadata verified gone; config diff vs. the post-mistake copy is exactly that one line). pm may have touched `installs/*/pm-runtime/selected.json` in the live home: not verifiable. `spikes/import_proof/lib.sh` now routes every call through a guard.
- Test imports with `python -I` from a neutral cwd: from the repo directory `import hermes_agui` succeeds from the cwd and fakes a pass.
- `plugins enable` of a plugin with a pyproject builds a new managed env generation (`installs/<id>/environments/<hash>/venv`); older generations stay on disk. "Current" = the newest.
- Admission failures (`DependencyConflict`) abort `enable` with a traceback, no half state seen.

## UNVERIFIED
- `ssh://`/`https://` git sources (only `file://` git remote tested); private-repo credentials in the Hermes install context (pm runs uv: needs a usable git credential/ssh agent there).
- Source copy vs editable: the package is installed as a copy in site-packages for 1c/1f (a host cannot edit it in place).
- The dev editable venv is not what a managed host runs: the live host's managed envs under `~/.hermes/installs/*/environments/*` were NOT touched; tests used throwaway homes whose source copy has no `.git` (pm logged "unknown-managed install"; pm behaviour on a real managed install of the same sha is assumed equal).
- `hermes plugins update` / `install --force` flow for the gateway half; auto-update (`plugins.auto_apply`) interaction with a pinned rev.
- Multiplexed `/p/<profile>/` gating (S9-A), default-on plugin toolsets, Tool Search hiding: S7 only.
- `n_tools` in `profiles.txt` was 33 in all three homes even though the tool is only offered in A: not investigated (A used the managed env, B/C the dev venv).
- Windows / Docker installs; plugin-eviction or release-age policies on other pm configurations (`pm/plugin_eviction.py` not read).
