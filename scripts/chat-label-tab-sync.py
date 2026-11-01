#!/usr/bin/env python3
"""Make the editor tabs of open Claude chats show the title each chat has on disk.

  python3 chat-label-tab-sync.py              # dry run: what would change
  python3 chat-label-tab-sync.py --apply      # IDE must be closed (Cmd+Q)
  python3 chat-label-tab-sync.py --tunggu     # detach, wait for the IDE to quit, then apply

Why: Antigravity / VS Code restores each tab with the name it saved at shutdown and only asks the
Claude extension for the real title when that tab is clicked. Chats relabelled on disk (backfill,
hand edits) therefore keep their old tab names across restarts. The saved names live in each
workspace's state.vscdb (ItemTable 'memento/workbench.parts.editor'); this rewrites them to the
chat's current title, cut to 25 characters exactly as the extension does.

The IDE overwrites state.vscdb when it quits, so --apply refuses while that IDE is running.
Every changed state.vscdb is copied to <claude config dir>/backups/chat-label-tab-sync-<time>/ first.
--apply also writes the titles chat-label-backfill.py --semula queued for chats that were open then.

--tunggu writes within about a second of the quit and shows a macOS notification when it is safe to
reopen. Reopened too soon (within a few seconds of quitting), it says so and waits for the next quit
instead of stopping. It waits up to 7 days, and a second --tunggu is a no-op. macOS only: the IDE
state lives under ~/Library/Application Support.
Test overrides: CHAT_LABEL_IDE_ROOTS (":"-separated "<App>=<User dir>"), CHAT_LABEL_PROJECTS_DIR,
CHAT_LABEL_BACKUP_DIR, CHAT_LABEL_IDE_RUNNING (comma list of App names treated as running) or
CHAT_LABEL_IDE_RUNNING_FILE (same list, re-read on every check), CHAT_LABEL_TAB_SYNC_LOG,
CHAT_LABEL_NOTIFY=0 (log only, no notification), CHAT_LABEL_TAB_SYNC_SETTLE (seconds between quit and write).
"""
import datetime
import fcntl
import glob
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time

KEY = "memento/workbench.parts.editor"
VIEW = "mainThreadWebview-claudeVSCodePanel"
MAX = 25
SUPPORT = os.path.expanduser("~/Library/Application Support")
APPS = {"Antigravity IDE": "Antigravity IDE", "Antigravity": "Antigravity", "Visual Studio Code": "Code",
        "Cursor": "Cursor", "Windsurf": "Windsurf"}
HOME = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
LOG = os.environ.get("CHAT_LABEL_TAB_SYNC_LOG") or os.path.join(HOME, "logs", "chat-label-tab-sync.log")
WAIT_MAX = 7 * 86400
POLL = 1
SETTLE = float(os.environ.get("CHAT_LABEL_TAB_SYNC_SETTLE") or 0.5)
REWARM = 30
_titles = {}


def ide_roots():
    env = os.environ.get("CHAT_LABEL_IDE_ROOTS")
    if env:
        return [tuple(p.split("=", 1)) for p in env.split(":") if "=" in p]
    return [(app, os.path.join(SUPPORT, d, "User")) for app, d in APPS.items()
            if os.path.isdir(os.path.join(SUPPORT, d, "User"))]


