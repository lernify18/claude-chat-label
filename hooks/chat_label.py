#!/usr/bin/env python3
"""Chat label: names every Claude Code chat tab `<day><letter>. <title>`, e.g. `12A. Fix login bug`.

day    = full days since the project's Day 1 + 1. Day 1 = the repo's first commit, or the day the folder
         was made when it has none; frozen in chat-label-projects.json on first use (hand-editable).
letter = A..Z, AA, AB ... = which chat of that day it is in that project; restarts every new day.
A git worktree or any subfolder counts as its parent project.

UserPromptSubmit: the first prompt gives the chat its label. Any custom title stops Claude Code from
generating its own AI title, so in the IDE the hook first waits (lock released, CHAT_LABEL_AI_WAIT s,
default 3.5; the hook timeout is 5) for the AI title the extension requests at the same moment, then sets
`<label>. <AI title>` through hookSpecificOutput.sessionTitle (same as /rename); no AI title in time =
`<label>. <first prompt text>`. Outside the IDE nothing generates a title, so there is no wait: the next
prompt sets it. A title the user set by hand is never touched.
SessionStart (resume/compact): re-injects the label and, for a chat still waiting, sets the title.
A chat relabelled by chat-label-backfill.py --semula while it was open (source "relabel") gets its new
title on its next prompt, since Claude Code would rewrite an appended title from memory.

State lives in the Claude config dir ($CLAUDE_CONFIG_DIR or ~/.claude), so it survives plugin updates.
Test overrides: CHAT_LABEL_STATE_DIR (state, session map, lock), CHAT_LABEL_TODAY (YYYY-MM-DD).
"""
import datetime
import fcntl
import json
import os
import re
import subprocess
import sys
import time

ANCHOR_DEFAULT = "2026-07-02"
SEP = ". "
KEEP_DAYS = 90
HOME = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")

INJECTED = re.compile(rb"CHAT LABEL untuk sesi ini = ([0-9A-Z]+)")
LABEL_PREFIX = re.compile(r"^(\d+[A-Z]+)\b")
TS = re.compile(rb'"timestamp":\s*"([^"]+)"')
CWD = re.compile(rb'"cwd":\s*("(?:[^"\\]|\\.)*")')
USER_LINE = re.compile(rb'"type":\s*"user"')
WRAPPED = re.compile(
    r"<(ide_selection|ide_opened_file|pasted_content|system-reminder|command-[a-z-]+)\b[^>]*>.*?</\1>", re.S
)


def state_dir():
    return os.environ.get("CHAT_LABEL_STATE_DIR") or HOME


def paths():
    d = state_dir()
    return (os.path.join(d, "chat-label-state.txt"), os.path.join(d, "chat-label-sessions.json"),
            os.path.join(d, "chat-label.lock"))


def projects_path():
    return os.path.join(state_dir(), "chat-label-projects.json")


def git(cwd, *args):
    try:
        r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=3)
    except OSError:
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def project_root(cwd):
    common = git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common and os.path.basename(common) == ".git":
        return os.path.realpath(os.path.dirname(common))
    return os.path.realpath(git(cwd, "rev-parse", "--show-toplevel") or cwd)


def first_day(root):
    dates = (git(root, "log", "--max-parents=0", "--format=%as", "HEAD") or "").split()
    if dates:
        return min(dates), "git"
    st = os.stat(root)
    return datetime.date.fromtimestamp(getattr(st, "st_birthtime", st.st_mtime)).isoformat(), "folder"


def today():
    t = os.environ.get("CHAT_LABEL_TODAY")
    return datetime.date.fromisoformat(t) if t else datetime.date.today()


def letters(n):
    out = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def day_number(date, anchor):
    return max(1, (date - datetime.date.fromisoformat(anchor)).days + 1)


def normalize(label, days):
    """Old hook printed the 27th+ chat as a number ("12132" = hari 121, chat 32)."""
    if not label:
        return None
    if re.fullmatch(r"\d+[A-Z]+", label):
        return label
    if label.isdigit():
        for d in days:
            s = str(d)
            if label.startswith(s) and len(label) > len(s) and int(label[len(s):]) >= 1:
                return s + letters(int(label[len(s):]))
    return None


