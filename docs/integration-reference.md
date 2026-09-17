# Ty Work Hub

A modular PyQt6 command center for Backlog Personal Tasks, Obsidian notes, calendar planning, work timing, and morning standup preparation.

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

For a windowless launch, use `TyWorkHub.pyw` with `pythonw.exe`. The installed desktop shortcut at `C:\Users\freemont\Desktop\Ty Work Hub.lnk` uses the project `.venv`, launches without a terminal window, and carries the app icon from `assets/ty-work-hub.ico`.

On first launch, open **Settings → Backlog**, paste a Backlog API key, and confirm the MiraX URLs and assignee ID. The key is stored in Windows Credential Manager through `keyring`, not in the JSON config. `BACKLOG_API_KEY` remains available as an environment override. Without a key, the app intentionally uses demo data.

For source-control workflows, open **Settings → Source Control**. The app can detect `svn.exe` and `TortoiseProc.exe`, and lets you map Backlog projects to named local working copies. Use **Add from Backlog projects** to create one profile per project found in the loaded tickets, then set each root with **Browse working copy** or **Enter root manually** (useful for UNC and mapped-drive paths). A profile without a root is discarded when you save. SVN authentication remains in SVN/TortoiseSVN and is never stored by Ty Work Hub.

All Backlog calls run on `QThread` workers. Personal Tasks refresh at the configured interval and the last successful result is cached for offline startup.

## Command-center features

- Compact Backlog Personal Tasks workspace with live project/status metadata, direct issue links, sortable columns, tokenized search, and project-specific status updates. Its selected-ticket inspector is organized into focused **Work**, **Backlog**, and **Context** tabs so timer, remote mutation, and local planning controls do not compete for attention.
- Persistent task views for assigned, My Day, in-progress, due-today, overdue, high-priority, starred, undated, and resolved work.
- Multi-row selection with bulk status and Obsidian-note actions, a contextual action menu, keyboard row navigation, and a responsive filter/display rail.
- A compact `MD` button on every task opens or creates its linked note and moves directly to the Obsidian workspace.
- Local favorites and a quick-capture inbox that remain available while Backlog is offline.
- Month, Week, Day, and Agenda calendar views with editable local events, Backlog deadline overlays, a clickable mini-calendar for week navigation, and an upcoming-task rail.
- One active work timer linked to the selected ticket and logged as a calendar work block.
- Reliable ticket-center state: cancelling the in-progress prompt cancels Start work, duplicate starts are ignored, Stop updates immediately, remote status changes are not painted as successful until Backlog confirms them, and permanent API rejections are never placed in an endless retry loop.
- Full Obsidian vault tree with a name filter, YAML metadata inspector, rendered Markdown preview, explicit source-edit mode, internal wiki-link navigation, ticket-note synchronization, and daily ticket links.
- A **Ticket AI** bar on the Obsidian workspace bound to the selected ticket: open its note, draft the note update, or generate study material without leaving the vault. Opening any ticket note selects that ticket automatically, so browsing the vault re-points the AI actions by ticket name.
- Morning Standup workspace with the imported 16-person team roster, click-to-complete ordering, “What I will say,” yesterday/today/blockers, meeting notes, clipboard summary, and autosave.
- Command Center with a favorites-aware priority queue, upcoming schedule, capture inbox, logged-time summary, quick actions, searchable command palette, and focus mode.
- Revision-aware queue acknowledgements: mark a ticket seen to move it back or hide its current revision; updated fields or a new comment count automatically bring it forward again.
- Review Board for tickets assigned to other people, with key/title lookup, assignee labels, direct opening, local seen state, and explicit untracking.
- Ticket Detail workspace opened from the inspector, with full ticket context, recent-comment-aware local-AI briefs, and separate current-situation, previous-work, next-step, and risk sections. Its controls are split into a **Generate** row (English brief, Obsidian note draft, study material, Japanese Backlog comment) and a **Save** row (apply note + study material, save study material only, copy brief), so producing a draft and writing it to the vault stay visibly distinct.
- Project environment profiles for 検証, ライブ, and ローカル sites, with one-click opening, copyable login details, and passwords stored in Windows Credential Manager.
- Ticket-aware Source Control workspace with local SVN status, coloured diffs, a side-by-side before/after compare against the base revision, a read-only breakdown of what a saved patch contains, a shelf-wide patch list with tiered multi-column sorting, a working-copy location tree, sortable and filterable changes, per-file locking, working-copy info, log, blame, update to a revision, cleanup, conflict resolution, TortoiseSVN launchers, and project-to-working-copy profiles.
- Patch-backed ticket shelves: create a verified ticket patch, shelve by reverting exactly its recorded versioned paths, inspect patch history and integrity metadata, then dry-run and apply/unshelve later without automatically updating or committing.
- Local-AI Obsidian drafting and Japanese Backlog comment generation through a llama.cpp server at `http://192.168.17.201:8080`; applying an Obsidian update creates a timestamped history backup first, while a generated comment remains an editable draft until **Post comment** is confirmed.
- Reusable programming study material generated as managed Markdown lessons under `Work/Programming`, with use cases, prerequisites, generalized examples, alternatives and trade-offs, exercises, and a lesson path linked back into the associated ticket note.
- **Study material** generates and saves independently of the ticket note. **Save study material only** writes just the lesson notes: the ticket note is never re-rendered, nothing is posted to Backlog, and the ticket note's `## Learning references` section only gains a missing link.
- Grouped navigation rail — **Focus** (Command Center, Personal Tasks, Ticket Detail), **Workspaces** (Review Board, Source Control, Obsidian), and **Planning** (Calendar, Morning Standup) — with attention counts on Personal Tasks and Review Board so a destination shows how much work it is holding before you open it.
- Responsive collapsible navigation and ticket inspector, plus dark/light themes, compact/comfortable density, 9–14 px base fonts, remembered task columns, startup-page selection, workday settings, custom Obsidian folders, and custom command launchers.
- Personal planning tools: a daily-resetting My Day list, local task reminders with quick snooze presets and desktop notifications, and 25/50-minute focus targets layered onto the work timer.

