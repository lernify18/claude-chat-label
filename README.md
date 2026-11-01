# chat-label — label automatik untuk tab chat Claude Code

Setiap chat Claude Code dapat label pendek di depan tajuknya, jadi chat lama senang dicari:

```
121F. Pelabelan chat untuk semua projek
121G. Kalendar pilihan tarikh
122A. Ada apa2 yg belum merge?
122B. Testing sesi login
```

- **Nombor** = hari ke-berapa projek itu (Hari 1 = commit pertama repo, atau tarikh folder dibuat).
- **Huruf** = chat ke-berapa pada hari itu dalam projek itu: A, B, … Z, AA, AB …
- **Tajuk** = tajuk yang Claude Code sendiri hasilkan. Kalau tiada, ayat pertama prompt anda digunakan.

Setiap projek ada kiraannya sendiri. Worktree dan subfolder dikira sebagai projek induknya.
Chat yang anda namakan sendiri (Rename) tidak akan diusik.

## Pasang

Dalam Claude Code (terminal, VS Code, Antigravity atau Cursor), taip dua arahan ini:

```
/plugin marketplace add lernify18/claude-chat-label
/plugin install chat-label@claude-chat-label
```

Kemudian buka chat baru. Di VS Code, Antigravity atau Cursor, buat **Reload Window** dahulu.

Atau dari terminal biasa:

```bash
claude plugin marketplace add lernify18/claude-chat-label
claude plugin install chat-label@claude-chat-label
```

**Keperluan:** Claude Code, `python3` dan `git`. Ia berjalan di macOS dan Linux. Untuk Windows, guna WSL.

## Bila label muncul

| Di mana | Prompt pertama | Prompt kedua |
|---|---|---|
| VS Code / Antigravity / Cursor | `12A. <tajuk Claude Code>` terus muncul | — |
| Terminal (`claude`) | Claude tahu labelnya, tajuk belum berubah | `12A. <ayat prompt pertama>` |

Claude sendiri pun tahu label chatnya, jadi anda boleh tanya «chat 12A tadi buat apa?».

## Chat lama

Chat yang dibuat sebelum plugin dipasang belum ada label. Taip:

```
/chat-label:backfill
```

Arahan ini hanya **menunjukkan** apa yang akan dilabel (dry run); tiada apa yang ditulis lagi. Kalau senarai itu
okey, taip:

```
/chat-label:backfill --apply
```

Setiap perubahan direkodkan dalam fail manifest, dan semuanya boleh dibuang semula dengan arahan undo yang
dipaparkan (`/chat-label:backfill --undo <manifest>`). Chat yang sedang terbuka tidak ditulis; ia akan dilabel
oleh hook pada prompt seterusnya.

## Tab lama masih nama lama selepas restart (macOS)

VS Code, Antigravity dan Cursor menyimpan nama tab semasa ditutup, dan memaparkan nama simpanan itu selepas
dibuka semula sehingga tab itu diklik. Untuk betulkan semua sekali gus:

```
/chat-label:tab-sync --tunggu
```

Kemudian tutup editor dengan **Cmd+Q**, tunggu notifikasi «Label tab siap», dan buka semula. Editor perlu
ditutup kerana ia menulis semula nama tab semasa keluar. Sandaran setiap fail yang diubah disimpan di
`~/.claude/backups/`. Tanpa `--tunggu`, arahan ini hanya menunjukkan berapa tab yang basi.

## Soalan lazim

**Saya dah pasang hook label ini secara manual dalam `settings.json`.** Plugin akan senyap sendiri supaya tiada
label berganda. Untuk beralih ke plugin, buang dua entri `chat-label.sh` dalam `~/.claude/settings.json`.

**Nombor hari projek saya tak betul.** Hari 1 setiap projek dibekukan dalam `~/.claude/chat-label-projects.json`
pada kali pertama digunakan. Tukar `anchor` projek itu (format `YYYY-MM-DD`) dan simpan.

**Data saya dihantar ke mana-mana?** Tidak. Semuanya berjalan di komputer anda: plugin hanya membaca transkrip
chat dalam `~/.claude/projects/` dan menulis label ke dalamnya. Tiada rangkaian.

**Tab di VS Code tak sempat dapat tajuk pada prompt pertama.** Hook menunggu tajuk Claude Code sehingga 3.5 saat.
Kalau tajuk lambat sampai, ayat prompt pertama digunakan dulu, dan tajuk sebenar menggantinya pada prompt
seterusnya.

## Buang

```
/plugin uninstall chat-label@claude-chat-label
```

Label yang sudah ditulis kekal pada chat. Fail state plugin ialah `~/.claude/chat-label-projects.json`,
`chat-label-sessions.json` dan `chat-label.lock`; padam fail-fail itu kalau mahu bersih sepenuhnya.

---

## English

Labels every Claude Code chat tab `<day><letter>. <title>` (e.g. `12A. Fix login bug`), counted per project:
the day since the project's first commit, and a letter for which chat of that day it is. The title is Claude
Code's own AI title, and chats you renamed by hand are never touched.

```
/plugin marketplace add lernify18/claude-chat-label
/plugin install chat-label@claude-chat-label
```

- `/chat-label:backfill` shows what old chats would get a label; add `--apply` to write it. It is undoable
  with `--undo <manifest>`.
- `/chat-label:tab-sync --tunggu` (macOS) fixes the restored tab names: quit the editor with Cmd+Q and reopen
  it after the notification.
- Requires `python3` and `git`; macOS or Linux (WSL on Windows). It is local only and makes no network calls.
- If you already run the hook by hand from `settings.json`, the plugin stays silent to avoid double labels.

## Development

```bash
python3 tests/test_chat_label.py          # hook + backfill unit tests
python3 tests/test_tab_sync.py            # tab-sync unit tests
bash tests/e2e_plugin.sh "$(mktemp -d)"   # real headless sessions (haiku) + install into a temp config
bash tests/e2e_commands.sh "$(mktemp -d)" # slash commands on throwaway data
bash tests/e2e_readme.sh "$(mktemp -d)"   # the install/uninstall commands in this README, in a temp config
```

MIT licence.