def scan(path):
    """Last custom/AI title, first injected label and first timestamp of a transcript."""
    t = {"custom": None, "ai": None, "injected": None, "first_ts": None, "first_prompt": None, "cwd": None}
    if not path or not os.path.isfile(path):
        return t
    with open(path, "rb") as fh:
        for line in fh:
            if t["first_ts"] is None:
                m = TS.search(line)
                if m:
                    t["first_ts"] = m.group(1).decode()
            if t["cwd"] is None:
                m = CWD.search(line)
                if m:
                    t["cwd"] = json.loads(m.group(1))
            if t["injected"] is None:
                m = INJECTED.search(line)
                if m:
                    t["injected"] = m.group(1).decode()
            if b'-title"' in line:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") == "custom-title" and d.get("customTitle"):
                    t["custom"] = d["customTitle"]
                elif d.get("type") == "ai-title" and d.get("aiTitle"):
                    t["ai"] = d["aiTitle"]
            elif t["first_prompt"] is None and USER_LINE.search(line):
                t["first_prompt"] = user_text(line)
    return t


def user_text(line):
    try:
        d = json.loads(line)
    except ValueError:
        return None
    if d.get("type") != "user" or d.get("isMeta"):
        return None
    c = (d.get("message") or {}).get("content")
    if isinstance(c, list):
        c = " ".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text")
    return c if isinstance(c, str) and c.strip() else None


def candidate_days(t, anchor):
    if t.get("first_ts"):
        try:
            utc = datetime.datetime.fromisoformat(t["first_ts"].replace("Z", "+00:00"))
            d = day_number(utc.astimezone().date(), anchor)
            return [d, d - 1, d + 1]
        except ValueError:
            pass
    return [day_number(today(), anchor)]


def snippet(prompt, limit=50):
    s = WRAPPED.sub(" ", prompt or "")
    s = re.sub(r"\[(Image|Pasted text)[^\]]*\]", " ", s)
    s = re.sub(r"[#*`>]+", " ", s)
    s = " ".join(s.split())
    if len(s) > limit:
        cut = s[:limit].rsplit(" ", 1)[0] or s[:limit]
        s = cut.rstrip(" ,.;:-") + "…"
    return s or "Chat baru"


def read_state(path):
    st = {"anchor": ANCHOR_DEFAULT, "last_date": "", "last_index": 0}
    if os.path.isfile(path):
        for line in open(path):
            k, _, v = line.strip().partition("=")
            if k == "anchor" and v:
                st["anchor"] = v
            elif k == "last_date":
                st["last_date"] = v
            elif k == "last_index" and v.isdigit():
                st["last_index"] = int(v)
    return st


def load_projects():
    try:
        with open(projects_path()) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def next_label(cwd):
    path = projects_path()
    projects = load_projects()
    root = project_root(cwd)
    p = projects.get(root)
    if p is None:
        anchor, source = first_day(root)
        p = projects[root] = {"anchor": anchor, "from": source, "last_date": "", "last_index": 0}
    now = today().isoformat()
    p["last_index"] = p["last_index"] + 1 if p["last_date"] == now else 1
    p["last_date"] = now
    save_json(path, projects)
    return "%d%s" % (day_number(today(), p["anchor"]), letters(p["last_index"]))


def load_sessions(path):
    try:
        data = json.load(open(path))
    except (OSError, ValueError):
        return {}
    floor = (today() - datetime.timedelta(days=KEEP_DAYS)).isoformat()
    return {k: v for k, v in data.items() if v.get("date", "") >= floor}


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=0)
    os.replace(tmp, path)


def remember(sessions, sid, label, source, title, first=None):
    sessions[sid] = {"label": label, "source": source, "title": title, "date": today().isoformat()}
    if first:
        sessions[sid]["first"] = first


def context(label):
    return ("CHAT LABEL untuk sesi ini = %s. Tab chat dinamakan automatik `%s. <tajuk asal>`. "
            "Kekalkan prefix %s bila merujuk kerja sesi ini." % (label, label, label))


def wanted(entry, t):
    return titled(entry["label"], t["ai"] or entry.get("first") or "Chat baru")


def settle(sessions, sid, entry, t):
    """A chat still waiting for its label on the tab. Returns the title to set, or None.
    Stays pending until that title is on disk: SessionStart's sessionTitle is not applied on every
    path (headless resume ignores it), so the next prompt sets it again."""
    title = wanted(entry, t)
    if t["custom"]:
        source = ("ai" if t["ai"] else "prompt") if t["custom"] == title else "user"
        remember(sessions, sid, entry["label"], source, t["custom"], entry.get("first"))
        return None
    return title


def titled(label, text):
    return "%s%s%s" % (label, SEP, text)


def label_from_transcript(t, anchor):
    if t["custom"]:
        m = LABEL_PREFIX.match(t["custom"])
        return m.group(1) if m else None
    return normalize(t["injected"], candidate_days(t, anchor))


