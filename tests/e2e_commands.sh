#!/bin/bash
# E2E: the plugin's slash commands in real headless Claude Code sessions (haiku), on throwaway data only:
# fake chat transcripts, a fake editor state.vscdb, and temp state/backup dirs (all CHAT_LABEL_* overrides),
# so no real chat, tab or label state is read or written.
#   /chat-label:backfill            dry run: reports, writes nothing
#   /chat-label:backfill --apply    labels the unlabelled chats, prints a manifest
#   /chat-label:backfill --undo m   removes exactly those lines
#   /chat-label:tab-sync            dry run: reports stale tabs, writes nothing
#   /chat-label:tab-sync --tunggu   waits while the editor "runs", writes right after it "quits"
#   bash tests/e2e_commands.sh <empty-temp-dir>
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
WORK="$(cd "${1:?give an empty temp dir}" && pwd -P)"
REPO="$WORK/projek-kawan"
CHATS="$WORK/projects/p"
USER_DIR="$WORK/ide/User"
DB="$USER_DIR/workspaceStorage/abc/state.vscdb"
RUNNING="$WORK/ide-running"
mkdir -p "$REPO" "$CHATS" "$WORK/state" "$WORK/registry" "$(dirname "$DB")"
git -C "$REPO" init -q
GIT_AUTHOR_DATE=2026-09-11T10:00:00 GIT_COMMITTER_DATE=2026-09-11T10:00:00 \
  git -C "$REPO" -c user.name=e2e -c user.email=e2e@e2e commit -q --allow-empty -m awal
export CHAT_LABEL_PROJECTS_DIR="$WORK/projects" CHAT_LABEL_STATE_DIR="$WORK/state" CHAT_LABEL_BACKUP_DIR="$WORK/backups" \
  CHAT_LABEL_SESSIONS_REGISTRY="$WORK/registry" CHAT_LABEL_TEST_CWD="/ujian-sementara/" \
  CHAT_LABEL_USER_SETTINGS="$WORK/no-user-settings.json" CHAT_LABEL_AI_WAIT=0 \
  CHAT_LABEL_IDE_ROOTS="Antigravity IDE=$USER_DIR" CHAT_LABEL_IDE_RUNNING_FILE="$RUNNING" CHAT_LABEL_NOTIFY=0 \
  CHAT_LABEL_TAB_SYNC_LOG="$WORK/tab-sync.log" CHAT_LABEL_TAB_SYNC_SETTLE=0.2
printf '' > "$RUNNING"

python3 - "$CHATS" "$REPO" "$DB" <<'PY'
import json, os, sqlite3, sys
chats, repo, db = sys.argv[1:]
rows = {"c1": ("2026-10-28T02:00:00.000Z", "tambah butang eksport", "Eksport pesanan CSV", None),
        "c2": ("2026-10-28T03:00:00.000Z", "kenapa login lambat", "Login lambat", None),
        "c3": ("2026-10-29T02:00:00.000Z", "semak resit", None, None),
        "c4": ("2026-10-29T03:00:00.000Z", "dah berlabel", "X", "49A. Dah ada label")}
for sid, (ts, prompt, ai, custom) in rows.items():
    lines = [{"type": "user", "timestamp": ts, "cwd": repo, "sessionId": sid,
              "message": {"role": "user", "content": [{"type": "text", "text": prompt}]}}]
    if ai:
        lines.append({"type": "ai-title", "aiTitle": ai, "sessionId": sid})
    if custom:
        lines.append({"type": "custom-title", "customTitle": custom, "sessionId": sid})
    with open(os.path.join(chats, sid + ".jsonl"), "w") as fh:
        fh.writelines(json.dumps(r) + "\n" for r in lines)
def tab(title, sid):
    return json.dumps({"viewType": "mainThreadWebview-claudeVSCodePanel", "title": title,
                       "state": json.dumps({"sessionID": sid})}, separators=(",", ":"))
editors = [{"id": "workbench.editors.webviewInput", "value": tab(t, s)}
           for t, s in (("Eksport pesanan CSV", "c1"), ("Claude Code", "c2"), ("49A. Dah ada label", "c4"))]
memento = json.dumps({"editorpart.state": {"serializedGrid": {"root": {"data": [{"data": {"editors": editors}}]}}}},
                     separators=(",", ":"))
con = sqlite3.connect(db)
con.execute("CREATE TABLE ItemTable (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)")
con.execute("INSERT INTO ItemTable VALUES (?,?)", ("memento/workbench.parts.editor", memento))
con.commit()
con.close()
PY

