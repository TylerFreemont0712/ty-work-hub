# Ty Work Hub Roadmap

Living product and implementation roadmap for the Schedule project. The app's working name in the UI is **Ty Work Hub**.

Last reviewed: 2026-09-17

## Linux and personal Git projects (2026-09-17) — Completed

- Added a standalone Git workspace alongside the SVN ticket shelf: status, working-tree/staged diffs, staging, unstaging, reviewed commits, fetch, fast-forward pull, push, retained stashes, and binary-capable tracked patch exports. Remote authentication uses Git's configured credential helpers or SSH agent.
- Added Settings → Git for default provider, repository, executable discovery, and explicit package-manager installation. Existing SVN profiles retain their default provider. Linux package installation uses native polkit authorization, with manual instructions when unavailable.
- Added Linux setup/launch scripts, portable font fallbacks, system-keyring guidance, and Windows/Ubuntu CI. Fresh installs use generic Backlog setup and localhost AI defaults; saved preferences remain intact.
- Verified Git integration and desktop regression checks on Windows and Ubuntu WSL, plus native Wayland and X11 windows. Fixed timestamp ties in SVN patch ordering, case-sensitive path handling on Linux, and canonical vault paths for Windows aliases and Linux symlinks. Real SVN integration passes on Windows and Ubuntu.
- Remaining desktop-specific verification: interactive keyring unlock and administrator prompts on each Linux distribution. Installer command plans are tested without modifying a user's production machine.

## Work timer, schedule, and time insights (2026-09-09) — Completed

- Put the work timer in the top row of every workspace. A chip shows a state dot, the counted time, what is being tracked, and one Start/Stop button; Ctrl+Shift+S toggles it from anywhere. An unnoticed running timer was the problem it exists to solve, so it never hides.
- Replaced the Today card's 25/50-minute focus blocks with a plain Start/Stop pair and two dropdowns: the ticket being worked on (or *No ticket — general work*) and the project, which follows the chosen ticket and can be overridden. Work with no Backlog project is filed under a configurable **Company General**. The focus-session concept was removed from the dashboard, the inspector, the command palette, and the workspace store.
- Added `modules/work_schedule.py`: a pure `WorkSchedule` of working days, day start/end, and any number of breaks. Sessions still record their real start and end; every total asks the schedule how much of that span counts, so evenings, weekends, and lunch are deducted at read time and a schedule change re-reads history correctly. Leaving the timer running overnight no longer inflates anything.
- The Today card's Start button starts only the clock. Starting from the ticket inspector still offers the in-progress status change and note creation, so a shortcut pressed in passing can never trigger a Backlog confirmation.
- Settings gained a **Schedule** tab: working days, day start/end, a break table with add/remove, the general project name, and a switch to count every tracked minute instead. It ships as Mon–Fri 09:00–18:00 with lunch 12:00–13:00 and migrates the old `workday_start`/`workday_end` hour settings once.
- Added a **Time insights** workspace under YOUR DAY: counted time in range, average per tracked day, counted today, and share of schedule; a per-day bar chart with the daily capacity as a guide; a timeline of the selected day showing schedule windows against tracked sessions; ranked breakdowns by ticket and by project; and every session listed, double-clickable to open its ticket. Charts are painted from theme tokens with no child widgets.
- The calendar's week and day grids now shade every hour the schedule does not count, lunch included, instead of a plain start/end hour pair.
- Added **Update note only** to Notes & knowledge: the local AI regenerates just that ticket's Obsidian note, shows the exact text first, backs up the previous version, and writes no study material and no Backlog comment. The older full flow is now labelled **Draft note & lessons**.
- Verified: Ruff, compileall, `tests.ui_smoke`, `tests.svn_smoke`, `tests.svn_integration`, and 15 `tests.rewrite_smoke` checks including new schedule-window, deduction, and breakdown coverage. Rendered Time insights at 1480×940 in two themes and at 900×600, plus the refreshed Today and Calendar screens. Live Backlog and llama.cpp services were not exercised.

Grounded follow-up: consider per-day schedule overrides for half-days and public holidays, and an editable session (fix a forgotten stop) once the shelf-style list proves out; both are only worth building after the schedule has been lived with for a few weeks.

## Theme system and multi-patch shelf (2026-09-09) — Completed

