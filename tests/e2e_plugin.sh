#!/bin/bash
# E2E: real headless Claude Code sessions (haiku) with this plugin loaded through --plugin-dir.
# Only project settings load (--setting-sources project), so the user's own hooks and plugins stay out,
# and state goes to a temp dir (CHAT_LABEL_STATE_DIR), so the real counters are never touched.
# Test projects: a git repo whose first commit is 2026-09-11, a worktree inside it, and a folder
# without git (Day 1 = the day the folder was made = today).
# -p never generates an AI title, so the test appends one ai-title line to its own session transcript,
# in the same shape Claude Code writes.
# Part 2 installs the plugin from this folder into a throwaway CLAUDE_CONFIG_DIR, the way a friend would.
#   bash tests/e2e_plugin.sh <empty-temp-dir>
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
WORK="${1:?give an empty temp dir}"
PROJ="$WORK/projek-e2e"
WT="$PROJ/worktrees/cabang"
PLAIN="$WORK/nota-e2e"
export CHAT_LABEL_STATE_DIR="$WORK/state" CHAT_LABEL_AI_WAIT=0
# The machine running this may itself have the hook hand-installed; point the guard at no settings.
export CHAT_LABEL_USER_SETTINGS="$WORK/no-user-settings.json"
mkdir -p "$PROJ" "$PLAIN" "$CHAT_LABEL_STATE_DIR"
git -C "$PROJ" init -q
GIT_AUTHOR_DATE=2026-09-11T10:00:00 GIT_COMMITTER_DATE=2026-09-11T10:00:00 \
  git -C "$PROJ" -c user.name=e2e -c user.email=e2e@e2e commit -q --allow-empty -m awal
git -C "$PROJ" worktree add -q -b cabang "$WT"
DAY=$(python3 -c "import datetime; print((datetime.date.today()-datetime.date(2026,9,11)).days+1)")

run() { local dir="$1"; shift; (cd "$dir" && env -u CLAUDECODE claude -p "$@" --model haiku --setting-sources project \
          --plugin-dir "$ROOT" --output-format json); }
