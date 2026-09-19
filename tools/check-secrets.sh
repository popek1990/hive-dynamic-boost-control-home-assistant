#!/usr/bin/env bash
#
# Scan this repository for things that must never be published: tokens, private
# addresses, coordinates, occupancy data.
#
# It looks at three different places, because they can differ:
#   tree     - tracked and untracked files (.gitignore respected)
#   staged   - what is about to be committed  (git diff --cached)
#   commits  - what is about to be pushed    (git log -p BASE..HEAD)
#
# Usage:
#   tools/check-secrets.sh                 # all three
#   tools/check-secrets.sh --staged        # only the staged changes (pre-commit hook)
#   tools/check-secrets.sh --commits       # only the new commits    (pre-push hook)
#   tools/check-secrets.sh --base v1.0.0   # where the new commits start
#   tools/check-secrets.sh --strict        # treat warnings as failures too
#
# As a hook (hooks are not versioned, so install it once):
#   printf '#!/bin/sh\nexec tools/check-secrets.sh --commits\n' > .git/hooks/pre-push
#   chmod +x .git/hooks/pre-push
#
# In --staged and --commits mode a hit is reported as "file:N:+line", where N is the
# position within that file's diff, not the line number in the file. The file name and the
# matched text are what you need to find it.
#
# False positive you are sure about? Add a regular expression matching the whole line to
# tools/check-secrets.allow - one per line. Keep that list short and explain each entry.
#
# Patterns specific to your own machine (server paths, user names, an internal IP prefix,
# your chat id) belong in tools/check-secrets.local, one "name|regex" per line. That file
# is git-ignored, so your own details never become part of a public repository.

set -uo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT" || exit 2

SELF="tools/check-secrets.sh"          # holds the patterns, so it would match itself
ALLOW="tools/check-secrets.allow"
LOCAL_PATTERNS="tools/check-secrets.local"
BASE=""
MODE="all"
STRICT=0

# Private temporary file, removed on every exit path. A predictable name in /tmp could be
# pre-created by another local user to make this scanner report "clean" and exit 0.
HITS="$(mktemp "${TMPDIR:-/tmp}/check-secrets.XXXXXX")" || exit 2
trap 'rm -f "$HITS"' EXIT INT TERM

while [ $# -gt 0 ]; do
  case "$1" in
    --staged)  MODE="staged" ;;
    --commits) MODE="commits" ;;
    --tree)    MODE="tree" ;;
    --strict)  STRICT=1 ;;
    --base)    shift; BASE="${1:-}" ;;
    -h|--help) sed -n '2,28p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

# Anything here blocks a commit or a push.
BLOCKING=(
  "telegram bot token|[0-9]{8,10}:[A-Za-z0-9_-]{35}"
  "home assistant token (jwt)|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
  "github token|(github_pat_|ghp_|gho_|ghs_|ghu_)[A-Za-z0-9_]{20,}"
  "google apps script url|AKfycb[A-Za-z0-9_-]{20,}"
  "bearer with a value|Bearer[[:space:]]+(eyJ|[A-Za-z0-9._-]{30,})"
  "private key|-----BEGIN [A-Z ]*PRIVATE KEY-----"
  "cloud keys|(AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|xox[baprs]-[0-9A-Za-z-]{10,})"
  "private ip address|(^|[^0-9.])(10\.[0-9]{1,3}|192\.168\.[0-9]{1,3}|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]{1,3})\.[0-9]{1,3}"
  "remote home assistant hostname|[a-z0-9-]+\.(ui\.nabu\.casa|nabu\.casa|duckdns\.org|ddns\.net)"
  "home assistant user id|(^|[^0-9a-f])[0-9a-f]{32}([^0-9a-f]|$)"
  "coordinates|(^|[^0-9])-?[0-9]{1,2}\.[0-9]{3,}[[:space:]]*,[[:space:]]*-?[0-9]{1,3}\.[0-9]{3,}"
  "coordinates with a decimal comma|[0-9]{1,2},[0-9]+[[:space:]]*[/,][[:space:]]*(−|–|-)?[0-9]{1,2},[0-9]+"
)

# Your own machine's patterns, kept out of the repository.
if [ -s "$LOCAL_PATTERNS" ]; then
  while IFS= read -r line; do
    case "$line" in ""|\#*) continue ;; esac
    BLOCKING+=("$line")
  done < "$LOCAL_PATTERNS"
fi