- Replaced the binary dark/light switch with a named-theme registry in `ui/theme.py`. Six themes ship: Sage Dark (default), Midnight, Ember, Graphite, Sage Light, and Nordic Light. One frozen `Theme` per appearance now supplies every colour in the app.
- Removed the hard-coded colours from the stylesheet, icon renderer, focus ring, week chart, task pills, queue and calendar delegates, review board, standup history, and calendar off-hours shading. Diff and compare palettes now follow the theme's light/dark flag instead of matching the literal string `"light"`, so a new dark theme needs no extra wiring. Adding a theme means adding one `Theme` entry.
- Switching a theme now re-tints every glyph, repaints the task table, and re-colours the diff views without rebuilding the Source Control workspace. The active navigation entry's icon carries the accent colour. Checkbox check marks are generated per theme so they read against the filled indicator.
- The top-bar palette button opens a theme menu grouped by dark and light; Ctrl+Shift+T steps to the next theme; Settings lists them with descriptions; the command palette has an entry per theme. Unknown saved theme keys fall back to the default.
- Trimmed the interface roughly ten percent: control padding, page margins, panel spacing, navigation width, table row heights, icon sizes, and the oversized page-title, hero, and metric type scale. The Today queue now fits an extra row at 1480×940 and the layout is unchanged at the 900 px minimum.
- Gave every patch its own identity so one long ticket can hold many: a `label`, an auto-assigned per-ticket `sequence`, an optional `group`, and an optional `note`, all stored in the manifest. Creating and shelving open a naming dialog with the next position pre-filled and the ticket's existing groups offered; the patch label field no longer inherits the ticket summary.
- The Patch Shelf gained `#`, Label, and Group columns, a live filter across label/group/ticket/file, an "Edit details…" action reachable from the toolbar, a double-click, the context menu, and the command palette, a "Set group…" fast path for organising an existing shelf, and "Renumber this ticket's patches" for series saved before numbering existed. Editing rewrites only the manifest; patch files keep their original names so an artifact pair can never half-rename.
- Manifests written before these fields load unchanged: a missing label falls back to the patch file name and a missing position renders as "—".
- Verified: Ruff, `tests.ui_smoke`, `tests.svn_smoke`, `tests.svn_integration`, and 13 `tests.rewrite_smoke` checks including a new palette-completeness test and a glyph-retint test. Rendered Today and Tasks in all six themes plus the eight workspaces and the 900×600 layouts. Drove `apply_theme` and `cycle_theme` through a real window across every workspace. Live Backlog, llama.cpp, and TortoiseSVN services were not exercised.

Grounded follow-up: let a saved task view remember a theme if per-context appearances turn out to be wanted; consider grouping the shelf table by ticket and group once a real shelf grows past a few dozen patches, and virtualize it at the same time as the SVN change table.

## September 2026 desktop rewrite — Completed

- Rebuilt the shell, Today dashboard, virtual Tasks and Review board, and Standup preparation UI; introduced a shared charcoal/sage and light visual system across all eight workspaces.
- Replaced per-row task widgets with a model/proxy/delegate architecture, indexed and debounced search, stable selection, natural sorting, one planning update per refresh, and bounded command-palette rendering. The latest local 10,000-ticket check populated the model in about 21 ms and filtered it in about 8 ms; the table allocates no child widgets per row.
- Added scratchpad autosave, daily/weekly routines, a seven-day work chart, standup history, and additive standup preparation from My Day and logged work. Today supports selecting a task in place for a focus session.
- Improved calendar rendering with reused parsed dates, readable event blocks, overlapping-event selection, and multi-hour/overnight coverage. Work totals split correctly across midnight.
- Added indexed timer lookup, failed-write rollback, unchanged-write avoidance, asynchronous AI connection checks, safe background shutdown, and owned Obsidian selection timers. Bulk Backlog status updates now wait for remote confirmation.
- Preserved the established Backlog, local AI, Obsidian, environment, SVN, and patch workflows and existing persisted file formats. Added an isolated `--demo` launch and reproducible screenshot tooling.
- Verified: existing UI smoke suite, SVN smoke suite, real SVN integration in a temporary repository, 11 rewrite checks, Python compilation, and Ruff. Rendered all eight workspaces plus narrow 900×600 layouts and both themes. Live Backlog and llama.cpp services were not exercised during this rewrite.

Grounded follow-up: virtualize the SVN change/shelf tables if real repositories show a similar row-volume bottleneck; move remaining ticket note/patch filesystem reads out of selection handlers when profiling network-backed vaults.

## Roadmap rules

- `Planned` means designed or requested, not started.
- `In progress` means actively being implemented.
- `Blocked` means a concrete external dependency or product decision prevents progress.
- `Completed` is reserved for work that is implemented, tested, and usable through the intended workflow.
- After a feature is fully implemented, update its status and add the next practical goals or ideas discovered during implementation. Do not promote partial work to Completed and do not fill the roadmap with speculative feature noise.
- Keep this file focused on product direction and meaningful engineering follow-up. Put transient debugging notes in code comments or issue notes instead.

## Product direction

Ty Work Hub should remain a fast personal work cockpit: the user should be able to find a ticket, understand its current state, choose the next action, perform it safely, and leave useful context for later with minimal context switching.

The long-term shape is an ever-expanding work surface built around a stable ticket context:

```text
Backlog ticket
    ├── status, comments, review queue
    ├── local workflow, My Day, reminder, focus timer
    ├── Obsidian note and daily record
    ├── calendar/work session
    └── source-control workspace and patch shelf
```

