"""python3 tests/test_chat_label.py"""
import csv
import datetime
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "hooks", "chat_label.py")
HOOK_CMD = [sys.executable, HOOK, "--plugin"]
BACKFILL = os.path.join(ROOT, "scripts", "chat-label-backfill.py")
spec = importlib.util.spec_from_file_location("chat_label", HOOK)
cl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cl)


def write_jsonl(path, rows):
    with open(path, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def injected(label, ts="2026-10-30T02:00:00.000Z"):
    return {"type": "attachment", "timestamp": ts, "attachment": {"type": "hook_additional_context",
            "content": ["CHAT LABEL untuk sesi ini = %s. WAJIB: mulakan sesi" % label]}}


def manifest_in(out):
    return out.strip().splitlines()[-1].split("manifest: ")[1]


def user(text, ts="2026-10-30T02:00:05.000Z"):
    return {"type": "user", "timestamp": ts, "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        os.environ["CHAT_LABEL_STATE_DIR"] = os.path.join(self.dir, "state")
        os.environ["CHAT_LABEL_TODAY"] = "2026-10-30"
        os.environ["CHAT_LABEL_BACKUP_DIR"] = os.path.join(self.dir, "backups")
        os.environ["CHAT_LABEL_AI_WAIT"] = "0"
        os.environ["CHAT_LABEL_FOLLOW"] = "0"
        os.environ["CHAT_LABEL_USER_SETTINGS"] = os.path.join(self.dir, "settings.json")
        os.makedirs(os.environ["CHAT_LABEL_STATE_DIR"])
        self.repo = self.make_repo("projek", "2026-07-02")

    def tearDown(self):
        self.tmp.cleanup()

    def make_repo(self, name, first_commit):
        path = os.path.realpath(os.path.join(self.dir, name))
        os.makedirs(path)
        env = dict(os.environ, GIT_AUTHOR_DATE=first_commit + "T10:00:00", GIT_COMMITTER_DATE=first_commit + "T10:00:00")
        for cmd in (["init", "-q"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "a"]):
            subprocess.run(["git", "-C", path, *cmd], check=True, env=env)
        return path

    def run_hook(self, inp):
        r = subprocess.run(HOOK_CMD, input=json.dumps(inp), capture_output=True, text=True, env=os.environ)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")
        return json.loads(r.stdout)["hookSpecificOutput"] if r.stdout.strip() else None

    def prompt(self, sid, text, transcript=None, cwd=None):
        return self.run_hook({"hook_event_name": "UserPromptSubmit", "session_id": sid, "prompt": text,
                              "cwd": cwd or self.repo,
                              "transcript_path": transcript or os.path.join(self.dir, sid + ".jsonl")})

    def projects(self):
        with open(os.path.join(os.environ["CHAT_LABEL_STATE_DIR"], "chat-label-projects.json")) as fh:
            return json.load(fh)

    def label(self, sid, text, cwd=None):
        out = self.prompt(sid, text, cwd=cwd)
        self.assertNotIn("sessionTitle", out)
        return re.search(r"= (\w+)\.", out["additionalContext"]).group(1)

    def set_state(self, last_date, last_index):
        with open(os.path.join(os.environ["CHAT_LABEL_STATE_DIR"], "chat-label-projects.json"), "w") as fh:
            json.dump({self.repo: {"anchor": "2026-07-02", "from": "git", "last_date": last_date,
                                   "last_index": last_index}}, fh)


class Pure(unittest.TestCase):
    def test_letters_continue_past_z(self):
        self.assertEqual([cl.letters(n) for n in (1, 26, 27, 32, 52, 53)], ["A", "Z", "AA", "AF", "AZ", "BA"])

    def test_normalize_old_overflow_numbers(self):
        self.assertEqual(cl.normalize("12132", [121, 120, 122]), "121AF")
        self.assertEqual(cl.normalize("11027", [110]), "110AA")
        self.assertEqual(cl.normalize("110A", [5]), "110A")
        self.assertIsNone(cl.normalize("999", [121]))

    def test_snippet_drops_ide_noise_and_truncates(self):
        p = "<ide_selection>const x = 1</ide_selection>[Image #1] boleh labelkan chat mcm ni? sebab sy dah banyak chat tak label dah susah nak cari"
        s = cl.snippet(p)
        self.assertTrue(s.startswith("boleh labelkan chat mcm ni?"))
        self.assertTrue(s.endswith("…"))
        self.assertLessEqual(len(s), 51)
        self.assertNotIn("const", s)
        self.assertEqual(cl.snippet("  "), "Chat baru")


class Prompt(Base):
    def test_first_prompt_labels_but_leaves_title_for_claude_code(self):
        self.set_state("2026-10-30", 33)
        a = self.prompt("s1", "Tambah butang eksport CSV")
        self.assertEqual(set(a), {"hookEventName", "additionalContext"})
        self.assertIn("CHAT LABEL untuk sesi ini = 121AH.", a["additionalContext"])
        self.assertNotIn("KEYWORD", a["additionalContext"])
        self.assertEqual(self.label("s2", "Lain"), "121AI")

    def test_new_day_starts_at_a(self):
        self.set_state("2026-10-29", 40)
        self.assertEqual(self.label("s1", "x"), "121A")

    def test_second_prompt_puts_label_before_ai_title(self):
        t = os.path.join(self.dir, "s1.jsonl")
        self.prompt("s1", "boleh tolong", t)
        write_jsonl(t, [user("boleh tolong"), {"type": "ai-title", "aiTitle": "Eksport pesanan CSV", "sessionId": "s1"}])
        out = self.prompt("s1", "lagi", t)
        self.assertEqual(out, {"hookEventName": "UserPromptSubmit", "sessionTitle": "121A. Eksport pesanan CSV"})
        self.assertIsNone(self.prompt("s1", "lagi", t))

    def test_no_ai_title_falls_back_to_first_prompt_then_swaps_later(self):
        t = os.path.join(self.dir, "s1.jsonl")
        self.prompt("s1", "boleh tolong", t)
        self.assertEqual(self.prompt("s1", "lagi", t)["sessionTitle"], "121A. boleh tolong")
        write_jsonl(t, [user("boleh tolong"),
                        {"type": "custom-title", "customTitle": "121A. boleh tolong", "sessionId": "s1"},
                        {"type": "ai-title", "aiTitle": "Eksport pesanan CSV", "sessionId": "s1"}])
        self.assertEqual(self.prompt("s1", "lagi", t)["sessionTitle"], "121A. Eksport pesanan CSV")

    def test_user_rename_is_never_overwritten(self):
        t = os.path.join(self.dir, "s1.jsonl")
        self.prompt("s1", "a", t)
        write_jsonl(t, [{"type": "custom-title", "customTitle": "TBR - nama saya", "sessionId": "s1"},
                        {"type": "ai-title", "aiTitle": "Tajuk AI", "sessionId": "s1"}])
        self.assertIsNone(self.prompt("s1", "b", t))
        self.assertIsNone(self.prompt("s1", "c", t))

    def test_already_named_chat_is_left_alone_and_uses_no_letter(self):
        self.set_state("2026-10-30", 5)
        t = os.path.join(self.dir, "s1.jsonl")
        write_jsonl(t, [injected("120B"), {"type": "custom-title", "customTitle": "20A. Override AI", "sessionId": "s1"}])
        self.assertIsNone(self.prompt("s1", "x", t))
        self.assertEqual(self.label("s2", "y"), "121F")

    def test_open_chat_keeps_label_old_hook_gave_it(self):
        t = os.path.join(self.dir, "s1.jsonl")
        write_jsonl(t, [injected("12132"), user("hai"), {"type": "ai-title", "aiTitle": "Pelabelan chat", "sessionId": "s1"}])
        out = self.prompt("s1", "x", t)
        self.assertEqual(out["sessionTitle"], "121AF. Pelabelan chat")
        self.assertIn("= 121AF.", out["additionalContext"])

    def test_resume_reinjects_label_startup_does_not(self):
        self.prompt("s1", "x")
        start = {"hook_event_name": "SessionStart", "session_id": "s1", "transcript_path": "/nope"}
        self.assertIsNone(self.run_hook(dict(start, source="startup")))
        out = self.run_hook(dict(start, source="resume"))
        self.assertIn("CHAT LABEL untuk sesi ini = 121A.", out["additionalContext"])
        self.assertNotIn("sessionTitle", out)

    def test_ide_first_prompt_waits_for_claude_codes_ai_title(self):
        os.environ["CHAT_LABEL_AI_WAIT"] = "3.5"
        t = os.path.join(self.dir, "s1.jsonl")
        later = threading.Timer(0.6, write_jsonl, [t, [user("boleh tolong"),
                                {"type": "ai-title", "aiTitle": "Eksport pesanan CSV", "sessionId": "s1"}]])
        later.start()
        start = time.time()
        out = self.prompt("s1", "boleh tolong", t)
        later.join()
        self.assertLess(time.time() - start, 3)
        self.assertEqual(out["sessionTitle"], "121A. Eksport pesanan CSV")
        self.assertIn("= 121A.", out["additionalContext"])
        with open(t, "a") as fh:
            fh.write(json.dumps({"type": "custom-title", "customTitle": "121A. Eksport pesanan CSV"}) + "\n")
        self.assertIsNone(self.prompt("s1", "lagi", t))

    def test_ide_default_wait_fits_inside_the_5s_hook_timeout(self):
        os.environ.pop("CHAT_LABEL_AI_WAIT")
        os.environ["CLAUDE_CODE_ENTRYPOINT"] = "claude-vscode"
        self.addCleanup(os.environ.pop, "CLAUDE_CODE_ENTRYPOINT")
        start = time.time()
        out = self.prompt("s1", "boleh tolong")
        self.assertLess(time.time() - start, 4.5)
        self.assertEqual(out["sessionTitle"], "121A. boleh tolong")
        os.environ["CLAUDE_CODE_ENTRYPOINT"] = "sdk-cli"
        start = time.time()
        self.assertEqual(self.label("s2", "x"), "121B")
        self.assertLess(time.time() - start, 1)

    def test_ide_no_ai_title_in_time_uses_first_prompt(self):
        os.environ["CHAT_LABEL_AI_WAIT"] = "0.4"
        out = self.prompt("s1", "boleh tolong")
        self.assertEqual(out["sessionTitle"], "121A. boleh tolong")

    def test_chat_relabelled_while_open_gets_new_title_and_label_on_next_prompt(self):
        t = os.path.join(self.dir, "s1.jsonl")
        write_jsonl(t, [user("x"), {"type": "custom-title", "customTitle": "TBR - nota", "sessionId": "s1"},
                        {"type": "ai-title", "aiTitle": "Isu emel", "sessionId": "s1"}])
        state = os.path.join(os.environ["CHAT_LABEL_STATE_DIR"], "chat-label-sessions.json")
        with open(state, "w") as fh:
            json.dump({"s1": {"label": "120C", "source": "relabel", "title": "120C. Isu emel", "date": "2026-10-30"}}, fh)
        out = self.prompt("s1", "lagi", t)
        self.assertEqual(out["sessionTitle"], "120C. Isu emel")
        self.assertIn("= 120C.", out["additionalContext"])
        with open(t, "a") as fh:
            fh.write(json.dumps({"type": "custom-title", "customTitle": "120C. Isu emel"}) + "\n")
        self.assertIsNone(self.prompt("s1", "lagi", t))

    def test_waiting_chat_does_not_block_another_chat(self):
        os.environ["CHAT_LABEL_AI_WAIT"] = "3"
        slow = subprocess.Popen(HOOK_CMD, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=os.environ)
        slow.stdin.write(json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": "a",
                                     "cwd": self.repo, "transcript_path": os.path.join(self.dir, "s1.jsonl")}))
        slow.stdin.close()
        time.sleep(0.5)
        os.environ["CHAT_LABEL_AI_WAIT"] = "0"
        start = time.time()
        self.assertEqual(self.label("s2", "b"), "121B")
        self.assertLess(time.time() - start, 1.5)
        self.assertEqual(json.loads(slow.stdout.read())["hookSpecificOutput"]["sessionTitle"], "121A. a")
        slow.wait()

    def test_resume_or_compact_settles_a_waiting_title(self):
        t = os.path.join(self.dir, "s1.jsonl")
        self.prompt("s1", "satu prompt panjang", t)
        write_jsonl(t, [user("satu prompt panjang"), {"type": "ai-title", "aiTitle": "Bina modul", "sessionId": "s1"}])
        out = self.run_hook({"hook_event_name": "SessionStart", "session_id": "s1", "source": "compact",
                             "transcript_path": t})
        self.assertEqual(out["sessionTitle"], "121A. Bina modul")
        self.assertIn("= 121A.", out["additionalContext"])
        self.assertEqual(self.prompt("s1", "lagi", t)["sessionTitle"], "121A. Bina modul")
        with open(t, "a") as fh:
            fh.write(json.dumps({"type": "custom-title", "customTitle": "121A. Bina modul", "sessionId": "s1"}) + "\n")
        self.assertIsNone(self.prompt("s1", "lagi", t))

    def test_title_applied_at_resume_is_recognised_not_taken_for_a_rename(self):
        t = os.path.join(self.dir, "s1.jsonl")
        self.prompt("s1", "x", t)
        write_jsonl(t, [user("x"), {"type": "ai-title", "aiTitle": "Bina modul", "sessionId": "s1"},
                        {"type": "custom-title", "customTitle": "121A. Bina modul", "sessionId": "s1"}])
        self.assertIsNone(self.prompt("s1", "lagi", t))
        state = json.load(open(os.path.join(os.environ["CHAT_LABEL_STATE_DIR"], "chat-label-sessions.json")))
        self.assertEqual((state["s1"]["source"], state["s1"]["title"]), ("ai", "121A. Bina modul"))

    def test_user_rename_while_waiting_is_kept(self):
        t = os.path.join(self.dir, "s1.jsonl")
        self.prompt("s1", "a", t)
        write_jsonl(t, [{"type": "custom-title", "customTitle": "Nama sendiri", "sessionId": "s1"},
                        {"type": "ai-title", "aiTitle": "Tajuk AI", "sessionId": "s1"}])
        out = self.run_hook({"hook_event_name": "SessionStart", "session_id": "s1", "source": "resume",
                             "transcript_path": t})
        self.assertNotIn("sessionTitle", out)
        self.assertIsNone(self.prompt("s1", "b", t))

    def test_each_project_counts_its_own_day_and_letters(self):
        hr = self.make_repo("hr", "2026-09-11")
        self.assertEqual(self.label("s1", "x"), "121A")
        self.assertEqual(self.label("s2", "y", cwd=hr), "50A")
        self.assertEqual(self.label("s3", "z"), "121B")
        self.assertEqual(self.label("s4", "w", cwd=hr), "50B")
        self.assertEqual(self.projects()[hr]["from"], "git")

    def test_worktree_and_subfolder_count_as_parent_project(self):
        sub = os.path.join(self.repo, "src", "lib")
        os.makedirs(sub)
        wt = os.path.join(self.repo, "worktrees", "feat-x")
        subprocess.run(["git", "-C", self.repo, "worktree", "add", "-q", "-b", "feat-x", wt], check=True)
        self.assertEqual(self.label("s1", "a"), "121A")
        self.assertEqual(self.label("s2", "b", cwd=wt), "121B")
        self.assertEqual(self.label("s3", "c", cwd=sub), "121C")
        self.assertEqual(list(self.projects()), [self.repo])

    def test_folder_without_git_counts_from_the_day_it_was_made(self):
        plain = os.path.join(self.dir, "nota")
        os.makedirs(plain)
        os.environ["CHAT_LABEL_TODAY"] = (datetime.date.today() + datetime.timedelta(days=9)).isoformat()
        self.assertEqual(self.label("s1", "x", cwd=plain), "10A")
        self.assertEqual(self.projects()[os.path.realpath(plain)]["from"], "folder")

    def test_day_one_is_frozen_and_hand_editable(self):
        self.prompt("s1", "x")
        reg = self.projects()
        reg[self.repo]["anchor"] = "2026-10-21"
        with open(os.path.join(os.environ["CHAT_LABEL_STATE_DIR"], "chat-label-projects.json"), "w") as fh:
            json.dump(reg, fh)
        self.assertEqual(self.label("s2", "y"), "10B")

    def test_broken_input_never_blocks(self):
        r = subprocess.run(HOOK_CMD, input="not json", capture_output=True, text=True, env=os.environ)
        self.assertEqual((r.returncode, r.stdout), (0, ""))