DAY0=$(python3 -c "import datetime; print((datetime.date(2026,10,28)-datetime.date(2026,9,11)).days+1)")
DAY1=$((DAY0 + 1))
run() { (cd "$REPO" && env -u CLAUDECODE claude -p "$1" --model haiku --setting-sources project \
          --plugin-dir "$ROOT" --output-format json); }
field() { python3 -c "import sys,json; print(json.load(sys.stdin)['$1'])"; }
title() { python3 -c "
import json,sys
t=None
for l in open(sys.argv[1]):
    d=json.loads(l)
    if d.get('type')=='custom-title': t=d['customTitle']
print(t)" "$CHATS/$1.jsonl"; }
expanded() { grep -c "$2" "$(ls ~/.claude/projects/*/"$1".jsonl | head -1)"; }
tabs() { sqlite3 "$DB" "select value from ItemTable" | python3 -c "
import json,sys
m=json.load(sys.stdin)
print(' | '.join(json.loads(e['value'])['title'] for e in m['editorpart.state']['serializedGrid']['root']['data'][0]['data']['editors']))"; }
sum() { cat "$CHATS"/*.jsonl | shasum | cut -c1-12; }

BEFORE=$(sum)
OUT1=$(run "/chat-label:backfill") || { echo "backfill dry run failed: $OUT1"; exit 1; }
AFTER1=$(sum)
OUT2=$(run "/chat-label:backfill --apply") || { echo "backfill apply failed: $OUT2"; exit 1; }
T_C1=$(title c1); T_C2=$(title c2); T_C3=$(title c3); T_C4=$(title c4)
MANIFEST=$(ls "$WORK"/backups/chat-label-*/backfill-manifest.json 2>/dev/null | head -1)

TABS0=$(tabs)
OUT3=$(run "/chat-label:tab-sync") || { echo "tab-sync dry run failed: $OUT3"; exit 1; }
TABS3=$(tabs)
printf 'Antigravity IDE' > "$RUNNING"
OUT4=$(run "/chat-label:tab-sync --tunggu") || { echo "tab-sync wait failed: $OUT4"; exit 1; }
sleep 2
TABS4=$(tabs)
printf '' > "$RUNNING"
for _ in $(seq 1 20); do grep -q "Label tab siap" "$WORK/tab-sync.log" 2>/dev/null && break; sleep 0.5; done
TABS5=$(tabs)

OUT5=$(run "/chat-label:backfill --undo $MANIFEST") || { echo "backfill undo failed: $OUT5"; exit 1; }
AFTER5=$(sum)

fail=0
check() { if eval "$2"; then echo "  ✅ $1"; else echo "  ❌ $1"; fail=1; fi; }
for n in 1 2 3 4 5; do eval "echo \"  reply $n: \$(printf '%s' \"\$OUT$n\" | field result | tr '\n' ' ' | head -c 150)\""; done
echo "  titles after apply: $T_C1 | $T_C2 | $T_C3 | $T_C4"
echo "  tabs: before [$TABS0] / waiting [$TABS4] / after quit [$TABS5]"
check "backfill dry run ran the script inside the command" '[ "$(expanded "$(printf "%s" "$OUT1" | field session_id)" "akan dilabel: 3")" -ge 1 ]'
check "backfill dry run reports 3 and writes nothing" 'printf "%s" "$OUT1" | field result | grep -q 3 && [ "$BEFORE" = "$AFTER1" ]'
check "backfill --apply: per project day, AI title else prompt text, skips a letter already used that day" \
  '[ "$T_C1" = "${DAY0}A. Eksport pesanan CSV" ] && [ "$T_C2" = "${DAY0}B. Login lambat" ] && [ "$T_C3" = "${DAY1}B. semak resit" ]'
check "backfill --apply leaves an already labelled chat alone" '[ "$T_C4" = "49A. Dah ada label" ]'
check "backfill --apply reply gives the undo command" 'printf "%s" "$OUT2" | field result | grep -q -- "--undo"'
check "tab-sync dry run reports 2 stale tabs and writes nothing" 'printf "%s" "$OUT3" | field result | grep -q 2 && [ "$TABS3" = "$TABS0" ]'
check "tab-sync --tunggu: nothing written while the editor runs" '[ "$TABS4" = "$TABS0" ]'
check "tab-sync --tunggu: right after quit, tabs show the new labels" \
  '[ "$TABS5" = "${DAY0}A. Eksport pesanan CSV | ${DAY0}B. Login lambat | 49A. Dah ada label" ] && grep -q "Label tab siap" "$WORK/tab-sync.log"'
check "tab-sync --tunggu reply tells them to quit with Cmd+Q" 'printf "%s" "$OUT4" | field result | grep -qi "cmd"'
check "backfill --undo restores the transcripts byte for byte" '[ -n "$MANIFEST" ] && [ "$AFTER5" = "$BEFORE" ]'
exit $fail