New capabilities should attach to this context instead of creating disconnected tools.

## Current baseline — completed before this roadmap

The project already provides a substantial PyQt6 command center:

- Backlog Personal Tasks refresh through a background worker, with demo mode, offline issue cache, project/status normalization, direct issue links, and configurable polling.
- Task views for assigned work, attention, review, seen/deferred, My Day, progress, due today, overdue, high priority, starred, undated, and resolved work.
- Search, sorting, configurable columns, responsive filter rail, multi-row selection, context menus, keyboard row navigation, favorites, and local queue acknowledgements that resurface changed tickets.
- A Review Board for watching tickets assigned to other people.
- A selected-ticket inspector and full Ticket Detail workspace with Backlog comments, local workflow state, related ticket keys, status changes, comment posting, and environment profiles.
- Local calendar views, editable events, Backlog deadline overlays, reminders, and one schedule-aware work timer with its own insights workspace.
- Command Center with priority queue, upcoming schedule, capture inbox, work metrics, quick links, command palette, and focus mode.
- Obsidian vault browsing/editing plus ticket-note creation, daily links, managed AI note sections, history backups, standup export, and reusable programming references.
- Morning Standup roster/order/notes and clipboard/export workflow.
- Six named themes, compact/comfortable density, configurable font size, responsive navigation and inspector, remembered task preferences, and custom launchers.
- Credential Manager storage for secrets and atomic JSON persistence for local state.
- A broad offscreen UI smoke test in `tests/ui_smoke.py` covering the main workflows with temporary data.

## Engineering foundations to strengthen

Status: In progress

These items reduce risk before the application gains process and source-control integrations:

- Split new domain logic out of `ui/main_window.py` instead of adding another large branch-heavy feature area.
- Add focused unit tests for configuration normalization, ticket/status helpers, persistence migration, patch/path validation, and command result parsing.
- Introduce a small consistent result/error model for external operations so Backlog, llama.cpp, and SVN work expose actionable failures without leaking secrets.
- Add a lightweight logging/diagnostic policy for external commands and worker lifecycles, with redaction and no credential logging.
- Add a documented version/migration strategy for JSON state as new features introduce profiles, patch metadata, or additional local preferences.
- Consider adding a formatter/linter/type-checker configuration once the project is ready to adopt one consistently; do not introduce a tool that rewrites the whole codebase as a side effect of a feature.

Completed during the SVN release:

- Added typed external-command results, SVN domain values, focused path/config validation, and explicit process errors.
- Added focused deterministic tests plus a real isolated SVN repository integration test.
- Added atomic config saving and backward-compatible validation for SVN profiles, saved task views, and invalid workday ranges.
- Audited undefined/unused names across the full project and removed all current findings.

## Priority 1 — SVN/TortoiseSVN patch-backed ticket workflow

Status: Completed — implemented and verified on 2026-08-26.

### Implementation outcome

The first comprehensive Source Control release now includes:

- Source Control settings with executable detection, patch shelf location, timeout, and project-mapped working-copy profiles.
- A responsive ticket-aware workspace with status, selected-path diff/compare, working-copy info, log, blame, update, cleanup, resolve, cancellation, output inspection, and manual ticket-key support.
- Ticket-scoped patch creation with atomic patch/manifest files, safe folder names, base/repository context, affected paths, schema version, state, warnings, and SHA-256 verification.
- “Shelve changes” as create → verify → confirm → revert exactly the recorded versioned paths, including partial-failure state instead of false success.
- Patch Shelf history with verified preview, exact-root/repository preflight, `svn patch --dry-run`, conflict detection, explicit apply confirmation, and retained artifacts.
- Safe refusal to shelve unversioned or unsupported binary content; those files are preserved and explained to the user.
- Command palette, navigation, toolbar, ticket inspector, ticket context menu, keyboard shortcuts, status feedback, dark/light styling, and responsive layout integration.
- Optional TortoiseSVN dialogs for check-for-modifications, commit, update, log, revision graph, repository browser, diff, blame, switch, merge, create patch, properties, lock, unlock, and resolve.
- Focused fake-process tests, cancellation/failure/timeout/integrity/path checks, UI smoke coverage, and a real temporary-repository shelve/apply round-trip on a Windows working-copy path with spaces and non-ASCII characters.

### User problem

When several Backlog tickets are active, the user needs to switch code context quickly. The desired workflow is:

```text
Ticket fix in working copy
    → create/save patch under that ticket's folder
    → review the patch and affected files
    → revert those patched files
    → work on another ticket
    → later select the ticket
    → preview/apply the saved patch
    → continue testing or commit through the chosen SVN UI
```

### Product vocabulary

Use familiar labels, with an explanatory subtitle where needed:

- **Show status** — current SVN state and modified/unversioned/conflicted files.
- **Show diff** — inspect local changes, optionally for selected files.
- **Compare with base** — compare the working copy against its SVN base revision.
- **Create patch** — save a patch from current changes without changing files.
- **Shelve changes** — create and verify a patch, then offer to revert only the files included in that patch.
- **Apply patch / Unshelve changes** — preview and apply a saved patch to a selected working copy.
- **Revert selected changes** — explicit destructive action with file-level scope and confirmation.
- **Update working copy** — fetch/apply repository changes; never combine with patch apply automatically.
- **Commit changes** — optional later action; always show files/message and require explicit confirmation.

“Shelve” and “unshelve” are product terms for the patch-backed workflow. Classic SVN does not provide a universal native shelf equivalent, so the app must explain that the shelf is a patch artifact plus metadata, not a server-side branch or hidden SVN commit.

### Proposed configuration

Add a Source Control settings area, keeping secrets out of config:

- SVN executable path, defaulting to a detected `svn.exe` when available.
- Optional TortoiseProc executable path, defaulting to a detected TortoiseSVN installation when available.
- One or more named working-copy profiles, each with a safe local root and optional project mapping.
- Patch shelf root, defaulting to an app-data location such as `%APPDATA%`/`~/.ty-work-app` under an `svn` or `patches` directory, unless the user chooses a project-local location.
- Default patch format/encoding and whether to open TortoiseSVN dialogs for supported actions.
- No password or repository credential field; SVN authentication belongs to the SVN/TortoiseSVN credential system.

Configuration loading must accept older files, normalize paths, reject nonexistent or unsafe roots at use time, and avoid writing a detected executable path until the user confirms or saves it.

### Proposed internal boundaries

Do not implement the feature as command strings spread through `MainWindow`. Introduce focused boundaries similar to the existing Backlog adapter and worker services:

- `modules/svn_models.py` (or equivalent): typed models for a working-copy profile, patch artifact, file change, command request, and command result.
- `modules/svn_service.py` (or equivalent): executable discovery, safe argument construction, working-copy validation, status/diff/patch/revert/apply operations, TortoiseSVN launch requests, and output parsing.
- `services/svn_service.py` or `services/svn_workers.py` (choose one clear location): QThread/QProcess integration, progress, cancellation, timeout, and lifecycle cleanup.
- `modules/patch_store.py` (or equivalent): patch folder layout, manifest creation, versioning, safe names, atomic writes, and shelf history.
- `ui/source_control_widget.py` and/or a focused dialog: status/diff results, patch selection, preflight preview, confirmation, and conflict/error states.
- A small `MainWindow` integration: selected-ticket context, navigation, refresh, command-palette entries, and status messaging only.

Reuse `issue_key()` for ticket identity and the existing `WorkspaceStore`/config patterns where appropriate, but do not overload `workspace.json` with large patch contents. Patch content should be a file; local metadata should be small and indexable.

### Proposed patch shelf layout

The exact location should remain configurable, but the logical structure should be stable and safe:

```text
<patch-root>/
  <project-safe-name>/
    <ticket-key-safe-name>/
      2026-08-26_143012_fix-name.patch
      2026-08-26_143012_fix-name.json
```

The manifest should include, at minimum:

- ticket key and optional title snapshot;
- working-copy profile/root identifier;
- patch file path and creation time;
- SVN base revision and repository URL when available;
- normalized affected paths;
- whether the patch was generated from the whole working copy or an explicit selection;
- whether the shelve revert was completed, partially completed, or not attempted;
- a content hash for integrity checking;
- a schema version for future migrations.

Never trust ticket titles, repository paths, or patch metadata as raw filesystem paths. Derive safe display names and keep the original values only as metadata.

### First vertical slice

Implement in this order:

1. Detect/configure `svn.exe` and validate a selected working copy.
2. Show status with parsed file states and a raw-output/details view.
3. Show diff for the working copy or selected files without mutation.
4. Create a patch artifact and manifest atomically, with a preview before saving.
5. Implement “Shelve changes” as create patch → verify file list/base → confirm → revert exactly those files → report the outcome. If any step fails, stop and preserve the user's files.
6. List patch artifacts for the selected ticket and show their metadata.
7. Preview and apply a selected patch with a dry-run/preflight where supported, explicit target selection, conflict reporting, and no automatic commit/update.
8. Add tests with fake executables/process results and temporary working copies; add one offscreen UI path for the full create/shelve/apply flow.

### Safety requirements

- All process calls must use argument arrays or `QProcess`; no shell interpolation.
- Keep the GUI responsive and provide cancellation for long operations.
- Show the exact working-copy root, patch file, affected files, base revision, and operation before any revert/apply/update/commit.
- Require confirmation for revert, apply, update, merge, cleanup, and commit. A read-only status or diff should not require confirmation.
- Never automatically commit, update, delete a patch, delete unversioned files, or run cleanup as part of shelving or unshelving.
- Refuse to apply a patch if the target working copy/profile does not match the recorded root/repository/base context unless the user explicitly chooses a reassignment path and the preflight explains the risk.
- Treat conflicts, missing files, local edits that were not part of the patch, and unversioned files as visible states, not silent exceptions.
- Redact credentials and sensitive repository details from user-facing logs where appropriate.
- Preserve the patch artifact after an apply, mark it as applied, and let the user archive/delete it separately.