def titles_in(path):
    with open(path, "rb") as fh:
        return cl.customs(fh.read())


class FakeClaudeCode(threading.Thread):
    """Re-appends session metadata the way Claude Code does: each time the follower writes `want`, the
    next re-append writes back whatever `replies` says (want = adopted, old = written from memory)."""

    def __init__(self, path, sid, want, replies):
        super().__init__(daemon=True)
        self.path, self.sid, self.want, self.replies = path, sid, want, list(replies)
        self.answered = 0

    def run(self):
        mine, end = 0, time.time() + 8
        while self.replies and time.time() < end:
            if titles_in(self.path).count(self.want) - mine > self.answered:
                reply = self.replies.pop(0)
                with open(self.path, "a") as fh:
                    fh.write(json.dumps({"type": "custom-title", "customTitle": reply, "sessionId": self.sid}) + "\n")
                mine += reply == self.want
                self.answered += 1
            time.sleep(0.02)


class Follow(Base):
    OLD = "121A. help export orders"
    WANT = "121A. Export orders to CSV"

    def setUp(self):
        super().setUp()
        os.environ["CHAT_LABEL_FOLLOW"] = "1"
        os.environ["CHAT_LABEL_FOLLOW_POLL"] = "0.05"
        os.environ["CHAT_LABEL_FOLLOW_MAX"] = "6"
        self.addCleanup(os.environ.pop, "CHAT_LABEL_FOLLOW_POLL")
        self.addCleanup(os.environ.pop, "CHAT_LABEL_FOLLOW_MAX")
        self.t = os.path.join(self.dir, "s1.jsonl")

    def materialize(self):
        """What the IDE writes once the first prompt's hook has returned."""
        write_jsonl(self.t, [{"type": "custom-title", "customTitle": self.OLD, "sessionId": "s1"},
                             {"type": "ai-title", "aiTitle": "Export orders to CSV", "sessionId": "s1"},
                             user("help export orders")])

    def ide_first_prompt(self):
        os.environ["CHAT_LABEL_AI_WAIT"] = "0.3"
        out = self.prompt("s1", "help export orders", self.t)
        self.assertEqual(out["sessionTitle"], self.OLD)

    def entry(self):
        with open(os.path.join(os.environ["CHAT_LABEL_STATE_DIR"], "chat-label-sessions.json")) as fh:
            return json.load(fh)["s1"]

    def wait_log(self, needle, secs=7):
        path = os.path.join(os.environ["CHAT_LABEL_STATE_DIR"], "chat-label-follow.log")
        end = time.time() + secs
        while time.time() < end:
            try:
                with open(path) as fh:
                    if needle in fh.read():
                        return
            except OSError:
                pass
            time.sleep(0.05)
        self.fail("log has no %r" % needle)

    def test_first_prompt_label_then_ai_title_once_claude_code_adopts_it(self):
        self.ide_first_prompt()
        self.materialize()
        FakeClaudeCode(self.t, "s1", self.WANT, [self.WANT]).start()
        self.wait_log("adopted after 1 write(s)")
        self.assertEqual(titles_in(self.t)[-1], self.WANT)
        self.assertEqual((self.entry()["source"], self.entry()["title"]), ("ai", self.WANT))
        self.assertIsNone(self.prompt("s1", "more", self.t))

    def test_writes_again_when_claude_code_puts_the_old_title_back(self):
        self.ide_first_prompt()
        self.materialize()
        FakeClaudeCode(self.t, "s1", self.WANT, [self.OLD, self.WANT]).start()
        self.wait_log("adopted after 2 write(s)")
        self.assertEqual(titles_in(self.t)[-1], self.WANT)

    def test_stops_at_a_rename_by_hand(self):
        self.ide_first_prompt()
        self.materialize()
        FakeClaudeCode(self.t, "s1", self.WANT, ["My own name"]).start()
        self.wait_log("stopped, another title: My own name")
        self.assertEqual(titles_in(self.t)[-1], "My own name")
        self.assertEqual(self.entry()["source"], "prompt")

    def test_no_ai_title_writes_nothing(self):
        self.ide_first_prompt()
        write_jsonl(self.t, [{"type": "custom-title", "customTitle": self.OLD, "sessionId": "s1"}, user("x")])
        time.sleep(0.5)
        self.assertEqual(titles_in(self.t), [self.OLD])

    def test_second_prompt_takes_the_followers_title_as_its_own(self):
        state = os.path.join(os.environ["CHAT_LABEL_STATE_DIR"], "chat-label-sessions.json")
        with open(state, "w") as fh:
            json.dump({"s1": {"label": "121A", "source": "prompt", "title": self.OLD, "date": "2026-10-30"}}, fh)
        self.materialize()
        with open(self.t, "a") as fh:
            fh.write(json.dumps({"type": "custom-title", "customTitle": self.WANT, "sessionId": "s1"}) + "\n")
        self.assertEqual(self.prompt("s1", "more", self.t)["sessionTitle"], self.WANT)
        self.assertEqual(self.entry()["source"], "ai")


