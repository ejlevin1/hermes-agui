#!/usr/bin/env bash
# Option 3: `uv pip install` hermes_agui into the managed environment; does it survive a plugin-set change and `pm repair`?
set -u; export W=${W:-$HOME/hermes-agui-work/proof}; source "$(dirname "$0")/lib.sh"
fresh_home 3; cp -R "$P/host_probe/agui_host_probe" "$HERMES_HOME/plugins/"
hx plugins enable agui_host_probe >/dev/null 2>&1
E1=$(managed_venv); echo "env before: $E1"
echo "\$ uv pip install -p <managed>/bin/python $REPO"; uv pip install -q -p "$E1/bin/python" "$REPO" 2>&1 | tail -3
echo -n "importable in env: "; "$E1/bin/python" -I -c "import hermes_agui;print(hermes_agui.__file__)"
# a second plugin that declares a dependency forces a new environment generation
mkdir -p "$W/variants/dep2/dep2plug"; printf 'name: dep2plug\nversion: 0.0.1\npython_dependencies:\n  - "six>=1.16,<2"\n' > "$W/variants/dep2/dep2plug/plugin.yaml"
echo "def register(ctx):\n    return None" > "$W/variants/dep2/dep2plug/__init__.py"
cp -R "$W/variants/dep2/dep2plug" "$HERMES_HOME/plugins/"; hx plugins enable dep2plug 2>&1 | tail -2
echo "envs now:"; ls -d "$HERMES_HOME"/installs/*/environments/*/venv
for E in "$HERMES_HOME"/installs/*/environments/*/venv; do echo -n "$(basename $(dirname $E)): "; "$E/bin/python" -I -c "import hermes_agui;print('hermes_agui present')" 2>&1 | tail -1; done
echo "\$ hermes pm repair"; hx pm repair 2>&1 | tail -4
for E in "$HERMES_HOME"/installs/*/environments/*/venv; do echo -n "after repair $(basename $(dirname $E)): "; "$E/bin/python" -I -c "import hermes_agui;print('hermes_agui present')" 2>&1 | tail -1; done
