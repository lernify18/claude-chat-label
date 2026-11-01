#!/bin/bash
# E2E: chat-label-tab-sync.py pada SALINAN state.vscdb sebenar + transkrip sebenar ~/.claude/projects.
# Sumber = folder sandaran tab-sync (<App>-<workspace>/state.vscdb), atau lalai: workspaceStorage
# Antigravity IDE semasa. State IDE sebenar tidak disentuh.
#   bash tests/e2e_tab_sync.sh <folder-kerja-sementara> [folder-sandaran]
set -u
WORK="${1:?beri folder kerja sementara}"
SRC="${2:-}"
TOOL="$(cd "$(dirname "$0")/.." && pwd -P)/scripts/chat-label-tab-sync.py"
USER_DIR="$WORK/ide/User"
mkdir -p "$USER_DIR/workspaceStorage"
if [ -n "$SRC" ]; then
  for d in "$SRC"/Antigravity_IDE-*/; do
    ws="$USER_DIR/workspaceStorage/$(basename "$d" | sed 's/^Antigravity_IDE-//')"
    mkdir -p "$ws" && cp "$d/state.vscdb" "$ws/"
  done
else
  for db in "$HOME/Library/Application Support/Antigravity IDE/User/workspaceStorage"/*/state.vscdb; do
    ws="$USER_DIR/workspaceStorage/$(basename "$(dirname "$db")")"
    mkdir -p "$ws" && sqlite3 "$db" ".backup '$ws/state.vscdb'"
  done
fi
# State label kosong + registry kosong: --apply tak menyalur relabel sebenar ke transkrip sebenar.
mkdir -p "$WORK/state" "$WORK/registry"
export CHAT_LABEL_IDE_ROOTS="Antigravity IDE=$USER_DIR" CHAT_LABEL_BACKUP_DIR="$WORK/backups" \
  CHAT_LABEL_STATE_DIR="$WORK/state" CHAT_LABEL_SESSIONS_REGISTRY="$WORK/registry" CHAT_LABEL_NOTIFY=0
cp -R "$USER_DIR/workspaceStorage" "$WORK/asal"

DRY=$(CHAT_LABEL_IDE_RUNNING="" python3 "$TOOL")
REFUSED=$(CHAT_LABEL_IDE_RUNNING="Antigravity IDE" python3 "$TOOL" --apply); RC_REFUSED=$?
SAME_AFTER_REFUSE=$(diff -rq "$WORK/asal" "$USER_DIR/workspaceStorage" >/dev/null && echo ya || echo tidak)
APPLY=$(CHAT_LABEL_IDE_RUNNING="" python3 "$TOOL" --apply); RC_APPLY=$?
AGAIN=$(CHAT_LABEL_IDE_RUNNING="" python3 "$TOOL")
VERIFY=$(python3 - "$WORK/asal" "$USER_DIR/workspaceStorage" "$TOOL" <<'EOF'
import glob, importlib.util, json, os, sqlite3, sys
spec = importlib.util.spec_from_file_location("ts", sys.argv[3]); ts = importlib.util.module_from_spec(spec); spec.loader.exec_module(ts)
index = ts.transcripts()
def load(db):
    con = sqlite3.connect(db)
    try:
        ok = con.execute("PRAGMA integrity_check").fetchone()[0]
        rows = dict(con.execute("SELECT key, value FROM ItemTable").fetchall())
    finally:
        con.close()
    return ok, rows
def tabs(node, out):
    if isinstance(node, dict):
        for v in node.values(): tabs(v, out)
    elif isinstance(node, list):
        for v in node: tabs(v, out)
    elif isinstance(node, str) and ts.VIEW in node:
        out.append(json.loads(node))
    return out
n_tabs = n_changed = bad = 0
for old_db in sorted(glob.glob(os.path.join(sys.argv[1], "*", "state.vscdb"))):
    new_db = os.path.join(sys.argv[2], os.path.basename(os.path.dirname(old_db)), "state.vscdb")
    ok, new = load(new_db); _, old = load(old_db)
    if ok != "ok": print("integriti gagal", new_db); bad += 1
    if {k: v for k, v in new.items() if k != ts.KEY} != {k: v for k, v in old.items() if k != ts.KEY}:
        print("kunci lain berubah", new_db); bad += 1
    if ts.KEY not in old: continue
    a, b = tabs(json.loads(old[ts.KEY]), []), tabs(json.loads(new[ts.KEY]), [])
    for x, y in zip(a, b):
        n_tabs += 1
        sid = json.loads(x.get("state") or "{}").get("sessionID")
        title = ts.disk_title(index[sid]) if sid in index else None
        want = ts.cut(title) if title else x["title"]
        if y["title"] != want: print("tajuk salah", sid, y["title"], want); bad += 1
        n_changed += x["title"] != y["title"]
        x.pop("title"); y.pop("title")
        if x != y: print("medan lain berubah", sid); bad += 1
    if len(a) != len(b): print("bilangan tab berubah", new_db); bad += 1
print("tab=%d berubah=%d masalah=%d" % (n_tabs, n_changed, bad))
EOF
)
BACKUPS=$(ls -d "$WORK"/backups/chat-label-tab-sync-*/*/ 2>/dev/null | wc -l | tr -d ' ')