class Guard(Base):
    """The plugin steps aside when the user's settings already run a hand-installed copy of the hook."""

    def write_settings(self, commands, name="settings.json"):
        path = os.path.join(self.dir, name)
        hooks = {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": c} for c in commands]}]}
        with open(path, "w") as fh:
            json.dump({"hooks": hooks}, fh)
        return path

    def test_hand_installed_hook_silences_the_plugin(self):
        for cmd in ('bash "$HOME/.claude/hooks/chat-label.sh"', "python3 ~/bin/chat_label.py"):
            self.write_settings(["afplay /System/Library/Sounds/Glass.aiff", cmd])
            self.assertIsNone(self.prompt("s-" + cmd[:4], "x"))
        self.assertFalse(os.path.exists(os.path.join(os.environ["CHAT_LABEL_STATE_DIR"], "chat-label-projects.json")))

    def test_hand_installed_copy_itself_still_labels(self):
        self.write_settings(['bash "$HOME/.claude/hooks/chat-label.sh"'])
        r = subprocess.run([sys.executable, HOOK], capture_output=True, text=True, env=os.environ,
                           input=json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": "x",
                                             "cwd": self.repo, "transcript_path": os.path.join(self.dir, "s1.jsonl")}))
        self.assertIn("= 121A.", r.stdout)

    def test_settings_local_json_counts_too(self):
        local = self.write_settings(['bash "$HOME/.claude/hooks/chat-label.sh"'], "settings.local.json")
        os.environ["CHAT_LABEL_USER_SETTINGS"] = os.pathsep.join([os.path.join(self.dir, "settings.json"), local])
        self.assertIsNone(self.prompt("s1", "x"))

    def test_plugin_runs_when_settings_have_no_copy(self):
        self.write_settings(["afplay /System/Library/Sounds/Glass.aiff",
                             'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/chat_label.py" --plugin',
                             "python3 ~/.claude/tools/chat-label-backfill.py --semula"])
        self.assertEqual(self.label("s1", "x"), "121A")

    def test_broken_or_odd_settings_never_block(self):
        for body in ("{not json", "[]", '{"hooks": {"Stop": null}}', '{"hooks": {"Stop": [{"hooks": [null]}]}}'):
            with open(os.path.join(self.dir, "settings.json"), "w") as fh:
                fh.write(body)
            r = subprocess.run(HOOK_CMD, capture_output=True, text=True, env=os.environ,
                               input=json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": body, "prompt": "x",
                                                 "cwd": self.repo, "transcript_path": "/nope"}))
            self.assertEqual((r.returncode, r.stderr), (0, ""), body)
            self.assertIn("CHAT LABEL untuk sesi ini", r.stdout, body)

    def test_reads_settings_from_the_claude_config_dir(self):
        os.environ.pop("CHAT_LABEL_USER_SETTINGS")
        cfg = os.path.join(self.dir, "cfg")
        os.makedirs(cfg)
        with open(os.path.join(cfg, "settings.json"), "w") as fh:
            json.dump({"hooks": {"SessionStart": [{"hooks": [{"command": "bash ~/.claude/hooks/chat-label.sh"}]}]}}, fh)
        os.environ["CLAUDE_CONFIG_DIR"] = cfg
        self.addCleanup(os.environ.pop, "CLAUDE_CONFIG_DIR")
        self.assertIsNone(self.prompt("s1", "x"))