# Worth a human look, but not automatically wrong.
WARNING=(
  "email address|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
  "tenancy wording|(lokator|najem|czynsz|wynajm|tenant)"
)

BLOCKS=0
WARNINGS=0

filter_allowed() {
  if [ -s "$ALLOW" ]; then grep -vEf "$ALLOW"; else cat; fi
}

report() {                # report BLOCK|warn <name> ; reads matches from "$HITS"
  local kind="$1" name="$2" lines
  lines="$(wc -l < "$HITS" | tr -d ' ')"
  [ "$lines" != "0" ] || return 0
  if [ "$kind" = "BLOCK" ]; then
    echo "  BLOCK  $name ($lines)"
    BLOCKS=$((BLOCKS + 1))
  else
    echo "  warn   $name ($lines)"
    WARNINGS=$((WARNINGS + 1))
    [ "$STRICT" = 1 ] && BLOCKS=$((BLOCKS + 1))
  fi
  head -20 "$HITS" | sed 's/^/    /'
}

scan_files() {            # scan the working tree, skipping this script and binaries
  local files entry name regex
  files="$(git ls-files -co --exclude-standard | grep -v "^$SELF$" || true)"
  if [ -z "$files" ]; then echo "  (no files to scan)"; return 0; fi
  for entry in "${BLOCKING[@]}" "|" "${WARNING[@]}"; do
    if [ "$entry" = "|" ]; then continue; fi
    name="${entry%%|*}"; regex="${entry#*|}"
    echo "$files" | tr '\n' '\0' | xargs -0 grep -nIE -- "$regex" 2>/dev/null \
      | filter_allowed > "$HITS" || true
    if printf '%s\n' "${BLOCKING[@]}" | grep -qxF -- "$entry"; then
      report BLOCK "$name"
    else
      report warn "$name"
    fi
  done
}

scan_diff() {             # scan_diff <label> <git-command...>, per file so hits are locatable
  local label="$1"; shift
  local names entry name regex file text any=0
  names="$("$@" --name-only --no-color 2>/dev/null | sort -u)" || true
  if [ -z "$names" ]; then echo "  ($label: nothing to scan)"; return 0; fi
  while IFS= read -r file; do
    [ -n "$file" ] || continue
    [ "$file" = "$SELF" ] && continue
    text="$("$@" --no-color -- "$file" 2>/dev/null)" || true
    [ -n "$text" ] || continue
    for entry in "${BLOCKING[@]}" "${WARNING[@]}"; do
      name="${entry%%|*}"; regex="${entry#*|}"
      printf '%s\n' "$text" | grep -E "^[+-]" | grep -nE -- "$regex" \
        | filter_allowed | sed "s|^|$file:|" > "$HITS" || true
      if printf '%s\n' "${BLOCKING[@]}" | grep -qxF -- "$entry"; then
        report BLOCK "$name"
      else
        report warn "$name"
      fi
      any=1
    done
  done <<< "$names"
  [ "$any" = 1 ] || echo "  ($label: nothing to scan)"
}

default_base() {
  if git rev-parse -q --verify refs/tags/v1.0.0 >/dev/null; then echo "v1.0.0"
  elif git rev-parse -q --verify origin/main >/dev/null; then echo "origin/main"
  else git rev-list --max-parents=0 HEAD | tail -n1; fi
}

echo "Secret scan in $ROOT"

if [ "$MODE" = "all" ] || [ "$MODE" = "tree" ]; then
  echo "- files (tracked and untracked)"
  scan_files
fi

if [ "$MODE" = "all" ] || [ "$MODE" = "staged" ]; then
  echo "- staged changes"
  scan_diff "staged changes" git diff --cached -U0
fi

if [ "$MODE" = "all" ] || [ "$MODE" = "commits" ]; then
  [ -n "$BASE" ] || BASE="$(default_base)"
  echo "- commits $BASE..HEAD"
  scan_diff "commits" git log -p -U0 "$BASE..HEAD"
fi

echo
if [ "$BLOCKS" != 0 ]; then
  echo "FAILED: $BLOCKS blocking pattern(s), $WARNINGS warning(s)."
  echo "Something above must not be published. Fix it before you commit or push."
  echo "If a hit is genuinely fine, add a line regex to $ALLOW and say why."
  exit 1
fi
if [ "$WARNINGS" != 0 ]; then
  echo "PASSED with $WARNINGS warning(s) - read them above and decide for yourself."
  exit 0
fi
echo "PASSED: nothing matched."
exit 0
