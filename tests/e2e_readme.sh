#!/bin/bash
# E2E: the install / uninstall commands exactly as README.md prints them, run in a throwaway
# CLAUDE_CONFIG_DIR (the user's real plugins are never touched). Commands are read from the README, so the
# docs and this test cannot drift apart.
#   bash tests/e2e_readme.sh <empty-temp-dir> [source]
# source = what replaces `lernify18/claude-chat-label` in the README commands: this folder by default,
# or `lernify18/claude-chat-label` itself to test the published GitHub repo.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
WORK="$(cd "${1:?give an empty temp dir}" && pwd -P)"
SOURCE="${2:-$ROOT}"
README="$ROOT/README.md"
export CLAUDE_CONFIG_DIR="$WORK/config"
mkdir -p "$CLAUDE_CONFIG_DIR"

CLI=$(grep -E '^claude plugin ' "$README")
SLASH=$(grep -E '^/plugin ' "$README" | sort -u)
UNINSTALL=$(printf '%s\n' "$SLASH" | grep ' uninstall ' | sed 's#^/plugin #claude plugin #')
SLASH_AS_CLI=$(printf '%s\n' "$SLASH" | grep -v ' uninstall ' | sed 's#^/plugin #claude plugin #' | sort -u)

log=""
rc_all=0
while IFS= read -r cmd; do
  real="${cmd//lernify18\/claude-chat-label/$SOURCE}"
  out=$(eval "$real" 2>&1); rc=$?
  log="$log\n  \$ $cmd\n    rc $rc: $(printf '%s' "$out" | tail -1)"
  [ $rc -eq 0 ] || rc_all=$rc
done <<< "$CLI"
LIST=$(claude plugin list 2>&1)
DETAILS=$(claude plugin details chat-label@claude-chat-label 2>&1)
INSTALLED=$(find "$CLAUDE_CONFIG_DIR/plugins" -name chat-label-backfill.py -not -path "*/marketplaces/*" 2>/dev/null | head -1)
UN_OUT=$(eval "$UNINSTALL" 2>&1); UN_RC=$?
LIST_AFTER=$(claude plugin list 2>&1)

documented=$(grep -oE '/chat-label:[a-z-]+' "$README" | sort -u | tr '\n' ' ')
flags_ok=1
for f in --apply --undo --tunggu --ringkas; do
  grep -q -- "$f" "$README" "$ROOT/commands/"*.md || continue
  grep -q -- "\"$f\"" "$ROOT/scripts/"*.py || { echo "  flag $f documented but not handled"; flags_ok=0; }
done

fail=0
check() { if eval "$2"; then echo "  ✅ $1"; else echo "  ❌ $1"; fail=1; fi; }
echo "source: $SOURCE"
printf "%b\n" "$log"
echo "  \$ $UNINSTALL  -> rc $UN_RC: $(printf '%s' "$UN_OUT" | tail -1)"
check "README terminal commands match its /plugin commands" '[ "$(printf "%s\n" "$CLI" | sort -u)" = "$SLASH_AS_CLI" ]'
check "README install commands succeed in a clean config" '[ -n "$CLI" ] && [ $rc_all -eq 0 ]'
check "plugin listed and enabled after install" 'printf "%s" "$LIST" | grep -q "chat-label@claude-chat-label" && printf "%s" "$LIST" | grep -qi "enabled"'
check "installed plugin has both commands and both hooks" \
  'printf "%s" "$DETAILS" | grep -q "backfill, tab-sync" && printf "%s" "$DETAILS" | grep -q "SessionStart, UserPromptSubmit"'
check "installed scripts are executable (commands call them directly)" '[ -n "$INSTALLED" ] && [ -x "$INSTALLED" ] && [ -x "$(dirname "$INSTALLED")/chat-label-tab-sync.py" ]'
check "every /chat-label:<x> in README is a real command ($documented)" \
  '( for c in $documented; do [ -f "$ROOT/commands/${c#/chat-label:}.md" ] || exit 1; done )'
check "every flag the docs mention is handled by a script" '[ $flags_ok -eq 1 ]'
check "README uninstall command removes it" '[ $UN_RC -eq 0 ] && ! printf "%s" "$LIST_AFTER" | grep -q "chat-label@claude-chat-label"'
exit $fail