def on_prompt(inp):
    sid = inp.get("session_id")
    if not sid:
        return None
    state_path, sessions_path, _ = paths()
    sessions = load_sessions(sessions_path)
    entry = sessions.get(sid)
    if entry and entry.get("source") not in ("prompt", "pending", "relabel"):
        return None
    t = scan(inp.get("transcript_path"))
    if entry is None:
        if t["custom"]:
            m = LABEL_PREFIX.match(t["custom"])
            remember(sessions, sid, m.group(1) if m else None, "user", t["custom"])
            save_json(sessions_path, sessions)
            return None
        anchor = read_state(state_path)["anchor"]
        label = normalize(t["injected"], candidate_days(t, anchor)) or next_label(inp.get("cwd") or os.getcwd())
        out = {"hookEventName": "UserPromptSubmit", "additionalContext": context(label)}
        if t["ai"]:
            out["sessionTitle"] = titled(label, t["ai"])
            remember(sessions, sid, label, "ai", out["sessionTitle"])
        else:
            remember(sessions, sid, label, "pending", None, snippet(inp.get("prompt", "")))
        save_json(sessions_path, sessions)
        return {"hookSpecificOutput": out}
    if entry["source"] == "relabel":
        title = entry["title"]
        remember(sessions, sid, entry["label"], "ai" if t["ai"] else "prompt", title)
        save_json(sessions_path, sessions)
        return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "sessionTitle": title,
                                       "additionalContext": context(entry["label"])}}
    if entry["source"] == "pending":
        title = settle(sessions, sid, entry, t)
        if title:
            remember(sessions, sid, entry["label"], "ai" if t["ai"] else "prompt", title)
        save_json(sessions_path, sessions)
        return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "sessionTitle": title}} if title else None
    if t["custom"] and t["custom"] != entry["title"]:
        entry["source"] = "user"
        save_json(sessions_path, sessions)
        return None
    if t["ai"]:
        title = titled(entry["label"], t["ai"])
        remember(sessions, sid, entry["label"], "ai", title)
        save_json(sessions_path, sessions)
        return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "sessionTitle": title}}
    return None


def on_session_start(inp):
    if inp.get("source") in ("startup", "clear", None):
        return None
    state_path, sessions_path, _ = paths()
    sessions = load_sessions(sessions_path)
    sid = inp.get("session_id") or ""
    entry = sessions.get(sid)
    t = scan(inp.get("transcript_path"))
    out = {"hookEventName": "SessionStart"}
    if entry and entry.get("source") == "pending":
        title = settle(sessions, sid, entry, t)
        if title and t["ai"]:
            out["sessionTitle"] = title
        save_json(sessions_path, sessions)
    label = entry.get("label") if entry else None
    if not label:
        label = label_from_transcript(t, read_state(state_path)["anchor"])
    if not label:
        return None
    out["additionalContext"] = context(label)
    return {"hookSpecificOutput": out}


def ai_wait():
    env = os.environ.get("CHAT_LABEL_AI_WAIT")
    if env is not None:
        return float(env)
    return 3.5 if os.environ.get("CLAUDE_CODE_ENTRYPOINT") == "claude-vscode" else 0.0


def await_title(inp):
    """Called without the lock: wait for Claude Code's AI title, then settle the waiting chat."""
    deadline = time.time() + ai_wait()
    while time.time() < deadline and not scan(inp.get("transcript_path"))["ai"]:
        time.sleep(0.15)
    with open(paths()[2], "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        sessions_path = paths()[1]
        sessions = load_sessions(sessions_path)
        sid = inp.get("session_id")
        entry = sessions.get(sid)
        if not entry or entry.get("source") != "pending":
            return None
        t = scan(inp.get("transcript_path"))
        title = settle(sessions, sid, entry, t)
        if title:
            remember(sessions, sid, entry["label"], "ai" if t["ai"] else "prompt", title)
        save_json(sessions_path, sessions)
        return title


def main():
    try:
        inp = json.loads(sys.stdin.read() or "{}")
        os.makedirs(state_dir(), exist_ok=True)
        with open(paths()[2], "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if inp.get("hook_event_name") == "UserPromptSubmit":
                out = on_prompt(inp)
            else:
                out = on_session_start(inp)
        first = out and out["hookSpecificOutput"].get("additionalContext") and "sessionTitle" not in out["hookSpecificOutput"]
        if first and inp.get("hook_event_name") == "UserPromptSubmit" and ai_wait() > 0:
            title = await_title(inp)
            if title:
                out["hookSpecificOutput"]["sessionTitle"] = title
        if out:
            print(json.dumps(out, ensure_ascii=False))
    except Exception as exc:  # a broken label must never block a prompt
        print("chat_label: %s" % exc, file=sys.stderr)


if __name__ == "__main__":
    main()