field() { python3 -c "import sys,json; print(json.load(sys.stdin)['$1'])"; }
transcript() { ls ~/.claude/projects/*/"$1".jsonl | head -1; }
last_title() { python3 -c "
import json,sys
t=None
for l in open(sys.argv[1]):
    if '\"custom-title\"' in l:
        d=json.loads(l)
        if d.get('type')=='custom-title': t=d['customTitle']
print(t)" "$(transcript "$1")"; }

ASK="Apakah CHAT LABEL untuk sesi ini? Balas label itu sahaja."
OUT1=$(run "$PROJ" "$ASK") || { echo "claude -p failed: $OUT1"; exit 1; }
SID=$(printf '%s' "$OUT1" | field session_id)
TITLE1=$(last_title "$SID")
printf '{"type":"ai-title","aiTitle":"Semak label chat E2E","sessionId":"%s"}\n' "$SID" >> "$(transcript "$SID")"
OUT2=$(run "$PROJ" "Ulang CHAT LABEL sesi ini sahaja." --resume "$SID") || { echo "resume failed: $OUT2"; exit 1; }
TITLE2=$(last_title "$SID")
OUT3=$(run "$WT" "$ASK") || { echo "worktree failed: $OUT3"; exit 1; }
SID3=$(printf '%s' "$OUT3" | field session_id)
TITLE3=$(last_title "$SID3")
OUT4=$(run "$PLAIN" "$ASK") || { echo "no-git folder failed: $OUT4"; exit 1; }
TITLE4=$(last_title "$(printf '%s' "$OUT4" | field session_id)")
OUT5=$(run "$WT" "Ulang CHAT LABEL sesi ini sahaja." --resume "$SID3") || { echo "worktree resume failed: $OUT5"; exit 1; }
TITLE5=$(last_title "$SID3")
# Like the IDE: Claude Code holds the AI title in memory but writes the transcript only AFTER the first
# prompt's hook returns, so the hook sets <label>. <prompt> and the detached follower writes
# <label>. <AI title>. -p generates no AI title, so the ai-title line is appended as soon as the file
# exists, the way the IDE writes it.
SID6=$(python3 -c "import uuid; print(uuid.uuid4())")
T6="$HOME/.claude/projects/$(printf '%s' "$(cd "$PROJ" && pwd -P)" | sed 's#[^A-Za-z0-9]#-#g')/$SID6.jsonl"
( for _ in $(seq 300); do [ -f "$T6" ] && break; sleep 0.1; done
  printf '{"type":"ai-title","aiTitle":"Tajuk asal IDE","sessionId":"%s"}\n' "$SID6" >> "$T6" ) &
OUT6=$(CHAT_LABEL_AI_WAIT=3.5 CHAT_LABEL_FOLLOW_POLL=0.2 run "$PROJ" \
  "Run the Bash command 'sleep 4' first, then reply with this session's CHAT LABEL only." \
  --session-id "$SID6" --allowedTools "Bash(sleep 4)") || { echo "IDE mode failed: $OUT6"; exit 1; }
wait
LOG6="$CHAT_LABEL_STATE_DIR/chat-label-follow.log"
for _ in $(seq 50); do grep -q "${SID6:0:8} " "$LOG6" 2>/dev/null && break; sleep 0.2; done
TITLE6=$(last_title "$SID6")
WANT6="${DAY}C. Tajuk asal IDE"
COUNT6=$(grep -c "\"customTitle\":\"$WANT6\"" "$T6")
REG="$CHAT_LABEL_STATE_DIR/chat-label-projects.json"
cp "$REG" "$WORK/registry-part1.json"

# Part 3 (runs before part 2 so the counter is known): a hand-installed copy in the user's settings AND
# the plugin both load. The guard must leave exactly one label: the next letter, injected once.
mkdir -p "$WORK/legacy/hooks"
printf '#!/bin/bash\nexec python3 "%s/hooks/chat_label.py"\n' "$ROOT" > "$WORK/legacy/hooks/chat-label.sh"
LEGACY_CMD="bash \\\"$WORK/legacy/hooks/chat-label.sh\\\""
cat > "$WORK/legacy/settings.json" <<JSON
{"hooks": {
  "SessionStart": [{"hooks": [{"type": "command", "command": "$LEGACY_CMD", "timeout": 5}]}],
  "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "$LEGACY_CMD", "timeout": 5}]}]
}}
JSON
both() { (cd "$PROJ" && env -u CLAUDECODE CHAT_LABEL_USER_SETTINGS="$WORK/legacy/settings.json" claude -p "$@" --model haiku \
          --setting-sources project --settings "$WORK/legacy/settings.json" --plugin-dir "$ROOT" --output-format json); }
OUT7=$(both "$ASK") || { echo "hook+plugin failed: $OUT7"; exit 1; }
SID7=$(printf '%s' "$OUT7" | field session_id)
TITLE7=$(last_title "$SID7")
INJECT7=$(grep "hook_additional_context" "$(transcript "$SID7")" | grep -c "untuk sesi ini = ")

# Part 2: install the way a friend would, into a throwaway config dir.
CFG="$WORK/config"
mkdir -p "$CFG"
ADD=$(CLAUDE_CONFIG_DIR="$CFG" claude plugin marketplace add "$ROOT" 2>&1); ADD_RC=$?
INS=$(CLAUDE_CONFIG_DIR="$CFG" claude plugin install chat-label@claude-chat-label 2>&1); INS_RC=$?
LIST=$(CLAUDE_CONFIG_DIR="$CFG" claude plugin list 2>&1)
INSTALLED=$(find "$CFG/plugins" -path "*chat-label*" -name hooks.json -not -path "*/marketplaces/*" 2>/dev/null | head -1)
FRESH="$WORK/projek-kawan"
mkdir -p "$FRESH"
git -C "$FRESH" init -q
git -C "$FRESH" -c user.name=e2e -c user.email=e2e@e2e commit -q --allow-empty -m awal
HOOKOUT=""
if [ -n "$INSTALLED" ]; then
  PLUGROOT="$(dirname "$(dirname "$INSTALLED")")"
  CMD=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['hooks']['UserPromptSubmit'][0]['hooks'][0]['command'])" "$INSTALLED")
  HOOKOUT=$(printf '{"hook_event_name":"UserPromptSubmit","session_id":"kawan-1","prompt":"hai","cwd":"%s","transcript_path":"/nope"}' "$FRESH" \
    | CLAUDE_PLUGIN_ROOT="$PLUGROOT" CHAT_LABEL_STATE_DIR= CLAUDE_CONFIG_DIR="$CFG" bash -c "$CMD")
fi

fail=0
check() { if eval "$2"; then echo "  ✅ $1"; else echo "  ❌ $1"; fail=1; fi; }
echo "session $SID (project day = $DAY)"
for n in 1 2 3 4 5 6; do eval "echo \"  title $n: \$TITLE$n | reply: \$(printf '%s' \"\$OUT$n\" | field result | head -c 60)\""; done
check "first prompt labels but leaves the title (no custom-title)" '[ "$TITLE1" = "None" ]'
check "Claude sees the label (additionalContext)" 'printf "%s" "$OUT1" | field result | grep -q "${DAY}A"'
check "second prompt: tab = ${DAY}A. <AI title>" '[ "$TITLE2" = "${DAY}A. Semak label chat E2E" ]'
check "resume keeps the same label (SessionStart)" 'printf "%s" "$OUT2" | field result | grep -q "${DAY}A"'
check "worktree continues the parent project counter (${DAY}B)" '[ "$TITLE3" = "None" ] && printf "%s" "$OUT3" | field result | grep -q "${DAY}B"'
check "no AI title: second prompt uses the first prompt text" '[ "$TITLE5" = "${DAY}B. Apakah CHAT LABEL untuk sesi ini? Balas label itu…" ]'
check "IDE mode: label on the first prompt (${DAY}C)" 'printf "%s" "$OUT6" | field result | grep -q "${DAY}C"'
check "IDE mode: final title = $WANT6 (follower), not the prompt text" '[ "$TITLE6" = "$WANT6" ]'
check "IDE mode: Claude Code itself wrote the follower's title back (adopted, $COUNT6 lines)" '[ "$COUNT6" -ge 2 ] && grep -q "${SID6:0:8} adopted" "$LOG6"'
check "folder without git starts at Day 1 (1A)" 'printf "%s" "$OUT4" | field result | grep -q "1A"'
check "registry: parent project only, counter 3, Day 1 = first commit" 'python3 -c "
import json,os,sys; r=json.load(open(sys.argv[1])); p=r[os.path.realpath(sys.argv[2])]
assert len(r)==2 and p[\"anchor\"]==\"2026-09-11\" and p[\"from\"]==\"git\" and p[\"last_index\"]==3
assert r[os.path.realpath(sys.argv[3])][\"from\"]==\"folder\"" "$WORK/registry-part1.json" "$PROJ" "$PLAIN"'
echo "  hook+plugin: title $TITLE7 | reply: $(printf '%s' "$OUT7" | field result | head -c 60) | injections $INJECT7"
check "hand-installed hook + plugin: one label ${DAY}D, injected once, title left alone" \
  '[ "$TITLE7" = "None" ] && [ "$INJECT7" = "1" ] && printf "%s" "$OUT7" | field result | grep -q "${DAY}D"'
check "hand-installed hook + plugin: counter moved by exactly one (4)" \
  'python3 -c "import json,os,sys; assert json.load(open(sys.argv[1]))[os.path.realpath(sys.argv[2])][\"last_index\"]==4" "$REG" "$PROJ"'
echo "  install: rc $ADD_RC/$INS_RC | $(printf '%s' "$INS" | tail -1)"
check "install: marketplace add + plugin install succeed" '[ $ADD_RC -eq 0 ] && [ $INS_RC -eq 0 ]'
check "install: plugin listed and enabled" 'printf "%s" "$LIST" | grep -q "chat-label" && printf "%s" "$LIST" | grep -qi "enabled"'
check "install: installed copy has hooks.json and the hook script" '[ -n "$INSTALLED" ] && [ -f "$(dirname "$INSTALLED")/chat_label.py" ]'
check "install: installed hook labels a new project 1A, state in that config dir" 'printf "%s" "$HOOKOUT" | grep -q "= 1A\." && [ -f "$CFG/chat-label-projects.json" ]'
exit $fail