### Useful SVN command coverage after the first slice

Group actions by risk and keep read-only actions easy to reach:

| Group | Candidate actions |
| --- | --- |
| Inspect | status, diff, compare with base, info, log, blame, list repository/working-copy details |
| Patch workflow | create patch, shelve, list shelves, inspect patch, apply/unshelve, mark/archive shelf |
| Local working copy | revert selected/all with scope, resolve, cleanup |
| Repository sync | update, switch, merge, relocate or repair only if a later design supports them |
| Commit/review | commit, show pending commit files, open TortoiseSVN check-for-modifications or commit dialog |
| Navigation | open working copy, open repository browser, open file/folder in Explorer, copy path/revision |

Each action should declare whether it is read-only, local-mutating, or repository-mutating so the UI can apply the correct confirmation and result presentation.

### Acceptance criteria for Priority 1

The first release of the integration is complete only when all of the following are true:

- A user can configure or select a working copy and see a clear validation result.
- A selected Backlog ticket can create a uniquely named patch under its ticket folder without blocking the UI.
- The patch preview identifies the affected files and recorded base context.
- Shelving reverts only the files represented by the saved patch and reports partial failure without pretending the shelf is complete.
- Unshelving previews the target, detects obvious mismatches, applies the patch, and reports conflicts or success without committing.
- Existing local edits outside the selected patch are preserved.
- Commands work with paths containing spaces and non-ASCII characters on Windows.
- Offline Backlog/demo mode still allows local source-control work when a ticket is selected or a ticket key is entered.
- The command palette, ticket inspector/context menu, keyboard path, and settings all agree on labels and state.
- Tests cover command construction, output parsing, path safety, manifest persistence, failure/cancellation, and the end-to-end UI flow.
- `README.md` documents setup, the shelve/unshelve semantics, safety behavior, and shortcuts.

## Completed release — Backlog Ticket Center reliability and SVN-grounded comments

Status: Completed — implemented and verified on 2026-08-26.

- Reorganized the Personal Tasks inspector into **Work**, **Backlog**, and **Context** tabs with clearer hierarchy, calmer spacing, immediate timer state, and task-specific busy/disabled states.
- Fixed Start work continuing after a cancelled status confirmation, duplicate starts reporting false success, ticket comparison relying on nullable numeric IDs, and Stop waiting for the next clock tick before the UI caught up.
- Made Backlog mutations truthful: status remains server-confirmed until the worker succeeds; controls display updating/posting/queued/rejected states; a rejected status restores the confirmed combo value.
- Parsed Backlog's structured error response while redacting credentials. Network/429/5xx failures can queue for retry, while permanent 4xx errors are explained and never retried indefinitely.
- Strengthened queued actions: only the newest pending status per ticket survives, identical comments do not duplicate, retry entries remain durable until success, and permanently rejected queued comments return to the editor for correction.
- Made unsent comment drafts ticket-scoped during the app session so changing selection cannot post one ticket's text to another.
- Added **AI Generate Ticket Comment** while preserving explicit **Post comment** review and confirmation. llama.cpp now returns structured Japanese content rendered under `【対応内容】`, `【実施箇所】`, and `【詳細】`.
- Grounded comment generation in the newest SHA-256-verified ticket patch's actual unified diff. Parsed patch paths own `【実施箇所】`; the model cannot invent files, completed work, or test results.
- Fixed Japanese mojibake in streamed llama.cpp output by decoding raw SSE bytes explicitly as UTF-8 instead of allowing the HTTP client to guess Latin-1; added byte-level Japanese regression coverage and verified the configured live server.
- Fixed the Ticket Detail generation lifecycle so successful llama.cpp work re-enables generation controls and turns Stop off.
- Verified with expanded offscreen UI coverage, API error/retry tests, mock llama.cpp schema checks, real SVN smoke/integration suites, and the configured live Qwen/llama.cpp server using an isolated verified patch.

Grounded follow-ups:

- Let the user choose which verified patch (or patches) should ground a comment when a ticket has several artifacts; the first release intentionally uses the newest one.
- Add an evidence preview beside the generated comment showing the exact patch, file list, and truncated diff sent to llama.cpp.
- Add a compact queued-action manager with retry now, edit/remove, last error, and attempt history instead of exposing only a count and automatic retry.
- Consider persisting ticket-scoped unsent drafts across restarts after defining retention and privacy expectations; current drafts intentionally live only for the app session.
- Support project-specific Japanese comment templates without weakening the fixed evidence and review guarantees.

## Priority 2 — Make ticket context more composable

Status: Planned

