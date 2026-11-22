# Contributing

This repository is meant to be public. Nothing machine-specific or secret may be committed.

## One-time setup per clone
```
brew install gitleaks          # or your platform's package
scripts/install-hooks.sh       # sets core.hooksPath=.githooks
```
Optionally list your own identifying strings (real name, domains, hostnames, internal service names), one extended
regex per line, in `~/.config/public-check/patterns`. That file stays on your machine; the hooks read it.

## What the hooks block
- **pre-commit** (`scripts/check-public.sh --staged`) and **pre-push** (`--range`): secrets found by gitleaks, and
  system details in added lines: absolute home paths (`/Users/<name>/`, `/home/<name>/`), private/Tailscale IPs, real
  email addresses (example.com and GitHub noreply are fine), Hermes install hashes, Cloudflare Access team domains, plus
  your personal patterns.
- **CI** runs `--all` (working tree) and `--history` (every commit) on every push, so a `--no-verify` commit is still
  caught before it matters.

Write paths as `~/...`, `<home>/...` or relative; use `example.com` hosts and synthetic data in tests and evidence.
A false positive gets a narrow regex in `.public-check-allow`.