Google Calendar remains an optional adapter in `modules/calendar_api.py`; the built-in local calendar works without Google credentials.

## Local AI

Ticket briefs, the Obsidian note template, standalone study material, and Backlog comment drafts run against a local
[llama.cpp](https://github.com/ggml-org/llama.cpp) server over its OpenAI-compatible API. Configure it under
**Settings → Local AI**:

| Setting | What it does |
| --- | --- |
| Server URL | Base URL of the server, e.g. `http://192.168.17.201:8080` |
| Model | Leave blank to use whatever model the server has loaded; llama.cpp serves one at a time |
| Generation timeout | How long the app waits before giving up |
| Reply budget | Ceiling on the reply in tokens. A full note template needs several thousand |
| Context window | The server's context size; the app trims the note and comment history to fit |
| Thinking | Off by default. Reasoning models spend reply tokens on thinking before answering |

**Test connection** reads `/props` and reports the loaded model, context window, and slot count, and fills in the
context window for you. **Load models** reads `/v1/models`.

Structured output uses `response_format: json_schema`, which llama.cpp enforces with a grammar, so a reply is either
valid JSON matching the schema or the request fails loudly. Replies are streamed, so a long generation shows live
progress and **Stop** cancels it at the next chunk. The Hub decodes llama.cpp's SSE stream explicitly as UTF-8 rather
than relying on HTTP charset guessing, preserving Japanese text in generated ticket comments.

### What the model is given

Each request sends the ticket, recent Backlog comments, the existing Obsidian note, and **the file list of this
ticket's verified SVN patches**. The patch context is what lets the note template report `changed_files` accurately
instead of guessing: the model can only name files a real patch actually touched. Nothing is written to Obsidian
until you review the preview and apply it.

**Study material** uses the same evidence, but the ticket only decides *which* techniques are worth teaching. The
lesson itself is generalized beyond the observed implementation and must not report this ticket's status, deadlines,
or remaining work — that is the note template's job. It is reviewed on the **Study material** tab and written with
**Save study material only**, which touches nothing but the lesson files and a missing link in the ticket note.

**AI Generate Ticket Comment** uses the newest verified patch for the selected ticket and includes its actual unified
diff, not only patch metadata. The model returns structured Japanese content, and the app renders it deterministically:

```text
【対応内容】
・対応結果の要約
【実施箇所】
・actual/path/from/the.patch
【詳細】
・diffから確認できる実装内容
```

The file list is owned by the parsed patch rather than the model, so a generated filename cannot enter the draft.
The button remains unavailable until the ticket has a saved patch, and generation stops with an actionable error if
none of that ticket's patch candidates can pass SHA-256 integrity verification. The draft is placed in the inspector’s Backlog tab for
review; posting still requires the existing explicit confirmation. Unsent comment text is kept separately per ticket
during the current app session, preventing a draft from following the user onto a different issue.

The app trims the note first and the comment history second when the prompt would not fit the context window, and
says how many comments it omitted. If the reply is cut off by the token budget the error says so and points at the
Reply budget setting, rather than failing with a JSON parse error.

## SVN patch workflow

The Source Control workspace is designed for switching safely between several ticket fixes:

1. Select a Backlog ticket or enter a ticket key manually.
2. Choose its configured working-copy profile and run **Show status**.
3. On the **Changes** tab, tick exactly the files the patch should contain.
4. Edit **Context** if you want a specific patch file name; it defaults to the ticket summary, or to the ticket key for a manually typed ticket.
5. Use **Create patch** on the Changes tab. The button shows how many files it will include, and only ticked files go in.
6. Later, select the saved artifact on the **Patch Shelf** and use **Apply patch**. The app runs `svn patch --dry-run`, verifies the recorded working-copy/repository context, shows conflicts, and asks again before applying.

### Where the actions live

The toolbar above the tabs holds working-copy-wide commands: **Show status**, **Compare with base**, **Log**, **Update…**, **Cleanup…**, and **Info**. **Update…** asks for a revision (`HEAD` or a number) and confirms the scope before running.

Everything that acts on a selection lives on the tab that shows that selection:

| Tab | What it is for | Actions |
| --- | --- | --- |
| **Changes** | Local working-copy state; decide what goes into a patch | Create patch, Revert selected, Show diff, Lock, Unlock |
| **Patch Shelf** | Every saved patch in the shelf | Apply patch, Delete patch, Refresh, Open shelf folder |
| **Patch Changes** | Read-only breakdown of the selected patch | — |
| **Before / After** | Base revision beside the working copy | First / Previous / Next / Last change |
| **Diff / Output** | Raw SVN command output and unified diffs | Copy output |

**More SVN** now holds only the patch-backed shelve workflow (**Shelve changes** and **Unshelve**) and the TortoiseSVN dialogs. Per-file commands — blame, resolve conflicts, lock, unlock, TortoiseSVN diff, copy path — are on the right-click menu of the Changes table.

### Reading changes

The **Changes** tab sorts on any column header. Sorting by **Path** keeps files in the same folder together instead of interleaving similar prefixes, and sorting by **State** brings conflicts to the top and pushes unversioned files to the bottom. The **Unversioned** checkbox hides files SVN does not track; the choice is remembered. The tree on the right shows where the selected file sits in the working copy, and selecting a node selects the matching row.

**Diff / Output** colours additions green, removals red, hunk headers yellow, and gives each file in a multi-file patch its own header colour. **Compare with base** fills the **Before / After** tab with the base revision beside the working-copy version for each selected file, scrolled in step and with changed lines marked; the block you are on is drawn in a stronger shade, and **First / Previous / Next / Last** (or `F7` / `Shift+F7`) step through them. Up to twelve files are compared at once.

### Locking files you do not want to patch

Some files change locally on every machine and should never ride along in a ticket patch — `web.config` and other environment configuration are the usual case. Select those rows and press **Lock**. Locking adds the file to SVN's conventional `ignore-on-commit` changelist, which is the same label TortoiseSVN uses, so the mark lives in the working copy and both tools agree.

A locked file stays fully visible and can still be ticked by hand; it is simply never selected automatically, so **Show status** and **Select patchable** leave it alone. Locked rows are dimmed, marked `· locked` in the State column, and sort to the bottom under every sort order, which keeps the rows that still need a decision together at the top. **Unlock** removes the label. Locking only labels a file — it never changes content.

### Browsing the shelf

The **Patch Shelf** lists every patch under the shelf folder, across all project and ticket folders, newest first. Columns are **Project**, **Ticket**, **Created**, **State**, **Files**, **Revision**, and **Patch**; Project and Ticket use the same names the shelf folders are created with. Tick **This ticket only** to narrow it to the ticket in the Ticket field. Whichever scope is active, the **Patches (n)** badge on Ticket Detail and the task inspector always counts only the selected ticket.

A manifest that cannot be read is reported in the shelf summary and the status bar rather than quietly disappearing from the list.

### Sorting the shelf

The shelf sorts on several columns at once, ranked by priority:

- **Left-click** a column heading to flip it between ascending and descending.
- **Right-click** a column heading to cycle its priority: none → ▲ → ▲▲ → ▲▲▲.

The arrows sit to the left of the column title. **▲▲▲ is compared first**, then ▲▲, then ▲; columns sharing a level are compared in the order you assigned them. So ▲▲▲ Project with ▲▲ Ticket groups the shelf by project, and orders each project's patches by ticket. With no priorities set the shelf falls back to Created, newest first. The line under the table always spells out the current order, for example `Sort: ▲▲▲ Project ↑ → ▲▲ Ticket ↑ → ▲ Created ↓`. The layout is remembered between sessions.

### Understanding a saved patch

**Patch Changes** is a read-only view of whichever patch is selected on the Patch Shelf. It reads the patch text itself rather than the manifest, so it shows what the patch will actually do: each file, whether it is modified, added, or deleted, and how many lines it adds and removes, with the same location tree as the Changes tab. Selecting a patch fills this tab automatically.

### Removing a patch

**Delete patch** on the Patch Shelf permanently removes the selected patch and its manifest after a confirmation that shows the ticket, state, file count, and creation time. Deleting a patch that is still `shelved` warns first, because its changes have already been reverted from the working copy and the patch is the only copy of that work. Nothing else in the shelf and nothing in the working copy is touched.

### From a ticket

**Ticket Detail** and the **Personal Tasks** inspector both carry a source-control row: **Make patch** opens the SVN workspace with the ticket already set as the patch context, **Shelve changes** does the same for the shelve flow, and **Patches (n)** opens that ticket's shelf and shows how many artifacts exist. The same actions are on the task context menu under **Source control / SVN** and in the command palette.

Patch artifacts are never deleted automatically — only an explicit, confirmed **Delete patch** removes one — and applying a patch never runs update or commit. Added versioned files are removed only during a confirmed shelf after their patch is verified. Unversioned files and unsupported binary changes are not silently shelved; the app preserves them and explains the limitation. Repository-mutating and destructive actions require confirmation.

The TortoiseSVN submenu under **More SVN** opens check-for-modifications, commit, update, log, revision graph, repository browser, diff, blame, switch, merge, create patch, properties, lock, unlock, and resolve.

## Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| `Ctrl+R` | Refresh Backlog |
| `Ctrl+N` | Create calendar event |
| `Ctrl+Shift+N` | Sync the selected ticket to Obsidian |
| `Ctrl+S` | Start work on the selected ticket |
| `Ctrl+Shift+C` | Review and post the selected ticket’s comment draft |
| `Ctrl+Shift+A` | Generate the English AI brief for the selected ticket |
| `Ctrl+Shift+L` | Generate study material from the selected ticket |
| `Ctrl+K` | Open the searchable command palette |
| `Ctrl+F` | Open Personal Tasks and focus search |
| `Ctrl+B` | Collapse or expand navigation |
| `Ctrl+Shift+B` | Toggle the ticket inspector |
| `Ctrl+Alt+S` | Open Source Control for the selected ticket |
| `Ctrl+Alt+D` | Show the selected ticket's SVN diff |
| `Ctrl+,` | Open Settings |
| `Ctrl+1` … `Ctrl+8` | Open the eight primary workspaces, in rail order |
| `Escape` | Leave focus mode |

Inside Personal Tasks, `J` and `K` move between rows, `Space` toggles a star, and double-clicking opens the issue in Backlog.

## Regression check

With dependencies installed, run the offscreen UI test:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m tests.ui_smoke
python -m tests.svn_smoke
python -m tests.svn_integration
python -m tests.llm_integration
```

`tests.svn_integration` creates and deletes its own temporary local repository and working copy. It skips cleanly when `svn.exe` or `svnadmin.exe` is unavailable. `tests.llm_integration` exercises every generation mode, progress reporting, cancellation, and the truncated-reply path against the configured llama.cpp server, and skips cleanly when that server is unreachable.

## Local data

Non-secret settings and local state are stored under `~/.ty-work-app`:

- `config.json`: preferences and paths
- `events.json`: calendar events and work sessions
- `standup.json`: roster state and daily standup notes
- `issues-cache.json`: last successful Personal Tasks response
- `workspace.json`: favorites, capture inbox, seen revisions, watched review tickets, reminders, and focus state
- `crash.log`: appended only when an unexpected Python error reaches the global handler

The default SVN patch shelf is `~/.ty-work-app/svn/patches`, configurable under Source Control settings. Each patch has a neighboring JSON manifest recording its ticket, profile, working-copy and repository context, base revision, affected paths, state, and SHA-256 integrity hash. File names are unique even when two patches are created in the same instant, so a new patch never overwrites an existing one. Deleting a patch removes both files and prunes the ticket and project folders once they are empty.

Ticket notes are written under `Work/Tickets/<Project>/<M-D-YYYY>/<Ticket>_<title>.md` by default, using the ticket's Backlog creation date. Re-syncing refreshes managed YAML metadata while preserving the Markdown body. Daily ticket links and standup exports go under `Work/Daily` by default. Environment URLs and usernames are stored in `config.json`; environment passwords stay in Windows Credential Manager.