def running(app):
    """pgrep -f cannot see the Electron main process's args on macOS; ps can. Unsure = running."""
    env = os.environ.get("CHAT_LABEL_IDE_RUNNING")
    if os.environ.get("CHAT_LABEL_IDE_RUNNING_FILE"):
        with open(os.environ["CHAT_LABEL_IDE_RUNNING_FILE"]) as fh:
            env = fh.read().strip()
    if env is not None:
        return app in env.split(",")
    try:
        r = subprocess.run(["ps", "-axww", "-o", "args="], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return True
    return r.returncode != 0 or "/%s.app/Contents/MacOS/" % app in r.stdout


def cut(title):
    return title if len(title) <= MAX else title[:MAX - 1] + "…"


def transcripts():
    base = os.environ.get("CHAT_LABEL_PROJECTS_DIR") or os.path.join(HOME, "projects")
    return {os.path.basename(p)[:-6]: p for p in glob.glob(os.path.join(base, "*", "*.jsonl"))}


def disk_title(path):
    """Cached on (mtime, size): the waiter warms it up so the write right after a quit is quick."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (st.st_mtime_ns, st.st_size)
    hit = _titles.get(path)
    if hit and hit[0] == key:
        return hit[1]
    custom = ai = None
    with open(path, "rb") as fh:
        for line in fh:
            if b'-title"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") == "custom-title" and d.get("customTitle"):
                custom = d["customTitle"]
            elif d.get("type") == "ai-title" and d.get("aiTitle"):
                ai = d["aiTitle"]
    _titles[path] = (key, custom or ai)
    return custom or ai


def backfill():
    spec = importlib.util.spec_from_file_location(
        "chat_label_backfill", os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat-label-backfill.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def retitle(node, index, changes):
    """Walk the editor memento; rewrite Claude tab titles in place. Returns the new node."""
    if isinstance(node, dict):
        return {k: retitle(v, index, changes) for k, v in node.items()}
    if isinstance(node, list):
        return [retitle(v, index, changes) for v in node]
    if isinstance(node, str) and '"viewType":"%s"' % VIEW in node:
        try:
            tab = json.loads(node)
            sid = json.loads(tab.get("state") or "{}").get("sessionID")
        except (ValueError, AttributeError):
            return node
        path = index.get(sid or "")
        title = disk_title(path) if path else None
        if title and cut(title) != tab.get("title"):
            changes.append((tab.get("title"), cut(title)))
            tab["title"] = cut(title)
            return json.dumps(tab, ensure_ascii=False, separators=(",", ":"))
    return node


def plan_db(db, index):
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    try:
        row = con.execute("SELECT value FROM ItemTable WHERE key=?", (KEY,)).fetchone()
    except sqlite3.Error:
        row = None
    finally:
        con.close()
    if not row:
        return None, []
    changes = []
    new = retitle(json.loads(row[0]), index, changes)
    return json.dumps(new, ensure_ascii=False, separators=(",", ":")), changes


def write_db(db, value, backup_dir, label):
    dest = os.path.join(backup_dir, label)
    os.makedirs(dest, exist_ok=True)
    for f in glob.glob(db + "*"):
        if not f.endswith(".backup"):
            shutil.copy2(f, dest)
    con = sqlite3.connect(db)
    try:
        with con:
            con.execute("UPDATE ItemTable SET value=? WHERE key=?", (value, KEY))
        ok = con.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        con.close()
    if ok != "ok":
        raise RuntimeError("integrity_check %s: %s" % (db, ok))


def watched_apps():
    """Running IDEs rewrite their tab names at quit, so they are synced after quitting even if
    they look clean now; closed IDEs only when they have stale tabs."""
    index = transcripts()
    return [app for app, user_dir in ide_roots() if running(app) or
            any(plan_db(db, index)[1] for db in glob.glob(os.path.join(user_dir, "workspaceStorage", "*", "state.vscdb")))]


def sync(apply, only=None):
    bf = backfill()
    if apply:
        n = bf.flush_relabels()
        if n:
            print("relabel beratur ditulis: %d" % n)
    else:
        n = len(bf.queued_relabels())
        if n:
            print("relabel beratur (ditulis dengan --apply): %d" % n)
    index = transcripts()
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = os.path.join(os.environ.get("CHAT_LABEL_BACKUP_DIR") or os.path.join(HOME, "backups"),
                              "chat-label-tab-sync-" + stamp)
    total, refused = 0, []
    for app, user_dir in ide_roots():
        if only is not None and app not in only:
            continue
        dbs = sorted(glob.glob(os.path.join(user_dir, "workspaceStorage", "*", "state.vscdb")))
        found = [(db, *plan_db(db, index)) for db in dbs]
        found = [f for f in found if f[2]]
        if not found:
            continue
        n = sum(len(f[2]) for f in found)
        total += n
        print("%s: %d tab basi dalam %d workspace" % (app, n, len(found)))
        for _, _, changes in found:
            for old, new in changes[:3]:
                print("   %r -> %r" % (old, new))
        if not apply:
            continue
        if running(app):
            refused.append(app)
            print("   DITOLAK: %s masih berjalan. Tutup dengan Cmd+Q dulu." % app)
            continue
        for db, value, _ in found:
            write_db(db, value, backup_dir, "%s-%s" % (APPS.get(app, app).replace(" ", "_"),
                                                       os.path.basename(os.path.dirname(db))))
        print("   dikemas kini; sandaran: %s" % backup_dir)
    print("jumlah tab basi: %d%s" % (total, "" if apply else " (larian kering, guna --apply)"))
    return 3 if refused else 0


def wait_then_apply():
    """The lock is inherited by the daemon and held until it exits, so only one waiter ever runs."""
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    lock = open(LOG + ".lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("penunggu sudah berjalan; log: %s" % LOG)
        return 0
    if os.fork():
        print("penunggu dimulakan; log: %s" % LOG)
        return 0
    os.setsid()
    if os.fork():
        os._exit(0)
    out = open(LOG, "a", buffering=1)
    os.dup2(out.fileno(), 1)
    os.dup2(out.fileno(), 2)
    sys.stdout = sys.stderr = out
    todo = watched_apps()
    print("[%s] menunggu %s ditutup (pid %d)" % (now(), ", ".join(todo) or "tiada", os.getpid()))
    start, code, warmed = time.time(), 0, 0
    while todo:
        if time.time() - start > WAIT_MAX:
            print("[%s] berhenti: %s tak ditutup dalam %d hari" % (now(), ", ".join(todo), WAIT_MAX // 86400))
            os._exit(1)
        if time.time() - warmed > REWARM:
            for path in transcripts().values():
                disk_title(path)
            warmed = time.time()
        for app in [a for a in todo if not running(a)]:
            time.sleep(SETTLE)
            print("[%s] %s dah tutup" % (now(), app))
            try:
                rc = sync(True, only={app})
            except Exception as exc:
                print("GAGAL: %s" % exc)
                code = 1
                todo.remove(app)
                continue
            if rc == 3 or running(app):
                notify("%s dibuka semula terlalu cepat. Cmd+Q sekali lagi dan tunggu notifikasi siap." % app)
                continue
            todo.remove(app)
            notify("Label tab siap. Boleh buka semula %s." % app)
        time.sleep(POLL)
    os._exit(code)


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def notify(text):
    print("[%s] %s" % (now(), text))
    if os.environ.get("CHAT_LABEL_NOTIFY") == "0":
        return
    try:
        subprocess.run(["osascript", "-e", 'display notification "%s" with title "Label chat"' % text],
                       capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def main(argv):
    if "--tunggu" in argv:
        return wait_then_apply()
    return sync("--apply" in argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