class BackfillBase(Base):
    def setUp(self):
        super().setUp()
        self.proj = os.path.join(self.dir, "proj")
        os.makedirs(self.proj)
        reg = os.path.join(self.dir, "registry")
        os.makedirs(reg)
        os.environ["CHAT_LABEL_SESSIONS_REGISTRY"] = reg
        with open(os.path.join(reg, "1.json"), "w") as fh:
            json.dump({"pid": os.getpid(), "sessionId": "live"}, fh)
        write_jsonl(os.path.join(self.proj, "old.jsonl"), [injected("11027", "2026-10-19T03:00:00.000Z"), user("x"),
                    {"type": "ai-title", "aiTitle": "Isu emel", "sessionId": "old"}])
        write_jsonl(os.path.join(self.proj, "snip.jsonl"), [injected("110B", "2026-10-19T04:00:00.000Z"), user("boleh semak order 123")])
        write_jsonl(os.path.join(self.proj, "named.jsonl"), [injected("110C"), {"type": "custom-title", "customTitle": "31A - x"}])
        write_jsonl(os.path.join(self.proj, "live.jsonl"), [injected("121D"), {"type": "ai-title", "aiTitle": "Y"}])
        write_jsonl(os.path.join(self.proj, "empty.jsonl"), [injected("110E")])
        self.addCleanup(os.environ.pop, "CHAT_LABEL_SESSIONS_REGISTRY")

    def backfill(self, *args):
        r = subprocess.run([sys.executable, BACKFILL, *args], capture_output=True, text=True, env=os.environ)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def last_custom(self, name):
        return cl.scan(os.path.join(self.proj, name + ".jsonl"))["custom"]


