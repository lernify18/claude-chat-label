---
description: Make restored VS Code / Antigravity / Cursor chat tabs show each chat's current label (macOS)
argument-hint: "[--tunggu | --apply]"
allowed-tools: ["Bash(${CLAUDE_PLUGIN_ROOT}/scripts/chat-label-tab-sync.py:*)"]
---

Result of the chat-label tab sync (arguments given: `$ARGUMENTS`):

```!
"${CLAUDE_PLUGIN_ROOT}/scripts/chat-label-tab-sync.py" $ARGUMENTS
```

The script already did all the work above; do not run it again and do not write any file yourself.
Background for your answer: the editor saves each chat tab's name when it quits and shows that saved
name after a restart until the tab is clicked, so chats labelled on disk can show old names. The editor
also rewrites those names when it quits, so the fix can only be written while it is closed.

Report it to the user in their language, briefly:

- **Dry run** (no arguments): say how many tabs are stale (`jumlah tab basi: N`) with up to 3 examples.
  If N is 0, say everything already matches. Otherwise suggest `/chat-label:tab-sync --tunggu`: it waits
  in the background, writes within a second after they quit the editor with Cmd+Q, and shows a macOS
  notification when it is safe to reopen.
- **`--tunggu`**: say the waiter is running (or was already running) and that they should now quit the
  editor with Cmd+Q, wait for the "Label tab siap" notification, then reopen it.
- **`--apply`**: if the output says `DITOLAK`, explain the editor is still open and suggest `--tunggu`
  instead. Otherwise say how many tabs were updated and where the backup went.
- If the output shows an error, quote it and stop.
