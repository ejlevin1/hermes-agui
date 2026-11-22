#!/usr/bin/env bash
# Update path of option 1: host plugin pins hermes-agui by git rev in its pyproject; bump the rev (and add real deps:
# ag-ui-protocol, httpx) and see whether `hermes pm install` / re-enable rolls the managed env forward. Needs variant 1f's repo-standin.
set -u; export W=${W:-$HOME/hermes-agui-work/proof}; source "$(dirname "$0")/lib.sh"
G=$W/repo-standin; SHA1=$(cat "$W/standin.sha")
fresh_home upd; cp -R "$W/variants/1f/agui_host_probe" "$HERMES_HOME/plugins/"
hx plugins enable agui_host_probe 2>&1 | tail -1
chk() { echo -n "$1: "; (cd /tmp && "$(managed_venv)/bin/python" -I -c "import hermes_agui,importlib.metadata as m;print(hermes_agui.__version__, '| ag-ui-protocol', m.version('ag-ui-protocol'))" 2>&1 | tail -1); }
ls -d "$HERMES_HOME"/installs/*/environments/*/venv | wc -l | xargs echo "envs:"
echo -n "v1 in env: "; (cd /tmp && "$(managed_venv)/bin/python" -I -c "import hermes_agui;print(hermes_agui.__version__)")
# new upstream commit: version 0.0.2 + real deps
(cd "$G" && sed -i.bak 's/^version = .*/version = "0.0.2"/; s/^dependencies = .*/dependencies = ["ag-ui-protocol>=0.1,<1", "httpx>=0.28.1,<1"]/' pyproject.toml && rm pyproject.toml.bak \
 && sed -i.bak 's/__version__ = .*/__version__ = "0.0.2"/' hermes_agui/__init__.py && rm hermes_agui/__init__.py.bak && git add -A && git -c user.email=t@t -c user.name=t commit -qm "0.0.2 + deps" && git rev-parse HEAD > "$W/standin2.sha")
SHA2=$(cat "$W/standin2.sha"); echo "bumping rev $SHA1 -> $SHA2"
sed -i.bak "s/$SHA1/$SHA2/" "$HERMES_HOME/plugins/agui_host_probe/pyproject.toml"; rm "$HERMES_HOME/plugins/agui_host_probe/pyproject.toml.bak"
echo "\$ hermes pm install"; hx pm install 2>&1 | tail -4
echo "envs:"; ls -dt "$HERMES_HOME"/installs/*/environments/*/venv | wc -l
echo "\$ hermes plugins disable+enable agui_host_probe"; hx plugins disable agui_host_probe 2>&1 | tail -1; hx plugins enable agui_host_probe 2>&1 | tail -3
chk "after re-enable (current env)"