class Backfill(BackfillBase):
    def test_dry_run_writes_nothing(self):
        out = self.backfill(self.proj)
        self.assertIn("akan dilabel: 2", out)
        self.assertIn("dinamakan 1, terbuka 1, tiada label asal 0, kosong 1", out)
        self.assertIsNone(self.last_custom("old"))

    def test_apply_then_undo(self):
        before = open(os.path.join(self.proj, "old.jsonl"), "rb").read()
        out = self.backfill(self.proj, "--apply")
        self.assertTrue(manifest_in(out).startswith(self.dir))
        manifest = manifest_in(out)
        self.assertEqual(self.last_custom("old"), "110AA. Isu emel")
        self.assertEqual(self.last_custom("snip"), "110B. boleh semak order 123")
        self.assertEqual(self.last_custom("named"), "31A - x")
        self.assertIsNone(self.last_custom("live"))
        sessions = json.load(open(os.path.join(os.environ["CHAT_LABEL_STATE_DIR"], "chat-label-sessions.json")))
        self.assertEqual(sessions["snip"]["source"], "prompt")
        self.assertIn("dibuang 2 baris", self.backfill("--undo", manifest))
        self.assertEqual(open(os.path.join(self.proj, "old.jsonl"), "rb").read(), before)



class TanpaLabel(BackfillBase):
    def setUp(self):
        super().setUp()
        self.chats = os.path.join(self.dir, "projects", "p")
        os.makedirs(self.chats)
        os.environ["CHAT_LABEL_PROJECTS_DIR"] = os.path.dirname(self.chats)
        os.environ["CHAT_LABEL_TEST_CWD"] = "/ujian-sementara/"
        self.addCleanup(os.environ.pop, "CHAT_LABEL_PROJECTS_DIR")
        self.addCleanup(os.environ.pop, "CHAT_LABEL_TEST_CWD")
        self.hr = self.make_repo("hr", "2026-09-11")
        self.late = self.make_repo("lewat", "2026-10-25")
        day = "2026-10-30T0%d:00:00.000Z"
        self.chat("a", self.repo, day % 1, [injected("121D", day % 1), {"type": "ai-title", "aiTitle": "Penjualan"}])
        self.chat("b", self.repo, day % 2, [{"type": "ai-title", "aiTitle": "Isu kerap"}])
        self.chat("c", self.repo, day % 3, [{"type": "custom-title", "customTitle": "121A - lama"}])
        self.chat("d", self.repo, day % 4, [{"type": "custom-title", "customTitle": "TBR - nota"}])
        self.chat("e", self.repo, day % 5, [{"type": "custom-title", "customTitle": "31L V - toot"}])
        self.chat("f", self.repo, day % 6, [{"type": "custom-title", "customTitle": "Publish Cloudflare"}])
        self.chat("g", self.hr, day % 7, [injected("121F", day % 7), {"type": "ai-title", "aiTitle": "Kehadiran"}])
        self.chat("h", "/ujian-sementara/x", day % 8, [])
        self.chat("i", self.late, "2026-10-20T02:00:00.000Z", [])
        write_jsonl(os.path.join(self.chats, "kosong.jsonl"), [injected("121Z")])

    def chat(self, sid, cwd, ts, rows):
        first = dict(user("mula " + sid, ts), cwd=cwd)
        write_jsonl(os.path.join(self.chats, sid + ".jsonl"), [first] + rows)

    def title(self, sid):
        return cl.scan(os.path.join(self.chats, sid + ".jsonl"))["custom"]

    def test_dry_run_lists_only_unlabelled_and_writes_nothing(self):
        out = self.backfill("--tanpa-label")
        self.assertIn("akan dilabel: 5 · langkau: berlabel 3, terbuka 0, kosong 1, ujian 1", out)
        self.assertIsNone(self.title("a"))
        self.assertFalse(os.path.exists(os.path.join(os.environ["CHAT_LABEL_STATE_DIR"], "chat-label-projects.json")))

    def test_apply_labels_per_project_keeps_the_rest_then_undo(self):
        before = open(os.path.join(self.chats, "f.jsonl"), "rb").read()
        manifest = manifest_in(self.backfill("--tanpa-label", "--apply"))
        self.assertEqual(self.title("a"), "121D. Penjualan")
        self.assertEqual(self.title("b"), "121B. Isu kerap")
        self.assertEqual(self.title("f"), "121C. Publish Cloudflare")
        self.assertEqual(self.title("g"), "50A. Kehadiran")
        self.assertEqual(self.title("i"), "1A. mula i")
        self.assertEqual([self.title(x) for x in "cde"], ["121A - lama", "TBR - nota", "31L V - toot"])
        self.assertIsNone(self.title("h"))
        reg = self.projects()
        self.assertEqual((reg[self.repo]["anchor"], reg[self.repo]["last_index"]), ("2026-07-02", 4))
        self.assertEqual((reg[self.late]["anchor"], reg[self.late]["from"]), ("2026-10-20", "chat"))
        sessions = json.load(open(os.path.join(os.environ["CHAT_LABEL_STATE_DIR"], "chat-label-sessions.json")))
        self.assertEqual(sessions["f"]["source"], "user")
        self.assertEqual(self.label("baru", "x"), "121E")
        self.assertIn("dibuang 5 baris", self.backfill("--undo", manifest))
        self.assertEqual(open(os.path.join(self.chats, "f.jsonl"), "rb").read(), before)

