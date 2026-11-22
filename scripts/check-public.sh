#!/usr/bin/env bash
# check-public.sh — keep secrets and machine/system details out of this public repo.
#
#   scripts/check-public.sh --staged          added lines in the index (pre-commit)
#   scripts/check-public.sh --range A..B      added lines in commits A..B (pre-push)
#   scripts/check-public.sh --all             every tracked file in the working tree (CI)
#   scripts/check-public.sh --history         every commit on every ref (CI; before going public)
#
# Two layers:
#   1. gitleaks (secrets: API keys, tokens, private keys). Required; install: brew install gitleaks.
#   2. system-info patterns: generic ones below (home paths, private IPs, real email addresses)
#      plus PERSONAL patterns from a file that is NEVER committed (your names, domains, hostnames):
#        ${PUBLIC_CHECK_PATTERNS:-$HOME/.config/public-check/patterns}   one extended regex per line, # comments
# Allowed exceptions (committed, keep it short and specific): .public-check-allow, one extended regex per line,
# matched against the offending line.
# Bypass for one commit (CI still runs): git commit --no-verify
set -euo pipefail

mode="${1:---staged}"; arg="${2:-}"
root="$(git rev-parse --show-toplevel)"; cd "$root"
red() { printf '\033[31m%s\033[0m\n' "$*" >&2; }

GENERIC=(
  '/Users/[A-Za-z0-9._-]+/'                                  # macOS home paths
  '/home/[a-z_][a-z0-9_-]*/'                                 # Linux home paths
  '[A-Za-z]:\\\\Users\\\\'                                   # Windows home paths
  '(^|[^0-9.])10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}([^0-9]|$)'      # private IPv4
  '(^|[^0-9.])192\.168\.[0-9]{1,3}\.[0-9]{1,3}([^0-9]|$)'
  '(^|[^0-9.])172\.(1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3}([^0-9]|$)'
  '(^|[^0-9.])100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3}([^0-9]|$)'  # CGNAT/Tailscale
  '[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}'  # email addresses (see allow list)
  '/\.hermes/installs/[0-9a-f]{8,}'                          # Hermes managed install hashes
  '\.cloudflareaccess\.com'                                  # Cloudflare Access team domains
)
EMAIL_OK='@(example\.(com|org|net)|users\.noreply\.github\.com|localhost)\b|noreply@'

personal="${PUBLIC_CHECK_PATTERNS:-$HOME/.config/public-check/patterns}"
patterns=("${GENERIC[@]}")
if [[ -f "$personal" ]]; then
  while IFS= read -r l; do [[ -z "${l// }" || "$l" == \#* ]] || patterns+=("$l"); done < "$personal"
elif [[ -z "${CI:-}" ]]; then
  red "public-check: no personal pattern file at $personal (generic checks only)."
fi
allow=()
[[ -f .public-check-allow ]] && while IFS= read -r l; do [[ -z "${l// }" || "$l" == \#* ]] || allow+=("$l"); done < .public-check-allow

# ---- 1. secrets (gitleaks) ----
if ! command -v gitleaks >/dev/null 2>&1; then red "public-check: gitleaks not installed (brew install gitleaks)"; exit 1; fi
case "$mode" in
  --staged)  gitleaks git --pre-commit --staged --no-banner --redact -l warn . ;;
  --range)   gitleaks git --log-opts="$arg" --no-banner --redact -l warn . ;;
  --all)     gitleaks dir --no-banner --redact -l warn . ;;
  --history) gitleaks git --log-opts="--all" --no-banner --redact -l warn . ;;
  *) red "usage: $0 --staged | --range A..B | --all | --history"; exit 2 ;;
esac || { red "public-check: gitleaks found a secret (above). Remove it; rotate it if it was ever pushed."; exit 1; }

# ---- 2. system info ----
# Produce "file:line:text" for the lines to inspect.
lines() {
  case "$mode" in
    --staged|--range)
      if [[ "$mode" == --staged ]]; then git diff --cached -U0 --no-color --diff-filter=ACMR; else git log -p -U0 --no-color --format= $arg; fi |   # $arg word-split on purpose (rev list)
      awk '/^\+\+\+ b\//{f=substr($0,7); next} /^@@/{split($3,a,","); n=substr(a[1],2)+0; next}
           /^\+/ && f!="" {print f ":" n ":" substr($0,2); n++}' ;;
    --all)     git grep -n -I -e '' -- . ;;
    --history) for c in $(git rev-list --all $(history_excludes)); do git grep -n -I -e '' "$c" -- . | sed "s/^$c://"; done | sort -u ;;
  esac
}
# --history skips commits reachable from the shas in .public-check-baseline (history reviewed and accepted as-is
# before the repo went public; Eric 21 Nov 2026). New commits are always checked.
history_excludes() { [[ -f .public-check-baseline ]] && grep -oE '^[0-9a-f]{7,40}' .public-check-baseline | sed 's/^/^/' || true; }
skip_file() { [[ "$1" == scripts/check-public.sh || "$1" == .public-check-allow || "$1" == .public-check-baseline ]]; }

hits=0
while IFS= read -r rec; do
  f="${rec%%:*}"; rest="${rec#*:}"; text="${rest#*:}"
  skip_file "$f" && continue
  for p in "${patterns[@]}"; do
    m="$(grep -oE -- "$p" <<<"$text" | head -1 || true)"
    [[ -z "$m" ]] && continue
    if [[ "$m" == *@* ]] && grep -qE -- "$EMAIL_OK" <<<"$m"; then continue; fi
    ok=0; for a in "${allow[@]+"${allow[@]}"}"; do grep -qE -- "$a" <<<"$text" && { ok=1; break; }; done
    (( ok )) && continue
    red "system info: ${f}:${rest%%:*}: matched /${p}/"; hits=$((hits+1)); break
  done
done < <(lines)

if (( hits )); then
  red "public-check: $hits line(s) carry machine/system details. Replace with placeholders (~, <home>, example.com),"
  red "or add a narrow regex to .public-check-allow if the match is a false positive."
  exit 1
fi
echo "public-check: ok ($mode)"