# Penunggu pada set salinan kedua: IDE dibuka semula ~1.5 s selepas tutup (insiden 31/10), kemudian tutup betul.
USER2="$WORK/ide2/User"; FLAG="$WORK/running"; LOG="$WORK/tunggu.log"
mkdir -p "$USER2" && cp -R "$WORK/asal" "$USER2/workspaceStorage"
printf 'Antigravity IDE' > "$FLAG"
CHAT_LABEL_IDE_ROOTS="Antigravity IDE=$USER2" CHAT_LABEL_IDE_RUNNING_FILE="$FLAG" CHAT_LABEL_TAB_SYNC_LOG="$LOG" \
  CHAT_LABEL_TAB_SYNC_SETTLE=3 python3 "$TOOL" --tunggu >/dev/null
WPID=$(sed -n 's/.*(pid \([0-9]*\)).*/\1/p' "$LOG" | head -1)
sleep 8
printf '' > "$FLAG"; sleep 1.5; printf 'Antigravity IDE' > "$FLAG"
for _ in $(seq 30); do grep -q "terlalu cepat" "$LOG" && break; sleep 0.5; done
SAME_AFTER_FAST=$(diff -rq "$WORK/asal" "$USER2/workspaceStorage" >/dev/null && echo ya || echo tidak)
ALIVE_AFTER_FAST=$(kill -0 "$WPID" 2>/dev/null && echo ya || echo tidak)
printf '' > "$FLAG"
for _ in $(seq 60); do grep -q "Label tab siap" "$LOG" && break; sleep 0.5; done
sleep 2
ALIVE_AFTER_DONE=$(kill -0 "$WPID" 2>/dev/null && echo ya || echo tidak)
WRITE_SECS=$(python3 - "$LOG" <<'EOF'
import datetime, re, sys
t = lambda s: datetime.datetime.fromisoformat(s)
lines = open(sys.argv[1]).read().splitlines()
closed = [re.match(r"\[(.*?)\]", l).group(1) for l in lines if l.endswith("dah tutup")]
done = [re.match(r"\[(.*?)\]", l).group(1) for l in lines if "Label tab siap" in l]
print(int((t(done[-1]) - t(closed[-1])).total_seconds()) if closed and done else 99)
EOF
)
SAME_AS_APPLY=$(for db in "$USER_DIR"/workspaceStorage/*/state.vscdb; do
  ws=$(basename "$(dirname "$db")")
  a=$(sqlite3 "$db" "SELECT value FROM ItemTable WHERE key='memento/workbench.parts.editor'" | shasum)
  b=$(sqlite3 "$USER2/workspaceStorage/$ws/state.vscdb" "SELECT value FROM ItemTable WHERE key='memento/workbench.parts.editor'" | shasum)
  [ "$a" = "$b" ] || echo "$ws"
done)
STALE=$(printf '%s' "$DRY" | sed -n 's/^jumlah tab basi: \([0-9]*\).*/\1/p')

fail=0
check() { if eval "$2"; then echo "  ✅ $1"; else echo "  ❌ $1"; fail=1; fi; }
echo "  larian kering: $(printf '%s' "$DRY" | head -1) · $VERIFY · sandaran $BACKUPS workspace"
check "larian kering jumpa tab basi dan tak menulis" '[ "${STALE:-0}" -gt 0 ]'
check "--apply ditolak (kod 3) bila IDE hidup, fail tak berubah" '[ $RC_REFUSED = 3 ] && [ "$SAME_AFTER_REFUSE" = ya ]'
check "--apply bila IDE tutup: setiap tab = tajuk di cakera (25 aksara), lain tak berubah, integriti ok" '[ $RC_APPLY = 0 ] && printf "%s" "$VERIFY" | grep -q "berubah=$STALE masalah=0"'
check "setiap workspace yang ditulis ada sandaran" '[ "$BACKUPS" -gt 0 ]'
check "larian kedua: 0 tab basi" 'printf "%s" "$AGAIN" | grep -q "jumlah tab basi: 0"'
echo "  penunggu: tulis ${WRITE_SECS}s selepas tutup · log $LOG"
check "penunggu: IDE dibuka semula terlalu cepat -> tiada tulisan, penunggu masih hidup" '[ "$SAME_AFTER_FAST" = ya ] && [ "$ALIVE_AFTER_FAST" = ya ]'
check "penunggu: tutup seterusnya -> tulis dalam ≤2 s, hasil sama dengan --apply, penunggu keluar" '[ "$WRITE_SECS" -le 2 ] && [ -z "$SAME_AS_APPLY" ] && [ "$ALIVE_AFTER_DONE" = tidak ]'
exit $fail