class Semula(TanpaLabel):
    def test_dry_run_relabels_everything_and_writes_nothing(self):
        out = self.backfill("--semula")
        self.assertIn("akan dilabel semula: 8 (terbuka 0) · langkau: kosong 1, ujian 1, sama 0", out)
        self.assertEqual(self.title("c"), "121A - lama")

    def test_apply_renumbers_per_project_uses_ai_title_converts_markers_then_undo(self):
        write_jsonl(os.path.join(self.chats, "live.jsonl"), [dict(user("buka", "2026-10-30T09:30:00.000Z"), cwd=self.repo)])
        before = open(os.path.join(self.chats, "d.jsonl"), "rb").read()
        out = self.backfill("--semula", "--apply")
        manifest = manifest_in(out)
        self.assertEqual([self.title(x) for x in "abcdef"], ["121A. Penjualan", "121B. Isu kerap", "121C. lama",
                                                             "121D. nota", "121E. toot", "121F. Publish Cloudflare"])
        self.assertEqual(self.title("g"), "50A. Kehadiran")
        self.assertEqual(self.title("i"), "1A. mula i")
        self.assertIsNone(self.title("h"))
        self.assertIsNone(self.title("live"))
        sessions = json.load(open(os.path.join(os.environ["CHAT_LABEL_STATE_DIR"], "chat-label-sessions.json")))
        self.assertEqual((sessions["live"]["source"], sessions["live"]["title"]), ("relabel", "121G. buka"))
        self.assertEqual(sessions["d"]["source"], "prompt")
        rows = list(csv.reader(open(os.path.join(os.path.dirname(manifest), "relabel-map.csv"))))
        self.assertEqual(rows[0][:5], ["session", "projek", "tarikh", "label_lama", "label_baru"])
        self.assertIn(["c", "projek", "2026-10-30", "121A", "121C", "121A - lama", "121C. lama", ""], rows)
        self.assertEqual(self.projects()[self.repo]["last_index"], 7)
        self.assertIn("akan dilabel semula: 1 (terbuka 1)", self.backfill("--semula"))
        self.backfill("--undo", manifest)
        self.assertEqual(open(os.path.join(self.chats, "d.jsonl"), "rb").read(), before)
        self.assertNotIn("live", json.load(open(os.path.join(os.environ["CHAT_LABEL_STATE_DIR"], "chat-label-sessions.json"))))

    def test_today_counter_continues_after_relabel(self):
        os.environ["CHAT_LABEL_TODAY"] = "2026-10-30"
        self.backfill("--semula", "--apply")
        self.assertEqual(self.projects()[self.repo]["last_index"], 6)
        self.assertEqual(self.label("baru", "x"), "121G")


class BackfillPluginArgs(TanpaLabel):
    def test_ringkas_caps_examples_and_undo_works_after_other_flags(self):
        for i in range(12):
            self.chat("x%02d" % i, self.repo, "2026-10-29T0%d:00:00.000Z" % (i % 10), [])
        out = self.backfill("--tanpa-label", "--ringkas")
        self.assertIn("… dan 7 lagi", out)
        self.assertEqual(len([ln for ln in out.splitlines() if ln[:8].strip() and "  " in ln and not ln.startswith(("…", "akan"))]), 10)
        manifest = manifest_in(self.backfill("--tanpa-label", "--ringkas", "--apply"))
        self.assertIn("dibuang 17 baris", self.backfill("--tanpa-label", "--undo", manifest))

    def test_undo_without_manifest_explains_and_writes_nothing(self):
        r = subprocess.run([sys.executable, BACKFILL, "--tanpa-label", "--undo"], capture_output=True, text=True, env=os.environ)
        self.assertEqual(r.returncode, 2)
        self.assertIn("--undo <manifest.json>", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=1)
