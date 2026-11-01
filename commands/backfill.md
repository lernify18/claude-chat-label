---
description: Label old chats that have no label yet (dry run unless --apply; --undo <manifest> reverses it)
argument-hint: "[--apply | --undo <manifest.json>]"
allowed-tools: ["Bash(${CLAUDE_PLUGIN_ROOT}/scripts/chat-label-backfill.py:*)"]
---

Result of the chat-label backfill (arguments given: `$ARGUMENTS`):

```!
"${CLAUDE_PLUGIN_ROOT}/scripts/chat-label-backfill.py" --tanpa-label --ringkas $ARGUMENTS
```

The script already did all the work above; do not run it again and do not write any file yourself.
Report it to the user in their language, briefly:

- **Dry run** (no `--apply`, no `--undo` in the arguments): say how many chats would get a label
  (`akan dilabel: N`) and show up to 5 of the example rows as `old → new` titles. Explain the skipped
  counts in plain words: `berlabel` = already labelled, `terbuka` = open right now (labelled later by the
  hook), `kosong` = no prompt, `ujian` = test chats under /tmp. If N is 0, say there is nothing to do.
  Otherwise tell them to run `/chat-label:backfill --apply` to write it. Nothing has been changed yet.
- **`--apply`**: say how many chats were labelled and give the manifest path from the `manifest:` line,
  with the exact undo command `/chat-label:backfill --undo <that path>`. Then say that tabs already open
  in VS Code / Antigravity / Cursor keep their old names until clicked, and `/chat-label:tab-sync`
  fixes that.
- **`--undo`**: say how many title lines were removed (`dibuang N baris tajuk`).
- If the output shows an error, quote it and stop.
