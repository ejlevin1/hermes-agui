#!/usr/bin/env bash
# One-time per clone: use the repo's hooks (.githooks/) for every commit and push, worktrees included.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
git config core.hooksPath .githooks
chmod +x .githooks/* scripts/check-public.sh
command -v gitleaks >/dev/null || echo "WARNING: gitleaks missing: brew install gitleaks" >&2
p="${PUBLIC_CHECK_PATTERNS:-$HOME/.config/public-check/patterns}"
[[ -f "$p" ]] || echo "NOTE: add your personal patterns (names, domains, hostnames) to $p (never committed)" >&2
echo "hooks installed (core.hooksPath=.githooks)"
