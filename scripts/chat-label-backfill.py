#!/usr/bin/env python3
"""Give old chats their tab label: appends one custom-title line per transcript (append-only, undoable).

  python3 chat-label-backfill.py --tanpa-label [--ringkas] [--apply]   # chats with no label yet, every project
  python3 chat-label-backfill.py --semula [--apply]                    # relabel EVERY chat, see below
  python3 chat-label-backfill.py --undo <manifest.json>                # remove exactly what an --apply wrote
  python3 chat-label-backfill.py <project-transcript-dir> [--apply]    # chats that got a label injected but no title

Without --apply nothing is written (dry run). Chats open in a running Claude process are never written.

--tanpa-label: across every project, only chats whose title has no label yet get one, in the per-project
scheme (day since the project's Day 1, letter in first-prompt order, skipping letters the project already
used that day). A title the user typed is kept after the label. Labelled chats (`12A. …`, `12A - …`,
`TBR - …`) are left as they are. Day 1 of each project not yet in chat-label-projects.json = the earlier of
its first commit (or folder date) and its first chat, and is written there so the hook carries on with the
same numbering. --ringkas prints at most 10 example rows.

--semula: every chat in every project, labelled or not, becomes `<project day><letter>. <title>`: letters
in first-prompt order within each project and day, title = Claude Code's AI title, else the current title
with its label stripped, else a snippet of the first prompt. Open chats are not written (Claude Code would
put its in-memory title back); they are queued in chat-label-sessions.json as source "relabel" and the hook
sets the new title on their next prompt. The apply writes relabel-map.csv (old -> new) next to the
manifest, and --undo of a relabel manifest also restores the sessions/projects state saved beside it.
--salur-relabel [--apply] writes those queued titles once their chats are closed (chat-label-tab-sync.py
does it when the IDE quits), appending to the newest relabel manifest.
"""
import collections
import csv
import datetime
import shutil
import fcntl
import glob
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks"))
import chat_label as cl  # noqa: E402

MANIFEST_DIR = os.environ.get("CHAT_LABEL_BACKUP_DIR") or os.path.join(cl.HOME, "backups")
LABELLED = re.compile(r"^\s*(?:\d+[A-Z]*\b|[A-Za-z]{2,5}\s*\d*[A-Z]*\s*[.\-–])")
PREFIX = re.compile(r"^\s*(?:\d+[A-Z]*(?:\s+[A-Z]\b)?|[A-Za-z]{2,5}\s*\d*[A-Z]*)\s*[.\-–]\s*")
TEST_CWD = tuple((os.environ.get("CHAT_LABEL_TEST_CWD") or "/private/tmp/:/private/var/folders/:/var/folders/:/tmp/").split(":"))