- Add a stable ticket-context service/view model so Backlog, notes, calendar, reminders, focus, and future source control do not each reconstruct selected-ticket state independently.
- Add a compact activity timeline for local actions and external sync results, with safe redaction.
- Improve cross-surface navigation so every ticket-related view can open the same inspector/detail context without losing the user's place.
- Add explicit loading/empty/error states to any new or expanded workspace.

## Priority 3 — Efficiency and polish

Status: Planned

Grounded by the local AI release:

- Surface the model's `reasoning_content` in the UI when thinking is enabled, so a user who turns it on can see why the model reached a conclusion.
- Reuse the streaming transport for a chat-style follow-up on a generated brief, instead of regenerating from scratch.
- Feed the before/after compare output into the note template when a ticket has an applied patch, so `implementation_flow` can describe the actual change rather than the file list alone.

Grounded by the AI-integration release:

- Let the Obsidian workspace preview a note update in place, so a vault-started generation can be reviewed without hopping to Ticket Detail.
- Offer study material for a *selection* of ticket notes, not one ticket, once the single-ticket lesson quality is trusted; the lesson writer already merges into managed sections.
- Show which lessons a ticket already links to before generating, so a repeat run is an obvious update rather than a guess.
- Reuse the elide-on-resize label pattern from Ticket Detail and the Obsidian AI bar for the remaining fixed-width labels in Source Control.

Grounded by the Source Control usability release:

- Reuse the tiered sort header on the Changes table so state, path, and lock state can be ranked the same way the shelf now is.
- Offer a shelf retention view now that every patch is visible in one place: filter by state, or bulk-delete everything already applied.

- Bring the shelve/unshelve workflow back into the primary UI once its wording settles; it currently sits in More SVN so Create patch and Apply patch stay unambiguous.
- Show a per-file diff for the row selected in Patch Changes, reusing the same before/after pane.

- Persist the changes-table sort column and the Changes/tree splitter position alongside the unversioned filter, so the workspace reopens the way it was left.
- Let the before/after compare open two saved patches against each other, not only the working copy against its base revision. The per-file compare payload already has the right shape for this.
- Move the ticket patch count behind the existing worker boundary if a shelf ever grows large enough that the single-level glob in `PatchStore.count_for_ticket` becomes noticeable when selecting a ticket.
- Consider a bulk cleanup on the Patch Shelf (select several artifacts, or drop everything already applied) now that single-patch deletion exists and the safety wording is settled.

- Add more focused tests around worker lifecycle and stale Qt wrapper cleanup.
- Improve command discoverability by showing relevant commands based on the selected ticket and current workspace state.
- Add user-configurable shortcut conflict detection before expanding the shortcut set.
- Add small performance checks for large issue lists, Obsidian vault indexes, and patch histories.
- Add import/export or backup guidance for local JSON state and patch manifests.
- Review the visual system after each major workspace addition for compact density, light theme contrast, keyboard focus, and narrow-window behavior.

## Ideas to evaluate when grounded by real usage

These are intentionally not committed features. Promote an idea only when implementation or user feedback provides a concrete reason:

- Per-ticket working-copy mappings and branch/repository hints.
- A “resume ticket” command that opens its note, working copy, patch shelf, and Backlog page together.
- A safe pre-commit checklist combining ticket status, diff summary, tests, note updates, and unresolved conflicts.
- Local ticket templates for recurring work patterns.
- A lightweight daily activity report assembled from timers, notes, completed actions, and source-control events.
- More adapters only when they reduce real context switching and can preserve the same security/offline/UX guarantees.
- SVN changelist support as an additional way to isolate ticket work before patch creation.
- A binary-safe companion shelf format for versioned binary assets; the current text-patch workflow intentionally refuses to revert binary content it cannot restore.
- Patch archive/restore controls and retention policies once real shelf volume justifies them; explicit deletion is implemented, bulk retention is not.
- A source-control activity timeline that records redacted operation intent and outcomes without storing credentials or oversized command output.
- Multi-working-copy batch status with strict concurrency limits and clear stale-state indicators.

## Completed implementation brief — historical reference

The following brief drove the completed first release and remains as a reference for its intended boundaries:

> Implement SVN/TortoiseSVN integration in `C:\Project\Schedule` as a patch-backed, ticket-aware workflow. Do not begin with a broad refactor and do not change unrelated features. First inspect `agent.md`, this roadmap, `config.py`, `modules/ticket_utils.py`, `modules/workspace_store.py`, `services/sync_service.py`, `ui/main_window.py`, the relevant widgets, and `tests/ui_smoke.py`.
>
> Build the first vertical slice in small verified steps: (1) safe executable/working-copy configuration and discovery, (2) background status and diff inspection, (3) atomic ticket-scoped patch creation with a manifest, (4) explicit “Shelve changes” that creates and verifies the patch before reverting only its affected files, and (5) patch listing, preflight, preview, and “Apply patch / Unshelve changes” without automatic commit or update. Support paths with spaces and non-ASCII characters. Use `svn.exe` for deterministic operations and TortoiseSVN only for optional GUI actions; use argument arrays or `QProcess`, never `shell=True` or interpolated command strings.
>
> Keep process/service logic out of `MainWindow`; use typed models, a focused SVN service, a worker/process boundary, and a focused source-control widget/dialog. Preserve Backlog demo/offline behavior and allow local SVN work when a ticket key is available. Store patch files outside `workspace.json`; use safe ticket/project folder names, atomic manifests, schema versions, hashes, base revision/repository metadata, and applied/shelved state. Validate roots and prevent path traversal. Show operation scope, affected files, base context, stdout/stderr-derived results, conflicts, partial failures, cancellation, and recovery actions. Require confirmation for revert/apply/update/merge/cleanup/commit. Never log credentials.
>
> Add focused tests for argument construction, executable discovery, output parsing, path validation, patch manifest persistence, command failures/timeouts/cancellation, and the patch workflow using temporary directories/fake process results. Extend the offscreen smoke test for the user-facing flow. Use the existing styles, responsive layout, command palette, selected-ticket semantics, and dark/light/compact/comfortable settings. Update the README for setup and shortcuts. Only after all acceptance criteria pass, mark the roadmap item Completed and add grounded follow-up goals.

## Change log

- 2026-08-28 — AI integration release: wired the local-AI pipeline into the rest of the app and reworked the surfaces around it.
  - **Navigation.** The rail is now grouped into **Focus** (Command Center, Personal Tasks, Ticket Detail), **Workspaces** (Review Board, Source Control, Obsidian), and **Planning** (Calendar, Morning Standup), which puts Calendar and Morning Standup last as requested. `PAGES` is derived from the grouping so the order, the `Ctrl+1`…`Ctrl+8` shortcuts, and the palette entries cannot drift apart. Added attention counts on Personal Tasks and Review Board, sourced from the workspace state already computed for the other views.
  - **Obsidian workspace.** Added a ticket-aware **Ticket AI** bar: open the selected ticket's note, draft its note update, or generate study material without leaving the vault. Opening any ticket note now selects that ticket from its `ticket_id` front matter, so browsing the vault re-points the AI actions by ticket name. Added a name filter over the vault tree.
  - **Study material as a separate output.** Added a `lessons` generation mode with its own schema and prompt, whose lesson prose is explicitly forbidden from reporting ticket status. Added `ObsidianSync.apply_learning_references`, which writes only the lesson notes: the ticket note is never re-rendered, and its `## Learning references` section only gains a missing link. Ticket Detail gained **Study material** and **Save study material only**, so lessons no longer require committing to a full note rewrite. Extracted `normalize_learning_references` so the note template and the lessons mode share one normalizer.
  - **Ticket Detail layout.** Split the single six-control row into a **Generate** row and a **Save** row, so producing a draft and writing it to the vault are visibly distinct. Renamed the learning tab to **Study material** to match the button vocabulary.
  - **Fit and legibility.** Measured every toolbar row against the 900px minimum with real font metrics: the Obsidian AI bar overflowed by 213px, so the ticket badge now elides to a cap and the status label takes the leftover width. The page title elides instead of clipping mid-word under the model badge, the note path in the metadata line is now vault-relative rather than absolute, the vault note header elides from the left, and the collapsed rail label for Ticket Detail became `TKT` because `DETAIL` did not fit 52px.
  - **Fixed on the way.** `Source Control` was a reachable page that config validation and the Settings start-page list both rejected, so it could never be the startup page. Selecting a new ticket kept the previous ticket's patch caption until the shelf lookup returned. `render_ai_note` built lesson links from raw category/slug while the writer filed them through `_safe_name`, so the two could disagree; both now use one link builder.
  - Verified with the offscreen and native UI smoke suites, the SVN suite, and a live run against the configured llama.cpp server (Qwen3.8-27B, 96k context), which now covers the new study-material mode end to end.

- 2026-08-26 — Fixed Japanese llama.cpp output being rendered as mojibake (`è¦...`) in AI ticket comments. The cause was Requests guessing the charset for an SSE response without an explicit charset; the streaming client now decodes wire bytes as UTF-8 and reports invalid UTF-8 clearly. Added a raw-byte Japanese SSE regression and reran the configured live Qwen/llama.cpp integration successfully.

- 2026-08-26 — Completed the Backlog Ticket Center reliability and comment-generation release. The selected-ticket inspector is now split into Work, Backlog, and Context tabs; start/stop/status/comment state is truthful and recoverable; structured Backlog errors distinguish retryable outages from permanent rejections; comment drafts are ticket-scoped; and llama.cpp generates the fixed Japanese Backlog template from the newest verified ticket patch's actual SVN diff. Expanded UI/API/queue/AI coverage and verified the full UI, SVN, and live llama.cpp paths.

