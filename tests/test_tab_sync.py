"""python3 tests/test_tab_sync.py"""
import datetime
import glob
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

TOOL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "chat-label-tab-sync.py")
KEY = "memento/workbench.parts.editor"


def tab(title, sid):
    return json.dumps({"origin": "x", "viewType": "mainThreadWebview-claudeVSCodePanel", "providedId": "claudeVSCodePanel",
                       "title": title, "state": json.dumps({"isFullEditor": False, "sessionID": sid}), "group": 2},
                      ensure_ascii=False, separators=(",", ":"))


def memento(*values):
    editors = [{"id": "workbench.editors.webviewInput", "value": v} for v in values]
    editors.append({"id": "workbench.editors.files.fileEditorInput", "value": json.dumps({"resourceJSON": {"path": "/a.ts"}})})
    return json.dumps({"editorpart.state": {"serializedGrid": {"root": {"data": [{"data": {"editors": editors, "mru": [0]}}]}}}},
                      ensure_ascii=False, separators=(",", ":"))


class TabSync(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        chats = os.path.join(d, "projects", "p")
        os.makedirs(chats)
        rows = {"lama": [{"type": "ai-title", "aiTitle": "Blok funnel data tab draf"},
                         {"type": "custom-title", "customTitle": "98C - Blok funnel data tab draf"}],
                "ai": [{"type": "ai-title", "aiTitle": "Tajuk AI sahaja"}],
                "betul": [{"type": "custom-title", "customTitle": "122A - Ada apa2 yg belum merge tak?"}],
                "panjang": [{"type": "custom-title", "customTitle": "121AR - ada sesi yg sedang berjalan"}]}
        for sid, lines in rows.items():
            with open(os.path.join(chats, sid + ".jsonl"), "w") as fh:
                for r in [{"type": "user", "message": {"content": "hai"}}] + lines:
                    fh.write(json.dumps(r) + "\n")
        self.ws = os.path.join(d, "ide", "User", "workspaceStorage", "abc")
        os.makedirs(self.ws)
        self.db = os.path.join(self.ws, "state.vscdb")
        self.before = memento(tab("Blok funnel data tab dra…", "lama"), tab("Claude Code", "ai"),
                              tab("122A - Ada apa2 yg belum…", "betul"), tab("121AR - ada sesi yg sedang berjalan", "panjang"),
                              tab("Hilang", "tiada-transkrip"))
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE ItemTable (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)")
        con.executemany("INSERT INTO ItemTable VALUES (?,?)", [(KEY, self.before), ("lain", "kekal")])
        con.commit()
        con.close()
        self.registry = os.path.join(d, "sessions")
        os.makedirs(self.registry)
        self.env = dict(os.environ, CHAT_LABEL_IDE_ROOTS="Antigravity IDE=" + os.path.join(d, "ide", "User"),
                        CHAT_LABEL_PROJECTS_DIR=os.path.join(d, "projects"), CHAT_LABEL_BACKUP_DIR=os.path.join(d, "backups"),
                        CHAT_LABEL_IDE_RUNNING="", CHAT_LABEL_STATE_DIR=d, CHAT_LABEL_SESSIONS_REGISTRY=self.registry,
                        CHAT_LABEL_NOTIFY="0")

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, *args, running=""):
        env = dict(self.env, CHAT_LABEL_IDE_RUNNING=running)
        return subprocess.run([sys.executable, TOOL, *args], capture_output=True, text=True, env=env)

    def value(self, key=KEY):
        con = sqlite3.connect(self.db)
        try:
            return con.execute("SELECT value FROM ItemTable WHERE key=?", (key,)).fetchone()[0]
        finally:
            con.close()

    def titles(self):
        out = []
        for e in json.loads(self.value())["editorpart.state"]["serializedGrid"]["root"]["data"][0]["data"]["editors"]:
            v = json.loads(e["value"])
            if v.get("viewType"):
                out.append(v["title"])
        return out

    def test_dry_run_lists_stale_tabs_and_writes_nothing(self):
        r = self.run_tool()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Antigravity IDE: 3 tab basi dalam 1 workspace", r.stdout)
        self.assertIn("'Blok funnel data tab dra…' -> '98C - Blok funnel data t…'", r.stdout)
        self.assertEqual(self.value(), self.before)

    def test_apply_refused_while_ide_runs(self):
        r = self.run_tool("--apply", running="Antigravity IDE")
        self.assertEqual(r.returncode, 3)
        self.assertIn("DITOLAK", r.stdout)
        self.assertEqual(self.value(), self.before)

    def test_apply_rewrites_only_claude_titles_backs_up_and_is_idempotent(self):
        r = self.run_tool("--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.titles(), ["98C - Blok funnel data t…", "Tajuk AI sahaja", "122A - Ada apa2 yg belum…",
                                         "121AR - ada sesi yg seda…", "Hilang"])
        after, before = json.loads(self.value()), json.loads(self.before)
        a_eds = after["editorpart.state"]["serializedGrid"]["root"]["data"][0]["data"]["editors"]
        b_eds = before["editorpart.state"]["serializedGrid"]["root"]["data"][0]["data"]["editors"]
        self.assertEqual(a_eds[-1], b_eds[-1])
        for a, b in zip(a_eds, b_eds):
            va, vb = json.loads(a["value"]), json.loads(b["value"])
            va.pop("title", None), vb.pop("title", None)
            self.assertEqual(va, vb)
        self.assertEqual(self.value("lain"), "kekal")
        backups = glob.glob(os.path.join(self.env["CHAT_LABEL_BACKUP_DIR"], "chat-label-tab-sync-*", "*", "state.vscdb"))
        self.assertEqual(len(backups), 1)
        con = sqlite3.connect(backups[0])
        self.assertEqual(con.execute("SELECT value FROM ItemTable WHERE key=?", (KEY,)).fetchone()[0], self.before)
        con.close()
        again = self.run_tool("--apply")
        self.assertIn("jumlah tab basi: 0", again.stdout)

    def test_waiter_holds_while_ide_runs_then_applies_after_quit(self):
        flag = os.path.join(self.tmp.name, "running")
        log = os.path.join(self.tmp.name, "sync.log")
        with open(flag, "w") as fh:
            fh.write("Antigravity IDE")
        env = dict(self.env, CHAT_LABEL_IDE_RUNNING_FILE=flag, CHAT_LABEL_TAB_SYNC_LOG=log)
        r = subprocess.run([sys.executable, TOOL, "--tunggu"], capture_output=True, text=True, env=env, timeout=10)
        self.assertIn("penunggu dimulakan", r.stdout)
        time.sleep(5)
        self.assertEqual(self.value(), self.before)
        self.assertIn("menunggu Antigravity IDE ditutup", open(log).read())
        with open(flag, "w") as fh:
            fh.write("")
        for _ in range(40):
            if "jumlah tab basi" in open(log).read():
                break
            time.sleep(0.5)
        self.assertIn("Antigravity IDE dah tutup", open(log).read())
        self.assertEqual(self.titles()[0], "98C - Blok funnel data t…")

    def test_waiter_keeps_waiting_when_ide_reopens_too_fast(self):
        flag = os.path.join(self.tmp.name, "running")
        log = os.path.join(self.tmp.name, "sync.log")
        set_running = lambda v: open(flag, "w").write(v)  # noqa: E731
        set_running("Antigravity IDE")
        env = dict(self.env, CHAT_LABEL_IDE_RUNNING_FILE=flag, CHAT_LABEL_TAB_SYNC_LOG=log, CHAT_LABEL_TAB_SYNC_SETTLE="3")
        subprocess.run([sys.executable, TOOL, "--tunggu"], capture_output=True, text=True, env=env, timeout=10)
        pid = int(open(log).read().split("(pid ")[1].split(")")[0])
        second = subprocess.run([sys.executable, TOOL, "--tunggu"], capture_output=True, text=True, env=env, timeout=10)
        self.assertIn("penunggu sudah berjalan", second.stdout)
        self.assertEqual(open(log).read().count("menunggu Antigravity IDE ditutup"), 1)
        time.sleep(1.5)
        set_running("")
        time.sleep(1.5)
        set_running("Antigravity IDE")
        for _ in range(20):
            if "terlalu cepat" in open(log).read():
                break
            time.sleep(0.5)
        self.assertIn("dibuka semula terlalu cepat", open(log).read())
        self.assertEqual(self.value(), self.before)
        os.kill(pid, 0)
        set_running("")
        for _ in range(30):
            if "Label tab siap" in open(log).read():
                break
            time.sleep(0.5)
        self.assertIn("Label tab siap. Boleh buka semula Antigravity IDE.", open(log).read())
        self.assertEqual(self.titles()[0], "98C - Blok funnel data t…")
        time.sleep(2)
        with self.assertRaises(OSError):
            os.kill(pid, 0)

    def test_apply_writes_queued_relabels_of_closed_chats_into_newest_manifest(self):
        today = datetime.date.today().isoformat()
        with open(os.path.join(self.tmp.name, "chat-label-sessions.json"), "w") as fh:
            json.dump({"lama": {"label": "122C", "source": "relabel", "title": "122C. Blok funnel data tab draf", "date": today},
                       "betul": {"label": "122A", "source": "relabel", "title": "122A. Masih terbuka", "date": today}}, fh)
        with open(os.path.join(self.registry, "1.json"), "w") as fh:
            json.dump({"pid": os.getpid(), "sessionId": "betul"}, fh)
        semula = os.path.join(self.env["CHAT_LABEL_BACKUP_DIR"], "chat-label-semula-2026-10-31-090416")
        os.makedirs(semula)
        with open(os.path.join(semula, "relabel-manifest.json"), "w") as fh:
            json.dump([{"path": "/x.jsonl", "line": "{}"}], fh)
        dry = self.run_tool()
        self.assertIn("relabel beratur (ditulis dengan --apply): 1", dry.stdout)
        r = self.run_tool("--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("relabel beratur ditulis: 1", r.stdout)
        self.assertEqual(self.titles()[0], "122C. Blok funnel data t…")
        self.assertEqual(self.titles()[2], "122A - Ada apa2 yg belum…")
        sessions = json.load(open(os.path.join(self.tmp.name, "chat-label-sessions.json")))
        self.assertEqual(sessions["lama"]["source"], "ai")
        self.assertEqual(sessions["betul"]["source"], "relabel")
        manifest = json.load(open(os.path.join(semula, "relabel-manifest.json")))
        self.assertEqual(len(manifest), 2)
        self.assertTrue(manifest[1]["path"].endswith("lama.jsonl"))
        self.assertIn("122C. Blok funnel data tab draf", manifest[1]["line"])
        self.assertNotIn("relabel beratur ditulis", self.run_tool("--apply").stdout)


if __name__ == "__main__":
    unittest.main(verbosity=1)