def live_session_ids():
    ids = set()
    reg = os.environ.get("CHAT_LABEL_SESSIONS_REGISTRY") or os.path.join(cl.HOME, "sessions")
    for f in glob.glob(os.path.join(reg, "*.json")):
        try:
            d = json.load(open(f))
            os.kill(int(d["pid"]), 0)
            ids.add(d["sessionId"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
    if not os.environ.get("CHAT_LABEL_SESSIONS_REGISTRY"):
        ps = subprocess.run(["ps", "-axww", "-o", "command"], capture_output=True, text=True).stdout
        ids.update(re.findall(r"--resume[= ]([0-9a-f-]{36})", ps))
    return ids


def plan(project_dir):
    anchor = cl.read_state(cl.paths()[0])["anchor"]
    live = live_session_ids()
    todo, skipped = [], {"dinamakan": 0, "terbuka": 0, "tiada label asal": 0, "kosong": 0}
    for path in sorted(glob.glob(os.path.join(os.path.abspath(project_dir), "*.jsonl"))):
        sid = os.path.basename(path)[:-6]
        t = cl.scan(path)
        if t["custom"]:
            skipped["dinamakan"] += 1
            continue
        if sid in live:
            skipped["terbuka"] += 1
            continue
        label = cl.normalize(t["injected"], cl.candidate_days(t, anchor))
        if not label:
            skipped["tiada label asal"] += 1
            continue
        text = t["ai"] or (cl.snippet(t["first_prompt"]) if t["first_prompt"] else None)
        if not text:
            skipped["kosong"] += 1
            continue
        todo.append({"path": path, "sid": sid, "label": label, "source": "ai" if t["ai"] else "prompt",
                     "title": cl.titled(label, text), "first_ts": t["first_ts"] or ""})
    todo.sort(key=lambda r: r["first_ts"])
    return todo, skipped


def projects_dir():
    return os.environ.get("CHAT_LABEL_PROJECTS_DIR") or os.path.join(cl.HOME, "projects")


def root_of(cwd):
    if os.path.isdir(cwd):
        return cl.project_root(cwd)
    return os.path.realpath(cwd.split("/worktrees/")[0])


def local_day(ts):
    return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().date()


def plan_unlabelled():
    live = live_session_ids()
    chats, skipped = [], {"berlabel": 0, "terbuka": 0, "kosong": 0, "ujian": 0}
    for path in sorted(glob.glob(os.path.join(projects_dir(), "*", "*.jsonl"))):
        t = cl.scan(path)
        if not (t["first_prompt"] and t["cwd"] and t["first_ts"]):
            skipped["kosong"] += 1
        elif (t["cwd"] + "/").startswith(TEST_CWD):
            skipped["ujian"] += 1
        else:
            t.update(path=path, sid=os.path.basename(path)[:-6], root=root_of(t["cwd"]), day=local_day(t["first_ts"]))
            chats.append(t)
    projects = cl.load_projects()
    first_chat = {}
    for c in chats:
        first_chat[c["root"]] = min(first_chat.get(c["root"], c["day"]), c["day"])
    for root, first in first_chat.items():
        if root not in projects:
            anchor, source = cl.first_day(root) if os.path.isdir(root) else (first.isoformat(), "chat")
            if first.isoformat() < anchor:
                anchor, source = first.isoformat(), "chat"
            projects[root] = {"anchor": anchor, "from": source, "last_date": "", "last_index": 0}
    used = collections.defaultdict(set)
    for c in chats:
        m = cl.LABEL_PREFIX.match(c["custom"] or "")
        if m:
            used[c["root"]].add(m.group(1))
    todo = []
    for c in sorted(chats, key=lambda c: c["first_ts"]):
        if c["custom"] and LABELLED.match(c["custom"]):
            skipped["berlabel"] += 1
        elif c["sid"] in live:
            skipped["terbuka"] += 1
        else:
            c["daynum"] = cl.day_number(c["day"], projects[c["root"]]["anchor"])
            todo.append(c)
    # The label Claude already used inside a chat is quoted in memory and PRs, so it wins when the
    # per-project day agrees with it (true for projects whose Day 1 is the old global anchor).
    for c in todo:
        own = cl.normalize(c["injected"], cl.candidate_days(c, cl.ANCHOR_DEFAULT))
        if own and own.startswith(str(c["daynum"])) and own[len(str(c["daynum"])):].isalpha() \
                and own not in used[c["root"]]:
            c["label"] = own
            used[c["root"]].add(own)
    for c in todo:
        if "label" not in c:
            n = 1
            while "%d%s" % (c["daynum"], cl.letters(n)) in used[c["root"]]:
                n += 1
            c["label"] = "%d%s" % (c["daynum"], cl.letters(n))
            used[c["root"]].add(c["label"])
    return [{"path": c["path"], "sid": c["sid"], "label": c["label"], "root": c["root"], "date": c["day"].isoformat(),
             "index": letter_index(c["label"][len(str(c["daynum"])):]),
             "source": "user" if c["custom"] else "ai" if c["ai"] else "prompt",
             "title": cl.titled(c["label"], c["custom"] or c["ai"] or cl.snippet(c["first_prompt"]))}
            for c in todo], skipped, projects


def bare(title):
    """Title text without the label or marker in front: "121D - x", "11827 - x", "TBR - x", "20A. x" -> "x"."""
    if not title or not LABELLED.match(title):
        return title
    return PREFIX.sub("", title, count=1).strip() or None


def collect():
    chats, skipped = [], {"kosong": 0, "ujian": 0}
    for path in sorted(glob.glob(os.path.join(projects_dir(), "*", "*.jsonl"))):
        t = cl.scan(path)
        if not (t["first_prompt"] and t["cwd"] and t["first_ts"]):
            skipped["kosong"] += 1
        elif (t["cwd"] + "/").startswith(TEST_CWD):
            skipped["ujian"] += 1
        else:
            t.update(path=path, sid=os.path.basename(path)[:-6], root=root_of(t["cwd"]), day=local_day(t["first_ts"]))
            chats.append(t)
    return chats, skipped


def anchors_for(chats):
    projects = cl.load_projects()
    first_chat = {}
    for c in chats:
        first_chat[c["root"]] = min(first_chat.get(c["root"], c["day"]), c["day"])
    for root, first in first_chat.items():
        if root not in projects:
            anchor, source = cl.first_day(root) if os.path.isdir(root) else (first.isoformat(), "chat")
            if first.isoformat() < anchor:
                anchor, source = first.isoformat(), "chat"
            projects[root] = {"anchor": anchor, "from": source, "last_date": "", "last_index": 0}
    return projects


def plan_relabel():
    live = live_session_ids()
    chats, skipped = collect()
    projects = anchors_for(chats)
    skipped["sama"] = 0
    count = collections.Counter()
    todo, every = [], []
    for c in sorted(chats, key=lambda c: (c["root"], c["first_ts"])):
        count[(c["root"], c["day"])] += 1
        index = count[(c["root"], c["day"])]
        label = "%d%s" % (cl.day_number(c["day"], projects[c["root"]]["anchor"]), cl.letters(index))
        title = cl.titled(label, c["ai"] or bare(c["custom"]) or cl.snippet(c["first_prompt"]))
        old = c["custom"] or c["ai"] or ""
        row = {"path": c["path"], "sid": c["sid"], "root": c["root"], "label": label, "title": title,
               "old": old, "old_label": (cl.LABEL_PREFIX.match(old) or [None, ""])[1] or "",
               "date": c["day"].isoformat(), "index": index, "source": "ai" if c["ai"] else "prompt",
               "live": c["sid"] in live, "first_ts": c["first_ts"]}
        every.append(row)
        if title == c["custom"]:
            skipped["sama"] += 1
            continue
        todo.append(row)
    return todo, skipped, projects, every


def apply_relabel(todo, projects, every):
    stamp = datetime.datetime.now().strftime("%Y-%m-%d-%H%M%S")
    out_dir = os.path.join(MANIFEST_DIR, "chat-label-semula-%s" % stamp)
    os.makedirs(out_dir, exist_ok=True)
    _, sessions_path, lock_path = cl.paths()
    for f in (sessions_path, cl.projects_path()):
        if os.path.isfile(f):
            shutil.copy2(f, out_dir)
        else:
            cl.save_json(os.path.join(out_dir, os.path.basename(f)), {})
    closed = [r for r in todo if not r["live"]]
    manifest = apply(closed, None, os.path.join(out_dir, "relabel-manifest.json"))
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        sessions = cl.load_sessions(sessions_path)
        for r in todo:
            if r["live"]:
                cl.remember(sessions, r["sid"], r["label"], "relabel", r["title"])
        cl.save_json(sessions_path, sessions)
        save_projects(every, projects)
    with open(os.path.join(out_dir, "relabel-map.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["session", "projek", "tarikh", "label_lama", "label_baru", "tajuk_lama", "tajuk_baru", "terbuka"])
        for r in sorted(todo, key=lambda r: (r["root"], r["first_ts"])):
            w.writerow([r["sid"], os.path.basename(r["root"]), r["date"], r["old_label"], r["label"], r["old"],
                        r["title"], "ya" if r["live"] else ""])
    return manifest


def letter_index(s):
    n = 0
    for ch in s:
        n = n * 26 + ord(ch) - 64
    return n


def save_projects(todo, planned):
    current = cl.load_projects()
    for root, p in planned.items():
        current.setdefault(root, p)
    now = cl.today().isoformat()
    for r in todo:
        if r["date"] == now:
            p = current[r["root"]]
            if p["last_date"] != now:
                p["last_date"], p["last_index"] = now, 0
            p["last_index"] = max(p["last_index"], r["index"])
    cl.save_json(cl.projects_path(), current)


def apply(todo, projects=None, manifest=None):
    if manifest is None:
        out_dir = os.path.join(MANIFEST_DIR, "chat-label-%s" % cl.today().isoformat())
        os.makedirs(out_dir, exist_ok=True)
        manifest = os.path.join(out_dir, "backfill-manifest.json")
    done = json.load(open(manifest)) if os.path.isfile(manifest) else []
    _, sessions_path, lock_path = cl.paths()
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        sessions = cl.load_sessions(sessions_path)
        for r in todo:
            line = json.dumps({"type": "custom-title", "customTitle": r["title"], "sessionId": r["sid"]},
                              ensure_ascii=False, separators=(",", ":"))
            with open(r["path"], "rb+") as fh:
                fh.seek(0, 2)
                lead = b""
                if fh.tell():
                    fh.seek(-1, 2)
                    lead = b"" if fh.read(1) == b"\n" else b"\n"
                fh.write(lead + line.encode() + b"\n")
            done.append({"path": r["path"], "line": line})
            cl.remember(sessions, r["sid"], r["label"], r["source"], r["title"])
        cl.save_json(sessions_path, sessions)
        if projects is not None:
            save_projects(todo, projects)
    json.dump(done, open(manifest, "w"), ensure_ascii=False, indent=1)
    return manifest


def queued_relabels():
    """Chats --semula queued while open ("relabel") whose Claude process has gone since."""
    _, sessions_path, _ = cl.paths()
    live = live_session_ids()
    index = {os.path.basename(p)[:-6]: p for p in glob.glob(os.path.join(projects_dir(), "*", "*.jsonl"))}
    return [{"sid": sid, "path": index[sid], "label": e["label"], "title": e["title"],
             "source": "ai" if cl.scan(index[sid])["ai"] else "prompt"}
            for sid, e in cl.load_sessions(sessions_path).items()
            if e.get("source") == "relabel" and e.get("title") and sid in index and sid not in live]


def flush_relabels():
    """Write the queued titles now instead of on each chat's next prompt; lines go into the newest
    relabel manifest so its --undo still removes everything --semula wrote."""
    todo = queued_relabels()
    if not todo:
        return 0
    manifests = sorted(glob.glob(os.path.join(MANIFEST_DIR, "chat-label-semula-*", "relabel-manifest.json")))
    apply(todo, None, manifests[-1] if manifests else None)
    return len(todo)


def undo(manifest):
    removed = 0
    if os.path.basename(manifest).startswith("relabel"):
        _, sessions_path, _ = cl.paths()
        for name, dest in (("chat-label-sessions.json", sessions_path), ("chat-label-projects.json", cl.projects_path())):
            saved = os.path.join(os.path.dirname(manifest), name)
            if os.path.isfile(saved):
                shutil.copy2(saved, dest)
    for r in json.load(open(manifest)):
        if not os.path.isfile(r["path"]):
            continue
        want = r["line"].encode()
        lines = open(r["path"], "rb").read().split(b"\n")
        kept = [ln for ln in lines if ln != want]
        if len(kept) != len(lines):
            removed += len(lines) - len(kept)
            tmp = r["path"] + ".undo-tmp"
            open(tmp, "wb").write(b"\n".join(kept))
            os.replace(tmp, r["path"])
    return removed


def main(argv):
    if "--undo" in argv:
        i = argv.index("--undo")
        if i + 1 >= len(argv) or not os.path.isfile(argv[i + 1]):
            print("beri fail manifest: --undo <manifest.json>")
            return 2
        print("dibuang %d baris tajuk" % undo(argv[i + 1]))
        return 0
    if argv and argv[0] == "--salur-relabel":
        todo = queued_relabels()
        for r in todo:
            print("%s  %s" % (r["sid"][:8], r["title"]))
        if "--apply" in argv:
            flush_relabels()
        print("relabel beratur (chat dah tutup): %d%s" % (len(todo), "" if "--apply" in argv else " (larian kering)"))
        return 0
    if argv and argv[0] == "--semula":
        todo, skipped, projects, every = plan_relabel()
        for r in todo:
            print("%s  %-22.22s %-40.40s -> %s%s" % (r["sid"][:8], os.path.basename(r["root"]), r["old"], r["title"],
                                                   "  [terbuka: prompt seterusnya]" if r["live"] else ""))
        live = sum(r["live"] for r in todo)
        print("akan dilabel semula: %d (terbuka %d) · langkau: %s" % (
            len(todo), live, ", ".join("%s %d" % kv for kv in skipped.items())))
        if "--apply" in argv:
            print("manifest: %s" % apply_relabel(todo, projects, every))
        return 0
    if argv and argv[0] == "--tanpa-label":
        todo, skipped, projects = plan_unlabelled()
        shown = todo[:10] if "--ringkas" in argv else todo
        for r in shown:
            print("%s  %-24.24s %s" % (r["sid"][:8], os.path.basename(r["root"]), r["title"]))
        if len(shown) < len(todo):
            print("… dan %d lagi" % (len(todo) - len(shown)))
        print("akan dilabel: %d · langkau: %s" % (len(todo), ", ".join("%s %d" % kv for kv in skipped.items())))
        if "--apply" in argv:
            print("manifest: %s" % apply(todo, projects))
        return 0
    if not argv or not os.path.isdir(argv[0]):
        print(__doc__)
        return 2
    todo, skipped = plan(argv[0])
    for r in todo:
        print("%s  %s" % (r["sid"][:8], r["title"]))
    print("akan dilabel: %d · langkau: %s" % (len(todo), ", ".join("%s %d" % kv for kv in skipped.items())))
    labels = [r["label"] for r in todo]
    dupes = sorted({x for x in labels if labels.count(x) > 1})
    if dupes:
        print("label berulang (asal dari hook lama): %s" % ", ".join(dupes))
    if "--apply" in argv:
        print("manifest: %s" % apply(todo))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