- 2026-08-26 — Local AI pipeline moved from Ollama to the llama.cpp server at `http://192.168.17.201:8080` (Qwen3.8-27B, 48k context, 4 slots), verified live. The client now speaks the OpenAI-compatible `/v1/chat/completions` API with `response_format: json_schema` so llama.cpp enforces the schema as a grammar, discovers the server through `/props` and `/v1/models`, and streams replies. Stale Ollama URLs and model tags are migrated on config load instead of failing at generation time. Added: a Test connection button that reports the loaded model, context window, and slots and fills in the context size; a reply-budget and context-window setting; an opt-in thinking toggle (off by default, because reasoning models spend the reply budget before answering); live progress and a Stop button for long generations; and prompt trimming that fits the note and comment history into the server's window and says what it omitted. A reply truncated by the token budget now explains itself instead of surfacing a JSON parse error. Closed the gap left when SVN shipped: every prompt now carries the file list of the ticket's saved patches, so `changed_files` reports files a patch actually touched rather than invented ones. Added `tests/llm_integration.py`, which covers all three generation modes, progress, cancellation, and the starved-budget path against the real server and skips when it is unreachable.

- 2026-08-26 — Patch Shelf opened up and given tiered sorting. The shelf now lists every patch under the shelf folder across all project and ticket folders instead of only the selected ticket's, with a **This ticket only** toggle for the old behaviour and a **Project** column ahead of Ticket, both using the shelf folder naming. Unreadable manifests are now counted and reported instead of being silently skipped. Added a tiered multi-column sort on the shelf header: left-click flips ascending/descending, right-click cycles a column's priority through none → ▲ → ▲▲ → ▲▲▲, higher tiers are compared first, and the effective order is spelled out under the table and remembered between sessions. Ticket and project names sort naturally, so DEV-2 stays ahead of DEV-10. The per-ticket **Patches (n)** badge still counts only the selected ticket whatever the shelf scope is.

- 2026-08-26 — Source Control reorganisation. Tabs now read in workflow order (Changes, Patch Shelf, Patch Changes, Before / After, Diff / Output). Selection-scoped actions moved onto the tab that shows the selection: Create patch, Revert selected, Show diff, Lock and Unlock on Changes; Apply patch and Delete patch on Patch Shelf. Create patch states its file count so it is obvious it uses only ticked files. The toolbar above the tabs now carries working-copy commands promoted out of More SVN (Log, Update to a revision, Cleanup, Info), leaving More SVN with only the shelve/unshelve workflow and the TortoiseSVN dialogs; per-file commands moved to the Changes table context menu. Added per-file locking backed by SVN's ignore-on-commit changelist, so locked files stay visible and patchable on request but are never selected automatically and sort to the bottom. Added the read-only Patch Changes tab, which parses the selected patch text into per-file operations and line counts. Strengthened the Before/After fills and added First/Previous/Next/Last change navigation with F7 / Shift+F7. Added a Ticket column to the Patch Shelf using the shelf folder naming. Fixed a defect found during the work: the shelf auto-selected its first row with signals blocked, so the selected patch was never read and the new Patch Changes tab stayed empty until a different row was clicked.

- 2026-08-26 — Source Control usability release. Widened the ticket field and made **Ticket Context** editable so patches are no longer all named `Manual-ticket-context.patch`. Added column sorting (path sorting groups siblings; state sorting surfaces conflicts first), a remembered **Unversioned** show/hide filter, a working-copy location tree beside the changes table, and reclaimed its width by shrinking the Use column and removing the Properties column (property status folded into the State cell and its tooltip). Made **Compare with base** a real side-by-side base/working-copy view backed by `svn cat -r BASE` instead of a duplicate of Show diff. Added unified-diff colouring with per-file header colours. Added confirmed patch deletion with a stronger warning for still-shelved patches. Added Backlog-project import and manual root entry to Source Control settings. Linked Ticket Detail, the task inspector, the context menu, and the command palette to patch creation, shelving, and a live patch count, with the ticket name filled in automatically. Fixed three defects found on the way: unstyled `QHeaderView`/corner-button backgrounds that drew a black strip beside the tables, `svn cat` CRLF output being compared against newline-translated working-copy text, and `PatchStore.create` silently overwriting an existing patch when two were created within the same clock tick.

- 2026-08-26 — Completed and verified the initial comprehensive SVN/TortoiseSVN release. Added the Source Control workspace, patch-backed shelf/unshelve flow, safe background process boundary, configuration, TortoiseSVN launchers, regression coverage, documentation, and grounded follow-up ideas.
- 2026-08-26 — Audit fixes: redacted Backlog API keys from request errors, constrained Obsidian note loading to the configured vault, persisted edits to all environment passwords, normalized invalid workday ranges, made config writes atomic, and removed all current undefined/unused-name findings.
- 2026-08-26 — Created the initial roadmap from the current codebase. SVN/TortoiseSVN remains planning-only; no implementation was made.
